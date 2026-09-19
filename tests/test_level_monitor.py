"""Tests for ``voice_typer.server.level_monitor`` (XV-54, XV-55, XV-58)."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.fixtures.wait_helpers import wait_for_event, wait_until


def _reset_level_monitor_state():
    """Reset all module-level state in level_monitor between tests."""
    import voice_typer.server.level_monitor as lm
    from voice_typer.server.level_monitor import worker as _lm_worker

    lm._test_mode = False
    lm._test_chunks.clear()
    lm._test_raw_chunks.clear()
    lm._test_filtered_chunks.clear()
    lm._test_start_time = 0.0
    lm._test_duration = 10.0
    lm._monitor_sample_rate = 16000
    lm._monitor_active = False
    lm._monitor_stream = None
    lm._monitor_level = 0.0
    lm._monitor_peak = 0.0
    lm._monitor_mic_id = None
    lm._level_processor = None
    # Reset the processor-config stash + cosmetic-bar flag too, without
    lm._level_processor_config = None
    lm._level_bar_filtered = False
    lm._dropped_level_chunks = 0
    lm._last_drop_log_time = 0.0
    lm._level_ring_buffer.clear()
    # Stop any worker thread from a previous test.
    lm._stop_level_worker()
    # Disarm a leaked auto-stop timer: a test that started a recording
    leaked_timer = lm._test_auto_stop_timer
    if leaked_timer is not None:
        leaked_timer.cancel()
    lm._test_auto_stop_timer = None
    from voice_typer.server.level_monitor import test_recording as _tr

    for _t in list(_tr._test_recording_expiry_timers):
        with contextlib.suppress(Exception):
            _t.cancel()
    _tr._test_recording_expiry_timers.clear()
    # Reset quality metrics.
    lm._test_peak_history.clear()
    lm._test_rms_history.clear()
    lm._test_clip_count = 0
    lm._test_silence_blocks = 0
    _lm_worker._reset_worker_error_state_for_tests()


@pytest.fixture(autouse=True)
def _reset_level_monitor():
    _reset_level_monitor_state()
    yield
    _reset_level_monitor_state()


@pytest.fixture(autouse=True)
def _isolate_test_recordings_dir(tmp_path, monkeypatch):
    """Point the mic-test WAV transport at a per-test directory."""
    from voice_typer.server.level_monitor import test_recording as _tr

    recordings = tmp_path / "mic-test-recordings"

    def _isolated_dir():
        recordings.mkdir(parents=True, exist_ok=True)
        return recordings

    monkeypatch.setattr(_tr, "_test_recordings_dir", _isolated_dir)
    yield


def _wire_stream_with_callback_capture(monkeypatch):
    """Wire a mock ``sd.InputStream`` that captures the callback for direct invocation."""
    import sounddevice as sd

    holder = {"callback": None}

    class _Stream:
        def __init__(self, *args, **kwargs):
            holder["callback"] = kwargs.get("callback")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.InputStream = _Stream  # type: ignore[assignment]
    sd.query_devices.return_value = {
        "name": "Mock Mic",
        "default_samplerate": 16000,
        "max_input_channels": 1,
        "hostapi": 0,
    }
    return holder


class TestRecordingTransportIsolation:
    """The mic-test WAV transport must be scoped to the per-test tmp dir."""

    def test_recordings_dir_scoped_to_test_tmp(self, tmp_path):
        from voice_typer.server.level_monitor import test_recording as _tr

        d = _tr._test_recordings_dir()
        assert d.is_relative_to(tmp_path)
        assert d.exists()


class TestOnlyRawChunksPopulated:
    """XV-54: the worker thread populates ONLY ``_test_raw_chunks``."""

    def test_worker_does_not_append_to_test_chunks(self, monkeypatch):
        """Processing a chunk via the worker should NOT append to _test_chunks."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)

        # Invoke the PortAudio callback directly (simulates audio arriving).
        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        holder["callback"](chunk, 512, None, None)

        # Wait for the worker thread to process the chunk.
        assert wait_until(lambda: len(lm._test_raw_chunks) > 0, timeout=1.0)

        assert len(lm._test_raw_chunks) == 1, (
            f"worker should append to _test_raw_chunks; got len={len(lm._test_raw_chunks)}"
        )
        # _test_chunks should NOT be appended to by the worker.
        assert len(lm._test_chunks) == 0, (
            f"XV-54: worker should NOT append to _test_chunks (only _test_raw_chunks); got len={len(lm._test_chunks)}"
        )

        lm.cancel_test_recording()
        lm.stop_monitoring()

    def test_stop_derives_audio_from_raw_audio_copy(self, monkeypatch):
        """stop_test_recording builds ``audio`` from ``raw_audio.copy()``."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)

        # Push 3 chunks via the callback.
        for _ in range(3):
            chunk = np.ones((512, 1), dtype=np.float32) * 0.5
            holder["callback"](chunk, 512, None, None)

        # Wait for the worker to process.
        assert wait_until(lambda: len(lm._test_raw_chunks) >= 3, timeout=1.0)
        assert len(lm._test_raw_chunks) >= 3

        result = lm.stop_test_recording()
        assert result["success"] is True
        assert result["duration_ms"] > 0

        # File-reference transport: the stop response carries small
        import io as _io
        import wave as _wave
        from pathlib import Path as _Path

        for key in ("audio_file", "raw_audio_file"):
            ref = result[key]
            assert isinstance(ref, dict) and ref["path"] and ref["bytes"] > 0
            raw_bytes = _Path(ref["path"]).read_bytes()
            assert len(raw_bytes) == ref["bytes"]
            with _wave.open(_io.BytesIO(raw_bytes), "rb") as wf:
                assert wf.getnframes() > 0

    def test_stop_returns_no_audio_when_only_test_chunks_populated(self, monkeypatch):
        """XV-54: if only ``_test_chunks`` is populated (NOT _test_raw_chunks),"""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)

        # Simulate a legacy test that appends to _test_chunks only (not
        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        lm._test_chunks.append(chunk)
        # _test_raw_chunks is empty.

        result = lm.stop_test_recording()
        assert result["success"] is True
        assert result["audio_file"] is None
        assert result["raw_audio_file"] is None
        assert result["message"] == "No audio captured"

    def test_no_duplicate_storage_in_production(self, monkeypatch):
        """XV-54 memory savings: in production (via the worker), only one"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)

        # Push 5 chunks via the callback.
        for _ in range(5):
            chunk = np.ones((512, 1), dtype=np.float32) * 0.3
            holder["callback"](chunk, 512, None, None)

        # Wait for the worker.
        assert wait_until(lambda: len(lm._test_raw_chunks) >= 5, timeout=1.0)

        assert len(lm._test_raw_chunks) == 5
        assert len(lm._test_chunks) == 0  # NOT populated by the worker
        # Total chunks stored: 5 (was 10 before ).
        total = len(lm._test_raw_chunks) + len(lm._test_chunks)
        assert total == 5, f"XV-54: total chunks stored should be 5 (one copy each); got {total}"

        lm.cancel_test_recording()
        lm.stop_monitoring()


class TestHeavyWorkOutsideLock:
    """XV-55: the filter chain + RMS/peak + quality metrics run OUTSIDE"""

    def test_get_level_not_blocked_by_slow_filter(self, monkeypatch):
        """XV-55: a 50ms slow filter on the worker thread must NOT block"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # Install a slow filter (50ms per chunk, simulates RNNoise).
        slow_filter_started = threading.Event()

        def slow_filter(chunk):
            slow_filter_started.set()
            time.sleep(0.05)
            return chunk

        slow_processor = MagicMock()
        slow_processor.process_chunk.side_effect = slow_filter
        lm._level_processor = slow_processor
        # Cosmetic-bar mode (the default) SKIPS the filter chain entirely,
        lm._level_processor_config = None
        lm._level_bar_filtered = True

        lm.start_monitoring(mic_id=None)
        try:
            # Push a chunk, the worker will spend 50ms processing it.
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            holder["callback"](chunk, 512, None, None)

            # Wait until the worker has entered the slow filter.
            assert wait_for_event(slow_filter_started, timeout=1.0), "worker never entered the slow filter"

            t0 = time.perf_counter()
            level = lm.get_level()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            assert isinstance(level, dict)
            assert "level" in level
            assert elapsed_ms < 10.0, (
                f"XV-55: get_level() took {elapsed_ms:.2f}ms with a 50ms slow "
                f"filter on the worker, heavy work must run OUTSIDE _monitor_lock."
            )
        finally:
            lm.stop_monitoring()

    def test_get_level_not_blocked_when_worker_falls_behind(self, monkeypatch):
        """XV-55 regression: ``get_level()`` must NOT block for N×50ms when"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # 50ms per chunk, simulates RNNoise on a slow CPU. The filter
        slow_filter_started = threading.Event()

        def slow_filter(chunk):
            slow_filter_started.set()
            time.sleep(0.05)
            return chunk

        slow_processor = MagicMock()
        slow_processor.process_chunk.side_effect = slow_filter
        lm._level_processor = slow_processor
        lm._level_processor_config = None
        lm._level_bar_filtered = True

        lm.start_monitoring(mic_id=None)
        try:
            # Queue 5 chunks via the callback. At 50ms/chunk the worker
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            for _ in range(5):
                holder["callback"](chunk, 512, None, None)

            # Wait until the worker has entered the (first) slow filter.
            assert wait_for_event(slow_filter_started, timeout=1.0), "worker never entered the slow filter"

            # Sample get_level() 3 times spread across the drain window.
            max_elapsed_ms = 0.0
            for _ in range(3):
                t0 = time.perf_counter()
                level = lm.get_level()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                max_elapsed_ms = max(max_elapsed_ms, elapsed_ms)
                assert isinstance(level, dict)
                assert "level" in level
                time.sleep(0.05)  # advance to mid-drain of next chunk

            assert max_elapsed_ms < 10.0, (
                f"XV-55: get_level() took {max_elapsed_ms:.2f}ms (max of 3 samples) "
                f"while the worker was draining 5 queued 50ms chunks, the heavy "
                f"work must run OUTSIDE _monitor_lock so get_level() doesn't block "
                f"for N×50ms when the worker falls behind."
            )
        finally:
            lm.stop_monitoring()

    def test_stop_test_recording_not_blocked_by_slow_filter(self, monkeypatch):
        """XV-55: a slow filter on the worker thread must NOT block"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # Slow filter (50ms) that signals when the worker has entered it,
        slow_filter_started = threading.Event()

        def slow_filter(chunk):
            slow_filter_started.set()
            time.sleep(0.05)
            return chunk

        slow_processor = MagicMock()
        slow_processor.process_chunk.side_effect = slow_filter
        lm._level_processor = slow_processor

        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)
        try:
            # Push a chunk, the worker will spend 50ms processing it.
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            holder["callback"](chunk, 512, None, None)

            # Wait until the worker has entered the slow filter (so we
            assert wait_for_event(slow_filter_started, timeout=1.0), "worker never entered the slow filter"

            t0 = time.perf_counter()
            result = lm.stop_test_recording()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            assert result["success"] is True
            assert elapsed_ms < 100.0, (
                f"XV-55: stop_test_recording() took {elapsed_ms:.2f}ms with a "
                f"50ms slow filter on the worker, heavy work must run OUTSIDE "
                f"_monitor_lock."
            )
        finally:
            lm.stop_monitoring()

    def test_filter_chain_still_applied_to_level_bar(self, monkeypatch):
        """XV-55 doesn't break the filter-chain integration: the level bar"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # Install a processor that halves the amplitude, the level bar
        processor = MagicMock()
        processor.process_chunk.side_effect = lambda chunk: chunk * 0.5
        lm._level_processor = processor

        lm.start_monitoring(mic_id=None)
        try:
            # Push a 0.5-amplitude chunk.
            chunk = np.ones((512, 1), dtype=np.float32) * 0.5
            holder["callback"](chunk, 512, None, None)

            # Wait for the worker to process it.
            assert wait_until(lambda: lm._monitor_level > 0, timeout=1.0)

            # The level should reflect the halved amplitude (0.25 * smoothing).
            assert 0 < lm._monitor_level < 0.3, (
                f"level={lm._monitor_level} should reflect the FILTERED audio (~0.1-0.25), not the raw 0.5"
            )
        finally:
            lm.stop_monitoring()

    def test_quality_metrics_still_computed_from_raw(self, monkeypatch):
        """XV-55 doesn't break the quality-metric computation: metrics"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # Install a processor that zeros out the audio (silence after filtering).
        processor = MagicMock()
        processor.process_chunk.side_effect = lambda chunk: np.zeros_like(chunk)
        lm._level_processor = processor

        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)
        try:
            # Push a 0.5-amplitude chunk.
            chunk = np.ones((512, 1), dtype=np.float32) * 0.5
            holder["callback"](chunk, 512, None, None)

            # Wait for the worker to process it.
            assert wait_until(
                lambda: len(lm._test_raw_chunks) > 0 and len(lm._test_rms_history) > 0,
                timeout=1.0,
            )

            assert wait_until(
                lambda: any(r > 0.4 for r in lm._test_rms_history),
                timeout=1.0,
            )

            assert len(lm._test_rms_history) > 0
            raw_rms = max(lm._test_rms_history)
            assert raw_rms > 0.4, (
                f"raw_rms={raw_rms} should reflect the RAW 0.5 amplitude, "
                f"not the filtered 0.0 (XV-55: quality metrics must use raw audio)"
            )

            # Stop and verify the quality report.
            result = lm.stop_test_recording()
            assert result["success"] is True
            assert result["quality"]["volume_rms"] > 0.4
        finally:
            lm.stop_monitoring()

    def test_lock_acquired_only_for_writes(self, monkeypatch):
        """XV-55: ``_monitor_lock`` is acquired TWICE per chunk, once"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # Slow filter (50ms) so we can observe the worker's lock state.
        filter_started = threading.Event()
        filter_in_progress = threading.Event()
        lock_check_done = threading.Event()

        def slow_filter(chunk):
            filter_started.set()
            # Check if the lock is currently held by ANY thread (it shouldn't be).
            if lm._monitor_lock.acquire(blocking=False):
                lm._monitor_lock.release()
            else:
                filter_in_progress.set()  # lock is held by someone else
            lock_check_done.set()
            time.sleep(0.05)
            return chunk

        processor = MagicMock()
        processor.process_chunk.side_effect = slow_filter
        lm._level_processor = processor
        lm._level_processor_config = None
        lm._level_bar_filtered = True

        lm.start_monitoring(mic_id=None)
        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            holder["callback"](chunk, 512, None, None)

            # Wait for the filter to start.
            assert filter_started.wait(timeout=1.0), "filter didn't start"
            assert lock_check_done.wait(timeout=1.0), "filter never finished its lock-state check"
            assert not filter_in_progress.is_set(), (
                "XV-55: _monitor_lock was held during the slow filter call, heavy work must run OUTSIDE the lock"
            )

            # Wait for the worker to finish.
            wait_until(lambda: lm._monitor_level > 0, timeout=1.0)
        finally:
            lm.stop_monitoring()


class TestDroppedChunksLogging:
    """XV-58: ``_dropped_level_chunks`` is logged with 5s throttling inside"""

    def test_get_level_diagnostics_returns_dict(self):
        """XV-58: get_level_diagnostics() returns a dict with the expected keys."""
        import voice_typer.server.level_monitor as lm

        diag = lm.get_level_diagnostics()
        assert isinstance(diag, dict)
        assert "dropped_level_chunks" in diag
        assert "total_dropped_level_chunks" in diag
        assert "ring_buffer_capacity" in diag
        assert "ring_buffer_len" in diag
        assert "monitor_active" in diag
        assert isinstance(diag["dropped_level_chunks"], int)
        assert isinstance(diag["total_dropped_level_chunks"], int)
        assert isinstance(diag["ring_buffer_capacity"], int)
        assert isinstance(diag["ring_buffer_len"], int)
        assert isinstance(diag["monitor_active"], bool)

    def test_get_level_diagnostics_reflects_drops(self):
        """XV-58: get_level_diagnostics() reflects the current drop count."""
        import voice_typer.server.level_monitor as lm

        lm._dropped_level_chunks = 42
        diag = lm.get_level_diagnostics()
        assert diag["dropped_level_chunks"] == 42

    def test_get_level_diagnostics_reflects_ring_buffer_state(self, monkeypatch):
        """XV-58: get_level_diagnostics() reflects the ring buffer fill level."""
        import voice_typer.server.level_monitor as lm

        # Push 3 chunks to the ring buffer.
        for _ in range(3):
            lm._level_ring_buffer.append((np.zeros((512, 1), dtype=np.float32), None))

        diag = lm.get_level_diagnostics()
        assert diag["ring_buffer_len"] == 3
        assert diag["ring_buffer_capacity"] == lm._LEVEL_RING_BUFFER_CAPACITY

    def test_worker_logs_dropped_chunks_with_throttling(self, monkeypatch, caplog):
        """XV-58: the worker logs ``_dropped_level_chunks`` every 5s and resets."""
        import logging

        import voice_typer.server.level_monitor as lm

        # Set the drop counter to a non-zero value.
        lm._dropped_level_chunks = 7
        # Set the last-log timestamp to 10s ago (past the 5s throttle).
        lm._last_drop_log_time = time.monotonic() - 10.0

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            # Manually invoke the worker loop body (the drop-check portion).
            if lm._dropped_level_chunks > 0:
                now = time.monotonic()
                if (now - lm._last_drop_log_time) >= 5.0:
                    dropped = lm._dropped_level_chunks
                    lm._dropped_level_chunks = 0
                    lm._last_drop_log_time = now
                    lm._log_drop_warning(dropped) if hasattr(lm, "_log_drop_warning") else None

            # Hmm, the actual logging is inlined in _level_worker_loop.
            pass

        # The above approach is awkward, let me instead drive the worker
        lm._dropped_level_chunks = 7
        lm._last_drop_log_time = time.monotonic() - 10.0

        # Set the stop event BEFORE calling the loop, so the loop exits
        lm._level_worker_stop_event.set()
        lm._level_worker_wake_event.set()  # wake the worker so it doesn't wait

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            lm._level_worker_loop()

        # The worker should have logged the drop count.
        drop_warnings = [r for r in caplog.records if "dropped" in r.message.lower()]
        assert len(drop_warnings) >= 1, (
            f"XV-58: worker should log dropped chunks; got records: {[r.message for r in caplog.records]}"
        )
        # The log message should contain the drop count (7).
        assert "7" in drop_warnings[0].message, (
            f"XV-58: log should mention '7' dropped chunks; got: {drop_warnings[0].message}"
        )
        assert lm._dropped_level_chunks == 0, (
            f"XV-58: _dropped_level_chunks should be reset to 0 after logging; got {lm._dropped_level_chunks}"
        )

        # Clean up.
        lm._level_worker_stop_event.clear()

    def test_worker_throttles_dropped_chunks_logging(self, monkeypatch, caplog):
        """XV-58: the worker does NOT log if <5s have passed since the last log."""
        import logging

        import voice_typer.server.level_monitor as lm

        # Set the drop counter, but the last log was <5s ago.
        lm._dropped_level_chunks = 5
        lm._last_drop_log_time = time.monotonic() - 1.0  # 1s ago (within throttle)

        lm._level_worker_stop_event.set()
        lm._level_worker_wake_event.set()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            lm._level_worker_loop()

        drop_warnings = [r for r in caplog.records if "dropped" in r.message.lower()]
        assert len(drop_warnings) == 0, (
            f"XV-58: worker should NOT log within 5s throttle window; got: {[r.message for r in drop_warnings]}"
        )
        # Counter should NOT be reset (no log emitted).
        assert lm._dropped_level_chunks == 5, (
            f"XV-58: _dropped_level_chunks should NOT be reset within throttle window; got {lm._dropped_level_chunks}"
        )

        lm._level_worker_stop_event.clear()

    def test_worker_does_not_log_when_no_drops(self, monkeypatch, caplog):
        """XV-58: the worker does NOT log if ``_dropped_level_chunks`` is 0."""
        import logging

        import voice_typer.server.level_monitor as lm

        lm._dropped_level_chunks = 0
        lm._last_drop_log_time = 0.0  # far in the past

        lm._level_worker_stop_event.set()
        lm._level_worker_wake_event.set()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            lm._level_worker_loop()

        drop_warnings = [r for r in caplog.records if "dropped" in r.message.lower()]
        assert len(drop_warnings) == 0, (
            f"XV-58: worker should NOT log when _dropped_level_chunks=0; got: {[r.message for r in drop_warnings]}"
        )

        lm._level_worker_stop_event.clear()

    def test_dropped_chunks_counter_incremented_on_ring_buffer_overflow(self, monkeypatch, caplog):
        """XV-58 + the PortAudio callback increments"""
        import logging

        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker as _lm_worker

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)
        # Park the background worker: it pops chunks off the same ring
        _lm_worker._stop_level_worker()
        try:
            cap = lm._LEVEL_RING_BUFFER_CAPACITY
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            for _ in range(cap):
                holder["callback"](chunk, 512, None, None)

            # Baseline AFTER the fill: every callback below overflows
            initial_total = _lm_worker._total_dropped_level_chunks
            for _ in range(5):
                holder["callback"](chunk, 512, None, None)

            # Drive the drain directly: backdate past the 5s throttle
            lm._last_drop_log_time = time.monotonic() - 10.0
            lm._level_worker_stop_event.set()
            lm._level_worker_wake_event.set()
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
                lm._level_worker_loop()

            assert _lm_worker._total_dropped_level_chunks > initial_total, (
                f"_total_dropped_level_chunks should have increased "
                f"after the ring-buffer overflow (was {initial_total}, "
                f"now {_lm_worker._total_dropped_level_chunks}). The "
                "cumulative counter is NEVER reset in production, so it "
                "monotonically increases as drops happen."
            )
        finally:
            lm.stop_monitoring()

    def test_total_dropped_level_chunks_is_cumulative_across_drains(self, monkeypatch, caplog):
        """``_total_dropped_level_chunks`` is CUMULATIVE, it"""
        import logging

        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker as _lm_worker

        # First burst: set the per-burst delta and trigger the worker's
        lm._dropped_level_chunks = 7
        lm._last_drop_log_time = time.monotonic() - 10.0  # past the 5s throttle
        lm._level_worker_stop_event.set()
        lm._level_worker_wake_event.set()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            lm._level_worker_loop()

        assert lm._dropped_level_chunks == 0, "Per-burst delta should be drained to 0 after the worker logs."
        assert _lm_worker._total_dropped_level_chunks == 7, (
            "cumulative counter should hold the drained count (7) "
            "after the first drain cycle, it is NEVER reset in production."
        )

        lm._level_worker_stop_event.clear()

        # Second burst: a different drop count, again drained by the worker.
        lm._dropped_level_chunks = 5
        lm._last_drop_log_time = time.monotonic() - 10.0  # past the 5s throttle
        lm._level_worker_stop_event.set()
        lm._level_worker_wake_event.set()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
            lm._level_worker_loop()

        # The cumulative counter should now hold 7 + 5 = 12 (NOT 5).
        assert _lm_worker._total_dropped_level_chunks == 12, (
            "cumulative counter should ACCUMULATE across drain "
            "cycles (7 + 5 = 12), NOT reset to the latest burst count. "
            f"Got {_lm_worker._total_dropped_level_chunks}."
        )
        assert lm._dropped_level_chunks == 0, "Per-burst delta should be drained to 0 after the second log."

        lm._level_worker_stop_event.clear()

    def test_get_level_diagnostics_reflects_total_drops(self):
        """``get_level_diagnostics()`` exposes the cumulative"""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker as _lm_worker

        # Set the cumulative counter directly (simulating prior drops
        _lm_worker._total_dropped_level_chunks = 42
        lm._dropped_level_chunks = 0

        diag = lm.get_level_diagnostics()
        assert diag["total_dropped_level_chunks"] == 42, (
            "get_level_diagnostics()['total_dropped_level_chunks'] "
            "should reflect the cumulative counter (42), independent of "
            "the per-burst delta."
        )
        assert diag["dropped_level_chunks"] == 0, "Per-burst delta should remain 0 (its post-drain state)."


class TestCancelTestRecordingLock:
    """YJ-50 (review-fix-C2-rework): ``cancel_test_recording`` must"""

    def test_cancel_test_recording_acquires_monitor_lock(self, monkeypatch):
        """``cancel_test_recording`` must call ``_monitor_lock.__enter__``."""
        import voice_typer.server.level_monitor as lm

        # Reset any leftover timer from prior tests.
        lm._test_auto_stop_timer = None

        # Install a fake timer so the cancel block has something to do.
        fake_timer = MagicMock()
        lm._test_auto_stop_timer = fake_timer

        # Wrap _monitor_lock to count entries.
        enter_calls: list[float] = []
        real_lock = lm._monitor_lock

        class _CountingLock:
            """Proxy that records ``__enter__`` calls and delegates to"""

            def __enter__(self):
                enter_calls.append(time.perf_counter())
                return real_lock.__enter__()

            def __exit__(self, *exc):
                return real_lock.__exit__(*exc)

        counting_lock = _CountingLock()
        monkeypatch.setattr(lm, "_monitor_lock", counting_lock)

        # Sanity: a fresh cancel_test_recording with no test active
        result = lm.cancel_test_recording()

        assert len(enter_calls) >= 2, (
            f"YJ-50: cancel_test_recording must acquire _monitor_lock "
            f"for the timer-cancel block (expected >= 2 entries: one "
            f"for the timer-cancel wrap + one for _cancel_test_locked); "
            f"got {len(enter_calls)} entries. Did the with-lock wrap "
            f"get removed from cancel_test_recording?"
        )

        # The timer must have been cancelled AND the global cleared.
        assert fake_timer.cancel.called, (
            "cancel_test_recording did not call timer.cancel(), the auto-stop timer would leak (YJ-50 regression)."
        )
        assert lm._test_auto_stop_timer is None, (
            f"cancel_test_recording did not clear _test_auto_stop_timer "
            f"(still {lm._test_auto_stop_timer!r}), YJ-50 regression."
        )
        # Sanity: the function returned a valid envelope.
        assert isinstance(result, dict)
        assert "success" in result

    def test_cancel_test_recording_waits_for_lock_contention(self, monkeypatch):
        """``cancel_test_recording`` must BLOCK on ``_monitor_lock`` if"""
        import voice_typer.server.level_monitor as lm

        # Reset any leftover timer.
        lm._test_auto_stop_timer = None

        # Install a fake timer that records when it was cancelled.
        fake_timer = MagicMock()
        cancel_timestamps: list[float] = []

        def _record_cancel():
            cancel_timestamps.append(time.perf_counter())

        fake_timer.cancel.side_effect = _record_cancel
        lm._test_auto_stop_timer = fake_timer

        # Use the REAL _monitor_lock for this test (the autouse fixture
        real_lock = threading.Lock()
        monkeypatch.setattr(lm, "_monitor_lock", real_lock)

        # Worker thread acquires the lock and holds it until released.
        lock_held = threading.Event()
        release_lock = threading.Event()

        def hold_lock():
            with real_lock:
                lock_held.set()
                # Wait for main thread to signal it's trying to enter
                release_lock.wait(timeout=5.0)

        worker = threading.Thread(target=hold_lock, daemon=True)
        worker.start()
        assert lock_held.wait(timeout=2.0), "worker did not acquire lock"

        # Spawn a thread to call cancel_test_recording. It should BLOCK
        cancel_done = threading.Event()
        cancel_result: list[dict] = []

        def call_cancel():
            cancel_result.append(lm.cancel_test_recording())
            cancel_done.set()

        cancel_thread = threading.Thread(target=call_cancel, daemon=True)
        cancel_thread.start()

        time.sleep(0.1)

        # lock). Either way, the timer.cancel() must NOT have been
        assert not cancel_done.is_set(), (
            "YJ-50: cancel_test_recording returned before the lock was "
            "released, the timer-cancel block did NOT wait for "
            "_monitor_lock (the with-lock wrap is missing)."
        )
        # Critical  assertion: the timer was NOT cancelled while
        assert len(cancel_timestamps) == 0, (
            f"YJ-50: timer.cancel() was called {len(cancel_timestamps)} "
            f"times BEFORE the lock was released, the timer-cancel "
            f"block is NOT inside the with _monitor_lock: wrap (the "
            f"original YJ-50 bug)."
        )

        # Release the lock, cancel_test_recording should now proceed.
        release_lock.set()
        assert cancel_done.wait(timeout=2.0), "cancel_test_recording did not return after lock release, deadlock?"

        # After lock release, the timer MUST have been cancelled exactly
        assert len(cancel_timestamps) == 1, (
            f"expected timer.cancel() to be called exactly once after lock release; got {len(cancel_timestamps)} calls."
        )
        assert lm._test_auto_stop_timer is None

        worker.join(timeout=2.0)
        cancel_thread.join(timeout=2.0)


class TestExpiredRecordingsSweep:
    """``_delete_expired_recordings`` removes only WAVs older than the TTL."""

    @staticmethod
    def _point_config_dir(monkeypatch, tmp_path):
        from voice_typer.server.config_internals import paths as _paths

        monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)

    def test_ttl_constant_is_5_minutes(self):
        from voice_typer.server.level_monitor import test_recording as _tr

        assert _tr.MIC_TEST_RECORDING_TTL_SEC == 300

    def test_sweep_deletes_only_expired_wavs(self, tmp_path, monkeypatch):
        """Old .wav/.wav.tmp gone, fresh .wav kept, non-wav untouched."""
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = tmp_path / _tr._TEST_RECORDINGS_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        old_wav = d / "test-raw-old.wav"
        old_wav.write_bytes(b"RIFF-old")
        old_tmp = d / "test-x.wav.tmp"
        old_tmp.write_bytes(b"partial")
        fresh_wav = d / "test-raw-fresh.wav"
        fresh_wav.write_bytes(b"RIFF-fresh")
        notes = d / "notes.txt"
        notes.write_bytes(b"not audio")
        now = time.time()
        old = now - (_tr.MIC_TEST_RECORDING_TTL_SEC + 60)
        os.utime(old_wav, (old, old))
        os.utime(old_tmp, (old, old))
        os.utime(fresh_wav, (now, now))
        os.utime(notes, (old, old))

        deleted = _tr._delete_expired_recordings()

        assert deleted == 2
        assert not old_wav.exists()
        assert not old_tmp.exists()
        assert fresh_wav.exists()
        assert notes.exists()

    def test_sweep_missing_dir_ok(self, tmp_path, monkeypatch):
        """No recordings dir yet (fresh install): no raise, nothing deleted."""
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        assert _tr._delete_expired_recordings() == 0


class TestExpiryScheduling:
    """Per-test TTL timers delete exactly the persisted uuid paths."""

    @pytest.mark.parametrize("ttl", [0.05, 0.2])
    def test_schedule_deletes_exact_paths_after_fire(self, tmp_path, ttl):
        """Short test TTL (never a 300s sleep): both files gone, sibling kept."""
        from voice_typer.server.level_monitor import test_recording as _tr

        a = tmp_path / "test-filtered-abc123.wav"
        b = tmp_path / "test-raw-def456.wav"
        a.write_bytes(b"A" * 64)
        b.write_bytes(b"B" * 64)
        sibling = tmp_path / "test-raw-sibling.wav"
        sibling.write_bytes(b"S" * 64)

        timer = _tr._schedule_test_recording_expiry([str(a), str(b)], ttl_sec=ttl)

        assert timer is not None
        assert timer.daemon is True
        assert wait_until(lambda: not a.exists() and not b.exists(), timeout=2.0)
        assert sibling.exists()
        # The fired handle discards itself from the module set.
        assert wait_until(lambda: timer not in _tr._test_recording_expiry_timers, timeout=2.0)

    def test_schedule_no_paths_no_timer(self):
        from voice_typer.server.level_monitor import test_recording as _tr

        assert _tr._schedule_test_recording_expiry([]) is None
        assert _tr._schedule_test_recording_expiry(None) is None

    def test_stop_arms_expiry_with_exact_paths(self, monkeypatch):
        """``stop_test_recording`` schedules deletion of the two persisted paths."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import test_recording as _tr

        scheduled: list[list[str]] = []

        def _capture(paths, ttl_sec=_tr.MIC_TEST_RECORDING_TTL_SEC):
            scheduled.append(list(paths))
            return None  # don't arm a real 300s timer in the suite

        monkeypatch.setattr(_tr, "_schedule_test_recording_expiry", _capture)

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)
        try:
            for _ in range(3):
                chunk = np.ones((512, 1), dtype=np.float32) * 0.5
                holder["callback"](chunk, 512, None, None)

            assert wait_until(lambda: len(lm._test_raw_chunks) >= 3, timeout=1.0)

            result = lm.stop_test_recording()
            assert result["success"] is True

            from pathlib import Path as _Path

            paths = {result["audio_file"]["path"], result["raw_audio_file"]["path"]}
            assert len(scheduled) == 1
            assert set(scheduled[0]) == paths
            for p in paths:
                assert _Path(p).is_file()
        finally:
            lm.stop_monitoring()


class TestRecordingsDirRemoval:
    """An emptied mic-test-recordings dir is removed; the next test recreates it."""

    @staticmethod
    def _point_config_dir(monkeypatch, tmp_path):
        from voice_typer.server.config_internals import paths as _paths

        monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)

    def _recordings_dir(self, tmp_path):
        from voice_typer.server.level_monitor import test_recording as _tr

        return tmp_path / _tr._TEST_RECORDINGS_DIRNAME

    def test_sweep_removes_dir_when_emptied(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = self._recordings_dir(tmp_path)
        d.mkdir(parents=True, exist_ok=True)
        old_wav = d / "test-raw-old.wav"
        old_wav.write_bytes(b"RIFF-old")
        old = time.time() - (_tr.MIC_TEST_RECORDING_TTL_SEC + 60)
        os.utime(old_wav, (old, old))

        assert _tr._delete_expired_recordings() == 1
        assert not d.exists()

    def test_sweep_keeps_dir_with_remaining_files(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = self._recordings_dir(tmp_path)
        d.mkdir(parents=True, exist_ok=True)
        fresh = d / "test-raw-fresh.wav"
        fresh.write_bytes(b"RIFF-fresh")

        assert _tr._delete_expired_recordings() == 0
        assert d.is_dir()
        assert fresh.exists()

    def test_remove_missing_dir_ok(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        assert not self._recordings_dir(tmp_path).exists()
        _tr._remove_recordings_dir_if_empty()

    def test_delete_paths_removes_dir_when_emptied(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = self._recordings_dir(tmp_path)
        d.mkdir(parents=True, exist_ok=True)
        a = d / "test-filtered-a.wav"
        b = d / "test-raw-b.wav"
        a.write_bytes(b"A" * 64)
        b.write_bytes(b"B" * 64)

        _tr._delete_test_recording_paths([str(a), str(b)])
        assert not d.exists()

    def test_delete_paths_keeps_dir_with_remaining_file(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = self._recordings_dir(tmp_path)
        d.mkdir(parents=True, exist_ok=True)
        a = d / "test-filtered-a.wav"
        b = d / "test-raw-b.wav"
        a.write_bytes(b"A" * 64)
        b.write_bytes(b"B" * 64)

        _tr._delete_test_recording_paths([str(a)])
        assert d.is_dir()
        assert not a.exists()
        assert b.exists()

    def test_purge_removes_empty_dir(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = self._recordings_dir(tmp_path)
        d.mkdir(parents=True, exist_ok=True)
        (d / "test-raw-old.wav").write_bytes(b"RIFF-old")

        _tr._purge_test_recordings()
        assert not d.exists()

    def test_write_recreates_removed_dir(self, tmp_path, monkeypatch):
        """Next mic test recreates the folder: a write into a removed dir works."""
        import io
        import wave

        from voice_typer.server.level_monitor import test_recording as _tr

        self._point_config_dir(monkeypatch, tmp_path)
        d = self._recordings_dir(tmp_path)
        assert not d.exists()

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 160)

        ref = _tr._write_test_wav(buf, "filtered")
        assert ref is not None
        assert d.is_dir()
        from pathlib import Path as _Path

        assert _Path(ref["path"]).is_file()
