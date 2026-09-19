"""
S2- regression test: ``_send_ctrl_v_win32`` returns bool, ``paste()`` returns True on success.
NOT complete cleanly; do not double-paste)
"""

from __future__ import annotations

import ctypes
import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import ClipboardManager  # noqa: E402


@pytest.fixture
def fake_win32_for_return_value():
    """Mock ``ctypes.windll`` so the Win32 code path runs on Linux."""
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.advapi32 = MagicMock()
    # Sane defaults for the few Win32 calls paste() may make.
    mock_user32.GetForegroundWindow.return_value = 0x12345
    mock_kernel32.GetLastError.return_value = 0
    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        # Pin is_macos() False too: manager._dispatch_keystroke checks
        patch.object(clip_mod, "is_macos", return_value=False),
        patch("ctypes.windll", mock_windll, create=True),
    ):
        yield {"user32": mock_user32, "windll": mock_windll}


class TestSendCtrlVWin32ReturnValue:
    """``_send_ctrl_v_win32`` returns an explicit bool."""

    def _make_cm(self):
        """Delegate to the shared canonical factory (XS-42 helper dedup)."""
        from tests.fixtures.clipboard_helpers import make_clipboard_manager

        return make_clipboard_manager()

    def test_returns_true_on_full_success(self, fake_win32_for_return_value):
        """returning 4 → _send_ctrl_v_win32 returns True."""
        cm = self._make_cm()
        fake_win32_for_return_value["user32"].SendInput.return_value = 4
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            result = cm._send_ctrl_v_win32()
        assert result is True, (
            "S2- regression: _send_ctrl_v_win32() must return True when "
            f"SendInput returns 4 (full success); got {result!r}."
        )

    def test_returns_false_on_partial_success(self, fake_win32_for_return_value):
        """returning 1..3 → _send_ctrl_v_win32 returns False."""
        cm = self._make_cm()
        # First SendInput (4-event Ctrl+V batch) returns 2 (partial).
        fake_win32_for_return_value["user32"].SendInput.side_effect = [2, 2]
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            result = cm._send_ctrl_v_win32()
        assert result is False, (
            "S2- regression: _send_ctrl_v_win32() must return False on "
            f"partial success (SendInput returned 1..3); got {result!r}."
        )

    def test_returns_true_on_zero_with_fallback(self, fake_win32_for_return_value):
        """returning 0 → fallback invoked, return False."""
        cm = self._make_cm()
        fake_win32_for_return_value["user32"].SendInput.return_value = 0
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            result = cm._send_ctrl_v_win32()
        assert result is False, (
            "S2- regression: _send_ctrl_v_win32() must return False when "
            "SendInput returns 0 (delivery unverified even with fallback); got "
            f"{result!r}."
        )


class TestPasteReturnsTrueOnFullSuccess:
    """S2-``paste()`` returns True (not False) on full success."""

    def _make_cm(self):
        """Delegate to the shared canonical factory (XS-42 helper dedup),"""
        from tests.fixtures.clipboard_helpers import make_clipboard_manager

        return make_clipboard_manager(save_restore=False, last_copied_text="test")

    def test_paste_returns_true_on_sendinput_full_success(self, fake_win32_for_return_value):
        """() returns True when SendInput returns 4."""
        cm = self._make_cm()
        fake_win32_for_return_value["user32"].SendInput.return_value = 4
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_Key") as mock_key,
            patch.object(clip_mod, "log") as mock_log,
            patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
            patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            mock_key.ctrl = "ctrl_key"
            result = cm.paste()
        assert result is True, (
            f"S2- regression: paste() must return True when SendInput returns 4 (full success); got {result!r}."
        )
        warning_msgs = [str(c) for c in mock_log.warning.call_args_list]
        spurious = [m for m in warning_msgs if "Auto-paste failed" in m and "partial success" in m]
        assert not spurious, (
            "S2- regression: paste() logged spurious 'Auto-paste failed' "
            "warning on full success (should only fire on partial success): "
            f"{spurious}"
        )

    def test_paste_returns_false_on_partial_success(self, fake_win32_for_return_value):
        """() returns False on partial success (1..3)."""
        cm = self._make_cm()
        # First SendInput (4-event batch) returns 2 (partial); second
        fake_win32_for_return_value["user32"].SendInput.side_effect = [2, 2]
        with (
            patch.object(clip_mod, "time") as mock_time,
            patch.object(clip_mod, "_Key") as mock_key,
            patch.object(clip_mod, "log") as mock_log,
            patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
            patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        ):
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            mock_key.ctrl = "ctrl_key"
            result = cm.paste()
        assert result is False, (
            f"S2- regression: paste() must return False on partial success (SendInput returned 1..3); got {result!r}."
        )
        # The "Auto-paste failed (SendInput partial success)" warning
        warning_msgs = [str(c) for c in mock_log.warning.call_args_list]
        expected_warning = [m for m in warning_msgs if "Auto-paste failed" in m and "partial success" in m]
        assert expected_warning, (
            "S2-paste() should log 'Auto-paste failed (SendInput "
            "partial success)' warning on partial success; got warnings: "
            f"{warning_msgs}"
        )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="S2- sentinel: requires real ctypes.windll.user32 (Windows-only).",
)
def test_paste_returns_true_on_sendinput_full_success_win32():
    """S2- Windows-only sentinel: paste() returns True on full success."""
    cm = ClipboardManager.__new__(ClipboardManager)
    cm.paste_enabled = True
    cm._keyboard = MagicMock()
    cm._last_paste_time = 0.0
    cm._clipboard_seq = 0
    cm._clipboard_save_restore_enabled = False
    cm._last_copied_text = "test"
    cm._restore_delay_ms = 150

    with (
        patch.object(clip_mod, "time") as mock_time,
        patch.object(clip_mod, "_Key") as mock_key,
        patch.object(clip_mod, "log") as mock_log,
        patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
        patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
        patch.object(ctypes.windll.user32, "SendInput", MagicMock(return_value=4)),
    ):
        mock_time.monotonic.return_value = 100.0
        mock_time.sleep = MagicMock()
        mock_key.ctrl = "ctrl_key"
        result = cm.paste()

    assert result is True
    warning_msgs = [str(c) for c in mock_log.warning.call_args_list]
    spurious = [m for m in warning_msgs if "Auto-paste failed" in m and "partial success" in m]
    assert not spurious, f"S2- regression on Windows: paste() logged spurious warning: {spurious}"
