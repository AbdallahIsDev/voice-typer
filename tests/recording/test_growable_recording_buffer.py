"""Focused tests for the contiguous growable recording buffer.

The recording storage was redesigned from ``deque(maxlen=N)`` of chunks +
rebuild-on-demand snapshot caches into ONE pre-allocated growable float32
ndarray (:class:`~voice_typer.server.recording._recorder_split.GrowableRecordingBuffer`)
with geometric capacity doubling. These tests pin the five core contracts
of that design:

1. growth across doubling boundaries preserves exact sample continuity;
2. snapshots are views over live storage with stable provenance identity
   (``view().base is storage``) while the data is physically linear;
3. the resample-path cache is invalidated when (dtype, src_sr, dst_sr)
   changes;
4. ``stop()`` returns audio bit-equal to the historical concatenation of
   the appended chunks (golden sine comparison);
5. ``stop()`` / ``discard()`` zero the old storage in the background
   after the handoff (SEC-audit-008 ordering preserved).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording._recorder_split import (
    GrowableRecordingBuffer,
    discard_recording,
    stop_recording,
    take_snapshot,
)


@pytest.fixture(autouse=True)
def _identity_prepare_audio(monkeypatch):
    """Patch the module-level ``prepare_audio`` binding that
    ``stop_recording`` invokes (the historical
    ``Recorder._prepare_audio`` delegator was removed) with an identity
    pass-through so the stop-path tests exercise the buffer mechanics."""
    import voice_typer.server.recording._recorder_split as split_mod

    monkeypatch.setattr(split_mod, "prepare_audio", lambda rec, audio, effective_sr_in, **kw: audio)


# ── helpers ──────────────────────────────────────────────────────────────


def _sine(freq: float, n_samples: int, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    t = np.arange(n_samples, dtype=np.float64) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class _RecorderSpy:
    """Records ``_note_buffer_capacity_eviction`` calls."""

    def __init__(self) -> None:
        self.evicted: list[int] = []

    def __call__(self, samples: int) -> None:
        self.evicted.append(int(samples))


# ── 1. growth across doubling boundaries ─────────────────────────────────


class TestGrowthAcrossDoublingBoundaries:
    def test_sample_continuity_across_many_reallocations(self):
        """Appends crossing several geometric-doubling reallocations must
        preserve exact sample order and values — no loss, duplication, or
        reordering at any boundary."""
        buf = GrowableRecordingBuffer(
            maxlen=None,
            nominal_sample_rate=16000,
            initial_capacity_samples=64,
            max_capacity_samples=1 << 40,
        )
        expected: list[np.ndarray] = []
        sizes = [7, 9, 31, 64, 65, 128, 1, 200, 513]
        for pos, n in enumerate(sizes):
            chunk = _sine(440.0, n)
            chunk[0] += pos  # make every chunk globally unique
            buf.append(chunk)
            expected.append(chunk)
        want = np.concatenate(expected)
        got = buf.view()
        assert got.size == want.size
        np.testing.assert_array_equal(got, want)
        # Doubling actually happened: capacity must have grown past the
        # initial allocation.
        assert buf.storage.shape[0] >= want.size

    def test_capacity_doubles_geometrically(self):
        """Capacity must follow the documented doubling policy (clamped to
        the hard cap), not grow by small increments."""
        buf = GrowableRecordingBuffer(
            maxlen=None,
            nominal_sample_rate=1,
            initial_capacity_samples=4,
            max_capacity_samples=10_000,
        )

        def cap() -> int:
            return int(buf.storage.shape[0]) if buf.storage is not None else 0

        observed = [cap()]
        for i in range(64):
            buf.append(np.array([float(i)], dtype=np.float32))
            if cap() != observed[-1]:
                observed.append(cap())
        # Every reallocation at least doubles (geometric growth), and the
        # final allocation is within one doubling of the data size.
        for prev, cur in zip(observed, observed[1:], strict=False):
            assert cur >= 2 * prev, f"capacity jump {prev} -> {cur} is not geometric"
        assert cap() < 4 * 64

    def test_hard_cap_freezes_storage_and_evicts_from_front(self):
        """At the hard cap the storage stops growing and behaves as a
        ring: oldest samples are evicted, newest kept, total bounded."""
        buf = GrowableRecordingBuffer(
            maxlen=None,
            nominal_sample_rate=8,
            initial_capacity_samples=8,
            max_capacity_samples=32,
        )
        stream = _sine(220.0, 500)
        for i in range(500):
            buf.append(stream[i : i + 1])
        assert buf.total_samples == 32
        assert len(buf) == 32
        assert buf.storage.shape[0] == 32
        np.testing.assert_array_equal(buf.view(), stream[-32:])
        assert buf.evicted_samples_total == 468
        assert buf.appended_samples_total == 500

    def test_chunk_count_maxlen_mirrors_deque_eviction(self):
        """maxlen eviction must drop exactly one oldest CHUNK per append
        once full (deque parity — the pipeline's counter compensation
        depends on this)."""
        spy = _RecorderSpy()
        buf = GrowableRecordingBuffer(maxlen=4, on_extra_eviction=spy)
        lens = [3, 5, 2, 9, 4, 6]
        chunks = [_sine(330.0, n) for n in lens]
        for c in chunks:
            buf.append(c)
        assert len(buf) == 4
        # Oldest two chunks (3+5 samples) evicted by the chunk-count rule.
        assert buf.evicted_samples_total == 8
        assert [x.size for x in buf] == [2, 9, 4, 6]
        assert spy.evicted == [], (
            "maxlen-mirrored eviction must NOT be reported through the "
            "extra-eviction hook (the append caller already compensated)"
        )
        joined = buf.view()
        np.testing.assert_array_equal(joined[:2], chunks[2])
        np.testing.assert_array_equal(joined[2:11], chunks[3])
        np.testing.assert_array_equal(joined[11:15], chunks[4])
        np.testing.assert_array_equal(joined[15:], chunks[5])


# ── 2. view identity & immutability expectations ─────────────────────────


class TestSnapshotViewIdentity:
    def test_view_is_zero_copy_with_stable_provenance(self):
        buf = GrowableRecordingBuffer(maxlen=None, nominal_sample_rate=16000)
        buf.append(_sine(440.0, 100))
        v1 = buf.view()
        assert np.shares_memory(v1, buf.storage)
        assert v1.base is buf.storage
        # Repeated views share the same backing object.
        v2 = buf.view()
        assert v2.base is buf.storage
        np.testing.assert_array_equal(v1, v2)

    def test_growth_rebinds_provenance_old_views_remain_valid(self):
        buf = GrowableRecordingBuffer(
            maxlen=None,
            nominal_sample_rate=1,
            initial_capacity_samples=4,
            max_capacity_samples=1 << 30,
        )
        buf.append(np.ones(4, dtype=np.float32))
        old_view = buf.view()
        old_base = old_view.base
        buf.append(np.full(100, 2.0, dtype=np.float32))
        new_view = buf.view()
        assert new_view.base is not old_base
        # Old view still readable with its original contents.
        np.testing.assert_array_equal(old_view, np.ones(4, dtype=np.float32))
        np.testing.assert_array_equal(new_view[:4], np.ones(4, dtype=np.float32))

    def test_wrapped_ring_view_is_contiguous_copy(self):
        buf = GrowableRecordingBuffer(
            maxlen=None,
            nominal_sample_rate=1,
            initial_capacity_samples=8,
            max_capacity_samples=8,
        )
        for i in range(20):
            buf.append(np.array([float(i)], dtype=np.float32))
        v = buf.view()
        assert v.flags.c_contiguous
        np.testing.assert_array_equal(v, np.arange(12, 20, dtype=np.float32))

    def test_chunks_are_copied_in_callers_do_not_alias_storage(self):
        """Appending must COPY: mutating the caller's array afterwards
        must not corrupt the stored recording (and vice versa)."""
        buf = GrowableRecordingBuffer(maxlen=None, nominal_sample_rate=16000)
        chunk = np.ones(8, dtype=np.float32)
        buf.append(chunk)
        chunk[:] = 9.0
        assert np.all(buf.view() == 1.0)
        stored = buf.view()
        stored[:] = 7.0  # callers treat views read-only, but pin isolation
        assert np.all(buf.view() == 7.0)


# ── 3. invalidation on sample-rate change ────────────────────────────────


class _FakeRecorderForSnapshot:
    """Minimal recorder double exposing what take_snapshot reads."""

    def __init__(self, *, effective_sr: int, target_sr: int = 16000) -> None:
        # STATE-OWNERSHIP: the buffer / lock / buffer-side sample
        # rate live on the owning ``AudioPipeline`` (production reads
        # them via ``recorder._audio_pipeline.<attr>``). This fake plays
        # both roles, so self-delegate the pipeline attribute — the
        # ``take_snapshot`` friend-access path resolves back to the
        # attributes below.
        self._audio_pipeline = self
        self.config = MagicMock(sample_rate=target_sr)
        self._buffer_sr = effective_sr
        self._effective_sr = effective_sr
        self._cached_target_sr = target_sr
        self._cached_resampled = np.array([], dtype=np.float32)
        self._cached_resampled_len = 0
        self._cached_native_chunk_count = 0
        self._cached_resample_key = ()
        self._cached_no_resample_len = -1
        self._cached_no_resample_arr = None
        self._cached_no_resample_segments = []
        self._cached_no_resample_concat_dirty = False
        self._cached_resampled_segments = []
        self._cached_resampled_concat_dirty = False
        self._lock = threading.Lock()
        self.resample_calls: list[tuple[int, int]] = []
        self.buffer = GrowableRecordingBuffer(maxlen=None, nominal_sample_rate=target_sr)
        self._buffer = self.buffer

    def _resample_chunk(self, audio, effective_sr, target_sr):
        self.resample_calls.append((int(effective_sr), int(target_sr)))
        step = max(1, effective_sr // target_sr)
        return (audio[::step] * 1.0).astype(np.float32, copy=False).reshape(-1)


class TestResampleCacheInvalidationOnRateChange:
    @pytest.fixture(autouse=True)
    def _route_resample_through_fake(self, monkeypatch):
        """Route the module-level ``resample_chunk`` binding (the
        historical ``Recorder._resample_chunk`` delegator was removed)
        through the fake recorder's recording stub."""

        def _route(recorder, audio, effective_sr, target_sr):
            return recorder._resample_chunk(audio, effective_sr, target_sr)

        monkeypatch.setattr("voice_typer.server.recording._recorder_split.resample_chunk", _route)

    def test_src_rate_change_invalidates_and_rebuilds_cache(self):
        rec = _FakeRecorderForSnapshot(effective_sr=48000)
        rec._audio_pipeline._buffer.append(np.full((6, 1), 1.0, dtype=np.float32))
        first = take_snapshot(rec)
        assert first.size == 2  # 48k → 16k decimation by 3
        calls_after_first = len(rec.resample_calls)

        # Same rate → incremental: appending new samples resamples ONLY
        # the new tail.
        rec._audio_pipeline._buffer.append(np.full((6, 1), 2.0, dtype=np.float32))
        second = take_snapshot(rec)
        assert second.size == 4
        assert len(rec.resample_calls) == calls_after_first + 1

        # Rate CHANGE mid-session: the cache must be discarded and the
        # whole window re-resampled under the new key.
        rec._audio_pipeline._buffer_sr = 8000
        third = take_snapshot(rec)
        assert rec._cached_resample_key == ("float32", 8000, 16000)
        # 12 samples @8k → decimation by max(1, 8000//16000)=1 → 12 out.
        assert third.size == 12
        assert rec._cached_native_chunk_count == 12
        assert rec._cached_resampled_len == 12

    def test_invalidated_cache_array_is_securely_zeroed(self):
        rec = _FakeRecorderForSnapshot(effective_sr=48000)
        rec._audio_pipeline._buffer.append(np.full((6, 1), 1.0, dtype=np.float32))
        take_snapshot(rec)
        old_capacity_array = rec._cached_resampled
        old_len = rec._cached_resampled_len
        assert old_len > 0

        rec._audio_pipeline._buffer_sr = 44100  # force key change
        take_snapshot(rec)
        # The superseded cache memory must be zeroed (SEC-audit-008) —
        # check the OLD backing array object, not the freshly rebuilt one.
        assert np.all(old_capacity_array[:old_len] == 0), (
            "cache invalidation must zero the superseded resampled cache in-place before it is replaced."
        )


# ── 4. stop contiguity equals historical concatenation ───────────────────


def _make_mock_recorder_for_stop(chunks: list[np.ndarray], *, buffer_sr: int = 16000):
    rec = MagicMock(name="recorder")
    rec._recording_event = threading.Event()
    rec._recording_event.set()
    rec._stop_generation = 0
    rec._user_stop_pending = False
    rec._audio_pipeline._lock = threading.Lock()
    rec._devices._mic_watcher = None
    buf = GrowableRecordingBuffer(maxlen=30000, nominal_sample_rate=16000)
    for c in chunks:
        buf.append(c)
    rec._audio_pipeline._buffer = buf
    rec._audio_pipeline._chunk_count = len(chunks)
    rec._audio_pipeline._buffer_sr = buffer_sr
    rec._effective_sr = buffer_sr
    rec._last_rms = 0.0
    rec._last_audio_stats = (0.0, 0.0, 0.0)
    rec._audio_pipeline._total_buffered_samples = buf.total_samples
    return rec


class TestStopContiguityGoldenSine:
    def test_stop_returns_bit_exact_concatenation_equivalent(self):
        """Golden comparison: the audio returned by ``stop_recording``
        must be bit-equal to the historical
        ``np.concatenate(list(deque)).reshape(-1)`` semantics."""
        chunks = [
            _sine(440.0, 512),
            _sine(880.0, 387),  # odd size on purpose
            (_sine(220.0, 256) * -1.0),
        ]
        golden = np.concatenate([c.reshape(-1) for c in chunks])

        rec = _make_mock_recorder_for_stop([c.copy() for c in chunks])
        audio = stop_recording(rec)

        assert audio.dtype == np.float32
        assert audio.flags.c_contiguous
        assert audio.flags.owndata
        assert audio.size == golden.size
        np.testing.assert_array_equal(audio, golden)

    def test_stop_across_doubling_boundary_is_continuous(self):
        """A session long enough to cross several growth reallocs must
        still produce one gap-free recording."""
        chunks = [_sine(440.0 + i * 10, 400) for i in range(12)]
        golden = np.concatenate(chunks)
        rec = _make_mock_recorder_for_stop(chunks)
        buf = rec._audio_pipeline._buffer
        # Force tiny initial capacity so appends cross doubling boundaries.
        buf._initial_capacity_samples = 512
        buf.set_hard_cap(1 << 40)
        audio = stop_recording(rec)
        np.testing.assert_array_equal(audio, golden)

    def test_stop_returns_audio_independent_of_background_zeroing(self):
        """The returned audio must be an owning copy: enqueueing the old
        storage for background zeroing AFTER the export must never touch
        it (stop()-race SEC fix preserved)."""
        golden = _sine(440.0, 64)
        rec = _make_mock_recorder_for_stop([golden.copy()])
        audio = stop_recording(rec)
        # Drain the shared clear worker so any enqueued zeroing ran.
        from voice_typer.server.recording.buffer import _stop_buffer_clear_worker

        _stop_buffer_clear_worker(timeout=2.0)
        assert audio.flags.owndata
        np.testing.assert_array_equal(audio, golden)


# ── 5. discard / stop zeroing ────────────────────────────────────────────


class TestDiscardAndStopZeroing:
    def test_discard_swaps_fresh_buffer_and_zeroes_old_storage(self):
        rec = MagicMock(name="recorder")
        rec._recording_event = threading.Event()
        rec._recording_event.set()
        rec._stop_generation = 0
        rec._user_stop_pending = False
        rec._audio_pipeline._lock = threading.Lock()
        rec._stream_lifecycle._stream = MagicMock()
        rec._teardown_stream = MagicMock()
        rec._stop_audio_worker = MagicMock()
        rec._capture.stop_event_worker_body = MagicMock()
        rec._stop_device_health_checker = MagicMock()
        rec._session_state.secure_clear_caches = MagicMock()
        rec._devices._mic_watcher = None
        rec._effective_sr = 16000
        rec._last_rms = 0.0
        rec._silence_timer = 0.0
        rec._silence_start_time = None
        rec._silence_warning_count = 0
        rec._silence_next_warning_wait = 10.0
        rec._note_buffer_capacity_eviction = MagicMock()

        buf = GrowableRecordingBuffer(maxlen=30000, nominal_sample_rate=16000)
        buf.append(np.full(64, 0.25, dtype=np.float32))
        storage_ref = buf.storage
        rec._audio_pipeline._buffer = buf
        rec._audio_pipeline._total_buffered_samples = 64

        discard_recording(rec)

        # Fresh empty container swapped in, same maxlen preserved.
        assert rec._audio_pipeline._buffer is not buf
        assert isinstance(rec._audio_pipeline._buffer, GrowableRecordingBuffer)
        assert len(rec._audio_pipeline._buffer) == 0
        assert rec._audio_pipeline._buffer.maxlen == 30000
        assert rec._audio_pipeline._total_buffered_samples == 0

        # Background worker must zero the OLD storage in-place.
        from voice_typer.server.recording.buffer import _stop_buffer_clear_worker

        _stop_buffer_clear_worker(timeout=2.0)
        assert np.all(storage_ref == 0), (
            "SEC-audit-008: discard() must zero the old contiguous storage via the background secure-clear worker."
        )

    def test_legacy_container_normalized_on_first_use(self):
        """A plain deque (hot-swap swap-in) must be transparently replaced
        by a growable buffer carrying over its content."""
        import collections

        rec = _FakeRecorderForSnapshot(effective_sr=16000)
        legacy = collections.deque(maxlen=12345)
        legacy.append(np.full(4, 3.0, dtype=np.float32))
        rec._audio_pipeline._buffer = legacy

        snap = take_snapshot(rec)

        assert isinstance(rec._audio_pipeline._buffer, GrowableRecordingBuffer)
        assert rec._audio_pipeline._buffer.maxlen == 12345
        assert len(rec._audio_pipeline._buffer) == 1
        assert rec._audio_pipeline._buffer.total_samples == 4
        np.testing.assert_array_equal(snap, np.full(4, 3.0, dtype=np.float32))

        # Subsequent appends land in the installed buffer.
        rec._audio_pipeline._buffer.append(np.full(2, 5.0, dtype=np.float32))
        assert rec._audio_pipeline._buffer.total_samples == 6
