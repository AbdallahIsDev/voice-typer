"""Tests for config load/save and field behavior."""

import json

from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config, _default_hotkey_for_platform

EXPECTED_DEFAULT_HOTKEY = _default_hotkey_for_platform()


class TestConfigSchemaVersion:
    """No config schema versioning."""

    def test_config_has_schema_version_field(self):

        c = Config()
        assert hasattr(c, "schema_version")
        assert c.schema_version == _CURRENT_SCHEMA_VERSION

    def test_config_save_load_preserves_schema_version(self, tmp_path, tmp_config_dir):

        c = Config()
        c.save()
        loaded = Config.load()
        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION

    def test_config_migration_from_version_0(self, tmp_path, tmp_config_dir):

        from voice_typer.server.config import Config

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f3>", "model_size": "tiny"}))
        loaded = Config.load()
        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION
        assert loaded.hotkey == "<f3>"


class TestCfg8DeprecatedFieldsRemoved:
    """the deprecated noise-filter and volume-duck fields"""

    DEPRECATED_REMOVED = [
        "noise_filter_enabled",
        "noise_filter_gate_threshold",
        "noise_filter_rnnoise",
        "noise_filter_post_capture",
        "volume_duck_per_session",
        "volume_duck_smart",
        "silence_rms_threshold",
        "silence_peak_threshold",
        "normalize_audio",
        "normalize_target_peak",
        "push_to_talk_hotkey",
    ]

    NON_DEPRECATED_KEPT = [
        # Non-deprecated noise-filter fields still present.
        "noise_filter_highpass",
        "noise_filter_highpass_cutoff_hz",
        "noise_filter_gate",
        "noise_filter_gate_hold_ms",
        "noise_suppression_method",
        "noise_filter_gate_open_threshold_db",
        "noise_filter_eq",
        "noise_filter_compressor",
        "noise_filter_limiter",
        "noise_filter_notch",
        # Non-deprecated volume-duck fields still present.
        "volume_duck_enabled",
        "volume_duck_level",
        "volume_duck_fade_ms",
        "volume_duck_smart_poll_interval_ms",
    ]

    def test_deprecated_fields_absent_from_allowlist(self):
        """Each deprecated field is NO LONGER in IPC_CONFIG_ALLOWLIST."""
        from voice_typer.server.config import IPC_CONFIG_ALLOWLIST

        for field in self.DEPRECATED_REMOVED:
            assert field not in IPC_CONFIG_ALLOWLIST, (
                f"Deprecated field {field!r} should be removed from IPC_CONFIG_ALLOWLIST (CFG-8)"
            )

    def test_non_deprecated_fields_still_present(self):
        """Sanity: the non-deprecated filter/duck fields ARE still in"""
        from voice_typer.server.config import IPC_CONFIG_ALLOWLIST

        for field in self.NON_DEPRECATED_KEPT:
            assert field in IPC_CONFIG_ALLOWLIST, (
                f"Non-deprecated field {field!r} should still be in IPC_CONFIG_ALLOWLIST (CFG-8 doesn't over-prune)"
            )

    def test_deprecated_fields_silently_dropped_by_validate_config_update(self):
        """
        Setting a deprecated field via ``set_config`` is silently
        ``test_ignores_unknown_fields_without_crashing`` contract.
        """
        from voice_typer.server.config import validate_config_update

        payload = {field: True for field in self.DEPRECATED_REMOVED}
        # Use float for the threshold field (the others are bool).
        payload["noise_filter_gate_threshold"] = 0.05
        validated, errors = validate_config_update(payload)
        # No errors, deprecated fields are silently dropped, not rejected.
        assert errors == [], f"Deprecated fields should be silently dropped, not raise errors; got: {errors}"
        # None of the deprecated fields appear in validated.
        for field in self.DEPRECATED_REMOVED:
            assert field not in validated

    def test_deprecated_and_valid_fields_in_same_payload(self):
        """A payload with both deprecated and valid fields: valid fields"""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "noise_filter_enabled": True,  # deprecated, dropped
                "volume_duck_smart": False,  # deprecated, dropped
                "noise_filter_highpass": False,  # valid, applied
                "volume_duck_enabled": True,  # valid, applied
                "hotkey": "<f4>",  # valid, applied
            }
        )
        assert errors == []
        assert validated == {
            "noise_filter_highpass": False,
            "volume_duck_enabled": True,
            "hotkey": "<f4>",
        }


class TestValidatorAndMigrationTypes:
    """
    the validator entry points and migration
    ``dict``/``list``. These tests pin the new type contracts.
    """

    def test_validate_config_update_returns_tuple_of_correct_types(self):
        """``validate_config_update`` must return a 2-tuple whose first"""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<f4>"})
        assert isinstance(validated, dict)
        assert isinstance(errors, list)
        for e in errors:
            assert isinstance(e, str)

    def test_validate_config_returns_list_of_str(self):
        """``validate_config`` must return a ``list[str]``."""
        from voice_typer.server.config import validate_config

        errors = validate_config(Config())
        assert isinstance(errors, list)
        for e in errors:
            assert isinstance(e, str)

    def test_ipc_config_allowlist_is_dict_of_fieldspec(self):
        """``IPC_CONFIG_ALLOWLIST`` is now typed as"""
        import typing

        import voice_typer.server.config_validators as cv

        hints = typing.get_type_hints(cv, include_extras=False)
        allowlist_hint = hints.get("IPC_CONFIG_ALLOWLIST")
        assert allowlist_hint is not None, "IPC_CONFIG_ALLOWLIST must have a type hint"
        origin = typing.get_origin(allowlist_hint)
        assert origin is dict, f"IPC_CONFIG_ALLOWLIST hint origin must be dict, got {origin!r}"
        args = typing.get_args(allowlist_hint)
        assert len(args) == 2, f"IPC_CONFIG_ALLOWLIST must be parameterised [str, FieldSpec], got args={args!r}"

    def test_migrate_to_v3_returns_dict(self):
        """``_migrate_to_v3`` returns a ``dict`` (migration contract)."""
        from voice_typer.server.config import _migrate_to_v3

        result = _migrate_to_v3({"silence_rms_threshold": 0.5, "_load_warnings": []})
        assert isinstance(result, dict)
        # The deprecated key was scrubbed.
        assert "silence_rms_threshold" not in result


class TestV5PushToTalkHotkeyPrune:
    """Config dataclass (PTT uses the main ``hotkey``). The v5 migration"""

    def test_migrate_to_v5_prunes_push_to_talk_hotkey(self):
        from voice_typer.server.config_internals.migrations import _migrate_to_v5

        result = _migrate_to_v5({"push_to_talk_hotkey": "<f9>", "hotkey": "<caps_lock>", "_load_warnings": []})
        assert "push_to_talk_hotkey" not in result
        assert result["hotkey"] == "<caps_lock>"

    def test_migrate_to_v5_noop_when_key_absent(self):
        from voice_typer.server.config_internals.migrations import _migrate_to_v5

        result = _migrate_to_v5({"hotkey": "<caps_lock>", "_load_warnings": []})
        assert "push_to_talk_hotkey" not in result
        assert result["hotkey"] == "<caps_lock>"
        assert result["_load_warnings"] == []

    def test_load_prunes_push_to_talk_hotkey_from_v4_config(self, tmp_path, tmp_config_dir):
        """A v4 config file carrying ``push_to_talk_hotkey`` loads cleanly"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "hotkey": "<f9>",
                    "push_to_talk_hotkey": "<f9>",
                    "recording_mode": "toggle",
                }
            )
        )
        loaded = Config.load()
        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION
        assert not hasattr(loaded, "push_to_talk_hotkey")
        assert loaded.hotkey == "<f9>"
