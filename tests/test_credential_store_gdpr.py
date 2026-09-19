"""DJ-24: ``_plaintext_config_cache`` retains plaintext API keys after GDPR delete."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    credential_store._reset_keyring_cache()
    credential_store._clear_plaintext_config_cache()
    yield
    credential_store._reset_keyring_cache()
    credential_store._clear_plaintext_config_cache()


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing)."""

    class _FailKeyring:
        name = "fail"

        def get_password(self, service, username):
            raise RuntimeError("no backend available")

        def set_password(self, service, username, password):
            raise RuntimeError("no backend available")

        def delete_password(self, service, username):
            raise RuntimeError("no backend available")

    fail_backend = _FailKeyring()
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = fail_backend

    fail_module = MagicMock()
    fail_module.Keyring = _FailKeyring
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
    )
    return fake_keyring


@pytest.fixture
def mock_keyring_available(monkeypatch):
    """Mock keyring as available with an in-memory store."""
    store: dict[tuple[str, str], str] = {}

    class _FakeBackend:
        """A fake keyring backend that stores secrets in a dict."""

        name = "FakeKeyring"

        def get_password(self, service, username):
            return store.get((service, username))

        def set_password(self, service, username, password):
            store[(service, username)] = password

        def delete_password(self, service, username):
            store.pop((service, username), None)

    backend = _FakeBackend()
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = backend
    fake_keyring.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring.delete_password.side_effect = lambda s, u: store.pop((s, u), None)

    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "FakeKeyring", None),
    )
    return {"store": store, "backend": backend, "keyring": fake_keyring}


# _clear_plaintext_config_cache helper ─────────────────────────


class TestClearPlaintextConfigCacheHelper:
    """The helper exists, is callable, and is idempotent."""

    def test_helper_exists_on_module(self):
        assert hasattr(credential_store, "_clear_plaintext_config_cache")
        assert callable(credential_store._clear_plaintext_config_cache)

    def test_helper_clears_populated_cache(self, tmp_path):
        # Pre-populate the cache as if a _read_plaintext_fallback call
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-leaked"}))
        # Force a read so the cache is populated.
        credential_store._read_plaintext_fallback("openai")
        assert credential_store._plaintext_config_cache, (
            "precondition: cache should be populated after _read_plaintext_fallback"
        )

        credential_store._clear_plaintext_config_cache()

        assert not credential_store._plaintext_config_cache, (
            "DJ-24: _clear_plaintext_config_cache must empty the cache dict"
        )

    def test_helper_is_idempotent_on_empty_cache(self):
        # Already empty (autouse fixture clears it).
        assert not credential_store._plaintext_config_cache
        # Must not raise.
        credential_store._clear_plaintext_config_cache()
        credential_store._clear_plaintext_config_cache()
        assert not credential_store._plaintext_config_cache


class TestDeleteSecretClearsCache:
    """``delete_secret()`` must invalidate ``_plaintext_config_cache``."""

    def test_delete_secret_clears_cache_after_plaintext_fallback_write(self, mock_keyring_unavailable, tmp_path):
        """config.json via the plaintext fallback, the parsed-config cache"""
        # Arrange: store a plaintext secret (falls back to config.json
        credential_store.store_secret("openai", "sk-secret-to-delete")
        # Force a read so the cache is populated with the plaintext.
        loaded = credential_store.load_secret("openai")
        assert loaded == "sk-secret-to-delete"
        assert credential_store._plaintext_config_cache, "precondition: cache should be populated after load_secret"
        # Sanity: the cached parsed dict still contains the plaintext.
        cached_entry = next(iter(credential_store._plaintext_config_cache.values()))
        assert cached_entry[1].get("openai_api_key") == "sk-secret-to-delete"

        # Act: delete the secret. This calls _write_plaintext_fallback('')
        credential_store.delete_secret("openai")

        # Assert: cache is empty, the stale plaintext value is no
        assert not credential_store._plaintext_config_cache, (
            "DJ-24: _plaintext_config_cache must be empty after delete_secret() "
            "so a memory dump between the GDPR delete and the next app restart "
            "doesn't expose the stale plaintext API key"
        )

    def test_delete_secret_for_one_provider_invalidates_cache_for_all(self, mock_keyring_unavailable, tmp_path):
        """DJ-24: the cache is keyed by file path, not by provider —"""
        # Arrange: populate config.json with TWO plaintext secrets.
        credential_store.store_secret("openai", "sk-openai")
        credential_store.store_secret("groq", "gsk_groq")
        # Seed the cache.
        credential_store.load_secret("openai")
        credential_store.load_secret("groq")
        assert credential_store._plaintext_config_cache

        # Act: delete just one provider.
        credential_store.delete_secret("openai")

        # Assert: cache is cleared ().
        assert not credential_store._plaintext_config_cache
        # And a subsequent load re-reads from disk, the deleted
        assert credential_store.load_secret("openai") is None
        assert credential_store.load_secret("groq") == "gsk_groq"


class TestClearInMemorySecretsClearsCache:
    """``clear_in_memory_secrets()`` must invalidate the cache too."""

    def test_clear_in_memory_secrets_empties_cache(self, mock_keyring_unavailable, tmp_path):
        """DJ-24: ``clear_in_memory_secrets()`` (called by the GDPR"""
        # Arrange: populate the cache.
        credential_store.store_secret("openai", "sk-leak")
        credential_store.load_secret("openai")
        assert credential_store._plaintext_config_cache

        config = MagicMock(spec=[field for field in credential_store.PROVIDER_TO_CONFIG_FIELD.values()])

        # Act.
        credential_store.clear_in_memory_secrets(config)

        # Assert: cache is empty ().
        assert not credential_store._plaintext_config_cache, (
            "DJ-24: clear_in_memory_secrets() must invalidate _plaintext_config_cache"
        )

    def test_clear_in_memory_secrets_clears_cache_even_if_setattr_fails(self, mock_keyring_unavailable, tmp_path):
        """DJ-24: the cache-clear must run even if the ``setattr``"""
        # Arrange: populate the cache.
        credential_store.store_secret("openai", "sk-leak")
        credential_store.load_secret("openai")
        assert credential_store._plaintext_config_cache

        # A config object whose __setattr__ raises (e.g. frozen dataclass).
        class _FrozenConfig:
            def __setattr__(self, key, value):
                raise AttributeError("frozen dataclass")

        config = _FrozenConfig()

        # Act, must not raise.
        cleared = credential_store.clear_in_memory_secrets(config)

        # Assert: cache is still cleared even though no field was set.
        assert not credential_store._plaintext_config_cache, (
            "DJ-24: cache must be cleared even if setattr fails for every field"
        )
        # Cleared count is 0 because every setattr raised.
        assert cleared == 0


class TestStoreSecretRejectsUnknownProvider:
    """``store_secret`` rejects unknown / typo'd / deprecated provider"""

    def test_rejects_typo_provider_name(self, mock_keyring_available, caplog):
        """``store_secret(\"openai_v2\", ...)`` must return False and NOT"""
        import logging

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            result = credential_store.store_secret("openai_v2", "sk-typo-test-12345")

        assert result is False, (
            "store_secret must return False for an unknown provider (prevents orphaned keychain entries)"
        )
        # The typo'd provider must NOT have been written to the keyring.
        store = mock_keyring_available["store"]
        assert (credential_store.KEYRING_SERVICE_NAME, "openai_v2") not in store, (
            "store_secret must NOT write to the keyring for an unknown provider, "
            "the whole point of the validation is to prevent orphaned entries"
        )
        # A WARNING must be logged so the operator / caller sees the rejection.
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("unknown provider" in msg and "openai_v2" in msg for msg in warning_msgs), (
            f"expected a WARNING mentioning 'unknown provider' and 'openai_v2'; got: {warning_msgs!r}"
        )

    def test_rejects_case_variant_provider_name(self, mock_keyring_available):
        """``store_secret(\"OpenAI\", ...)`` (case typo) must be rejected —"""
        result = credential_store.store_secret("OpenAI", "sk-case-test-12345")
        assert result is False
        store = mock_keyring_available["store"]
        assert (credential_store.KEYRING_SERVICE_NAME, "OpenAI") not in store

    def test_rejects_deprecated_provider_name(self, mock_keyring_available):
        """A plausible deprecated name (e.g. ``\"polisher\"``, an old name"""
        result = credential_store.store_secret("polisher", "sk-deprecated-test")
        assert result is False
        store = mock_keyring_available["store"]
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store

    def test_rejects_empty_string_provider(self, mock_keyring_available):
        """An empty-string provider must be rejected (not in the map)."""
        result = credential_store.store_secret("", "sk-empty-provider")
        assert result is False

    def test_rejection_does_not_write_plaintext_fallback(self, mock_keyring_unavailable, tmp_path):
        """Even on the keyring-unavailable path, the rejection must NOT"""
        result = credential_store.store_secret("openai_v2", "sk-no-fallback-please")
        assert result is False
        # config.json must NOT have been created (or, if it exists from a
        # prior test step, must NOT contain the orphaned field). Since
        config_file = tmp_path / "config.json"
        if config_file.exists():
            data = json.loads(config_file.read_text())
            assert "openai_v2_api_key" not in data, (
                "store_secret must NOT write a plaintext fallback field for an unknown provider"
            )

    def test_rejection_sets_outcome_reason(self, mock_keyring_available):
        """reason mentioning the unknown provider name, so the IPC handler"""
        credential_store.store_secret("openai_v2", "sk-outcome-test")
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext", (
            f"expected stored_in='plaintext' for the rejection path (matches the "
            f"existing non-string-value rejection pattern); got: {outcome['stored_in']!r}"
        )
        assert outcome["provider"] == "openai_v2"
        assert outcome["reason"] is not None
        assert "openai_v2" in outcome["reason"], (
            f"the reason must mention the unknown provider name; got: {outcome['reason']!r}"
        )

    def test_known_provider_still_works(self, mock_keyring_available):
        """Regression guard: the validation must NOT break the happy path"""
        secrets = {
            "openai": "sk-openai-known",
            "groq": "gsk-groq-known",
            "deepgram": "dg-deepgram-known",
            "cloud": "cl-cloud-known",
            "llm": "llm-llm-known",
        }
        for provider, value in secrets.items():
            result = credential_store.store_secret(provider, value)
            assert result is True, (
                f"store_secret({provider!r}, ...) must succeed (return True) for a known provider, "
                f"the validation must not break the happy path"
            )
            assert credential_store.load_secret(provider) == value, (
                f"load_secret({provider!r}) must return the stored value for a known provider"
            )

    def test_empty_value_for_known_provider_still_deletes(self, mock_keyring_available):
        """Regression guard: the validation runs BEFORE the empty-value"""
        # Pre-populate the keyring.
        credential_store.store_secret("openai", "sk-to-be-cleared")
        assert credential_store.load_secret("openai") == "sk-to-be-cleared"

        # Empty value → delete.
        result = credential_store.store_secret("openai", "")
        assert result is True, "store_secret('openai', '') must return True (delete path) for a known provider"
        assert credential_store.load_secret("openai") is None, (
            "the openai key must be deleted after store_secret('openai', '')"
        )

    def test_empty_value_for_unknown_provider_is_rejected(self, mock_keyring_available):
        """The validation runs BEFORE the empty-value branch, so"""
        mock_keyring_available["store"][(credential_store.KEYRING_SERVICE_NAME, "openai_v2")] = "sk-orphan-pre-existing"

        result = credential_store.store_secret("openai_v2", "")
        assert result is False, (
            "store_secret('openai_v2', '') must be rejected by the validation, "
            "the empty-value delete path is NOT reached for unknown providers"
        )
        # The orphaned entry is STILL there, store_secret didn't delete it.
        store = mock_keyring_available["store"]
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "openai_v2")) == "sk-orphan-pre-existing", (
            "store_secret('openai_v2', '') must NOT delete the orphaned entry, "
            "the validation rejects the call before the empty-value branch runs"
        )


class TestKnownProvidersHistory:
    """superset of ``PROVIDER_TO_CONFIG_FIELD`` keys plus any historical /"""

    def test_history_is_frozenset(self):
        """The history must be a ``frozenset`` (immutable, hashable) so"""
        assert isinstance(credential_store._KNOWN_PROVIDERS_HISTORY, frozenset), (
            f"_KNOWN_PROVIDERS_HISTORY must be a frozenset; got: "
            f"{type(credential_store._KNOWN_PROVIDERS_HISTORY).__name__}"
        )

    def test_history_includes_all_current_providers(self):
        """current providers. This is the minimum invariant; deprecated"""
        current = set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys())
        history = set(credential_store._KNOWN_PROVIDERS_HISTORY)
        assert current.issubset(history), (
            f"_KNOWN_PROVIDERS_HISTORY must include all current providers; missing: {current - history}"
        )

    def test_history_is_module_level_attribute(self, monkeypatch):
        """The history must be a module-level attribute (not a function"""
        test_history = frozenset({"openai", "groq", "deepgram", "cloud", "llm", "openai_v2"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)
        assert credential_store._KNOWN_PROVIDERS_HISTORY is test_history

    def test_history_is_in_all(self):
        """``_KNOWN_PROVIDERS_HISTORY`` must be listed in ``__all__`` so"""
        assert "_KNOWN_PROVIDERS_HISTORY" in credential_store.__all__, (
            "_KNOWN_PROVIDERS_HISTORY must be in __all__ so it's part of the public API"
        )


class TestDeleteSecretOrphanCleanup:
    """deletes any orphaned keychain entries for historical / deprecated /"""

    def test_delete_secret_cleans_up_orphaned_deprecated_entry(self, mock_keyring_available, monkeypatch):
        """``delete_secret(\"openai\")`` must ALSO delete an orphaned"""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-current-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-orphaned-polisher"

        # Act: delete the openai entry. The orphan-cleanup must ALSO
        credential_store.delete_secret("openai")

        # Assert: both entries are gone.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store, (
            "delete_secret('openai') must delete the openai keychain entry"
        )
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store, (
            "delete_secret('openai') must ALSO delete the orphaned 'polisher' entry "
            "(via _KNOWN_PROVIDERS_HISTORY iteration), closing the GDPR orphan gap"
        )

    def test_delete_secret_cleans_up_typo_entry(self, mock_keyring_available, monkeypatch):
        """``delete_secret(\"openai\")`` must ALSO delete an orphaned"""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"openai_v2"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai_v2")] = "sk-typo-orphan"

        credential_store.delete_secret("openai")

        assert (credential_store.KEYRING_SERVICE_NAME, "openai_v2") not in store, (
            "delete_secret('openai') must delete the orphaned 'openai_v2' typo entry"
        )

    def test_delete_secret_cleans_up_multiple_orphans(self, mock_keyring_available, monkeypatch):
        """``delete_secret`` must clean up ALL deprecated entries in"""
        test_history = frozenset(
            set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher", "openai_v2", "whisper_cloud"}
        )
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        for orphan in ("polisher", "openai_v2", "whisper_cloud"):
            store[(credential_store.KEYRING_SERVICE_NAME, orphan)] = f"sk-orphan-{orphan}"

        credential_store.delete_secret("groq")

        for orphan in ("polisher", "openai_v2", "whisper_cloud"):
            assert (credential_store.KEYRING_SERVICE_NAME, orphan) not in store, (
                f"delete_secret('groq') must delete the orphaned '{orphan}' entry"
            )

    def test_delete_secret_skips_current_providers_in_history_iteration(self, mock_keyring_available, monkeypatch):
        """``PROVIDER_TO_CONFIG_FIELD``), the privacy service's per-provider"""
        # History includes a deprecated name + all current providers.
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "groq")] = "sk-groq"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-polisher"

        credential_store.delete_secret("openai")

        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store
        # It must still be present (delete_secret("openai") must NOT
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "groq")) == "sk-groq", (
            "delete_secret('openai') must NOT delete the 'groq' entry, "
            "groq is a current provider and the orphan-cleanup iteration skips "
            "current providers (handled by the privacy service's per-provider loop)"
        )

    def test_delete_secret_no_op_when_history_has_no_deprecated_names(self, mock_keyring_available):
        """When ``_KNOWN_PROVIDERS_HISTORY`` has no deprecated names"""
        # Default history (just current providers, no deprecated names).
        assert frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) == credential_store._KNOWN_PROVIDERS_HISTORY

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        # Inject a phantom entry that's NOT in the history.
        store[(credential_store.KEYRING_SERVICE_NAME, "phantom_typo")] = "sk-phantom"

        credential_store.delete_secret("openai")

        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "phantom_typo")) == "sk-phantom", (
            "delete_secret must NOT delete a phantom typo entry that's not in "
            "_KNOWN_PROVIDERS_HISTORY, the fix only cleans up KNOWN deprecated names"
        )

    def test_delete_secret_orphan_cleanup_is_idempotent(self, mock_keyring_available, monkeypatch):
        """Calling ``delete_secret`` twice must not raise, the second"""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-polisher"

        # First call, deletes openai + polisher.
        credential_store.delete_secret("openai")
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store

        # Second call, must NOT raise. The orphan-cleanup iteration
        credential_store.delete_secret("groq")  # different provider, same history iteration

        # Both openai and polisher are still gone.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store

    def test_delete_secret_orphan_cleanup_via_store_secret_empty_value(self, mock_keyring_available, monkeypatch):
        """``store_secret(\"openai\", \"\")`` calls ``delete_secret(\"openai\")``"""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-polisher-orphan"

        result = credential_store.store_secret("openai", "")
        assert result is True, "store_secret('openai', '') must return True (delete path)"

        # Both openai and the orphaned polisher entry are gone.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store, (
            "store_secret('openai', '') → delete_secret('openai') must ALSO clean up "
            "the orphaned 'polisher' entry via the history iteration"
        )

    def test_delete_secret_orphan_cleanup_works_when_keyring_unavailable(self, mock_keyring_unavailable, monkeypatch):
        """When keyring is unavailable, the orphan-cleanup iteration is"""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        # Must not raise, the unavailable keyring means the orphan
        credential_store.delete_secret("openai")
