"""CR-25: split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

import json
from unittest.mock import MagicMock, patch


class TestTrayControllerProtocolCompliance:
    """Verify VoiceTyperApp implements all TrayController protocol methods."""

    # DEAD-008: toggle_autostart, set_notifications, set_silence_*,
    # set_max_recording_time_seconds, create_desktop_shortcut removed
    # from TrayController protocol — no caller existed.  The public
    # methods are now just the ones the tray menu actually invokes.
    # ARCH-DEAD-SETTINGS: show_settings / open_settings removed along
    # with voice_typer.server.settings; the Electron frontend owns the
    # settings UI now.
    REQUIRED_PUBLIC_METHODS = [
        "toggle_dictation",
        "quit",
    ]

    REQUIRED_CALLBACK_METHODS = [
        "_toggle_autostart",
        "_set_notifications",
        "_select_microphone",
        # RW-9 Phase 2: ``_change_model`` and ``_restart_hotkey`` removed —
        # the tray now calls ``change_model`` (a TrayController Protocol
        # method) which internally invokes ``self.models.change_model``
        # directly. Hotkey changes go through ``app.hotkeys.restart``
        # (see service.py), not a Protocol method on the controller.
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

    def test_ctrl_logoff_event_starts_quit_thread(self, app):
        """CTRL_LOGOFF_EVENT should start a quit thread."""
        with patch("voice_typer.server.app.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(5)  # CTRL_LOGOFF_EVENT

        assert result is True

    def test_ctrl_shutdown_event_starts_quit_thread(self, app):
        """CTRL_SHUTDOWN_EVENT should start a quit thread."""
        with patch("voice_typer.server.app.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(6)  # CTRL_SHUTDOWN_EVENT

        assert result is True

    def test_ctrl_c_event_starts_quit_thread(self, app):
        """CTRL_C_EVENT should start a quit thread."""
        with patch("voice_typer.server.app.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(0)  # CTRL_C_EVENT

        assert result is True

    def test_unknown_event_returns_false(self, app):
        """Unknown event types should return False."""
        result = app._win32_console_handler(99)
        assert result is False


# ── TEST-004: restart_app cleanup path ───────────────────────────────────


class TestMicrophoneSelection:
    def test_select_mic_by_id_updates_config(self, app):
        app._select_microphone("3")
        assert app.config.microphone == "3"

    def test_select_none_resets_to_default(self, app):
        app.config.microphone = "5"
        app._select_microphone(None)
        # pyrefly: ignore [unnecessary-comparison]
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


# ─── Integration: real startup path ────────────────────────────────────
