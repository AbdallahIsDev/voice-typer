"""Tests for clipboard copy and paste logic."""

import sys
import pytest
from unittest.mock import patch, MagicMock

mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server.clipboard import ClipboardManager


class TestCopy:
    def test_copy_puts_text_on_clipboard(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.pyperclip", MagicMock())
        import voice_typer.server.clipboard as mod
        mod.pyperclip = MagicMock()

        cm = ClipboardManager(paste_enabled=False)
        result = cm.copy("hello world")

        assert result is True
        mod.pyperclip.copy.assert_called_once_with("hello world")

    def test_copy_returns_false_for_empty_text(self):
        cm = ClipboardManager(paste_enabled=False)
        assert cm.copy("") is False
        assert cm.copy(None) is False

    def test_copy_returns_false_on_exception(self, monkeypatch):
        import voice_typer.server.clipboard as mod
        mod.pyperclip = MagicMock()
        mod.pyperclip.copy.side_effect = Exception("clipboard locked")

        cm = ClipboardManager(paste_enabled=False)
        assert cm.copy("test") is False


class TestPaste:
    def _make_cm(self, **kwargs) -> ClipboardManager:
        """Helper to create ClipboardManager with rate-limit bypassed."""
        cm = ClipboardManager(**kwargs)
        cm._last_paste_time = -999.0  # well before any rate-limit window
        cm._keyboard = MagicMock()
        return cm

    def test_paste_sends_keystroke(self, monkeypatch):
        import voice_typer.server.clipboard as mod
        mod.time = MagicMock()
        mod.time.monotonic.return_value = 100.0
        monkeypatch.setattr("voice_typer.server.clipboard.sys.platform", "linux")

        cm = self._make_cm(paste_enabled=True)
        result = cm.paste()

        assert result is True
        cm._keyboard.press.assert_called()
        cm._keyboard.release.assert_called()

    def test_paste_skips_when_disabled(self):
        cm = self._make_cm(paste_enabled=False)
        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()

    def test_paste_skips_when_rate_limited(self):
        cm = ClipboardManager(paste_enabled=True)
        cm._last_paste_time = 100.0
        cm._keyboard = MagicMock()

        import voice_typer.server.clipboard as mod
        mod.time.monotonic = MagicMock(return_value=100.3)

        result = cm.paste()

        assert result is False
        cm._keyboard.press.assert_not_called()

    def test_paste_returns_false_on_keyboard_error(self, monkeypatch):
        import voice_typer.server.clipboard as mod
        mod.time = MagicMock()
        mod.time.monotonic.return_value = 100.0
        monkeypatch.setattr("voice_typer.server.clipboard.sys.platform", "linux")

        cm = self._make_cm(paste_enabled=True)
        cm._keyboard = MagicMock()
        cm._keyboard.press.side_effect = Exception("keyboard error")

        result = cm.paste()

        assert result is False

    def test_is_terminal_process(self):
        assert ClipboardManager._is_terminal_process("windowsterminal.exe") is True
        assert ClipboardManager._is_terminal_process("cmd.exe") is True
        assert ClipboardManager._is_terminal_process("notepad.exe") is False
        assert ClipboardManager._is_terminal_process(None) is False
        assert ClipboardManager._is_terminal_process("") is False
