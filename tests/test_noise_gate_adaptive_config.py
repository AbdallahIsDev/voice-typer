"""Focused tests for the ``noise_filter_gate_adaptive`` config knob."""

from __future__ import annotations

import pytest
from voice_typer.server.config._schema import _ConfigSchema
from voice_typer.server.config_validators import (
    IPC_CONFIG_ALLOWLIST,
    validate_config_update,
)


class TestAdaptiveGateSchema:
    def test_dataclass_declares_bool_default_off(self) -> None:
        field = _ConfigSchema.__dataclass_fields__["noise_filter_gate_adaptive"]
        assert field.default is False
        assert "bool" in str(field.type)


class TestAdaptiveGateInConfigSignature:
    """``rebuild_from_config``'s short-circuit must see the field."""

    def test_field_listed_in_config_signature_fields(self) -> None:
        from voice_typer.server.audio_processor import _CONFIG_SIGNATURE_FIELDS

        assert "noise_filter_gate_adaptive" in _CONFIG_SIGNATURE_FIELDS, (
            "noise_filter_gate_adaptive is missing from "
            "_CONFIG_SIGNATURE_FIELDS, an adaptive-only config change "
            "keeps the old signature and the chain is NOT rebuilt."
        )

    def test_adaptive_only_change_flips_signature(self) -> None:
        """different signatures (the pre-fix behavior: identical signature"""
        from voice_typer.server.audio_processor import _config_signature

        cfg_off = _ConfigSchema()
        cfg_on = _ConfigSchema()
        cfg_on.noise_filter_gate_adaptive = True

        sig_off = _config_signature(cfg_off, 16000)
        sig_on = _config_signature(cfg_on, 16000)

        assert sig_off != sig_on, (
            "adaptive-only change produced an identical config signature, rebuild_from_config would skip the rebuild."
        )
        # Stability: the same config must keep hashing to the same value.
        assert _config_signature(cfg_off, 16000) == sig_off

    def test_adaptive_only_change_triggers_chain_rebuild(self) -> None:
        """
        End-to-end short-circuit behavior: rebuilding with a config
        ``build_chain`` (the unchanged-config rebuild must not).
        """
        from unittest.mock import patch

        import voice_typer.server.audio_processor as ap_mod
        from voice_typer.server.audio_processor import AudioProcessor

        cfg_off = _ConfigSchema()
        cfg_on = _ConfigSchema()
        cfg_on.noise_filter_gate_adaptive = True

        with patch.object(ap_mod, "build_chain", wraps=ap_mod.build_chain) as bc:
            proc = AudioProcessor(cfg_off, 16000, quiet=True)
            baseline = bc.call_count
            assert baseline >= 1, "AudioProcessor construction must build the chain"

            # Unchanged config → short-circuit, no additional build.
            proc.rebuild_from_config(cfg_off)
            assert bc.call_count == baseline, (
                "rebuild_from_config rebuilt for an UNCHANGED config, the short-circuit is broken."
            )

            # Adaptive-only change → must NOT short-circuit.
            proc.rebuild_from_config(cfg_on)
            assert bc.call_count == baseline + 1, (
                "rebuild_from_config skipped the rebuild for an "
                "adaptive-only config change, the field is missing from "
                "the config signature."
            )


class TestAdaptiveGateAllowlistEntry:
    def test_field_is_allowlisted(self) -> None:
        assert "noise_filter_gate_adaptive" in IPC_CONFIG_ALLOWLIST
        expected_type, _validator = IPC_CONFIG_ALLOWLIST["noise_filter_gate_adaptive"]
        assert expected_type is bool

    @pytest.mark.parametrize("value", [True, False])
    def test_valid_bools_accepted(self, value: bool) -> None:
        _, validator = IPC_CONFIG_ALLOWLIST["noise_filter_gate_adaptive"]
        assert validator(value) in (None, [])

    @pytest.mark.parametrize("value", ["true", 1, 1.0, None])
    def test_wrong_type_rejected(self, value: object) -> None:
        _, validator = IPC_CONFIG_ALLOWLIST["noise_filter_gate_adaptive"]
        assert validator(value) not in (None, [])


class TestAdaptiveGateValidateConfigUpdate:
    def test_valid_value_round_trips(self) -> None:
        validated, errors = validate_config_update({"noise_filter_gate_adaptive": True})
        assert errors == []
        assert validated == {"noise_filter_gate_adaptive": True}

    def test_non_bool_reports_error(self) -> None:
        validated, errors = validate_config_update({"noise_filter_gate_adaptive": "yes"})
        assert validated == {}
        assert len(errors) == 1
        assert "noise_filter_gate_adaptive" in errors[0]
