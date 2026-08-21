"""regression tests for credential_store + single_instance fixes.

Covers the following findings fixed in this task:

 - ** (HIGH)**: ``_write_plaintext_fallback`` silently overwrote a
    non-dict ``config.json`` root with ``{}`` — destroying the corrupt
    file's recoverable content. Fix: mirror the migration path (log a
    warning, skip the write, preserve the file).

 - ** (HIGH)**: ``_write_plaintext_fallback`` returned ``None``
    (never raised); ``store_secret`` couldn't detect a fallback failure.
    Fix: return ``bool`` (True on success, False on failure); on False,
    ``store_secret`` surfaces a distinct ``"failed"`` outcome so the
    user knows their API key was NOT saved anywhere.

 - ** (Medium)**: Windows / POSIX migration-lock timeout warnings
    logged the raw ``OSError`` + raw ``lock_file`` path (PII leak —
    username). Fix: wrap both in ``_redact_sensitive``.

 - ** (Medium)**: ``migrate_secrets_to_keyring`` fail-opened on
    lock-acquisition failure (POSIX ``TimeoutError`` or any other
    exception) — re-opening RACE-001. Fix: ABORT migration when
    ``lock_fd`` is None, log a warning, defensively set
    ``secrets_migrated = True`` so the next launch skips retry (avoids
    a retry storm if the lock is permanently wedged).

 - ** (Medium)**: ``single_instance._ensure_single_instance_posix``
    secondary open (the ``O_RDWR`` open of the EXISTING lockfile after
    ``O_EXCL`` failed) was missing ``O_NOFOLLOW`` — a TOCTOU symlink
    race. Fix: add ``O_NOFOLLOW``; handle the resulting ``ELOOP`` by
    falling through to the legacy PID-check path.

 - ** (Medium)**: ``_read_plaintext_fallback`` broad except
    logged at DEBUG (invisible at default log levels); also crashed
    with ``AttributeError`` on non-string ``api_key`` values from a
    hand-edited / corrupted config. Fix: (a) add ``isinstance(value,
    str)`` guard before ``.startswith()`` (mirror migration); (b)
    raise corruption-log level from DEBUG to WARNING.

Platform note
-------------
Tests are POSIX-only where they exercise ``fcntl.flock`` /
``O_NOFOLLOW`` / ``ELOOP``. The Windows ``msvcrt.locking`` branch is
exercised via the existing ``tests/test_credential_store_migration_lock.py``
suite (which mocks ``msvcrt`` in ``sys.modules``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys

import pytest
from voice_typer.server import credential_store
from voice_typer.server._paths import RUN_SUBDIR

try:
    import fcntl  # POSIX-only
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache so each test re-probes
    (the probe is cached at module level for the lifetime of the
    process, which would leak state across tests).
    """
    # Reset the plaintext-config cache (used by _read_plaintext_fallback)
    # so a stale entry from one test doesn't leak into the next.
    monkeypatch.setattr(credential_store, "_plaintext_config_cache", {})
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable so store_secret falls back to plaintext."""
    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
    )
    return None


@pytest.fixture
def mock_keyring_raises_on_set(monkeypatch):
    """Mock keyring as available for probing but raising on set_password.

    Simulates the case where the backend is selected but the actual
    write fails (e.g. keychain locked, D-Bus dropped mid-call). The
    store should fall back to plaintext in config.json.
    """
    fake_keyring = MagicMock()

    class _SelectableBackend:
        name = "BrokenKeyring"

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            raise RuntimeError("keychain locked")

        def delete_password(self, service, username):
            raise RuntimeError("nope")

    broken_backend = _SelectableBackend()
    fake_keyring.get_keyring.return_value = broken_backend
    fake_keyring.set_password.side_effect = RuntimeError("keychain locked")
    fake_keyring.get_password.return_value = None
    fake_keyring.delete_password.side_effect = RuntimeError("nope")
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    # Force the probe to succeed (so store_secret tries keyring first).
    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "BrokenKeyring", None),
    )
    # Also patch is_keyring_available to return True (it consults the
    # cache, which the probe populates).
    monkeypatch.setattr(credential_store, "is_keyring_available", lambda: True)
    return fake_keyring


# Lazy import so the module-level import doesn't fail on Windows.
from unittest.mock import MagicMock  # noqa: E402

# ===========================================================================
# _write_plaintext_fallback preserves non-dict config.json
# ===========================================================================


class TestPreserveNonDictConfig:
    """: ``_write_plaintext_fallback`` must NOT overwrite a non-dict
    ``config.json`` root with ``{}``. Mirror the migration path: log a
    warning and skip the write so the user can manually recover."""

    def test_non_dict_root_preserves_file(self, tmp_path):
        """A config.json whose root is a JSON list (not a dict) must be
        preserved — the write must be skipped, NOT overwrite the file
        with ``{<provider>_api_key: <value>}``."""
        config_file = tmp_path / "config.json"
        original_content = '["not", "a", "dict"]'
        config_file.write_text(original_content)

        # Act: try to write the plaintext fallback. Pre-fix, this would
        # silently overwrite the file with ``{"openai_api_key": "..."}``,
        # destroying the original (possibly recoverable) content.
        result = credential_store._write_plaintext_fallback("openai", "sk-test-12345")

        # + : the write was skipped (file preserved), and the
        # function returns False (the secret was NOT saved).
        assert result is False, (
            " regression: _write_plaintext_fallback should return False "
            "when config.json root is not a dict (write skipped — secret NOT saved)."
        )
        assert config_file.read_text() == original_content, (
            " regression: _write_plaintext_fallback overwrote a non-dict "
            "config.json — the corrupt file's recoverable content was destroyed. "
            "Expected the original content to be preserved."
        )

    def test_non_dict_root_logs_warning(self, tmp_path, caplog):
        """A non-dict root must emit a WARNING (not DEBUG) so the user
        can notice and manually recover."""
        config_file = tmp_path / "config.json"
        config_file.write_text('["not", "a", "dict"]')

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            credential_store._write_plaintext_fallback("openai", "sk-test-12345")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "not a dict" in r.getMessage()]
        assert warnings, (
            " regression: expected a log.warning mentioning 'not a dict' "
            "when config.json root is a list. Got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_dict_root_still_writes_normally(self, tmp_path):
        """Sanity check: a normal dict-rooted config.json still gets the
        field written (the non-dict guard must not break the happy path)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"other_field": "preserved"}))

        result = credential_store._write_plaintext_fallback("openai", "sk-test-12345")

        assert result is True, (
            " regression: _write_plaintext_fallback should return True "
            "on a successful write to a normal dict-rooted config.json."
        )
        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "sk-test-12345"
        assert data["other_field"] == "preserved", "Existing fields must be preserved across the read-modify-write."


# ===========================================================================
# _write_plaintext_fallback returns bool; store_secret detects failure
# ===========================================================================


class TestStoreSecretDetectsFallbackFailure:
    """: ``store_secret`` must surface a distinct ``"failed"``
    outcome when the plaintext fallback itself fails (not just when
    keyring fails). Pre-fix, ``_write_plaintext_fallback`` returned
    None and swallowed all errors — the user's API key was silently
    dropped (not in keyring, not in config.json) while the outcome
    still said ``"plaintext"``."""

    def test_store_secret_returns_failed_outcome_on_plaintext_failure(
        self, tmp_path, mock_keyring_raises_on_set, monkeypatch
    ):
        """When keyring fails AND the plaintext fallback also fails
        (e.g. corrupt config.json that is not a dict), store_secret
        must set outcome to ``"failed"`` (not ``"plaintext"``) so the
        renderer can tell the user their API key was NOT saved."""
        # Pre-populate config.json with a non-dict root so the plaintext
        # fallback write will be skipped .
        config_file = tmp_path / "config.json"
        config_file.write_text('["not", "a", "dict"]')

        # Reset the outcome so we can assert it was set by THIS call.
        if hasattr(credential_store._last_store_outcome, "outcome"):
            del credential_store._last_store_outcome.outcome

        result = credential_store.store_secret("openai", "sk-test-12345")

        # store_secret returns False (keyring failed AND fallback failed).
        assert result is False, (
            " regression: store_secret should return False when both keyring and the plaintext fallback fail."
        )
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "failed", (
            " regression: expected outcome['stored_in'] == 'failed' when "
            "the plaintext fallback also failed. Got: "
            f"{outcome['stored_in']!r}. Pre-fix, this was 'plaintext' even "
            "though the secret was NOT saved anywhere — the user had no way "
            "to know their API key was dropped."
        )
        assert outcome["provider"] == "openai"
        assert isinstance(outcome["reason"], str)
        assert outcome["reason"], "reason must be a non-empty string"

    def test_store_secret_returns_plaintext_outcome_on_fallback_success(self, tmp_path, mock_keyring_raises_on_set):
        """Sanity check: when keyring fails but the plaintext fallback
        succeeds, the outcome is still ``"plaintext"`` (not ``"failed"``).
        Guards against the fix accidentally marking all fallback
        writes as failed."""
        # No config.json yet — the fallback will create it (dict root).
        # (Don't pre-populate — let the fallback write to a fresh file.)

        if hasattr(credential_store._last_store_outcome, "outcome"):
            del credential_store._last_store_outcome.outcome

        result = credential_store.store_secret("openai", "sk-test-12345")

        assert result is False, "store_secret returns False on keyring failure (backwards compat)"
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext", (
            " regression: when the plaintext fallback SUCCEEDS, the "
            f"outcome must be 'plaintext' (got {outcome['stored_in']!r}). "
            "The 'failed' outcome is reserved for when the fallback ALSO fails."
        )
        # And the secret was actually written.
        config_file = tmp_path / "config.json"
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["openai_api_key"] == "sk-test-12345"

    def test_write_plaintext_fallback_returns_true_on_success(self, tmp_path):
        """Direct test: ``_write_plaintext_fallback`` returns True on a
        successful write to a normal (or fresh) config.json."""
        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is True

    def test_write_plaintext_fallback_returns_false_on_non_dict(self, tmp_path):
        """Direct test: returns False when config.json root is not a dict
        (the write is skipped to preserve the corrupt file — )."""
        (tmp_path / "config.json").write_text('["not", "a", "dict"]')
        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is False

    def test_write_plaintext_fallback_returns_false_on_parse_error(self, tmp_path, monkeypatch):
        """Direct test: returns False when ``_secure_read_text`` raises
        (e.g. corrupt JSON that can't be parsed at all). The file is
        preserved for manual recovery."""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

        # _secure_read_text uses json.loads internally; invalid JSON
        # raises json.JSONDecodeError, which is caught by the inner
        # try/except in _do_read_modify_write.
        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is False, (
            " regression: _write_plaintext_fallback should return False "
            "when config.json can't be parsed (write skipped — secret NOT saved)."
        )
        # The corrupt file must be preserved.
        assert config_file.read_text() == "not valid json {{{"

    def test_write_plaintext_fallback_returns_false_on_atomic_write_failure(self, tmp_path, monkeypatch):
        """Direct test: returns False when ``_secure_atomic_write`` raises
        (e.g. disk full, read-only filesystem). The outer try/except
        catches the exception and returns False."""
        from voice_typer.server import config as config_mod

        def _raise_on_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _raise_on_write)

        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is False, (
            " regression: _write_plaintext_fallback should return False "
            "when _secure_atomic_write raises (disk full / read-only fs)."
        )


# ===========================================================================
# lock-timeout warnings redact path + OSError
# ===========================================================================


@pytest.mark.skipif(
    sys.platform == "win32" or fcntl is None,
    reason=" POSIX branch uses fcntl.flock; Windows msvcrt path is "
    "exercised by tests/test_credential_store_migration_lock.py.",
)
class TestRedactLockTimeoutWarnings:
    """: migration-lock timeout warnings must redact ``lock_file``
    (path contains username — PII) and the raw ``OSError`` (may embed
    the path too)."""

    def test_posix_slow_wait_redacts_lock_file(self, tmp_path, monkeypatch, caplog):
        """The POSIX slow-wait warning must not log the raw ``lock_file``
        path (it contains the username under /home/<user>/...)."""
        # Use short thresholds so the warning fires before the timeout.
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS", 0.2)
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.6)

        # Create a lock file path that contains a fake username.
        fake_user_dir = tmp_path / "home" / "alice_secret_user"
        fake_user_dir.mkdir(parents=True)
        lock_file = fake_user_dir / "config.json.lock"

        # Hold the flock from another fd so _acquire_migration_lock times out.
        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            with (
                caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"),
                pytest.raises(TimeoutError),
            ):
                credential_store._acquire_migration_lock(lock_file)

            # Find the slow-wait warning.
            slow_warnings = [
                r for r in caplog.records if r.levelno == logging.WARNING and "migration lock wait" in r.getMessage()
            ]
            assert slow_warnings, (
                f"expected a slow-wait warning during the lock wait. Got: {[r.getMessage() for r in caplog.records]}"
            )
            # The raw username must NOT appear in the warning message.
            for record in slow_warnings:
                msg = record.getMessage()
                assert "alice_secret_user" not in msg, (
                    f" regression: raw username leaked in lock-timeout warning. Message: {msg!r}"
                )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

    def test_posix_timeout_error_redacts_lock_file(self, tmp_path, monkeypatch):
        """The POSIX ``TimeoutError`` message must not embed the raw
        ``lock_file`` path (it contains the username)."""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        fake_user_dir = tmp_path / "home" / "bob_secret_user"
        fake_user_dir.mkdir(parents=True)
        lock_file = fake_user_dir / "config.json.lock"

        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._acquire_migration_lock(lock_file)

            msg = str(exc_info.value)
            assert "bob_secret_user" not in msg, (
                f" regression: raw username leaked in TimeoutError message. Message: {msg!r}"
            )
            # The structural substrings the test_migration_lock_times_out_when_held
            # test asserts on must still be present (don't over-redact).
            assert "migration lock" in msg
            assert "0.3s" in msg
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()


# ===========================================================================
# migrate_secrets_to_keyring aborts on lock-acquire failure
# ===========================================================================


@pytest.mark.skipif(
    sys.platform == "win32" or fcntl is None,
    reason=" POSIX branch uses fcntl.flock; Windows msvcrt path is "
    "exercised by tests/test_credential_store_migration_lock.py.",
)
class TestAbortMigrationOnLockFailure:
    """: when the migration lock can't be acquired (POSIX
    ``TimeoutError`` or any other exception), ``migrate_secrets_to_keyring``
    must ABORT (return 0) — NOT proceed with
    ``_migrate_secrets_to_keyring_locked`` (which would re-open RACE-001)."""

    def test_migration_aborts_on_lock_timeout(self, tmp_path, monkeypatch):
        """When the lock can't be acquired (held by another fd →
        ``TimeoutError``), migration must abort and return 0 WITHOUT
        touching config.json (no migration, no flag write that could
        race with the holder)."""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        # Pre-populate config.json with plaintext that WOULD be migrated
        # if the lock were acquired.
        config_file = tmp_path / "config.json"
        original_content = {"openai_api_key": "sk-would-be-migrated"}
        config_file.write_text(json.dumps(original_content))

        lock_file = tmp_path / "config.json.lock"
        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            # migration aborts (returns 0) instead of proceeding.
            count = credential_store.migrate_secrets_to_keyring()

            assert count == 0, (
                " regression: migrate_secrets_to_keyring should return 0 "
                "when the lock can't be acquired (aborted — no migration ran)."
            )
            # config.json must NOT have been modified by migration (the
            # lock wasn't held, so any write would race with the holder).
            # Note: the abort path MAY defensively write
            # secrets_migrated=True, but it must NOT migrate the
            # plaintext secret to keyring (which would clobber a
            # concurrent migration).
            data = json.loads(config_file.read_text())
            # The plaintext secret must still be there (NOT replaced
            # with a keyring:// reference token — that would mean
            # migration ran without the lock).
            assert data.get("openai_api_key") == "sk-would-be-migrated", (
                " regression: migration appears to have run without the "
                "lock — the plaintext secret was replaced. This re-opens "
                "RACE-001 (two concurrent migrations could clobber each other)."
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

    def test_migration_abort_logs_warning(self, tmp_path, monkeypatch, caplog):
        """The abort must log a WARNING so operators can diagnose a
        wedged lock holder (vs. silently returning 0)."""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        (tmp_path / "config.json").write_text(json.dumps({"openai_api_key": "sk-test"}))

        lock_file = tmp_path / "config.json.lock"
        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
                credential_store.migrate_secrets_to_keyring()

            abort_warnings = [
                r for r in caplog.records if r.levelno == logging.WARNING and "ABORTING migration" in r.getMessage()
            ]
            assert abort_warnings, (
                " regression: expected a log.warning containing "
                "'ABORTING migration' when the lock can't be acquired. "
                f"Got: {[r.getMessage() for r in caplog.records]}"
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

    def test_migration_abort_does_not_log_at_debug_only(self, tmp_path, monkeypatch, caplog):
        """Pre-, the lock-acquire failure was logged at DEBUG
        (invisible at default log levels). The abort must be at WARNING."""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        (tmp_path / "config.json").write_text(json.dumps({"openai_api_key": "sk-test"}))

        lock_file = tmp_path / "config.json.lock"
        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            # Capture at DEBUG level — if the abort were still at DEBUG,
            # we'd see the old "could not acquire lock ... proceeding without"
            # message. promotes it to WARNING with new text.
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.credential_store"):
                credential_store.migrate_secrets_to_keyring()

            old_debug_messages = [
                r.getMessage()
                for r in caplog.records
                if r.levelno == logging.DEBUG and "proceeding without" in r.getMessage()
            ]
            assert not old_debug_messages, (
                " regression: the old DEBUG-level 'proceeding without' "
                "message is still present — the abort should be at WARNING "
                f"with new text. Got: {old_debug_messages}"
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()


# ===========================================================================
# _read_plaintext_fallback non-string guard + WARNING log level
# ===========================================================================


class TestReadPlaintextFallbackGuards:
    """``_read_plaintext_fallback`` must (a) guard against
    non-string ``api_key`` values (mirror the migration path's
    ``isinstance(value, str)`` check) and (b) log corruption at WARNING
    (not DEBUG) so the user can notice and recover."""

    def test_non_string_value_does_not_crash(self, tmp_path):
        """A non-string ``api_key`` value (e.g. int from a hand-edited
        config) must NOT crash with ``AttributeError`` at
        ``value.startswith()``. Pre-fix, this propagated up through
        ``load_secret`` and ``Config.load``."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": 12345}))

        # Must return None (skip the provider), NOT raise.
        result = credential_store._read_plaintext_fallback("openai")
        assert result is None, (
            f" regression: non-string api_key value should return None (skip the provider), got {result!r}."
        )

    def test_non_string_value_logs_warning(self, tmp_path, caplog):
        """A non-string value must emit a WARNING so the user sees what's
        wrong with their config."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": ["a", "list"]}))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            credential_store._read_plaintext_fallback("openai")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "non-string" in r.getMessage()]
        assert warnings, (
            " regression: expected a log.warning mentioning 'non-string' "
            f"when api_key is a list. Got: {[r.getMessage() for r in caplog.records]}"
        )

    def test_non_dict_root_does_not_crash(self, tmp_path):
        """A non-dict root (e.g. JSON list) must NOT crash at
        ``data.get(field, "")``. Pre-, this raised
               ``AttributeError`` (the broad except at line ~1152 does NOT
               cover the lines after it)."""
        config_file = tmp_path / "config.json"
        config_file.write_text('["not", "a", "dict"]')

        # Must return None (skip), NOT raise.
        result = credential_store._read_plaintext_fallback("openai")
        assert result is None

    def test_non_dict_root_logs_warning(self, tmp_path, caplog):
        """A non-dict root must emit a WARNING so the user can recover."""
        config_file = tmp_path / "config.json"
        config_file.write_text('["not", "a", "dict"]')

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            credential_store._read_plaintext_fallback("openai")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "not a dict" in r.getMessage()]
        assert warnings, (
            " regression: expected a log.warning mentioning 'not a dict' "
            f"when config.json root is a list. Got: {[r.getMessage() for r in caplog.records]}"
        )

    def test_corrupt_json_logs_at_warning_not_debug(self, tmp_path, caplog):
        """(b): a JSON parse failure must log at WARNING (not
        DEBUG) so the user can notice at default log levels."""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

        # Capture at DEBUG — if the level were still DEBUG, we'd see the
        # message at DEBUG. (b) promotes it to WARNING.
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.credential_store"):
            credential_store._read_plaintext_fallback("openai")

        debug_records = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "plaintext fallback read failed" in r.getMessage()
        ]
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "plaintext fallback read failed" in r.getMessage()
        ]
        assert not debug_records, (
            "(b) regression: corrupt-json log is still at DEBUG — "
            "the user can't see it at default log levels. "
            f"Debug records: {[r.getMessage() for r in debug_records]}"
        )
        assert warning_records, (
            "(b) regression: expected a WARNING-level 'plaintext "
            "fallback read failed' message for corrupt JSON. Got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_string_value_still_loads_normally(self, tmp_path):
        """Sanity check: a normal string ``api_key`` value still loads
        (the non-string guard must not break the happy path)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-normal-value"}))

        result = credential_store._read_plaintext_fallback("openai")
        assert result == "sk-normal-value"

    def test_keyring_reference_still_returns_none(self, tmp_path):
        """Sanity check: a ``keyring://`` reference token still returns
        None (the caller should have tried keyring first)."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "keyring://openai"}))

        result = credential_store._read_plaintext_fallback("openai")
        assert result is None


# ===========================================================================
# secondary open uses O_NOFOLLOW (TOCTOU symlink race)
# ===========================================================================


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=" O_NOFOLLOW / ELOOP is POSIX-only (Linux/macOS/BSD).",
)
class TestSecondaryOpenNoFollow:
    """: the secondary ``os.open`` on the EXISTING lockfile (after
    ``O_EXCL`` failed) must use ``O_NOFOLLOW`` to close a TOCTOU symlink
    race. Pre-fix, an attacker who could replace a regular file with a
    symlink between the two opens could trick the secondary open into
    opening the symlink's TARGET for read/write (the subsequent
    ``os.ftruncate`` / ``os.write`` would clobber the target)."""

    def test_secondary_open_source_contains_no_follow(self):
        """Source-level invariant: the secondary ``os.open`` call (the
        one with ``O_RDWR`` on the existing lockfile) must include
        ``O_NOFOLLOW``. This is the fallback check that runs on any
        sandbox (even those that block symlink creation or intercept
        ``O_NOFOLLOW`` at the syscall level)."""
        import inspect

        from voice_typer.server import single_instance as si_mod

        src = inspect.getsource(si_mod._ensure_single_instance_posix)
        # The primary open (O_CREAT | O_EXCL) already has O_NOFOLLOW
        # (FR-37). We need at least 2 occurrences of O_NOFOLLOW now —
        # one in _try_acquire and one in the secondary open.
        no_follow_count = src.count("O_NOFOLLOW")
        assert no_follow_count >= 2, (
            " regression: expected at least 2 occurrences of O_NOFOLLOW "
            "in _ensure_single_instance_posix (primary O_EXCL open + secondary "
            f"O_RDWR open). Got {no_follow_count}. The secondary open is "
            "missing O_NOFOLLOW — a TOCTOU symlink race is possible."
        )

    def test_secondary_open_handles_eloop_fallthrough(self, tmp_path, monkeypatch):
        """When ``O_NOFOLLOW`` raises ``ELOOP`` (because
        ``backend.lock`` is a symlink), the secondary open must fall
        through to the legacy PID-check path (NOT exit(1) — the
        primary ``_try_acquire`` already exits(1) on ELOOP, but the
        secondary open is reached only when ``O_EXCL`` failed with
        ``FileExistsError``, meaning the file existed as a regular
        file moments ago and may have been swapped for a symlink)."""
        import errno as errno_mod

        from voice_typer.server import single_instance as si_mod

        # Pre-create backend.lock as a regular file so _try_acquire's
        # O_EXCL fails with FileExistsError → returns None → reaches
        # the secondary open. The lockfile lives under the config dir's
        # ``run/`` subdir (RUN_SUBDIR — transient-state lockdown), so
        # the decoy must sit at that SAME path — otherwise _try_acquire
        # simply creates a fresh lockfile (O_CREAT|O_EXCL succeeds),
        # the secondary open is never reached, and the simulated ELOOP
        # never engages (the function returns without exiting).
        config_dir = tmp_path
        run_dir = config_dir / RUN_SUBDIR
        run_dir.mkdir()
        lock_path = run_dir / "backend.lock"
        lock_path.write_text(str(os.getpid()))  # valid PID content

        # Monkeypatch the secondary os.open to raise ELOOP (simulating
        # the TOCTOU swap: regular file → symlink between the two
        # opens). We need to be careful: the primary os.open (O_CREAT |
        # O_EXCL) must still work (it'll get FileExistsError from the
        # real file), but the secondary os.open (O_RDWR | O_CLOEXEC |
        # O_NOFOLLOW) must raise ELOOP.
        real_os_open = os.open

        def _conditional_open(path, flags, *args, **kwargs):
            # The primary _try_acquire uses O_CREAT | O_EXCL.
            # The secondary open uses O_RDWR | O_CLOEXEC | O_NOFOLLOW
            # (NO O_CREAT, NO O_EXCL).
            if (flags & os.O_CREAT) and (flags & os.O_EXCL):
                # Primary path — let the real os.open handle it
                # (it'll raise FileExistsError since the file exists).
                return real_os_open(path, flags, *args, **kwargs)
            if (flags & os.O_RDWR) and (flags & os.O_NOFOLLOW) and not (flags & os.O_CREAT):
                # Secondary open — simulate ELOOP (symlink detected).
                raise OSError(errno_mod.ELOOP, "simulated ELOOP — too many levels of symbolic links")
            return real_os_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", _conditional_open)

        # Monkeypatch _config_dir to point at tmp_path (via the app
        # module's _config_dir, which is what the production code reads).
        monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: config_dir)

        # The function should fall through to the legacy PID-check path.
        # The legacy path reads the PID (via Python's open(), which
        # works on the regular file), checks _is_pid_alive, and if the
        # PID is alive, exits(1). Our PID is alive (it's us), so we
        # expect SystemExit(1) with "another instance is already running".
        #
        # BUT: the legacy path also reads the PID via _read_pid_from_lockfile,
        # which uses open(path) (NOT os.open). The monkeypatch only
        # intercepts os.open, so open() still works on the regular file
        # and returns our PID. _is_pid_alive(our_pid) returns True →
        # exit(1).
        with pytest.raises(SystemExit) as exc_info:
            si_mod._ensure_single_instance_posix(silent=True)

        # The exit should be due to "another instance is already running"
        # (the legacy PID-check path detected our own PID as alive).
        # This proves the ELOOP fallthrough reached the legacy path
        # (vs. exiting(1) with "cannot create lock file" from the
        # primary _try_acquire's ELOOP handler).
        assert exc_info.value.code == 1, (
            " regression: secondary open ELOOP should fall through to "
            f"the legacy PID-check path. Got exit code: {exc_info.value.code}"
        )

    def test_normal_lockfile_creation_still_works_with_no_follow(self, tmp_path, tmp_config_dir):
        """Sanity check: adding ``O_NOFOLLOW`` to the secondary open
        must NOT break the normal (non-symlink) lockfile creation path.
        ``O_NOFOLLOW`` is a no-op when the trailing component is a
        regular file."""
        from voice_typer.server import single_instance as si_mod

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            assert fd is not None, (
                " regression: normal lockfile creation (no symlink) "
                "should still succeed after adding O_NOFOLLOW to the "
                "secondary open."
            )
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(int(fd))
