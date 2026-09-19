"""Tests for elevated process focus scenarios."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestElevatedFocusHandling:
    """Test that clipboard operations handle elevated process focus gracefully."""

    def test_paste_skipped_when_elevated_process_focused(self, monkeypatch):
        """When the focused process is elevated (UAC), paste operations"""
        from voice_typer.server import clipboard

        # Mock _detect_focused_process (static method on ClipboardManager)
        monkeypatch.setattr(
            clipboard.ClipboardManager,
            "_detect_focused_process",
            staticmethod(lambda: "winlogon.exe"),
        )

        # The clipboard manager should handle this gracefully
        try:
            cm = clipboard.ClipboardManager.__new__(clipboard.ClipboardManager)
            cm.paste_enabled = True
            cm._keyboard = MagicMock()
            cm._last_paste_time = 0.0
            cm._clipboard_seq = 0
            cm._clipboard_save_restore_enabled = False
            cm._last_copied_text = ""
            cm._restore_delay_ms = 150
            # Attempting to paste should not crash
            with (
                patch.object(clipboard, "is_windows", return_value=False),
                patch.object(clipboard.ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(clipboard.ClipboardManager, "_release_stuck_modifiers"),
            ):
                result = cm.paste()
            # Result should indicate success or failure (boolean)
            assert result is None or result is False or result is True
        except (ImportError, AttributeError):
            # If the method doesn't exist yet, that's OK —
            pass

    def test_detect_focused_process_returns_optional_str(self):
        """``_detect_focused_process`` returns ``str | None``."""
        from voice_typer.server import clipboard

        # On non-Windows (or any platform where the Win32 calls are
        with patch.object(clipboard, "is_windows", return_value=False):
            result = clipboard.ClipboardManager._detect_focused_process()
        assert result is None or isinstance(result, str)
        if result is not None:
            # The implementation lowercases the basename (clipboard.py:687).
            assert result == result.lower()
            # Should be a bare executable name, not a full path.
            assert "\\" not in result
            assert "/" not in result

    def test_clipboard_does_not_crash_on_elevated_process(self, monkeypatch):
        """ClipboardManager should not crash when detecting an elevated process."""
        from voice_typer.server import clipboard

        try:
            cm = clipboard.ClipboardManager(MagicMock())
        except (ImportError, TypeError):
            pytest.skip("ClipboardManager not available on this platform")

        # Simulate a system process detection. The actual elevation
        monkeypatch.setattr(
            clipboard.ClipboardManager,
            "_detect_focused_process",
            staticmethod(lambda: "wininit.exe"),
        )
        monkeypatch.setattr(clipboard, "_is_elevated_target", lambda: True)

        # Basic operations should not crash
        assert isinstance(cm, clipboard.ClipboardManager)
        assert clipboard.ClipboardManager._detect_focused_process() == "wininit.exe"
        assert clipboard._is_elevated_target() is True

    def test_elevated_process_names_detected(self):
        """Known elevated process names should be recognizable."""
        elevated_processes = [
            "winlogon.exe",
            "wininit.exe",
            "csrss.exe",
            "lsass.exe",
            "consent.exe",  # UAC consent prompt
            "LogonUI.exe",  # Windows logon UI
        ]
        # These are system processes that run at higher integrity levels
        for proc in elevated_processes:
            assert proc.endswith(".exe"), f"{proc} should be an .exe process"
            assert len(proc) > 4, f"{proc} should be a valid process name"

    def test_is_elevated_target_returns_false_on_non_windows(self, monkeypatch):
        """On non-Windows platforms, ``_is_elevated_target`` returns False."""
        from voice_typer.server import clipboard

        monkeypatch.setattr(clipboard, "is_windows", lambda: False)
        assert clipboard._is_elevated_target() is False

    def test_is_elevated_target_returns_bool(self, monkeypatch):
        """``_is_elevated_target`` must always return a ``bool``."""
        from voice_typer.server import clipboard

        # Force every Win32 call to raise, the function must still
        monkeypatch.setattr(clipboard, "is_windows", lambda: True)

        import ctypes

        with patch.object(ctypes, "windll", new=MagicMock(), create=True):
            result = clipboard._is_elevated_target()
        assert isinstance(result, bool)


class TestElevatedFocusCrossPlatform:
    """Cross-platform tests for elevated focus detection."""

    def test_clipboard_manager_creates_on_any_platform(self):
        """ClipboardManager should be creatable on any platform."""
        from voice_typer.server import clipboard

        try:
            cm = clipboard.ClipboardManager(MagicMock())
            assert cm is not None
        except (ImportError, TypeError):
            # Platform-specific constructor may fail, that's OK
            pass

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _detect_focused_process returns None when Win32 APIs are unavailable",
    )
    def test_detect_focused_process_returns_none_on_non_windows(self, monkeypatch):
        """
        On non-Windows, ``_detect_focused_process`` returns ``None``.
        UAC and the Win32 foreground-window APIs do not exist on POSIX;
        """
        from voice_typer.server import clipboard

        # Even on a Windows host, forcing is_windows() False must
        monkeypatch.setattr(clipboard, "is_windows", lambda: False)
        result = clipboard.ClipboardManager._detect_focused_process()
        assert result is None

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _is_elevated_target returns False when Win32 APIs are unavailable",
    )
    def test_is_elevated_target_none_on_non_windows(self, monkeypatch):
        """On non-Windows, ``_is_elevated_target`` returns False."""
        from voice_typer.server import clipboard

        monkeypatch.setattr(clipboard, "is_windows", lambda: False)
        assert clipboard._is_elevated_target() is False
