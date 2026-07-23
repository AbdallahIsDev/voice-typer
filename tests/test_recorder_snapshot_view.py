"""XZ-8 regression test: ``take_snapshot`` must return a VIEW, not a copy.

Finding XV-22 (audit): the snapshot function was alleged to do an
``O(N) re-copy of cached prefix on every snapshot (~200GB memcpy over
30min session)``.

Verification-gate outcome (2026-07-24):
  ``voice_typer/server/recording/_recorder_split.py::take_snapshot``
  already returns a VIEW into the cached prefix array on every code
  path — it uses ``cached[:]`` (a NumPy basic slice, which is a view
  sharing the underlying buffer) and NOT ``cached.copy()`` or
  ``np.array(cached)``. This was the NEW-PERF-003 optimisation: it
  eliminated ~7,200 × 1.9 MB ≈ 14 GB of garbage allocation per
  30-minute recording session that the previous ``.copy()``-on-every-
  snapshot behaviour produced.

These tests pin that property so a future refactor cannot silently
reintroduce the ``.copy()`` (or ``np.array(...)``, or
``np.concatenate(...)``-of-the-return) regression. Each test asserts
both:
  1. ``np.shares_memory(snapshot, recorder._cached_resampled)`` — the
     returned array is a view, not an owning copy.
  2. The snapshot VALUES are correct (so the view isn't pointing at
     stale / wrong data).

Note: the *intermediate* ``np.concatenate([cached, new_resampled])``
on the resample path (line ~170) and ``np.concatenate(chunks)`` on the
no-resample path (line ~201) still perform an O(N) re-copy of the
cached prefix when NEW chunks arrive. That is a separate, deeper
optimisation (geometric-growth / ring buffer) that is out of scope for
the XV-22 gate, which is specifically about whether the snapshot
*return value* is a copy. These tests do NOT pin the intermediate
concatenate behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_typer.server.recording import Recorder

REPO_ROOT_CONTEXT = "voice_typer.server.recording"  # noqa: N816 (readability)


def _make_recorder(sample_rate: int = 16000, effective_sr: int | None = None) -> Recorder:  # type: ignore[name-defined]
    """Build a minimal ``Recorder`` for snapshot-view tests.

    Mirrors the setup in ``tests/test_recording.py``'s
    ``test_snapshot_returns_audio_without_clearing_buffer``: a MagicMock
    config, ``_recording_event`` set, ``_effective_sr`` /
    ``_post_filter_sr`` primed, and a MagicMock ``_stream`` so ``stop()``
    doesn't try to talk to PortAudio.
    """
    config = MagicMock(sample_rate=sample_rate, microphone=None)
    r = Recorder(config)
    r._recording_event.set()
    r._effective_sr = effective_sr if effective_sr is not None else sample_rate
    # CR-15/CR-20: mirror _effective_sr (no audio_processor in these tests)
    r._post_filter_sr = r._effective_sr
    r._stream = MagicMock()
    return r


# ── Resample path: effective_sr != target_sr ──────────────────────


class TestSnapshotResamplePathReturnsView:
    """XZ-8: snapshot() on the resample path (effective_sr != target_sr)
    must return a VIEW into ``_cached_resampled``, never a fresh copy.

    Pre-XV-22-fix: ``snapshot()`` returned ``self._cached_resampled.copy()``
    on every call (~14 GB / 30-min session of garbage).
    Post-XV-22-fix (NEW-PERF-003): returns ``self._cached_resampled[:]``
    (a view). These tests pin the post-fix behaviour.
    """

    def test_no_new_chunks_returns_view_of_cached_prefix(self, monkeypatch):
        """When no new chunks have arrived since the last snapshot, the
        return value must be a view of ``_cached_resampled`` (not a
        copy). This is the common case for the 4 Hz streaming poll."""
        # Stub _resample_chunk so we don't need scipy.
        def fake_resample(audio, effective_sr, target_sr):
            return audio[:: max(1, effective_sr // target_sr)].astype(np.float32, copy=False)

        r = _make_recorder(sample_rate=16000, effective_sr=48000)
        monkeypatch.setattr(r, "_resample_chunk", fake_resample)

        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        # First snapshot populates the cache.
        first = r.snapshot()
        cached_after_first = r._cached_resampled

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
        """When new chunks HAVE arrived, snapshot() resamples the new
        chunks and concatenates them onto the cached prefix. The RETURN
        value must still be a VIEW of the (newly rebuilt) cache — not a
        copy of it."""
        calls: list[int] = []

        def fake_resample(audio, effective_sr, target_sr):
            calls.append(len(audio))
            return audio[:: max(1, effective_sr // target_sr)].astype(np.float32, copy=False)

        r = _make_recorder(sample_rate=16000, effective_sr=48000)
        monkeypatch.setattr(r, "_resample_chunk", fake_resample)

        r._buffer = [np.ones((6, 1), dtype=np.float32)]
        r.snapshot()  # populate cache

        # Append a new chunk → next snapshot must resample + concatenate.
        r._buffer.append(np.ones((6, 1), dtype=np.float32))

        snap = r.snapshot()
        cached = r._cached_resampled

        # The return value must be a view of the (rebuilt) cache.
        assert np.shares_memory(snap, cached), (
            "XZ-8: snapshot() on the resample path (with new chunks) must "
            "return a VIEW of the rebuilt _cached_resampled, not a copy. "
            f"Got owndata={snap.flags.owndata}."
        )
        # And the values must be correct: 2 chunks of 6 samples each,
        # decimated 48000→16000 (factor 3) → 2 samples per chunk → 4 total.
        assert snap.size == cached.size
        assert snap.size > 0
        # Only the new chunk should have been resampled (cache hit on prefix).
        assert len(calls) == 2, (
            f"Expected exactly 2 resample calls (one per snapshot with new "
            f"chunks), got {len(calls)}. Cache prefix should suppress "
            f"re-resampling of already-cached chunks."
        )

    def test_resample_failure_returns_view_of_cached_prefix(self, monkeypatch):
        """When the resample of a new chunk FAILS, snapshot() must
        still return a VIEW of the (unchanged) cached prefix. The
        ERR-001 path drops the bad chunk but must not switch to
        returning a copy."""
        from voice_typer.server.recording.exceptions import ResampleError

        call_count = [0]

        def fake_resample(audio, effective_sr, target_sr):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise ResampleError("simulated decoder failure")
            return audio[:: max(1, effective_sr // target_sr)].astype(np.float32, copy=False)

        r = _make_recorder(sample_rate=16000, effective_sr=48000)
        monkeypatch.setattr(r, "_resample_chunk", fake_resample)

        r._buffer = [np.ones((6, 1), dtype=np.float32)]
        r.snapshot()  # populate cache (call_count → 1)

        # Append a new chunk whose resample will fail (call_count → 2).
        r._buffer.append(np.ones((6, 1), dtype=np.float32))

        snap = r.snapshot()
        # The return must still be a view of the (unchanged) cache.
        assert np.shares_memory(snap, r._cached_resampled), (
            "XZ-8: snapshot() on the resample-failure path must return a "
            "VIEW of _cached_resampled, not a copy."
        )


# ── No-resample path: effective_sr == target_sr ───────────────────


class TestSnapshotNoResamplePathReturnsView:
    """XZ-8: snapshot() on the no-resample path (effective_sr ==
    target_sr) must return a VIEW, never a copy."""

    def test_no_new_chunks_returns_view_of_cached_array(self):
        """When the buffer length hasn't changed since the last
        snapshot, the return must be a view of
        ``_cached_no_resample_arr``."""
        r = _make_recorder(sample_rate=16000, effective_sr=16000)
        r._buffer = [np.array([[1.0], [2.0], [3.0]], dtype=np.float32)]

        first = r.snapshot()  # builds the no-resample cache
        cached = r._cached_no_resample_arr
        assert cached is not None

        # Second snapshot, no new chunks → must hit the cache and return a view.
        second = r.snapshot()

        assert np.shares_memory(second, cached), (
            "XZ-8: snapshot() on the no-resample path (no new chunks) must "
            "return a VIEW of _cached_no_resample_arr, not a copy. Got "
            f"owndata={second.flags.owndata}."
        )
        np.testing.assert_array_equal(second, first)
        np.testing.assert_array_equal(second, np.array([1.0, 2.0, 3.0], dtype=np.float32))

    def test_with_new_chunks_returns_view_of_rebuilt_array(self):
        """When new chunks arrive, the no-resample path rebuilds the
        cache via ``np.concatenate(chunks)``. The RETURN value must be
        a view of that rebuilt array, not a copy."""
        r = _make_recorder(sample_rate=16000, effective_sr=16000)
        r._buffer = [np.array([[1.0], [2.0]], dtype=np.float32)]
        r.snapshot()  # populate cache

        # Append a new chunk → cache is invalidated and rebuilt.
        r._buffer.append(np.array([[3.0]], dtype=np.float32))
        snap = r.snapshot()
        cached = r._cached_no_resample_arr

        assert cached is not None
        assert np.shares_memory(snap, cached), (
            "XZ-8: snapshot() on the no-resample path (with new chunks) "
            "must return a VIEW of the rebuilt _cached_no_resample_arr, "
            f"not a copy. Got owndata={snap.flags.owndata}."
        )
        np.testing.assert_array_equal(snap, np.array([1.0, 2.0, 3.0], dtype=np.float32))


# ── Empty-buffer fast path ────────────────────────────────────────


class TestSnapshotEmptyBufferReturnsFreshEmptyArray:
    """XZ-8: the empty-buffer fast path returns a fresh empty array.
    This is O(1) and does not involve the cached prefix at all, so the
    view-vs-copy question is moot — but pin the behaviour so the fast
    path doesn't accidentally start returning the cached array."""

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
