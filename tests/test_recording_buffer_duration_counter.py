"""Tests for the O(1) buffered-samples counter on ``Recorder``.

Background
----------
``Recorder.current_duration_seconds`` is polled at 4 Hz by the streaming
thread as an early-exit guard BEFORE calling ``snapshot()``. Pre-fix,
each poll iterated the whole deque via
``sum(int(c.shape[0]) for c in buffer)`` — O(chunks) per call. On a
30-min dictation at ~16 Hz chunk arrival, that summed over ~28k chunks
per poll.

The fix maintains a running ``_total_buffered_samples: int`` counter,
incremented under ``_lock`` in ``AudioPipeline.append_to_buffer_locked``
and reset to 0 in:

- ``SessionState.reset_session_state`` (called from ``start()``)
- ``_recorder_split.stop_recording`` empty-buffer path (next to
  ``_chunk_count = 0``)
- ``_recorder_split.stop_recording`` main path (after the buffer swap)
- ``_recorder_split.discard_recording`` (after the buffer swap)

The counter also compensates for deque eviction: when ``_buffer.maxlen``
is set and the deque is full, ``append`` silently drops the leftmost
chunk; we peek at ``buffer[0]`` BEFORE the append and subtract its
sample count so the running total stays in sync.

These tests pin:

1. ``append_to_buffer_locked`` increments the counter by the appended
   chunk's sample count.
2. Eviction compensation subtracts the evicted chunk's samples.
3. ``current_duration_seconds`` reads the counter in O(1) (no chunk
   iteration) and returns ``counter / sample_rate``.
4. ``current_duration_seconds`` returns 0.0 when the buffer is empty
   (defensive guard against a stale counter).
5. ``current_duration_seconds`` returns 0.0 when sample rate is 0 /
   unknown.
6. ``reset_session_state`` zeros the counter (start path).
7. ``stop_recording`` zeros the counter on both empty-buffer and
   main paths.
8. ``discard_recording`` zeros the counter.
9. ``stop_recording`` computes peak/silence_pct with a SINGLE
   ``np.abs`` allocation (pre-fix allocated two ~115 MB transients
   for a 30-min dictation).

The tests use MagicMock recorder stubs with real ``threading.Lock`` and
``collections.deque`` so the lock + deque semantics work without
touching PortAudio, real worker threads, or the real audio processor
chain. Each test is deterministic and sub-second.
"""

from __future__ import annotations

import collections
import inspect
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import (
    discard_recording,
    stop_recording,
)
from voice_typer.server.recording.audio_pipeline import AudioPipeline
from voice_typer.server.recording.recorder import Recorder
from voice_typer.server.recording.session_state import SessionState

# ── Helpers ───────────────────────────────────────────────────────


def _make_pipeline_stub_recorder(*, maxlen: int | None = 30000) -> MagicMock:
    """Build a MagicMock recorder with the minimum stubs
    ``AudioPipeline.append_to_buffer_locked`` needs to run without
    touching PortAudio or the real audio processor chain.

    Real ``threading.Lock`` + real ``collections.deque`` so the lock
    acquire + deque append/eviction work with real semantics. The
    counter ``_total_buffered_samples`` is initialized to 0 so the
    tests can assert the exact delta after each append.
    """
    recorder = MagicMock(name="RecorderStub")
    recorder._lock = threading.Lock()
    recorder._buffer = collections.deque(maxlen=maxlen)
    recorder._chunk_count = 0
    recorder._total_buffered_samples = 0
    recorder._dropped_chunks = 0
    return recorder


# ── append_to_buffer_locked counter increment ────────────────────


class TestAppendIncrementsCounter:
    """``append_to_buffer_locked`` must increment
    ``_total_buffered_samples`` by the appended chunk's sample count."""

    def test_single_append_increments_counter(self) -> None:
        recorder = _make_pipeline_stub_recorder()
        pipeline = AudioPipeline(recorder)
        chunk = np.ones(512, dtype=np.float32)

        pipeline.append_to_buffer_locked(chunk)

        assert recorder._total_buffered_samples == 512
        assert recorder._chunk_count == 1

    def test_multiple_appends_accumulate(self) -> None:
        recorder = _make_pipeline_stub_recorder()
        pipeline = AudioPipeline(recorder)

        pipeline.append_to_buffer_locked(np.ones(100, dtype=np.float32))
        pipeline.append_to_buffer_locked(np.ones(200, dtype=np.float32))
        pipeline.append_to_buffer_locked(np.ones(300, dtype=np.float32))

        assert recorder._total_buffered_samples == 600
        assert recorder._chunk_count == 3

    def test_empty_chunk_appends_zero(self) -> None:
        """An empty chunk (size=0) must not corrupt the counter."""
        recorder = _make_pipeline_stub_recorder()
        pipeline = AudioPipeline(recorder)

        pipeline.append_to_buffer_locked(np.array([], dtype=np.float32))

        assert recorder._total_buffered_samples == 0
        assert recorder._chunk_count == 1


# ── Eviction compensation ────────────────────────────────────────


class TestEvictionCompensation:
    """When ``_buffer.maxlen`` is reached, ``append`` silently drops
    the oldest chunk. The counter must subtract the evicted chunk's
    samples so the running total stays in sync with the deque's
    actual contents."""

    def test_eviction_subtracts_oldest_chunk_samples(self) -> None:
        """Fill the deque to ``maxlen``, then append one more — the
        oldest chunk's samples must be subtracted from the counter."""
        maxlen = 3
        recorder = _make_pipeline_stub_recorder(maxlen=maxlen)
        pipeline = AudioPipeline(recorder)

        # Fill the deque to capacity with 100-sample chunks.
        for _ in range(maxlen):
            pipeline.append_to_buffer_locked(np.ones(100, dtype=np.float32))
        assert recorder._total_buffered_samples == maxlen * 100

        # Append one more — the leftmost chunk (100 samples) is evicted.
        pipeline.append_to_buffer_locked(np.ones(50, dtype=np.float32))

        # Expected: (maxlen chunks × 100) - 100 (evicted) + 50 (new) = 250
        assert recorder._total_buffered_samples == maxlen * 100 - 100 + 50
        assert len(recorder._buffer) == maxlen  # deque stays at maxlen

    def test_evicted_chunk_with_different_size_subtracted_correctly(self) -> None:
        """The eviction compensation must use the EVICTED chunk's
        sample count, not the new chunk's. Pin this with chunks of
        different sizes."""
        maxlen = 2
        recorder = _make_pipeline_stub_recorder(maxlen=maxlen)
        pipeline = AudioPipeline(recorder)

        pipeline.append_to_buffer_locked(np.ones(1000, dtype=np.float32))
        pipeline.append_to_buffer_locked(np.ones(500, dtype=np.float32))
        assert recorder._total_buffered_samples == 1500

        # Append a 10-sample chunk — the 1000-sample chunk is evicted.
        pipeline.append_to_buffer_locked(np.ones(10, dtype=np.float32))

        # Expected: 1500 - 1000 (evicted) + 10 (new) = 510
        assert recorder._total_buffered_samples == 510

    def test_no_eviction_when_maxlen_is_none(self) -> None:
        """When ``maxlen`` is ``None`` (unbounded deque), no eviction
        happens — the counter just accumulates."""
        recorder = _make_pipeline_stub_recorder(maxlen=None)
        pipeline = AudioPipeline(recorder)

        for _ in range(100):
            pipeline.append_to_buffer_locked(np.ones(10, dtype=np.float32))

        assert recorder._total_buffered_samples == 1000
        assert len(recorder._buffer) == 100


# ── current_duration_seconds O(1) read ───────────────────────────


def _make_duration_stub(
    *,
    buffer_chunks: list[np.ndarray] | None = None,
    total_samples: int = 0,
    buffer_sr: int | None = None,
    effective_sr: int = 16000,
) -> MagicMock:
    """Build a MagicMock recorder with the minimum attributes
    ``Recorder.current_duration_seconds`` reads."""
    recorder = MagicMock(name="RecorderStub")
    if buffer_chunks is None:
        recorder._buffer = collections.deque(buffer_chunks or [])
    else:
        recorder._buffer = collections.deque(buffer_chunks)
    recorder._total_buffered_samples = total_samples
    recorder._buffer_sr = buffer_sr
    recorder._effective_sr = effective_sr
    return recorder


class TestCurrentDurationSeconds:
    """``current_duration_seconds`` must be an O(1) scalar read of
    ``_total_buffered_samples / sample_rate`` — no chunk iteration."""

    def test_returns_counter_divided_by_sample_rate(self) -> None:
        """For 16000 samples at 16 kHz → 1.0 second."""
        recorder = _make_duration_stub(
            buffer_chunks=[np.ones(16000, dtype=np.float32)],
            total_samples=16000,
            buffer_sr=16000,
        )
        assert Recorder.current_duration_seconds.fget(recorder) == pytest.approx(1.0)

    def test_returns_zero_when_buffer_empty(self) -> None:
        """Empty buffer → 0.0 (defensive guard against stale counter)."""
        recorder = _make_duration_stub(
            buffer_chunks=[],
            total_samples=12345,  # stale value — guard must still return 0.0
            buffer_sr=16000,
        )
        assert Recorder.current_duration_seconds.fget(recorder) == 0.0

    def test_returns_zero_when_sample_rate_unknown(self) -> None:
        """``_buffer_sr`` None and ``_effective_sr`` 0 → 0.0."""
        recorder = _make_duration_stub(
            buffer_chunks=[np.ones(100, dtype=np.float32)],
            total_samples=100,
            buffer_sr=None,
            effective_sr=0,
        )
        assert Recorder.current_duration_seconds.fget(recorder) == 0.0

    def test_falls_back_to_effective_sr_when_buffer_sr_none(self) -> None:
        """When ``_buffer_sr`` is None (no audio processor active yet),
        fall back to ``_effective_sr`` (the device's native rate)."""
        recorder = _make_duration_stub(
            buffer_chunks=[np.ones(48000, dtype=np.float32)],
            total_samples=48000,
            buffer_sr=None,
            effective_sr=48000,
        )
        assert Recorder.current_duration_seconds.fget(recorder) == pytest.approx(1.0)

    def test_does_not_iterate_buffer_chunks(self) -> None:
        """The property must NOT iterate the deque — pin this by
        making iteration raise (so a regression to O(chunks) would
        fail loudly). We replace the deque with a custom container
        whose ``__iter__`` raises."""
        recorder = _make_duration_stub(
            buffer_chunks=None,
            total_samples=16000,
            buffer_sr=16000,
        )

        class _NoIterDeque:
            """Stand-in for ``_buffer`` whose ``__bool__`` returns
            True (so the empty-buffer guard doesn't short-circuit)
            but ``__iter__`` raises — pins the O(1) contract."""

            def __bool__(self) -> bool:
                return True

            def __iter__(self):
                raise AssertionError(
                    "current_duration_seconds must NOT iterate _buffer — "
                    "it should read _total_buffered_samples directly."
                )

        recorder._buffer = _NoIterDeque()
        # Must not raise — the property reads _total_buffered_samples
        # without iterating.
        assert Recorder.current_duration_seconds.fget(recorder) == pytest.approx(1.0)


# ── reset_session_state zeros the counter ────────────────────────


def _make_session_state_stub() -> MagicMock:
    """Build a MagicMock recorder with the minimum attributes
    ``SessionState.reset_session_state`` touches (the counter + a few
    adjacent fields)."""
    recorder = MagicMock(name="RecorderStub")
    recorder._buffer = collections.deque(maxlen=100)
    recorder._chunk_count = 42
    recorder._total_buffered_samples = 99999
    recorder._cached_resampled = np.array([1.0, 2.0], dtype=np.float32)
    recorder._cached_native_chunk_count = 5
    recorder._cached_resample_key = ("stale",)
    recorder._cached_no_resample_len = 100
    recorder._cached_no_resample_arr = np.array([1.0], dtype=np.float32)
    recorder._cached_resampled_segments = []
    recorder._cached_resampled_concat_dirty = False
    recorder._dropped_chunks = 3
    recorder._rms_callback_error_count = 2
    recorder._silence_timer = 1.5
    recorder._silence_start_time = 123.0
    recorder._silence_warning_count = 1
    recorder._silence_next_warning_wait = 5.0
    recorder._recent_rms_values = collections.deque(maxlen=10)
    recorder._recording_start_time = 0.0
    recorder._buffer_sr = 16000
    # VAD + clip + XRUN state (reset_session_state touches all of these).
    recorder._vad = MagicMock()
    recorder._vad_state = "STALE"
    recorder._vad_consecutive_speech_frames = 99
    recorder._vad_consecutive_silence_frames = 99
    recorder._vad_speech_threshold_db = 0
    recorder._vad_silence_threshold_db = 0
    recorder._vad_calibration_rms_values = [1.0]
    recorder._vad_calibrated = True
    recorder._user_stop_pending = True
    recorder._preroll_buffer = collections.deque(maxlen=10)
    recorder._device_disconnected = True
    recorder._device_disconnect_retries = 5
    recorder._previous_chunk_pending = True
    recorder._skipped_frames = 99
    recorder._dropped_ring_chunks = 5
    recorder._device_check_counter = 7
    recorder._cached_target_sr = 0
    recorder._cached_vad_enabled = True
    recorder._cached_use_silero_vad = True
    recorder._cached_silero_available = True
    recorder._cached_vad_resample_up_down = (1, 1)
    recorder._cached_vad_resample_sr = 9999
    recorder._xruns = 5
    recorder._xrun_timestamps = collections.deque(maxlen=10)
    recorder._clip_count = 3
    recorder._peak = 0.5
    recorder._last_clip_log_time = 100.0
    recorder._last_rms = 0.1
    recorder.config = MagicMock(sample_rate=16000, max_recording_time_seconds=900)
    recorder._audio_processor = None
    return recorder


class TestResetSessionStateZerosCounter:
    """``SessionState.reset_session_state`` (called from ``start()``)
    must zero ``_total_buffered_samples`` so the new session's
    ``current_duration_seconds`` polls start from 0."""

    def test_reset_zeros_counter(self) -> None:
        recorder = _make_session_state_stub()
        assert recorder._total_buffered_samples == 99999  # precondition

        SessionState(recorder).reset_session_state(recorder)

        assert recorder._total_buffered_samples == 0
        assert recorder._chunk_count == 0
        assert len(recorder._buffer) == 0


# ── stop_recording zeros the counter on both paths ───────────────


class TestStopRecordingZerosCounter:
    """``stop_recording`` must zero ``_total_buffered_samples`` on
    BOTH the empty-buffer fast-path AND the main (snapshot) path —
    otherwise ``current_duration_seconds`` would continue returning
    the snapshot session's total until the next ``start()`` reset."""

    def test_empty_buffer_path_zeros_counter(self) -> None:
        """When ``_buffer`` is empty inside the locked block, the
        empty-buffer fast-path runs and must zero the counter."""
        # Reuse the mock factory from the stop tests — it sets up all
        # the stubs stop_recording needs.
        from tests.test_recorder_split_stop import _build_mock_recorder

        recorder = _build_mock_recorder(buffer_chunks=[])
        # Simulate a prior session's stale counter.
        recorder._total_buffered_samples = 99999

        stop_recording(recorder)

        assert recorder._total_buffered_samples == 0

    def test_main_path_zeros_counter_after_swap(self) -> None:
        """When ``_buffer`` has chunks, the main path swaps in a
        fresh empty deque and must zero the counter to match."""
        from tests.test_recorder_split_stop import _build_mock_recorder

        chunk = np.ones(100, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])
        # Simulate the session's accumulated samples.
        recorder._total_buffered_samples = 100

        stop_recording(recorder)

        assert recorder._total_buffered_samples == 0
        assert len(recorder._buffer) == 0


# ── discard_recording zeros the counter ──────────────────────────


class TestDiscardRecordingZerosCounter:
    """``discard_recording`` must zero ``_total_buffered_samples``
    when it swaps in a fresh empty deque — otherwise
    ``current_duration_seconds`` would continue returning the
    discarded session's total until the next ``start()`` reset."""

    def test_discard_zeros_counter(self) -> None:
        from tests.test_recorder_split_stop import _build_mock_recorder

        chunk = np.ones(50, dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])
        recorder._total_buffered_samples = 50

        discard_recording(recorder)

        assert recorder._total_buffered_samples == 0
        assert len(recorder._buffer) == 0


# ── stop_recording peak/silence_pct single np.abs allocation ─────


class TestStopRecordingStatsSingleAbsAllocation:
    """``stop_recording`` must compute peak allocation-free (via
    ``max(flat.max(), -flat.min())``) and compute ``np.abs(flat)``
    ONCE for silence_pct (reused). Pre-fix, the code allocated
    ``np.abs(flat).max()`` (~115 MB) AND ``np.abs(audio)`` (~115 MB)
    for the silence mask — a ~230 MB transient for a 30-min 16 kHz
    mono dictation."""

    def test_peak_silence_pct_values_unchanged(self) -> None:
        """The peak and silence_pct values must be IDENTICAL to the
        pre-fix computation. Pin with a known signal."""
        from tests.test_recorder_split_stop import _build_mock_recorder

        # 4 silent + 6 loud samples, max abs = 0.5.
        chunk = np.array(
            [0.0, 0.0, 0.0005, 0.0009, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
            dtype=np.float32,
        )
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        stop_recording(recorder)

        rms, peak, silence_pct = recorder._last_audio_stats
        # Pre-fix expected: peak = 0.5, silence_pct = 40.0.
        assert peak == pytest.approx(0.5, abs=1e-6)
        assert silence_pct == pytest.approx(40.0, abs=1e-3)

    def test_peak_handles_negative_amplitude(self) -> None:
        """``max(|x|) == max(max(x), -min(x))`` must handle a signal
        with a large negative peak (e.g. -0.9). Pre-fix used
        ``np.abs(flat).max()`` which gives the same result, but the
        new max/min formula must not regress."""
        from tests.test_recorder_split_stop import _build_mock_recorder

        chunk = np.array([-0.9, 0.1, -0.5, 0.3], dtype=np.float32)
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        stop_recording(recorder)

        _, peak, _ = recorder._last_audio_stats
        assert peak == pytest.approx(0.9, abs=1e-6)

    def test_np_abs_called_once_per_stop(self) -> None:
        """``np.abs`` must be called at most ONCE on the full audio
        array during stop() — pre-fix called it TWICE (once for peak,
        once for silence_pct). We patch ``numpy.abs`` and count calls
        whose input is the full ``flat`` array (the only large input
        in this test)."""
        from tests.test_recorder_split_stop import _build_mock_recorder

        chunk = np.ones(100, dtype=np.float32) * 0.5
        recorder = _build_mock_recorder(buffer_chunks=[chunk])

        # Patch np.abs at the module where stop_recording looks it up.
        # ``_recorder_split`` does ``import numpy as np`` at module
        # top — patching ``numpy.abs`` globally catches all calls.
        real_abs = np.abs
        call_count = {"n": 0}

        def counting_abs(a, *args, **kwargs):
            # Only count calls on the full audio array (size >= 100).
            # ``np.abs`` may also be called on tiny arrays elsewhere
            # in the stop() path (e.g. per-chunk diagnostics), so we
            # filter by size to isolate the peak/silence_pct calls.
            arr = np.asarray(a)
            if arr.size >= 100:
                call_count["n"] += 1
            return real_abs(a, *args, **kwargs)

        # Patch the ``np.abs`` lookup in the _recorder_split module
        # so the call goes through our counter.
        import voice_typer.server.recording._recorder_split as _split

        original_abs = _split.np.abs
        _split.np.abs = counting_abs
        try:
            stop_recording(recorder)
        finally:
            _split.np.abs = original_abs

        # Pre-fix: 2 calls (one for peak, one for silence_pct).
        # Post-fix: 1 call (only silence_pct; peak uses max/min).
        assert call_count["n"] <= 1, (
            f"stop_recording must call np.abs at most ONCE on the full "
            f"audio array (pre-fix called it twice, allocating ~230 MB "
            f"transient for a 30-min dictation). Got {call_count['n']} "
            f"calls."
        )


# ── Source-string contracts ──────────────────────────────────────


class TestSourceStringContracts:
    """Pin the source-level contracts so a future refactor can't
    silently regress the O(1) counter or the single-allocation
    stats computation."""

    def test_current_duration_seconds_reads_counter(self) -> None:
        """The property body must reference
        ``_total_buffered_samples`` (the O(1) counter) — NOT iterate
        ``_buffer``."""
        src = inspect.getsource(Recorder.current_duration_seconds.fget)
        assert "_total_buffered_samples" in src, (
            "current_duration_seconds must read _total_buffered_samples "
            "(the O(1) counter) — a regression to sum(int(c.shape[0]) "
            "for c in buffer) would re-introduce the O(chunks) per-poll "
            "cost the counter was added to avoid."
        )
        # The old O(chunks) reduction ASSIGNMENT must NOT appear in
        # the function body (the docstring may reference it as
        # historical context, but the executable code must not).
        # Match the assignment pattern ``total_samples = sum(...)`` so
        # we only flag a real regression, not a comment.
        assert "total_samples = sum(" not in src, (
            "current_duration_seconds must NOT iterate _buffer via "
            "`total_samples = sum(int(c.shape[0]) for c in buffer)` — "
            "that's the O(chunks) per-poll hot path the counter replaces."
        )
        # The property must return the counter divided by the sample
        # rate (the new O(1) read).
        assert "self._total_buffered_samples / sr" in src, (
            "current_duration_seconds must return self._total_buffered_samples / sr (the O(1) scalar read)."
        )

    def test_append_to_buffer_locked_increments_counter(self) -> None:
        """``AudioPipeline.append_to_buffer_locked`` must increment
        ``_total_buffered_samples`` under the lock."""
        src = inspect.getsource(AudioPipeline.append_to_buffer_locked)
        assert "_total_buffered_samples" in src, (
            "append_to_buffer_locked must maintain _total_buffered_samples so current_duration_seconds can be O(1)."
        )
        assert "+=" in src, "append_to_buffer_locked must increment the counter."

    def test_reset_session_state_zeros_counter(self) -> None:
        """``SessionState.reset_session_state`` must zero
        ``_total_buffered_samples`` alongside ``_chunk_count``."""
        src = inspect.getsource(SessionState.reset_session_state)
        assert "_total_buffered_samples = 0" in src, (
            "reset_session_state must zero _total_buffered_samples so the new session's duration polls start from 0."
        )

    def test_stop_recording_uses_max_min_for_peak(self) -> None:
        """``stop_recording`` must compute peak via
        ``max(float(flat.max()), -float(flat.min()))`` (allocation-free)
        — NOT ``np.abs(flat).max()`` (which allocates a ~115 MB
        transient)."""
        from voice_typer.server.recording._recorder_split import stop_recording

        src = inspect.getsource(stop_recording)
        assert "max(float(flat.max()), -float(flat.min()))" in src, (
            "stop_recording must compute peak allocation-free via "
            "max(float(flat.max()), -float(flat.min())) — mirrors "
            "AudioPipeline.compute_rms_and_peak. Pre-fix used "
            "np.abs(flat).max() which allocated a ~115 MB transient."
        )
        # The old np.abs(flat).max() pattern must NOT appear.
        assert "np.abs(flat).max()" not in src, (
            "stop_recording must NOT use np.abs(flat).max() — that "
            "allocates a ~115 MB transient for a 30-min dictation."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
