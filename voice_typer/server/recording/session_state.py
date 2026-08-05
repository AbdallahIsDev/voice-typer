"""Session state management for :class:`Recorder` (extracted from ``recorder.py``).

Phase 4.5 — extracted from :mod:`.recorder` to shrink the
3772-LOC ``recorder.py`` god class (see  in ``review.md``).
Owns the per-session state reset, config-derived scalar caching,
secure-cache clearing (the bulk ``_secure_clear_caches`` — NOT
``_secure_clear_session_caches`` which stays on Recorder for source-
inspection contracts), buffer resizing for the effective sample rate,
and the preroll prepend.

Collaborator pattern
--------------------
:class:`SessionState` is constructed by ``Recorder.__init__`` with a
back-reference to the owning ``Recorder`` instance
(``SessionState(recorder)``). The collaborator reference is used to
access *shared* state that lives on ``Recorder`` and is NOT moved here:

- ``self._recorder._buffer`` — main audio buffer (deque)
- ``self._recorder._chunk_count`` / ``_cached_resampled`` / etc. — cached state
- ``self._recorder._effective_sr`` / ``_buffer_sr`` — sample-rate tracking
- ``self._recorder._ring_buffer`` — SPSC ring buffer (deque)
- ``self._recorder._preroll_buffer`` — preroll deque
- ``self._recorder.config`` — for ``sample_rate`` / ``max_recording_time_seconds``
- ``self._recorder._audio_processor`` — filter chain
- ``self._recorder._vad`` / ``_vad_state`` / etc. — VAD state
- ``self._recorder._recent_rms_values`` / ``_silence_timer`` / etc. — RMS state
- ``self._recorder._device_disconnected`` / ``_device_disconnect_retries`` — disconnect state
- ``self._recorder._xruns`` / ``_xrun_timestamps`` / ``_clip_count`` / etc. — XRUN state
- ``self._recorder._cached_target_sr`` / ``_cached_vad_*`` — cached scalars
- ``self._recorder._cached_resampled_segments`` / ``_cached_resampled_concat_dirty`` — segment cache
- ... and any other state referenced in the extracted bodies

Each method on this class takes ``recorder`` as an explicit parameter
(the owning :class:`Recorder` instance) rather than reading
``self._recorder``. This lets ``Recorder`` keep 1-line delegator methods
that pass ``self`` straight through (``self._session_state.X(self)``),
and matches the existing pattern where ``self`` references in the
original body become ``recorder.X`` in the extracted body.

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
to inject fake resample behavior. The ``_recording_pkg._get_resample_poly()``
indirection (see the module docstring of :mod:`.recorder` §Patch-path) is
preserved here so the patch takes effect at call time. The same indirection
is used for ``_recording_pkg._secure_clear_array(...)`` ( regression
pinned by ``tests/test_secure_clear_array.py``) and for the module-level
``_recording_pkg._AUDIO_RING_BUFFER_CAPACITY`` constant used in
:meth:`SessionState.resize_buffers_for_sample_rate` — accessing it via the
package namespace means a future test patch on the package binding would
propagate here automatically (matching how :mod:`.audio_pipeline` /
:mod:`.device_manager` already consume package-level names).
"""

from __future__ import annotations

import collections
import logging
import os
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from voice_typer.server import recording as _recording_pkg
from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE
from voice_typer.server.vad_processor import (
    DEFAULT_VAD_SILENCE_THRESHOLD_DB,
    DEFAULT_VAD_SPEECH_THRESHOLD_DB,
    VadState,
)

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")

if TYPE_CHECKING:
    pass


class SessionState:
    """Session state management for :class:`Recorder`.

    Phase 4.5 — extracted from :mod:`.recorder`. See the module
        docstring for the collaborator-pattern rationale and the list of
        recorder-owned vs. collaborator-owned state.
    """

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        # import (``recorder`` imports ``session_state`` at module top to
        # construct this class in ``Recorder.__init__``). Each method on
        # this class ALSO takes ``recorder`` as an explicit parameter —
        # the back-reference is kept for parity with the other
        # collaborators (:class:`AudioPipeline` / :class:`DeviceManager` /
        # :class:`DisconnectHandler`) and for any future helper that
        # doesn't want the explicit parameter form.
        self._recorder = recorder

    # ── Per-session state reset ──────────────────────────────────────────

    def reset_session_state(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._reset_session_state`.

                Reset ALL per-session state for a fresh recording session.

        reset ALL per-session state here, not just the buffer.
                Previously some flags (_max_duration_warning_sent,
                _silence_warning_sent, etc.) persisted across recordings,
                causing stale state to suppress warnings on the next session.

        (revised): The dead ``_silence_warning_sent`` and
                ``_max_duration_warning_sent`` boolean flags have been REMOVED.
                They were declared and reset here but NEVER read in any
                conditional — the actual silence-warning state machine uses
                the integer counter ``_silence_warning_count`` (which IS read
                elsewhere). The dead flags were misleading maintainers into
                thinking warning deduplication existed when it didn't.

                Called by :meth:`start` after the cache-clearance +
                pre-flight-permission gate. The state reset block is extracted
                out of ``start()``'s body so ``start()`` stays a readable
                orchestrator that calls helpers in order.
        """
        recorder._buffer.clear()
        recorder._chunk_count = 0
        # PERF: zero the running buffered-samples counter so the next
        # session's ``current_duration_seconds`` polls start from 0.
        # Without this, the counter would carry over the previous
        # session's total and over-report duration on the first poll.
        recorder._total_buffered_samples = 0
        recorder._cached_resampled = np.array([], dtype=np.float32)
        recorder._cached_native_chunk_count = 0
        # also reset the cache key so a new session doesn't
        # reuse a stale prefix from a different sample rate.
        recorder._cached_resample_key = ()
        # invalidate the no-resample cache too.
        recorder._cached_no_resample_len = -1
        recorder._cached_no_resample_arr = None
        # zero each segment's underlying numpy buffer BEFORE
        # dropping the list reference, mirroring the secure-clear
        # contract in ``secure_clear_caches`` / ``_secure_clear_session_caches``.
        # ``reset_session_state`` runs from ``start()`` AFTER
        # ``_secure_clear_session_caches`` (which already zeroed the
        # previous session's segments), so the list is normally
        # already-empty here — but defensive zeroing protects against
        # any code path that populates the list between
        # ``_secure_clear_session_caches`` and this reset (e.g. a
        # racing ``snapshot()`` from the streaming thread), and keeps
        # the contract symmetric with the ``stop()``/``discard()``
        # path so a future maintainer can't regress one without
        # regressing the other.
        try:
            for seg in recorder._cached_resampled_segments:
                if seg is not None and seg.size > 0:
                    _recording_pkg._secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled_segments in reset_session_state",
                exc_info=True,
            )
        # zero the no-resample-path segment list too, mirroring the
        # resample-path loop above and the bulk ``secure_clear_caches``
        # contract. ``reset_session_state`` runs from ``start()`` AFTER
        # ``_secure_clear_session_caches`` (which zeros the cached arrays
        # and the resample-path segment list), so the no-resample segment
        # list is normally already-empty here — but defensive zeroing
        # protects against any code path that populates the list between
        # ``_secure_clear_session_caches`` and this reset (e.g. a racing
        # ``snapshot()`` from the streaming thread), and keeps the
        # contract symmetric with the ``stop()``/``discard()`` path so a
        # future maintainer can't regress one without regressing the other.
        try:
            for seg in recorder._cached_no_resample_segments:
                if seg is not None and seg.size > 0:
                    _recording_pkg._secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_segments in reset_session_state",
                exc_info=True,
            )
        # reset the resample-path segment list + dirty flag so a
        # new session starts with an empty cache (no stale segments
        # carried over from the previous session).
        recorder._cached_resampled_segments = []
        recorder._cached_resampled_concat_dirty = False
        # reset the no-resample-path segment list + dirty flag for the
        # same reason — ``_ensure_no_resample_concat`` short-circuits
        # when the dirty flag is ``False``, so a stale ``True`` would
        # serve a stale or empty cache on the next snapshot.
        recorder._cached_no_resample_segments = []
        recorder._cached_no_resample_concat_dirty = False
        # reset per-session error counters so each session
        # reports its own dropped-chunk / RMS-callback-error totals
        # (previously these accumulated across sessions).
        recorder._dropped_chunks = 0
        recorder._rms_callback_error_count = 0
        recorder._silence_timer = 0.0
        recorder._silence_start_time = None
        recorder._silence_warning_count = 0
        recorder._silence_next_warning_wait = 10.0
        recorder._recent_rms_values.clear()
        recorder._recording_start_time = time.perf_counter()
        # Critical: reset the buffer-sample-rate tracker so a
        # fresh session starts from ``None``. Matches the ``discard()``
        # / ``stop()`` reset in ``_recorder_split.py`` /
        # ``_secure_clear_caches``. The ``None`` sentinel makes the
        # ``_buffer_sr or _effective_sr`` fallback idiom work until
        # the first chunk arrives.
        recorder._buffer_sr = None
        # reset the per-chunk VAD property cache so stale values
        # from a prior session don't leak. The cache is recomputed by
        # ``_refresh_vad_caches()`` after the device loop finalizes
        # ``_effective_sr``.
        recorder._cached_vad_enabled = False
        recorder._cached_use_silero_vad = False
        recorder._cached_silero_available = False
        recorder._cached_vad_resample_up_down = None
        recorder._cached_vad_resample_sr = None
        # Reset XRUN and clipping counters
        recorder._xruns = 0
        recorder._xrun_timestamps.clear()
        recorder._clip_count = 0
        recorder._peak = 0.0
        recorder._last_clip_log_time = 0.0
        recorder._last_rms = 0.0
        # reset VAD state machine.
        # VadProcessor.reset() handles the actual state restoration.
        # The property-shim assignments below are kept as a redundant
        # safety net AND as source-level documentation that start()
        # resets the VAD calibration state — existing tests pin on the
        # literal attribute names (``_vad_calibration_rms_values`` /
        # ``_vad_calibrated``) appearing in start()'s source.
        recorder._vad.reset()
        recorder._vad_state = VadState.UNKNOWN
        recorder._vad_consecutive_speech_frames = 0
        recorder._vad_consecutive_silence_frames = 0
        recorder._vad_speech_threshold_db = DEFAULT_VAD_SPEECH_THRESHOLD_DB
        recorder._vad_silence_threshold_db = DEFAULT_VAD_SILENCE_THRESHOLD_DB
        # reset auto-calibration
        recorder._vad_calibration_rms_values = []
        recorder._vad_calibrated = False
        # STREAM-FIX: reset user-stop-pending flag for the new
        # session so a stale True doesn't suppress a genuine disconnect
        # warning in this session.
        recorder._user_stop_pending = False
        # ADR 0007 §3.5: AGC reset deleted (method removed).
        # AUDIO-PRE: clear pre-roll buffer
        # SEC-audit-008: Zero the preroll buffer contents before clearing
        for chunk in recorder._preroll_buffer:
            if isinstance(chunk, np.ndarray):
                chunk.fill(0)
        recorder._preroll_buffer.clear()
        # AUDIO-HOT: reset disconnect state
        recorder._device_disconnected = False
        recorder._device_disconnect_retries = 0
        # reset ring buffer drop counter for the new session
        recorder._dropped_ring_chunks = 0
        # AUDIO-HOT: reset periodic device check counter
        recorder._device_check_counter = 0
        # PERF-: cache the target sample rate once at start()
        # so the audio callback / snapshot() doesn't re-read
        # self.config.sample_rate on every call.
        recorder._cached_target_sr = recorder.config.sample_rate

        # AUDIO-PROC: reset filter state for a new session so the
        # high-pass IIR doesn't carry state from the previous recording.
        if recorder._audio_processor is not None:
            recorder._audio_processor.reset()

    # ── Config-derived scalar caching ───────────────────────────────────

    def cache_session_config(self, recorder: Any) -> int:
        """Body of :meth:`Recorder._cache_session_config`.

                Cache config-derived scalars for the upcoming session and return ``max_rec``.

        PERF-: cache config values at start() time so the
                audio callback doesn't do 5x getattr per iteration.
                Coerce to float so a non-numeric MagicMock config (in tests)
                doesn't cause TypeError in the silence_timer comparison.

                Returns ``max_rec`` (the parsed ``_cached_max_recording_time``
                as an int, or 0 on TypeError/ValueError) so ``start()`` can
                pass it to ``_resize_buffers_for_sample_rate`` later — the
                dynamic buffer sizing is deferred until the device loop
                finalizes ``effective_sr``.
        """
        _silence_warning = recorder.config.silence_warning_seconds
        # stop_on_silence_seconds is a Config dataclass field (default
        # 60.0) — always present on a real Config instance, so the
        # getattr fallback could never fire on a real Config.
        _stop_on_silence = recorder.config.stop_on_silence_seconds
        _silence = float(_silence_warning) if isinstance(_silence_warning, int | float) else 20.0
        recorder._cached_silence_warning = _silence
        _stop_silence = float(_stop_on_silence) if isinstance(_stop_on_silence, int | float) else 60.0
        recorder._cached_stop_on_silence = _stop_silence
        # SIMPLIFY-001: single explicit field replaces the old 3-field split
        # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu,
        # and max_recording_time_seconds=0 auto-selection). Always defaults to 900.
        recorder._cached_max_recording_time = int(recorder.config.max_recording_time_seconds)

        # dynamic buffer sizing is DEFERRED until after the
        # device loop below sets ``effective_sr``. The original
        # implementation computed ``needed_chunks`` here using a stale
        # 0.064s chunk-duration assumption (1024 samples / 16kHz), but
        # the actual blocksize is 512 and the effective sample rate may
        # be 44.1/48kHz (device native rate). Computing the size now
        # would under-allocate by ~3× at 48kHz and silently evict the
        # first ~25 minutes of a 30-minute dictation. See the resize
        # block after the device loop succeeds.
        try:
            max_rec = int(recorder._cached_max_recording_time)
        except (TypeError, ValueError):
            max_rec = 0
        return max_rec

    # ── Secure cache clearing (bulk — NOT _secure_clear_session_caches) ─

    def secure_clear_caches(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._secure_clear_caches`.

        securely zero cached audio arrays BEFORE reassignment.

                ``stop()`` and ``discard()`` previously reassigned
                ``_cached_resampled`` and ``_cached_no_resample_arr`` to fresh
                empty arrays without first zeroing the underlying numpy
                buffers.  The cached arrays can hold up to ~30 min of 16 kHz
                float32 audio (~115 MB) of the user's voice, so simply dropping
                the reference left that data in process memory until the numpy
                allocator reused the block — defeating SEC-audit-008's intent.

                This helper factors the 4-way duplication between ``stop()``'s
                two code paths (empty-buffer early return + main path) and
                ``discard()`` into a single place, AND fixes the regression by
                calling ``_secure_clear_array`` on each non-empty cache before
                replacing it.

                Idempotent: safe to call when the caches are already empty /
                ``None`` (the size guard skips the zeroing).

                NOTE: this is the bulk ``_secure_clear_caches`` called by
                ``stop()`` / ``discard()``. The smaller ``_secure_clear_session_caches``
                helper called from ``start()`` STAYS on ``Recorder`` — it has a
                positive source-inspection contract (``tests/test_secure_clear_array.py``
                pins that ``Recorder._secure_clear_session_caches`` source
                contains ``_secure_clear_array(self._cached_resampled)`` and
                ``_secure_clear_array(self._cached_no_resample_arr)``). The
                primary agent will leave that method untouched on ``Recorder``.
        """
        # Route through ``_recording_pkg.`` so test patches of the form
        # ``monkeypatch.setattr("voice_typer.server.recording._secure_clear_array", ...)``
        # take effect at runtime (matching ``_secure_clear_array_background``
        # in stop()/discard() and the secure-clear block in start()).
        try:
            if recorder._cached_resampled is not None and recorder._cached_resampled.size > 0:
                _recording_pkg._secure_clear_array(recorder._cached_resampled)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled",
                exc_info=True,
            )
        try:
            if recorder._cached_no_resample_arr is not None and recorder._cached_no_resample_arr.size > 0:
                _recording_pkg._secure_clear_array(recorder._cached_no_resample_arr)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_arr",
                exc_info=True,
            )
        # (High): the resample-path segment list
        # (``_cached_resampled_segments``) holds the per-snapshot
        # resampled prefix as a Python list of contiguous ndarrays.
        # ``_ensure_resampled_concat`` may keep
        # ``_cached_resampled`` pointing at ``segments[0]`` directly
        # (the 1-segment fast path), so simply dropping the list
        # reference (as the original code did) leaves up to ~115 MB of
        # dictated float32 audio in process memory until the numpy
        # allocator reuses the blocks — defeating 's intent for
        # the segment cache. Zero each segment in-place BEFORE the
        # list reassignment. Best-effort: a failure to zero one
        # segment doesn't block zeroing the rest or the cache reset.
        try:
            for seg in recorder._cached_resampled_segments:
                if seg is not None and seg.size > 0:
                    _recording_pkg._secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled_segments",
                exc_info=True,
            )
        # (High): mirror the resample-path loop above for the
        # no-resample-path segment list. The no-resample path is the
        # COMMON path in production (AudioProcessor resamples to 16 kHz
        # before appending, so ``_buffer_sr == target_sr``), so this
        # list is the primary storage for the dictated prefix in a
        # typical session. Dropping the list reference without first
        # zeroing each segment left up to ~115 MB of float32 audio in
        # process memory until the numpy allocator reused the blocks —
        # defeating SEC-audit-008's intent for the no-resample-path
        # segment cache. Best-effort: a failure to zero one segment
        # doesn't block zeroing the rest or the cache reset.
        try:
            for seg in recorder._cached_no_resample_segments:
                if seg is not None and seg.size > 0:
                    _recording_pkg._secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_segments",
                exc_info=True,
            )
        recorder._cached_resampled = np.array([], dtype=np.float32)
        recorder._cached_no_resample_arr = None
        recorder._cached_native_chunk_count = 0
        recorder._cached_no_resample_len = -1
        # now that every segment's underlying numpy buffer has
        # been zeroed in-place (above), drop the list references and
        # reset the dirty flags so the next session starts clean.
        recorder._cached_resampled_segments = []
        recorder._cached_resampled_concat_dirty = False
        recorder._cached_no_resample_segments = []
        recorder._cached_no_resample_concat_dirty = False
        # reset the audio processor's filter state too.
        # IIR ``zi`` arrays + RNNoise ``_carry`` (up to 479 samples,
        # ~2 KB at 16 kHz float32) retain audio-derived residuals
        # after stop()/discard(). ``AudioProcessor.reset()`` was only
        # called from ``Recorder.start()`` pre-fix, so the filter
        # state from the prior recording lingered in process memory
        # until the next start() — defeating SEC-audit-008's intent
        # for the filter-state path. Best-effort: a missing or
        # misbehaving ``reset`` is swallowed so secure-clear still
        # completes for the numpy caches above.
        try:
            if recorder._audio_processor is not None:
                recorder._audio_processor.reset()
        except Exception:
            log.debug(
                "[RECORDER] _audio_processor.reset() in _secure_clear_caches failed",
                exc_info=True,
            )
        # Critical: reset the buffer-sample-rate tracker so a
        # subsequent ``start()`` doesn't reuse the stale rate. Matches
        # the explicit reset in ``_recorder_split.discard_recording``
        # (which also routes through this helper for the cache arrays).
        recorder._buffer_sr = None

    # ── Dynamic buffer sizing ───────────────────────────────────────────

    def resize_buffers_for_sample_rate(self, recorder: Any, effective_sr: int, max_rec: int) -> None:
        """Body of :meth:`Recorder._resize_buffers_for_sample_rate`.

        Dynamically size the main buffer, ring buffer, and pre-roll deque.

        ── dynamic buffer sizing (deferred from start() state reset) ──
        Now that the device loop has finalized ``effective_sr`` (the
        device's native sample rate, which may be 44.1/48kHz), size
        both the main recording buffer and the pre-roll deque using
        the ACTUAL chunk duration ``blocksize / effective_sr``.

        previously the main buffer was sized against a stale
        1024-sample/16kHz assumption (chunk_seconds=0.064). At 48kHz
        with 512-sample blocks the real chunk_seconds is 512/48000 ≈
        0.0107s, so 30000 default chunks only hold ~5.3 min — a
        30-min dictation silently lost the first ~25 min via deque
        maxlen eviction. We resize to ``int(max_rec / chunk_seconds)
        + safety`` so the buffer can always hold the full configured
        max_recording_time_seconds. Existing buffer contents (empty
        at this point in start()) are preserved via list(deque).

        the pre-roll deque was sized in __init__ using
        ``config.sample_rate`` (16kHz). At 48kHz the same 1-second
        pre-roll needs 3× the chunk capacity. Re-size here using
        ``effective_sr`` so the pre-roll actually captures the
        configured ``pre_roll_buffer_seconds``. Existing pre-roll
        chunks already captured by the audio callback (between
        stream.start() above and here) are preserved.
        """
        blocksize = _AUDIO_BLOCKSIZE  # matches sd.InputStream blocksize below
        sizing_sr = effective_sr if effective_sr > 0 else recorder.config.sample_rate
        if sizing_sr <= 0:
            sizing_sr = recorder.config.sample_rate
        chunk_seconds = blocksize / sizing_sr if sizing_sr > 0 else 0.064

        if max_rec > 0 and chunk_seconds > 0:
            needed_chunks = int(max_rec / chunk_seconds) + 1000  # +1K safety
            current_maxlen = recorder._buffer.maxlen or 0
            if needed_chunks > current_maxlen:
                # Preserve any data already in the buffer (defensive —
                # start() clears the buffer at line ~1220, so this is
                # normally empty) when resizing.
                old_data = list(recorder._buffer)
                recorder._buffer = collections.deque(old_data, maxlen=needed_chunks)
                log.debug(
                    "[RECORDING] Buffer sized for %ds max recording at %d Hz "
                    "(blocksize=%d, chunk_seconds=%.4f): %d chunks",
                    max_rec,
                    sizing_sr,
                    blocksize,
                    chunk_seconds,
                    needed_chunks,
                )

        # resize the SPSC ring buffer (callback -> worker handoff)
        # proportional to the effective sample rate so it always holds
        # ~1 second of audio regardless of the device's native rate.
        #
        # High: pre-fix the capacity was sized for ~4 seconds
        # (``int(sizing_sr / blocksize * 4.0)``). When the worker fell
        # behind (RNNoise at 50ms/chunk on a 16kHz device where chunks
        # arrive every 32ms), the ring buffer filled to 4s and stayed
        # there (worker throughput 20 chunks/s vs callback 31 chunks/s
        # → backlog grew 11 chunks/s → ring filled in ~11s). Under
        # this steady-state overload, silence auto-stop latency =
        # silence_threshold (5s) + ring_backlog (4s) = 9.0s from when
        # the user stopped speaking. The 4s headroom was sized for
        # "VAD inference latency spikes" but Silero VAD is 1-5ms
        # against a 32ms budget — 1000× overkill. Reducing to 1s
        # limits worst-case silence latency to ~6s while still
        # absorbing 1s spikes (GC pauses, etc.). The 4s capacity is
        # also available as ``_AUDIO_RING_BUFFER_CAPACITY_FALLBACK_S``
        # for environments where the worker is known to be slow
        # (set via env var).
        # default to 2.0s of headroom so the ring buffer can
        # absorb the pre-roll filter-chain prepend duration (—
        # prepend now runs on the worker thread while live audio
        # accumulates in the ring buffer) plus RNNoise worker stalls.
        # The env var ``VOICE_TYPER_RING_BUFFER_SECONDS`` overrides
        # this for environments that need tighter (or looser) capacity.
        _ring_buffer_seconds = float(os.environ.get("VOICE_TYPER_RING_BUFFER_SECONDS", "2.0"))
        if sizing_sr > 0:
            new_ring_capacity = int(sizing_sr / blocksize * _ring_buffer_seconds)
            # floor at 64 chunks so a 16 kHz / 512-block device
            # still gets ~2s of headroom (64 * 512 / 16000 = 2.048s),
            # preserving the RNNoise-worker-stall headroom intent that
            # previously lived in the (now-removed) ``_uu36_*`` override
            # block in ``_recorder_split.start_recording``. 64 chunks
            # is also sufficient for one VAD inference spike (Silero
            # ~1-5ms against a 32ms budget).
            if new_ring_capacity < 64:
                new_ring_capacity = 64
            if new_ring_capacity != _recording_pkg._AUDIO_RING_BUFFER_CAPACITY and new_ring_capacity > 0:
                # Preserve any chunks already in the ring buffer
                # (defensive -- start() clears the ring buffer in
                # ``_start_audio_worker`` before this point, so this
                # is normally empty) when resizing.
                _old_ring = list(recorder._ring_buffer)
                recorder._ring_buffer = collections.deque(_old_ring, maxlen=new_ring_capacity)
                log.debug(
                    "[RECORDING] Ring buffer sized for ~%.1fs at %d Hz (blocksize=%d): %d chunks (was %d)",
                    _ring_buffer_seconds,
                    sizing_sr,
                    blocksize,
                    new_ring_capacity,
                    _recording_pkg._AUDIO_RING_BUFFER_CAPACITY,
                )

        # re-size the pre-roll deque using the effective sample
        # rate. The deque was created in __init__ with a placeholder
        # capacity based on config.sample_rate (16kHz); for a 48kHz
        # device that capacity is 3× too small, so a 1s pre-roll would
        # only capture ~0.33s. Preserve any preroll already captured by
        # the audio callback (it may have fired between stream.start()
        # and here).
        if recorder._preroll_active and recorder._preroll_seconds > 0 and sizing_sr > 0:
            new_preroll_maxlen = int(recorder._preroll_seconds * sizing_sr / blocksize) + 2
            current_preroll_maxlen = recorder._preroll_buffer.maxlen or 0
            if new_preroll_maxlen != current_preroll_maxlen:
                old_preroll = list(recorder._preroll_buffer)
                recorder._preroll_buffer = collections.deque(old_preroll, maxlen=new_preroll_maxlen)
                log.debug(
                    "[RECORDING] Pre-roll buffer sized for %.2fs at %d Hz (blocksize=%d): %d chunks (was %d)",
                    recorder._preroll_seconds,
                    sizing_sr,
                    blocksize,
                    new_preroll_maxlen,
                    current_preroll_maxlen,
                )

    # ── Preroll prepend at start() ──────────────────────────────────────

    def prepend_preroll_to_buffer(self, recorder: Any) -> None:
        """Body of :meth:`Recorder._prepend_preroll_to_buffer`.

        Prepend captured pre-roll chunks to the main recording buffer.

        AUDIO-PRE: prepend pre-roll buffer to reduce cold-start latency.
        The pre-roll buffer captured audio before recording officially
        started, so we insert it at the beginning of the main buffer.
        R18-F12: route each pre-roll chunk through the audio filter
        chain (``_audio_processor.process_chunk``) BEFORE prepending
        to ``_buffer``. Pre-fix the raw pre-roll chunks bypassed the
        filter chain, so the first ~300ms of a recording sounded
        unfiltered (no RNNoise / no gate) before the live callback
        path kicked in. Filtering here keeps the pre-roll amplitude
        envelope consistent with the rest of the recording.

        After prepend, the pre-roll deque is zeroed and cleared so the
        user's voice data does not linger in process memory for the
        entire recording session (SEC-audit-008 privacy gap).
        """
        if recorder._preroll_buffer:
            preroll_chunks = list(recorder._preroll_buffer)
            if preroll_chunks:
                for chunk in reversed(preroll_chunks):
                    mono_chunk = recorder._ensure_mono(chunk)
                    # R18-F12: best-effort filter — if the processor
                    # raises (or returns None), fall back to the raw
                    # chunk so pre-roll never blocks start().
                    if recorder._audio_processor is not None:
                        try:
                            filtered = recorder._audio_processor.process_chunk(
                                mono_chunk,
                                input_sample_rate=recorder._effective_sr,
                            )
                            if filtered is not None:
                                mono_chunk = filtered
                        except Exception:
                            log.debug(
                                "[RECORDING] pre-roll process_chunk failed; using raw chunk",
                                exc_info=True,
                            )
                    recorder._buffer.appendleft(mono_chunk.copy())
                log.debug(
                    "[RECORDING] Prepended %d pre-roll chunks (~%.1fs)",
                    len(preroll_chunks),
                    len(preroll_chunks) * 512 / recorder._effective_sr,
                )
                # zero + clear the pre-roll deque after the
                # prepend. Without this, the pre-roll chunks (which
                # are now duplicated into ``self._buffer``) remained
                # referenced by ``_preroll_buffer`` until the next
                # ``start()`` call zero-filled them -- keeping up to
                # ``preroll_seconds * native_sr * 4`` bytes of the
                # user's voice data alive in process memory for the
                # entire recording session (SEC-audit-008 privacy gap
                # + unnecessary memory pressure). The audio callback
                # only writes to ``_preroll_buffer`` when
                # ``_recording_event`` is clear (pre-roll mode), which
                # is now set, so clearing here is safe.
                for _chunk in preroll_chunks:
                    if isinstance(_chunk, np.ndarray):
                        _chunk.fill(0)
                recorder._preroll_buffer.clear()
