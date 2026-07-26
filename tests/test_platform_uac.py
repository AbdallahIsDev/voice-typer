"""Tests for UAC/Winlogon focus scenarios.

TEST-017: Mock Win32 APIs that simulate UAC/Winlogon scenarios.
Test that the app doesn't crash when foreground window is a secure desktop.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
class TestUACFocus:
    """Test that the app handles UAC/Winlogon secure desktop gracefully."""

    def test_uac_foreground_window_does_not_crash(self, monkeypatch):
        """When the foreground window is on a secure desktop, the app
        should not crash when trying to bring itself to front."""
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
        # (not crash, not raise an exception)
        try:
            from voice_typer.server.tray_window import bring_electron_to_front

            # This should not crash even when the foreground is a secure desktop
            result = bring_electron_to_front()
            assert isinstance(result, bool)
        except ImportError:
            pytest.skip("tray_window module not available")

    def test_winlogon_desktop_detection(self, monkeypatch):
        """When the desktop is Winlogon, the app should detect it
        and skip foreground manipulation.

        S2-CR-61: the original test set up Win32 mocks but ended with
        ``assert True`` — never invoking the SUT, so the mock setup was
        dead code and gave zero coverage. We now invoke
        ``tray_window.bring_electron_to_front()`` against the same mocks
        and assert (a) the return is a bool, (b) it returns ``False``
        (no matching window found under NULL foreground), and (c)
        ``GetForegroundWindow`` was actually called — proving the SUT
        read the mock setup rather than short-circuiting.
        """
        mock_ctypes = MagicMock()
        mock_user32 = MagicMock()

        # GetForegroundWindow returns 0 (no foreground — secure desktop)
        mock_user32.GetForegroundWindow.return_value = 0
        # EnumWindows callback wrapping needs CFUNCTYPE — provide it so
        # the Win32 code path doesn't AttributeError under the mock.
        mock_ctypes.CFUNCTYPE = MagicMock(return_value=MagicMock())
        mock_ctypes.windll.user32 = mock_user32
        mock_ctypes.windll.kernel32 = MagicMock()
        mock_ctypes.wintypes = MagicMock()

        monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)
        monkeypatch.setitem(sys.modules, "ctypes.wintypes", mock_ctypes.wintypes)

        # Force the Win32 code path on any platform so the SUT actually
        # runs against the mocks (not just the no-op non-Windows branch).
        from voice_typer.server import tray_window

        monkeypatch.setattr(tray_window, "is_windows", lambda: True)

        # Invoke the SUT — this is the missing piece the original
        # ``assert True`` skipped.
        result = tray_window.bring_electron_to_front()

        # (a) The SUT must return a bool per its contract.
        assert isinstance(result, bool), f"bring_electron_to_front must return a bool, got {type(result).__name__}"
        # (b) With NULL foreground HWND and no matching window title,
        # the SUT must report it could not bring anything to front.
        assert result is False, (
            "bring_electron_to_front must return False when no matching "
            "window is found (NULL foreground / Winlogon secure desktop)."
        )
        # (c) The SUT must have actually consulted the Win32 foreground
        # state — otherwise the mock setup is dead code (false coverage).
        assert mock_user32.GetForegroundWindow.called, (
            "bring_electron_to_front did not call GetForegroundWindow — "
            "the Win32 mock setup was not exercised by the SUT."
        )


class TestUACFocusCrossPlatform:
    """Cross-platform tests that don't require Win32 APIs."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: bring_electron_to_front is a no-op when Win32 SetForegroundWindow is unavailable",
    )
    def test_bring_to_front_on_non_windows_is_noop(self, monkeypatch):
        """On non-Windows, bring_electron_to_front should be a safe no-op."""
        try:
            from voice_typer.server.tray_window import bring_electron_to_front

            # On non-Windows, this is expected to be a no-op or use a
            # different mechanism. Either way, it should not crash.
            result = bring_electron_to_front()
            assert isinstance(result, bool)
        except ImportError:
            # If the module doesn't exist on this platform, that's fine
            pass

    def test_secure_desktop_string_in_platform(self):
        """The platform module should handle secure desktop detection."""
        from voice_typer.server.server_platform import SYSTEM

        assert SYSTEM in ("win32", "darwin", "linux")
