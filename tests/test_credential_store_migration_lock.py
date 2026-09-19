"""Regression tests for the Windows branch of ``_acquire_migration_lock``."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import types

import pytest
from voice_typer.server import credential_store


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield


def _install_fake_msvcrt(monkeypatch, *, locking_impl):
    """Install a fake ``msvcrt`` module in ``sys.modules``."""
    fake = types.ModuleType("msvcrt")
    fake.locking = locking_impl  # type: ignore[attr-defined]
    fake.LK_LOCK = 1  # type: ignore[attr-defined]
    fake.LK_NBLCK = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    return fake


class TestWindowsMigrationLockTimeout:
    """Windows branch must log visibly when ``msvcrt.locking`` times out."""

    def test_timeout_logs_warning_and_returns_fd(self, tmp_path, monkeypatch, caplog):
        """function must log a visible WARNING and STILL return the fd"""
        # Force the Windows branch.
        monkeypatch.setattr(credential_store, "_is_windows", lambda: True)

        def _locking_raises(fd, mode, nbytes):
            raise OSError("lock timed out")

        _install_fake_msvcrt(monkeypatch, locking_impl=_locking_raises)

        lock_file = tmp_path / "config.json.lock"
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            returned = credential_store._acquire_migration_lock(lock_file)

        # Fail-open: fd is returned (NOT None, NOT raised).
        assert returned is not None, (
            "Regression: _acquire_migration_lock returned None on "
            "Windows lock timeout, the fail-open contract is to "
            "return the fd so the caller's finally: lock_fd.close() "
            "works; the caller (migrate_secrets_to_keyring) only "
            "checks for exceptions, not for lock validity."
        )
        # Visible: a warning was logged mentioning the timeout.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, (
            "Regression: _acquire_migration_lock swallowed the Windows "
            "msvcrt.locking timeout silently, no WARNING was logged. "
            "Two app instances could both pass and clobber each other's "
            "config.json write with no trace in logs."
        )
        joined = " ".join(r.getMessage() for r in warnings)
        assert "Windows migration lock acquire timed out" in joined, (
            "Regression: warning was logged but did not mention the "
            "timeout, expected 'Windows migration lock acquire timed "
            f"out' in message. Got: {joined!r}"
        )
        with contextlib.suppress(OSError):
            returned.close()

    def test_timeout_warning_message_mentions_race(self, tmp_path, monkeypatch, caplog):
        """The warning text must mention 'race possible' so operators"""
        monkeypatch.setattr(credential_store, "_is_windows", lambda: True)

        def _locking_raises(fd, mode, nbytes):
            raise OSError("lock timed out")

        _install_fake_msvcrt(monkeypatch, locking_impl=_locking_raises)

        lock_file = tmp_path / "config.json.lock"
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            returned = credential_store._acquire_migration_lock(lock_file)

        try:
            joined = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
            assert "race possible" in joined, (
                "Warning text must mention 'race possible' so operators "
                "grep-ing logs for race conditions can find it. Got: "
                f"{joined!r}"
            )
        finally:
            with contextlib.suppress(OSError):
                returned.close()

    def test_success_does_not_log_warning(self, tmp_path, monkeypatch, caplog):
        """the fd is returned. This guards against the fix accidentally"""
        monkeypatch.setattr(credential_store, "_is_windows", lambda: True)

        calls: list[tuple] = []

        def _locking_ok(fd, mode, nbytes):
            calls.append((fd, mode, nbytes))

        _install_fake_msvcrt(monkeypatch, locking_impl=_locking_ok)

        lock_file = tmp_path / "config.json.lock"
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            returned = credential_store._acquire_migration_lock(lock_file)

        try:
            assert returned is not None
            assert calls, "msvcrt.locking was not called"
            # No warning should be logged on the success path.
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert not warnings, (
                f"Unexpected warnings on successful lock acquire: {[r.getMessage() for r in warnings]!r}"
            )
        finally:
            with contextlib.suppress(OSError):
                returned.close()

    def test_timeout_does_not_raise_to_caller(self, tmp_path, monkeypatch):
        """The Windows timeout must NOT propagate as an exception —"""
        monkeypatch.setattr(credential_store, "_is_windows", lambda: True)

        def _locking_raises(fd, mode, nbytes):
            raise OSError("lock timed out")

        _install_fake_msvcrt(monkeypatch, locking_impl=_locking_raises)

        lock_file = tmp_path / "config.json.lock"
        # Must not raise.
        returned = credential_store._acquire_migration_lock(lock_file)
        try:
            assert returned is not None
        finally:
            with contextlib.suppress(OSError):
                returned.close()

    def test_timeout_warning_visible_through_migrate(self, tmp_path, monkeypatch, caplog):
        """End-to-end: ``migrate_secrets_to_keyring`` must still"""
        monkeypatch.setattr(credential_store, "_is_windows", lambda: True)

        def _locking_raises(fd, mode, nbytes):
            raise OSError("lock timed out")

        _install_fake_msvcrt(monkeypatch, locking_impl=_locking_raises)

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"secrets_migrated": True}))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            count = credential_store.migrate_secrets_to_keyring()

        # Migration completes (fail-open), no exception, count is 0
        assert count == 0, (
            f"migrate_secrets_to_keyring returned {count}, expected 0 "
            "since secrets_migrated was already True. The function must "
            "still complete when the Windows lock times out."
        )
        # Warning was logged.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Windows migration lock acquire timed out" in r.getMessage() for r in warnings), (
            f"Expected a warning about the Windows lock timeout, got: {[r.getMessage() for r in warnings]!r}"
        )


class TestPosixBranchUnchanged:
    """Sanity-check that the POSIX branch (``fcntl.flock``) is"""

    def test_posix_success_no_warning(self, tmp_path, monkeypatch, caplog):
        """On POSIX (the test host), ``fcntl.flock`` succeeds without"""
        # _is_windows() returns False on Linux by default, no patch.
        lock_file = tmp_path / "config.json.lock"

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            returned = credential_store._acquire_migration_lock(lock_file)

        try:
            assert returned is not None
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert not warnings, (
                "POSIX branch should not emit warnings on successful "
                f"lock acquire: {[r.getMessage() for r in warnings]!r}"
            )
        finally:
            with contextlib.suppress(OSError):
                returned.close()


class TestLockAbortDefersRetry:
    """BP-131: a lock-acquire failure must DEFER migration, not mark it done."""

    def test_lock_abort_records_deferral_not_success(self, tmp_config_dir, monkeypatch):
        """Abort path: no success flag, deferral diagnostic present."""
        import json

        from voice_typer.server.credential_store import _migration as migration_mod

        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-test-plaintext"}), encoding="utf-8")

        def _boom(lock_file):
            raise TimeoutError("simulated lock contention")

        monkeypatch.setattr(migration_mod, "_acquire_migration_lock", _boom)

        assert migration_mod.migrate_secrets_to_keyring() == 0

        on_disk = json.loads(config_file.read_text(encoding="utf-8"))
        assert on_disk.get("secrets_migrated", False) is False, (
            "lock-abort must NOT set secrets_migrated (that would skip the promised next-launch retry forever)"
        )
        assert on_disk.get("secrets_migrated_keyring_was_unavailable") is True, (
            "lock-abort must record the deferral diagnostic so the retry is observable"
        )
        # The plaintext secret is untouched (nothing was migrated).
        assert on_disk.get("openai_api_key") == "sk-test-plaintext"
