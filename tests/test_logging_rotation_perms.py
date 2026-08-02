"""regression: post-rotation log file mode must be 0o600 on POSIX.

Previously, ``setup_logging`` set ``os.umask(0o077)`` only inside a
try/finally scope — the ``finally`` restored the parent's umask
(typically 0o022) BEFORE any rotation could fire. Python's stock
``RotatingFileHandler.doRollover`` then created the new active log
file with mode ``0o666 & ~umask = 0o644`` (world-readable on POSIX).
Dictated-text previews, exception tracebacks, hotkey registrations,
and config-path values all landed in a world-readable file on
multi-user POSIX systems.

After the refactor, ``log._SecureRotatingFileHandler.doRollover`` calls
``super().doRollover()`` first, then ``os.chmod(self.baseFilename,
0o600)`` on POSIX — restoring the 0o600 mode on the freshly-created
active log so the privacy guarantee survives rotation.

This test writes >5 MiB of log records to force a rotation, then
asserts the active log file's mode is 0o600. It runs ONLY on POSIX
(on Windows the file mode is governed by ACLs, not the POSIX mode
bits — the chmod is a documented best-effort no-op there).
"""

from __future__ import annotations

import logging
import os
import stat
import sys

import pytest
from voice_typer.server import log as vt_log

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="is POSIX-only — Windows log file perms are governed by ACLs, not the POSIX mode bits",
)


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot + restore the ``voice_typer`` logger state.

    Mirrors the autouse fixture in ``tests/test_logging_setup.py`` so
    a ``setup_logging`` call inside a test does not pollute subsequent
    tests in the same process.
    """
    vt_root = logging.getLogger("voice_typer")
    saved_handlers = list(vt_root.handlers)
    saved_filters = list(vt_root.filters)
    saved_level = vt_root.level
    true_root = logging.getLogger()
    saved_true_handlers = list(true_root.handlers)
    saved_session_id = vt_log._session_id
    yield
    vt_root.handlers = saved_handlers
    vt_root.filters = saved_filters
    vt_root.setLevel(saved_level)
    true_root.handlers = saved_true_handlers
    vt_log._session_id = saved_session_id
    vt_log.close_devnull_files()


def test_handler_is_secure_rotating_subclass(tmp_path, monkeypatch):
    """``setup_logging`` installs a ``_SecureRotatingFileHandler``,
    not the stock ``RotatingFileHandler``. The subclass overrides
    ``doRollover`` to re-chmod the active log file to 0o600 after each
    rotation.
    """
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path)
    file_handlers = [
        h for h in logging.getLogger("voice_typer").handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert file_handlers, "expected a RotatingFileHandler on the voice_typer logger"
    assert isinstance(file_handlers[0], vt_log._SecureRotatingFileHandler), (
        "setup_logging must install a _SecureRotatingFileHandler (the "
        "subclass that re-chmods to 0o600 after each rotation), not the "
        "stock logging.handlers.RotatingFileHandler"
    )
    vt_log.reset()


def test_initial_log_file_mode_is_0o600(tmp_path, monkeypatch):
    """(sanity): the initial active log file is 0o600 on POSIX.

    This is the pre-existing behaviour — the regression test
    below (``test_post_rotation_mode_is_0o600``) asserts the SAME
    mode persists after a rotation fires.
    """
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path)
    log_file = tmp_path / "voice-typer.log"
    assert log_file.exists()
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", f"initial log file mode must be 0o600 on POSIX; got {oct(mode)}"
    vt_log.reset()


def test_post_rotation_mode_is_0o600(tmp_path, monkeypatch):
    """after a rotation fires, the new active log file is 0o600.

    Writes >5 MiB of records to force a rotation, then asserts the
    new active log file's mode is 0o600. Previously, this was 0o644
    (world-readable) because the umask had been restored before the
    rotation fired.
    """
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path, debug=True)
    log_file = tmp_path / "voice-typer.log"
    # Sanity: initial mode is 0o600 ( invariant).
    assert oct(stat.S_IMODE(os.stat(log_file).st_mode)) == "0o600"

    # Force a rotation by writing >5 MiB of records. Use a long
    # sentence per record so the PII redaction filter (which replaces
    # any run of identical chars with ``***``) doesn't collapse the
    # payload. 8 KiB per record -> ~700 records to hit 5 MiB.
    vt_logger = logging.getLogger("voice_typer.server.log_rotation_test")
    vt_logger.setLevel(logging.DEBUG)
    payload = "the quick brown fox jumps over the lazy dog " * 180  # ~8 KiB
    assert len(payload) > 7000
    for _ in range(700):
        vt_logger.info(payload)

    # Flush all handlers so the rotation actually fires.
    for h in logging.getLogger("voice_typer").handlers:
        with __import__("contextlib").suppress(Exception):
            h.flush()

    # The active log file must still be 0o600 after the rotation.
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", (
        f"post-rotation log file mode must be 0o600 on POSIX; got {oct(mode)}. "
        "Previously this was 0o644 (world-readable) because the umask had been "
        "restored before the rotation fired."
    )

    # The rotated backup file (``.1``) must ALSO be 0o600 — it was
    # renamed from the original active file (which was 0o600), so the
    # mode carries over via rename.
    backup = tmp_path / "voice-typer.log.1"
    assert backup.exists(), "expected a rotated backup file voice-typer.log.1 after >5 MiB write"
    backup_mode = stat.S_IMODE(os.stat(backup).st_mode)
    assert oct(backup_mode) == "0o600", f"rotated backup file mode must be 0o600 on POSIX; got {oct(backup_mode)}"
    vt_log.reset()


def test_secure_rotating_file_handler_chmods_after_rollover(tmp_path):
    """unit test: ``_SecureRotatingFileHandler.doRollover`` calls
    ``os.chmod(self.baseFilename, 0o600)`` after ``super().doRollover()``.

    Isolates the subclass behaviour from ``setup_logging`` so a future
    refactor that swaps the handler factory doesn't silently bypass
    the chmod.
    """
    log_file = tmp_path / "secure.log"
    # Construct with a small maxBytes so a single emit triggers rotation.
    handler = vt_log._SecureRotatingFileHandler(log_file, maxBytes=128, backupCount=2)
    handler.setLevel(logging.DEBUG)
    # Manually flip the active file's mode to 0o644 to simulate the
    # post-rotation state (``super().doRollover`` opens the new active
    # file with the current umask, which under pytest is typically
    # 0o022 → 0o644).
    os.chmod(log_file, 0o644)
    assert oct(stat.S_IMODE(os.stat(log_file).st_mode)) == "0o644"

    # Emit enough data to exceed maxBytes, then call doRollover directly.
    record = logging.LogRecord(
        "vt",
        logging.INFO,
        __file__,
        1,
        "x" * 256,
        None,
        None,
    )
    handler.emit(record)
    handler.doRollover()

    # The new active file (post-rollover) must be 0o600.
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", (
        f"_SecureRotatingFileHandler.doRollover must chmod the active file to 0o600; got {oct(mode)}"
    )
    handler.close()


# umask tightening + chmod inside the lock ────────────────────


def test_do_rollover_restores_umask_after_success(tmp_path):
    """``doRollover`` tightens the process umask to 0o077 for the
    duration of the rollover and restores the prior umask in ``finally``.
    """
    log_file = tmp_path / "ue17-umask-restore.log"
    handler = vt_log._SecureRotatingFileHandler(log_file, maxBytes=64, backupCount=1)
    handler.setLevel(logging.DEBUG)

    sentinel_umask = 0o037
    saved = os.umask(sentinel_umask)
    try:
        record = logging.LogRecord("vt", logging.INFO, __file__, 1, "x" * 128, None, None)
        handler.emit(record)
        handler.doRollover()
        assert os.umask(sentinel_umask) == sentinel_umask, (
            f"doRollover must restore the prior umask; expected "
            f"{oct(sentinel_umask)}, got {oct(os.umask(sentinel_umask))}"
        )
    finally:
        os.umask(saved)
        handler.close()


def test_do_rollover_restores_umask_on_early_return(tmp_path):
    """the umask is restored even when ``doRollover`` takes the
    early-return path (``_rotation_needed()`` returns False).
    """
    log_file = tmp_path / "ue17-umask-early.log"
    handler = vt_log._SecureRotatingFileHandler(log_file, maxBytes=1024, backupCount=1)
    handler.setLevel(logging.DEBUG)

    sentinel_umask = 0o037
    saved = os.umask(sentinel_umask)
    try:
        handler.doRollover()
        assert os.umask(sentinel_umask) == sentinel_umask, (
            f"doRollover must restore the prior umask even on the "
            f"early-return path; expected {oct(sentinel_umask)}, got "
            f"{oct(os.umask(sentinel_umask))}"
        )
    finally:
        os.umask(saved)
        handler.close()


def test_do_rollover_umask_tightened_during_super_call(tmp_path, monkeypatch):
    """during ``super().doRollover()`` the umask must be 0o077 so
    the new active log file is born 0o600 from the first ``open()`` call.
    """
    log_file = tmp_path / "ue17-umask-during.log"
    handler = vt_log._SecureRotatingFileHandler(log_file, maxBytes=64, backupCount=1)
    handler.setLevel(logging.DEBUG)

    captured = []
    original = logging.handlers.RotatingFileHandler.doRollover

    def spy(self):
        captured.append(os.umask(0o077))
        os.umask(captured[-1])
        return original(self)

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "doRollover", spy)

    sentinel = 0o022
    saved = os.umask(sentinel)
    try:
        record = logging.LogRecord("vt", logging.INFO, __file__, 1, "x" * 128, None, None)
        handler.emit(record)
        handler.doRollover()
    finally:
        os.umask(saved)
        handler.close()

    assert captured, "test setup failed: super().doRollover() was not invoked"
    assert captured[0] == 0o077, (
        f"during super().doRollover() the umask must be 0o077; got "
        f"{oct(captured[0])}. Previously the umask was the parent's value "
        f"(typically 0o022) and the file was created world-readable (0o644)."
    )


def test_do_rollover_chmod_runs_inside_lock(tmp_path, monkeypatch):
    """the post-rotation ``os.chmod`` runs INSIDE the lock (before
    ``_release_rotation_lock``).
    """
    log_file = tmp_path / "ue17-chmod-order.log"
    handler = vt_log._SecureRotatingFileHandler(log_file, maxBytes=64, backupCount=1)
    handler.setLevel(logging.DEBUG)

    call_order: list[str] = []
    original_release = handler._release_rotation_lock

    def spy_release(fd):
        call_order.append("release")
        return original_release(fd)

    monkeypatch.setattr(handler, "_release_rotation_lock", spy_release)

    original_chmod = os.chmod

    def spy_chmod(path, *args, **kwargs):
        if str(path) == str(log_file):
            call_order.append("chmod")
        return original_chmod(path, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", spy_chmod)

    record = logging.LogRecord("vt", logging.INFO, __file__, 1, "x" * 128, None, None)
    handler.emit(record)
    handler.doRollover()
    handler.close()

    assert "chmod" in call_order, f"chmod not called; call_order={call_order!r}"
    assert "release" in call_order, f"release not called; call_order={call_order!r}"
    assert call_order.index("chmod") < call_order.index("release"), (
        f"chmod must run BEFORE release; got call_order={call_order!r}"
    )


# the fix-5: post-rotation chmod failure must be logged (not silently suppressed) ──


def test_do_rollover_chmod_failure_emits_warning(tmp_path, monkeypatch, caplog):
    """the fix-5: when the post-rotation ``os.chmod`` raises ``OSError``,
    ``doRollover`` must emit a ``WARNING`` log (not silently swallow the
    error via ``contextlib.suppress``).

    Pre-fix, a chmod failure (NFS root-squash, read-only mount, SELinux
    policy) left the freshly-rotated log file at 0o644 (world-readable)
    indefinitely with no operator-visible signal. The WARNING is the
    only surface for this failure mode.

    Also asserts the warning does NOT leak the log file path (the path
    contains the user's home directory — PII). Mirrors the the fix-F13
    stance used for the lock-failure log.
    """
    log_file = tmp_path / "xe19_5_chmod_warn.log"
    handler = vt_log._SecureRotatingFileHandler(log_file, maxBytes=64, backupCount=1)
    handler.setLevel(logging.DEBUG)

    # Make os.chmod raise OSError ONLY for the active log file (so the
    # rotation-lock file chmod, config-dir chmod, etc. are unaffected).
    real_chmod = os.chmod

    def failing_chmod(path, *args, **kwargs):
        if str(path) == str(log_file):
            raise OSError(1, "Operation not permitted (simulated NFS root-squash)")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", failing_chmod)

    # The umask tightening inside doRollover still produces 0o600 at
    # creation; the chmod call is the belt-and-suspenders path that
    # we're forcing to fail. Emit + rollover to trigger the chmod.
    record = logging.LogRecord("vt", logging.INFO, __file__, 1, "x" * 128, None, None)
    with caplog.at_level(logging.WARNING, logger=vt_log.log.name):
        handler.emit(record)
        handler.doRollover()
    handler.close()

    chmod_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "[LOG-SETUP]" in r.getMessage()
        and "chmod to 0o600 failed" in r.getMessage()
    ]
    assert chmod_warnings, (
        "the fix-5: expected a WARNING log when post-rotation os.chmod raises OSError; "
        "got records: " + repr([r.getMessage() for r in caplog.records])
    )
    msg = chmod_warnings[0].getMessage()
    # Must NOT leak the log file path (PII: home directory).
    assert str(log_file) not in msg, (
        f"the fix-5: chmod-failure WARNING must not leak the log file path; got: {msg!r}"
    )
    # Must include the exception class name so the operator can diagnose.
    # ``os.chmod`` raises ``PermissionError`` (a subclass of ``OSError``)
    # on EPERM/EACCES — either name is acceptable as long as SOME
    # exception class name is logged.
    import re

    class_name_match = re.search(r"chmod to 0o600 failed \(([A-Za-z_]+)\)", msg)
    assert class_name_match, (
        f"the fix-5: chmod-failure WARNING must include the exception class name in parens; got: {msg!r}"
    )
    assert class_name_match.group(1), (
        f"the fix-5: exception class name in parens must be non-empty; got: {msg!r}"
    )
