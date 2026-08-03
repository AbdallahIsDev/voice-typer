"""WM_HOTKEY message-loop strategy for ``WindowsNativeHotkey``.

Extracted from the original ``windows_native.py`` god-class (EC-29
split). The message loop is the event-driven hotkey detection path
used when ``RegisterHotKey`` succeeds OR a ``WH_KEYBOARD_LL`` hook
is installed. It pumps thread messages so the OS can deliver
``WM_HOTKEY`` (or call the hook proc).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes

from ..base import log
from ..win32_vk import _WM_HOTKEY


def run_message_loop(self, callback, low_level_hook=False):
    """Message-pump hotkey detection (event-driven, ~0% CPU while idle).

    Two modes, selected by *low_level_hook*:

    * ``low_level_hook=True`` — a WH_KEYBOARD_LL hook is already installed
      (see ``_install_low_level_hook``). The hook procedure fires
      *callback* directly on the matching key-down (it sees EVERY
      keystroke system-wide, including ESC as WM_SYSKEYDOWN, before any
      app and regardless of RegisterHotKey ownership / foreground focus).
      The message pump here just keeps the thread alive so the hook's
      CallNextHookEx chain and the OS can deliver events; it does NOT
      need to inspect messages itself.

    * ``low_level_hook=False`` — RegisterHotKey succeeded, so WM_HOTKEY
      messages are posted to this thread. We detect them here.

    ESC-CANCEL-DELIVERY (regression fix): this is the reliable path for
    keys that GetAsyncKeyState polling misses. For the registered case,
    ``RegisterHotKey(NULL, id, MOD, vk)`` posts ``WM_HOTKEY`` to THIS
    thread's message queue regardless of foreground focus. For the
    low-level-hook case, the hook catches the key even when another
    process owns it via RegisterHotKey (the 1409 "already registered"
    failure), which is the exact real-world ESC failure we fix.
    """
    if not self._user32:
        # No win32 — should never happen on this path, but fall back to
        # polling defensively.
        log.warning("[HOTKEY] No user32 on message-loop path — falling back to polling")
        self._using_polling = True
        self._run_polling_loop(callback)
        return

    # Set argtypes/restype for the message-pump calls we use here.
    try:
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(ctypes.wintypes.MSG),
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.UINT,
        ]
        self._user32.GetMessageW.restype = ctypes.c_long
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
        self._user32.TranslateMessage.restype = ctypes.c_int
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
        self._user32.DispatchMessageW.restype = ctypes.c_long
    except Exception:
        log.exception("[HOTKEY] Failed to set message-pump argtypes — falling back to polling")
        self._using_polling = True
        self._run_polling_loop(callback)
        return

    if low_level_hook:
        log.info(
            "[HOTKEY] Message loop running with WH_KEYBOARD_LL hook (vk=0x%X, mods=0x%X)",
            self._vk,
            self._modifiers,
        )
    else:
        log.info(
            "[HOTKEY] WM_HOTKEY message loop running (vk=0x%X, id=%d, mods=0x%X)",
            self._vk,
            self._hotkey_id,
            self._modifiers,
        )
    msg = ctypes.wintypes.MSG()
    while not self._stop_event.is_set():
        # PM_REMOVE = 0x0001: remove the message from the queue.
        ret = self._user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
        if ret == 0:  # WM_QUIT
            log.info("[HOTKEY] WM_QUIT received — exiting message loop")
            break
        if ret == -1:  # error
            log.warning("[HOTKEY] GetMessageW returned -1 (error) — exiting message loop")
            break
        if not low_level_hook and msg.message == _WM_HOTKEY and msg.wParam == self._hotkey_id:
            log.info("[HOTKEY FIRED] WM_HOTKEY received for %s", self.hotkey_str)
            try:
                callback()
            except Exception:
                # shield the callback so a single failure
                # doesn't kill the message loop (mirrors polling loop).
                log.exception("[HOTKEY] Callback raised in WM_HOTKEY loop; hotkey still armed for next press")
        # Always translate/dispatch so any other messages (timers, etc.)
        # are processed normally — required for the hook to function.
        try:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log.debug("[HOTKEY] Translate/Dispatch failed for a message", exc_info=True)
