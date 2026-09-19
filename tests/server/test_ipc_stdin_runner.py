"""Behavioral tests for ``voice_typer.server.ipc.stdin_runner.StdinRunnerMixin``."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    server,
)


class TestFullLineDispatch:
    """A complete JSON line (with trailing ``\\n``) is the canonical unit"""

    def test_single_full_line_dispatched(self, server) -> None:
        """One complete JSON command produces one response envelope."""
        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["id"] == 1
        assert msg["type"] == "status"

    def test_multiple_full_lines_in_order(self, server) -> None:
        """Multiple complete lines are dispatched in the order they were"""
        stdin = io.StringIO(
            '{"type":"get_status","id":1}\n{"type":"get_status","id":2}\n{"type":"get_status","id":3}\n'
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines, start=1):
            msg = json.loads(line)
            assert msg["id"] == i
            assert msg["type"] == "status"

    def test_blank_and_whitespace_only_lines_skipped(self, server) -> None:
        """Lines that strip to empty (blank, or only whitespace) are"""
        stdin = io.StringIO(
            "\n"  # blank
            "   \n"  # whitespace only
            "\t\n"  # tab only
            '{"type":"get_status","id":1}\n'  # real command
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        lines = stdout.getvalue().strip().split("\n")
        # Only one response, the three whitespace-only lines were skipped.
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == 1

    def test_line_with_leading_trailing_whitespace_stripped(self, server) -> None:
        """A real JSON command surrounded by leading/trailing whitespace"""
        stdin = io.StringIO('   {"type":"get_status","id":1}   \n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["id"] == 1
        assert msg["type"] == "status"


class TestEofAndCleanExit:
    """When stdin reaches EOF (or raises OSError on read), the loop exits"""

    def test_eof_exits_cleanly_and_calls_disconnect(self, server) -> None:
        """An empty stdin (EOF immediately) exits the loop and calls"""
        stdin = io.StringIO("")  # EOF immediately
        stdout = io.StringIO()
        server._running = True
        # Mock the disconnect hook so we can assert it was called.
        server._on_ipc_client_disconnect = MagicMock()
        server._run(_stdin=stdin, _stdout=stdout)
        # No output written (no commands processed).
        assert stdout.getvalue() == ""
        # Disconnect was called once with the EOF reason.
        server._on_ipc_client_disconnect.assert_called_once()
        args, _ = server._on_ipc_client_disconnect.call_args
        assert "stdin EOF" in args[0]

    def test_eof_after_commands_still_calls_disconnect(self, server) -> None:
        """After processing commands and reaching EOF, the disconnect"""
        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._on_ipc_client_disconnect = MagicMock()
        server._run(_stdin=stdin, _stdout=stdout)
        # The command was dispatched.
        assert "status" in stdout.getvalue()
        # Disconnect was called after EOF.
        server._on_ipc_client_disconnect.assert_called_once()

    def test_oserror_on_iter_returns_without_raising(self, server) -> None:
        """If ``iter(stdin)`` raises ``OSError`` (e.g. stdin not"""

        class _BrokenStdin:
            def __iter__(self):
                raise OSError("stdin not available")

        stdout = io.StringIO()
        server._running = True
        server._on_ipc_client_disconnect = MagicMock()
        # Must not raise.
        server._run(_stdin=_BrokenStdin(), _stdout=stdout)
        server._on_ipc_client_disconnect.assert_not_called()


class TestUnicodeHandling:
    """JSON payloads with unicode (CJK / emoji / accented Latin)"""

    def test_cjk_characters_in_command_data(self, server) -> None:
        """A command with CJK characters in its data field is dispatched"""
        stdin = io.StringIO(
            json.dumps(
                {
                    "type": "set_config",
                    "id": 1,
                    "data": {"hotkey": "<f2>"},
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        # The ack response shape is preserved.
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["type"] == "ack"

    def test_unicode_in_response_payload(self, server, mock_app) -> None:
        """A handler that returns unicode in its response envelope (e.g."""
        # Stub the get_status handler to return a unicode-laden message
        unicode_payload = "こんにちは世界 🌍 café"
        server._dispatch = lambda msg: {
            "id": msg.get("id"),
            "type": "echo",
            "data": {"text": unicode_payload},
        }
        stdin = io.StringIO('{"type":"echo","id":42}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        msg = json.loads(stdout.getvalue().strip())
        assert msg["id"] == 42
        assert msg["type"] == "echo"
        assert msg["data"]["text"] == unicode_payload

    def test_emoji_in_command_id_field_round_trips(self, server) -> None:
        """Even an emoji in the id field (unusual but legal JSON)"""
        stdin = io.StringIO('{"type":"get_status","id":"🚀"}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        msg = json.loads(stdout.getvalue().strip())
        assert msg["id"] == "🚀"


class TestErrorEnvelopes:
    """The stdin runner emits well-formed error envelopes for the three"""

    def test_invalid_json_emits_bare_error_envelope(self, server) -> None:
        """A line that fails JSON parsing emits a bare"""
        stdin = io.StringIO("{{{not json\n")
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert msg["data"] == {"message": "invalid JSON"}
        # No ``code`` field (bare backward-compat envelope).
        assert "code" not in msg["data"]

    def test_non_dict_json_emits_invalid_payload_envelope(self, server) -> None:
        """A JSON line that parses to a non-dict (list, int, str, None)"""
        for bad_payload in ["[1, 2, 3]", "42", '"hello"', "null"]:
            stdin = io.StringIO(bad_payload + "\n")
            stdout = io.StringIO()
            server._running = True
            server._run(_stdin=stdin, _stdout=stdout)
            msg = json.loads(stdout.getvalue().strip())
            assert msg["type"] == "error", f"payload={bad_payload!r}"
            assert msg["data"]["message"] == "message must be a JSON object"
            assert msg["data"]["code"] == "client.invalid_payload"

    def test_dispatch_exception_emits_internal_error_and_continues(self, server) -> None:
        """``server.internal_error`` envelope and continues processing"""
        call_count = [0]

        def _flaky_dispatch(msg):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated handler crash")
            return {"id": msg.get("id"), "type": "status", "data": {}}

        server._dispatch = _flaky_dispatch
        stdin = io.StringIO(
            '{"type":"get_status","id":1}\n'  # crashes
            '{"type":"get_status","id":2}\n'  # succeeds
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 2, "both lines must produce a response, the crash on line 1 must not kill the loop"
        err_msg = json.loads(lines[0])
        assert err_msg["type"] == "error"
        assert err_msg["data"]["code"] == "server.internal_error"
        assert err_msg["data"]["message"] == "internal error"
        ok_msg = json.loads(lines[1])
        assert ok_msg["id"] == 2
        assert ok_msg["type"] == "status"
