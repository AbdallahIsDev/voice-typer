"""Shared Win32 SendInput keyboard-injection helpers.

This module owns the Win32 ``INPUT`` / ``KEYBDINPUT`` ctypes definitions
and a small ``_send_keyboard_event`` wrapper that the
:mod:`voice_typer.server.hotkeys.windows.caps_lock_suppressor` (and any
future Win32 keyboard-injection site in the hotkeys package) uses to
synthesize keypresses via the modern ``SendInput`` API rather than the
deprecated ``keybd_event`` function.

``keybd_event`` was superseded by ``SendInput`` in Windows 2000; it still
works today but is documented as deprecated and may be removed in a
future Windows release. ``SendInput`` is also the canonical API used by
the clipboard paste path (see ``voice_typer/server/clipboard/windows.py``),
so this module reuses the same ctypes structure layout to keep the two
sites consistent.

The structures mirror the Win32 SDK definitions:
https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input

Only the keyboard branch of the ``INPUT`` union is populated — the
mouse/hardware input structs are intentionally omitted to keep the
surface small (no hotkey code path synthesizes mouse or hardware input).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from typing import Any

# ULONG_PTR is pointer-sized: 4 bytes on 32-bit Windows, 8 bytes on
# 64-bit. ``wintypes`` does not export it directly; select the right
# width by ``sizeof(c_void_p)``. Mirrors the same selector in
# ``clipboard/windows.py`` so the two definitions stay bit-for-bit
# identical.
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32


class KEYBDINPUT(ctypes.Structure):
    """Win32 ``KEYBDINPUT`` — payload for a keyboard INPUT event.

    See https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput
    """

    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )
    # ``KEYEVENTF_KEYUP`` = 0x0002 — releases a key previously held down
    # by a synthetic press. Matches the Win32 constant and the legacy
    # ``_KEYEVENTF_KEYUP`` symbol in ``win32_vk``.
    KEYUP = 0x0002


class InputUnion(ctypes.Union):
    """Win32 ``INPUT`` union — only the keyboard branch is populated."""

    _fields_ = (("ki", KEYBDINPUT),)


class INPUT(ctypes.Structure):
    """Win32 ``INPUT`` structure (keyboard variant).

    See https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
    """

    _fields_ = (
        ("type", wintypes.DWORD),
        # The C struct has an anonymous union here; ctypes requires a
        # name to address the union member. Named ``ki`` to match the
        # keyboard-branch convention used by ``clipboard/windows.py``.
        ("ki", InputUnion),
    )
    # ``INPUT_KEYBOARD`` = 1 — selects the ``ki`` union member.
    KEYBOARD = 1


def _build_keyboard_input(vk: int, scan: int, flags: int) -> INPUT:
    """Build a single Win32 ``INPUT`` struct for a keyboard event.

    Parameters mirror the ``keybd_event`` arguments the codebase used
    before this module existed (``bVk``, ``bScan``, ``dwFlags``) so the
    ``caps_lock_suppressor`` callsites can switch over with no behavior
    change — the same scan codes and flag values (e.g.
    ``KEYEVENTF_KEYUP``) flow through unchanged.
    """
    return INPUT(
        INPUT.KEYBOARD,
        InputUnion(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def _send_keyboard_event(user32: Any, vk: int, scan: int, flags: int) -> int:
    """Send a single keyboard event via the modern ``SendInput`` API.

    Replaces the deprecated ``user32.keybd_event(bVk, bScan, dwFlags,
    dwExtraInfo)`` callsite. The behavior is preserved 1:1: a single
    keypress is synthesized with the same virtual-key code, hardware
    scan code, and flags as before. ``dwExtraInfo`` is always 0 (no
    caller in the hotkeys package ever set a non-zero value).

    Returns the number of events successfully inserted into the input
    queue (0 = failure, 1 = success). The caller is responsible for any
    retry / fallback path — the existing caps-lock-suppression
    callsites treat a partial / failed synthetic press as best-effort
    (logged via the surrounding ``log.exception`` handler) and do not
    retry, matching the prior ``keybd_event`` semantics (which had no
    return value at all).
    """
    inp = _build_keyboard_input(vk, scan, flags)
    events = (INPUT * 1)(inp)
    return user32.SendInput(1, ctypes.byref(events), ctypes.sizeof(INPUT))


__all__ = [
    "INPUT",
    "InputUnion",
    "KEYBDINPUT",
    "_build_keyboard_input",
    "_send_keyboard_event",
]
