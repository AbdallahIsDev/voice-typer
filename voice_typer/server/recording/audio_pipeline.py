"""Audio-chunk processing pipeline for :class:`Recorder` (extracted from ``recorder.py``).

The six named helpers that were split out of
``Recorder._process_audio_chunk`` in a previous session
(``_detect_device_disconnect``, ``_handle_xrun_status``,
``_apply_filter_chain``, ``_append_to_buffer_locked``,
``_compute_rms_and_peak``, ``_run_vad_state_machine``) are moved here.
``Recorder`` keeps 1-line delegator methods so existing call sites,
subclass overrides, and ``inspect.getsource`` checks that look for the
methods on the ``Recorder`` class continue to work.

Collaborator pattern
--------------------
:class:`AudioPipeline` is constructed by ``Recorder.__init__`` with a
back-reference to the owning ``Recorder`` instance
(``AudioPipeline(recorder)``). The collaborator reference is used to
access *shared* state that lives on ``Recorder`` and is NOT moved here:

- ``self._recorder._chunk_count`` / ``_buffer`` / ``_lock`` — buffer state
- ``self._recorder._effective_sr`` / ``_buffer_sr`` — sample-rate tracking
- ``self._recorder._audio_processor`` — filter chain
- ``self._recorder._xruns`` / ``_xrun_timestamps`` / ``_xrun_threshold`` /
  ``on_xrun_threshold`` — XRUN tracking + callback
- ``self._recorder._device_disconnected`` / ``_disconnect_handler_running`` /
  ``_stop_generation`` / ``_recording_event`` — disconnect detection state
- ``self._recorder._spawn_device_thread`` / ``_handle_device_disconnect`` —
  disconnect-handler scheduling
- ``self._recorder._vad_auto_calibrate`` / ``_vad_update`` /
  ``_refresh_vad_caches`` / cached VAD properties — VAD state machine
- ``self._recorder._silence_timer`` / ``_silence_start_time`` /
  ``_silence_warning_count`` / cached silence thresholds — silence auto-stop
- ``self._recorder._recording_start_time`` / ``on_rms_level`` /
  ``on_silence_warning`` / ``on_silence_auto_stop`` /
  ``on_max_duration_auto_stop`` — recording session + callbacks

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
to inject fake resample behavior. The ``_recording_pkg._get_resample_poly()``
indirection (see the module docstring of :mod:`.recorder` §Patch-path) is
preserved here so the patch takes effect at call time.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from voice_typer.server import recording as _recording_pkg
from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES, WHISPER_SAMPLE_RATE
from voice_typer.server.vad import compute_vad_prob
from voice_typer.server.vad_processor import VadState

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")

if TYPE_CHECKING:
    from .recorder import Recorder


# XRUN rolling window parameters
_XRUN_ALERT_THRESHOLD = 5  # alert if N xruns in the window
_XRUN_ALERT_PERIOD = 10.0  # ...within M seconds


# PERF- / MAX_BUFFER_CHUNKS is dynamically adjusted in start() based
# on max_recording_time_seconds AND the device's effective sample rate.
BUFFER_WARNING_THRESHOLD = 5000
TELEMETRY_LOG_INTERVAL = 1000

# the periodic buffer-telemetry log is diagnostic noise for the vast
# majority of users. Gated behind VOICE_TYPER_VERBOSE so it only appears
# when someone is actively debugging audio/ring-buffer behaviour.
_BUFFER_TELEMETRY_ENABLED = os.environ.get("VOICE_TYPER_VERBOSE", "").lower() in (
    "1",
    "true",
    "yes",
)


class AudioPipeline:
    """Audio-chunk processing pipeline for :class:`Recorder`.

    Extracted from the six named helpers split out of
    ``Recorder._process_audio_chunk``. Each method on this class is the
    moved body of the corresponding ``Recorder._<helper>`` method, with
    ``self.X`` references rewritten to ``self._recorder.X`` for shared
    state. ``Recorder`` keeps 1-line delegators on each helper name so
    existing call sites and source-inspection checks continue to work.
    """

    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def detect_device_disconnect(self, indata: np.ndarray) -> bool:
        """Detect a USB/BT device disconnect via zero-filled input (HOTKEY-CRASH).

        Returns ``True`` if a disconnect was detected and the worker
        thread should skip the rest of ``_process_audio_chunk`` for this
        chunk (the chunk is either a deliberate stop draining zeros, or
        a real disconnect that has been scheduled for handler-thread
        recovery). Returns ``False`` when the chunk is normal non-zero
        audio that should continue through the processing pipeline.

        HOTKEY-CRASH: detect device disconnect (zero-filled indata
        when device is still "open" but USB/BT was unplugged).
        Guard against false positives during rapid hotkey toggling:
        when stop() clears _recording_event, PortAudio may deliver
        zero-filled frames as the stream drains. We must NOT treat
        those as device disconnects, because _handle_device_disconnect
        would race with the deliberate stop() to close the stream.

        Low: use ``not np.any(indata)`` instead of
        ``np.count_nonzero(indata) == 0``. ``np.any`` short-circuits
        at the first non-zero element (O(1) for the common non-zero
        case), while ``np.count_nonzero`` always scans all 512 samples
        (O(N) per chunk × 16 Hz = 8192 sample-scans/sec). The two are
        semantically equivalent: both return True iff every element is
        zero. Preserves the early-return semantics.
        """
        recorder = self._recorder
        if not ((indata.size == 0 or not np.any(indata)) and recorder._chunk_count > 10):
            return False
        # re-entrancy guard — if a previous chunk already detected
        # the disconnect and scheduled a handler thread, don't spawn
        # another. Pre-fix, every subsequent zero-filled chunk would
        # re-enter this block, set the flag again (no-op), and spawn
        # ANOTHER device-disconnect-handler thread — a thread-spawn
        # storm on truly silent (or disconnected) input. With 100
        # zero-filled callbacks after the warmup window, this would
        # spawn ~89 threads.
        #
        # The flag is cleared by _handle_device_disconnect on successful
        # stream restart and by start(), so this guard only suppresses
        # the storm during the retry window — it does NOT suppress a
        # legitimate re-detection after a successful restart.
        if recorder._device_disconnected:
            return True
        # HOTKEY-CRASH: double-check that recording is still active.
        # The early-return check in the callback passed, but stop() may
        # have cleared _recording_event between that check and this
        # point (the callback and worker run on different threads, so
        # the Event flag change is visible immediately).
        if not recorder._recording_event.is_set():
            return True  # deliberate stop, not a disconnect
        recorder._device_disconnected = True
        # New disconnect cycle — clear the single-flight guard so a
        # fresh handler can spawn even if a prior handler hasn't fully
        # exited yet (e.g. test simulating restart by clearing
        # _device_disconnected, then sending another zero chunk).
        recorder._disconnect_handler_running = False
        log.warning("[RECORDING] Zero-filled indata detected — possible device disconnect")
        # Schedule disconnect handling off the worker thread.
        # HOTKEY-CRASH: capture the current stop_generation so the
        # handler can bail if a stop/start cycle happened in between.
        _captured_gen = recorder._stop_generation
        # use _spawn_device_thread so the handler is registered with
        # thread_registry (when available) and single-flight guarded so
        # a flapping device can't spawn multiple concurrent handlers.
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
                dropped (the in-flight chunk is partially stale — appending it
                to ``_buffer`` would corrupt the transcriber's input with a
                discontinuity). Returns ``False`` for clean status so the
                processing pipeline continues.

        Check PortAudio status flags for XRUNs. Use a rolling
                window of xrun timestamps to reduce log spam while still
                alerting on sustained issues.
                Low: ``if status:`` is True for ANY set flag, including
                ``priming_output`` which fires on the first callback after every
                stream start (PortAudio is priming buffers — NOT an xrun).
                Pre-fix, this over-counted ``_xruns`` by 1 on every ``start()``.
                Narrow to ``status.input_overflow`` which is the real xrun flag
                for input streams.
                R18-F13: also accept a raw integer status whose bit 1
                (``paInputOverflow == 2`` in PortAudio's flag enum) is set —
                tests pass ``status=2`` to simulate a CallbackFlags object
                without constructing one. ``sounddevice.CallbackFlags`` is a
                subclass of ``int``, so this also covers the case where the flag
                object is passed but its ``.input_overflow`` attribute lookup
                is bypassed.
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
        recorder._xruns += 1
        now = time.monotonic()
        recorder._xrun_timestamps.append(now)
        # check rolling window — only log if threshold
        # exceeded within the alert period
        window_start = now - _XRUN_ALERT_PERIOD
        recent_count = sum(1 for t in recorder._xrun_timestamps if t >= window_start)
        if recent_count >= _XRUN_ALERT_THRESHOLD or recorder._xruns == 1:
            log.warning(
                "[RECORDING] PortAudio status flag: %s (xrun_count=%d, recent=%d/%.0fs)",
                status,
                recorder._xruns,
                recent_count,
                _XRUN_ALERT_PERIOD,
            )
        # Item 1: fire threshold callback for tray notification.
        # Low: use ``%`` instead of ``==`` so the callback fires every N
        # xruns (not just once at exactly N). Pre-fix, ``==`` fired
        # EXACTLY ONCE per session — when ``_xruns`` incremented from 9
        # to 10 — and never again. A user with 100+ xruns saw 1
        # notification then nothing.
        if recorder._xruns % recorder._xrun_threshold == 0 and recorder.on_xrun_threshold:
            with contextlib.suppress(Exception):
                recorder.on_xrun_threshold(recorder._xruns)
        # R18-F13: drop the partial chunk on xrun status. PortAudio
        # reports ``input_overflow`` when the callback couldn't keep up
        # — the in-flight chunk is partially stale (the backend overwrote
        # a portion of the buffer before the callback copied it out).
        # Appending it to ``_buffer`` would corrupt the transcriber's
        # input with a discontinuity. Return here so the chunk is
        # discarded; the next clean chunk resumes normal buffering.
        # ``_xruns`` was already incremented above so telemetry still
        # reflects the drop.
        return True

    def apply_filter_chain(self, indata: np.ndarray) -> np.ndarray:
        """Convert multi-channel input to mono and apply the real-time filter chain.

        AUDIO-CH: convert multi-channel input to mono via
        :meth:`_ensure_mono`.

        AUDIO-PROC: apply real-time noise filtering BEFORE the buffer
        append so (a) `filtered` is defined when we use it inside the
        lock, and (b) the stored audio, silence detection, and waveform
        bubble all see the cleaned signal that the transcriber will
        receive.  This runs OUTSIDE the lock — process_chunk() is
        non-blocking and operates only on the local `indata` copy.  See
        recording.py callback ordering in the auto-volume-duck
        architecture doc §6.4.

        Also updates ``recorder._buffer_sr`` to track the post-filter
        sample rate so :meth:`stop` / :meth:`snapshot` know whether to
        resample again. Pre-fix, ``_buffer_sr`` was never set, so
        ``stop()`` read ``_effective_sr`` (the device's 48 kHz) and the
        subsequent ``resample_poly(audio, 1, 3)`` decimated the 16 kHz
        audio 3:1 → chipmunk-pitched garbage on every non-16 kHz mic.
        """
        recorder = self._recorder
        indata_mono = recorder._ensure_mono(indata)
        if recorder._audio_processor is not None:
            # CRIT-6: pass the stream's native rate so the processor can
            # resample to the chain's construction rate (16 kHz) before
            # filtering. Without this argument the resampler is bypassed
            # and filters built at 16 kHz are fed native-rate audio
            # (e.g. 48 kHz), silently mistuning every coefficient.
            #
            # the previous ``indata_mono.copy()`` was a redundant
            # allocation on the worker hot path. ``indata`` is already
            # an owned copy (the audio callback did ``indata.copy()``
            # before enqueuing to the ring buffer — PortAudio reuses
            # the input buffer). ``_ensure_mono`` returns either the
            # same reference (mono input) or a fresh ``np.mean`` result
            # (stereo downmix). ``process_chunk`` does NOT mutate its
            # input — it returns a new array (resample_poly / lfilter /
            # etc. all allocate). The defensive copy was duplicating
            # the callback's already-owned copy for the common mono
            # case (~2KB per chunk at 60-94 Hz).
            filtered = recorder._audio_processor.process_chunk(indata_mono, input_sample_rate=recorder._effective_sr)
            # Critical: the AudioProcessor resamples each chunk to its
            # chain's construction rate (typically 16 kHz) before
            # filtering, so the audio appended to ``_buffer`` is at the
            # processor's rate — NOT the device's native rate. Track
            # this so ``stop()`` / ``snapshot()`` use the correct source
            # rate when deciding whether to resample again.
            proc_sr = getattr(recorder._audio_processor, "_sample_rate", None)
            recorder._buffer_sr = int(proc_sr) if proc_sr is not None else recorder._effective_sr
        else:
            filtered = indata_mono
            # Critical: no processor → no resampling happened, so the
            # buffer holds audio at the device's native rate. Track this
            # so ``stop()`` / ``snapshot()`` skip the resample.
            recorder._buffer_sr = recorder._effective_sr
        return filtered

    def append_to_buffer_locked(self, filtered: np.ndarray) -> tuple[int, int]:
        """Append ``filtered`` to ``_buffer`` under the lock; return ``(chunk_count, buffer_len)``.

                RACE-001: minimize lock scope — only buffer append and counter
                need atomicity. Callback refs and silence state are read outside
                the lock — these are set once at start() and cleared at stop(),
                so a torn read just means we miss one callback or fire one
                extra, which is acceptable. The alternative (holding the lock
                while calling user code) risks deadlocks.

        Backpressure detection — if the deque dropped chunks
                (maxlen exceeded), increment a counter and warn the user.
        """
        recorder = self._recorder
        with recorder._lock:
            # Store FILTERED audio so the transcriber receives the
            # cleaned signal. PERF-12: ``filtered`` is already an owned
            # array — in the processor branch, ``process_chunk`` is
            # called with ``indata_mono.copy()`` and either returns that
            # same owned copy (passthrough) or a fresh array from the
            # filter chain. In the no-processor branch, ``indata_mono``
            # is either the owned ``chunk_copy`` (ndim==1), a fresh
            # ``np.mean`` result (multi-channel downmix), or a view of
            # ``chunk_copy`` (reshape); numpy views keep their base
            # alive via ``.base``, so the buffer safely owns its data
            # without a redundant ``.copy()`` here (saves ~2KB
            # alloc/chunk at 16Hz).
            # PERF: maintain a running sample counter so
            # ``current_duration_seconds`` (polled at 4 Hz by the
            # streaming thread) is O(1) instead of O(chunks).
            # Incremented under the same lock as the buffer append so
            # the counter never drifts from the deque's contents.
            #
            # Eviction compensation: if ``_buffer.maxlen`` is set and
            # the deque is already full, ``append`` silently drops the
            # leftmost (oldest) chunk. We peek at ``buffer[0]`` BEFORE
            # the append and subtract its sample count so the running
            # total stays in sync with the deque's actual contents.
            # Without this compensation the counter would monotonically
            # grow (never shrink) and ``current_duration_seconds``
            # would over-report once the buffer fills — a regression
            # vs. the previous ``sum(int(c.shape[0]) for c in buffer)``
            # which naturally accounted for eviction by re-iterating
            # the deque on every call.
            _buf = recorder._buffer
            _maxlen = _buf.maxlen
            if _maxlen is not None and len(_buf) >= _maxlen:
                try:
                    _evicted = _buf[0]
                    recorder._total_buffered_samples -= int(_evicted.shape[0])
                except (AttributeError, IndexError, TypeError):
                    # Defensive: a malformed evicted chunk (rare)
                    # shouldn't corrupt the counter. Fall back to
                    # ``len(_evicted)`` which works for any sequence.
                    with contextlib.suppress(TypeError):
                        recorder._total_buffered_samples -= len(_evicted)
            _buf.append(filtered)
            recorder._chunk_count += 1
            # ``filtered.shape[0]`` is the number of samples in the
            # chunk (1-D mono after ``_ensure_mono`` /
            # ``process_chunk``). ``int()`` coerces the numpy int64 to
            # a Python int so the running sum stays a plain int (avoids
            # numpy scalar boxing on every increment).
            try:
                recorder._total_buffered_samples += int(filtered.shape[0])
            except (AttributeError, IndexError):
                # Defensive: a malformed chunk without ``shape`` (rare)
                # shouldn't corrupt the counter. Fall back to
                # ``len(filtered)`` which works for any sequence.
                recorder._total_buffered_samples += len(filtered)
            chunk_count = recorder._chunk_count
            buffer_len = len(_buf)

        # Backpressure detection — if the deque dropped
        # chunks (maxlen exceeded), increment a counter and warn the
        # user
        if recorder._buffer.maxlen is not None and buffer_len >= recorder._buffer.maxlen - 1:
            recorder._dropped_chunks = recorder._dropped_chunks + 1
            if recorder._dropped_chunks == 1 or recorder._dropped_chunks % 100 == 0:
                log.warning(
                    "[RECORDING] Buffer full — oldest audio dropped (total=%d). ASR is slower than real-time.",
                    recorder._dropped_chunks,
                )
        return chunk_count, buffer_len

    def compute_rms_and_peak(self, filtered: np.ndarray) -> tuple[float, float, float]:
        """Compute ``(chunk_rms, chunk_peak, chunk_duration)`` for the filtered chunk.

        RMS / peak computation (operates on FILTERED audio so the
        waveform bubble and silence detection see what the transcriber
        will see, not raw mic input).
        AUDIO-NP: use np.dot instead of np.mean(indata**2) to avoid the
        intermediate squared array allocation.
        """
        recorder = self._recorder
        if filtered.size:
            # AUDIO-NP: single-pass RMS using np.dot — avoids creating
            # the intermediate abs_filtered**2 array.
            flat = filtered.reshape(-1)
            chunk_rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            # PERF-: allocation-free peak — reuse the existing
            # ``flat`` view instead of materializing np.abs(filtered).
            # max(|x|) == max(max(x), -min(x)) — two reductions on the
            # same contiguous view, no intermediate array allocated.
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
        """Run the VAD state machine + silence/max-duration auto-stop callbacks.

        auto-calibrate VAD thresholds from ambient noise.
        Silero VAD probability (with resample to 16kHz).
        VAD state machine + silence timer.
                H12: silence warning / auto-stop / max-duration callbacks.

                The callback refs (``silence_warning_cb`` etc.) are passed in
                explicitly because they were already snapshotted outside the
                lock by the caller — re-reading them from ``self`` here would
                be a second torn read with no consistency guarantee. The RMS
                callback (``on_rms_level``) is fired separately by the caller
                after this method returns.
        """
        recorder = self._recorder
        # auto-calibrate VAD thresholds from ambient noise
        recorder._vad_auto_calibrate(chunk_rms, chunk_duration)

        # compute Silero VAD probability if enabled.
        # this previously ran in the audio callback
        # (~1-5ms for 512 samples on CPU) and was a real-time safety
        # violation. It now runs on the worker thread.
        # VAD-GATE: skip Silero inference when VAD is disabled (all
        # audio enhancements off) to avoid wasting CPU.
        # read the cached VAD properties (set at start() / on config
        # change) instead of dispatching 3 property lookups per chunk
        # × 16 Hz = 48 lookups/sec.
        vad_prob = None
        if recorder._cached_vad_enabled and recorder._cached_use_silero_vad and recorder._cached_silero_available:
            try:
                # impl-vad-fix: Silero VAD only accepts {8000, 16000} Hz.
                # The mic's native rate may be 44100 or 48000, which
                # previously raised:
                #   ValueError: Supported sampling rates: [8000, 16000]
                # Resample to 16000 using the same scipy resample_poly
                # path as _resample_audio_impl (gcd up/down pattern).
                #
                # High: use ``_buffer_sr`` (the post-process_chunk rate
                # set above) instead of ``_effective_sr`` (the device's
                # native rate). When a processor is active,
                # ``_buffer_sr == 16000`` and the VAD branch is skipped
                # entirely — no double-resample. Pre-fix used
                # ``_effective_sr`` (e.g. 48000) which caused
                # ``resample_poly(filtered, 1, 3)`` to decimate the
                # already-16 kHz audio 3:1 → ~170 samples presented to
                # Silero → speech probability systematically biased low
                # → silence_timer accumulated faster → recording
                # auto-stopped prematurely mid-sentence.
                #
                # use the cached (up, down) tuple instead of recomputing
                # ``math.gcd`` per chunk.
                _vad_sr = recorder._buffer_sr if recorder._buffer_sr is not None else recorder._effective_sr
                if _vad_sr != recorder._cached_vad_resample_sr:
                    # ``_buffer_sr`` changed since the cache was last
                    # computed (e.g. first chunk after start(), or a
                    # hot-plug rebuild that called set_sample_rate).
                    recorder._refresh_vad_caches()
                _up_down = recorder._cached_vad_resample_up_down
                if _up_down is not None:
                    try:
                        resample_poly = _recording_pkg._get_resample_poly()
                        _up, _down = _up_down
                        vad_audio = resample_poly(filtered.ravel(), _up, _down).astype(np.float32)
                        vad_sr = WHISPER_SAMPLE_RATE
                    except Exception:
                        # scipy unavailable or resample failed — fall
                        # back to RMS rather than crashing the worker.
                        vad_audio = filtered
                        vad_sr = _vad_sr
                else:
                    # ``_buffer_sr`` is already 8000 or 16000 — no
                    # resample needed, feed ``filtered`` directly.
                    vad_audio = filtered
                    vad_sr = _vad_sr if _vad_sr in SILERO_VAD_SAMPLE_RATES else WHISPER_SAMPLE_RATE
                vad_prob = compute_vad_prob(vad_audio, vad_sr)
            except Exception:
                vad_prob = None  # fall back to RMS

        # VAD state machine with hysteresis
        # Convert RMS to dBFS for VAD thresholds
        chunk_rms_db = 20.0 * math.log10(chunk_rms) if chunk_rms > 0 else -90.0
        vad_state = recorder._vad_update(chunk_rms_db, vad_prob=vad_prob)

        # Use VAD state machine for silence detection
        # Voice detected by loudness → reset silence timer
        # use ``perf_ts`` (captured in the RT callback when the chunk
        # arrived) instead of ``time.perf_counter()`` (worker time).
        # Pre-fix, the silence timer measured worker-processing time,
        # which inflated by the ring-buffer backlog when the worker
        # fell behind. With the prior reduced ring buffer (1s), the
        # backlog is bounded, but anchoring to callback-arrival time is
        # still more accurate: the silence auto-stop fires based on
        # when the user actually stopped speaking, not when the worker
        # happened to process the silent chunk. The ``perf_ts``
        # parameter was previously dead (added with intent to anchor
        # the silence timer but never actually used); this coordinates
        # with the prior fix to make it live.
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

    def process_audio_chunk(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
        perf_ts: float,
    ) -> None:
        """Body of :meth:`Recorder._process_audio_chunk` — runs on the worker thread.

        Phase 4.5 — extracted from :mod:`.recorder`. See the module
                docstring of :mod:`.recorder` for the full Patch-path compatibility
                rationale (the call sites to ``_detect_device_disconnect`` /
                ``_handle_xrun_status`` / etc. route through ``self._recorder.X``
                so test patches of the form
                ``monkeypatch.setattr(Recorder, "_detect_device_disconnect", fake)``
                continue to take effect).

                This method contains the heavy processing pipeline that was
                previously in the PortAudio callback (``_audio_callback_dispatch``).
                It is called by ``_audio_worker_loop`` for each chunk popped from
                the ring buffer.

                Operations (in order):
                - HOTKEY-CRASH: device disconnect detection (zero-fill + periodic)
        XRUN status flag handling + on_xrun_threshold callback
                - AUDIO-CH: mono conversion
                - AUDIO-PROC: filter chain application
                - Buffer append + chunk count + backpressure detection
                - RMS / peak computation
                - AUDIO-CLIP: clipping detection + IPC event push
        VAD auto-calibration
        Silero VAD probability (with resample to 16kHz)
        VAD state machine + silence timer
                - H12: silence warning / auto-stop / max-duration callbacks
                - T021: on_rms_level callback (filtered chunk forwarded)
                - Telemetry logs

                All of this previously ran on the real-time audio thread,
                violating the ~32ms deadline (scipy resample + Silero VAD can
                take 5-50ms combined). Moving it to the worker thread restores
                real-time safety.
        """
        recorder = self._recorder
        # HOTKEY-CRASH: device disconnect detection (early return path).
        if recorder._detect_device_disconnect(indata):
            return

        # the per-N-chunks blocking ``sd.query_devices()``
        # probe on the audio worker thread was removed — it is fully
        # redundant with ``_device_health_checker_loop`` (a dedicated
        # daemon thread that wakes every ``_device_check_interval_s``
        # and runs the same ``sd.query_devices(current_device)`` probe
        # with the same disconnect-handling logic). Running the probe
        # here cost a blocking RPC on the audio hot path every ~500
        # chunks; the health-checker thread covers the case off the
        # hot path. See ``_device_health_checker_loop`` and
        # ``_start_device_health_checker``.

        # NOTE: Dead-air timeout was REMOVED in
        # Redundant with stop_on_silence_seconds (auto-stop already resets on
        # speech). The _update_dead_air_simple() method was also removed along
        # with _dead_air_timeout / _dead_air_speech_detected / _dead_air_silence_start.
        # Do NOT re-add — it added no unique behavior.

        # XRUN status flag handling (early return path on overflow).
        if recorder._handle_xrun_status(status):
            return

        # the old PERF-011 frame-skip logic
        # (_previous_chunk_pending) is replaced by ring buffer overflow
        # detection in the callback. If the ring buffer was full, the
        # callback already logged a warning and dropped the chunk. By
        # the time we reach here, the chunk is in the ring buffer and
        # we must process it.

        # AUDIO-CH + AUDIO-PROC: mono conversion + real-time noise filtering.
        filtered = recorder._apply_filter_chain(indata)

        # Buffer append + chunk count + backpressure detection.
        chunk_count, buffer_len = recorder._append_to_buffer_locked(filtered)

        # Read callback refs outside the lock — these are set once
        # at start() and cleared at stop(), so a torn read just
        # means we miss one callback or fire one extra, which is
        # acceptable. The alternative (holding the lock while
        # calling user code) risks deadlocks.
        rms_callback = recorder.on_rms_level
        silence_warning_cb = recorder.on_silence_warning
        silence_auto_stop_cb = recorder.on_silence_auto_stop
        max_duration_cb = recorder.on_max_duration_auto_stop
        # the dead ``recent_rms = recent_rms_snapshot`` alias
        # was removed (its only writer, the snapshot inside the lock
        # above, was also dead — see the RACE-003 note above).
        recording_start = recorder._recording_start_time

        # ── Everything below runs OUTSIDE the lock ──

        # RMS / peak / chunk-duration computation (operates on FILTERED
        # audio so the waveform bubble and silence detection see what
        # the transcriber will see, not raw mic input).
        chunk_rms, chunk_peak, chunk_duration = recorder._compute_rms_and_peak(filtered)

        # ADR 0007 §3.5: the old per-chunk AGC (_agc_update, C1)
        # has been removed. It duplicated the Compressor filter in
        # the new audio filter chain. The Compressor now handles
        # dynamic range compression with proper attack/release.
        # _last_rms stores the post-filter RMS for UI/IPC.

        with recorder._lock:
            recorder._last_rms = chunk_rms

        # AUDIO-CLIP: Track clipping + push IPC event (delegated to a
        # dedicated helper so the clipping-detection logic is testable
        # in isolation and the heavy ``_process_audio_chunk`` body
        # stays readable).
        recorder._detect_and_emit_clipping(chunk_peak)

        # PERF-11: append to the live deque (atomic under
        # GIL — ``deque.append`` is a single C-level op with no torn
        # state). The live rolling-RMS consumer is this
        # ``self._recent_rms_values.append(chunk_rms)`` call, which
        # future code (e.g. waveform bubble, VAD auto-calibration) can
        # read via ``self._recent_rms_values`` under the same lock.
        recorder._recent_rms_values.append(chunk_rms)

        # +  + H12: VAD auto-calibration + Silero
        # VAD probability + VAD state machine + silence/max-duration
        # auto-stop callbacks + telemetry logs.
        recorder._run_vad_state_machine(
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

        # Fire RMS callback OUTSIDE the lock.
        # the ``audio_chunk`` parameter was REMOVED from the
        # ``on_rms_level`` callback contract.  The previous 3-arg form
        # ``rms_callback(chunk_rms, chunk_peak, filtered)`` forwarded
        # the filtered audio chunk so downstream consumers
        # (WaveformBubble via ``RecordingController.on_recorder_rms``)
        # COULD run Silero VAD on it — but BUBBLE- removed the
        # VAD gate entirely (the device's native sample-rate audio was
        # being fed to a model that assumes 16 kHz, biasing
        # probabilities low and collapsing the bars).  No current
        # consumer uses the chunk, so it was dead weight on the audio
        # hot path (a numpy reference count inc/dec per chunk at 16 Hz)
        # and a privacy surface (the raw audio chunk was held by every
        # listener even though none of them read it).  Callers MUST now
        # use the 2-arg signature ``rms_callback(chunk_rms, chunk_peak)``.
        if rms_callback is not None:
            try:
                rms_callback(chunk_rms, chunk_peak)
            except Exception:
                # previously this called
                # ``log.debug(..., exc_info=True)`` on EVERY
                # callback raise.  The audio callback fires at
                # ~16 Hz; a buggy downstream consumer (e.g. a VAD
                # that throws on every chunk) would trigger full
                # traceback formatting 16 times per second, which
                # is a significant CPU cost on the audio thread
                # and can cause XRUNs.  We now only format the
                # traceback on the FIRST raise and every 100th
                # subsequent raise; the rest are logged without
                # exc_info so the formatting cost is avoided.
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
