"""Tests for WindowsNativeHotkey readiness handshake.

These tests mock ctypes.windll.user32 and kernel32 to simulate specific
failure modes and success scenarios without requiring a Windows host.
"""

import sys
import threading
import time
import ctypes
import ctypes.wintypes

import pytest
from unittest.mock import MagicMock, patch


# ─── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_win32(monkeypatch):
    """Provide mocked user32 and kernel32 DLLs.

    Default behavior: all Win32 calls succeed.  The polling loop exits
    quickly so tests don't hang.
    """
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()

    # Default: success for all Win32 calls
    mock_user32.RegisterHotKey.return_value = 1  # BOOL TRUE
    mock_user32.UnregisterHotKey.return_value = 1
    mock_user32.PostThreadMessageW.return_value = 1
    mock_user32.GetAsyncKeyState.return_value = 0  # key not pressed

    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.Sleep = MagicMock()

    # Patch ctypes.windll (Linux has no windll attribute by default)
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)

    return mock_user32, mock_kernel32


# ─── RegisterHotKey failure ──────────────────────────────────────────────────


class TestRegisterHotKeyFailure:
    """When RegisterHotKey fails, start() must raise within timeout."""

    def test_raises_on_register_failure(self, mock_win32):
        """RegisterHotKey returns 0 -> start() raises RuntimeError."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0  # BOOL FALSE
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        with pytest.raises(RuntimeError, match="Failed to register hotkey"):
            backend.start(MagicMock())

    def test_raises_within_timeout(self, mock_win32):
        """start() should raise quickly on RegisterHotKey failure."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        start_time = time.monotonic()
        with pytest.raises(RuntimeError):
            backend.start(MagicMock())
        elapsed = time.monotonic() - start_time
        assert elapsed < 7.0, f"Took too long: {elapsed:.1f}s"

    def test_error_code_in_message(self, mock_win32):
        """The error message should include the Win32 error code."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        with pytest.raises(RuntimeError, match="1409"):
            backend.start(MagicMock())


# ─── Success scenario ────────────────────────────────────────────────────────


class TestSuccessScenario:
    """On success, is_alive() returns True and _ready_event is set."""

    def test_ready_event_set_on_success(self, mock_win32):
        """After successful start(), _ready_event should be set."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        assert backend._ready_event.is_set()
        assert backend._success is True
        backend.stop()

    def test_is_alive_returns_true(self, mock_win32):
        """After successful start(), is_alive() returns True while thread runs."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        assert backend.is_alive() is True
        backend.stop()

    def test_is_alive_false_after_stop(self, mock_win32):
        """After stop(), is_alive() returns False."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())
        backend.stop()

        assert backend.is_alive() is False

    def test_registered_flag_true(self, mock_win32):
        """On success, _success should be True (survives thread cleanup)."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        assert backend._success is True
        backend.stop()


# ─── diagnose() method ───────────────────────────────────────────────────────


class TestDiagnoseMethod:
    """Test diagnose() reports success/failure state correctly."""

    def test_diagnose_before_start(self):
        """Before start(), diagnose() should say 'no thread started'."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        info = backend.diagnose()
        assert "no thread" in info.lower()

    def test_diagnose_on_success(self, mock_win32):
        """After successful start(), diagnose() includes key info."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        info = backend.diagnose()
        assert "WindowsNativeHotkey" in info
        assert "<f2>" in info
        assert "0x71" in info  # VK code for F2
        backend.stop()

    def test_diagnose_on_register_failure(self, mock_win32):
        """After RegisterHotKey failure, _ready_event is set and _success is False."""
        mock_user32, _ = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        with pytest.raises(RuntimeError):
            backend.start(MagicMock())

        assert backend._ready_event.is_set()
        assert backend._success is False


# ─── Mocking verification ────────────────────────────────────────────────────


class TestMockVerification:
    """Verify that our mocking actually hits the right code paths."""

    def test_register_hotkey_called(self, mock_win32):
        """RegisterHotKey should be called during start()."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        mock_user32.RegisterHotKey.assert_called_once()
        backend.stop()

    def test_register_hotkey_uses_ctrl_modifier_for_ctrl_digit(self, mock_win32):
        """Ctrl+1 should register Ctrl as a modifier and 1 as the main key."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<ctrl>+1")
        backend.start(MagicMock())

        args = mock_user32.RegisterHotKey.call_args[0]
        assert args[2] == 0x4000 | 0x0002
        assert args[3] == ord("1")
        backend.stop()

    def test_stop_calls_cleanup(self, mock_win32):
        """stop() should call UnregisterHotKey."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())
        backend.stop()

        mock_user32.UnregisterHotKey.assert_called()
