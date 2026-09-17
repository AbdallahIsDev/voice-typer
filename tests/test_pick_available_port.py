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

        This is the CR-7 gold-standard contract, by the time the caller
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
        semantics are inverted there, it would let a second socket
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
        """``listen()`` succeeds on the returned socket, confirms the
        socket is valid for direct hand-off to ``start_tcp`` (the
        no-race-window gold-standard path)."""
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            # listen() must succeed, this is what _accept_tcp does.
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

        The CR-7 fix preserves this fallback behavior, the returned
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
