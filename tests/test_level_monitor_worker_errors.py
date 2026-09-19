"""Tests for UE-24: per-burst level-worker error counter + throttled WARNING/ERROR."""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _reset_worker_error_state():
    """Reset UE-24 worker-error state to post-import defaults."""
    import voice_typer.server.level_monitor as lm
    from voice_typer.server.level_monitor import worker

    worker._reset_worker_error_state_for_tests()
    # Stop any worker thread from a previous test.
    lm._stop_level_worker()
    # Clear the stop event so a subsequent _level_worker_loop() call
    lm._level_worker_stop_event.clear()
    lm._level_worker_wake_event.clear()


@pytest.fixture(autouse=True)
def _reset_worker_errors():
    _reset_worker_error_state()
    yield
    _reset_worker_error_state()


def _run_one_worker_iteration():
    """Drive ``_level_worker_loop`` through exactly one iteration."""
    import voice_typer.server.level_monitor as lm

    lm._level_worker_stop_event.set()
    lm._level_worker_wake_event.set()
    lm._level_worker_loop()
    # Clear the stop event so subsequent iterations of the fixture
    lm._level_worker_stop_event.clear()


class TestWorkerErrorThrottledWarning:
    """UE-24: ``_level_worker_errors`` is logged with 5s throttling inside"""

    def test_worker_logs_errors_with_throttling(self, caplog):
        """UE-24: the worker logs ``_level_worker_errors`` every 5s and resets."""
        from voice_typer.server.level_monitor import worker

        # Set the error counter to a non-zero value.
        worker._level_worker_errors = 7
        # Set the window-start so the rate calculation is well-defined
        worker._level_worker_error_window_start = time.monotonic() - 10.0
        # Set the last-log timestamp to 10s ago (past the 5s throttle).
        worker._last_worker_error_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING and "level-worker chunk errors" in r.message
        ]
        assert len(error_warnings) >= 1, (
            f"UE-24: worker should log level-worker chunk errors at WARNING; "
            f"got records: {[r.message for r in caplog.records]}"
        )
        # The log message should contain the error count (7).
        assert "7" in error_warnings[0].message, (
            f"UE-24: log should mention '7' level-worker chunk errors; got: {error_warnings[0].message}"
        )
        # WARNING (not ERROR) because rate is below threshold.
        assert error_warnings[0].levelno == logging.WARNING, (
            f"UE-24: low-rate errors should log at WARNING (not ERROR); got level={error_warnings[0].levelname}"
        )
        assert worker._level_worker_errors == 0, (
            f"UE-24: _level_worker_errors should be reset to 0 after logging; got {worker._level_worker_errors}"
        )
        assert worker._level_worker_error_window_start == 0.0, (
            f"UE-24: _level_worker_error_window_start should be reset to 0.0 "
            f"after logging; got {worker._level_worker_error_window_start}"
        )

    def test_worker_throttles_error_logging(self, caplog):
        """UE-24: the worker does NOT log if <5s have passed since the last log."""
        from voice_typer.server.level_monitor import worker

        # Set the error counter, but the last log was <5s ago.
        worker._level_worker_errors = 5
        worker._level_worker_error_window_start = time.monotonic() - 1.0
        worker._last_worker_error_log_time = time.monotonic() - 1.0  # within throttle

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING and "level-worker chunk errors" in r.message
        ]
        assert len(error_warnings) == 0, (
            f"UE-24: worker should NOT log within 5s throttle window; got: {[r.message for r in error_warnings]}"
        )
        # Counter should NOT be reset (no log emitted).
        assert worker._level_worker_errors == 5, (
            f"UE-24: _level_worker_errors should NOT be reset within throttle window; got {worker._level_worker_errors}"
        )

    def test_worker_does_not_log_when_no_errors(self, caplog):
        """UE-24: the worker does NOT log if ``_level_worker_errors`` is 0."""
        from voice_typer.server.level_monitor import worker

        worker._level_worker_errors = 0
        worker._last_worker_error_log_time = 0.0  # far in the past

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_warnings = [
            r for r in caplog.records if r.levelno >= logging.WARNING and "level-worker chunk errors" in r.message
        ]
        assert len(error_warnings) == 0, (
            f"UE-24: worker should NOT log when _level_worker_errors=0; got: {[r.message for r in error_warnings]}"
        )

    def test_worker_error_counter_incremented_on_process_chunk_exception(self, monkeypatch, caplog):
        """UE-24: the drain loop's ``except Exception`` branch increments"""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        # Patch _process_level_chunk (in the worker module, which is
        def _raise(_indata, _status):
            raise RuntimeError("simulated RNNoise model corruption")

        monkeypatch.setattr(worker, "_process_level_chunk", _raise)

        # Push 3 chunks to the ring buffer.
        for _ in range(3):
            lm._level_ring_buffer.append((np.ones((512, 1), dtype=np.float32), None))

        # Set the throttle anchor far in the past so the WARNING fires
        worker._last_worker_error_log_time = time.monotonic() - 10.0
        worker._level_worker_error_window_start = time.monotonic() - 10.0

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        assert worker._level_worker_errors == 0, (
            f"UE-24: _level_worker_errors should be 0 after the throttled "
            f"WARNING reset it; got {worker._level_worker_errors}"
        )

        # 3 per-chunk DEBUG logs should have been emitted (one
        debug_chunks = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "level worker thread error processing chunk" in r.message
        ]
        assert len(debug_chunks) == 3, (
            f"UE-24: expected 3 per-chunk DEBUG logs (one per raised chunk); "
            f"got {len(debug_chunks)}: {[r.message for r in debug_chunks]}"
        )

        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "level-worker chunk errors" in r.message
        ]
        assert len(warnings) == 1, (
            f"UE-24: expected 1 throttled WARNING; got {len(warnings)}: {[r.message for r in warnings]}"
        )
        assert "3" in warnings[0].message, f"UE-24: WARNING should mention '3' errors; got: {warnings[0].message}"


class TestWorkerErrorEscalationToError:
    """UE-24: when the per-second error rate exceeds"""

    def test_high_rate_escalates_to_error(self, caplog):
        """UE-24: 100 errors in 1s (100/sec >> 10/sec threshold) → ERROR."""
        from voice_typer.server.level_monitor import worker

        # 100 errors accumulated in a 1-second window, 100/sec, well
        worker._level_worker_errors = 100
        worker._level_worker_error_window_start = time.monotonic() - 1.0
        worker._last_worker_error_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_logs = [
            r for r in caplog.records if r.levelno == logging.ERROR and "level-worker chunk errors" in r.message
        ]
        assert len(error_logs) == 1, (
            f"UE-24: high error rate should escalate to ERROR; got "
            f"{len(error_logs)} ERROR logs. All records: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )
        assert "100" in error_logs[0].message, (
            f"UE-24: ERROR log should mention '100' errors; got: {error_logs[0].message}"
        )
        # Counter should be reset after logging.
        assert worker._level_worker_errors == 0

    def test_threshold_boundary_warning_below(self, caplog):
        """UE-24: rate just BELOW the threshold (9/sec) → WARNING (not ERROR)."""
        from voice_typer.server.level_monitor import worker

        # 9 errors in 1 second, 9/sec, just below the 10/sec threshold.
        worker._level_worker_errors = 9
        worker._level_worker_error_window_start = time.monotonic() - 1.0
        worker._last_worker_error_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
        warning_logs = [
            r for r in caplog.records if r.levelno == logging.WARNING and "level-worker chunk errors" in r.message
        ]
        assert len(error_logs) == 0, (
            f"UE-24: rate below threshold should NOT escalate to ERROR; "
            f"got ERROR logs: {[r.message for r in error_logs]}"
        )
        assert len(warning_logs) == 1, (
            f"UE-24: rate below threshold should still log at WARNING; got: {[r.message for r in warning_logs]}"
        )

    def test_threshold_boundary_error_above(self, caplog):
        """UE-24: rate just ABOVE the threshold (11/sec) → ERROR."""
        from voice_typer.server.level_monitor import worker

        # 11 errors in 1 second, 11/sec, just above the 10/sec threshold.
        worker._level_worker_errors = 11
        worker._level_worker_error_window_start = time.monotonic() - 1.0
        worker._last_worker_error_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_logs = [
            r for r in caplog.records if r.levelno == logging.ERROR and "level-worker chunk errors" in r.message
        ]
        assert len(error_logs) == 1, (
            f"UE-24: rate above threshold should escalate to ERROR; got "
            f"{len(error_logs)} ERROR logs. All records: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )

    def test_escalation_threshold_is_configurable(self, caplog, monkeypatch):
        """UE-24: lowering ``_LEVEL_WORKER_ERROR_RATE_THRESHOLD`` causes"""
        from voice_typer.server.level_monitor import worker

        # 5 errors in 1s, 5/sec, normally below the 10/sec threshold.
        worker._level_worker_errors = 5
        worker._level_worker_error_window_start = time.monotonic() - 1.0
        worker._last_worker_error_log_time = time.monotonic() - 10.0
        # Lower the threshold to 3/sec, 5/sec now exceeds it.
        monkeypatch.setattr(worker, "_LEVEL_WORKER_ERROR_RATE_THRESHOLD", 3.0)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        error_logs = [
            r for r in caplog.records if r.levelno == logging.ERROR and "level-worker chunk errors" in r.message
        ]
        assert len(error_logs) == 1, (
            f"UE-24: lowering threshold should escalate 5/sec to ERROR; "
            f"got: {[(r.levelname, r.message) for r in caplog.records]}"
        )


class TestWorkerErrorStateReset:
    """UE-24: the autouse fixture resets ``_level_worker_errors`` /"""

    def test_reset_helper_zeros_all_three_state_fields(self):
        """``_reset_worker_error_state_for_tests`` zeros every counter."""
        from voice_typer.server.level_monitor import worker

        # Pollute the state.
        worker._level_worker_errors = 42
        worker._last_worker_error_log_time = 12345.678
        worker._level_worker_error_window_start = 99999.0

        worker._reset_worker_error_state_for_tests()

        assert worker._level_worker_errors == 0
        assert worker._last_worker_error_log_time == 0.0
        assert worker._level_worker_error_window_start == 0.0

    def test_state_is_zero_at_test_start(self):
        """The autouse fixture has already reset state before the test"""
        from voice_typer.server.level_monitor import worker

        assert worker._level_worker_errors == 0
        assert worker._last_worker_error_log_time == 0.0
        assert worker._level_worker_error_window_start == 0.0

    def test_state_does_not_leak_between_tests_a(self):
        """Test A: pollutes the state. ``test_state_does_not_leak_between_tests_b``"""
        from voice_typer.server.level_monitor import worker

        worker._level_worker_errors = 100
        worker._last_worker_error_log_time = 555.0
        worker._level_worker_error_window_start = 777.0

    def test_state_does_not_leak_between_tests_b(self):
        """Test B: asserts the pollution from test A was reset by the"""
        from voice_typer.server.level_monitor import worker

        assert worker._level_worker_errors == 0, (
            f"UE-24: state leaked from test A; _level_worker_errors={worker._level_worker_errors}"
        )
        assert worker._last_worker_error_log_time == 0.0, (
            f"UE-24: state leaked from test A; _last_worker_error_log_time={worker._last_worker_error_log_time}"
        )
        assert worker._level_worker_error_window_start == 0.0, (
            f"UE-24: state leaked from test A; "
            f"_level_worker_error_window_start={worker._level_worker_error_window_start}"
        )


class TestRetainsPerChunkDebugLog:
    """UE-24: the existing per-chunk DEBUG log is retained so a full"""

    def test_per_chunk_debug_log_emitted_with_exc_info(self, monkeypatch, caplog):
        """The per-chunk DEBUG log carries ``exc_info=True`` so the"""
        from voice_typer.server.level_monitor import worker

        def _raise(_indata, _status):
            raise RuntimeError("diagnostic traceback test")

        monkeypatch.setattr(worker, "_process_level_chunk", _raise)

        import voice_typer.server.level_monitor as lm

        lm._level_ring_buffer.append((np.ones((512, 1), dtype=np.float32), None))
        worker._last_worker_error_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        debug_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "level worker thread error processing chunk" in r.message
        ]
        assert len(debug_logs) == 1
        # ``exc_info=True`` means the record's ``exc_info`` is
        assert debug_logs[0].exc_info is not None, (
            "UE-24: per-chunk DEBUG log must carry exc_info=True so the "
            "traceback is retained for diagnosis; got exc_info=None. "
            "(If you removed ``exc_info=True`` from the log.debug call, "
            "operators can no longer diagnose WHICH filter stage raised.)"
        )
        # The exception type should be the RuntimeError we raised.
        assert debug_logs[0].exc_info[0] is RuntimeError


class TestDoesNotBreakDroppedChunksPath:
    """UE-24 added a SECOND throttled-log block right after the existing"""

    def test_dropped_chunks_still_logged(self, caplog):
        """``_dropped_level_chunks`` is still logged when >0 and throttle"""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        lm._dropped_level_chunks = 7
        lm._last_drop_log_time = time.monotonic() - 10.0
        worker._level_worker_errors = 0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        drop_warnings = [r for r in caplog.records if "dropped" in r.message.lower()]
        assert len(drop_warnings) >= 1, (
            f"UE-24: dropped-chunks WARNING should still fire; got records: {[r.message for r in caplog.records]}"
        )
        assert "7" in drop_warnings[0].message
        assert worker._level_worker_errors == 0


# Suppress unused-import warnings for ``MagicMock`` / ``np``, they're
_ = (MagicMock, np)
