"""IPC run-loop tests (stdin/stdout) and TCP accept-loop / end-to-end coverage.

Classes:
- TestRunLoop              , basic stdin/stdout dispatch loop
- TestRunLoopRestartQuit   , restart/quit ack ordering inside the loop
- TestStopUnblocksAcceptLoop, NEW-IPC-001 stop() must unblock accept()
- TestEndToEndHappyPath    , TEST-002 multi-command roundtrip

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

import io
import json
from unittest.mock import MagicMock

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
