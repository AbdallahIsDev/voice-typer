"""Unit tests for the CapsLock suppression helpers.

These helpers are mixed into ``WindowsNativeHotkey`` (the EC-29 split of
the original ``windows_native.py`` god-class). They are module-level
functions that receive the host instance as ``self`` and rely on two
attributes:

* ``self._user32`` — a ctypes ``windll.user32``-like object exposing
  ``GetKeyState(vk)`` (returns a short whose bit 0 is the toggle state)
  and ``keybd_event(vk, scan, flags, extra)``.
* ``self._kernel32`` — a ``windll.kernel32``-like object exposing
  ``Sleep(ms)``.
* ``self._caps_lock_suppressing`` — a bool flag set while the synthetic
  keypress is in flight.

All tests run headless against fake host objects (no real Win32 calls).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from voice_typer.server.hotkeys.win32_vk import _KEYEVENTF_KEYUP, _VK_CAPITAL
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
    user32 = MagicMock(name="user32")
    user32.GetKeyState.return_value = toggle_state
    return user32


class TestSuppressCapsLockToggle:
    """``suppress_caps_lock_toggle`` — reactive undo of the OS toggle."""

    def test_noop_when_user32_or_kernel32_missing(self):
        """With ``_user32`` or ``_kernel32`` falsy, the helper must
        return immediately (no attribute access, no exception)."""
        host = _FakeHost(user32=None, kernel32=None)
        suppress_caps_lock_toggle(host)  # must not raise
        assert host._caps_lock_suppressing is False

        host2 = _FakeHost(user32=MagicMock(), kernel32=None)
        suppress_caps_lock_toggle(host2)
        host2._user32.GetKeyState.assert_not_called()

    def test_undoes_toggle_when_caps_lock_turned_on(self):
        """When ``GetKeyState`` reports the toggle ON (bit 0 set), the
        helper must send a synthetic keydown+keyup to toggle it back
        OFF, sleep briefly for the OS to dispatch, and reset the
        suppressing flag."""
        user32 = _make_user32(toggle_state=1)
        kernel32 = MagicMock(name="kernel32")
        host = _FakeHost(user32=user32, kernel32=kernel32)

        suppress_caps_lock_toggle(host)

        user32.GetKeyState.assert_called_once_with(_VK_CAPITAL)
        # keydown + keyup synthetic Caps Lock press.
        assert user32.keybd_event.call_count == 2
        keydown_call, keyup_call = user32.keybd_event.call_args_list
        assert keydown_call.args == (_VK_CAPITAL, 0x45, 0, 0)
        assert keyup_call.args == (_VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP, 0)
        # Brief sleep lets the OS process the synthetic events.
        kernel32.Sleep.assert_called_once_with(5)
        assert host._caps_lock_suppressing is False, (
            "the suppressing flag must be reset after the synthetic press"
        )

    def test_no_synthetic_press_when_toggle_off(self):
        """When ``GetKeyState`` reports the toggle OFF, no synthetic
        keypress is sent — but the flag is still cleared and the sleep
        still happens (cheap idle path)."""
        user32 = _make_user32(toggle_state=0)
        kernel32 = MagicMock(name="kernel32")
        host = _FakeHost(user32=user32, kernel32=kernel32)

        suppress_caps_lock_toggle(host)

        user32.keybd_event.assert_not_called()
        kernel32.Sleep.assert_called_once_with(5)
        assert host._caps_lock_suppressing is False

    def test_exception_logs_and_resets_flag(self, caplog):
        """If ``GetKeyState`` itself raises, the helper must log via
        ``log.exception`` and reset the suppressing flag so the polling
        loop is not left in a stuck state."""
        user32 = MagicMock(name="user32")
        user32.GetKeyState.side_effect = OSError("user32 gone")
        kernel32 = MagicMock(name="kernel32")
        host = _FakeHost(user32=user32, kernel32=kernel32)
        host._caps_lock_suppressing = True  # simulate an in-flight press

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.hotkeys"):
            suppress_caps_lock_toggle(host)

        assert host._caps_lock_suppressing is False, (
            "the suppressing flag must be reset even when the call fails"
        )
        error_records = [r for r in caplog.records if "Failed to suppress Caps Lock toggle" in r.message]
        assert error_records, "suppress_caps_lock_toggle must log 'Failed to suppress Caps Lock toggle' on failure"
        assert error_records[0].exc_info is not None


class TestEnsureCapsLockOff:
    """``ensure_caps_lock_off`` — proactive defense-in-depth toggle-off."""

    def test_noop_without_user32(self):
        """With ``_user32`` falsy the helper must return immediately."""
        host = _FakeHost(user32=None, kernel32=None)
        ensure_caps_lock_off(host)  # must not raise

    def test_forces_off_when_caps_lock_is_on(self, caplog):
        """When the toggle state is ON, the helper must send the
        synthetic keydown+keyup and log at INFO."""
        user32 = _make_user32(toggle_state=1)
        host = _FakeHost(user32=user32, kernel32=MagicMock())

        with caplog.at_level(logging.INFO, logger="voice_typer.server.hotkeys"):
            ensure_caps_lock_off(host)

        user32.GetKeyState.assert_called_once_with(_VK_CAPITAL)
        assert user32.keybd_event.call_count == 2
        assert any("Proactive caps lock toggle-off" in r.message for r in caplog.records)

    def test_no_press_when_caps_lock_is_off(self):
        """When the toggle state is OFF, no synthetic keypress is sent."""
        user32 = _make_user32(toggle_state=0)
        host = _FakeHost(user32=user32, kernel32=MagicMock())

        ensure_caps_lock_off(host)

        user32.keybd_event.assert_not_called()

    def test_exception_logs_without_raising(self, caplog):
        """A failure inside the helper must be caught and logged via
        ``log.exception``, never propagated to the polling loop."""
        user32 = MagicMock(name="user32")
        user32.GetKeyState.side_effect = OSError("user32 gone")
        host = _FakeHost(user32=user32, kernel32=MagicMock())

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.hotkeys"):
            ensure_caps_lock_off(host)  # must not raise

        assert any("Failed to force caps lock off" in r.message for r in caplog.records)
