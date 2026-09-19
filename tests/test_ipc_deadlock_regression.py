"""regression tests: TCP-teardown deadlock in ``IPCServer._send``."""

from __future__ import annotations

import inspect
import socket
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer, _TCPLineIO


class TestSendRestoresPrevTimeout:
    """``_send`` must restore the PREVIOUS timeout (not ``None``)."""

    def test_send_does_not_mutate_socket_timeout(self):
        """The select-based write-readiness gate (``_await_socket_writable``)"""
        src = inspect.getsource(IPCServer._send)
        # The new approach uses _await_socket_writable (select.select).
        assert "_await_socket_writable" in src, (
            "_send must use _await_socket_writable to gate writes on socket write-readiness (select-based timeout)."
        )
        assert "_prev_timeout" not in src, (
            "_send must NOT capture _prev_timeout, the select-based "
            "approach doesn't mutate the socket timeout, so there's "
            "nothing to capture or restore."
        )
        assert "gettimeout()" not in src, (
            "_send must NOT call gettimeout(), the select-based approach doesn't need to read the previous timeout."
        )
        # No finally block needed (nothing to restore).
        assert "finally:" not in src, (
            "_send must NOT have a finally block, without the settimeout dance there's no timeout state to restore."
        )

    def test_send_restores_prev_timeout_behaviorally(self):
        """When ``_send`` runs against a client with a non-None previous"""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._pending_tcp = []
        server._tcp_mode = True

        # Use a real socketpair so settimeout/gettimeout are real.
        srv, cli = socket.socketpair()
        try:
            # Wrap the server end in _TCPLineIO so _send can write to it.
            tcp_client = _TCPLineIO(srv)
            # Set a non-None previous timeout (simulating the auth-read
            AUTH_DEADLINE = 7.0  # noqa: N806
            srv.settimeout(AUTH_DEADLINE)
            assert srv.gettimeout() == AUTH_DEADLINE  # sanity

            server._tcp_client = tcp_client

            received = []
            reader = threading.Thread(
                target=lambda: received.append(cli.recv(65536)),
                daemon=True,
            )
            reader.start()

            # Send a small message.  _send must restore AUTH_DEADLINE.
            server._send({"type": "test", "id": 1})

            reader.join(timeout=2.0)
            assert received, "reader should have received the message"

            assert srv.gettimeout() == AUTH_DEADLINE, (
                f"_send must restore the PREVIOUS timeout ({AUTH_DEADLINE}s), "
                f"not None (blocking). Got {srv.gettimeout()!r}, this is the "
                "deadlock root cause if it's None."
            )
        finally:
            srv.close()
            cli.close()

    def test_send_restores_none_when_prev_was_none(self):
        """If the previous timeout was already ``None`` (no auth deadline"""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._pending_tcp = []
        server._tcp_mode = True

        srv, cli = socket.socketpair()
        try:
            tcp_client = _TCPLineIO(srv)
            # Previous timeout is None (default for a fresh socket).
            assert srv.gettimeout() is None
            server._tcp_client = tcp_client

            received = []
            reader = threading.Thread(
                target=lambda: received.append(cli.recv(65536)),
                daemon=True,
            )
            reader.start()

            server._send({"type": "test", "id": 2})
            reader.join(timeout=2.0)
            assert received

            assert srv.gettimeout() is None, (
                "_send must restore the PREVIOUS timeout. When prev was "
                "None, the restored value should be None (not a hardcoded "
                "non-None value that would clobber future deadlines)."
            )
        finally:
            srv.close()
            cli.close()


class TestTCPLineIOCloseUsesShutdown:
    """``_TCPLineIO.close`` must ``shutdown(SHUT_RDWR)`` before"""

    def test_close_source_uses_shutdown(self):
        """The source of ``_TCPLineIO.close`` must call"""
        src = inspect.getsource(_TCPLineIO.close)
        assert "shutdown" in src, (
            "_TCPLineIO.close must call self.conn.shutdown() to interrupt in-progress reads (deadlock fix)."
        )
        assert "SHUT_RDWR" in src, (
            "_TCPLineIO.close must use socket.SHUT_RDWR (full duplex "
            "shutdown) so both reads and writes are interrupted."
        )

    def test_close_does_not_deadlock_with_concurrent_read(self):
        """End-to-end: a concurrent ``recv`` blocked on the socket must"""
        srv, cli = socket.socketpair()
        try:
            io = _TCPLineIO(srv)

            # Start a reader thread that blocks on readline (waiting
            read_results: list[str] = []
            # Signal that the reader thread has STARTED and is
            reader_started = threading.Event()

            def reader():
                reader_started.set()
                try:
                    line = io.readline()
                    read_results.append(line)
                except OSError:
                    read_results.append("<aborted>")

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            # Bounded wait (1s) for the reader to start. If this
            reader_started.wait(timeout=1.0)

            # Close MUST interrupt the blocked readline and return
            close_done = threading.Event()

            def closer():
                io.close()
                close_done.set()

            ct = threading.Thread(target=closer, daemon=True)
            ct.start()

            # The close must complete within 2 seconds.  If the bug is
            assert close_done.wait(timeout=2.0), (
                "_TCPLineIO.close() deadlocked against a concurrent "
                "readline(), the fix (shutdown before close) is missing "
                "or broken."
            )
            # The reader thread should also have exited (readline returned
            t.join(timeout=2.0)
        finally:
            with __import__("contextlib").suppress(Exception):
                srv.close()
            with __import__("contextlib").suppress(Exception):
                cli.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
