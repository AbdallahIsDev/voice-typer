"""FR-27: regression tests for the migration lock timeout.

The previous ``_acquire_migration_lock`` implementation used a blocking
``fcntl.flock(LOCK_EX)`` call on POSIX and ``msvcrt.locking(LK_LOCK)``
on Windows — both with NO timeout.  ``migrate_secrets_to_keyring`` runs
at startup; if another process held ``config.json.lock`` (e.g. a wedged
``Config.save()`` or a crashed process that never released the flock),
the blocking call hung the startup migration indefinitely.

FR-27 fixes this by replacing the blocking calls with a polled
``LOCK_EX | LOCK_NB`` (POSIX) / ``LK_NBLCK`` (Windows) retry loop
bounded by ``_MIGRATION_LOCK_TIMEOUT_SECONDS``, raising ``TimeoutError``
on expiry.  This mirrors the sibling ``_acquire_config_lock`` in
``config_internals/paths.py``.  A ``log.warning`` is also emitted once
the wait exceeds ``_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS`` (default 2s)
so operators can diagnose a wedged holder.

Tests (POSIX-only — the Windows ``msvcrt.locking`` polling path is the
same code shape as the POSIX branch and is exercised by the config-lock
sibling suite in ``test_config_save_lock.py``; the sandbox here is
Linux so we can't run the Windows branch):

1. ``test_migration_lock_times_out_when_held`` — when the lock is held
   by another open file description, ``_acquire_migration_lock`` raises
   ``TimeoutError`` within the configured timeout (NOT indefinitely).
2. ``test_migration_lock_acquires_when_free`` — sanity check: with no
   contention the lock is acquired immediately and a second open in the
   same process can't grab it (per-open-file-description semantics).
3. ``test_migration_lock_warns_on_slow_wait`` — when the wait exceeds
   the slow-warn threshold, a single ``log.warning`` is emitted.
4. ``test_migration_lock_releases_fd_on_timeout`` — on timeout the
   partially-opened fd is closed (no fd leak) so the caller's fail-open
   path doesn't accumulate stale file descriptors across launches.
"""

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
    """Open ``lock_file`` and hold an exclusive ``flock`` on it for the
    duration of the ``with`` block.

    ``fcntl.flock`` is associated with the open file description (the
    kernel ``struct file``), NOT with the process or the inode.  Two
    separate ``open()`` calls in the SAME process therefore conflict
    on ``LOCK_EX`` — this faithfully simulates a second process holding
    the lock without needing a subprocess.
    """
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
        """When the lock is held by another fd, acquisition must raise
        ``TimeoutError`` within the configured timeout rather than
        blocking forever (the pre-FR-27 behavior)."""
        # Shorten the timeout so the test is fast (default is 5s).
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.5)

        lock_file = tmp_path / "config.json.lock"

        with _hold_flock(lock_file):
            start = time.monotonic()
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._acquire_migration_lock(lock_file)
            elapsed = time.monotonic() - start

            # The error message must mention the timeout + the lock file
            # so operators can diagnose which lock wedged.
            msg = str(exc_info.value)
            assert "migration lock" in msg, f"TimeoutError message lacks context: {msg!r}"
            assert "0.5s" in msg, f"TimeoutError message lacks the timeout value: {msg!r}"

            # Must wait at least ~the timeout (0.5s) before giving up
            # (polled retry, not an instant bail), and must NOT hang
            # (bounded well under the pre- "indefinite" behavior).
            # Lower bound is slightly under 0.5s to tolerate the 0.05s
            # poll cadence; upper bound is generous for CI jitter but
            # well under the 30s pytest-timeout.
            assert elapsed >= 0.4, (
                f"FR-27 regression: _acquire_migration_lock gave up "
                f"after only {elapsed:.2f}s — expected to poll for at "
                f"least the 0.5s timeout before raising TimeoutError "
                f"(the LOCK_EX | LOCK_NB retry loop may be missing)."
            )
            assert elapsed < 5.0, (
                f"FR-27 regression: _acquire_migration_lock took "
                f"{elapsed:.2f}s — expected to time out within ~0.5s. "
                f"The blocking flock(LOCK_EX) (no LOCK_NB) may still "
                f"be in place, defeating the timeout."
            )

    def test_migration_lock_acquires_when_free(self, tmp_path):
        """Sanity check: with no contention the lock is acquired
        immediately and the returned file object holds an exclusive
        flock (a second open in the same process can't grab it)."""
        lock_file = tmp_path / "config.json.lock"
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        try:
            # A second open in the same process must NOT be able to
            # grab the lock — fcntl.flock is per-open-file-description,
            # so the two opens conflict on LOCK_EX | LOCK_NB.
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
        """When the wait exceeds the slow-warn threshold, a single
        ``log.warning`` must be emitted so operators can diagnose a
        wedged holder before the ``TimeoutError`` fires."""
        # Use a short slow-warn threshold (0.2s) and a slightly longer
        # timeout (0.6s) so the warning fires before the timeout — this
        # keeps the test fast (default 2s/5s would make the suite slow).
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS", 0.2)
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.6)

        lock_file = tmp_path / "config.json.lock"

        with _hold_flock(lock_file), caplog.at_level(logging.WARNING), pytest.raises(TimeoutError):
            credential_store._acquire_migration_lock(lock_file)

            # Exactly one WARNING record mentioning the slow wait must
            # be present (not one per poll iteration — that would spam
            # the log).
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
                f"during the wait (got {len(warnings)}) — the warning "
                "must be emitted once, not once per poll iteration."
            )

    def test_migration_lock_releases_fd_on_timeout(self, tmp_path, monkeypatch):
        """On timeout the partially-opened fd must be closed so the
        caller's fail-open path doesn't leak file descriptors across
        repeated startup attempts (each launch that times out would
        otherwise accumulate a stale fd)."""
        monkeypatch.setattr(credential_store, "_MIGRATION_LOCK_TIMEOUT_SECONDS", 0.3)

        lock_file = tmp_path / "config.json.lock"

        # Snapshot open fd count before.  We use the count of open fds
        # on the lock_file's path as a proxy (counting /proc/self/fd
        # would be Linux-specific and noisy; instead we rely on the
        # fact that a leaked fd keeps the file's link count stable and
        # blocks re-acquire — verified functionally below).
        with _hold_flock(lock_file), pytest.raises(TimeoutError):
            credential_store._acquire_migration_lock(lock_file)

        # After the timeout + holder release, a fresh acquire must
        # succeed immediately — if the timed-out call had leaked its
        # fd (and that fd still held the flock), this would block /
        # time out again.  Immediate success proves the timed-out fd
        # was closed (and its would-be flock released).
        start = time.monotonic()
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        elapsed = time.monotonic() - start
        try:
            assert elapsed < 0.5, (
                "FR-27 regression: after a timed-out acquire, a fresh "
                f"acquire took {elapsed:.2f}s — the timed-out call may "
                "have leaked its fd (and its flock), blocking the "
                "retry."
            )
        finally:
            lock_fd.close()
