"""Modifier name canonicalisation helpers.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
().

This module owns:

- :data:`_MOD_CANONICAL_MAP` — wire-protocol modifier name →
  canonical lowercase form (collapses ``Cmd``/``Win``/``Super`` to
  ``"cmd"`` for cross-platform matching).
- :func:`_canonical_modifier` — wire-protocol name → canonical.
- :func:`_canonical_modifier_name_for_token` — hotkey-spec token →
  canonical.
- :func:`_modifier_to_token` — wire-protocol name → spec token
  (used by the recorder to rebuild a spec from captured events).
- :func:`_key_name_to_token` — wire-protocol key name → spec token
  (reverse of :func:`._normalize_key_name`).
"""


# ─── Modifier name canonicalization ───────────────────────────────────────

_MOD_CANONICAL_MAP = {
    # NSEvent / macOS modifiers
    "Ctrl": "ctrl",
    "Shift": "shift",
    "Alt": "alt",
    "Cmd": "cmd",
    # Windows modifiers
    "Win": "cmd",
    # Linux modifiers
    "Super": "cmd",
}


def _canonical_modifier(wire_name: str) -> str | None:
    """Convert a wire-protocol modifier name (e.g. 'Win', 'Super', 'Cmd')
    to a canonical lowercase form ('ctrl', 'shift', 'alt', 'cmd').

    Returns None if the name is not a recognized modifier.
    """
    return _MOD_CANONICAL_MAP.get(wire_name)


def _canonical_modifier_name_for_token(token: str) -> str | None:
    """Convert a hotkey-spec modifier token (e.g. 'ctrl', 'alt', 'cmd',
    'win', 'super') to canonical form."""
    aliases = {
        "ctrl": "ctrl",
        "shift": "shift",
        "alt": "alt",
        "altgr": "alt",  # treat AltGr as Alt for matching purposes
        "cmd": "cmd",
        "win": "cmd",
        "super": "cmd",
    }
    return aliases.get(token)


def _modifier_to_token(wire_name: str) -> str:
    """Convert a wire-protocol modifier name to a spec token."""
    mapping = {
        "Ctrl": "ctrl",
        "Shift": "shift",
        "Alt": "alt",
        "Cmd": "cmd",
        "Win": "win",
        "Super": "super",
        "Fn": "fn",
    }
    return mapping.get(wire_name, wire_name.lower())


def _key_name_to_token(name: str) -> str | None:
    """Convert a wire-protocol key name back to a spec token."""
    # Reverse of _normalize_key_name
    if not name:
        return None
    # Function keys
    if name.startswith("F") and name[1:].isdigit():
        return name.lower()
    # Single letter / digit
    if len(name) == 1:
        return name.lower()
    # Special keys (reverse map)
    reverse = {
        "Space": "space",
        "Enter": "enter",
        "Tab": "tab",
        "Esc": "esc",
        "Backspace": "backspace",
        "Insert": "insert",
        "Delete": "delete",
        "Home": "home",
        "End": "end",
        "PageUp": "page_up",
        "PageDown": "page_down",
        "CapsLock": "caps_lock",
        "NumLock": "num_lock",
        "ScrollLock": "scroll_lock",
        "PrintScreen": "print_screen",
        "Pause": "pause",
        "Up": "up",
        "Down": "down",
        "Left": "left",
        "Right": "right",
        "MediaPlay": "media_play_pause",
        "MediaStop": "media_stop",
        "MediaNext": "media_next",
        "MediaPrev": "media_prev",
        "Num0": "num_0",
        "Num1": "num_1",
        "Num2": "num_2",
        "Num3": "num_3",
        "Num4": "num_4",
        "Num5": "num_5",
        "Num6": "num_6",
        "Num7": "num_7",
        "Num8": "num_8",
        "Num9": "num_9",
        "NumDecimal": "num_decimal",
        "NumAdd": "num_add",
        "NumSubtract": "num_subtract",
        "NumMultiply": "num_multiply",
        "NumDivide": "num_divide",
        "NumEnter": "num_enter",
    }
    return reverse.get(name)
