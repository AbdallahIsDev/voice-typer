"""All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``"""

import json
from unittest.mock import MagicMock, patch


class TestTrayControllerProtocolCompliance:
    """Verify VoiceTyperApp implements all TrayController protocol methods."""

    REQUIRED_PUBLIC_METHODS = [
        "toggle_dictation",
        "quit",
    ]

    REQUIRED_CALLBACK_METHODS = [
        "_toggle_autostart",
        "_set_notifications",
        "_select_microphone",
        # ``_change_model`` and ``_restart_hotkey`` removed —
    ]

    def test_app_has_all_traycontroller_public_methods(self, app):
        """VoiceTyperApp must expose public methods for the TrayController protocol."""
        for method in self.REQUIRED_PUBLIC_METHODS:
            assert hasattr(app, method), f"Missing public method: {method}"
            assert callable(getattr(app, method)), f"Attribute '{method}' exists but is not callable"

    def test_app_has_all_tray_callback_methods(self, app):
        """VoiceTyperApp must have the private methods wired as TrayIcon callbacks."""
        for method in self.REQUIRED_CALLBACK_METHODS:
            assert hasattr(app, method), f"Missing callback method: {method}"
            assert callable(getattr(app, method)), f"Attribute '{method}' exists but is not callable"


class TestWin32ConsoleHandler:
    """P2 fix: Test the Win32 console control handler."""

    def test_ctrl_close_event_frees_console(self, app):
        """CTRL_CLOSE_EVENT should call FreeConsole and redirect stdout."""
        app._kernel32 = MagicMock()
        app._kernel32.FreeConsole.return_value = 1

        with patch("builtins.open", MagicMock()) as mock_open:
            mock_file = MagicMock()
            mock_open.return_value = mock_file
            result = app._win32_console_handler(2)  # CTRL_CLOSE_EVENT

        assert result is True
        app._kernel32.FreeConsole.assert_called_once()

    def test_ctrl_logoff_event_invokes_fast_cleanup(self, app, monkeypatch):
        """CTRL_LOGOFF_EVENT (5) must route to ``_do_fast_cleanup``"""
        fast_cleanup = MagicMock()
        monkeypatch.setattr(app.shutdown, "_do_fast_cleanup", fast_cleanup)
        monkeypatch.setattr("voice_typer.server.shutdown_controller.os._exit", lambda code=0: None)

        result = app._win32_console_handler(5)  # CTRL_LOGOFF_EVENT

        assert result is True
        fast_cleanup.assert_called_once_with()

    def test_ctrl_shutdown_event_invokes_fast_cleanup(self, app, monkeypatch):
        """CTRL_SHUTDOWN_EVENT (6) must route to ``_do_fast_cleanup``"""
        fast_cleanup = MagicMock()
        monkeypatch.setattr(app.shutdown, "_do_fast_cleanup", fast_cleanup)
        monkeypatch.setattr("voice_typer.server.shutdown_controller.os._exit", lambda code=0: None)

        result = app._win32_console_handler(6)  # CTRL_SHUTDOWN_EVENT

        assert result is True
        fast_cleanup.assert_called_once_with()

    def test_ctrl_c_event_starts_quit_thread(self, app):
        """CTRL_C_EVENT should start a quit thread (signal_handlers' own"""
        with patch("voice_typer.server.signal_handlers.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(0)  # CTRL_C_EVENT

        assert result is True
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    def test_unknown_event_returns_false(self, app):
        """Unknown event types should return False."""
        result = app._win32_console_handler(99)
        assert result is False


class TestMicrophoneSelection:
    def test_select_mic_by_id_updates_config(self, app):
        app._select_microphone("3")
        assert app.config.microphone == "3"

    def test_select_none_resets_to_default(self, app):
        app.config.microphone = "5"
        app._select_microphone(None)
        assert app.config.microphone is None

    def test_select_mic_saves_config(self, app, tmp_config_dir):
        app._select_microphone("2")
        config_file = tmp_config_dir / "config.json"
        data = json.loads(config_file.read_text())
        assert data["microphone"] == "2"

    def test_select_mic_recreates_recorder(self, app):
        old_recorder = app.recorder
        app._select_microphone("1")
        assert app.recorder is not old_recorder
        assert app.config.microphone == "1"


class TestRefreshMicrophonesFailurePath:
    """``app.refresh_microphones()`` (TrayController protocol)"""

    def test_refresh_microphones_logs_warning_when_load_fails(self, app, monkeypatch, caplog):
        """When ``load_microphones`` raises (device enumeration error),"""
        import logging

        import voice_typer.server.startup_tasks as startup_tasks

        def _boom(_app):
            raise OSError("device enumeration failed")

        monkeypatch.setattr(startup_tasks, "load_microphones", _boom)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            # Must not raise, the failure path is caught and logged.
            app.refresh_microphones()

        warning_records = [r for r in caplog.records if "[TRAY] refresh_microphones failed" in r.message]
        assert warning_records, (
            "refresh_microphones must log '[TRAY] refresh_microphones failed' when load_microphones raises"
        )
        assert warning_records[0].exc_info is not None, (
            "the failure warning must include exc_info (log.warning(..., exc_info=True))"
        )

    def test_refresh_microphones_success_path_delegates(self, app, monkeypatch):
        """argument (TrayController protocol contract)."""
        import voice_typer.server.startup_tasks as startup_tasks

        calls = []
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda self_: calls.append(self_))

        app.refresh_microphones()

        assert calls == [app], "refresh_microphones must call startup_tasks.load_microphones(self)"
