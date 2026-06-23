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
_push_event: "Optional[Callable[[dict], None]]" = None


def _set_push_event(fn) -> None:
    global _push_event
    _push_event = fn


def _push_event_now(msg: dict) -> bool:
    """Push a raw event to the active IPC server, if one is wired.

    Returns True if the event was sent, False if no server is active.
    Safe to call from any thread; never raises.
    """
    fn = _push_event
    if fn is None:
        return False
    try:
        fn(msg)
        return True
    except Exception:
        log.debug("[IPC] _push_event_now raised", exc_info=True)
        return False


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
        _set_push_event(self.push)
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
        """Signal the stdin loop to stop on the next iteration."""
        global _push_event
        self._running = False
        _push_event = None
        if self._tcp_client is not None:
            self._tcp_client.close()
            self._tcp_client = None
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
            conn, addr = server.accept()
            log.info("[TCP] client connected from %s:%d", *addr)
            server.close()
        except Exception:
            log.exception("[TCP] failed to bind/accept on port %d", port)
            return

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
                # Side-effect: live-register/unregister the prewarm
                # scheduled task when fast_startup changes, so the
                # Settings toggle takes effect without a restart.
                if (
                    "fast_startup" in validated
                    and validated["fast_startup"] != getattr(self.app.config, "fast_startup", None)
                ):
                    self.app.config.fast_startup = bool(validated["fast_startup"])
                # Apply only allowlisted, validated values.
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
                resp["type"] = "ack"
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

        elif cmd == "get_volume_backend_status":
            # Returns the active volume backend's name + capability flags
            # ARCH-005: delegates to service layer
            try:
                status = self.service.get_volume_backend_status()
                status["is_windows"] = sys.platform == "win32"
                resp["type"] = "volume_backend_status"
                resp["data"] = status
            except Exception as e:
                log.error("[IPC] get_volume_backend_status failed: %s", e, exc_info=True)
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
            try:
                self._send(resp)
                self.service.restart()
            except Exception as e:
                log.error("[IPC] restart_app failed: %s", e, exc_info=True)
                # The ack was already sent; can't recover from here.
            return None

        elif cmd == "quit_app":
            resp["type"] = "ack"
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

        return resp

    # ── Output ──────────────────────────────────────────────────────────

    def push(self, msg: dict) -> None:
        """Send an unsolicited event (no ``id`` field)."""
        self._send(msg)

    def _send(self, msg: dict | None, _out=None) -> None:
        if msg is None:
            return
        with self._lock:
            line = json.dumps(msg)
            if _out is not None:
                _out.write(line + "\n")
                _out.flush()
            elif self._tcp_client is not None:
                self._tcp_client.write(line + "\n")
                self._tcp_client.flush()
                # PERF-NEW-014 / SEC-008: drain at most the most recent
                # K pending entries, not the whole list.  When the
                # client was disconnected for a while, _pending_tcp
                # could have grown to thousands of entries (16 Hz
                # waveform bubble * minutes of disconnect).  Draining
                # all of them on every push event was O(n) per push
                # and blocked the audio thread.
                _DRAIN_CAP = 100
                if self._pending_tcp:
                    pending = self._pending_tcp[-_DRAIN_CAP:]
                    self._pending_tcp.clear()
                    for p in pending:
                        try:
                            self._tcp_client.write(p + "\n")
                            self._tcp_client.flush()
                        except Exception:
                            log.debug("[IPC] client write failed during pending drain")
                            break
            elif self._tcp_mode:
                # SEC-008: cap _pending_tcp to prevent unbounded
                # memory growth while the client is disconnected.
                # When the cap is hit, drop the OLDEST entries
                # (waveform bubble level events are stale by the
                # time the client reconnects; transcription-complete
                # events are also in history_db).
                _PENDING_CAP = 1000
                self._pending_tcp.append(line)
                if len(self._pending_tcp) > _PENDING_CAP:
                    dropped = len(self._pending_tcp) - _PENDING_CAP
                    del self._pending_tcp[:dropped]
                    log.warning(
                        "[IPC] _pending_tcp cap exceeded; dropped %d old entries",
                        dropped,
                    )
            else:
                # No IPC client connected (e.g. the ``voice-typer`` console
                # script running without an Electron frontend).  Do NOT dump
                # raw JSON to stdout — it pollutes the terminal and interleaves
                # with structured logs.  Push events are only meaningful to an
                # IPC client; with none attached, they are silently dropped.
                log.debug("[IPC] no client connected; dropping push event")


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
    # When run as ``python -m voice_typer.server.ipc_server``, this
    # module is loaded as ``__main__`` and is NOT registered in
    # ``sys.modules`` under its canonical dotted name.  Any code that
    # later does ``from voice_typer.server.ipc_server import ...``
    # (notably ``app._wire_waveform_bubble``, which imports
    # ``_push_event_now``) would trigger a SECOND module load with
    # fresh, uninitialized globals — so ``_push_event`` would be ``None``
    # in the copy the bubble callbacks read from, and every push event
    # would silently fail (``push=NO IPC``).  Register the canonical name
    # to point at THIS running module so all imports return the same
    # single instance whose ``_push_event`` is set by ``IPCServer.start()``.
    _CANONICAL = "voice_typer.server.ipc_server"
    if _CANONICAL not in sys.modules:
        sys.modules[_CANONICAL] = sys.modules["__main__"]

    from voice_typer.server.app import VoiceTyperApp, _setup_logging, _ensure_single_instance

    _setup_logging()
    _single_instance_mutex = _ensure_single_instance(silent=True)

    # NEW-SEC-015: the os._exit monkey-patch that printed a stack trace
    # on every shutdown has been removed.

    app = VoiceTyperApp()

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
        sys.exit(1)

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
        log.info("[IPC] app.start() returned normally, process exiting")
    except SystemExit as _se:
        # sys.exit() or os._exit() called from within pystray or runtime.
        # Catch it so we can log the cause, then re-raise.
        log.info("[IPC] app.start() exited via sys.exit(%s)", _se.code)
        raise
    except Exception:
        # ERR-ERR-002 (fix): was `except BaseException` which also caught
        # KeyboardInterrupt and GeneratorExit. Now catches only Exception
        # so Ctrl+C and SystemExit propagate normally to the finally block.
        log.exception("app.start() raised — shutting down")
        sys.exit(1)
    else:
        log.info("[IPC] main() exiting normally")
    finally:
        log.info("[IPC] main() reached finally")
    # Keep mutex alive by referencing it until exit
    _ = _single_instance_mutex


if __name__ == "__main__":
    main()
