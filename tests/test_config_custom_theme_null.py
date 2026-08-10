"""FR-3 regression test: ``custom_theme: null`` is accepted by the IPC
validator and handled as "clear custom_theme" by Config.

Pre-fix, the IPC validator (``config_validators.py``) rejected
``custom_theme: None`` with ``"must be dict, got NoneType"`` because
the expected_type was the bare ``dict``. The renderer's ``useTheme.ts``
sends ``custom_theme: null`` when the user clicks "Clear custom theme /
revert to preset" — so the user's clear action silently failed (server
returned ``code: "invalid_field"`` while the local React state still
held the cleared theme). On next restart the stale custom_theme dict
reappeared.

Post-fix, the validator accepts ``None`` (returns None for None) and
the IPC allowlist's expected_type is widened to ``(dict, type(None))``
so the pre-validator type check passes for ``None``. Config.load()
already handled None (line ~1416: ``if "custom_theme" in data and
data["custom_theme"] is not None:``), and Config.save() serializes
None as JSON ``null`` — so the round-trip just works.

Platform note: validated ON LINUX (sandbox). The validator is pure
Python (no platform-specific code); Windows/macOS validation is
redundant but listed for completeness.
"""

from __future__ import annotations

import json
from pathlib import Path

from voice_typer.server.config import Config
from voice_typer.server.config_validators import (
    IPC_CONFIG_ALLOWLIST,
    _make_custom_theme_validator,
    validate_config_update,
)

VALID_CUSTOM_THEME: dict = {
    "light": {
        "--background": "#ffffff",
        "--foreground": "#000000",
        "--primary": "#0000ff",
        "--bg-subtle": "#f0f0f0",
        "--border": "#cccccc",
        "--text-muted": "#666666",
    },
    "dark": {
        "--background": "#000000",
        "--foreground": "#ffffff",
        "--primary": "#0000ff",
        "--bg-subtle": "#1a1a1a",
        "--border": "#333333",
        "--text-muted": "#999999",
    },
}


class TestValidatorAcceptsNone:
    """FR-3: ``_make_custom_theme_validator`` accepts None."""

    def test_validator_returns_none_for_none(self) -> None:
        """The validator must return ``None`` (success) for input
        ``None`` — not an error string."""
        validator = _make_custom_theme_validator()
        result = validator(None)
        assert result is None, (
            f"FR-3 regression: validator rejected None with {result!r}. "
            "The renderer's useTheme.ts sends custom_theme: null when "
            "the user clears the custom theme; the validator must "
            "accept None as the canonical 'clear / revert to preset' "
            "sentinel."
        )

    def test_validator_still_rejects_non_dict_non_none(self) -> None:
        """Non-dict, non-None values must still be rejected (e.g.
        integer, list, string)."""
        validator = _make_custom_theme_validator()
        for bad_value in (42, [1, 2, 3], "not a dict", 3.14):
            result = validator(bad_value)
            assert result is not None, (
                f"FR-3: validator accepted non-dict non-None value "
                f"{bad_value!r} — only dict and None should be accepted."
            )
            assert "must be a dict" in result, (
                f"FR-3: error message for {bad_value!r} should mention 'must be a dict', got {result!r}"
            )

    def test_validator_still_accepts_valid_dict(self) -> None:
        """A valid custom_theme dict must still pass — FR-3 only widens
        the accepted set, it doesn't loosen the dict-shape rules."""
        validator = _make_custom_theme_validator()
        result = validator(VALID_CUSTOM_THEME)
        assert result is None, f"FR-3: validator rejected a valid custom_theme dict with {result!r}"

    def test_validator_still_rejects_invalid_dict(self) -> None:
        """A dict missing required keys must still be rejected."""
        validator = _make_custom_theme_validator()
        bad_theme = {"light": {"--background": "#ffffff"}}  # missing keys
        result = validator(bad_theme)
        assert result is not None
        assert "must be a string" in result or "must be a dict" in result


class TestAllowlistAcceptsNone:
    """FR-3: the IPC_CONFIG_ALLOWLIST entry accepts None at the
    type-check stage (before the field validator runs)."""

    def test_allowlist_expected_type_is_tuple_including_none(self) -> None:
        """The expected_type for ``custom_theme`` must be a tuple that
        includes ``type(None)`` so the pre-validator type check passes
        for None. Pre-fix it was the bare ``dict``."""
        spec = IPC_CONFIG_ALLOWLIST["custom_theme"]
        expected_type = spec[0]
        assert isinstance(expected_type, tuple), (
            f"FR-3: expected_type for custom_theme should be a tuple "
            f"(dict, type(None)) so None passes the type check; got "
            f"{type(expected_type).__name__}"
        )
        assert dict in expected_type, f"FR-3: expected_type tuple must include dict; got {expected_type}"
        assert type(None) in expected_type, f"FR-3: expected_type tuple must include type(None); got {expected_type}"


class TestValidateConfigUpdateAcceptsNone:
    """FR-3 end-to-end: ``validate_config_update({'custom_theme': None})``
    succeeds and returns the value in the validated dict."""

    def test_validate_config_update_accepts_none_custom_theme(self) -> None:
        validated, errors = validate_config_update({"custom_theme": None})
        assert errors == [], (
            f"FR-3: validate_config_update rejected custom_theme=None "
            f"with errors {errors!r}. Pre-fix the rejection was "
            f"'field 'custom_theme' must be dict, got NoneType'."
        )
        assert "custom_theme" in validated
        assert validated["custom_theme"] is None

    def test_validate_config_update_still_accepts_valid_dict(self) -> None:
        validated, errors = validate_config_update({"custom_theme": VALID_CUSTOM_THEME})
        assert errors == []
        assert validated["custom_theme"] == VALID_CUSTOM_THEME

    def test_validate_config_update_still_rejects_int(self) -> None:
        """Non-dict non-None values must still be rejected by the type
        check (before the field validator even runs)."""
        validated, errors = validate_config_update({"custom_theme": 42})
        assert errors, (
            "FR-3: validate_config_update should still reject non-dict "
            "non-None values (e.g. int) at the type-check stage."
        )
        assert any("custom_theme" in e and "must be" in e for e in errors)
        assert "custom_theme" not in validated


class TestConfigRoundTripWithNone:
    """FR-3: Config.load() and Config.save() round-trip None correctly."""

    def test_save_load_round_trip_with_none(self, tmp_config_dir: Path) -> None:
        """Saving a Config with custom_theme=None, then loading it,
        must produce a Config with custom_theme=None (not a dict, not
        a stale value)."""

        # Construct a Config with a valid custom_theme, save it.
        cfg1 = Config()
        cfg1.custom_theme = VALID_CUSTOM_THEME
        cfg1.save()

        # Verify the on-disk JSON has the dict.
        on_disk = json.loads((tmp_config_dir / "config.json").read_text())
        assert on_disk["custom_theme"] == VALID_CUSTOM_THEME

        # Now simulate the user clearing the custom theme: load,
        # set custom_theme to None, save.
        cfg2 = Config.load()
        assert cfg2.custom_theme == VALID_CUSTOM_THEME
        cfg2.custom_theme = None
        cfg2.save()

        # On-disk JSON must have custom_theme: null.
        on_disk = json.loads((tmp_config_dir / "config.json").read_text())
        assert on_disk["custom_theme"] is None, (
            f"FR-3: after saving custom_theme=None, on-disk JSON should "
            f"have 'custom_theme: null'; got {on_disk.get('custom_theme')!r}"
        )

        # Reload — Config.custom_theme must be None.
        cfg3 = Config.load()
        assert cfg3.custom_theme is None

    def test_load_treats_null_as_clear(self, tmp_config_dir: Path) -> None:
        """A hand-edited config.json with ``"custom_theme": null`` must
        load as Config.custom_theme = None (no validation error, no
        reset warning)."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"custom_theme": None, "hotkey": "<caps_lock>"}))

        cfg = Config.load()
        assert cfg.custom_theme is None
        # No load warnings about custom_theme (the pre-fix
        # validator would have rejected None and added a warning).
        warnings = cfg.last_load_warnings or []
        assert not any("custom_theme" in w for w in warnings), (
            f"FR-3: Config.load() emitted unexpected custom_theme warnings for None value: {warnings}"
        )
