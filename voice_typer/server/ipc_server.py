# handlers extracted to handlers/ package as mixins
"""JSON-lines IPC server over stdin/stdout OR TCP.

Reads JSON commands from stdin (legacy) or a TCP socket (Electron),
dispatches to the VoiceTyperApp instance, and writes JSON responses.

Usage (TCP mode — Electron)::

    python -m voice_typer.server.ipc_server --port 9876

Usage (stdin/stdout mode — ``voice-typer`` CLI)::

    python -m voice_typer.server.ipc_server
"""

import asyncio  # noqa: F401  # re-exported for tests (ipc_server.asyncio) + asyncio.Semaphore annotation below
import contextlib  # noqa: F401  # re-exported for tests (ipc_server.contextlib)
import json  # noqa: F401  # re-exported for tests (ipc_server.json)
import os  # noqa: F401  # re-exported for tests (ipc_server.os)
import socket
import sys  # noqa: F401  # re-exported for tests (ipc_server.sys)
import threading
import time  # noqa: F401  # re-exported for tests (ipc_server.time.monotonic patch target)
import typing
from concurrent.futures import ThreadPoolExecutor
from types import FrameType  # noqa: F401  # re-exported for tests
from typing import TYPE_CHECKING

from voice_typer.server import event_bus  # noqa: F401  # re-exported for tests
from voice_typer.server._paths import IPC_PORT  # noqa: F401  # re-exported for tests
from voice_typer.server.asr_errors import ConsentRequiredError  # noqa: F401  # re-exported for tests
from voice_typer.server.log import (  # noqa: F401  # re-exported for tests
    reset_correlation_id,
    set_correlation_id,
)

if TYPE_CHECKING:
    # Typed ``app`` parameter on ``IPCServer.__init__``. The
    # protocol is structural (``@runtime_checkable``); ``MagicMock``
    # fixtures still satisfy it (the runtime check inspects attribute
    # names, not types), so test code that passes a ``MagicMock`` does
    # not need to import ``AppProtocol``.
    from voice_typer.server.providers import AppProtocol

    # concrete type for the ``service`` DI parameter (was ``Any``).
    # Imported under TYPE_CHECKING to avoid a runtime circular import —
    # VoiceTyperService imports from this module's neighbours.
    from voice_typer.server.service import VoiceTyperService

#  (consolidated): the helper leaf submodules under
# ``voice_typer.server.ipc`` (``validation.py``, ``transport.py``,
# ``rate_limiter.py``, ``history_bounds.py``) are the CANONICAL source
# for every helper this module uses.  The dead duplicates
# ``ipc/server.py``, ``ipc/main.py``, ``ipc/process_meta.py`` and
# ``ipc/push_events.py`` were deleted ( / test_dead_code_stays_removed)
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
# that version uses the  pattern-based denylist (defense-in-depth
# beyond the explicit frozenset); the config_sanitizer version is a
# simpler implementation kept for service-layer callers that don't need
# the pattern matching.
from voice_typer.server.config_sanitizer import (  # noqa: F401
    _SECRET_CONFIG_FIELDS,
)

# the ``log`` / ``_push_event_now`` helpers used to be
# defined inline here.  They moved to the
# ``voice_typer.server.ipc._helpers`` leaf submodule so that
# ``ipc_server.py`` can be loaded as ``__main__`` (via
# ``python -m voice_typer.server.ipc_server``) without needing the
# ``sys.modules[_CANONICAL] = sys.modules["__main__"]`` registration
# hack to keep lazy imports (in providers.py / sidecar_ws.py / app.py /
# __main__.py) resolving to the same object.  The names are re-exported
# here ('noqa: F401' on the import line) so existing
# ``from voice_typer.server.ipc_server import log`` /
# ``... import _push_event_now`` callers keep working unchanged.
# ``_READONLY_COMMANDS`` previously lived in ``ipc._helpers``;
# it now lives in ``voice_typer.server.ipc.registry`` (see the import
# block below).  The ``ipc._helpers`` duplicate is a legacy leftover
# (outside the  disjoint set) and is no longer the authoritative
# source.
#  typed response envelope and command-handler aliases.
#
# ``ResponseEnvelope`` is the canonical shape of every IPC frame pushed or
# dispatched: a dict with at least ``type`` (str) and optional ``data``,
# ``id``. Using a type alias (instead of bare ``dict``) lets the type
# checker verify handler signatures and the dispatch-table value type, so
# a typo in a handler-method name surfaces at IPCServer construction
# (where the bound-method cache is built) rather than at dispatch time.
#  ResponseEnvelope + CommandHandler canonical definitions
# moved to voice_typer.server.ipc.validation (breaks the circular import
# and lets handler modules import them from a non-god-module location).
# ``_READONLY_COMMANDS`` now lives in ``ipc.registry`` (
# extraction — see the import above).  The set lists dispatch commands
# whose handlers do NOT mutate shared app/service state; they bypass the
# per-server ``_dispatch_lock`` so a long-running state-mutating handler
# (e.g. ``download_model``) does not block a quick status poll from a
# second authenticated connection.
#  (High): the unauthenticated stdin/stdout IPC listener is gated
# behind this env var. ``start()`` refuses to spawn the stdin listener
# thread when ``_tcp_mode`` is False AND the env var is not set to
# ``"1"`` — closing the "unprotected stdin IPC path is still the
# default" hole. The ``--allow-stdin`` CLI flag in :func:`parse_ipc_args`
# sets this env var as the alternative gate for development / testing.
# Production callers (``main()``) always set ``_tcp_mode = True`` before
# ``start()`` so the gate never fires in production; the gate exists to
# catch direct-API / test paths that would otherwise silently expose an
# unauthenticated command channel on the user's terminal.
# (consolidated): the canonical definition now lives in
# :mod:`voice_typer.server.ipc._helpers` (the leaf helpers module) so
# both ``ipc/entrypoint.py`` (the ``--allow-stdin`` flag setter) and
# ``ipc/lifecycle.py`` (the ``start()`` gate) import the same single
# source. The name is re-exported here so existing
# ``from voice_typer.server.ipc_server import _STDIN_IPC_ENV_VAR``
# callers AND the ``hasattr(ipc_server_mod, "_STDIN_IPC_ENV_VAR")`` test
# contract (UE-13) keep working unchanged.
from voice_typer.server.ipc._helpers import (  # noqa: E402, F401
    _STDIN_IPC_ENV_VAR,  # noqa: F401
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

# the command-dispatch registry, the read-only command set, and
# the Python-only exception set all live in the
# mod:`voice_typer.server.ipc.registry` leaf submodule.  Pre-
# ``_COMMAND_REGISTRY`` + ``_PYTHON_ONLY_COMMANDS`` were class attributes
# on :class:`IPCServer` (defined ~1,400 lines into this 2,100-line
# god-module) and ``_READONLY_COMMANDS`` lived in ``ipc._helpers``; the
# split made the three-layers-must-agree parity contract harder to
# reason about.  The extraction is behavior-preserving — same dict,
# same keys, same values.  The names are re-exported here
# ('noqa: F401' on the import line) so existing
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
from voice_typer.server.ipc.registry import (  # noqa: E402
    _COMMAND_REGISTRY,
    _PYTHON_ONLY_COMMANDS,
    _READONLY_COMMANDS,  # noqa: F401  # re-exported for tests (tests/test_ipc_server.py)
)

# NOTE: ``_get_rate_limiter`` is intentionally NOT imported here — it is
# defined locally below as a thin re-export (see the
#  comment block) so tests that monkey-patch
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


# ──: per-process rate limiter get-or-create ───────────────
#
# this is a THIN RE-EXPORT — the canonical implementation lives in
# ``voice_typer.server.ipc.rate_limiter._get_rate_limiter``. Tests in
# ``tests/test_r4_f18_rate_limiter_concurrent_init.py`` and
# ``tests/test_cr_fixes.py`` monkey-patch ``ipc_server._RateLimiter`` with
# a counting stand-in to widen the race window ( / ). The
# re-export delegates to the canonical implementation with the patched
# ``_RateLimiter`` class injected via ``_cls=``, so the patched class is
# still observed (preserving the test contract) while the get-or-create
# logic is single-sourced in the leaf module.
def _get_rate_limiter(server: "object") -> _RateLimiter:
    """Thin re-export — canonical implementation in
    ``voice_typer.server.ipc.rate_limiter``.

    Tests monkey-patch ``ipc_server._RateLimiter`` to widen the race
    window ( / ). We delegate to the canonical implementation
    with the patched class injected via ``_cls=`` so the patched class
    is still observed.
    """
    from voice_typer.server.ipc import rate_limiter as _rate_limiter_mod

    return _rate_limiter_mod._get_rate_limiter(server, _cls=_RateLimiter)


# Module-level push hook.  ``_push_event_now`` now lives in
# ``ipc._helpers`` ( refactor — see the import above).  It is a
# thin shim over ``event_bus.publish`` so existing lazy imports
# (``from voice_typer.server.ipc_server import _push_event_now``) keep
# working.  Domain code should call ``event_bus.publish`` directly.
# The _push_event_registry/_push_event_registry_lock aliases
# and _set_push_event/_clear_push_event shims have been removed — domain
# code and tests now call ``event_bus.subscribe`` /
# ``event_bus.unsubscribe`` directly.


# the per-command ``_handle_*`` methods live in the
# ``handlers/`` subpackage as mixin classes.  The handler mixins import
# their own helpers (``log`` from ``handlers._log``, validation from
# ``ipc.validation``, etc.) and do NOT import from this module, so there
# is no circular dependency to break.
#
# the ``sys.modules[_CANONICAL] = sys.modules["__main__"]``
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


from voice_typer.server.handlers.cloud_test_handlers import (  # noqa: E402
    CloudTestHandlersMixin,
)
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
from voice_typer.server.handlers.repaste_handlers import RepasteHandlersMixin  # noqa: E402
from voice_typer.server.handlers.status_handlers import StatusHandlersMixin  # noqa: E402
from voice_typer.server.handlers.system_handlers import SystemHandlersMixin  # noqa: E402
from voice_typer.server.handlers.templates_handlers import TemplatesHandlersMixin  # noqa: E402
from voice_typer.server.handlers.vocabulary_automation_handlers import (  # noqa: E402
    VocabularyAutomationHandlersMixin,
)
from voice_typer.server.handlers.vocabulary_handlers import VocabularyHandlersMixin  # noqa: E402

# Lifecycle / dispatcher / stdin-runner mixins extracted from the
# ``IPCServer`` class body (XZ-IPC-007 follow-up). Each mixin module
# owns the verbatim method bodies; the methods are mixed into
# :class:`IPCServer` via multiple inheritance. ``inspect.getsource``
# resolves through MRO so the source-string-pinning tests that do
# ``inspect.getsource(IPCServer.<method>)`` keep finding the moved
# bodies — every pinned substring is preserved verbatim.
from voice_typer.server.ipc.dispatcher import DispatcherMixin  # noqa: E402

# Process entry-point functions (``main`` / ``parse_ipc_args`` /
# ``_set_process_metadata``) extracted to
# :mod:`voice_typer.server.ipc.entrypoint`. Re-exported here so existing
# ``from voice_typer.server.ipc_server import main`` /
# ``... import parse_ipc_args`` callers — and the
# ``inspect.getsource(ipc_server.main)`` source-string-pinning tests in
# ``tests/test_ipc_server.py``, ``tests/test_startup_error_log_cap.py``,
# ``tests/server/test_ipc_server_regressions.py``,
# ``tests/test_electron_ipc_and_build.py``,
# ``tests/regressions/cli_exit_codes_test.py``,
# ``tests/app/test_app_lifecycle_fixes.py`` — keep working unchanged.
# The tests' substring checks (``_frame: FrameType | None``,
# ``sys.exit(EXIT_CRASH)``, ``server._tcp_mode = True``,
# ``startup-error.log``, ``write_startup_diagnostic(``,
# ``_ensure_single_instance``) all match the verbatim body in
# ``ipc/entrypoint.py``.
from voice_typer.server.ipc.entrypoint import (  # noqa: E402, F401
    _set_process_metadata,
    main,
    parse_ipc_args,
)
from voice_typer.server.ipc.lifecycle import LifecycleMixin  # noqa: E402

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
    _PendingBuffer,
)
from voice_typer.server.ipc.stdin_runner import StdinRunnerMixin  # noqa: E402
from voice_typer.server.ipc.transport_tcp import TCPTransportMixin  # noqa: E402


class IPCServer(
    TCPTransportMixin,
    OutputMixin,
    StdinRunnerMixin,
    DispatcherMixin,
    LifecycleMixin,
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
    CloudTestHandlersMixin,
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
    # the  "slow client blocks all dispatchers" problem
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

    # ``_COMMAND_REGISTRY`` and ``_PYTHON_ONLY_COMMANDS`` are
    # canonical to :mod:`voice_typer.server.ipc.registry` (imported at
    # module top — see the ```` comment block above).  They are
    # re-aliased here as class attributes so every existing
    # ``IPCServer._COMMAND_REGISTRY`` / ``IPCServer._PYTHON_ONLY_COMMANDS``
    # call site (pinned by ``tests/test_ipc_shutdown_registry.py``,
    # ``tests/test_ec4_python_command_registry_parity.py``,
    # ``tests/test_ipc_command_registry_sync.py``,
    # ``tests/tauri/mig19/test_phase4_validation.py``,
    # ``tests/tauri/test_tauri_sidecar_gate.py``) keeps working
    # unchanged.  ``__init__`` iterates over ``self._COMMAND_REGISTRY``
    # to typo-validate every entry resolves to a callable bound method
    # ( / ); the iteration observes this alias and therefore
    # the registry's canonical dict.
    _COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY
    _PYTHON_ONLY_COMMANDS: frozenset[str] = _PYTHON_ONLY_COMMANDS

    def __init__(
        self,
        app: "AppProtocol",
        service: "VoiceTyperService | None" = None,
    ) -> None:
        # dependency-injection seam.
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
            # wire VoiceTyperService as the service boundary.
            # IPC routes delegate through the service instead of calling
            # self.app directly. This allows a second transport (CLI,
            # gRPC) to reuse the same service layer without duplicating
            # app glue.
            from voice_typer.server.service import VoiceTyperService

            self.service = VoiceTyperService(app)
            # wire the service-layer mic cache invalidator so
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
                if recorder_devices is not None and hasattr(recorder_devices, "set_service_cache_invalidator"):
                    recorder_devices.set_service_cache_invalidator(lambda: self.service.refresh_microphones(force=True))
            except Exception:
                log.debug(
                    "[IPC] failed to wire service-layer cache invalidator",
                    exc_info=True,
                )
        self._running = False
        # use RLock instead of Lock so _hook_tray_set_state
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
        # Bounded FIFO buffer for push events queued while the TCP
        # client is disconnected. Pre-fix this was a plain ``list[str]``
        # and the cap-drop in ``OutputMixin._send`` was an O(N)
        # ``del list[:dropped]`` on every append (15-50 Hz waveform-bubble
        # push rate while the client was disconnected). Now a
        # ``_PendingBuffer`` (a ``deque`` subclass with ``maxlen``) — the
        # cap is enforced automatically by ``append``/``extend`` (O(1)
        # popleft on overflow). The manual cap-drop logic in ``_send``
        # is kept in source for backward compat with the source-string
        # checks in ``tests/test_ipc_pending_tcp_remerge.py`` but is dead
        # code at runtime (the ``len > cap`` guard never trips because
        # ``maxlen`` already prevents growth).
        self._pending_tcp: _PendingBuffer = _PendingBuffer(maxlen=_TCP_PENDING_BUFFER_CAP)
        # store the listening TCP server socket so stop()
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
        #
        # A SEPARATE ``_tcp_dispatch_pool`` (lazily created in
        # ``start_tcp``) handles dispatch submissions so a long-lived
        # connection read-loop can never starve short-lived dispatches.
        # Pre-split both work types shared this single pool: with N
        # concurrent authenticated connections each holding a worker on
        # its blocking read-loop, dispatch submissions queued behind
        # them indefinitely (full starvation at the connection cap).
        self._tcp_worker_pool: ThreadPoolExecutor | None = None
        self._tcp_dispatch_pool: ThreadPoolExecutor | None = None
        # this server's push callable, registered in the
        # module-level _push_event_registry on start() and unregistered
        # on stop().  Tracked on the instance so stop() can remove just
        # our callable without affecting other active servers.
        self._push_fn: typing.Callable[[dict], None] | None = None

        # heartbeat watchdog state.
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

        # per-instance flag (was module-level in sidecar_ws.py).
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

        # declare the per-instance rate-limiter attribute on
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

        # Declare the 5 WS-pool attributes on the ``IPCServer`` class
        # itself (were dynamically injected by the module-level
        # ``_get_ws_dispatch_pool`` / ``_get_ws_connection_semaphore``
        # helpers in ``sidecar_ws.py``, with ``# type: ignore[attr-defined]``
        # silencing the missing-attribute diagnostic at every assignment
        # / read site). Declaring them here means the type checker can
        # verify both the ``setattr`` sites and the ``getattr`` fast paths
        # in ``sidecar_ws.py``; 9 ``# type: ignore[attr-defined]``
        # suppressions in ``sidecar_ws.py`` are removed as a result.
        #
        # The attributes are genuinely ``Optional`` — they are ``None``
        # until the WS dispatch path is first entered (a server running
        # in TCP / standalone mode never touches them). The lazy-attach
        # pattern is preserved: ``sidecar_ws._get_ws_dispatch_pool``
        # (and siblings) still call ``getattr(server, "_ws_...", None)``
        # first and only construct + assign on miss — but the assignment
        # is now a plain ``server._ws_... = x`` with no type-ignore.
        self._ws_dispatch_pool: ThreadPoolExecutor | None = None
        self._ws_drained_event: threading.Event | None = None
        self._ws_inflight_lock: threading.Lock | None = None
        self._ws_inflight_count: int = 0
        self._ws_connection_semaphore: asyncio.Semaphore | None = None

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

        # per-server dispatch lock serializing state-mutating
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

        #  (Medium): per-instance shutdown re-entrancy gate.
        # ``_handle_shutdown`` checks this event at the top and no-ops
        # the second invocation. Pre-, a double-``shutdown`` (e.g.
        # the Tauri host's WS transport retrying after a slow ack) would
        # spawn a SECOND untracked ``ipc-shutdown-cleanup`` daemon thread
        # — both threads would race into ``service.quit()`` /
        # ``_do_cleanup()`` and double-free the mic stream, hotkey
        # listeners, single-instance mutex, etc. The event is set BEFORE
        # the cleanup thread is spawned so the second invocation's
        # no-op is atomic with the first's thread-spawn decision.
        self._shutdown_started: threading.Event = threading.Event()

        # registry-typo validation at construction time. We resolve
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

    # ── Lifecycle / Dispatcher / Stdin-runner methods live on the
    # corresponding mixins (``LifecycleMixin``, ``DispatcherMixin``,
    # ``StdinRunnerMixin`` — see the imports above). They were moved
    # verbatim from this class body so ``inspect.getsource(IPCServer.X)
    # still resolves through MRO to the mixin's source, preserving
    # every source-string-pinning test contract.
    pass  # class body intentionally minimal — see mixins above.


# ── Entry point ─────────────────────────────────────────────────────────
# ``main`` / ``parse_ipc_args`` / ``_set_process_metadata`` are now
# imported from :mod:`voice_typer.server.ipc.entrypoint` (see the import
# block above). The verbatim bodies live there; ``inspect.getsource(
# ipc_server.main)`` resolves through the re-export and returns the
# source from the entrypoint module, preserving the source-string-pinning
# test contracts that assert substrings in ``main``'s body.


if __name__ == "__main__":
    main()
