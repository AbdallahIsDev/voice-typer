"""Regression tests for the modifier-only-hotkey rejection in
:func:`voice_typer.server.config_validators.hotkey._check_multi_non_modifier`.

Pre-fix, Stage 5 of the per-field hotkey validator rejected combos with
**more than one** non-modifier key (``len(non_mods) > 1``) but silently
accepted combos with **zero** non-modifier keys
(``len(non_mods) == 0``). The latter is structurally invalid for every
global-hotkey backend the project ships:

* ``pynput``'s ``GlobalHotKeysListener`` matches a hotkey spec by
  watching for the non-modifier keypress while the listed modifiers are
  held — with no non-modifier key, the listener has nothing to match.
* Win32 ``RegisterHotKey`` requires a non-modifier virtual-key code as
  its ``vk`` argument; passing only modifiers is a silent no-op (the
  call returns ``True`` but the OS never fires ``WM_HOTKEY``).
* The macOS ``CGEventTap`` listener matches on the non-modifier key's
  keycode; a modifier-only combo produces a tap that never triggers.

A user (or a buggy renderer, or a malicious IPC client writing directly
to ``config.json``) who set ``"hotkey": "<ctrl>+<shift>"`` would see
"hotkey armed" in the UI and never be able to trigger dictation — a
silent registration failure with no diagnostic.

The fix tightens Stage 5 to ``if len(non_mods) != 1:`` so both
violations (zero AND more-than-one) are rejected at config-load time
with an actionable error message that includes the actual count.

These tests pin the new behaviour so a future refactor cannot silently
revert to the looser ``> 1`` check (which is the natural "minimal"
implementation and thus an easy regression target).

All tests run ON LINUX (sandbox). The fix spec mandates the four
regression cases listed below on Linux specifically; Windows / macOS
code paths are exercised by mocking ``sys.platform`` (the existing
``test_reserved_hotkeys.py`` and ``test_hotkey_validation.py`` use the
same pattern via ``cv._sys.platform = ...``).
"""

from __future__ import annotations

import sys

import pytest
from voice_typer.server.config_validators import _validate_hotkey
from voice_typer.server.config_validators.hotkey import (
    _HOTKEY_MODIFIERS,
    _check_multi_non_modifier,
    _parse_hotkey_parts,
)

# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def linux_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``sys.platform`` to ``"linux"`` for the duration of one test.

    The hotkey validator's platform-conditional branches (per-platform
    reserved-shortcut tables, Alt+Shift Windows block, Cmd+letter macOS
    block) read ``sys.platform`` transitively via
    :func:`voice_typer.server.platform_utils.is_windows` /
    :func:`is_macos`. Mutating the shared ``sys`` module via
    ``monkeypatch.setattr(sys, "platform", "linux")`` propagates to
    every consumer (the project's own tests use the equivalent
    ``cv._sys.platform = ...`` pattern, which mutates the same global).
    """
    monkeypatch.setattr(sys, "platform", "linux")


# ──────────────────────────────────────────────────────────────────────────
# the four mandated regression cases (FI-9 spec)
# ──────────────────────────────────────────────────────────────────────────


class TestModifierOnlyCombosRejectedOnLinux:
    """The four hotkey strings the fix spec calls out by name must be
    rejected on Linux after the fix.

    Pre-fix, all four had ``len(non_mods) == 0`` and passed Stage 5
    silently — they would then fail to register at runtime with no
    diagnostic. Post-fix, all four are rejected at config-load time
    with the ``"hotkey must have exactly one non-modifier key (got 0)"``
    error.
    """

    def test_ctrl_plus_shift_rejected_on_linux(self, linux_platform: None) -> None:
        """``<ctrl>+<shift>`` — two modifiers, zero non-modifiers."""
        result = _validate_hotkey("<ctrl>+<shift>")
        assert result is not None, (
            "<ctrl>+<shift> has zero non-modifier keys and must be rejected "
            "(pynput/RegisterHotKey/CGEventTap all require a non-modifier trigger key)"
        )
        assert "exactly one non-modifier" in result, (
            f"error should mention the exactly-one-non-modifier rule; got: {result!r}"
        )
        assert "(got 0)" in result, f"error should report the actual count (0); got: {result!r}"

    def test_bare_cmd_l_rejected_on_linux(self) -> None:
        """``<cmd_l>`` alone — a single modifier token, zero non-modifiers.

        Note: the canonical parser resolves ``cmd_l`` → ``cmd`` and
        deduplicates, so ``_parse_hotkey_parts("<cmd_l>")`` returns
        ``["cmd"]`` (one entry, still a modifier). The structural check
        sees ``len(non_mods) == 0`` and rejects. No platform pin is
        needed because the structural Stage 5 runs before any
        platform-conditional stage and does not consult ``sys.platform``.
        """
        result = _validate_hotkey("<cmd_l>")
        assert result is not None, "<cmd_l> alone has zero non-modifier keys and must be rejected"
        assert "exactly one non-modifier" in result, (
            f"error should mention the exactly-one-non-modifier rule; got: {result!r}"
        )
        assert "(got 0)" in result, f"error should report the actual count (0); got: {result!r}"

    def test_cmd_l_plus_cmd_r_rejected_on_linux(self) -> None:
        """``<cmd_l>+<cmd_r>`` — two modifier tokens that canonicalize
        to the same ``cmd``, zero non-modifiers.

        This case is subtle: the user typed two distinct physical keys,
        but the canonical parser deduplicates both to ``cmd`` (the
        shared canonical modifier name), yielding ``parts == ["cmd"]``
        with ``len(non_mods) == 0``. Without the fix this would pass
        validation AND the renderer-side check (which also uses the
        canonical parser), producing a config that loads cleanly but
        cannot be armed.
        """
        result = _validate_hotkey("<cmd_l>+<cmd_r>")
        assert result is not None, "<cmd_l>+<cmd_r> canonicalizes to a single modifier and must be rejected"
        assert "exactly one non-modifier" in result, (
            f"error should mention the exactly-one-non-modifier rule; got: {result!r}"
        )
        assert "(got 0)" in result, f"error should report the actual count (0); got: {result!r}"

    def test_alt_plus_shift_rejected_on_linux(self, linux_platform: None) -> None:
        """``<alt>+<shift>`` — two modifiers, zero non-modifiers.

        On Windows this is additionally blocked by Stage 7 (Alt+Shift
        language switching), but on Linux Stage 7 is a no-op. Without
        the Stage 5 fix, Linux would silently accept this combo. The
        platform pin to ``linux`` is deliberate: it asserts the
        rejection comes from Stage 5 (the structural rule), NOT from
        the Windows-only Stage 7 Alt+Shift block.
        """
        result = _validate_hotkey("<alt>+<shift>")
        assert result is not None, "<alt>+<shift> has zero non-modifier keys and must be rejected on Linux"
        # The Stage 5 message — NOT the Stage 7 "Alt+Shift is reserved
        # by Windows" message — must be the one returned on Linux.
        assert "exactly one non-modifier" in result, (
            f"on Linux the rejection must come from the structural Stage 5 rule, "
            f"not the Windows-only Stage 7 Alt+Shift block; got: {result!r}"
        )
        assert "language switching" not in result, (
            f"Stage 7 (Windows-only) message leaked through on Linux; got: {result!r}"
        )
        assert "(got 0)" in result, f"error should report the actual count (0); got: {result!r}"


# ──────────────────────────────────────────────────────────────────────────
# positive controls — exactly one non-modifier is still allowed
# ──────────────────────────────────────────────────────────────────────────


class TestExactlyOneNonModifierStillAllowed:
    """Guards against an over-blocking regression where the tightened
    ``!= 1`` check accidentally rejects valid combos.

    The fix changes ``> 1`` to ``!= 1``, which means a typo like
    ``== 0`` or ``>= 1`` would silently reject every valid hotkey. The
    parametrize below covers the full allow-surface: a bare
    non-modifier, a non-modifier plus one modifier, and a non-modifier
    plus two modifiers.
    """

    @pytest.mark.parametrize(
        "hotkey",
        [
            # bare non-modifier keys (no modifiers) — chosen to avoid the
            # universal reserved-shortcut list (which includes <space>,
            # <tab>, <enter>, <backspace>) and the per-letter reserved
            # app-shortcut rules.
            "<f5>",
            "<f6>",
            "<f7>",
            "<caps_lock>",
            "<delete>",
            "<insert>",
            "<home>",
            "<end>",
            "<page_up>",
            "<page_down>",
            # non-modifier + one modifier
            "<ctrl>+<q>",  # Ctrl+Q is NOT in _BLOCKED_CTRL_LETTERS
            "<alt>+<q>",
            "<alt>+<r>",
            "<shift>+<f5>",
            "<ctrl>+<f1>",
            "<cmd>+<f3>",
            # non-modifier + two modifiers (Stage 8 / Stage 9 only block
            # PURE Ctrl+<letter> / PURE Shift+<letter>; adding a second
            # modifier takes the combo out of those blanket rules)
            "<ctrl>+<alt>+<v>",
            "<ctrl>+<alt>+<u>",
            "<ctrl>+<shift>+<f1>",
            "<alt>+<shift>+<f9>",
        ],
    )
    def test_exactly_one_non_modifier_allowed_on_linux(self, hotkey: str, linux_platform: None) -> None:
        result = _validate_hotkey(hotkey)
        # Compute non-mod count for the assertion message.
        parts = _parse_hotkey_parts(hotkey)
        non_mods = [p for p in parts if p not in _HOTKEY_MODIFIERS]
        assert len(non_mods) == 1, (
            f"test-data invariant: {hotkey!r} should have exactly 1 non-modifier "
            f"for the positive-control parametrize, got {len(non_mods)} ({non_mods})"
        )
        assert result is None, f"{hotkey!r} has exactly one non-modifier key and must be allowed; got: {result!r}"


# ──────────────────────────────────────────────────────────────────────────
# negative control — more-than-one non-modifier is still rejected
# ──────────────────────────────────────────────────────────────────────────


class TestMoreThanOneNonModifierStillRejected:
    """Guards against a regression where the tightened check
    accidentally lets the >1 case through (e.g. a typo to ``== 0``
    would reject zero but accept two-or-more).
    """

    @pytest.mark.parametrize(
        "hotkey, expected_count",
        [
            ("<a>+<b>", 2),
            ("<f1>+<f2>", 2),
            ("<delete>+<end>", 2),
            ("<home>+<page_up>", 2),
            ("<a>+<b>+<c>", 3),
            ("<ctrl>+<a>+<b>", 2),  # one modifier + two non-modifiers
            ("<ctrl>+<alt>+<f1>+<f2>", 2),  # two modifiers + two non-modifiers
        ],
    )
    def test_multi_non_modifier_rejected(self, hotkey: str, expected_count: int) -> None:
        result = _validate_hotkey(hotkey)
        assert result is not None, f"{hotkey!r} has {expected_count} non-modifier keys and must be rejected"
        assert "exactly one non-modifier" in result, (
            f"error should mention the exactly-one-non-modifier rule; got: {result!r}"
        )
        assert f"(got {expected_count})" in result, (
            f"error should report the actual count ({expected_count}); got: {result!r}"
        )


# ──────────────────────────────────────────────────────────────────────────
# direct unit test for the stage helper (white-box)
# ──────────────────────────────────────────────────────────────────────────


class TestCheckMultiNonModifierHelperContract:
    """Direct unit tests for the Stage 5 helper.

    The orchestrator (``_validate_hotkey``) calls Stage 5 with
    ``_parse_hotkey_parts(value)`` as input. Calling the helper
    directly lets us assert the ``!= 1`` boundary precisely — including
    the empty-list edge case that the public validator never reaches
    (the orchestrator returns ``"hotkey has no keys"`` BEFORE Stage 5
    when ``parts`` is empty), but which the helper itself must still
    handle correctly so a future refactor that reorders the stages
    doesn't silently break the zero-non-modifier gate.
    """

    def test_zero_non_modifiers_returns_error(self) -> None:
        # parts == all-modifiers (zero non-mods) — the FI-9 case.
        assert _check_multi_non_modifier(["ctrl", "shift"]) is not None
        assert _check_multi_non_modifier(["cmd"]) is not None
        assert _check_multi_non_modifier(["alt", "shift"]) is not None
        assert _check_multi_non_modifier([]) is not None  # empty list edge case

    def test_one_non_modifier_returns_none(self) -> None:
        assert _check_multi_non_modifier(["v"]) is None
        assert _check_multi_non_modifier(["ctrl", "v"]) is None
        assert _check_multi_non_modifier(["ctrl", "alt", "v"]) is None
        assert _check_multi_non_modifier(["shift", "f5"]) is None

    def test_two_non_modifiers_returns_error(self) -> None:
        assert _check_multi_non_modifier(["a", "b"]) is not None
        assert _check_multi_non_modifier(["ctrl", "a", "b"]) is not None
        assert _check_multi_non_modifier(["a", "b", "c"]) is not None

    def test_error_message_includes_count_for_zero(self) -> None:
        result = _check_multi_non_modifier(["ctrl", "shift"])
        assert result is not None
        assert "got 0" in result, f"zero-non-mod error should include 'got 0'; got: {result!r}"

    def test_error_message_includes_count_for_two(self) -> None:
        result = _check_multi_non_modifier(["a", "b"])
        assert result is not None
        assert "got 2" in result, f"two-non-mod error should include 'got 2'; got: {result!r}"

    def test_error_message_includes_count_for_three(self) -> None:
        result = _check_multi_non_modifier(["a", "b", "c"])
        assert result is not None
        assert "got 3" in result, f"three-non-mod error should include 'got 3'; got: {result!r}"


# ──────────────────────────────────────────────────────────────────────────
# integration: the rejection surfaces through validate_config_update
# ──────────────────────────────────────────────────────────────────────────


class TestModifierOnlyRejectedViaIpc:
    """End-to-end: a modifier-only hotkey set via the IPC config-update
    path is rejected with the structural error (not silently accepted).

    The IPC allowlist routes ``hotkey`` and ``repaste_hotkey`` through
    ``_validate_hotkey``; a modifier-only value must produce a
    per-field error and must NOT appear in the validated payload (so
    the dispatcher treats the update atomically and refuses to apply
    it).
    """

    def test_ctrl_plus_shift_rejected_in_validate_config_update(self, linux_platform: None) -> None:
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<ctrl>+<shift>"})
        # The per-field validator rejected it, so it must NOT be in
        # the validated payload.
        assert "hotkey" not in validated, (
            f"modifier-only hotkey must not appear in validated payload; got: {validated!r}"
        )
        # At least one error must mention the structural rule.
        structural_errors = [e for e in errors if "exactly one non-modifier" in e]
        assert structural_errors, f"expected a structural error mentioning 'exactly one non-modifier'; got: {errors!r}"

    def test_cmd_l_alone_rejected_in_validate_config_update(self) -> None:
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<cmd_l>"})
        assert "hotkey" not in validated, (
            f"modifier-only hotkey must not appear in validated payload; got: {validated!r}"
        )
        structural_errors = [e for e in errors if "exactly one non-modifier" in e]
        assert structural_errors, f"expected a structural error mentioning 'exactly one non-modifier'; got: {errors!r}"
