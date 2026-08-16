"""Extracted hotkey formatting logic from tray.py.

_format_hotkey_label was previously an inline static method
in TrayIcon.  It is now a standalone function so other modules (e.g.
tray_window.py) can import it without creating a TrayIcon instance.

(HOTKEY-UNIFY-003): this function now mirrors the canonical
TypeScript implementation in
``client/src/renderer/src/components/hotkey-utils.ts::formatHotkeyLabel``
exactly — same displayMap, same F-key regex (``^f\\d{1,2}$``), same
single-char uppercasing, same "capitalize first letter" fallback, and
the same empty-string → "None" behavior. This keeps the tray menu
labels byte-identical to the Settings UI labels for any given hotkey
string. The parity corpus lives in ``tests/test_hotkey_format.py``.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


# Display map for known keys. Mirrors the TS displayMap in
# hotkey-utils.ts verbatim. Keys are intentionally lowercase; the
# lookup below is case-sensitive (the TS version does NOT lowercase
# the key before lookup, so we don't either — this preserves parity
# for inputs like "<F12>" which fall through to the default
# capitalize-first-letter branch).
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

# Matches f1..f99 — same as the TS /^f\d{1,2}$/ regex (case-sensitive,
# lowercase f only). Used to uppercase function-key tokens like "f2"
# → "F2". Two-digit support (f10..f99) matches the TS regex even
# though only f1..f12 are practically used; this keeps parity exact.
_FKEY_RE = re.compile(r"^f\d{1,2}$")

# Strips angle brackets from a pynput token, e.g. "<ctrl>" → "ctrl".
# Equivalent to the TS `part.replace(/[<>]/g, "")`.
_STRIP_BRACKETS_RE = re.compile(r"[<>]")


def notification_hotkey_label(hotkey: object) -> str:
    """Display label for a notification message's hotkey hint.

    Returns ``format_hotkey_label`` output for a configured hotkey
    (e.g. ``"Caps Lock"`` for ``<caps_lock>``), or the generic
    ``"your hotkey"`` when nothing is configured. Notification paths
    are safety nets — they must never crash or render a raw "None"
    just because the config has no hotkey yet.
    """
    if not hotkey:
        return "your hotkey"
    return format_hotkey_label(str(hotkey))


def format_hotkey_label(hotkey: str) -> str:
    """Format a hotkey string like '<ctrl>+<shift>+f2' into 'Ctrl+Shift+F2'.

        Handles pynput-style angle-bracket notation and normalizes
        modifier names to user-friendly display form.

    this is a faithful Python port of the canonical TS
        implementation in
        ``client/src/renderer/src/components/hotkey-utils.ts::formatHotkeyLabel``.
        The two must produce identical output for any given input —
        see ``tests/test_hotkey_format.py`` for the parity corpus.
    """
    # Mirror the TS early-return: `if (!hotkey) return "None"`.
    # In Python, `not ""` is True and `not None` is True, so this
    # catches both empty-string and None inputs (defensive — the
    # signature is `str`, but tray_menu.display_hotkey can pass the
    # fallback through here, and a stray None would otherwise crash
    # the .split() call below).
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
        # rest as-is. Mirrors `key.charAt(0).toUpperCase() + key.slice(1)`.
        parts.append(key[:1].upper() + key[1:])
    return "+".join(parts)
