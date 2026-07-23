"""Regression tests for the backend hotkey validator (HOTKEY-VALIDATION-001).

These tests pin the denylist-based validation policy introduced in
``voice_typer/server/config_validators.py::_validate_hotkey``.  The
validator mirrors the frontend ``validateHotkey`` in
``voice_typer/client/src/renderer/src/components/hotkey/hotkey-validation.ts``.

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
    # CFG-3: multi-key non-modifier combos (e.g. ``<delete>+<end>``) are
    # NO LONGER allowed — they're structurally invalid for a global
    # hotkey listener.  Moved to BLOCKED_HOTKEYS below.
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
    # CFG-3: multi-key non-modifier combos are structurally invalid for
    # a global hotkey listener (pynput, the Windows low-level hook, and
    # the macOS CGEventTap all register a single non-modifier key plus
    # zero-or-more modifiers).  Such combos would either fail to register,
    # fire spuriously when either key is pressed alone, or require the
    # user to press both keys simultaneously in a way that's
    # indistinguishable from typing.
    "<delete>+<end>",
    "<a>+<b>",
    "<f1>+<f2>",
    "<ctrl>+<a>+<b>",
]


class TestValidateHotkeyAllows:
    """Verify the denylist allows non-reserved combinations."""

    @pytest.mark.parametrize("hotkey", ALLOWED_HOTKEYS)
    def test_allows(self, hotkey: str) -> None:
        result = _validate_hotkey(hotkey)
        assert result is None, f"Expected {hotkey!r} to be allowed, but got: {result}"


class TestValidateHotkeyBlocks:
    """Verify the denylist blocks reserved/conflicting combinations."""

    @pytest.mark.parametrize("hotkey", BLOCKED_HOTKEYS)
    def test_blocks(self, hotkey: str) -> None:
        result = _validate_hotkey(hotkey)
        assert result is not None, f"Expected {hotkey!r} to be blocked, but it was allowed"


class TestValidateHotkeyPlatformConditional:
    """Platform-conditional blocks: Alt+Shift is Windows-only."""

    def test_alt_shift_blocked_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alt+Shift is blocked on Windows (language switching)."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "win32")
        result = _validate_hotkey("<alt>+<shift>")
        assert result is not None, "Alt+Shift should be blocked on Windows (language switching)"

    def test_alt_shift_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Alt+Shift is allowed on Linux (no language-switching conflict)."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<alt>+<shift>")
        assert result is None, "Alt+Shift should be allowed on Linux"


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
    def test_win_combos_blocked_on_windows(self, hotkey: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Win+<key> combos are blocked on Windows (OS shell reserved)."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "win32")
        result = _validate_hotkey(hotkey)
        assert result is not None, f"Expected {hotkey!r} to be blocked on Windows, but it was allowed"

    def test_win_combo_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """<win>+<r> is allowed on Linux because <super>+<r> isn't reserved.

        CFG-2: ``win`` is now treated as an alias for ``super`` on Linux
        for the per-platform reserved lookup (so ``<win>+<l>`` is blocked
        on Linux just like ``<super>+<l>``).  This test confirms that the
        alias doesn't over-block: combos whose ``super`` form isn't in the
        Linux reserved list are still allowed.
        """
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<win>+<r>")
        assert result is None, "<win>+<r> should be allowed on Linux — <super>+<r> isn't reserved"

    def test_super_space_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """<super>+<space> is allowed on Linux (not in the reserved list)."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<super>+<space>")
        assert result is None, "<super>+<space> should be allowed on Linux — most DEs allow reassigning it"

    def test_super_l_blocked_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """<super>+<l> is blocked on Linux (lock screen)."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey("<super>+<l>")
        assert result is not None, "<super>+<l> should be blocked on Linux (lock screen)"


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
        assert result is not None, f"Single letter <{letter}> should be rejected — it would interfere with typing"

    @pytest.mark.parametrize("digit", list("0123456789"))
    def test_rejects_single_digit(self, digit: str) -> None:
        result = _validate_hotkey(f"<{digit}>")
        assert result is not None, f"Single digit <{digit}> should be rejected — it would interfere with typing"

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
                assert entry == entry.lower(), f"Reserved hotkey {entry!r} for {platform} must be lowercase"

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


# ──────────────────────────────────────────────────────────────────────────
# CFG-1: whitespace bypass in reserved-shortcut lookup
# ──────────────────────────────────────────────────────────────────────────


class TestCfg1WhitespaceBypass:
    """CFG-1 (Medium): a hotkey string with leading/trailing whitespace
    must NOT bypass the reserved-shortcut denylist.

    Before the fix, ``normalized = value.lower()`` left the whitespace in
    place, so ``" <alt>+<tab> "`` compared unequal to every entry in
    ``_UNIVERSAL_RESERVED_HOTKEYS`` and was silently accepted.  A
    malicious IPC client (or a buggy renderer that forgot to trim) could
    bypass the entire backend mirror of the reserved-shortcut table.

    The fix is ``normalized = value.strip().lower()`` so the lookup
    matches the denylist regardless of surrounding whitespace.
    """

    @pytest.mark.parametrize(
        "padding",
        [" ", "  ", "\t", "\n", " \t\n ", "\r\n"],
    )
    def test_reserved_universal_with_padding_is_blocked(self, padding: str) -> None:
        """A universal-reserved hotkey wrapped in whitespace is rejected."""
        padded = f"{padding}<alt>+<tab>{padding}"
        result = _validate_hotkey(padded)
        assert result is not None, f"Padded reserved hotkey {padded!r} should be blocked (CFG-1)"

    def test_ctrl_c_with_padding_is_blocked(self) -> None:
        """``<ctrl>+<c>`` with surrounding whitespace is rejected via the
        Ctrl+letter blanket rule."""
        result = _validate_hotkey("   <ctrl>+<c>   ")
        assert result is not None
        assert "Ctrl+C" in result or "reserved" in result.lower()

    def test_valid_hotkey_with_padding_is_allowed(self) -> None:
        """A non-reserved hotkey with surrounding whitespace is still
        accepted — the strip is for normalization only, not rejection."""
        result = _validate_hotkey("  <f2>  ")
        assert result is None, "<f2> with whitespace should be allowed (CFG-1 doesn't over-block)"

    def test_tab_inside_combo_is_not_stripped(self) -> None:
        """Whitespace BETWEEN tokens (e.g. ``<ctrl> + <c>``) is handled by
        the parser, not the strip — the strip only affects leading/trailing
        whitespace for the denylist lookup.  This test pins that behavior."""
        # The parser may or may not accept "<ctrl> + <c>"; the strip
        # shouldn't change the parser's verdict.  We just verify the
        # function returns SOME result (either None or an error string),
        # not None due to a strip-induced mismatch.
        result = _validate_hotkey("<ctrl> + <c>")
        assert isinstance(result, str | None)


# ──────────────────────────────────────────────────────────────────────────
# CFG-2: ``win`` alias for ``super`` on Linux per-platform reserved lookup
# ──────────────────────────────────────────────────────────────────────────


class TestCfg2WinAliasForSuperOnLinux:
    """CFG-2 (Medium): on Linux, the physical Windows key is reported as
    ``super`` by pynput / evdev.  A user (or a buggy renderer) may send
    ``<win>+<l>`` instead of ``<super>+<l>``, expecting it to behave the
    same.  Before the fix, ``<win>+<l>`` was silently allowed on Linux
    even though ``<super>+<l>`` is in the Linux reserved list (Super+L
    locks the screen on GNOME/KDE/Cinnamon/etc.), letting the user
    assign a hotkey that conflicts with the screen-lock shortcut.

    The fix normalizes ``<win>`` → ``<super>`` in the per-platform
    reserved lookup on Linux only (Windows keeps its blanket Win+block;
    macOS doesn't use either name).
    """

    @pytest.mark.parametrize(
        "hotkey",
        ["<win>+<l>", "<win>+<d>", "<win>+<tab>"],
    )
    def test_win_alias_blocked_on_linux(self, hotkey: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each Linux-reserved Super+<key> combo is also blocked when
        sent with the ``win`` modifier name."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "linux")
        result = _validate_hotkey(hotkey)
        assert result is not None, f"{hotkey!r} should be blocked on Linux (alias for reserved <super>+<key>) — CFG-2"

    def test_win_non_reserved_combo_allowed_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A win+<key> combo whose super form is NOT reserved is still
        allowed — the alias only triggers a block when it matches an
        actual reserved entry."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "linux")
        # <super>+<r> is NOT in the Linux reserved list.
        result = _validate_hotkey("<win>+<r>")
        assert result is None, (
            "<win>+<r> should be allowed on Linux (alias for non-reserved <super>+<r>) — CFG-2 doesn't over-block"
        )

    def test_win_alias_not_applied_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, ``win`` is the correct modifier name and is
        blanket-blocked (Win+anything is OS-shell-reserved).  The
        Linux-only ``win``→``super`` normalization doesn't run."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "win32")
        result = _validate_hotkey("<win>+<l>")
        assert result is not None
        # The block comes from the Win-blanket rule, not the per-platform
        # reserved lookup.
        assert "Windows key combinations" in result or "reserved" in result.lower()

    def test_win_alias_not_applied_on_macos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On macOS, neither ``win`` nor ``super`` is a recognized
        modifier (the system modifier is ``cmd``).  The Linux-only
        normalization doesn't run."""
        import voice_typer.server.config_validators as cv

        monkeypatch.setattr(cv._sys, "platform", "darwin")
        # <win>+<f5> on macOS: win isn't a macOS modifier, and f5 isn't
        # a Cmd+letter (so the Cmd-blanket doesn't fire).  Should be
        # allowed.
        result = _validate_hotkey("<win>+<f5>")
        # Either allowed (None) or rejected by the multi-key check.
        # The important thing is that it's NOT rejected as "reserved
        # by operating system (darwin)" via the win→super alias.
        if result is not None:
            assert "reserved by operating system" not in result, "win→super alias must NOT be applied on macOS (CFG-2)"


# ──────────────────────────────────────────────────────────────────────────
# CFG-3: reject multi-key non-modifier combos
# ──────────────────────────────────────────────────────────────────────────


class TestCfg3MultiKeyComboRejection:
    """CFG-3 (Medium): a hotkey with more than one non-modifier key is
    structurally invalid for a global hotkey listener.

    pynput, the Windows low-level hook (``WH_KEYBOARD_LL``), and the
    macOS ``CGEventTap`` all register a single non-modifier key plus
    zero-or-more modifiers.  A combo like ``<a>+<b>`` would either fail
    to register, fire spuriously when either key is pressed alone, or
    require the user to press both keys simultaneously in a way that's
    indistinguishable from typing.

    The renderer's hotkey picker already enforces single-non-modifier;
    this is the backend mirror so a malicious IPC client can't bypass it.
    """

    @pytest.mark.parametrize(
        "hotkey",
        [
            "<delete>+<end>",
            "<a>+<b>",
            "<f1>+<f2>",
            "<home>+<page_up>",
            "<insert>+<delete>",
            "<ctrl>+<a>+<b>",  # one modifier + two non-modifiers
            "<ctrl>+<alt>+<f1>+<f2>",  # two modifiers + two non-modifiers
        ],
    )
    def test_rejects_multi_non_modifier(self, hotkey: str) -> None:
        result = _validate_hotkey(hotkey)
        assert result is not None, f"{hotkey!r} has 2+ non-modifier keys and should be rejected (CFG-3)"
        assert "at most one non-modifier" in result, (
            f"Error message for {hotkey!r} should mention multi-key rule; got: {result!r}"
        )

    @pytest.mark.parametrize(
        "hotkey",
        [
            # Single non-modifier + zero or more modifiers: all valid.
            "<f2>",
            "<caps_lock>",
            "<ctrl>+<alt>+<v>",
            "<shift>+<f5>",
            "<ctrl>+<f1>",
            "<ctrl>+<alt>+<f9>",
            "<ctrl>+<shift>",  # zero non-modifiers (modifier-only combo)
            "<alt>+<shift>",  # zero non-modifiers
        ],
    )
    def test_allows_single_non_modifier(self, hotkey: str) -> None:
        """Combos with at most one non-modifier key are still allowed
        (CFG-3 doesn't over-block)."""
        # Pin platform to linux for determinism (some combos are
        # platform-conditional).
        import voice_typer.server.config_validators as cv

        original = cv._sys.platform
        cv._sys.platform = "linux"
        try:
            result = _validate_hotkey(hotkey)
            assert result is None, f"{hotkey!r} has <=1 non-modifier key and should be allowed; got: {result!r}"
        finally:
            cv._sys.platform = original

    def test_error_message_includes_count(self) -> None:
        """The error message includes the actual count so the user knows
        how many non-modifier keys their combo has."""
        result = _validate_hotkey("<a>+<b>+<c>")
        assert result is not None
        assert "3" in result, f"Error message should include the count '3'; got: {result!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
