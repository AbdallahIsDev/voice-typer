"""Regression tests for DE-2A credential_store.py fixes (Group 4: Security & Data).

Covers the findings from the comprehensive review that are fully contained
within ``voice_typer/server/credential_store.py``:

  - **``secrets_migrated`` flag was set
    unconditionally, so a system where keyring later became available
    would silently keep plaintext API keys in ``config.json`` forever.
    Fixed: when keyring is unavailable AND there were real plaintext
    secrets skipped, the ``secrets_migrated`` flag is NOT set; a
    separate ``secrets_migrated_keyring_was_unavailable`` diagnostic
    flag is recorded instead, so the next launch re-attempts migration
    automatically.

  - **``load_secret`` returned silently on the
    keyring-success path — a compromised process exfiltrating secrets
    via repeated ``load_secret`` calls left no trace in logs. Fixed:
    an INFO audit log is emitted on the keyring-success path matching
    the store-side format (provider + length only — never the value
    itself).

  - **Already-fixed verifications** for
    (``_write_plaintext_fallback`` acquires the config lock),
    (``_redact_sensitive`` delegates to
    ``_secrets.redact_api_keys``), and
    (``KEYRING_SERVICE_NAME`` is reverse-DNS) — these are smoke-tested
    here so a future regression to the pre-fix behavior is caught.

The fixtures mirror those in ``tests/test_credential_store.py`` so the
two test files share a consistent mocking convention (TEST-033).
"""

from __future__ import annotations

import json
import logging
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache so each test re-probes
    (the probe is cached at module level for the lifetime of the
    process, which would leak state across tests).
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


def _install_fake_keyring(monkeypatch, *, available: bool):
    """Install a dict-backed fake keyring module + fail backend module.

    Mirrors the conventions used by ``tests/test_credential_store.py``
    (TEST-033) so the two files share the same mocking style.
    """
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


@pytest.fixture
def mock_keyring_available(monkeypatch):
    """Mock keyring as available with an in-memory store."""
    return _install_fake_keyring(monkeypatch, available=True)


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing)."""
    return _install_fake_keyring(monkeypatch, available=False)


# secrets_migrated gating ──────────────────────────────────


class TestSecretsMigratedGating:
    """``secrets_migrated`` must NOT be set when keyring was
    unavailable AND real plaintext secrets were skipped — so the next
    launch re-attempts migration automatically once keyring becomes
    available.
    """

    def test_migrate_retried_after_keyring_becomes_available(self, monkeypatch, tmp_path):
        """End-to-end regression for the bug.

        Scenario: a system where keyring is unavailable on first launch
        (so plaintext API keys are kept) but becomes available on a
        later launch. Pre-fix, the first launch set
        ``secrets_migrated=True``, so the second launch skipped
        migration and plaintext keys persisted forever. Post-fix, the
        first launch leaves ``secrets_migrated`` unset and records
        ``secrets_migrated_keyring_was_unavailable=True``; the second
        launch (keyring now available) re-runs migration and moves the
        secrets to keyring.
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-retry-me"}))

        # ── First launch: keyring unavailable ──────────────────────────
        _install_fake_keyring(monkeypatch, available=False)
        first = credential_store.migrate_secrets_to_keyring()
        assert first == 0  # nothing moved

        data_after_first = json.loads(config_file.read_text())
        assert data_after_first["openai_api_key"] == "sk-retry-me"  # plaintext kept
        assert "secrets_migrated" not in data_after_first  # NOT gated
        assert data_after_first["secrets_migrated_keyring_was_unavailable"] is True

        # ── Second launch: keyring now available ───────────────────────
        _install_fake_keyring(monkeypatch, available=True)
        second = credential_store.migrate_secrets_to_keyring()
        assert second == 1  # migrated this time

        data_after_second = json.loads(config_file.read_text())
        assert data_after_second["openai_api_key"] == "keyring://openai"
        assert data_after_second["secrets_migrated"] is True
        # diagnostic flag cleared on clean completion.
        assert "secrets_migrated_keyring_was_unavailable" not in data_after_second

    def test_migrate_sets_flag_when_no_plaintext_to_skip(self, mock_keyring_unavailable, tmp_path):
        """If keyring is unavailable BUT there are no real plaintext
        secrets to migrate (all empty or already reference tokens),
        ``secrets_migrated`` IS set — there's nothing to retry, so
        leaving the gate open would cause pointless re-runs on every
        launch.
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "",  # empty — nothing to migrate
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
        """A successful migration (keyring available, secrets moved)
        must clear any stale ``secrets_migrated_keyring_was_unavailable``
        flag set by a prior run that hit the unavailable-keyring path.
        Otherwise the diagnostic would linger forever even after the
        user's secrets are safely in keyring.
        """
        config_file = tmp_path / "config.json"
        # Pre-populate config with both a real plaintext secret AND the
        # diagnostic flag from a prior (unavailable-keyring) run.
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
        """If ``secrets_migrated`` is already True (prior successful
        migration), the function must early-return 0 without re-running
        the per-provider loop. This is the idempotency gate used by
        Config.load — doesn't change this behavior (it only
        changes WHEN the flag is set on the unavailable-keyring path).
        """
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


# load_secret audit log ────────────────────────────────────


class TestLoadSecretAuditLog:
    """``load_secret`` must emit an INFO audit log on the
    keyring-success path so repeated secret reads (e.g. by a compromised
    process exfiltrating secrets) leave a trace in the log.

    The log must match the store-side format: provider name + value
    length only — NEVER the value itself.
    """

    def test_load_secret_emits_info_log_on_keyring_success(self, mock_keyring_available, caplog):
        """A successful ``load_secret`` from keyring must emit an INFO
        log record identifying the provider and the value length."""
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
        # Length is logged for diagnostics — verify it's present.
        assert str(len("sk-audit-log-test-12345")) in msg

    def test_load_secret_audit_log_does_not_leak_value(self, mock_keyring_available, caplog):
        """The INFO audit log must NOT contain the secret value — only
        provider name + length. Defense in depth alongside the existing
        store-side redaction."""
        secret = "sk-DO-NOT-LEAK-IN-AUDIT-1234567890"
        credential_store.store_secret("openai", secret)
        caplog.clear()

        with caplog.at_level(logging.INFO, logger="voice_typer.server.credential_store"):
            credential_store.load_secret("openai")

        for record in caplog.records:
            assert secret not in record.getMessage(), f"Secret value leaked in audit log: {record.getMessage()!r}"

    def test_load_secret_no_info_log_on_plaintext_fallback(self, mock_keyring_unavailable, tmp_path, caplog):
        """When keyring is unavailable and load_secret falls back to
        reading from ``config.json``, no INFO audit log is emitted
        (keyring-success-only). The plaintext fallback path is already
        silent by design — the audit log is specifically for keyring
        reads (which are the "secure" path a compromised process would
        target)."""
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
        """When keyring returns None (secret not in keychain) and the
        plaintext fallback is also empty, no INFO audit log is emitted
        (no successful keyring read happened)."""
        with caplog.at_level(logging.INFO, logger="voice_typer.server.credential_store"):
            result = credential_store.load_secret("deepgram")

        assert result is None
        audit_records = [
            r for r in caplog.records if r.levelno == logging.INFO and "loaded secret for provider" in r.getMessage()
        ]
        assert audit_records == []


# ── Already-fixed verifications (smoke tests) ───────────────────────────


class TestAlreadyFixedVerifications:
    """Smoke tests verifying that fixes already applied,
    remain in place. A future regression that
    re-introduces the pre-fix behavior will trip one of these.
    """

    def test_xz_sec_02_write_plaintext_fallback_uses_config_lock(self):
        """``_write_plaintext_fallback`` must
        acquire ``_acquire_config_lock()`` for the full read-modify-write.

        We verify by inspecting the function source — the
        ``_acquire_config_lock`` symbol must appear inside the function
        body. A regression that removes the lock would silently re-open
        the concurrent-``Config.save()``-vs-``store_secret`` race.
        """
        import inspect

        src = inspect.getsource(credential_store._write_plaintext_fallback)
        assert "_acquire_config_lock" in src, (
            "regression: _write_plaintext_fallback no longer "
            "acquires _acquire_config_lock — concurrent Config.save() / "
            "delete_secret could clobber the field written here."
        )
        assert "with _acquire_config_lock()" in src, (
            "regression: _acquire_config_lock must be used as a "
            "context manager ('with _acquire_config_lock():') wrapping the "
            "read-modify-write body."
        )

    def test_xz_sec_07_redact_sensitive_delegates_to_secrets(self):
        """``_redact_sensitive`` must
        delegate API-key redaction to ``_secrets.redact_api_keys`` (the
        canonical helper) rather than maintaining a divergent regex.

        Verified behaviorally: a 24-char bare alphanumeric token (which
        the old credential_store-local regex with 32+ char threshold
        would NOT redact) MUST be redacted, because the shared
        ``_KEY_PATTERNS`` uses 20+ chars.
        """
        # 24-char bare token — would slip past the old 32+ char threshold.
        # GitLab PATs / GitHub PATs / Slack legacy tokens are 20-28 chars.
        s = "token: abcd1234efgh5678ijkl9012"
        redacted = credential_store._redact_sensitive(s)
        assert "abcd1234efgh5678ijkl9012" not in redacted, (
            "regression: 24-char bare token was NOT redacted — "
            "_redact_sensitive may have reverted to its old 32+ char threshold "
            "instead of delegating to _secrets.redact_api_keys (20+ char)."
        )
        assert "[redacted]" in redacted

    def test_xz_sec_08_service_name_is_reverse_dns(self):
        """``KEYRING_SERVICE_NAME`` must be the canonical
        ``com.voicetyper.*`` reverse-DNS form (matching the bundle
        identifier / polkit action RDNN root), not the bare
        ``voice-typer`` that another app could register and use to read
        Voice Typer secrets."""
        assert credential_store.KEYRING_SERVICE_NAME == "com.voicetyper.keyring", (
            "regression: KEYRING_SERVICE_NAME no longer uses the canonical "
            "com.voicetyper.* reverse-DNS root — another app registering "
            "the same service name could read Voice Typer secrets, and "
            "the product-namespace drift guard "
            "(tests/test_product_namespace_consistency.py) would fail."
        )
        # Legacy names must include the bare form so one-time migration
        # can copy entries forward.
        assert "voice-typer" in credential_store._LEGACY_KEYRING_SERVICE_NAMES


# keyring service-name cutover ──────────────────────────────


class TestLegacyServiceNameCutover:
    """``_migrate_legacy_service_names_locked`` re-registers keyring
    entries from EVERY legacy service name (bare + prior reverse-DNS)
    under the current ``KEYRING_SERVICE_NAME`` and deletes the originals
    — the keyring half of the product-namespace migration (the polkit
    half lives in install_permissions.py)."""

    def test_copies_entries_from_all_legacy_names(self, monkeypatch):
        """Entries stored under each name in
        ``_LEGACY_KEYRING_SERVICE_NAMES`` are re-registered under the
        current service name, and the legacy entries are deleted."""
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
        # chronological).
        last_legacy = legacy_names[-1]
        assert store[(current, "openai")] == f"sk-{last_legacy}", (
            "the current service name must hold the migrated entry"
        )
        for legacy in legacy_names:
            assert (legacy, "openai") not in store, f"legacy entry under {legacy!r} must be deleted after cutover"


# non-string api_key value crashes migration ───────────────────


class TestNonStringApiKeySkippedGracefully:
    """DE-23: ``migrate_secrets_to_keyring`` must skip a corrupted /
    hand-edited ``api_key`` field whose value is a non-string type
    (dict, list, int) instead of crashing the entire migration loop
    with ``AttributeError: 'dict' object has no attribute 'startswith'``.

    Pre-fix, the crash propagated up through ``Config.load``'s except
    block, logged a warning, and never set ``secrets_migrated``, so
    the crash + warning repeated on every launch with no resolution
    path. Post-fix, the non-string field is logged + skipped and the
    remaining providers are still migrated.
    """

    def test_dict_api_key_does_not_crash_migration(self, mock_keyring_available, tmp_path):
        """A ``dict`` value for ``openai_api_key`` (hand-edited config)
        must not raise — migration must skip it and continue."""
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

        # groq was migrated; openai was skipped.
        assert migrated == 1, (
            "DE-23: migration should have migrated the valid groq_api_key "
            "(1 secret) even when openai_api_key is a non-string value."
        )

        data = json.loads(config_file.read_text())
        # The corrupted dict value must be preserved (we don't touch it).
        assert data["openai_api_key"] == {"secret": "sk-leaked"}
        # groq was migrated to a keyring:// reference.
        assert data["groq_api_key"] == "keyring://groq"
        # secrets_migrated MUST be set — otherwise the crash-loop bug
        # (the original  symptom) would persist on every launch
        # because the corrupted field would keep triggering the crash.
        assert data["secrets_migrated"] is True

    def test_int_api_key_does_not_crash_migration(self, mock_keyring_available, tmp_path):
        """An ``int`` value for an api_key field must be skipped, not
        crash with AttributeError."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": 12345,  # int — non-string
                }
            )
        )

        migrated = credential_store.migrate_secrets_to_keyring()
        assert migrated == 0  # nothing to migrate (the int was skipped)

        data = json.loads(config_file.read_text())
        # The int value is preserved (we don't touch what we can't migrate).
        assert data["openai_api_key"] == 12345
        # secrets_migrated IS set because no real plaintext was skipped
        # (matches the  "no plaintext to skip" path).
        assert data["secrets_migrated"] is True

    def test_list_api_key_logs_warning_and_skips(self, mock_keyring_available, tmp_path, caplog):
        """A ``list`` value for an api_key field must log a WARNING so
        the user can see which field is corrupted, then skip it."""
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

        # The warning must mention the provider + field name + type so
        # the user can locate the corrupted entry in their config.json.
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("openai_api_key" in msg and "non-string" in msg for msg in warning_msgs), (
            "DE-23: a WARNING must be logged when a non-string api_key "
            f"value is skipped. Got warnings: {warning_msgs!r}"
        )
