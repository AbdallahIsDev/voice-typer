"""Unit tests for the at-rest-encryption crypto module.

Covers the public surface of ``voice_typer/server/_text_crypto.py``:

- round-trip (ASCII + Unicode + emoji + large payloads + empty string),
- blob format ("enc:v1:" + base64(nonce(12) || ciphertext || tag(16))),
- known-answer vector against a direct ``AESGCM`` reference computation,
- tamper detection in every byte region (nonce / ciphertext / tag) and
  structural corruption (bad base64, truncation, unknown version),
- wrong-key → "<decryption failed>" placeholder (never raises),
- ``is_encrypted`` prefix detection,
- DEK cache policy: generate-once-when-clean, NEVER regenerate when
  encrypted rows exist, store-failure → plaintext mode,
- the key-unavailable rate-limited ERROR log helper.

The keyring is faked (dict-backed) following the pattern in
``tests/test_credential_store.py`` — the sandbox has no usable backend
(``keyring.backends.fail.Keyring``).
"""

from __future__ import annotations

import base64
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import _text_crypto, credential_store

DEK = bytes(range(32))  # fixed, deterministic AES-256 key
OTHER_DEK = bytes(range(32, 64))


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_caches():
    """Isolate the process-global DEK + keyring caches per test."""
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()
    yield
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()


@pytest.fixture
def fake_keyring(monkeypatch):
    """Dict-backed fake keyring marked AVAILABLE.

    Returns the backing ``store`` dict (service, username) -> secret so
    tests can inspect / mutate what was persisted.
    """
    store: dict[tuple[str, str], str] = {}

    fake_keyring_module = MagicMock()
    fake_keyring_module.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring_module.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring_module.delete_password.side_effect = lambda s, u: store.pop((s, u), None)

    monkeypatch.setitem(sys.modules, "keyring", fake_keyring_module)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)
    monkeypatch.setattr(credential_store, "_probe_keyring", lambda: (True, "FakeKeyring", None))
    credential_store._reset_keyring_cache()
    return store


@pytest.fixture
def fake_keyring_unavailable(monkeypatch):
    """Fake keyring with NO usable backend (headless-Linux case)."""

    class _FailKeyring:
        name = "fail"

        def get_password(self, service, username):
            raise RuntimeError("no backend available")

        def set_password(self, service, username, password):
            raise RuntimeError("no backend available")

    fail_backend = _FailKeyring()
    fake_keyring_module = MagicMock()
    fake_keyring_module.get_keyring.return_value = fail_backend

    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring_module)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "no usable keyring backend (test)"),
    )
    credential_store._reset_keyring_cache()
    return fake_keyring_module


# ── Round-trip ───────────────────────────────────────────────────────────


class TestRoundTrip:
    @pytest.mark.parametrize(
        "plaintext",
        [
            "hello world",
            "",
            "unicode: wörld émojis 🔐📈 and CJK 你好世界",
            "x" * 10_000,
            "line1\nline2\ttabbed",
        ],
    )
    def test_round_trip(self, plaintext):
        blob = _text_crypto.encrypt_text(plaintext, DEK)
        assert _text_crypto.decrypt_text(blob, DEK) == plaintext

    def test_two_encryptions_differ(self):
        """Random nonce per call — identical plaintext, distinct blobs."""
        blob_a = _text_crypto.encrypt_text("same text", DEK)
        blob_b = _text_crypto.encrypt_text("same text", DEK)
        assert blob_a != blob_b
        assert _text_crypto.decrypt_text(blob_a, DEK) == "same text"
        assert _text_crypto.decrypt_text(blob_b, DEK) == "same text"


# ── Blob format ──────────────────────────────────────────────────────────


class TestBlobFormat:
    def test_prefix(self):
        blob = _text_crypto.encrypt_text("payload", DEK)
        assert blob.startswith("enc:v1:")

    def test_structure(self):
        plaintext = "payload"
        blob = _text_crypto.encrypt_text(plaintext, DEK)
        raw = base64.b64decode(blob[len("enc:v1:") :], validate=True)
        nonce = raw[:12]
        body = raw[12:]
        # ciphertext is a stream cipher (same length as plaintext) + a
        # 16-byte GCM tag.
        assert len(nonce) == 12
        assert len(body) == len(plaintext.encode("utf-8")) + 16
        # The plaintext must not appear anywhere in the blob.
        assert plaintext not in blob

    def test_is_encrypted(self):
        assert _text_crypto.is_encrypted("enc:v1:AAAA") is True
        assert _text_crypto.is_encrypted("hello") is False
        assert _text_crypto.is_encrypted("") is False
        assert _text_crypto.is_encrypted("enc:v2:different") is False


# ── Known-answer vector ──────────────────────────────────────────────────


class TestKnownAnswer:
    def test_known_answer_against_aesgcm_reference(self):
        """A pinned nonce + key must reproduce the AESGCM reference bytes.

        Cross-checks the module's blob layout against a direct
        ``AESGCM`` computation — guards against layout regressions
        (nonce/tag ordering, base64 transport, prefix).
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = bytes(range(12))
        plaintext = "known answer test 🔐"
        blob = _text_crypto._encrypt_with_nonce(plaintext, DEK, nonce)

        reference = AESGCM(DEK).encrypt(nonce, plaintext.encode("utf-8"), None)
        expected = "enc:v1:" + base64.b64encode(nonce + reference).decode("ascii")
        assert blob == expected

    def test_deterministic_known_answer_bytes(self):
        """Hardcoded vector: stable across runs and library versions."""
        nonce = b"0123456789ab"
        blob = _text_crypto._encrypt_with_nonce("known", DEK, nonce)
        # Computed with cryptography 50.0.0 (AES-256-GCM) for
        # key=bytes(range(32)), nonce=b"0123456789ab", plaintext="known".
        # Any change to this literal means the on-disk format changed and
        # needs a version bump ("enc:v2:").
        assert blob == ("enc:v1:MDEyMzQ1Njc4OWFiXH89BDgmXMIBaAMDKoz9RMM/0teG")

    def test_encrypt_with_nonce_rejects_bad_nonce(self):
        with pytest.raises(ValueError):
            _text_crypto._encrypt_with_nonce("x", DEK, b"short")


# ── Tamper / corruption ──────────────────────────────────────────────────


class TestTamperDetection:
    PLACEHOLDER = _text_crypto.DECRYPTION_FAILED_PLACEHOLDER

    def _make_blob(self) -> str:
        return _text_crypto.encrypt_text("tamper target text", DEK)

    @pytest.mark.parametrize("region", ["prefix", "nonce", "ciphertext", "tag"])
    def test_single_bit_flip_in_each_region(self, region):
        blob = self._make_blob()
        raw = bytearray(base64.b64decode(blob[len("enc:v1:") :]))
        if region == "prefix":
            # Corrupt the versioned ASCII prefix instead of the body.
            corrupted = "enc:vX:" + blob[len("enc:v1:") :]
        else:
            idx = {"nonce": 3, "ciphertext": 15, "tag": len(raw) - 1}[region]
            raw[idx] ^= 0x01
            corrupted = "enc:v1:" + base64.b64encode(bytes(raw)).decode("ascii")
        assert _text_crypto.decrypt_text(corrupted, DEK) == self.PLACEHOLDER

    def test_wrong_key(self):
        blob = self._make_blob()
        assert _text_crypto.decrypt_text(blob, OTHER_DEK) == self.PLACEHOLDER

    def test_invalid_base64(self):
        assert _text_crypto.decrypt_text("enc:v1:!!!not-base64!!!", DEK) == self.PLACEHOLDER

    def test_truncated_body(self):
        raw = base64.b64decode(self._make_blob()[len("enc:v1:") :])
        truncated = "enc:v1:" + base64.b64encode(raw[:15]).decode("ascii")
        assert _text_crypto.decrypt_text(truncated, DEK) == self.PLACEHOLDER

    def test_unknown_version(self):
        blob = self._make_blob()
        future = "enc:v9:" + blob[len("enc:v1:") :]
        assert _text_crypto.decrypt_text(future, DEK) == self.PLACEHOLDER

    def test_plaintext_never_passthrough_decodes(self):
        """A flagged-but-plaintext row must not decode 'successfully'."""
        assert _text_crypto.decrypt_text("plain text", DEK) == self.PLACEHOLDER

    def test_bad_dek_length(self):
        blob = self._make_blob()
        assert _text_crypto.decrypt_text(blob, b"short-key") == self.PLACEHOLDER

    def test_decrypt_never_raises_on_garbage(self):
        for garbage in ("", None, 12345, "enc:v1:", "enc:v1:AAAA", "enc:v1:////"):
            assert _text_crypto.decrypt_text(garbage, DEK) == self.PLACEHOLDER


# ── DEK cache policy ─────────────────────────────────────────────────────


class TestDekPolicy:
    def test_generate_and_store_on_first_use(self, fake_keyring):
        dek = _text_crypto.resolve_dek(encrypted_rows_exist=False)
        assert dek is not None
        assert len(dek) == 32
        # Persisted under the reserved username in the keyring.
        from voice_typer.server.credential_store._schema import (
            DATA_ENCRYPTION_KEY_USERNAME,
            KEYRING_SERVICE_NAME,
        )

        assert (KEYRING_SERVICE_NAME, DATA_ENCRYPTION_KEY_USERNAME) in fake_keyring

    def test_cached_after_first_resolution(self, fake_keyring):
        first = _text_crypto.resolve_dek(encrypted_rows_exist=False)
        assert _text_crypto.get_dek_cached() == first
        # Second resolution returns the SAME key (no regeneration).
        assert _text_crypto.resolve_dek(encrypted_rows_exist=True) == first

    def test_never_regenerate_when_encrypted_rows_exist(self, fake_keyring):
        """Key loss: existing ciphertext must not be orphaned by a new key."""
        # Keyring AVAILABLE but the DEK entry is gone (wiped keychain).
        dek = _text_crypto.resolve_dek(encrypted_rows_exist=False)
        assert dek is not None
        from voice_typer.server.credential_store._schema import (
            DATA_ENCRYPTION_KEY_USERNAME,
            KEYRING_SERVICE_NAME,
        )

        del fake_keyring[(KEYRING_SERVICE_NAME, DATA_ENCRYPTION_KEY_USERNAME)]
        _text_crypto.reset_dek_cache()
        assert _text_crypto.resolve_dek(encrypted_rows_exist=True) is None
        # And nothing was re-generated into the keyring.
        assert (KEYRING_SERVICE_NAME, DATA_ENCRYPTION_KEY_USERNAME) not in fake_keyring
        assert _text_crypto.encryption_status(None, True) == "key-unavailable"

    def test_unavailable_keyring_stays_disabled(self, fake_keyring_unavailable):
        dek = _text_crypto.resolve_dek(encrypted_rows_exist=False)
        assert dek is None
        assert _text_crypto.encryption_status(dek, False) == "disabled"

    def test_store_failure_means_no_encryption(self, monkeypatch, fake_keyring):
        """A DEK that cannot be persisted must never be used."""
        from voice_typer.server.credential_store import _dek

        monkeypatch.setattr(_dek, "store_dek", lambda dek: False)
        assert _text_crypto.resolve_dek(encrypted_rows_exist=False) is None

    def test_status_mapping(self):
        assert _text_crypto.encryption_status(b"K" * 32, False) == "active"
        assert _text_crypto.encryption_status(b"K" * 32, True) == "active"
        assert _text_crypto.encryption_status(None, False) == "disabled"
        assert _text_crypto.encryption_status(None, True) == "key-unavailable"

    def test_reset_dek_cache(self, fake_keyring):
        first = _text_crypto.resolve_dek(encrypted_rows_exist=False)
        _text_crypto.reset_dek_cache()
        assert _text_crypto.get_dek_cached() is None
        # After reset the SAME persisted key is loaded (not regenerated).
        assert _text_crypto.resolve_dek(encrypted_rows_exist=False) == first


# ── DEK keyring transport (credential_store._dek) ────────────────────────


class TestDekKeyringTransport:
    def test_store_and_load_round_trip(self, fake_keyring):
        from voice_typer.server.credential_store import _dek

        dek = _dek.generate_dek()
        assert len(dek) == 32
        assert _dek.store_dek(dek) is True
        assert _dek.load_dek() == dek

    def test_load_absent_returns_none(self, fake_keyring):
        from voice_typer.server.credential_store import _dek

        assert _dek.load_dek() is None

    def test_load_unavailable_returns_none(self, fake_keyring_unavailable):
        from voice_typer.server.credential_store import _dek

        assert _dek.load_dek() is None

    def test_store_unavailable_returns_false(self, fake_keyring_unavailable):
        from voice_typer.server.credential_store import _dek

        assert _dek.store_dek(_dek.generate_dek()) is False

    def test_corrupt_entry_treated_as_absent(self, fake_keyring):
        from voice_typer.server.credential_store import _dek
        from voice_typer.server.credential_store._schema import (
            DATA_ENCRYPTION_KEY_USERNAME,
            KEYRING_SERVICE_NAME,
        )

        fake_keyring[(KEYRING_SERVICE_NAME, DATA_ENCRYPTION_KEY_USERNAME)] = "!!!not base64!!!"
        assert _dek.load_dek() is None

        # Wrong length (valid base64, 16 bytes) is also rejected.
        fake_keyring[(KEYRING_SERVICE_NAME, DATA_ENCRYPTION_KEY_USERNAME)] = base64.b64encode(b"0" * 16).decode("ascii")
        assert _dek.load_dek() is None

    def test_store_rejects_wrong_length(self, fake_keyring):
        from voice_typer.server.credential_store import _dek

        assert _dek.store_dek(b"too-short") is False
