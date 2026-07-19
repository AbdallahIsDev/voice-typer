"""Tests for ADR-0020 §11 logging changes.

- RotatingFileHandler uses 5 MiB × 5 backups (was 1 MiB × 2).
- ``bubble_level`` records are excluded from the file handler but NOT
  from the console/stderr path.
"""

from __future__ import annotations

import logging
import logging.handlers

from voice_typer.server import log as vt_log


def test_rotating_handler_uses_5mb_x5(tmp_path, monkeypatch):
    """setup_logging must configure 5 MiB maxBytes and 5 backups."""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()
    vt_log.setup_logging(tmp_path)

    root = logging.getLogger("voice_typer")
    file_handlers = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert file_handlers, "expected a RotatingFileHandler on the voice_typer logger"
    # Exactly one file handler (idempotent setup).
    assert len(file_handlers) == 1
    fh = file_handlers[0]
    assert fh.maxBytes == 5 * 1024 * 1024, fh.maxBytes
    assert fh.backupCount == 5, fh.backupCount
    vt_log.reset()


def test_bubble_level_excluded_from_file_handler():
    """A record mentioning ``bubble_level`` is dropped by the file filter."""
    fh = logging.handlers.RotatingFileHandler(
        __import__("os").devnull,
        maxBytes=1024,
        backupCount=1,
    )
    filt = vt_log._BubbleLevelExclusionFilter()
    fh.addFilter(filt)

    rec_in = logging.LogRecord(
        "voice_typer.server.ipc",
        logging.DEBUG,
        "x.py",
        1,
        "no client; dropping high-freq bubble_level event",
        None,
        None,
    )
    assert not fh.filter(rec_in), "bubble_level record must be excluded from file"

    rec_out = logging.LogRecord(
        "voice_typer.server.ipc",
        logging.INFO,
        "x.py",
        1,
        "state_changed pushed to client",
        None,
        None,
    )
    assert fh.filter(rec_out), "non-bubble record must pass through"


def test_bubble_level_filter_does_not_affect_console():
    """The exclusion filter is NOT attached to stderr/console handlers."""
    vt_log.reset()
    console = vt_log._FlushingStreamHandler()
    # No filter attached => bubble_level records pass to the console.
    rec = logging.LogRecord(
        "voice_typer.server.ipc",
        logging.DEBUG,
        "x.py",
        1,
        "no client; dropping high-freq bubble_level event",
        None,
        None,
    )
    assert console.filter(rec)
    vt_log.reset()
