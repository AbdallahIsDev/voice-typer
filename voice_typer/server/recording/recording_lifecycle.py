"""Recording-session lifecycle bodies extracted from the ``Recorder`` class.

Owns :func:`start_recording` / :func:`stop_recording` /
:func:`discard_recording`; ``Recorder.start`` / ``stop`` / ``discard``
are 1-line delegators so existing call sites, subclass overrides, and
``inspect.getsource`` checks on the ``Recorder`` class keep working.
Tests patch this module's ``prepare_audio`` / ``refresh_vad_caches``
attributes, the owning-module seam (C-ARCH-2).
"""

from __future__ import annotations

import collections
import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import (
    peak_amplitude,
    scaled_audio_blocksize,
    silence_percent,
)
from voice_typer.server._lazy_import import lazy_module

from . import (
    buffer as _buffer_mod,  # owning module of the secure-clear helpers (C-ARCH-2)
    recording_buffer,
)
from .format import prepare_audio
from .vad_helpers import refresh_vad_caches

if TYPE_CHECKING:
    from .recorder import Recorder

log = logging.getLogger("voice_typer.server.recording")

np = lazy_module("numpy")


def discard_recording(recorder: Recorder) -> None:
    """Discard current recording without processing.

    Extracted verbatim from ``Recorder.discard`` ( split). The
        timeout constants ``_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S`` and
        ``_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S`` are imported lazily from
        :mod:`.recorder` to avoid a circular import (recorder.py imports this
        module at the top of its class body).
    """
    # Lazy import: recorder.py is still loading when this module is first
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S,
        _EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S,
    )

    recorder._recording_event.clear()
    # STREAM-FIX (Task 6): set _user_stop_pending before stream.stop() so
    recorder._user_stop_pending = True
    # 17-H-: increment stop_generation for symmetry with stop() so
    recorder._stop_generation += 1
    # guard _effective_sr reset with the lock so a concurrent
    with recorder._audio_pipeline._lock:
        recorder._effective_sr = recorder.config.sample_rate
        # reset ``_buffer_sr`` to ``None`` so the next session
        recorder._audio_pipeline._buffer_sr = None
    recorder._last_rms = 0.0
    recorder._silence_timer = 0.0
    recorder._silence_start_time = None
    recorder._silence_warning_count = 0
    recorder._silence_next_warning_wait = 10.0
    # securely zero cached audio arrays BEFORE reassignment
    recorder._session_state.secure_clear_caches(recorder)
    # 17-H-: drain callback + stop + close via _teardown_stream()
    recorder._teardown_stream()
    # stop the audio worker thread. drain=False because
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # stop the IPC event worker with drain=False, the recording was
    with recorder._worker_lifecycle_lock:
        recorder._capture.stop_event_worker_body(recorder, timeout=_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
    # Stop the device health checker thread (mirrors the event worker).
    recorder._stop_device_health_checker(timeout=0.0)
    with recorder._audio_pipeline._lock:
        # SEC-audit-008: defer buffer zeroing to background daemon
        recording_buffer._ensure_growable_buffer(recorder)
        _old_buffer = recorder._audio_pipeline._buffer
        # Contiguous storage: data lives in ONE ndarray, so "swap in a
        recorder._audio_pipeline._buffer = recording_buffer._fresh_recording_buffer_like(recorder, _old_buffer)
        # PERF: zero the running buffered-samples counter, the fresh
        recorder._audio_pipeline._total_buffered_samples = 0
        _buffer_mod._secure_clear_array_background(_old_buffer)


def start_recording(recorder: Recorder) -> None:
    """Body of :meth:`Recorder.start` (after the ``_start_lock`` permission-gate block).

    Extracted from :mod:`.recorder` to shrink the
        3772-LOC ``recorder.py`` god class. The ``with self._start_lock:``
        block (containing the recording-event check + microphone-permission
        pre-flight) stays on ``Recorder.start`` so the source-inspection
        test (``tests/test_recording.py::TestRec5StartLock``) continues to
        pin the lock contract.

        This function runs WITHOUT holding ``_start_lock``, it is called
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
        conditional, the actual silence-warning state machine uses
        the integer counter ``_silence_warning_count`` (which IS read
        at recording.py:1109). The dead flags were misleading
        maintainers into thinking warning deduplication existed when
    it didn't: see FORENSIC_REVIEW_COMPLETE.md →

        SEC-audit-008: ``_secure_clear_array`` is now actually used
        here to zero cached audio arrays (``_cached_resampled`` and
        ``_cached_no_resample_arr``) before they're dropped. This
        prevents forensic recovery of audio data from process memory
        between sessions.

        CRITICAL, DO NOT RESTRUCTURE (2026-07-20)
        ========================================
        The device-enumeration block below (``last_error``,
        ``selected_device``, ``effective_sr``, the ``for candidate in
        candidates`` loop, the fallback loop, and the
        ``if recorder._stream is None:`` check) MUST stay at this
        function's body scope. OUTSIDE the ``callback`` closure built
        by ``recorder._stream_lifecycle.build_audio_callback(recorder)``
        above. A previous
        merge accidentally nested this block INSIDE the ``def
        callback()`` closure, which made ``last_error`` a local of
        ``callback`` instead of ``start()``, raising
        ``UnboundLocalError`` on every recording start.

        The device-loop bodies live on :class:`.stream_lifecycle.StreamLifecycle`
        (``open_stream_for_candidates`` / ``open_stream_fallback``, invoked
        from this function's scope via ``recorder._stream_lifecycle``, OUTSIDE
        the callback closure), so the
        structural contract above is preserved.

        DO NOT move device enumeration inside the callback closure.
        DO NOT re-add ``set_thread_registry``: it was merge damage.
    """
    # Lazy import: ``recorder.py`` is still loading when this module

    # SEC-audit-008: securely zero cached
    recorder._secure_clear_session_caches()

    # per-session state reset () ──
    recorder._session_state.reset_session_state(recorder)

    max_rec = recorder._session_state.cache_session_config(recorder)

    device = recorder._devices._resolve_device()
    candidates = recorder._devices._same_physical_microphone_candidates(device)

    # build the PortAudio callback closure () ──
    callback = recorder._stream_lifecycle.build_audio_callback(recorder)

    # CRITICAL, DO NOT RESTRUCTURE (2026-07-20)
    last_error: Exception | None = None
    selected_device: Any = None
    effective_sr: int = recorder.config.sample_rate
    used_fallback = False

    selected_device, effective_sr, last_error = recorder._stream_lifecycle.open_stream_for_candidates(
        recorder, candidates, callback, effective_sr, last_error
    )

    # If all same-name candidates failed, try ALL available input devices
    if recorder._stream_lifecycle._stream is None and not used_fallback:
        selected_device, effective_sr, used_fallback, last_error = recorder._stream_lifecycle.open_stream_fallback(
            recorder, candidates, callback, effective_sr, last_error
        )

    if recorder._stream_lifecycle._stream is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("No input device could be opened")

    recorder._session_state.resize_buffers_for_sample_rate(recorder, effective_sr, max_rec)

    # Scale the SPSC ring buffer to ~2s of headroom at the
    sizing_sr = effective_sr if effective_sr > 0 else recorder.config.sample_rate
    if sizing_sr > 0:
        sizing_blocksize = scaled_audio_blocksize(sizing_sr)
        new_ring_capacity = max(64, int(sizing_sr / sizing_blocksize * 2.0))
        for _payload in recorder._ring_buffer:
            _arr = _payload[0] if isinstance(_payload, tuple) else _payload
            if isinstance(_arr, np.ndarray):
                _arr.fill(0)
        recorder._ring_buffer = collections.deque(maxlen=new_ring_capacity)

    if selected_device != device and isinstance(selected_device, int):
        # Session-local fallback ONLY: the opened stream uses the
        if device is None and not used_fallback:
            log.debug(
                "[RECORDING] System Default resolved to device [%s] for this session",
                selected_device,
            )
        else:
            _label = "System Default" if device is None else device
            log.info(
                "[RECORDING] Selected microphone [%s] unavailable; using device [%s] "
                "for this session (saved selection unchanged)",
                _label,
                selected_device,
            )

    recorder._recording_event.set()

    # inside ``AudioCallbackDispatcher.audio_worker_loop`` (capture.py)

    target_sr = recorder.config.sample_rate
    # The mutable resampler state lives on the .resampling submodule;
    from voice_typer.server.recording import resampling as _recording_resampling

    if (
        effective_sr != target_sr
        and _recording_resampling._resample_poly is None
        and _recording_resampling._resample_poly_error is None
    ):
        # Skip the synchronous warm-up when the scipy preloader daemon
        _preloader = getattr(recorder, "_scipy_preloader_thread", None)
        if isinstance(_preloader, threading.Thread) and _preloader.is_alive():
            log.debug("[RECORDING] scipy preloader in flight, resampler warm-up left to the background thread")
        else:
            # Warm up synchronously when no background preloader is
            recorder.warm_up_resampler()

    # best-effort retune of the AudioProcessor's filter chain
    try:
        from .disconnect_handler import retune_audio_processor

        retune_audio_processor(
            recorder._audio_processor,
            effective_sr,
            recorder.config,
            context="on start",
        )
    except Exception:
        log.warning(
            "[RECORDING] retune_audio_processor failed on start, per-chunk resample will run on the worker thread",
            exc_info=True,
        )

    # refresh the per-chunk VAD property cache now that
    refresh_vad_caches(recorder)

    # Start the audio worker thread AFTER ``_recording_event.set()``
    try:
        recorder._start_audio_worker()
        audio_worker_started = True
    except BaseException:
        audio_worker_started = False
        recorder._stop_generation += 1
        recorder._recording_event.clear()
        with contextlib.suppress(Exception):
            recorder._teardown_stream()
        raise

    # Start the IPC event worker thread AFTER the audio worker
    try:
        with recorder._worker_lifecycle_lock:
            recorder._capture.start_event_worker_body(recorder)
    except BaseException:
        recorder._stop_generation += 1
        recorder._recording_event.clear()
        with contextlib.suppress(Exception):
            recorder._teardown_stream()
        if audio_worker_started:
            with contextlib.suppress(Exception):
                recorder._stop_audio_worker(timeout=0.5, drain=False)
        raise

    # Start the device health checker thread (off the audio
    recorder._devices._start_device_health_checker()

    # Wire the idle-recording gate. ``Recorder.start`` is the
    _mic_watcher = recorder._devices._mic_watcher
    if _mic_watcher is not None:
        _mic_watcher.set_idle(False)


def stop_recording(recorder: Recorder) -> np.ndarray:
    """Stop recording and return the complete audio array.

        Body of :meth:`Recorder.stop`: extracted verbatim (with ``self.X``
    rewritten to ``recorder.X``) by the split to shrink the
        ~2748-LOC ``recorder.py`` god class. ``Recorder.stop`` becomes a
        1-line delegator so existing call sites, subclass overrides, and
        ``inspect.getsource`` checks that look for the method on the
        ``Recorder`` class continue to work. There is NO source-inspection
        test contract pinning ``Recorder.stop`` source (verified via
        ``rg "inspect.getsource.*Recorder\\.stop\\b" tests/``, the matches
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
          5. ``_teardown_stream()``: 300 ms callback-drain poll, then
             ``stream.stop()`` + ``stream.close()`` (shared with
             ``discard_recording``; see the helper's docstring for the
    PERF- history).
          6. Clear ``_user_stop_pending`` (any future
             ``_stream_finished_callback`` is now a genuine disconnect).
          7. ``_stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
             drain=True)``: drain=True so the last few hundred ms of
             audio (chunks still in the ring buffer) end up in
             ``recorder._audio_pipeline._buffer`` and are concatenated below
    ().
          8. ``_stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
             drain=True)``: drains the IPC event queue.
          9. ``_stop_device_health_checker(timeout=0.0)``, fire-and-
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
         12. ``_prepare_audio(audio, effective_sr)``: H15: resample from
             scratch (no cache) for the full audio.
         13. ``log.info`` the stop summary (duration, sr, samples, RMS,
             peak, silence_pct, stream/concat/resample/total ms); near-
             silence warning when ``rms < 0.001``.

        The empty-buffer fast path zeros the cached audio arrays
        (``_secure_clear_caches``), resets ``_chunk_count = 0``, and
    returns an empty ``float32`` array,  secure-clear
        contract.

        Critical: ``_buffer_sr`` (the actual rate of the audio in
        ``recorder._audio_pipeline._buffer``) is captured into a local BEFORE
        ``_secure_clear_caches`` resets it to ``None``. The local is the
        authoritative source rate for the snapshotted audio, the chunks
        in ``_captured_chunks`` were appended at this rate by
        ``_process_audio_chunk``. Using ``_effective_sr`` here (the
        device's native rate) would cause ``_prepare_audio`` to resample
        the already-16 kHz audio a second time (chipmunk voice).
    """
    # Lazy import: ``recorder.py`` is still loading when this module
    from voice_typer.server.recording.recorder import (
        _AUDIO_WORKER_JOIN_TIMEOUT_S,
        _EVENT_WORKER_JOIN_TIMEOUT_S,
    )

    # Fast-path ONLY when no worker refs exist. A
    if (
        not recorder._recording_event.is_set()
        and recorder._worker_thread is None
        and recorder._event_worker_thread is None
    ):
        return np.array([], dtype=np.float32)

    stop_started = time.perf_counter()
    recorder._recording_event.clear()

    # HOTKEY-CRASH: increment stop_generation so any stale disconnect
    recorder._stop_generation += 1

    # STREAM-FIX: mark that we're about to call stream.stop()
    recorder._user_stop_pending = True

    # 17-H-: drain callback + stop + close via _teardown_stream()
    recorder._teardown_stream()
    # STREAM-FIX: clear the user-stop-pending flag now
    recorder._user_stop_pending = False
    stream_ms = (time.perf_counter() - stop_started) * 1000

    # stop the audio worker thread. drain=True so the
    recorder._stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

    # cut the worst-case stop() latency from ~5.8s to ~2.4s
    with recorder._worker_lifecycle_lock:
        recorder._capture.stop_event_worker_body(recorder, timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

    # device health checker fire-and-forget. Pass timeout=0.0
    recorder._stop_device_health_checker(timeout=0.0)

    # snapshot the buffer under the lock, then release the lock BEFORE
    concat_started = time.perf_counter()
    with recorder._audio_pipeline._lock:
        if not recorder._audio_pipeline._buffer:
            # securely zero cached audio arrays BEFORE
            recorder._session_state.secure_clear_caches(recorder)
            recorder._audio_pipeline._chunk_count = 0
            # PERF: zero the running buffered-samples counter alongside
            recorder._audio_pipeline._total_buffered_samples = 0
            # idle-recording gate. Return to the 12 s idle
            _mic_watcher = recorder._devices._mic_watcher
            if _mic_watcher is not None:
                _mic_watcher.set_idle(True)
            return np.array([], dtype=np.float32)
        # Non-empty: normalize legacy containers (hot-swap deque / test
        recording_buffer._ensure_growable_buffer(recorder)
        _old_buffer = recorder._audio_pipeline._buffer
        recorder._audio_pipeline._buffer = recording_buffer._fresh_recording_buffer_like(recorder, _old_buffer)
        # PERF: zero the running buffered-samples counter, the fresh
        recorder._audio_pipeline._total_buffered_samples = 0
        # Critical: capture ``_buffer_sr`` into a local
        _captured_buffer_sr = recorder._audio_pipeline._buffer_sr
        # securely zero cached audio arrays BEFORE
        recorder._session_state.secure_clear_caches(recorder)
    # materialize the contiguous result OUTSIDE the lock so the
    audio = _old_buffer.export_copy()
    concat_ms = (time.perf_counter() - concat_started) * 1000

    # SEC-audit-008 (race fix): enqueue the OLD storage for background
    _buffer_mod._secure_clear_array_background(_old_buffer)

    # Log audio statistics for diagnostics
    effective_sr = _captured_buffer_sr if _captured_buffer_sr is not None else recorder._effective_sr

    # Pipeline ``prepare_audio`` with the stats computation below.
    resample_started = time.perf_counter()
    _resample_result: dict[str, Any] = {"audio": None, "exc": None}

    def _run_prepare_audio() -> None:
        try:
            _resample_result["audio"] = prepare_audio(recorder, audio, effective_sr)
        except BaseException as exc:  # noqa: BLE001, re-raised after join
            _resample_result["exc"] = exc

    _resample_thread = threading.Thread(
        target=_run_prepare_audio,
        name="stop-prepare-audio",
        daemon=True,
    )
    _resample_thread.start()

    duration = len(audio) / effective_sr if len(audio) > 0 else 0
    # initialize ``rms``/``peak``/``silence_pct`` BEFORE
    rms: float = 0.0
    peak: float = 0.0
    silence_pct: float = 0.0
    if len(audio) > 0:
        # AUDIO-NP: use np.dot for RMS in stop() too
        if audio.size:
            flat = audio.reshape(-1)
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            # PERF: allocation-free peak + silence stats (shared helpers).
            peak = peak_amplitude(flat)
            silence_pct = silence_percent(flat)
        else:
            peak = 0.0
            rms = 0.0
            silence_pct = 0.0
        recorder._last_rms = rms
        # store the full-recording stats so the
        recorder._last_audio_stats = (rms, peak, silence_pct)
    else:
        recorder._last_rms = 0.0
        recorder._last_audio_stats = (0.0, 0.0, 0.0)
        log.warning("[RECORDING] No audio data captured!")

    # Wait for the resample thread to complete and propagate any
    _resample_thread.join()
    if _resample_result["exc"] is not None:
        raise _resample_result["exc"]
    audio = _resample_result["audio"]
    resample_ms = (time.perf_counter() - resample_started) * 1000

    # AUDIO-PROC: post-capture spectral noise reduction (offline,

    total_ms = (time.perf_counter() - stop_started) * 1000
    if len(audio) > 0:
        log.info(
            "[RECORDING] Audio stopped: duration=%.1fs, sr=%d, samples=%d | "
            "RMS=%.6f, peak=%.6f, silence=%.1f%% | "
            "stream=%.0fms, concat=%.0fms, resample=%.0fms, total=%.0fms",
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

    # idle-recording gate. Return to the 12 s idle cadence
    _mic_watcher = recorder._devices._mic_watcher
    if _mic_watcher is not None:
        _mic_watcher.set_idle(True)

    return audio
