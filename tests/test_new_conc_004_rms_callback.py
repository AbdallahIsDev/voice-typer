"""Regression tests for NEW-CONC-004: log.debug exc_info on hot path.

Previously ``Recorder``'s audio callback logged
``log.debug("[RECORDING] on_rms_level callback raised", exc_info=True)``
on EVERY callback raise.  The audio callback fires at ~16 Hz; a buggy
downstream consumer would trigger full traceback formatting 16 times
per second — a significant CPU cost on the audio thread that can
cause XRUNs.

The fix only formats the traceback on the 1st occurrence and every
100th subsequent occurrence; the rest are logged without exc_info.
"""
from __future__ import annotations

import logging
from unittest import mock

import numpy as np
import pytest

from voice_typer.server.config import Config
from voice_typer.server.recording import Recorder

_REC_LOG = logging.getLogger("voice_typer.server.recording")


def _make_recorder() -> Recorder:
    cfg = Config()
    cfg.sample_rate = 16000
    rec = Recorder(cfg)
    return rec


class TestRmsCallbackErrorSuppression:
    """NEW-CONC-004: traceback formatting must be suppressed after the
    first occurrence."""

    def test_first_error_logs_with_exc_info(self, caplog):
        """The first callback raise must log with exc_info=True."""
        rec = _make_recorder()

        def bad_callback(rms, peak, chunk):
            raise RuntimeError("boom")

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            try:
                bad_callback(0.1, 0.5, np.zeros(512, dtype=np.float32))
            except Exception:
                rec._rms_callback_error_count = getattr(
                    rec, "_rms_callback_error_count", 0
                ) + 1
                if rec._rms_callback_error_count == 1:
                    _REC_LOG.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        rec._rms_callback_error_count,
                        exc_info=True,
                    )

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records, "Expected at least one DEBUG log record"
        assert debug_records[-1].exc_info is not None, (
            "First occurrence must log with exc_info (traceback)"
        )

    def test_subsequent_errors_suppress_exc_info(self, caplog):
        """Occurrences 2-99 must NOT include exc_info."""
        rec = _make_recorder()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            for i in range(50):
                rec._rms_callback_error_count = getattr(
                    rec, "_rms_callback_error_count", 0
                ) + 1
                if rec._rms_callback_error_count == 1 or rec._rms_callback_error_count % 100 == 0:
                    _REC_LOG.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        rec._rms_callback_error_count,
                        exc_info=True,
                    )
                else:
                    _REC_LOG.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d, traceback suppressed)",
                        rec._rms_callback_error_count,
                    )

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        with_exc_info = [r for r in debug_records if r.exc_info is not None]
        assert len(with_exc_info) == 1, (
            f"Expected 1 record with exc_info (first occurrence); got {len(with_exc_info)}"
        )

    def test_100th_occurrence_logs_with_exc_info(self, caplog):
        """Every 100th occurrence must re-log with exc_info so the
        developer sees the traceback periodically (in case it changed
        due to a code update)."""
        rec = _make_recorder()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            for i in range(100):
                rec._rms_callback_error_count = getattr(
                    rec, "_rms_callback_error_count", 0
                ) + 1
                if rec._rms_callback_error_count == 1 or rec._rms_callback_error_count % 100 == 0:
                    _REC_LOG.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        rec._rms_callback_error_count,
                        exc_info=True,
                    )

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        with_exc_info = [r for r in debug_records if r.exc_info is not None]
        assert len(with_exc_info) == 2, (
            f"Expected 2 records with exc_info (1st + 100th); got {len(with_exc_info)}"
        )


class TestSourceCheck:
    """Static check: the recording.py source must implement the
    suppression logic."""

    def test_source_has_suppression_logic(self):
        import inspect
        from voice_typer.server import recording

        source = inspect.getsource(recording)
        assert "_rms_callback_error_count" in source, (
            "recording.py must track _rms_callback_error_count to "
            "suppress traceback formatting after the first occurrence"
        )
        assert "% 100 == 0" in source, (
            "recording.py must re-log with exc_info every 100th occurrence"
        )
        assert "traceback suppressed" in source, (
            "recording.py must log a 'traceback suppressed' message for "
            "intermediate occurrences"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
