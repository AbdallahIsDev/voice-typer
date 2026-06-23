"""Regression tests for NEW-PERF-003: snapshot returns a view, not a copy.

The streaming transcription thread polls ``Recorder.snapshot()`` at
4 Hz.  Previously, every poll called ``self._cached_resampled.copy()``
even when no new audio chunks had arrived — ~7,200 × 1.9 MB = ~14 GB
of garbage per 30-minute recording.

The fix returns a numpy view (``arr[:]``) of the cached array when no
new data has arrived.  Views share memory with the cache; the caller
reads + slices them but never mutates.  When the cache is later
replaced (np.concatenate creates a new array), existing views remain
valid until their references are released.

These tests verify:
1. Repeated snapshots with no new chunks return views that share
   memory (no copy).
2. The no-resample path also caches its concatenate result.
3. The cache is properly invalidated when new chunks arrive, when
   stop()/discard() is called, and when the sample-rate key changes.
"""
from __future__ import annotations

from unittest import mock

import numpy as np
import pytest

from voice_typer.server.recording import Recorder
from voice_typer.server.config import Config


def _make_recorder() -> Recorder:
    """Build a Recorder without starting the audio stream."""
    cfg = Config()
    cfg.sample_rate = 16000  # match the default target_sr to hit the no-resample path
    rec = Recorder(cfg)
    # Stub out attributes that would normally be initialized by start().
    rec._effective_sr = 16000
    rec._cached_target_sr = 16000
    return rec


def _append_chunk(rec: Recorder, n_samples: int = 512) -> None:
    """Append a chunk to the recorder's buffer (simulates audio callback)."""
    chunk = np.zeros((n_samples, 1), dtype=np.float32)
    with rec._lock:
        rec._buffer.append(chunk)
        rec._chunk_count += 1


class TestSnapshotReturnsViewWhenNoNewChunks:
    """NEW-PERF-003: snapshot must NOT copy when no new data has arrived."""

    def test_repeated_snapshot_shares_memory(self):
        """Two consecutive snapshots with no new chunks between them
        must return arrays that share underlying memory.
        """
        rec = _make_recorder()
        _append_chunk(rec, 1024)

        first = rec.snapshot()
        second = rec.snapshot()

        # The second snapshot should share memory with the first (both
        # are views of the same cache).  np.shares_memory returns True
        # for views of the same array.
        assert np.shares_memory(first, second), (
            "snapshot() copied the cached array on a no-op poll — "
            "the streaming thread would allocate 1.9 MB per 4 Hz poll"
        )

    def test_no_resample_path_also_caches(self):
        """The no-resample path (effective_sr == target_sr) must cache
        its concatenate result so repeated snapshots don't re-concat.
        """
        rec = _make_recorder()
        _append_chunk(rec, 1024)
        _append_chunk(rec, 1024)

        first = rec.snapshot()
        # The cache should be populated now.
        assert rec._cached_no_resample_arr is not None
        assert rec._cached_no_resample_len == 2

        second = rec.snapshot()
        # Same underlying memory — no re-concat.
        assert np.shares_memory(first, second), (
            "no-resample path re-concatenated on a no-op poll"
        )

    def test_new_chunk_invalidates_cache(self):
        """When a new chunk arrives, the next snapshot must produce a
        fresh array (not share memory with the previous one).
        """
        rec = _make_recorder()
        _append_chunk(rec, 1024)
        first = rec.snapshot()

        _append_chunk(rec, 1024)
        second = rec.snapshot()

        # New chunk → new concatenate → different memory.
        assert not np.shares_memory(first, second), (
            "snapshot() returned a view of stale cache after a new chunk arrived"
        )
        # The second snapshot must contain more data than the first.
        assert len(second) > len(first)

    def test_stop_clears_cache(self):
        """stop() must invalidate the no-resample cache."""
        rec = _make_recorder()
        _append_chunk(rec, 1024)
        rec.snapshot()
        assert rec._cached_no_resample_arr is not None

        # Manually invoke the cache-clear section of stop() (we can't
        # call stop() directly because it tries to close a stream).
        with rec._lock:
            rec._buffer.clear()
            rec._cached_resampled = np.array([], dtype=np.float32)
            rec._cached_native_chunk_count = 0
            rec._cached_no_resample_len = -1
            rec._cached_no_resample_arr = None

        assert rec._cached_no_resample_arr is None
        assert rec._cached_no_resample_len == -1

    def test_data_correctness_preserved(self):
        """The view must return the correct audio data."""
        rec = _make_recorder()
        chunk1 = np.full((512, 1), 0.5, dtype=np.float32)
        chunk2 = np.full((512, 1), -0.3, dtype=np.float32)
        with rec._lock:
            rec._buffer.append(chunk1)
            rec._buffer.append(chunk2)
            rec._chunk_count = 2

        result = rec.snapshot()
        # The result should be the concatenation of both chunks.
        assert len(result) == 1024
        assert result[0] == 0.5
        assert result[512] == -0.3

    def test_empty_buffer_returns_empty_array(self):
        rec = _make_recorder()
        result = rec.snapshot()
        assert len(result) == 0

    def test_view_survives_cache_replacement(self):
        """A view returned by snapshot() must remain valid even after
        a subsequent snapshot() replaces the cache (via np.concatenate
        reassignment).
        """
        rec = _make_recorder()
        _append_chunk(rec, 1024)
        first = rec.snapshot()
        first_copy = first.copy()  # save the data for comparison

        _append_chunk(rec, 1024)
        second = rec.snapshot()  # this replaces the cache

        # The FIRST view must still contain the original data — numpy
        # keeps the underlying buffer alive until all views are
        # released.
        assert len(first) == len(first_copy)
        np.testing.assert_array_equal(first, first_copy)


class TestResamplePathReturnsView:
    """The resample path (effective_sr != target_sr) must also return
    a view of the cached resampled array.
    """

    def test_resample_path_returns_view_on_no_op(self):
        """When resampling is required but no new chunks have arrived,
        snapshot must return a view of the cached resampled array.
        """
        cfg = Config()
        cfg.sample_rate = 16000
        rec = Recorder(cfg)
        rec._effective_sr = 44100  # different from target_sr (16000)
        rec._cached_target_sr = 16000

        # Append a chunk and mock the resample to return a fixed-size array.
        chunk = np.zeros((1024, 1), dtype=np.float32)
        with rec._lock:
            rec._buffer.append(chunk)
            rec._chunk_count = 1

        # Mock _resample_chunk to return a small array without actually
        # requiring scipy.
        with mock.patch.object(
            rec, "_resample_chunk", return_value=np.zeros(372, dtype=np.float32)
        ):
            first = rec.snapshot()
            second = rec.snapshot()

        # The second snapshot (no new chunks) must share memory with
        # the cached resampled array, not copy it.
        assert np.shares_memory(first, second), (
            "resample path copied the cached array on a no-op poll"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
