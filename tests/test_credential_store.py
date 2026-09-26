"""Tests for the encrypted credential store."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    # Reset the cache before AND after each test so a stale "available"
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


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

    # Patch the keyring module import inside credential_store.
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    # Also patch the fail backend module so _probe_keyring can import it.
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    # Force is_keyring_available to re-probe and return True.
    credential_store._reset_keyring_cache()

    # Patch _probe_keyring to return our fake backend.
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "FakeKeyring", None),
    )

    return {"store": store, "backend": backend, "keyring": fake_keyring}


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing)."""

    # The fail backend, _probe_keyring checks isinstance(backend, FailKeyring)
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
    # Force the probe to fail.
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
    )
    return fake_keyring


@pytest.fixture
def mock_keyring_raises_on_set(monkeypatch):
    """Mock keyring as available for probing but raising on set_password."""
    fake_keyring = MagicMock()

    class _SelectableBackend:
        """Selectable (not the fail backend) but raises on set."""

        name = "BrokenKeyring"

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            raise RuntimeError("keychain locked")

        def delete_password(self, service, username):
            raise RuntimeError("keychain locked")

    fake_keyring.get_keyring.return_value = _SelectableBackend()
    fake_keyring.set_password.side_effect = RuntimeError("keychain locked")
    fake_keyring.get_password.return_value = None
    fake_keyring.delete_password.side_effect = RuntimeError("keychain locked")

    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "BrokenKeyring", None),
    )
    return fake_keyring


class TestStoreLoadDelete:
    """Tests for store_secret / load_secret / delete_secret."""

    def test_store_secret_calls_keyring_with_right_args(self, mock_keyring_available):
        """store_secret should call keyring.set_password with"""
        result = credential_store.store_secret("openai", "sk-test-12345")
        assert result is True
        mock_keyring_available["keyring"].set_password.assert_called_once_with(
            credential_store.KEYRING_SERVICE_NAME, "openai", "sk-test-12345"
        )
        # Verify the secret is retrievable
        assert credential_store.load_secret("openai") == "sk-test-12345"

    def test_load_secret_calls_keyring_with_right_args(self, mock_keyring_available):
        """load_secret should call keyring.get_password with"""
        credential_store.store_secret("groq", "gsk_abc")
        result = credential_store.load_secret("groq")
        assert result == "gsk_abc"
        mock_keyring_available["keyring"].get_password.assert_called_with(credential_store.KEYRING_SERVICE_NAME, "groq")

    def test_load_secret_returns_none_when_not_set(self, mock_keyring_available):
        """load_secret should return None for a provider that was never set."""
        assert credential_store.load_secret("deepgram") is None

    def test_delete_secret_calls_keyring_delete(self, mock_keyring_available):
        """delete_secret should call keyring.delete_password."""
        credential_store.store_secret("openai", "sk-test")
        credential_store.delete_secret("openai")
        mock_keyring_available["keyring"].delete_password.assert_called_with(
            credential_store.KEYRING_SERVICE_NAME, "openai"
        )
        # Secret is gone from keyring
        assert credential_store.load_secret("openai") is None

    def test_store_secret_empty_value_deletes(self, mock_keyring_available):
        """store_secret with an empty value should delete the secret"""
        credential_store.store_secret("openai", "sk-test")
        credential_store.store_secret("openai", "")
        assert credential_store.load_secret("openai") is None

    def test_delete_secret_idempotent_when_not_set(self, mock_keyring_available):
        """delete_secret on a never-set provider should not raise."""
        # Should not raise
        credential_store.delete_secret("llm")

    def test_store_secret_falls_back_to_config_json_on_keyring_failure(self, mock_keyring_raises_on_set, tmp_path):
        """When keyring raises, store_secret should fall back to writing"""
        result = credential_store.store_secret("openai", "sk-fallback-test")
        assert result is False  # False = fell back to plaintext

        # Verify config.json contains the plaintext value
        config_file = tmp_path / "config.json"
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "sk-fallback-test"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only 0o600 check")
    def test_plaintext_fallback_uses_0600_permissions(self, mock_keyring_raises_on_set, tmp_path):
        """The plaintext fallback must write config.json with 0o600 perms"""
        credential_store.store_secret("openai", "sk-fallback-test")
        config_file = tmp_path / "config.json"
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

    def test_load_secret_falls_back_to_config_json(self, mock_keyring_unavailable, tmp_path):
        """When keyring is unavailable, load_secret should read from"""
        # Pre-populate config.json with a plaintext value
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-from-config"}))

        result = credential_store.load_secret("openai")
        assert result == "sk-from-config"

    def test_load_secret_returns_none_for_keyring_reference_without_keyring(self, mock_keyring_unavailable, tmp_path):
        """A keyring:// reference token in config.json means the real"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "keyring://openai"}))
        result = credential_store.load_secret("openai")
        assert result is None

    def test_store_secret_never_logs_value(self, mock_keyring_raises_on_set, caplog):
        """Secret values must NEVER appear in log messages, only"""
        secret = "sk-super-secret-DO-NOT-LOG-1234567890"
        with caplog.at_level("WARNING", logger="voice_typer.server.credential_store"):
            credential_store.store_secret("openai", secret)

        # The secret value must not appear in any log record
        for record in caplog.records:
            assert secret not in record.getMessage(), f"Secret value leaked in log: {record.getMessage()!r}"

    def test_load_secret_falls_back_when_keyring_returns_none(self, mock_keyring_available, tmp_path):
        """If keyring returns None (secret not in keychain), load_secret"""
        # Pre-populate config.json with a plaintext value, but don't
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-mid-migration"}))

        result = credential_store.load_secret("openai")
        assert result == "sk-mid-migration"


class TestMigrateSecretsToKeyring:
    """Tests for the one-time plaintext → keyring migration."""

    def test_migrate_moves_plaintext_to_keyring(self, mock_keyring_available, tmp_path):
        """migrate_secrets_to_keyring should read plaintext API keys"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-migrate-me",
                    "groq_api_key": "gsk-migrate-me",
                    "deepgram_api_key": "",
                    "hotkey": "<caps_lock>",
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 2  # openai + groq migrated; deepgram was empty

        # Verify config.json now has reference tokens
        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "keyring://openai"
        assert data["groq_api_key"] == "keyring://groq"
        assert data["deepgram_api_key"] == ""  # still empty
        assert data["hotkey"] == "<caps_lock>"  # untouched
        assert data["secrets_migrated"] is True

        # Verify keyring has the secrets
        assert credential_store.load_secret("openai") == "sk-migrate-me"
        assert credential_store.load_secret("groq") == "gsk-migrate-me"

    def test_migrate_is_idempotent(self, mock_keyring_available, tmp_path):
        """Running migrate twice should not double-store or re-migrate"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-migrate-me"}))

        first = credential_store.migrate_secrets_to_keyring()
        assert first == 1

        # Capture the post-first-migration state
        data_after_first = json.loads(config_file.read_text())

        second = credential_store.migrate_secrets_to_keyring()
        assert second == 0

        data_after_second = json.loads(config_file.read_text())
        assert data_after_first == data_after_second

    def test_migrate_skips_already_migrated_references(self, mock_keyring_available, tmp_path):
        """a prior migration that didn't set the flag), migrate should"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "keyring://openai",
                    "groq_api_key": "gsk-still-plaintext",
                    "secrets_migrated": False,  # flag missing → run migration
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 1  # only groq needed migration

        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "keyring://openai"  # unchanged
        assert data["groq_api_key"] == "keyring://groq"  # migrated
        assert data["secrets_migrated"] is True

    def test_migrate_keeps_plaintext_when_keyring_unavailable(self, mock_keyring_unavailable, tmp_path):
        """user's keys still work. Per XZ-SEC-04, the ``secrets_migrated``"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-keep-me"}))

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 0  # nothing moved to keyring

        # Plaintext value preserved
        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "sk-keep-me"
        assert "secrets_migrated" not in data, (
            "XZ-SEC-04 regression: secrets_migrated must NOT be set when "
            "keyring was unavailable AND real plaintext was skipped, "
            "otherwise the next launch would skip migration and the "
            "plaintext would persist forever."
        )
        assert data.get("secrets_migrated_keyring_was_unavailable") is True, (
            "XZ-SEC-04: the secrets_migrated_keyring_was_unavailable "
            "diagnostic flag must be set so operators can see why "
            "migration was deferred."
        )

    def test_migrate_handles_missing_config_file(self, mock_keyring_available, tmp_path):
        """If config.json doesn't exist, migrate should write a minimal"""
        config_file = tmp_path / "config.json"
        assert not config_file.exists()

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 0

        # A minimal config.json should now exist with the flag set
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["secrets_migrated"] is True

    def test_migrate_handles_corrupt_config_file(self, mock_keyring_available, tmp_path, caplog):
        """If config.json is corrupt JSON, migrate should log a warning"""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

        with caplog.at_level("WARNING", logger="voice_typer.server.credential_store"):
            migrated = credential_store.migrate_secrets_to_keyring()

        assert migrated == 0
        # The warning should mention "cannot parse config.json"
        assert any("cannot parse config.json" in r.getMessage() for r in caplog.records)

    def test_migrate_preserves_unrelated_config_fields(self, mock_keyring_available, tmp_path):
        """Migration must not touch non-secret config fields."""
        config_file = tmp_path / "config.json"
        original = {
            "openai_api_key": "sk-test",
            "hotkey": "<f5>",
            "model_size": "small.en",
            "autostart": True,
            "language": "fr",
            "streaming_chunk_seconds": 12.0,
        }
        config_file.write_text(json.dumps(original))

        credential_store.migrate_secrets_to_keyring()

        data = json.loads(config_file.read_text())
        # Non-secret fields untouched
        assert data["hotkey"] == "<f5>"
        assert data["model_size"] == "small.en"
        assert data["autostart"] is True
        assert data["language"] == "fr"
        assert data["streaming_chunk_seconds"] == 12.0
        # Secret field migrated
        assert data["openai_api_key"] == "keyring://openai"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only 0o600 check")
    def test_migrate_preserves_0600_perms(self, mock_keyring_available, tmp_path):
        """enforces this, but verify it end-to-end)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-test"}))
        # Set 0o600 explicitly to match the production save() path
        os.chmod(config_file, 0o600)

        credential_store.migrate_secrets_to_keyring()

        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"


class TestKeyringStatus:
    """Tests for the keyring availability probe and status reporting."""

    def test_status_available_when_keyring_works(self, mock_keyring_available):
        """get_keyring_status should return available=True when a real"""
        status = credential_store.get_keyring_status()
        assert status["available"] is True
        assert status["fallback"] is False
        assert status["backend"] == "FakeKeyring"
        assert status["reason"] is None

    def test_status_unavailable_when_keyring_missing(self, mock_keyring_unavailable):
        """get_keyring_status should return available=False when only"""
        status = credential_store.get_keyring_status()
        assert status["available"] is False
        assert status["fallback"] is True
        assert status["reason"] is not None
        assert "fail" in status["reason"].lower() or "no usable" in status["reason"].lower()

    def test_status_reason_is_string_not_secret(self, mock_keyring_unavailable):
        """The reason string should be a short diagnostic message,"""
        status = credential_store.get_keyring_status()
        assert isinstance(status["reason"], str)
        # Reason should not contain anything that looks like an API key
        assert "sk-" not in status["reason"]

    def test_is_keyring_available_caches_result(self, mock_keyring_available):
        """is_keyring_available should cache the probe result for the"""
        # First call probes
        first = credential_store.is_keyring_available()
        assert first is True
        # Second call should return cached value without re-probing
        assert credential_store._keyring_available_cache is True


class TestProviderMapping:
    """Tests for the provider ↔ Config field name mapping."""

    def test_all_providers_have_config_field(self):
        """Every provider in PROVIDER_TO_CONFIG_FIELD should map to a"""
        from voice_typer.server.config import Config

        for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
            assert field_name in Config.__dataclass_fields__, (
                f"provider {provider!r} maps to non-existent field {field_name!r}"
            )

    def test_reverse_mapping_is_consistent(self):
        """CONFIG_FIELD_TO_PROVIDER should be the exact inverse of"""
        for provider, field in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
            assert credential_store.CONFIG_FIELD_TO_PROVIDER[field] == provider
        assert len(credential_store.CONFIG_FIELD_TO_PROVIDER) == len(credential_store.PROVIDER_TO_CONFIG_FIELD)

    def test_expected_providers_are_present(self):
        """The five known providers (openai / groq / deepgram / cloud / llm)"""
        expected = {"openai", "groq", "deepgram", "cloud", "llm"}
        assert set(credential_store.PROVIDER_TO_CONFIG_FIELD.keys()) == expected

    def test_reference_token_format(self):
        """The keyring:// reference prefix should produce tokens of the"""
        for provider in credential_store.PROVIDER_TO_CONFIG_FIELD:
            token = f"{credential_store.KEYRING_REF_PREFIX}{provider}"
            assert token.startswith("keyring://")
            assert token.endswith(provider)


class TestRedactSensitive:
    """Tests for the _redact_sensitive defense-in-depth helper."""

    def test_redact_strips_posix_home_path(self):
        """A /home/<user> path should be replaced with [path]."""
        s = "D-Bus error: /home/alice/.cache/keyring failed to load"
        redacted = credential_store._redact_sensitive(s)
        assert "/home/alice" not in redacted
        assert "[path]" in redacted

    def test_redact_strips_macos_home_path(self):
        """A /Users/<user> path should be replaced with [path]."""
        s = "Keychain error: /Users/bob/Library/Keychain/login.keychain"
        redacted = credential_store._redact_sensitive(s)
        assert "/Users/bob" not in redacted
        assert "[path]" in redacted

    def test_redact_strips_windows_user_path(self):
        """A C:\\Users\\<user> path should be replaced with [path]."""
        s = r"Failed: C:\Users\carol\AppData\Local\keyring"
        redacted = credential_store._redact_sensitive(s)
        assert "C:\\Users\\carol" not in redacted
        assert "[path]" in redacted

    def test_redact_strips_api_key_with_sk_prefix(self):
        """An OpenAI-style sk-... key should be replaced with [redacted]."""
        s = "backend rejected: sk-abcdefghij1234567890XYZ"
        redacted = credential_store._redact_sensitive(s)
        assert "sk-abcdefghij1234567890XYZ" not in redacted
        assert "[redacted]" in redacted

    def test_redact_strips_api_key_with_gsk_prefix(self):
        """A Groq-style gsk_... key should be replaced with [redacted]."""
        s = "error: gsk_1234567890abcdefghijklmnop"
        redacted = credential_store._redact_sensitive(s)
        assert "gsk_1234567890abcdefghijklmnop" not in redacted
        assert "[redacted]" in redacted

    def test_redact_strips_long_alphanumeric_run(self):
        """A 32+ char alphanumeric run (bearer-token-like) should be"""
        s = "token: abcdefghijklmnopqrstuvwxyz123456 invalid"
        redacted = credential_store._redact_sensitive(s)
        assert "abcdefghijklmnopqrstuvwxyz123456" not in redacted
        assert "[redacted]" in redacted

    def test_redact_truncates_long_strings(self):
        """_REASON_MAX_LEN chars (with a '...' suffix) so the renderer"""
        # 14 chars * 20 = 280 chars, with spaces, no alphanumeric run
        long_str = "session bus error " * 20
        assert len(long_str) > credential_store._REASON_MAX_LEN
        redacted = credential_store._redact_sensitive(long_str)
        assert len(redacted) == credential_store._REASON_MAX_LEN
        assert redacted.endswith("...")

    def test_redact_preserves_short_strings(self):
        """Short, clean diagnostic strings should pass through unchanged."""
        s = "no usable keyring backend (fail backend selected)"
        assert credential_store._redact_sensitive(s) == s

    def test_redact_none_passes_through(self):
        """None should pass through unchanged (so callers can pass"""
        assert credential_store._redact_sensitive(None) is None

    def test_redact_empty_string_passes_through(self):
        """Empty string should pass through unchanged."""
        assert credential_store._redact_sensitive("") == ""


class TestGetKeyringStatusConsistency:
    """Tests that get_keyring_status returns a consistent cached snapshot."""

    def test_status_returns_cached_reason_without_reprobing(self, mock_keyring_unavailable, monkeypatch):
        """When the cache is populated, get_keyring_status should NOT"""
        # First call populates the cache.
        status1 = credential_store.get_keyring_status()
        assert status1["available"] is False
        assert status1["reason"] is not None
        cached_reason = status1["reason"]

        sentinel_called = []

        def _sentinel_probe():
            sentinel_called.append(True)
            return (False, "DIFFERENT_BACKEND", "DIFFERENT_REASON")

        monkeypatch.setattr(credential_store, "_probe_keyring", _sentinel_probe)

        status2 = credential_store.get_keyring_status()
        assert sentinel_called == [], "get_keyring_status should NOT re-probe when cache is populated"
        assert status2["reason"] == cached_reason
        assert status2["backend"] == status1["backend"]

    def test_status_reason_is_redacted(self, monkeypatch):
        """redacted form (not the raw reason)."""
        credential_store._reset_keyring_cache()
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (
                False,
                "BrokenBackend",
                "D-Bus error at /home/alice/.cache/dbus rejected token sk-abcdefghij1234567890",
            ),
        )
        status = credential_store.get_keyring_status()
        assert status["available"] is False
        assert status["reason"] is not None
        assert "/home/alice" not in status["reason"]
        assert "sk-abcdefghij1234567890" not in status["reason"]
        assert "[path]" in status["reason"]
        assert "[redacted]" in status["reason"]


class TestLoadStoreNeverLeakViaException:
    """secret value in its exception message, load_secret / store_secret"""

    def test_load_secret_redacts_secret_in_exception_log(self, monkeypatch, caplog):
        """
        If keyring.get_password raises with the secret value embedded
        WARNING log from load_secret must NOT contain the secret.
        """
        secret = "sk-DO-NOT-LEAK-1234567890abcdef"

        # Make is_keyring_available() return True without going through
        monkeypatch.setattr(credential_store, "_probe_keyring", lambda: (True, "BuggyBackend", None))
        credential_store._reset_keyring_cache()

        # Inject a fake keyring whose get_password raises an exception
        fake_keyring = MagicMock()
        fake_keyring.get_password.side_effect = RuntimeError(f"backend crashed; last seen value was {secret}")
        fake_keyring.set_password.side_effect = RuntimeError("unused")
        fake_keyring.delete_password.side_effect = RuntimeError("unused")
        monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

        with caplog.at_level("WARNING", logger="voice_typer.server.credential_store"):
            result = credential_store.load_secret("openai")

        assert result is None  # no plaintext fallback available
        for record in caplog.records:
            assert secret not in record.getMessage(), f"Secret leaked in log: {record.getMessage()!r}"

    def test_store_secret_redacts_secret_in_exception_log(self, monkeypatch, caplog):
        """If keyring.set_password raises with the secret value embedded"""
        secret = "sk-DO-NOT-LEAK-1234567890abcdef"

        monkeypatch.setattr(credential_store, "_probe_keyring", lambda: (True, "BuggyBackend", None))
        credential_store._reset_keyring_cache()

        fake_keyring = MagicMock()
        fake_keyring.set_password.side_effect = RuntimeError(f"keychain rejected {secret}")
        fake_keyring.get_password.return_value = None
        fake_keyring.delete_password.side_effect = RuntimeError("unused")
        monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

        with caplog.at_level("WARNING", logger="voice_typer.server.credential_store"):
            result = credential_store.store_secret("openai", secret)

        assert result is False  # fell back to plaintext
        for record in caplog.records:
            assert secret not in record.getMessage(), f"Secret leaked in log: {record.getMessage()!r}"


class TestMigrationMidFailureSafety:
    """
    Tests that migration preserves all secrets even when keyring
    Contract: a secret is EITHER in keyring OR in config.json, never
    """

    def test_migrate_preserves_failed_provider_plaintext(self, monkeypatch, tmp_path):
        """If keyring succeeds for provider A but fails for provider B,"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-openai-migrate-ok",
                    "groq_api_key": "gsk-groq-migrate-fail",
                }
            )
        )

        # Build a fake keyring that succeeds for openai but fails for groq.
        store: dict[tuple[str, str], str] = {}

        def _set_password(service, username, password):
            if username == "groq":
                raise RuntimeError("keychain locked for groq")
            store[(service, username)] = password

        def _get_password(service, username):
            return store.get((service, username))

        def _delete_password(service, username):
            store.pop((service, username), None)

        fake_keyring = MagicMock()
        fake_keyring.set_password.side_effect = _set_password
        fake_keyring.get_password.side_effect = _get_password
        fake_keyring.delete_password.side_effect = _delete_password
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

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 1  # only openai migrated

        assert store.get((credential_store.KEYRING_SERVICE_NAME, "openai")) == ("sk-openai-migrate-ok")
        assert (credential_store.KEYRING_SERVICE_NAME, "groq") not in store

        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "keyring://openai"
        assert data["groq_api_key"] == "gsk-groq-migrate-fail"
        # ``secrets_migrated`` must NOT be set, the next launch
        assert "secrets_migrated" not in data, (
            "XE-3-2: secrets_migrated must NOT be set when set_password "
            "raised mid-migration, the next launch must re-attempt so "
            "the failed provider eventually migrates."
        )
        assert data.get("secrets_migrated_keyring_was_unavailable") is True

    def test_migrate_no_data_loss_when_atomic_write_fails(self, monkeypatch, tmp_path):
        """If the final _secure_atomic_write fails AFTER keyring has"""
        config_file = tmp_path / "config.json"
        original_data = {"openai_api_key": "sk-migrate-me"}
        config_file.write_text(json.dumps(original_data))

        store: dict[tuple[str, str], str] = {}
        fake_keyring = MagicMock()
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

        # Make _secure_atomic_write fail (simulating disk full / perm
        from voice_typer.server import config as config_mod

        def _failing_write(path, content):
            raise OSError("disk full")

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _failing_write)

        migrated = credential_store.migrate_secrets_to_keyring()
        # Migration counted the secret as moved (set_password succeeded)
        assert migrated == 1

        # Secret IS in keyring
        assert store.get((credential_store.KEYRING_SERVICE_NAME, "openai")) == ("sk-migrate-me")

        # Original config.json is UNTOUCHED (atomic write failed before
        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "sk-migrate-me"


class TestReferenceTokenUnforgeability:
    """Tests that a malicious config.json cannot redirect a field's"""

    def test_load_secret_uses_provider_arg_not_token_suffix(self, mock_keyring_available):
        """provider arg is what determines the keyring lookup key."""
        # Store distinct secrets for each provider.
        credential_store.store_secret("openai", "sk-openai-real")
        credential_store.store_secret("llm", "sk-llm-real")

        assert credential_store.load_secret("openai") == "sk-openai-real"
        assert credential_store.load_secret("llm") == "sk-llm-real"

        # Verify the keyring was queried with the right provider arg.
        calls = mock_keyring_available["keyring"].get_password.call_args_list
        service_provider_pairs = [(call.args[0], call.args[1]) for call in calls]
        assert (
            credential_store.KEYRING_SERVICE_NAME,
            "openai",
        ) in service_provider_pairs
        assert (
            credential_store.KEYRING_SERVICE_NAME,
            "llm",
        ) in service_provider_pairs
        # A buggy "load openai's secret by reading the llm entry" would

    def test_config_field_to_provider_is_exact_inverse(self):
        """PROVIDER_TO_CONFIG_FIELD. This is the contract that makes"""
        for provider, field in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
            assert credential_store.CONFIG_FIELD_TO_PROVIDER[field] == provider
        # No field maps to two providers (no ambiguity).
        fields = list(credential_store.PROVIDER_TO_CONFIG_FIELD.values())
        assert len(fields) == len(set(fields)), (
            "duplicate field names in PROVIDER_TO_CONFIG_FIELD, "
            "a malicious config could redirect one field to another provider's secret"
        )


class TestMultiProviderConcurrentAccess:
    """Smoke test for multi-provider concurrent access."""

    def test_concurrent_multi_provider_store_load(self, mock_keyring_available):
        """Store all five providers' secrets in rapid succession, then"""
        secrets = {
            "openai": "sk-openai-1234567890",
            "groq": "gsk-groq-0987654321",
            "deepgram": "dg-deepgram-abcdef",
            "cloud": "cl-cloud-xyz123",
            "llm": "llm-llm-qwerty789",
        }

        # Store all
        for provider, value in secrets.items():
            assert credential_store.store_secret(provider, value) is True

        # Load all and verify
        for provider, expected in secrets.items():
            assert credential_store.load_secret(provider) == expected, (
                f"load_secret({provider!r}) returned wrong value, "
                f"expected {expected!r}, got {credential_store.load_secret(provider)!r}"
            )

        # Verify each provider's entry in the keyring store is distinct
        store = mock_keyring_available["store"]
        for provider, expected in secrets.items():
            key = (credential_store.KEYRING_SERVICE_NAME, provider)
            assert store[key] == expected, (
                f"keyring store has wrong value for {provider!r}: expected {expected!r}, got {store[key]!r}"
            )


def _install_fake_keyring(monkeypatch, *, available: bool):
    """Install a dict-backed fake keyring module + fail backend module."""
    store: dict[tuple[str, str], str] = {}

    class _FakeBackend:
        name = "FakeKeyring"

        def get_password(self, service, username):
            return store.get((service, username))

        def set_password(self, service, username, password):
            store[(service, username)] = password

        def delete_password(self, service, username):
            store.pop((service, username), None)

    class _FailKeyring:
        name = "fail"

        def get_password(self, service, username):
            raise RuntimeError("no backend available")

        def set_password(self, service, username, password):
            raise RuntimeError("no backend available")

        def delete_password(self, service, username):
            raise RuntimeError("no backend available")

    backend = _FakeBackend() if available else _FailKeyring()
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = backend
    fake_keyring.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring.delete_password.side_effect = lambda s, u: store.pop((s, u), None)

    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = _FailKeyring if not available else type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    if available:
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (True, "FakeKeyring", None),
        )
    else:
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
        )
    return {"store": store, "backend": backend, "keyring": fake_keyring}


class TestSecretsMigratedGating:
    """``secrets_migrated`` must NOT be set when keyring was"""

    def test_migrate_retried_after_keyring_becomes_available(self, monkeypatch, tmp_path):
        """End-to-end regression for the bug."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-retry-me"}))

        _install_fake_keyring(monkeypatch, available=False)
        first = credential_store.migrate_secrets_to_keyring()
        assert first == 0  # nothing moved

        data_after_first = json.loads(config_file.read_text())
        assert data_after_first["openai_api_key"] == "sk-retry-me"  # plaintext kept
        assert "secrets_migrated" not in data_after_first  # NOT gated
        assert data_after_first["secrets_migrated_keyring_was_unavailable"] is True

        _install_fake_keyring(monkeypatch, available=True)
        second = credential_store.migrate_secrets_to_keyring()
        assert second == 1  # migrated this time

        data_after_second = json.loads(config_file.read_text())
        assert data_after_second["openai_api_key"] == "keyring://openai"
        assert data_after_second["secrets_migrated"] is True
        assert "secrets_migrated_keyring_was_unavailable" not in data_after_second

    def test_migrate_sets_flag_when_no_plaintext_to_skip(self, mock_keyring_unavailable, tmp_path):
        """If keyring is unavailable BUT there are no real plaintext"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "",  # empty, nothing to migrate
                    "groq_api_key": "keyring://groq",  # already a reference
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 0

        data = json.loads(config_file.read_text())
        # Flag IS set because no real plaintext was skipped.
        assert data["secrets_migrated"] is True
        # Diagnostic flag NOT set (no skip happened).
        assert "secrets_migrated_keyring_was_unavailable" not in data

    def test_migrate_diagnostic_flag_cleared_on_successful_completion(self, mock_keyring_available, tmp_path):
        """A successful migration (keyring available, secrets moved)"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-migrate-me",
                    "secrets_migrated_keyring_was_unavailable": True,
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 1

        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "keyring://openai"
        assert data["secrets_migrated"] is True
        # Stale diagnostic flag cleared.
        assert "secrets_migrated_keyring_was_unavailable" not in data

    def test_migrate_skips_when_secrets_migrated_already_set(self, mock_keyring_available, tmp_path):
        """If ``secrets_migrated`` is already True (prior successful"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-should-not-be-touched",
                    "secrets_migrated": True,
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 0

        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "sk-should-not-be-touched"
        assert data["secrets_migrated"] is True


class TestLoadSecretAuditLog:
    """keyring-success path so repeated secret reads (e.g. by a compromised"""

    def test_load_secret_emits_info_log_on_keyring_success(self, mock_keyring_available, caplog):
        """A successful ``load_secret`` from keyring must emit an INFO"""
        credential_store.store_secret("openai", "sk-audit-log-test-12345")
        # Clear store-side INFO log so we can isolate the load-side log.
        caplog.clear()

        with caplog.at_level(logging.INFO, logger="voice_typer.server.credential_store"):
            result = credential_store.load_secret("openai")

        assert result == "sk-audit-log-test-12345"
        # Exactly one INFO record from load_secret (the audit log).
        audit_records = [
            r for r in caplog.records if r.levelno == logging.INFO and "loaded secret for provider" in r.getMessage()
        ]
        assert len(audit_records) == 1, (
            f"expected exactly one load_secret INFO audit log, got {[r.getMessage() for r in audit_records]}"
        )
        msg = audit_records[0].getMessage()
        assert "openai" in msg
        assert "keyring" in msg
        # Length is logged for diagnostics, verify it's present.
        assert str(len("sk-audit-log-test-12345")) in msg

    def test_load_secret_audit_log_does_not_leak_value(self, mock_keyring_available, caplog):
        """The INFO audit log must NOT contain the secret value, only"""
        secret = "sk-DO-NOT-LEAK-IN-AUDIT-1234567890"
        credential_store.store_secret("openai", secret)
        caplog.clear()

        with caplog.at_level(logging.INFO, logger="voice_typer.server.credential_store"):
            credential_store.load_secret("openai")

        for record in caplog.records:
            assert secret not in record.getMessage(), f"Secret value leaked in audit log: {record.getMessage()!r}"

    def test_load_secret_no_info_log_on_plaintext_fallback(self, mock_keyring_unavailable, tmp_path, caplog):
        """reading from ``config.json``, no INFO audit log is emitted"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-from-config"}))

        with caplog.at_level(logging.INFO, logger="voice_typer.server.credential_store"):
            result = credential_store.load_secret("openai")

        assert result == "sk-from-config"
        # No INFO audit log on the fallback path.
        audit_records = [
            r for r in caplog.records if r.levelno == logging.INFO and "loaded secret for provider" in r.getMessage()
        ]
        assert audit_records == []

    def test_load_secret_no_info_log_when_keyring_returns_none(self, mock_keyring_available, caplog):
        """plaintext fallback is also empty, no INFO audit log is emitted"""
        with caplog.at_level(logging.INFO, logger="voice_typer.server.credential_store"):
            result = credential_store.load_secret("deepgram")

        assert result is None
        audit_records = [
            r for r in caplog.records if r.levelno == logging.INFO and "loaded secret for provider" in r.getMessage()
        ]
        assert audit_records == []


class TestAlreadyFixedVerifications:
    """Smoke tests verifying that fixes already applied,"""

    def test_xz_sec_02_write_plaintext_fallback_uses_config_lock(self):
        """``_write_plaintext_fallback`` must"""
        import inspect

        src = inspect.getsource(credential_store._write_plaintext_fallback)
        assert "_acquire_config_lock" in src, (
            "regression: _write_plaintext_fallback no longer "
            "acquires _acquire_config_lock, concurrent Config.save() / "
            "delete_secret could clobber the field written here."
        )
        assert "with _acquire_config_lock()" in src, (
            "regression: _acquire_config_lock must be used as a "
            "context manager ('with _acquire_config_lock():') wrapping the "
            "read-modify-write body."
        )

    def test_xz_sec_07_redact_sensitive_delegates_to_secrets(self):
        """``_redact_sensitive`` must"""
        # GitLab PATs / GitHub PATs / Slack legacy tokens are 20-28 chars.
        s = "token: abcd1234efgh5678ijkl9012"
        redacted = credential_store._redact_sensitive(s)
        assert "abcd1234efgh5678ijkl9012" not in redacted, (
            "regression: 24-char bare token was NOT redacted, "
            "_redact_sensitive may have reverted to its old 32+ char threshold "
            "instead of delegating to _secrets.redact_api_keys (20+ char)."
        )
        assert "[redacted]" in redacted

    def test_xz_sec_08_service_name_is_reverse_dns(self):
        """``KEYRING_SERVICE_NAME`` must be the canonical"""
        assert credential_store.KEYRING_SERVICE_NAME == "com.Lausu.keyring", (
            "regression: KEYRING_SERVICE_NAME no longer uses the canonical "
            "com.Lausu.* reverse-DNS root, another app registering "
            "the same service name could read Lausu secrets, and "
            "the product-namespace drift guard "
            "(tests/test_product_namespace_consistency.py) would fail."
        )
        # Legacy names must include the bare form so one-time migration
        assert "lausu" in credential_store._LEGACY_KEYRING_SERVICE_NAMES


class TestLegacyServiceNameCutover:
    """``_migrate_legacy_service_names_locked`` re-registers keyring"""

    def test_copies_entries_from_all_legacy_names(self, monkeypatch):
        """current service name, and the legacy entries are deleted."""
        harness = _install_fake_keyring(monkeypatch, available=True)
        store = harness["store"]

        current = credential_store.KEYRING_SERVICE_NAME
        legacy_names = credential_store._LEGACY_KEYRING_SERVICE_NAMES
        assert len(legacy_names) >= 2, (
            "the bare legacy name AND the prior reverse-DNS name must both be listed (reverse-chronological)"
        )
        for legacy in legacy_names:
            store[(legacy, "openai")] = f"sk-{legacy}"

        copied = credential_store._migrate_legacy_service_names_locked()

        assert copied == len(legacy_names), "every legacy entry must be copied forward"
        # Last-processed legacy name wins (loop order is reverse-
        last_legacy = legacy_names[-1]
        assert store[(current, "openai")] == f"sk-{last_legacy}", (
            "the current service name must hold the migrated entry"
        )
        for legacy in legacy_names:
            assert (legacy, "openai") not in store, f"legacy entry under {legacy!r} must be deleted after cutover"


class TestNonStringApiKeySkippedGracefully:
    """DE-23: ``migrate_secrets_to_keyring`` must skip a corrupted /"""

    def test_dict_api_key_does_not_crash_migration(self, mock_keyring_available, tmp_path):
        """A ``dict`` value for ``openai_api_key`` (hand-edited config)"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    # Corrupted: openai_api_key is a dict instead of str.
                    "openai_api_key": {"secret": "sk-leaked"},
                    # Valid plaintext: must still be migrated.
                    "groq_api_key": "groq-sk-migrate-me",
                }
            )
        )

        # Must not raise.
        migrated = credential_store.migrate_secrets_to_keyring()

        assert migrated == 1, (
            "DE-23: migration should have migrated the valid groq_api_key "
            "(1 secret) even when openai_api_key is a non-string value."
        )

        data = json.loads(config_file.read_text())
        # The corrupted dict value must be preserved (we don't touch it).
        assert data["openai_api_key"] == {"secret": "sk-leaked"}
        assert data["groq_api_key"] == "keyring://groq"
        assert data["secrets_migrated"] is True

    def test_int_api_key_does_not_crash_migration(self, mock_keyring_available, tmp_path):
        """An ``int`` value for an api_key field must be skipped, not"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": 12345,  # int, non-string
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 0  # nothing to migrate (the int was skipped)

        data = json.loads(config_file.read_text())
        # The int value is preserved (we don't touch what we can't migrate).
        assert data["openai_api_key"] == 12345
        assert data["secrets_migrated"] is True

    def test_list_api_key_logs_warning_and_skips(self, mock_keyring_available, tmp_path, caplog):
        """A ``list`` value for an api_key field must log a WARNING so"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": ["sk-should-not-be-here"],
                }
            )
        )

        with caplog.at_level(logging.WARNING, logger=credential_store.log.name):
            credential_store.migrate_secrets_to_keyring()

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("openai_api_key" in msg and "non-string" in msg for msg in warning_msgs), (
            "DE-23: a WARNING must be logged when a non-string api_key "
            f"value is skipped. Got warnings: {warning_msgs!r}"
        )
