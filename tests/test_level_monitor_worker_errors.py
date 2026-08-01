"""Tests for UE-24: per-burst level-worker error counter + throttled WARNING/ERROR.

``level_monitor/worker._level_worker_loop`` previously caught every
``Exception`` from ``_process_level_chunk`` at DEBUG. A sustained
failure mode (corrupted RNNoise model, numpy mismatch, filter
misconfiguration) was therefore silent at default log levels — the
level bar would freeze with no operator-visible breadcrumb and no
increment to ``_dropped_level_chunks`` (chunks are popped before the
error).

UE-24 mirrors the ``_dropped_level_chunks`` 5-second throttle pattern:
``worker._level_worker_errors`` accumulates per-chunk failures, is
logged + reset every 5s (if >0), and escalates from WARNING to ERROR
when the per-second rate exceeds
``_LEVEL_WORKER_ERROR_RATE_THRESHOLD`` (default 10/sec).

These tests mirror the structure of ``TestDroppedChunksLogging`` in
``tests/test_level_monitor.py``.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Test fixtures
# ═══════════════════════════════════════════════════════════════════════════


def _reset_worker_error_state():
    """Reset UE-24 worker-error state to post-import defaults.

    Also stops any level-worker thread from a previous test so the
    loop's ``global`` writes (which run on the worker thread when the
    loop is driven by an actual ``start_monitoring`` call) don't race
    with the test's direct manipulation of the globals.
    """
    import voice_typer.server.level_monitor as lm
    from voice_typer.server.level_monitor import worker

    worker._reset_worker_error_state_for_tests()
    # Stop any worker thread from a previous test.
    lm._stop_level_worker()
    # Clear the stop event so a subsequent _level_worker_loop() call
    # doesn't exit immediately before running one iteration.
    lm._level_worker_stop_event.clear()
    lm._level_worker_wake_event.clear()


@pytest.fixture(autouse=True)
def _reset_worker_errors():
    _reset_worker_error_state()
    yield
    _reset_worker_error_state()


def _run_one_worker_iteration():
    """Drive ``_level_worker_loop`` through exactly one iteration.

    Sets the stop + wake events BEFORE calling the loop so it:
      1. skips the ``wait()`` (wake is set)
      2. drains any queued chunks (usually none in these tests)
      3. runs the throttled drop-check + error-check blocks
      4. sees ``_level_worker_stop_event`` set and returns

    Mirrors the pattern in
    ``TestDroppedChunksLogging::test_worker_logs_dropped_chunks_with_throttling``.
    """
    import voice_typer.server.level_monitor as lm

    lm._level_worker_stop_event.set()
    lm._level_worker_wake_event.set()
    lm._level_worker_loop()
    # Clear the stop event so subsequent iterations of the fixture
    # don't exit immediately.
    lm._level_worker_stop_event.clear()


# ═══════════════════════════════════════════════════════════════════════════
# throttled WARNING logging of per-chunk processing errors
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkerErrorThrottledWarning:
    """UE-24: ``_level_worker_errors`` is logged with 5s throttling inside
    ``_level_worker_loop`` and reset to 0 after logging.

    The counter is incremented in the drain loop's ``except Exception``
    branch when ``_process_level_chunk`` raises; the worker thread logs
    it every 5s (if >0) to avoid log spam under sustained failure.
    """

    def test_worker_logs_errors_with_throttling(self, caplog):
        """UE-24: the worker logs ``_level_worker_errors`` every 5s and resets."""
        from voice_typer.server.level_monitor import worker

        # Set the error counter to a non-zero value.
        worker._level_worker_errors = 7
        # Set the window-start so the rate calculation is well-defined
        # (10s ago — rate = 7/10 = 0.7/sec, well under the 10/sec
        # escalation threshold, so we expect WARNING not ERROR).
        worker._level_worker_error_window_start = time.monotonic() - 10.0
        # Set the last-log timestamp to 10s ago (past the 5s throttle).
        worker._last_worker_error_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        # at least one WARNING-level record mentioning
        # "level-worker chunk errors" should have been emitted.
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
        # counter should be reset to 0 after logging.
        assert worker._level_worker_errors == 0, (
            f"UE-24: _level_worker_errors should be reset to 0 after logging; got {worker._level_worker_errors}"
        )
        # window-start should also be reset to 0.0.
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

        # no log should be emitted (within the 5s throttle window).
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
        """UE-24: the drain loop's ``except Exception`` branch increments
        ``_level_worker_errors`` when ``_process_level_chunk`` raises.

        This is the integration test: it verifies the actual code path
        (not just the throttled-log block) by patching
        ``_process_level_chunk`` to raise, pushing a chunk to the ring
        buffer, and running one loop iteration.

        The DEBUG per-chunk log is also verified here so the per-chunk
        traceback breadcrumb is preserved (UE-24 explicitly retains it
        for diagnosis).

        Note: we pre-set ``_level_worker_error_window_start`` to 10s
        ago so the rate (3 errors / 10s = 0.3/sec) stays well below the
        10/sec escalation threshold — otherwise the three errors would
        all land in the same millisecond (rate ~3000/sec) and escalate
        to ERROR. The ``if == 0.0:`` guard in the drain loop respects
        the pre-set value, so the rate computes against the 10s window.
        """
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        # Patch _process_level_chunk (in the worker module, which is
        # what the loop calls) to raise on every chunk.
        def _raise(_indata, _status):
            raise RuntimeError("simulated RNNoise model corruption")

        monkeypatch.setattr(worker, "_process_level_chunk", _raise)

        # Push 3 chunks to the ring buffer.
        for _ in range(3):
            lm._level_ring_buffer.append((np.ones((512, 1), dtype=np.float32), None))

        # Set the throttle anchor far in the past so the WARNING fires
        # on this iteration.
        worker._last_worker_error_log_time = time.monotonic() - 10.0
        # Pre-set window_start to 10s ago — the ``if == 0.0:`` guard in
        # the drain loop respects this so the rate computes as
        # 3 errors / 10s = 0.3/sec (WARNING, not ERROR).
        worker._level_worker_error_window_start = time.monotonic() - 10.0

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        # 3 errors should have been counted (one per chunk) and
        # then reset to 0 by the throttled-log block.
        assert worker._level_worker_errors == 0, (
            # Counter is reset AFTER logging; we expect the WARNING to
            # have fired (throttle was 10s ago) and reset the counter.
            f"UE-24: _level_worker_errors should be 0 after the throttled "
            f"WARNING reset it; got {worker._level_worker_errors}"
        )

        # 3 per-chunk DEBUG logs should have been emitted (one
        # per chunk) — the per-chunk traceback breadcrumb is retained.
        debug_chunks = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "level worker thread error processing chunk" in r.message
        ]
        assert len(debug_chunks) == 3, (
            f"UE-24: expected 3 per-chunk DEBUG logs (one per raised chunk); "
            f"got {len(debug_chunks)}: {[r.message for r in debug_chunks]}"
        )

        # a WARNING summarising the burst should also have fired.
        # Rate = 3 / 10s = 0.3/sec, well below the 10/sec threshold.
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "level-worker chunk errors" in r.message
        ]
        assert len(warnings) == 1, (
            f"UE-24: expected 1 throttled WARNING; got {len(warnings)}: {[r.message for r in warnings]}"
        )
        assert "3" in warnings[0].message, f"UE-24: WARNING should mention '3' errors; got: {warnings[0].message}"


# ═══════════════════════════════════════════════════════════════════════════
# ERROR escalation when rate exceeds threshold
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkerErrorEscalationToError:
    """UE-24: when the per-second error rate exceeds
    ``_LEVEL_WORKER_ERROR_RATE_THRESHOLD`` (default 10/sec), the throttled
    log escalates from WARNING to ERROR so a frozen level bar surfaces
    at default log levels.

    At the ~16 Hz block rate, 10/sec means >60% of chunks are failing —
    a clear signal that the filter chain is broken (corrupted RNNoise
    model, numpy mismatch, etc.).
    """

    def test_high_rate_escalates_to_error(self, caplog):
        """UE-24: 100 errors in 1s (100/sec >> 10/sec threshold) → ERROR."""
        from voice_typer.server.level_monitor import worker

        # 100 errors accumulated in a 1-second window — 100/sec, well
        # above the 10/sec threshold.
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
        # The ERROR log should mention both the count (100) and the
        # threshold (10.0).
        assert "100" in error_logs[0].message, (
            f"UE-24: ERROR log should mention '100' errors; got: {error_logs[0].message}"
        )
        # Counter should be reset after logging.
        assert worker._level_worker_errors == 0

    def test_threshold_boundary_warning_below(self, caplog):
        """UE-24: rate just BELOW the threshold (9/sec) → WARNING (not ERROR)."""
        from voice_typer.server.level_monitor import worker

        # 9 errors in 1 second — 9/sec, just below the 10/sec threshold.
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

        # 11 errors in 1 second — 11/sec, just above the 10/sec threshold.
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
        """UE-24: lowering ``_LEVEL_WORKER_ERROR_RATE_THRESHOLD`` causes
        a previously-WARNING rate to escalate to ERROR.

        Verifies the threshold is read at log time (not cached at module
        import) so operators can tune it via monkeypatch / env-var
        wiring without restarting the worker.
        """
        from voice_typer.server.level_monitor import worker

        # 5 errors in 1s — 5/sec, normally below the 10/sec threshold.
        worker._level_worker_errors = 5
        worker._level_worker_error_window_start = time.monotonic() - 1.0
        worker._last_worker_error_log_time = time.monotonic() - 10.0
        # Lower the threshold to 3/sec — 5/sec now exceeds it.
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


# ═══════════════════════════════════════════════════════════════════════════
# counter state isolation between tests (no leakage)
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkerErrorStateReset:
    """UE-24: the autouse fixture resets ``_level_worker_errors`` /
    ``_last_worker_error_log_time`` / ``_level_worker_error_window_start``
    between tests so a sustained-error test doesn't leak its counter
    into a later test.

    The reset helper (``worker._reset_worker_error_state_for_tests``)
    is itself tested here so a future refactor that drops it fails
    loudly (the autouse fixture depends on it).
    """

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
        """The autouse fixture has already reset state before the test
        body runs — so a fresh test sees zero counters."""
        from voice_typer.server.level_monitor import worker

        assert worker._level_worker_errors == 0
        assert worker._last_worker_error_log_time == 0.0
        assert worker._level_worker_error_window_start == 0.0

    def test_state_does_not_leak_between_tests_a(self):
        """Test A: pollutes the state. ``test_state_does_not_leak_between_tests_b``
        then asserts the state is back to zero (the autouse fixture
        reset it between the two tests)."""
        from voice_typer.server.level_monitor import worker

        worker._level_worker_errors = 100
        worker._last_worker_error_log_time = 555.0
        worker._level_worker_error_window_start = 777.0

    def test_state_does_not_leak_between_tests_b(self):
        """Test B: asserts the pollution from test A was reset by the
        autouse fixture."""
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


# ═══════════════════════════════════════════════════════════════════════════
# Sanity: per-chunk DEBUG log retained (diagnostic breadcrumb)
# ═══════════════════════════════════════════════════════════════════════════


class TestRetainsPerChunkDebugLog:
    """UE-24: the existing per-chunk DEBUG log is retained so a full
    traceback is still available at DEBUG level for diagnosis. The
    throttled WARNING/ERROR is a SEPARATE log line (count summary),
    NOT a replacement for the per-chunk traceback.

    This pins the design decision: a future "let's drop the DEBUG log
    to reduce log volume" refactor would silently lose the per-chunk
    traceback (which is the only way to diagnose WHICH filter stage is
    raising).
    """

    def test_per_chunk_debug_log_emitted_with_exc_info(self, monkeypatch, caplog):
        """The per-chunk DEBUG log carries ``exc_info=True`` so the
        traceback is logged for post-mortem diagnosis."""
        from voice_typer.server.level_monitor import worker

        def _raise(_indata, _status):
            raise RuntimeError("diagnostic traceback test")

        monkeypatch.setattr(worker, "_process_level_chunk", _raise)

        import voice_typer.server.level_monitor as lm

        lm._level_ring_buffer.append((np.ones((512, 1), dtype=np.float32), None))
        # Set the throttle anchor to 0.0 so the WARNING fires and
        # resets the counter — we don't want the WARNING to dominate
        # the assertion below.
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
        # populated (a 3-tuple of (type, value, traceback)).
        assert debug_logs[0].exc_info is not None, (
            "UE-24: per-chunk DEBUG log must carry exc_info=True so the "
            "traceback is retained for diagnosis; got exc_info=None. "
            "(If you removed ``exc_info=True`` from the log.debug call, "
            "operators can no longer diagnose WHICH filter stage raised.)"
        )
        # The exception type should be the RuntimeError we raised.
        assert debug_logs[0].exc_info[0] is RuntimeError


# ═══════════════════════════════════════════════════════════════════════════
# Sanity:  doesn't break the existing _dropped_level_chunks path
# ═══════════════════════════════════════════════════════════════════════════


class TestDoesNotBreakDroppedChunksPath:
    """UE-24 added a SECOND throttled-log block right after the existing
    ``_dropped_level_chunks`` block. This test verifies the dropped-chunks
    path still works (the new block doesn't shadow it or short-circuit
    the loop).

    Mirrors ``TestDroppedChunksLogging::test_worker_logs_dropped_chunks_with_throttling``
    but lives here so a UE-24 regression that breaks the dropped-chunks
    path (e.g. an early ``return`` in the new block) fails this test
    file directly.
    """

    def test_dropped_chunks_still_logged(self, caplog):
        """``_dropped_level_chunks`` is still logged when >0 and throttle
        has elapsed — UE-24 didn't accidentally swallow it."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        lm._dropped_level_chunks = 7
        lm._last_drop_log_time = time.monotonic() - 10.0
        # state: zero errors so the new block is a no-op.
        worker._level_worker_errors = 0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            _run_one_worker_iteration()

        drop_warnings = [r for r in caplog.records if "dropped" in r.message.lower()]
        assert len(drop_warnings) >= 1, (
            f"UE-24: dropped-chunks WARNING should still fire; got records: {[r.message for r in caplog.records]}"
        )
        assert "7" in drop_warnings[0].message
        # counter should be unchanged (no errors occurred).
        assert worker._level_worker_errors == 0


# Suppress unused-import warnings for ``MagicMock`` / ``np`` — they're
# used by future test additions and provide a convenient reference for
# the test author (mirrors the import block in test_level_monitor.py).
_ = (MagicMock, np)
