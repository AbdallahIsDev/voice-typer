"""Regression tests for the Windows branch of ``_acquire_migration_lock``.

Finding (Medium): the Windows branch wrapped
``msvcrt.locking(fd, msvcrt.LK_LOCK, 1)`` in
``with contextlib.suppress(OSError):``. ``msvcrt.locking(LK_LOCK)``
raises ``PermissionError`` (a subclass of ``OSError``) after ~10s
timeout when the lock cannot be acquired. The suppress silently
swallowed this and returned the fd as if the lock had been acquired.
Two app instances could both pass and clobber each other's
``config.json`` write — with no trace in logs.

Fix: replace the ``contextlib.suppress`` with an explicit
``try: msvcrt.locking(...); except OSError: log.warning(...)``.
The fd is still returned (fail-open stance preserved — the caller
``migrate_secrets_to_keyring`` already handles the
``lock_fd is None`` case separately), but the failure is now
VISIBLE in logs so a subsequent race condition is diagnosable.

Platform note
--------------

These tests run on Linux by mocking ``msvcrt`` in ``sys.modules``
and forcing ``_is_windows()`` to return ``True`` so the Windows
code path is exercised. The POSIX branch (``fcntl.flock``) is
unchanged — it blocks indefinitely and never raises, so the
``contextlib.suppress`` is harmless there and is not tested here.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import types

import pytest
from voice_typer.server import credential_store

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield


def _install_fake_msvcrt(monkeypatch, *, locking_impl):
    """Install a fake ``msvcrt`` module in ``sys.modules``.

    ``locking_impl`` is the callable used as ``msvcrt.locking``. The
    fake module also exposes ``LK_LOCK`` and ``LK_NBLCK`` so the
    :func:`_acquire_migration_lock` Windows branch can run regardless
    of which lock mode production code selects.

    The production code uses ``LK_NBLCK`` (non-blocking) inside a
    polled retry loop, not ``LK_LOCK`` (which blocks internally for
    ~1s on Windows before raising ``OSError``). Both constants are
    provided so this fixture is robust to either implementation.
    """
    fake = types.ModuleType("msvcrt")
    fake.locking = locking_impl  # type: ignore[attr-defined]
    fake.LK_LOCK = 1  # type: ignore[attr-defined]
    fake.LK_NBLCK = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    return fake


# ── Tests ───────────────────────────────────────────────────────────────


class TestWindowsMigrationLockTimeout:
    """Windows branch must log visibly when ``msvcrt.locking`` times out."""

    def test_timeout_logs_warning_and_returns_fd(self, tmp_path, monkeypatch, caplog):
        """When ``msvcrt.locking`` raises ``OSError`` (timeout), the
        function must log a visible WARNING and STILL return the fd
        (fail-open, but visible).

        Previously the timeout was swallowed by
        ``contextlib.suppress(OSError)`` and the fd was returned
        silently — two app instances could both pass and clobber each
        other's ``config.json`` write with no trace in logs.
        """
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
            "Windows lock timeout — the fail-open contract is to "
            "return the fd so the caller's finally: lock_fd.close() "
            "works; the caller (migrate_secrets_to_keyring) only "
            "checks for exceptions, not for lock validity."
        )
        # Visible: a warning was logged mentioning the timeout.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, (
            "Regression: _acquire_migration_lock swallowed the Windows "
            "msvcrt.locking timeout silently — no WARNING was logged. "
            "Two app instances could both pass and clobber each other's "
            "config.json write with no trace in logs."
        )
        joined = " ".join(r.getMessage() for r in warnings)
        assert "Windows migration lock acquire timed out" in joined, (
            "Regression: warning was logged but did not mention the "
            "timeout — expected 'Windows migration lock acquire timed "
            f"out' in message. Got: {joined!r}"
        )
        with contextlib.suppress(OSError):
            returned.close()

    def test_timeout_warning_message_mentions_race(self, tmp_path, monkeypatch, caplog):
        """The warning text must mention 'race possible' so operators
        grep-ing logs for race conditions can find it."""
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
        """When ``msvcrt.locking`` succeeds, no warning is logged and
        the fd is returned. This guards against the fix accidentally
        logging on the happy path."""
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
        """The Windows timeout must NOT propagate as an exception —
        the fail-open contract is that the caller
        (``migrate_secrets_to_keyring``) gets a usable fd back.

        If the exception propagated, the caller's ``except Exception``
        would set ``lock_fd = None`` (a different fail-open path) —
        which is also valid, but the spec says return the fd anyway
        so the timeout is logged ONCE at the lock helper layer (not
        re-logged by the caller as 'could not acquire lock')."""
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
        """End-to-end: ``migrate_secrets_to_keyring`` must still
        complete (fail-open) when the Windows lock times out — the
        warning is the only observable effect at this layer.

        This guards against a future refactor that catches the warning
        at the caller and turns it back into a silent fail-open.
        """
        monkeypatch.setattr(credential_store, "_is_windows", lambda: True)

        def _locking_raises(fd, mode, nbytes):
            raise OSError("lock timed out")

        _install_fake_msvcrt(monkeypatch, locking_impl=_locking_raises)

        # Pre-populate config.json with secrets_migrated=True so the
        # migration function returns quickly after acquiring (or
        # failing to acquire) the lock.
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"secrets_migrated": True}))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
            count = credential_store.migrate_secrets_to_keyring()

        # Migration completes (fail-open) — no exception, count is 0
        # because secrets_migrated was already True.
        assert count == 0, (
            f"migrate_secrets_to_keyring returned {count} — expected 0 "
            "since secrets_migrated was already True. The function must "
            "still complete when the Windows lock times out."
        )
        # Warning was logged.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Windows migration lock acquire timed out" in r.getMessage() for r in warnings), (
            f"Expected a warning about the Windows lock timeout, got: {[r.getMessage() for r in warnings]!r}"
        )


class TestPosixBranchUnchanged:
    """Sanity-check that the POSIX branch (``fcntl.flock``) is
    unchanged — it must NOT emit a warning when the lock is acquired
    successfully. This guards against the fix accidentally applying
    to both branches."""

    def test_posix_success_no_warning(self, tmp_path, monkeypatch, caplog):
        """On POSIX (the test host), ``fcntl.flock`` succeeds without
        raising — no warning should be logged."""
        # _is_windows() returns False on Linux by default — no patch.
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
