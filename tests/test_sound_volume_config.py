"""Focused tests for the ``sound_volume`` config field (SEC-002 contract).

Covers the three sides of the field's contract:

1. Schema — the Python ``Config`` dataclass declares
   ``sound_volume: float = 1.0`` (the pre-feature cue level).
2. Allowlist — ``IPC_CONFIG_ALLOWLIST["sound_volume"]`` accepts floats
   in [0.0, 1.0] and rejects out-of-range / wrong-type values at the
   IPC ``set_config`` boundary.
3. Round-trip — ``validate_config_update`` validates the field exactly
   like the dispatcher will use it.
"""

from __future__ import annotations

import pytest
from voice_typer.server.config._schema import _ConfigSchema
from voice_typer.server.config_validators import (
    IPC_CONFIG_ALLOWLIST,
    validate_config_update,
)


class TestSoundVolumeSchema:
    def test_dataclass_default_is_unity(self) -> None:
        field = _ConfigSchema.__dataclass_fields__["sound_volume"]
        assert field.default == 1.0
        assert field.type in {"float", "sound_volume: float"} or "float" in str(field.type)


class TestSoundVolumeAllowlistEntry:
    def test_field_is_allowlisted(self) -> None:
        assert "sound_volume" in IPC_CONFIG_ALLOWLIST
        expected_type, validator = IPC_CONFIG_ALLOWLIST["sound_volume"]
        assert float in (expected_type if isinstance(expected_type, tuple) else (expected_type,))

    @pytest.mark.parametrize("value", [0.0, 0.25, 0.5, 1.0])
    def test_valid_range_accepted(self, value: float) -> None:
        _, validator = IPC_CONFIG_ALLOWLIST["sound_volume"]
        assert validator(value) in (None, [])

    @pytest.mark.parametrize("value", [-0.1, 1.01, 2.0, 100.0])
    def test_out_of_range_rejected(self, value: float) -> None:
        _, validator = IPC_CONFIG_ALLOWLIST["sound_volume"]
        assert validator(value) not in (None, [])

    @pytest.mark.parametrize("value", ["0.5", True, None])
    def test_wrong_type_rejected(self, value: object) -> None:
        _, validator = IPC_CONFIG_ALLOWLIST["sound_volume"]
        assert validator(value) not in (None, [])


class TestSoundVolumeValidateConfigUpdate:
    def test_valid_value_round_trips(self) -> None:
        validated, errors = validate_config_update({"sound_volume": 0.4})
        assert errors == []
        assert validated == {"sound_volume": 0.4}

    def test_out_of_range_value_reports_error(self) -> None:
        validated, errors = validate_config_update({"sound_volume": 1.5})
        assert validated == {}
        assert len(errors) == 1
        assert "sound_volume" in errors[0]

    def test_unrelated_payload_unchanged(self) -> None:
        validated, errors = validate_config_update({"sound_feedback_enabled": False, "sound_volume": 0.0})
        assert errors == []
        assert validated == {
            "sound_feedback_enabled": False,
            "sound_volume": 0.0,
        }
