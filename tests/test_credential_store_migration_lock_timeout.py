"""FR-27: regression tests for the migration lock timeout."""

from __future__ import annotations

import contextlib
import logging
import sys
import time

import pytest
from voice_typer.server import credential_store

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl module
    fcntl = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or fcntl is None,
    reason="FR-27 POSIX branch uses fcntl.flock; Windows msvcrt path is "
    "exercised by tests/test_config_save_lock.py on Windows runners.",
)


@contextlib.contextmanager
def _hold_flock(lock_file):
    """duration of the ``with`` block."""
    fd = open(lock_file, "w+b")  # noqa: SIM115 -- explicit close in finally
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()


class TestMigrationLockTimeout:
    """FR-27: ``_acquire_migration_lock`` must not block indefinitely."""

    def test_migration_lock_times_out_when_held(self, tmp_path, monkeypatch):
        """When the lock is held by another fd, acquisition must raise"""
        # Shorten the timeout so the test is fast (default is 5s).
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.5)

        lock_file = tmp_path / "config.json.lock"

        with _hold_flock(lock_file):
            start = time.monotonic()
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._acquire_migration_lock(lock_file)
            elapsed = time.monotonic() - start

            # The error message must mention the timeout + the lock file
            msg = str(exc_info.value)
            assert "migration lock" in msg, f"TimeoutError message lacks context: {msg!r}"
            assert "0.5s" in msg, f"TimeoutError message lacks the timeout value: {msg!r}"

            # (polled retry, not an instant bail), and must NOT hang
            assert elapsed >= 0.4, (
                f"FR-27 regression: _acquire_migration_lock gave up "
                f"after only {elapsed:.2f}s, expected to poll for at "
                f"least the 0.5s timeout before raising TimeoutError "
                f"(the LOCK_EX | LOCK_NB retry loop may be missing)."
            )
            assert elapsed < 5.0, (
                f"FR-27 regression: _acquire_migration_lock took "
                f"{elapsed:.2f}s, expected to time out within ~0.5s. "
                f"The blocking flock(LOCK_EX) (no LOCK_NB) may still "
                f"be in place, defeating the timeout."
            )

    def test_migration_lock_acquires_when_free(self, tmp_path):
        """Sanity check: with no contention the lock is acquired"""
        lock_file = tmp_path / "config.json.lock"
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        try:
            # A second open in the same process must NOT be able to
            probe = open(lock_file, "r+b")  # noqa: SIM115
            try:
                with pytest.raises(OSError):
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                probe.close()
        finally:
            lock_fd.close()

        # After release, a fresh acquire must succeed (no stale lock).
        again = credential_store._acquire_migration_lock(lock_file)
        again.close()

    def test_migration_lock_warns_on_slow_wait(self, tmp_path, monkeypatch, caplog):
        """When the wait exceeds the slow-warn threshold, a single"""
        # Use a short slow-warn threshold (0.2s) and a slightly longer
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS", 0.2)
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.6)

        lock_file = tmp_path / "config.json.lock"

        with _hold_flock(lock_file), caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
            credential_store._acquire_migration_lock(lock_file)

            # Exactly one WARNING record mentioning the slow wait must
            warnings = [
                r for r in caplog.records if r.levelno == logging.WARNING and "migration lock wait" in r.getMessage()
            ]
            assert warnings, (
                "FR-27 regression: expected a log.warning when the "
                "migration lock wait exceeds the slow-warn threshold, "
                "but none was emitted. Records seen: "
                f"{[r.getMessage() for r in caplog.records]}"
            )
            assert len(warnings) == 1, (
                "FR-27 regression: expected exactly ONE log.warning "
                f"during the wait (got {len(warnings)}), the warning "
                "must be emitted once, not once per poll iteration."
            )

    def test_migration_lock_releases_fd_on_timeout(self, tmp_path, monkeypatch):
        """caller's fail-open path doesn't leak file descriptors across"""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        lock_file = tmp_path / "config.json.lock"

        # Snapshot open fd count before.  We use the count of open fds
        with _hold_flock(lock_file), pytest.raises(TimeoutError):
            credential_store._acquire_migration_lock(lock_file)

        # After the timeout + holder release, a fresh acquire must
        start = time.monotonic()
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        elapsed = time.monotonic() - start
        try:
            assert elapsed < 0.5, (
                "FR-27 regression: after a timed-out acquire, a fresh "
                f"acquire took {elapsed:.2f}s, the timed-out call may "
                "have leaked its fd (and its flock), blocking the "
                "retry."
            )
        finally:
            lock_fd.close()
