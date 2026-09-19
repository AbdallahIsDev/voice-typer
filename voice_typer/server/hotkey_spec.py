"""Canonical hotkey spec parser, the single source of truth."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


# Canonical modifier names (lowercase). All aliases normalise to one of
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


@dataclass(frozen=True)
class HotkeySpec:
    """Parsed hotkey specification."""

    modifiers: tuple[str, ...]
    keys: tuple[str, ...]
    is_modifier_only: bool
    is_empty: bool

    @property
    def main_key(self) -> str | None:
        """First non-modifier key, or ``None`` if there are none."""
        return self.keys[0] if self.keys else None

    def to_spec_string(self) -> str:
        """Convert back to pynput-style string: ``'<ctrl>+<alt>+v'``."""
        parts = [f"<{m}>" for m in self.modifiers] + [f"<{k}>" for k in self.keys]
        return "+".join(parts)


def parse_hotkey(spec: str) -> HotkeySpec:
    """Parse a pynput-style hotkey string into a :class:`HotkeySpec`."""
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
    modifiers.sort()

    return HotkeySpec(
        modifiers=tuple(modifiers),
        keys=tuple(keys),
        is_modifier_only=len(keys) == 0 and len(modifiers) > 0,
        is_empty=False,
    )


__all__ = [
    "MODIFIER_ALIASES",
    "HotkeySpec",
    "parse_hotkey",
]
