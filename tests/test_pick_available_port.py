"""CR-7 regression tests: ``_pick_available_port`` socket pass-through.

The previous implementation used a probe-then-bind pattern: it bound a
probe socket, closed it, returned the port number, then the caller
(``_accept_tcp``) bound a fresh socket to that port.  Between the probe
close and the real bind, another local process could grab the port
(``Address already in use``).

The CR-7 fix returns the BOUND probe socket alongside the port number,
so callers can pass it through to ``start_tcp`` (which calls ``listen``
on it directly, no re-bind → no race window).

See:
- ``voice_typer/server/ipc_server.py:_pick_available_port``
- ``voice_typer/server/ipc_server.py:IPCServer.start_tcp``
- ``voice_typer/server/ipc_server.py:IPCServer._accept_tcp``
- ``review.md`` finding CR-7
"""

import socket
import time
from unittest.mock import MagicMock


class TestPickAvailablePortReturnsBoundSocket:
    """``_pick_available_port`` returns ``(port, bound_socket)`` tuple."""

    def test_returns_tuple(self):
        """Return value is a 2-tuple of (int, socket)."""
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            assert isinstance(port, int)
            assert 1 <= port <= 65535
            assert isinstance(sock, socket.socket)
        finally:
            sock.close()

    def test_returned_socket_is_bound(self):
        """The returned socket is already bound (no re-bind needed).

        This is the CR-7 gold-standard contract — by the time the caller
        receives the socket, the kernel has already reserved the port.
        No other local process can claim it.
        """
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            # getsockname() returns the bound (host, port).  If the
            # socket weren't bound, getsockname() would raise.
            host, bound_port = sock.getsockname()
            assert host == "127.0.0.1"
            assert bound_port == port
        finally:
            sock.close()

    def test_returned_socket_has_reuseaddr(self):
        """The returned socket has SO_REUSEADDR set (matches legacy behavior)
        on POSIX; on Windows the option is deliberately NOT set (its
        semantics are inverted there — it would let a second socket
        hijack the port), so we assert the socket is usable instead."""
        import os

        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            if os.name == "nt":
                # Windows: SO_REUSEADDR would enable port hijacking, so
                # the probe skips it. The socket must still be usable.
                sock.listen(1)
            else:
                reuse = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
                assert reuse != 0  # 1 = enabled
        finally:
            sock.close()

    def test_returned_socket_is_listenable(self):
        """``listen()`` succeeds on the returned socket — confirms the
        socket is valid for direct hand-off to ``start_tcp`` (the
        no-race-window gold-standard path)."""
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            # listen() must succeed — this is what _accept_tcp does.
            sock.listen(1)
            # Verify the socket is now in LISTEN state by binding a
            # client and connecting.
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                client.connect(("127.0.0.1", port))
                conn, addr = sock.accept()
                conn.close()
            finally:
                client.close()
        finally:
            with __import__("contextlib").suppress(OSError):
                sock.close()


class TestPickAvailablePortFallback:
    """The ephemeral-port fallback still returns a bound socket."""

    def test_all_busy_falls_back_to_ephemeral(self):
        """When every port in range is busy, OS assigns an ephemeral one.

        The CR-7 fix preserves this fallback behavior — the returned
        socket is still bound to 127.0.0.1:0 (kernel-assigned port).
        """
        from voice_typer.server.ipc_server import _pick_available_port

        # Hold a non-REUSEADDR socket on a port, then ask for that
        # exact port with max_tries=1.  The function should fall
        # through to the ephemeral-port branch (bind to port 0).
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        busy_port = holder.getsockname()[1]
        try:
            port, sock = _pick_available_port(busy_port, max_tries=1)
            try:
                # Should be a valid port, and the returned socket must
                # be bound to that port.
                assert isinstance(port, int)
                assert 1 <= port <= 65535
                assert sock.getsockname()[1] == port
            finally:
                sock.close()
        finally:
            holder.close()


class TestStartTcpAcceptsTuple:
    """``start_tcp`` accepts either ``int`` or ``(port, sock)`` tuple."""

    def test_start_tcp_with_int_legacy(self):
        """Passing an ``int`` triggers the legacy bind path (backward compat).

        This is critical for existing callers (the ``--port`` CLI path
        and all existing tests that pass an int).
        """
        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        srv = IPCServer(app)

        # Pick a free port via the new API, then close the socket so the
        # port is free for the legacy int path to re-bind.
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        sock.close()

        srv._running = True
        import os

        old = os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)
        try:
            srv.start_tcp(port)
            # Wait for the accept thread to bind and store the socket.
            deadline = time.monotonic() + 2.0
            while srv._tcp_server_socket is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert srv._tcp_server_socket is not None, "legacy int path failed to bind"
            srv.stop()
            # Allow the accept loop to exit and clear the ref.
            deadline = time.monotonic() + 2.0
            while srv._tcp_server_socket is not None and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            if old is not None:
                os.environ["VOICE_TYPER_IPC_TOKEN"] = old

    def test_start_tcp_with_tuple_no_race_window(self):
        """Passing a ``(port, sock)`` tuple uses the pre-bound socket
        directly — no race window.  This is the CR-7 gold-standard path.
        """
        from voice_typer.server.ipc_server import IPCServer, _pick_available_port

        app = MagicMock()
        srv = IPCServer(app)

        # Pre-bind a socket via _pick_available_port.
        port, sock = _pick_available_port(0, max_tries=1)

        srv._running = True
        import os

        old = os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)
        try:
            # Pass the tuple — start_tcp should use the pre-bound socket.
            srv.start_tcp((port, sock))
            # Wait for the accept thread to call listen() and store the
            # socket.  Note: the stored socket should be the SAME object
            # we passed in (no re-bind happened).
            deadline = time.monotonic() + 2.0
            while srv._tcp_server_socket is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert srv._tcp_server_socket is not None, "tuple path failed to listen"
            # The stored socket MUST be the same pre-bound socket we
            # passed in (proves no re-bind happened → no race window).
            assert srv._tcp_server_socket is sock, (
                "start_tcp with tuple should reuse the same socket object — "
                "a different object means it re-bound (race window re-opened)"
            )
            srv.stop()
            deadline = time.monotonic() + 2.0
            while srv._tcp_server_socket is not None and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            if old is not None:
                os.environ["VOICE_TYPER_IPC_TOKEN"] = old
            # Ensure socket is closed even if start_tcp didn't get that far.
            with __import__("contextlib").suppress(OSError):
                sock.close()

    def test_start_tcp_with_tuple_client_can_connect(self):
        """End-to-end: a real TCP client can connect to the pre-bound
        socket after ``start_tcp((port, sock))`` is called."""
        from voice_typer.server.ipc_server import IPCServer, _pick_available_port

        app = MagicMock()
        srv = IPCServer(app)

        port, sock = _pick_available_port(0, max_tries=1)

        srv._running = True
        import os

        old = os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)
        try:
            srv.start_tcp((port, sock))
            # Wait for listen().
            deadline = time.monotonic() + 2.0
            while srv._tcp_server_socket is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert srv._tcp_server_socket is not None

            # A client should be able to connect.
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                client.settimeout(2.0)
                client.connect(("127.0.0.1", port))
                # Don't send auth — the server will close after the
                # auth timeout, which is fine for this test.
            finally:
                client.close()

            srv.stop()
            deadline = time.monotonic() + 2.0
            while srv._tcp_server_socket is not None and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            if old is not None:
                os.environ["VOICE_TYPER_IPC_TOKEN"] = old
            with __import__("contextlib").suppress(OSError):
                sock.close()
