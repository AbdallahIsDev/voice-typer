"""Extracted helpers for :mod:`.recorder` — partial split of the Recorder god-class.

This module is the first step of the PVT-006 / god-class decomposition. The
``Recorder`` class in :mod:`.recorder` mixed 7+ disjoint concerns in a single
~3000-line class. Phase 4.5 / ARCH-045 had already extracted
:mod:`.device_manager`, :mod:`.resampling`, :mod:`.exceptions`, and
:mod:`.buffer`; this module continues the split by moving the two largest
tail-of-file methods (``snapshot`` and ``discard``) out of ``recorder.py``
into focused helper functions here.

The functions take the ``Recorder`` instance as their first argument (named
``recorder``) and access its private attributes / methods directly. This
preserves the existing runtime contract (same attributes read, same methods
called, same lock acquired) while shrinking ``recorder.py`` by ~200 lines.
``Recorder.snapshot`` and ``Recorder.discard`` become 1-line delegators so
existing call sites, subclass overrides, and ``inspect.getsource`` checks
that look for the method on the ``Recorder`` class continue to work.

The full split plan (to be completed once parallel surgical fixes to
``recorder.py`` land) is:

  - ``recorder/capture.py`` — audio callback + worker loop
    (``_audio_callback_dispatch``, ``_audio_worker_loop``,
    ``_event_worker_loop``, ``_start_audio_worker``, ``_stop_audio_worker``,
    ``_start_event_worker``, ``_stop_event_worker``).
  - ``recorder/lifecycle.py`` — stream lifecycle
    (``start``, ``stop``, ``discard``, ``_teardown_stream``). ``discard``
    is extracted here as the first step.
  - ``recorder/device_management.py`` — the 12 device methods (most already
    delegated to :class:`.device_manager.DeviceManager`).
  - ``recorder/format.py`` — audio format helpers
    (``snapshot``, ``_resample_chunk``, ``_prepare_audio``,
    ``_resample_audio_impl``, ``_ensure_mono``). ``snapshot`` is extracted
    here as the first step.
  - ``recorder/worker_threads.py`` — worker-thread management.

The conversion from ``recorder.py`` (module) to ``recorder/`` (package) is
deferred until all in-flight surgical fixes to specific ``recorder.py``
line ranges have landed, to avoid line-number conflicts with parallel
sub-agents.
"""

from __future__ import annotations

import collections
import itertools
import logging
from typing import TYPE_CHECKING

import numpy as np

from .exceptions import ResampleError

if TYPE_CHECKING:
    from .recorder import Recorder

log = logging.getLogger("voice_typer.server.recording")


def take_snapshot(recorder: Recorder) -> np.ndarray:
    """Return current recorded audio without clearing the active buffer.

    Extracted verbatim from ``Recorder.snapshot`` (PVT-006 split). Uses a
    cached resampled prefix to avoid O(n²) resampling on every call. Only
    new chunks since the last snapshot are resampled, then concatenated
    with the cached prefix.

    PERF-NEW-002 / PERF-NEW-003: previously this called
    ``list(self._buffer)[start:]`` which allocated a full list copy of the
    deque on every snapshot (20K allocs/sec under sustained recording).
    Replaced with ``itertools.islice`` which is O(1) in the deque size and
    avoids the intermediate list. Also avoided the O(n)
    ``np.concatenate([cached, new])`` allocation when there's nothing new
    to add.

    NEW-PERF-003: when no new chunks have arrived since the last snapshot
    (the common case for the streaming thread polling at 4 Hz), return a
    VIEW into the cached array instead of a full copy. The streaming
    caller only reads the array and slices it (which produces another
    view); it never mutates the data. The cache is replaced (not mutated
    in place) when new chunks arrive, so existing views remain valid until
    their references are released. This eliminates ~7,200 × 1.9 MB = ~14
    GB of garbage allocation per 30-minute recording session.

    NEW-PERF-007: avoid acquiring ``recorder._lock`` at all when the buffer
    is empty. The streaming thread polls at 4 Hz; if the recorder hasn't
    started yet (or just stopped), each poll would contend with the audio
    callback's lock acquisition for no reason. The lock-free
    ``len(recorder._buffer)`` check is safe because:
    - ``len()`` on a collections.deque is atomic in CPython.
    - If the buffer transitions from empty → non-empty between our check
      and the lock acquisition, the locked path handles it correctly
      (returns the new chunk).
    - If the buffer transitions from non-empty → empty (e.g. stop()
      called), the locked path returns the empty-array early-out. No
      correctness issue.
    """
    # NEW-PERF-007: lock-free fast path for the empty-buffer case.
    # Avoids 4 Hz lock contention with the audio callback thread when the
    # recorder isn't actively recording.
    if not recorder._buffer:
        return np.array([], dtype=np.float32)
    with recorder._lock:
        if not recorder._buffer:
            return np.array([], dtype=np.float32)
        effective_sr = recorder._effective_sr
        # PERF-NEW-021: read the cached target_sr instead of
        # recorder.config.sample_rate to avoid attribute lookup under lock.
        target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate

        # ARCH-040: invalidate the cache if any of the parameters that
        # affect the resampled output have changed since the last
        # snapshot. Without this, a dtype or sample-rate change
        # mid-session would return stale (and wrong-rate) cached audio.
        new_key = (
            str(recorder._buffer[0].dtype) if len(recorder._buffer) > 0 else "float32",
            effective_sr,
            target_sr,
        )
        if recorder._cached_resample_key != new_key:
            recorder._cached_resampled = np.array([], dtype=np.float32)
            recorder._cached_native_chunk_count = 0
            recorder._cached_resample_key = new_key
            # NEW-PERF-003: invalidate the no-resample cache too — a
            # sample-rate or dtype change invalidates both.
            recorder._cached_no_resample_len = -1
            recorder._cached_no_resample_arr = None

        if effective_sr != target_sr and len(recorder._buffer) > recorder._cached_native_chunk_count:
            # PERF-NEW-003: islice avoids the full-deque list copy. Only
            # the slice we actually need is materialized.
            new_chunks = list(
                itertools.islice(
                    recorder._buffer,
                    recorder._cached_native_chunk_count,
                    None,
                )
            )
            if new_chunks:
                new_audio = np.concatenate(new_chunks, axis=0).reshape(-1)
                # ERR-001: if resampling fails, drop the bad chunk rather
                # than appending native-rate audio that would corrupt the
                # streaming transcription.
                try:
                    new_resampled = recorder._resample_chunk(new_audio, effective_sr, target_sr)
                except ResampleError as e:
                    log.warning(
                        "[RECORDING] Snapshot resample failed; dropping %d native samples: %s",
                        len(new_audio),
                        e,
                    )
                    recorder._cached_native_chunk_count = len(recorder._buffer)
                    # NEW-PERF-003: return a view, not a copy.
                    return recorder._cached_resampled[:]
                # PERF-NEW-002: avoid the O(n) reallocation when the cached
                # prefix is empty (first snapshot of a session).
                if len(recorder._cached_resampled) > 0:
                    recorder._cached_resampled = np.concatenate([recorder._cached_resampled, new_resampled])
                else:
                    recorder._cached_resampled = new_resampled
                recorder._cached_native_chunk_count = len(recorder._buffer)
            # NEW-PERF-003: return a VIEW into the cache. The caller
            # (streaming.py) only reads + slices this array; it never
            # mutates. When the cache is later replaced by a new
            # np.concatenate(...) assignment, this view remains valid (numpy
            # keeps the underlying buffer alive until all views are
            # released). This eliminates the 1.9 MB copy on every 4 Hz poll
            # — ~14 GB of garbage per 30-min recording.
            return recorder._cached_resampled[:]
        elif effective_sr == target_sr:
            # No resampling needed, just concatenate all.
            # PERF-NEW-003: islice over the deque avoids the full list copy.
            # ``np.fromiter`` would be even faster but requires a flat
            # iterator; the deque holds 2D chunks so we still need one
            # concatenate.
            #
            # NEW-PERF-003: cache the no-resample concatenation too, so
            # repeated snapshots with no new chunks don't repeat the
            # concatenate. When chunks ARE new, we rebuild the cache. The
            # cache key is the buffer length — if it hasn't changed, the
            # cached array is still valid.
            buf_len = len(recorder._buffer)
            if (
                getattr(recorder, "_cached_no_resample_len", -1) == buf_len
                and recorder._cached_no_resample_arr is not None
            ):
                return recorder._cached_no_resample_arr[:]
            chunks = list(itertools.islice(recorder._buffer, 0, None))
            audio = np.concatenate(chunks, axis=0).reshape(-1)
            recorder._cached_no_resample_len = buf_len
            recorder._cached_no_resample_arr = audio
            return audio[:]
        else:
            # No new chunks, return cached.
            # NEW-PERF-003: return a VIEW, not a copy. See comment in the
            # resample branch above for why this is safe.
            return recorder._cached_resampled[:]


def discard_recording(recorder: Recorder) -> None:
    """Discard current recording without processing.

    Extracted verbatim from ``Recorder.discard`` (PVT-006 split). The
    constants ``_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S``,
    ``_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S``, and
    ``DEFAULT_MAX_BUFFER_CHUNKS`` are imported lazily from
    :mod:`.recorder` to avoid a circular import (recorder.py imports this
    module at the top of its class body).
    """
    # Lazy import: recorder.py is still loading when this module is first
    # imported (it imports this module near the top, before the constants
    # are defined). By deferring the import to call time we read the
    # constants from the fully-loaded module.
    from voice_typer.server import recording as _recording_pkg
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S,
        _EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S,
        DEFAULT_MAX_BUFFER_CHUNKS,
    )

    recorder._recording_event.clear()
    # STREAM-FIX (Task 6): set _user_stop_pending before stream.stop() so
    # the audio callback's early-return guard suppresses the false "Stream
    # finished unexpectedly" warning. The stop() path sets this flag;
    # discard() was missing it, so cancelling a recording via the Cancel
    # button still fired the warning. This mirrors the stop() contract:
    # any code path that intentionally stops the stream must set
    # _user_stop_pending first so the callback knows the stream end is
    # expected, not a crash.
    recorder._user_stop_pending = True
    # 17-H-FIX-2: increment stop_generation for symmetry with stop() so
    # any stale disconnect handler launched from the audio callback
    # (during discard's stream.stop()) bails out instead of racing with
    # the teardown — matching stop()'s HOTKEY-CRASH guard.
    recorder._stop_generation += 1
    # ARCH-021: guard _effective_sr reset with the lock so a concurrent
    # snapshot() reader sees a consistent value.
    with recorder._lock:
        recorder._effective_sr = recorder.config.sample_rate
    recorder._last_rms = 0.0
    recorder._silence_timer = 0.0
    recorder._silence_start_time = None
    recorder._silence_warning_count = 0
    recorder._silence_next_warning_wait = 10.0
    # G4-H-06: securely zero cached audio arrays BEFORE reassignment
    # (previously this just dropped the references, leaving the discarded
    # session's voice data in process memory). Factored into
    # ``_secure_clear_caches`` (shared with stop()'s two paths).
    recorder._secure_clear_caches()
    # 17-H-FIX-2: drain callback + stop + close via _teardown_stream()
    # (shared with stop()). The previous inline stream.stop()/close() here
    # had NO _is_in_audio_callback poll, risking use-after-free or deadlock
    # when ESC-cancel landed during a busy audio callback (which fires
    # ~16×/s). The helper polls for up to 300ms before close() and is
    # idempotent if the stream was already None.
    recorder._teardown_stream()
    # RT-SAFE-001: stop the audio worker thread. drain=False because
    # discard() doesn't need the in-flight audio — it's about to clear
    # recorder._buffer anyway. The worker clears the ring buffer and exits
    # after its current chunk (if any). Any chunk the worker appends to
    # recorder._buffer before exiting is cleared below.
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # RW-8: stop the IPC event worker with drain=False — the recording was
    # cancelled, so queued IPC events (e.g. audio_clip from the discarded
    # audio) don't need to be published. The queue is cleared so the
    # worker exits promptly.
    recorder._stop_event_worker(timeout=_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # CPU-03: stop the device health checker thread (mirrors the event worker).
    recorder._stop_device_health_checker()
    with recorder._lock:
        # MEM-04 / SEC-audit-008: defer buffer zeroing to background daemon
        # thread so discard() returns immediately (the secure clear happens
        # off the hot path).
        _old_buffer = recorder._buffer
        recorder._buffer = collections.deque(
            maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
        )
        _recording_pkg._secure_clear_array_background(_old_buffer)
