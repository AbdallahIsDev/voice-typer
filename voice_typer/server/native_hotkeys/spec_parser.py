"""Hotkey spec parsing and key-name normalisation."""

import logging
from typing import Any

# Preserve the original logger name (``voice_typer.server.native_hotkeys``)
log = logging.getLogger("voice_typer.server.native_hotkeys")


def parse_hotkey_spec(spec: str) -> dict[str, Any] | None:
    """Parse a pynput-style hotkey spec into a structured form.

    Returns a dict with keys:
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    parsed = parse_hotkey(spec)
    if parsed.is_empty:
        return None

    # Collapse canonical win/super → cmd for cross-platform wire matching.
    _canonical_to_native = {
        "win": "cmd",
        "super": "cmd",
        "alt_gr": "altgr",
    }

    modifiers: set[str] = {_canonical_to_native.get(m, m) for m in parsed.modifiers}

    # Non-modifier keys: only one allowed; extras ignored with a warning
    main_key: str | None = None
    if parsed.keys:
        main_key = _normalize_key_name(parsed.keys[0])
        if len(parsed.keys) > 1:
            log.warning(
                "Hotkey spec %r has multiple non-modifier keys; using first",
                spec,
            )

    if not modifiers and main_key is None:
        return None

    is_fn_only = modifiers == {"fn"} and main_key is None
    is_modifier_only = (not main_key) and bool(modifiers)
    is_caps_lock = main_key == "CapsLock"

    return {
        "modifiers": modifiers,
        "main_key": main_key,
        "is_fn_only": is_fn_only,
        "is_modifier_only": is_modifier_only,
        "is_caps_lock": is_caps_lock,
    }


def _normalize_key_name(token: str) -> str:
    """Normalize a non-modifier key token to the wire-protocol name."""
    t = token.lower().strip()
    # Function keys
    if t.startswith("f") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return f"F{n}"
    # Special keys
    special_map = {
        "space": "Space",
        "enter": "Enter",
        "return": "Enter",
        "tab": "Tab",
        "esc": "Esc",
        "escape": "Esc",
        "backspace": "Backspace",
        "insert": "Insert",
        "delete": "Delete",
        "del": "Delete",
        "home": "Home",
        "end": "End",
        "page_up": "PageUp",
        "pageup": "PageUp",
        "page_down": "PageDown",
        "pagedown": "PageDown",
        "caps_lock": "CapsLock",
        "capslock": "CapsLock",
        "num_lock": "NumLock",
        "numlock": "NumLock",
        "scroll_lock": "ScrollLock",
        "scrolllock": "ScrollLock",
        "print_screen": "PrintScreen",
        "printscreen": "PrintScreen",
        "pause": "Pause",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "media_play_pause": "MediaPlay",
        "media_play": "MediaPlay",
        "media_stop": "MediaStop",
        "media_next": "MediaNext",
        "media_prev": "MediaPrev",
        "media_previous": "MediaPrev",
    }
    if t in special_map:
        return special_map[t]
    # Single letter
    if len(t) == 1 and t.isalpha():
        return t.upper()
    # Single digit
    if len(t) == 1 and t.isdigit():
        return t
    # Numpad keys
    numpad_map = {
        "num_0": "Num0",
        "num_1": "Num1",
        "num_2": "Num2",
        "num_3": "Num3",
        "num_4": "Num4",
        "num_5": "Num5",
        "num_6": "Num6",
        "num_7": "Num7",
        "num_8": "Num8",
        "num_9": "Num9",
        "numpad_0": "Num0",
        "numpad_1": "Num1",
        "numpad_2": "Num2",
        "numpad_3": "Num3",
        "numpad_4": "Num4",
        "numpad_5": "Num5",
        "numpad_6": "Num6",
        "numpad_7": "Num7",
        "numpad_8": "Num8",
        "numpad_9": "Num9",
        "num_decimal": "NumDecimal",
        "num_add": "NumAdd",
        "num_subtract": "NumSubtract",
        "num_multiply": "NumMultiply",
        "num_divide": "NumDivide",
        "num_enter": "NumEnter",
    }
    if t in numpad_map:
        return numpad_map[t]
    # Unknown, return as-is (will likely never match)
    return token
