"""CapsLock suppression helpers for ``WindowsNativeHotkey``.

Extracted from the original ``windows_native.py`` god-class
split). When the hotkey is Caps Lock, the OS toggles the caps state
on every physical press. These helpers undo that toggle (reactive
suppression) and proactively force caps lock OFF (periodic
defense-in-depth).

Modernized to use the Win32 ``SendInput`` API instead of the
deprecated ``keybd_event`` function. ``SendInput`` supersedes
``keybd_event`` (deprecated since Windows 2000); the behavior is
preserved 1:1 — the same virtual-key code, scan code, and flag
values flow through unchanged.
"""

from __future__ import annotations

from ..base import log
from ..win32_vk import _KEYEVENTF_KEYUP, _VK_CAPITAL
from ._win32_keyboard import _send_keyboard_event


def suppress_caps_lock_toggle(self) -> None:
    """Undo the OS-level caps-lock toggle when the hotkey is Caps Lock.

    Windows toggles the caps-lock state as part of processing the
    VK_CAPITAL keyDown, before the foreground app sees it. The native
    ``windows-key-listener.exe`` binary suppresses this via its
    ``WH_KEYBOARD_LL`` hook (see ``should_suppress_keydown`` in
    ``voice_typer/server/native/windows-key-listener.c``). The legacy
    polling backend can't install a low-level hook from Python without
    significant complexity, so we use a different approach: read the
    current toggle state via ``GetKeyState`` and, if the key is now
    toggled ON, send a synthetic Caps Lock keypress via ``SendInput``
    to toggle it back OFF.

    The ``_caps_lock_suppressing`` flag is set while the synthetic
    keypress is in flight so the polling loop skips processing —
    otherwise the synthetic events would re-trigger the callback or
    prematurely fire on_release.
    """
    if not self._user32 or not self._kernel32:
        return
    try:
        self._caps_lock_suppressing = True
        try:
            # GetKeyState returns a short where bit 0 (0x1) is the
            # toggle state. If 1, Caps Lock was just toggled ON by
            # the physical press — undo it with a synthetic press.

            toggle_state = self._user32.GetKeyState(_VK_CAPITAL) & 0x1
            if toggle_state:
                # Synthetic keydown + keyup toggles the state back.
                # The 0x45 scan code is the hardware scan code for
                # Caps Lock — preserved verbatim from the prior
                # ``keybd_event`` callsite so the synthetic press is
                # indistinguishable from a real one at the keyboard
                # driver layer.

                _send_keyboard_event(self._user32, _VK_CAPITAL, 0x45, 0)
                _send_keyboard_event(self._user32, _VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP)
                log.debug("[HOTKEY] Suppressed Caps Lock toggle (toggled back off)")
        finally:
            # Brief sleep to let the OS process the synthetic events
            # before clearing the flag. Without this, the next
            # iteration of the polling loop might see the synthetic
            # keyup and prematurely fire on_release. 5ms is enough
            # for the OS to dispatch the events but short enough
            # that the user doesn't notice a delay.

            self._kernel32.Sleep(5)
            self._caps_lock_suppressing = False
    except Exception:
        log.exception("[HOTKEY] Failed to suppress Caps Lock toggle")
        self._caps_lock_suppressing = False


def ensure_caps_lock_off(self) -> None:
    """Proactively ensure Caps Lock is OFF (not toggled).

    Unlike ``suppress_caps_lock_toggle()`` which reacts to a key-press
    event, this method proactively checks the current caps-lock state
    and toggles it OFF if it is ON. It is called:

    - At registration time (when the hotkey starts)
    - Periodically every ~200ms while the polling loop runs

    This is defense-in-depth against the caps-lock toggle race where
    the OS toggles caps ON before the reactive suppression can undo
    it. The ``_caps_lock_suppressing`` flag is NOT set here because
    this method is called outside of a key-press event context (no
    risk of feedback loop with the polling loop).
    """
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
