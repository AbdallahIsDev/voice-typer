"""regression tests: ``_pick_available_port`` socket pass-through."""

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
        """
        The returned socket is already bound (no re-bind needed).
        This is the CR-7 gold-standard contract, by the time the caller
        """
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            host, bound_port = sock.getsockname()
            assert host == "127.0.0.1"
            assert bound_port == port
        finally:
            sock.close()

    def test_returned_socket_has_reuseaddr(self):
        """The returned socket has SO_REUSEADDR set (matches legacy behavior)"""
        import os

        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            if os.name == "nt":
                sock.listen(1)
            else:
                reuse = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
                assert reuse != 0  # 1 = enabled
        finally:
            sock.close()

    def test_returned_socket_is_listenable(self):
        """no-race-window gold-standard path)."""
        from voice_typer.server.ipc_server import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            sock.listen(1)
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
        """When every port in range is busy, OS assigns an ephemeral one."""
        from voice_typer.server.ipc_server import _pick_available_port

        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        busy_port = holder.getsockname()[1]
        try:
            port, sock = _pick_available_port(busy_port, max_tries=1)
            try:
                # Should be a valid port, and the returned socket must
                assert isinstance(port, int)
                assert 1 <= port <= 65535
                assert sock.getsockname()[1] == port
            finally:
                sock.close()
        finally:
            holder.close()
