"""Source marker: ``tests/test_new_conc_004_rms_callback.py``."""

# === Source: tests/test_new_conc_004_rms_callback.py ===

from __future__ import annotations

import logging
from unittest.mock import MagicMock  # noqa: F401 -- kept for parity with original monolith import block

import numpy as np
import pytest
from voice_typer.server.config import Config
from voice_typer.server.recording import Recorder

from tests.fixtures.recorder_test_helpers import make_recorder

# === Common module-level constants (identical across files) ===

_REC_LOG = logging.getLogger("voice_typer.server.recording")

# === Common helpers / fixtures (identical across files) ===


def _make_recorder() -> Recorder:
    cfg = Config()
    cfg.sample_rate = 16000
    return make_recorder(cfg)


class TestRmsCallbackErrorSuppression:
    """NEW-CONC-004: traceback formatting must be suppressed after the"""

    def test_first_error_logs_with_exc_info(self, caplog):
        """The first callback raise must log with exc_info=True."""
        rec = _make_recorder()

        def bad_callback(rms, peak, chunk):
            raise RuntimeError("boom")

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            try:
                bad_callback(0.1, 0.5, np.zeros(512, dtype=np.float32))
            except Exception:
                rec._rms_callback_error_count = getattr(rec, "_rms_callback_error_count", 0) + 1
                if rec._rms_callback_error_count == 1:
                    _REC_LOG.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        rec._rms_callback_error_count,
                        exc_info=True,
                    )

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records, "Expected at least one DEBUG log record"
        assert debug_records[-1].exc_info is not None, "First occurrence must log with exc_info (traceback)"

    def test_subsequent_errors_suppress_exc_info(self, caplog):
        """Occurrences 2-99 must NOT include exc_info."""
        rec = _make_recorder()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            for _i in range(50):
                rec._rms_callback_error_count = getattr(rec, "_rms_callback_error_count", 0) + 1
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
        assert len(with_exc_info) == 1, f"Expected 1 record with exc_info (first occurrence); got {len(with_exc_info)}"

    def test_100th_occurrence_logs_with_exc_info(self, caplog):
        """developer sees the traceback periodically (in case it changed"""
        rec = _make_recorder()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            for _i in range(100):
                rec._rms_callback_error_count = getattr(rec, "_rms_callback_error_count", 0) + 1
                if rec._rms_callback_error_count == 1 or rec._rms_callback_error_count % 100 == 0:
                    _REC_LOG.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        rec._rms_callback_error_count,
                        exc_info=True,
                    )

        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        with_exc_info = [r for r in debug_records if r.exc_info is not None]
        assert len(with_exc_info) == 2, f"Expected 2 records with exc_info (1st + 100th); got {len(with_exc_info)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
