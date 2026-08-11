"""Regression tests for modifier-only-hotkey handling in
:func:`voice_typer.server.config_validators.hotkey._check_multi_non_modifier`.

HISTORY (why these tests flip-flopped): Stage 5 originally rejected
combos with more than one non-modifier key (``len(non_mods) > 1``) and
silently accepted zero-non-modifier combos. An older fix tightened it
to ``!= 1`` on the (then-true) premise that "no listener backend can
register a modifier-only hotkey" — the pynput/RegisterHotKey/CGEventTap
listeners all require a concrete trigger key. Since then the runtime
gained first-class modifier-only support:

* the native backends (``windows-key-listener.exe``, Linux evdev,
  macOS) fire on modifier RELEASE via ``_run_modifier_only_polling_loop``
  (``voice_typer/server/hotkeys/windows/polling_strategy.py``), and
  ``WindowsNativeHotkey`` (the polling fallback) does the same;
* the frontend ``validateHotkey`` (``hotkey-validation.ts`` rule 5)
  explicitly ACCEPTS pure-modifier combos — "they're valid modifier-only
  release triggers in the native backends" — and ``HotkeyPicker``
  commits them (``useHotkeyCapture.ts`` ``commitModifierOnlyRef``).

The backend validator was the odd layer out: it rejected modifier-only
hotkeys at ``set_config`` time, so a user who captured ``<ctrl>+<shift>``
(or a bare ``<alt>``) in the renderer got "hotkey must have exactly one
non-modifier key (got 0)" and could never save it — a cross-layer
contract violation visible in real logs as repeated ``set_config
rejected`` WARNINGs. Stage 5 now allows ZERO non-modifier keys (as long
as the combo has at least one modifier) and rejects only MORE than one
non-modifier key.

These tests pin the CURRENT contract so a future refactor cannot
silently revert to the ``!= 1`` rejection (which contradicts the
frontend + runtime) NOR loosen the ``> 1`` structural gate.

All tests run ON LINUX (sandbox). The Windows / macOS code paths are
exercised by mocking ``sys.platform`` (the existing
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
    """Pin ``sys.platform`` to ``\"linux\"`` for the duration of one test.

    The hotkey validator's platform-conditional branches (per-platform
    reserved-shortcut tables, Alt+Shift Windows block, Cmd+letter macOS
    block) read ``sys.platform`` transitively via
    :func:`voice_typer.server.platform_utils.is_windows` /
    :func:`is_macos`. Mutating the shared ``sys`` module via
    ``monkeypatch.setattr(sys, \"platform\", \"linux\")`` propagates to
    every consumer (the project's own tests use the equivalent
    ``cv._sys.platform = ...`` pattern, which mutates the same global).
    """
    monkeypatch.setattr(sys, "platform", "linux")


@pytest.fixture
def windows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``sys.platform`` to ``\"win32\"`` for one test."""
    monkeypatch.setattr(sys, "platform", "win32")


def _assert_allowed(hotkey: str) -> None:
    """Assert ``_validate_hotkey`` accepts ``hotkey`` with a clear message."""
    result = _validate_hotkey(hotkey)
    assert result is None, (
        f"{hotkey!r} must be allowed — the frontend validateHotkey and the "
        f"native/polling backends support modifier-only release triggers; "
        f"got: {result!r}"
    )


# ──────────────────────────────────────────────────────────────────────────
# modifier-only combos are ALLOWED (the current contract)
# ──────────────────────────────────────────────────────────────────────────


class TestModifierOnlyCombosAllowed:
    """Modifier-only hotkeys (zero non-modifier keys) are valid release
    triggers and must be accepted by the backend validator — matching the
    frontend ``validateHotkey`` (rule 5) and the runtime backends'
    modifier-only polling loop.

    This is the regression surface for the log signature
    ``set_config rejected: field 'hotkey' hotkey must have exactly one
    non-modifier key (got 0)`` that users hit when the renderer
    committed a modifier-only capture and the backend refused it.
    """

    def test_ctrl_plus_shift_allowed(self, linux_platform: None) -> None:
        """``<ctrl>+<shift>`` — two modifiers, zero non-modifiers."""
        _assert_allowed("<ctrl>+<shift>")

    def test_ctrl_plus_alt_allowed(self, linux_platform: None) -> None:
        """``<ctrl>+<alt>`` — two modifiers, zero non-modifiers."""
        _assert_allowed("<ctrl>+<alt>")

    def test_alt_plus_shift_allowed_on_linux(self, linux_platform: None) -> None:
        """``<alt>+<shift>`` is allowed on Linux (modifier-only release
        trigger). On Windows it stays blocked by Stage 7 (language
        switching) — covered in ``TestAltShiftStillBlockedOnWindows``."""
        _assert_allowed("<alt>+<shift>")

    def test_bare_alt_allowed(self, linux_platform: None) -> None:
        """``<alt>`` alone — a single modifier as a release trigger."""
        _assert_allowed("<alt>")

    def test_bare_ctrl_allowed(self, linux_platform: None) -> None:
        """``<ctrl>`` alone."""
        _assert_allowed("<ctrl>")

    def test_bare_shift_allowed(self, linux_platform: None) -> None:
        """``<shift>`` alone — the frontend pins this exact case
        (``hotkey-validation.test.ts``: \"Shift alone is a valid
        single-key trigger\")."""
        _assert_allowed("<shift>")

    def test_bare_cmd_l_allowed(self, linux_platform: None) -> None:
        """``<cmd_l>`` alone canonicalizes to ``cmd`` (a modifier) — a
        valid modifier-only trigger. (The canonical parser resolves
        ``cmd_l`` → ``cmd``, so ``_parse_hotkey_parts`` returns
        ``[\"cmd\"]``.)"""
        _assert_allowed("<cmd_l>")

    def test_cmd_l_plus_cmd_r_allowed(self, linux_platform: None) -> None:
        """``<cmd_l>+<cmd_r>`` — both canonicalize to ``cmd``, yielding
        parts == ``[\"cmd\"]`` (one modifier, zero non-modifiers)."""
        _assert_allowed("<cmd_l>+<cmd_r>")


class TestBareShellModifiersStillBlocked:
    """Bare shell-modifier keys remain blocked by Stage 2 (the universal
    reserved list), NOT by Stage 5 — relaxing the zero-non-modifier rule
    must not let ``<win>`` / ``<cmd>`` / ``<super>`` through."""

    @pytest.mark.parametrize("hotkey", ["<win>", "<cmd>", "<super>"])
    def test_bare_shell_modifier_still_blocked(self, hotkey: str, linux_platform: None) -> None:
        result = _validate_hotkey(hotkey)
        assert result is not None, (
            f"{hotkey!r} is a bare shell modifier (Win opens Start, Cmd is a "
            f"macOS system gesture, Super is the Linux shell key) and must "
            f"stay blocked; got: {result!r}"
        )
        assert "reserved" in result.lower(), (
            f"the rejection should come from the reserved-shortcut rule; got: {result!r}"
        )


class TestAltShiftStillBlockedOnWindows:
    """Stage 7 (Alt+Shift = Windows language switching) still applies —
    relaxing Stage 5 must not bypass it."""

    def test_alt_plus_shift_blocked_on_windows(self, windows_platform: None) -> None:
        result = _validate_hotkey("<alt>+<shift>")
        assert result is not None, "<alt>+<shift> must stay blocked on Windows (language switching)"
        # Either Stage 3 (win32 per-platform reserved lists
        # ``<alt>+<shift>``) or Stage 7 (language-switching rule) fires
        # first — both reject, and the message is reserved-related.
        assert "reserved" in result.lower() or "language switching" in result, (
            f"the Windows rejection must be a reserved/language-switching message; got: {result!r}"
        )


# ──────────────────────────────────────────────────────────────────────────
# positive controls — exactly one non-modifier is still allowed
# ──────────────────────────────────────────────────────────────────────────


class TestExactlyOneNonModifierStillAllowed:
    """Guards against an over-blocking regression where the Stage 5
    change accidentally rejects valid combos."""

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
    """Guards against the Stage 5 gate being loosened past ``> 1`` (e.g.
    a typo to ``>= 0`` would accept every structurally-invalid combo)."""

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

    Calling the helper directly lets us assert the ``> 1`` boundary
    precisely — including the empty-list edge case that the public
    validator never reaches (the orchestrator returns ``\"hotkey has no
    keys\"`` BEFORE Stage 5 when ``parts`` is empty), but which the
    helper itself must still handle correctly so a future refactor that
    reorders the stages doesn't silently drop the zero-length gate.
    """

    def test_zero_non_modifiers_returns_none(self) -> None:
        # parts == all-modifiers (zero non-mods) — the modifier-only case.
        assert _check_multi_non_modifier(["ctrl", "shift"]) is None
        assert _check_multi_non_modifier(["cmd"]) is None
        assert _check_multi_non_modifier(["alt", "shift"]) is None
        assert _check_multi_non_modifier(["alt"]) is None

    def test_empty_list_returns_error(self) -> None:
        # empty list edge case — no modifiers AND no keys is meaningless.
        assert _check_multi_non_modifier([]) is not None

    def test_one_non_modifier_returns_none(self) -> None:
        assert _check_multi_non_modifier(["v"]) is None
        assert _check_multi_non_modifier(["ctrl", "v"]) is None
        assert _check_multi_non_modifier(["ctrl", "alt", "v"]) is None
        assert _check_multi_non_modifier(["shift", "f5"]) is None

    def test_two_non_modifiers_returns_error(self) -> None:
        assert _check_multi_non_modifier(["a", "b"]) is not None
        assert _check_multi_non_modifier(["ctrl", "a", "b"]) is not None
        assert _check_multi_non_modifier(["a", "b", "c"]) is not None

    def test_error_message_includes_count_for_two(self) -> None:
        result = _check_multi_non_modifier(["a", "b"])
        assert result is not None
        assert "got 2" in result, f"two-non-mod error should include 'got 2'; got: {result!r}"

    def test_error_message_includes_count_for_three(self) -> None:
        result = _check_multi_non_modifier(["a", "b", "c"])
        assert result is not None
        assert "got 3" in result, f"three-non-mod error should include 'got 3'; got: {result!r}"


# ──────────────────────────────────────────────────────────────────────────
# integration: modifier-only passes through the IPC config-update path
# ──────────────────────────────────────────────────────────────────────────


class TestModifierOnlyAcceptedViaIpc:
    """End-to-end: a modifier-only hotkey set via the IPC config-update
    path is ACCEPTED (it lands in the validated payload with no errors).

    Regression: pre-fix the backend rejected it (``got 0``), so the
    renderer's committed capture could never be saved — the user saw
    repeated ``set_config rejected`` WARNINGs in the log.
    """

    def test_ctrl_plus_shift_accepted_in_validate_config_update(self, linux_platform: None) -> None:
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<ctrl>+<shift>"})
        # The per-field validator accepted it, so it MUST be in the
        # validated payload.
        assert validated.get("hotkey") == "<ctrl>+<shift>", (
            f"modifier-only hotkey must land in the validated payload; got: {validated!r}"
        )
        assert not errors, f"expected no validation errors for a modifier-only hotkey; got: {errors!r}"

    def test_bare_alt_accepted_in_validate_config_update(self, linux_platform: None) -> None:
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<alt>"})
        assert validated.get("hotkey") == "<alt>", (
            f"bare modifier must land in the validated payload; got: {validated!r}"
        )
        assert not errors, f"expected no validation errors for a bare modifier; got: {errors!r}"

    def test_win_combo_still_rejected_in_validate_config_update(self, windows_platform: None) -> None:
        """The relaxation must NOT open the door for shell-reserved
        combos — ``<win>+<e>`` is still rejected at the IPC boundary."""
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<win>+<e>"})
        assert "hotkey" not in validated, (
            f"shell-reserved hotkey must not appear in validated payload; got: {validated!r}"
        )
        assert errors, f"expected a validation error for <win>+<e>; got: {errors!r}"
