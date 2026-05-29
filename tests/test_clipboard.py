"""Tests for clipboard copy and safe-paste logic."""

import sys
import pytest
from unittest.mock import patch, MagicMock

# Mock pynput BEFORE any import of voice_typer.clipboard
# pynput requires a display on Linux which isn't available in CI/headless
mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.clipboard import ClipboardManager


class TestCopy:
    def test_copy_puts_text_on_clipboard(self, monkeypatch):
        monkeypatch.setattr("voice_typer.clipboard.pyperclip", MagicMock())
        import voice_typer.clipboard as mod
        mod.pyperclip = MagicMock()

        cm = ClipboardManager(paste_enabled=False)
        result = cm.copy("hello world")

        assert result is True
        mod.pyperclip.copy.assert_called_once_with("hello world")

    def test_copy_returns_false_for_empty_text(self):
        cm = ClipboardManager(paste_enabled=False)
        assert cm.copy("") is False
        # pyrefly: ignore [bad-argument-type]
        assert cm.copy(None) is False

    def test_copy_returns_false_on_exception(self, monkeypatch):
        import voice_typer.clipboard as mod
        mod.pyperclip = MagicMock()
        mod.pyperclip.copy.side_effect = Exception("clipboard locked")

        cm = ClipboardManager(paste_enabled=False)
        assert cm.copy("test") is False


class TestPaste:
    @patch("voice_typer.clipboard.is_text_input_focused", return_value=True)
    def test_paste_sends_keystroke_when_focused(self, mock_focus, monkeypatch):
        import voice_typer.clipboard as mod
        mod.time = MagicMock()

        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = MagicMock()

        result = cm.paste()

        assert result is True
        cm._keyboard.press.assert_called()
        cm._keyboard.release.assert_called()

    @patch("voice_typer.clipboard.is_text_input_focused", return_value=False)
    def test_paste_skips_when_no_focus_windows(self, mock_focus, monkeypatch):
        """On Windows, paste is skipped after retry when focus stays False."""
        import voice_typer.clipboard as mod
        mod.time = MagicMock()
        monkeypatch.setattr("voice_typer.clipboard.sys.platform", "win32")
        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = MagicMock()

        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()
        assert mock_focus.call_count == 2  # initial + retry

    def test_paste_skips_when_disabled(self):
        cm = ClipboardManager(paste_enabled=False)
        cm._keyboard = MagicMock()

        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()

    @patch("voice_typer.clipboard.is_text_input_focused", return_value=None)
    def test_paste_skipped_when_focus_unknown_by_default(self, mock_focus, monkeypatch):
        """When focus is unknown (None) and unsafe_paste_on_unknown_focus is False, skip."""
        import voice_typer.clipboard as mod
        mod.time = MagicMock()

        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = MagicMock()

        result = cm.paste()

        # Default: unsafe_paste_on_unknown_focus=False -> skip paste
        assert result is False

    @patch("voice_typer.clipboard.is_text_input_focused", return_value=None)
    def test_paste_attempts_when_focus_unknown_and_opted_in(self, mock_focus, monkeypatch):
        """When focus is unknown (None) and unsafe_paste_on_unknown_focus is True, attempt."""
        import voice_typer.clipboard as mod
        mod.time = MagicMock()

        cm = ClipboardManager(paste_enabled=True, unsafe_paste_on_unknown_focus=True)
        cm._keyboard = MagicMock()

        result = cm.paste()

        assert result is True

    @patch("voice_typer.clipboard.is_text_input_focused")
    def test_paste_retries_on_windows_when_focus_disrupted(self, mock_focus, monkeypatch):
        """On Windows, if focus returns False then True on retry, paste succeeds."""
        import voice_typer.clipboard as mod
        mod.time = MagicMock()
        monkeypatch.setattr("voice_typer.clipboard.sys.platform", "win32")
        mock_focus.side_effect = [False, True]

        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = MagicMock()

        result = cm.paste()

        assert result is True
        cm._keyboard.press.assert_called()
        assert mock_focus.call_count == 2

    @patch("voice_typer.clipboard.is_text_input_focused")
    def test_paste_no_retry_on_non_windows(self, mock_focus, monkeypatch):
        """On non-Windows, there is no retry — paste is skipped immediately."""
        import voice_typer.clipboard as mod
        mod.time = MagicMock()
        monkeypatch.setattr("voice_typer.clipboard.sys.platform", "linux")
        mock_focus.side_effect = [False, True]

        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = MagicMock()

        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()
        assert mock_focus.call_count == 1

    @patch("voice_typer.clipboard.is_text_input_focused", return_value=True)
    def test_paste_returns_false_on_keyboard_error(self, mock_focus, monkeypatch):
        import voice_typer.clipboard as mod
        mod.time = MagicMock()

        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = MagicMock()
        cm._keyboard.press.side_effect = Exception("keyboard error")

        result = cm.paste()

        assert result is False

    def test_is_terminal_process(self):
        """Test terminal process detection."""
        assert ClipboardManager._is_terminal_process("windowsterminal.exe") is True
        assert ClipboardManager._is_terminal_process("cmd.exe") is True
        assert ClipboardManager._is_terminal_process("notepad.exe") is False
        assert ClipboardManager._is_terminal_process(None) is False
        assert ClipboardManager._is_terminal_process("") is False
