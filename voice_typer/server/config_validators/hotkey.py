"""Hotkey validation: reserved-shortcut denylist and structural checks.

Extracted from the original monolithic ``config_validators.py`` (package
split).  Provides:

* the canonical reserved-shortcut table (loaded once from
  ``voice_typer/server/hotkey_reserved.json``),
* :func:`_platform_key` — current-platform key for the table lookup,
* :func:`_parse_hotkey_parts` — delegates to
  :mod:`voice_typer.server.hotkey_spec` for canonical parsing,
* the 9 ``_check_*`` stage helpers that make up :func:`_validate_hotkey`,
* :func:`_validate_hotkey` — the per-field validator used by
  ``IPC_CONFIG_ALLOWLIST``.

The 9 stage helpers are extracted into small functions so the
orchestrator (``_validate_hotkey``) stays readable. Each helper returns
an error string or ``None``; the first non-``None`` wins, preserving
the original short-circuit ordering exactly.
"""

from __future__ import annotations

import json as _json
from pathlib import Path as _Path

from voice_typer.server.platform_utils import is_macos as _is_macos
from voice_typer.server.platform_utils import is_windows as _is_windows

# ──────────────────────────────────────────────────────────────────────────
# HOTKEY-VALIDATION-001: OS-reserved shortcuts that must never be assigned
# as global hotkeys.  This is the backend mirror of the frontend
# ``RESERVED_SHORTCUTS`` table in
# ``voice_typer/client/src/renderer/src/components/hotkey-validation.ts``.
#
# HOTKEY-SHARED-001 (Task 1.4): the reserved-shortcut tables are now loaded
# from a single canonical JSON file at
# ``voice_typer/server/hotkey_reserved.json``. Both the frontend (via Vite
# JSON import) and the backend (via ``json.load``) consume the SAME file,
# eliminating the "MUST be kept in sync" duplication problem. A CI test
# (``tests/test_hotkey_reserved_sync.py``) verifies the two in-memory
# structures are byte-identical.
#
# In addition to the per-platform explicit denylist, ``_validate_hotkey``
# applies the following blanket rules (mirroring the frontend):
#   - Win+* and Super+* are blocked on Windows and Linux respectively
#     (system-wide shell shortcuts).
#   - Alt+Tab, Alt+F4, Alt+Esc, Alt+Space are blocked on every platform
#     (window management).
#   - Alt+Shift is blocked on Windows (language switching).
#   - Ctrl+<common-letter> (c, v, x, z, a, s, y, w, f, p, n, o, t, l, r,
#     h, j, k, b, i, u, d, e, g, m, q) is blocked (Copy/Paste/Undo/Save/
#     etc.).  Ctrl+<F-key> and Ctrl+<special-key> are allowed.
#   - Shift+<letter> is blocked (interferes with capitalization).
#     Shift+<F-key> and Shift+<special-key> are allowed.
#
# All other combinations (including Alt+<letter>) are allowed by default.
# This is a denylist design, not a blanket rule design.
# ──────────────────────────────────────────────────────────────────────────

# HOTKEY-SHARED-001: load the canonical reserved-shortcut table from the
# JSON file. The file lives in the parent ``server/`` directory (this
# module is at ``server/config_validators/hotkey.py``, so we go up one
# level to reach ``server/hotkey_reserved.json``) so the relative path is
# stable regardless of the working directory.
_RESERVED_DATA_PATH = _Path(__file__).resolve().parent.parent / "hotkey_reserved.json"


def _load_reserved_data() -> dict:
    """Load and cache the reserved-hotkey JSON config.

    Returns a dict with keys:
        - ``universal_reserved``: list[str]
        - ``per_platform_reserved``: dict[str, list[str]]
        - ``blocked_ctrl_letters``: list[str]
        - ``modifiers``: list[str]
    """
    with _RESERVED_DATA_PATH.open("r", encoding="utf-8") as f:
        return _json.load(f)


_RESERVED_DATA = _load_reserved_data()

# Per-platform reserved shortcuts. Stored in the SAME format as user input
# (angle brackets, lowercase) so we can compare directly with
# ``value.lower()``. Built from the JSON file at module init.
_RESERVED_HOTKEYS: dict[str, set[str]] = {
    platform: set(entries) for platform, entries in _RESERVED_DATA["per_platform_reserved"].items()
}

# Universal window-management shortcuts blocked on EVERY platform.
# Alt+Tab, Alt+F4, Alt+Esc, Alt+Space are OS-level window management
# on Windows, macOS (with Alt=Option), and most Linux desktops.
# Stored in the SAME format as user input (angle brackets, lowercase)
# so we can compare directly with ``value.lower()``.
_UNIVERSAL_RESERVED_HOTKEYS = frozenset(_RESERVED_DATA["universal_reserved"])

# Common Ctrl+<letter> shortcuts that are universally expected by users
# (Copy, Paste, Undo, Save, Select All, etc.).  Mirrors the frontend
# behavior.  These are blocked regardless of platform.
_BLOCKED_CTRL_LETTERS = frozenset(_RESERVED_DATA["blocked_ctrl_letters"])

# Modifier keys recognized in the hotkey string (pynput-style, lowercase).
_HOTKEY_MODIFIERS = frozenset(_RESERVED_DATA["modifiers"])


def _platform_key() -> str:
    """Return the platform key for ``_RESERVED_HOTKEYS`` lookup."""
    if _is_windows():
        return "win32"
    if _is_macos():
        return "darwin"
    return "linux"


def _parse_hotkey_parts(hotkey: str) -> list[str]:
    """Parse a hotkey string like ``"<ctrl>+<alt>+v"`` into ``["ctrl","alt","v"]``.

    (Hotkey parser unification): this now delegates to the
        canonical :func:`voice_typer.server.hotkey_spec.parse_hotkey` and
        flattens the resulting :class:`HotkeySpec` (canonical modifiers
        followed by non-modifier keys) back into a flat list of
        lowercased tokens, preserving the original list-returning API.

        Behavioural changes versus the prior strip-and-split implementation:

        - Aliases are resolved (e.g. ``<control>`` → ``"ctrl"``,
          ``<globe>`` → ``"fn"``, ``<altgr>`` → ``"alt_gr"``).
        - Duplicate tokens are deduplicated (e.g. ``<ctrl>+<ctrl>+<v>``
          → ``["ctrl", "v"]``).
        - Modifiers are sorted alphabetically; non-modifier keys keep
          their original order.

        These changes are safe for the validator's consumers, which only
        use ``len(parts)``, ``parts[0]``, ``any(p in ... for p in parts)``,
        and ``[p for p in parts if p (not) in _HOTKEY_MODIFIERS]`` — all
        of which are insensitive to ordering, dedup, and alias resolution
        (every canonical modifier name is in ``_HOTKEY_MODIFIERS``).
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    spec = parse_hotkey(hotkey)
    if spec.is_empty:
        return []
    return list(spec.modifiers) + list(spec.keys)


def _check_basic_shape(value: object) -> str | None:
    """Stage 1: type / length / emptiness guards (shared by all hotkeys)."""
    if not isinstance(value, str):
        return f"must be a string, got {type(value).__name__}"
    if len(value) > 256:
        return "exceeds maximum length 256"
    if not value.strip():
        return "must not be empty"
    return None


def _check_universal_reserved(normalized: str) -> str | None:
    """Stage 2: OS / common-app shortcuts blocked on EVERY platform.

    Includes window-management shortcuts (Alt+Tab/F4/Esc/Space) and
    Enter-based combos (Enter, Ctrl+Enter, Shift+Enter) which interfere
    with typing, form submission, and messaging shortcuts.
    """
    if normalized in _UNIVERSAL_RESERVED_HOTKEYS:
        return "reserved — conflicts with operating system or common app shortcuts"
    return None


def _check_platform_reserved(normalized: str, platform: str) -> str | None:
    """Stage 3: per-platform OS-reserved shortcuts.

    On Linux the physical Windows key is reported as ``super`` by
    pynput / evdev, but a user (or a buggy renderer) may send ``<win>``
    instead. ``<win>`` and ``<super>`` are distinct tokens in the
    canonical parser (see ``hotkey_spec.py``), so an exact string match
    against the Linux reserved list (which uses ``<super>+<key>``)
    silently lets ``<win>+<l>`` through even though ``<super>+<l>`` is
    reserved (lock screen). Normalize ``<win>`` to ``<super>`` on Linux
    ONLY — Windows keeps its blanket Win+block (stage 5) and macOS
    doesn't use either name (its system modifier is ``cmd``).
    """
    reserved = _RESERVED_HOTKEYS.get(platform, set())
    if not reserved:
        return None
    lookup = normalized
    if platform == "linux":
        lookup = normalized.replace("<win>", "<super>")
    for r in reserved:
        if r == lookup:
            return f"reserved by operating system ({platform})"
    return None


def _check_single_alphanumeric(parts: list[str]) -> str | None:
    """Stage 4: reject a standalone single letter/digit (HOTKEY-VALIDATION-002).

    A standalone <a> would trigger dictation every time the user types
    'a'. Multi-key combos (Alt+Q, Ctrl+V) are NOT affected — they have
    2+ parts and are checked by the later stages.
    """
    if len(parts) == 1:
        sole = parts[0]
        if len(sole) == 1 and sole.isalnum():
            return f"single letters and digits can't be used as hotkeys — '{sole}' would interfere with typing"
    return None


def _check_multi_non_modifier(parts: list[str]) -> str | None:
    """Stage 5: reject combos with more than one non-modifier key.

    A global hotkey listener (pynput, the Windows low-level hook
    ``WH_KEYBOARD_LL``, the macOS ``CGEventTap``) registers a single
    non-modifier key plus zero-or-more modifiers. A combo like
    ``<a>+<b>`` would either fail to register, fire spuriously when
    either key is pressed alone, or require the user to press both keys
    simultaneously in a way that's indistinguishable from typing.

    This stage runs BEFORE the Ctrl+letter / Shift+letter blanket
    rules so a structurally invalid combo like ``<ctrl>+<a>+<b>``
    (which would otherwise match the Ctrl+A reserved-shortcut rule) is
    rejected with the structural error rather than the
    reserved-shortcut error.
    """
    non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
    if len(non_mods) > 1:
        return (
            f"hotkey has {len(non_mods)} non-modifier keys — "
            "at most one non-modifier key is supported (global hotkey "
            "listeners register a single non-modifier plus modifiers)"
        )
    return None


def _check_os_shell_combos(parts: list[str], platform: str) -> str | None:
    """Stage 6: Win+* / Super+* (Windows shell) and Cmd+<letter> (macOS).

    The Win/Super blanket block applies only on Windows (where the Win
    key is heavily reserved by the OS shell). On Linux, Super combos are
    deferred to the per-platform reserved list. Cmd+<letter> is blocked
    on macOS but Cmd+<F-key>/<special-key> are allowed.
    """
    has_win = any(p in ("win", "super") for p in parts)
    has_cmd = any(p in ("cmd", "cmd_l", "cmd_r") for p in parts)
    if has_win and platform == "win32":
        return "Windows key combinations are reserved by the OS shell"
    if has_cmd and platform == "darwin" and len(parts) > 1:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        for nm in non_mods:
            if len(nm) == 1 and nm.isalpha():
                return f"Cmd+{nm.upper()} is reserved by macOS / common apps"
    return None


def _check_alt_shift(parts: list[str], platform: str) -> str | None:
    """Stage 7: Alt+Shift block (Windows language switching)."""
    if platform == "win32":
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        has_alt = any(p.startswith("alt") for p in parts)
        has_shift = any(p.startswith("shift") for p in parts)
        if has_alt and has_shift and not non_mods:
            return "Alt+Shift is reserved by Windows for language switching"
    return None


def _check_ctrl_letter(parts: list[str]) -> str | None:
    """Stage 8: Ctrl+<common-letter> block (Copy/Paste/Undo/Save/etc.).

    Only applies to PURE Ctrl+<letter> — if another modifier is present
    (e.g. Ctrl+Alt+U), the combo is allowed because it doesn't conflict
    with the common app shortcuts.
    """
    has_ctrl = any(p.startswith("ctrl") for p in parts)
    if has_ctrl:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        modifiers_non_ctrl = [p for p in parts if p in _HOTKEY_MODIFIERS and not p.startswith("ctrl")]
        if not modifiers_non_ctrl:
            for nm in non_mods:
                if nm in _BLOCKED_CTRL_LETTERS:
                    return f"Ctrl+{nm.upper()} is a reserved application shortcut"
    return None


def _check_shift_letter(parts: list[str]) -> str | None:
    """Stage 9: Shift+<letter> block (interferes with capitalization).

    Only applies to PURE Shift+<letter> — if another modifier is present,
    the combo is allowed (e.g. Ctrl+Shift+Z = redo in many apps).
    """
    has_shift = any(p.startswith("shift") for p in parts)
    if has_shift:
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        modifiers_non_shift = [p for p in parts if p in _HOTKEY_MODIFIERS and not p.startswith("shift")]
        if not modifiers_non_shift:
            for nm in non_mods:
                if len(nm) == 1 and (nm.isalpha() or nm.isdigit()):
                    return f"Shift+{nm.upper()} interferes with text capitalization or symbol input"
    return None


def _validate_hotkey(value: object) -> str | None:
    """Validate a hotkey string against the reserved-shortcut denylist.

        Returns ``None`` if valid, or a human-readable error string if invalid.
        Mirrors the frontend ``validateHotkey`` in ``hotkey-validation.ts``.

        HOTKEY-VALIDATION-001: this replaces the previous length-only check
        (``_make_str_validator(max_len=256)``) which accepted OS-reserved
        shortcuts like ``<alt>+<tab>`` and conflict-prone combos like
        ``<ctrl>+<c>``.

    the 9 validation stages below are extracted into small
        ``_check_*`` helpers so the orchestrator stays readable. Each helper
        returns an error string or ``None``; the first non-``None`` wins,
        preserving the original short-circuit ordering exactly.
    """
    if (err := _check_basic_shape(value)) is not None:
        return err
    # `_check_basic_shape` already rejected non-strings above, but pyrefly
    # can't see through the helper's borrow — narrow explicitly so the
    # `_parse_hotkey_parts(value)` / `value.strip()` calls below type-check.
    if not isinstance(value, str):
        return f"must be a string, got {type(value).__name__}"

    parts = _parse_hotkey_parts(value)
    if not parts:
        return "hotkey has no keys"

    # Strip leading/trailing whitespace BEFORE lowercasing so a padded
    # reserved hotkey like ``"  <alt>+<tab>  "`` matches the denylist
    # (the denylist entries are stored without padding). The parser
    # already strips whitespace between tokens, so internal whitespace
    # is unaffected.
    normalized = value.strip().lower()

    # Stages run in priority order; the first rejection wins.
    for check in (
        lambda: _check_universal_reserved(normalized),
        lambda: _check_platform_reserved(normalized, _platform_key()),
        lambda: _check_single_alphanumeric(parts),
        lambda: _check_multi_non_modifier(parts),
        lambda: _check_os_shell_combos(parts, _platform_key()),
        lambda: _check_alt_shift(parts, _platform_key()),
        lambda: _check_ctrl_letter(parts),
        lambda: _check_shift_letter(parts),
    ):
        if (err := check()) is not None:
            return err

    return None


__all__ = [
    # Reserved-shortcut table
    "_RESERVED_DATA_PATH",
    "_load_reserved_data",
    "_RESERVED_DATA",
    "_RESERVED_HOTKEYS",
    "_UNIVERSAL_RESERVED_HOTKEYS",
    "_BLOCKED_CTRL_LETTERS",
    "_HOTKEY_MODIFIERS",
    # Platform helpers
    "_platform_key",
    "_parse_hotkey_parts",
    # Stage helpers (9 stages)
    "_check_basic_shape",
    "_check_universal_reserved",
    "_check_platform_reserved",
    "_check_single_alphanumeric",
    "_check_multi_non_modifier",
    "_check_os_shell_combos",
    "_check_alt_shift",
    "_check_ctrl_letter",
    "_check_shift_letter",
    # Main validator
    "_validate_hotkey",
]
