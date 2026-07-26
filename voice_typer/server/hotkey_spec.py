"""Canonical hotkey spec parser — the single source of truth.

RW-1 (Hotkey parser unification): previously the codebase had four
independent hotkey parsers with subtly different behaviour:

1. ``_parse_hotkey_parts`` in ``config_validators.py`` — simple
   strip + split + lower.
2. ``_parse_hotkey_to_pynput`` in ``hotkeys.py`` — returns pynput
   ``Key`` / ``KeyCode`` objects.
3. ``parse_hotkey_to_win32`` in ``hotkeys.py`` — returns
   ``(vk, modifiers)`` for Win32 ``RegisterHotKey``.
4. ``parse_hotkey_spec`` in ``native_hotkeys.py`` — returns a dict
   with ``modifiers``, ``main_key``, ``is_modifier_only``, etc.

They diverged on modifier-alias handling (e.g. ``win`` / ``super`` /
``cmd`` normalisation), key-name normalisation (e.g. ``caps_lock``
vs ``CapsLock`` wire-protocol name) and multi-key handling
(first-match-wins vs. silently dropping extras).

This module provides :func:`parse_hotkey` — the SINGLE CANONICAL
parser. All other parsers should delegate to it for the
tokenisation / alias-resolution step, then apply their own
platform-specific concerns (VK-code lookup, pynput ``Key`` mapping,
wire-protocol name normalisation, RegisterHotKey modifier-bit
collapsing).

The canonical parser PRESERVES distinction between ``win`` /
``super`` / ``cmd`` (they are three different canonical names).
Platform-specific adapters are responsible for any collapsing
required by the underlying API (e.g. Win32 ``RegisterHotKey`` uses a
single ``_MOD_WIN`` bit for all three; pynput has only
``Key.cmd``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


# Canonical modifier names (lowercase). All aliases normalise to one of
# these. This is the SINGLE SOURCE OF TRUTH for modifier alias
# resolution — no other module should maintain its own alias table.
MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "altgr": "alt_gr",
    "alt_gr": "alt_gr",
    "right_alt": "alt_gr",
    "ralt": "alt_gr",
    "cmd": "cmd",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
    "win": "win",
    "win_l": "win",
    "win_r": "win",
    "super": "super",
    "super_l": "super",
    "super_r": "super",
    "fn": "fn",
    "globe": "fn",
}

#: Frozen set of all canonical modifier names (the values of
#: ``MODIFIER_ALIASES``). Useful for membership checks.
CANONICAL_MODIFIERS: frozenset[str] = frozenset(set(MODIFIER_ALIASES.values()))


@dataclass(frozen=True)
class HotkeySpec:
    """Parsed hotkey specification.

    Attributes:
        modifiers: Canonical modifier names, sorted alphabetically and
            deduplicated. Examples: ``("ctrl", "shift")``,
            ``("alt_gr",)``, ``("win",)``.
        keys: Non-modifier key names (lowercase, no angle brackets).
            The first element (if any) is the "main" key. Subsequent
            elements are extra keys that the canonical parser kept
            rather than silently dropping — adapters that only support
            one main key (Win32 ``RegisterHotKey``, pynput) take
            ``keys[0]`` and ignore the rest.
        is_modifier_only: True if there is at least one modifier and
            no non-modifier keys (e.g. ``<alt>``, ``<ctrl>+<shift>``).
        is_empty: True if the spec produced no modifiers and no keys
            (e.g. empty string, ``"   "``, ``"<>+<>"``).
    """

    modifiers: tuple[str, ...]
    keys: tuple[str, ...]
    is_modifier_only: bool
    is_empty: bool

    @property
    def main_key(self) -> str | None:
        """First non-modifier key, or ``None`` if there are none."""
        return self.keys[0] if self.keys else None

    def to_spec_string(self) -> str:
        """Convert back to pynput-style string: ``'<ctrl>+<alt>+v'``.

        Round-trips through :func:`parse_hotkey` (modulo alias
        normalisation and modifier reordering). Useful for logging
        and for serialising a normalised form of a user-provided spec.
        """
        parts = [f"<{m}>" for m in self.modifiers] + [f"<{k}>" for k in self.keys]
        return "+".join(parts)


def parse_hotkey(spec: str) -> HotkeySpec:
    """Parse a pynput-style hotkey string into a :class:`HotkeySpec`.

    This is the SINGLE CANONICAL parser. All other parsers should
    delegate to this for tokenisation and alias resolution.

    Accepted syntax::

        "<ctrl>+<alt>+v"     # angle-bracketed modifiers + bare key
        "ctrl+alt+v"         # no angle brackets
        "<Ctrl>+<ALT>+V"     # mixed case (normalised to lowercase)
        "<ctrl>+<ctrl>+<v>"  # duplicates removed
        "<alt>"              # modifier-only
        ""                   # empty → is_empty=True

    The parser:

    - Strips ``<`` / ``>`` from each ``+``-separated token.
    - Lowercases everything.
    - Resolves aliases via :data:`MODIFIER_ALIASES` (so ``"control"``
      → ``"ctrl"``, ``"globe"`` → ``"fn"``, etc.).
    - Deduplicates modifiers and keys (preserving first-seen order for
      keys; sorting modifiers alphabetically for deterministic
      comparison).
    - Preserves the distinction between ``win`` / ``super`` / ``cmd``
      (platform-specific adapters are responsible for collapsing
      these where necessary).
    - Preserves the distinction between ``alt`` and ``alt_gr``.

    The parser does NOT do key-name normalisation (e.g. ``caps_lock``
    → ``CapsLock`` wire-protocol name) — that is a separate concern
    handled by ``_normalize_key_name`` in ``native_hotkeys.py``.
    Likewise, VK-code lookup (``hotkeys._VK_MAP``) and pynput
    ``Key`` / ``KeyCode`` conversion are separate concerns handled by
    the respective adapters.
    """
    if not spec:
        return HotkeySpec(modifiers=(), keys=(), is_modifier_only=False, is_empty=True)

    parts: list[str] = []
    for raw in spec.split("+"):
        part = raw.replace("<", "").replace(">", "").strip().lower()
        if part:
            parts.append(part)

    if not parts:
        return HotkeySpec(modifiers=(), keys=(), is_modifier_only=False, is_empty=True)

    modifiers: list[str] = []
    keys: list[str] = []
    for part in parts:
        if part in MODIFIER_ALIASES:
            mod = MODIFIER_ALIASES[part]
            if mod not in modifiers:
                modifiers.append(mod)
        else:
            if part not in keys:
                keys.append(part)

    # Sort modifiers for deterministic comparison (so ``<alt>+<ctrl>``
    # and ``<ctrl>+<alt>`` produce the same ``HotkeySpec``).
    modifiers.sort()

    return HotkeySpec(
        modifiers=tuple(modifiers),
        keys=tuple(keys),
        is_modifier_only=len(keys) == 0 and len(modifiers) > 0,
        is_empty=False,
    )


__all__ = [
    "MODIFIER_ALIASES",
    "CANONICAL_MODIFIERS",
    "HotkeySpec",
    "parse_hotkey",
]
