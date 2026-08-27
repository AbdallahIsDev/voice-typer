"""Win32 ctypes context setup helpers for ``WindowsNativeHotkey``.

Extracted from the original ``windows_native.py`` god-class
split). These helpers set ``argtypes``/``restype`` for the Win32
DLL functions used by the polling loop, message loop, LL hook,
and CapsLock suppressor strategies.

Setting argtypes BEFORE any Win32 call is critical: without them,
ctypes defaults to ``c_int`` which truncates 64-bit pointers
(``HWND``, ``HHOOK``, ``LPARAM``) on 64-bit Windows, causing
silent corruption.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import Any

from ..win32_vk import (
    _MOD_ALT,
    _MOD_CONTROL,
    _MOD_SHIFT,
    _MOD_WIN,
    _VK_CONTROL,
    _VK_LWIN,
    _VK_MENU,
    _VK_RWIN,
    _VK_SHIFT,
)


def compute_modifier_vks(modifiers: int) -> list[int]:
    """Return the VK codes for the given Win32 modifier flags.

    Maps ``_MOD_ALT`` → ``VK_MENU`` (covers both LAlt/RAlt),
    ``_MOD_CONTROL`` → ``VK_CONTROL`` (covers LCtrl/RCtrl),
    ``_MOD_SHIFT`` → ``VK_SHIFT`` (covers LShift/RShift), and
    ``_MOD_WIN`` → both ``VK_LWIN`` and ``VK_RWIN`` (the Win key
    has no combined VK like ``VK_MENU`` for Alt).
    """
    modifier_vks: list[int] = []
    if modifiers & _MOD_ALT:
        modifier_vks.append(_VK_MENU)
    if modifiers & _MOD_CONTROL:
        modifier_vks.append(_VK_CONTROL)
    if modifiers & _MOD_SHIFT:
        modifier_vks.append(_VK_SHIFT)
    if modifiers & _MOD_WIN:
        modifier_vks.append(_VK_LWIN)
        modifier_vks.append(_VK_RWIN)
    return modifier_vks


def setup_main_argtypes(user32: Any, kernel32: Any) -> None:
    """Set ``argtypes``/``restype`` for the main Win32 calls used by ``start()``.

    Covers:

    - ``BOOL RegisterHotKey(HWND, int, UINT, UINT)``
    - ``BOOL UnregisterHotKey(HWND, int)``
    - ``BOOL PostThreadMessageW(DWORD, UINT, WPARAM, LPARAM)``
    - ``DWORD GetLastError(void)``
    - ``SHORT GetAsyncKeyState(int)``
    - ``VOID Sleep(DWORD)``
    - ``UINT SendInput(UINT, LPINPUT, int)`` — modern keyboard-injection
      API used by the CapsLock suppressor (replaces the deprecated
      ``keybd_event``). The ``INPUT`` struct payload is defined in
      :mod:`voice_typer.server.hotkeys.windows._win32_keyboard`.
    - ``SHORT GetKeyState(int)`` — toggle/pressed state.
    """
    from ctypes.wintypes import BOOL, DWORD, HWND, INT, UINT, WPARAM

    # BOOL RegisterHotKey(HWND, int, UINT, UINT)
    user32.RegisterHotKey.argtypes = [HWND, INT, UINT, UINT]
    user32.RegisterHotKey.restype = BOOL

    # BOOL UnregisterHotKey(HWND, int)
    user32.UnregisterHotKey.argtypes = [HWND, INT]
    user32.UnregisterHotKey.restype = BOOL

    # BOOL PostThreadMessageW(DWORD threadId, UINT msg, WPARAM, LPARAM)
    user32.PostThreadMessageW.argtypes = [
        DWORD,
        UINT,
        WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = BOOL

    # DWORD GetLastError(void)
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = DWORD

    # SHORT GetAsyncKeyState(int)
    user32.GetAsyncKeyState.argtypes = [INT]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    # VOID Sleep(DWORD)
    kernel32.Sleep.argtypes = [DWORD]
    kernel32.Sleep.restype = None

    # UINT SendInput(UINT cInputs, LPINPUT pInputs, int cbSize) — modern
    # keyboard-injection API. Replaces the deprecated ``keybd_event``;
    # the INPUT struct payload is defined in
    # :mod:`voice_typer.server.hotkeys.windows._win32_keyboard`.
    # ``LPINPUT`` is a pointer-to-INPUT; ``c_void_p`` is a portable
    # stand-in (the production callsite passes ``ctypes.byref(events)``
    # where ``events`` is an ``INPUT * N`` array — ctypes accepts a
    # ``c_void_p`` argtype for any byref/array pointer).
    user32.SendInput.argtypes = [UINT, ctypes.c_void_p, ctypes.c_int]
    user32.SendInput.restype = UINT

    # SHORT GetKeyState(int nVirtKey)
    user32.GetKeyState.argtypes = [INT]
    user32.GetKeyState.restype = ctypes.c_short


def setup_message_pump_argtypes(user32: Any) -> None:
    """Set ``argtypes``/``restype`` for the WM_HOTKEY message pump.

    Covers ``GetMessageW``, ``TranslateMessage``, ``DispatchMessageW``.
    """
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(ctypes.wintypes.MSG),
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_long
    user32.TranslateMessage.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
    user32.TranslateMessage.restype = ctypes.c_int
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_long


def setup_ll_hook_argtypes(user32: Any, hook_proc_type: Any) -> None:
    """Set ``argtypes``/``restype`` for the WH_KEYBOARD_LL hook functions.

    Covers ``SetWindowsHookExW``, ``CallNextHookEx``, ``UnhookWindowsHookEx``.
    """
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        hook_proc_type,
        ctypes.wintypes.HINSTANCE,
        ctypes.wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [
        ctypes.wintypes.HHOOK,
        ctypes.c_int,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_long
    user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = ctypes.c_int
