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
import json
import os
import socket
import sys
import threading
import time
import typing
from concurrent.futures import ThreadPoolExecutor
from types import FrameType
from typing import TYPE_CHECKING

from voice_typer.server import event_bus
from voice_typer.server._paths import IPC_PORT
from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.log import reset_correlation_id, set_correlation_id

if TYPE_CHECKING:
    # Typed ``app`` parameter on ``IPCServer.__init__``. The
    # protocol is structural (``@runtime_checkable``); ``MagicMock``
    # fixtures still satisfy it (the runtime check inspects attribute
    # names, not types), so test code that passes a ``MagicMock`` does
    # not need to import ``AppProtocol``.
    from voice_typer.server.providers import AppProtocol

    # GT-D1-5: concrete type for the ``service`` DI parameter (was ``Any``).
    # Imported under TYPE_CHECKING to avoid a runtime circular import —
    # VoiceTyperService imports from this module's neighbours.
    from voice_typer.server.service import VoiceTyperService

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
# Canonical home for ``_SECRET_CONFIG_FIELDS`` is the transport-neutral
# :mod:`voice_typer.server.config_sanitizer` module (the canonical source
# for config redaction across service and IPC layers). The name is still
# re-exported from this module so existing importers
# (``from voice_typer.server.ipc_server import _SECRET_CONFIG_FIELDS``)
# keep working unchanged. ``_sanitize_config_for_ipc`` is intentionally
# still imported from :mod:`voice_typer.server.ipc.history_bounds` because
# that version uses the DE-33 pattern-based denylist (defense-in-depth
# beyond the explicit frozenset); the config_sanitizer version is a
# simpler implementation kept for service-layer callers that don't need
# the pattern matching.
from voice_typer.server.config_sanitizer import (  # noqa: F401
    _SECRET_CONFIG_FIELDS,
)

# S1-CR-66: the ``log`` / ``_push_event_now`` helpers used to be
# defined inline here.  They moved to the
# ``voice_typer.server.ipc._helpers`` leaf submodule so that
# ``ipc_server.py`` can be loaded as ``__main__`` (via
# ``python -m voice_typer.server.ipc_server``) without needing the
# ``sys.modules[_CANONICAL] = sys.modules["__main__"]`` registration
# hack to keep lazy imports (in providers.py / sidecar_ws.py / app.py /
# __main__.py) resolving to the same object.  The names are re-exported
# here (``# noqa: F401``) so existing
# ``from voice_typer.server.ipc_server import log`` /
# ``... import _push_event_now`` callers keep working unchanged.
# UE-32: ``_READONLY_COMMANDS`` previously lived in ``ipc._helpers``;
# it now lives in ``voice_typer.server.ipc.registry`` (see the import
# block below).  The ``ipc._helpers`` duplicate is a legacy leftover
# (outside the UE-32 disjoint set) and is no longer the authoritative
# source.
from voice_typer.server.ipc._helpers import (  # noqa: E402, F401
    _push_event_now,
    log,
)
from voice_typer.server.ipc.history_bounds import (  # noqa: F401
    _HISTORY_LIMIT_DEFAULT,
    _HISTORY_LIMIT_MAX,
    _REDACTED_SENTINEL,
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

# UE-32: the command-dispatch registry, the read-only command set, and
# the Python-only exception set all live in the
# :mod:`voice_typer.server.ipc.registry` leaf submodule.  Pre-UE-32
# ``_COMMAND_REGISTRY`` + ``_PYTHON_ONLY_COMMANDS`` were class attributes
# on :class:`IPCServer` (defined ~1,400 lines into this 2,100-line
# god-module) and ``_READONLY_COMMANDS`` lived in ``ipc._helpers``; the
# split made the three-layers-must-agree parity contract harder to
# reason about.  The extraction is behavior-preserving — same dict,
# same keys, same values.  The names are re-exported here
# (``# noqa: F401``) so existing
# ``from voice_typer.server.ipc_server import _COMMAND_REGISTRY`` /
# ``... import _READONLY_COMMANDS`` /
# ``... import _PYTHON_ONLY_COMMANDS`` callers keep working unchanged.
# :class:`IPCServer` re-aliases ``_COMMAND_REGISTRY`` and
# ``_PYTHON_ONLY_COMMANDS`` as class attributes (see the class body
# below) so every ``IPCServer._COMMAND_REGISTRY`` /
# ``IPCServer._PYTHON_ONLY_COMMANDS`` call site — pinned by
# ``tests/test_ipc_shutdown_registry.py``,
# ``tests/test_ec4_python_command_registry_parity.py``,
# ``tests/test_ipc_command_registry_sync.py``,
# ``tests/tauri/mig19/test_phase4_validation.py``,
# ``tests/tauri/test_tauri_sidecar_gate.py`` — keeps working unchanged.
from voice_typer.server.ipc.registry import (  # noqa: E402, F401
    _COMMAND_REGISTRY,
    _PYTHON_ONLY_COMMANDS,
    _READONLY_COMMANDS,
)

# NOTE: ``_get_rate_limiter`` is intentionally NOT imported here — it is
# defined locally below as a thin re-export (see the CR-11 / R4-F18 /
# DR-45 comment block) so tests that monkey-patch
# ``ipc_server._RateLimiter`` are still observed (the re-export passes
# the patched class to the canonical leaf implementation via ``_cls=``).
from voice_typer.server.ipc.transport import (  # noqa: F401
    _pick_available_port,
    _TCPLineIO,
)
from voice_typer.server.ipc.validation import (  # noqa: F401
    CommandHandler,
    ErrorEnvelope,
    ResponseEnvelope,
    _error_response,
    _validate_dict_payload,
)

# GT-29 / GT-D1-10: typed response envelope and command-handler aliases.
#
# ``ResponseEnvelope`` is the canonical shape of every IPC frame pushed or
# dispatched: a dict with at least ``type`` (str) and optional ``data``,
# ``id``. Using a type alias (instead of bare ``dict``) lets the type
# checker verify handler signatures and the dispatch-table value type, so
# a typo in a handler-method name surfaces at IPCServer construction
# (where the bound-method cache is built) rather than at dispatch time.
# YJ-1 / YJ-27: ResponseEnvelope + CommandHandler canonical definitions
# moved to voice_typer.server.ipc.validation (breaks the circular import
# and lets handler modules import them from a non-god-module location).

# GT-25: ``_READONLY_COMMANDS`` now lives in ``ipc.registry`` (UE-32
# extraction — see the import above).  The set lists dispatch commands
# whose handlers do NOT mutate shared app/service state; they bypass the
# per-server ``_dispatch_lock`` so a long-running state-mutating handler
# (e.g. ``download_model``) does not block a quick status poll from a
# second authenticated connection.

# UE-13 (High): the unauthenticated stdin/stdout IPC listener is gated
# behind this env var. ``start()`` refuses to spawn the stdin listener
# thread when ``_tcp_mode`` is False AND the env var is not set to
# ``"1"`` — closing the "unprotected stdin IPC path is still the
# default" hole. The ``--allow-stdin`` CLI flag in :func:`parse_ipc_args`
# sets this env var as the alternative gate for development / testing.
# Production callers (``main()``) always set ``_tcp_mode = True`` before
# ``start()`` so the gate never fires in production; the gate exists to
# catch direct-API / test paths that would otherwise silently expose an
# unauthenticated command channel on the user's terminal.
_STDIN_IPC_ENV_VAR: str = "VOICE_TYPER_ALLOW_STDIN_IPC"


# ── CR-11 / R4-F18: per-process rate limiter get-or-create ───────────────
#
# DR-45: this is a THIN RE-EXPORT — the canonical implementation lives in
# ``voice_typer.server.ipc.rate_limiter._get_rate_limiter``. Tests in
# ``tests/test_r4_f18_rate_limiter_concurrent_init.py`` and
# ``tests/test_cr_fixes.py`` monkey-patch ``ipc_server._RateLimiter`` with
# a counting stand-in to widen the race window (CR-11 / R4-F18). The
# re-export delegates to the canonical implementation with the patched
# ``_RateLimiter`` class injected via ``_cls=``, so the patched class is
# still observed (preserving the test contract) while the get-or-create
# logic is single-sourced in the leaf module.
def _get_rate_limiter(server: "object") -> _RateLimiter:
    """Thin re-export — canonical implementation in
    ``voice_typer.server.ipc.rate_limiter``.

    Tests monkey-patch ``ipc_server._RateLimiter`` to widen the race
    window (CR-11 / R4-F18). We delegate to the canonical implementation
    with the patched class injected via ``_cls=`` so the patched class
    is still observed.
    """
    from voice_typer.server.ipc import rate_limiter as _rate_limiter_mod

    return _rate_limiter_mod._get_rate_limiter(server, _cls=_RateLimiter)


# Module-level push hook.  ``_push_event_now`` now lives in
# ``ipc._helpers`` (S1-CR-66 refactor — see the import above).  It is a
# thin shim over ``event_bus.publish`` so existing lazy imports
# (``from voice_typer.server.ipc_server import _push_event_now``) keep
# working.  Domain code should call ``event_bus.publish`` directly.
# B-1 FIX-12: the _push_event_registry/_push_event_registry_lock aliases
# and _set_push_event/_clear_push_event shims have been removed — domain
# code and tests now call ``event_bus.subscribe`` /
# ``event_bus.unsubscribe`` directly.


# ARCH-REFAC-002: the per-command ``_handle_*`` methods live in the
# ``handlers/`` subpackage as mixin classes.  The handler mixins import
# their own helpers (``log`` from ``handlers._log``, validation from
# ``ipc.validation``, etc.) and do NOT import from this module, so there
# is no circular dependency to break.
#
# S1-CR-66: the ``sys.modules[_CANONICAL] = sys.modules["__main__"]``
# registration hack that used to live here has been removed.  The hack
# was originally needed because ``python -m voice_typer.server.ipc_server``
# loads this file as ``__main__`` (NOT under the canonical dotted name),
# and the handler mixins used to import ``log`` / ``_push_event_now``
# from the canonical name.  Both preconditions are now gone:
#
#   1. The handler mixins no longer import from this module — they
#      import ``log`` from ``voice_typer.server.handlers._log`` and
#      validation helpers from ``voice_typer.server.ipc.validation``.
#      The import cycle the hack was working around no longer exists.
#
#   2. The remaining lazy ``from voice_typer.server.ipc_server import X``
#      imports in ``providers.py`` / ``sidecar_ws.py`` / ``app.py`` /
#      ``__main__.py`` resolve module-level helpers (``log``,
#      ``_push_event_now``) that now live in ``ipc._helpers`` and are
#      re-exported here.  When this file is loaded as ``__main__``, a
#      subsequent canonical-name import would re-execute this file
#      (producing a duplicate ``IPCServer`` class object), but the
#      duplicate is never instantiated: ``providers.build_ipc_server``
#      constructs the canonical-named ``IPCServer``, and the
#      ``__main__``-mode copy is dead code whose class object is
#      discarded.  No observable behavior change.


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

# TCP transport and output/push methods extracted to leaf mixins.
# ``_SHUTDOWN_ALLOWLIST`` / ``_TCP_PENDING_DRAIN_CAP`` / ``_TCP_PENDING_BUFFER_CAP``
# are re-exported here so existing ``from voice_typer.server.ipc_server import
# _SHUTDOWN_ALLOWLIST`` callers (tests/test_ipc_send_shutdown_allowlist.py,
# tests/test_zr43_zr70_constants.py) keep working unchanged.
from voice_typer.server.ipc.sender import (  # noqa: E402, F401
    _SHUTDOWN_ALLOWLIST,
    _TCP_PENDING_BUFFER_CAP,
    _TCP_PENDING_DRAIN_CAP,
    OutputMixin,
)
from voice_typer.server.ipc.transport_tcp import TCPTransportMixin  # noqa: E402


class IPCServer(
    TCPTransportMixin,
    OutputMixin,
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

    # UE-32: ``_COMMAND_REGISTRY`` and ``_PYTHON_ONLY_COMMANDS`` are
    # canonical to :mod:`voice_typer.server.ipc.registry` (imported at
    # module top — see the ``UE-32`` comment block above).  They are
    # re-aliased here as class attributes so every existing
    # ``IPCServer._COMMAND_REGISTRY`` / ``IPCServer._PYTHON_ONLY_COMMANDS``
    # call site (pinned by ``tests/test_ipc_shutdown_registry.py``,
    # ``tests/test_ec4_python_command_registry_parity.py``,
    # ``tests/test_ipc_command_registry_sync.py``,
    # ``tests/tauri/mig19/test_phase4_validation.py``,
    # ``tests/tauri/test_tauri_sidecar_gate.py``) keeps working
    # unchanged.  ``__init__`` iterates over ``self._COMMAND_REGISTRY``
    # to typo-validate every entry resolves to a callable bound method
    # (GT-29 / DT-5); the iteration observes this alias and therefore
    # the registry's canonical dict.
    _COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY
    _PYTHON_ONLY_COMMANDS: frozenset[str] = _PYTHON_ONLY_COMMANDS

    def __init__(
        self,
        app: "AppProtocol",
        service: "VoiceTyperService | None" = None,
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
        # ``app`` is now typed as ``AppProtocol`` (was ``Any``).
        # ``AppProtocol`` is a ``@runtime_checkable`` structural type —
        # a MagicMock satisfies it (the runtime check inspects attribute
        # NAMES via ``getattr_static``, not types), and the static
        # annotation does NOT force test files to import the protocol
        # because the annotation is a forward ref resolved only under
        # ``TYPE_CHECKING``. Existing test code that passes a MagicMock
        # therefore keeps working unchanged.
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
            # DJ-68: wire the service-layer mic cache invalidator so
            # the OS device-change watcher (which already invalidates
            # DeviceManager._device_list_cache via _invalidate_device_cache)
            # ALSO invalidates the service-layer 5s-TTL cache
            # (_microphones_cache_ts in MicrophoneTestMixin). Without
            # this, after a USB/BT hot-plug event the Electron UI
            # continues to show the stale microphone dropdown (including
            # the unplugged device, missing the newly-plugged one) for
            # up to 5s. Best-effort: guarded so a recorder-without-
            # DeviceManager (tests) doesn't fail.
            try:
                recorder_devices = getattr(app.recorder, "_devices", None)
                if recorder_devices is not None and hasattr(
                    recorder_devices, "set_service_cache_invalidator"
                ):
                    recorder_devices.set_service_cache_invalidator(
                        lambda: self.service.refresh_microphones(force=True)
                    )
            except Exception:
                log.debug(
                    "[IPC] failed to wire DJ-68 service-layer cache invalidator",
                    exc_info=True,
                )
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
        # Declare ``_stdin_thread`` as ``Thread | None`` so the
        # ``self._stdin_thread = None`` branch in ``start()`` (tcp_mode
        # path) type-checks. Without this annotation, pyrefly infers the
        # attribute type from the FIRST assignment
        # (``threading.Thread(...)`` in the non-tcp branch) and rejects
        # the subsequent ``None`` assignment as bad-assignment. Mirrors
        # the ``_heartbeat_thread`` pattern above.
        self._stdin_thread: threading.Thread | None = None
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

        # GT-30: declare the per-instance rate-limiter attribute on
        # ``IPCServer`` itself (was dynamically injected by the
        # module-level ``_get_rate_limiter`` helper, with a
        # ``# type: ignore[attr-defined]`` silencing the missing-attribute
        # diagnostic). Declaring it here means the type checker can
        # verify both the ``setattr`` site and the ``getattr`` fast path
        # in ``_get_rate_limiter``; a refactor that drops the
        # ``_get_rate_limiter`` injection is now visible as
        # ``_rate_limiter_instance is None`` at dispatch time rather than
        # as a silent slow-path regression.
        self._rate_limiter_instance: _RateLimiter | None = None

        # Cached snapshot of ``self.app._shutting_down`` for the hot
        # ``_send`` path. Previously ``_send`` did
        # ``getattr(self.app, "_shutting_down", False) is True`` on every
        # call (15-50 Hz waveform-bubble push rate) — the ``getattr`` with
        # a default is ~2× slower than a direct attribute access because
        # it always invokes ``__getattribute__`` even on hit. We cache
        # the value on the IPCServer instance and refresh it in
        # ``start()`` (→ False) and ``stop()`` (→ True). ``_send`` reads
        # ``self._cached_shutting_down`` via a defensive
        # ``getattr(self, "_cached_shutting_down", False)`` so test
        # fixtures that bypass ``__init__`` (e.g.
        # ``IPCServer.__new__(IPCServer)`` in
        # ``tests/test_ipc_layer_fixes.py`` and
        # ``tests/test_ipc_server.py``) keep working without explicitly
        # setting the field — they get the ``False`` default, matching
        # the previous ``getattr(self.app, "_shutting_down", False)``
        # behaviour for tests that set ``server.app._shutting_down = False``.
        # The cache is intentionally a SNAPSHOT taken at start/stop time,
        # not a live view of ``self.app._shutting_down`` — the
        # ``restart_app`` path sets ``self.app._shutting_down = True``
        # BEFORE calling ``stop()``, so during the brief window between
        # that set and the ``stop()`` call, the cache is stale (still
        # False). This is acceptable: the ``relaunch_app`` push event is
        # in ``_SHUTDOWN_ALLOWLIST`` and is delivered regardless, and
        # other events being written during this ~10ms window is fine —
        # the TCP client is still alive (``stop()`` hasn't closed the
        # socket yet). Once ``stop()`` runs, the cache flips to True and
        # suppression kicks in for real.
        self._cached_shutting_down: bool = False

        # GT-25: per-server dispatch lock serializing state-mutating
        # handler invocations. Read-only handlers (see
        # ``_READONLY_COMMANDS``) bypass this lock. The lock is held ONLY
        # for the handler body — NOT for the dispatch I/O (read, parse,
        # response write) — so a slow state-mutating handler (e.g.
        # ``download_model``) blocks OTHER state-mutating dispatches but
        # NOT read-only status polls or the accept loop. ``RLock`` so a
        # handler that re-enters ``_dispatch`` on the same thread (e.g.
        # via ``event_bus.publish`` triggering a synchronously-delivered
        # event) does not self-deadlock.
        self._dispatch_lock = threading.RLock()

        # UE-18 (Medium): per-instance shutdown re-entrancy gate.
        # ``_handle_shutdown`` checks this event at the top and no-ops
        # the second invocation. Pre-UE-18, a double-``shutdown`` (e.g.
        # the Tauri host's WS transport retrying after a slow ack) would
        # spawn a SECOND untracked ``ipc-shutdown-cleanup`` daemon thread
        # — both threads would race into ``service.quit()`` /
        # ``_do_cleanup()`` and double-free the mic stream, hotkey
        # listeners, single-instance mutex, etc. The event is set BEFORE
        # the cleanup thread is spawned so the second invocation's
        # no-op is atomic with the first's thread-spawn decision.
        self._shutdown_started: threading.Event = threading.Event()

        # DT-5: registry-typo validation at construction time. We resolve
        # every ``_COMMAND_REGISTRY`` method-name string to its attribute on
        # ``self`` via ``getattr`` and assert it's callable. A typo in the
        # class-level registry now surfaces at IPCServer construction (every
        # test that builds an IPCServer) instead of only when the buggy
        # command is dispatched. The previous ``_command_handlers`` instance
        # cache (built here and stored on ``self``) was dead code —
        # ``_dispatch`` resolves the handler the same way at dispatch time
        # via ``getattr(self, handler_name, None)`` so test-time
        # monkey-patches are observed. The cache is no longer built; the
        # class-level ``_COMMAND_REGISTRY: dict[str, str]`` remains the
        # introspection source-of-truth (pinned by
        # ``tests/tauri/mig19/test_phase4_validation.py`` and
        # ``tests/test_ipc_shutdown_registry.py``).
        for _cmd, _method_name in self._COMMAND_REGISTRY.items():
            _bound = getattr(self, _method_name, None)
            if not callable(_bound):
                raise RuntimeError(
                    f"_COMMAND_REGISTRY[{_cmd!r}] resolves to non-callable "
                    f"attribute {_method_name!r} on IPCServer — registry "
                    "entry and handler method have drifted out of sync."
                )

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
        # Refresh the cached shutdown flag. ``start()`` is called once at
        # server boot (when the host connects) and again after a
        # stop()/restart cycle in tests, so this is the canonical
        # "we're not shutting down" transition point.
        self._cached_shutting_down = False
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
        #
        # UE-13 (High): the unauthenticated stdin IPC path is gated
        # behind ``VOICE_TYPER_ALLOW_STDIN_IPC=1``. When ``_tcp_mode`` is
        # False (the legacy stdin/stdout path) AND the env var is not
        # set, the stdin listener is REFUSED — a WARNING is logged and
        # ``_stdin_thread`` is set to ``None``. This prevents an
        # unauthenticated command channel from opening on the user's
        # terminal: on Linux TIOCSTI injection is possible, and on every
        # platform an accidental paste of JSON into the terminal triggers
        # unintended IPC commands. Direct API users and tests that need
        # the stdin listener must set ``VOICE_TYPER_ALLOW_STDIN_IPC=1``
        # (the ``--allow-stdin`` CLI flag in :func:`parse_ipc_args` is
        # the alternative gate — it sets the env var).
        if not self._tcp_mode:
            if os.environ.get(_STDIN_IPC_ENV_VAR) == "1":
                self._stdin_thread = threading.Thread(
                    target=self._run,
                    name="ipc-server",
                    daemon=True,
                )
                self._stdin_thread.start()
            else:
                # UE-13: refuse to start the unauthenticated stdin
                # listener. ``_tcp_mode`` is False (so the caller did
                # NOT explicitly opt into TCP/WS mode) AND the env-var
                # gate is unset — this is the "unprotected stdin IPC
                # path is still the default" scenario the gate exists
                # to close. Log a WARNING (not an error: the server is
                # still usable for TCP/WS dispatch via the methods on
                # ``self``; only the stdin listener is gated off) and
                # leave ``_stdin_thread = None`` so ``stop()`` /
                # ``_thread_registry`` see no thread to join.
                log.warning(
                    "[IPC] stdin listener gated off — set %s=1 (or pass "
                    "--allow-stdin) to enable unauthenticated stdin/stdout "
                    "IPC mode. Refusing to start the listener.",
                    _STDIN_IPC_ENV_VAR,
                )
                self._stdin_thread = None
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
        # respawn, and (2) the Rust host dispatches a
        # ``heartbeat`` command every 10s and triggers respawn
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
        # Refresh the cached shutdown flag. ``stop()`` is the canonical
        # "we're shutting down" transition point. ``_send`` reads
        # ``self._cached_shutting_down`` (defensively via ``getattr``) on
        # every push event and short-circuits the TCP write for
        # non-critical events when this is True — see
        # ``_SHUTDOWN_ALLOWLIST`` for the allowlist of events that MUST
        # still be delivered.
        #
        # NOTE: ``restart_app`` sets ``self.app._shutting_down = True``
        # BEFORE ``stop()`` is called, so during the brief window between
        # that set and this ``stop()`` call, the cache is stale (still
        # False). This is acceptable — see the ``__init__`` comment for
        # ``_cached_shutting_down``.
        self._cached_shutting_down = True
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
            # Bound the in-flight handler drain so teardown doesn't
            # race with running handlers. ``shutdown(wait=False)`` only
            # cancels queued futures; in-flight handlers keep running on the
            # pool's worker threads. We drain them with a hard 5s deadline
            # on a daemon thread so this ``stop()`` call never blocks
            # indefinitely.
            join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
            join_thread.start()
            join_thread.join(timeout=5.0)
            if join_thread.is_alive():
                log.warning("[SHUTDOWN] tcp_dispatch_pool did not drain in 5s — proceeding anyway")
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

    # ── Heartbeat watchdog (RW-10) ───────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """RW-10: daemon thread that watches for Electron heartbeat timeouts.

        Wakes every ``_HEARTBEAT_INTERVAL_SECONDS`` (5s) and calls
        :meth:`_check_heartbeat_timeout`.  When the timeout fires
        (9 missed heartbeats = 45s without a heartbeat from Electron;
        reduced from 120s/24 misses to align with the Rust-side
        ~30-45s supervisor respawn window), the loop returns —
        ``app.quit()`` has already been triggered, which runs the
        shared ``_do_cleanup()`` path from RW-3 (restores volume,
        flushes recovery, releases the mutex, closes PortAudio) and
        breaks the pystray loop so the process exits.

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
        waiting for the real-time 45s timeout to elapse).

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
        # (non-zero) so the supervisor treats this as a
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
                import os as _os

                _os._exit(1)

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

    def _handle_heartbeat(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
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

    def _handle_relaunch_ack(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
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

    def wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Wait for Electron's ``relaunch_ack`` signal (PERF-005).

        Public wrapper around the private ``_relaunch_ack_event`` so
        :class:`voice_typer.server.app.VoiceTyperApp` does not have to
        reach into IPC-server private state during ``restart_app``.

        The event is cleared before waiting so a stale ack from a prior
        restart cycle cannot satisfy a fresh one. Returns ``True`` if the
        ack was signalled within ``timeout`` seconds, ``False`` on
        timeout.

        Parameters
        ----------
        timeout :
            Maximum seconds to wait for the ack (mirrors the original
            ``2.0`` hardcoded value used by ``restart_app``).
        """
        self._relaunch_ack_event.clear()
        return self._relaunch_ack_event.wait(timeout=timeout)

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

    def _send_stdin_error_envelope(
        self,
        *,
        message: str,
        code: str | None = None,
        legacy_code: str | None = None,
        _out: typing.IO[str] | None = None,
    ) -> None:
        """Build + send an error envelope on the legacy stdin/stdout path.

        ZR-76: consolidates the three inline error-envelope construction
        sites in :meth:`_run` (invalid payload / invalid JSON /
        internal_error) into a single helper so the envelope shape is
        defined in one place. The TCP / WS paths use
        :meth:`_shutting_down_error` (which returns the envelope; the
        caller sends it via ``_send`` with the TCP ``_client`` kwarg);
        this stdin-path helper sends directly because every call site
        uses ``_out=stdout`` (the TextIO variant of ``_send``).

        ``code`` is optional so the helper can express the bare
        ``{"message": "invalid JSON"}`` envelope (IPC-5 backward-compat
        with ``tests/test_server.py``'s ``test_handles_invalid_json``,
        which asserts the no-``code`` shape). ``legacy_code`` carries
        the one-release alias for the invalid-payload site.
        """
        data: dict[str, object] = {"message": message}
        if code is not None:
            data["code"] = code
        if legacy_code is not None:
            data["legacy_code"] = legacy_code
        self._send({"type": "error", "data": data}, _out=_out)

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
                        # ZR-76: route through the shared
                        # ``_send_stdin_error_envelope`` helper so the
                        # envelope shape is defined in one place.
                        # Namespaced form (canonical) + legacy alias
                        # (one-release compat).
                        self._send_stdin_error_envelope(
                            message="message must be a JSON object",
                            code="client.invalid_payload",
                            legacy_code="invalid_payload",
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
                    # ZR-76: route through the shared helper. ``code``
                    # is intentionally omitted to preserve the
                    # IPC-5 backward-compat contract pinned by
                    # ``tests/test_server.py::test_handles_invalid_json``
                    # (bare ``{"message": "invalid JSON"}`` envelope).
                    self._send_stdin_error_envelope(
                        message="invalid JSON",
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
                    # EC-FIX-2 / EC-10: align to the namespaced
                    # ``server.internal_error`` form (same as the TCP
                    # dispatch-level error handler above) so the
                    # renderer can switch on a single canonical prefix.
                    # ZR-76: route through the shared helper.
                    self._send_stdin_error_envelope(
                        message="internal error",
                        code="server.internal_error",
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

        GT-25 / GT-45: state-mutating handler invocations are serialized
        on ``self._dispatch_lock`` with a TOCTOU-closing re-check of
        ``app._shutting_down`` inside the lock. Read-only handlers (see
        ``_READONLY_COMMANDS``) bypass the lock; their best-effort
        shutdown re-check is done unlocked (mirroring the original
        PVT-G5-004 gate).
        """
        # PVT-G5-004: cooperative shutdown gate. When the app is shutting
        # down (``app._shutting_down is True``), reject all NEW dispatch
        # requests with a structured ``shutting_down`` error so the client
        # can stop retrying and tear down cleanly. ``is True`` (rather than
        # a truthiness check) mirrors the existing ``_send`` shutdown-
        # suppress gate (see the ``_cached_shutting_down`` read in
        # ``OutputMixin._send`` in ``voice_typer/server/ipc/sender.py``)
        # so MagicMock-based test fixtures — which expose
        # ``_shutting_down`` as a child mock that is truthy but not
        # ``is True`` — keep exercising the dispatch path instead of
        # short-circuiting here.
        #
        # DJ-31/DJ-32: read the cached snapshot (refreshed in start()/stop())
        # via a defensive ``getattr(self, ...)`` so test fixtures that bypass
        # ``__init__`` (mirroring the sender.py:224 pattern) keep working
        # without explicitly setting the field. ``getattr`` traversal of
        # ``self`` is cheaper than ``getattr(self.app, '_shutting_down',
        # False)`` because ``self`` is a direct local whereas ``self.app``
        # is an attribute chain that always invokes ``__getattribute__``.
        if getattr(self, "_cached_shutting_down", False) is True:
            return self._shutting_down_error(msg)

        cmd = msg.get("type")
        data = msg.get("data")
        resp: ResponseEnvelope = {"id": msg.get("id")} if "id" in msg else {}

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
        # DT-5: resolve the handler via the class-level ``_COMMAND_REGISTRY``
        # (the introspection source-of-truth) plus ``getattr`` so test-time
        # monkey-patches (``monkeypatch.setattr(server, '_handle_<cmd>', ...)``)
        # are observed at dispatch time. Registry-typo validation is
        # performed once at IPCServer construction (see ``__init__``); there
        # is NO instance-level cache — the previous ``_command_handlers``
        # dict was dead code (built but never consulted at dispatch time)
        # and has been removed. The ``CommandHandler`` annotation on the
        # local ``handler`` variable gives the type checker a ``Callable``
        # value type instead of ``Any``.
        handler_name = self._COMMAND_REGISTRY.get(cmd_key)
        handler: CommandHandler | None = None
        if handler_name is not None:
            _resolved = getattr(self, handler_name, None)
            if callable(_resolved):
                # YJ-27: ``getattr(self, name, None)`` returns ``Any``;
                # ``callable()`` narrows that to a callable type whose
                # return is inferred as ``object`` (pyrefly infers the
                # narrowest callable supertype). Direct assignment to
                # ``handler`` would fail ``bad-assignment`` because
                # ``(...) -> object`` is not assignable to ``CommandHandler``
                # (whose return type is ``ResponseEnvelope | None`` — a
                # narrower type than ``object``, and return types are
                # covariant). ``typing.cast`` is the typed, intentional
                # assertion that the resolved attribute matches the
                # ``CommandHandler`` contract: every entry in
                # ``_COMMAND_REGISTRY`` maps to a ``_handle_<cmd>``
                # method on this class, and the ``__init__``
                # registry-typo validation loop (DT-5) asserts each
                # entry resolves to a callable attribute at construction
                # time — so a non-CommandHandler resolution would have
                # surfaced as an ``IPCServer.__init__`` test failure
                # before reaching this line. ``cast`` is preferred over
                # the previous ``# type: ignore[assignment]`` suppression
                # because it (1) preserves the type checker's ability
                # to flag genuine ``CommandHandler``-shape mismatches
                # on the assignment LHS, (2) does not silently mask
                # future type errors on this line, and (3) keeps the
                # cast local — if YJ-1's full handler annotation
                # migration ever lands, the cast can be removed without
                # touching anything else.
                handler = typing.cast(CommandHandler, _resolved)
        try:
            if handler is None:
                result = self._handle_unknown_command(cmd, data, resp)
            elif cmd_key in _READONLY_COMMANDS:
                # GT-25: read-only handlers bypass the dispatch lock —
                # they don't mutate shared app/service state, so a
                # long-running state-mutating handler on another thread
                # can't block a quick status poll.
                # GT-45: best-effort unlocked re-check (the initial
                # PVT-G5-004 gate already covered the common case).
                if getattr(self, "_cached_shutting_down", False) is True:
                    result = self._shutting_down_error(msg)
                else:
                    result = handler(data, resp)
            else:
                # GT-25 + GT-45: state-mutating handlers serialize on the
                # per-server dispatch lock; the shutdown re-check happens
                # INSIDE the lock so the (locked) handler invocation is
                # atomic with the (locked, on the ShutdownController side)
                # shutdown-flag set — closing the TOCTOU window between
                # the unlocked gate at the top of ``_dispatch`` and the
                # handler call.
                with self._dispatch_lock:
                    if getattr(self, "_cached_shutting_down", False) is True:
                        result = self._shutting_down_error(msg)
                    else:
                        result = handler(data, resp)
        except ConsentRequiredError as exc:
            # DE-31: consent errors get a structured ``consent_required``
            # envelope (NOT the generic ``server.internal_error`` toast)
            # so the renderer's consent-dialog logic can surface a
            # provider-specific dialog. This clause MUST come before any
            # generic ``except Exception`` (at the call sites) — otherwise
            # the consent signal would be swallowed into a generic toast.
            resp["type"] = "error"
            resp["data"] = {
                "code": "server.consent_required",
                "message": str(exc),
                "provider": getattr(exc, "provider", ""),
                "scope": getattr(exc, "scope", ""),
            }
            log.warning(
                "[IPC] consent required for %s: provider=%s scope=%s",
                cmd_key,
                getattr(exc, "provider", ""),
                getattr(exc, "scope", ""),
            )
            result = resp
        finally:
            if _corr_token is not None:
                reset_correlation_id(_corr_token)

        # NEW-IPC-006: ensure every response has a `data` field so the
        # client can always read `resp.data` without a defensive guard.
        # Commands that return None (restart_app/quit_app) send their
        # response internally and skip this.
        if result is not None:
            result.setdefault("data", {})
            # S3-CR-27: stamp the inbound request id onto the response
            # envelope so clients using id-based request/response
            # correlation (the standard JSON-RPC-like pattern in
            # ``usePython.ts``) can match the response back to the
            # originating request. Pre-fix, ``_validate_dict_payload``
            # returned a FRESH error-envelope dict with no ``id`` field;
            # every handler that did ``if error: return error`` discarded
            # the ``resp`` dict (which had ``id`` pre-populated) — so
            # validation rejections orphaned the pending request and the
            # renderer would time out instead of resolving the rejection.
            # Stamping here (in ``_dispatch``) is the defensive single
            # chokepoint: it catches validation errors, handler-thrown
            # exception envelopes, and any future error path that
            # forgets to propagate ``id``.
            if isinstance(msg, dict) and "id" in msg and "id" not in result:
                result["id"] = msg["id"]

        return result

    def _shutting_down_error(self, msg: dict) -> ResponseEnvelope:
        """Build a structured ``server.shutting_down`` error envelope.

        EC-FIX-2 / EC-10: aligned to the namespaced ``server.*`` form so
        the WS path (sidecar_ws.py) and the TCP / stdin path produce
        identical envelopes — restoring the IPC-5 parity contract.

        Factored out of ``_dispatch`` (GT-45) so the initial PVT-G5-004
        gate and the per-handler-call TOCTOU re-check share a single
        source of truth for the envelope shape.

        The return type is ``ResponseEnvelope``
        (``dict[str, object]``) rather than :class:`ErrorEnvelope`
        because TypedDicts are invariant and not subtypes of ``dict``;
        the construction-site ``# ErrorEnvelope contract — see
        validation.py`` comment documents the contract without
        enforcing it at the type level.
        """
        # ErrorEnvelope contract — see validation.py
        err: ResponseEnvelope = {
            "type": "error",
            "data": {
                "code": "server.shutting_down",
                "message": "server is shutting down",
            },
        }
        if isinstance(msg, dict) and "id" in msg:
            err["id"] = msg["id"]
        return err

    # UE-32: the inline _COMMAND_REGISTRY dict literal previously lived
    # here (~180 lines, including the ~30 "REMOVED" historical comments).
    # It has been extracted to
    # :mod:`voice_typer.server.ipc.registry` as the canonical single
    # source of truth (same dict, same keys, same values —
    # behavior-preserving extraction). The class-level alias declared
    # at the top of the class body (``_COMMAND_REGISTRY: dict[str, str]
    # = _COMMAND_REGISTRY``) re-exports it as a class attribute so
    # every existing ``IPCServer._COMMAND_REGISTRY`` call site keeps
    # working unchanged. The "REMOVED" historical comments were
    # consolidated into a ``# Registry history`` block at the top of
    # ``ipc/registry.py`` (the regression guard in
    # ``tests/test_dead_code_stays_removed.py`` already pins the
    # removals independently).

    def _handle_tray_click(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
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

        The return type remains ``ResponseEnvelope`` (not
        :class:`ErrorEnvelope`) because this handler has a non-error
        success path returning ``{"type": "result", "data": {"ok": True}}``.
        The two error-construction sites below are still governed by the
        :class:`ErrorEnvelope` contract (see ``validation.py``).
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
        # EC-FIX-2 / EC-10: align to the namespaced
        # ``server.unknown_tray_item`` form so the renderer's
        # ``ErrorEvent.code`` narrowing switches on a single canonical
        # prefix (``server.*``) across all error emitters.
        if tray is None or not hasattr(tray, "dispatch_tray_action"):
            # ErrorEnvelope contract — see validation.py
            resp["type"] = "error"
            resp["data"] = {"code": "server.unknown_tray_item", "id": item_id}
            return resp

        handled = tray.dispatch_tray_action(item_id)
        if not handled:
            # ErrorEnvelope contract — see validation.py
            resp["type"] = "error"
            resp["data"] = {"code": "server.unknown_tray_item", "id": item_id}
            return resp

        return {"type": "result", "data": {"ok": True}}

    def _handle_unknown_command(
        self, cmd: object | None, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope:
        """Handle the ``__unknown__`` IPC command."""
        # ErrorEnvelope contract — see validation.py
        resp["type"] = "error"
        # ERR-009: include a structured `code` field so clients can
        # distinguish "unknown command" (caller bug / version skew)
        # from "command handler raised" (server-side fault). The
        # previous payload only had a free-text `message`, which
        # forced clients to substring-match the message to tell
        # the two cases apart.
        # EC-FIX-2 / EC-10: align to the namespaced ``server.unknown_command``
        # form so the renderer's ``ErrorEvent.code`` narrowing can
        # switch on a single canonical prefix (``server.*``).
        resp["data"] = {
            "code": "server.unknown_command",
            # PI-23: legacy_code preserves the bare form for back-compat
            # with older Electron builds that substring-match the code
            # field instead of switching on the namespaced prefix.
            "legacy_code": "unknown_command",
            "message": f"Unknown command: {cmd}",
            "command": cmd,
        }
        # No ``cast`` — ``resp`` has been mutated in place to match the
        # :class:`ErrorEnvelope` shape. The return type is
        # ``ResponseEnvelope`` (``dict[str, object]``) rather than
        # :class:`ErrorEnvelope` because TypedDicts are invariant and not
        # subtypes of ``dict``.
        return resp

    def _handle_shutdown(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope:
        """Handle the ``shutdown`` IPC command (EC-FIX-2 / EC-9).

        ADR-0020 §10: cooperative shutdown. The Tauri host sends this
        to ask the backend to release the mic / volume / mutex and
        exit cleanly. Previously this command was intercepted by
        ``sidecar_ws._make_dispatch`` BEFORE dispatch, calling
        ``server.app.quit()`` directly and bypassing the service layer
        — so any future shutdown side-effect added to
        :meth:`VoiceTyperService.quit` silently wouldn't run on Tauri.

        The fix registers ``shutdown`` in :data:`_COMMAND_REGISTRY` so
        the command flows through the shared dispatch table on every
        transport (TCP / stdin / WS) and delegates to
        :meth:`self.service.quit` (the same path ``quit_app`` already
        takes). The ack is returned synchronously; the actual teardown
        happens on the service layer's shutdown controller (which
        schedules cleanup on a background thread, so the ack frame
        reaches the host before the process exits).

        The response shape (``{"type": "result", "data": {"ack": True}}``)
        matches the prior WS-path ack so the Tauri Rust host's
        ``shutdown`` match arm (which awaits this exact envelope) keeps
        working unchanged.

        GT-5: the ack is set on ``resp`` and returned BEFORE
        ``self.service.quit()`` is invoked. ``service.quit()`` runs
        ``_do_cleanup()`` synchronously (30+ steps, ~95s worst case);
        the Tauri host's ``SHUTDOWN_ACK_TIMEOUT_MS=2000ms`` fires long
        before cleanup completes, force-killing the sidecar
        mid-cleanup. Running cleanup on a daemon background thread lets
        the dispatch loop flush the ack frame immediately — the host
        receives the ack within milliseconds and proceeds to its
        graceful-wait while the sidecar's cleanup runs concurrently.

        GT-C3-7: the background-thread cleanup catches ``BaseException``
        (NOT just ``Exception``) so a ``SystemExit`` / ``KeyboardInterrupt``
        raised inside ``service.quit()`` is logged server-side rather
        than silently killing the cleanup thread. The ack is unaffected
        — it was already returned before the thread started.
        """
        # UE-18 (Medium): per-instance shutdown re-entrancy gate. The
        # Tauri host's WS transport can legitimately send ``shutdown``
        # twice (e.g. a slow ack + a supervisor retry, or a WS-close
        # race with the cooperative-shutdown frame). Pre-UE-18, the
        # second invocation spawned a SECOND untracked
        # ``ipc-shutdown-cleanup`` daemon thread — both threads would
        # race into ``service.quit()`` / ``_do_cleanup()`` and
        # double-free the mic stream, hotkey listeners, single-instance
        # mutex, etc. The ``_shutdown_started`` event is set BEFORE the
        # cleanup thread is spawned so the second invocation's no-op is
        # atomic with the first's thread-spawn decision; the second
        # invocation still returns the ack envelope (the host's
        # ``SHUTDOWN_ACK_TIMEOUT_MS`` retry expects it).
        if self._shutdown_started.is_set():
            # Already shutting down — return the same ack envelope so
            # the host's retry timer resolves immediately. No second
            # cleanup thread is spawned; the first one (already running
            # on the ``ipc-shutdown-cleanup`` daemon thread) owns the
            # ``service.quit()`` invocation.
            resp["type"] = "result"
            resp["data"] = {"ack": True}
            return resp
        self._shutdown_started.set()

        # GT-5: build the ack envelope FIRST and return it. The dispatch
        # loop flushes the wire frame before the background cleanup
        # thread can make progress (the daemon thread doesn't get
        # scheduled until the dispatch loop yields or blocks on I/O).
        resp["type"] = "result"
        resp["data"] = {"ack": True}

        # GT-5 + GT-C3-7: run service.quit() on a background daemon
        # thread so the synchronous ~95s _do_cleanup does NOT block the
        # dispatch pool thread that's about to flush the ack frame. The
        # host's 2s SHUTDOWN_ACK_TIMEOUT_MS fires long before cleanup
        # completes; without the background thread, the host force-kills
        # the sidecar mid-cleanup (crash_recovery/history_db flush,
        # recorder.stop, hotkey unregisters, PID file clear, tray.stop,
        # Win32 mutex CloseHandle are all interrupted).
        def _bg_cleanup() -> None:
            # EC-FIX-2: delegate to the service layer (NOT
            # self.app.quit()) so shutdown side-effects added to
            # VoiceTyperService.quit run identically across TCP / stdin
            # / WS transports.
            try:
                self.service.quit()
            except BaseException as e:  # noqa: BLE001 — GT-C3-7
                # The service-layer shutdown controller is best-effort;
                # a failure here (e.g. the tray is mid-teardown, a
                # KeyboardInterrupt during a sleep, or a SystemExit
                # raised deep inside _do_cleanup) must NOT silently kill
                # the cleanup thread and leave resources held. Log
                # server-side so the operator can diagnose; the host's
                # hard-timeout backstop (kill_children) fires either way.
                # ``BaseException`` (rather than ``Exception``) catches
                # ``SystemExit`` / ``KeyboardInterrupt`` too — the ack
                # was already returned, so there's nothing to recover.
                log.error(
                    "[IPC] shutdown: service.quit() raised: %s",
                    e,
                    exc_info=True,
                )

        # UE-18: register the cleanup thread on the central
        # ``_thread_registry`` (if the app provides one) so
        # ``shutdown_all()`` can join it during ``VoiceTyperApp.quit()``
        # — pre-UE-18 the thread was untracked, so a fast process exit
        # could orphan it mid-cleanup and leave resources held.
        cleanup_thread = threading.Thread(
            target=_bg_cleanup,
            name="ipc-shutdown-cleanup",
            daemon=True,
        )
        _registry = getattr(self.app, "_thread_registry", None)
        if _registry is not None:
            _registry.register(
                name="ipc-shutdown-cleanup",
                thread=cleanup_thread,
                stop_event=None,
                join_timeout=2.0,
            )
        cleanup_thread.start()
        return resp

    # ── Output ──────────────────────────────────────────────────────────


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


def parse_ipc_args() -> tuple[int | None, bool]:
    """Parse the IPC server CLI args (EC-8 extraction from ``main()``).

    Returns ``(port, ws_mode)`` where ``port`` is the ``--port N`` value
    (or ``None`` for stdin/stdout mode) and ``ws_mode`` is True when
    ``--ws`` was passed (Tauri sidecar WebSocket mode).

    Side effects:
        - Sets ``VOICE_TYPER_DEBUG=1`` env var when ``--debug`` is passed
          (must be set BEFORE ``_setup_logging()`` is called so the
          debug level is honoured by the log config).
        - Sets ``TAURI_SIDECAR=1`` env var when ``--ws`` is passed so
          downstream gates (heartbeat watchdog, single-instance mutex)
          know to defer to the Tauri host.

    Exits:
        - ``--help`` / ``--version`` exit via argparse (exit code 0).
        - Invalid combos (``--ws`` + ``--port``) or out-of-range ports
          exit via ``sys.exit(EXIT_BAD_ARGS)``.

    The args are parsed BEFORE the single-instance lock is acquired so
    ``--version`` works even when another instance is already running
    (mirrors ``voice_typer.__main__``).
    """
    import argparse
    import importlib.metadata
    import os

    from voice_typer.__main__ import EXIT_BAD_ARGS

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
    parser.add_argument(
        "--allow-stdin",
        action="store_true",
        default=False,
        help=(
            "UE-13: explicitly enable the unauthenticated stdin/stdout "
            "IPC listener (sets VOICE_TYPER_ALLOW_STDIN_IPC=1). The "
            "stdin listener is gated off by default for security: "
            "stdin commands bypass the VOICE_TYPER_IPC_TOKEN handshake. "
            "Use this flag for development and testing only."
        ),
    )
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    if args.debug:
        os.environ["VOICE_TYPER_DEBUG"] = "1"
    # UE-13: --allow-stdin sets the env var that ``IPCServer.start()``
    # checks before spawning the stdin listener. The env var (not the
    # CLI flag) is the canonical gate so direct-API users (tests,
    # ``IPCServer(app); server.start()``) can opt in without going
    # through ``main()`` / argparse.
    if args.allow_stdin:
        os.environ[_STDIN_IPC_ENV_VAR] = "1"
        log.info(
            "[IPC] --allow-stdin: %s=1 set (stdin listener will be spawned if _tcp_mode is False)",
            _STDIN_IPC_ENV_VAR,
        )
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
    # Tauri host's single-instance plugin + supervisor replace
    # them. The env var is set here (rather than required to be set by
    # the host) so a `python -m voice_typer.server.ipc_server --ws`
    # invocation from a terminal also gets the right behavior.
    if ws_mode:
        os.environ["TAURI_SIDECAR"] = "1"
        log.info("[IPC] --ws mode enabled (TAURI_SIDECAR=1 env set)")
    return port, ws_mode


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
    # FR-26 (privacy): tighten the process umask to ``0o077`` (owner-only)
    # at process startup so ALL files created by the sidecar — including
    # the history DB ``-wal`` / ``-shm`` sidecar files that SQLite creates
    # lazily on the first WAL-mode write — are owner-only by default.
    # Previously the chmod loop in ``history_db_internals/schema.py``
    # ran BEFORE the sidecar files existed, so they inherited the parent
    # shell's umask (typically ``0o022`` → files created ``0o644`` =
    # world-readable on multi-user POSIX). ``check_wal_mode`` re-runs
    # the chmod loop after PRAGMA WAL mode is set (closing the
    # creation-time race for the writer's first connection), but a
    # defense-in-depth umask at process startup covers ALL future
    # sidecar recreations (e.g. after a ``wal_checkpoint(TRUNCATE)``
    # drops the sidecars and they get recreated on the next write).
    # Done BEFORE any other subsystem init so every file the sidecar
    # creates benefits. Best-effort — ``os.umask`` always succeeds on
    # POSIX and is a no-op on Windows (which uses ACLs instead).
    if os.name == "posix":
        os.umask(0o077)

    # BRAND-METADATA: set process metadata early, before any subsystem
    # init, so the OS sees the correct identity from the start.
    _set_process_metadata()

    # NEW-CLI-003: import the standardized exit-code constant.
    # EXIT_BAD_ARGS is now used inside ``parse_ipc_args()`` (extracted
    # EC-8); main() needs only EXIT_CRASH for the construction-failure
    # and app.start()-failure paths. Previously EXIT_CRASH was imported
    # but unused and the crash path called sys.exit with a raw literal.
    from voice_typer.__main__ import EXIT_CRASH
    # S1-CR-66: the ``sys.modules[_CANONICAL] = sys.modules["__main__"]``
    # registration hack that used to live at module level has been
    # removed.  See the ARCH-REFAC-002 comment block above the mixin
    # imports for the rationale.

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
            def _on_sigusr1(_signum: int, _frame: FrameType | None) -> None:
                faulthandler.dump_traceback_later(timeout=1.0)

            signal.signal(signal.SIGUSR1, _on_sigusr1)
    except Exception:
        pass  # Not available on all platforms

    # NEW-DOC-006: parse arguments BEFORE acquiring the single-instance
    # lock, so ``--version`` works even when another instance is running
    # (mirrors voice_typer.__main__, which parses args before app.main()).
    # EC-8: the argparse setup + validation + env-var side effects are
    # extracted to ``parse_ipc_args()`` above so ``main()`` no longer
    # mixes CLI parsing with app construction / transport dispatch.
    import os

    from voice_typer.server.app import VoiceTyperApp, _ensure_single_instance, _setup_logging

    port, ws_mode = parse_ipc_args()

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
        # EC-FIX-2 / EC-8: the io.StringIO → traceback → _redact_text →
        # _secure_atomic_write → /tmp-fallback pattern is encapsulated
        # in ``ipc_diagnostics.write_startup_diagnostic`` so the
        # construction-failure and app.start()-failure paths share a
        # single source of truth (the two inline blocks had already
        # drifted once — CR-10's overwrite-vs-append fix was applied to
        # only one). The helper preserves the historical
        # "Voice Typer startup failed at <time>" header so
        # ``tests/test_ipc_server_main_diagnostics.py``'s substring
        # assertions keep passing.
        from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

        write_startup_diagnostic("construction")
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
    # XZ-IPC-001 / d-review Finding 1: ``main()`` NEVER uses the
    # unauthenticated stdin/stdout IPC path. The three launch modes are:
    #   1. ``--port N``        — explicit TCP, Electron connects over the
    #                            network with a session token.
    #   2. ``--ws``            — Tauri sidecar WebSocket (also
    #                            token-authenticated via env var).
    #   3. standalone (neither flag) — auto-pick a port, set a session
    #                            token, start TCP, and launch the
    #                            Electron frontend to connect back. The
    #                            Python process is the parent; stdin is
    #                            the user's terminal (or /dev/null when
    #                            launched by a desktop launcher).
    # In ALL three modes the stdin listener would be an unauthenticated
    # command channel: on Linux TIOCSTI injection is possible, and on
    # every platform an accidental paste of JSON into the terminal
    # triggers unintended IPC commands. We therefore set
    # ``_tcp_mode = True`` UNCONDITIONALLY before ``server.start()`` so
    # ``start()`` skips spawning the stdin listener thread. The standalone
    # path below still calls ``start_tcp()`` (the bound-socket overload)
    # after ``start()`` to begin accepting connections.
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

        standalone_port, standalone_sock = _pick_available_port(IPC_PORT)

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
        # EC-FIX-2 / EC-8: route through the shared diagnostic helper
        # (same as the construction-failure path above). The helper
        # preserves the historical
        # "\n--- app.start() failed at <time> ---\n" header and the
        # CR-10 overwrite-vs-append semantics so repeated relaunch
        # crashes don't grow ``startup-error.log`` without bound.
        from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

        write_startup_diagnostic("app.start()")
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
