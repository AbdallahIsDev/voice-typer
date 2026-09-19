"""regression tests for credential_store + single_instance fixes."""

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


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    # Reset the plaintext-config cache (used by _read_plaintext_fallback)
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
    """Mock keyring as available for probing but raising on set_password."""
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
    monkeypatch.setattr(credential_store, "is_keyring_available", lambda: True)
    return fake_keyring


# Lazy import so the module-level import doesn't fail on Windows.
from unittest.mock import MagicMock  # noqa: E402


class TestPreserveNonDictConfig:
    """: ``_write_plaintext_fallback`` must NOT overwrite a non-dict"""

    def test_non_dict_root_preserves_file(self, tmp_path):
        """A config.json whose root is a JSON list (not a dict) must be"""
        config_file = tmp_path / "config.json"
        original_content = '["not", "a", "dict"]'
        config_file.write_text(original_content)

        # Act: try to write the plaintext fallback. Pre-fix, this would
        result = credential_store._write_plaintext_fallback("openai", "sk-test-12345")

        assert result is False, (
            " regression: _write_plaintext_fallback should return False "
            "when config.json root is not a dict (write skipped, secret NOT saved)."
        )
        assert config_file.read_text() == original_content, (
            " regression: _write_plaintext_fallback overwrote a non-dict "
            "config.json, the corrupt file's recoverable content was destroyed. "
            "Expected the original content to be preserved."
        )

    def test_non_dict_root_logs_warning(self, tmp_path, caplog):
        """A non-dict root must emit a WARNING (not DEBUG) so the user"""
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
        """Sanity check: a normal dict-rooted config.json still gets the"""
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


class TestStoreSecretDetectsFallbackFailure:
    """: ``store_secret`` must surface a distinct ``\"failed\"``"""

    def test_store_secret_returns_failed_outcome_on_plaintext_failure(
        self, tmp_path, mock_keyring_raises_on_set, monkeypatch
    ):
        """When keyring fails AND the plaintext fallback also fails"""
        # Pre-populate config.json with a non-dict root so the plaintext
        config_file = tmp_path / "config.json"
        config_file.write_text('["not", "a", "dict"]')

        # Reset the outcome so we can assert it was set by THIS call.
        if hasattr(credential_store._last_store_outcome, "outcome"):
            del credential_store._last_store_outcome.outcome

        result = credential_store.store_secret("openai", "sk-test-12345")

        assert result is False, (
            " regression: store_secret should return False when both keyring and the plaintext fallback fail."
        )
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "failed", (
            " regression: expected outcome['stored_in'] == 'failed' when "
            "the plaintext fallback also failed. Got: "
            f"{outcome['stored_in']!r}. Pre-fix, this was 'plaintext' even "
            "though the secret was NOT saved anywhere, the user had no way "
            "to know their API key was dropped."
        )
        assert outcome["provider"] == "openai"
        assert isinstance(outcome["reason"], str)
        assert outcome["reason"], "reason must be a non-empty string"

    def test_store_secret_returns_plaintext_outcome_on_fallback_success(self, tmp_path, mock_keyring_raises_on_set):
        """Sanity check: when keyring fails but the plaintext fallback"""
        # No config.json yet, the fallback will create it (dict root).

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
        """Direct test: ``_write_plaintext_fallback`` returns True on a"""
        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is True

    def test_write_plaintext_fallback_returns_false_on_non_dict(self, tmp_path):
        """Direct test: returns False when config.json root is not a dict"""
        (tmp_path / "config.json").write_text('["not", "a", "dict"]')
        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is False

    def test_write_plaintext_fallback_returns_false_on_parse_error(self, tmp_path, monkeypatch):
        """Direct test: returns False when ``_secure_read_text`` raises"""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is False, (
            " regression: _write_plaintext_fallback should return False "
            "when config.json can't be parsed (write skipped, secret NOT saved)."
        )
        # The corrupt file must be preserved.
        assert config_file.read_text() == "not valid json {{{"

    def test_write_plaintext_fallback_returns_false_on_atomic_write_failure(self, tmp_path, monkeypatch):
        """Direct test: returns False when ``_secure_atomic_write`` raises"""
        from voice_typer.server import config as config_mod

        def _raise_on_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _raise_on_write)

        result = credential_store._write_plaintext_fallback("openai", "sk-test")
        assert result is False, (
            " regression: _write_plaintext_fallback should return False "
            "when _secure_atomic_write raises (disk full / read-only fs)."
        )


@pytest.mark.skipif(
    sys.platform == "win32" or fcntl is None,
    reason=" POSIX branch uses fcntl.flock; Windows msvcrt path is "
    "exercised by tests/test_credential_store_migration_lock.py.",
)
class TestRedactLockTimeoutWarnings:
    """: migration-lock timeout warnings must redact ``lock_file``"""

    def test_posix_slow_wait_redacts_lock_file(self, tmp_path, monkeypatch, caplog):
        """The POSIX slow-wait warning must not log the raw ``lock_file``"""
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
        """The POSIX ``TimeoutError`` message must not embed the raw"""
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
            assert "migration lock" in msg
            assert "0.3s" in msg
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()


@pytest.mark.skipif(
    sys.platform == "win32" or fcntl is None,
    reason=" POSIX branch uses fcntl.flock; Windows msvcrt path is "
    "exercised by tests/test_credential_store_migration_lock.py.",
)
class TestAbortMigrationOnLockFailure:
    """
    : when the migration lock can't be acquired (POSIX
    ``_migrate_secrets_to_keyring_locked`` (which would re-open RACE-001).
    """

    def test_migration_aborts_on_lock_timeout(self, tmp_path, monkeypatch):
        """When the lock can't be acquired (held by another fd →"""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        # Pre-populate config.json with plaintext that WOULD be migrated
        config_file = tmp_path / "config.json"
        original_content = {"openai_api_key": "sk-would-be-migrated"}
        config_file.write_text(json.dumps(original_content))

        lock_file = tmp_path / "config.json.lock"
        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            count = credential_store.migrate_secrets_to_keyring()

            assert count == 0, (
                " regression: migrate_secrets_to_keyring should return 0 "
                "when the lock can't be acquired (aborted, no migration ran)."
            )
            # config.json must NOT have been modified by migration (the
            # secrets_migrated=True, but it must NOT migrate the
            data = json.loads(config_file.read_text())
            # The plaintext secret must still be there (NOT replaced
            assert data.get("openai_api_key") == "sk-would-be-migrated", (
                " regression: migration appears to have run without the "
                "lock, the plaintext secret was replaced. This re-opens "
                "RACE-001 (two concurrent migrations could clobber each other)."
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

    def test_migration_abort_logs_warning(self, tmp_path, monkeypatch, caplog):
        """The abort must log a WARNING so operators can diagnose a"""
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
        """Pre-, the lock-acquire failure was logged at DEBUG"""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        (tmp_path / "config.json").write_text(json.dumps({"openai_api_key": "sk-test"}))

        lock_file = tmp_path / "config.json.lock"
        holder = open(lock_file, "w+b")  # noqa: SIM115
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            # Capture at DEBUG level, if the abort were still at DEBUG,
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.credential_store"):
                credential_store.migrate_secrets_to_keyring()

            old_debug_messages = [
                r.getMessage()
                for r in caplog.records
                if r.levelno == logging.DEBUG and "proceeding without" in r.getMessage()
            ]
            assert not old_debug_messages, (
                " regression: the old DEBUG-level 'proceeding without' "
                "message is still present, the abort should be at WARNING "
                f"with new text. Got: {old_debug_messages}"
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()


class TestReadPlaintextFallbackGuards:
    """``_read_plaintext_fallback`` must (a) guard against"""

    def test_non_string_value_does_not_crash(self, tmp_path):
        """A non-string ``api_key`` value (e.g. int from a hand-edited"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": 12345}))

        # Must return None (skip the provider), NOT raise.
        result = credential_store._read_plaintext_fallback("openai")
        assert result is None, (
            f" regression: non-string api_key value should return None (skip the provider), got {result!r}."
        )

    def test_non_string_value_logs_warning(self, tmp_path, caplog):
        """A non-string value must emit a WARNING so the user sees what's"""
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
        """A non-dict root (e.g. JSON list) must NOT crash at"""
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
        """(b): a JSON parse failure must log at WARNING (not"""
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{{")

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
            "(b) regression: corrupt-json log is still at DEBUG, "
            "the user can't see it at default log levels. "
            f"Debug records: {[r.getMessage() for r in debug_records]}"
        )
        assert warning_records, (
            "(b) regression: expected a WARNING-level 'plaintext "
            "fallback read failed' message for corrupt JSON. Got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_string_value_still_loads_normally(self, tmp_path):
        """Sanity check: a normal string ``api_key`` value still loads"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-normal-value"}))

        result = credential_store._read_plaintext_fallback("openai")
        assert result == "sk-normal-value"

    def test_keyring_reference_still_returns_none(self, tmp_path):
        """Sanity check: a ``keyring://`` reference token still returns"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "keyring://openai"}))

        result = credential_store._read_plaintext_fallback("openai")
        assert result is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=" O_NOFOLLOW / ELOOP is POSIX-only (Linux/macOS/BSD).",
)
class TestSecondaryOpenNoFollow:
    """: the secondary ``os.open`` on the EXISTING lockfile (after"""

    def test_secondary_open_source_contains_no_follow(self):
        """one with ``O_RDWR`` on the existing lockfile) must include"""
        import inspect

        from voice_typer.server import single_instance as si_mod

        src = inspect.getsource(si_mod._ensure_single_instance_posix)
        # The primary open (O_CREAT | O_EXCL) already has O_NOFOLLOW
        no_follow_count = src.count("O_NOFOLLOW")
        assert no_follow_count >= 2, (
            " regression: expected at least 2 occurrences of O_NOFOLLOW "
            "in _ensure_single_instance_posix (primary O_EXCL open + secondary "
            f"O_RDWR open). Got {no_follow_count}. The secondary open is "
            "missing O_NOFOLLOW, a TOCTOU symlink race is possible."
        )

    def test_secondary_open_handles_eloop_fallthrough(self, tmp_path, monkeypatch):
        """When ``O_NOFOLLOW`` raises ``ELOOP`` (because"""
        import errno as errno_mod

        from voice_typer.server import single_instance as si_mod

        # Pre-create backend.lock as a regular file so _try_acquire's
        config_dir = tmp_path
        run_dir = config_dir / RUN_SUBDIR
        run_dir.mkdir()
        lock_path = run_dir / "backend.lock"
        lock_path.write_text(str(os.getpid()))  # valid PID content

        # Monkeypatch the secondary os.open to raise ELOOP (simulating
        real_os_open = os.open

        def _conditional_open(path, flags, *args, **kwargs):
            # The primary _try_acquire uses O_CREAT | O_EXCL.
            if (flags & os.O_CREAT) and (flags & os.O_EXCL):
                # Primary path, let the real os.open handle it
                return real_os_open(path, flags, *args, **kwargs)
            if (flags & os.O_RDWR) and (flags & os.O_NOFOLLOW) and not (flags & os.O_CREAT):
                # Secondary open, simulate ELOOP (symlink detected).
                raise OSError(errno_mod.ELOOP, "simulated ELOOP, too many levels of symbolic links")
            return real_os_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", _conditional_open)

        # Monkeypatch _config_dir to point at tmp_path (via the owning
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: config_dir)

        # The function should fall through to the legacy PID-check path.
        with pytest.raises(SystemExit) as exc_info:
            si_mod._ensure_single_instance_posix(silent=True)

        # The exit should be due to "another instance is already running"
        assert exc_info.value.code == 1, (
            " regression: secondary open ELOOP should fall through to "
            f"the legacy PID-check path. Got exit code: {exc_info.value.code}"
        )

    def test_normal_lockfile_creation_still_works_with_no_follow(self, tmp_path, tmp_config_dir):
        """Sanity check: adding ``O_NOFOLLOW`` to the secondary open"""
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
