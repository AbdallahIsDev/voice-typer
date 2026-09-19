"""regression tests for the recorder snapshot storage invariants"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import (
    stop_recording,
    take_snapshot,
)
from voice_typer.server.recording.stream_lifecycle import StreamLifecycle

# ``stop_recording`` invokes the free function ``prepare_audio``

_prepare_audio_mock_holder: dict = {}


@pytest.fixture(autouse=True)
def _mock_prepare_audio(monkeypatch):
    """Patch ``_recorder_split.prepare_audio`` with an identity"""
    import voice_typer.server.recording._recorder_split as split_mod

    mock = MagicMock(name="prepare_audio", side_effect=lambda rec, audio, effective_sr_in, **kw: audio)
    monkeypatch.setattr(split_mod, "prepare_audio", mock)
    _prepare_audio_mock_holder["mock"] = mock
    yield mock
    _prepare_audio_mock_holder.pop("mock", None)


def _prep() -> MagicMock:
    return _prepare_audio_mock_holder["mock"]


def _make_recorder_for_snapshot(
    *,
    sample_rate: int = 16000,
    effective_sr: int | None = None,
) -> MagicMock:
    """Build a minimal mock recorder for ``take_snapshot`` tests."""
    config = MagicMock(sample_rate=sample_rate, microphone=None)
    rec = MagicMock(name="recorder")
    rec.config = config
    rec._recording_event = threading.Event()
    rec._recording_event.set()
    rec._effective_sr = effective_sr if effective_sr is not None else sample_rate
    rec._post_filter_sr = rec._effective_sr
    rec._stream_lifecycle._stream = MagicMock()
    rec._audio_pipeline._lock = threading.Lock()
    rec._audio_pipeline._buffer = None  # set per-test
    # Cache fields ``take_snapshot`` initializes / reads.
    rec._cached_resampled = np.array([], dtype=np.float32)
    rec._cached_native_chunk_count = 0
    rec._cached_resample_key = ()
    rec._cached_resampled_segments = []
    rec._cached_resampled_concat_dirty = False
    rec._cached_no_resample_len = -1
    rec._cached_no_resample_arr = None
    rec._cached_no_resample_segments = []
    rec._cached_no_resample_concat_dirty = False
    rec._audio_pipeline._buffer_sr = effective_sr if effective_sr is not None else sample_rate
    # ``_cached_target_sr`` is the cached ``config.sample_rate`` read
    rec._cached_target_sr = sample_rate
    return rec


class TestContiguousSnapshotStorage:
    """``take_snapshot`` must not accumulate per-snapshot state."""

    def test_no_resample_snapshots_leave_no_per_snapshot_state(self):
        """After many snapshots with new chunks on the no-resample path"""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=sample_rate)
        rec._audio_pipeline._buffer_sr = sample_rate

        import collections

        rec._audio_pipeline._buffer = collections.deque(maxlen=10_000)

        chunk_value = 0
        expected_total_samples = 0
        snap = None
        for i in range(70):  # well past the historical 64-entry threshold
            chunk_value = float(i + 1) * 0.01
            rec._audio_pipeline._buffer.append(np.full(4, chunk_value, dtype=np.float32))
            expected_total_samples += 4
            snap = take_snapshot(rec)
            # The snapshot must reflect the running total.
            assert snap.size == expected_total_samples, (
                f"snapshot {i}: expected {expected_total_samples} samples, got {snap.size}"
            )

        assert rec._cached_resampled_segments == [], (
            "contiguous storage: _cached_resampled_segments must stay empty, "
            "per-snapshot segment accumulation is the ~3x memory multiplier "
            "regression this design eliminates."
        )
        assert rec._cached_no_resample_segments == [], (
            "contiguous storage: _cached_no_resample_segments must stay empty."
        )
        # The snapshot is a view over the single contiguous storage.
        assert np.shares_memory(snap, rec._audio_pipeline._buffer.storage)

    def test_resample_snapshots_extend_one_contiguous_cache(self, monkeypatch):
        """On the resample path the incremental cache must be ONE capacity"""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=48000)
        # _buffer_sr != target_sr → resample path.
        rec._audio_pipeline._buffer_sr = 48000

        def fake_resample(recorder, audio, effective_sr_in, target_sr):
            # Decimate by the integer ratio (48000 // 16000 == 3).
            step = max(1, effective_sr_in // target_sr)
            return audio[::step].astype(np.float32, copy=False).reshape(-1)

        monkeypatch.setattr("voice_typer.server.recording._recorder_split.resample_chunk", fake_resample)

        import collections

        rec._audio_pipeline._buffer = collections.deque(maxlen=10_000)

        expected_total_samples = 0
        snap = None
        for i in range(70):
            rec._audio_pipeline._buffer.append(np.ones((6, 1), dtype=np.float32) * (i + 1))
            expected_total_samples += 2  # 6 samples / 3 = 2 samples
            snap = take_snapshot(rec)
            assert snap.size == expected_total_samples, (
                f"snapshot {i}: expected {expected_total_samples} samples, got {snap.size}"
            )

        assert rec._cached_resampled_segments == [], (
            "contiguous storage: _cached_resampled_segments must stay empty on the resample path too."
        )
        assert np.shares_memory(snap, rec._cached_resampled)
        expected = np.repeat(np.arange(1, 71, dtype=np.float32), 2)
        np.testing.assert_array_equal(snap, expected)

    def test_compaction_preserves_snapshot_values(self):
        """duplicate any samples. This pins the data-correctness"""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=sample_rate)
        rec._audio_pipeline._buffer_sr = sample_rate

        import collections

        rec._audio_pipeline._buffer = collections.deque(maxlen=10_000)

        # Append distinct chunks; capture the expected concatenated
        expected_values: list[float] = []
        for i in range(70):
            chunk = np.array([float(i + 1)], dtype=np.float32)
            rec._audio_pipeline._buffer.append(chunk)
            expected_values.append(float(i + 1))
            take_snapshot(rec)

        # The final snapshot must contain ALL the values.
        snap = take_snapshot(rec)
        np.testing.assert_array_equal(
            snap,
            np.array(expected_values, dtype=np.float32),
        )

    def test_segment_state_never_grows_with_snapshot_count(self):
        """Per-snapshot state must stay FLAT as the snapshot count grows —"""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=sample_rate)
        rec._audio_pipeline._buffer_sr = sample_rate

        import collections

        rec._audio_pipeline._buffer = collections.deque(maxlen=10_000)

        for i in range(200):
            rec._audio_pipeline._buffer.append(np.array([float(i)], dtype=np.float32))
            take_snapshot(rec)
            assert len(rec._cached_no_resample_segments) == 0
            assert len(rec._cached_resampled_segments) == 0


def _make_recorder_for_teardown(*, callback_in_flight: bool) -> MagicMock:
    """Build a mock recorder for ``StreamLifecycle.teardown_stream_body``."""
    rec = MagicMock(name="recorder")
    rec._is_in_audio_callback = threading.Event()
    if callback_in_flight:
        rec._is_in_audio_callback.set()
    return rec


class TestTeardownPollSkipFastPath:
    """``teardown_stream_body`` must skip the 300ms callback-"""

    def test_skips_poll_when_callback_clear(self, monkeypatch):
        """first check, ``time.sleep`` must NOT be called (the fast-path"""
        rec = _make_recorder_for_teardown(callback_in_flight=False)
        lifecycle = StreamLifecycle(rec)
        # Set the stream on the OWNING lifecycle; capture the reference
        stream_ref = lifecycle._stream = MagicMock(name="stream")

        # Track time.sleep and time.perf_counter calls.
        sleep_calls: list[float] = []
        perf_counter_calls: int = 0

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        real_perf_counter = time.perf_counter

        def fake_perf_counter():
            nonlocal perf_counter_calls
            perf_counter_calls += 1
            return real_perf_counter()

        monkeypatch.setattr(
            "voice_typer.server.recording.stream_lifecycle.time.sleep",
            fake_sleep,
        )
        monkeypatch.setattr(
            "voice_typer.server.recording.stream_lifecycle.time.perf_counter",
            fake_perf_counter,
        )

        # CLEAN path (force=False), the fast-path under test.
        lifecycle.teardown_stream_body(rec, force=False)

        assert sleep_calls == [], (
            " regression: time.sleep was called even though "
            "_is_in_audio_callback was clear on the first check. The "
            "fast-path must skip the entire poll loop."
        )
        # computation entirely, so perf_counter must NOT be called
        assert perf_counter_calls == 0, (
            f" regression: time.perf_counter was called "
            f"{perf_counter_calls} times even though the fast-path "
            f"should skip the deadline computation. The deadline "
            f"arithmetic must only run when the callback is in-flight."
        )
        # The stream must still be closed + cleared.
        stream_ref.stop.assert_called_once()
        stream_ref.close.assert_called_once()
        assert lifecycle._stream is None

    def test_polls_when_callback_in_flight(self, monkeypatch):
        """When ``_is_in_audio_callback.is_set()`` is True, the poll"""
        rec = _make_recorder_for_teardown(callback_in_flight=True)
        lifecycle = StreamLifecycle(rec)
        stream_ref = lifecycle._stream = MagicMock(name="stream")

        # Simulate the callback clearing after 2 poll iterations.
        sleep_count = [0]

        def fake_sleep(seconds):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                rec._is_in_audio_callback.clear()

        monkeypatch.setattr(
            "voice_typer.server.recording.stream_lifecycle.time.sleep",
            fake_sleep,
        )

        lifecycle.teardown_stream_body(rec, force=False)

        # The poll loop must have run (sleep called ≥ 1 time).
        assert sleep_count[0] >= 1, (
            " regression: time.sleep was not called even though "
            "_is_in_audio_callback was set on the first check. The "
            "poll loop must run when the callback is in-flight."
        )
        stream_ref.stop.assert_called_once()
        stream_ref.close.assert_called_once()
        assert lifecycle._stream is None

    def test_force_path_skips_poll_when_callback_clear(self, monkeypatch):
        """The force=True path (disconnect recovery) must also skip"""
        rec = _make_recorder_for_teardown(callback_in_flight=False)
        lifecycle = StreamLifecycle(rec)
        stream_ref = lifecycle._stream = MagicMock(name="stream")

        sleep_calls: list[float] = []
        monkeypatch.setattr(
            "voice_typer.server.recording.stream_lifecycle.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        lifecycle.teardown_stream_body(rec, force=True)

        assert sleep_calls == [], (
            " regression (force path): time.sleep was called even though _is_in_audio_callback was clear."
        )
        stream_ref.abort.assert_called_once()
        stream_ref.close.assert_called_once()
        assert lifecycle._stream is None

    def test_returns_early_when_stream_is_none(self):
        """Idempotent contract: when ``_stream`` is None, the body"""
        rec = _make_recorder_for_teardown(callback_in_flight=False)
        rec._stream_lifecycle._stream = None
        lifecycle = StreamLifecycle(rec)

        # Should not raise.
        lifecycle.teardown_stream_body(rec, force=False)

        # No stream methods called (stream is None).
        assert lifecycle._stream is None


def _build_mock_recorder_for_stop(
    *,
    buffer_chunks: list[np.ndarray] | None = None,
    buffer_sr: int | None = 16000,
    effective_sr: int = 16000,
    prepare_audio_delay_s: float = 0.0,
) -> MagicMock:
    """Build a mock recorder for ``stop_recording`` tests."""
    import collections

    rec = MagicMock(name="recorder")
    rec._recording_event = threading.Event()
    rec._recording_event.set()
    rec._stop_generation = 0
    rec._user_stop_pending = False
    rec._audio_pipeline._lock = threading.Lock()

    if buffer_chunks is None:
        buffer_chunks = [np.zeros(100, dtype=np.float32)]
    rec._audio_pipeline._buffer = collections.deque(buffer_chunks, maxlen=30_000)
    rec._audio_pipeline._chunk_count = len(buffer_chunks)
    rec._audio_pipeline._buffer_sr = buffer_sr
    rec._effective_sr = effective_sr
    rec._last_rms = 0.0
    rec._last_audio_stats = (0.0, 0.0, 0.0)

    # Identity _prepare_audio with optional delay (simulates a slow
    def _prepare_audio_with_delay(rec, audio, effective_sr_in, **kw):
        if prepare_audio_delay_s > 0:
            time.sleep(prepare_audio_delay_s)
        return audio

    _prep().side_effect = _prepare_audio_with_delay
    return rec


class TestStopRecordingPrepareAudioPipelining:
    """background thread so it overlaps with the RMS / peak / silence_pct"""

    def test_prepare_audio_called_exactly_once(self):
        """``_prepare_audio`` must be called exactly once (on the"""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32)],
        )
        stop_recording(rec)
        _prep().assert_called_once()

    def test_prepare_audio_called_with_captured_buffer_sr(self):
        """The resample thread must receive the captured"""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(100, dtype=np.float32)],
            buffer_sr=16000,
            effective_sr=48000,
        )
        stop_recording(rec)
        _prep().assert_called_once()
        effective_sr_passed = _prep().call_args.args[2]
        assert effective_sr_passed == 16000, (
            f"XV-31 regression: _prepare_audio was called with "
            f"effective_sr={effective_sr_passed}, expected 16000 (the "
            f"captured _buffer_sr). The pipelining closure must capture "
            f"the local, using _effective_sr (48000) would decimate "
            f"the already-16 kHz audio 3:1 → chipmunk voice."
        )

    def test_prepare_audio_returned_to_caller(self):
        """The return value of ``_prepare_audio`` must be the return"""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32) * 0.5],
        )
        resampled = np.full(200, 0.25, dtype=np.float32)
        _prep().side_effect = None
        _prep().return_value = resampled

        result = stop_recording(rec)
        assert result is resampled, (
            " regression: stop_recording did not return the "
            "_prepare_audio return value. The pipelining must capture "
            "the thread's result and return it."
        )

    def test_stats_overlap_with_prepare_audio(self, monkeypatch):
        """``_prepare_audio`` (on the resample thread) must OVERLAP"""
        times: dict[str, float] = {}

        def _slow_prepare(rec, audio, effective_sr_in, **kw):
            times["prepare_started"] = time.perf_counter()
            time.sleep(2.0)
            times["prepare_finished"] = time.perf_counter()
            return audio

        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(100_000, dtype=np.float32) * 0.5],
        )
        _prep().side_effect = _slow_prepare

        real_dot = np.dot

        def _probing_dot(a, b, out=None):
            times.setdefault("stats_started", time.perf_counter())
            return real_dot(a, b) if out is None else real_dot(a, b, out)

        monkeypatch.setattr(np, "dot", _probing_dot)
        stop_recording(rec)

        assert "prepare_started" in times and "stats_started" in times
        assert times["prepare_started"] <= times["stats_started"], (
            " regression: the stats computation started BEFORE the "
            "resample thread, _prepare_audio is NOT pipelined. The "
            "resample thread must be started BEFORE the stats "
            "computation."
        )
        assert times["stats_started"] < times["prepare_finished"], (
            " regression: the stats computation finished after the "
            "resample, they did NOT overlap. The resample thread "
            "must be started BEFORE the stats computation."
        )

    def test_prepare_audio_exception_propagates(self):
        """exception must propagate out of ``stop_recording`` (not be"""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32)],
        )
        _prep().side_effect = RuntimeError("simulated resample failure")

        with pytest.raises(RuntimeError, match="simulated resample failure"):
            stop_recording(rec)

    def test_step_order_preserved(self):
        """The method-call order contract"""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32)],
        )
        call_log: list[str] = []

        def log_call(name):
            def _hook(*a, **k):
                call_log.append(name)
                return None

            return _hook

        rec._teardown_stream.side_effect = log_call("teardown_stream")
        rec._stop_audio_worker.side_effect = log_call("stop_audio_worker")
        rec._capture.stop_event_worker_body.side_effect = log_call("stop_event_worker")
        rec._stop_device_health_checker.side_effect = log_call("stop_device_health_checker")
        rec._session_state.secure_clear_caches.side_effect = log_call("secure_clear_caches")

        def _prepare_audio_hook(rec, audio, effective_sr_in, *a, **k):
            call_log.append("prepare_audio")
            return audio

        _prep().side_effect = _prepare_audio_hook

        stop_recording(rec)

        expected_order = [
            "teardown_stream",
            "stop_audio_worker",
            "stop_event_worker",
            "stop_device_health_checker",
            "secure_clear_caches",
            "prepare_audio",
        ]
        observed = [c for c in call_log if c in expected_order]
        assert observed == expected_order, (
            f"stop_recording step order mismatch: expected {expected_order}, "
            f"got {observed}. The pipelining must preserve the "
            f"source-order contract, _prepare_audio must START after "
            f"secure_clear_caches (the thread is started after the "
            f"concat, which is after the lock release)."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--timeout=30"]))
