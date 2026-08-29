"""regression tests for the recorder snapshot storage invariants
and start/stop latency fixes.

Covers three focused contracts:

(a) **contiguous snapshot storage** (memory):
    ``take_snapshot`` must serve every no-resample snapshot as a zero-copy
    view over ONE pre-allocated growable float32 buffer, and must NOT
    accumulate per-snapshot state (the historical segment-list +
    lazy-concat caches are gone — replaced by the contiguous storage whose
    geometric growth is amortized O(1) per appended sample). These tests
    replace the former ``TestSegmentListCompaction`` pins: the segment
    machinery (and its ``_SEGMENT_LIST_COMPACTION_THRESHOLD == 64``
    constant) was REMOVED by the single-contiguous-storage redesign, which
    makes unbounded per-snapshot list growth impossible by construction.

(b) ** — teardown poll skip fast-path** (latency):
    ``StreamLifecycle.teardown_stream_body`` must skip the 300ms
    callback-drain poll (the ``time.perf_counter()`` deadline +
    ``time.sleep`` poll loop) entirely when
    ``recorder._is_in_audio_callback`` is already clear on the first
    check. The existing ``while`` loop already short-circuits on the
    first iteration, but the explicit ``if`` guard also skips the
    deadline arithmetic — a tiny but non-zero saving on every
    ``stop()`` (the common case is that the RT callback has already
    returned by the time teardown runs).

(c) ** — ``_prepare_audio`` pipelining** (latency):
    ``stop_recording`` must start ``_prepare_audio`` (the resample)
    on a background thread so it overlaps with the RMS / peak /
    silence_pct stats computation. The resample (~200 ms for 30 s of
    16 kHz mono) and the stats (~150 ms for 30-min 16 kHz mono) both
    run on the original audio array; running them concurrently cuts
    the worst-case stop() tail by the smaller of the two.

The original  task spec asked for a third test
"(c) start() returns within 200ms with model reload in flight" tied
to .  was SKIPPED because at the time the synchronous
``warm_up_resampler()`` call was pinned by an existing source-order
contract test (``tests/test_recorder_split_start.py::
TestResamplerWarmUp``) and the full fix required modifying
``Recorder.__init__`` in ``recorder.py`` (out of that sub-agent's
owned files). The hotkey-path warm-up has since been made
preloader-aware — ``start_recording`` skips the synchronous call while
the ``__init__``-spawned scipy preloader thread is still alive — and
that policy is pinned by
``tests/recording/test_recorder_start_critical_path.py``. The
pipelining test below substitutes for (c) —
it covers the latency fix that DID land.
"""

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

# ---------------------------------------------------------------------------
# (a) contiguous snapshot storage — no per-snapshot growth
# ---------------------------------------------------------------------------


def _make_recorder_for_snapshot(
    *,
    sample_rate: int = 16000,
    effective_sr: int | None = None,
) -> MagicMock:
    """Build a minimal mock recorder for ``take_snapshot`` tests.

    Mirrors the setup in ``tests/test_recorder_snapshot_view.py``'s
    ``_make_recorder``: a MagicMock config, ``_recording_event`` set,
    ``_effective_sr`` / ``_post_filter_sr`` primed, a real
    ``threading.Lock`` for ``_lock``, and the cache fields
    ``take_snapshot`` reads.
    """
    config = MagicMock(sample_rate=sample_rate, microphone=None)
    rec = MagicMock(name="recorder")
    rec.config = config
    rec._recording_event = threading.Event()
    rec._recording_event.set()
    rec._effective_sr = effective_sr if effective_sr is not None else sample_rate
    rec._post_filter_sr = rec._effective_sr
    rec._stream = MagicMock()
    rec._lock = threading.Lock()
    rec._buffer = None  # set per-test
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
    # ``_buffer_sr`` defaults to None — ``take_snapshot`` falls back to
    # ``_effective_sr`` via the ``getattr(recorder, "_buffer_sr", None)
    # or recorder._effective_sr`` idiom.
    rec._buffer_sr = effective_sr if effective_sr is not None else sample_rate
    # ``_cached_target_sr`` is the cached ``config.sample_rate`` read
    # under the lock to avoid attribute lookup.
    rec._cached_target_sr = sample_rate
    return rec


class TestContiguousSnapshotStorage:
    """``take_snapshot`` must not accumulate per-snapshot state.

    The contiguous-storage redesign replaced the segment lists + lazy
    concat caches with ONE growable float32 buffer: snapshots are views
    over its filled region, and new samples extend it in place. These
    tests pin the invariants that make unbounded per-snapshot growth
    impossible by construction (the property the old compaction machinery
    existed to approximate).
    """

    def test_no_resample_snapshots_leave_no_per_snapshot_state(self):
        """After many snapshots with new chunks on the no-resample path
        (the COMMON production path), no segment list may grow — the
        snapshot must be served from the single contiguous buffer."""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=sample_rate)
        rec._buffer_sr = sample_rate

        import collections

        rec._buffer = collections.deque(maxlen=10_000)

        chunk_value = 0
        expected_total_samples = 0
        snap = None
        for i in range(70):  # well past the historical 64-entry threshold
            chunk_value = float(i + 1) * 0.01
            rec._buffer.append(np.full(4, chunk_value, dtype=np.float32))
            expected_total_samples += 4
            snap = take_snapshot(rec)
            # The snapshot must reflect the running total.
            assert snap.size == expected_total_samples, (
                f"snapshot {i}: expected {expected_total_samples} samples, got {snap.size}"
            )

        assert rec._cached_resampled_segments == [], (
            "contiguous storage: _cached_resampled_segments must stay empty — "
            "per-snapshot segment accumulation is the ~3x memory multiplier "
            "regression this design eliminates."
        )
        assert rec._cached_no_resample_segments == [], (
            "contiguous storage: _cached_no_resample_segments must stay empty."
        )
        # The snapshot is a view over the single contiguous storage.
        assert np.shares_memory(snap, rec._buffer.storage)

    def test_resample_snapshots_extend_one_contiguous_cache(self, monkeypatch):
        """On the resample path the incremental cache must be ONE capacity
        array extended in place (geometric growth), not a per-snapshot
        segment list; snapshot values must remain correct across many
        snapshots."""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=48000)
        # _buffer_sr != target_sr → resample path.
        rec._buffer_sr = 48000

        # Stub _resample_chunk so we don't need scipy.
        def fake_resample(audio, effective_sr_in, target_sr):
            # Decimate by the integer ratio (48000 // 16000 == 3).
            step = max(1, effective_sr_in // target_sr)
            return audio[::step].astype(np.float32, copy=False).reshape(-1)

        monkeypatch.setattr(rec, "_resample_chunk", fake_resample)

        import collections

        rec._buffer = collections.deque(maxlen=10_000)

        expected_total_samples = 0
        snap = None
        for i in range(70):
            rec._buffer.append(np.ones((6, 1), dtype=np.float32) * (i + 1))
            expected_total_samples += 2  # 6 samples / 3 = 2 samples
            snap = take_snapshot(rec)
            assert snap.size == expected_total_samples, (
                f"snapshot {i}: expected {expected_total_samples} samples, got {snap.size}"
            )

        assert rec._cached_resampled_segments == [], (
            "contiguous storage: _cached_resampled_segments must stay empty on the resample path too."
        )
        assert np.shares_memory(snap, rec._cached_resampled)
        # Values: chunk i contributes ones*(i+1) decimated by 3 → each of
        # its 2 samples equals (i+1).
        expected = np.repeat(np.arange(1, 71, dtype=np.float32), 2)
        np.testing.assert_array_equal(snap, expected)

    def test_compaction_preserves_snapshot_values(self):
        """After many snapshots, the snapshot must still return the
        correct concatenated audio — the storage must not lose or
        duplicate any samples. This pins the data-correctness
        invariant across growth reallocations."""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=sample_rate)
        rec._buffer_sr = sample_rate

        import collections

        rec._buffer = collections.deque(maxlen=10_000)

        # Append distinct chunks; capture the expected concatenated
        # values for verification after the storage has grown.
        expected_values: list[float] = []
        for i in range(70):
            chunk = np.array([float(i + 1)], dtype=np.float32)
            rec._buffer.append(chunk)
            expected_values.append(float(i + 1))
            take_snapshot(rec)

        # The final snapshot must contain ALL the values.
        snap = take_snapshot(rec)
        np.testing.assert_array_equal(
            snap,
            np.array(expected_values, dtype=np.float32),
        )

    def test_segment_state_never_grows_with_snapshot_count(self):
        """Per-snapshot state must stay FLAT as the snapshot count grows —
        the bounded-growth invariant (formerly pinned via the 64-entry
        compaction threshold, now guaranteed by construction: there is no
        per-snapshot container left)."""
        sample_rate = 16000
        rec = _make_recorder_for_snapshot(sample_rate=sample_rate, effective_sr=sample_rate)
        rec._buffer_sr = sample_rate

        import collections

        rec._buffer = collections.deque(maxlen=10_000)

        for i in range(200):
            rec._buffer.append(np.array([float(i)], dtype=np.float32))
            take_snapshot(rec)
            assert len(rec._cached_no_resample_segments) == 0
            assert len(rec._cached_resampled_segments) == 0


# ---------------------------------------------------------------------------
# (b)  — teardown poll skip when callback clear
# ---------------------------------------------------------------------------


def _make_recorder_for_teardown(*, callback_in_flight: bool) -> MagicMock:
    """Build a mock recorder for ``StreamLifecycle.teardown_stream_body``.

    ``callback_in_flight`` controls whether ``_is_in_audio_callback``
    is set (True) or clear (False) on the first check — the fast-path
    fires only when it's clear.
    """
    rec = MagicMock(name="recorder")
    rec._stream = MagicMock(name="stream")
    # ``_is_in_audio_callback`` is a ``threading.Event``. ``is_set()``
    # returns the current state. We use a real Event so the contract
    # matches production (the production code calls
    # ``.is_set()`` / ``.set()`` / ``.clear()``).
    rec._is_in_audio_callback = threading.Event()
    if callback_in_flight:
        rec._is_in_audio_callback.set()
    return rec


class TestTeardownPollSkipFastPath:
    """``teardown_stream_body`` must skip the 300ms callback-
    drain poll entirely when ``_is_in_audio_callback`` is already clear
    on the first check. The common case is that the RT callback (~10µs)
    has already returned by the time teardown runs."""

    def test_skips_poll_when_callback_clear(self, monkeypatch):
        """When ``_is_in_audio_callback.is_set()`` is False on the
        first check, ``time.sleep`` must NOT be called (the fast-path
        skips the poll loop entirely) AND ``time.perf_counter`` must
        NOT be called for the deadline computation."""
        rec = _make_recorder_for_teardown(callback_in_flight=False)
        # Capture the stream reference before teardown sets it to None.
        stream_ref = rec._stream
        lifecycle = StreamLifecycle(rec)

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

        # CLEAN path (force=False) — the fast-path under test.
        lifecycle.teardown_stream_body(rec, force=False)

        assert sleep_calls == [], (
            " regression: time.sleep was called even though "
            "_is_in_audio_callback was clear on the first check. The "
            "fast-path must skip the entire poll loop."
        )
        # perf_counter is called once at the start of teardown_stream_body
        # (no — actually the body doesn't call perf_counter; only the
        # poll loop does). The fast-path must skip the deadline
        # computation entirely, so perf_counter must NOT be called
        # for the deadline. (We allow zero calls because the body
        # itself doesn't use perf_counter.)
        assert perf_counter_calls == 0, (
            f" regression: time.perf_counter was called "
            f"{perf_counter_calls} times even though the fast-path "
            f"should skip the deadline computation. The deadline "
            f"arithmetic must only run when the callback is in-flight."
        )
        # The stream must still be closed + cleared.
        stream_ref.stop.assert_called_once()
        stream_ref.close.assert_called_once()
        assert rec._stream is None

    def test_polls_when_callback_in_flight(self, monkeypatch):
        """When ``_is_in_audio_callback.is_set()`` is True, the poll
        loop must run until the callback clears (simulating the
        callback finishing mid-poll). This pins the safety contract:
        the fast-path MUST NOT skip the poll when the callback is
        genuinely in-flight."""
        rec = _make_recorder_for_teardown(callback_in_flight=True)
        stream_ref = rec._stream
        lifecycle = StreamLifecycle(rec)

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
        assert rec._stream is None

    def test_force_path_skips_poll_when_callback_clear(self, monkeypatch):
        """The force=True path (disconnect recovery) must also skip
        the poll when the callback is clear — mirrors the CLEAN path
        fast-path."""
        rec = _make_recorder_for_teardown(callback_in_flight=False)
        stream_ref = rec._stream
        lifecycle = StreamLifecycle(rec)

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
        assert rec._stream is None

    def test_returns_early_when_stream_is_none(self):
        """Idempotent contract: when ``_stream`` is None, the body
        must return immediately without touching the callback flag or
        calling any stream methods. (Existing contract — pinned here
        so the fast-path refactor doesn't break it.)"""
        rec = _make_recorder_for_teardown(callback_in_flight=False)
        rec._stream = None
        lifecycle = StreamLifecycle(rec)

        # Should not raise.
        lifecycle.teardown_stream_body(rec, force=False)

        # No stream methods called (stream is None).
        assert rec._stream is None


# ---------------------------------------------------------------------------
# (c)  — _prepare_audio pipelining
# ---------------------------------------------------------------------------


def _build_mock_recorder_for_stop(
    *,
    buffer_chunks: list[np.ndarray] | None = None,
    buffer_sr: int | None = 16000,
    effective_sr: int = 16000,
    prepare_audio_delay_s: float = 0.0,
) -> MagicMock:
    """Build a mock recorder for ``stop_recording`` tests.

    Mirrors ``tests/test_recorder_split_stop.py``'s
    ``_build_mock_recorder``: a MagicMock with a real ``_lock``, a real
    ``collections.deque`` for ``_buffer``, and a MagicMock
    ``_prepare_audio`` whose ``side_effect`` returns the input audio
    (identity) after an optional delay (to simulate a slow resample).
    """
    import collections

    rec = MagicMock(name="recorder")
    rec._recording_event = threading.Event()
    rec._recording_event.set()
    rec._stop_generation = 0
    rec._user_stop_pending = False
    rec._lock = threading.Lock()

    if buffer_chunks is None:
        buffer_chunks = [np.zeros(100, dtype=np.float32)]
    rec._buffer = collections.deque(buffer_chunks, maxlen=30_000)
    rec._chunk_count = len(buffer_chunks)
    rec._buffer_sr = buffer_sr
    rec._effective_sr = effective_sr
    rec._last_rms = 0.0
    rec._last_audio_stats = (0.0, 0.0, 0.0)

    # Identity _prepare_audio with optional delay (simulates a slow
    # resample so we can observe the overlap with stats computation).
    def _prepare_audio_with_delay(audio, effective_sr_in, **kw):
        if prepare_audio_delay_s > 0:
            time.sleep(prepare_audio_delay_s)
        return audio

    rec._prepare_audio.side_effect = _prepare_audio_with_delay
    return rec


class TestStopRecordingPrepareAudioPipelining:
    """``stop_recording`` starts ``_prepare_audio`` on a
    background thread so it overlaps with the RMS / peak / silence_pct
    stats computation. The method-call order contract
    (``secure_clear_caches`` → ``prepare_audio``) is preserved, but
    the resample runs concurrently with the stats — cutting the
    worst-case stop() tail by the smaller of the two."""

    def test_prepare_audio_called_exactly_once(self):
        """``_prepare_audio`` must be called exactly once (on the
        background thread). The pipelining must NOT call it twice."""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32)],
        )
        stop_recording(rec)
        rec._prepare_audio.assert_called_once()

    def test_prepare_audio_called_with_captured_buffer_sr(self):
        """The resample thread must receive the captured
        ``_buffer_sr`` (the rate the audio was appended at), NOT
        ``_effective_sr``. This is the XV-31 chipmunk-voice regression
        guard — re-pinned here because the pipelining moves the call
        onto a background thread where the captured local could be
        lost if the thread closure is mis-wired."""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(100, dtype=np.float32)],
            buffer_sr=16000,
            effective_sr=48000,
        )
        stop_recording(rec)
        rec._prepare_audio.assert_called_once()
        effective_sr_passed = rec._prepare_audio.call_args.args[1]
        assert effective_sr_passed == 16000, (
            f"XV-31 regression: _prepare_audio was called with "
            f"effective_sr={effective_sr_passed}, expected 16000 (the "
            f"captured _buffer_sr). The pipelining closure must capture "
            f"the local — using _effective_sr (48000) would decimate "
            f"the already-16 kHz audio 3:1 → chipmunk voice."
        )

    def test_prepare_audio_returned_to_caller(self):
        """The return value of ``_prepare_audio`` must be the return
        value of ``stop_recording`` (the resampled audio). The
        pipelining must capture the thread's return value and return
        it from ``stop_recording`` — not the original pre-resample
        array."""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32) * 0.5],
        )
        # Replace the identity side_effect with one that returns a
        # distinct array (simulating a real resample that changes the
        # data).
        resampled = np.full(200, 0.25, dtype=np.float32)
        rec._prepare_audio.side_effect = None
        rec._prepare_audio.return_value = resampled

        result = stop_recording(rec)
        assert result is resampled, (
            " regression: stop_recording did not return the "
            "_prepare_audio return value. The pipelining must capture "
            "the thread's result and return it."
        )

    def test_stats_overlap_with_prepare_audio(self, monkeypatch):
        """``_prepare_audio`` (on the resample thread) must OVERLAP
        the stats computation — proven deterministically, without
        wall-clock thresholds.

        The previous version asserted total ``stop_recording()`` wall
        time < 300ms; it flaked under full-suite parallel load
        (observed 511ms on a loaded host — the simulated 100ms
        resample alone already exceeds any scheduler guarantee, so the
        assertion measured machine load, not pipelining). It also
        could NOT catch its own named regression: stats on a 100K-
        sample array take ~1ms, so sequential execution totals ~101ms
        < 300ms on an idle box.

        Instead, three timestamps from the same monotonic clock are
        compared for ORDER, which load cannot break:

            prepare_started < stats_started < prepare_finished

        ``prepare_started`` is recorded on the resample thread's first
        line; ``prepare_finished`` after its 2s simulated resample;
        ``stats_started`` is the main thread's first stats op — the
        ``np.dot`` RMS call, verified to be the ONLY ``np.dot`` call
        in the mocked ``stop_recording`` path (probed: 1 call, ~1ms in).
        Sequential regression → stats precede the thread → first
        assert fails. Stats-after-resample → second assert fails. The
        2s simulated resample gives the main thread ~1000x the
        worst-case scheduler-latency margin, so preemption cannot
        produce a false failure.
        """
        times: dict[str, float] = {}

        def _slow_prepare(audio, effective_sr_in, **kw):
            times["prepare_started"] = time.perf_counter()
            time.sleep(2.0)
            times["prepare_finished"] = time.perf_counter()
            return audio

        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(100_000, dtype=np.float32) * 0.5],
        )
        rec._prepare_audio.side_effect = _slow_prepare

        real_dot = np.dot

        def _probing_dot(a, b, out=None):
            times.setdefault("stats_started", time.perf_counter())
            return real_dot(a, b) if out is None else real_dot(a, b, out)

        monkeypatch.setattr(np, "dot", _probing_dot)
        stop_recording(rec)

        assert "prepare_started" in times and "stats_started" in times
        assert times["prepare_started"] <= times["stats_started"], (
            " regression: the stats computation started BEFORE the "
            "resample thread — _prepare_audio is NOT pipelined. The "
            "resample thread must be started BEFORE the stats "
            "computation."
        )
        assert times["stats_started"] < times["prepare_finished"], (
            " regression: the stats computation finished after the "
            "resample — they did NOT overlap. The resample thread "
            "must be started BEFORE the stats computation."
        )

    def test_prepare_audio_exception_propagates(self):
        """If ``_prepare_audio`` raises on the background thread, the
        exception must propagate out of ``stop_recording`` (not be
        swallowed). The pipelining must re-raise after joining the
        thread."""
        rec = _build_mock_recorder_for_stop(
            buffer_chunks=[np.ones(50, dtype=np.float32)],
        )
        rec._prepare_audio.side_effect = RuntimeError("simulated resample failure")

        with pytest.raises(RuntimeError, match="simulated resample failure"):
            stop_recording(rec)

    def test_step_order_preserved(self):
        """The method-call order contract
        (``teardown_stream`` → ``stop_audio_worker`` → ``stop_event_worker``
        → ``stop_device_health_checker`` → ``secure_clear_caches`` →
        ``prepare_audio``) must be preserved. The pipelining moves
        ``_prepare_audio`` to a background thread, but the thread
        STARTS ``_prepare_audio`` immediately after concat (after
        ``secure_clear_caches`` has run inside the lock)."""
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
        rec._stop_event_worker.side_effect = log_call("stop_event_worker")
        rec._stop_device_health_checker.side_effect = log_call("stop_device_health_checker")
        rec._secure_clear_caches.side_effect = log_call("secure_clear_caches")

        def _prepare_audio_hook(audio, effective_sr_in, *a, **k):
            call_log.append("prepare_audio")
            return audio

        rec._prepare_audio.side_effect = _prepare_audio_hook

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
            f"source-order contract — _prepare_audio must START after "
            f"secure_clear_caches (the thread is started after the "
            f"concat, which is after the lock release)."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--timeout=30"]))
