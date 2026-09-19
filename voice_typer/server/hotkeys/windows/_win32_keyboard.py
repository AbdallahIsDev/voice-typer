"""Shared Win32 SendInput keyboard-injection helpers."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from typing import Any

# ULONG_PTR is pointer-sized: 4 bytes on 32-bit Windows, 8 bytes on
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class KEYBDINPUT(ctypes.Structure):
    """Win32 ``KEYBDINPUT``: payload for a keyboard INPUT event.

    See https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput
    """

    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )
    # by a synthetic press. Matches the Win32 constant and the legacy
    KEYUP = 0x0002


class InputUnion(ctypes.Union):
    """Win32 ``INPUT`` union, only the keyboard branch is populated."""

    _fields_ = (("ki", KEYBDINPUT),)


class INPUT(ctypes.Structure):
    """Win32 ``INPUT`` structure (keyboard variant).

    See https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
    """

    _fields_ = (
        ("type", wintypes.DWORD),
        # The C struct has an anonymous union here; ctypes requires a
        ("ki", InputUnion),
    )
    # ``INPUT_KEYBOARD`` = 1, selects the ``ki`` union member.
    KEYBOARD = 1


def _build_keyboard_input(vk: int, scan: int, flags: int) -> INPUT:
    """Build a single Win32 ``INPUT`` struct for a keyboard event."""
    return INPUT(
        INPUT.KEYBOARD,
        InputUnion(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def _send_keyboard_event(user32: Any, vk: int, scan: int, flags: int) -> int:
    """Send a single keyboard event via the modern ``SendInput`` API.

    Returns the number of events successfully inserted into the input
    """
    inp = _build_keyboard_input(vk, scan, flags)
    events = (INPUT * 1)(inp)
    # ``user32`` is typed ``Any`` (ctypes WinDLL), so ``SendInput``
    return int(user32.SendInput(1, ctypes.byref(events), ctypes.sizeof(INPUT)))


__all__ = [
    "INPUT",
    "InputUnion",
    "KEYBDINPUT",
    "_build_keyboard_input",
    "_send_keyboard_event",
]
