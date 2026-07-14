"""Tests for clipboard copy and paste logic."""

import sys
from unittest.mock import MagicMock

import pytest

mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server.clipboard import ClipboardManager  # noqa: E402


class TestCopy:
    def test_copy_puts_text_on_clipboard(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.pyperclip", MagicMock())
        import voice_typer.server.clipboard as mod

        mod.pyperclip = MagicMock()
        # PLAT-PASTEVR: copy() verifies clipboard content via pyperclip.paste().
        # Make paste() return the same text so verification passes on first try.
        mod.pyperclip.paste.return_value = "hello world"

        cm = ClipboardManager(paste_enabled=False)
        result = cm.copy("hello world")

        # copy() returns a ClipboardSnapshot (or None if snapshot capture
        # was skipped/empty) on success — never the boolean True/False.
        assert result is None or isinstance(result, mod.ClipboardSnapshot)
        mod.pyperclip.copy.assert_called_with("hello world")
        # PLAT-PASTEVR: with working verification, copy is called exactly once
        assert mod.pyperclip.copy.call_count == 1

    def test_copy_returns_false_for_empty_text(self):
        cm = ClipboardManager(paste_enabled=False)
        assert cm.copy("") is None
        assert cm.copy(None) is None

    def test_copy_returns_false_on_exception(self, monkeypatch):
        import voice_typer.server.clipboard as mod

        mod.pyperclip = MagicMock()
        mod.pyperclip.copy.side_effect = Exception("clipboard locked")

        cm = ClipboardManager(paste_enabled=False)
        # copy() does NOT return False on failure — it raises
        # ClipboardCopyError so the caller can write crash recovery.
        with pytest.raises(mod.ClipboardCopyError):
            cm.copy("test")


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
        # Platform is now centralized in platform_utils; clipboard.py uses
        # is_windows()/is_macos() imported into its namespace. Force the
        # non-Windows, non-macOS keystroke path (Ctrl+V via pynput).
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.clipboard.is_macos", lambda: False)

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
        # Platform is now centralized in platform_utils; force the
        # non-Windows, non-macOS keystroke path (Ctrl+V via pynput).
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        monkeypatch.setattr("voice_typer.server.clipboard.is_macos", lambda: False)

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
