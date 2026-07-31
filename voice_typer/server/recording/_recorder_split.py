"""Extracted helpers for :mod:`.recorder` — partial split of the Recorder god-class.

This module is the first step of the  / god-class decomposition. The
``Recorder`` class in :mod:`.recorder` mixed 7+ disjoint concerns in a single
~3000-line class. Phase 4.5 /  had already extracted
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
import time
from typing import TYPE_CHECKING, Any

import numpy as np

# ``retune_audio_processor`` consolidates the retune block that
# was duplicated between ``start_recording`` (below) and
# ``DisconnectHandler.restart_stream``.
from .disconnect_handler import retune_audio_processor
from .exceptions import ResampleError

if TYPE_CHECKING:
    from .recorder import Recorder

log = logging.getLogger("voice_typer.server.recording")


def _ensure_resampled_concat(recorder: Recorder) -> None:
    """Lazily materialize ``recorder._cached_resampled`` from the
    segment list.

    The resample path of :func:`take_snapshot` keeps the cached prefix as
    a *list* of resampled segments (``recorder._cached_resampled_segments``)
    and only re-concatenates them into a single contiguous ndarray when a
    caller actually needs one. This eliminates the O(N) re-copy of the
    cached prefix that previously happened on every snapshot with new
    chunks (``np.concatenate([cached, new_resampled])`` where ``cached``
    grew linearly with session length).

    This helper is a no-op when the segment list has not changed since
    the last materialization (``_cached_resampled_concat_dirty == False``)
    -- the existing ``_cached_resampled`` array stays valid and any
    previously-returned views into it remain correct.

    Post-condition: ``recorder._cached_resampled`` is a contiguous ndarray
    containing the concatenation of all segments in
    ``recorder._cached_resampled_segments`` (or an empty float32 array
    when the list is empty), and ``_cached_resampled_concat_dirty`` is
    ``False``.
    """
    if not recorder._cached_resampled_concat_dirty:
        return
    segments = recorder._cached_resampled_segments
    if not segments:
        recorder._cached_resampled = np.array([], dtype=np.float32)
    elif len(segments) == 1:
        # Avoid the np.concatenate overhead for the single-segment case
        # (common at the start of a session). The segment is already a
        # contiguous ndarray, so we can use it directly.
        recorder._cached_resampled = segments[0]
    else:
        recorder._cached_resampled = np.concatenate(segments)
    recorder._cached_resampled_concat_dirty = False


def _ensure_no_resample_concat(recorder: Recorder) -> None:
    """Lazily materialize ``recorder._cached_no_resample_arr`` from the
        no-resample segment list.

    mirrors :func:`_ensure_resampled_concat` for the no-resample
        branch of :func:`take_snapshot`. The cached prefix is kept as a
        *list* of segments (``recorder._cached_no_resample_segments``) — one
        per snapshot that saw new chunks — and only re-concatenated into a
        contiguous ndarray when a caller actually needs one. This eliminates
        the O(N) re-copy of the entire buffer that previously happened on
        every snapshot (``np.concatenate(chunks, axis=0).reshape(-1)`` where
        ``chunks`` was the full deque).

        This helper is a no-op when the segment list has not changed since
        the last materialization (``_cached_no_resample_concat_dirty ==
        False``) — the existing ``_cached_no_resample_arr`` array stays
        valid and any previously-returned views into it remain correct.

        Post-condition: ``recorder._cached_no_resample_arr`` is a contiguous
        ndarray containing the concatenation of all segments in
        ``recorder._cached_no_resample_segments`` (or an empty float32 array
        when the list is empty), and ``_cached_no_resample_concat_dirty`` is
        ``False``.
    """
    if not recorder._cached_no_resample_concat_dirty:
        return
    segments = recorder._cached_no_resample_segments
    if not segments:
        recorder._cached_no_resample_arr = np.array([], dtype=np.float32)
    elif len(segments) == 1:
        # Avoid the np.concatenate overhead for the single-segment case
        # (common at the start of a session). The segment is already a
        # contiguous ndarray, so we can use it directly.
        recorder._cached_no_resample_arr = segments[0]
    else:
        recorder._cached_no_resample_arr = np.concatenate(segments)
    recorder._cached_no_resample_concat_dirty = False


def take_snapshot(recorder: Recorder) -> np.ndarray:
    """Return current recorded audio without clearing the active buffer.

    Extracted verbatim from ``Recorder.snapshot`` ( split). Uses a
        cached resampled prefix to avoid O(n²) resampling on every call. Only
        new chunks since the last snapshot are resampled, then concatenated
        with the cached prefix.

    PERF- / PERF-: previously this called
        ``list(self._buffer)[start:]`` which allocated a full list copy of the
        deque on every snapshot (20K allocs/sec under sustained recording).
        Replaced with ``itertools.islice`` which is O(1) in the deque size and
        avoids the intermediate list. Also avoided the O(n)
        ``np.concatenate([cached, new])`` allocation when there's nothing new
        to add.

    when no new chunks have arrived since the last snapshot
        (the common case for the streaming thread polling at 4 Hz), return a
        VIEW into the cached array instead of a full copy. The streaming
        caller only reads the array and slices it (which produces another
        view); it never mutates the data. The cache is replaced (not mutated
        in place) when new chunks arrive, so existing views remain valid until
        their references are released. This eliminates ~7,200 × 1.9 MB = ~14
        GB of garbage allocation per 30-minute recording session.

    avoid acquiring ``recorder._lock`` at all when the buffer
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
    # lock-free fast path for the empty-buffer case.
    # Avoids 4 Hz lock contention with the audio callback thread when the
    # recorder isn't actively recording.
    if not recorder._buffer:
        return np.array([], dtype=np.float32)
    with recorder._lock:
        if not recorder._buffer:
            return np.array([], dtype=np.float32)
        # use ``_buffer_sr`` (the actual sample rate of the audio in
        # ``recorder._buffer``) instead of ``_effective_sr`` (the device's
        # native rate). When an AudioProcessor is active, ``process_chunk``
        # resamples to the chain's construction rate (typically 16 kHz)
        # before appending — so the buffer holds chain-rate audio. Using
        # ``_effective_sr`` here would cause ``_resample_chunk`` to
        # resample a second time (chain→target on top of the native→chain
        # resample already done by ``process_chunk``), which (a) wastes
        # CPU and (b) can introduce artifacts from the double resample.
        # Fall back to ``_effective_sr`` when ``_buffer_sr`` is 0/unset
        # (defensive — should never happen because ``__init__`` and
        # ``start()`` both initialize it). : use ``getattr`` with a
        # ``None`` default so a ``Recorder`` instance that hasn't yet
        # had ``_buffer_sr`` assigned (e.g. in unit tests that bypass
        # ``start()``) doesn't raise ``AttributeError`` — the comment's
        # stated intent is a fallback, and the ``or`` idiom only
        # short-circuits on falsy values, not on missing attributes.
        # ``None`` is falsy, so the fallback to ``_effective_sr`` fires.
        effective_sr = getattr(recorder, "_buffer_sr", None) or recorder._effective_sr
        # PERF-: read the cached target_sr instead of
        # recorder.config.sample_rate to avoid attribute lookup under lock.
        target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate

        # invalidate the cache if any of the parameters that
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
            # invalidate the no-resample cache too -- a
            # sample-rate or dtype change invalidates both.
            recorder._cached_no_resample_len = -1
            recorder._cached_no_resample_arr = None
            # invalidate the segment list + lazy-concat cache too.
            recorder._cached_resampled_segments = []
            recorder._cached_resampled_concat_dirty = False
            # invalidate the no-resample segment list + lazy-concat
            # cache too (mirror the resample-path invalidation).
            recorder._cached_no_resample_segments = []
            recorder._cached_no_resample_concat_dirty = False

        if effective_sr != target_sr and len(recorder._buffer) > recorder._cached_native_chunk_count:
            # PERF-: islice avoids the full-deque list copy. Only
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
                # if resampling fails, drop the bad chunk rather
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
                    # materialize the cached concat (no-op if clean)
                    # so the view we return points at the current prefix.
                    _ensure_resampled_concat(recorder)
                    # return a view, not a copy.
                    return recorder._cached_resampled[:]
                # append the new resampled segment to the segment
                # list (O(1)) instead of re-concatenating the entire
                # cached prefix (O(N) where N = total cached samples).
                # The concat is materialized lazily by
                # ``_ensure_resampled_concat`` below when the caller
                # actually needs a contiguous array. Snapshots that see
                # no new chunks reuse the cached concat (zero memcpy).
                recorder._cached_resampled_segments.append(new_resampled)
                recorder._cached_resampled_concat_dirty = True
                recorder._cached_native_chunk_count = len(recorder._buffer)
            # materialize the lazy concat if the segment list
            # changed since the last call. No-op when no new chunks
            # arrived -- the existing cached_resampled stays valid and
            # the view we return shares memory with it.
            _ensure_resampled_concat(recorder)
            # return a VIEW into the cache. The caller
            # (streaming.py) only reads + slices this array; it never
            # mutates. When the cache is later replaced by a new
            # np.concatenate(...) assignment, this view remains valid (numpy
            # keeps the underlying buffer alive until all views are
            # released). This eliminates the 1.9 MB copy on every 4 Hz poll
            # -- ~14 GB of garbage per 30-min recording.
            return recorder._cached_resampled[:]
        elif effective_sr == target_sr:
            # No resampling needed, just concatenate all.
            #
            # mirror the resample-path segment-list + lazy-concat
            # optimization. The previous implementation cached the
            # concatenation by ``buf_len`` only and missed on every
            # poll (the streaming thread polls at 4 Hz while the audio
            # worker appends at 16 Hz → ``buf_len`` always differs
            # between polls). The cache-miss path ran
            # ``np.concatenate(chunks, axis=0).reshape(-1)`` over the
            # full deque on every poll — rebuilding the full array
            # every poll, ~460 MB/s of memcpy + garbage allocation on
            # a 30-min 16 kHz mono dictation (~115 MB buffer). This is
            # the COMMON path because ``AudioProcessor`` resamples to
            # 16 kHz before appending (so ``_buffer_sr == target_sr``).
            #
            # We now keep the cached prefix as a *list* of segments
            # (one per snapshot that saw new chunks) and only
            # re-concatenate when the list changes (``_ensure_no_resample_concat``).
            # Snapshots that see no new chunks reuse the cached concat
            # (zero memcpy). When new chunks DO arrive, only the new
            # tail is materialized (via ``islice``) and appended; the
            # cached prefix is re-concatenated lazily.
            buf_len = len(recorder._buffer)
            cached_chunks = recorder._cached_no_resample_len
            # Detect invalidated / inconsistent cache state:
            #   * ``cached_chunks < 0``: set by ``_secure_clear_caches``
            #     (in the session_state module, which we cannot modify)
            #     / ``reset_session_state`` / the key-change invalidation
            #     block above. The segments list may still hold stale
            #     references (the secure-clear path zeros the concat
            #     array but doesn't touch the segment list — mirroring
            #     the resample-path's ``_cached_resampled_segments``
            #     treatment), so we drop them here on first use after
            #     invalidation.
            #   * ``cached_chunks > buf_len``: the buffer shrank
            #     (defensive — should not happen in normal operation
            #     because discard/stop replace the deque AND invalidate
            #     the cache, but guards against a future code path that
            #     clears the buffer without invalidating).
            if cached_chunks < 0 or cached_chunks > buf_len:
                if recorder._cached_no_resample_segments:
                    recorder._cached_no_resample_segments = []
                    recorder._cached_no_resample_concat_dirty = True
                cached_chunks = 0
            if buf_len > cached_chunks:
                # PERF-: islice avoids the full-deque list copy.
                # Only the new tail is materialized.
                new_chunks = list(
                    itertools.islice(
                        recorder._buffer,
                        cached_chunks,
                        None,
                    )
                )
                if new_chunks:
                    new_audio = np.concatenate(new_chunks, axis=0).reshape(-1)
                    # Append the new segment to the segment list (O(1))
                    # instead of re-concatenating the entire cached
                    # prefix (O(N) where N = total cached samples). The
                    # concat is materialized lazily by
                    # ``_ensure_no_resample_concat`` below when the
                    # caller actually needs a contiguous array.
                    recorder._cached_no_resample_segments.append(new_audio)
                    recorder._cached_no_resample_concat_dirty = True
            recorder._cached_no_resample_len = buf_len
            # materialize the lazy concat if the segment list changed
            # since the last call. No-op when no new chunks arrived --
            # the existing cached_no_resample_arr stays valid and the
            # view we return shares memory with it.
            _ensure_no_resample_concat(recorder)
            # return a VIEW into the cache. The caller
            # (streaming.py) only reads + slices this array; it never
            # mutates. When the cache is later replaced by a new
            # np.concatenate(...) assignment, this view remains valid
            # (numpy keeps the underlying buffer alive until all views
            # are released). This eliminates the per-poll copy.
            return recorder._cached_no_resample_arr[:]
        else:
            # No new chunks, return cached.
            # return a VIEW, not a copy. See comment in the
            # resample branch above for why this is safe.
            return recorder._cached_resampled[:]


def discard_recording(recorder: Recorder) -> None:
    """Discard current recording without processing.

    Extracted verbatim from ``Recorder.discard`` ( split). The
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
    # 17-H-: increment stop_generation for symmetry with stop() so
    # any stale disconnect handler launched from the audio callback
    # (during discard's stream.stop()) bails out instead of racing with
    # the teardown — matching stop()'s HOTKEY-CRASH guard.
    recorder._stop_generation += 1
    # guard _effective_sr reset with the lock so a concurrent
    # snapshot() reader sees a consistent value.
    with recorder._lock:
        recorder._effective_sr = recorder.config.sample_rate
        # reset ``_buffer_sr`` to ``None`` so the next session
        # starts clean (matches the ``_effective_sr`` reset above, but
        # uses ``None`` so stop()/snapshot() fall back to ``_effective_sr``
        # via the ``_buffer_sr or _effective_sr`` idiom until
        # ``_process_audio_chunk`` updates it on the first chunk).
        # Without this, a subsequent ``stop()``/``snapshot()`` would use
        # the previous session's chain rate as the resample source rate —
        # causing a wrong-rate resample on the first chunk of the new
        # session.
        recorder._buffer_sr = None
    recorder._last_rms = 0.0
    recorder._silence_timer = 0.0
    recorder._silence_start_time = None
    recorder._silence_warning_count = 0
    recorder._silence_next_warning_wait = 10.0
    # securely zero cached audio arrays BEFORE reassignment
    # (previously this just dropped the references, leaving the discarded
    # session's voice data in process memory). Factored into
    # ``_secure_clear_caches`` (shared with stop()'s two paths).
    recorder._secure_clear_caches()
    # 17-H-: drain callback + stop + close via _teardown_stream()
    # (shared with stop()). The previous inline stream.stop()/close() here
    # had NO _is_in_audio_callback poll, risking use-after-free or deadlock
    # when ESC-cancel landed during a busy audio callback (which fires
    # ~16×/s). The helper polls for up to 300ms before close() and is
    # idempotent if the stream was already None.
    recorder._teardown_stream()
    # stop the audio worker thread. drain=False because
    # discard() doesn't need the in-flight audio — it's about to clear
    # recorder._buffer anyway. The worker clears the ring buffer and exits
    # after its current chunk (if any). Any chunk the worker appends to
    # recorder._buffer before exiting is cleared below.
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # stop the IPC event worker with drain=False — the recording was
    # cancelled, so queued IPC events (e.g. audio_clip from the discarded
    # audio) don't need to be published. The queue is cleared so the
    # worker exits promptly.
    recorder._stop_event_worker(timeout=_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # CPU-03: stop the device health checker thread (mirrors the event worker).
    # Fire-and-forget (timeout=0.0): the device-health checker is a daemon
    # that sleeps 30s between probes, so joining it almost always times out.
    # Worse, the underlying ``DeviceManager._start_device_health_checker``
    # (in a sibling module we don't own) assigns the thread reference BEFORE
    # calling ``Thread.start()`` without holding a lock — a concurrent
    # discard() that calls ``_stop_device_health_checker()`` can observe the
    # not-yet-started thread and raise
    # ``RuntimeError("cannot join thread before it is started")`` when the
    # timing is tight (the sentinel-driven fast event-worker exit in
    # ``_stop_event_worker`` widened the race window enough to surface the
    # bug under the  hammer). Signalling the stop event without joining
    # eliminates the race: the daemon exits on its next ``wait()`` return
    # (≤30s), and the next ``start()`` sees ``is_alive()==False`` and spawns
    # a fresh checker. Mirrors the ``stop()`` path's fire-and-forget call.
    recorder._stop_device_health_checker(timeout=0.0)
    with recorder._lock:
        # SEC-audit-008: defer buffer zeroing to background daemon
        # thread so discard() returns immediately (the secure clear happens
        # off the hot path).
        _old_buffer = recorder._buffer
        recorder._buffer = collections.deque(
            maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
        )
        _recording_pkg._secure_clear_array_background(_old_buffer)


def start_recording(recorder: Recorder) -> None:
    """Body of :meth:`Recorder.start` (after the ``_start_lock`` permission-gate block).

    Phase 4.5 — extracted from :mod:`.recorder` to shrink the
        3772-LOC ``recorder.py`` god class. The ``with self._start_lock:``
        block (containing the recording-event check + microphone-permission
        pre-flight) stays on ``Recorder.start`` so the source-inspection
        test (``tests/test_recording.py::TestRec5StartLock``) continues to
        pin the lock contract.

        This function runs WITHOUT holding ``_start_lock`` — it is called
        after ``Recorder.start`` releases the lock. The lock contract is
        that ``start()`` and ``discard()`` cannot both pass the
        ``_recording_event.is_set()`` check at the same time, which the
        lock guarantees by serializing the gate.

    reset ALL per-session state here, not just the buffer.
        Previously some flags (_max_duration_warning_sent,
        _silence_warning_sent, etc.) persisted across recordings,
        causing stale state to suppress warnings on the next session.

    (revised): The dead ``_silence_warning_sent`` and
        ``_max_duration_warning_sent`` boolean flags have been REMOVED.
        They were declared and reset here but NEVER read in any
        conditional — the actual silence-warning state machine uses
        the integer counter ``_silence_warning_count`` (which IS read
        at recording.py:1109). The dead flags were misleading
        maintainers into thinking warning deduplication existed when
    it didn't — see FORENSIC_REVIEW_COMPLETE.md →

        SEC-audit-008: ``_secure_clear_array`` is now actually used
        here to zero cached audio arrays (``_cached_resampled`` and
        ``_cached_no_resample_arr``) before they're dropped. This
        prevents forensic recovery of audio data from process memory
        between sessions.

        CRITICAL — DO NOT RESTRUCTURE (2026-07-20)
        ========================================
        The device-enumeration block below (``last_error``,
        ``selected_device``, ``effective_sr``, the ``for candidate in
        candidates`` loop, the fallback loop, and the
        ``if recorder._stream is None:`` check) MUST stay at this
        function's body scope — OUTSIDE the ``callback`` closure built
        by ``recorder._build_audio_callback()`` above. A previous
        merge accidentally nested this block INSIDE the ``def
        callback()`` closure, which made ``last_error`` a local of
        ``callback`` instead of ``start()``, raising
        ``UnboundLocalError`` on every recording start.

        The device-loop bodies are extracted into
        ``recorder._open_stream_for_candidates`` /
        ``recorder._open_stream_fallback`` (both called from this
        function's scope, OUTSIDE the callback closure), so the
        structural contract above is preserved.

        DO NOT move device enumeration inside the callback closure.
        DO NOT re-add ``set_thread_registry`` — it was merge damage.
    """
    # Lazy import: ``recorder.py`` is still loading when this module
    # is first imported (it imports this module near the top, before
    # the constants are defined). By deferring the import to call
    # time we read the package namespace (mirrors ``discard_recording``).
    from voice_typer.server import recording as _recording_pkg

    # SEC-audit-008: securely zero cached
    # audio arrays before clearing. The cache-clearance block is
    # extracted into ``_secure_clear_session_caches`` so the
    # source-string regression test
    # (``test_recorder_start_except_clause_does_not_swallow_nameerror``)
    # can pin the narrowed handler clause at the helper-method
    # granularity. See the helper's docstring for the full rationale.
    recorder._secure_clear_session_caches()

    # per-session state reset () ──
    recorder._reset_session_state()

    # ── cache config-derived scalars for the audio callback hot path ──
    max_rec = recorder._cache_session_config()

    device = recorder._resolve_device()
    candidates = recorder._same_physical_microphone_candidates(device)

    # build the PortAudio callback closure () ──
    callback = recorder._build_audio_callback()

    # =====================================================================
    # CRITICAL — DO NOT RESTRUCTURE (2026-07-20)
    # =====================================================================
    # The device-enumeration block below (last_error, selected_device,
    # effective_sr, ``for candidate in candidates``, the fallback loop,
    # and the ``if recorder._stream is None:`` check) MUST stay at
    # this function's body scope — this 4-space indent level, OUTSIDE
    # the ``callback`` closure defined above.
    #
    # A previous merge accidentally nested this block INSIDE the
    # ``def callback()`` closure. That made ``last_error`` a local of
    # ``callback``, not ``start()``. When ``start()`` checked ``if
    # last_error is not None:``, Python raised:
    #     UnboundLocalError: cannot access local variable 'last_error'
    #     where it is not associated with a value
    # → recording start crashed on every attempt.
    #
    # The fallback loop (``for candidate in all_candidates``) was also
    # misplaced — trapped inside the preroll-buffer block instead of
    # ``if recorder._stream is None and not used_fallback:``.
    #
    # DO NOT move device enumeration inside the callback closure.
    # The device-loop bodies are now extracted into
    # ``_open_stream_for_candidates`` / ``_open_stream_fallback`` (both
    # called from this function's scope, OUTSIDE the callback closure),
    # so the structural contract above is preserved.
    # DO NOT re-add ``set_thread_registry`` — it was merge damage, not
    # in the original codebase, and referenced a function that did not
    # exist. The ``recording/__init__.py`` stub for it is dead code.
    # =====================================================================
    last_error: Exception | None = None
    selected_device: Any = None
    effective_sr: int = recorder.config.sample_rate
    used_fallback = False

    selected_device, effective_sr, last_error = recorder._open_stream_for_candidates(
        candidates, callback, effective_sr, last_error
    )

    # If all same-name candidates failed, try ALL available input devices
    if recorder._stream is None and not used_fallback:
        selected_device, effective_sr, used_fallback, last_error = recorder._open_stream_fallback(
            candidates, callback, effective_sr, last_error
        )

    if recorder._stream is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("No input device could be opened")

    # ── dynamic buffer sizing (deferred until effective_sr known) ──
    recorder._resize_buffers_for_sample_rate(effective_sr, max_rec)

    if selected_device != device and isinstance(selected_device, int):
        log.info(
            "[RECORDING] Selected microphone [%s] failed; using device [%s]",
            device,
            selected_device,
        )
        recorder.config.microphone = str(selected_device)
        # PERF-: persist the microphone-fallback update on
        # a background daemon thread so the 50-500 ms blocking
        # write doesn't stall the recording-start critical path.
        # The fallback is best-effort persistence — if the process
        # crashes before the write lands, the user just re-selects
        # the mic on next start.

        def _persist_mic() -> None:
            if not recorder.config.save():
                log.debug("[RECORDING] Could not persist microphone fallback")

        # use _spawn_device_thread so the persistence thread
        # is registered with thread_registry (when available),
        # allowing ``shutdown_all()`` to join it during process exit
        # (preventing a half-written config file).
        recorder._spawn_device_thread(
            name="mic-fallback-save",
            target=_persist_mic,
        )

    recorder._recording_event.set()

    # AUDIO-PRE: prepend pre-roll buffer to reduce cold-start latency.
    recorder._prepend_preroll_to_buffer()

    target_sr = recorder.config.sample_rate
    if (
        effective_sr != target_sr
        and _recording_pkg._resample_poly is None
        and _recording_pkg._resample_poly_error is None
    ):
        # Warm up synchronously to avoid racing with stop()
        recorder.warm_up_resampler()

    # High: when the device's effective sample rate differs
    # from the audio processor's chain construction rate, rebuild
    # the chain at the new rate so (a) filter coefficients are
    # tuned to the actual native rate ( mitigation — an 80 Hz
    # high-pass built at 16 kHz actually cuts at 240 Hz when fed 48
    # kHz audio, removing male speech fundamentals), and (b) the
    # per-chunk ``process_chunk`` call avoids the RT-thread
    # ``resample_poly`` branch (5-50ms × 16 Hz = 80-800ms/sec of
    # RT-thread CPU) because ``input_sample_rate == _sample_rate``
    # short-circuits at audio_processor.py:283. The
    # ``_rebuild_audio_processor(force_sr=...)`` API was added by
    # but was never called from the
    # recorder — every chunk paid the resample cost after a
    # hot-plug or on first start with a non-16 kHz device.
    #
    # the retune block was consolidated into
    # ``retune_audio_processor`` (shared with
    # ``DisconnectHandler.restart_stream``) so the 3-level fallback
    # chain (set_sample_rate → rebuild_from_config → log-and-continue)
    # lives in one place. The helper is a no-op when
    # ``_sample_rate == effective_sr`` or when ``_audio_processor`` is None.
    retune_audio_processor(
        recorder._audio_processor,
        effective_sr,
        recorder.config,
        context="on start",
    )

    # refresh the per-chunk VAD property cache now that
    # ``_effective_sr`` (and the AudioProcessor's ``_sample_rate``,
    # if the above retuned it) are finalized. The cache lets the
    # 16 Hz audio worker hot path read scalars instead of
    # dispatching 3 property lookups per chunk × 16 Hz = 48/sec.
    recorder._refresh_vad_caches()

    # Start the audio worker thread AFTER the pre-roll
    # buffer has been prepended (so the worker doesn't race with
    # start()'s appendleft) and AFTER _recording_event.set() (so the
    # callback will actually push to the ring buffer). The worker
    # drains the ring buffer and runs the heavy processing pipeline
    # (filter chain, VAD, resample, state machine) off the real-time
    # audio thread.
    recorder._start_audio_worker()

    # Start the IPC event worker thread AFTER the audio worker
    # so the audio worker can enqueue IPC events (e.g. audio_clip)
    # as soon as it begins processing chunks. The event worker is
    # stopped by stop()/discard() — see _stop_event_worker.
    recorder._start_event_worker()

    # CPU-03: start the device health checker thread (off the audio
    # worker) so device-disconnect detection doesn't block the hot path.
    recorder._start_device_health_checker()


def stop_recording(recorder: Recorder) -> np.ndarray:
    """Stop recording and return the complete audio array.

        Body of :meth:`Recorder.stop` — extracted verbatim (with ``self.X``
    rewritten to ``recorder.X``) by  / Phase 4.5 to shrink the
        ~2748-LOC ``recorder.py`` god class. ``Recorder.stop`` becomes a
        1-line delegator so existing call sites, subclass overrides, and
        ``inspect.getsource`` checks that look for the method on the
        ``Recorder`` class continue to work. There is NO source-inspection
        test contract pinning ``Recorder.stop`` source (verified via
        ``rg "inspect.getsource.*Recorder\\.stop\\b" tests/`` — the matches
        on ``IPCServer.stop`` are unrelated); the simple Option B delegate
        is sufficient.

        Step ordering (preserved verbatim):

          1. ``_recording_event`` early-out fast path (returns empty
             ``float32`` array when not recording).
          2. Clear ``_recording_event`` (the gate the audio callback and
             streaming thread poll).
          3. Increment ``_stop_generation`` (HOTKEY-CRASH: stale
             disconnect handlers from the audio callback bail out).
          4. Set ``_user_stop_pending = True`` (STREAM-FIX:
             ``_stream_finished_callback`` suppresses the false "Stream
             finished unexpectedly" warning during the intentional stop).
          5. ``_teardown_stream()`` — 300 ms callback-drain poll, then
             ``stream.stop()`` + ``stream.close()`` (shared with
             ``discard_recording``; see the helper's docstring for the
    PERF- history).
          6. Clear ``_user_stop_pending`` (any future
             ``_stream_finished_callback`` is now a genuine disconnect).
          7. ``_stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
             drain=True)`` — drain=True so the last few hundred ms of
             audio (chunks still in the ring buffer) end up in
             ``recorder._buffer`` and are concatenated below
    ().
          8. ``_stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
             drain=True)`` — drains the IPC event queue.
          9. ``_stop_device_health_checker(timeout=0.0)`` — fire-and-
             forget (the daemon exits on its next 30 s wait() return).
         10. Snapshot ``_buffer`` under ``_lock``: swap the deque for a
             fresh empty one + capture the chunk list + capture
             ``_buffer_sr`` local + ``_secure_clear_caches``; then
             release the lock and ``np.concatenate`` the captured chunks
             OUTSIDE the lock so the audio worker's append path (which
             acquires the same lock) is not blocked for the 50–300 ms
             concat duration.
         11. Compute audio stats (RMS via ``np.dot``, peak, silence
             percentage) and store them in ``_last_audio_stats`` so the
    transcription engine can reuse them ().
         12. ``_prepare_audio(audio, effective_sr)`` — H15: resample from
             scratch (no cache) for the full audio.
         13. ``log.info`` the stop summary (duration, sr, samples, RMS,
             peak, silence_pct, stream/concat/resample/total ms); near-
             silence warning when ``rms < 0.001``.

        The empty-buffer fast path zeros the cached audio arrays
        (``_secure_clear_caches``), resets ``_chunk_count = 0``, and
    returns an empty ``float32`` array —  secure-clear
        contract.

        Critical: ``_buffer_sr`` (the actual rate of the audio in
        ``recorder._buffer``) is captured into a local BEFORE
        ``_secure_clear_caches`` resets it to ``None``. The local is the
        authoritative source rate for the snapshotted audio — the chunks
        in ``_captured_chunks`` were appended at this rate by
        ``_process_audio_chunk``. Using ``_effective_sr`` here (the
        device's native rate) would cause ``_prepare_audio`` to resample
        the already-16 kHz audio a second time (chipmunk voice).
    """
    # Lazy import: ``recorder.py`` is still loading when this module
    # is first imported (it imports this module near the top, before
    # the constants are defined). By deferring the import to call time
    # we read the constants from the fully-loaded module — mirrors
    # ``discard_recording`` and ``start_recording``.
    from voice_typer.server import recording as _recording_pkg
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_JOIN_TIMEOUT_S,
        _EVENT_WORKER_JOIN_TIMEOUT_S,
        DEFAULT_MAX_BUFFER_CHUNKS,
    )

    if not recorder._recording_event.is_set():
        return np.array([], dtype=np.float32)

    stop_started = time.perf_counter()
    recorder._recording_event.clear()

    # HOTKEY-CRASH: increment stop_generation so any stale disconnect
    # handlers from the audio callback know to bail out.
    recorder._stop_generation += 1

    # STREAM-FIX: mark that we're about to call stream.stop()
    # intentionally, so _stream_finished_callback doesn't warn about
    # an "unexpected" disconnect. Cleared after stream.close() below.
    recorder._user_stop_pending = True

    # 17-H-: drain callback + stop + close via _teardown_stream()
    # (shared with discard()). The 300ms callback poll is preserved
    # verbatim — see the helper's docstring/comments for the
    # PERF- history.
    recorder._teardown_stream()
    # STREAM-FIX: clear the user-stop-pending flag now
    # that stream.close() has completed. Any future
    # _stream_finished_callback invocation is now genuinely
    # unexpected (device disconnect).
    recorder._user_stop_pending = False
    stream_ms = (time.perf_counter() - stop_started) * 1000

    # stop the audio worker thread. drain=True so the
    # worker finishes processing any chunks still in the ring buffer
    # -- those chunks end up in recorder._buffer, which we concatenate
    # below. Without this drain, the last few hundred ms of audio
    # (chunks pushed to the ring buffer but not yet processed by the
    # worker) would be lost.
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

    # cut the worst-case stop() latency from ~5.8s to ~2.4s
    # by using a short 0.1s join for the event worker (down from 2.0s)
    # and fire-and-forget for the device health checker (down from
    # 1.0s join). The event worker drains its tiny queue in <10ms, so
    # 0.1s is generous; the device health checker sleeps 30s between
    # probes so a 1.0s join almost always timed out anyway. Both are
    # daemon threads, so even if they don't exit within the timeout,
    # they cannot block process exit. The audio worker join (2.0s) is
    # unchanged because it must drain up to 64 ring-buffer chunks of
    # in-flight audio (drain=True) to avoid losing the last few
    # hundred ms of the recording.
    # Use the full _EVENT_WORKER_JOIN_TIMEOUT_S (2.0s) so a slow
    # event_bus.publish (e.g. a backed-up TCP subscriber) has time
    # to drain. Pre-fix this was 0.1s which was too short for any
    # publish > 100ms — the daemon was left running and the test
    # contract (drain completes within stop()) was violated.
    recorder._stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

    # device health checker fire-and-forget. Pass timeout=0.0
    # so ``_stop_device_health_checker`` only signals the stop event
    # without joining (the daemon thread exits on its next 30s wait()
    # return).
    recorder._stop_device_health_checker(timeout=0.0)

    # snapshot the buffer chunks under the lock, then release
    # the lock BEFORE the O(N) np.concatenate. Previously the lock was
    # held across the concatenate (50-300ms for a 30-min recording),
    # blocking the audio worker's append path (which acquires the same
    # lock) for the full duration. The worker would stall, the ring
    # buffer would overflow, and the last few chunks of the recording
    # would be dropped. The fix mirrors the discard() path: inside the
    # lock, swap the deque for a fresh empty one + capture the chunks
    # list; outside the lock, concatenate the captured chunks.
    concat_started = time.perf_counter()
    with recorder._lock:
        if not recorder._buffer:
            # securely zero cached audio arrays BEFORE
            # reassignment (previously this just dropped the
            # references, leaving the previous session's voice data
            # in process memory until the numpy allocator reused
            # the block).
            recorder._secure_clear_caches()
            recorder._chunk_count = 0
            return np.array([], dtype=np.float32)
        # capture the chunk list and swap in a fresh deque
        # INSIDE the lock (the swap is O(1) -- just a deque
        # construction + attribute assignment). The expensive
        # ``np.concatenate`` is deferred to after the lock release.
        _old_buffer = recorder._buffer
        _captured_chunks = list(_old_buffer)
        recorder._buffer = collections.deque(
            maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
        )
        _recording_pkg._secure_clear_array_background(_old_buffer)
        # Critical: capture ``_buffer_sr`` into a local
        # BEFORE ``_secure_clear_caches`` resets it to ``None``.
        # The local is the authoritative source rate for the
        # audio we just snapshotted — the chunks in
        # ``_captured_chunks`` were appended at this rate by
        # ``_process_audio_chunk``.
        _captured_buffer_sr = recorder._buffer_sr
        # securely zero cached audio arrays BEFORE
        # reassignment (same rationale as the empty-buffer path
        # above; factored into ``_secure_clear_caches`` to avoid
        # 4-way duplication across stop()'s two paths and discard()).
        recorder._secure_clear_caches()
    # concatenate the captured chunks OUTSIDE the lock so the
    # audio worker (and any other ``recorder._lock`` acquirer) is not
    # blocked for the 50-300ms concat duration.
    audio = np.concatenate(_captured_chunks, axis=0).reshape(-1)
    concat_ms = (time.perf_counter() - concat_started) * 1000

    # Log audio statistics for diagnostics
    # Critical: prefer the captured ``_buffer_sr`` (the
    # actual sample rate of the audio that was just concatenated
    # out of ``_buffer``) over ``_effective_sr`` (the device's
    # native rate). When an AudioProcessor is active,
    # ``process_chunk`` resamples each chunk to the chain's
    # construction rate (16 kHz) before appending, so
    # ``_buffer_sr == 16000`` regardless of the device's native
    # rate. Pre-fix, ``stop()`` read ``_effective_sr`` (e.g.
    # 48000) and the subsequent ``_prepare_audio`` call did
    # ``resample_poly(audio, 1, 3)`` — decimating the already-16
    # kHz audio 3:1 → ~5333 samples presented as "16 kHz" →
    # pitched up 3× (chipmunk voice). The captured local is the
    # snapshot taken inside the lock above (before
    # ``_secure_clear_caches`` reset it to ``None``); the
    # ``or recorder._effective_sr`` fallback covers the
    # ``_buffer_sr is None`` case.
    effective_sr = _captured_buffer_sr if _captured_buffer_sr is not None else recorder._effective_sr
    duration = len(audio) / effective_sr if len(audio) > 0 else 0
    # initialize ``rms``/``peak``/``silence_pct`` BEFORE
    # the conditional below so the later ``log.info(... rms, peak,
    # silence_pct, ...)`` call site (which is also gated by
    # ``len(audio) > 0``) cannot reference an unbound name when
    # pyrefly analyses control flow.
    rms: float = 0.0
    peak: float = 0.0
    silence_pct: float = 0.0
    if len(audio) > 0:
        # AUDIO-NP: use np.dot for RMS in stop() too
        if audio.size:
            flat = audio.reshape(-1)
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            peak = float(np.abs(flat).max())
        else:
            peak = 0.0
            rms = 0.0
        silence_pct = float(np.sum(np.abs(audio) < 0.001) / audio.size * 100)
        recorder._last_rms = rms
        # store the full-recording stats so the
        # transcription engine can reuse them instead of recomputing
        # the same RMS/peak/silence_pct on the same audio array
        # (saves 1-3 ms + 3× 1.9 MB transient memory per dictation).
        recorder._last_audio_stats = (rms, peak, silence_pct)
    else:
        recorder._last_rms = 0.0
        recorder._last_audio_stats = (0.0, 0.0, 0.0)
        log.warning("[RECORDING] No audio data captured!")

    # H15: stop() should NOT use cache - resample from scratch for full audio
    resample_started = time.perf_counter()
    audio = recorder._prepare_audio(audio, effective_sr)
    resample_ms = (time.perf_counter() - resample_started) * 1000

    # AUDIO-PROC: post-capture spectral noise reduction (offline,
    # safe to block).  Runs AFTER resampling so noisereduce
    # operates on the final 16 kHz audio.  ~200 ms for 30 s audio.
    # ADR 0007 §3.8: post-capture noisereduce removed. The real-time
    # NoiseSuppressor filter in the chain handles denoising. The
    # old process_full_audio() call is removed because:
    # 1. It only ran in stop(), so the streaming path missed it.
    # 2. The "first 0.5s is silence" assumption was fragile.
    # 3. noisereduce is no longer a dependency.

    total_ms = (time.perf_counter() - stop_started) * 1000
    if len(audio) > 0:
        log.info(
            "[RECORDING] Audio stopped: duration=%.1fs, sr=%d, samples=%d, "
            "RMS=%.6f, peak=%.6f, silence=%.1f%% | "
            "stream=%.0fms concat=%.0fms resample=%.0fms total=%.0fms",
            duration,
            effective_sr,
            len(audio),
            rms,
            peak,
            silence_pct,
            stream_ms,
            concat_ms,
            resample_ms,
            total_ms,
        )
        if rms < 0.001:
            log.warning(
                "[RECORDING] Near-silence detected! (RMS=%.6f) Microphone may not be capturing audio.",
                rms,
            )
    else:
        # Warning already emitted above when len(audio) == 0
        pass

    return audio
