"""Extracted hotkey formatting logic from tray.py."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


# Display map for known keys. Mirrors the TS displayMap in
_DISPLAY_MAP: dict[str, str] = {
    "ctrl": "Ctrl",
    "ctrl_l": "Ctrl",
    "ctrl_r": "Ctrl",
    "shift": "Shift",
    "shift_l": "Shift",
    "shift_r": "Shift",
    "alt": "Alt",
    "alt_l": "Alt",
    "alt_r": "Alt",
    "alt_gr": "AltGr",
    "cmd": "Cmd",
    "cmd_l": "Cmd",
    "cmd_r": "Cmd",
    "win": "Win",
    "super": "Super",
    "fn": "Fn",
    "globe": "\U0001f310",  # 🌐
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Esc",
    "caps_lock": "Caps Lock",
    "num_lock": "Num Lock",
    "scroll_lock": "Scroll Lock",
    "print_screen": "Print Screen",
    "pause": "Pause",
    "insert": "Insert",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "up": "\u2191",  # ↑
    "down": "\u2193",  # ↓
    "left": "\u2190",  # ←
    "right": "\u2192",  # →
}

# Matches f1..f99, same as the TS /^f\d{1,2}$/ regex (case-sensitive,
_FKEY_RE = re.compile(r"^f\d{1,2}$")

# Strips angle brackets from a pynput token, e.g. "<ctrl>" → "ctrl".
_STRIP_BRACKETS_RE = re.compile(r"[<>]")


def notification_hotkey_label(hotkey: object) -> str:
    """Display label for a notification message's hotkey hint.

    Returns ``format_hotkey_label`` output for a configured hotkey
    """
    if not hotkey:
        return "your hotkey"
    return format_hotkey_label(str(hotkey))


def format_hotkey_label(hotkey: str) -> str:
    """Format a hotkey string like '<ctrl>+<shift>+f2' into 'Ctrl+Shift+F2'."""
    # catches both empty-string and None inputs (defensive, the
    if not hotkey:
        return "None"
    parts: list[str] = []
    for part in hotkey.split("+"):
        key = _STRIP_BRACKETS_RE.sub("", part).strip()
        if key in _DISPLAY_MAP:
            parts.append(_DISPLAY_MAP[key])
            continue
        if _FKEY_RE.match(key):
            parts.append(key.upper())
            continue
        if len(key) == 1:
            parts.append(key.upper())
            continue
        # Default fallback: capitalize the first character, keep the
        parts.append(key[:1].upper() + key[1:])
    return "+".join(parts)
