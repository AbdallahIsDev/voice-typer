# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""TCP transport helpers: port picker and line-IO wrapper.

Phase 4.5 / ARCH-045 — extracted from the original ``ipc_server.py``
god-module.  Contains the TCP port picker (used by standalone mode to
auto-pick a free port for the backend's TCP server) and the
:class:`_TCPLineIO` wrapper that turns a TCP socket into a text-mode
line-based IO (``write()`` / ``flush()`` / ``readline()`` / ``__iter__``).
"""

import contextlib
import io
import logging
import socket

log = logging.getLogger("voice_typer.server.ipc_server")


def _pick_available_port(start: int = 9876, max_tries: int = 100) -> tuple[int, socket.socket]:
    """Return ``(port, bound_socket)`` for the first TCP port >= ``start`` free on 127.0.0.1.

    P1-1.2: used by standalone mode to auto-pick a port for the backend's
    TCP server.  Starts at the default IPC port (9876) and increments
    until a free port is found (capped at ``max_tries`` attempts).  Falls
    back to an OS-assigned ephemeral port (port=0) if every port in the
    range is busy — this guarantees the function never fails.

    CR-7 fix: the BOUND socket is returned alongside the port number so
    the caller can pass it through to :meth:`IPCServer.start_tcp` (which
    accepts either an ``int`` for backward compatibility or a
    ``(port, sock)`` tuple for the no-race-window gold-standard path).
    The previous probe-then-bind pattern closed the probe socket before
    the real ``bind()`` in ``_accept_tcp``, opening a (small but real)
    race window where another local process could grab the port.  By
    handing the already-bound socket to ``start_tcp``, the kernel
    guarantees no other process can claim that port between probe and
    listen.

    The returned socket has ``SO_REUSEADDR`` set and is bound to
    ``127.0.0.1:port`` but NOT yet listening — the caller is expected to
    call ``.listen()`` on it (or pass it to ``start_tcp`` which does so).
    Callers that only want the port number (and accept the race window)
    can close the socket themselves::

        port, sock = _pick_available_port(...)
        sock.close()  # releases the port; race window re-opens
    """
    for offset in range(max_tries):
        candidate = start + offset
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", candidate))
        except OSError:
            # Port busy — close the probe socket and try the next one.
            with contextlib.suppress(OSError):
                s.close()
            continue
        # CR-7: return the ACTUAL bound port (s.getsockname()[1]), not
        # ``candidate``.  When ``candidate == 0`` (ephemeral-port
        # request), the OS assigns a real port number which we must
        # surface to the caller.  The bound socket is returned so the
        # caller can pass it through to start_tcp (no race window).
        return s.getsockname()[1], s
    # All ports in range are busy — let the OS assign an ephemeral one.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    return s.getsockname()[1], s


class _TCPLineIO:
    """Wraps a TCP socket as a text-mode line-based IO.

    Provides ``write()`` + ``flush()`` (like TextIO) and
    ``readline()`` + ``__iter__`` (like a line reader).
    """

    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn
        # XV-86: use the default chunk buffer size (io.DEFAULT_BUFFER_SIZE,
        # typically 8 KiB) rather than ``buffering=1``. ``buffering=1`` means
        # "line buffered" in text mode — meaningful only for writes (flush
        # when a newline is seen); for reads CPython silently treats it as
        # the default, but the intent is non-obvious and linters flag it as
        # a potential small-recv-buffer smell. An explicit
        # ``io.DEFAULT_BUFFER_SIZE`` removes the ambiguity and ensures the
        # BufferedReader pulls the largest chunk the kernel will hand over
        # per ``recv()`` call, minimising syscalls under load.
        self._reader = conn.makefile("r", encoding="utf-8", buffering=io.DEFAULT_BUFFER_SIZE)

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
        # Close the underlying socket FIRST so any in-progress/blocked
        # read (a ``recv`` running on another thread, e.g. the dispatch
        # loop) is interrupted and releases the ``BufferedReader`` lock.
        # Otherwise ``BufferedReader.close()`` deadlocks against the
        # concurrent read and the caller (e.g. teardown / cleanup) hangs
        # forever.  ``shutdown`` + ``close`` raise on an already-closed
        # socket, so both are wrapped in suppress.
        with contextlib.suppress(Exception):
            self.conn.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(Exception):
            self.conn.close()
        with contextlib.suppress(Exception):
            self._reader.close()


__all__ = ["_pick_available_port", "_TCPLineIO"]
