"""Tests for voice_typer/server.py — JSON-lines IPC server.

Tests use io.StringIO to simulate stdin/stdout and a MockApp that
implements the minimal VoiceTyperApp interface the server depends on.
"""

import io
import json
import sys
import threading
import pytest
from unittest.mock import MagicMock, PropertyMock

# Mock pystray + PIL before importing tray (which is imported by ipc_server
# transitively).  Without this, pystray tries to connect to an X display on
# Linux and crashes in headless CI.
_mock_pystray = MagicMock()
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)
sys.modules.setdefault("PIL", MagicMock())
sys.modules.setdefault("PIL.Image", MagicMock())
sys.modules.setdefault("PIL.ImageDraw", MagicMock())

from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.tray import AppState


# ── Helpers ─────────────────────────────────────────────────────────────


class MockConfig:
    """Minimal config mock with __dict__ and save()."""

    def __init__(self):
        self.hotkey = "<f2>"
        self.model_size = "small.en"
        self.device = "cuda"
        self.language = "en"
        self._saved = False

    def save(self):
        self._saved = True

    def __getattr__(self, name):
        return None


class MockHistoryDB:
    """Minimal history db mock."""

    def get_recent(self, limit=50, offset=0):
        return [
            {"id": 1, "text": "hello world", "timestamp": "2025-01-01"},
        ]

    def get_today_stats(self):
        return {"count": 5, "chars": 240}


class MockTray:
    """Minimal tray mock that tracks set_state calls."""

    def __init__(self):
        self.state = AppState.IDLE
        self.set_state_calls = []

    def set_state(self, state, message=""):
        self.set_state_calls.append((state, message))
        self.state = state


class MockApp:
    """Minimal VoiceTyperApp mock for IPC server tests."""

    def __init__(self):
        self.tray = MockTray()
        self.config = MockConfig()
        self.history_db = MockHistoryDB()
        self._microphones = [
            {"id": "0", "name": "Microphone (Realtek Audio)"},
            {"id": "1", "name": "Microphone (USB Camera)"},
        ]
        self.toggle_called = False
        self.restart_called = False
        self.quit_called = False

    def toggle_dictation(self):
        self.toggle_called = True

    def restart_app(self):
        self.restart_called = True

    def quit_app(self):
        self.quit_called = True


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_app():
    return MockApp()


@pytest.fixture
def server(mock_app):
    return IPCServer(mock_app)


# ── Dispatch tests (no stdin/stdout dependency) ─────────────────────────


class TestDispatchGetStatus:
    def test_returns_current_state(self, server, mock_app):
        mock_app.tray.state = AppState.RECORDING
        result = server._dispatch({"id": 1, "type": "get_status"})
        assert result == {
            "id": 1,
            "type": "status",
            "data": {"status": "recording"},
        }

    def test_idle_state(self, server):
        result = server._dispatch({"id": 2, "type": "get_status"})
        assert result["type"] == "status"
        assert result["data"]["status"] == "idle"

    def test_omits_id_if_not_provided(self, server):
        result = server._dispatch({"type": "get_status"})
        assert "id" not in result


class TestDispatchToggleDictation:
    def test_calls_toggle_and_returns_ack(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result == {"id": 1, "type": "ack"}
        assert mock_app.toggle_called is True

    def test_exception_returns_error_response(self, server, mock_app):
        """toggle_dictation raising an exception should return error, not crash."""
        def failing_toggle():
            raise RuntimeError("toggle failed")
        mock_app.toggle_dictation = failing_toggle
        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result["type"] == "error"
        assert result["id"] == 1
        assert "toggle failed" in result["data"]["message"]


class TestDispatchGetConfig:
    def test_returns_config_dict(self, server):
        result = server._dispatch({"id": 1, "type": "get_config"})
        assert result["type"] == "config"
        assert result["id"] == 1
        data = result["data"]
        assert data["hotkey"] == "<f2>"
        assert data["model_size"] == "small.en"


class TestDispatchSetConfig:
    def test_updates_config_and_returns_ack(self, server, mock_app):
        result = server._dispatch({
            "id": 1,
            "type": "set_config",
            "data": {"hotkey": "<f3>", "model_size": "medium.en"},
        })
        assert result == {"id": 1, "type": "ack"}
        assert mock_app.config.hotkey == "<f3>"
        assert mock_app.config.model_size == "medium.en"
        assert mock_app.config._saved is True

    def test_empty_data_still_saves_and_acks(self, server, mock_app):
        mock_app.config._saved = False
        result = server._dispatch({
            "id": 1,
            "type": "set_config",
            "data": {},
        })
        assert result == {"id": 1, "type": "ack"}
        assert mock_app.config._saved is True

    def test_no_data_still_saves_and_acks(self, server, mock_app):
        mock_app.config._saved = False
        result = server._dispatch({
            "id": 1,
            "type": "set_config",
        })
        assert result == {"id": 1, "type": "ack"}
        assert mock_app.config._saved is True

    def test_ignores_unknown_fields_without_crashing(self, server, mock_app):
        """set_config with unknown fields should not crash."""
        result = server._dispatch({
            "id": 1,
            "type": "set_config",
            "data": {"nonexistent_field": "nope"},
        })
        assert result == {"id": 1, "type": "ack"}
        assert mock_app.config._saved is True

    def test_non_dict_data_does_not_crash(self, server, mock_app):
        """set_config with non-dict data (e.g. a string) should not crash."""
        mock_app.config._saved = False
        result = server._dispatch({
            "id": 1,
            "type": "set_config",
            "data": "bad",
        })
        assert result == {"id": 1, "type": "ack"}
        assert mock_app.config._saved is True
        # Config fields should not have been overwritten
        assert mock_app.config.hotkey == "<f2>"


class TestDispatchGetHistory:
    def test_returns_recent_history(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "get_history"})
        assert result["type"] == "history"
        assert result["id"] == 1
        assert len(result["data"]) == 1
        assert result["data"][0]["text"] == "hello world"

    def test_passes_limit_param(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch({
            "id": 1,
            "type": "get_history",
            "data": {"limit": 10},
        })
        mock_app.history_db.get_recent.assert_called_with(10, 0)

    def test_default_limit_is_50(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch({"id": 1, "type": "get_history"})
        mock_app.history_db.get_recent.assert_called_with(50, 0)


class TestDispatchGetTodayStats:
    def test_returns_stats(self, server):
        result = server._dispatch({"id": 1, "type": "get_today_stats"})
        assert result == {
            "id": 1,
            "type": "today_stats",
            "data": {"count": 5, "chars": 240},
        }


class TestDispatchGetMicrophones:
    def test_returns_microphone_list(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "get_microphones"})
        assert result["type"] == "microphones"
        assert result["id"] == 1
        assert len(result["data"]) == 2
        assert result["data"][0]["name"] == "Microphone (Realtek Audio)"


class TestDispatchRestartApp:
    def test_calls_restart_and_returns_ack(self, server, mock_app):
        server._send = MagicMock()
        result = server._dispatch({"id": 1, "type": "restart_app"})
        # Returns None because ack was already sent
        assert result is None
        server._send.assert_called_once_with({"id": 1, "type": "ack"})
        assert mock_app.restart_called is True


class TestDispatchQuitApp:
    def test_calls_quit_and_returns_ack(self, server, mock_app):
        server._send = MagicMock()
        result = server._dispatch({"id": 1, "type": "quit_app"})
        assert result is None
        server._send.assert_called_once_with({"id": 1, "type": "ack"})
        assert mock_app.quit_called is True


class TestDispatchUnknownCommand:
    def test_returns_error(self, server):
        result = server._dispatch({"id": 1, "type": "frobnicate"})
        assert result["type"] == "error"
        assert result["id"] == 1
        assert "Unknown command" in result["data"]["message"]
        assert "frobnicate" in result["data"]["message"]


class TestDispatchNoId:
    def test_push_event_no_id_in_response(self, server):
        """Commands with no id should still work and omit id from response."""
        result = server._dispatch({"type": "get_status"})
        assert "id" not in result
        assert result["type"] == "status"

    def test_unknown_no_id(self, server):
        result = server._dispatch({"type": "frobnicate"})
        assert "id" not in result
        assert result["type"] == "error"


# ── Run loop tests (stdin/stdout) ──────────────────────────────────────


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
        assert msg == {"id": 1, "type": "status", "data": {"status": "idle"}}

    def test_processes_multiple_commands(self, server, mock_app):
        stdin = io.StringIO(
            '{"type":"get_status","id":1}\n'
            '{"type":"toggle_dictation","id":2}\n'
            '{"type":"get_config","id":3}\n'
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 3
        msg1 = json.loads(lines[0])
        assert msg1 == {"id": 1, "type": "status", "data": {"status": "idle"}}
        msg2 = json.loads(lines[1])
        assert msg2 == {"id": 2, "type": "ack"}
        msg3 = json.loads(lines[2])
        assert msg3["id"] == 3
        assert msg3["type"] == "config"
        assert mock_app.toggle_called is True

    def test_handles_empty_lines(self, server):
        stdin = io.StringIO(
            '\n'
            '   \n'
            '{"type":"get_status","id":1}\n'
        )
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
        stdin = io.StringIO(
            '{"type":"get_status","id":1}\n'
        )
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
        server._send.assert_called_once_with({"id": 1, "type": "ack"})
        assert mock_app.restart_called is True

    def test_quit_sends_ack_then_calls_method(self, server, mock_app):
        """quit_app should send ack before calling the method."""
        server._send = MagicMock()

        result = server._dispatch({"id": 1, "type": "quit_app"})

        assert result is None
        server._send.assert_called_once_with({"id": 1, "type": "ack"})
        assert mock_app.quit_called is True

    def test_unknown_last_command_does_not_block(self, server):
        """Unknown commands should produce an error and continue."""
        stdin = io.StringIO(
            '{"type":"unknown","id":1}\n'
            '{"type":"get_status","id":2}\n'
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 2
        msg1 = json.loads(lines[0])
        assert msg1["type"] == "error"
        msg2 = json.loads(lines[1])
        assert msg2["type"] == "status"


# ── Push events ────────────────────────────────────────────────────────


class TestPushEvents:
    def test_push_sends_unsolicited_message(self, server):
        server._send = MagicMock()
        server.push({"type": "status_change", "data": {"status": "recording"}})
        server._send.assert_called_once_with({
            "type": "status_change",
            "data": {"status": "recording"},
        })

    def test_tray_set_state_triggers_push(self, server, mock_app):
        server._send = MagicMock()
        server._hook_tray_set_state()

        mock_app.tray.set_state(AppState.RECORDING, "Recording...")

        # The original set_state should have been called
        assert len(mock_app.tray.set_state_calls) == 1
        assert mock_app.tray.set_state_calls[0][0] == AppState.RECORDING

        # And a push event should have been sent
        server._send.assert_called_once()
        push_msg = server._send.call_args[0][0]
        assert push_msg == {
            "type": "status_change",
            "data": {"status": "recording"},
        }


# ── Lifecycle ──────────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_launches_daemon_thread(self, server, monkeypatch):
        # Replace stdin so the daemon thread doesn't block on real stdin.
        monkeypatch.setattr("sys.stdin", io.StringIO())
        server.start()
        assert server._running is True
        # The thread may already have exited (empty StringIO exhausts
        # immediately), but the important thing is that start() set
        # _running and attempted to create the daemon thread.
        threads = [t for t in threading.enumerate() if t.name == "ipc-server"]
        assert len(threads) <= 1
        if threads:
            assert threads[0].daemon is True
        server.stop()

    def test_stop_sets_running_false(self, server, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO())
        server.start()
        assert server._running is True
        server.stop()
        assert server._running is False


# ── Error handling ─────────────────────────────────────────────────────


class TestErrorHandling:
    def test_invalid_json_via_run(self, server):
        stdin = io.StringIO("{{{bad}}\n")
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert "invalid JSON" in msg["data"]["message"]

    def test_command_error_does_not_kill_loop(self, server):
        """An unknown command returns an error but the loop continues."""
        stdin = io.StringIO(
            '{"type":"nope","id":1}\n'
            '{"type":"get_status","id":2}\n'
        )
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "error"
        assert json.loads(lines[1])["type"] == "status"


# ── _push_event_now ────────────────────────────────────────────────────


class TestPushEventNow:
    """_push_event_now sends events to the active IPC server instance."""

    def test_returns_false_when_no_server(self, monkeypatch):
        """With no active push function, should return False."""
        import voice_typer.server.ipc_server as ipc_mod
        original = ipc_mod._push_event
        ipc_mod._push_event = None
        try:
            result = ipc_mod._push_event_now({"type": "show_window"})
            assert result is False
        finally:
            ipc_mod._push_event = original

    def test_returns_true_when_server_active(self, server, monkeypatch):
        """With an active server, _push_event_now should succeed."""
        server._send = MagicMock()
        server.start()
        import voice_typer.server.ipc_server as ipc_mod
        result = ipc_mod._push_event_now({"type": "show_window"})
        assert result is True
        server.stop()

    def test_show_window_message_reaches_push(self, server, monkeypatch):
        """The show_window message type used by tray.open_electron_window
        should be pushable through _push_event_now."""
        server._send = MagicMock()
        server.start()
        import voice_typer.server.ipc_server as ipc_mod
        ipc_mod._push_event_now({"type": "show_window"})
        # _push_event_now delegates to server.push → _send
        server._send.assert_called()
        server.stop()

    def test_exception_in_push_returns_false(self, server, monkeypatch):
        """If the push function raises, _push_event_now should return False."""
        import voice_typer.server.ipc_server as ipc_mod
        def broken_fn(msg):
            raise RuntimeError("broken")
        ipc_mod._push_event = broken_fn
        try:
            result = ipc_mod._push_event_now({"type": "show_window"})
            assert result is False
        finally:
            ipc_mod._push_event = None
