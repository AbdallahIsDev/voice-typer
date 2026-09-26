"""Session state management for :class:`Recorder` (extracted from ``recorder.py``).

Extracted from :mod:`.recorder` to shrink the
3772-LOC ``recorder.py`` god class (see  in ``review.md``).
Owns the per-session state reset, config-derived scalar caching,
secure-cache clearing (the bulk ``_secure_clear_caches``, NOT
``_secure_clear_session_caches`` which stays on Recorder for source-
inspection contracts), buffer resizing for the effective sample rate,
and the preroll prepend.

Collaborator pattern
--------------------
:class:`SessionState` is constructed by ``Recorder.__init__`` with a
back-reference to the owning ``Recorder`` instance
(``SessionState(recorder)``). The collaborator reference is used to
access *shared* state that lives on ``Recorder`` and is NOT moved here:

- ``self._recorder._audio_pipeline._buffer``: main audio buffer (deque)
- ``self._recorder._audio_pipeline._chunk_count`` / ``_cached_resampled`` / etc., cached state
- ``self._recorder._effective_sr`` / ``_buffer_sr``, sample-rate tracking
- ``self._recorder._ring_buffer``: SPSC ring buffer (deque)
- ``self._recorder._preroll_buffer``: preroll deque
- ``self._recorder.config``: for ``sample_rate`` / ``max_recording_time_seconds``
- ``self._recorder._audio_processor``: filter chain
- ``self._recorder._vad`` (VadProcessor). VAD state (accessed via
  ``recorder._vad.<attr>``; the historical ``Recorder._vad_*`` shims
  were removed)
- ``self._recorder._recent_rms_values`` / ``_silence_timer`` / etc., RMS state
- ``self._recorder._devices._device_disconnected`` /
  ``_device_disconnect_retries``: disconnect state (DeviceManager)
- ``recorder._audio_pipeline._xruns`` / ``_xrun_timestamps`` /
  ``_clip_count`` / etc. XRUN/clip telemetry (owned by
  :class:`.audio_pipeline.AudioPipeline`)
- ``self._recorder._cached_target_sr`` / ``_cached_vad_*``, cached scalars
- ``self._recorder._cached_resampled_segments`` / ``_cached_resampled_concat_dirty``, segment cache
- ... and any other state referenced in the extracted bodies

Each method on this class takes ``recorder`` as an explicit parameter
(the owning :class:`Recorder` instance) rather than reading
``self._recorder``. Call sites invoke the methods on
``recorder._session_state`` directly (``recorder._session_state.X(recorder)``;
the historical ``Recorder`` delegators were removed), and this matches
the existing pattern where ``self`` references in the
original body become ``recorder.X`` in the extracted body.

Patch-path compatibility
------------------------
This module consumes the owning submodules directly (C-ARCH-2):
``_secure_clear_array`` is imported from :mod:`.buffer`. The ring
buffer's resize guard compares against the LIVE ``maxlen`` of
``recorder._ring_buffer`` (not a module constant), so any producer
that sizes the ring (``recorder_init``'s default-capacity deque, the
start-path reassignment in :mod:`.recording_lifecycle`, or the restart
path below) is respected. Tests that need to stub the secure-clear
path patch the OWNING module:
``monkeypatch.setattr("voice_typer.server.recording.buffer._secure_clear_array", ...)``
— but note ``secure_clear_caches`` binds the function at import time by
design (the historical package-object indirection was removed with the
class of no-op test patches it enabled; see C-ARCH-2).
"""

from __future__ import annotations

import collections
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.recording.buffer import _secure_clear_array
from voice_typer.server.recording.format import ensure_mono
from voice_typer.server.vad_processor import (
    DEFAULT_VAD_SILENCE_THRESHOLD_DB,
    DEFAULT_VAD_SPEECH_THRESHOLD_DB,
    VadState,
)

# All submodules use the package-level logger so log records propagate
log = logging.getLogger("voice_typer.server.recording")

if TYPE_CHECKING:
    pass


def coerce_max_recording_time(value: int) -> int:
    """Coerce a max-recording-time scalar to ``int`` (0 on failure)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class SessionState:
    """Session state management for :class:`Recorder`."""

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        self._recorder = recorder

        # Sliding-window flap detection: the disconnect-handler
        recorder._restart_timestamps = collections.deque()
        # Default: 3 restarts within the window triggers on_device_lost.
        recorder._flapping_max_restarts = 3
        # Default: 60s sliding window. Long enough to catch a slow flap
        recorder._flapping_window_seconds = 60.0

    def reset_session_state(self, recorder: Any) -> None:
        """Reset per-session state (a ``SessionState`` method invoked"""
        recorder._audio_pipeline._buffer.clear()
        recorder._audio_pipeline._chunk_count = 0
        # PERF: zero the running buffered-samples counter so the next
        recorder._audio_pipeline._total_buffered_samples = 0
        recorder._cached_resampled = np.array([], dtype=np.float32)
        # contiguous storage: the resampled cache's filled-prefix length
        recorder._cached_resampled_len = 0
        recorder._cached_native_chunk_count = 0
        # also reset the cache key so a new session doesn't
        recorder._cached_resample_key = ()
        # invalidate the no-resample cache too.
        recorder._cached_no_resample_len = -1
        recorder._cached_no_resample_arr = None
        # already-empty here, but defensive zeroing protects against
        try:
            for seg in recorder._cached_resampled_segments:
                if seg is not None and seg.size > 0:
                    _secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled_segments in reset_session_state",
                exc_info=True,
            )
        # list is normally already-empty here, but defensive zeroing
        try:
            for seg in recorder._cached_no_resample_segments:
                if seg is not None and seg.size > 0:
                    _secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_segments in reset_session_state",
                exc_info=True,
            )
        # reset the resample-path segment list + dirty flag so a
        recorder._cached_resampled_segments = []
        recorder._cached_resampled_concat_dirty = False
        # reset the no-resample-path segment list + dirty flag for the
        recorder._cached_no_resample_segments = []
        recorder._cached_no_resample_concat_dirty = False
        # reset per-session error counters so each session
        recorder._dropped_chunks = 0
        recorder._rms_callback_error_count = 0
        recorder._silence_timer = 0.0
        recorder._silence_start_time = None
        recorder._silence_warning_count = 0
        recorder._silence_next_warning_wait = 10.0
        recorder._recent_rms_values.clear()
        recorder._recording_start_time = time.perf_counter()
        # Critical: reset the buffer-sample-rate tracker so a
        recorder._audio_pipeline._buffer_sr = None
        # reset the per-chunk VAD property cache so stale values
        recorder._cached_vad_enabled = False
        recorder._cached_use_silero_vad = False
        recorder._cached_silero_available = False
        recorder._cached_vad_resample_up_down = None
        recorder._cached_vad_resample_sr = None
        # Reset XRUN and clipping counters. STATE-OWNERSHIP: the
        _pipeline = recorder._audio_pipeline
        _pipeline._xruns = 0
        _pipeline._xrun_timestamps.clear()
        _pipeline._clip_count = 0
        _pipeline._peak = 0.0
        _pipeline._last_clip_log_time = 0.0
        recorder._last_rms = 0.0
        # reset VAD state machine.
        recorder._vad.reset()
        recorder._vad.state = VadState.UNKNOWN
        recorder._vad.consecutive_speech_frames = 0
        recorder._vad.consecutive_silence_frames = 0
        recorder._vad.speech_threshold_db = DEFAULT_VAD_SPEECH_THRESHOLD_DB
        recorder._vad.silence_threshold_db = DEFAULT_VAD_SILENCE_THRESHOLD_DB
        # reset auto-calibration
        recorder._vad.calibration_rms_values = []
        recorder._vad.calibrated = False
        # STREAM-FIX: reset user-stop-pending flag for the new
        recorder._user_stop_pending = False
        # ADR 0007 §3.5: AGC reset deleted (method removed).
        for chunk in recorder._preroll_buffer:
            if isinstance(chunk, np.ndarray):
                chunk.fill(0)
        recorder._preroll_buffer.clear()
        # AUDIO-HOT: reset disconnect state
        recorder._devices._device_disconnected = False
        recorder._devices._device_disconnect_retries = 0
        # Sliding-window flap detection: clear the restart-
        recorder._restart_timestamps.clear()
        # reset ring buffer drop counter for the new session
        recorder._dropped_ring_chunks = 0
        # AUDIO-HOT: reset periodic device check counter
        recorder._devices._device_check_counter = 0
        # PERF-: cache the target sample rate once at start()
        recorder._cached_target_sr = recorder.config.sample_rate

        # AUDIO-PROC: reset filter state for a new session so the
        if recorder._audio_processor is not None:
            recorder._audio_processor.reset()

    def cache_session_config(self, recorder: Any) -> int:
        """Cache config-derived session scalars (a ``SessionState``"""
        _silence_warning = recorder.config.silence_warning_seconds
        # stop_on_silence_seconds is a Config dataclass field (default
        _stop_on_silence = recorder.config.stop_on_silence_seconds
        _silence = float(_silence_warning) if isinstance(_silence_warning, int | float) else 20.0
        recorder._cached_silence_warning = _silence
        _stop_silence = float(_stop_on_silence) if isinstance(_stop_on_silence, int | float) else 60.0
        recorder._cached_stop_on_silence = _stop_silence
        # Single explicit field replaces the old 3-field split
        recorder._cached_max_recording_time = int(recorder.config.max_recording_time_seconds)

        # dynamic buffer sizing is DEFERRED until after the
        return coerce_max_recording_time(recorder._cached_max_recording_time)

    # ── Secure cache clearing (bulk. NOT _secure_clear_session_caches) ─

    def secure_clear_caches(self, recorder: Any) -> None:
        """Securely clear cached audio arrays (a ``SessionState`` method"""
        # Direct owning-module call (C-ARCH-2): the historical
        try:
            if recorder._cached_resampled is not None and recorder._cached_resampled.size > 0:
                _secure_clear_array(recorder._cached_resampled)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled",
                exc_info=True,
            )
        try:
            if recorder._cached_no_resample_arr is not None and recorder._cached_no_resample_arr.size > 0:
                _secure_clear_array(recorder._cached_no_resample_arr)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_arr",
                exc_info=True,
            )
        # (High): the resample-path segment list
        try:
            for seg in recorder._cached_resampled_segments:
                if seg is not None and seg.size > 0:
                    _secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled_segments",
                exc_info=True,
            )
        # (High): mirror the resample-path loop above for the
        try:
            for seg in recorder._cached_no_resample_segments:
                if seg is not None and seg.size > 0:
                    _secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_segments",
                exc_info=True,
            )
        recorder._cached_resampled = np.array([], dtype=np.float32)
        recorder._cached_resampled_len = 0
        recorder._cached_no_resample_arr = None
        recorder._cached_native_chunk_count = 0
        recorder._cached_no_resample_len = -1
        # now that every segment's underlying numpy buffer has
        recorder._cached_resampled_segments = []
        recorder._cached_resampled_concat_dirty = False
        recorder._cached_no_resample_segments = []
        recorder._cached_no_resample_concat_dirty = False
        # reset the audio processor's filter state too.
        try:
            if recorder._audio_processor is not None:
                recorder._audio_processor.reset()
        except Exception:
            log.debug(
                "[RECORDER] _audio_processor.reset() in _secure_clear_caches failed",
                exc_info=True,
            )
        # Critical: reset the buffer-sample-rate tracker so a
        recorder._audio_pipeline._buffer_sr = None

    def resize_buffers_for_sample_rate(self, recorder: Any, effective_sr: int, max_rec: int) -> None:
        """Resize the buffers for the effective sample rate (a"""
        sizing_sr = effective_sr if effective_sr > 0 else recorder.config.sample_rate
        if sizing_sr <= 0:
            sizing_sr = recorder.config.sample_rate
        # Rate-scaled ~32 ms blocks, the SAME blocksize the stream was
        blocksize = scaled_audio_blocksize(sizing_sr)
        chunk_seconds = blocksize / sizing_sr if sizing_sr > 0 else 0.064

        if max_rec > 0 and chunk_seconds > 0:
            needed_chunks = int(max_rec / chunk_seconds) + 1000  # +1K safety
            current_maxlen = recorder._audio_pipeline._buffer.maxlen or 0
            if needed_chunks > current_maxlen:
                if hasattr(recorder._audio_pipeline._buffer, "set_hard_cap"):
                    # Contiguous storage: raise the chunk-count cap and the
                    recorder._audio_pipeline._buffer.maxlen = needed_chunks
                    recorder._audio_pipeline._buffer.set_hard_cap(needed_chunks * 2 * blocksize)
                else:
                    # buffer (defensive. Start() clears the buffer, so
                    old_data = list(recorder._audio_pipeline._buffer)
                    recorder._audio_pipeline._buffer = collections.deque(old_data, maxlen=needed_chunks)
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
        _ring_buffer_seconds = float(os.environ.get("VOICE_TYPER_RING_BUFFER_SECONDS", "2.0"))
        if sizing_sr > 0:
            new_ring_capacity = int(sizing_sr / blocksize * _ring_buffer_seconds)
            # floor at 64 chunks so a 16 kHz / 512-block device
            if new_ring_capacity < 64:
                new_ring_capacity = 64
            # Guard against the LIVE ring capacity, not the module
            _prev_ring_maxlen = recorder._ring_buffer.maxlen
            if new_ring_capacity > 0 and (_prev_ring_maxlen is None or new_ring_capacity != _prev_ring_maxlen):
                # (defensive -- start() clears the ring buffer in
                _old_ring = list(recorder._ring_buffer)
                recorder._ring_buffer = collections.deque(_old_ring, maxlen=new_ring_capacity)
                log.debug(
                    "[RECORDING] Ring buffer sized for ~%.1fs at %d Hz (blocksize=%d): %d chunks (was %d)",
                    _ring_buffer_seconds,
                    sizing_sr,
                    blocksize,
                    new_ring_capacity,
                    # report the LIVE previous capacity (0 marks the
                    _prev_ring_maxlen if _prev_ring_maxlen is not None else 0,
                )

        # re-size the pre-roll deque using the effective sample
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

    def prepend_preroll_to_buffer(self, recorder: Any) -> None:
        """Prepend the preroll buffer to the main buffer (a"""
        if recorder._preroll_buffer:
            preroll_chunks = list(recorder._preroll_buffer)
            if preroll_chunks:
                for chunk in reversed(preroll_chunks):
                    mono_chunk = ensure_mono(recorder, chunk)
                    # Best-effort filter: if the processor raises, keep the raw chunk.
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
                    recorder._audio_pipeline._buffer.appendleft(mono_chunk.copy())
                # Real chunk size for the duration estimate: the
                _preroll_chunk_s = scaled_audio_blocksize(recorder._effective_sr) / recorder._effective_sr
                log.debug(
                    "[RECORDING] Prepended %d pre-roll chunks (~%.1fs)",
                    len(preroll_chunks),
                    len(preroll_chunks) * _preroll_chunk_s,
                )
                # zero + clear the pre-roll deque after the
                for _chunk in preroll_chunks:
                    if isinstance(_chunk, np.ndarray):
                        _chunk.fill(0)
                recorder._preroll_buffer.clear()


np = lazy_module("numpy")
