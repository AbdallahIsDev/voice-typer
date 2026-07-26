"""TASK-9 (HOTKEY-UNIFY-003): parity corpus for ``format_hotkey_label``.

The Python ``voice_typer.server.tray_hotkey.format_hotkey_label`` must
produce byte-identical output to the canonical TypeScript
``formatHotkeyLabel`` in
``client/src/renderer/src/components/hotkey/hotkey-utils.ts`` for any given
hotkey string. This file pins that parity with a ~30-case corpus
covering:

  - modifier combos (ctrl, alt, shift, cmd, win, super, alt_gr, fn)
  - F-keys (f1..f12) as single keys and inside combos
  - special keys (caps_lock, space, enter, tab, esc, delete, home,
    end, page_up, page_down)
  - arrow keys (up, down, left, right — rendered as Unicode arrows)
  - single characters (a → A, 1 → 1)
  - the Fn / Globe macOS-only keys (🌐)
  - the empty-string case (→ "None", matching the TS early-return)
  - a mixed combo whose final token is a modifier (the formatter
    doesn't validate, it only formats)

If you change either implementation, regenerate the expected outputs
by running the TS version against the same corpus and pasting the
results here — the two columns must stay in lock-step.
"""

from __future__ import annotations

import pytest
from voice_typer.server.tray_hotkey import format_hotkey_label

# Each tuple is (input_hotkey, expected_label). The expected labels
# were derived from the canonical TS implementation in
# hotkey-utils.ts::formatHotkeyLabel — this is the parity contract.
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
    # Mixed combo, last token is a modifier — formatter doesn't
    # validate, it only formats. The combo-validity is enforced
    # elsewhere by config_validators._validate_hotkey.
    ("<caps_lock>+<ctrl>", "Caps Lock+Ctrl"),
]


@pytest.mark.parametrize(("hotkey", "expected"), TEST_CASES)
def test_format_hotkey_label_parity(hotkey: str, expected: str) -> None:
    """Python ``format_hotkey_label`` must match the TS canonical output.

    TASK-9: this is the parity contract — every entry in ``TEST_CASES``
    must produce the exact same string the TS implementation would
    produce for the same input. If this test fails, either:
      (a) the Python port drifted from the TS canonical version, or
      (b) the TS canonical version changed and the corpus wasn't
          regenerated.
    Either way, the two implementations must be brought back into
    lock-step before merging.
    """
    actual = format_hotkey_label(hotkey)
    assert actual == expected, (
        f"format_hotkey_label({hotkey!r}) returned {actual!r}, expected {expected!r} (must match TS formatHotkeyLabel)"
    )


class TestFormatHotkeyLabelBranches:
    """Branch-coverage tests for the formatter's internal logic.

    These complement the parametrized parity corpus by pinning the
    three formatting branches (displayMap / F-key regex / single-char
    uppercasing) and the default fallback explicitly, so a regression
    in any single branch is immediately identifiable.
    """

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
        # with the TS regex, even though f-keys beyond f24 are rare).
        assert format_hotkey_label("<f99>") == "F99"

    def test_single_char_branch(self) -> None:
        """A single character is uppercased (a → A, 1 → 1)."""
        assert format_hotkey_label("<a>") == "A"
        assert format_hotkey_label("<z>") == "Z"
        assert format_hotkey_label("<1>") == "1"
        assert format_hotkey_label("<0>") == "0"

    def test_default_fallback_capitalizes_first_letter(self) -> None:
        """Unknown multi-char tokens get the first letter capitalized.

        This is the catch-all branch — it mirrors the TS
        `key.charAt(0).toUpperCase() + key.slice(1)` fallback.
        """
        # "media_play_pause" isn't in the displayMap, so it falls
        # through to the default branch: "M" + "edia_play_pause".
        assert format_hotkey_label("<media_play_pause>") == "Media_play_pause"

    def test_empty_string_returns_none(self) -> None:
        """Empty input → 'None' (matches TS early-return)."""
        assert format_hotkey_label("") == "None"


class TestTrayMenuDisplayHotkeyDelegation:
    """tray_menu.display_hotkey must delegate to format_hotkey_label.

    TASK-9: the delegation chain
    ``tray_menu.display_hotkey → tray_hotkey.format_hotkey_label`` must
    stay intact so the tray menu benefits from the unified formatter.
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

        # display_hotkey("") falls back to <f4>, then format_hotkey_label
        # turns it into "F4". Note: this is DIFFERENT from calling
        # format_hotkey_label("") directly (which returns "None"),
        # because display_hotkey substitutes the fallback BEFORE the
        # formatter sees the empty string.
        assert tray_menu.display_hotkey("", fallback="<f4>") == "F4"

    def test_display_hotkey_uses_fallback_when_none(self) -> None:
        """None hotkey → fallback is formatted via format_hotkey_label."""
        from voice_typer.server import tray_menu

        assert tray_menu.display_hotkey(None, fallback="<f9>") == "F9"
