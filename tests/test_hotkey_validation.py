"""Regression tests for the backend hotkey validator (HOTKEY-VALIDATION-001).

These tests pin the denylist-based validation policy introduced in
``voice_typer/server/config_validators.py::_validate_hotkey``.  The
validator mirrors the frontend ``validateHotkey`` in
``voice_typer/client/src/renderer/src/components/hotkey-validation.ts``.

The policy is a DENYLIST design, not a blanket rule design:
  - Allow Alt+<letter> by default (e.g. <alt>+<q>, <alt>+<r>).
  - Allow Ctrl+Shift, Ctrl+Alt+<key>, Delete+End, and similar non-reserved
    combinations.
  - Block OS-reserved shortcuts (Alt+Tab, Alt+F4, Alt+Esc, Alt+Space,
    Win+*, Cmd+<letter> on macOS, etc.).
  - Block common Ctrl+<letter> app shortcuts (Copy/Paste/Undo/Save/etc.).
  - Block Shift+<letter> (interferes with capitalization).
  - Block Alt+Shift on Windows (language switching).
"""

from __future__ import annotations

import pytest
from voice_typer.server.config_validators import (
    _BLOCKED_CTRL_LETTERS,
    _HOTKEY_MODIFIERS,
    _RESERVED_HOTKEYS,
    _validate_hotkey,
)

# ── Allowed combinations ──────────────────────────────────────────────

ALLOWED_HOTKEYS = [
    "<caps_lock>",
    "<ctrl>",
    "<alt>",
    "<shift>",
    "<f2>",
    "<delete>",
    "<insert>",
    "<home>",
    "<end>",
    "<page_up>",
    "<page_down>",
    # Alt+<letter> is allowed by default (denylist design).
    "<alt>+<q>",
    "<alt>+<r>",
    "<alt>+<z>",
    "<alt>+<v>",
    # Ctrl+Shift (modifier-only combo) is allowed.
    "<ctrl>+<shift>",
    # Ctrl+Alt+<key> is allowed.
    "<ctrl>+<alt>+<u>",
    "<ctrl>+<alt>+<v>",
    # Ctrl+Q is now allowed (user choice — no longer in blocked list).
    "<ctrl>+<q>",
    # Multi-key non-modifier combos are allowed.
    "<delete>+<end>",
    # F-key combos with modifiers are allowed.
    "<shift>+<f5>",
    "<ctrl>+<f1>",
    "<ctrl>+<alt>+<f2>",
]


# ── Blocked combinations ─────────────────────────────────────────────

# HOTKEY-VALIDATION-002 (Task 2.2.5): Win+<key> combos are Windows-only
# reserved shortcuts. They are in a separate platform-conditional list
# (WINDOWS_BLOCKED_HOTKEYS) so the test only expects them to be blocked
# when running on Windows. On Linux, the "win" modifier name isn't used
# (Linux uses "super"), and Super+<key> is handled by the per-platform
# reserved list (Super+L, Super+D, Super+Tab). On macOS, the Win key
# doesn't exist as a system modifier.
WINDOWS_BLOCKED_HOTKEYS = [
    "<win>+<l>",
    "<win>+<e>",
    "<win>+<v>",
    "<win>+<d>",
    "<win>+<r>",
]

BLOCKED_HOTKEYS = [
    # Universal reserved shortcuts (blocked on ALL platforms).
    "<alt>+<tab>",
    "<alt>+<f4>",
    "<alt>+<esc>",
    "<alt>+<space>",
    # Enter-based combos — interfere with typing, form submission,
    # and messaging shortcuts.
    "<enter>",
    "<ctrl>+<enter>",
    "<shift>+<enter>",
    # Bare modifier keys — Win opens Start menu, Cmd is a system
    # gesture on macOS. Blocked on all platforms.
    "<win>",
    "<cmd>",
    # Tab navigation — interferes with keyboard navigation
    # and tab switching in browsers/applications.
    "<tab>",
    "<shift>+<tab>",
    "<ctrl>+<tab>",
    "<ctrl>+<shift>+<tab>",
    # Fullscreen / special behavior.
    "<alt>+<enter>",
    # Linux Super key alone (Activities overview / app launcher).
    "<super>",
    # Backspace would fire while deleting text during normal typing.
    "<backspace>",
    # Space alone would fire on every space bar press while typing.
    "<space>",
    # Common Ctrl+<letter> app shortcuts.
    "<ctrl>+<c>",
    "<ctrl>+<v>",
    "<ctrl>+<x>",
    "<ctrl>+<z>",
    "<ctrl>+<a>",
    "<ctrl>+<s>",
    "<ctrl>+<y>",
    "<ctrl>+<w>",
    "<ctrl>+<f>",
    "<ctrl>+<p>",
    "<ctrl>+<n>",
    "<ctrl>+<o>",
    "<ctrl>+<t>",
    "<ctrl>+<l>",
    "<ctrl>+<r>",
    "<ctrl>+<h>",
    "<ctrl>+<j>",
    "<ctrl>+<k>",
    "<ctrl>+<b>",
    "<ctrl>+<i>",
    "<ctrl>+<u>",
    "<ctrl>+<d>",
    "<ctrl>+<e>",
    "<ctrl>+<g>",
    "<ctrl>+<m>",
    # Shift+<letter> (interferes with capitalization).
    "<shift>+<z>",
    "<shift>+<a>",
    "<shift>+<m>",
]


class TestValidateHotkeyAllows:
    """Verify the denylist allows non-reserved combinations."""

    @pytest.mark.parametrize("hotkey", ALLOWED_HOTKEYS)
    def test_allows(self, hotkey: str) -> None:
        result = _validate_hotkey(hotkey)
        assert result is None, (
            f"Expected {hotkey!r} to be allowed, but got: {result}"
        )


class TestValidateHotkeyBlocks:
    """Verify the denylist blocks reserved/conflicting combinations."""

    @pytest.mark.parametrize("hotkey", BLOCKED_HOTKEYS)
    def test_blocks(self, hotkey: str) -> None:
        result = _validate_hotkey(hotkey)
        assert result is not None, (
            f"Expected {hotkey!r} to be blocked, but it was allowed"
        )


class TestValidateHotkeyPlatformConditional:
    """Platform-conditional blocks: Alt+Shift is Windows-only."""

    def test_alt_shift_blocked_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alt+Shift is blocked on Windows (language switching)."""
        import voice_typer.server.config_validators as cv
        monkeypatch.setattr(cv._sys, "platform", "win32")
        result = _validate_hotkey("<alt>+<shift>")
        assert result is not None, (
            "Alt+Shift should be blocked on Windows (language switching)"
        )

    def test_alt_shift_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alt+Shift is allowed on Linux (no language-switching conflict)."""
        import voice_typer.server.config_validators as cv
        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<alt>+<shift>")
        assert result is None, (
            "Alt+Shift should be allowed on Linux"
        )


class TestValidateHotkeyWindowsSpecific:
    """Windows-specific blocks: Win+<key> is only blocked on Windows.

    HOTKEY-VALIDATION-002 (Task 2.2.5): the prior code blanket-blocked
    Win/Super+anything on BOTH Windows and Linux. This incorrectly rejected
    <super>+<space> on Linux (a combo most Linux DEs allow reassigning).
    The blanket block now applies only on Windows. On Linux, Super combos
    are checked against the per-platform reserved list (Super+L, Super+D,
    Super+Tab).
    """

    @pytest.mark.parametrize("hotkey", WINDOWS_BLOCKED_HOTKEYS)
    def test_win_combos_blocked_on_windows(
        self, hotkey: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Win+<key> combos are blocked on Windows (OS shell reserved)."""
        import voice_typer.server.config_validators as cv
        monkeypatch.setattr(cv._sys, "platform", "win32")
        result = _validate_hotkey(hotkey)
        assert result is not None, (
            f"Expected {hotkey!r} to be blocked on Windows, but it was allowed"
        )

    def test_win_combo_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Win+<key> is allowed on Linux (Win modifier isn't used on Linux)."""
        import voice_typer.server.config_validators as cv
        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<win>+<r>")
        assert result is None, (
            "<win>+<r> should be allowed on Linux (Win isn't a Linux modifier)"
        )

    def test_super_space_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """<super>+<space> is allowed on Linux (not in the reserved list)."""
        import voice_typer.server.config_validators as cv
        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<super>+<space>")
        assert result is None, (
            "<super>+<space> should be allowed on Linux — most DEs allow reassigning it"
        )

    def test_super_l_blocked_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """<super>+<l> is blocked on Linux (lock screen)."""
        import voice_typer.server.config_validators as cv
        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<super>+<l>")
        assert result is not None, (
            "<super>+<l> should be blocked on Linux (lock screen)"
        )


class TestValidateHotkeyEdgeCases:
    """Edge cases: empty, non-string, oversized, structural."""

    def test_rejects_empty_string(self) -> None:
        assert _validate_hotkey("") is not None

    def test_rejects_whitespace_only(self) -> None:
        assert _validate_hotkey("   ") is not None

    def test_rejects_non_string(self) -> None:
        assert _validate_hotkey(123) is not None  # type: ignore[arg-type]
        assert _validate_hotkey(None) is not None  # type: ignore[arg-type]
        assert _validate_hotkey([]) is not None  # type: ignore[arg-type]

    def test_rejects_oversized(self) -> None:
        huge = "<" + "a" * 300 + ">"
        assert _validate_hotkey(huge) is not None

    def test_rejects_no_keys_after_parse(self) -> None:
        assert _validate_hotkey("<>+<>") is not None
        assert _validate_hotkey("++++") is not None


class TestValidateHotkeySingleLetterRejection:
    """HOTKEY-VALIDATION-002 (Task 2.2.5): single letters/digits can't be
    standalone hotkeys — they'd interfere with normal typing. The prior fix
    added letters/digits to KEY_CODE_TO_PYNPUT (so Alt+Q parses) but forgot
    to add this validation rule, silently accepting <a>, <1>, etc.
    """

    @pytest.mark.parametrize("letter", list("abcdefghijklmnopqrstuvwxyz"))
    def test_rejects_single_letter(self, letter: str) -> None:
        result = _validate_hotkey(f"<{letter}>")
        assert result is not None, (
            f"Single letter <{letter}> should be rejected — it would interfere with typing"
        )

    @pytest.mark.parametrize("digit", list("0123456789"))
    def test_rejects_single_digit(self, digit: str) -> None:
        result = _validate_hotkey(f"<{digit}>")
        assert result is not None, (
            f"Single digit <{digit}> should be rejected — it would interfere with typing"
        )

    def test_allows_single_special_key(self) -> None:
        """Non-letter, non-digit single keys are still valid (F-keys, Caps Lock, etc.)."""
        for key in ("<f2>", "<caps_lock>", "<delete>", "<insert>", "<home>", "<end>"):
            result = _validate_hotkey(key)
            assert result is None, f"{key} should be allowed as a single key"

    def test_allows_letter_in_combo(self) -> None:
        """Letters ARE allowed when part of a combo (e.g. Alt+Q)."""
        for hotkey in ("<alt>+<q>", "<ctrl>+<alt>+<u>", "<shift>+<f5>"):
            result = _validate_hotkey(hotkey)
            assert result is None, f"{hotkey} should be allowed (letter in combo is fine)"


class TestReservedHotkeysTable:
    """Verify the _RESERVED_HOTKEYS table invariants."""

    def test_has_entries_for_all_platforms(self) -> None:
        assert "win32" in _RESERVED_HOTKEYS
        assert "darwin" in _RESERVED_HOTKEYS
        assert "linux" in _RESERVED_HOTKEYS

    def test_all_entries_are_lowercase(self) -> None:
        for platform, entries in _RESERVED_HOTKEYS.items():
            for entry in entries:
                assert entry == entry.lower(), (
                    f"Reserved hotkey {entry!r} for {platform} must be lowercase"
                )

    def test_linux_does_not_reserve_super_space(self) -> None:
        # Invariant: <super>+<space> is intentionally NOT reserved on Linux.
        # The frontend test "still offers <super>+<space> on Linux" pins this.
        assert "<super>+<space>" not in _RESERVED_HOTKEYS["linux"]


class TestBlockedCtrlLetters:
    """Verify the _BLOCKED_CTRL_LETTERS set covers common app shortcuts."""

    def test_contains_copy_paste_undo(self) -> None:
        assert "c" in _BLOCKED_CTRL_LETTERS  # Copy
        assert "v" in _BLOCKED_CTRL_LETTERS  # Paste
        assert "x" in _BLOCKED_CTRL_LETTERS  # Cut
        assert "z" in _BLOCKED_CTRL_LETTERS  # Undo
        assert "y" in _BLOCKED_CTRL_LETTERS  # Redo
        assert "a" in _BLOCKED_CTRL_LETTERS  # Select All
        assert "s" in _BLOCKED_CTRL_LETTERS  # Save
        assert "f" in _BLOCKED_CTRL_LETTERS  # Find
        assert "p" in _BLOCKED_CTRL_LETTERS  # Print
        assert "w" in _BLOCKED_CTRL_LETTERS  # Close (Windows/Linux)

    def test_all_entries_are_single_lowercase_letters(self) -> None:
        for letter in _BLOCKED_CTRL_LETTERS:
            assert len(letter) == 1
            assert letter.isalpha()
            assert letter.islower()


class TestHotkeyModifiers:
    """Verify the _HOTKEY_MODIFIERS set covers all modifier variants."""

    def test_contains_core_modifiers(self) -> None:
        for mod in ("ctrl", "shift", "alt", "cmd", "win", "super", "fn"):
            assert mod in _HOTKEY_MODIFIERS

    def test_contains_left_right_variants(self) -> None:
        for mod in ("ctrl_l", "ctrl_r", "shift_l", "shift_r", "alt_l", "alt_r"):
            assert mod in _HOTKEY_MODIFIERS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
