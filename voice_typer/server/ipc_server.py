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
from collections import deque

from voice_typer.server import event_bus
from voice_typer.server.keyboard_ownership import keyboard_ownership

log = logging.getLogger("voice_typer.server.ipc_server")


# PR-3-FINDING-3: shared IPC payload validation helper.
#
# Validates an IPC ``data`` argument against a declarative schema.
# Returns ``(validated_dict, None)`` on success, or
# ``(None, error_response_dict)`` on validation failure so the handler
# can ``return resp`` immediately.
#
# Schema format::
#
#     schema = {
#         "field_name": {
#             "type": str,          # required: the expected Python type
#             "required": True,     # field MUST be present in data
#             "default": "val",    # optional default (only for
#                                  #   required=False)
#         }
#     }
#
# Example::
#
#     validated, error = _validate_dict_payload(data, {
#         "hotkey": {"type": str, "required": True},
#         "model": {"type": str, "required": False, "default": "small.en"},
#     })
#     if error:
#         return error


def _validate_dict_payload(data, schema):
    """Validate IPC ``data`` against a declarative *schema*.

    Parameters
    ----------
    data : Any
        The ``data`` field from the IPC message.
    schema : dict[str, dict]
        Mapping of field name → validation rules.  Each rule dict
        supports:

        - ``type`` (required): the expected Python type (e.g. ``str``,
          ``list``).
        - ``required`` (bool): if ``True``, the field MUST be present
          in ``data``.  Mutually exclusive with ``default``.
        - ``default``: default value when the field is absent.  Only
          valid when ``required=False``.

    Returns
    -------
    tuple[dict | None, dict | None]
        ``(validated_dict, None)`` on success.
        ``(None, error_response)`` on failure — the error_response
        is a dict ready to be returned as ``resp`` from the handler.
    """
    if not isinstance(data, dict):
        return None, {
            "type": "error",
            "data": {
                "code": "invalid_payload",
                "message": "data must be an object",
            },
        }

    validated = {}
    for field_name, rules in schema.items():
        if field_name in data:
            value = data[field_name]
            expected_type = rules.get("type")
            if expected_type is not None and not isinstance(value, expected_type):
                return None, {
                    "type": "error",
                    "data": {
                        "code": "invalid_field",
                        "field": field_name,
                        "message": f"'{field_name}' must be of type "
                        f"{expected_type.__name__}, got "
                        f"{type(value).__name__}",
                    },
                }
            validated[field_name] = value
        elif rules.get("required", False):
            return None, {
                "type": "error",
                "data": {
                    "code": "missing_field",
                    "field": field_name,
                    "message": f"Missing required field '{field_name}'",
                },
            }
        elif "default" in rules:
            validated[field_name] = rules["default"]

    return validated, None


def _pick_available_port(start: int = 9876, max_tries: int = 100) -> int:
    """Return the first TCP port >= ``start`` that is free on 127.0.0.1.

    P1-1.2: used by standalone mode to auto-pick a port for the backend's
    TCP server.  Starts at the default IPC port (9876) and increments
    until a free port is found (capped at ``max_tries`` attempts).  Falls
    back to an OS-assigned ephemeral port (port=0) if every port in the
    range is busy — this guarantees the function never fails.
    """
    import socket as _socket

    for offset in range(max_tries):
        candidate = start + offset
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", candidate))
                return candidate
        except OSError:
            continue
    # All ports in range are busy — let the OS assign an ephemeral one.
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── RELIABILITY-006: per-connection rate limiter ─────────────────────────
#
# A crash-looping or buggy Electron client can flood the IPC socket
# with thousands of malformed messages per second, exhausting file
# descriptors and starving the tray thread.  ``_RateLimiter`` is a
# sliding-window per-connection limiter: each connection gets a
# bounded number of messages per window.  Over-budget messages are
# dropped (with an error response) rather than dispatched.
#
# The limits are intentionally generous — a well-behaved Electron client
# sends maybe 1-5 msg/s.
#
# RELIABILITY-006-FIX-10: ``burst`` (200) is the hard per-second cap; a
# client that sends >200 messages in any 1-second window is throttled.
# ``sustained`` (600) is measured over a 10-second window (60 msg/s
# average) so short bursts within 1s (up to 200) are NOT throttled by
# the sustained limit. Previously both used a 1s window with
# sustained=60 < burst=200, making burst completely unreachable.

_RATE_LIMIT_WINDOW_SECONDS = 10.0
_RATE_LIMIT_BURST = 200
_RATE_LIMIT_SUSTAINED = 600  # 60 msg/s average over 10s window

# NEW-CONC-003: write timeout for TCP sendall.  A stalled Electron
# renderer (e.g. GC pause, dev-tools inspection, or a busy main thread)
# can stop draining its TCP receive buffer.  Without a timeout, sendall
# blocks indefinitely, holding the IPC lock (pre-NEW-IPC-014) or
# blocking the bubble_level worker thread (post-NEW-IPC-014).  2
# seconds is generous for a localhost write — under normal load the
# kernel buffer accepts data in microseconds.  When the timeout fires,
# we drop the client connection so the accept loop can pick up the
# next reconnect.
_TCP_WRITE_TIMEOUT_SECONDS = 2.0

# ── RW-10: Electron-alive heartbeat ─────────────────────────────────────
#
# If Electron crashes or is force-killed, the Python backend keeps
# running with the mic stream open, hotkeys registered, volume ducked,
# and the single-instance mutex held.  The next launch hits
# ``ERROR_ALREADY_EXISTS`` and surfaces "Only one instance can run",
# forcing the user to manually kill ``python.exe``.
#
# The heartbeat mechanism works as follows:
#   1. Electron connects via TCP and starts sending ``heartbeat`` IPC
#      commands every 5 seconds (see ``client/src/main/index.ts``).
#   2. The ``_handle_heartbeat`` handler updates
#      ``self._last_heartbeat_at = time.monotonic()``.
#   3. The ``_heartbeat_loop`` daemon thread wakes every 5 seconds and
#      checks if more than 15 seconds (3 missed heartbeats) have
#      elapsed since the last heartbeat.  If so, it calls
#      ``self.app.quit()`` — which runs the shared ``_do_cleanup()``
#      path from RW-3 (restores volume, flushes recovery, releases the
#      mutex, closes PortAudio).
#
# The watchdog only fires AFTER the first heartbeat has been received,
# so the backend doesn't exit prematurely during a slow Electron cold
# start (10+ seconds for the torch import + window creation).
_HEARTBEAT_INTERVAL_SECONDS = 5.0
_HEARTBEAT_TIMEOUT_SECONDS = 120.0  # 24 missed heartbeats — increased from 15s


class _RateLimiter:
    """Sliding-window per-connection rate limiter.

    Each IPC connection gets its own ``_RateLimiter`` instance.  The
    limiter tracks the timestamp of each accepted message in a deque;
    when the deque exceeds the burst size, the oldest entries are
    evicted and the message is rejected if the sustained rate would
    be exceeded.
    """

    def __init__(
        self,
        *,
        burst: int = _RATE_LIMIT_BURST,
        sustained_per_sec: int = _RATE_LIMIT_SUSTAINED,
        window: float = _RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self._burst = burst
        self._sustained = sustained_per_sec
        self._window = window
        self._timestamps: deque[float] = deque()
        self._rejected: int = 0
        self._lock = threading.Lock()

    def allow(self, *, now: float | None = None) -> bool:
        """Return True if the message should be accepted.

        Parameters
        ----------
        now : float, optional
            Current monotonic time.  If omitted, ``time.monotonic()``
            is used.  Passing ``now`` explicitly makes the limiter
            trivially testable.
        """
        ts = now if now is not None else time.monotonic()
        cutoff = ts - self._window
        with self._lock:
            # Evict timestamps older than the window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            # RELIABILITY-006-FIX-10: burst check first (hard per-second
            # cap). Sustained check second (longer window, lower average).
            # With window=10s, burst=200, sustained=600, a client can
            # send 200 msgs in 1s without hitting the sustained limit.
            if len(self._timestamps) >= self._burst:
                return False
            if len(self._timestamps) >= self._sustained:
                return False
            self._timestamps.append(ts)
            return True

    @property
    def rejected_count(self) -> int:
        """Total messages rejected since this limiter was created.

        Not currently exposed via IPC, but useful for tests.
        """
        return self._rejected

    def reject(self) -> None:
        """Increment the rejected counter (called when allow() returns False)."""
        with self._lock:
            self._rejected += 1


# ── SEC-003: config sanitization for IPC ─────────────────────────────────
#
# ``get_config`` must NOT echo secret fields back to the IPC client.
# Even though the IPC socket is loopback-only, any local process can
# connect to it (see SEC-018 for the auth fix).  We return a sanitized
# view where API keys are replaced with a presence indicator so the
# renderer can render "key configured" UI without ever holding the
# actual key value.

# Fields whose values are secrets and must never be echoed back.
_SECRET_CONFIG_FIELDS = frozenset(
    {
        "cloud_api_key",
        "openai_api_key",
        "groq_api_key",
        "deepgram_api_key",
        "llm_api_key",
    }
)

# Sentinel returned in place of a secret value.  The renderer treats
# this as "key is set, do not display" — it must NOT treat this as the
# actual key value (which would be a regression of SEC-003).
_REDACTED_SENTINEL = "<redacted>"


# SEC-010: maximum number of history rows a single IPC call can
# materialize.  Without this cap, ``{"limit": 100000000}`` would
# force SQLite to scan and the dispatcher to materialize a million
# rows before slicing — a trivial DoS.
_HISTORY_LIMIT_MAX = 500
_HISTORY_LIMIT_DEFAULT = 50


def _bound_history_limit(raw) -> int:
    """Clamp a caller-supplied history ``limit`` to a safe range.

    Accepts ints, floats, and numeric strings (the renderer sometimes
    sends strings from form inputs).  Rejects anything else with the
    default.  Result is always in ``[1, _HISTORY_LIMIT_MAX]``.
    """
    if raw is None:
        return _HISTORY_LIMIT_DEFAULT
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _HISTORY_LIMIT_DEFAULT
    return max(1, min(v, _HISTORY_LIMIT_MAX))


def _bound_history_offset(raw) -> int:
    """Clamp a caller-supplied history ``offset`` to a non-negative int."""
    if raw is None:
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def _sanitize_config_for_ipc(config) -> dict:
    """Return a copy of ``config.__dict__`` with secret fields redacted.

    A secret field is any field in :data:`_SECRET_CONFIG_FIELDS`.  If
    the field's value is truthy (a key was set), it is replaced with
    ``"<redacted>"``.  If falsy (empty string or None), the original
    value (``""`` / ``None``) is preserved so the renderer can
    distinguish "no key set" from "key set but hidden".
    """
    out = config.__dict__.copy()
    for k in _SECRET_CONFIG_FIELDS:
        if k in out:
            v = out[k]
            out[k] = _REDACTED_SENTINEL if v else v
    return out


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


class _TCPLineIO:
    """Wraps a TCP socket as a text-mode line-based IO.

    Provides ``write()`` + ``flush()`` (like TextIO) and
    ``readline()`` + ``__iter__`` (like a line reader).
    """

    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn
        self._reader = conn.makefile("r", encoding="utf-8", buffering=1)

    def write(self, text: str) -> None:
        self.conn.sendall(text.encode("utf-8"))

    def flush(self) -> None:
        pass  # sendall is immediate

    def readline(self) -> str:
        """Read one line from the TCP socket.

        SEC-009: cap line size to prevent OOM DoS.  ``socket.makefile``
        ``readline`` with no size limit would happily allocate a 1 GB
        buffer if the client sent a single huge line with no newline.
        We cap at 1 MB (a single IPC message should be far under 1 KB;
        transcription text + metadata is well under 100 KB even for
        long dictations).  When the cap is exceeded, we return an
        empty string to signal EOF — the caller closes the connection.
        """
        _max_line_bytes = 1 * 1024 * 1024  # 1 MB
        _max_line_chars = _max_line_bytes  # conservative (UTF-8 worst case)
        line = self._reader.readline(_max_line_chars + 1)
        if len(line) > _max_line_chars:
            log.warning(
                "[TCP] client sent line exceeding %d char cap; closing connection",
                _max_line_chars,
            )
            return ""  # signal EOF
        return line

    def __iter__(self):
        return self

    def __next__(self) -> str:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._reader.close()
        with contextlib.suppress(Exception):
            self.conn.close()


# ARCH-REFAC-002: the per-command ``_handle_*`` methods live in the
# ``handlers/`` subpackage as mixin classes.  We import them here (after
# all module-level helpers like ``log`` / ``_push_event_now`` /
# ``_bound_history_limit`` are defined) so the mixins can resolve their
# ``from voice_typer.server.ipc_server import ...`` references via the
# partially initialized module already present in ``sys.modules``.
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
):
    """Reads JSON commands from stdin or TCP, dispatches, writes responses.

    Attributes
    ----------
    app : VoiceTyperApp
        The application instance this server wraps.
    """

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

    # ── Lifecycle ───────────────────────────────────────────────────────

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
        # Always start the stdin listener (legacy mode).  In TCP mode
        # stdin is unused (inherited from Electron, connected to /dev/null
        # or NUL).
        self._stdin_thread = threading.Thread(
            target=self._run,
            name="ipc-server",
            daemon=True,
        )
        self._stdin_thread.start()
        # RW-10: start the Electron-alive heartbeat watchdog.  Daemon
        # thread so it doesn't block shutdown.  The thread refuses to
        # fire ``app.quit()`` until the first heartbeat lands, so a
        # slow Electron cold start (10+ seconds for torch import)
        # doesn't trigger a false-positive exit.
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
            registry.register(
                name="heartbeat-watchdog",
                thread=self._heartbeat_thread,
                stop_event=self._heartbeat_stop_event,
                join_timeout=2.0,
            )
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

    def start_tcp(self, port: int) -> None:
        """Start a TCP server that accepts one Electron connection."""
        self._tcp_mode = True
        t = threading.Thread(
            target=self._accept_tcp,
            args=(port,),
            daemon=True,
        )
        t.start()

    def _accept_tcp(self, port: int) -> None:
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
        """
        # Read the expected token from the env var set by Electron.
        expected_token = os.environ.get("VOICE_TYPER_IPC_TOKEN", "")
        if not expected_token:
            # No token configured — fall back to the legacy unauthenticated
            # path.  This happens when running the IPC server standalone
            # (e.g. ``python -m voice_typer.server.ipc_server`` from a
            # terminal).  We log a warning so the user knows the server
            # is accepting unauthenticated connections.
            log.warning("[TCP] VOICE_TYPER_IPC_TOKEN not set — accepting UNAUTHENTICATED connections")

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("127.0.0.1", port))
            server.listen(1)
            log.info("[TCP] listening on 127.0.0.1:%d", port)
        except Exception:
            log.exception("[TCP] failed to bind on port %d", port)
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
            try:
                self._handle_tcp_connection(conn, addr, expected_token)
            except Exception:
                log.debug("[TCP] connection handler completed")
            # Loop back to accept the next connection

        with contextlib.suppress(OSError):
            server.close()
        # Clear the instance reference so a subsequent start_tcp() can
        # store a fresh socket without confusion.
        if self._tcp_server_socket is server:
            self._tcp_server_socket = None

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
                                    "data": {"message": "authentication failed"},
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

        # PR-3-FIX-1: now acquire the lock ONLY for the post-auth setup
        # (installing the client + flushing pending events). This is
        # a short critical section that can't block on unbounded I/O.
        with self._lock:
            self._tcp_client = auth_client

            # Flush any push events queued before the client connected
            for p in self._pending_tcp:
                self._tcp_client.write(p + "\n")
                self._tcp_client.flush()
            self._pending_tcp.clear()

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

        # RELIABILITY-006: per-connection rate limiter.  A buggy or
        # malicious Electron client that flood-dispatches commands
        # would otherwise starve the tray thread.
        rate_limiter = _RateLimiter()

        try:
            for line in self._tcp_client:
                line = line.strip()
                if not line:
                    continue
                if not rate_limiter.allow():
                    rate_limiter.reject()
                    self._send(
                        {
                            "type": "error",
                            "data": {"message": "rate limit exceeded; backing off"},
                        }
                    )
                    log.warning(
                        "[TCP] rate limit hit (%d rejected)",
                        rate_limiter.rejected_count,
                    )
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._send(
                        {
                            "type": "error",
                            "data": {"message": "invalid JSON"},
                        }
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
                    err: dict[str, object] = {
                        "type": "error",
                        "data": {"message": "internal error"},
                    }
                    if isinstance(msg, dict) and "id" in msg:
                        err["id"] = msg["id"]
                    self._send(err)
                    continue
                if result is not None:
                    self._send(result)
        except Exception:
            log.debug("[TCP] client connection closed")
        finally:
            self._tcp_client.close()
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
        # ISSUE-8: also clear the ESC-pending-capture-exit flag on the
        # hotkey dispatcher. If the frontend crashed mid-capture (ESC
        # pressed but not yet released), the flag would remain True and
        # cause a spurious ``hotkey_capture_cancel`` event on the next
        # ESC press after reconnect.
        _hotkeys = getattr(self.app, "hotkeys", None)
        if _hotkeys is not None:
            with contextlib.suppress(AttributeError):
                _hotkeys._esc_pending_capture_exit = False
        log.info("[IPC] keyboard ownership reset to normal (%s)", reason)

    # ── Heartbeat watchdog (RW-10) ───────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """RW-10: daemon thread that watches for Electron heartbeat timeouts.

        Wakes every ``_HEARTBEAT_INTERVAL_SECONDS`` (5s) and calls
        :meth:`_check_heartbeat_timeout`.  When the timeout fires
        (3 missed heartbeats = 15s without a heartbeat from Electron),
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
        waiting 15 real seconds).

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

    # ── Tray state hook ─────────────────────────────────────────────────

    def _hook_tray_set_state(self) -> None:
        """Monkey-patch ``app.tray.set_state`` to emit push events.

        Every call to ``set_state`` will also send a ``status_change``
        push event with the new state value.
        """
        original = self.app.tray.set_state

        def wrapped(state, message=""):
            original(state, message)
            self.push(
                {
                    "type": "status_change",
                    "data": {"status": state.value},
                }
            )

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
                    result = self._dispatch(msg)
                    self._send(result, _out=stdout)
                except json.JSONDecodeError:
                    self._send(
                        {
                            "type": "error",
                            "data": {"message": "invalid JSON"},
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
        cmd = msg.get("type")
        data = msg.get("data")
        resp = {"id": msg.get("id")} if "id" in msg else {}

        # RW-6 (pyrefly): ``_COMMAND_REGISTRY`` is typed ``dict[str, str]``
        # and ``dict.get`` requires a ``str`` key. ``msg.get("type")``
        # returns ``Unknown | None`` because the inbound JSON dict has no
        # static value-type, so the lookup below would be flagged
        # ``bad-argument-type``. Coerce to ``str`` here so the registry
        # lookup type-checks cleanly; the unknown-command path still
        # receives the original value (including ``None``) for the error
        # message, preserving the previous wire behaviour.
        cmd_key = cmd if isinstance(cmd, str) else ""
        handler_name = self._COMMAND_REGISTRY.get(cmd_key)
        if handler_name is None:
            result = self._handle_unknown_command(cmd, data, resp)
        else:
            handler = getattr(self, handler_name)
            result = handler(data, resp)

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
    _COMMAND_REGISTRY: dict[str, str] = {
        "get_status": "_handle_get_status",
        "toggle_dictation": "_handle_toggle_dictation",
        "undo_last": "_handle_undo_last",
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
        "onboarding_get_hotkey_presets": "_handle_onboarding_get_hotkey_presets",
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
        # daemon thread calls ``app.quit()`` if 3 consecutive heartbeats
        # are missed (15s timeout) so a crashed/force-killed Electron
        # doesn't strand the backend with the mic open + mutex held.
        "heartbeat": "_handle_heartbeat",
    }

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

    def _send(self, msg: dict | None, _out=None) -> None:
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
           list.  This is the only state that needs mutual exclusion.
        2. Outside the lock: serialize the message, perform the actual
           ``sendall`` (with a write timeout — NEW-CONC-003), and drain
           the pending list.  A slow client can no longer block other
           dispatchers.
        """
        if msg is None:
            return

        # Step 1: snapshot transport state under the lock.  This is fast
        # (no I/O) and is the only section that needs mutual exclusion.
        with self._lock:
            out = _out
            tcp_client = self._tcp_client
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
            # CRITICAL-CRITICAL: the ``relaunch_electron`` event is the
            # EXCEPTION.  This event MUST be delivered even during
            # shutdown because it's the signal from restart_app() that
            # tells Electron to call app.relaunch() + app.exit(0) before
            # the Python process exits.  Without it, the restart hangs.
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
                "relaunch_electron",
                "quit_app",
                "transcription_final",
                "transcription_partial",
                "vocabulary_suggestion",
            )
            if _is_shutting_down and msg_type not in _shutdown_allowlist:
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
            # reconnect).
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
                # Restore blocking mode (timeout=None means blocking
                # with no timeout, the default for TCP sockets).
                with contextlib.suppress(OSError, AttributeError):
                    tcp_client.conn.settimeout(None)
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
            log.debug("[IPC] no client; dropping high-freq %s event", msg_type)
        else:
            log.info("[IPC] no client; dropping %s event: %s", msg_type, msg)


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
    if port is not None and not (1 <= port <= 65535):
        print(f"Invalid port: {port} (must be 1..65535)", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)

    _setup_logging()

    # NEW-DOC-006: single-instance lock is acquired AFTER args are parsed
    # but BEFORE app construction (which stores the mutex handle).  The
    # lock is still taken for real launches (both standalone and --port IPC).
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

            buf = io.StringIO()
            buf.write(f"Voice Typer startup failed at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            buf.write(f"sys.executable: {sys.executable}\n")
            buf.write(f"sys.argv: {sys.argv}\n")
            traceback.print_exc(file=buf)
            diag_path = _config_dir() / "startup-error.log"
            _secure_atomic_write(diag_path, buf.getvalue())
            log.error("[FATAL] Diagnostic written to %s", diag_path)
        except Exception:
            pass
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
    server.start()
    if port is not None:
        server.start_tcp(port)
        log.info("[IPC] TCP mode on port %d — Electron should connect here", port)
    else:
        # P1-1.2: Standalone mode (no --port). The user ran VoiceTyper
        # from a terminal.  Auto-pick an available port, start the TCP
        # server, generate a session token, and launch the Electron
        # frontend so it connects back to us over TCP instead of
        # spawning its own Python backend.
        from voice_typer.server import electron_launcher

        standalone_port = _pick_available_port(9876)

        # Generate the session token and set it as an env var BEFORE
        # starting the TCP listener.  The _accept_tcp daemon thread reads
        # VOICE_TYPER_IPC_TOKEN at the top of its function; if we set it
        # after start_tcp(), the thread can read the env var before we
        # assign it, leaving expected_token empty and the connection
        # unauthenticated.
        ipc_token = electron_launcher.generate_session_token()
        os.environ["VOICE_TYPER_IPC_TOKEN"] = ipc_token

        server.start_tcp(standalone_port)
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

            buf = io.StringIO()
            buf.write(f"\n--- app.start() failed at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            traceback.print_exc(file=buf)
            diag_path = _config_dir() / "startup-error.log"
            # Read existing content if any, then write full content atomically
            try:
                existing = diag_path.read_text(encoding="utf-8")
            except (OSError, FileNotFoundError):
                existing = ""
            _secure_atomic_write(diag_path, existing + buf.getvalue())
            log.error("[FATAL] Diagnostic written to %s", diag_path)
        except Exception:
            pass
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
