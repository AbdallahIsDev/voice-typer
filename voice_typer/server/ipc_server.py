# handlers extracted to handlers/ package as mixins
"""JSON-lines IPC server over the Tauri sidecar WebSocket or stdin/stdout.

Reads JSON commands from the WS transport (``--ws``: event_bus →
sidecar_ws → Tauri host) or stdin (gated dev/test mode), dispatches
to the VoiceTyperApp instance, and writes JSON responses.

Usage (Tauri sidecar WebSocket)::

    python -m voice_typer.server.ipc_server --ws

The retired TCP transport (``--port``) was removed; the flag now
rejects with EXIT_BAD_ARGS and is pinned by entrypoint tests.
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
    from voice_typer.server.providers import AppProtocol

    # concrete type for the ``service`` DI parameter (was ``Any``).
    from voice_typer.server.service import VoiceTyperService

# noqa F401 = intentional). Identity: ipc_server.X is ipc.<leaf>.X.
# pattern-based denylist for IPC get_config (SEC-003).
from voice_typer.server.config_sanitizer import (  # noqa: F401
    _SECRET_CONFIG_FIELDS,
)

# log / _push_event_now live in ipc._helpers (loadable as __main__ without the
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

# Dispatch tables: ipc.registry (CONTRIBUTING §6.4 parity with Rust allowlist).
from voice_typer.server.ipc.registry import (  # noqa: E402
    _COMMAND_REGISTRY,
    _PYTHON_ONLY_COMMANDS,
    _READONLY_COMMANDS,  # noqa: F401  # re-exported for tests (tests/test_ipc_server.py)
)

# _get_rate_limiter is a local thin re-export (see below) so tests that
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


# Thin re-export: canonical get-or-create lives in ipc.rate_limiter; injects
def _get_rate_limiter(server: "object") -> _RateLimiter:
    """Thin re-export; canonical impl in ``voice_typer.server.ipc.rate_limiter``."""
    from voice_typer.server.ipc import rate_limiter as _rate_limiter_mod

    return _rate_limiter_mod._get_rate_limiter(server, _cls=_RateLimiter)


# _push_event_now re-exported from ipc._helpers (shim over event_bus.publish).


# Per-command _handle_* methods live in handlers/ mixins (no circular imports

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
from voice_typer.server.ipc.dispatcher import DispatcherMixin  # noqa: E402
from voice_typer.server.ipc.entrypoint import (  # noqa: E402, F401
    _set_process_metadata,
    main,
    parse_ipc_args,
)
from voice_typer.server.ipc.lifecycle import LifecycleMixin  # noqa: E402
from voice_typer.server.ipc.sender import (  # noqa: E402, F401
    _SHUTDOWN_ALLOWLIST,
    _TCP_PENDING_BUFFER_CAP,
    _TCP_PENDING_DRAIN_CAP,
    OutputMixin,
    _PendingBuffer,
)
from voice_typer.server.ipc.stdin_runner import StdinRunnerMixin  # noqa: E402


class IPCServer(
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
    """Reads JSON commands from WS or stdin, dispatches, writes responses.

    Attributes
    ----------
    app : VoiceTyperApp
        The application instance this server wraps.
    """

    # Per-instance write lock for _send (socket.sendall can interleave
    _tcp_write_lock = threading.Lock()

    # Class aliases for ipc.registry tables (CONTRIBUTING §6.4); __init__
    _COMMAND_REGISTRY: dict[str, str] = _COMMAND_REGISTRY
    _PYTHON_ONLY_COMMANDS: frozenset[str] = _PYTHON_ONLY_COMMANDS

    def __init__(
        self,
        app: "AppProtocol",
        service: "VoiceTyperService | None" = None,
    ) -> None:
        # Ordered construction phases. Each private helper owns a
        self._init_app_and_service(app, service)
        self._init_core_locks()
        self._init_tcp_transport_state()
        self._init_lifecycle_events()
        self._init_ready_and_rate_limit_state()
        self._init_sidecar_ws_state()
        self._init_dispatch_gates()
        self._init_validate_command_registry()

    def _init_app_and_service(
        self,
        app: "AppProtocol",
        service: "VoiceTyperService | None",
    ) -> None:
        """Wire the DI seam: app + service boundary (+ optional cache"""
        self.app = app
        # Once-per-server gate for :meth:`wire_background_integrations`
        self._background_integrations_wired = False
        if service is not None:
            self.service = service
            return

        # wire VoiceTyperService as the service boundary.
        from voice_typer.server.service import VoiceTyperService

        self.service = VoiceTyperService(app)

    def wire_background_integrations(self) -> None:
        """Wire deferred background integrations (post-start phase)."""
        if self._background_integrations_wired:
            return
        self._background_integrations_wired = True

        # background thread, so this wiring is deferred to a daemon
        def _wire_service_cache_invalidator() -> None:
            try:
                recorder_devices = getattr(self.app.recorder, "_devices", None)
                if recorder_devices is not None and hasattr(
                    recorder_devices,
                    "set_service_cache_invalidator",
                ):
                    recorder_devices.set_service_cache_invalidator(lambda: self.service.refresh_microphones(force=True))
            except Exception:
                log.debug(
                    "[IPC] failed to wire service-layer cache invalidator",
                    exc_info=True,
                )

        # RACE-008: daemon=True is acceptable because this thread is a
        _wire_thread = threading.Thread(
            target=_wire_service_cache_invalidator,
            name="ipc-cache-invalidator-wiring",
            daemon=True,
        )
        _wire_thread.start()

    def _init_core_locks(self) -> None:
        """Runtime flag + the two distinct serialization locks."""
        self._running = False
        # use RLock instead of Lock so _hook_tray_set_state
        self._lock = threading.RLock()
        # Per-instance override of the class-level ``_tcp_write_lock``
        self._tcp_write_lock = threading.Lock()

    def _init_tcp_transport_state(self) -> None:
        """Injected-client slots, pending-push buffer, worker pools."""
        self._tcp_client: _TCPLineIO | None = None
        self._tcp_mode = False
        # Bounded FIFO buffer for push events queued while no client connected
        self._pending_tcp: _PendingBuffer = _PendingBuffer(maxlen=_TCP_PENDING_BUFFER_CAP)
        # No listener exists; stop() closes it only if set (always None)
        self._tcp_server_socket: socket.socket | None = None
        # SEC-8: connection handler worker pool slot (stays None, no listener)
        self._tcp_worker_pool: ThreadPoolExecutor | None = None
        self._tcp_dispatch_pool: ThreadPoolExecutor | None = None
        # this server's push callable, registered in the
        self._push_fn: typing.Callable[[dict], None] | None = None

    def _init_lifecycle_events(self) -> None:
        """Heartbeat / stdin / relaunch / shutdown-completion events."""
        # first ``heartbeat`` IPC command.  The watchdog daemon thread
        self._last_heartbeat_at: float | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop_event = threading.Event()
        # Set by the shared shutdown path (``shutdown.cleanup.do_cleanup``)
        self._shutdown_completed_event = threading.Event()
        # Declare ``_stdin_thread`` as ``Thread | None`` so the
        self._stdin_thread: threading.Thread | None = None
        # PERF-005: the Tauri host sets this event when it receives the
        self._relaunch_ack_event = threading.Event()

    def _init_ready_and_rate_limit_state(self) -> None:
        """First-connection ready flag + per-instance rate limiter slot."""
        # ``IPCServer`` instance passed to ``sidecar_ws.run()`` so the ``ready``
        self._ready_emitted: bool = False

        # ``# type: ignore[attr-defined]`` silencing the missing-attribute
        self._rate_limiter_instance: _RateLimiter | None = None

    def _init_sidecar_ws_state(self) -> None:
        """WS dispatch pool, inflight accounting, graceful-shutdown slots.

        The 5 WS-pool attributes live on ``IPCServer`` itself (were
        dynamically injected by the module-level
        ``_get_ws_dispatch_pool`` / ``_get_ws_connection_semaphore``
        helpers in ``sidecar_ws.py``, with ``# type: ignore[attr-defined]``
        silencing the missing-attribute diagnostic at every assignment
        / read site). Declaring them here means the type checker can
        verify both the ``setattr`` sites and the ``getattr`` fast paths
        in ``sidecar_ws.py``; 9 ``# type: ignore[attr-defined]``
        suppressions in ``sidecar_ws.py`` are removed as a result.

        The attributes are genuinely ``Optional`` where marked: they
        stay ``None`` until the WS dispatch path is first entered (a
        server running in TCP / standalone mode never touches them).
        The lazy-attach pattern is preserved: ``sidecar_ws._get_ws_dispatch_pool``
        (and siblings) still call ``getattr(server, "_ws_...", None)``
        first and only construct + assign on miss, but the assignment
        is now a plain ``server._ws_... = x`` with no type-ignore.
        NOTE: the pool / event / lock / count are PRE-CONSTRUCTED here
        (not left ``None`` for lazy first-use): the WS dispatch factory
        (``sidecar_ws._make_dispatch``) previously created them on the
        first frame, but the creation logic is pure constructor work
        with no WS-loop dependency, doing it here removes a lazy-
        init branch from every dispatch and from the shutdown drain
        path. ``_ws_connection_semaphore`` stays ``None``: an
        ``asyncio.Semaphore`` binds to the loop it is first awaited on,
        and ``IPCServer.__init__`` runs OUTSIDE any loop, so it must
        remain lazily created by the WS connection path
        (``sidecar_ws_internals.connection._get_ws_connection_semaphore``).
        """
        self._ws_dispatch_pool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-dispatch",
        )
        self._ws_drained_event: threading.Event = threading.Event()
        self._ws_drained_event.set()  # initially drained, count is 0
        self._ws_inflight_lock: threading.Lock = threading.Lock()
        self._ws_inflight_count: int = 0
        self._ws_connection_semaphore: asyncio.Semaphore | None = None

        # ``# type: ignore[attr-defined]`` suppression, the injectors
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_authenticated_conns: set | None = None
        self._ws_dispatch_futures: set | None = None
        self._ws_graceful_stop_requested: bool = False
        self._ws_graceful_shutdown_installed: bool = False
        self._ws_encode_pool: ThreadPoolExecutor | None = None

        # Explicit hook slot for the WS graceful-shutdown wrapper (see
        self.ws_graceful_shutdown: typing.Callable[[], None] | None = None
        self._ws_stop_hook: typing.Callable[[], None] | None = None

    def _init_dispatch_gates(self) -> None:
        """Hot-path shutdown cache, dispatch lock, re-entrancy gate."""
        # NOTE: see docs/code-notes/ipc.md
        self._cached_shutting_down: bool = False

        # per-server dispatch lock serializing state-mutating
        self._dispatch_lock = threading.RLock()

        #  (Medium): per-instance shutdown re-entrancy gate.
        self._shutdown_started: threading.Event = threading.Event()

    def _init_validate_command_registry(self) -> None:
        """Registry-typo validation at construction time."""
        for _cmd, _method_name in self._COMMAND_REGISTRY.items():
            _bound = getattr(self, _method_name, None)
            if not callable(_bound):
                raise RuntimeError(
                    f"_COMMAND_REGISTRY[{_cmd!r}] resolves to non-callable "
                    f"attribute {_method_name!r} on IPCServer, registry "
                    "entry and handler method have drifted out of sync."
                )

    # ── Lifecycle / Dispatcher / Stdin-runner methods live on the
    pass  # class body intentionally minimal: see mixins above.


# block above). The verbatim bodies live there; ``inspect.getsource(


if __name__ == "__main__":
    main()
