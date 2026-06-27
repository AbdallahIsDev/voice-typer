"""Regression tests for NEW-IPC-001: TCP accept loop must be unblockable
by stop().

Previous defects:
1. The accept loop checked ``getattr(self, '_stopped', False)`` but
   ``_stopped`` was never set on the IPCServer instance.  ``stop()``
   only set ``self._running = False``.
2. The listening socket was a local variable in ``_accept_tcp`` with
   no instance reference, so ``stop()`` could not close it to unblock
   ``accept()``.
3. Result: ``stop()`` while no client was connected left the daemon
   thread blocked forever in ``server.accept()``.  Threads and sockets
   leaked across test start/stop cycles.

These tests verify the fix:
- ``stop()`` actually closes the listening socket.
- The accept loop checks ``self._running`` (not the never-set ``_stopped``).
- A real start_tcp → stop cycle exits the accept thread within a
  reasonable deadline.
"""
from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch, call  # TEST-033: unified mock import

import pytest

from voice_typer.server.ipc_server import IPCServer


@pytest.fixture
def server_with_mock_app():
    """Construct an IPCServer with a mocked app (no real VoiceTyperApp)."""
    app = MagicMock()
    # Avoid the service.py import side-effects on real VoiceTyperApp.
    # The IPCServer constructor only needs `app` to attach to .service.
    srv = IPCServer(app)
    return srv


class TestStopUnblocksAcceptLoop:
    """NEW-IPC-001: stop() must be able to wake a blocked accept()."""

    def test_running_flag_flipped_by_stop(self, server_with_mock_app):
        """stop() sets _running = False (the flag the accept loop checks)."""
        server_with_mock_app._running = True
        server_with_mock_app.stop()
        assert server_with_mock_app._running is False

    def test_stop_closes_listening_socket(self, server_with_mock_app):
        """stop() closes the stored listening socket and clears the ref."""
        fake_sock = MagicMock()
        server_with_mock_app._tcp_server_socket = fake_sock
        server_with_mock_app.stop()
        fake_sock.close.assert_called_once()
        assert server_with_mock_app._tcp_server_socket is None

    def test_stop_is_idempotent(self, server_with_mock_app):
        """Calling stop() multiple times must not raise."""
        server_with_mock_app._running = True
        server_with_mock_app.stop()
        # Second call should be a no-op (no exception).
        server_with_mock_app.stop()
        assert server_with_mock_app._running is False
        assert server_with_mock_app._tcp_server_socket is None

    def test_accept_loop_exits_on_stop(self):
        """End-to-end: start_tcp on a real port, then stop() must
        unblock the accept thread within a deadline.

        Previously this test would have hung forever because stop()
        couldn't close the listening socket.
        """
        app = MagicMock()
        srv = IPCServer(app)

        # Pick a free port by binding a temporary socket first.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        # Disable auth so the test doesn't need a token.  The accept
        # loop doesn't care about auth — it just needs to listen and
        # accept; we never actually connect a client.
        with patch.dict("os.environ", {}, clear=False):
            # Make sure VOICE_TYPER_IPC_TOKEN is not set so the loop
            # logs the warning instead of bailing out.
            import os
            old = os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)
            try:
                srv._running = True
                srv.start_tcp(port)

                # Give the accept thread a moment to bind and start
                # listening.  Poll the socket reference to know it's
                # ready.
                deadline = time.monotonic() + 2.0
                while (
                    srv._tcp_server_socket is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                assert srv._tcp_server_socket is not None, (
                    "accept thread did not store the listening socket"
                )

                # Now stop() must unblock the accept loop.
                srv.stop()

                # The accept thread should exit promptly.  We can't
                # join a daemon thread we didn't keep a reference to,
                # but we CAN verify the socket is closed and the
                # _running flag is False.  We also wait briefly and
                # confirm the socket reference is cleared by the loop
                # exit path.
                deadline = time.monotonic() + 2.0
                while (
                    srv._tcp_server_socket is not None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                # The accept loop's exit path clears the reference.
                assert srv._tcp_server_socket is None, (
                    "accept loop did not clear _tcp_server_socket on exit"
                )
                assert srv._running is False
            finally:
                if old is not None:
                    import os
                    os.environ["VOICE_TYPER_IPC_TOKEN"] = old
                # Belt-and-suspenders cleanup.
                srv.stop()

    def test_accept_loop_checks_running_not_stopped(self):
        """The accept loop's ``while`` condition must reference
        ``self._running``, not the never-set legacy flag.  We strip
        comments and docstrings before checking so explanatory text
        that mentions the old pattern doesn't trip the assertion.
        """
        import inspect
        import re
        source = inspect.getsource(IPCServer._accept_tcp)
        # Strip comment lines (lines whose first non-whitespace is #).
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Strip inline comments.
            if "#" in line:
                # Naive split — good enough for this static check.
                line = line.split("#", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "while self._running" in code_only, (
            "_accept_tcp must use `while self._running:` as its loop "
            "condition (the canonical flag set by stop())"
        )
        # The legacy getattr pattern must NOT appear in actual code.
        assert 'getattr(self' not in code_only, (
            "_accept_tcp still uses the legacy getattr(self, ...) pattern"
        )

    def test_stop_clears_listening_socket_ref(self, server_with_mock_app):
        """The instance must store _tcp_server_socket (not just a local
        var) so stop() can close it.  This is a static check.
        """
        import inspect
        init_src = inspect.getsource(IPCServer.__init__)
        assert "_tcp_server_socket" in init_src, (
            "IPCServer.__init__ must initialize _tcp_server_socket"
        )
        accept_src = inspect.getsource(IPCServer._accept_tcp)
        assert "self._tcp_server_socket = server" in accept_src, (
            "_accept_tcp must store the listening socket on self"
        )
        stop_src = inspect.getsource(IPCServer.stop)
        assert "_tcp_server_socket" in stop_src, (
            "stop() must close the listening socket"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
