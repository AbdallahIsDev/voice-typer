"""Regression tests for ``Config._save_unlocked`` plaintext data-loss fix.

Pre-fix (around ``voice_typer/server/config.py::_save_unlocked``)::

    credential_store.store_secret(provider, value, _caller_holds_config_lock=True)
    data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"

The return value of :func:`credential_store.store_secret` was IGNORED.
``store_secret`` returns:

* ``True``  — secret committed to the OS keychain (or deleted via the
  empty-value path).
* ``False`` — keyring was unavailable or errored; the secret was
  written to ``config.json`` as a plaintext fallback.

By unconditionally overwriting ``data[field_name]`` with the
``keyring://<provider>`` reference token — EVEN WHEN ``store_secret``
returned ``False`` — the final
``_secure_atomic_write(config_file, content)`` at the end of
``_save_unlocked`` persisted the REFERENCE TOKEN instead of the
plaintext value. The plaintext that
:func:`credential_store._write_plaintext_fallback` had just written was
CLOBBERED by the final write. The user's API key was silently dropped
from disk (the keyring didn't have it, and config.json now contained a
``keyring://openai`` reference that pointed at a keyring the user did
not have).

Post-fix::

    stored_to_keyring = credential_store.store_secret(
        provider, value, _caller_holds_config_lock=True
    )
    if stored_to_keyring:
        data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"
    # else: leave data[field_name] as the plaintext value — the final
    # _secure_atomic_write persists it in one write.

Tests
-----

1. ``test_save_preserves_plaintext_and_single_write_on_keyring_set_password_failure``
   — simulates a keyring ``set_password`` failure during ``Config.save()``.
   Asserts (a) only one ``config.json`` write occurs (the final
   ``_secure_atomic_write`` from ``_save_unlocked``), and (b) the
   plaintext secret is preserved in config.json after save (NOT
   replaced with a ``keyring://<provider>`` reference token).

2. ``test_save_replaces_with_keyring_reference_on_success`` — when
   ``store_secret`` returns ``True`` (keyring success), the on-disk
   field IS replaced with the ``keyring://<provider>`` reference token
   (the happy path is preserved — no regression).

3. ``test_save_preserves_plaintext_via_real_store_secret_fallback`` —
   end-to-end regression: real ``store_secret`` with a broken keyring
   (NO mock on ``_write_plaintext_fallback``). Verifies the FINAL
   on-disk config.json contains the plaintext value (the redundant
   ``_write_plaintext_fallback`` write is overwritten by the final
   write, which now contains the plaintext rather than the reference
   token).
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache so each test re-probes
    (the probe is cached at module level for the lifetime of the
    process, which would leak state across tests).
    """
    from voice_typer.server import credential_store

    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


@pytest.fixture
def keyring_raises_on_set(monkeypatch):
    """Mock keyring as available for probing but raising on ``set_password``.

    Simulates the case where the backend is selected (so
    ``is_keyring_available()`` returns True and ``Config._save_unlocked``
    routes the API key through ``store_secret``) but the actual write
    fails — e.g. the Keychain is locked, D-Bus dropped mid-call, or the
    secret-service helper crashed. ``store_secret`` catches the
    exception, falls back to ``_write_plaintext_fallback``, and returns
    ``False``.
    """
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
    """Mock keyring as available with an in-memory store that succeeds.

    Used to verify the happy path (``store_secret`` returns ``True`` →
    on-disk field IS replaced with the ``keyring://<provider>``
    reference token).
    """
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


# ── Tests ───────────────────────────────────────────────────────────────


class TestConfigSaveUnlockedDataLoss:
    """Verify ``Config._save_unlocked`` preserves the plaintext secret
    when ``store_secret`` returns ``False`` (keyring failure → plaintext
    fallback)."""

    def test_save_preserves_plaintext_and_single_write_on_keyring_set_password_failure(
        self, tmp_path, monkeypatch, keyring_raises_on_set
    ):
        """Simulate a keyring ``set_password`` failure during
        ``Config.save()`` and assert:

        (a) Only ONE ``config.json`` write occurs (the final
            ``_secure_atomic_write`` from ``_save_unlocked``). The
            redundant per-provider read-modify-write inside
            ``credential_store._write_plaintext_fallback`` is mocked
            to a no-op so this assertion isolates the
            ``_save_unlocked`` behavior: the fix must persist the
            plaintext value via a SINGLE final write, not by relying
            on ``_write_plaintext_fallback``'s separate write (which
            was overwritten by the final write pre-fix, causing data
            loss).

        (b) The plaintext secret is preserved in config.json after
            save — NOT replaced with a ``keyring://openai`` reference
            token. Pre-fix, the final write replaced the plaintext
            with the reference token, clobbering the value that
            ``_write_plaintext_fallback`` had just written and
            silently dropping the user's API key.
        """
        from voice_typer.server import config as config_mod, credential_store
        from voice_typer.server.config import Config

        # Mock _write_plaintext_fallback to a no-op so the redundant
        # write is eliminated from the count. This isolates the test
        # to the _save_unlocked behavior: when store_secret returns
        # False, _save_unlocked must persist the plaintext via its
        # OWN final _secure_atomic_write (not rely on
        # _write_plaintext_fallback's separate write).
        monkeypatch.setattr(
            credential_store,
            "_write_plaintext_fallback",
            lambda provider, value, *, caller_holds_config_lock=False: None,
        )

        # Wrap _secure_atomic_write to count calls AND forward to the
        # real implementation so config.json actually lands on disk
        # (the test asserts on the post-save on-disk content).
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
        # and config.json.lock acquires — those are not "config.json
        # writes" in the data-loss sense).
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

        # Assertion (b): the plaintext secret is preserved in
        # config.json (NOT replaced with the keyring://openai
        # reference token).
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
            "reference token even though keyring failed — the user "
            "has no keyring, so the reference points at nothing and "
            "the secret is effectively lost."
        )

    def test_save_replaces_with_keyring_reference_on_success(self, tmp_path, keyring_succeeds):
        """Happy-path regression: when ``store_secret`` returns ``True``
        (keyring success), the on-disk ``openai_api_key`` field IS
        replaced with the ``keyring://openai`` reference token. This
        must not regress — the fix only changes the ``False`` branch.
        """
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
        """End-to-end regression: real ``store_secret`` with a broken
        keyring (NO mock on ``_write_plaintext_fallback`` — the
        redundant per-provider read-modify-write DOES run).

        This test verifies the data-preservation aspect of the fix
        end-to-end: even with the redundant ``_write_plaintext_fallback``
        write happening inside ``store_secret``, the FINAL on-disk
        config.json must contain the plaintext value (not the
        ``keyring://openai`` reference token).

        Pre-fix, the final ``_secure_atomic_write`` from
        ``_save_unlocked`` overwrote the plaintext (written by
        ``_write_plaintext_fallback``) with the reference token —
        silently dropping the user's API key. Post-fix, the final
        write contains the plaintext (because ``data[field_name]``
        was left as the plaintext value when ``store_secret``
        returned ``False``).
        """
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        c = Config()
        c.openai_api_key = "sk-test-secret-endtoend"

        # Sanity: keyring is "available" but set_password raises, so
        # store_secret will fall back to _write_plaintext_fallback and
        # return False.
        assert credential_store.is_keyring_available() is True

        result = c.save()
        assert result is True

        # The FINAL on-disk config.json must contain the plaintext
        # value. Pre-fix, this was the reference token (data loss).
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
            "failed — the reference points at a keyring the user does "
            "not have, so the secret is effectively lost."
        )
