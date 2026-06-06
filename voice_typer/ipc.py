"""Inter-process communication (IPC) between the main tray app and Flet subprocess.

Uses a simple newline-delimited JSON protocol over localhost TCP.

Server (main app) listens on a random port; the Flet subprocess connects as a client.
Messages are JSON objects terminated by ``\\n``.
"""

import json
import logging
import socket
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

_DELIM = b"\n"


def _send_json(sock: socket.socket, obj: dict) -> None:
    """Send a JSON object as a single newline-delimited message."""
    data = json.dumps(obj).encode() + _DELIM
    sock.sendall(data)


def _recv_json(sock: socket.socket) -> Optional[dict]:
    """Receive a single newline-delimited JSON message. Returns None on EOF."""
    buf = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)
        if _DELIM in buf:
            line = bytes(buf[: buf.index(_DELIM)])
            return json.loads(line)


# ─── Server (main app side) ───────────────────────────────────────────


class IPCServer:
    """Starts a localhost TCP server that accepts commands from the Flet subprocess."""

    def __init__(self, handler: Callable[[dict], Optional[dict]]):
        """
        Parameters
        ----------
        handler : callable
            Receives a decoded JSON dict, returns an optional response dict.
        """
        self._handler = handler
        self._server_socket: Optional[socket.socket] = None
        self._port: int = 0
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        """Bind to a random free port and start accepting connections."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("127.0.0.1", 0))
        self._server_socket.listen(1)
        self._port = self._server_socket.getsockname()[1]

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("[IPC] Server listening on port %d", self._port)

    def _run(self) -> None:
        while self._server_socket is not None:
            try:
                client, addr = self._server_socket.accept()
            except OSError:
                break
            threading.Thread(
                target=self._handle_client, args=(client,), daemon=True
            ).start()

    def _handle_client(self, client: socket.socket) -> None:
        try:
            client.settimeout(300)
            while True:
                msg = _recv_json(client)
                if msg is None:
                    break
                try:
                    response = self._handler(msg)
                    if response is not None:
                        _send_json(client, response)
                except Exception as e:
                    log.error("[IPC] Handler error: %s", e)
                    _send_json(client, {"error": str(e)})
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop(self) -> None:
        """Shut down the server."""
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
        log.info("[IPC] Server stopped")


# ─── Client (Flet subprocess side) ────────────────────────────────────


class IPCClient:
    """Sends commands to the main app's IPC server."""

    def __init__(self, port: int):
        self._port = port

    def send(self, command: str, **kwargs) -> Optional[dict]:
        """Send a command and return the response (or None on failure)."""
        msg = {"cmd": command, **kwargs}
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(("127.0.0.1", self._port))
            _send_json(sock, msg)
            resp = _recv_json(sock)
            sock.close()
            return resp
        except Exception as e:
            log.warning("[IPC] send(%s) failed: %s", command, e)
            return None
