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

log = logging.getLogger("voice_typer.server.ipc_server")


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
        line = self._reader.readline()
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
        self._running = False
        self._lock = threading.Lock()
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
        """Accept one connection, then run the TCP IPC loop."""
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
            # Flush any push events queued before the client connected
            for p in self._pending_tcp:
                self._tcp_client.write(p + "\n")
                self._tcp_client.flush()
            self._pending_tcp.clear()

        try:
            for line in self._tcp_client:
                line = line.strip()
                if not line:
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
            resp["data"] = {"status": self.app.tray.state.value}

        elif cmd == "toggle_dictation":
            try:
                self.app.toggle_dictation()
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] toggle_dictation failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_config":
            resp["type"] = "config"
            resp["data"] = self.app.config.__dict__.copy()

        elif cmd == "set_config":
            try:
                if isinstance(data, dict):
                    # Side-effect: live-register/unregister the prewarm
                    # scheduled task when fast_startup changes, so the
                    # Settings toggle takes effect without a restart.
                    if (
                        "fast_startup" in data
                        and data["fast_startup"] != getattr(self.app.config, "fast_startup", None)
                    ):
                        self.app.config.fast_startup = bool(data["fast_startup"])
                        # _sync_prewarm_task reads config.fast_startup.
                        try:
                            self.app._sync_prewarm_task()
                        except Exception as e:
                            log.warning("[IPC] prewarm sync failed: %s", e)
                    for k, v in data.items():
                        if hasattr(self.app.config, k):
                            setattr(self.app.config, k, v)
                self.app.config.save()
                # Side-effect: when the autostart toggle changes, sync
                # the OS autostart entry (registry/plist/.desktop) live
                # so the user doesn't need to restart for the setting to
                # take effect.  _sync_autostart reads config.autostart.
                if isinstance(data, dict) and "autostart" in data:
                    try:
                        self.app._sync_autostart()
                    except Exception as e:
                        log.warning("[IPC] autostart sync failed: %s", e)
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] set_config failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_history":
            try:
                limit = (data or {}).get("limit", 50) if isinstance(data, dict) else 50
                offset = (data or {}).get("offset", 0) if isinstance(data, dict) else 0
                resp["type"] = "history"
                resp["data"] = self.app.history_db.get_recent(limit, offset)
            except Exception as e:
                log.error("[IPC] get_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_today_stats":
            try:
                resp["type"] = "today_stats"
                resp["data"] = self.app.history_db.get_today_stats()
            except Exception as e:
                log.error("[IPC] get_today_stats failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "delete_history":
            try:
                rec_id = data.get("id") if isinstance(data, dict) else None
                if rec_id is None:
                    raise ValueError("Missing 'id'")
                self.app.history_db.delete(rec_id)
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] delete_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "clear_history":
            try:
                self.app.history_db.clear_all()
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
                new_val = self.app.history_db.toggle_favorite(rec_id)
                resp["type"] = "ack"
                resp["data"] = {"favorite": new_val}
            except Exception as e:
                log.error("[IPC] toggle_favorite failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_favorites":
            try:
                limit = (data or {}).get("limit", 50) if isinstance(data, dict) else 50
                offset = (data or {}).get("offset", 0) if isinstance(data, dict) else 0
                resp["type"] = "history"
                resp["data"] = self.app.history_db.get_favorites(limit, offset)
            except Exception as e:
                log.error("[IPC] get_favorites failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "search_history":
            try:
                query = data.get("query") if isinstance(data, dict) else ""
                limit = data.get("limit", 50) if isinstance(data, dict) else 50
                offset = data.get("offset", 0) if isinstance(data, dict) else 0
                resp["type"] = "history"
                resp["data"] = self.app.history_db.search(query, limit, offset)
            except Exception as e:
                log.error("[IPC] search_history failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_microphones":
            try:
                resp["type"] = "microphones"
                resp["data"] = self.app._microphones
            except Exception as e:
                log.error("[IPC] get_microphones failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_vocabulary":
            try:
                from voice_typer.server.vocabulary import VocabularyManager
                mgr = VocabularyManager()
                resp["type"] = "vocabulary"
                resp["data"] = mgr.get_all()
            except Exception as e:
                log.error("[IPC] get_vocabulary failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "save_vocabulary":
            try:
                from voice_typer.server.vocabulary import (
                    VocabularyManager, CATEGORIES, VOCAB_FILENAME,
                )
                from voice_typer.server.config import _config_dir
                import json

                # Build a set of bundled entries so we only save user customizations.
                # This prevents saving bundled corrections into the user file, which
                # would cause duplicate entries in list-based categories on the next
                # VocabularyManager load (bundled + user file with bundled copies).
                mgr = VocabularyManager()
                bundled = mgr._load_bundled()

                user_only: dict[str, object] = {}
                for cat in CATEGORIES:
                    incoming = (data or {}).get(cat)
                    bundled_cat = bundled.get(cat)

                    if cat in ("misspellings", "technical_terms", "names", "products"):
                        if isinstance(incoming, dict):
                            bd = bundled_cat if isinstance(bundled_cat, dict) else {}
                            diff = {k: v for k, v in incoming.items() if bd.get(k) != v}
                            if diff:
                                user_only[cat] = diff
                    elif cat in ("phrase_corrections", "extra_word_patterns"):
                        if isinstance(incoming, list):
                            bs: set[tuple[str, str]] = set()
                            if isinstance(bundled_cat, list):
                                for item in bundled_cat:
                                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                                        bs.add((item[0], item[1]))
                            diff = [
                                item for item in incoming
                                if isinstance(item, (list, tuple)) and len(item) >= 2
                                and (item[0], item[1]) not in bs
                            ]
                            if diff:
                                user_only[cat] = diff

                # Write only user customizations to the user file
                user_path = _config_dir() / VOCAB_FILENAME
                user_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = user_path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(user_only, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp.replace(user_path)

                resp["type"] = "ack"
                resp["data"] = {"imported_categories": len(user_only)}
            except Exception as e:
                log.error("[IPC] save_vocabulary failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "restart_app":
            resp["type"] = "ack"
            try:
                self._send(resp)
                self.app.restart_app()
            except Exception as e:
                log.error("[IPC] restart_app failed: %s", e, exc_info=True)
                # The ack was already sent; can't recover from here.
            return None

        elif cmd == "quit_app":
            resp["type"] = "ack"
            try:
                self._send(resp)
                self.app.quit_app()
            except Exception as e:
                log.error("[IPC] quit_app failed: %s", e, exc_info=True)
            return None

        else:
            resp["type"] = "error"
            resp["data"] = {"message": f"Unknown command: {cmd}"}

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
                for p in self._pending_tcp:
                    self._tcp_client.write(p + "\n")
                    self._tcp_client.flush()
                self._pending_tcp.clear()
            elif self._tcp_mode:
                self._pending_tcp.append(line)
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

    # Hook os._exit to log who calls it — pystray or our own code might
    # exit the process without going through an exception handler.
    _original_os_exit = os._exit
    def _logged_os_exit(code):
        print(f"[IPC] os._exit({code}) called from:", file=sys.stderr)
        import traceback as _tb
        _tb.print_stack(file=sys.stderr)
        _original_os_exit(code)
    os._exit = _logged_os_exit

    app = VoiceTyperApp()

    # Parse --port for TCP mode
    port = None
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            try:
                port = int(sys.argv[idx + 1])
            except ValueError:
                print(f"Invalid port: {sys.argv[idx + 1]}", file=sys.stderr)
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
    except BaseException:
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
