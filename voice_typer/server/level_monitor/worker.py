"""Level worker thread for the level_monitor package (AC-129).

RT-SAFE-001 (c-review PERF-03): the PortAudio callback previously ran
the FULL filter chain (may include RNNoise, 5–50 ms per chunk on CPU),
allocated squared + abs arrays for RMS/peak, and appended
``indata.copy()`` to two test lists — all under ``_monitor_lock``.
That violated the ~32 ms PortAudio deadline whenever the level monitor
was active.

The callback now does ONLY ``deque.append((indata.copy(), status))``
+ ``Event.set()`` (~10 µs). All heavy work runs on the dedicated
worker thread defined here (``_level_worker_loop``) that drains the
ring buffer under ``_monitor_lock`` — the same pattern used by
``recording.py``'s audio callback since RT-SAFE-001.

ER-75: the worker's backstop ``wait()`` timeout was raised from 50 ms
to 250 ms — the stop path already calls
``_level_worker_wake_event.set()`` so stop latency is unaffected; the
timeout only governs the "missed wakeup" recovery interval (a rare
edge case). 250 ms cuts idle wakeups 5× with no functional change.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import numpy as np

from ._state import _state

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger("voice_typer.server.level_monitor")


def _ensure_level_worker_running() -> None:
    """Start the level worker thread if it isn't already running.

    Idempotent: if a worker from a previous ``start_monitoring`` call is
    still alive (e.g. test fixtures that reset module state without
    calling ``stop_monitoring``), reuse it — the worker checks
    ``_monitor_active`` inside its loop. Called from ``start_monitoring``
    after the stream is opened + ``_monitor_active`` is set.

    The worker is a daemon so it never blocks process exit;
    ``stop_monitoring`` signals it via ``_level_worker_stop_event`` for
    clean shutdown.
    """
    if _state._level_worker_thread is not None and _state._level_worker_thread.is_alive():
        # Worker still alive from a previous start_monitoring call —
        # reuse it. Clear the stop event in case stop_monitoring was
        # called and then start_monitoring was called again.
        _state._level_worker_stop_event.clear()
        return
    _state._level_worker_stop_event.clear()
    _state._level_worker_wake_event.clear()
    _state._level_worker_thread = threading.Thread(
        target=_level_worker_loop,
        name="level-monitor-worker",
        daemon=True,
    )
    _state._level_worker_thread.start()


def _stop_level_worker() -> None:
    """Signal the level worker thread to stop and join it (best-effort).

    Called from ``stop_monitoring``. Safe to call when the worker isn't
    running (no-op). Joins with a short timeout so a stuck worker
    doesn't block the caller — the worker is a daemon so it'll exit
    when the process does.
    """
    thread = _state._level_worker_thread
    if thread is None:
        return
    _state._level_worker_stop_event.set()
    _state._level_worker_wake_event.set()  # wake the worker so it sees the stop
    if thread is not threading.current_thread():
        thread.join(timeout=1.0)
    _state._level_worker_thread = None
    # Clear the stop event so the next _ensure_level_worker_running call
    # can reuse the (now-stopped) thread slot for a fresh worker.
    _state._level_worker_stop_event.clear()


def _level_worker_loop() -> None:
    """Level worker thread main loop.

    Consumes chunks from the SPSC ring buffer
    (``_level_ring_buffer``) and runs the heavy processing pipeline
    (filter chain via ``_level_processor``, RMS/peak smoothing, test
    chunk accumulation + quality metrics). This thread is the SINGLE
    consumer — the PortAudio callback is the single producer, so no
    locks are needed for the ring buffer access
    (``collections.deque`` append/popleft are atomic under CPython's
    GIL for SPSC).

    The shared monitor/test state (``_monitor_level``, ``_test_chunks``,
    etc.) IS protected by ``_monitor_lock`` because
    ``get_level()`` / ``stop_test_recording()`` read it from other
    threads.

    Shutdown: exits when ``_level_worker_stop_event`` is set. Drains
    any remaining chunks before exiting so a stop right after a
    callback doesn't lose the last level update.

    ER-75: the backstop ``wait()`` timeout was 50 ms (pre-refactor).
    Raised to 250 ms — the stop path already calls
    ``_level_worker_wake_event.set()`` so stop latency is unaffected;
    the timeout only governs the "missed wakeup" recovery interval
    (a rare edge case when the audio device underflows or stalls).
    250 ms cuts idle wakeups 5× with no functional change.
    """
    while True:
        # Wait for work or stop signal. ER-75: raised from 50 ms to
        # 250 ms — the timeout only governs the missed-wakeup recovery
        # interval (stop latency is unaffected because ``_stop_level_worker``
        # calls ``_level_worker_wake_event.set()``).
        if not _state._level_worker_stop_event.is_set():
            _state._level_worker_wake_event.wait(
                timeout=_state._LEVEL_WORKER_BACKSTOP_TIMEOUT_SEC,
            )
        _state._level_worker_wake_event.clear()

        # Drain all available chunks. Each chunk is processed by
        # _process_level_chunk which does the heavy lifting.
        while True:
            try:
                chunk_data = _state._level_ring_buffer.popleft()
            except IndexError:
                break
            try:
                _process_level_chunk(*chunk_data)
            except Exception:
                # Log and continue — a single bad chunk must NOT kill
                # the worker (otherwise all subsequent level updates are
                # lost until the next start_monitoring).
                log.debug(
                    "[LEVEL-MON] level worker thread error processing chunk",
                    exc_info=True,
                )

        # XV-58: throttled log of dropped chunks. The counter is
        # incremented in the PortAudio callback (RT thread) when the
        # ring buffer overflows; we log it every 5s (if >0) and reset.
        # ``int`` read + reset is atomic under CPython's GIL, so no
        # lock is needed here. The 5s throttle prevents log spam under
        # sustained overload (e.g. RNNoise taking 50ms/chunk on a slow
        # CPU -> 100% drop rate -> would otherwise log on every 50ms
        # iteration = 20 logs/sec).
        if _state._dropped_level_chunks > 0:
            now = time.monotonic()
            if (now - _state._last_drop_log_time) >= 5.0:
                dropped = _state._dropped_level_chunks
                _state._dropped_level_chunks = 0
                _state._last_drop_log_time = now
                # R3-F6: re-arm the RT-callback one-shot latch so the
                # next burst of drops surfaces its first-drop warning.
                _state._first_drop_warning_emitted = False
                log.warning(
                    "[LEVEL-MON] %d audio chunks dropped in the last ~5s "
                    "(worker thread couldn't keep up with the PortAudio "
                    "callback rate; consider disabling RNNoise or reducing "
                    "the filter chain cost)",
                    dropped,
                )

        # ER-14: idle-timeout auto-stop. If no IPC ``get_level`` poll
        # has been received in ``_state._LEVEL_IDLE_TIMEOUT_SEC``
        # seconds (default 5.0), auto-stop the stream. The tray bubble
        # is likely hidden; the level bar isn't visible. The next
        # ``start_monitoring`` / ``get_level`` poll will re-start it.
        # This prevents the RNNoise filter chain from pegging a core
        # when the frontend forgot to call ``level_monitor_stop``.
        # Lazy import to avoid a top-level circular dependency
        # (monitoring.py imports worker.py for _ensure_level_worker_running).
        from .monitoring import _idle_timeout_auto_stop

        _idle_timeout_auto_stop()

        if _state._level_worker_stop_event.is_set():
            return


def _process_level_chunk(indata: np.ndarray, status: Any) -> None:
    """Process a single audio chunk on the level worker thread.

    RT-SAFE-001 (c-review PERF-03): this is the heavy work that used
    to run on the PortAudio RT thread. It is now invoked from
    ``_level_worker_loop`` so it can take 5–50 ms (RNNoise) without
    missing the ~32 ms PortAudio deadline.

    XV-55: the heavy computation (filter chain via
    ``_level_processor.process_chunk``, ``np.abs`` / ``np.sqrt`` /
    ``np.mean`` for RMS/peak, raw-audio quality metrics) runs OUTSIDE
    ``_monitor_lock`` so ``get_level()`` / ``stop_test_recording()`` /
    other worker iterations are not blocked waiting for the lock while
    RNNoise churns. The lock is acquired only for the shared-state
    writes (``_monitor_level``, ``_monitor_peak``, ``_test_raw_chunks``
    append, quality-metric appends).

    XV-54: only ``_test_raw_chunks`` is populated with RAW audio.
    ``_test_chunks`` is kept as a backward-compat shim (still bounded +
    cleared) for tests outside this module's scope, but is no longer
    appended to here.

    PVT-013: the FILTERED audio (``flat_filtered``, the post-
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

    # XV-55: snapshot shared state under the lock (quick). The heavy
    # computation below reads these but doesn't write them; re-checking
    # ``_monitor_active`` and ``_test_mode`` under the lock at write
    # time guards against a concurrent stop_monitoring() /
    # stop_test_recording() that flips the flags while we're computing.
    with _state._monitor_lock:
        active = _state._monitor_active
        test_mode = _state._test_mode
    if not active:
        return

    # -- Heavy work OUTSIDE the lock --
    # XV-55: ``_level_processor.process_chunk`` can take 5-50 ms
    # (RNNoise on CPU). Holding ``_monitor_lock`` during that time
    # would block ``get_level()`` (called by the IPC handler on the
    # main thread) and ``stop_test_recording()`` -- visible as a frozen
    # level bar / mic-test-stop latency. The lock is acquired only for
    # the shared-state writes below.
    flat = indata.ravel()
    rms: float | None = None
    peak: float | None = None
    raw_rms_for_quality: float | None = None
    raw_peak_for_quality: float | None = None
    # PVT-013: filtered audio to append to ``_test_filtered_chunks``
    # under the lock. Populated ONLY when a live processor is active
    # and returned non-None (otherwise the post-hoc filter at stop
    # time handles the "after" WAV). Computed outside the lock (the
    # ``.copy()`` is cheap — 512 float32 = 2 KB).
    filtered_chunk_for_test: np.ndarray | None = None
    if len(flat) > 0:
        # Apply noise filters to the level bar audio if a processor is
        # active, so the bar reflects what the user hears after
        # filtering, not the raw mic input. ``_level_processor`` is
        # only mutated by ``update_level_processor`` (which acquires
        # ``_monitor_lock``), so reading it here without the lock is
        # safe -- worst case we use a stale reference for one chunk.
        processor = _state._level_processor
        if processor is not None:
            filtered = processor.process_chunk(indata.reshape(-1, 1))
            # ``process_chunk`` may return ``None`` to pass-through
            # (e.g. when the filter chain is disabled at runtime).
            flat_filtered = filtered.ravel() if filtered is not None else flat
            if flat_filtered.size > 0:
                rms = float(np.sqrt(np.dot(flat_filtered, flat_filtered) / flat_filtered.size))
            else:
                rms = 0.0
            # PVT-013: capture the filtered audio for the test's
            # "after" WAV so stop_test_recording doesn't need to
            # re-run the filter chain synchronously (7-70s block).
            # ``flat_filtered`` may be a view of ``filtered`` (fresh
            # array) or of ``indata`` (when ``filtered is None``);
            # ``.copy()`` defends against both aliasing the RT
            # callback's reusable buffer and the post-stop mutation
            # of a transient array.
            if test_mode and filtered is not None:
                filtered_chunk_for_test = flat_filtered.copy()
        else:
            # No live processor: use the raw flat block for both RMS
            # and peak (no extra allocation needed).
            flat_filtered = flat
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size)) if flat.size > 0 else 0.0
        # Allocation-free peak: max(abs(x)) is computed as max(max(x), -min(x))
        # so no temporary ``np.abs`` array is allocated per chunk.
        peak = max(float(flat_filtered.max()), -float(flat_filtered.min())) if flat_filtered.size > 0 else 0.0

        # XV-55: compute test-quality metrics from RAW audio outside
        # the lock too (np.sqrt/mean/square on a 512-sample block is
        # cheap but still RT-relevant under load).
        if test_mode:
            if flat.size > 0:
                raw_rms_for_quality = float(np.sqrt(np.dot(flat, flat) / flat.size))
                raw_peak_for_quality = max(float(flat.max()), -float(flat.min()))
            else:
                raw_rms_for_quality = 0.0
                raw_peak_for_quality = 0.0

    # -- Shared-state writes UNDER the lock (quick) --
    # XV-55: only the writes to ``_monitor_level``, ``_monitor_peak``,
    # ``_test_raw_chunks`` (append), ``_test_filtered_chunks`` (append),
    # and the quality-metric lists are lock-protected. These are all
    # O(1) -- the heavy work is done.
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
        # XV-54: ``_test_raw_chunks`` holds the RAW audio ("before" WAV).
        # PVT-013: ``_test_filtered_chunks`` holds the FILTERED audio
        # ("after" WAV) — populated only when a live processor was
        # active for this chunk. ``_test_chunks`` is NOT populated
        # (kept as a backward-compat shim).
        if _state._test_mode and len(flat) > 0:
            # Track quality metrics from RAW audio (not filtered)
            # so the quality report reflects the true mic input
            # independent of any active filter settings.
            if raw_rms_for_quality is not None:
                _state._test_raw_chunks.append(indata.copy())
                _state._test_rms_history.append(raw_rms_for_quality)
            # PVT-013: append the filtered chunk (if captured) so
            # stop_test_recording can build the "after" WAV without
            # re-running the filter chain synchronously.
            if filtered_chunk_for_test is not None:
                _state._test_filtered_chunks.append(filtered_chunk_for_test)
            if raw_peak_for_quality is not None:
                _state._test_peak_history.append(raw_peak_for_quality)
                if raw_rms_for_quality is not None and raw_rms_for_quality < 0.0005:
                    _state._test_silence_blocks += 1
                if raw_peak_for_quality > 0.95:
                    _state._test_clip_count += 1
        if rms is not None and peak is not None and rms == 0.0 and peak == 0.0:
            _state._consecutive_zero_chunks += 1
            if _state._consecutive_zero_chunks >= _state._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD:
                # Local import to avoid a top-level circular dependency
                # (monitoring.py imports worker.py for _ensure_level_worker_running).
                from .monitoring import _emit_device_lost

                _emit_device_lost("zero_chunks")
        else:
            _state._consecutive_zero_chunks = 0
        if _state._monitor_active and rms is not None and peak is not None:
            from .monitoring import _push_mic_level

            _push_mic_level(rms, peak, _state._monitor_active)
