"""CapsLock suppression helpers for ``WindowsNativeHotkey``."""

from __future__ import annotations

from ..base import log
from ..win32_vk import _KEYEVENTF_KEYUP, _VK_CAPITAL
from ._win32_keyboard import _send_keyboard_event


def suppress_caps_lock_toggle(self) -> None:
    """Undo the OS-level caps-lock toggle when the hotkey is Caps Lock."""
    if not self._user32 or not self._kernel32:
        return
    try:
        self._caps_lock_suppressing = True
        try:
            # GetKeyState returns a short where bit 0 (0x1) is the

            toggle_state = self._user32.GetKeyState(_VK_CAPITAL) & 0x1
            if toggle_state:
                # Synthetic keydown + keyup toggles the state back.

                _send_keyboard_event(self._user32, _VK_CAPITAL, 0x45, 0)
                _send_keyboard_event(self._user32, _VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP)
                log.debug("[HOTKEY] Suppressed Caps Lock toggle (toggled back off)")
        finally:
            # Brief sleep to let the OS process the synthetic events

            self._kernel32.Sleep(5)
            self._caps_lock_suppressing = False
    except Exception:
        log.exception("[HOTKEY] Failed to suppress Caps Lock toggle")
        self._caps_lock_suppressing = False


def ensure_caps_lock_off(self) -> None:
    """Proactively ensure Caps Lock is OFF (not toggled)."""
    if not self._user32:
        return
    try:
        toggle_state = self._user32.GetKeyState(_VK_CAPITAL) & 0x1
        if toggle_state:
            _send_keyboard_event(self._user32, _VK_CAPITAL, 0x45, 0)
            _send_keyboard_event(self._user32, _VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP)
            log.info("[HOTKEY] Proactive caps lock toggle-off (was ON, forced OFF)")
    except Exception:
        log.exception("[HOTKEY] Failed to force caps lock off")
