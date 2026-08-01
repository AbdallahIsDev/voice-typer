"""SI-13: Windows branch of ``_acquire_rotation_lock`` must NOT silently
swallow the ``PermissionError`` raised by ``msvcrt.locking(LK_LOCK, 1)``
after its ~10s timeout.

Previously the Windows branch used ``contextlib.suppress(OSError)``, which
meant two processes racing rotation could both believe they held the lock
and proceed to rotate concurrently. The fix keeps the fail-open stance
(still returns the fd so logging keeps working) but emits a visible
``log.warning`` so operators can see the race risk.

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


class _FakeMsvcrt:
    """Minimal stub of :mod:`msvcrt` exposing only ``locking`` + constants.

    ``locking`` is configurable: by default it raises ``PermissionError``
    (the real ``LK_LOCK`` behaviour after ~10s timeout when the byte range
    is already locked by another process).
    """

    LK_LOCK = 2
    LK_UNLCK = 3

    def __init__(self, *, raise_on_lock: bool = True):
        self._raise_on_lock = raise_on_lock
        self.lock_calls: list[tuple[int, int, int]] = []

    def locking(self, fd, mode, nbytes):  # noqa: D401 - mimic msvcrt API
        self.lock_calls.append((fd, mode, nbytes))
        if self._raise_on_lock:
            # PermissionError is an OSError subclass — matches the real
            # ``msvcrt.locking`` timeout failure mode on Windows.
            raise PermissionError(13, "Permission denied (lock timeout)")


@pytest.fixture
def windows_env(monkeypatch, tmp_path):
    """Force the Windows code path of ``_acquire_rotation_lock`` on Linux.

    - Patches ``os.name`` to ``"nt"`` (only inside ``vt_log``).
    - Injects a fake ``msvcrt`` module into ``sys.modules`` so the
      function's ``import msvcrt`` succeeds.
    - Attaches a ``logging.NullHandler``-capturing log to the
      ``voice_typer.server.log`` logger so we can assert on warnings.
    """
    fake_msvcrt = _FakeMsvcrt(raise_on_lock=True)
    fake_module = types.ModuleType("msvcrt")
    fake_module.locking = fake_msvcrt.locking  # type: ignore[attr-defined]
    fake_module.LK_LOCK = _FakeMsvcrt.LK_LOCK  # type: ignore[attr-defined]
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


def _make_handler(tmp_path) -> vt_log._SecureRotatingFileHandler:
    log_file = tmp_path / "app.log"
    return vt_log._SecureRotatingFileHandler(str(log_file), maxBytes=1024, backupCount=2)


def test_windows_lock_timeout_emits_warning_and_returns_fd(windows_env):
    """When ``msvcrt.locking`` raises ``OSError`` the Windows branch must:

    1. Emit a ``log.warning`` mentioning the rotation race risk.
    2. Still return the open fd (fail-open, not fail-closed).
    """
    handler = _make_handler(windows_env["tmp_path"])
    try:
        fd = handler._acquire_rotation_lock()
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    # Fail-open: the fd must still be returned (an int) so logging works.
    assert isinstance(fd, int), "fail-open must still return fd"

    # Visible: a warning must have been logged.
    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert warnings, "expected at least one warning record on lock timeout"
    msg = warnings[0].getMessage()
    assert "LOG-SETUP" in msg, msg
    assert "Windows rotation lock acquire timed out" in msg, msg
    assert "rotation race possible" in msg, msg

    # And ``msvcrt.locking`` must actually have been invoked (sanity).
    assert windows_env["msvcrt"].lock_calls, "msvcrt.locking must be called"


def test_windows_lock_success_no_warning(windows_env, monkeypatch):
    """When ``msvcrt.locking`` succeeds, no warning is emitted and fd returned."""
    windows_env["msvcrt"]._raise_on_lock = False  # type: ignore[attr-defined]

    handler = _make_handler(windows_env["tmp_path"])
    try:
        fd = handler._acquire_rotation_lock()
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    assert isinstance(fd, int), "fd must be returned on successful lock"
    assert windows_env["msvcrt"].lock_calls, "locking must be attempted"
    # No warning records should exist on the success path.
    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert not warnings, f"unexpected warnings on success: {warnings}"


def test_windows_warning_does_not_leak_lock_file_path(windows_env):
    """The warning message must NOT include the lock file path (PII: home dir).

    This mirrors the UE-4-F13 stance used elsewhere in this module: log
    only enough for an operator to diagnose the failure mode, never the
    on-disk path layout.
    """
    handler = _make_handler(windows_env["tmp_path"])
    try:
        fd = handler._acquire_rotation_lock()
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        handler.close()

    warnings = [r for r in windows_env["records"] if r.levelno == logging.WARNING]
    assert warnings
    lock_path = handler._rotation_lock_path
    assert lock_path not in warnings[0].getMessage(), "lock file path must not be leaked in warning"
