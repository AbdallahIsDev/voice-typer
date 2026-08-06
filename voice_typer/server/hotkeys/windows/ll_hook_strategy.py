"""WH_KEYBOARD_LL low-level hook strategy for ``WindowsNativeHotkey``.

Extracted from the original ``windows_native.py`` god-class (EC-29
split). The low-level hook is the most robust hotkey-detection
path on Windows — Windows calls our hook procedure for EVERY
keystroke system-wide, BEFORE any app sees it and REGARDLESS of
RegisterHotKey ownership or foreground focus. This is the same
mechanism the native ``windows-key-listener.exe`` binary uses.

The hook proc MUST return within ~1ms or Windows marks it
unresponsive and bypasses it. Previously the user callback ran
inline (10-100ms of recorder/IPC work). Now the hook proc
enqueues a callback onto a bounded queue and returns immediately;
a dedicated worker thread drains the queue.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import threading
from collections.abc import Callable

from ..base import log
from ..win32_vk import (
    _WHC_KEYBOARD_LL,
    _WM_KEYDOWN,
    _WM_KEYUP,
    _WM_SYSKEYDOWN,
    _WM_SYSKEYUP,
)


def start_hook_callback_worker(self) -> None:
    """Start the dedicated worker thread for LL hook callbacks."""
    if self._hook_callback_thread is not None and self._hook_callback_thread.is_alive():
        return

    def _worker() -> None:
        while True:
            try:
                fn = self._hook_callback_queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            if fn is None:
                return
            try:
                fn()
            except Exception:
                log.exception(
                    "[HOTKEY] Hook callback raised in worker thread — "
                    "callback dropped, hotkey still armed for next press"
                )

    self._hook_callback_thread = threading.Thread(target=_worker, daemon=True, name="WinHookCb")
    self._hook_callback_thread.start()


def enqueue_hook_callback(self, fn: Callable[[], None] | None) -> None:
    """Enqueue a callback for the worker thread. Non-blocking."""
    if fn is None:
        return
    try:
        self._hook_callback_queue.put_nowait(fn)
    except queue.Full:
        # raised maxsize from 64 to 256 (4× headroom) so a
        # brief worker stall doesn't drop callbacks. When we DO overflow,
        # log at ERROR (not WARNING) — a dropped hotkey callback means
        # the user pressed the hotkey and nothing happened, which is a
        # user-visible failure worth surfacing in error-level metrics.
        log.error(
            "[HOTKEY] LL hook callback queue full (size=%d) — dropping callback. "
            "The hook worker thread may be stuck; hotkey presses will be missed "
            "until it recovers.",
            self._hook_callback_queue.maxsize,
        )


def install_low_level_hook(self, callback) -> bool:
    """Install a WH_KEYBOARD_LL low-level keyboard hook for this hotkey.

    ESC-CANCEL-DELIVERY (regression fix): returns True if the hook was
    installed successfully. The hook procedure runs in THIS thread (the
    one pumping GetMessageW) and fires *callback* when the configured key
    (VK + modifiers) goes down — catching ESC/system keys that
    GetAsyncKeyState polling and even a failed RegisterHotKey miss.

    Returns False if the hook cannot be installed (e.g. SetWindowsHookEx
    is unavailable / fails), so the caller can fall back to the
    RegisterHotKey message loop or polling.

    The hook proc is stored on ``self._hook_proc`` to keep it alive
    (ctypes callbacks are collected if only referenced by the hook), and
    ``self._hook_handle`` is uninstalled in ``stop()``.
    """
    if not self._user32 or not self._kernel32:
        return False
    try:
        # Start the worker thread BEFORE installing the hook
        # so the queue is being drained the moment the first key
        # event arrives.
        self._start_hook_callback_worker()

        # KeyboardProc signature: (nCode, wParam, lParam) -> LRESULT.
        # lParam is a pointer to KBDLLHOOKSTRUCT.
        hook_proc = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )

        # KBDLLHOOKSTRUCT: vkCode, scanCode, flags, time, dwExtraInfo
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_ulong),
                ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        backend = self  # closure reference

        def _hook_proc(n_code, w_param, l_param):
            try:
                if n_code == 0:  # HC_ACTION
                    ks = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = ks.vkCode
                    is_caps = backend._is_caps_lock_hotkey
                    # Match the configured key. For main-key
                    # hotkeys (``backend._vk is not None``) this is
                    # ``vk == backend._vk`` AND modifiers held. For
                    # modifier-only hotkeys (``backend._vk is None``)
                    # this is ``vk in backend._modifier_vks_for_hook``.
                    if backend._vk is not None:
                        vk_matches = vk == backend._vk and backend._modifiers_pressed()
                    else:
                        vk_matches = vk in backend._modifier_vks_for_hook
                    # Key-down path
                    if w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN) and vk_matches:
                        if is_caps:
                            # Caps Lock: swallow the keydown so the OS
                            # never toggles caps state. The toggle fires
                            # on the physical key-up (see below). This is
                            # the same suppression the native
                            # windows-key-listener.exe binary performs.
                            log.info("[HOTKEY] Swallowed Caps Lock keydown (suppress OS toggle)")
                            return 1  # swallow — do not call CallNextHookEx
                        if backend._on_release_callback is not None:
                            # Push-to-talk: start recording on press.
                            log.info(
                                "[HOTKEY FIRED] WH_KEYBOARD_LL caught vk=0x%X (PTT)",
                                vk,
                            )
                            # Dispatch to worker thread.
                            backend._enqueue_hook_callback(callback)
                        elif getattr(backend, "_toggle_on_keyup", False):
                            # Toggle mode (user requested): defer the
                            # toggle to key-up so holding the key cannot
                            # start-then-stop recording. Do nothing here.
                            pass
                        else:
                            # Legacy toggle (e.g. ESC cancel): fire on
                            # key-down as before.
                            log.info(
                                "[HOTKEY FIRED] WH_KEYBOARD_LL caught vk=0x%X (%s)",
                                vk,
                                backend.hotkey_str,
                            )
                            # Dispatch to worker thread.
                            backend._enqueue_hook_callback(callback)
                    # Key-up path
                    elif w_param in (_WM_KEYUP, _WM_SYSKEYUP) and vk_matches:
                        if is_caps:
                            # Caps Lock: fire the toggle exactly once on
                            # the physical key-up, and swallow the keyup
                            # so the OS sees no orphan key-up.
                            log.info("[HOTKEY FIRED] WH_KEYBOARD_LL Caps Lock key-up (toggle)")
                            # Dispatch to worker thread.
                            backend._enqueue_hook_callback(callback)
                            return 1  # swallow keyup
                        if backend._on_release_callback is not None:
                            # Push-to-talk: stop recording on release.
                            log.info(
                                "[HOTKEY] Key released via WH_KEYBOARD_LL hook (vk=0x%X)",
                                vk,
                            )
                            # Dispatch to worker thread.
                            backend._enqueue_hook_callback(backend._on_release_callback)
                        elif getattr(backend, "_toggle_on_keyup", False):
                            # Toggle mode: fire the toggle on key-up.
                            # Holding the key (no key-up) never toggles,
                            # so a press-and-hold cannot start-then-stop
                            # recording. This is the user-requested
                            # behavior.
                            log.info(
                                "[HOTKEY FIRED] WH_KEYBOARD_LL key-up (toggle, vk=0x%X)",
                                vk,
                            )
                            # Dispatch to worker thread.
                            backend._enqueue_hook_callback(callback)
            except Exception:
                log.debug("[HOTKEY] LL hook proc error", exc_info=True)
            # Pass to the next hook so we don't break other hooks.
            return backend._user32.CallNextHookEx(backend._hook_handle or 0, n_code, w_param, l_param)

        self._hook_proc = hook_proc(_hook_proc)
        # Set argtypes for SetWindowsHookExW / UnhookWindowsHookEx.
        self._user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            hook_proc,
            ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.DWORD,
        ]
        self._user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
        self._user32.CallNextHookEx.argtypes = [
            ctypes.wintypes.HHOOK,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        self._user32.CallNextHookEx.restype = ctypes.c_long
        self._user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
        self._user32.UnhookWindowsHookEx.restype = ctypes.c_int

        # hMod must be NULL for a low-level hook (it runs in this process).
        handle = self._user32.SetWindowsHookExW(_WHC_KEYBOARD_LL, self._hook_proc, 0, 0)
        if not handle:
            # previously this log line claimed "(GetLastError)"
            # but never fetched the error code. Mirror the RegisterHotKey
            # pattern at line 198-205 — fetch immediately and include both
            # decimal and hex so the user can look up the Win32 error.
            err = self._kernel32.GetLastError()
            self._last_error = err
            log.warning(
                "[HOTKEY] SetWindowsHookExW(WH_KEYBOARD_LL) failed, "
                "GetLastError=%d (0x%X) — falling back to RegisterHotKey/polling",
                err,
                err,
            )
            self._hook_proc = None
            return False
        self._hook_handle = handle
        log.info("[HOTKEY] WH_KEYBOARD_LL hook installed (vk=0x%X)", self._vk)
        return True
    except Exception:
        log.exception("[HOTKEY] Failed to install WH_KEYBOARD_LL hook")
        self._hook_proc = None
        self._hook_handle = None
        return False
