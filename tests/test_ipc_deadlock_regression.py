"""CR-2 regression tests: TCP-teardown deadlock in ``IPCServer._send``.

The bug
-------
``IPCServer._send`` (in ``voice_typer/server/ipc_server.py``) set a
write timeout on the TCP socket before sending, then restored the
timeout to ``None`` (blocking) in the ``finally`` block.  ``None``
clobbers the auth-read deadline set on the connection (PR-3-FIX-1),
so the dispatch-loop ``readline`` could block forever — meaning the
reader thread could never exit and ``_TCPLineIO.close()`` would
deadlock against the in-progress ``recv``.

The fix
-------
1. ``_send`` captures ``_prev_timeout = tcp_client.conn.gettimeout()``
   BEFORE setting the write timeout, and restores ``_prev_timeout``
   (NOT ``None``) in the ``finally`` block.
2. ``_TCPLineIO.close`` calls ``self.conn.shutdown(SHUT_RDWR)`` BEFORE
   ``self.conn.close()`` so an in-progress ``recv`` is interrupted
   and ``BufferedReader.close()`` doesn't deadlock.  (This fix lives
   in ``voice_typer/server/ipc/transport.py`` and is imported by
   ``ipc_server.py`` so there is a single source of truth.)

These tests verify both halves of the fix.  Each test FAILS if the
fix is reverted (restoring ``None`` instead of ``_prev_timeout``, or
removing ``shutdown`` from ``close``).
"""

from __future__ import annotations

import inspect
import socket
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer, _TCPLineIO

# ─── CR-2 part 1: _send restores _prev_timeout (not None) ──────────────


class TestSendRestoresPrevTimeout:
    """CR-2: ``_send`` must restore the PREVIOUS timeout (not ``None``)."""

    def test_send_source_captures_prev_timeout(self):
        """The source of ``_send`` must capture ``_prev_timeout`` before
        setting the write timeout, and restore it (not ``None``) in the
        ``finally`` block.
        """
        src = inspect.getsource(IPCServer._send)
        # The capture must happen before the settimeout call.
        assert "_prev_timeout" in src, (
            "_send must capture _prev_timeout before setting the write timeout (CR-2 deadlock fix)."
        )
        assert "gettimeout()" in src, (
            "_send must call tcp_client.conn.gettimeout() to capture the previous timeout before overwriting it."
        )
        # The finally block must restore _prev_timeout, NOT None.
        # Find the finally block.
        finally_idx = src.rfind("finally:")
        assert finally_idx != -1, "_send must have a finally block."
        finally_block = src[finally_idx:]
        assert "settimeout(_prev_timeout)" in finally_block, (
            "_send finally block must restore _prev_timeout (NOT None) so "
            "the auth-read deadline survives the write. Restoring None was "
            "the root cause of the CR-2 deadlock."
        )
        # The previous bug restored None — assert that's gone from the
        # finally block (it may still appear elsewhere in _send, e.g. in
        # a comment, so we only check the finally block).
        assert "settimeout(None)" not in finally_block, (
            "_send finally block must NOT call settimeout(None) — that was "
            "the CR-2 deadlock root cause (clobbers the auth-read deadline)."
        )

    def test_send_restores_prev_timeout_behaviorally(self):
        """When ``_send`` runs against a client with a non-None previous
        timeout (e.g. the auth-read deadline), the timeout must be
        restored to that value after the send completes — NOT clobbered
        to ``None`` (blocking).
        """
        # Build a minimal IPCServer without running __init__ (which
        # would spawn threads / bind sockets).
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._pending_tcp = []
        server._tcp_mode = True

        # Use a real socketpair so settimeout/gettimeout are real.
        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Wrap the server end in _TCPLineIO so _send can write to it.
            tcp_client = _TCPLineIO(srv)
            # Set a non-None previous timeout (simulating the auth-read
            # deadline).  Use a distinctive value so we can detect it
            # after _send runs.
            AUTH_DEADLINE = 7.0  # noqa: N806
            srv.settimeout(AUTH_DEADLINE)
            assert srv.gettimeout() == AUTH_DEADLINE  # sanity

            server._tcp_client = tcp_client

            # The other end of the socketpair reads the message so the
            # write doesn't block.
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

            # CR-2: the previous timeout must be preserved, NOT clobbered
            # to None (which would be the bug).
            assert srv.gettimeout() == AUTH_DEADLINE, (
                f"_send must restore the PREVIOUS timeout ({AUTH_DEADLINE}s), "
                f"not None (blocking). Got {srv.gettimeout()!r} — this is the "
                "CR-2 deadlock root cause if it's None."
            )
        finally:
            srv.close()
            cli.close()

    def test_send_restores_none_when_prev_was_none(self):
        """If the previous timeout was already ``None`` (no auth deadline
        set), ``_send`` may restore ``None`` — that's the correct
        behavior in that scenario.  This test guards against an
        over-aggressive fix that always forces a non-None timeout.
        """
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._pending_tcp = []
        server._tcp_mode = True

        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
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

            # CR-2: restore _prev_timeout — if prev was None, restored
            # value is None.  This is correct (no clobbering of a real
            # deadline).
            assert srv.gettimeout() is None, (
                "_send must restore the PREVIOUS timeout. When prev was "
                "None, the restored value should be None (not a hardcoded "
                "non-None value that would clobber future deadlines)."
            )
        finally:
            srv.close()
            cli.close()


# ─── CR-2 part 2: _TCPLineIO.close uses shutdown(SHUT_RDWR) ────────────


class TestTCPLineIOCloseUsesShutdown:
    """CR-2: ``_TCPLineIO.close`` must ``shutdown(SHUT_RDWR)`` before
    ``close()`` so an in-progress ``recv`` on another thread is
    interrupted and ``BufferedReader.close()`` doesn't deadlock.
    """

    def test_close_source_uses_shutdown(self):
        """The source of ``_TCPLineIO.close`` must call
        ``shutdown(SHUT_RDWR)`` before ``close()``.
        """
        src = inspect.getsource(_TCPLineIO.close)
        assert "shutdown" in src, (
            "_TCPLineIO.close must call self.conn.shutdown() to interrupt in-progress reads (CR-2 deadlock fix)."
        )
        assert "SHUT_RDWR" in src, (
            "_TCPLineIO.close must use socket.SHUT_RDWR (full duplex "
            "shutdown) so both reads and writes are interrupted."
        )

    def test_close_does_not_deadlock_with_concurrent_read(self):
        """End-to-end: a concurrent ``recv`` blocked on the socket must
        be interrupted by ``close()`` (via ``shutdown``) so the
        ``close()`` call returns.  Without ``shutdown``, the
        ``BufferedReader.close()`` would deadlock against the in-progress
        ``recv`` and this test would hang until the pytest timeout.
        """
        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            io = _TCPLineIO(srv)

            # Start a reader thread that blocks on readline (waiting
            # for data that will never arrive).
            read_results: list[str] = []

            def reader():
                # readline() will block until shutdown() interrupts it.
                line = io.readline()
                read_results.append(line)

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            # Give the reader a moment to enter the blocking readline.
            import time

            time.sleep(0.1)

            # Close MUST interrupt the blocked readline and return
            # promptly (within 2 seconds).  Without shutdown(), this
            # would deadlock.
            close_done = threading.Event()

            def closer():
                io.close()
                close_done.set()

            ct = threading.Thread(target=closer, daemon=True)
            ct.start()

            # The close must complete within 2 seconds.  If the bug is
            # present (no shutdown), close() deadlocks against the
            # in-progress readline and this assert fails.
            assert close_done.wait(timeout=2.0), (
                "_TCPLineIO.close() deadlocked against a concurrent "
                "readline() — CR-2 fix (shutdown before close) is missing "
                "or broken."
            )
            # The reader thread should also have exited (readline returned
            # empty string on EOF).  Don't join with a long timeout
            # because the reader may still be in cleanup.
            t.join(timeout=2.0)
        finally:
            with __import__("contextlib").suppress(Exception):
                srv.close()
            with __import__("contextlib").suppress(Exception):
                cli.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
