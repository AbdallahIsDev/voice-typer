"""Mid-recording snapshot extraction and the incremental resampled-view cache.

Owns :func:`take_snapshot` (the body of ``Recorder.snapshot``) and its
resampled-cache helpers. Tests patch this module's ``resample_chunk``
attribute, the owning-module seam (C-ARCH-2).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE
from voice_typer.server._lazy_import import lazy_module

from . import (
    buffer as _buffer_mod,  # owning module of the secure-clear helpers (C-ARCH-2)
    recording_buffer,
)
from .exceptions import ResampleError
from .format import resample_chunk

if TYPE_CHECKING:
    from .recorder import Recorder
    from .recording_buffer import GrowableRecordingBuffer

log = logging.getLogger("voice_typer.server.recording")

np = lazy_module("numpy")


def _invalidate_resampled_cache(recorder: Recorder, new_key: tuple) -> None:
    """Zero-and-reset the resampled-stream cache (dtype/src/dst change).

    Mirrors the historical invalidation block: the previous cache array is
    securely zeroed BEFORE being replaced (SEC-audit-008), the resample
    cursor returns to the start of the buffer, and the compat segment-list
    slots are reset. Caller holds ``recorder._audio_pipeline._lock``."""
    cached = getattr(recorder, "_cached_resampled", None)
    if cached is not None and cached.size > 0:
        _buffer_mod._secure_clear_array(cached)
    recorder._cached_resampled = np.array([], dtype=np.float32)
    recorder._cached_resampled_len = 0
    recorder._cached_native_chunk_count = 0
    recorder._cached_resample_key = new_key
    # Compat slots retained (pinned by tests): the segment lists are no
    recorder._cached_no_resample_len = -1
    recorder._cached_no_resample_arr = None
    recorder._cached_resampled_segments = []
    recorder._cached_resampled_concat_dirty = False
    recorder._cached_no_resample_segments = []
    recorder._cached_no_resample_concat_dirty = False


def _append_resampled_samples(recorder: Recorder, samples: np.ndarray) -> None:
    """Append freshly resampled samples to the incremental resampled cache.

    The cache array (``_cached_resampled``) OWNS a geometrically grown
    capacity buffer; ``_cached_resampled_len`` tracks the filled prefix.
    Growth reallocates by doubling and copies only the filled prefix —
    amortized O(1) per sample versus the historical rebuild-on-demand.
    Caller holds ``recorder._audio_pipeline._lock``."""
    m = int(samples.shape[0])
    if m == 0:
        return
    cur = recorder._cached_resampled
    k = int(recorder._cached_resampled_len)
    cap = cur.size if cur is not None else 0
    need = k + m
    if need > cap:
        initial = max(
            _AUDIO_BLOCKSIZE,
            recording_buffer._GROWABLE_BUFFER_INITIAL_CAPACITY_SECONDS * 16000,
        )
        new_cap = max(initial, 2 * cap, need)
        grown = np.empty(new_cap, dtype=np.float32)
        if k > 0:
            grown[:k] = cur[:k]
        grown[k:need] = samples
        recorder._cached_resampled = grown
    elif cur is not None:
        cur[k:need] = samples
    recorder._cached_resampled_len = need


def take_snapshot(recorder: Recorder) -> np.ndarray:
    """Return current recorded audio without clearing the active buffer.

    Contiguous-storage implementation: the recording lives in ONE growable
    float32 ndarray (:class:`GrowableRecordingBuffer` on
    ``recorder._audio_pipeline._buffer``), so both snapshot flavors are O(1):

      - no-resample path (the COMMON production path, an AudioProcessor
        resamples each chunk to 16 kHz before it is appended, so
        ``_buffer_sr == target_sr``): return a zero-copy VIEW over the
        buffer's filled region. Zero allocation, zero memcpy per 4 Hz poll.
      - resample path (raw native-rate audio whose rate differs from the
        target): newly arrived samples are resampled ONCE and appended to
        the incremental ``_cached_resampled`` capacity array (geometric
        growth, no segment lists, no rebuild-on-demand concat); return a
        view of its filled prefix.

    View contract (pinned by ``tests/test_recorder_snapshot_view.py``):
    every non-empty snapshot is a numpy VIEW sharing memory with its
    backing store, ``recorder._cached_resampled`` on the resample path,
    ``recorder._audio_pipeline._buffer.storage`` on the no-resample path. The cache arrays
    are REPLACED (never mutated in place) on growth/invalidation, so
    previously-returned views remain valid until released. streaming.py's
    zero-gate (zero the audio buffer before release) relies on exactly
    this identity.

    Lock discipline: unchanged from the deque implementation, a lock-free
    empty fast path (avoids 4 Hz contention with the audio callback when
    idle), then everything else under ``recorder._audio_pipeline._lock``, which serializes
    against the audio worker's append path.
    """
    # lock-free fast path for the empty-buffer case.
    if not recorder._audio_pipeline._buffer:
        return np.array([], dtype=np.float32)
    with recorder._audio_pipeline._lock:
        recording_buffer._ensure_growable_buffer(recorder)
        buf = recorder._audio_pipeline._buffer
        if not buf or buf.total_samples == 0:
            return np.array([], dtype=np.float32)
        # (defensive, should never happen because ``__init__`` and
        effective_sr = recorder._audio_pipeline._buffer_sr or recorder._effective_sr
        # read the cached target_sr instead of
        target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate

        # invalidate the cache if any of the parameters that
        new_key = (
            buf.first_dtype,
            effective_sr,
            target_sr,
        )
        if recorder._cached_resample_key != new_key:
            _invalidate_resampled_cache(recorder, new_key)

        if effective_sr != target_sr:
            return _snapshot_resampled_locked(recorder, buf, effective_sr, target_sr)
        # No resampling needed: the contiguous storage IS the snapshot.
        return buf.view()


def _snapshot_resampled_locked(
    recorder: Recorder,
    buf: GrowableRecordingBuffer,
    effective_sr: int,
    target_sr: int,
) -> np.ndarray:
    """Incremental-resample branch of :func:`take_snapshot`.

    Resamples ONLY the samples that arrived since the previous call
    (absolute cursor ``_cached_native_chunk_count``, repurposed from
    chunk-count to SAMPLE-count by the contiguous-storage change), appends
    them to the incremental cache, and returns a view over the cache's
    filled prefix. Caller holds ``recorder._audio_pipeline._lock``.
    """
    end_abs = buf.appended_samples_total
    start_abs = max(int(recorder._cached_native_chunk_count), buf.evicted_samples_total)
    cached_len = int(getattr(recorder, "_cached_resampled_len", 0))
    if end_abs <= start_abs:
        # Nothing new to resample. Return the current prefix view.
        return recorder._cached_resampled[:cached_len]
    raw_new = buf.sample_range(start_abs, end_abs)
    # if resampling fails, drop the bad chunk rather
    try:
        new_resampled = resample_chunk(recorder, raw_new, effective_sr, target_sr)
    except ResampleError as e:
        log.warning(
            "[RECORDING] Snapshot resample failed; dropping %d native samples: %s",
            len(raw_new),
            e,
        )
        recorder._cached_native_chunk_count = end_abs
        # return a view of the unchanged cached prefix.
        return recorder._cached_resampled[:cached_len]
    _append_resampled_samples(recorder, np.asarray(new_resampled, dtype=np.float32).reshape(-1))
    recorder._cached_native_chunk_count = end_abs
    # return a VIEW into the cache (``.base is _cached_resampled``) —
    return recorder._cached_resampled[: recorder._cached_resampled_len]
