"""XZ-8 regression test: ``take_snapshot`` must return a VIEW, not a copy."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording import Recorder

REPO_ROOT_CONTEXT = "voice_typer.server.recording"  # noqa: N816 (readability)


def _make_recorder(sample_rate: int = 16000, effective_sr: int | None = None) -> Recorder:  # type: ignore[name-defined]
    """Build a minimal ``Recorder`` for snapshot-view tests."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    r = make_fake_recorder(sample_rate=sample_rate)
    r._recording_event.set()
    r._effective_sr = effective_sr if effective_sr is not None else sample_rate
    r._post_filter_sr = r._effective_sr
    r._stream_lifecycle._stream = MagicMock()
    return r


class TestSnapshotResamplePathReturnsView:
    """
    XZ-8: snapshot() on the resample path (effective_sr != target_sr)
    Post-XV-22-fix (NEW-PERF-003): returns ``self._cached_resampled[:]``
    """

    def test_no_new_chunks_returns_view_of_cached_prefix(self, monkeypatch):
        """copy). This is the common case for the 4 Hz streaming poll."""

        def fake_resample(audio, effective_sr, target_sr):
            return audio[:: max(1, effective_sr // target_sr)].astype(np.float32, copy=False)

        def _route(recorder, audio, effective_sr, target_sr):
            return fake_resample(audio, effective_sr, target_sr)

        r = _make_recorder(sample_rate=16000, effective_sr=48000)
        monkeypatch.setattr("voice_typer.server.recording._recorder_split.resample_chunk", _route)

        r._audio_pipeline._buffer = [np.ones((6, 1), dtype=np.float32)]

        # First snapshot populates the cache.
        first = r.snapshot()
        # Contiguous storage: ``_cached_resampled`` owns a grown capacity
        cached_after_first = r._cached_resampled[: r._cached_resampled_len]

        # Second snapshot, no new chunks → must return a VIEW of the cache.
        second = r.snapshot()

        assert np.shares_memory(second, r._cached_resampled), (
            "XZ-8: snapshot() on the resample path (no new chunks) must "
            "return a VIEW of _cached_resampled, not a copy. Got an array "
            f"that does not share memory with the cache (owndata="
            f"{second.flags.owndata}). XV-22 regression: returning "
            f".copy() would re-introduce ~14 GB/30-min of garbage."
        )
        # And the values must match (the view isn't pointing at stale data).
        np.testing.assert_array_equal(second, cached_after_first)
        np.testing.assert_array_equal(second, first)

    def test_with_new_chunks_returns_view_of_cached_prefix(self, monkeypatch):
        """When new chunks HAVE arrived, snapshot() resamples the new"""
        calls: list[int] = []

        def fake_resample(audio, effective_sr, target_sr):
            calls.append(len(audio))
            return audio[:: max(1, effective_sr // target_sr)].astype(np.float32, copy=False)

        def _route(recorder, audio, effective_sr, target_sr):
            return fake_resample(audio, effective_sr, target_sr)

        r = _make_recorder(sample_rate=16000, effective_sr=48000)
        monkeypatch.setattr("voice_typer.server.recording._recorder_split.resample_chunk", _route)

        r._audio_pipeline._buffer = [np.ones((6, 1), dtype=np.float32)]
        r.snapshot()  # populate cache

        # Append a new chunk → next snapshot must resample + concatenate.
        r._audio_pipeline._buffer.append(np.ones((6, 1), dtype=np.float32))

        snap = r.snapshot()
        cached = r._cached_resampled

        # The return value must be a view of the (extended) cache.
        assert np.shares_memory(snap, cached), (
            "XZ-8: snapshot() on the resample path (with new chunks) must "
            "return a VIEW of the extended _cached_resampled, not a copy. "
            f"Got owndata={snap.flags.owndata}."
        )
        # Contiguous storage: the filled prefix length is tracked
        assert snap.size == r._cached_resampled_len
        assert snap.size > 0
        # Only the new chunk should have been resampled (cache hit on prefix).
        assert len(calls) == 2, (
            f"Expected exactly 2 resample calls (one per snapshot with new "
            f"chunks), got {len(calls)}. Cache prefix should suppress "
            f"re-resampling of already-cached chunks."
        )

    def test_resample_failure_returns_view_of_cached_prefix(self, monkeypatch):
        """When the resample of a new chunk FAILS, snapshot() must"""
        from voice_typer.server.recording.exceptions import ResampleError

        call_count = [0]

        def fake_resample(audio, effective_sr, target_sr):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise ResampleError("simulated decoder failure")
            return audio[:: max(1, effective_sr // target_sr)].astype(np.float32, copy=False)

        def _route(recorder, audio, effective_sr, target_sr):
            return fake_resample(audio, effective_sr, target_sr)

        r = _make_recorder(sample_rate=16000, effective_sr=48000)
        monkeypatch.setattr("voice_typer.server.recording._recorder_split.resample_chunk", _route)

        r._audio_pipeline._buffer = [np.ones((6, 1), dtype=np.float32)]
        r.snapshot()  # populate cache (call_count → 1)

        # Append a new chunk whose resample will fail (call_count → 2).
        r._audio_pipeline._buffer.append(np.ones((6, 1), dtype=np.float32))

        snap = r.snapshot()
        # The return must still be a view of the (unchanged) cache.
        assert np.shares_memory(snap, r._cached_resampled), (
            "XZ-8: snapshot() on the resample-failure path must return a VIEW of _cached_resampled, not a copy."
        )


class TestSnapshotNoResamplePathReturnsView:
    """XZ-8: snapshot() on the no-resample path (effective_sr =="""

    def test_no_new_chunks_returns_view_of_cached_array(self):
        """return must be a view of the contiguous recording buffer's"""
        r = _make_recorder(sample_rate=16000, effective_sr=16000)
        r._audio_pipeline._buffer = [np.array([[1.0], [2.0], [3.0]], dtype=np.float32)]

        first = r.snapshot()  # storage is contiguous now; nothing to build
        cached = r._audio_pipeline._buffer.storage
        assert cached is not None

        # Second snapshot, no new chunks → must hit the cache and return a view.
        second = r.snapshot()

        assert np.shares_memory(second, cached), (
            "XZ-8: snapshot() on the no-resample path (no new chunks) must "
            "return a VIEW of _buffer.storage, not a copy. Got "
            f"owndata={second.flags.owndata}."
        )
        np.testing.assert_array_equal(second, first)
        np.testing.assert_array_equal(second, np.array([1.0, 2.0, 3.0], dtype=np.float32))

    def test_with_new_chunks_returns_view_of_extended_storage(self):
        """storage, not a copy."""
        r = _make_recorder(sample_rate=16000, effective_sr=16000)
        r._audio_pipeline._buffer = [np.array([[1.0], [2.0]], dtype=np.float32)]
        r.snapshot()

        # Append a new chunk → storage may reallocate; snapshot extends.
        r._audio_pipeline._buffer.append(np.array([[3.0]], dtype=np.float32))
        snap = r.snapshot()
        cached = r._audio_pipeline._buffer.storage

        assert cached is not None
        assert np.shares_memory(snap, cached), (
            "XZ-8: snapshot() on the no-resample path (with new chunks) "
            "must return a VIEW of the extended _buffer.storage, "
            f"not a copy. Got owndata={snap.flags.owndata}."
        )
        np.testing.assert_array_equal(snap, np.array([1.0, 2.0, 3.0], dtype=np.float32))


class TestSnapshotEmptyBufferReturnsFreshEmptyArray:
    """XZ-8: the empty-buffer fast path returns a fresh empty array."""

    def test_empty_buffer_returns_empty_float32(self):
        r = _make_recorder(sample_rate=16000, effective_sr=16000)
        # No buffer set → deque is empty.
        snap = r.snapshot()
        assert snap.dtype == np.float32
        assert snap.size == 0
        # Must be a fresh array, not a view into any cache.
        assert snap.flags.owndata


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
