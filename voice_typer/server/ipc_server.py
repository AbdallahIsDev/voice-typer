# ARCH-REFAC-002: handlers extracted to handlers/ package as mixins
"""JSON-lines IPC server over stdin/stdout OR TCP.

Reads JSON commands from stdin (legacy) or a TCP socket (Electron),
dispatches to the VoiceTyperApp instance, and writes JSON responses.

Usage (TCP mode — Electron)::

    python -m voice_typer.server.ipc_server --port 9876

Usage (stdin/stdout mode — ``voice-typer`` CLI)::

    python -m voice_typer.server.ipc_server
"""

import contextlib
import hmac
import json
import logging
import os
import socket
import sys
import threading
import time
import typing
from concurrent.futures import ThreadPoolExecutor

from voice_typer.server import event_bus

# CR-1 (consolidated): the helper leaf submodules under
# ``voice_typer.server.ipc`` (``validation.py``, ``transport.py``,
# ``rate_limiter.py``, ``history_bounds.py``) are the CANONICAL source
# for every helper this module uses.  The dead duplicates
# ``ipc/server.py``, ``ipc/main.py``, ``ipc/process_meta.py`` and
# ``ipc/push_events.py`` were deleted (CR-019 / test_dead_code_stays_removed)
# and the local copies of these helpers that used to live in
# ``ipc_server.py`` were replaced with the re-export imports below so the
# two implementations cannot silently drift.  The ``IPCServer`` class,
# ``_push_event_now``, ``_set_process_metadata`` and ``main`` remain
# canonical to this module — they have no parallel implementation under
# ``ipc/``.
#
# The ``noqa: F401`` markers flag these as intentional re-exports: the
# names are used by this module's ``IPCServer`` body AND by external
# ``from voice_typer.server.ipc_server import X`` imports (pinned by
# ``tests/test_pick_available_port.py``, ``tests/test_cr_fixes.py``,
# ``tests/test_ipc4_rate_limiter_dual_window.py``,
# ``tests/test_r4_f18_rate_limiter_concurrent_init.py``,
# ``tests/test_server.py``, ``tests/test_dead_code_stays_removed.py``
# and ``tests/tauri/mig19/test_phase4_validation.py``).  Object identity
# (``ipc_server._RateLimiter is ipc.rate_limiter._RateLimiter``) is the
# single-source-of-truth guarantee — see
# ``test_ipc_server_imports_TCPLineIO_from_transport`` for the pinned
# pattern.
from voice_typer.server.ipc.history_bounds import (  # noqa: F401
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _REDACTED_SENTINEL,
    _SECRET_CONFIG_FIELDS,
    _bound_history_limit,
    _bound_history_offset,
    _sanitize_config_for_ipc,
)
from voice_typer.server.ipc.rate_limiter import (  # noqa: F401
    _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS,
    _HEARTBEAT_INTERVAL_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
    _RATE_LIMIT_BURST,
    _RATE_LIMIT_BURST_WINDOW_SECONDS,
    _RATE_LIMIT_SUSTAINED,
    _RATE_LIMIT_WINDOW_SECONDS,
    _RATE_LIMITER_INIT_LOCK,
    _TCP_WRITE_TIMEOUT_SECONDS,
    COMMAND_COSTS,
    DEFAULT_COST,
    _RateLimiter,
)

# NOTE: ``_get_rate_limiter`` is intentionally NOT imported here — it is
# defined locally below (see the CR-11 / R4-F18 comment block) so tests
# that monkey-patch ``ipc_server._RateLimiter`` are observed by the
# get-or-create's module-global class lookup.
from voice_typer.server.ipc.transport import (  # noqa: F401
    _pick_available_port,
    _TCPLineIO,
)
from voice_typer.server.ipc.validation import (  # noqa: F401
    _error_response,
    _validate_dict_payload,
)
from voice_typer.server.keyboard_ownership import keyboard_ownership
from voice_typer.server.log_rate_limit import log_rate_limited

log = logging.getLogger("voice_typer.server.ipc_server")


# ── CR-11 / R4-F18: per-process rate limiter get-or-create ───────────────
#
# ``_get_rate_limiter(server)`` is the canonical lazy get-or-create for the
# per-process ``_RateLimiter``.  It is defined LOCALLY here (rather than
# only re-exported from ``voice_typer.server.ipc.rate_limiter``) because
# tests in ``tests/test_r4_f18_rate_limiter_concurrent_init.py`` and
# ``tests/test_cr_fixes.py`` monkey-patch ``ipc_server._RateLimiter`` with
# a counting stand-in to widen the race window — the patched class is
# only observed if ``_get_rate_limiter`` looks up ``_RateLimiter`` from
# THIS module's globals at call time.  A function imported from the leaf
# module would resolve ``_RateLimiter`` against the LEAF module's globals
# and silently ignore the monkey-patch.
#
# The class object (``_RateLimiter``) and the init lock
# (``_RATE_LIMITER_INIT_LOCK``) are still the canonical leaf-module
# objects, imported above — they're single-source-of-truth.  Only the
# get-or-create function is duplicated, with the leaf copy at
# ``voice_typer/server/ipc/rate_limiter.py`` kept in sync.  See the test
# ``test_leaf_copy_also_has_init_lock`` for the pinned invariant.
def _get_rate_limiter(server: "object") -> _RateLimiter:
    """Return the per-process ``_RateLimiter`` for ``server`` (CR-11).

    Lazily creates and stores the limiter on the server instance so
    reconnects within the same process share the same sliding-window
    budget. A local attacker can no longer reset the budget by
    disconnecting and reconnecting.

    R4-F18: the get-or-create sequence is atomic across threads thanks
    to ``_RATE_LIMITER_INIT_LOCK``. The lock is module-level (shared
    across all server instances) — that's correct because the critical
    section is "check this specific ``server._rate_limiter_instance``
    and, if missing, create+store". Different server instances have
    different ``_rate_limiter_instance`` attributes, so the lock
    serializes only the get-or-create on the SAME server (which is the
    only race that matters); different servers can init in parallel
    without contention. The lock is held for microseconds at most (no
    I/O, no ``allow()`` call), so contention is negligible.
    """
    # Fast path: limiter already exists on the server instance — return
    # it WITHOUT acquiring the init lock. This is the common case after
    # the first dispatch on each server; the lock is only needed for
    # the brief first-call race. The fast path is safe because
    # ``server._rate_limiter_instance`` is set atomically by the
    # ``setattr`` below (CPython's GIL makes single-attribute writes
    # atomic) and the ``_RateLimiter`` instance itself is fully
    # thread-safe (its own ``self._lock`` guards deque mutation).
    limiter = getattr(server, "_rate_limiter_instance", None)
    if isinstance(limiter, _RateLimiter):
        return limiter

    # Slow path: limiter is None or a non-_RateLimiter (e.g. an
    # auto-vivified MagicMock child). Acquire the init lock and
    # RE-CHECK — another thread may have created+stored the limiter
    # between our fast-path check and the lock acquisition (classic
    # double-checked locking pattern).
    with _RATE_LIMITER_INIT_LOCK:
        limiter = getattr(server, "_rate_limiter_instance", None)
        if not isinstance(limiter, _RateLimiter):
            limiter = _RateLimiter()
            # ``setattr`` on a MagicMock overrides the auto-vivified child
            # attribute; on a real IPCServer it just sets the attribute.
            server._rate_limiter_instance = limiter  # type: ignore[attr-defined]
        return limiter


# Module-level push hook.  Set by the active IPCServer instance when it
# starts; cleared when it stops.  Using a module global (instead of
# e.g. ``app._ipc_server``) means listeners from any module can push
# events without needing a reference to the app or the server, and
# without closure-capture surprises when multiple VoiceTyperApp
# instances exist in the same process (tests, restarts, etc.).
#
# NEW-IPC-013: this used to be a single Optional[Callable].  When two
# IPCServer instances existed in the same process (e.g. a test fixture
# plus the production server), the second start() would stomp the
# first server's push fn, and the first server's stop() would clear
# the global — leaving the second server unable to push events.  We
# now keep a registry (set) of push functions; _push_event_now fans
# out to ALL registered servers.  Each IPCServer registers on start
# and unregisters on stop, so the registry stays consistent across
# any number of concurrent instances.
#
# B-1: the registry and helpers below are now THIN SHIMS over
# ``voice_typer.server.event_bus``.  Domain modules should call
# ``event_bus.publish(event)`` directly; the names here are kept so
# existing lazy imports (``from voice_typer.server.ipc_server import
# directly (``ipc_server._push_event_now``) and tests that manipulate the
# registry set directly (``event_bus._subscribers.clear()``) continue to
# work.  The shims reference the SAME underlying set and lock objects
# as ``event_bus._subscribers`` / ``event_bus._lock`` so manipulating
# one affects the other.
# B-1 FIX-12: the _push_event_registry/_push_event_registry_lock aliases and
# _set_push_event/_clear_push_event shims have been removed.  Domain code and
# tests now call ``event_bus.subscribe`` / ``event_bus.unsubscribe`` directly.


def _push_event_now(msg: dict) -> bool:
    """Push a raw event to ALL active IPC servers, if any are wired.

    B-1: thin shim over ``event_bus.publish``.  Domain code should
    call ``event_bus.publish`` directly; this function is preserved
    so existing lazy imports continue to work.

    Returns True if at least one server accepted the event, False if
    no server is active.  Safe to call from any thread; never raises.

    NEW-IPC-013: previously pushed to a single global callable.  When
    two IPCServer instances existed in the same process (tests +
    production), the second start() would stomp the first's push fn,
    and the first's stop() would clear the global entirely — leaving
    the second server unable to push.  We now fan out to ALL servers
    in the registry so both receive the event.
    """
    return event_bus.publish(msg)


# ARCH-REFAC-002: the per-command ``_handle_*`` methods live in the
# ``handlers/`` subpackage as mixin classes.  We import them here (after
# all module-level helpers like ``log`` / ``_push_event_now`` /
# ``_get_rate_limiter`` and the imported ``_bound_history_limit`` /
# ``_RateLimiter`` / ``_validate_dict_payload`` names are bound) so the
# mixins can resolve their ``from voice_typer.server.ipc_server import
# ...`` references via the partially initialized module already present
# in ``sys.modules``.
#
# CRITICAL: Register the canonical module name BEFORE the mixin imports.
# When ``python -m voice_typer.server.ipc_server`` loads this module,
# it is stored in ``sys.modules`` as ``__main__``, NOT under its
# canonical dotted name.  The mixin handlers do
# ``from voice_typer.server.ipc_server import log, _push_event_now``;
# without this registration, Python creates a FRESH module for the
# canonical name, which then tries to import the mixins again —
# producing a circular ``ImportError``.
_CANONICAL = "voice_typer.server.ipc_server"
if _CANONICAL not in sys.modules:
    sys.modules[_CANONICAL] = sys.modules["__main__"]


from voice_typer.server.handlers.config_handlers import ConfigHandlersMixin  # noqa: E402
from voice_typer.server.handlers.dictation_handlers import DictationHandlersMixin  # noqa: E402
from voice_typer.server.handlers.history_handlers import HistoryHandlersMixin  # noqa: E402
from voice_typer.server.handlers.level_monitor_handlers import (  # noqa: E402
    LevelMonitorHandlersMixin,
)
from voice_typer.server.handlers.microphone_handlers import MicrophoneHandlersMixin  # noqa: E402
from voice_typer.server.handlers.microphone_test_handlers import (  # noqa: E402
    MicrophoneTestHandlersMixin,
)
from voice_typer.server.handlers.model_handlers import ModelHandlersMixin  # noqa: E402
from voice_typer.server.handlers.onboarding_handlers import OnboardingHandlersMixin  # noqa: E402
from voice_typer.server.handlers.privacy_handlers import (  # noqa: E402
    PrivacyHandlersMixin,
)
from voice_typer.server.handlers.repaste_handlers import RepasteHandlersMixin  # noqa: E402
from voice_typer.server.handlers.status_handlers import StatusHandlersMixin  # noqa: E402
from voice_typer.server.handlers.system_handlers import SystemHandlersMixin  # noqa: E402
from voice_typer.server.handlers.templates_handlers import TemplatesHandlersMixin  # noqa: E402
from voice_typer.server.handlers.vocabulary_automation_handlers import (  # noqa: E402
    VocabularyAutomationHandlersMixin,
)
from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402


class IPCServer(
    ConfigHandlersMixin,
    StatusHandlersMixin,
    DictationHandlersMixin,
    HistoryHandlersMixin,
    MicrophoneHandlersMixin,
    VocabularyHandlersMixin,
    TemplatesHandlersMixin,
    OnboardingHandlersMixin,
    MicrophoneTestHandlersMixin,
    LevelMonitorHandlersMixin,
    ModelHandlersMixin,
    SystemHandlersMixin,
    VocabularyAutomationHandlersMixin,
    RepasteHandlersMixin,
    PrivacyHandlersMixin,
):
    """Reads JSON commands from stdin or TCP, dispatches, writes responses.

    Attributes
    ----------
    app : VoiceTyperApp
        The application instance this server wraps.
    """

    # Dedicated per-instance write-serialization lock for the TCP
    # write path in ``_send``. ``socket.sendall`` releases the GIL
    # between ``send()`` syscalls (when the kernel send buffer is
    # full), so two concurrent ``sendall`` calls on the same socket
    # can interleave their bytes at the kernel send-buffer level,
    # corrupting the JSON-lines protocol. Serializing ONLY the
    # write+flush+drain section (NOT the snapshot phase, NOT the
    # dispatch read path) prevents interleaving without re-introducing
    # the NEW-IPC-014 "slow client blocks all dispatchers" problem
    # that ``self._lock`` had when it covered the entire send path.
    #
    # Production instances override this in ``__init__`` with a
    # per-instance ``threading.Lock`` so two IPCServer instances
    # don't share a lock. The class-level fallback exists ONLY for
    # tests that bypass ``__init__`` via ``IPCServer.__new__(IPCServer)``
    # and exercise ``_send`` — those tests don't set ``_tcp_write_lock``
    # explicitly, so without the fallback they'd raise
    # ``AttributeError``. Sequential test execution makes the
    # shared fallback safe (no cross-test contention).
    _tcp_write_lock = threading.Lock()

    def __init__(
        self,
        app,
        service: "typing.Any | None" = None,
    ) -> None:
        # ARCH-REFAC-004: dependency-injection seam.
        #
        # ``IPCServer(app)`` (no ``service``) is the backward-compatible
        # path used by all existing call sites — production entry point
        # and 20+ test files.  It constructs a real ``VoiceTyperService``
        # over ``app`` exactly as before.
        #
        # ``IPCServer(app, service=fake)`` is the DI path used by tests
        # that want to exercise the IPC dispatch layer in isolation
        # from the service implementation.  The injected ``service`` is
        # stored verbatim on ``self.service``; no ``VoiceTyperService``
        # is constructed.  This lets a test substitute a ``MagicMock``
        # (see ``tests/fixtures/ipc_test_helpers.py:make_fake_service``)
        # and assert on the IPC layer's behavior without coupling to
        # ``VoiceTyperService``'s internal app glue.
        #
        # ``app`` is typed as ``Any`` (not ``AppProtocol``) so that
        # existing MagicMock-based test fixtures keep working without
        # importing the protocol module.  ``AppProtocol`` is a
        # structural type — a MagicMock satisfies it — but annotating
        # the parameter with ``AppProtocol`` would force every test
        # file that constructs ``IPCServer(app)`` to import the
        # protocol, which is an unnecessary migration burden.  The
        # protocol is for documentation and the introspection
        # regression test in ``tests/test_di_providers.py``.
        self.app = app
        if service is not None:
            self.service = service
        else:
            # ARCH-005: wire VoiceTyperService as the service boundary.
            # IPC routes delegate through the service instead of calling
            # self.app directly. This allows a second transport (CLI,
            # gRPC) to reuse the same service layer without duplicating
            # app glue.
            from voice_typer.server.service import VoiceTyperService

            self.service = VoiceTyperService(app)
        self._running = False
        # NEW-CQ-018: use RLock instead of Lock so _hook_tray_set_state
        # (which calls self.push() → self._send() → acquires _lock) can
        # safely re-enter if a future change makes set_state indirectly
        # trigger an IPC call. Lock would deadlock; RLock allows the
        # same thread to re-acquire.
        self._lock = threading.RLock()
        # Per-instance override of the class-level ``_tcp_write_lock``
        # fallback. See the class-level docstring above for the
        # rationale (write-serialization lock separate from
        # ``self._lock`` so a slow client blocks other writers — not
        # other dispatchers' snapshots or the read path).
        self._tcp_write_lock = threading.Lock()
        self._tcp_client: _TCPLineIO | None = None
        self._tcp_mode = False
        self._pending_tcp: list[str] = []
        # NEW-IPC-001: store the listening TCP server socket so stop()
        # can close it to unblock the accept() loop.  Previously the
        # socket was a local variable in _accept_tcp and stop() had no
        # way to wake the loop, leaving the daemon thread blocked on
        # accept() forever (acceptable in production but leaks threads
        # and sockets in test start/stop cycles).
        self._tcp_server_socket: socket.socket | None = None
        # SEC-8: TCP connection handler worker pool. Lazily created in
        # start_tcp() so test-only IPCServer constructions don't spawn
        # background threads. Each accepted connection is handed off to
        # this pool IMMEDIATELY after accept(), so the auth handshake
        # (with its 5s timeout) runs on a worker thread — a slow or
        # malicious client that opens a connection and sends nothing
        # can no longer stall the accept loop and block the next
        # legitimate client from being accepted.
        self._tcp_worker_pool: ThreadPoolExecutor | None = None
        # NEW-IPC-013: this server's push callable, registered in the
        # module-level _push_event_registry on start() and unregistered
        # on stop().  Tracked on the instance so stop() can remove just
        # our callable without affecting other active servers.
        self._push_fn: typing.Callable[[dict], None] | None = None

        # RW-10: heartbeat watchdog state.
        #
        # ``_last_heartbeat_at`` is ``None`` until Electron sends its
        # first ``heartbeat`` IPC command.  The watchdog daemon thread
        # started in ``start()`` refuses to fire ``app.quit()`` while
        # this is ``None``, so the backend doesn't exit prematurely
        # during a slow Electron cold start (10+ seconds for the torch
        # import on first launch).  Once the first heartbeat lands,
        # the timestamp is updated on every subsequent heartbeat.
        self._last_heartbeat_at: float | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop_event = threading.Event()
        # PERF-005: Electron sets this event when it receives the
        # ``relaunch_electron`` request and is about to relaunch.  restart_app
        # waits on it (bounded by a 2s timeout) instead of a fixed time.sleep,
        # so the tray thread is unblocked as soon as Electron acks (or after
        # the timeout).  Cleared before each wait so a stale ack from a prior
        # restart can't satisfy a fresh one.
        self._relaunch_ack_event = threading.Event()

        # CR-4: per-instance flag (was module-level in sidecar_ws.py).
        # ``sidecar_ws._handle_connection`` reads/writes this attribute on the
        # ``IPCServer`` instance passed to ``sidecar_ws.run()`` so the ``ready``
        # event is emitted only on the first authenticated WS connection.
        # Previously this was a module-level global in ``sidecar_ws.py`` —
        # correct for production, but never reset between test runs that import
        # the module once and call ``run()`` multiple times with different
        # ``IPCServer`` instances. Per-instance state makes each server own its
        # own flag, so a fresh ``IPCServer`` starts with ``_ready_emitted =
        # False`` automatically. See ``_reset_ready_emitted()`` for the
        # test-only helper that resets this between runs of the same server.
        self._ready_emitted: bool = False

    # ── Lifecycle ───────────────────────────────────────────────────────

    def _reset_ready_emitted(self) -> None:
        """Test-only: reset the per-instance ``_ready_emitted`` flag.

        CR-4: in production, ``_ready_emitted`` is set to ``True`` on the
        first authenticated WS connection and never reset — this is the
        intended behavior so a transient WS reconnect after a drop does
        NOT re-emit the ``ready`` event. However, tests that construct a
        single ``IPCServer`` and call ``sidecar_ws.run(server)`` multiple
        times in the same process need to reset the flag between runs to
        verify the "first connection emits ready" path.

        The cleaner alternative — constructing a fresh ``IPCServer`` per
        test — is what we recommend, and is what the per-instance move
        enables (a fresh instance starts with ``_ready_emitted = False``
        automatically). This helper exists for the small number of tests
        that, for fixture-sharing reasons, must reuse the same instance.

        Marked "test-only" by convention (leading underscore + docstring)
        rather than by a runtime guard — the cost of an accidental
        production call is just a duplicate ``ready`` event, which the
        host already tolerates (it's idempotent on the UI side).
        """
        self._ready_emitted = False

    def start(self) -> None:
        """Start the IPC server in a daemon thread.

        Also hooks ``app.tray.set_state`` so that every state change emits
        a ``status_change`` push event back to the frontend.
        """
        self._running = True
        # Expose the server on the app so listeners (waveform bubble,
        # streaming partials, etc.) can push events without an explicit
        # reference being threaded through every call site.
        self.app._ipc_server = self
        # ALSO register the push function at module level.  This is
        # the bullet-proof path: any code (waveform listeners, hot
        # paths, audio callback) can call ``event_bus.publish(msg)``
        # without holding a reference to the app or the server.
        # NEW-IPC-013: _set_push_event now adds to a registry instead
        # of stomping a single global.  We track our own push callable
        # so stop() can unregister just ours without affecting other
        # active servers.
        # B-1: subscribe through the event_bus directly.
        self._push_fn = self.push
        event_bus.subscribe(self._push_fn)
        self._hook_tray_set_state()
        # NEW-PRIV-0xx / d-review Finding 1: do NOT start the stdin
        # listener in TCP/WS mode. A direct-terminal invocation
        # (``python -m voice_typer.server.ipc_server --port N``) would
        # otherwise accept unauthenticated JSON commands on stdin while
        # the TCP socket enforces the VOICE_TYPER_IPC_TOKEN handshake.
        # The stdin listener is only for the legacy stdin/stdout IPC mode
        # (``_tcp_mode`` is False). In TCP mode stdin is unused (inherited
        # from Electron, connected to /dev/null or NUL).
        if not self._tcp_mode:
            self._stdin_thread = threading.Thread(
                target=self._run,
                name="ipc-server",
                daemon=True,
            )
            self._stdin_thread.start()
        else:
            self._stdin_thread = None
        # RW-10: start the Electron-alive heartbeat watchdog.  Daemon
        # thread so it doesn't block shutdown.  The thread refuses to
        # fire ``app.quit()`` until the first heartbeat lands, so a
        # slow Electron cold start (10+ seconds for torch import)
        # doesn't trigger a false-positive exit.
        # ADR-0020 §2 + §10: under the Tauri sidecar path
        # (TAURI_SIDECAR=1), the Python-side heartbeat watchdog
        # (ADR-0018) is disabled. The Tauri Rust host owns liveness
        # via TWO mechanisms: (1) WS-close / process exit triggers
        # FT-1 respawn, and (2) the Rust host dispatches a
        # ``heartbeat`` command every 10s and triggers FT-1 respawn
        # on 3 consecutive misses (≥30s unresponsive — catches GIL
        # contention / infinite loops / blocking C calls that keep
        # the socket open but don't respond to dispatches). The
        # Python ``_handle_heartbeat`` handler is registered in
        # ``_COMMAND_REGISTRY`` and updates ``_last_heartbeat_at``
        # for the (disabled) watchdog's bookkeeping. See
        # ``src-tauri/src/sidecar/ws.rs`` (reconnect_ws heartbeat
        # task) and ``voice_typer/server/sidecar_ws.py`` (Heartbeat
        # docstring) for the full picture.
        _tauri_sidecar = os.environ.get("TAURI_SIDECAR") == "1"
        if _tauri_sidecar:
            log.info(
                "[IPC] TAURI_SIDECAR=1 — skipping heartbeat-watchdog thread "
                "(Tauri Rust host owns liveness via WS-close + heartbeat dispatch)"
            )
            self._heartbeat_thread = None
        else:
            self._heartbeat_stop_event.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="heartbeat-watchdog",
                daemon=True,
            )
            self._heartbeat_thread.start()
        # THREAD-REGISTRY: register both IPC threads with the central
        # registry (if the app provides one) so ``shutdown_all()`` can
        # signal and join them during ``VoiceTyperApp.quit()``.
        #
        # heartbeat-watchdog: registers WITH a stop_event
        # (``_heartbeat_stop_event``) because the loop wakes on
        # ``Event.wait(timeout)`` — setting the event unblocks it
        # immediately and the thread exits cleanly.
        #
        # ipc-server (stdin listener): registers with ``stop_event=None``
        # because the thread blocks on ``for line in iter(stdin)`` —
        # there is no event it checks between reads. The existing
        # ``stop()`` path closes the TCP client socket and sets
        # ``_running = False`` (checked between lines), but the stdin
        # loop only exits naturally on EOF/OSError. The registry's
        # ``shutdown_all()`` will still JOIN the stdin thread (with a
        # short timeout) to verify it's tracked; the existing per-site
        # ``stop()`` is responsible for the actual cleanup.
        registry = getattr(self.app, "_thread_registry", None)
        if registry is not None:
            # ADR-0020 §10: heartbeat-watchdog is skipped under TAURI_SIDECAR=1,
            # so only register it if it actually exists.
            if self._heartbeat_thread is not None:
                registry.register(
                    name="heartbeat-watchdog",
                    thread=self._heartbeat_thread,
                    stop_event=self._heartbeat_stop_event,
                    join_timeout=2.0,
                )
            if self._stdin_thread is not None:
                registry.register(
                    name="ipc-server",
                    thread=self._stdin_thread,
                    stop_event=None,
                    join_timeout=0.5,
                )
        log.info("[IPC] server started; push hook registered")

    def stop(self) -> None:
        """Signal the stdin loop and TCP accept loop to stop.

        NEW-IPC-001: previously ``stop()`` only set ``_running = False``
        and cleared the push hook, but the TCP accept loop checked
        ``getattr(self, '_stopped', False)`` — a flag that was never
        set anywhere — and the listening socket was a local variable
        in ``_accept_tcp`` with no external reference.  The result was
        that ``stop()`` could not unblock a daemon thread sitting in
        ``server.accept()``; the thread (and socket) leaked until
        process exit.  We now (a) reuse ``_running`` as the lifecycle
        flag the accept loop checks, and (b) close the listening socket
        here so ``accept()`` raises ``OSError`` and the loop exits
        cleanly.

        NEW-IPC-013: stop() now unregisters OUR push callable from the
        module-level registry instead of clearing the global outright.
        Other active servers in the same process keep working.

        S-5: ``_stdin_thread`` is now joined with a short timeout so
        the thread is properly tracked and doesn't leak in test
        start/stop cycles. The stdin thread is a daemon that blocks
        on ``for line in iter(stdin)``, so a 0.5s timeout is
        sufficient — the thread exits naturally on stdin EOF/OSError.
        """
        self._running = False
        # Unregister our push callable.  Other servers in the registry
        # are unaffected.
        # B-1: unsubscribe through the event_bus directly.
        push_fn = getattr(self, "_push_fn", None)
        if push_fn is not None:
            event_bus.unsubscribe(push_fn)
            self._push_fn = None
        if self._tcp_client is not None:
            self._tcp_client.close()
            self._tcp_client = None
        # Close the listening socket to unblock the accept() loop.
        # The accept loop catches OSError and breaks out.
        server_sock = self._tcp_server_socket
        if server_sock is not None:
            with contextlib.suppress(OSError):
                server_sock.close()
            self._tcp_server_socket = None
        # SEC-8: shut down the TCP worker pool so queued (not-yet-
        # started) connection handoffs are dropped and in-flight
        # workers' teardown is no longer tracked. The accept loop
        # also shuts the pool down when it exits naturally; this is
        # the belt-and-suspenders path for callers that close the
        # listening socket directly (e.g. test fixtures) without
        # waiting for the accept thread to observe the close.
        pool = self._tcp_worker_pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_worker_pool = None
        # RW-10: signal the heartbeat watchdog to exit.  The thread
        # sleeps on ``_heartbeat_stop_event.wait(timeout=INTERVAL)``;
        # setting the event wakes it immediately so it doesn't linger
        # past shutdown.  (It's a daemon thread, so even if it lingered
        # it wouldn't block process exit — but explicit shutdown is
        # cleaner for test start/stop cycles.)
        self._heartbeat_stop_event.set()
        # THREAD-REGISTRY: unregister both IPC threads so a subsequent
        # ``start()`` cycle (common in tests) re-registers cleanly
        # without triggering the "Re-registering name" warning. Safe to
        # call when no entry exists (unregister is a no-op for unknown
        # names).
        registry = getattr(self.app, "_thread_registry", None)
        if registry is not None:
            registry.unregister("heartbeat-watchdog")
            registry.unregister("ipc-server")
        # S-5: join the stdin thread so it doesn't leak in test
        # start/stop cycles.  The thread is a daemon that blocks on
        # ``for line in iter(stdin)``, so a 0.5s timeout is sufficient
        # — the thread exits naturally on stdin EOF/OSError (set by
        # closing the TCP client socket above) or when _running becomes
        # False (checked between lines).
        stdin_thread = getattr(self, "_stdin_thread", None)
        if stdin_thread is not None and stdin_thread.is_alive():
            stdin_thread.join(timeout=0.5)
        # Keep the app-level reference so existing closures still
        # work after a stop+start cycle in tests.

    # ── TCP listener ───────────────────────────────────────────────

    def start_tcp(self, port) -> None:
        """Start a TCP server that accepts one Electron connection.

        CR-7 fix: ``port`` may be either:

        - an ``int`` (legacy / backward-compatible) — this method will
          create and bind its own socket to ``127.0.0.1:port``.  There
          is an inherent race window between this call and the bind
          (another local process could grab the port).
        - a ``(port_int, bound_socket)`` tuple (gold-standard — no race
          window).  The caller has already bound the socket (typically
          via :func:`_pick_available_port`); this method simply calls
          ``listen()`` on it and starts accepting connections.  The
          kernel guarantees no other process can claim the port between
          the probe and the listen.
        """
        self._tcp_mode = True
        # SEC-8: lazily create the worker pool that handles accepted
        # TCP connections off the accept-loop thread. A small pool is
        # sufficient — production has a single Electron client, and the
        # auth handshake's 5s timeout ensures slow/malicious clients
        # don't hold a worker indefinitely. Reusing the pool across
        # start_tcp() calls is fine; it's only torn down by stop().
        if self._tcp_worker_pool is None:
            self._tcp_worker_pool = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="tcp-worker",
            )
        t = threading.Thread(
            target=self._accept_tcp,
            args=(port,),
            daemon=True,
        )
        t.start()

    def _accept_tcp(self, port) -> None:
        """Accept one connection, then run the TCP IPC loop.

        SEC-018: the first line from the client must be a JSON object
        with ``{"type": "auth", "token": "<session-token>"}`` matching
        the ``VOICE_TYPER_IPC_TOKEN`` env var set by the Electron
        parent.  If the token is missing or doesn't match, the
        connection is dropped immediately.  This prevents any local
        process from connecting to 127.0.0.1:9876 and sending
        ``quit_app`` / ``set_config`` / etc.

        The token is generated by the Electron main process (see
        ``client/src/main/index.ts:startPython``) and passed to the
        Python subprocess via the ``VOICE_TYPER_IPC_TOKEN`` env var.
        Both sides see the same random per-launch value; no other
        process can know it.

        CR-7: ``port`` may be either an ``int`` (legacy) or a
        ``(port_int, bound_socket)`` tuple (gold-standard — eliminates
        the probe-then-bind race window).  See :meth:`start_tcp`.
        """
        # Read the expected token from the env var set by Electron.
        expected_token = os.environ.get("VOICE_TYPER_IPC_TOKEN", "")
        if not expected_token:
            # SEC-2: mirror the WS path (sidecar_ws._authenticate) — refuse
            # ALL connections when the token is unset. The host must always
            # set this env var; an unset token means any local process
            # could otherwise connect to 127.0.0.1:9876 and dispatch
            # arbitrary IPC commands (quit_app, set_config, etc.).
            #
            # We still bind+listen (so stop()/socket cleanup semantics work
            # — see test_accept_loop_can_be_stopped), but every accepted
            # connection is immediately closed in _handle_tcp_connection
            # below before any auth or dispatch runs.
            log.error(
                "[TCP] VOICE_TYPER_IPC_TOKEN not set — refusing ALL connections "
                "(the host must always set this env var)."
            )

        # CR-7: unpack the (port, bound_socket) tuple if provided — the
        # socket is already bound, so we skip the bind() call entirely
        # and go straight to listen().  This eliminates the race window
        # where another local process could grab the port between the
        # probe close() and the real bind().
        if isinstance(port, tuple):
            port_num, bound_sock = port
            server = bound_sock
            try:
                server.listen(1)
                log.info(
                    "[TCP] listening on 127.0.0.1:%d (pre-bound socket — no race window)",
                    port_num,
                )
            except Exception:
                log.exception("[TCP] failed to listen on pre-bound socket port %d", port_num)
                # Make sure we don't leak the socket on listen failure.
                with contextlib.suppress(OSError):
                    server.close()
                return
        else:
            port_num = port
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind(("127.0.0.1", port_num))
                server.listen(1)
                log.info("[TCP] listening on 127.0.0.1:%d", port_num)
            except Exception:
                log.exception("[TCP] failed to bind on port %d", port_num)
                # Make sure we don't leak the socket on bind failure.
                with contextlib.suppress(OSError):
                    server.close()
                return

        # NEW-IPC-001: store the listening socket on the instance so
        # stop() can close it to unblock the accept() call below.
        self._tcp_server_socket = server

        # NEW-IPC-001: accept connections in a loop so that a network
        # blip, sleep/resume, or Electron crash+restart doesn't brick
        # the Python IPC forever. Previously `server.close()` was
        # called after the first accept, so a single disconnect meant
        # no reconnection was possible until the Python process was
        # manually restarted.
        #
        # We check `self._running` (the canonical lifecycle flag set to
        # False by stop()) instead of the legacy getattr(self, "_stopped",
        # False) — that flag was never set anywhere, so the loop only
        # exited via OSError when stop() closed the socket (which it
        # couldn't do, because the socket was a local variable).
        while self._running:
            try:
                conn, addr = server.accept()
                log.info("[TCP] client connected from %s:%d", *addr)
            except OSError:
                # Server socket closed during shutdown (stop() called
                # server_sock.close()).
                break
            # SEC-8: hand the connection off to a worker thread
            # IMMEDIATELY so a slow/malicious client cannot block the
            # accept loop. Previously _handle_tcp_connection was called
            # inline here, so a client that opened a connection and sent
            # nothing would stall the accept loop for the full 5-second
            # auth timeout (soft DoS) — any other client that connected
            # during that window would be queued in the kernel backlog
            # and not picked up until the stalled auth timed out. The
            # auth handshake (and its timeout) now runs on a worker
            # thread, leaving the accept loop free to accept the next
            # connection right away.
            pool = self._tcp_worker_pool
            if pool is None:
                # Defensive: pool was never created (shouldn't happen
                # since start_tcp creates it before starting the accept
                # thread). Close the connection and keep looping.
                with contextlib.suppress(OSError):
                    conn.close()
                continue
            pool.submit(self._run_tcp_handler_safely, conn, addr, expected_token)
            # Loop back to accept the next connection

        # SEC-8: shut down the worker pool now that no new connections
        # will arrive. cancel_futures=True drops queued (not-yet-started)
        # submissions; in-flight workers are responsible for their own
        # teardown (the auth timeout + dispatch loop's OSError handling
        # ensure they exit promptly when their socket is closed).
        pool = self._tcp_worker_pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_worker_pool = None

        with contextlib.suppress(OSError):
            server.close()
        # Clear the instance reference so a subsequent start_tcp() can
        # store a fresh socket without confusion.
        if self._tcp_server_socket is server:
            self._tcp_server_socket = None

    def _run_tcp_handler_safely(self, conn, addr, expected_token: str) -> None:
        """SEC-8: run ``_handle_tcp_connection`` on a worker thread.

        Wraps the handler with the same exception handling that the
        accept loop used to apply inline, so an unexpected exception
        in one handler doesn't silently kill the worker thread (which
        would otherwise be reported only via the ThreadPoolExecutor's
        internal error handler and easy to miss in production logs).
        """
        try:
            self._handle_tcp_connection(conn, addr, expected_token)
        except OSError:
            # Routine: the handler already logged the disconnect at
            # DEBUG/INFO and ran its teardown. Nothing to surface.
            log.debug("[TCP] connection handler completed")
        except Exception:
            # Anything else escaping the handler (e.g. teardown in
            # _on_ipc_client_disconnect raising) is unexpected and
            # must be visible in production logs.
            log.warning(
                "[TCP] connection handler terminated unexpectedly",
                exc_info=True,
            )

    def _handle_tcp_connection(self, conn, addr, expected_token: str) -> None:
        """Handle a single TCP client connection (auth + dispatch loop).

        Extracted from _accept_tcp by NEW-IPC-001 so the accept loop
        can handle reconnections.

        PR-3-FIX-1: the auth handshake is now performed OUTSIDE
        ``self._lock`` to prevent a single-connection DoS. Previously,
        a client that opened a TCP connection and sent nothing would
        block the dispatcher thread AND hold ``self._lock``
        indefinitely, freezing the entire IPC server (every ``push()``
        event from any thread would block on the lock). Now the auth
        read has a 5-second timeout and the lock is only acquired
        after auth succeeds, to install the client and flush pending
        events. The token comparison also uses ``hmac.compare_digest``
        for constant-time comparison (consistent with
        ``security.verify_restart_token``).
        """
        # PR-3-FIX-1: set a read timeout BEFORE the auth readline so a
        # malicious client that connects but sends nothing can't hold
        # the thread indefinitely.
        _tcp_auth_timeout_seconds = 5.0
        with contextlib.suppress(OSError, AttributeError):
            conn.settimeout(_tcp_auth_timeout_seconds)  # socket may be a mock in tests

        # SEC-2: mirror the WS path — if the token is unset, refuse the
        # connection immediately. We log ERROR once at bind time (above)
        # and once per connection so a misconfigured launch surfaces in
        # either spot. Closing the conn unblocks the accept loop so the
        # server stays responsive to stop().
        if not expected_token:
            log.error(
                "[TCP] refusing connection from %s:%d — VOICE_TYPER_IPC_TOKEN not set",
                addr[0],
                addr[1],
            )
            with contextlib.suppress(OSError):
                conn.close()
            return

        # PR-3-FIX-1: perform the auth handshake OUTSIDE self._lock so
        # a stalled auth read doesn't block push() events from other
        # threads.
        if expected_token:
            auth_client = _TCPLineIO(conn)
            try:
                auth_line = auth_client.readline()
                if not auth_line:
                    log.warning("[TCP] client disconnected before sending auth")
                    auth_client.close()
                    return
                auth_msg = json.loads(auth_line.strip())
                # PR-3-FIX-1: use hmac.compare_digest for constant-time
                # comparison (consistent with security.verify_restart_token).
                # Check isinstance FIRST so .get() doesn't raise on
                # non-dict JSON values (e.g. 42, [1,2,3], "hi").
                token_valid = (
                    isinstance(auth_msg, dict)
                    and auth_msg.get("type") == "auth"
                    and isinstance(auth_msg.get("token", ""), str)
                    and hmac.compare_digest(auth_msg.get("token", ""), expected_token)
                )
                if not token_valid:
                    log.warning("[TCP] auth failed — invalid token")
                    try:
                        auth_client.write(
                            json.dumps(
                                {
                                    "type": "error",
                                    "data": {
                                        # IPC-5 (2026-07-18): add
                                        # ``code: "auth_failed"`` for
                                        # envelope consistency with
                                        # the other TCP/WS error
                                        # paths (rate_limited,
                                        # invalid_payload,
                                        # internal_error, etc.). The
                                        # WS path closes the socket
                                        # with code 1008 instead of
                                        # emitting an error frame, so
                                        # there is no WS-side code to
                                        # match — but a client reading
                                        # the TCP error frame can now
                                        # distinguish auth failure
                                        # from other errors without
                                        # substring-matching the
                                        # message.
                                        "code": "auth_failed",
                                        "message": "authentication failed",
                                    },
                                }
                            )
                            + "\n"
                        )
                        auth_client.flush()
                    except Exception:
                        pass
                    auth_client.close()
                    return
                log.info("[TCP] auth OK")
            except json.JSONDecodeError:
                log.warning("[TCP] auth failed — invalid JSON on first line")
                auth_client.close()
                return
            except Exception:
                log.warning("[TCP] auth handshake raised", exc_info=True)
                auth_client.close()
                return
        else:
            auth_client = _TCPLineIO(conn)

        # =====================================================================
        # CRITICAL FIX — DO NOT REMOVE (2026-07-20)
        # =====================================================================
        # Clear the 5s auth-read timeout (set at line ~1171) AFTER auth
        # succeeds. Without this ``conn.settimeout(None)``, the 5s timeout
        # leaks into the long-lived dispatch ``readline()`` loop, which then
        # raises ``socket.timeout`` (an OSError) every 5 seconds of idle.
        #
        # Symptom if removed: the app enters an infinite reconnect loop —
        #   connect → auth OK → 5s idle → socket.timeout →
        #   "client connection closed" → socket close → RST →
        #   Electron logs "connection reset by Python backend" → reconnect.
        # Every 5 seconds, forever. The backend appears "connected" but
        # never stays up long enough to handle real IPC.
        #
        # The timeout is set for auth (to reject stalled clients) and MUST
        # be cleared here for the dispatch loop (which blocks on readline).
        # =====================================================================
        with contextlib.suppress(OSError, AttributeError):
            conn.settimeout(None)  # blocking; socket may be a mock in tests

        # PR-3-FIX-1: now acquire the lock ONLY for the post-auth setup
        # (installing the client + snapshotting pending events). This is
        # a short critical section that can't block on unbounded I/O.
        # PERF-13: the pending-event flush is performed OUTSIDE the lock
        # (mirrors the ``_send`` snapshot-then-flush pattern at the
        # ``pending = list(self._pending_tcp); self._pending_tcp.clear()``
        # block). Pre-fix the lock was held across every
        # ``tcp_client.write`` + ``flush`` of the backlog, so a slow
        # client on first connect could stall all dispatchers.
        with self._lock:
            self._tcp_client = auth_client
            # Snapshot + clear pending under the lock (fast, no I/O).
            pending_flush = list(self._pending_tcp) if self._pending_tcp else None
            if pending_flush:
                self._pending_tcp.clear()

        # Flush the snapshot OUTSIDE the lock — a slow Electron
        # renderer can stall here without blocking other dispatchers.
        if pending_flush:
            for p in pending_flush:
                try:
                    auth_client.write(p + "\n")
                    auth_client.flush()
                except Exception:
                    log.debug("[TCP] pending flush write failed on connect")
                    break

        # ERR-017: emit a state_changed event on connect so the
        # renderer immediately knows the current app state (was
        # previously left stale until the next state transition).
        try:
            current_state = getattr(self.app.tray, "_state", None)
            current_msg = getattr(self.app.tray, "_message", "")
            if current_state is not None:
                self.push(
                    {
                        "type": "state_changed",
                        "data": {
                            "status": getattr(current_state, "value", str(current_state)),
                            "message": current_msg,
                        },
                    }
                )
        except Exception:
            log.debug("[TCP] failed to emit initial state_changed on connect")

        # RELIABILITY-006 + CR-11: per-process rate limiter. A buggy or
        # malicious Electron client that flood-dispatches commands would
        # otherwise starve the tray thread. CR-11: the limiter is shared
        # across all TCP connections to this server (looked up via
        # ``_get_rate_limiter(self)``) so a local attacker can no longer
        # reset the 200-message burst budget by disconnecting and
        # reconnecting — the 10s sliding window continues to evict old
        # timestamps across reconnects.
        rate_limiter = _get_rate_limiter(self)

        # SEC-8: capture a LOCAL reference to the authenticated client.
        # With the worker-pool fix, multiple handlers can run
        # concurrently (e.g. a slow-auth client still in its 5s auth
        # window while a fast-auth client connects and authenticates).
        # If a second client authenticates, ``self._tcp_client`` is
        # reassigned to the new client; iterating ``self._tcp_client``
        # directly would then read from the WRONG socket. Capturing the
        # local reference here ensures this handler's dispatch loop
        # always reads from the client it authenticated.
        client = auth_client

        try:
            for line in client:
                line = line.strip()
                if not line:
                    continue
                # PVT-G5-012: parse JSON BEFORE the rate-limit check so
                # the request ``id`` (when present) is available for the
                # rate-limit error response — clients using id-based
                # JSON-RPC-style correlation can then match the rejection
                # back to the originating request. Previously the check
                # fired on the raw line BEFORE ``json.loads``, so the
                # rate-limit error response carried no ``id``.
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # IPC-5 (2026-07-18): the error envelope now
                    # includes a ``code: "invalid_payload"`` field to
                    # match the WS path so the client can distinguish
                    # invalid JSON from rate-limit and dispatch errors.
                    # PVT-G5-011: pass the LOCAL ``client`` (captured at
                    # the top of the dispatch loop) so a concurrent
                    # fast-auth reconnect that reassigns ``self._tcp_client``
                    # doesn't redirect this error to the wrong socket.
                    # PVT-G5-012: ``id`` is unavailable here (json.loads
                    # failed before we could parse it) — match the WS path
                    # which also omits ``id`` on invalid_payload.
                    self._send(
                        {
                            "type": "error",
                            "data": {
                                "code": "invalid_payload",
                                "message": "invalid JSON",
                            },
                        },
                        _client=client,
                    )
                    continue
                # G4-M-09: pass ``command=msg_type`` so the per-command
                # cost map (``COMMAND_COSTS``) is applied — e.g.
                # ``download_model`` consumes 50 of the 200 burst units,
                # so a buggy client can fire at most 4 expensive commands
                # per second before the 5th is rejected. Cheap commands
                # (``heartbeat``, ``get_status``) keep the cost-1 behavior.
                # The legacy ``rate_limiter.allow()`` form (no ``command``
                # kwarg) is still supported and treats the call as cost 1.
                msg_type = msg.get("type") if isinstance(msg, dict) else ""
                if not rate_limiter.allow(command=msg_type):
                    # SEC-6: ``allow()`` increments the rejected counter
                    # atomically when it returns False — no separate
                    # ``reject()`` call needed (and calling it would
                    # double-count under the new atomic semantics).
                    #
                    # IPC-5 (2026-07-18): the error envelope now
                    # includes a ``code: "rate_limited"`` field to
                    # match the WS path (``sidecar_ws._make_dispatch``)
                    # so a client can distinguish rate-limit rejections
                    # from invalid-JSON and dispatch-exception errors
                    # without substring-matching the message text. The
                    # ADR-0020 §2 envelope contract is
                    # ``{"type":"error","data":{"code":<str>,"message":<str>}}``.
                    # PVT-G5-011: pass the LOCAL ``client`` so the error
                    # reaches the originating socket, not a concurrently-
                    # reconnected ``self._tcp_client``.
                    # PVT-G5-012: include ``id`` (when present in the
                    # parsed msg) so the client can correlate the
                    # rejection to the originating request.
                    rate_err: dict[str, object] = {
                        "type": "error",
                        "data": {
                            "code": "rate_limited",
                            "message": "rate limit exceeded; backing off",
                        },
                    }
                    if isinstance(msg, dict) and "id" in msg:
                        rate_err["id"] = msg["id"]
                    self._send(rate_err, _client=client)
                    log.warning(
                        "[TCP] rate limit hit (%d rejected)",
                        rate_limiter.rejected_count,
                    )
                    continue
                # ERR-018: isolate handler exceptions from socket I/O
                # errors.  Previously, ANY exception raised by
                # ``self._dispatch(msg)`` (other than JSONDecodeError,
                # which only fires for ``json.loads``) bubbled up to
                # the outer ``except Exception:`` clause below, which
                # logs "client connection closed" at DEBUG and
                # disconnects the client.  Handler bugs were therefore
                # silently swallowed and a single bad handler killed
                # the entire IPC session.  We now log the exception at
                # ERROR with ``exc_info`` and send a structured error
                # response so the client gets a clear signal and the
                # connection survives.  The outer ``except Exception:``
                # is now reserved for genuine socket I/O errors.
                try:
                    result = self._dispatch(msg)
                except Exception as dispatch_exc:
                    log.error(
                        "[TCP] unhandled exception in dispatch for message type %r: %s",
                        msg.get("type") if isinstance(msg, dict) else None,
                        dispatch_exc,
                        exc_info=True,
                    )
                    # B-6: preserve the request ``id`` on the error
                    # response so clients using ``id``-based
                    # request/response correlation (the standard
                    # JSON-RPC-like pattern) can match the error back
                    # to the originating request.  Without this, a
                    # buggy handler effectively orphaned every pending
                    # request — the client received an ``{"type":
                    # "error"}`` with no ``id`` and could not tell
                    # which request failed.  The message stays the
                    # generic ``"internal error"`` (we deliberately do
                    # NOT leak ``str(dispatch_exc)`` to avoid exposing
                    # server internals over IPC).
                    # ADR-0020 round-2 fix: add `code: "internal_error"`
                    # for consistency with other error envelopes
                    # (invalid_payload, rate_limited, etc. all carry a
                    # `code` field). The NEW-IPC-107 fix in usePython.ts
                    # and the Rust dispatch() command both read `code`
                    # with a `"unknown"` fallback, so this is backward-
                    # compatible but now consistent.
                    # PVT-G5-011: pass the LOCAL ``client`` so the error
                    # reaches the originating socket, not a concurrently-
                    # reconnected ``self._tcp_client``.
                    err: dict[str, object] = {
                        "type": "error",
                        "data": {
                            "code": "internal_error",
                            "message": "internal error",
                        },
                    }
                    if isinstance(msg, dict) and "id" in msg:
                        err["id"] = msg["id"]
                    self._send(err, _client=client)
                    continue
                if result is not None:
                    # PVT-G5-011: route the dispatch response to the LOCAL
                    # client that issued the request, not the (possibly
                    # reassigned) ``self._tcp_client``.
                    self._send(result, _client=client)
        except OSError:
            # Routine socket close / EOF: the client disconnected.
            log.debug("[TCP] client connection closed")
        except Exception:
            # Anything else escaping the dispatch loop is unexpected
            # (e.g. rate-limiter state corruption, partial-frame bugs)
            # and was previously swallowed at DEBUG. Surface it.
            log.warning("[TCP] unexpected error in connection loop", exc_info=True)
        finally:
            # SEC-8: close the LOCAL client reference (not
            # ``self._tcp_client``) and only clear the instance field
            # if it still points to us. Another handler may have
            # already replaced ``self._tcp_client`` with a newer
            # authenticated client; blindly closing it would terminate
            # the wrong connection.
            client.close()
            with self._lock:
                if self._tcp_client is client:
                    self._tcp_client = None
            log.info("[TCP] client disconnected")
            # TASK-0010: if the frontend crashed mid-capture (before
            # sending ``set_esc_cancel_paused: false``), the backend
            # would otherwise be stuck in ``"hotkey_capture"`` state
            # forever, suppressing all hotkey interactions until
            # restart. Reset keyboard ownership so the next client
            # reconnect starts clean. Skipped during server shutdown
            # so an active recording isn't interrupted by teardown.
            self._on_ipc_client_disconnect("IPC client disconnected")

    def _on_ipc_client_disconnect(self, reason: str) -> None:
        """Reset keyboard ownership when the IPC client disconnects.

        TASK-0010 (backend ownership watchdog): if the frontend
        crashes mid-capture (before sending
        ``set_esc_cancel_paused: false``), the backend would
        otherwise be stuck in ``"hotkey_capture"`` state forever,
        suppressing all hotkey interactions until restart. Resetting
        ownership here ensures the next client reconnect starts
        clean.

        Skipped during server shutdown (``self._running == False``)
        so an active recording isn't interrupted by the teardown
        sequence — we only want to fire on an *unexpected* client
        disconnect, not on a planned stop().

        The reset is idempotent: calling it when ownership is
        already ``"normal"`` is a no-op. Safe to call from multiple
        disconnect paths (TCP + stdin EOF) — the second call is a
        no-op.
        """
        if not self._running:
            # Server is shutting down (stop() was called). Don't
            # reset ownership — a recording might be in progress
            # and the teardown sequence will handle cleanup.
            log.debug("[IPC] client disconnect during shutdown; skipping keyboard ownership reset")
            return
        keyboard_ownership().reset()
        # ISSUE-8 / M-94: also clear the ESC-pending-capture-exit Event
        # on the hotkey dispatcher. If the frontend crashed mid-capture
        # (ESC pressed but not yet released), the flag would remain set
        # and cause a spurious ``hotkey_capture_cancel`` event on the
        # next ESC press after reconnect. M-94 replaced the plain bool
        # with a ``threading.Event`` so the 3 threads that touch this
        # flag (ESC listener / ESC release handler / this IPC worker)
        # cannot race on the read-modify-write cycle. ``.clear()`` is
        # atomic.
        _hotkeys = getattr(self.app, "hotkeys", None)
        if _hotkeys is not None:
            with contextlib.suppress(AttributeError):
                _hotkeys._esc_pending_capture_exit_event.clear()
        log.info("[IPC] keyboard ownership reset to normal (%s)", reason)

    # ── Heartbeat watchdog (RW-10) ───────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """RW-10: daemon thread that watches for Electron heartbeat timeouts.

        Wakes every ``_HEARTBEAT_INTERVAL_SECONDS`` (5s) and calls
        :meth:`_check_heartbeat_timeout`.  When the timeout fires
        (24 missed heartbeats = 120s without a heartbeat from Electron),
        the loop returns — ``app.quit()`` has already been triggered,
        which runs the shared ``_do_cleanup()`` path from RW-3
        (restores volume, flushes recovery, releases the mutex, closes
        PortAudio) and breaks the pystray loop so the process exits.

        The thread is a daemon so it doesn't block shutdown.  ``stop()``
        sets ``_heartbeat_stop_event`` to wake the thread immediately
        on a planned shutdown.
        """
        while not self._heartbeat_stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
            if self._check_heartbeat_timeout():
                return  # app.quit() was called; thread exits

    def _check_heartbeat_timeout(self) -> bool:
        """Return True and call ``app.quit()`` if the heartbeat is overdue.

        RW-10: extracted as a separate method so tests can invoke it
        directly without spinning up the daemon thread (and without
        waiting for the real-time 120s timeout to elapse).

        Returns ``True`` when ``app.quit()`` was called, ``False``
        otherwise.  The ``False`` cases are:

        - ``_last_heartbeat_at is None``: Electron has not yet sent
          its first heartbeat.  The watchdog must NOT fire here, or a
          slow Electron cold start (10+ seconds for the torch import)
          would cause a false-positive exit.
        - ``now - last <= _HEARTBEAT_TIMEOUT_SECONDS``: the most
          recent heartbeat is fresh enough; Electron is still alive.

        The ``True`` case calls ``self.app.quit()`` — which runs the
        shared ``_do_cleanup()`` cleanup path (RW-3) so the mic
        stream, hotkeys, volume duck, and single-instance mutex are
        properly released before the process exits.  ``app.quit()``
        also calls ``tray.stop()`` which breaks the pystray loop,
        letting ``app.start()`` return and the process exit naturally
        (quit() only calls ``sys.exit()`` from the main thread; from
        a daemon thread it relies on tray.stop() to unwind the main
        loop).

        CR-9: if ``tray.stop()`` hangs (observed on certain Linux
        backends + Windows Server), the daemon thread scheduled here
        force-exits the process via ``os._exit(1)`` after a 10-second
        grace period. See the inline comment in the ``True`` branch.
        """
        last = self._last_heartbeat_at
        if last is None:
            # No heartbeat yet — Electron hasn't connected.  Don't
            # fire.  This is the critical guard that prevents a false
            # positive during a slow Electron cold start.
            return False
        now = time.monotonic()
        if now - last <= _HEARTBEAT_TIMEOUT_SECONDS:
            return False
        log.warning(
            "[HEARTBEAT] No heartbeat from Electron in %.1fs (>%0.1fs) "
            "— backend will quit (Electron likely crashed or was "
            "force-killed)",
            now - last,
            _HEARTBEAT_TIMEOUT_SECONDS,
        )
        try:
            self.app.quit()
        except Exception:
            log.exception("[HEARTBEAT] app.quit() raised during heartbeat timeout")

        # CR-9: force-exit fallback if ``tray.stop()`` hangs.
        #
        # ``app.quit()`` from a daemon thread relies on
        # ``tray.stop()`` breaking the pystray loop so ``app.start()``
        # returns and the process exits naturally (``quit()`` only
        # calls ``sys.exit(0)`` from the main thread). pystray on
        # certain Linux backends (AppIndicator with stale dbus) and on
        # Windows Server (with RDP session disconnects) has been
        # observed to hang inside ``stop()`` — leaving the process
        # stuck with the mic open and the single-instance mutex held.
        #
        # Mitigation: schedule a daemon thread that sleeps 10 seconds
        # (grace period for ``quit()`` to unwind naturally), then calls
        # ``os._exit(1)``. If ``quit()`` succeeded, the process is
        # already gone before the grace period expires — the daemon
        # thread is reaped by the OS. If ``quit()`` hung, the daemon
        # thread force-exits the process after 10s.
        #
        # ``os._exit`` (not ``sys.exit``) bypasses Python's normal
        # shutdown sequence (no atexit handlers, no finally blocks) —
        # appropriate here because the graceful ``_do_cleanup()`` path
        # already ran inside ``app.quit()`` above. We use ``os._exit(1)``
        # (non-zero) so the host's FT-1 supervisor treats this as a
        # crash and respawns with backoff, rather than silently exiting
        # and looking like a clean shutdown.
        try:
            import threading as _threading

            def _force_exit_after_grace() -> None:
                # 10-second grace period (default; constant is patchable
                # for tests). Must be longer than the slowest legitimate
                # quit() path — PortAudio stream teardown + history DB
                # flush + mutex release ≈ 2-3s in the worst observed case.
                time.sleep(_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS)
                log.error(
                    "[HEARTBEAT] app.quit() did not exit within %ds — "
                    "force-exiting via os._exit(1) (tray.stop() likely hung)",
                    int(_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS),
                )
                os._exit(1)

            _threading.Thread(
                target=_force_exit_after_grace,
                name="heartbeat-force-exit",
                daemon=True,
            ).start()
        except Exception:
            log.exception(
                "[HEARTBEAT] failed to schedule force-exit watchdog — process may hang if tray.stop() is stuck"
            )
        return True

    def _handle_heartbeat(self, data, resp) -> dict:
        """Handle the ``heartbeat`` IPC command (RW-10).

        Electron's main process sends this every 5 seconds (see
        ``client/src/main/index.ts``) once the TCP connection is
        established.  The handler updates ``_last_heartbeat_at`` so
        the :meth:`_heartbeat_loop` daemon thread knows Electron is
        still alive.

        The response is a trivial ``heartbeat_ack`` — Electron does
        not act on it (the heartbeat is fire-and-forget), but
        returning a well-formed response keeps the IPC dispatcher's
        ``result.setdefault('data', {})`` path happy and lets
        ``sendToPython()`` resolve its promise instead of timing out.
        """
        self._last_heartbeat_at = time.monotonic()
        resp["type"] = "heartbeat_ack"
        return resp

    def _handle_relaunch_ack(self, data, resp) -> None:
        """PERF-005: Electron ack that it has received and is processing the
        ``relaunch_electron`` request.

        ``restart_app`` waits on ``self._relaunch_ack_event`` (bounded by a
        2s timeout) instead of a fixed ``time.sleep(0.3)``, so the tray
        thread is unblocked as soon as Electron acks — rather than always
        blocking 300ms.  The handler returns ``None`` (no response body):
        restart_app owns the socket teardown, and any response write races
        the imminent shutdown, so there is nothing meaningful to return.
        """
        self._relaunch_ack_event.set()
        return None

    # ── Tray state hook ─────────────────────────────────────────────────

    def _hook_tray_set_state(self) -> None:
        """Monkey-patch ``app.tray.set_state`` to emit push events.

        Every call to ``set_state`` will also send a ``status_change``
        push event with the new state value.

        Idempotent: guarded so a ``start()`` → ``stop()`` → ``start()``
        cycle (common in tests and possible during restart) does not
        stack another wrapper on top of an already-wrapped
        ``set_state``. Without the guard, each state change would emit
        N ``status_change`` events after N start cycles.
        """
        # Already wrapped on a prior start() — leave the existing
        # wrapper in place so push events stay deduplicated.
        if getattr(self.app.tray.set_state, "_vt_wrapped", False):
            return

        original = self.app.tray.set_state

        def wrapped(state, message=""):
            original(state, message)
            self.push(
                {
                    "type": "status_change",
                    "data": {"status": state.value},
                }
            )

        wrapped._vt_wrapped = True
        self.app.tray.set_state = wrapped

    # ── Main loop (stdin, legacy) ──────────────────────────────────────

    def _run(
        self,
        _stdin=None,
        _stdout=None,
    ) -> None:
        """Read JSON lines from stdin, dispatch, write responses to stdout.

        Parameters
        ----------
        _stdin : Optional[TextIO]
            Input stream (default ``sys.stdin``).  Provided for testing.
        _stdout : Optional[TextIO]
            Output stream (default ``sys.stdout``).  Provided for testing.
        """
        stdin = _stdin or sys.stdin
        stdout = _stdout or sys.stdout
        try:
            iterator = iter(stdin)
        except OSError:
            return  # stdin not available (e.g. during testing)
        try:
            for line in iterator:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    # CR-31: validate that the parsed JSON is a dict
                    # before dispatch. ``_dispatch`` calls
                    # ``msg.get("type")`` which raises ``AttributeError``
                    # if ``msg`` is a list/int/str/None (all valid JSON).
                    # Previously the ``except json.JSONDecodeError`` did
                    # NOT catch ``AttributeError``, so a single non-dict
                    # JSON line on stdin killed the IPC thread silently
                    # (keyboard ownership was not reset, app became
                    # unresponsive with no diagnostic). The TCP path was
                    # hardened by ERR-018 but the stdin path was not
                    # updated in lockstep.
                    if not isinstance(msg, dict):
                        self._send(
                            {
                                "type": "error",
                                "data": {
                                    "code": "invalid_payload",
                                    "message": "message must be a JSON object",
                                },
                            },
                            _out=stdout,
                        )
                        continue
                    result = self._dispatch(msg)
                    self._send(result, _out=stdout)
                except json.JSONDecodeError:
                    # IPC-5 note: the TCP path now emits
                    # ``{"code": "invalid_payload", "message": "invalid JSON"}``
                    # to match the WS path (see ``_handle_tcp_connection``).
                    # The stdin/stdout (legacy console) path is
                    # intentionally left WITHOUT the ``code`` field to
                    # preserve backward compatibility with the
                    # existing ``test_handles_invalid_json`` contract
                    # in ``tests/test_server.py`` (which asserts the
                    # bare ``{"message": "invalid JSON"}`` envelope).
                    # The stdin path is not in the IPC-5 parity scope
                    # (the directive only mentions TCP vs WS); a
                    # future task may align all three paths.
                    self._send(
                        {
                            "type": "error",
                            "data": {"message": "invalid JSON"},
                        },
                        _out=stdout,
                    )
                except Exception as dispatch_exc:
                    # CR-31: mirror the TCP path's ERR-018 hardening —
                    # catch ANY exception from ``_dispatch`` so a
                    # handler bug doesn't silently kill the stdin
                    # thread. Log server-side with traceback; return a
                    # generic ``internal_error`` envelope to the client.
                    log.error(
                        "[IPC] stdin dispatch failed for line=%r: %s",
                        line[:120],
                        dispatch_exc,
                        exc_info=True,
                    )
                    self._send(
                        {
                            "type": "error",
                            "data": {
                                "code": "internal_error",
                                "message": "internal error",
                            },
                        },
                        _out=stdout,
                    )
        except OSError:
            pass  # stdin closed (e.g. during test teardown)
        # TASK-0010: stdin EOF (or OSError on read) means the IPC
        # client is gone. If we're still running, reset keyboard
        # ownership so a crashed CLI client doesn't leave the
        # backend stuck in ``"hotkey_capture"`` state. The helper
        # is a no-op during shutdown (``self._running == False``).
        self._on_ipc_client_disconnect("stdin EOF — IPC client disconnected")

    # ── Dispatcher ──────────────────────────────────────────────────────

    def _dispatch(self, msg: dict) -> dict | None:
        """Route a command and return the response dict.

        Returns ``None`` for commands that send their response internally
        (e.g. ``restart_app`` / ``quit_app`` which kill the process).

        REFACTOR: previously this was a 54-branch if/elif chain spanning
        ~880 lines. It is now a single dict lookup against
        ``_COMMAND_REGISTRY``, with each command implemented as a
        dedicated ``_handle_<cmd>`` method. This improves:
          - Testability: each handler can be unit-tested in isolation.
          - Readability: the dispatch logic is one screen, not 20.
          - Maintainability: adding a command is one method + one
            registry entry, not inserting into a giant elif chain.
        The handler bodies are identical to the old elif blocks -- this
        is a mechanical refactor with zero behavior change.
        """
        # PVT-G5-004: cooperative shutdown gate. When the app is shutting
        # down (``app._shutting_down is True``), reject all NEW dispatch
        # requests with a structured ``shutting_down`` error so the client
        # can stop retrying and tear down cleanly. ``is True`` (rather than
        # a truthiness check) mirrors the existing ``_send`` shutdown-
        # suppress gate (line ~2166) so MagicMock-based test fixtures —
        # which expose ``_shutting_down`` as a child mock that is truthy
        # but not ``is True`` — keep exercising the dispatch path instead
        # of short-circuiting here.
        if getattr(self.app, "_shutting_down", False) is True:
            err: dict[str, object] = {
                "type": "error",
                "data": {
                    "code": "shutting_down",
                    "message": "server is shutting down",
                },
            }
            if isinstance(msg, dict) and "id" in msg:
                err["id"] = msg["id"]
            return err

        cmd = msg.get("type")
        data = msg.get("data")
        resp = {"id": msg.get("id")} if "id" in msg else {}

        # RW-13: propagate the inbound request id as a correlation id for
        # the duration of this dispatch.  Every log emitted by a handler
        # (and any code it calls synchronously) now carries
        # correlation_id=<request id>, so a client's request and all the
        # server-side log lines it triggered can be tied together in a
        # JSON log backend without threading the id through every call.
        # The token is reset in the ``finally`` below so concurrent
        # requests (each on its own call to _dispatch) don't leak ids
        # into one another.  ``msg.get("id")`` may be None/absent for
        # fire-and-forget notifications — in that case no correlation id
        # is set and logs fall back to the no-correlation schema.
        _corr_token = None
        _req_id = msg.get("id") if isinstance(msg, dict) else None
        if _req_id is not None:
            from voice_typer.server.log import set_correlation_id

            _corr_token = set_correlation_id(str(_req_id))
        # RW-6 (pyrefly): ``_COMMAND_REGISTRY`` is typed ``dict[str, str]``
        # and ``dict.get`` requires a ``str`` key. ``msg.get("type")``
        # returns ``Unknown | None`` because the inbound JSON dict has no
        # static value-type, so the lookup below would be flagged
        # ``bad-argument-type``. Coerce to ``str`` here so the registry
        # lookup type-checks cleanly; the unknown-command path still
        # receives the original value (including ``None``) for the error
        # message, preserving the previous wire behaviour.
        cmd_key = cmd if isinstance(cmd, str) else ""
        try:
            handler_name = self._COMMAND_REGISTRY.get(cmd_key)
            if handler_name is None:
                result = self._handle_unknown_command(cmd, data, resp)
            else:
                handler = getattr(self, handler_name)
                result = handler(data, resp)
        finally:
            if _corr_token is not None:
                from voice_typer.server.log import reset_correlation_id

                reset_correlation_id(_corr_token)

        # NEW-IPC-006: ensure every response has a `data` field so the
        # client can always read `resp.data` without a defensive guard.
        # Commands that return None (restart_app/quit_app) send their
        # response internally and skip this.
        if result is not None:
            result.setdefault("data", {})

        return result

    # Command registry: maps IPC command name to handler method.
    # Built once at class definition time; _dispatch does a single dict lookup.
    # Each handler takes (data, resp) and returns resp (to send) or None
    # (for commands that send their response internally, like restart_app).
    #
    # IPC-1 reconciliation (2026-07-18): the registry contains exactly 69
    # commands. The 67 "domain" handlers live in voice_typer/server/handlers/
    # (one mixin module per domain). The remaining two — `heartbeat` (RW-10,
    # ADR-0018 Electron-alive watchdog) and `relaunch_ack` (PERF-005, ack of
    # `relaunch_electron` so `restart_app` can drop its fixed 300 ms sleep) —
    # are resident on IPCServer itself because they touch IPC-server-owned
    # state (`_last_heartbeat_at`, `_relaunch_ack_event`) and don't belong to
    # any domain mixin. The earlier "68 commands" claim in ADR-0020 §2 was
    # stale; `relaunch_ack` was added by PERF-005 after the original count.
    _COMMAND_REGISTRY: dict[str, str] = {
        "get_status": "_handle_get_status",
        "toggle_dictation": "_handle_toggle_dictation",
        "undo_last": "_handle_undo_last",
        # UX-23: re-paste the last transcription (repaste_handlers mixin).
        "repaste_last": "_handle_repaste_last",
        "get_config": "_handle_get_config",
        "get_defaults": "_handle_get_defaults",
        "set_config": "_handle_set_config",
        "get_history": "_handle_get_history",
        "get_today_stats": "_handle_get_today_stats",
        "delete_history": "_handle_delete_history",
        "restore_history": "_handle_restore_history",
        "clear_history": "_handle_clear_history",
        "toggle_favorite": "_handle_toggle_favorite",
        "get_favorites": "_handle_get_favorites",
        "search_history": "_handle_search_history",
        "get_microphones": "_handle_get_microphones",
        "refresh_microphones": "_handle_refresh_microphones",
        "get_rms_level": "_handle_get_rms_level",
        "get_volume_backend_status": "_handle_get_volume_backend_status",
        "get_audio_status": "_handle_get_audio_status",
        "get_model_status": "_handle_get_model_status",
        # ADR-0009 Issue 3: prewarm cache status (Hot/Partial/Cold label,
        # cache ratio, last-run timestamp, elapsed seconds) for the About
        # page's "Cache Status" card.
        "get_prewarm_status": "_handle_get_prewarm_status",
        # Task 3: manually trigger a prewarm run (force=True) from the
        # About page's "Run Prewarm Now" button. Spawns a detached
        # subprocess; the frontend polls get_prewarm_status to track it.
        "run_prewarm": "_handle_run_prewarm",
        # Task 2: open the prewarm log file in the OS default text editor
        # from the About page's "View prewarm log" button.
        "open_prewarm_log": "_handle_open_prewarm_log",
        "get_vocabulary": "_handle_get_vocabulary",
        "save_vocabulary": "_handle_save_vocabulary",
        "get_templates": "_handle_get_templates",
        "save_templates": "_handle_save_templates",
        "restart_app": "_handle_restart_app",
        "quit_app": "_handle_quit_app",
        "onboarding_is_first_run": "_handle_onboarding_is_first_run",
        "onboarding_start": "_handle_onboarding_start",
        "onboarding_get_step": "_handle_onboarding_get_step",
        "onboarding_next_step": "_handle_onboarding_next_step",
        "onboarding_prev_step": "_handle_onboarding_prev_step",
        "onboarding_set_microphone": "_handle_onboarding_set_microphone",
        "onboarding_set_hotkey": "_handle_onboarding_set_hotkey",
        "onboarding_set_model": "_handle_onboarding_set_model",
        "onboarding_skip": "_handle_onboarding_skip",
        "onboarding_apply": "_handle_onboarding_apply",
        "onboarding_get_microphones": "_handle_onboarding_get_microphones",
        "onboarding_get_model_options": "_handle_onboarding_get_model_options",
        "onboarding_get_model_catalog": "_handle_onboarding_get_model_catalog",
        "onboarding_get_hotkey_presets": "_handle_onboarding_get_hotkey_presets",
        # UX-4 / UX-27: platform-conditional permission probe
        # (macOS Accessibility / Linux input group + udev rule) used by
        # the Permissions step.
        "onboarding_check_permissions": "_handle_onboarding_check_permissions",
        # Onboarding keyboard-permission request + wizard reset.
        # The handlers live in ``handlers/onboarding_handlers.py`` and
        # were wired up here per the SK sub-agent's _COMMAND_REGISTRY
        # cross-area note. Without this registration the renderer's
        # invoke calls returned ``unknown_command``.
        "onboarding_request_keyboard_permission": "_handle_onboarding_request_keyboard_permission",
        "onboarding_reset": "_handle_onboarding_reset",
        "microphone_test_start": "_handle_microphone_test_start",
        "microphone_test_stop": "_handle_microphone_test_stop",
        "microphone_test_cancel": "_handle_microphone_test_cancel",
        "microphone_test_status": "_handle_microphone_test_status",
        "microphone_test_get_level": "_handle_microphone_test_get_level",
        "level_monitor_start": "_handle_level_monitor_start",
        "level_monitor_stop": "_handle_level_monitor_stop",
        "level_monitor_status": "_handle_level_monitor_status",
        "import_model": "_handle_import_model",
        "download_model": "_handle_download_model",
        "cancel_model_download": "_handle_cancel_model_download",
        # NEW-PAUSE-001: pause/resume in-progress model downloads.
        "pause_model_download": "_handle_pause_model_download",
        "resume_model_download": "_handle_resume_model_download",
        # NEW-MODEL-001: full model catalog (rich metadata for the
        # Models page: VRAM, languages, speed/accuracy ratings).
        "get_model_catalog": "_handle_get_model_catalog",
        "test_llm_connection": "_handle_test_llm_connection",
        "delete_model": "_handle_delete_model",
        "export_diagnostics": "_handle_export_diagnostics",
        "check_accessibility": "_handle_check_accessibility",
        "set_tray_locale": "_handle_set_tray_locale",
        "show_electron_notification": "_handle_show_electron_notification",
        # ESC-FIX-001: pause/resume the global ESC cancel hotkey so the
        # frontend (HotkeyPicker in hotkey capture mode) can temporarily
        # disable it, preventing the backend from processing Escape while
        # the UI is capturing a custom hotkey.
        "set_esc_cancel_paused": "_handle_set_esc_cancel_paused",
        # P5: vocabulary automation — confidence-score-based correction
        # suggestions.  See ``vocabulary_automation_handlers.py``.
        "get_vocabulary_suggestions": "_handle_get_vocabulary_suggestions",
        "apply_vocabulary_suggestion": "_handle_apply_vocabulary_suggestion",
        "dismiss_vocabulary_suggestion": "_handle_dismiss_vocabulary_suggestion",
        # PR-2 Finding #3: force-cancel a stuck transcription.  Invokes
        # ``_force_recover_from_stuck_transcription(force=True)`` to reset
        # the busy flag and tray state immediately, bypassing the normal
        # 3×90s watchdog timeout.
        "force_cancel_transcription": "_handle_force_cancel_transcription",
        # RW-10: Electron-alive heartbeat.  Electron's main process
        # sends this every 5 seconds; the backend's heartbeat-watchdog
        # daemon thread calls ``app.quit()`` if 24 consecutive heartbeats
        # are missed (120s timeout) so a crashed/force-killed Electron
        # doesn't strand the backend with the mic open + mutex held.
        "heartbeat": "_handle_heartbeat",
        # PERF-005: Electron acks receipt/processing of ``relaunch_electron``
        # so restart_app can drop its fixed 300ms sleep in favour of an
        # event-driven wait (bounded by a 2s timeout).
        "relaunch_ack": "_handle_relaunch_ack",
        # ADR-0020 §6.5 / §16: Tauri sidecar tray-menu click dispatch.
        # The Tauri host forwards a clicked menu item id; the backend looks
        # it up in the tray's id→callback map and invokes the action. Unknown
        # ids return a structured ``unknown_tray_item`` error (distinct from
        # ``unknown_command``) so the host can surface "missing item" vs
        # "unknown command" differently.
        "tray_click": "_handle_tray_click",
        # CR-009 / Fix-A (IMPROVE-mode run, 2026-07-21): GDPR Art. 17 (right
        # to erasure) and Art. 20 (right to data portability) handlers.
        # Registered by PrivacyHandlersMixin; service methods live on
        # VoiceTyperService (delete_all_personal_data / export_gdpr_bundle).
        "delete_all_personal_data": "_handle_delete_all_personal_data",
        "export_gdpr_bundle": "_handle_export_gdpr_bundle",
    }

    def _handle_tray_click(self, data, resp) -> dict:
        """ADR-0020 §6.5 / §16: dispatch a Tauri tray-menu click by item id.

        Looks the clicked ``id`` up via the tray's ``dispatch_tray_action``
        and returns ``{"ok": True}`` on success.  A missing ``id`` yields a
        ``missing_field`` error; an id the tray doesn't recognise yields a
        distinct ``unknown_tray_item`` error (so the host can tell "malformed
        request" from "item not found").

        CR-12: validation is delegated to the shared
        ``_validate_dict_payload`` helper (the contract source of truth)
        rather than an inline ``isinstance`` check, so the error envelope
        (``invalid_payload`` / ``invalid_field`` / ``missing_field``)
        matches every other handler in the codebase.
        """
        validated, error = _validate_dict_payload(
            data,
            {
                "id": {"type": str, "required": True},
            },
        )
        if error:
            return error

        item_id = validated["id"]
        tray = getattr(self.app, "tray", None)
        if tray is None or not hasattr(tray, "dispatch_tray_action"):
            resp["type"] = "error"
            resp["data"] = {"code": "unknown_tray_item", "id": item_id}
            return resp

        handled = tray.dispatch_tray_action(item_id)
        if not handled:
            resp["type"] = "error"
            resp["data"] = {"code": "unknown_tray_item", "id": item_id}
            return resp

        return {"type": "result", "data": {"ok": True}}

    def _handle_unknown_command(self, cmd, data, resp) -> dict | None:
        """Handle the ``__unknown__`` IPC command."""
        resp["type"] = "error"
        # ERR-009: include a structured `code` field so clients can
        # distinguish "unknown command" (caller bug / version skew)
        # from "command handler raised" (server-side fault). The
        # previous payload only had a free-text `message`, which
        # forced clients to substring-match the message to tell
        # the two cases apart.
        resp["data"] = {
            "code": "unknown_command",
            "message": f"Unknown command: {cmd}",
            "command": cmd,
        }
        return resp

    # ── Output ──────────────────────────────────────────────────────────

    def push(self, msg: dict) -> None:
        """Send an unsolicited event (no ``id`` field)."""
        self._send(msg)

    def _send(self, msg: dict | None, _out=None, _client=None) -> None:
        """Serialize *msg* and write it to the active transport.

        NEW-IPC-014 / NEW-CONC-001 / NEW-CONC-003: previously the entire
        send path (json.dumps + sendall + pending drain) ran under
        ``self._lock``, which meant:

        - Every other IPC dispatch command blocked while a slow Electron
          renderer drained its TCP receive buffer (NEW-CONC-001).
        - The audio-callback-spawned bubble_level worker could stall
          inside ``sendall`` with no timeout, holding the lock and
          stalling every other dispatch path (NEW-CONC-003).
        - ``Microphone.tsx::testMicrophone → get_microphones`` saw
          user-visible lag during recording (NEW-CONC-001 details).

        The fix splits the work:
        1. Under the lock: snapshot the current client / mode / pending
           list.  This is the only section that needs mutual exclusion.
        2. Outside the lock: serialize the message, perform the actual
           ``sendall`` (with a write timeout — NEW-CONC-003), and drain
           the pending list.  A slow client can no longer block other
           dispatchers.

        PVT-G5-011: the optional ``_client`` parameter lets a TCP
        dispatch loop write its response to the LOCAL client it
        authenticated (captured at the top of the loop) rather than
        ``self._tcp_client`` — which may have been reassigned to a
        newer connection by a concurrent fast-auth client (SEC-8 race).
        Defaults to ``None`` (fall back to ``self._tcp_client``) so the
        push-event path (``server.push()``) and existing call sites are
        backward-compatible.
        """
        if msg is None:
            return

        # Step 1: snapshot transport state under the lock.  This is fast
        # (no I/O) and is the only section that needs mutual exclusion.
        with self._lock:
            out = _out
            # PVT-G5-011: prefer the caller-provided local client (the
            # one this dispatch loop authenticated) over ``self._tcp_client``
            # (which a concurrent fast-auth reconnect may have replaced).
            # ``_client`` defaults to ``None`` for the push-event path.
            tcp_client = _client if _client is not None else self._tcp_client
            tcp_mode = self._tcp_mode
            # Snapshot and clear the pending list atomically.  Anything
            # pushed between this snapshot and the actual send will be
            # picked up by the NEXT _send call (or this one's drain
            # loop, since we re-check after each write).
            pending = list(self._pending_tcp) if self._pending_tcp else None
            if pending:
                self._pending_tcp.clear()

        # Step 2: serialize + write OUTSIDE the lock.  A slow client can
        # stall here without blocking other dispatchers.
        line = json.dumps(msg)

        if out is not None:
            # Stdin/stdout mode — used in tests and the legacy console
            # script.  Writes to a TextIO are typically fast (pipe to
            # Electron parent), but still don't need the lock.
            out.write(line + "\n")
            out.flush()
            return

        if tcp_client is not None:
            # QUIT-CLEAN-001: if the app is shutting down, skip the TCP
            # write for non-critical events.  Electron closes its end of
            # the socket as soon as it receives the ``quit_app`` event;
            # any subsequent push from the cleanup path (waveform bubble
            # worker, state-changed hooks, hotkey-backend teardown) would
            # hit a half-closed socket and raise ``[WinError 10053]```.
            #
            # CRITICAL-CRITICAL: the ``relaunch_app`` event is the
            # EXCEPTION.  This event MUST be delivered even during
            # shutdown because it's the signal from restart_app() that
            # tells the host (Tauri ``app.restart()`` / Electron
            # ``app.relaunch() + app.exit(0)``) to relaunch before
            # the Python process exits.  Without it, the restart hangs.
            # PVT-2 cleanup: the published event name is ``relaunch_app``
            # (no longer ``relaunch_electron``); the Rust WS bridge no
            # longer renames it.
            #
            # ``is True`` (rather than a truthiness check) is intentional:
            # the real ``VoiceTyperApp`` sets ``_shutting_down = True``
            # literally, and MagicMock-based test fixtures expose
            # ``_shutting_down`` as a child mock (which is truthy but
            # not ``is True``).  Using ``is True`` keeps the test path
            # exercising the write logic instead of the shutdown short-
            # circuit.
            _is_shutting_down = getattr(self.app, "_shutting_down", False) is True
            msg_type = msg.get("type", "")
            # Allow critical shutdown events through; suppress others.
            # PR-2-FIX-2: expanded allowlist to include content-bearing events
            # that the user is waiting for. transcription_final carries the
            # final transcription text — if it's suppressed during shutdown,
            # the user sees no result on the Home page and perceives data loss
            # (the data IS saved to history_db, but the UI never updates).
            # transcription_partial and vocabulary_suggestion are similarly
            # content-bearing. High-frequency events (bubble_level, audio_level)
            # are still suppressed.
            _shutdown_allowlist = (
                "relaunch_app",
                "quit_app",
                "transcription_final",
                "transcription_partial",
                "vocabulary_suggestion",
            )
            # PVT-G5-013: dispatch responses (which carry an ``id`` field)
            # MUST be exempted from the shutdown suppress — otherwise the
            # client waits forever for a response to an in-flight request
            # that the server has already processed. Only push events
            # (no ``id``) are suppressed; they are replayed via state
            # snapshots on reconnect.
            if _is_shutting_down and "id" not in msg and msg_type not in _shutdown_allowlist:
                with self._lock:
                    if self._tcp_client is tcp_client:
                        with contextlib.suppress(Exception):
                            self._tcp_client.close()
                        self._tcp_client = None
                return
            # NEW-CONC-003: set a write timeout so a stalled renderer
            # can't block the worker thread indefinitely.  2 seconds is
            # generous for a localhost TCP write — under normal load the
            # kernel buffer accepts the data immediately.  If we hit the
            # timeout, the write raises ``socket.timeout`` and we drop
            # the connection (the accept loop will catch the next
            # reconnect).  We restore the PREVIOUS timeout afterwards
            # rather than forcing blocking mode: the auth read set a
            # deadline (PR-3-FIX-1) and we must not clobber it to
            # ``None`` (blocking), or the dispatch-loop ``readline`` would
            # block forever and the connection could never be reaped/
            # closed on cleanup (SEC-018 auth-timeout/close path).
            #
            # Write-serialization: the entire settimeout → write →
            # flush → drain → restore-timeout block runs under
            # ``self._tcp_write_lock``. ``socket.sendall`` releases
            # the GIL between ``send()`` syscalls (when the kernel
            # send buffer is full), so two concurrent ``sendall``
            # calls on the same socket CAN interleave their bytes at
            # the kernel send-buffer level, corrupting the JSON-lines
            # protocol. The dedicated write lock (separate from
            # ``self._lock``, which guards only the snapshot phase)
            # serializes ONLY writers — a slow client blocks other
            # writers, but not other dispatchers' snapshots or the
            # read path. The 2s write timeout bounds the stall.
            # Holding the lock across settimeout/restore also
            # prevents a race where two threads clobber each other's
            # timeout (one restores ``None`` while another is
            # mid-write, blocking the writer forever).
            with self._tcp_write_lock:
                _prev_timeout = tcp_client.conn.gettimeout()
                with contextlib.suppress(OSError, AttributeError):
                    tcp_client.conn.settimeout(_TCP_WRITE_TIMEOUT_SECONDS)
                # settimeout can fail if the socket is already closed;
                # that's fine — the write below will also fail and we'll
                # drop the connection cleanly.
                try:
                    tcp_client.write(line + "\n")
                    tcp_client.flush()
                    # PERF-NEW-014 / SEC-008: drain at most the most recent
                    # K pending entries, not the whole list.  When the
                    # client was disconnected for a while, _pending_tcp
                    # could have grown to thousands of entries (16 Hz
                    # waveform bubble * minutes of disconnect).  Draining
                    # all of them on every push event was O(n) per push
                    # and blocked the audio thread.
                    _drain_cap = 100
                    if pending:
                        # Already snapshot under lock — drain up to _drain_cap
                        # of the most recent entries.
                        recent = pending[-_drain_cap:]
                        for p in recent:
                            try:
                                tcp_client.write(p + "\n")
                                tcp_client.flush()
                            except Exception:
                                log.debug("[IPC] client write failed during pending drain")
                                break
                except (TimeoutError, OSError) as exc:
                    log.debug("[IPC] client write failed: %s", exc)
                    # Mark the client as dead so the accept loop will pick
                    # up the next reconnect.  We do this under the lock to
                    # avoid a race with a concurrent _send that just
                    # snapshotted the (now-dead) client.
                    with self._lock:
                        if self._tcp_client is tcp_client:
                            with contextlib.suppress(Exception):
                                self._tcp_client.close()
                            self._tcp_client = None
                finally:
                    # Restore the previous timeout (NOT blocking ``None``) so
                    # the dispatch-loop ``readline`` keeps its auth deadline
                    # and the worker can exit/be reaped on cleanup.  Setting
                    # ``None`` here was the root cause of the
                    # auth-timeout/close deadlock (CR-2): a blocking socket
                    # could never time out, so the reader thread never exited
                    # and ``_TCPLineIO.close()`` deadlocked against the
                    # in-progress ``recv``.
                    with contextlib.suppress(OSError, AttributeError):
                        tcp_client.conn.settimeout(_prev_timeout)
            return

        if tcp_mode:
            # SEC-008: cap _pending_tcp to prevent unbounded
            # memory growth while the client is disconnected.
            # When the cap is hit, drop the OLDEST entries
            # (waveform bubble level events are stale by the
            # time the client reconnects; transcription-complete
            # events are also in history_db).
            _pending_cap = 1000
            with self._lock:
                # Re-merge any pending we snapshot earlier (they belong
                # before this new message in the queue).
                if pending:
                    self._pending_tcp.extend(pending)
                self._pending_tcp.append(line)
                if len(self._pending_tcp) > _pending_cap:
                    dropped = len(self._pending_tcp) - _pending_cap
                    del self._pending_tcp[:dropped]
                    cap_dropped = dropped
                else:
                    cap_dropped = 0
            if cap_dropped:
                log.warning(
                    "[IPC] _pending_tcp cap exceeded; dropped %d old entries",
                    cap_dropped,
                )
            return

        # No IPC client connected.  Two scenarios:
        #
        # 1. Console mode: the user ran ``voice-typer`` (or
        #    ``python -m voice_typer.server.ipc_server`` without
        #    ``--port``) for diagnosis.  Previously push events
        #    were silently dropped here at DEBUG level, making
        #    the console session useless for observing state
        #    changes / errors / background-audio events.
        #    NEW-IPC-008: surface these at INFO level so the
        #    user can actually see what the app is doing.
        #
        # 2. Brief disconnect during normal Electron use: the
        #    client is reconnecting.  INFO-level logging here
        #    is mildly noisy but bounded — the rate of push
        #    events is dominated by waveform bubbles which are
        #    already capped by the audio callback.  Acceptable
        #    trade-off vs. diagnostic value.
        msg_type = msg.get("type", "unknown")
        # Waveform bubble level events are very high frequency
        # (15-50 Hz).  Keep them at DEBUG to avoid flooding the
        # log; everything else goes to INFO so the user can
        # see state changes, errors, etc.
        if msg_type in ("bubble_level", "waveform"):
            # G4-M-28: bubble_level / waveform are emitted at 15-50 Hz
            # by the audio worker; even DEBUG-level flooding here can
            # saturate a slow disk's log buffer. Rate-limit to every
            # 100th occurrence (matches the ipc-no-client-drop INFO
            # gate below) so a sustained no-client condition during
            # recording doesn't drown the log.
            log_rate_limited(
                log,
                logging.DEBUG,
                "[IPC] no client; dropping high-freq %s event",
                msg_type,
                key="ipc-no-client-drop-high-freq",
                every_n=100,
            )
        else:
            # CR-8: never log the message body — push events include
            # transcription text (``transcription_partial`` /
            # ``transcription_final``) which is user PII.  Log only the
            # type and a size hint so the operator can see drop rate
            # without leaking dictated content to the log file.
            #
            # G4-M-28: a disconnected Electron client during a
            # transcription (mic still recording, hotkeys still firing)
            # produces a steady stream of push events. The previous
            # unconditional ``log.info`` per drop could emit thousands
            # of lines per minute — saturating the rotating log handler
            # and obscuring genuine errors. Rate-limit to the 1st and
            # every 100th occurrence; suppressed occurrences go to
            # DEBUG with a "(suppressed occurrence N)" suffix so they
            # remain visible when debug-level logging is enabled.
            log_rate_limited(
                log,
                logging.INFO,
                "[IPC] no client; dropping %s event (size=%d)",
                msg_type,
                len(str(msg)),
                key="ipc-no-client-drop",
                every_n=100,
            )


# ── Entry point ─────────────────────────────────────────────────────────


def _set_process_metadata() -> None:
    """Set process-level metadata (console title, AppUserModelID, etc.).

    BRAND-METADATA: On Windows the Python backend appears as a generic
    pythonw.exe in Task Manager.  We call the platform helper to set
    the console title and AppUserModelID, which improves the process
    identity wherever the OS supports it.
    """
    from voice_typer.server.branding import APP_NAME
    from voice_typer.server.platform_utils import _set_windows_process_metadata

    _set_windows_process_metadata(APP_NAME)


def main() -> None:
    """Create a ``VoiceTyperApp``, wrap it in an ``IPCServer``, and block.

    Designed as the subprocess entry point for an Electron frontend::

        python -m voice_typer.server.ipc_server          # stdin/stdout
        python -m voice_typer.server.ipc_server --port N  # TCP

    In TCP mode, stdout/stderr are NOT piped (Electron uses
    ``stdio: "inherit"``) so there is no pipe-backpressure issue
    during the heavy torch import.  Push events reach the frontend
    via TCP, and the terminal sees normal log output.
    """
    # BRAND-METADATA: set process metadata early, before any subsystem
    # init, so the OS sees the correct identity from the start.
    _set_process_metadata()

    # NEW-CLI-003: import the standardized exit-code constants. Both
    # EXIT_BAD_ARGS (bad --port) and EXIT_CRASH (uncaught exception in
    # app.start()) are used below; previously EXIT_CRASH was imported
    # but unused and the crash path called sys.exit with a raw literal.
    from voice_typer.__main__ import EXIT_BAD_ARGS, EXIT_CRASH
    # The canonical-name registration (``sys.modules[_CANONICAL]``)
    # is handled at module level, before the mixin imports, so it
    # applies to ALL execution modes (__main__, -m, and direct import).

    # RACE-018: Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    try:
        import faulthandler

        faulthandler.enable()
        # Optional: register SIGUSR1 for on-demand thread dumps (POSIX only)
        import signal

        if hasattr(signal, "SIGUSR1"):
            # TASK-14: ``faulthandler.dump_traceback_later`` has the
            # signature ``(timeout: float, repeat: bool = False, ...)
            # -> None`` and does NOT match the ``signal.signal`` handler
            # protocol ``(signum: int, frame: FrameType | None) -> Any``.
            # Passing it directly would crash with TypeError the first
            # time the signal fires (missing ``timeout`` positional).
            # Wrap it in a closure that calls ``dump_traceback_later``
            # with a 1-second delay — the documented use case for
            # on-demand thread dumps from SIGUSR1.
            def _on_sigusr1(_signum: int, _frame: "typing.Any") -> None:
                faulthandler.dump_traceback_later(timeout=1.0)

            signal.signal(signal.SIGUSR1, _on_sigusr1)
    except Exception:
        pass  # Not available on all platforms

    # NEW-DOC-006: parse arguments BEFORE acquiring the single-instance
    # lock, so ``--version`` works even when another instance is running
    # (mirrors voice_typer.__main__, which parses args before app.main()).
    import argparse
    import importlib.metadata
    import os

    from voice_typer.server.app import VoiceTyperApp, _ensure_single_instance, _setup_logging
    from voice_typer.server.config import _config_dir

    try:
        _pkg_version = importlib.metadata.version("voice-typer")
    except Exception:
        _pkg_version = "1.0.0"

    parser = argparse.ArgumentParser(
        prog="voice_typer.server.ipc_server",
        description="Voice Typer IPC server (spawned by Electron)",
        add_help=False,  # we add --help manually to avoid conflict with app
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help="TCP port to listen on (1..65535). If omitted, uses stdin/stdout IPC.",
    )
    parser.add_argument(
        "--ws",
        action="store_true",
        default=False,
        help=(
            "ADR-0020: run as a Tauri sidecar. Binds a localhost WebSocket "
            "server on an OS-assigned ephemeral port (127.0.0.1:0), prints "
            'a single {"event":"server_started","port":N} JSON line to '
            "stdout, then accepts WS connections authenticated by the "
            "VOICE_TYPER_IPC_TOKEN env var. Mutually exclusive with --port."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_pkg_version}",
        help="Show version and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging to the console.",
    )
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    if args.debug:
        os.environ["VOICE_TYPER_DEBUG"] = "1"
    port = args.port
    ws_mode = args.ws
    # ADR-0020 §2: --ws and --port are mutually exclusive. --ws binds
    # an OS-assigned ephemeral port and reports it via stdout; --port
    # binds a fixed port for the legacy Electron TCP path.
    if ws_mode and port is not None:
        print("--ws and --port are mutually exclusive", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)
    if port is not None and not (1 <= port <= 65535):
        print(f"Invalid port: {port} (must be 1..65535)", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)
    # ADR-0020 §2 + §10: when running as a Tauri sidecar, set the
    # TAURI_SIDECAR=1 env var so downstream gates (heartbeat watchdog,
    # VoiceTyperSingleInstance mutex) know to disable themselves. The
    # Tauri host's single-instance plugin + FT-1 supervisor replace
    # them. The env var is set here (rather than required to be set by
    # the host) so a `python -m voice_typer.server.ipc_server --ws`
    # invocation from a terminal also gets the right behavior.
    if ws_mode:
        os.environ["TAURI_SIDECAR"] = "1"
        log.info("[IPC] --ws mode enabled (TAURI_SIDECAR=1 env set)")

    _setup_logging()

    # NEW-DOC-006: single-instance lock is acquired AFTER args are parsed
    # but BEFORE app construction (which stores the mutex handle).  The
    # lock is still taken for real launches (both standalone and --port IPC).
    #
    # ADR-0020 §12: under the Tauri sidecar path (TAURI_SIDECAR=1), the
    # Tauri host's `tauri-plugin-single-instance` plugin already enforces
    # single-instance via the OS's native mechanism (Win32 named mutex on
    # Windows, NSApplication activation on macOS, lockfile on Linux). The
    # Python-side `VoiceTyperSingleInstance` Win32 mutex (app.py:2086)
    # would double-lock on Windows and block the second-instance focus
    # path, so we skip it under Tauri.
    _tauri_sidecar = os.environ.get("TAURI_SIDECAR") == "1"
    if _tauri_sidecar:
        log.info("[IPC] TAURI_SIDECAR=1 — skipping Python-side single-instance mutex (Tauri host owns it)")
        _single_instance_mutex = None
    else:
        _single_instance_mutex = _ensure_single_instance(silent=True)

    # NEW-SEC-015: the os._exit monkey-patch that printed a stack trace
    # on every shutdown has been removed.

    try:
        app = VoiceTyperApp()
    except Exception:
        # Under pythonw.exe, _setup_logging() redirects stdout/stderr to
        # devnull, so ANY exception here is invisible to the user — they
        # only see "Python process exited: 1" + the misleading "Only one
        # instance" dialog from Electron.  Log the full traceback to both
        # the app's log file and a dedicated diagnostic file so debugging
        # is possible.
        log.exception("[FATAL] VoiceTyperApp() construction failed")
        try:
            import io
            import traceback

            from voice_typer.server.config import _secure_atomic_write
            from voice_typer.server.security import _redact_text

            buf = io.StringIO()
            buf.write(f"Voice Typer startup failed at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            buf.write(f"sys.executable: {sys.executable}\n")
            # G4-M-27: redact secret-bearing argv entries before dumping.
            # ``sys.argv`` may carry ``--ipc-token sk-…`` or env-style
            # ``KEY=value`` pairs that include API keys / bearer tokens.
            # The PIIRedactionFilter attached to the rotating log handler
            # would scrub these in normal log lines, but this diagnostic
            # file is written via _secure_atomic_write — bypassing the
            # logging filter. Pipe each argv entry through ``_redact_text``
            # so secrets are masked the same way they would be in a log
            # record.
            redacted_argv = [_redact_text(str(arg)) for arg in sys.argv]
            buf.write(f"sys.argv: {redacted_argv}\n")
            traceback.print_exc(file=buf)
            # G4-M-27: redact the traceback text too. ``traceback.print_exc``
            # can include ``str(exception)`` which may carry a URL with
            # ``?key=sk-…`` or an env-var dump from a buggy handler —
            # piping through ``_redact_text`` mirrors the PIIRedactionFilter
            # behavior that the rotating file log applies to ``log.exception``.
            diag_path = _config_dir() / "startup-error.log"
            _secure_atomic_write(diag_path, _redact_text(buf.getvalue()))
            log.error("[FATAL] Diagnostic written to %s", diag_path)
        except Exception as write_exc:
            # CR-40: last-resort — try stderr then a temp file so the
            # traceback isn't lost (e.g. read-only config dir under
            # pythonw.exe where stdout/stderr are also devnull).
            print(buf.getvalue(), file=sys.stderr)
            try:
                import tempfile
                from pathlib import Path

                # G4-M-27: the /tmp fallback must be (a) PII-redacted
                # (same as the config-dir path) and (b) owner-only.
                # ``Path.write_text`` creates the file with the process
                # umask (typically 0o644) — world-readable, which leaks
                # the redacted-but-still-sensitive traceback (paths,
                # library versions, possibly partial secrets that
                # ``_redact_text`` missed) to any local user.
                # ``os.open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)``
                # + ``os.fdopen`` creates the file atomically with
                # owner-only permissions; ``O_EXCL`` prevents silently
                # clobbering an existing file (a symlink attack vector).
                redacted_payload = _redact_text(buf.getvalue())
                tmp = Path(tempfile.gettempdir()) / "voice-typer-startup-error.log"
                fd = os.open(
                    str(tmp),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as f:
                    f.write(redacted_payload)
                log.error(
                    "[FATAL] Could not write %s; wrote to %s instead (write error: %s)",
                    diag_path,
                    tmp,
                    write_exc,
                )
            except Exception:
                log.error("[FATAL] Could not write diagnostic anywhere: %s", write_exc)
        # NEW-CLI-003: use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)

    # PLAT-HLEAK: store the mutex handle on the app instance so
    # quit() can CloseHandle it on shutdown
    app._mutex_handle = _single_instance_mutex

    # ARCH-REFAC-004: use the providers.build_ipc_server composition
    # root instead of constructing IPCServer directly.  Behavior is
    # identical today (build_ipc_server just calls IPCServer(app));
    # the factory exists so future wiring (logging, metrics, feature
    # flags, an alternate service implementation) lives in one place
    # rather than being threaded through this entry point.
    from voice_typer.server.providers import build_ipc_server

    server = build_ipc_server(app)
    # d-review Finding 1: in explicit TCP (--port) or Tauri WS (--ws) mode
    # the backend is driven by Electron/Tauri over the network, not by
    # legacy stdin/stdout IPC. Mark TCP mode BEFORE start() so the stdin
    # listener (an unauthenticated command path) is not spawned.
    if port is not None or ws_mode:
        server._tcp_mode = True
    server.start()
    # ADR-0020 §2: --ws mode starts the WebSocket sidecar server instead
    # of the TCP server. The WS server binds 127.0.0.1:0, prints the
    # `server_started` JSON to stdout, and accepts authenticated WS
    # connections from the Tauri Rust host. The TCP / standalone paths
    # below are unchanged for the Electron fallback.
    if ws_mode:
        from voice_typer.server import sidecar_ws

        log.info("[IPC] starting Tauri sidecar WebSocket server (sidecar_ws.run)")
        # ADR-0020 round-2 fix: do NOT call server.push({"type": "ready"})
        # here — in WS mode, server.push writes to the TCP _tcp_client
        # which is None (no TCP server started). The `ready` event is
        # emitted by sidecar_ws._handle_connection() via event_bus.publish
        # AFTER the first WS client authenticates, so the Tauri host
        # receives it over the WS connection.
        # sidecar_ws.run() blocks until the asyncio loop is cancelled
        # (SIGTERM from the host's kill_children backstop). Returns an
        # exit code; we propagate it.
        _ws_exit = sidecar_ws.run(server)
        if _ws_exit != 0:
            log.warning("[IPC] sidecar_ws.run exited with code %d", _ws_exit)
        sys.exit(_ws_exit)
    elif port is not None:
        server.start_tcp(port)
        log.info("[IPC] TCP mode on port %d — Electron should connect here", port)
    else:
        # P1-1.2: Standalone mode (no --port). The user ran VoiceTyper
        # from a terminal.  Auto-pick an available port, start the TCP
        # server, generate a session token, and launch the Electron
        # frontend so it connects back to us over TCP instead of
        # spawning its own Python backend.
        from voice_typer.server import electron_launcher

        standalone_port, standalone_sock = _pick_available_port(9876)

        # Generate the session token and set it as an env var BEFORE
        # starting the TCP listener.  The _accept_tcp daemon thread reads
        # VOICE_TYPER_IPC_TOKEN at the top of its function; if we set it
        # after start_tcp(), the thread can read the env var before we
        # assign it, leaving expected_token empty and the connection
        # unauthenticated.
        ipc_token = electron_launcher.generate_session_token()
        os.environ["VOICE_TYPER_IPC_TOKEN"] = ipc_token

        # CR-7: pass the BOUND socket through to start_tcp so there's
        # no race window between _pick_available_port's probe and the
        # real bind() in _accept_tcp.  The kernel guarantees no other
        # local process can claim the port between probe and listen.
        server.start_tcp((standalone_port, standalone_sock))
        log.info(
            "[IPC] standalone TCP mode on port %d — Electron will connect here",
            standalone_port,
        )

        # Launch Electron as a subprocess.  Pass the port + token via
        # env vars so Electron's main process detects them and connects
        # directly instead of spawning its own Python backend.
        electron_pid = electron_launcher.launch_electron_frontend(
            standalone_port,
            ipc_token,
        )
        if electron_pid is not None:
            # Track PID on the app instance so quit() can terminate
            # the subprocess during shutdown (P1-1.3).
            app._electron_pid = electron_pid
            # Also register with tray_window so its existing cleanup
            # path (which calls get_electron_pid()) still works.
            try:
                from voice_typer.server.tray_window import set_electron_pid

                set_electron_pid(electron_pid)
            except Exception:
                log.debug("[IPC] could not register Electron PID with tray_window", exc_info=True)
            log.info(
                "[STARTUP] Standalone mode — launched Electron (PID=%s) on port %d",
                electron_pid,
                standalone_port,
            )
        else:
            log.error(
                "[STARTUP] Standalone mode — failed to launch Electron; backend is running on port %d with no UI",
                standalone_port,
            )

    # Tell the frontend we're ready — Electron defers window creation until this.
    server.push({"type": "ready"})
    log.info("[IPC] entering app.start() (tray event loop)")
    try:
        app.start()  # blocks (tray event loop)
        # QUIT-CLEAN-001: keep shutdown quiet.  Only ``[QUIT] Quitting
        # Voice Typer...`` (from app.quit_app) and ``[SHUTDOWN]
        # Shutdown complete, exiting`` (from app.quit) should be at
        # INFO during a normal quit; everything else is internal
        # bookkeeping that the user doesn't need to see.
        log.debug("[IPC] Shutdown complete")
    except SystemExit as _se:
        # sys.exit() or os._exit() called from within pystray or runtime.
        # Catch it so we can log the cause, then re-raise.
        log.debug("[IPC] app.start() exited via sys.exit(%s)", _se.code)
        raise
    except Exception:
        # ERR-ERR-002 (fix): was `except BaseException` which also caught
        # KeyboardInterrupt and GeneratorExit. Now catches only Exception
        # so Ctrl+C and SystemExit propagate normally to the finally block.
        log.exception("[FATAL] app.start() raised — shutting down")
        # Also write to the diagnostic file for users running under
        # pythonw.exe where stdout/stderr are devnull.
        try:
            import io
            import traceback

            from voice_typer.server.config import _secure_atomic_write
            from voice_typer.server.security import _redact_text

            buf = io.StringIO()
            buf.write(f"\n--- app.start() failed at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            traceback.print_exc(file=buf)
            diag_path = _config_dir() / "startup-error.log"
            # CR-10: OVERWRITE (not append) the diagnostic file.  The
            # previous implementation read the existing content and
            # appended, which made ``startup-error.log`` grow without
            # bound across repeated ``app.start()`` failures (the
            # operator would hit the same crash on every relaunch and
            # the file accumulated every traceback).  Capping at one
            # entry mirrors the construction-failure path above (line
            # ~2445) and keeps the file a useful "what just happened"
            # diagnostic instead of a multi-MB append-only log.
            #
            # G4-M-27: pipe through ``_redact_text`` (same as the
            # construction-failure path) so secrets in the traceback —
            # e.g. ``ConnectionError("?key=sk-…")`` — are masked
            # before the file is written. The PIIRedactionFilter on
            # the rotating log handler scrubs ``log.exception`` lines,
            # but this diagnostic file bypasses the logging filter.
            _secure_atomic_write(diag_path, _redact_text(buf.getvalue()))
            log.error("[FATAL] Diagnostic written to %s", diag_path)
        except Exception as write_exc:
            # CR-40: last-resort — try stderr then a temp file so the
            # traceback isn't lost (e.g. read-only config dir under
            # pythonw.exe where stdout/stderr are also devnull).
            print(buf.getvalue(), file=sys.stderr)
            try:
                import tempfile
                from pathlib import Path

                # G4-M-27: mirror the construction-failure /tmp fallback:
                # PII-redact the payload AND create the file with
                # ``os.open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)``
                # + ``os.fdopen`` so the file is owner-only. The
                # previous ``tmp.write_text`` call created the file
                # with the process umask (typically 0o644), leaking
                # the traceback to any local user.
                redacted_payload = _redact_text(buf.getvalue())
                tmp = Path(tempfile.gettempdir()) / "voice-typer-startup-error.log"
                fd = os.open(
                    str(tmp),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as f:
                    f.write(redacted_payload)
                log.error(
                    "[FATAL] Could not write %s; wrote to %s instead (write error: %s)",
                    diag_path,
                    tmp,
                    write_exc,
                )
            except Exception:
                log.error("[FATAL] Could not write diagnostic anywhere: %s", write_exc)
        # NEW-CLI-003: use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)
    else:
        pass
    finally:
        pass
    # Keep mutex alive by referencing it until exit
    _ = _single_instance_mutex


if __name__ == "__main__":
    main()
