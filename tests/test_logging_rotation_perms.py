"""regression: post-rotation log file mode must be 0o600 on POSIX."""

from __future__ import annotations

import logging
import os
import stat
import sys

import pytest
from voice_typer.server import log as vt_log

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="is POSIX-only, Windows log file perms are governed by ACLs, not the POSIX mode bits",
)


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot + restore the ``voice_typer`` logger state."""
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


def test_handler_is_secure_truncating_subclass(tmp_path, monkeypatch):
    """``setup_logging`` installs a ``_SecureTruncatingFileHandler``,"""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path)
    file_handlers = [
        h for h in logging.getLogger("voice_typer").handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert file_handlers, "expected a RotatingFileHandler on the voice_typer logger"
    assert isinstance(file_handlers[0], vt_log._SecureTruncatingFileHandler), (
        "setup_logging must install a _SecureTruncatingFileHandler (the "
        "subclass that re-chmods to 0o600 after each truncate), not the "
        "stock logging.handlers.RotatingFileHandler"
    )
    vt_log.reset()


def test_initial_log_file_mode_is_0o600(tmp_path, monkeypatch):
    """(sanity): the initial active log file is 0o600 on POSIX."""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path)
    # O1 layout: the active log lives under ``<config_dir>/logs/``.
    log_file = vt_log.get_log_file_path(tmp_path)
    assert log_file.exists()
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", f"initial log file mode must be 0o600 on POSIX; got {oct(mode)}"
    vt_log.reset()


def test_truncate_in_place_keeps_mode_0o600(tmp_path, monkeypatch):
    """
    after the single-file truncation fires, the ACTIVE log file
    Pins a small ``maxBytes`` on the installed handler (production's
    """
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path, debug=True)
    # O1 layout: the active log lives under ``<config_dir>/logs/``.
    log_file = vt_log.get_log_file_path(tmp_path)
    # Sanity: initial mode is 0o600 ( invariant).
    assert oct(stat.S_IMODE(os.stat(log_file).st_mode)) == "0o600"

    file_handlers = [
        h for h in logging.getLogger("voice_typer").handlers if isinstance(h, vt_log._SecureTruncatingFileHandler)
    ]
    assert file_handlers, "expected the _SecureTruncatingFileHandler installed by setup_logging"
    monkeypatch.setattr(file_handlers[0], "maxBytes", 512 * 1024)

    # Force a truncate-in-place by writing past the pinned threshold. Use
    vt_logger = logging.getLogger("voice_typer.server.log_rotation_test")
    vt_logger.setLevel(logging.DEBUG)
    payload = "the quick brown fox jumps over the lazy dog " * 180  # ~8 KiB
    assert len(payload) > 7000
    for _ in range(100):
        vt_logger.info(payload)

    # Flush all handlers so the truncation actually fires.
    for h in logging.getLogger("voice_typer").handlers:
        with __import__("contextlib").suppress(Exception):
            h.flush()

    # The active log file must still be 0o600 after the truncate.
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", (
        f"post-truncate log file mode must be 0o600 on POSIX; got {oct(mode)}. "
        "Previously this was 0o644 (world-readable) because the umask had been "
        "restored before the rotation fired."
    )

    # Single-file policy: NO numbered backup may exist.
    backup = vt_log.get_logs_dir(tmp_path) / "lausu.log.1"
    assert not backup.exists(), (
        "single-file policy: lausu.log.1 must NOT be created, the log "
        "truncates in place instead of rotating to numbered backups"
    )
    vt_log.reset()


def test_secure_truncating_file_handler_truncates_and_chmods(tmp_path):
    """unit test: ``_SecureTruncatingFileHandler.doRollover`` truncates"""
    log_file = tmp_path / "secure.log"
    # Construct with a small maxBytes so a single emit triggers truncation.
    handler = vt_log._SecureTruncatingFileHandler(log_file, maxBytes=128, backupCount=0)
    handler.setLevel(logging.DEBUG)
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

    # The active file (single file, truncated in place) must be 0o600.
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", (
        f"_SecureTruncatingFileHandler.doRollover must chmod the active file to 0o600; got {oct(mode)}"
    )
    # Single-file policy: no numbered backup.
    assert not (tmp_path / "secure.log.1").exists(), "secure.log.1 must not exist"
    handler.close()


def test_do_rollover_leaves_umask_untouched(tmp_path):
    """``doRollover`` (truncate-in-place, single-file policy) NEVER"""
    log_file = tmp_path / "ue17-umask-restore.log"
    handler = vt_log._SecureTruncatingFileHandler(log_file, maxBytes=64, backupCount=0)
    handler.setLevel(logging.DEBUG)

    sentinel_umask = 0o037
    saved = os.umask(sentinel_umask)
    try:
        record = logging.LogRecord("vt", logging.INFO, __file__, 1, "x" * 128, None, None)
        handler.emit(record)
        handler.doRollover()
        assert os.umask(sentinel_umask) == sentinel_umask, (
            f"doRollover must leave the process umask untouched; expected "
            f"{oct(sentinel_umask)}, got {oct(os.umask(sentinel_umask))}"
        )
    finally:
        os.umask(saved)
        handler.close()


def test_do_rollover_leaves_umask_untouched_on_early_return(tmp_path):
    """the process umask is left untouched even when ``doRollover``"""
    log_file = tmp_path / "ue17-umask-early.log"
    handler = vt_log._SecureTruncatingFileHandler(log_file, maxBytes=1024, backupCount=0)
    handler.setLevel(logging.DEBUG)

    sentinel_umask = 0o037
    saved = os.umask(sentinel_umask)
    try:
        handler.doRollover()
        assert os.umask(sentinel_umask) == sentinel_umask, (
            f"doRollover must leave the process umask untouched even on "
            f"the early-return path; expected {oct(sentinel_umask)}, got "
            f"{oct(os.umask(sentinel_umask))}"
        )
    finally:
        os.umask(saved)
        handler.close()


def test_do_rollover_truncates_in_place_without_super_rollover(tmp_path, monkeypatch):
    """would rename the file to a numbered ``.1`` backup). The process umask"""
    log_file = tmp_path / "ue17-inplace.log"
    handler = vt_log._SecureTruncatingFileHandler(log_file, maxBytes=64, backupCount=0)
    handler.setLevel(logging.DEBUG)

    # Pre-seed the active file with content that exceeds maxBytes.
    with open(log_file, "w", encoding="utf-8") as fh:
        fh.write("x" * 200)

    # Spy on the stock doRollover, it must NOT be invoked.
    original = logging.handlers.RotatingFileHandler.doRollover
    calls = []

    def spy(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(logging.handlers.RotatingFileHandler, "doRollover", spy)

    sentinel = 0o022
    saved = os.umask(sentinel)
    try:
        handler.doRollover()
        # The stock rename-based rollover was never invoked.
        assert not calls, "super().doRollover() must NOT be called (single-file policy)"
        # The active file was truncated IN PLACE, still exists, 0 bytes.
        assert os.path.exists(log_file)
        assert os.path.getsize(log_file) == 0
        # No numbered backup was created.
        assert not os.path.exists(f"{log_file}.1")
    finally:
        os.umask(saved)
        handler.close()


def test_do_rollover_chmod_runs_inside_lock(tmp_path, monkeypatch):
    """the post-rotation ``os.chmod`` runs INSIDE the lock (before"""
    log_file = tmp_path / "ue17-chmod-order.log"
    handler = vt_log._SecureTruncatingFileHandler(log_file, maxBytes=64, backupCount=0)
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
    # Drop the setup emit's own lock release: only the rollover below counts.
    call_order.clear()
    handler.doRollover()
    handler.close()

    assert "chmod" in call_order, f"chmod not called; call_order={call_order!r}"
    assert "release" in call_order, f"release not called; call_order={call_order!r}"
    assert call_order.index("chmod") < call_order.index("release"), (
        f"chmod must run BEFORE release; got call_order={call_order!r}"
    )


def test_do_rollover_chmod_failure_emits_warning(tmp_path, monkeypatch, caplog):
    """the fix-5: when the post-rotation ``os.chmod`` raises ``OSError``,"""
    log_file = tmp_path / "xe19_5_chmod_warn.log"
    handler = vt_log._SecureTruncatingFileHandler(log_file, maxBytes=64, backupCount=0)
    handler.setLevel(logging.DEBUG)

    real_chmod = os.chmod

    def failing_chmod(path, *args, **kwargs):
        if str(path) == str(log_file):
            raise OSError(1, "Operation not permitted (simulated NFS root-squash)")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", failing_chmod)

    # The umask tightening inside doRollover still produces 0o600 at
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
    assert str(log_file) not in msg, f"the fix-5: chmod-failure WARNING must not leak the log file path; got: {msg!r}"
    # Must include the exception class name so the operator can diagnose.
    import re

    class_name_match = re.search(r"chmod to 0o600 failed \(([A-Za-z_]+)\)", msg)
    assert class_name_match, (
        f"the fix-5: chmod-failure WARNING must include the exception class name in parens; got: {msg!r}"
    )
    assert class_name_match.group(1), f"the fix-5: exception class name in parens must be non-empty; got: {msg!r}"
