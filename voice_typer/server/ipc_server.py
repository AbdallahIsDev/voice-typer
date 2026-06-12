"""JSON-lines IPC server over stdin/stdout.

Reads JSON commands from stdin, dispatches to the VoiceTyperApp instance,
and writes JSON responses to stdout.  Used by an Electron (or any other)
frontend that spawns this module as a subprocess.

Usage::

    python -m voice_typer.server.ipc_server
"""

import json
import logging
import sys
import threading

log = logging.getLogger(__name__)


class IPCServer:
    """Reads JSON commands from stdin, writes JSON responses to stdout.

    Attributes
    ----------
    app : VoiceTyperApp
        The application instance this server wraps.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._running = False
        self._lock = threading.Lock()

    # ── Lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the IPC server in a daemon thread.

        Also hooks ``app.tray.set_state`` so that every state change emits
        a ``status_change`` push event back to the frontend.
        """
        self._running = True
        self._hook_tray_set_state()
        thread = threading.Thread(target=self._run, name="ipc-server", daemon=True)
        thread.start()

    def stop(self) -> None:
        """Signal the stdin loop to stop on the next iteration."""
        self._running = False

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

    # ── Main loop ───────────────────────────────────────────────────────

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
                    for k, v in data.items():
                        if hasattr(self.app.config, k):
                            setattr(self.app.config, k, v)
                self.app.config.save()
                resp["type"] = "ack"
            except Exception as e:
                log.error("[IPC] set_config failed: %s", e, exc_info=True)
                resp["type"] = "error"
                resp["data"] = {"message": str(e)}

        elif cmd == "get_history":
            try:
                limit = (data or {}).get("limit", 50) if isinstance(data, dict) else 50
                resp["type"] = "history"
                resp["data"] = self.app.history_db.get_recent(limit)
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

        elif cmd == "get_microphones":
            try:
                resp["type"] = "microphones"
                resp["data"] = self.app._microphones
            except Exception as e:
                log.error("[IPC] get_microphones failed: %s", e, exc_info=True)
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
        """Write a single JSON line to stdout and flush.

        Uses a reentrant lock so concurrent calls from the stdin loop and
        push events do not interleave bytes on the pipe.

        Parameters
        ----------
        _out : Optional[TextIO]
            Output stream (default ``sys.stdout``).  Provided for testing.
        """
        if msg is None:
            return
        with self._lock:
            out = _out or sys.stdout
            line = json.dumps(msg)
            out.write(line + "\n")
            out.flush()


# ── Entry point ─────────────────────────────────────────────────────────


def main() -> None:
    """Create a ``VoiceTyperApp``, wrap it in an ``IPCServer``, and block.

    Designed as the subprocess entry point for an Electron frontend::

        python -m voice_typer.server.ipc_server
    """
    from voice_typer.server.app import VoiceTyperApp, _setup_logging, _ensure_single_instance

    _setup_logging()
    _single_instance_mutex = _ensure_single_instance(silent=True)

    app = VoiceTyperApp()
    server = IPCServer(app)
    server.start()
    # Tell the frontend we're ready — Electron defers window creation until this.
    server.push({"type": "ready"})
    app.start()  # blocks (tray event loop)
    # Keep mutex alive by referencing it until exit
    _ = _single_instance_mutex


if __name__ == "__main__":
    main()
