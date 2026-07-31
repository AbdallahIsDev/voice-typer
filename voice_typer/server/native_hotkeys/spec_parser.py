"""Hotkey spec parsing and key-name normalisation.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
() — see ``native_hotkeys/__init__.py`` for the package-level
re-export surface that preserves the legacy
``voice_typer.server.native_hotkeys`` import path.

This module owns:

- :func:`parse_hotkey_spec` — parse a pynput-style hotkey spec (e.g.
  ``<ctrl>+<alt>+v``) into the dict consumed by the wire-protocol
  matcher in :mod:`.base`.
- :func:`_normalize_key_name` — convert a pynput-style key token
  (e.g. ``page_up``) to the wire-protocol name (e.g. ``PageUp``).
- :data:`log` — the package-level logger (``voice_typer.server.native_hotkeys``).
  Defined here (rather than in :mod:`.base`) because :mod:`.base`
  imports from this module at top level; defining ``log`` here keeps
  the dependency graph acyclic.
"""

import logging

# Preserve the original logger name (``voice_typer.server.native_hotkeys``)
# so log records emitted from any submodule land under the same logger as
# before the split.  Submodules import this `log` instead of creating
# their own.
log = logging.getLogger("voice_typer.server.native_hotkeys")


# ─── Hotkey spec parsing ───────────────────────────────────────────────────


def parse_hotkey_spec(spec: str) -> dict | None:
    """Parse a pynput-style hotkey spec into a structured form.

        Returns a dict with keys:
            - ``modifiers``: set of modifier names (lowercased: ctrl, shift, alt, cmd, fn)
            - ``main_key``: the non-modifier key name, or None if modifier-only
            - ``is_fn_only``: True if the hotkey is exactly ``<fn>``
            - ``is_modifier_only``: True if the hotkey is a single modifier
              (e.g. ``<alt>``, ``<caps_lock>``)
            - ``is_caps_lock``: True if main_key is "CapsLock"

        Returns None if the spec is empty or unparseable.

    (Hotkey parser unification): this now delegates to the
        canonical :func:`voice_typer.server.hotkey_spec.parse_hotkey` for
        tokenisation and alias resolution, then converts the resulting
        :class:`HotkeySpec` to the dict format consumers expect.

        Platform-specific modifier collapsing — preserved for backward
        compatibility with the wire-protocol matching logic in this
        module (``_on_modifier_event`` maps wire ``Cmd``/``Win``/``Super``
        events to ``"cmd"``, and ``<cmd>`` is expected to match
        ``MOD_DOWN:Win`` on Windows and ``MOD_DOWN:Super`` on Linux):

        - Canonical ``"win"`` → ``"cmd"`` (this adapter collapses)
        - Canonical ``"super"`` → ``"cmd"`` (this adapter collapses)
        - Canonical ``"alt_gr"`` → ``"altgr"`` (wire-protocol name has
          no underscore)

        The canonical parser preserves the distinction; this adapter
        collapses for platform-specific matching. The Win32 adapter
        (``parse_hotkey_to_win32``) collapses ``win`` / ``super`` / ``cmd``
        into ``_MOD_WIN`` for the same reason (Windows does not
        distinguish).
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    parsed = parse_hotkey(spec)
    if parsed.is_empty:
        return None

    # Collapse canonical win/super → cmd for cross-platform wire matching.
    # Map alt_gr → altgr (no underscore) for backward compat with the
    # existing wire-protocol matching in this module.
    _canonical_to_native = {
        "win": "cmd",
        "super": "cmd",
        "alt_gr": "altgr",
    }

    modifiers: set[str] = {_canonical_to_native.get(m, m) for m in parsed.modifiers}

    # Non-modifier keys: only one allowed; extras ignored with a warning
    # (preserves the previous first-match-wins behaviour).
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
    """Normalize a non-modifier key token to the wire-protocol name.

    Key-name maps: this function is the canonical
    name-to-name transformer for the THREE independent key-name tables:

      Frontend: KEY_CODE_TO_PYNPUT (hotkey-utils.ts) — e.code → pynput
      Backend:  _VK_MAP (hotkeys.py) — pynput name → Win32 VK code
      Native:   _normalize_key_name (here) — pynput name → wire name

    All three must agree on the set of names ("f1", "space",
    "caps_lock", "page_up", etc.). This function is the one to update
    when adding a new key name — then update the other two tables in
    parallel so they stay in sync.
    """
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
    # Unknown — return as-is (will likely never match)
    return token
