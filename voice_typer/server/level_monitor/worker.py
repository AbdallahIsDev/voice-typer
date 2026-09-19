"""Microphone level-monitor worker thread."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING

import numpy as np

from voice_typer.server._audio_constants import AUDIO_CLIPPING_THRESHOLD, AUDIO_SILENCE_RMS

from ._state import _state

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger("voice_typer.server.level_monitor")

# How many ring-buffer chunks the level worker drains between
_DRAIN_STOP_CHECK_INTERVAL = 4

# Thread-registry names for the two long-lived daemons. Exported so
LEVEL_WORKER_NAME = "level-monitor-worker"
MIC_LEVEL_WORKER_NAME = "level-monitor-mic-level-worker"
# Join timeouts used when registering with ThreadRegistry. Matches the
_WORKER_JOIN_TIMEOUT_S = 1.0


def set_thread_registry(registry: Any | None) -> None:
    """Install a central ThreadRegistry for shutdown coordination."""
    _state._thread_registry = registry


def _register_with_thread_registry(
    name: str,
    thread: threading.Thread,
    stop_event: threading.Event,
) -> None:
    """Best-effort registration of a freshly started worker thread."""
    registry = _state._thread_registry
    if registry is None:
        return
    with contextlib.suppress(Exception):
        registry.register(
            name=name,
            thread=thread,
            stop_event=stop_event,
            join_timeout=_WORKER_JOIN_TIMEOUT_S,
        )


def _unregister_from_thread_registry(name: str) -> None:
    """Best-effort removal of a worker's registry entry after it exits."""
    registry = _state._thread_registry
    if registry is None:
        return
    with contextlib.suppress(Exception):
        registry.unregister(name)


# ``_level_worker_loop`` catches ``Exception`` from ``_process_level_chunk``
_level_worker_errors: int = 0
_last_worker_error_log_time: float = 0.0
_level_worker_error_window_start: float = 0.0
_LEVEL_WORKER_ERROR_LOG_THROTTLE_SEC: float = 5.0
_LEVEL_WORKER_ERROR_RATE_THRESHOLD: float = 10.0

# ``_state._dropped_level_chunks`` is a per-burst delta: the RT callback
_total_dropped_level_chunks: int = 0


def _reset_worker_error_state_for_tests() -> None:
    """Reset the  per-burst error counter to its post-import defaults."""
    global _level_worker_errors, _last_worker_error_log_time, _level_worker_error_window_start
    global _total_dropped_level_chunks
    _level_worker_errors = 0
    _last_worker_error_log_time = 0.0
    _level_worker_error_window_start = 0.0
    _total_dropped_level_chunks = 0


def _ensure_level_worker_running() -> None:
    """Start the level worker thread if it isn't already running."""
    if _state._level_worker_thread is not None and _state._level_worker_thread.is_alive():
        # Worker still alive from a previous start_monitoring call —
        _state._level_worker_stop_event.clear()
        return
    _state._level_worker_stop_event.clear()
    _state._level_worker_wake_event.clear()
    # Clear the ring buffer of any stale chunks from a previous session
    _state._level_ring_buffer.clear()
    _state._level_worker_thread = threading.Thread(
        target=_level_worker_loop,
        name=LEVEL_WORKER_NAME,
        daemon=True,
    )
    _state._level_worker_thread.start()
    # Register with the central ThreadRegistry (if one was set) so
    _register_with_thread_registry(
        LEVEL_WORKER_NAME,
        _state._level_worker_thread,
        _state._level_worker_stop_event,
    )


def _stop_level_worker() -> None:
    """Signal the level worker thread to stop and join it (best-effort)."""
    thread = _state._level_worker_thread
    if thread is None:
        return
    _state._level_worker_stop_event.set()
    _state._level_worker_wake_event.set()  # wake the worker so it sees the stop
    if thread is not threading.current_thread():
        thread.join(timeout=1.0)
        if thread.is_alive():
            # Worker did not exit within the 1-second join timeout —
            log.error(
                "[LEVEL-MON] level worker thread did not exit within the "
                "1s join timeout, leaving _level_worker_thread slot "
                "occupied to prevent duplicate workers (the stuck worker "
                "is a daemon and will exit with the process)",
            )
            return
    _state._level_worker_thread = None
    # Clear the ring buffer after the worker has been joined so any
    _state._level_ring_buffer.clear()
    # Clear the stop event so the next _ensure_level_worker_running call
    _state._level_worker_stop_event.clear()
    # Remove the registry entry so a subsequent spawn re-registers
    _unregister_from_thread_registry(LEVEL_WORKER_NAME)


def _level_worker_loop() -> None:
    """Level worker thread main loop."""
    # ``global`` declarations for the per-burst error counter
    global _level_worker_errors, _last_worker_error_log_time, _level_worker_error_window_start
    global _total_dropped_level_chunks
    while True:
        # Wait for work or stop signal. : raised from 50 ms to
        if not _state._level_worker_stop_event.is_set():
            _state._level_worker_wake_event.wait(
                timeout=_state._LEVEL_WORKER_BACKSTOP_TIMEOUT_SEC,
            )
        _state._level_worker_wake_event.clear()

        # Drain all available chunks. Each chunk is processed by
        _drain_count = 0
        while True:
            try:
                chunk_data = _state._level_ring_buffer.popleft()
            except IndexError:
                break
            try:
                _process_level_chunk(*chunk_data)
            except Exception:
                # Log and continue, a single bad chunk must NOT kill
                if _level_worker_error_window_start == 0.0:
                    _level_worker_error_window_start = time.monotonic()
                _level_worker_errors += 1
                log.debug(
                    "[LEVEL-MON] level worker thread error processing chunk",
                    exc_info=True,
                )
            _drain_count += 1
            if _drain_count % _DRAIN_STOP_CHECK_INTERVAL == 0:
                if _state._level_worker_stop_event.is_set():
                    # Bail out of the drain early (sacrifice in-flight
                    break
                time.sleep(0)  # yield GIL to reduce CPU burn

        # throttled log of dropped chunks. The counter is
        if _state._dropped_level_chunks > 0:
            now = time.monotonic()
            if (now - _state._last_drop_log_time) >= 5.0:
                dropped = _state._dropped_level_chunks
                _state._dropped_level_chunks = 0
                _state._last_drop_log_time = now
                # Accumulate the per-burst drop count into the
                _total_dropped_level_chunks += dropped
                # Re-arm the RT-callback one-shot latch so the
                _state._first_drop_warning_emitted = False
                log.warning(
                    "[LEVEL-MON] %d audio chunks dropped in the last ~5s "
                    "(worker thread couldn't keep up with the PortAudio "
                    "callback rate; consider disabling RNNoise or reducing "
                    "the filter chain cost)",
                    dropped,
                )

        # throttled log of per-chunk processing errors. Mirrors
        if _level_worker_errors > 0:
            now = time.monotonic()
            if (now - _last_worker_error_log_time) >= _LEVEL_WORKER_ERROR_LOG_THROTTLE_SEC:
                errors = _level_worker_errors
                window_start = _level_worker_error_window_start
                if window_start == 0.0:
                    # Defensive: errors > 0 implies the drain-loop
                    window_start = now
                elapsed = max(now - window_start, 1e-6)
                # Reset before logging so a concurrent error (the
                _level_worker_errors = 0
                _level_worker_error_window_start = 0.0
                _last_worker_error_log_time = now
                rate_per_sec = errors / elapsed
                threshold = _LEVEL_WORKER_ERROR_RATE_THRESHOLD
                if rate_per_sec > threshold:
                    log.error(
                        "[LEVEL-MON] %d level-worker chunk errors in the "
                        "last ~%.1fs (%.1f/sec > %.1f/sec threshold; likely "
                        "a corrupted RNNoise model, numpy version mismatch, "
                        "or filter misconfiguration, the level bar is "
                        "frozen until the stream is restarted)",
                        errors,
                        elapsed,
                        rate_per_sec,
                        threshold,
                    )
                else:
                    log.warning(
                        "[LEVEL-MON] %d level-worker chunk errors in the "
                        "last ~%.1fs (%.1f/sec; a single bad chunk must "
                        "not kill the worker but sustained errors "
                        "indicate a filter-chain problem)",
                        errors,
                        elapsed,
                        rate_per_sec,
                    )

        # idle-timeout auto-stop. If no IPC ``get_level`` poll
        from .monitoring import _idle_timeout_auto_stop

        if _idle_timeout_auto_stop():
            # idle-timeout closed the stream. Exit the worker
            _state._level_worker_thread = None
            # Natural exit: drop the registry entry so ``shutdown_all()``
            _unregister_from_thread_registry(LEVEL_WORKER_NAME)
            return

        if _state._level_worker_stop_event.is_set():
            return


def _process_level_chunk(indata: np.ndarray, status: Any) -> None:
    """Process a single audio chunk on the level worker thread.

     (c-review PERF-03): this is the heavy work that used
    to run on the PortAudio RT thread. It is now invoked from
    ``_level_worker_loop`` so it can take 5–50 ms (RNNoise) without
    missing the ~32 ms PortAudio deadline.

    the heavy computation (filter chain via
    ``_level_processor.process_chunk``, ``np.abs`` / ``np.sqrt`` /
    ``np.mean`` for RMS/peak, raw-audio quality metrics) runs OUTSIDE
    ``_monitor_lock`` so ``get_level()`` / ``stop_test_recording()`` /
    other worker iterations are not blocked waiting for the lock while
    RNNoise churns. The lock is acquired only for the shared-state
    writes (``_monitor_level``, ``_monitor_peak``, ``_test_raw_chunks``
    append, quality-metric appends).

    only ``_test_raw_chunks`` is populated with RAW audio.
    ``_test_chunks`` is kept as a backward-compat shim (still bounded +
    cleared) for tests outside this module's scope, but is no longer
    appended to here.

    the FILTERED audio (``flat_filtered``, the post-
    ``process_chunk`` output) is ALSO appended to ``_test_filtered_chunks``
    when a live processor is active and returned non-None. At
    ``stop_test_recording`` time, this buffer is concatenated directly
    into the returned ``audio`` ("after" WAV) so the 7-70s synchronous
    re-filter is skipped. When no live processor is active,
    ``_test_filtered_chunks`` stays empty and stop falls back to
    ``raw_audio.copy()`` + post-hoc filter (existing behavior).
    """
    if status:
        log.debug("[LEVEL-MON] PortAudio status: %s", status)

    # snapshot shared state under the lock (quick). The heavy
    with _state._monitor_lock:
        active = _state._monitor_active
        test_mode = _state._test_mode
    if not active:
        return

    # -- Heavy work OUTSIDE the lock --
    flat = indata.ravel()
    rms: float | None = None
    peak: float | None = None
    raw_rms_for_quality: float | None = None
    raw_peak_for_quality: float | None = None
    # filtered audio to append to ``_test_filtered_chunks``
    filtered_chunk_for_test: np.ndarray | None = None
    if len(flat) > 0:
        # Lightweight level-bar mode. When ``_level_bar_filtered``
        processor = _state._level_processor
        run_filter_chain = processor is not None and (test_mode or _state._level_bar_filtered)
        if run_filter_chain:
            filtered = processor.process_chunk(indata.reshape(-1, 1))
            # ``process_chunk`` may return ``None`` to pass-through
            flat_filtered = filtered.ravel() if filtered is not None else flat
            if flat_filtered.size > 0:
                rms = float(np.sqrt(np.dot(flat_filtered, flat_filtered) / flat_filtered.size))
            else:
                rms = 0.0
            # capture the filtered audio for the test's
            if test_mode and filtered is not None:
                filtered_chunk_for_test = flat_filtered.copy()
        else:
            # No live processor, OR cosmetic-bar-only mode: use the raw
            flat_filtered = flat
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size)) if flat.size > 0 else 0.0
        # Allocation-free peak: max(abs(x)) is computed as max(max(x), -min(x))
        peak = max(float(flat_filtered.max()), -float(flat_filtered.min())) if flat_filtered.size > 0 else 0.0

        # compute test-quality metrics from RAW audio outside
        if test_mode:
            if flat.size > 0:
                raw_rms_for_quality = float(np.sqrt(np.dot(flat, flat) / flat.size))
                raw_peak_for_quality = max(float(flat.max()), -float(flat.min()))
            else:
                raw_rms_for_quality = 0.0
                raw_peak_for_quality = 0.0

    # -- Shared-state writes UNDER the lock (quick) --
    with _state._monitor_lock:
        if not _state._monitor_active:
            return  # monitor stopped while we were computing
        if len(flat) > 0:
            # Smooth with exponential moving average
            if rms is not None:
                _state._monitor_level = (_state._monitor_level * 0.6) + (rms * 0.4)
            if peak is not None:
                _state._monitor_peak = max(_state._monitor_peak * 0.8, peak)
        else:
            _state._monitor_level *= 0.85
            _state._monitor_peak *= 0.85

        # If a test recording is active, also accumulate audio.
        if _state._test_mode and len(flat) > 0:
            # Track quality metrics from RAW audio (not filtered)
            if raw_rms_for_quality is not None:
                _state._test_raw_chunks.append(indata.copy())
                _state._test_rms_history.append(raw_rms_for_quality)
            # append the filtered chunk (if captured) so
            if filtered_chunk_for_test is not None:
                _state._test_filtered_chunks.append(filtered_chunk_for_test)
            if raw_peak_for_quality is not None:
                _state._test_peak_history.append(raw_peak_for_quality)
                if raw_rms_for_quality is not None and raw_rms_for_quality < AUDIO_SILENCE_RMS:
                    _state._test_silence_blocks += 1
                if raw_peak_for_quality >= AUDIO_CLIPPING_THRESHOLD:
                    _state._test_clip_count += 1
        if rms is not None and peak is not None and rms == 0.0 and peak == 0.0:
            _state._consecutive_zero_chunks += 1
            if _state._consecutive_zero_chunks >= _state._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD:
                # Local import to avoid a top-level circular dependency
                from .monitoring import _emit_device_lost

                _emit_device_lost("zero_chunks")
        else:
            _state._consecutive_zero_chunks = 0
        # Push the DISPLAY values, not the raw chunk values. The
        if _state._monitor_active and rms is not None and peak is not None:
            from .monitoring import _push_mic_level

            _push_mic_level(
                min(1.0, _state._monitor_level * _state._LEVEL_DISPLAY_GAIN),
                _state._monitor_peak,
                _state._monitor_active,
            )
