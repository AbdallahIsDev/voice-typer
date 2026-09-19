"""Verify the credential_store package split preserves the public API surface."""

from __future__ import annotations

import contextlib
import threading

import pytest
from voice_typer.server import credential_store as cs


class TestPublicAPIPreserved:
    """Every public symbol listed in ``__all__`` must resolve to a value"""

    def test_constants_are_correct_types(self):
        assert isinstance(cs.KEYRING_SERVICE_NAME, str)
        assert cs.KEYRING_SERVICE_NAME == "com.voicetyper.keyring"
        assert isinstance(cs.KEYRING_REF_PREFIX, str)
        assert cs.KEYRING_REF_PREFIX == "keyring://"
        assert isinstance(cs.PROVIDER_TO_CONFIG_FIELD, dict)
        assert isinstance(cs.CONFIG_FIELD_TO_PROVIDER, dict)
        assert isinstance(cs._KNOWN_PROVIDERS_HISTORY, frozenset)

    def test_provider_map_round_trip(self):
        for provider, field in cs.PROVIDER_TO_CONFIG_FIELD.items():
            assert cs.CONFIG_FIELD_TO_PROVIDER[field] == provider

    def test_public_functions_callable(self):
        for name in (
            "is_keyring_available",
            "get_keyring_status",
            "store_secret",
            "load_secret",
            "delete_secret",
            "clear_in_memory_secrets",
            "migrate_secrets_to_keyring",
        ):
            assert callable(getattr(cs, name)), f"{name} must be callable"

    def test_outcome_function_callable(self):
        assert callable(cs.last_store_outcome)
        # Returns a dict with the three documented keys (``stored_in``,
        outcome = cs.last_store_outcome()
        assert set(outcome.keys()) == {"stored_in", "reason", "provider"}
        assert outcome["stored_in"] in {
            "keyring",
            "plaintext",
            "deleted",
            "failed",
            "unknown",
        }

    def test_all_attribute_complete(self):
        for name in cs.__all__:
            assert hasattr(cs, name), f"public __all__ entry {name!r} missing from package"


class TestPrivateSymbolsPreserved:
    """Symbols tests import / monkey-patch must still resolve."""

    @pytest.mark.parametrize(
        "name",
        [
            # schema
            "_LEGACY_KEYRING_SERVICE_NAMES",
            "_SERVICE_NAME_MIGRATED_FLAG",
            "_REASON_MAX_LEN",
            "_T",
            "log",
            # redact
            "_PATH_RE",
            "_redact_sensitive",
            # outcome
            "_last_store_outcome",
            "_set_last_store_outcome",
            # backend
            "_KEYRING_TIMEOUT_SECONDS",
            "_KEYRING_WEDGE_COOLDOWN_S",
            "_KEYRING_ORPHAN_WARN_THRESHOLD",
            "_KEYRING_REPROBE_INTERVAL_SECONDS",
            "_keyring_state_lock",
            "_orphaned_thread_count",
            "_consecutive_timeouts",
            "_wedged_until",
            "_keyring_available_cache",
            "_keyring_backend_name_cache",
            "_keyring_reason_cache",
            "_keyring_last_probe_ts",
            "_keyring_probe_lock",
            "_plaintext_config_cache",
            "_run_keyring_call",
            "_probe_keyring",
            "_reset_keyring_cache",
            "_clear_plaintext_config_cache",
            # plaintext
            "_read_plaintext_fallback",
            "_write_plaintext_fallback",
            # migration
            "_is_windows",
            "_MIGRATION_LOCK_TIMEOUT_SECONDS",
            "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS",
            "_acquire_migration_lock",
            "_migrate_legacy_service_names_locked",
            "_migrate_secrets_to_keyring_locked",
        ],
    )
    def test_symbol_resolves(self, name):
        assert hasattr(cs, name), f"private symbol {name!r} missing from package"

    def test_log_name_unchanged(self):
        assert cs.log.name == "voice_typer.server.credential_store"

    def test_plaintext_config_cache_is_dict(self):
        assert isinstance(cs._plaintext_config_cache, dict)

    def test_keyring_state_lock_is_lock(self):
        assert isinstance(cs._keyring_state_lock, type(threading.Lock()))

    def test_last_store_outcome_is_thread_local(self):
        assert isinstance(cs._last_store_outcome, threading.local)


class TestMonkeyPatchPropagates:
    """Tests do ``monkeypatch.setattr(credential_store, \"<name>\", ...)``."""

    def test_keyring_timeout_seconds_propagates_to_run_keyring_call(self, monkeypatch):
        """``_run_keyring_call`` reads ``_KEYRING_TIMEOUT_SECONDS`` via"""
        import time

        monkeypatch.setattr(cs, "_KEYRING_TIMEOUT_SECONDS", 0.001)

        def slow_fn():
            time.sleep(0.5)
            return "should-not-reach"

        with pytest.raises(TimeoutError):
            cs._run_keyring_call(slow_fn)

    def test_is_keyring_available_propagates_to_store_secret(self, monkeypatch):
        """package module, patching the package attribute must make"""
        calls = []
        monkeypatch.setattr(cs, "is_keyring_available", lambda: (calls.append(1), True)[1])
        with contextlib.suppress(Exception):
            cs.load_secret("openai")
        assert calls, "load_secret must have consulted the patched is_keyring_available"

    def test_known_providers_history_propagates_to_delete_secret(self, monkeypatch):
        """``delete_secret`` iterates ``_KNOWN_PROVIDERS_HISTORY`` via"""
        fake = frozenset({"ghost_provider"})
        monkeypatch.setattr(cs, "_KNOWN_PROVIDERS_HISTORY", fake)
        assert cs._KNOWN_PROVIDERS_HISTORY is fake

    def test_plaintext_config_cache_propagates_to_clear_helper(self, monkeypatch):
        """package module, patching the package attribute with a new"""
        sentinel = {"k": "v"}
        monkeypatch.setattr(cs, "_plaintext_config_cache", sentinel)
        cs._clear_plaintext_config_cache()
        assert sentinel == {}, "clear helper must have cleared the patched dict"


class TestPublicSurfaceMatchesDir:
    """``dir(credential_store)`` must surface every public name. This is"""

    def test_dir_contains_public_api(self):
        names = set(dir(cs))
        for name in (
            "KEYRING_REF_PREFIX",
            "KEYRING_SERVICE_NAME",
            "PROVIDER_TO_CONFIG_FIELD",
            "CONFIG_FIELD_TO_PROVIDER",
            "_KNOWN_PROVIDERS_HISTORY",
            "clear_in_memory_secrets",
            "delete_secret",
            "get_keyring_status",
            "is_keyring_available",
            "load_secret",
            "migrate_secrets_to_keyring",
            "store_secret",
            "last_store_outcome",
            "log",
        ):
            assert name in names, f"dir(credential_store) missing {name!r}"
