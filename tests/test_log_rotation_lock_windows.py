"""Windows branch of ``_acquire_rotation_lock`` must NOT silently
swallow the ``PermissionError`` raised by ``msvcrt.locking(LK_LOCK, 1)``
after its ~10s timeout.

Previously the Windows branch used ``contextlib.suppress(OSError)``, which
meant two processes racing rotation could both believe they held the lock
and proceed to rotate concurrently. The fix is FAIL-CLOSED: after the
``LK_LOCK`` timeout the function retries once with ``LK_NBLCK`` (non-
blocking); if that also fails, the open fd is closed and ``None`` is
returned so ``doRollover`` skips rotation (no rotation without the
inter-process lock). A ``log.warning`` is emitted so operators can see
the persistent contention.

These tests run on LINUX by mocking out ``os.name`` and injecting a fake
``msvcrt`` module into ``sys.modules`` so the Windows code path executes.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import types

import pytest
from voice_typer.server import log as vt_log

# Real ``msvcrt`` constant values (used here so the fake module mirrors
# the real Windows surface — the implementation references these by name,
# so the values only need to be distinct integers).
_LK_LOCK = 1  # msvcrt.LK_LOCK — blocking, ~10s timeout
_LK_NBLCK = 2  # msvcrt.LK_NBLCK — non-blocking, fails immediately
_LK_UNLCK = 0  # msvcrt.LK_UNLCK — unlock


class _FakeMsvcrt:
    """Minimal stub of :mod:`msvcrt` exposing only ``locking`` + constants.

    ``locking`` is configurable: by default it raises ``PermissionError``
    on BOTH ``LK_LOCK`` and ``LK_NBLCK`` (the real ``LK_LOCK`` behaviour
    after ~10s timeout when the byte range is already locked by another
    process, and the real ``LK_NBLCK`` behaviour when the byte range is
    still locked). Tests can override per-mode behaviour via
    ``raise_on_modes`` to exercise the LK_NBLCK retry-success path.
    """

    LK_LOCK = _LK_LOCK
    LK_NBLCK = _LK_NBLCK
    LK_UNLCK = _LK_UNLCK

    def __init__(self, *, raise_on_modes: frozenset[int] | None = None):
        # Default: raise on LK_LOCK (the timeout failure mode).
        # LK_NBLCK also raises by default — exercises the fail-closed path.
        if raise_on_modes is None:
            raise_on_modes = frozenset({_LK_LOCK, _LK_NBLCK})
        self._raise_on_modes = raise_on_modes
        self.lock_calls: list[tuple[int, int, int]] = []

    def locking(self, fd, mode, nbytes):  # noqa: D401 - mimic msvcrt API
        self.lock_calls.append((fd, mode, nbytes))
        if mode in self._raise_on_modes:
            # PermissionError is an OSError subclass — matches the real
            # ``msvcrt.locking`` timeout / immediate-fail failure mode on
            # Windows.
            raise PermissionError(13, "Permission denied (lock unavailable)")


@pytest.fixture
def windows_env(monkeypatch, tmp_path):
    """Force the Windows code path of ``_acquire_rotation_lock`` on Linux.

    - Patches ``os.name`` to ``"nt"`` (only inside ``vt_log``).
    - Injects a fake ``msvcrt`` module into ``sys.modules`` so the
      function's ``import msvcrt`` succeeds.
    - Attaches a ``logging.NullHandler``-capturing log to the
      ``voice_typer.server.log`` logger so we can assert on warnings.
    """
    fake_msvcrt = _FakeMsvcrt()
    fake_module = types.ModuleType("msvcrt")
    fake_module.locking = fake_msvcrt.locking  # type: ignore[attr-defined]
    fake_module.LK_LOCK = _FakeMsvcrt.LK_LOCK  # type: ignore[attr-defined]
    fake_module.LK_NBLCK = _FakeMsvcrt.LK_NBLCK  # type: ignore[attr-defined]
    fake_module.LK_UNLCK = _FakeMsvcrt.LK_UNLCK  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "msvcrt", fake_module)
    # Patch ``os.name`` only as seen by the log module — the real ``os``
    # module is shared, so we patch the attribute on the imported alias.
    monkeypatch.setattr(vt_log.os, "name", "nt", raising=True)

    logger = logging.getLogger("voice_typer.server.log")
    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    capture = _CaptureHandler(level=logging.WARNING)
    logger.addHandler(capture)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)

    yield {
        "msvcrt": fake_msvcrt,
        "records": records,
        "tmp_path": tmp_path,
    }

    logger.removeHandler(capture)
    logger.setLevel(prev_level)


def _make_handler(tmp_path) -> vt_log._SecureTruncatingFileHandler:
    log_file = tmp_path / "app.log"
    return vt_log._SecureTruncatingFileHandler(str(log_file), maxBytes=1024, backupCount=0)


def test_windows_lock_timeout_fails_closed_and_warns(windows_env):
    """When ``msvcrt.locking`` raises ``OSError`` on BOTH ``LK_LOCK`` and
    the ``LK_NBLCK`` retry, the Windows branch must:

    1. Emit a ``log.warning`` mentioning the fail-closed rotation skip.
    2. Return ``None`` (fail-CLOSED) so ``doRollover`` skips rotation.
    3. Close the open fd so it is not leaked.

    Pre-fix the branch used ``contextlib.suppress(OSError)`` and returned
    ``fd`` (fail-open) — two processes racing rotation could both believe
    they held the lock and proceed to clobber each other's rename.
    """
    handler = _make_handler(windows_env["tmp_path"])
    fd = handler._acquire_rotation_lock()
    try:
        # Fail-closed: ``None`` must be returned (NOT an int fd) so the
        # caller's ``_rotation_needed()`` short-circuit kicks in and no
        # rotation is attempted without the inter-process lock.
        assert fd is None, (
            "fail-closed must return None when both LK_LOCK and LK_NBLCK fail; "
            "returning fd would let two racing processes both proceed to rotate"
        )
    finally:
        # If a bug regresses to fail-open, close the returned fd so the
        # test does not leak file descriptors.
        if isinstance(fd, int):
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    # Visible: a warning must have been logged.
    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert warnings, "expected at least one warning record on persistent lock failure"
    msg = warnings[0].getMessage()
    assert "LOG-SETUP" in msg, msg
    assert "Windows rotation lock acquire failed" in msg, msg
    assert "fail-closed" in msg, msg

    # ``msvcrt.locking`` must have been invoked TWICE: once with LK_LOCK
    # (blocking ~10s) and once with LK_NBLCK (non-blocking retry).
    modes_called = [call[1] for call in windows_env["msvcrt"].lock_calls]
    assert _LK_LOCK in modes_called, f"LK_LOCK must be attempted; got {modes_called!r}"
    assert _LK_NBLCK in modes_called, f"LK_NBLCK retry must be attempted after LK_LOCK times out; got {modes_called!r}"
    # LK_LOCK must come BEFORE LK_NBLCK (retry happens after timeout).
    assert modes_called.index(_LK_LOCK) < modes_called.index(_LK_NBLCK), (
        f"LK_LOCK must precede LK_NBLCK retry; got {modes_called!r}"
    )


def test_windows_lock_nblck_retry_succeeds_after_lk_lock_timeout(windows_env):
    """When ``LK_LOCK`` times out but the ``LK_NBLCK`` retry succeeds,
    the Windows branch must:

    1. Return the open fd (lock IS now held — the byte was released
       during the ~10s block).
    2. Emit NO warning (the lock was eventually acquired; no operator
       action needed).
    """
    # LK_LOCK raises (timeout), LK_NBLCK succeeds (byte released in the
    # ~10s window between the two calls).
    windows_env["msvcrt"]._raise_on_modes = frozenset({_LK_LOCK})  # type: ignore[attr-defined]

    handler = _make_handler(windows_env["tmp_path"])
    try:
        fd = handler._acquire_rotation_lock()
    finally:
        if isinstance(fd, int):
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    # The retry succeeded — the fd must be returned (lock held).
    assert isinstance(fd, int), (
        "fd must be returned when LK_NBLCK retry succeeds (lock IS held); fail-closed is only for the both-fail path"
    )

    # No warning should be emitted — the lock was acquired.
    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert not warnings, f"unexpected warnings on LK_NBLCK retry success (lock acquired): {warnings}"

    # Both modes must have been attempted: LK_LOCK first, LK_NBLCK retry second.
    modes_called = [call[1] for call in windows_env["msvcrt"].lock_calls]
    assert modes_called == [_LK_LOCK, _LK_NBLCK], f"expected [LK_LOCK, LK_NBLCK] call sequence; got {modes_called!r}"


def test_windows_lock_success_no_warning(windows_env, monkeypatch):
    """When ``msvcrt.locking`` succeeds on the first ``LK_LOCK`` call, no
    warning is emitted, no ``LK_NBLCK`` retry is attempted, and the fd is
    returned."""
    # LK_LOCK succeeds (no modes raise).
    windows_env["msvcrt"]._raise_on_modes = frozenset()  # type: ignore[attr-defined]

    handler = _make_handler(windows_env["tmp_path"])
    try:
        fd = handler._acquire_rotation_lock()
    finally:
        if isinstance(fd, int):
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    assert isinstance(fd, int), "fd must be returned on successful lock"
    modes_called = [call[1] for call in windows_env["msvcrt"].lock_calls]
    assert modes_called == [_LK_LOCK], (
        f"LK_NBLCK retry must NOT be attempted when LK_LOCK succeeds; got {modes_called!r}"
    )
    # No warning records should exist on the success path.
    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert not warnings, f"unexpected warnings on success: {warnings}"


def test_windows_warning_does_not_leak_lock_file_path(windows_env):
    """The warning message must NOT include the lock file path (PII: home dir).

    This mirrors the the fix-F13 stance used elsewhere in this module: log
    only enough for an operator to diagnose the failure mode, never the
    on-disk path layout.
    """
    handler = _make_handler(windows_env["tmp_path"])
    fd = handler._acquire_rotation_lock()
    try:
        assert fd is None, "fail-closed must return None on persistent lock failure"
    finally:
        if isinstance(fd, int):
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert warnings
    lock_path = handler._rotation_lock_path
    assert lock_path not in warnings[0].getMessage(), "lock file path must not be leaked in warning"
