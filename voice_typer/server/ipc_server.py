"""JSON-lines IPC server over stdin/stdout OR TCP.

Reads JSON commands from stdin (legacy) or a TCP socket (Electron),
dispatches to the VoiceTyperApp instance, and writes JSON responses.

Usage (TCP mode — Electron)::

    python -m voice_typer.server.ipc_server --port 9876

Usage (stdin/stdout mode — ``voice-typer`` CLI)::

    python -m voice_typer.server.ipc_server
"""

import json
import logging
import os
import socket
import sys
import threading
import time
from collections import deque

from voice_typer.server.config import validate_config_update
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

log = logging.getLogger("voice_typer.server.ipc_server")


# ── RELIABILITY-006: per-connection rate limiter ─────────────────────────
#
# A crash-looping or buggy Electron client can flood the IPC socket
# with thousands of malformed messages per second, exhausting file
# descriptors and starving the tray thread.  ``_RateLimiter`` is a
# sliding-window per-connection limiter: each connection gets a
# bounded number of messages per window.  Over-budget messages are
# dropped (with an error response) rather than dispatched.
#
# The limits are intentionally generous (60 msg/s sustained, 200 msg
# burst) — a well-behaved Electron client sends maybe 1-5 msg/s.

_RATE_LIMIT_WINDOW_SECONDS = 1.0
_RATE_LIMIT_BURST = 200
_RATE_LIMIT_SUSTAINED = 60  # per second

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
        self._timestamps: "deque[float]" = deque()
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
            # Reject if we're at the burst cap or would exceed the
            # sustained rate.
            if len(self._timestamps) >= self._burst:
                return False
            if len(self._timestamps) >= self._sustained:
                # Allow bursts up to ``burst``, but if we've already
                # hit the sustained rate within the window, reject.
                # This prevents a slow trickle from saturating the
                # dispatcher indefinitely.
                return False
            self._timestamps.append(ts)
            return True

    @property
    def rejected_count(self) -> int:
        """Total messages rejected since this limiter was created.

        Not currently exposed via IPC, but useful for tests.
        """
        return getattr(self, "_rejected", 0)

    def reject(self) -> None:
        """Increment the rejected counter (called when allow() returns False)."""
        with self._lock:
            self._rejected = getattr(self, "_rejected", 0) + 1


# ── SEC-003: config sanitization for IPC ─────────────────────────────────
#
# ``get_config`` must NOT echo secret fields back to the IPC client.
# Even though the IPC socket is loopback-only, any local process can
# connect to it (see SEC-018 for the auth fix).  We return a sanitized
# view where API keys are replaced with a presence indicator so the
# renderer can render "key configured" UI without ever holding the
# actual key value.

# Fields whose values are secrets and must never be echoed back.
_SECRET_CONFIG_FIELDS = frozenset({
    "cloud_api_key",
    "openai_api_key",
    "groq_api_key",
    "deepgram_api_key",
    "llm_api_key",
})

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
_push_event_registry: "set[Callable[[dict], None]]" = set()
_push_event_registry_lock = threading.Lock()


def _set_push_event(fn) -> None:
    """Register *fn* as an active push target.

    NEW-IPC-013: now operates on a registry instead of a single global
    callable.  Safe to call from multiple IPCServer instances in the
    same process.
    """
    if fn is None:
        return
    with _push_event_registry_lock:
        _push_event_registry.add(fn)


def _clear_push_event(fn) -> None:
    """Unregister *fn* from the active push target set.

    Used by IPCServer.stop() to remove its own push callable without
    affecting other registered servers.
    """
    with _push_event_registry_lock:
        _push_event_registry.discard(fn)


def _push_event_now(msg: dict) -> bool:
    """Push a raw event to ALL active IPC servers, if any are wired.

    Returns True if at least one server accepted the event, False if
    no server is active.  Safe to call from any thread; never raises.

    NEW-IPC-013: previously pushed to a single global callable.  When
    two IPCServer instances existed in the same process (tests +
    production), the second start() would stomp the first's push fn,
    and the first's stop() would clear the global entirely — leaving
    the second server unable to push.  We now fan out to ALL servers
    in the registry so both receive the event.
    """
    with _push_event_registry_lock:
        fns = list(_push_event_registry)
    if not fns:
        return False
    delivered = False
    for fn in fns:
        try:
            fn(msg)
            delivered = True
        except Exception:
            log.debug("[IPC] _push_event_now raised", exc_info=True)
    return delivered


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
        _MAX_LINE_BYTES = 1 * 1024 * 1024  # 1 MB
        _MAX_LINE_CHARS = _MAX_LINE_BYTES  # conservative (UTF-8 worst case)
        line = self._reader.readline(_MAX_LINE_CHARS + 1)
        if len(line) > _MAX_LINE_CHARS:
            log.warning(
                "[TCP] client sent line exceeding %d char cap; closing connection",
                _MAX_LINE_CHARS,
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
        try:
            self._reader.close()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass


class IPCServer:
    """Reads JSON commands from stdin or TCP, dispatches, writes responses.

    Attributes
    ----------
    app : VoiceTyperApp
        The application instance this server wraps.
    """

    def __init__(self, app) -> None:
        self.app = app
        # ARCH-005: wire VoiceTyperService as the service boundary.
        # IPC routes delegate through the service instead of calling
        # self.app directly. This allows a second transport (CLI, gRPC)
        # to reuse the same service layer without duplicating app glue.
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
        self._push_fn: "Optional[Callable[[dict], None]]" = None

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
        # paths, audio callback) can call ``_push_event_now(msg)``
        # without holding a reference to the app or the server.
        # NEW-IPC-013: _set_push_event now adds to a registry instead
        # of stomping a single global.  We track our own push callable
        # so stop() can unregister just ours without affecting other
        # active servers.
        self._push_fn = self.push
        _set_push_event(self._push_fn)
        self._hook_tray_set_state()
        # Always start the stdin listener (legacy mode).  In TCP mode
        # stdin is unused (inherited from Electron, connected to /dev/null
        # or NUL).
        self._stdin_thread = threading.Thread(
            target=self._run, name="ipc-server",
            daemon=True,
        )
        self._stdin_thread.start()
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
        """
        self._running = False
        # Unregister our push callable.  Other servers in the registry
        # are unaffected.
        push_fn = getattr(self, "_push_fn", None)
        if push_fn is not None:
            _clear_push_event(push_fn)
            self._push_fn = None
        if self._tcp_client is not None:
            self._tcp_client.close()
            self._tcp_client = None
        # Close the listening socket to unblock the accept() loop.
        # The accept loop catches OSError and breaks out.
        server_sock = self._tcp_server_socket
        if server_sock is not None:
            try:
                server_sock.close()
            except OSError:
                pass
            self._tcp_server_socket = None
        # Keep the app-level reference so existing closures still
        # work after a stop+start cycle in tests.

    # ── TCP listener ───────────────────────────────────────────────

    def start_tcp(self, port: int) -> None:
        """Start a TCP server that accepts one Electron connection."""
        self._tcp_mode = True
        t = threading.Thread(
            target=self._accept_tcp, args=(port,), daemon=True,
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
            log.warning(
                "[TCP] VOICE_TYPER_IPC_TOKEN not set — accepting UNAUTHENTICATED connections"
            )

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("127.0.0.1", port))
            server.listen(1)
            log.info("[TCP] listening on 127.0.0.1:%d", port)
        except Exception:
            log.exception("[TCP] failed to bind on port %d", port)
            # Make sure we don't leak the socket on bind failure.
            try:
                server.close()
            except OSError:
                pass
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
                log.debug("[TCP] connection handler raised", exc_info=True)
            # Loop back to accept the next connection

        try:
            server.close()
        except OSError:
            pass
        # Clear the instance reference so a subsequent start_tcp() can
        # store a fresh socket without confusion.
        if self._tcp_server_socket is server:
            self._tcp_server_socket = None

    def _handle_tcp_connection(self, conn, addr, expected_token: str) -> None:
        """Handle a single TCP client connection (auth + dispatch loop).

        Extracted from _accept_tcp by NEW-IPC-001 so the accept loop
        can handle reconnections.
        """
        with self._lock:
            self._tcp_client = _TCPLineIO(conn)
            # SEC-018: authenticate the connection.  The first line
            # from the client must be a JSON auth message with the
            # correct session token.  If it doesn't match (or the
            # token env var was set but no auth message arrives
            # within the timeout), drop the connection.
            if expected_token:
                try:
                    auth_line = self._tcp_client.readline()
                    if not auth_line:
                        log.warning("[TCP] client disconnected before sending auth")
                        self._tcp_client.close()
                        self._tcp_client = None
                        return
                    auth_msg = json.loads(auth_line.strip())
                    if (
                        not isinstance(auth_msg, dict)
                        or auth_msg.get("type") != "auth"
                        or auth_msg.get("token") != expected_token
                    ):
                        log.warning("[TCP] auth failed — invalid token")
                        # Send an error response so the client knows
                        # why it was dropped.
                        try:
                            self._tcp_client.write(
                                json.dumps({
                                    "type": "error",
                                    "data": {"message": "authentication failed"},
                                }) + "\n"
                            )
                            self._tcp_client.flush()
                        except Exception:
                            pass
                        self._tcp_client.close()
                        self._tcp_client = None
                        return
                    log.info("[TCP] auth OK")
                except json.JSONDecodeError:
                    log.warning("[TCP] auth failed — invalid JSON on first line")
                    self._tcp_client.close()
                    self._tcp_client = None
                    return
                except Exception:
                    log.warning("[TCP] auth handshake raised", exc_info=True)
                    self._tcp_client.close()
                    self._tcp_client = None
                    return

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
                self.push({
                    "type": "state_changed",
                    "data": {
                        "status": getattr(current_state, "value", str(current_state)),
                        "message": current_msg,
                    },
                })
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
                    self._send({
                        "type": "error",
                        "data": {"message": "rate limit exceeded; backing off"},
                    })
                    log.warning(
                        "[TCP] rate limit hit (%d rejected)",
                        rate_limiter.rejected_count,
                    )
                    continue
                try:
                    msg = json.loads(line)
                    result = self._dispatch(msg)
                    if result is not None:
                        self._send(result)
                except json.JSONDecodeError:
                    self._send({
                        "type": "error",
                        "data": {"message": "invalid JSON"},
                    })
        except Exception:
            log.debug("[TCP] client connection lost", exc_info=True)
        finally:
            self._tcp_client.close()
            self._tcp_client = None
            log.info("[TCP] client disconnected")

    # ── Tray state hook ─────────────────────────────────────────────────

    def _hook_tray_set_state(self) -> None:
        """Monkey-patch ``app.tray.set_state`` to emit push events.

        Every call to ``set_state`` will also send a ``status_change``
        push event with the new state value.
        """
        original = self.app.tray.set_state

        def wrapped(state, message=""):
            original(state, message)
            self.push({
                "type": "status_change",
                "data": {"status": state.value},
            })

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
                    self._send({
                        "type": "error",
                        "data": {"message": "invalid JSON"},
                    }, _out=stdout)
        except OSError:
            pass  # stdin closed (e.g. during test teardown)

    # ── Dispatcher ──────────────────────────────────────────────────────

    def _dispatch(self, msg: dict) -> dict | None:
        """Route a command and return the response dict.

        Returns ``None`` for commands that send their response internally
        (e.g. ``restart_app`` / ``quit_app`` which kill the process).
        """
        cmd = msg.get("type")
        data = msg.get("data")
        resp = {"id": msg.get("id")} if "id" in msg else {}

        if cmd == "get_status":
            resp["type"] = "status"
            # ERR-021: get_status() now returns a dict with status +
            # xruns_since_start. Preserve backward-compat by passing
            # the whole dict through.
            status_data = self.service.get_status()
            if isinstance(status_data, dict):
                resp["data"] = status_data
            else:
                # Backward-compat: older service.get_status() returned a string.
                resp["data"] = {"status": status_data}

        elif cmd == "toggle_dictation":
            try:
                self.service.toggle_dictation()
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] toggle_dictation failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "undo_last":
            # UX-003: undo last transcription via backspace keystrokes
            try:
                self.service.undo_last()
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] undo_last failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_config":
            resp["type"] = "config"
            # SEC-003: previously this returned config.__dict__.copy()
            # which exposed every *_api_key field in cleartext over the
            # loopback TCP socket.  Any local process could netcat the
            # IPC port and exfiltrate OpenAI/Groq/Deepgram/LLM keys.
            # We now return a sanitized view where secret fields are
            # replaced with a presence indicator ("" if unset,
            # "<redacted>" if set) so the renderer can show "key
            # configured" without ever receiving the key value.
            resp["data"] = self.service.get_config()

        elif cmd == "get_defaults":
            # UX-018: return the default Config() values so the
            # renderer's "Reset to Defaults" button doesn't have to
            # hardcode 22+ field defaults (which silently drift from
            # the Python Config dataclass).  The renderer calls this
            # once, then sends the result via set_config.
            try:
                resp["type"] = "defaults"
                resp["data"] = self.service.get_defaults()
            except Exception as e:
                log.error("[IPC] get_defaults failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "set_config":
            try:
                # NEW-IPC-005: reject non-dict data with an explicit error
                # instead of silently no-oping. Previously, if data was a
                # list/string/None, the isinstance guard skipped all
                # setattr + side-effect blocks but still returned
                # {type: "ack"} success — the worst IPC failure mode.
                if not isinstance(data, dict):
                    resp["type"] = "error"
                    resp["data"] = {"message": "set_config requires data: object"}
                    log.warning("[IPC] set_config rejected: data is %s, not dict", type(data).__name__)
                    return resp
                # SEC-002: validate the caller payload against the
                # explicit IPC allowlist BEFORE touching the Config
                # object.  Unknown keys are silently dropped (debug-
                # logged); type/range/enum violations abort the
                # entire payload atomically and return an error so
                # the renderer can surface the rejection.
                validated, errors = validate_config_update(data)
                if errors:
                    log.warning("[IPC] set_config rejected: %s", "; ".join(errors))
                    resp["type"] = "error"
                    resp["data"] = {"message": errors[0]}
                    return resp
                # NEW-IPC-015: echo accepted + rejected keys so the
                # renderer can show the user which fields were applied
                # and which were silently dropped (unknown keys).
                accepted_keys = list(validated.keys())
                rejected_keys = [k for k in data.keys() if k not in validated]
                # NEW-IPC-016: when model_size or asr_backend changes,
                # apply it to the active engine so the next dictation
                # uses the new model without requiring a restart.
                if (
                    "model_size" in validated
                    and validated["model_size"]
                        != getattr(self.app.config, "model_size", None)
                ):
                    try:
                        self.app.change_model(validated["model_size"])
                    except Exception as e:
                        log.warning("[IPC] change_model failed: %s", e)
                if (
                    "asr_backend" in validated
                    and validated["asr_backend"]
                        != getattr(self.app.config, "asr_backend", None)
                ):
                    try:
                        self.app.models.set_active_backend(
                            validated["asr_backend"]
                        )
                    except Exception as e:
                        log.warning("[IPC] set_active_backend failed: %s", e)
                # Apply only allowlisted, validated values.
                # RACE-011: hold the app's config-mutation lock for the
                # full apply+save sequence so a concurrent
                # SettingsController.apply() (from the deprecated
                # tkinter settings window) can't interleave attribute
                # writes with this IPC-driven update. Without this
                # lock, half the fields could come from IPC and half
                # from the tkinter window, producing a torn config.
                with self.app._config_mutation_lock:
                    for k, v in validated.items():
                        setattr(self.app.config, k, v)
                    self.app.config.save()
                # ARCH-043: invalidate the tray menu cache so the next
                # menu build picks up the new config values (model size,
                # hotkey, etc.). Without this, the tray menu shows stale
                # state until the next state-changed event.
                try:
                    self.app.tray.invalidate_menu_cache()
                except Exception:
                    log.debug("[IPC] tray.invalidate_menu_cache failed", exc_info=True)
                # ARCH-005: Delegate all side effects to the service layer
                self.service.apply_config_side_effects(data)

                # Push a config_changed event so the renderer (App.tsx)
                # can update UI-local state (font-scale, theme, etc.)
                # immediately instead of waiting for the next mount.
                # The event carries the validated updates so the
                # renderer doesn't need an extra get_config round-trip.
                try:
                    _push_event_now({
                        "type": "config_changed",
                        "data": validated,
                    })
                except Exception:
                    log.debug("[IPC] config_changed push failed", exc_info=True)

                resp["type"] = "ack"
                # NEW-IPC-015: echo accepted + rejected keys so the
                # renderer can show the user which fields were applied
                # and which were silently dropped (unknown keys).
                # Only include data when there are rejected keys, so
                # the common case (all keys accepted) returns a plain
                # {type: "ack"} matching existing callers.
                if rejected_keys:
                    resp["data"] = {"accepted": accepted_keys, "rejected": rejected_keys}
            except Exception as e:
                log.error("[IPC] set_config failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_history":
            try:
                # SEC-010: bound limit/offset to prevent DoS via huge values.
                raw = (data or {}) if isinstance(data, dict) else {}
                limit = _bound_history_limit(raw.get("limit", 50))
                offset = _bound_history_offset(raw.get("offset", 0))
                resp["type"] = "history"
                resp["data"] = self.service.get_history(limit, offset)
            except Exception as e:
                log.error("[IPC] get_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_today_stats":
            try:
                resp["type"] = "today_stats"
                resp["data"] = self.service.get_today_stats()
            except Exception as e:
                log.error("[IPC] get_today_stats failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "delete_history":
            try:
                rec_id = data.get("id") if isinstance(data, dict) else None
                if rec_id is None:
                    raise ValueError("Missing 'id'")
                self.service.delete_history(rec_id)
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] delete_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "restore_history":
            # NEW-UX-004: re-insert a previously-deleted record so the
            # renderer's Undo-delete toast can recover the entry.
            try:
                record = data.get("record") if isinstance(data, dict) else None
                if not isinstance(record, dict):
                    raise ValueError("Missing 'record' dict")
                new_id = self.service.restore_history(record)
                resp["type"] = "ack"
                resp["data"] = {"id": new_id}
            except Exception as e:
                log.error("[IPC] restore_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "clear_history":
            try:
                self.service.clear_history()
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] clear_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "toggle_favorite":
            try:
                rec_id = data.get("id") if isinstance(data, dict) else None
                if rec_id is None:
                    raise ValueError("Missing 'id'")
                new_val = self.service.toggle_favorite(rec_id)
                resp["type"] = "ack"
                resp["data"] = {"favorite": new_val}
            except Exception as e:
                log.error("[IPC] toggle_favorite failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_favorites":
            try:
                # SEC-010: bound limit/offset.
                raw = (data or {}) if isinstance(data, dict) else {}
                limit = _bound_history_limit(raw.get("limit", 50))
                offset = _bound_history_offset(raw.get("offset", 0))
                resp["type"] = "history"
                resp["data"] = self.service.get_favorites(limit, offset)
            except Exception as e:
                log.error("[IPC] get_favorites failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "search_history":
            try:
                raw = data if isinstance(data, dict) else {}
                query = raw.get("query", "")
                # SEC-010: bound limit/offset.
                limit = _bound_history_limit(raw.get("limit", 50))
                offset = _bound_history_offset(raw.get("offset", 0))
                resp["type"] = "history"
                resp["data"] = self.service.search_history(query, limit, offset)
            except Exception as e:
                log.error("[IPC] search_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_microphones":
            try:
                resp["type"] = "microphones"
                resp["data"] = self.service.get_microphones()
            except Exception as e:
                log.error("[IPC] get_microphones failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "refresh_microphones":
            # AUDIO-MIC: re-query PortAudio for available microphones.
            # Called when the user clicks "Refresh Microphones" in the
            # Electron UI after plugging in a new USB/BT device.
            try:
                mics = self.service.refresh_microphones()
                resp["type"] = "microphones"
                resp["data"] = mics
            except Exception as e:
                log.error("[IPC] refresh_microphones failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_rms_level":
            # AUDIO-RMS: return the current RMS level from the recorder.
            # Allows the Electron UI to show real-time audio level
            # without depending on the waveform bubble callback.
            try:
                result = self.service.get_rms_level()
                resp["type"] = "rms_level"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] get_rms_level failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_volume_backend_status":
            # Returns the active volume backend's name + capability flags
            # ARCH-005: delegates to service layer
            try:
                status = self.service.get_volume_backend_status()
                status["is_windows"] = is_windows()
                resp["type"] = "volume_backend_status"
                resp["data"] = status
            except Exception as e:
                log.error("[IPC] get_volume_backend_status failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_audio_status":
            # ADR 0007: returns the current audio filter chain status
            # (filter names, degraded flags, VAD backend, sample rate).
            try:
                app = self.service._app
                processor = getattr(app, "_audio_processor", None)
                if processor is not None:
                    resp["type"] = "audio_status"
                    resp["data"] = {
                        "filter_chain": processor.filter_names,
                        "degraded": processor.is_degraded,
                        "degraded_reasons": processor.degraded_reasons,
                        "latency_ms": processor.total_latency_ms,
                        "vad_backend": "silero" if getattr(app.config, "use_silero_vad", True) else "rms",
                        "sample_rate": getattr(app.config, "sample_rate", 16000),
                    }
                else:
                    resp["type"] = "audio_status"
                    resp["data"] = {
                        "filter_chain": [],
                        "degraded": False,
                        "degraded_reasons": [],
                        "latency_ms": 0.0,
                        "vad_backend": "rms",
                        "sample_rate": 16000,
                    }
            except Exception as e:
                log.error("[IPC] get_audio_status failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_model_status":
            # Item 10/11: check which models are actually on disk.
            # Returns a dict mapping model name → {downloaded: bool, deps_ok: bool}.
            # ARCH-005: delegates to service layer
            try:
                status = self.service.get_model_status()
                resp["type"] = "model_status"
                resp["data"] = status
            except Exception as e:
                log.error("[IPC] get_model_status failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_vocabulary":
            # ARCH-005: delegates to service layer
            try:
                result = self.service.get_vocabulary()
                resp["type"] = "vocabulary"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] get_vocabulary failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "save_vocabulary":
            # ARCH-005: delegates to service layer
            # NEW-SEC-011: cap payload size to prevent DoS. A 1 GB JSON
            # payload would exhaust disk and CPU; a 10 MB `good` value
            # would re-compile regex per transcription chunk.
            try:
                if not isinstance(data, dict):
                    resp["type"] = "error"
                    resp["data"] = {"message": "save_vocabulary requires data: object"}
                    return resp
                # Cap total JSON payload at 1 MB
                _MAX_VOCAB_PAYLOAD = 1 * 1024 * 1024
                import json as _json_mod
                payload_size = len(_json_mod.dumps(data))
                if payload_size > _MAX_VOCAB_PAYLOAD:
                    resp["type"] = "error"
                    resp["data"] = {"message": (
                        f"vocabulary payload too large ({payload_size}"
                        f" bytes; max {_MAX_VOCAB_PAYLOAD})"
                    )}
                    log.warning("[IPC] save_vocabulary rejected: payload %d > %d", payload_size, _MAX_VOCAB_PAYLOAD)
                    return resp
                # Cap individual string values at 1024 chars
                _MAX_VALUE_LEN = 1024
                for cat, entries in data.items():
                    if isinstance(entries, dict):
                        for k, v in entries.items():
                            if isinstance(v, str) and len(v) > _MAX_VALUE_LEN:
                                resp["type"] = "error"
                                resp["data"] = {"message": (
                                    f"vocabulary value too long in {cat}.{k}"
                                    f" ({len(v)} > {_MAX_VALUE_LEN})"
                                )}
                                return resp
                    elif isinstance(entries, list):
                        for entry in entries:
                            if isinstance(entry, (list, tuple)):
                                for v in entry:
                                    if isinstance(v, str) and len(v) > _MAX_VALUE_LEN:
                                        resp["type"] = "error"
                                        resp["data"] = {"message": (
                                            f"vocabulary value too long in {cat}"
                                            f" ({len(v)} > {_MAX_VALUE_LEN})"
                                        )}
                                        return resp
                result = self.service.save_vocabulary_with_diff(data)
                resp["type"] = "ack"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] save_vocabulary failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        # #6: Template IPC routes
        elif cmd == "get_templates":
            try:
                templates = self.service.get_templates()
                resp["type"] = "templates"
                resp["data"] = {"templates": templates}
            except Exception as e:
                log.error("[IPC] get_templates failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "save_templates":
            try:
                templates = (data or {}).get("templates", [])
                if not isinstance(templates, list):
                    raise ValueError("templates must be a list")
                self.service.save_templates(templates)
                resp["type"] = "ack"
                resp["data"] = {"saved": len(templates)}
            except Exception as e:
                log.error("[IPC] save_templates failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "restart_app":
            resp["type"] = "ack"
            # NEW-IPC-006: ensure ack carries an explicit ``data: {}`` for
            # shape consistency with the other ack responses.  This call
            # sends the response directly (returns None) so the
            # ``resp.setdefault("data", {})`` at the end of _dispatch
            # never runs for this branch — we add it here instead.
            resp.setdefault("data", {})
            try:
                self._send(resp)
                self.service.restart()
            except Exception as e:
                log.error("[IPC] restart_app failed: %s", e, exc_info=True)
                # The ack was already sent; can't recover from here.
            return None

        elif cmd == "quit_app":
            resp["type"] = "ack"
            # NEW-IPC-006: same as restart_app — add explicit ``data: {}``.
            resp.setdefault("data", {})
            try:
                self._send(resp)
                self.service.quit()
            except Exception as e:
                log.error("[IPC] quit_app failed: %s", e, exc_info=True)
            return None

        # ── #8: Onboarding IPC routes ────────────────────────────────
        elif cmd == "onboarding_is_first_run":
            try:
                result = self.service.onboarding_is_first_run()
                resp["type"] = "onboarding_first_run"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_is_first_run failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_start":
            try:
                result = self.service.onboarding_start()
                resp["type"] = "onboarding_step"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_start failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_get_step":
            try:
                result = self.service.onboarding_get_step()
                resp["type"] = "onboarding_step"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_get_step failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_next_step":
            try:
                result = self.service.onboarding_next_step()
                resp["type"] = "onboarding_step"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_next_step failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_prev_step":
            try:
                result = self.service.onboarding_prev_step()
                resp["type"] = "onboarding_step"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_prev_step failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_set_microphone":
            try:
                mic_id = (data or {}).get("mic_id") if isinstance(data, dict) else None
                result = self.service.onboarding_set_microphone(mic_id)
                resp["type"] = "ack" if "error" not in result else "error"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_set_microphone failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_set_hotkey":
            try:
                hotkey = (data or {}).get("hotkey", "<f2>") if isinstance(data, dict) else "<f2>"
                result = self.service.onboarding_set_hotkey(hotkey)
                resp["type"] = "ack" if "error" not in result else "error"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_set_hotkey failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_set_model":
            try:
                model = (data or {}).get("model", "small.en") if isinstance(data, dict) else "small.en"
                result = self.service.onboarding_set_model(model)
                resp["type"] = "ack" if "error" not in result else "error"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_set_model failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_skip":
            try:
                result = self.service.onboarding_skip()
                resp["type"] = "ack" if "error" not in result else "error"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_skip failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_apply":
            try:
                result = self.service.onboarding_apply()
                resp["type"] = "ack" if "error" not in result else "error"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_apply failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_get_microphones":
            try:
                result = self.service.onboarding_get_microphones()
                resp["type"] = "onboarding_microphones"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_get_microphones failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_get_model_options":
            try:
                result = self.service.onboarding_get_model_options()
                resp["type"] = "onboarding_models"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_get_model_options failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "onboarding_get_hotkey_presets":
            try:
                result = self.service.onboarding_get_hotkey_presets()
                resp["type"] = "onboarding_hotkey_presets"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] onboarding_get_hotkey_presets failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        # ── Microphone test (NEW) ─────────────────────────────────────
        elif cmd == "microphone_test_start":
            try:
                d = data if isinstance(data, dict) else {}
                mic_id = d.get("mic_id", None)
                duration = float(d.get("duration", 10.0))
                filters = d.get("filters", None)
                result = self.service.microphone_test_start(mic_id=mic_id, duration=duration, filters=filters)
                resp["type"] = "microphone_test_result"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] microphone_test_start failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "microphone_test_stop":
            try:
                result = self.service.microphone_test_stop()
                resp["type"] = "microphone_test_result"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] microphone_test_stop failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "microphone_test_cancel":
            try:
                result = self.service.microphone_test_cancel()
                resp["type"] = "microphone_test_result"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] microphone_test_cancel failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "microphone_test_status":
            try:
                result = self.service.microphone_test_status()
                resp["type"] = "microphone_test_status"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] microphone_test_status failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "microphone_test_get_level":
            try:
                result = self.service.microphone_test_get_level()
                resp["type"] = "microphone_test_level"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] microphone_test_get_level failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        # ── Continuous level monitor (NEW) ────────────────────────────
        elif cmd == "level_monitor_start":
            try:
                mic_id = (data or {}).get("mic_id", None) if isinstance(data, dict) else None
                result = self.service.level_monitor_start(mic_id=mic_id)
                resp["type"] = "level_monitor_status"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] level_monitor_start failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "level_monitor_stop":
            try:
                result = self.service.level_monitor_stop()
                resp["type"] = "level_monitor_status"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] level_monitor_stop failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "level_monitor_status":
            try:
                result = self.service.level_monitor_status()
                resp["type"] = "level_monitor_status"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] level_monitor_status failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        # ── UX-005: Download model IPC route ─────────────────────────
        elif cmd == "download_model":
            try:
                model_name = (data or {}).get("model", "") if isinstance(data, dict) else ""
                if not model_name:
                    resp["type"] = "error"
                    resp["data"] = {"message": "Missing 'model' parameter"}
                else:
                    result = self.service.download_model(model_name)
                    resp["type"] = "download_model_result"
                    resp["data"] = result
            except Exception as e:
                log.error("[IPC] download_model failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "cancel_model_download":
            # NEW-PRIV-011: cancel an in-progress HuggingFace download.
            try:
                result = self.service.cancel_model_download()
                resp["type"] = "ack"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] cancel_model_download failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "test_llm_connection":
            # NEW-DEAD-015: wire up the previously-dead
            # ``LLMPolisher.test_connection`` method so the renderer can
            # add a "Test connection" button on the Settings page.
            try:
                result = self.service.test_llm_connection()
                resp["type"] = "test_llm_connection_result"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] test_llm_connection failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "delete_model":
            # NEW-UX-005: actually delete the model files from disk,
            # not just remove from the UI list.
            try:
                model_name = (data or {}).get("model", "") if isinstance(data, dict) else ""
                if not model_name:
                    resp["type"] = "error"
                    resp["data"] = {"message": "Missing 'model' parameter"}
                else:
                    result = self.service.delete_model(model_name)
                    resp["type"] = "delete_model_result"
                    resp["data"] = result
            except Exception as e:
                log.error("[IPC] delete_model failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        # ── PROD-010: Export diagnostics ──────────────────────────────
        elif cmd == "export_diagnostics":
            try:
                result = self.service.export_diagnostics()
                resp["type"] = "diagnostics_result"
                resp["data"] = result
            except Exception as e:
                log.error("[IPC] export_diagnostics failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "check_accessibility":
            # PLAT-030: macOS Accessibility permission check.
            # Returns {"granted": bool, "platform": "macos"|"windows"|"linux"}.
            # On non-macOS platforms, always returns granted=True (no
            # accessibility permission required). The Electron UI uses
            # this to show a persistent warning banner on macOS when
            # the permission is missing, and to gate the onboarding
            # wizard's "Grant Accessibility" step.
            try:
                import sys as _sys
                granted = True
                if is_macos():
                    try:
                        import ctypes
                        # AXIsProcessTrusted() is the official API.
                        # Returns True iff the process has Accessibility.
                        app_services = ctypes.cdll.LoadLibrary(
                            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                        )
                        granted = bool(app_services.AXIsProcessTrusted())
                    except Exception:
                        # Fallback: osascript check
                        import subprocess as _sp
                        try:
                            result = _sp.run(
                                ["osascript", "-e",
                                 'tell application "System Events" to UI elements enabled'],
                                capture_output=True, text=True, timeout=3,
                            )
                            granted = result.returncode == 0 and "true" in result.stdout.lower()
                        except Exception:
                            granted = False
                resp["type"] = "accessibility_status"
                resp["data"] = {
                    "granted": granted,
                    "platform": _sys.platform,
                }
            except Exception as e:
                log.error("[IPC] check_accessibility failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "set_tray_locale":
            # TRAY-008: Set the tray menu locale. Called by the Electron
            # UI when the user changes the UI language in Settings.
            # The tray menu is rebuilt on the next state change so the
            # new labels take effect.
            try:
                from voice_typer.server.tray import set_tray_locale, get_tray_locale
                locale = data if isinstance(data, str) else (data or {}).get("locale", "en")
                set_tray_locale(locale)
                # Force a tray menu rebuild so the new labels show immediately.
                try:
                    self.app.tray.invalidate_menu_cache()
                except Exception:
                    pass
                resp["type"] = "ack"
                resp["data"] = {"locale": get_tray_locale()}
            except Exception as e:
                log.error("[IPC] set_tray_locale failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "show_electron_notification":
            # TRAY-035: Push a notification to the Electron UI for
            # persistent/critical messages that need longer display
            # than the OS-default ~5s tray notification. The Electron
            # Notification API supports a `duration` parameter (via
            # setTimeout auto-close) and can show a toast/banner that
            # stays until dismissed.
            try:
                if not isinstance(data, dict):
                    resp["type"] = "error"
                    resp["data"] = {"message": "show_electron_notification requires data: object"}
                    return resp
                title = str(data.get("title", "Voice Typer"))
                message = str(data.get("message", ""))
                duration_ms = int(data.get("duration_ms", 0))  # 0 = persistent
                critical = bool(data.get("critical", False))
                _push_event_now({
                    "type": "electron_notification",
                    "data": {
                        "title": title,
                        "message": message,
                        "duration_ms": duration_ms,
                        "critical": critical,
                    },
                })
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] show_electron_notification failed: %s", e)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        else:
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

        # NEW-IPC-006: ensure every response has a `data` field so the
        # client can always read `resp.data` without a defensive guard.
        # Previously 5 commands returned ``{type: "ack"}`` with no data,
        # forcing the renderer to do ``result?.field ?? null`` in every
        # call site.  Setting ``data: {}`` for empty acks is backward-
        # compatible: existing call sites that do ``if (result?.x)``
        # still see ``undefined`` for missing fields, and call sites
        # that do ``Object.keys(result ?? {})`` no longer crash.
        resp.setdefault("data", {})

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
            # write entirely.  Electron closes its end of the socket as
            # soon as it receives the ``quit_app`` event; any subsequent
            # push from the Python cleanup path (waveform bubble worker,
            # state-changed hooks, hotkey-backend teardown) would hit a
            # half-closed socket and raise ``[WinError 10053]`` /
            # ``ConnectionResetError``.  The error itself was already
            # logged at DEBUG, but suppressing the write in the first
            # place keeps the log (and any DEBUG-enabled user terminal)
            # quiet during shutdown.  We still mark the client as dead
            # so a subsequent (theoretical) reconnect can succeed.
            #
            # ``is True`` (rather than a truthiness check) is intentional:
            # the real ``VoiceTyperApp`` sets ``_shutting_down = True``
            # literally, and MagicMock-based test fixtures expose
            # ``_shutting_down`` as a child mock (which is truthy but
            # not ``is True``).  Using ``is True`` keeps the test path
            # exercising the write logic instead of the shutdown short-
            # circuit.
            if getattr(self.app, "_shutting_down", False) is True:
                with self._lock:
                    if self._tcp_client is tcp_client:
                        try:
                            self._tcp_client.close()
                        except Exception:
                            pass
                        self._tcp_client = None
                return
            # NEW-CONC-003: set a write timeout so a stalled renderer
            # can't block the worker thread indefinitely.  2 seconds is
            # generous for a localhost TCP write — under normal load the
            # kernel buffer accepts the data immediately.  If we hit the
            # timeout, the write raises ``socket.timeout`` and we drop
            # the connection (the accept loop will catch the next
            # reconnect).
            try:
                tcp_client.conn.settimeout(_TCP_WRITE_TIMEOUT_SECONDS)
            except (OSError, AttributeError):
                # settimeout can fail if the socket is already closed;
                # that's fine — the write below will also fail and we'll
                # drop the connection cleanly.
                pass
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
                _DRAIN_CAP = 100
                if pending:
                    # Already snapshot under lock — drain up to _DRAIN_CAP
                    # of the most recent entries.
                    recent = pending[-_DRAIN_CAP:]
                    for p in recent:
                        try:
                            tcp_client.write(p + "\n")
                            tcp_client.flush()
                        except Exception:
                            log.debug("[IPC] client write failed during pending drain")
                            break
            except (OSError, socket.timeout) as exc:
                log.debug("[IPC] client write failed: %s", exc)
                # Mark the client as dead so the accept loop will pick
                # up the next reconnect.  We do this under the lock to
                # avoid a race with a concurrent _send that just
                # snapshotted the (now-dead) client.
                with self._lock:
                    if self._tcp_client is tcp_client:
                        try:
                            self._tcp_client.close()
                        except Exception:
                            pass
                        self._tcp_client = None
            finally:
                # Restore blocking mode (timeout=None means blocking
                # with no timeout, the default for TCP sockets).
                try:
                    tcp_client.conn.settimeout(None)
                except (OSError, AttributeError):
                    pass
            return

        if tcp_mode:
            # SEC-008: cap _pending_tcp to prevent unbounded
            # memory growth while the client is disconnected.
            # When the cap is hit, drop the OLDEST entries
            # (waveform bubble level events are stale by the
            # time the client reconnects; transcription-complete
            # events are also in history_db).
            _PENDING_CAP = 1000
            with self._lock:
                # Re-merge any pending we snapshot earlier (they belong
                # before this new message in the queue).
                if pending:
                    self._pending_tcp.extend(pending)
                self._pending_tcp.append(line)
                if len(self._pending_tcp) > _PENDING_CAP:
                    dropped = len(self._pending_tcp) - _PENDING_CAP
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
    # NEW-CLI-003: import the standardized exit-code constants. Both
    # EXIT_BAD_ARGS (bad --port) and EXIT_CRASH (uncaught exception in
    # app.start()) are used below; previously EXIT_CRASH was imported
    # but unused and the crash path called sys.exit with a raw literal.
    from voice_typer.__main__ import EXIT_BAD_ARGS, EXIT_CRASH
    # When run as ``python -m voice_typer.server.ipc_server``, this
    # module is loaded as ``__main__`` and is NOT registered in
    # ``sys.modules`` under its canonical dotted name.  Any code that
    # later does ``from voice_typer.server.ipc_server import ...``
    # (notably ``app._wire_waveform_bubble``, which imports
    # ``_push_event_now``) would trigger a SECOND module load with
    # fresh, uninitialized globals — so the push-event registry would
    # be empty in the copy the bubble callbacks read from, and every
    # push event would silently fail (``push=NO IPC``).  Register the
    # canonical name to point at THIS running module so all imports
    # return the same single instance whose push-event registry is
    # populated by ``IPCServer.start()``.
    _CANONICAL = "voice_typer.server.ipc_server"
    if _CANONICAL not in sys.modules:
        sys.modules[_CANONICAL] = sys.modules["__main__"]

    # RACE-018: Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    try:
        import faulthandler
        faulthandler.enable()
        # Optional: register SIGUSR1 for on-demand thread dumps (POSIX only)
        import signal
        if hasattr(signal, 'SIGUSR1'):
            signal.signal(signal.SIGUSR1, faulthandler.dump_traceback_later)
    except Exception:
        pass  # Not available on all platforms

    from voice_typer.server.app import VoiceTyperApp, _setup_logging, _ensure_single_instance

    _setup_logging()
    _single_instance_mutex = _ensure_single_instance(silent=True)

    # NEW-SEC-015: the os._exit monkey-patch that printed a stack trace
    # on every shutdown has been removed.

    app = VoiceTyperApp()
    # PLAT-HLEAK: store the mutex handle on the app instance so
    # quit() can CloseHandle it on shutdown
    app._mutex_handle = _single_instance_mutex

    # NEW-CLI-002: use argparse for --port instead of hand-rolled
    # sys.argv walk. Supports --port=N and --port N, validates the
    # port range (1..65535), and emits --help. Previously a typo in
    # a wrapper script (e.g. --port with no value as the last arg)
    # silently started Python in stdin/stdout mode.
    import argparse
    parser = argparse.ArgumentParser(
        prog="voice_typer.server.ipc_server",
        description="Voice Typer IPC server (spawned by Electron)",
        add_help=False,  # we add --help manually to avoid conflict with app
    )
    parser.add_argument("--port", type=int, default=None, metavar="N",
                        help="TCP port to listen on (1..65535). "
                             "If omitted, uses stdin/stdout IPC.")
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    port = args.port
    if port is not None and not (1 <= port <= 65535):
        print(f"Invalid port: {port} (must be 1..65535)", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)

    server = IPCServer(app)
    server.start()
    if port is not None:
        server.start_tcp(port)
        log.info("[IPC] TCP mode on port %d — Electron should connect here", port)
    else:
        log.info("[IPC] stdin/stdout mode")

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
        log.debug("[IPC] app.start() returned normally, process exiting")
    except SystemExit as _se:
        # sys.exit() or os._exit() called from within pystray or runtime.
        # Catch it so we can log the cause, then re-raise.
        log.debug("[IPC] app.start() exited via sys.exit(%s)", _se.code)
        raise
    except Exception:
        # ERR-ERR-002 (fix): was `except BaseException` which also caught
        # KeyboardInterrupt and GeneratorExit. Now catches only Exception
        # so Ctrl+C and SystemExit propagate normally to the finally block.
        log.exception("app.start() raised — shutting down")
        # NEW-CLI-003: use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)
    else:
        log.debug("[IPC] main() exiting normally")
    finally:
        log.debug("[IPC] main() reached finally")
    # Keep mutex alive by referencing it until exit
    _ = _single_instance_mutex


if __name__ == "__main__":
    main()
