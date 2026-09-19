"""TASK-9 (HOTKEY-UNIFY-003): parity corpus for ``format_hotkey_label``."""

from __future__ import annotations

import pytest
from voice_typer.server.tray_hotkey import format_hotkey_label

TEST_CASES: list[tuple[str, str]] = [
    ("<ctrl>+<alt>+v", "Ctrl+Alt+V"),
    ("<ctrl>+<shift>+v", "Ctrl+Shift+V"),
    ("<shift>+<f5>", "Shift+F5"),
    ("<caps_lock>", "Caps Lock"),
    ("<alt>", "Alt"),
    ("<f2>", "F2"),
    ("<f12>", "F12"),
    ("<up>", "\u2191"),  # ↑
    ("<down>", "\u2193"),  # ↓
    ("<left>", "\u2190"),  # ←
    ("<right>", "\u2192"),  # →
    ("<space>", "Space"),
    ("<enter>", "Enter"),
    ("<tab>", "Tab"),
    ("<esc>", "Esc"),
    ("<delete>", "Delete"),
    ("<home>", "Home"),
    ("<end>", "End"),
    ("<page_up>", "Page Up"),
    ("<page_down>", "Page Down"),
    ("<cmd>+<shift>+v", "Cmd+Shift+V"),
    ("<win>+<e>", "Win+E"),
    ("<super>+<space>", "Super+Space"),
    ("<alt_gr>+<e>", "AltGr+E"),
    ("<fn>", "Fn"),
    ("<globe>", "\U0001f310"),  # 🌐
    ("<ctrl>+<alt>+<f2>", "Ctrl+Alt+F2"),
    ("<shift>+<tab>", "Shift+Tab"),
    ("", "None"),  # Empty → "None" (matches TS behavior)
    # Mixed combo, last token is a modifier, formatter doesn't
    ("<caps_lock>+<ctrl>", "Caps Lock+Ctrl"),
]


@pytest.mark.parametrize(("hotkey", "expected"), TEST_CASES)
def test_format_hotkey_label_parity(hotkey: str, expected: str) -> None:
    """Python ``format_hotkey_label`` must match the TS canonical output."""
    actual = format_hotkey_label(hotkey)
    assert actual == expected, (
        f"format_hotkey_label({hotkey!r}) returned {actual!r}, expected {expected!r} (must match TS formatHotkeyLabel)"
    )


class TestFormatHotkeyLabelBranches:
    """Branch-coverage tests for the formatter's internal logic."""

    def test_display_map_branch(self) -> None:
        """Tokens in _DISPLAY_MAP are returned verbatim from the map."""
        # Spot-check each display-map category.
        assert format_hotkey_label("<ctrl>") == "Ctrl"
        assert format_hotkey_label("<ctrl_l>") == "Ctrl"
        assert format_hotkey_label("<ctrl_r>") == "Ctrl"
        assert format_hotkey_label("<alt_gr>") == "AltGr"
        assert format_hotkey_label("<caps_lock>") == "Caps Lock"
        assert format_hotkey_label("<page_down>") == "Page Down"

    def test_fkey_regex_branch(self) -> None:
        """Tokens matching ^f\\d{1,2}$ are uppercased (f1..f99)."""
        assert format_hotkey_label("<f1>") == "F1"
        assert format_hotkey_label("<f9>") == "F9"
        assert format_hotkey_label("<f10>") == "F10"
        assert format_hotkey_label("<f12>") == "F12"
        # The regex allows 1-2 digits, so f99 also matches (parity
        assert format_hotkey_label("<f99>") == "F99"

    def test_single_char_branch(self) -> None:
        """A single character is uppercased (a → A, 1 → 1)."""
        assert format_hotkey_label("<a>") == "A"
        assert format_hotkey_label("<z>") == "Z"
        assert format_hotkey_label("<1>") == "1"
        assert format_hotkey_label("<0>") == "0"

    def test_default_fallback_capitalizes_first_letter(self) -> None:
        """Unknown multi-char tokens get the first letter capitalized."""
        # "media_play_pause" isn't in the displayMap, so it falls
        assert format_hotkey_label("<media_play_pause>") == "Media_play_pause"

    def test_empty_string_returns_none(self) -> None:
        """Empty input → 'None' (matches TS early-return)."""
        assert format_hotkey_label("") == "None"


class TestTrayMenuDisplayHotkeyDelegation:
    """
    tray_menu.display_hotkey must delegate to format_hotkey_label.
    These tests pin that contract.
    """

    def test_display_hotkey_delegates_to_format_hotkey_label(self) -> None:
        """display_hotkey must call format_hotkey_label on its input."""
        from voice_typer.server import tray_menu

        # A non-empty hotkey is passed straight through.
        assert tray_menu.display_hotkey("<ctrl>+<alt>+v") == "Ctrl+Alt+V"
        assert tray_menu.display_hotkey("<caps_lock>") == "Caps Lock"

    def test_display_hotkey_uses_fallback_when_empty(self) -> None:
        """Empty hotkey → fallback is formatted via format_hotkey_label."""
        from voice_typer.server import tray_menu

        assert tray_menu.display_hotkey("", fallback="<f4>") == "F4"

    def test_display_hotkey_uses_fallback_when_none(self) -> None:
        """None hotkey → fallback is formatted via format_hotkey_label."""
        from voice_typer.server import tray_menu

        assert tray_menu.display_hotkey(None, fallback="<f9>") == "F9"
