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
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store

# ── Fixtures (mirrors tests/test_credential_store.py) ───────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache and the plaintext config
    cache so each test re-probes / re-populates from scratch.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
