"""Unit tests for the CapsLock suppression helpers."""

from __future__ import annotations

import ctypes
import logging
from unittest.mock import MagicMock

from voice_typer.server.hotkeys.win32_vk import _KEYEVENTF_KEYUP, _VK_CAPITAL
from voice_typer.server.hotkeys.windows._win32_keyboard import (
    INPUT,
    KEYBDINPUT,
    _build_keyboard_input,
    _send_keyboard_event,
)
from voice_typer.server.hotkeys.windows.caps_lock_suppressor import (
    ensure_caps_lock_off,
    suppress_caps_lock_toggle,
)


class _FakeHost:
    """Duck-typed stand-in for the ``self`` the helpers expect."""

    def __init__(self, user32=None, kernel32=None):
        self._user32 = user32
        self._kernel32 = kernel32
        self._caps_lock_suppressing = False


def _make_user32(toggle_state: int = 0) -> MagicMock:
    """Build a MagicMock user32 with a configured GetKeyState + SendInput."""
    user32 = MagicMock(name="user32")
    user32.GetKeyState.return_value = toggle_state
    # SendInput returns UINT, 1 means the single keyboard event was
    user32.SendInput.return_value = 1
    return user32


def _capture_sendinput_events(user32: MagicMock) -> list[INPUT]:
    """wVk/wScan/dwFlags fields directly."""
    captured: list[INPUT] = []

    def _capture(n_inputs: int, events_ptr: object, _cb_size: int) -> int:
        # ``events_ptr`` is a ``ctypes.byref(...)`` CArgObject pointing
        typed = ctypes.cast(events_ptr, ctypes.POINTER(INPUT))
        for i in range(n_inputs):
            captured.append(typed[i])
        return 1  # success, mirrors the production happy path

    user32.SendInput.side_effect = _capture
    return captured


class TestSuppressCapsLockToggle:
    """``suppress_caps_lock_toggle``, reactive undo of the OS toggle."""

    def test_noop_when_user32_or_kernel32_missing(self):
        """With ``_user32`` or ``_kernel32`` falsy, the helper must"""
        host = _FakeHost(user32=None, kernel32=None)
        suppress_caps_lock_toggle(host)  # must not raise
        assert host._caps_lock_suppressing is False

        host2 = _FakeHost(user32=MagicMock(), kernel32=None)
        suppress_caps_lock_toggle(host2)
        host2._user32.GetKeyState.assert_not_called()

    def test_undoes_toggle_when_caps_lock_turned_on(self):
        """helper must send a synthetic keydown+keyup to toggle it back"""
        user32 = _make_user32(toggle_state=1)
        captured = _capture_sendinput_events(user32)
        kernel32 = MagicMock(name="kernel32")
        host = _FakeHost(user32=user32, kernel32=kernel32)

        suppress_caps_lock_toggle(host)

        user32.GetKeyState.assert_called_once_with(_VK_CAPITAL)
        # Two SendInput calls: keydown + keyup synthetic Caps Lock press.
        assert user32.SendInput.call_count == 2
        assert len(captured) == 2, f"expected exactly two captured INPUT structs (keydown + keyup); got {len(captured)}"
        keydown, keyup = captured
        assert keydown.type == INPUT.KEYBOARD
        assert keydown.ki.ki.wVk == _VK_CAPITAL
        assert keydown.ki.ki.wScan == 0x45
        assert keydown.ki.ki.dwFlags == 0
        assert keyup.type == INPUT.KEYBOARD
        assert keyup.ki.ki.wVk == _VK_CAPITAL
        assert keyup.ki.ki.wScan == 0x45
        assert keyup.ki.ki.dwFlags == _KEYEVENTF_KEYUP
        # Brief sleep lets the OS process the synthetic events.
        kernel32.Sleep.assert_called_once_with(5)
        assert host._caps_lock_suppressing is False, "the suppressing flag must be reset after the synthetic press"

    def test_no_synthetic_press_when_toggle_off(self):
        """When ``GetKeyState`` reports the toggle OFF, no synthetic"""
        user32 = _make_user32(toggle_state=0)
        kernel32 = MagicMock(name="kernel32")
        host = _FakeHost(user32=user32, kernel32=kernel32)

        suppress_caps_lock_toggle(host)

        user32.SendInput.assert_not_called()
        kernel32.Sleep.assert_called_once_with(5)
        assert host._caps_lock_suppressing is False

    def test_exception_logs_and_resets_flag(self, caplog):
        """If ``GetKeyState`` itself raises, the helper must log via"""
        user32 = MagicMock(name="user32")
        user32.GetKeyState.side_effect = OSError("user32 gone")
        kernel32 = MagicMock(name="kernel32")
        host = _FakeHost(user32=user32, kernel32=kernel32)
        host._caps_lock_suppressing = True  # simulate an in-flight press

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.hotkeys"):
            suppress_caps_lock_toggle(host)

        assert host._caps_lock_suppressing is False, "the suppressing flag must be reset even when the call fails"
        error_records = [r for r in caplog.records if "Failed to suppress Caps Lock toggle" in r.message]
        assert error_records, "suppress_caps_lock_toggle must log 'Failed to suppress Caps Lock toggle' on failure"
        assert error_records[0].exc_info is not None


class TestEnsureCapsLockOff:
    """``ensure_caps_lock_off``, proactive defense-in-depth toggle-off."""

    def test_noop_without_user32(self):
        """With ``_user32`` falsy the helper must return immediately."""
        host = _FakeHost(user32=None, kernel32=None)
        ensure_caps_lock_off(host)  # must not raise

    def test_forces_off_when_caps_lock_is_on(self, caplog):
        """When the toggle state is ON, the helper must send the"""
        user32 = _make_user32(toggle_state=1)
        captured = _capture_sendinput_events(user32)
        host = _FakeHost(user32=user32, kernel32=MagicMock())

        with caplog.at_level(logging.INFO, logger="voice_typer.server.hotkeys"):
            ensure_caps_lock_off(host)

        user32.GetKeyState.assert_called_once_with(_VK_CAPITAL)
        assert user32.SendInput.call_count == 2
        assert len(captured) == 2
        keydown, keyup = captured
        assert keydown.ki.ki.wVk == _VK_CAPITAL
        assert keydown.ki.ki.wScan == 0x45
        assert keydown.ki.ki.dwFlags == 0
        assert keyup.ki.ki.wVk == _VK_CAPITAL
        assert keyup.ki.ki.wScan == 0x45
        assert keyup.ki.ki.dwFlags == _KEYEVENTF_KEYUP
        assert any("Proactive caps lock toggle-off" in r.message for r in caplog.records)

    def test_no_press_when_caps_lock_is_off(self):
        """When the toggle state is OFF, no synthetic keypress is sent."""
        user32 = _make_user32(toggle_state=0)
        host = _FakeHost(user32=user32, kernel32=MagicMock())

        ensure_caps_lock_off(host)

        user32.SendInput.assert_not_called()

    def test_exception_logs_without_raising(self, caplog):
        """A failure inside the helper must be caught and logged via"""
        user32 = MagicMock(name="user32")
        user32.GetKeyState.side_effect = OSError("user32 gone")
        host = _FakeHost(user32=user32, kernel32=MagicMock())

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.hotkeys"):
            ensure_caps_lock_off(host)  # must not raise

        assert any("Failed to force caps lock off" in r.message for r in caplog.records)


class TestSendKeyboardEventHelper:
    """vk/scan/flags mapping so a future refactor cannot silently change"""

    def test_build_keyboard_input_carries_vk_scan_flags(self):
        """``_build_keyboard_input`` must populate wVk/wScan/dwFlags"""
        inp = _build_keyboard_input(_VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP)
        assert inp.type == INPUT.KEYBOARD
        assert inp.ki.ki.wVk == _VK_CAPITAL
        assert inp.ki.ki.wScan == 0x45
        assert inp.ki.ki.dwFlags == _KEYEVENTF_KEYUP
        assert inp.ki.ki.time == 0
        assert inp.ki.ki.dwExtraInfo == 0

    def test_send_keyboard_event_invokes_sendinput_with_one_event(self):
        """``_send_keyboard_event`` must call ``user32.SendInput`` with"""
        user32 = MagicMock(name="user32")
        user32.SendInput.return_value = 1

        result = _send_keyboard_event(user32, _VK_CAPITAL, 0x45, 0)

        assert result == 1
        user32.SendInput.assert_called_once()
        args, _ = user32.SendInput.call_args
        assert args[0] == 1  # one input event
        assert args[2] == ctypes.sizeof(INPUT)  # cbSize

    def test_send_keyboard_event_returns_zero_on_failure(self):
        """
        When ``SendInput`` returns 0 (no events inserted), the helper
        ``SendInput`` return-value contract (UINT count of events
        """
        user32 = MagicMock(name="user32")
        user32.SendInput.return_value = 0

        result = _send_keyboard_event(user32, _VK_CAPITAL, 0x45, 0)

        assert result == 0
        assert user32.SendInput.call_count == 1

    def test_keyup_constant_matches_keybdinput_legacy_value(self):
        """``KEYBDINPUT.KEYUP`` must equal ``_KEYEVENTF_KEYUP`` (0x0002) —"""
        assert KEYBDINPUT.KEYUP == _KEYEVENTF_KEYUP == 0x0002
