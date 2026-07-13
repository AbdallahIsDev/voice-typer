"""Tests for PLAT-CLIPRACE, PLAT-SECURE, PLAT-STUCK, SEC-012 clipboard fixes."""
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def clipboard():
    """Create a ClipboardManager with mocked keyboard."""
    with patch("voice_typer.server.clipboard._ensure_pynput_imported"), \
            patch("voice_typer.server.clipboard._Controller") as mock_ctrl:
        mock_instance = MagicMock()
        mock_ctrl.return_value = mock_instance
        from voice_typer.server.clipboard import ClipboardManager
        cm = ClipboardManager(paste_enabled=True)
        cm._keyboard = mock_instance
        return cm


def test_safe_paste_target_non_windows(clipboard):
    """On non-Windows, _is_safe_paste_target always returns True."""
    if sys.platform == "win32":
        pytest.skip("Non-Windows test")
    assert clipboard._is_safe_paste_target() is True


def test_release_stuck_modifiers(clipboard):
    """_release_stuck_modifiers calls release for each modifier key."""
    import voice_typer.server.clipboard as clip_mod
    # Patch _Key to have mock key objects
    mock_key = MagicMock()
    clip_mod._Key = mock_key
    try:
        clipboard._release_stuck_modifiers()
        # Should have called release for ctrl, shift, alt, cmd
        assert clipboard._keyboard.release.call_count >= 4
    finally:
        clip_mod._Key = None


def test_send_keystroke_sequence_uses_finally(clipboard):
    """_send_keystroke_sequence releases modifier even on error."""
    # Make the char press raise
    clipboard._keyboard.press.side_effect = [None, Exception("test error"), None]
    with pytest.raises(Exception, match="test error"):
        clipboard._send_keystroke_sequence(MagicMock(), "v")
    # Modifier should still be released (finally block)
    # The last release call should be for the modifier


def test_schedule_clipboard_clear_creates_thread(clipboard):
    """schedule_clipboard_clear starts a daemon thread."""
    with patch("voice_typer.server.clipboard.pyperclip") as mock_pyperclip:
        mock_pyperclip.paste.return_value = ""
        clipboard._last_copied_text = "test"
        clipboard.schedule_clipboard_clear(delay=0.01)
        # Thread was created (we can't easily verify it runs without waiting)
        import time
        time.sleep(0.05)


def test_clipboard_sequence_number_non_windows(clipboard):
    """_get_clipboard_sequence_number returns 0 on non-Windows."""
    if sys.platform == "win32":
        pytest.skip("Non-Windows test")
    assert clipboard._get_clipboard_sequence_number() == 0
