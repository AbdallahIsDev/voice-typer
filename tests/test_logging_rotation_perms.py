"""FR-2 regression: post-rotation log file mode must be 0o600 on POSIX.

Pre-FR-2, ``setup_logging`` set ``os.umask(0o077)`` only inside a
try/finally scope — the ``finally`` restored the parent's umask
(typically 0o022) BEFORE any rotation could fire. Python's stock
``RotatingFileHandler.doRollover`` then created the new active log
file with mode ``0o666 & ~umask = 0o644`` (world-readable on POSIX).
Dictated-text previews, exception tracebacks, hotkey registrations,
and config-path values all landed in a world-readable file on
multi-user POSIX systems.

Post-FR-2, ``log._SecureRotatingFileHandler.doRollover`` calls
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
    reason="FR-2 is POSIX-only — Windows log file perms are governed by ACLs, not the POSIX mode bits",
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
    """FR-2: ``setup_logging`` installs a ``_SecureRotatingFileHandler``,
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
        "FR-2: setup_logging must install a _SecureRotatingFileHandler (the "
        "subclass that re-chmods to 0o600 after each rotation), not the "
        "stock logging.handlers.RotatingFileHandler"
    )
    vt_log.reset()


def test_initial_log_file_mode_is_0o600(tmp_path, monkeypatch):
    """FR-2 (sanity): the initial active log file is 0o600 on POSIX.

    This is the pre-existing G4-H-07 behaviour — the regression test
    below (``test_post_rotation_mode_is_0o600``) asserts the SAME
    mode persists after a rotation fires.
    """
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path)
    log_file = tmp_path / "voice-typer.log"
    assert log_file.exists()
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", (
        f"FR-2: initial log file mode must be 0o600 on POSIX; got {oct(mode)}"
    )
    vt_log.reset()


def test_post_rotation_mode_is_0o600(tmp_path, monkeypatch):
    """FR-2: after a rotation fires, the new active log file is 0o600.

    Writes >5 MiB of records to force a rotation, then asserts the
    new active log file's mode is 0o600. Pre-FR-2, this was 0o644
    (world-readable) because the umask had been restored before the
    rotation fired.
    """
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path, debug=True)
    log_file = tmp_path / "voice-typer.log"
    # Sanity: initial mode is 0o600 (G4-H-07 invariant).
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
        f"FR-2: post-rotation log file mode must be 0o600 on POSIX; got {oct(mode)}. "
        "Pre-FR-2 this was 0o644 (world-readable) because the umask had been "
        "restored before the rotation fired."
    )

    # The rotated backup file (``.1``) must ALSO be 0o600 — it was
    # renamed from the original active file (which was 0o600), so the
    # mode carries over via rename.
    backup = tmp_path / "voice-typer.log.1"
    assert backup.exists(), "FR-2: expected a rotated backup file voice-typer.log.1 after >5 MiB write"
    backup_mode = stat.S_IMODE(os.stat(backup).st_mode)
    assert oct(backup_mode) == "0o600", (
        f"FR-2: rotated backup file mode must be 0o600 on POSIX; got {oct(backup_mode)}"
    )
    vt_log.reset()


def test_secure_rotating_file_handler_chmods_after_rollover(tmp_path):
    """FR-2 unit test: ``_SecureRotatingFileHandler.doRollover`` calls
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
        "vt", logging.INFO, __file__, 1, "x" * 256, None, None,
    )
    handler.emit(record)
    handler.doRollover()

    # The new active file (post-rollover) must be 0o600.
    mode = stat.S_IMODE(os.stat(log_file).st_mode)
    assert oct(mode) == "0o600", (
        f"FR-2: _SecureRotatingFileHandler.doRollover must chmod the active "
        f"file to 0o600; got {oct(mode)}"
    )
    handler.close()
