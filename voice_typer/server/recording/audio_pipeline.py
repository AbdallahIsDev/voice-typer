"""Audio-chunk processing pipeline for Recorder (filters/VAD/telemetry).

C-ARCH-2: patch owning submodule attributes. RACE-001: minimize lock scope
on buffer append. NOTE: see docs/code-notes/recording.md#package-layout
"""

from __future__ import annotations

import collections
import contextlib
import logging
import math
import os
import queue
import threading
import time
from typing import Any

from voice_typer.server._audio_constants import (
    AUDIO_SILENCE_RMS,
    SILERO_VAD_SAMPLE_RATES,
    WHISPER_SAMPLE_RATE,
)
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.recording import resampling as _resampling_mod
from voice_typer.server.recording.format import ensure_mono
from voice_typer.server.recording.vad_helpers import refresh_vad_caches, vad_auto_calibrate, vad_update
from voice_typer.server.vad import compute_vad_prob
from voice_typer.server.vad_processor import VadState

# All submodules use the package-level logger so log records propagate
log = logging.getLogger("voice_typer.server.recording")


# ``scipy.signal`` is bound lazily (see :func:`_ensure_sp_signal`) so
_sp_signal: Any | None = None


def _ensure_sp_signal() -> Any | None:
    """Import ``scipy.signal`` lazily on first VAD use.

    Returns the ``scipy.signal`` module, or ``None`` when scipy is
    """
    global _sp_signal
    if _sp_signal is not None:
        return _sp_signal
    try:
        from scipy import signal as _sp_signal  # noqa: N816
    except Exception:  # scipy not installed or broken (e.g. numpy/scipy version mismatch)
        _sp_signal = None
    return _sp_signal


# Near-silence bypass thresholds (linear peak amplitude). Chunks that
_NEAR_SILENCE_BYPASS_PEAK = 0.002
_NEAR_SILENCE_BYPASS_PEAK_CEILING = 0.01

# XRUN rolling window parameters
_XRUN_WINDOW_MAXLEN = 10  # keep last 10 xrun timestamps
_XRUN_ALERT_THRESHOLD = 5  # alert if N xruns in the window
_XRUN_ALERT_PERIOD = 10.0  # ...within M seconds


# PERF- / MAX_BUFFER_CHUNKS is dynamically adjusted in start() based
BUFFER_WARNING_THRESHOLD = 5000
TELEMETRY_LOG_INTERVAL = 1000

# the periodic buffer-telemetry log is diagnostic noise for the vast
_BUFFER_TELEMETRY_ENABLED = os.environ.get("VOICE_TYPER_VERBOSE", "").lower() in (
    "1",
    "true",
    "yes",
)


class AudioPipeline:
    """Audio-chunk processing pipeline for :class:`Recorder`."""

    def __init__(self, recorder: Any) -> None:
        # Collaborator back-reference. Typed ``Any`` to avoid a circular
        self._recorder = recorder
        from ._recorder_split import GrowableRecordingBuffer
        from .recorder import DEFAULT_MAX_BUFFER_CHUNKS

        self._buffer: Any = GrowableRecordingBuffer(
            maxlen=DEFAULT_MAX_BUFFER_CHUNKS,
            nominal_sample_rate=recorder.config.sample_rate,
            on_extra_eviction=recorder._note_buffer_capacity_eviction,
        )
        self._lock = threading.Lock()
        # Sample rate of the audio currently held in ``_buffer``
        self._buffer_sr: int | None = None
        # Number of chunks appended to ``_buffer`` this session.
        self._chunk_count: int = 0
        # PERF: O(1) running total of buffered samples (maintained by
        self._total_buffered_samples: int = 0
        # STATE-OWNERSHIP: the XRUN / clipping / peak telemetry
        self._xruns: int = 0
        self._xrun_threshold: int = 10  # notify after this many xruns
        # rolling window of xrun timestamps for rate-limited logging
        self._xrun_timestamps: collections.deque = collections.deque(maxlen=_XRUN_WINDOW_MAXLEN)
        self._clip_count: int = 0
        self._peak: float = 0.0
        self._last_clip_log_time: float = 0.0

    def detect_device_disconnect(self, indata: np.ndarray) -> bool:
        """Detect a USB/BT device disconnect via zero-filled input (HOTKEY-CRASH).

        Returns ``True`` if a disconnect was detected and the worker
        """
        recorder = self._recorder
        if not ((indata.size == 0 or not np.any(indata)) and self._chunk_count > 10):
            return False
        if recorder._devices._device_disconnected:
            return True
        # HOTKEY-CRASH: double-check that recording is still active.
        if not recorder._recording_event.is_set():
            return True  # deliberate stop, not a disconnect
        recorder._devices._device_disconnected = True
        # New disconnect cycle, clear the single-flight guard so a
        recorder._disconnect_handler._single_flight_running = False
        log.warning("[RECORDING] Zero-filled indata detected, possible device disconnect")
        # Schedule disconnect handling off the worker thread.
        _captured_gen = recorder._stop_generation
        # use _spawn_device_thread so the handler is registered with
        recorder._spawn_device_thread(
            name="device-disconnect-handler",
            target=recorder._handle_device_disconnect,
            kwargs={"_captured_generation": _captured_gen},
            single_flight=True,
        )
        return True

    def handle_xrun_status(self, status: Any) -> bool:
        """Inspect the PortAudio ``status`` for an input-overflow XRUN.

        Returns ``True`` if an XRUN was detected and the chunk should be
        """
        recorder = self._recorder
        _xrun_overflow = False
        if status:
            _overflow_attr = getattr(status, "input_overflow", None)
            if _overflow_attr is True:
                _xrun_overflow = True
            elif _overflow_attr is None and isinstance(status, int):
                # PortAudio paInputOverflow = bit 1 (value 2).
                _xrun_overflow = bool(status & 2)
        if not _xrun_overflow:
            return False
        # STATE-OWNERSHIP: the xrun counters live on THIS pipeline
        self._xruns += 1
        now = time.monotonic()
        self._xrun_timestamps.append(now)
        # check rolling window, only log if threshold
        window_start = now - _XRUN_ALERT_PERIOD
        recent_count = sum(1 for t in self._xrun_timestamps if t >= window_start)
        if recent_count >= _XRUN_ALERT_THRESHOLD or self._xruns == 1:
            log.warning(
                "[RECORDING] PortAudio status flag: %s (xrun_count=%d, recent=%d/%.0fs)",
                status,
                self._xruns,
                recent_count,
                _XRUN_ALERT_PERIOD,
            )
        if self._xruns % self._xrun_threshold == 0 and recorder.on_xrun_threshold:
            with contextlib.suppress(Exception):
                recorder.on_xrun_threshold(self._xruns)
        # Drop the partial chunk on xrun status: PortAudio marks the
        # chunk untrustworthy after an overflow/underflow.
        return True

    def apply_filter_chain(self, indata: np.ndarray) -> np.ndarray:
        """Convert multi-channel input to mono and apply the real-time filter chain."""
        recorder = self._recorder
        indata_mono = ensure_mono(recorder, indata)
        # Near-silence fast path: skip the filter chain for chunks that
        if indata_mono.size:
            _should_bypass = False
            try:
                _flat = indata_mono.reshape(-1)
                _bypass_peak = max(float(_flat.max()), -float(_flat.min()))
            except Exception:
                _bypass_peak = float("inf")
            if _bypass_peak < _NEAR_SILENCE_BYPASS_PEAK:
                _should_bypass = True
            elif _bypass_peak < _NEAR_SILENCE_BYPASS_PEAK_CEILING:
                # Ambient band: only pay the RMS reduction when peak
                try:
                    _bypass_rms = float(np.sqrt(np.dot(_flat, _flat) / _flat.size))
                except Exception:
                    _bypass_rms = float("inf")
                _should_bypass = _bypass_rms < AUDIO_SILENCE_RMS
            if _should_bypass:
                _proc = recorder._audio_processor
                _proc_sr = getattr(_proc, "_sample_rate", None) if _proc is not None else None
                if _proc is None or _proc_sr == recorder._effective_sr:
                    self._buffer_sr = recorder._effective_sr
                    return np.zeros_like(indata_mono)
        if recorder._audio_processor is not None:
            # etc. all allocate). The defensive copy was duplicating
            filtered = recorder._audio_processor.process_chunk(indata_mono, input_sample_rate=recorder._effective_sr)
            # Critical: the AudioProcessor resamples each chunk to its
            proc_sr = getattr(recorder._audio_processor, "_sample_rate", None)
            self._buffer_sr = int(proc_sr) if proc_sr is not None else recorder._effective_sr
        else:
            filtered = indata_mono
            # Critical: no processor → no resampling happened, so the
            self._buffer_sr = recorder._effective_sr
        return filtered

    def append_to_buffer_locked(self, filtered: np.ndarray) -> tuple[int, int]:
        """Append ``filtered`` to ``_buffer`` under the lock; return ``(chunk_count, buffer_len)``.

                RACE-001: minimize lock scope, only buffer append and counter
                need atomicity. Callback refs and silence state are read outside
                the lock, these are set once at start() and cleared at stop(),
                so a torn read just means we miss one callback or fire one
                extra, which is acceptable. The alternative (holding the lock
                while calling user code) risks deadlocks.

        Backpressure detection, if the deque dropped chunks
                (maxlen exceeded), increment a counter and warn the user.
        """
        recorder = self._recorder
        with self._lock:
            # cleaned signal. PERF-12: ``filtered`` is already an owned
            _buf = self._buffer
            _maxlen = _buf.maxlen
            if _maxlen is not None and len(_buf) >= _maxlen:
                try:
                    _evicted = _buf[0]
                except IndexError:
                    # Empty-buffer race (rare), nothing was evicted.
                    _evicted = None
                if _evicted is not None:
                    try:
                        self._total_buffered_samples -= int(_evicted.shape[0])
                    except (AttributeError, TypeError):
                        # Defensive: a malformed evicted chunk (rare)
                        with contextlib.suppress(TypeError):
                            self._total_buffered_samples -= len(_evicted)
            _buf.append(filtered)
            self._chunk_count += 1
            # ``filtered.shape[0]`` is the number of samples in the
            try:
                self._total_buffered_samples += int(filtered.shape[0])
            except (AttributeError, IndexError):
                # Defensive: a malformed chunk without ``shape`` (rare)
                self._total_buffered_samples += len(filtered)
            chunk_count = self._chunk_count
            buffer_len = len(_buf)

        # Backpressure detection, if the deque dropped
        if self._buffer.maxlen is not None and buffer_len >= self._buffer.maxlen - 1:
            recorder._dropped_chunks = recorder._dropped_chunks + 1
            if recorder._dropped_chunks == 1 or recorder._dropped_chunks % 100 == 0:
                log.warning(
                    "[RECORDING] Buffer full, oldest audio dropped (total=%d). ASR is slower than real-time.",
                    recorder._dropped_chunks,
                )
        return chunk_count, buffer_len

    def compute_rms_and_peak(self, filtered: np.ndarray) -> tuple[float, float, float]:
        """Compute ``(chunk_rms, chunk_peak, chunk_duration)`` for the filtered chunk."""
        recorder = self._recorder
        if filtered.size:
            # AUDIO-NP: single-pass RMS using np.dot, avoids creating
            flat = filtered.reshape(-1)
            chunk_rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            # PERF-: allocation-free peak, reuse the existing
            chunk_peak = max(float(flat.max()), -float(flat.min()))
        else:
            chunk_peak = 0.0
            chunk_rms = 0.0
        chunk_duration = len(filtered) / recorder._effective_sr
        return chunk_rms, chunk_peak, chunk_duration

    def run_vad_state_machine(
        self,
        filtered: np.ndarray,
        chunk_rms: float,
        chunk_duration: float,
        perf_ts: float,
        chunk_count: int,
        buffer_len: int,
        recording_start: float,
        silence_warning_cb: Any,
        silence_auto_stop_cb: Any,
        max_duration_cb: Any,
    ) -> None:
        """Run the VAD state machine + silence/max-duration auto-stop callbacks."""
        recorder = self._recorder
        # Auto-calibrate on RAW RMS (post-process_chunk rate path uses _buffer_sr).
        _raw_chunk_rms = getattr(self, "_pending_raw_chunk_rms", None)
        if _raw_chunk_rms is None:
            _raw_chunk_rms = chunk_rms
        vad_auto_calibrate(recorder, _raw_chunk_rms, chunk_duration)

        vad_prob = None
        if recorder._cached_vad_enabled and recorder._cached_use_silero_vad and recorder._cached_silero_available:
            try:
                _vad_sr = self._buffer_sr if self._buffer_sr is not None else recorder._effective_sr
                if _vad_sr != recorder._cached_vad_resample_sr:
                    # ``_buffer_sr`` changed since the cache was last
                    refresh_vad_caches(recorder)
                # lazily import scipy.signal on first VAD resample —
                _ensure_sp_signal()
                _up_down = recorder._cached_vad_resample_up_down
                if _up_down is not None:
                    try:
                        _up, _down = _up_down
                        # Use the cached FIR-tap path (same as
                        try:
                            taps = _resampling_mod._get_resample_fir_taps(_up, _down)
                            # ``_get_resample_fir_taps`` returns the
                            vad_audio = _resampling_mod._resample_via_cached_taps(
                                taps,
                                filtered.ravel(),
                                _up,
                                _down,
                                _sp_signal.upfirdn,
                            )
                        except Exception:
                            # Fall back to ``resample_poly`` if
                            resample_poly = _resampling_mod._get_resample_poly()
                            vad_audio = np.asarray(
                                resample_poly(
                                    filtered.ravel(),
                                    _up,
                                    _down,
                                ),
                                dtype=np.float32,
                            )
                        vad_sr = WHISPER_SAMPLE_RATE
                    except Exception:
                        # scipy unavailable or resample failed, fall
                        vad_audio = filtered
                        vad_sr = _vad_sr
                else:
                    # ``_buffer_sr`` is already 8000 or 16000, no
                    vad_audio = filtered
                    vad_sr = _vad_sr if _vad_sr in SILERO_VAD_SAMPLE_RATES else WHISPER_SAMPLE_RATE
                vad_prob = compute_vad_prob(vad_audio, vad_sr)
            except Exception:
                vad_prob = None  # fall back to RMS

        # VAD state machine with hysteresis
        chunk_rms_db = 20.0 * math.log10(chunk_rms) if chunk_rms > 0 else -90.0
        vad_state = vad_update(recorder, chunk_rms_db, vad_prob=vad_prob)

        if vad_state == VadState.SILENCE:
            if recorder._silence_start_time is None:
                recorder._silence_start_time = perf_ts
            recorder._silence_timer = perf_ts - recorder._silence_start_time
        else:
            recorder._silence_start_time = None
            recorder._silence_timer = 0.0

        # Use cached config values (PERF-)
        silence_warning_seconds = recorder._cached_silence_warning
        stop_on_silence_seconds = recorder._cached_stop_on_silence

        # H12a: Repeating silence warnings with exponential backoff
        if recorder._silence_timer >= silence_warning_seconds:
            time_since_first_warning = recorder._silence_timer - silence_warning_seconds
            expected_warnings = 0
            cumulative = 0.0
            wait = 10.0
            while cumulative <= time_since_first_warning:
                expected_warnings += 1
                cumulative += wait
                wait *= 2
            if expected_warnings > recorder._silence_warning_count:
                recorder._silence_warning_count = expected_warnings
                if silence_warning_cb is not None:
                    with contextlib.suppress(Exception):
                        silence_warning_cb()

        if recorder._silence_timer >= stop_on_silence_seconds and silence_auto_stop_cb is not None:
            with contextlib.suppress(Exception):
                silence_auto_stop_cb()

        # H12b: Maximum recording duration auto-stop
        recording_duration = time.perf_counter() - recording_start
        max_recording_time_seconds = recorder._cached_max_recording_time
        if recording_duration >= max_recording_time_seconds and max_duration_cb is not None:
            with contextlib.suppress(Exception):
                max_duration_cb()

        if chunk_count == BUFFER_WARNING_THRESHOLD:
            log.warning("[RECORDING] Buffer is large (5k chunks, ~5 min). Consider stopping recording.")
        if _BUFFER_TELEMETRY_ENABLED and chunk_count % TELEMETRY_LOG_INTERVAL == 0:
            log.debug(
                "[RECORDING] Buffer telemetry: chunks=%d, buffer_count=%d",
                chunk_count,
                buffer_len,
            )

    def detect_and_emit_clipping(self, recorder: Any, chunk_peak: float) -> None:
        """AUDIO-CLIP: track clipping + push a real-time IPC event.

        The historical ``Recorder._detect_and_emit_clipping`` pure
        delegator was removed: this ``AudioPipeline`` method is invoked
        directly by ``process_audio_chunk``. Extracted from
        ``process_audio_chunk`` for testability and readability. The
        ``audio_clip`` event is throttled to 1 Hz (same as the log) so
        the IPC channel isn't flooded. The event is enqueued on a
        non-blocking ``queue.Queue`` and drained by a dedicated event
        worker thread (see ``capture.AudioCallbackDispatcher``). This
        keeps the audio worker thread off the IPC transport - a slow
        TCP subscriber (or a blocked predecessor renderer) can no longer
        stall the worker and cause ring-buffer overflows / dropped
        audio. ``put_nowait`` + ``queue.Full`` suppression so a
        backed-up event worker can never block the audio thread.

        Side effects: increments ``self._clip_count`` (owned here),
        updates ``self._peak`` and ``self._last_clip_log_time``, may
        push an event to ``recorder._event_queue``.
        """
        if chunk_peak >= 0.99:
            # STATE-OWNERSHIP: clip/peak telemetry lives on THIS pipeline.
            self._clip_count += 1
            if chunk_peak > self._peak:
                self._peak = chunk_peak
            now = time.perf_counter()
            if now - self._last_clip_log_time >= 1.0:
                log.debug(
                    "[RECORDING] Clipping detected: peak=%.4f, count=%d chunks.",
                    chunk_peak,
                    self._clip_count,
                )
                self._last_clip_log_time = now
                with contextlib.suppress(queue.Full):
                    recorder._event_queue.put_nowait(
                        {
                            "type": "audio_clip",
                            "data": {
                                "peak": float(chunk_peak),
                                "count": int(self._clip_count),
                            },
                        }
                    )

    def process_audio_chunk(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
        perf_ts: float,
    ) -> None:
        """Body of :meth:`Recorder._process_audio_chunk`, runs on the worker thread."""
        recorder = self._recorder
        # HOTKEY-CRASH: device disconnect detection (early return path).
        if self.detect_device_disconnect(indata):
            return

        # XRUN status flag handling (early return path on overflow).
        if self.handle_xrun_status(status):
            return

        # the old PERF-011 frame-skip logic

        # Compute the RAW (pre-filter) chunk RMS from ``indata`` for
        if recorder._cached_vad_enabled and indata.size:
            _raw_flat = indata.reshape(-1)
            self._pending_raw_chunk_rms = float(np.sqrt(np.dot(_raw_flat, _raw_flat) / _raw_flat.size))
        else:
            self._pending_raw_chunk_rms = 0.0

        # AUDIO-CH + AUDIO-PROC: mono conversion + real-time noise filtering.
        filtered = self.apply_filter_chain(indata)

        # Buffer append + chunk count + backpressure detection.
        chunk_count, buffer_len = self.append_to_buffer_locked(filtered)

        # Read callback refs outside the lock, these are set once
        rms_callback = recorder.on_rms_level
        silence_warning_cb = recorder.on_silence_warning
        silence_auto_stop_cb = recorder.on_silence_auto_stop
        max_duration_cb = recorder.on_max_duration_auto_stop
        # above, was also dead: see the RACE-003 note above).
        recording_start = recorder._recording_start_time

        # RMS / peak / chunk-duration computation (operates on FILTERED
        chunk_rms, chunk_peak, chunk_duration = self.compute_rms_and_peak(filtered)

        # ADR 0007 §3.5: the old per-chunk AGC (_agc_update, C1)

        # Plain float assignment: a single-word store needs no lock, and the
        # discard path writes it lock-free too (consistent discipline).
        recorder._last_rms = chunk_rms

        # AUDIO-CLIP: Track clipping + push IPC event (delegated to a
        self.detect_and_emit_clipping(recorder, chunk_peak)

        # PERF-11: append to the live deque (atomic under
        recorder._recent_rms_values.append(chunk_rms)

        # +  + H12: VAD auto-calibration + Silero
        self.run_vad_state_machine(
            filtered,
            chunk_rms,
            chunk_duration,
            perf_ts,
            chunk_count,
            buffer_len,
            recording_start,
            silence_warning_cb,
            silence_auto_stop_cb,
            max_duration_cb,
        )

        if rms_callback is not None:
            try:
                rms_callback(chunk_rms, chunk_peak)
            except Exception:
                recorder._rms_callback_error_count = recorder._rms_callback_error_count + 1
                if recorder._rms_callback_error_count == 1 or recorder._rms_callback_error_count % 100 == 0:
                    log.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        recorder._rms_callback_error_count,
                        exc_info=True,
                    )
                else:
                    log.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d, traceback suppressed)",
                        recorder._rms_callback_error_count,
                    )


np = lazy_module("numpy")
