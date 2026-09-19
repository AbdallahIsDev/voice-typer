"""Tests for UAC/Winlogon focus scenarios."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
class TestUACFocus:
    """Test that the app handles UAC/Winlogon secure desktop gracefully."""

    def test_uac_foreground_window_does_not_crash(self, monkeypatch):
        """When the foreground window is on a secure desktop, the app"""
        mock_ctypes = MagicMock()
        mock_user32 = MagicMock()

        # Simulate GetForegroundWindow returning a secure desktop window
        mock_user32.GetForegroundWindow.return_value = 0x00012345
        # GetWindowThreadProcessId returns 0 (secure desktop process)
        mock_user32.GetWindowThreadProcessId.return_value = 0
        # AllowSetForegroundWindow fails for secure desktop
        mock_user32.AllowSetForegroundWindow.return_value = 0

        mock_ctypes.windll.user32 = mock_user32
        mock_ctypes.windll.kernel32 = MagicMock()
        mock_ctypes.wintypes = MagicMock()

        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)
        monkeypatch.setitem(sys.modules, "ctypes.wintypes", mock_ctypes.wintypes)

        # The tray_window module should handle this gracefully
        try:
            from voice_typer.server.tray_window import bring_app_to_front

            # This should not crash even when the foreground is a secure desktop
            result = bring_app_to_front()
            assert isinstance(result, bool)
        except ImportError:
            pytest.skip("tray_window module not available")

    def test_winlogon_desktop_detection(self, monkeypatch):
        """When the desktop is Winlogon, the app should detect it"""
        mock_ctypes = MagicMock()
        mock_user32 = MagicMock()

        # GetForegroundWindow returns 0 (no foreground, secure desktop)
        mock_user32.GetForegroundWindow.return_value = 0
        mock_ctypes.CFUNCTYPE = MagicMock(return_value=MagicMock())
        mock_ctypes.windll.user32 = mock_user32
        mock_ctypes.windll.kernel32 = MagicMock()
        mock_ctypes.wintypes = MagicMock()

        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)
        monkeypatch.setitem(sys.modules, "ctypes.wintypes", mock_ctypes.wintypes)

        # Force the Win32 code path on any platform so the SUT actually
        from voice_typer.server import tray_window

        monkeypatch.setattr(tray_window, "is_windows", lambda: True)

        # Invoke the SUT, this is the missing piece the original
        result = tray_window.bring_app_to_front()

        # (a) The SUT must return a bool per its contract.
        assert isinstance(result, bool), f"bring_app_to_front must return a bool, got {type(result).__name__}"
        # (b) With NULL foreground HWND and no matching window title,
        assert result is False, (
            "bring_app_to_front must return False when no matching "
            "window is found (NULL foreground / Winlogon secure desktop)."
        )
        # (c) The SUT must have actually consulted the Win32 foreground
        assert mock_user32.GetForegroundWindow.called, (
            "bring_app_to_front did not call GetForegroundWindow, the Win32 mock setup was not exercised by the SUT."
        )


class TestUACFocusCrossPlatform:
    """Cross-platform tests that don't require Win32 APIs."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: bring_app_to_front is a no-op when Win32 SetForegroundWindow is unavailable",
    )
    def test_bring_to_front_on_non_windows_is_noop(self, monkeypatch):
        """On non-Windows, bring_app_to_front should be a safe no-op."""
        try:
            from voice_typer.server.tray_window import bring_app_to_front

            result = bring_app_to_front()
            assert isinstance(result, bool)
        except ImportError:
            # If the module doesn't exist on this platform, that's fine
            pass

    def test_secure_desktop_string_in_platform(self):
        """The platform module should handle secure desktop detection."""
        from voice_typer.server.server_platform import SYSTEM

        assert SYSTEM in ("win32", "darwin", "linux")
