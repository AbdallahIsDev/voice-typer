"""YJ-24 regression test: non-string hotkey values are coerced to ``None``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from voice_typer.server.config_validators import (
    _check_cross_field_hotkey_conflicts,
    validate_config,
)


class TestNonStringHotkeyCoercion:
    """Pin the YJ-24 behaviour: non-string hotkeys become ``None``."""

    def test_int_hotkey_coerced_to_none(self) -> None:
        """``hotkey = 123`` (int from a hand-edited config.json) is"""
        cfg = SimpleNamespace(
            hotkey=123,  # type: ignore[assignment]
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        assert "field_values" in captured, "_check_cross_field_hotkey_conflicts was not invoked, patch failed"
        fv = captured["field_values"]
        assert fv["hotkey"] is None, f"expected hotkey coerced to None, got {fv['hotkey']!r}"
        # The legitimate string hotkey passes through unchanged.
        assert fv["repaste_hotkey"] == "<f6>"

    def test_bool_hotkey_coerced_to_none(self) -> None:
        """``hotkey = True`` (bool from a hand-edited config.json) is"""
        cfg = SimpleNamespace(
            hotkey=True,  # type: ignore[assignment]
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] is None, f"expected bool hotkey coerced to None, got {fv['hotkey']!r}"

    def test_list_hotkey_coerced_to_none(self) -> None:
        """multi-character array) is also coerced to ``None``."""
        cfg = SimpleNamespace(
            hotkey=["<ctrl>", "<space>"],  # type: ignore[assignment]
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] is None, f"expected list hotkey coerced to None, got {fv['hotkey']!r}"

    def test_all_hotkeys_non_string_does_not_raise(self) -> None:
        """If ALL hotkey fields are non-string (e.g. a corrupted"""
        cfg = SimpleNamespace(
            hotkey=123,  # type: ignore[assignment]
            repaste_hotkey=456,  # type: ignore[assignment]
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv == {
            "hotkey": None,
            "repaste_hotkey": None,
        }, f"expected all-None hotkey_values, got {fv!r}"

    def test_string_hotkey_not_coerced(self) -> None:
        """coercion only applies to non-string values. This guards against"""
        cfg = SimpleNamespace(
            hotkey="<f5>",
            repaste_hotkey="<f6>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] == "<f5>", f"string hotkey should pass through unchanged, got {fv['hotkey']!r}"

    def test_mixed_string_and_non_string_hotkeys(self) -> None:
        """same config: only the non-string ones are coerced."""
        cfg = SimpleNamespace(
            hotkey="<f5>",  # valid string
            repaste_hotkey=0,  # type: ignore[assignment]  # non-string (int 0)
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return []

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert isinstance(errors, list)
        fv = captured["field_values"]
        assert fv["hotkey"] == "<f5>", "string hotkey should pass through"
        assert fv["repaste_hotkey"] is None, f"int 0 hotkey should be coerced to None, got {fv['repaste_hotkey']!r}"

    def test_validate_config_returns_list_without_raising(self) -> None:
        """End-to-end: ``validate_config`` on a config with non-string"""
        cfg = SimpleNamespace(
            hotkey=123,  # type: ignore[assignment]
            repaste_hotkey=456,  # type: ignore[assignment]
        )
        errors = validate_config(cfg)
        assert isinstance(errors, list)
        # No conflict because the coerced None values are all skipped.
        assert not any("Hotkey conflict" in e for e in errors), f"unexpected hotkey conflict from None values: {errors}"

    def test_check_cross_field_helper_handles_none_directly(self) -> None:
        """Direct call to the real (un-patched)"""
        errors = _check_cross_field_hotkey_conflicts(
            {
                "hotkey": None,
                "repaste_hotkey": None,
            }
        )
        assert errors == [], f"expected no conflicts for all-None hotkeys, got {errors}"
