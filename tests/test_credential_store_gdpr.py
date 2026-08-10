"""DJ-24 — ``_plaintext_config_cache`` retains plaintext API keys after GDPR delete.

The GDPR Art. 17 ``delete_all_personal_data`` flow (in
``voice_typer/server/service/privacy.py``) calls
``credential_store.delete_secret(provider, config=app_config)`` for every
provider, then ``credential_store.clear_in_memory_secrets(app_config)``
as a belt-and-suspenders pass. Both paths write ``""`` to the on-disk
``config.json`` and ``setattr`` the in-memory ``Config`` dataclass
fields to ``""``.

BUT neither path touched the module-level ``_plaintext_config_cache``
dict (added by ER-79 to avoid 5× re-parses of ``config.json`` at
startup). The cache holds the parsed dict — which still contains the
PRE-clear plaintext API keys. After a GDPR delete, no caller does a
subsequent ``_read_plaintext_fallback`` (which would re-parse via the
mtime check), so the stale dict lives in process memory until the app
restarts.

DJ-24 fix: a new ``_clear_plaintext_config_cache()`` helper mirrors
``_reset_keyring_cache()``. It's called from:

  - ``delete_secret()`` — after the ``_write_plaintext_fallback('')``
    call so the on-disk-clear's mtime bump is paired with a cache
    invalidation.
  - ``clear_in_memory_secrets()`` — after the ``setattr`` loop so the
    cache is invalidated whenever in-memory attributes are zeroed
    (defensive — covers any future caller that bypasses
    ``delete_secret`` and goes straight to ``clear_in_memory_secrets``).

This test file asserts:

  1. ``_plaintext_config_cache`` is empty (or has no entry for the
     config_file path) after a single ``delete_secret()`` call.
  2. ``_plaintext_config_cache`` is empty after
     ``clear_in_memory_secrets()``.
  3. The helper is exposed on the module (so the privacy mixin and
     tests can call it directly if needed).
  4. The helper is idempotent — calling it on an already-empty cache
     is a no-op.

Additional coverage (GDPR orphan-cleanup + store_secret validation):

  5. ``store_secret`` rejects unknown / typo'd / deprecated provider
     names at the top of the function (WARNING + return False), so no
     NEW orphaned OS-keychain entries can be created.
  6. ``_KNOWN_PROVIDERS_HISTORY`` frozenset is a module-level attribute
     that is a superset of ``PROVIDER_TO_CONFIG_FIELD`` keys plus any
     historical / deprecated names, and is monkey-patchable for tests.
  7. ``delete_secret`` iterates ``_KNOWN_PROVIDERS_HISTORY`` and
     deletes any orphaned keychain entries for historical / deprecated
     provider names — closing the "GDPR delete leaves orphaned
     OS-keychain entries" gap.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store

# ── Fixtures (mirrors tests/test_credential_store.py) ───────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache and the plaintext config
    cache so each test re-probes / re-populates from scratch.
    """
    credential_store._reset_keyring_cache()
    credential_store._clear_plaintext_config_cache()
    yield
    credential_store._reset_keyring_cache()
    credential_store._clear_plaintext_config_cache()


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing).

    DJ-24 specifically affects the plaintext-fallback path — when
    keyring is available, plaintext API keys never reach
    ``config.json`` in the first place. We need the unavailable path
    to populate ``_plaintext_config_cache`` with real plaintext.
    """

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
    """Mock keyring as available with an in-memory store.

    Mirrors the ``mock_keyring_available`` fixture in
    ``tests/test_credential_store.py``. Returns a dict with ``store``
    (the in-memory keyring store) so individual tests can inspect what
    was stored / deleted, and inject orphaned entries for deprecated /
    typo'd provider names before calling ``delete_secret``.
    """
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
        # had populated it with a parsed config.json containing
        # plaintext API keys.
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


# delete_secret clears the cache ───────────────────────────────


class TestDeleteSecretClearsCache:
    """``delete_secret()`` must invalidate ``_plaintext_config_cache``."""

    def test_delete_secret_clears_cache_after_plaintext_fallback_write(self, mock_keyring_unavailable, tmp_path):
        """DJ-24: after ``delete_secret(provider)`` writes ``""`` to
        config.json via the plaintext fallback, the parsed-config cache
        must be invalidated so the stale plaintext value doesn't
        survive in process memory."""
        # Arrange: store a plaintext secret (falls back to config.json
        # because keyring is unavailable). This populates the on-disk
        # config.json with the plaintext value AND the parsed-config
        # cache (via the subsequent _read_plaintext_fallback call we
        # make here to seed the cache).
        credential_store.store_secret("openai", "sk-secret-to-delete")
        # Force a read so the cache is populated with the plaintext.
        loaded = credential_store.load_secret("openai")
        assert loaded == "sk-secret-to-delete"
        assert credential_store._plaintext_config_cache, "precondition: cache should be populated after load_secret"
        # Sanity: the cached parsed dict still contains the plaintext.
        cached_entry = next(iter(credential_store._plaintext_config_cache.values()))
        assert cached_entry[1].get("openai_api_key") == "sk-secret-to-delete"

        # Act: delete the secret. This calls _write_plaintext_fallback('')
        # which writes "" to config.json, AND () calls
        # _clear_plaintext_config_cache() so the stale cache is dropped.
        credential_store.delete_secret("openai")

        # Assert: cache is empty — the stale plaintext value is no
        # longer reachable via the cache.
        assert not credential_store._plaintext_config_cache, (
            "DJ-24: _plaintext_config_cache must be empty after delete_secret() "
            "so a memory dump between the GDPR delete and the next app restart "
            "doesn't expose the stale plaintext API key"
        )

    def test_delete_secret_for_one_provider_invalidates_cache_for_all(self, mock_keyring_unavailable, tmp_path):
        """DJ-24: the cache is keyed by file path, not by provider —
        deleting one provider's secret invalidates the whole parsed
        dict (which contains every provider's value). Verify a
        subsequent load_secret() re-reads from disk and observes the
        cleared value."""
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
        # And a subsequent load re-reads from disk — the deleted
        # provider is gone, the other provider is still there.
        assert credential_store.load_secret("openai") is None
        assert credential_store.load_secret("groq") == "gsk_groq"


# clear_in_memory_secrets clears the cache ─────────────────────


class TestClearInMemorySecretsClearsCache:
    """``clear_in_memory_secrets()`` must invalidate the cache too."""

    def test_clear_in_memory_secrets_empties_cache(self, mock_keyring_unavailable, tmp_path):
        """DJ-24: ``clear_in_memory_secrets()`` (called by the GDPR
        delete flow as belt-and-suspenders) must also invalidate the
        parsed-config cache. Without this, the cache survives even if
        a caller bypasses ``delete_secret`` and goes straight to
        ``clear_in_memory_secrets``."""
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
        """DJ-24: the cache-clear must run even if the ``setattr``
        loop raises for every field (e.g. frozen dataclass). The
        cache-clear is independent of the field-clear — defense in
        depth so a partial-clean GDPR delete still drops the cache."""
        # Arrange: populate the cache.
        credential_store.store_secret("openai", "sk-leak")
        credential_store.load_secret("openai")
        assert credential_store._plaintext_config_cache

        # A config object whose __setattr__ raises (e.g. frozen dataclass).
        class _FrozenConfig:
            def __setattr__(self, key, value):
                raise AttributeError("frozen dataclass")

        config = _FrozenConfig()

        # Act — must not raise.
        cleared = credential_store.clear_in_memory_secrets(config)

        # Assert: cache is still cleared even though no field was set.
        assert not credential_store._plaintext_config_cache, (
            "DJ-24: cache must be cleared even if setattr fails for every field"
        )
        # Cleared count is 0 because every setattr raised.
        assert cleared == 0


# ── store_secret unknown-provider validation ──────────────────────


class TestStoreSecretRejectsUnknownProvider:
    """``store_secret`` rejects unknown / typo'd / deprecated provider
    names at the top of the function.

    Pre-fix, ``store_secret("openai_v2", "sk-...")`` would happily call
    ``keyring.set_password(KEYRING_SERVICE_NAME, "openai_v2", "sk-...")``,
    creating a keychain entry under the typo'd name. The GDPR delete
    path iterates ``PROVIDER_TO_CONFIG_FIELD`` (the 5 current
    providers), so the ``openai_v2`` entry would NEVER be deleted — an
    orphan that persists in the OS keychain indefinitely (macOS
    Keychain survives app uninstall).

    Post-fix, ``store_secret`` checks ``provider in
    PROVIDER_TO_CONFIG_FIELD`` at the very top (before the empty-value
    / type-guard / keyring-store branches) and rejects unknown
    providers with a WARNING log + ``return False``. This prevents NEW
    orphans from being created; ``delete_secret``'s
    ``_KNOWN_PROVIDERS_HISTORY`` iteration (covered by
    ``TestDeleteSecretOrphanCleanup`` below) cleans up PRE-EXISTING
    orphans.
    """

    def test_rejects_typo_provider_name(self, mock_keyring_available, caplog):
        """``store_secret("openai_v2", ...)`` must return False and NOT
        write anything to the keyring."""
        import logging

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            result = credential_store.store_secret("openai_v2", "sk-typo-test-12345")

        assert result is False, (
            "store_secret must return False for an unknown provider (prevents orphaned keychain entries)"
        )
        # The typo'd provider must NOT have been written to the keyring.
        store = mock_keyring_available["store"]
        assert (credential_store.KEYRING_SERVICE_NAME, "openai_v2") not in store, (
            "store_secret must NOT write to the keyring for an unknown provider — "
            "the whole point of the validation is to prevent orphaned entries"
        )
        # A WARNING must be logged so the operator / caller sees the rejection.
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("unknown provider" in msg and "openai_v2" in msg for msg in warning_msgs), (
            f"expected a WARNING mentioning 'unknown provider' and 'openai_v2'; got: {warning_msgs!r}"
        )

    def test_rejects_case_variant_provider_name(self, mock_keyring_available):
        """``store_secret("OpenAI", ...)`` (case typo) must be rejected —
        provider names are case-sensitive and ``PROVIDER_TO_CONFIG_FIELD``
        uses lowercase keys."""
        result = credential_store.store_secret("OpenAI", "sk-case-test-12345")
        assert result is False
        store = mock_keyring_available["store"]
        assert (credential_store.KEYRING_SERVICE_NAME, "OpenAI") not in store

    def test_rejects_deprecated_provider_name(self, mock_keyring_available):
        """A plausible deprecated name (e.g. ``"polisher"`` — an old name
        for the LLM polisher that's now ``"llm"``) must be rejected
        because it's not in ``PROVIDER_TO_CONFIG_FIELD``."""
        result = credential_store.store_secret("polisher", "sk-deprecated-test")
        assert result is False
        store = mock_keyring_available["store"]
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store

    def test_rejects_empty_string_provider(self, mock_keyring_available):
        """An empty-string provider must be rejected (not in the map)."""
        result = credential_store.store_secret("", "sk-empty-provider")
        assert result is False

    def test_rejection_does_not_write_plaintext_fallback(self, mock_keyring_unavailable, tmp_path):
        """Even on the keyring-unavailable path, the rejection must NOT
        fall through to the plaintext fallback (``_write_plaintext_fallback``).
        The validation runs BEFORE the keyring-availability check, so
        the plaintext fallback is never reached for unknown providers."""
        result = credential_store.store_secret("openai_v2", "sk-no-fallback-please")
        assert result is False
        # config.json must NOT have been created (or, if it exists from a
        # prior test step, must NOT contain the orphaned field). Since
        # ``_write_plaintext_fallback`` looks up
        # ``PROVIDER_TO_CONFIG_FIELD.get("openai_v2")`` → None → returns
        # early without writing, this is defense-in-depth even if the
        # validation were bypassed.
        config_file = tmp_path / "config.json"
        if config_file.exists():
            data = json.loads(config_file.read_text())
            assert "openai_v2_api_key" not in data, (
                "store_secret must NOT write a plaintext fallback field for an unknown provider"
            )

    def test_rejection_sets_outcome_reason(self, mock_keyring_available):
        """The ``last_store_outcome()`` must reflect the rejection with a
        reason mentioning the unknown provider name — so the IPC handler
        can surface *why* the store failed to the user."""
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
        """Regression guard: the validation must NOT break the happy path
        for known providers. All 5 current providers must still store
        successfully."""
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
                f"store_secret({provider!r}, ...) must succeed (return True) for a known provider — "
                f"the validation must not break the happy path"
            )
            assert credential_store.load_secret(provider) == value, (
                f"load_secret({provider!r}) must return the stored value for a known provider"
            )

    def test_empty_value_for_known_provider_still_deletes(self, mock_keyring_available):
        """Regression guard: the validation runs BEFORE the empty-value
        branch, so ``store_secret("openai", "")`` (clear the openai key)
        must still hit the delete path for a known provider."""
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
        """The validation runs BEFORE the empty-value branch, so
        ``store_secret("openai_v2", "")`` is REJECTED (return False) —
        the empty-value delete path is not reached. Callers that need
        to clear a stale orphaned entry for a deprecated / typo'd name
        must use ``delete_secret`` directly (which iterates
        ``_KNOWN_PROVIDERS_HISTORY``)."""
        # Pre-populate the keyring with an orphaned entry (simulating a
        # pre-validation store_secret call).
        mock_keyring_available["store"][(credential_store.KEYRING_SERVICE_NAME, "openai_v2")] = "sk-orphan-pre-existing"

        # store_secret with empty value → rejected (validation first).
        result = credential_store.store_secret("openai_v2", "")
        assert result is False, (
            "store_secret('openai_v2', '') must be rejected by the validation — "
            "the empty-value delete path is NOT reached for unknown providers"
        )
        # The orphaned entry is STILL there — store_secret didn't delete it.
        # (delete_secret is the cleanup path, covered by TestDeleteSecretOrphanCleanup.)
        store = mock_keyring_available["store"]
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "openai_v2")) == "sk-orphan-pre-existing", (
            "store_secret('openai_v2', '') must NOT delete the orphaned entry — "
            "the validation rejects the call before the empty-value branch runs"
        )


# ── _KNOWN_PROVIDERS_HISTORY frozenset ────────────────────────────


class TestKnownProvidersHistory:
    """``_KNOWN_PROVIDERS_HISTORY`` is a module-level frozenset that's a
    superset of ``PROVIDER_TO_CONFIG_FIELD`` keys plus any historical /
    deprecated / typo'd provider names. The GDPR delete path
    (``delete_secret``) iterates it to clean up orphaned keychain
    entries."""

    def test_history_is_frozenset(self):
        """The history must be a ``frozenset`` (immutable, hashable) so
        it can be safely iterated without risk of mid-iteration
        mutation, and so it can be used as a dict key if needed."""
        assert isinstance(credential_store._KNOWN_PROVIDERS_HISTORY, frozenset), (
            f"_KNOWN_PROVIDERS_HISTORY must be a frozenset; got: "
            f"{type(credential_store._KNOWN_PROVIDERS_HISTORY).__name__}"
        )

    def test_history_includes_all_current_providers(self):
        """Every key in ``PROVIDER_TO_CONFIG_FIELD`` must be in
        ``_KNOWN_PROVIDERS_HISTORY`` — the history is a SUPERSET of the
        current providers. This is the minimum invariant; deprecated
        names are ADDITIVE on top."""
        current = set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys())
        history = set(credential_store._KNOWN_PROVIDERS_HISTORY)
        assert current.issubset(history), (
            f"_KNOWN_PROVIDERS_HISTORY must include all current providers; missing: {current - history}"
        )

    def test_history_is_module_level_attribute(self, monkeypatch):
        """The history must be a module-level attribute (not a function
        local) so tests can monkey-patch it to inject test-only
        deprecated names. This verifies the monkey-patch pattern works
        and is restored after the test."""
        test_history = frozenset({"openai", "groq", "deepgram", "cloud", "llm", "openai_v2"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)
        assert credential_store._KNOWN_PROVIDERS_HISTORY is test_history
        # monkeypatch restores the original after the test (verified by
        # the next test's assertion against the unmodified history).

    def test_history_is_in_all(self):
        """``_KNOWN_PROVIDERS_HISTORY`` must be listed in ``__all__`` so
        it's part of the module's public API (tests and the privacy
        service can rely on it)."""
        assert "_KNOWN_PROVIDERS_HISTORY" in credential_store.__all__, (
            "_KNOWN_PROVIDERS_HISTORY must be in __all__ so it's part of the public API"
        )


# ── delete_secret orphan-cleanup via _KNOWN_PROVIDERS_HISTORY ─────


class TestDeleteSecretOrphanCleanup:
    """``delete_secret`` iterates ``_KNOWN_PROVIDERS_HISTORY`` and
    deletes any orphaned keychain entries for historical / deprecated /
    typo'd provider names.

    The privacy service's GDPR delete loop iterates
    ``PROVIDER_TO_CONFIG_FIELD`` (the 5 current providers) and calls
    ``delete_secret(provider, config=...)`` for each. Pre-fix,
    ``delete_secret`` only deleted the specific provider's keychain
    entry — so entries stored under names NOT in
    ``PROVIDER_TO_CONFIG_FIELD`` (e.g. a deprecated ``"polisher"``
    name, or a typo'd ``"openai_v2"`` from a pre-validation
    ``store_secret`` call) would persist in the OS keychain
    indefinitely (macOS Keychain survives app uninstall).

    Post-fix, ``delete_secret`` iterates ``_KNOWN_PROVIDERS_HISTORY``
    AFTER deleting the specific provider, and deletes each historical /
    deprecated entry from the keychain (best-effort, idempotent).
    """

    def test_delete_secret_cleans_up_orphaned_deprecated_entry(self, mock_keyring_available, monkeypatch):
        """``delete_secret("openai")`` must ALSO delete an orphaned
        ``"polisher"`` entry (a plausible deprecated name for the LLM
        polisher that's now ``"llm"``) when ``"polisher"`` is in
        ``_KNOWN_PROVIDERS_HISTORY``."""
        # Inject the deprecated name into the history (simulating a
        # prior app version that used "polisher" before renaming to "llm").
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        # Pre-populate the keyring with BOTH a current entry and an
        # orphaned deprecated entry.
        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-current-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-orphaned-polisher"

        # Act: delete the openai entry. The orphan-cleanup must ALSO
        # delete the "polisher" entry.
        credential_store.delete_secret("openai")

        # Assert: both entries are gone.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store, (
            "delete_secret('openai') must delete the openai keychain entry"
        )
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store, (
            "delete_secret('openai') must ALSO delete the orphaned 'polisher' entry "
            "(via _KNOWN_PROVIDERS_HISTORY iteration) — closing the GDPR orphan gap"
        )

    def test_delete_secret_cleans_up_typo_entry(self, mock_keyring_available, monkeypatch):
        """``delete_secret("openai")`` must ALSO delete an orphaned
        ``"openai_v2"`` typo entry when it's in
        ``_KNOWN_PROVIDERS_HISTORY``."""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"openai_v2"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai_v2")] = "sk-typo-orphan"

        credential_store.delete_secret("openai")

        assert (credential_store.KEYRING_SERVICE_NAME, "openai_v2") not in store, (
            "delete_secret('openai') must delete the orphaned 'openai_v2' typo entry"
        )

    def test_delete_secret_cleans_up_multiple_orphans(self, mock_keyring_available, monkeypatch):
        """``delete_secret`` must clean up ALL deprecated entries in
        ``_KNOWN_PROVIDERS_HISTORY``, not just the first one."""
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
        """The history iteration must SKIP current providers (those in
        ``PROVIDER_TO_CONFIG_FIELD``) — the privacy service's per-provider
        loop handles them, so re-deleting would be redundant. This test
        verifies the skip by checking that ``delete_secret("openai")``
        does NOT delete the ``"groq"`` entry (groq is a current provider
        that the privacy service's loop would handle separately)."""
        # History includes a deprecated name + all current providers.
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "groq")] = "sk-groq"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-polisher"

        credential_store.delete_secret("openai")

        # openai was the specific provider → deleted.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        # polisher is a deprecated name in the history → deleted by the
        # orphan-cleanup iteration.
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store
        # groq is a CURRENT provider → SKIPPED by the orphan-cleanup
        # iteration (the privacy service's per-provider loop handles it).
        # It must still be present (delete_secret("openai") must NOT
        # delete groq).
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "groq")) == "sk-groq", (
            "delete_secret('openai') must NOT delete the 'groq' entry — "
            "groq is a current provider and the orphan-cleanup iteration skips "
            "current providers (handled by the privacy service's per-provider loop)"
        )

    def test_delete_secret_no_op_when_history_has_no_deprecated_names(self, mock_keyring_available):
        """When ``_KNOWN_PROVIDERS_HISTORY`` has no deprecated names
        (only current providers), the orphan-cleanup iteration is a
        no-op — ``delete_secret`` just deletes the specific provider.
        This is the default state of the production code (no deprecated
        names are known yet)."""
        # Default history (just current providers, no deprecated names).
        assert frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) == credential_store._KNOWN_PROVIDERS_HISTORY

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        # Inject a phantom entry that's NOT in the history.
        store[(credential_store.KEYRING_SERVICE_NAME, "phantom_typo")] = "sk-phantom"

        credential_store.delete_secret("openai")

        # openai was deleted.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        # phantom_typo is NOT in the history → NOT deleted (it remains
        # an orphan — the fix only cleans up KNOWN deprecated names).
        # This is the documented limitation: unknown typo'd names that
        # aren't in the history persist (the secretstorage enumeration
        # approach for fully-unknown names is a separate, larger scope).
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "phantom_typo")) == "sk-phantom", (
            "delete_secret must NOT delete a phantom typo entry that's not in "
            "_KNOWN_PROVIDERS_HISTORY — the fix only cleans up KNOWN deprecated names"
        )

    def test_delete_secret_orphan_cleanup_is_idempotent(self, mock_keyring_available, monkeypatch):
        """Calling ``delete_secret`` twice must not raise — the second
        call's orphan-cleanup iteration attempts to delete
        already-deleted entries, which is a no-op
        (``PasswordDeleteError`` / KeyError is caught and logged at
        debug)."""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-polisher"

        # First call — deletes openai + polisher.
        credential_store.delete_secret("openai")
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store

        # Second call — must NOT raise. The orphan-cleanup iteration
        # attempts to delete "polisher" again (already gone) — the
        # fake backend's delete_password uses store.pop(..., None) which
        # is a no-op for missing keys.
        credential_store.delete_secret("groq")  # different provider, same history iteration

        # Both openai and polisher are still gone.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store

    def test_delete_secret_orphan_cleanup_via_store_secret_empty_value(self, mock_keyring_available, monkeypatch):
        """``store_secret("openai", "")`` calls ``delete_secret("openai")``
        (the empty-value branch), which must ALSO trigger the
        orphan-cleanup iteration. This verifies the cleanup runs on the
        user-initiated single-provider clear path (not just the GDPR
        bulk-delete path)."""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        store = mock_keyring_available["store"]
        store[(credential_store.KEYRING_SERVICE_NAME, "openai")] = "sk-openai"
        store[(credential_store.KEYRING_SERVICE_NAME, "polisher")] = "sk-polisher-orphan"

        # store_secret with empty value → delete path → orphan cleanup.
        result = credential_store.store_secret("openai", "")
        assert result is True, "store_secret('openai', '') must return True (delete path)"

        # Both openai and the orphaned polisher entry are gone.
        assert (credential_store.KEYRING_SERVICE_NAME, "openai") not in store
        assert (credential_store.KEYRING_SERVICE_NAME, "polisher") not in store, (
            "store_secret('openai', '') → delete_secret('openai') must ALSO clean up "
            "the orphaned 'polisher' entry via the history iteration"
        )

    def test_delete_secret_orphan_cleanup_works_when_keyring_unavailable(self, mock_keyring_unavailable, monkeypatch):
        """When keyring is unavailable, the orphan-cleanup iteration is
        SKIPPED (it's inside the ``if is_keyring_available():`` block),
        so ``delete_secret`` just does the plaintext-fallback clear.
        This is the correct behavior — there's nothing to delete from a
        non-existent keyring. The test verifies no exception is raised
        and the plaintext-fallback path still runs."""
        test_history = frozenset(set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) | {"polisher"})
        monkeypatch.setattr(credential_store, "_KNOWN_PROVIDERS_HISTORY", test_history)

        # Must not raise — the unavailable keyring means the orphan
        # iteration is skipped, but the plaintext-fallback clear still
        # runs (and clears the config.json field for the specific
        # provider).
        credential_store.delete_secret("openai")
