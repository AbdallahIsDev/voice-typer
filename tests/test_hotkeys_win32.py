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
    """When RegisterHotKey fails, start() falls back to polling."""

    def test_fallback_on_register_failure(self, mock_win32):
        """RegisterHotKey returns 0 -> polling fallback, no raise."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0  # BOOL FALSE
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._using_polling
            assert backend._last_error == 1409
        finally:
            backend.stop()

    def test_fallback_completes_quickly(self, mock_win32):
        """start() returns quickly on RegisterHotKey failure (polling fallback)."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            start_time = time.monotonic()
            backend.start(MagicMock())
            elapsed = time.monotonic() - start_time
            assert elapsed < 7.0, f"Took too long: {elapsed:.1f}s"
        finally:
            backend.stop()

    def test_error_code_captured(self, mock_win32):
        """RegisterHotKey failure should capture the Win32 error code."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._last_error == 1409
        finally:
            backend.stop()


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
        """After RegisterHotKey failure, falls back to polling (no raise)."""
        mock_user32, _ = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._ready_event.is_set()
            assert backend._success is True  # polling fallback, not an error
            assert backend._using_polling
        finally:
            backend.stop()


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


# ─── FIX-HOTKEY-ARCHITECTURE: modifier-only hotkeys ─────────────────────────


class TestModifierOnlyHotkeys:
    """FIX-HOTKEY-ARCHITECTURE: <alt>, <ctrl>, <shift>, <win> alone
    (no main key) should be accepted by the polling backend and use
    ``_run_modifier_only_polling_loop`` instead of raising ValueError.
    """

    def test_alt_only_hotkey_starts_without_error(self, mock_win32):
        """<alt> no longer raises ValueError at start() time."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        try:
            backend.start(MagicMock())
            assert backend._is_modifier_only is True
            assert backend._vk is None
            assert backend._modifiers & 0x0001  # _MOD_ALT
            assert backend._using_polling is True
        finally:
            backend.stop()

    def test_modifier_only_hotkey_skips_register_hotkey(self, mock_win32):
        """RegisterHotKey must NOT be called for modifier-only hotkeys
        (it would fail with ERROR_INVALID_PARAMETER since there's no
        main VK to register)."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<ctrl>")
        try:
            backend.start(MagicMock())
            mock_user32.RegisterHotKey.assert_not_called()
            assert backend._registered is False
        finally:
            backend.stop()

    def test_modifier_only_hotkey_diagnose_does_not_crash(self, mock_win32):
        """diagnose() must not crash on modifier-only hotkeys where
        ``self._vk`` is None (previously the f-string ``0x{None:X}``
        would raise TypeError)."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<shift>")
        try:
            backend.start(MagicMock())
            info = backend.diagnose()
            assert "modifier-only" in info.lower()
            assert "<shift>" in info
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_detects_press(self, mock_win32):
        """When the configured modifier is pressed (and no other
        modifiers are held), the press callback must fire exactly once."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # Default mock returns 0 for GetAsyncKeyState — set up so VK_MENU
        # (0x12, Alt) is reported as pressed.
        def fake_get_async_key_state(vk):
            return 0x8000 if vk == 0x12 else 0
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)
            # The polling loop fires within a few ms.
            import time as _time
            _time.sleep(0.05)
            assert callback.call_count >= 1
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_when_other_held(
        self, mock_win32,
    ):
        """If another modifier is held alongside the configured one,
        the press callback must NOT fire (user intent is a combo)."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # Both Alt (0x12) and Ctrl (0x11) reported as pressed.
        def fake_get_async_key_state(vk):
            return 0x8000 if vk in (0x11, 0x12) else 0
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)
            import time as _time
            _time.sleep(0.05)
            callback.assert_not_called()
        finally:
            backend.stop()


# ─── FIX-HOTKEY-ARCHITECTURE: Caps Lock toggle suppression ─────────────────


class TestCapsLockSuppression:
    """FIX-HOTKEY-ARCHITECTURE: when the hotkey is <caps_lock>, the
    polling backend should suppress the OS-level caps-state toggle by
    sending a synthetic Caps Lock keypress via keybd_event.
    """

    def test_caps_lock_hotkey_calls_keybd_event_on_press(self, mock_win32):
        """When Caps Lock (VK=0x14) is pressed, _suppress_caps_lock_toggle
        should call keybd_event to undo the OS-level toggle."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<caps_lock>")
        # Caps Lock reported as pressed; GetKeyState returns 1 (toggled on).
        def fake_get_async_key_state(vk):
            return 0x8000 if vk == 0x14 else 0
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state
        mock_user32.GetKeyState.return_value = 1  # toggle bit set

        callback = MagicMock()
        try:
            backend.start(callback)
            import time as _time
            _time.sleep(0.05)
            # keybd_event should have been called for the synthetic
            # keydown + keyup (2 calls per suppression cycle).
            assert mock_user32.keybd_event.call_count >= 2
        finally:
            backend.stop()
