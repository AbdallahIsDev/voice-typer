"""Regression tests for ``Config._save_unlocked`` plaintext data-loss fix."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    from voice_typer.server import credential_store

    credential_store._reset_keyring_cache()
    with credential_store._keyring_state_lock:
        credential_store._backend._orphaned_thread_count = 0
        credential_store._backend._consecutive_timeouts = 0
        credential_store._backend._wedged_until = 0.0
    yield
    credential_store._reset_keyring_cache()
    with credential_store._keyring_state_lock:
        credential_store._backend._orphaned_thread_count = 0
        credential_store._backend._consecutive_timeouts = 0
        credential_store._backend._wedged_until = 0.0


@pytest.fixture
def keyring_raises_on_set(monkeypatch):
    """Mock keyring as available for probing but raising on ``set_password``."""
    from voice_typer.server import credential_store

    fake_keyring = MagicMock()

    class _BrokenBackend:
        name = "BrokenKeyring"

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            raise RuntimeError("keychain locked")

        def delete_password(self, service, username):
            raise RuntimeError("keychain locked")

    fake_keyring.get_keyring.return_value = _BrokenBackend()
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


@pytest.fixture
def keyring_succeeds(monkeypatch):
    """Mock keyring as available with an in-memory store that succeeds."""
    from voice_typer.server import credential_store

    store: dict[tuple[str, str], str] = {}

    class _FakeBackend:
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


class TestConfigSaveUnlockedDataLoss:
    """Verify ``Config._save_unlocked`` preserves the plaintext secret"""

    def test_save_preserves_plaintext_and_single_write_on_keyring_set_password_failure(
        self, tmp_path, monkeypatch, keyring_raises_on_set
    ):
        """Simulate a keyring ``set_password`` failure during"""
        from voice_typer.server import config as config_mod, credential_store
        from voice_typer.server.config import Config

        # Mock _write_plaintext_fallback to a no-op so the redundant
        monkeypatch.setattr(
            credential_store,
            "_write_plaintext_fallback",
            lambda provider, value, *, caller_holds_config_lock=False: None,
        )

        real_secure_atomic_write = config_mod._secure_atomic_write
        write_calls: list = []

        def counting_secure_atomic_write(path, content, **kwargs):
            write_calls.append((str(path), content))
            return real_secure_atomic_write(path, content, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_atomic_write", counting_secure_atomic_write)

        # Build a Config with a real-looking plaintext API key.
        c = Config()
        c.openai_api_key = "sk-test-secret-preserveme"

        # Sanity: keyring is "available" (probe) but set_password raises.
        assert credential_store.is_keyring_available() is True

        result = c.save()
        assert result is True, "Config.save() must succeed even when keyring fails"

        # Filter writes to config.json (exclude config.json.bak writes
        config_file_str = str(tmp_path / "config.json")
        config_json_writes = [(p, c) for (p, c) in write_calls if p == config_file_str]

        # Assertion (a): only ONE config.json write occurred.
        assert len(config_json_writes) == 1, (
            "Expected exactly 1 write to config.json (the final "
            "_secure_atomic_write from _save_unlocked). Got "
            f"{len(config_json_writes)} writes. The redundant "
            "_write_plaintext_fallback write was mocked to a no-op "
            "so this count isolates the _save_unlocked behavior. "
            f"Write paths: {[p for p, _ in write_calls]}"
        )

        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk.get("openai_api_key") == "sk-test-secret-preserveme", (
            "Data-loss regression: openai_api_key on disk should be "
            "the plaintext value (keyring failed → plaintext fallback). "
            "Pre-fix, the final _secure_atomic_write overwrote the "
            "plaintext with the 'keyring://openai' reference token, "
            f"silently dropping the user's API key. Got: {on_disk.get('openai_api_key')!r}"
        )
        # And it must NOT be the reference token.
        assert not on_disk["openai_api_key"].startswith(credential_store.KEYRING_REF_PREFIX), (
            "Data-loss regression: openai_api_key on disk was "
            f"replaced with a {credential_store.KEYRING_REF_PREFIX!r} "
            "reference token even though keyring failed, the user "
            "has no keyring, so the reference points at nothing and "
            "the secret is effectively lost."
        )

    def test_save_replaces_with_keyring_reference_on_success(self, tmp_path, keyring_succeeds):
        """Happy-path regression: when ``store_secret`` returns ``True``"""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        c = Config()
        c.openai_api_key = "sk-test-secret-keyringok"

        # Sanity: keyring is available and set_password succeeds.
        assert credential_store.is_keyring_available() is True

        result = c.save()
        assert result is True

        # The secret was stored in the (mocked) keyring.
        assert (
            credential_store.KEYRING_SERVICE_NAME,
            "openai",
        ) in keyring_succeeds["store"], (
            "Happy-path regression: store_secret should have deposited the secret in the keyring backend on success."
        )

        # The on-disk field is the reference token, NOT the plaintext.
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk["openai_api_key"] == "keyring://openai", (
            "Happy-path regression: on keyring success, the on-disk "
            "openai_api_key should be replaced with the "
            "'keyring://openai' reference token. Got: "
            f"{on_disk.get('openai_api_key')!r}"
        )
        # And the plaintext is NOT on disk.
        assert "sk-test-secret-keyringok" not in (tmp_path / "config.json").read_text(), (
            "Happy-path regression: the plaintext API key leaked to config.json even though keyring storage succeeded."
        )

    def test_save_preserves_plaintext_via_real_store_secret_fallback(self, tmp_path, keyring_raises_on_set):
        """End-to-end regression: real ``store_secret`` with a broken"""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        c = Config()
        c.openai_api_key = "sk-test-secret-endtoend"

        assert credential_store.is_keyring_available() is True

        result = c.save()
        assert result is True

        # The FINAL on-disk config.json must contain the plaintext
        on_disk = json.loads((tmp_path / "config.json").read_text())
        assert on_disk.get("openai_api_key") == "sk-test-secret-endtoend", (
            "End-to-end data-loss regression: after Config.save() with "
            "a broken keyring, the final config.json should contain "
            "the plaintext openai_api_key (the redundant "
            "_write_plaintext_fallback write is overwritten by the "
            "final _secure_atomic_write, which post-fix contains the "
            f"plaintext). Got: {on_disk.get('openai_api_key')!r}"
        )
        assert not on_disk["openai_api_key"].startswith(credential_store.KEYRING_REF_PREFIX), (
            "End-to-end data-loss regression: the final config.json "
            "contains a keyring:// reference token even though keyring "
            "failed, the reference points at a keyring the user does "
            "not have, so the secret is effectively lost."
        )
