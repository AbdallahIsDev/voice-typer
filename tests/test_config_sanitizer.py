"""Tests for the transport-neutral config sanitizer."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from voice_typer.server.config import Config
from voice_typer.server.config_sanitizer import (
    REDACTED_SENTINEL,
    SECRET_CONFIG_FIELDS,
    sanitize_config_for_ipc,
)
from voice_typer.server.credential_store import PROVIDER_TO_CONFIG_FIELD


@dataclass
class _FakeConfig:
    """Minimal dataclass stand-in for ``voice_typer.server.config.Config``."""

    hotkey: str | None = None
    language: str | None = None
    model_name: str | None = None
    cloud_api_key: str = ""
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    deepgram_api_key: str | None = None
    llm_api_key: str | None = None


class TestSecretFieldsRedacted:
    """Every field in :data:`SECRET_CONFIG_FIELDS` must be redacted."""

    @pytest.mark.parametrize("field", sorted(SECRET_CONFIG_FIELDS))
    def test_each_secret_field_is_redacted_when_truthy(self, field):
        real_value = f"sk-{field}-real-value-12345"
        cfg = _FakeConfig(**{field: real_value})
        result = sanitize_config_for_ipc(cfg)
        assert result[field] == REDACTED_SENTINEL
        # SEC-003: the real value must not appear anywhere in the
        assert real_value not in str(result)

    def test_all_known_secret_fields_covered(self):
        """FR-19: pin the structural link between SECRET_CONFIG_FIELDS"""
        assert frozenset(PROVIDER_TO_CONFIG_FIELD.values()) == SECRET_CONFIG_FIELDS

    def test_secret_config_fields_subset_of_config_dataclass_fields(self):
        """FR-19: every secret field must be a declared ``Config``"""
        config_field_names = set(Config.__dataclass_fields__.keys())
        assert SECRET_CONFIG_FIELDS.issubset(config_field_names), (
            f"SECRET_CONFIG_FIELDS has entries not on Config dataclass: {SECRET_CONFIG_FIELDS - config_field_names}"
        )

    def test_falsy_secret_value_preserved_not_redacted(self):
        """Empty-string / None secrets are preserved so the renderer can"""
        cfg = _FakeConfig(
            cloud_api_key="",
            openai_api_key=None,
            groq_api_key="groq-real",
        )
        result = sanitize_config_for_ipc(cfg)
        assert result["cloud_api_key"] == ""
        assert result["openai_api_key"] is None
        assert result["groq_api_key"] == REDACTED_SENTINEL


class TestNonSecretFieldsPreserved:
    """Non-secret fields pass through unchanged."""

    def test_non_secret_fields_pass_through(self):
        cfg = _FakeConfig(
            hotkey="<f9>",
            language="fr",
            model_name="small.en",
            cloud_api_key="sk-real",
        )
        result = sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f9>"
        assert result["language"] == "fr"
        assert result["model_name"] == "small.en"
        assert result["cloud_api_key"] == REDACTED_SENTINEL

    def test_returns_plain_dict(self):
        cfg = _FakeConfig(hotkey="<f2>")
        result = sanitize_config_for_ipc(cfg)
        assert isinstance(result, dict)

    def test_does_not_mutate_input_config(self):
        """Sanitizing must not mutate the original Config object."""
        cfg = _FakeConfig(cloud_api_key="sk-original")
        sanitize_config_for_ipc(cfg)
        assert cfg.cloud_api_key == "sk-original"


class TestMissingFieldsHandledGracefully:
    """Older Config snapshots that lack a secret field must not crash."""

    def test_missing_secret_field_not_synthesized(self):
        # after ``asdict``. The sanitizer must NOT add a phantom
        cfg = _FakeConfig(hotkey="<f2>")  # cloud_api_key stays at ""
        result = sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f2>"
        assert result["cloud_api_key"] == ""

    def test_empty_config_returns_only_declared_fields(self):
        # ``asdict`` returns exactly the set of declared
        cfg = _FakeConfig()
        result = sanitize_config_for_ipc(cfg)
        # Every key in the result is either a declared dataclass
        expected_keys = set(_FakeConfig.__dataclass_fields__.keys()) | {"last_load_warnings"}
        assert set(result.keys()) == expected_keys


class TestNoTransientAttributesLeaked:
    """FR-20: the sanitizer must NOT leak transient / private attributes."""

    def test_does_not_leak_last_saved_bytes(self):
        """The ``_last_saved_bytes`` cache (set by ``Config.__post_init__``"""
        cfg = Config()
        # ``__post_init__`` sets ``_last_saved_bytes`` to None.
        object.__setattr__(cfg, "_last_saved_bytes", b'{"should": "not leak"}')
        result = sanitize_config_for_ipc(cfg)
        assert "_last_saved_bytes" not in result
        # Belt and suspenders: the cached bytes value must not appear
        assert b'"should": "not leak"' not in str(result).encode("utf-8", errors="ignore")

    def test_does_not_leak_last_load_warnings(self, monkeypatch):
        """the dataclass-fields-only denylist (see"""
        from pathlib import Path

        cfg = Config()
        # Use a path that genuinely starts with the user's home
        home = str(Path.home())
        cfg.last_load_warnings = [
            f"{home}/sensitive-path/config.json was migrated",
            "field 'openai_api_key' had value 'sk-leak-me' which was reset",
        ]
        result = sanitize_config_for_ipc(cfg)
        # The key IS surfaced (renderer contract).
        assert "last_load_warnings" in result
        # (e.g. ``/home/user``) must NOT appear in the IPC payload.
        assert home not in str(result), (
            f"_redact_home_path should have replaced the home prefix with ``~``; got {result['last_load_warnings']!r}"
        )
        # And the API key is masked.
        assert "sk-leak-me" not in str(result), (
            f"redact_pii should have masked the API key; got {result['last_load_warnings']!r}"
        )

    def test_does_not_leak_mutation_lock(self):
        """The ``_mutation_lock`` ClassVar (an ``RLock`` instance)"""
        cfg = Config()
        import threading

        cfg.set_mutation_lock(threading.RLock())
        result = sanitize_config_for_ipc(cfg)
        assert "_mutation_lock" not in result

    def test_output_keys_exactly_match_config_dataclass_fields(self):
        """FR-20: the output key set is EXACTLY the set of declared"""
        import typing

        cfg = Config()
        # Populate transient attrs to ensure they're filtered out.
        object.__setattr__(cfg, "_last_saved_bytes", b"secret-cache-bytes")
        cfg.last_load_warnings = ["leak-attempt"]
        result = sanitize_config_for_ipc(cfg)
        # ``Config.__dataclass_fields__`` includes ``ClassVar`` fields
        expected_keys = {
            name
            for name, f in Config.__dataclass_fields__.items()
            if typing.get_origin(typing.get_type_hints(Config).get(name, f.type)) is not typing.ClassVar
            and typing.get_type_hints(Config).get(name, f.type) is not typing.ClassVar
            and not (isinstance(f.type, str) and "ClassVar" in f.type)
        }
        expected_keys.discard("_mutation_lock")
        expected_keys.discard("_SECRET_FIELD_NAMES_FALLBACK")
        expected_keys.add("last_load_warnings")
        assert set(result.keys()) == expected_keys, (
            f"Sanitizer output keys mismatch.\n"
            f"  Expected (Config dataclass fields, ClassVar excluded, "
            f"plus last_load_warnings): {sorted(expected_keys)}\n"
            f"  Got: {sorted(result.keys())}\n"
            f"  Extra (should NOT be present): "
            f"{sorted(set(result.keys()) - expected_keys)}\n"
            f"  Missing (should be present): "
            f"{sorted(expected_keys - set(result.keys()))}"
        )


class TestSentinelValue:
    def test_sentinel_is_redacted_marker(self):
        assert REDACTED_SENTINEL == "<redacted>"


class TestSetConfigErrorEnvelope:
    """FR-22: ``_handle_set_config`` includes ``data.errors`` (full list)"""

    def _make_ipc_server(self):
        """Construct a real IPCServer wired to fake app + service."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, fake_app, fake_service = make_ipc_server_with_fakes()
        fake_app._ipc_server = server
        return server, fake_app, fake_service

    def test_single_error_envelope_has_errors_list(self):
        """A single-field invalid payload must still include"""
        ipc_server, _, _ = self._make_ipc_server()
        resp = ipc_server._handle_set_config({"model_size": "not-a-real-model"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert "errors" in resp["data"]
        assert isinstance(resp["data"]["errors"], list)
        assert len(resp["data"]["errors"]) == 1
        # Backward compat: message is still errors[0].
        assert resp["data"]["message"] == resp["data"]["errors"][0]

    def test_multi_error_envelope_includes_all_errors(self):
        """FR-22: an N-field invalid payload must include ALL N errors"""
        ipc_server, _, _ = self._make_ipc_server()
        # Two invalid fields: bad model_size + bad text_size.
        resp = ipc_server._handle_set_config(
            {
                "model_size": "not-a-real-model",
                "text_size": "not-an-int",
            },
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        errors = resp["data"]["errors"]
        assert isinstance(errors, list)
        assert len(errors) >= 2, f"FR-22: expected >=2 errors in envelope, got {len(errors)}: {errors}"
        # Both invalid field names appear in the error strings.
        errors_text = " ".join(errors)
        assert "model_size" in errors_text
        assert "text_size" in errors_text
        # Backward compat: message is errors[0].
        assert resp["data"]["message"] == errors[0]


class TestHistoryEnabledField:
    """the IPC allowlist accepts it (so the renderer can toggle it via"""

    def test_history_enabled_field_exists_with_default_true(self):
        """upgrades)."""
        assert "history_enabled" in Config.__dataclass_fields__, (
            "FR-28: Config dataclass must declare a 'history_enabled' "
            "field (default True). P4-A4 owns the dictation_pipeline "
            "gate that reads it."
        )
        cfg = Config()
        assert cfg.history_enabled is True, (
            "FR-28: history_enabled must default to True so existing "
            "users keep their history-on behavior after upgrade."
        )

    def test_history_enabled_in_ipc_allowlist(self):
        """renderer can toggle it via set_config (Settings → Privacy →"""
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        assert "history_enabled" in IPC_CONFIG_ALLOWLIST, (
            "FR-28: 'history_enabled' must be in IPC_CONFIG_ALLOWLIST so the renderer can toggle it via set_config."
        )
        expected_type, validator = IPC_CONFIG_ALLOWLIST["history_enabled"]
        assert expected_type is bool
        # Validator must accept True / False and reject non-bool.
        assert validator(True) is None
        assert validator(False) is None

    def test_history_enabled_round_trips_through_save_load(self, tmp_config_dir):
        """Save a Config with history_enabled=False, reload it, verify"""
        cfg1 = Config()
        cfg1.history_enabled = False
        cfg1.save()

        cfg2 = Config.load()
        assert cfg2.history_enabled is False, (
            "FR-28: history_enabled=False did not round-trip through "
            "save/load. The dictation_pipeline gate (P4-A4) relies on "
            "this field persisting correctly."
        )

    def test_set_config_history_enabled_via_ipc(self):
        """``history_enabled: False`` must succeed (ack) and the value"""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        ipc_server, _, fake_service = make_ipc_server_with_fakes()
        resp = ipc_server._handle_set_config({"history_enabled": False}, {})
        assert resp["type"] == "ack", f"FR-28: set_config(history_enabled=False) should succeed; got resp={resp}"
        # Verify apply_config received the validated update.
        fake_service.apply_config.assert_called_once()
        applied = fake_service.apply_config.call_args[0][0]
        assert applied == {"history_enabled": False}

    def test_set_config_history_enabled_rejects_non_bool(self):
        """FR-28: the validator must reject non-bool values (e.g."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        ipc_server, _, _ = make_ipc_server_with_fakes()
        resp = ipc_server._handle_set_config({"history_enabled": "true"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert any("history_enabled" in e for e in resp["data"]["errors"])


class TestDeadBranchesRemoved:
    """FR-21: the dead ``if \"volume_duck_smart\" in updates:`` branch"""

    def test_volume_duck_smart_branch_absent_from_source(self):
        """The ``if \"volume_duck_smart\" in updates:`` branch must NOT"""
        import inspect

        from voice_typer.server import config_applier

        source = inspect.getsource(config_applier)
        # that call must NOT appear inside a ``volume_duck_smart``
        assert 'if "volume_duck_smart" in updates' not in source, (
            "FR-21 regression: the dead ``if 'volume_duck_smart' in "
            "updates:`` branch is still present in config_applier."
        )

    def test_push_to_talk_hotkey_disjunct_absent_from_hotkey_restart_branch(
        self,
    ):
        """The ``or \"push_to_talk_hotkey\" in updates`` disjunct must"""
        import inspect

        from voice_typer.server import config_applier

        source = inspect.getsource(config_applier)
        assert 'or "push_to_talk_hotkey" in updates' not in source, (
            "FR-21 regression: the dead "
            "``or 'push_to_talk_hotkey' in updates`` disjunct is still "
            "present in config_applier."
        )

    def test_hotkey_restart_still_fires_on_hotkey_change(self):
        """FR-21 behavior parity: removing the dead disjunct must NOT"""
        from voice_typer.server.service import VoiceTyperService

        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        svc = VoiceTyperService(fake_app)
        svc.apply_config_side_effects({"hotkey": "<f3>"})
        fake_app.hotkeys.restart.assert_called()

    def test_hotkey_restart_still_fires_on_recording_mode_change(self):
        """FR-21 behavior parity: a ``recording_mode`` change must"""
        from voice_typer.server.service import VoiceTyperService

        from tests.fixtures.ipc_test_helpers import make_fake_app

        fake_app = make_fake_app()
        svc = VoiceTyperService(fake_app)
        svc.apply_config_side_effects({"recording_mode": "push_to_talk"})
        fake_app.hotkeys.restart.assert_called()
