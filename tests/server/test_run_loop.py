"""IPC run-loop tests (stdin/stdout) and TCP accept-loop / end-to-end coverage.

Classes:
- TestRunLoop                — basic stdin/stdout dispatch loop
- TestRunLoopRestartQuit     — restart/quit ack ordering inside the loop
- TestStopUnblocksAcceptLoop — NEW-IPC-001 stop() must unblock accept()
- TestEndToEndHappyPath      — TEST-002 multi-command roundtrip

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

import inspect
import io
import json
import socket
import time
from unittest.mock import MagicMock, patch

from tests.server.conftest import (  # noqa: F401
    IPCServer,
    mock_app,
    server,
    server_with_mock_app,
)


class TestRunLoop:
    def test_processes_single_command(self, server):
        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        output = stdout.getvalue()
        lines = output.strip().split("\n")
        assert len(lines) == 1
        msg = json.loads(lines[0])
        # get_status now returns a dict with xruns_since_start.
        assert msg["id"] == 1
        assert msg["type"] == "status"
        assert msg["data"]["status"] == "idle"
        assert "xruns_since_start" in msg["data"]

    def test_processes_multiple_commands(self, server, mock_app):
        stdin = io.StringIO(
            '{"type":"get_status","id":1}\n{"type":"toggle_dictation","id":2}\n{"type":"get_config","id":3}\n'
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 3
        msg1 = json.loads(lines[0])
        # get_status now returns a dict with xruns_since_start.
        assert msg1["id"] == 1
        assert msg1["type"] == "status"
        assert msg1["data"]["status"] == "idle"
        msg2 = json.loads(lines[1])
        # ack responses now include ``data: {}``.
        assert msg2 == {"id": 2, "type": "ack", "data": {}}
        msg3 = json.loads(lines[2])
        assert msg3["id"] == 3
        assert msg3["type"] == "config"
        assert mock_app.toggle_called is True

    def test_handles_empty_lines(self, server):
        stdin = io.StringIO('\n   \n{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 1

    def test_handles_invalid_json(self, server):
        stdin = io.StringIO("not valid json\n")
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        msg = json.loads(stdout.getvalue().strip())
        assert msg == {"type": "error", "data": {"message": "invalid JSON"}}

    def test_stop_breaks_loop(self, server):
        """stop() should cause _run() to exit without writing output."""
        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server.stop()  # set _running = False before loop runs
        server._run(_stdin=stdin, _stdout=stdout)
        # The loop should break immediately - no output written
        assert stdout.getvalue() == ""


class TestRunLoopRestartQuit:
    def test_restart_sends_ack_then_calls_method(self, server, mock_app):
        """restart_app should send ack before calling the method."""
        server._send = MagicMock()

        result = server._dispatch({"id": 1, "type": "restart_app"})

        assert result is None
        # ack now includes explicit ``data: {}``.
        server._send.assert_called_once_with({"id": 1, "type": "ack", "data": {}})
        assert mock_app.restart_called is True

    def test_quit_sends_ack_then_calls_method(self, server, mock_app):
        """quit_app should send ack before calling the method."""
        server._send = MagicMock()

        result = server._dispatch({"id": 1, "type": "quit_app"})

        assert result is None
        # ack now includes explicit ``data: {}``.
        server._send.assert_called_once_with({"id": 1, "type": "ack", "data": {}})
        assert mock_app.quit_called is True

    def test_unknown_last_command_does_not_block(self, server):
        """Unknown commands should produce an error and continue."""
        stdin = io.StringIO('{"type":"unknown","id":1}\n{"type":"get_status","id":2}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 2
        msg1 = json.loads(lines[0])
        assert msg1["type"] == "error"
        msg2 = json.loads(lines[1])
        assert msg2["type"] == "status"


# === : TCP accept loop must be unblockable by stop() ===
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
                while srv._tcp_server_socket is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert srv._tcp_server_socket is not None, "accept thread did not store the listening socket"

                # Now stop() must unblock the accept loop.
                srv.stop()

                # The accept thread should exit promptly.  We can't
                # join a daemon thread we didn't keep a reference to,
                # but we CAN verify the socket is closed and the
                # _running flag is False.  We also wait briefly and
                # confirm the socket reference is cleared by the loop
                # exit path.
                deadline = time.monotonic() + 2.0
                while srv._tcp_server_socket is not None and time.monotonic() < deadline:
                    time.sleep(0.02)
                # The accept loop's exit path clears the reference.
                assert srv._tcp_server_socket is None, "accept loop did not clear _tcp_server_socket on exit"
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
            "_accept_tcp must use `while self._running:` as its loop condition (the canonical flag set by stop())"
        )
        # The legacy getattr pattern must NOT appear in actual code.
        assert "getattr(self" not in code_only, "_accept_tcp still uses the legacy getattr(self, ...) pattern"

    def test_stop_clears_listening_socket_ref(self, server_with_mock_app):
        """The instance must store _tcp_server_socket (not just a local
        var) so stop() can close it.  This is a static check.
        """
        init_src = inspect.getsource(IPCServer.__init__)
        assert "_tcp_server_socket" in init_src, "IPCServer.__init__ must initialize _tcp_server_socket"
        accept_src = inspect.getsource(IPCServer._accept_tcp)
        assert "self._tcp_server_socket = server" in accept_src, "_accept_tcp must store the listening socket on self"
        stop_src = inspect.getsource(IPCServer.stop)
        assert "_tcp_server_socket" in stop_src, "stop() must close the listening socket"


# End-to-end happy-path test ─────────────────────────────────


class TestEndToEndHappyPath:
    """TEST-002: exercise the full IPC dispatch roundtrip from
    get_status → toggle_dictation → get_history → set_config.

    This test doesn't test the actual audio recording (that requires
    hardware), but it verifies that the IPC dispatcher correctly
    routes commands to the app, the app processes them, and the
    response shape is correct end-to-end."""

    def test_full_ipc_roundtrip(self, server, mock_app):
        """Verify a sequence of IPC commands produces correct responses."""
        # removed unused local `import json` (ruff F401).
        # json is already imported at module level (line 8).

        # 1. Check initial status
        result = server._dispatch({"id": 1, "type": "get_status"})
        assert result["type"] == "status"
        assert result["data"]["status"] == "idle"

        # 2. Toggle dictation (start)
        result = server._dispatch({"id": 2, "type": "toggle_dictation"})
        assert result["type"] == "ack"
        assert mock_app.toggle_called is True

        # 3. Get config (verify it's sanitized)
        mock_app.config.cloud_api_key = "sk-test-key"
        result = server._dispatch({"id": 3, "type": "get_config"})
        assert result["type"] == "config"
        assert result["data"]["cloud_api_key"] == "<redacted>"

        # 4. Set config (verify allowlist)
        result = server._dispatch(
            {
                "id": 4,
                "type": "set_config",
                "data": {"hotkey": "<f5>"},
            }
        )
        assert result["type"] == "ack"
        assert mock_app.config.hotkey == "<f5>"

        # 5. Get history
        result = server._dispatch({"id": 5, "type": "get_history"})
        assert result["type"] == "history"
        assert len(result["data"]) >= 1

        # 6. Get today stats
        result = server._dispatch({"id": 6, "type": "get_today_stats"})
        assert result["type"] == "today_stats"
        assert "count" in result["data"]

        # 7. Toggle dictation (stop)
        result = server._dispatch({"id": 7, "type": "toggle_dictation"})
        assert result["type"] == "ack"

        # 8. Verify the app processed everything
        assert mock_app.toggle_called is True
        assert mock_app.config._saved is True

    def test_undo_last_ipc_command(self, server, mock_app):
        """TEST-002: undo_last IPC command is dispatched correctly."""
        # Add undo_last to mock_app
        mock_app.undo_called = False

        def undo_last():
            mock_app.undo_called = True

        mock_app.undo_last = undo_last

        result = server._dispatch({"id": 1, "type": "undo_last"})
        # ack responses now include ``data: {}``.
        assert result == {"id": 1, "type": "ack", "data": {}}
        assert mock_app.undo_called is True

    def test_error_recovery_after_failed_command(self, server, mock_app):
        """TEST-002: after a failed command, the server should still
        process subsequent commands."""

        # Make toggle_dictation fail
        def failing_toggle():
            raise RuntimeError("toggle failed")

        mock_app.toggle_dictation = failing_toggle

        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result["type"] == "error"
        # (): generic envelope, no str(e) leak.
        assert result["data"]["code"] == "server.internal_error"
        assert result["data"]["message"] == "internal error"

        # Next command should still work
        result = server._dispatch({"id": 2, "type": "get_status"})
        assert result["type"] == "status"
        assert result["data"]["status"] == "idle"
