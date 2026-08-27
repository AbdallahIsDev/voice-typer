"""Level worker thread for the level_monitor package ().

 (c-review PERF-03): the PortAudio callback previously ran
the FULL filter chain (may include RNNoise, 5–50 ms per chunk on CPU),
allocated squared + abs arrays for RMS/peak, and appended
``indata.copy()`` to two test lists — all under ``_monitor_lock``.
That violated the ~32 ms PortAudio deadline whenever the level monitor
was active.

The callback now does ONLY ``deque.append((indata.copy(), status))``
+ ``Event.set()`` (~10 µs). All heavy work runs on the dedicated
worker thread defined here (``_level_worker_loop``) that drains the
ring buffer under ``_monitor_lock`` — the same pattern used by
``recording.py``'s audio callback since

the worker's backstop ``wait()`` timeout was raised from 50 ms
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

# ─── : per-burst level-worker error counter ───────────────────────
# ``_level_worker_loop`` catches ``Exception`` from ``_process_level_chunk``
# at DEBUG so a single bad chunk doesn't kill the worker. Previously a
# sustained failure mode (corrupted RNNoise model, numpy mismatch, filter
# misconfiguration) was completely silent at default log levels — the
# level bar would freeze with no WARNING / ERROR breadcrumb.  mirrors
# the ``_dropped_level_chunks`` 5-second throttle pattern (which lives on
# ``_state`` for test-poke compat): ``_level_worker_errors`` accumulates
# per-chunk failures and is logged + reset every 5s (if >0); if the
# per-second rate exceeds ``_LEVEL_WORKER_ERROR_RATE_THRESHOLD``, the log
# escalates from WARNING to ERROR (operator-visible signal that the
# filter chain is broken and the level bar is effectively frozen).
#
# These globals live on ``worker.py`` (not ``_state``) so the disjoint
# fix for  stays within ``worker.py``. The worker thread is the
# ONLY writer; ``int`` / ``float`` read + reset is atomic under
# CPython's GIL, so no lock is needed (same rationale as
# ``_dropped_level_chunks``). Tests in ``tests/test_level_monitor*.py``
# access them via ``worker._level_worker_errors`` etc. (NOT via
# ``lm._level_worker_errors``, which would require routing through the
# package's custom ``_LevelMonitorModule`` ``__getattr__``).
_level_worker_errors: int = 0
_last_worker_error_log_time: float = 0.0
_level_worker_error_window_start: float = 0.0
_LEVEL_WORKER_ERROR_LOG_THROTTLE_SEC: float = 5.0
_LEVEL_WORKER_ERROR_RATE_THRESHOLD: float = 10.0

# ─── cumulative dropped-chunks counter ──────────────────────
# ``_state._dropped_level_chunks`` is a per-burst delta: the RT callback
# increments it on ring-buffer overflow, and the worker thread drains it
# to 0 every 5s after logging. That makes it useless for cumulative
# telemetry — a test that snapshots it before/after a single overflow
# can flake if the worker drains between the snapshot and the check
# (exactly the regression in
# ``test_dropped_chunks_counter_incremented_on_ring_buffer_overflow``).
#
# This counter is the cumulative sibling: it is incremented by the
# worker (NEVER by the RT callback) at the same time it drains the
# per-burst counter, and it is NEVER reset in production. It survives
# the 5s throttle and the worker's drain cycle, so it's the correct
# field for "how many chunks has this process dropped since
# ``start_monitoring`` was first called" telemetry.
#
# Module-level global (NOT on ``_state``) for two reasons:
#   1. ``_state.py`` is outside this fix's owned files, so adding a new
#      field to ``_State.__init__`` would require editing it. A worker.py
#      global mirrors the existing ``_level_worker_errors`` pattern.
#   2. ``_state.reset_for_tests`` wipes ``_state.__dict__`` (via
#      ``__dict__.clear()``), which would silently evict any attribute
#      added externally. A worker.py module-level global survives
#      ``reset_for_tests`` and is reset explicitly by
#      ``_reset_worker_error_state_for_tests`` (extended below) so test
#      isolation is preserved.
#
# Tests access it via ``worker._total_dropped_level_chunks`` (mirroring
# ``worker._level_worker_errors``). It is ALSO surfaced via
# ``get_level_diagnostics()["total_dropped_level_chunks"]`` for any IPC
# caller that wants the cumulative count alongside the per-burst delta.
_total_dropped_level_chunks: int = 0


def _reset_worker_error_state_for_tests() -> None:
    """Reset the  per-burst error counter to its post-import defaults.

    Mirrors ``_state.reset_for_tests`` for the worker-error sub-state.
    Test fixtures call this between tests so a sustained-error test
    doesn't leak its counter into a later test. Safe to call from any
    thread (GIL-atomic int/float writes).

    Also resets the cumulative ``_total_dropped_level_chunks``
    counter so a drop-heavy test doesn't leak its total into the next
    test's assertions. Production code NEVER resets this counter — only
    this test-only helper does.
    """
    global _level_worker_errors, _last_worker_error_log_time, _level_worker_error_window_start
    global _total_dropped_level_chunks
    _level_worker_errors = 0
    _last_worker_error_log_time = 0.0
    _level_worker_error_window_start = 0.0
    _total_dropped_level_chunks = 0


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
    # Clear the ring buffer of any stale chunks from a previous session
    # (mirrors ``recording/capture.py``: the previous worker has been
    # stopped, so any chunks left in the buffer are orphans from a
    # closed stream and must not bleed into the fresh worker's first
    # iteration). Done BEFORE the thread is spawned so the new worker
    # starts with an empty queue.
    _state._level_ring_buffer.clear()
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

    If the worker fails to exit within the 1-second join timeout, the
    thread slot (``_level_worker_thread``) is LEFT OCCUPIED. This
    prevents ``_ensure_level_worker_running`` from spawning a duplicate
    worker that would race the stuck thread for ``_level_ring_buffer``
    pops (SPSC contract violation) and double-publish ``mic_level``
    events. The stop event + ring-buffer clear below are skipped in
    that case so the stuck worker still has a consistent view if it
    eventually drains; the next ``_ensure_level_worker_running`` call
    will reuse the (still-alive) thread instead of starting a new one.
    """
    thread = _state._level_worker_thread
    if thread is None:
        return
    _state._level_worker_stop_event.set()
    _state._level_worker_wake_event.set()  # wake the worker so it sees the stop
    if thread is not threading.current_thread():
        thread.join(timeout=1.0)
        if thread.is_alive():
            # Worker did not exit within the 1-second join timeout —
            # likely stuck inside a long ``process_chunk`` call
            # (RNNoise on a stalled CPU, a numpy deadlock, or a
            # garbage-collection pause longer than 1 s). Leave the slot
            # occupied so ``_ensure_level_worker_running`` reuses this
            # thread instead of spawning a duplicate worker that would
            # race it for the ring buffer (SPSC) and double-publish
            # ``mic_level`` events. The operator-visible ERROR log
            # surfaces the stuck worker so it isn't silently leaked.
            log.error(
                "[LEVEL-MON] level worker thread did not exit within the "
                "1s join timeout — leaving _level_worker_thread slot "
                "occupied to prevent duplicate workers (the stuck worker "
                "is a daemon and will exit with the process)",
            )
            return
    _state._level_worker_thread = None
    # Clear the ring buffer after the worker has been joined so any
    # chunks the worker didn't drain (e.g. because stop_monitoring was
    # called between the worker's last drain and its exit) don't bleed
    # into the next session's fresh worker. Mirrors the pattern in
    # ``recording/capture.py``.
    _state._level_ring_buffer.clear()
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

    the backstop ``wait()`` timeout was 50 ms (pre-refactor).
    Raised to 250 ms — the stop path already calls
    ``_level_worker_wake_event.set()`` so stop latency is unaffected;
    the timeout only governs the "missed wakeup" recovery interval
    (a rare edge case when the audio device underflows or stalls).
    250 ms cuts idle wakeups 5× with no functional change.
    """
    # ``global`` declarations for the per-burst error counter
    # (defined at module top). Hoisted to the function header for
    # readability — Python treats ``global`` as function-scoped
    # regardless of where in the function the statement appears.
    # ``_total_dropped_level_chunks`` is also declared global so
    # the drain-block below can ``+=`` it (the worker is the ONLY
    # writer in production; ``int +=`` is GIL-atomic on CPython).
    global _level_worker_errors, _last_worker_error_log_time, _level_worker_error_window_start
    global _total_dropped_level_chunks
    while True:
        # Wait for work or stop signal. : raised from 50 ms to
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
                #
                # previously this branch ONLY logged at DEBUG, so
                # a sustained failure mode (corrupted RNNoise model,
                # numpy mismatch, filter misconfiguration) was completely
                # silent at default log levels — the level bar would
                # freeze with no operator-visible breadcrumb. We now
                # also increment ``_level_worker_errors`` (module-level
                # counter, declared ``global`` at the top of this
                # function) and surface it via the throttled
                # WARNING/ERROR block below (mirrors
                # ``_dropped_level_chunks``). The per-chunk DEBUG log is
                # retained so a full traceback is still available at
                # DEBUG level for diagnosis.
                if _level_worker_error_window_start == 0.0:
                    _level_worker_error_window_start = time.monotonic()
                _level_worker_errors += 1
                log.debug(
                    "[LEVEL-MON] level worker thread error processing chunk",
                    exc_info=True,
                )

        # throttled log of dropped chunks. The counter is
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
                # Accumulate the per-burst drop count into the
                # cumulative counter BEFORE resetting the per-burst
                # delta. The cumulative counter is NEVER reset in
                # production (only by the test-only
                # ``_reset_worker_error_state_for_tests`` helper), so
                # it gives IPC callers a stable "drops since
                # ``start_monitoring`` first ran" total — independent
                # of the 5s throttle cycle that resets the per-burst
                # delta. ``int += int`` is GIL-atomic on CPython, so
                # no lock is needed (the worker is the ONLY writer).
                _total_dropped_level_chunks += dropped
                # Re-arm the RT-callback one-shot latch so the
                # next burst of drops surfaces its first-drop warning.
                _state._first_drop_warning_emitted = False
                log.warning(
                    "[LEVEL-MON] %d audio chunks dropped in the last ~5s "
                    "(worker thread couldn't keep up with the PortAudio "
                    "callback rate; consider disabling RNNoise or reducing "
                    "the filter chain cost)",
                    dropped,
                )

        # throttled log of per-chunk processing errors. Mirrors
        # the ``_dropped_level_chunks`` 5-second throttle pattern above:
        # the counter is incremented in the drain loop's ``except``
        # branch when ``_process_level_chunk`` raises; we log it every
        # 5s (if >0) and reset. The per-second rate (errors / window
        # elapsed since the first error of this burst) selects WARNING
        # vs ERROR — a rate above
        # ``_LEVEL_WORKER_ERROR_RATE_THRESHOLD`` (default 10/sec, i.e.
        # >60% of chunks failing at the ~16 Hz block rate) escalates to
        # ERROR so a frozen level bar surfaces at default log levels
        # instead of being silently swallowed at DEBUG.
        if _level_worker_errors > 0:
            now = time.monotonic()
            if (now - _last_worker_error_log_time) >= _LEVEL_WORKER_ERROR_LOG_THROTTLE_SEC:
                errors = _level_worker_errors
                window_start = _level_worker_error_window_start
                if window_start == 0.0:
                    # Defensive: errors > 0 implies the drain-loop
                    # ``except`` branch set this. Fall back to ``now``
                    # so the rate computation doesn't divide by zero.
                    window_start = now
                elapsed = max(now - window_start, 1e-6)
                # Reset before logging so a concurrent error (the
                # worker is the only writer; this is GIL-safe) doesn't
                # double-count into the next window.
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
                        "or filter misconfiguration — the level bar is "
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
        # has been received in ``_state._LEVEL_IDLE_TIMEOUT_SEC``
        # seconds (default 60.0), auto-stop the stream. The tray bubble
        # is likely hidden; the level bar isn't visible. The next
        # ``start_monitoring`` / ``get_level`` poll will re-start it.
        # This prevents the RNNoise filter chain from pegging a core
        # when the frontend forgot to call ``level_monitor_stop``.
        # Lazy import to avoid a top-level circular dependency
        # (monitoring.py imports worker.py for _ensure_level_worker_running).
        from .monitoring import _idle_timeout_auto_stop

        if _idle_timeout_auto_stop():
            # idle-timeout closed the stream. Exit the worker
            # loop so the thread terminates — eliminates the 4 Hz idle
            # wakeups (250 ms backstop ``wait()`` timeout × forever)
            # that would otherwise drain the battery on an idle laptop
            # (~345k idle wakeups/day). The next ``start_monitoring``
            # call spawns a fresh worker via
            # ``_ensure_level_worker_running`` (thread creation is
            # ~1 ms — negligible vs. the 60 s idle window).
            #
            # Race-safety: clear ``_level_worker_thread`` BEFORE
            # returning so a concurrent ``start_monitoring`` call's
            # ``_ensure_level_worker_running`` sees "no worker" and
            # spawns a fresh one. Relying solely on ``is_alive()``
            # would race: between this point and the thread actually
            # exiting, ``is_alive()`` is still True, so
            # ``_ensure_level_worker_running`` would mistakenly reuse
            # the exiting thread and the new stream's chunks would have
            # no consumer (frozen level bar).
            #
            # SPSC safety: at this point the worker has already exited
            # its drain loop above (the last ``_level_ring_buffer.popleft()``
            # call is long past) and is past the last shared-state
            # write. Clearing the slot here cannot cause a duplicate
            # consumer because the new worker (if spawned) is the only
            # thread popping from ``_level_ring_buffer``; this thread is
            # about to exit and will never pop again. The
            # ``_ensure_level_worker_running`` clear of the ring buffer
            # (on fresh-worker spawn) eliminates any residual chunks
            # from the closed stream so the new worker starts clean.
            _state._level_worker_thread = None
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
    # ``_level_processor.process_chunk`` can take 5-50 ms
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
    # filtered audio to append to ``_test_filtered_chunks``
    # under the lock. Populated ONLY when a live processor is active
    # and returned non-None (otherwise the post-hoc filter at stop
    # time handles the "after" WAV). Computed outside the lock (the
    # ``.copy()`` is cheap — 512 float32 = 2 KB).
    filtered_chunk_for_test: np.ndarray | None = None
    if len(flat) > 0:
        # Lightweight level-bar mode. When ``_level_bar_filtered``
        # is False (default) AND ``_test_mode`` is False, SKIP the
        # filter chain — compute RMS/peak on RAW audio only. The filter
        # chain (which may include RNNoise, 5-50 ms per chunk on CPU)
        # is wasted work for the cosmetic level bar (the user just
        # wants to see "is the mic picking up sound?"), and running it
        # at 31-94 Hz pegs a core for a non-functional visualization.
        #
        # The filter chain STILL runs when ``_test_mode`` is True (the
        # test's "after" WAV needs the filtered audio) OR when the user
        # has explicitly opted in via ``_level_bar_filtered = True``.
        processor = _state._level_processor
        run_filter_chain = processor is not None and (test_mode or _state._level_bar_filtered)
        if run_filter_chain:
            filtered = processor.process_chunk(indata.reshape(-1, 1))
            # ``process_chunk`` may return ``None`` to pass-through
            # (e.g. when the filter chain is disabled at runtime).
            flat_filtered = filtered.ravel() if filtered is not None else flat
            if flat_filtered.size > 0:
                rms = float(np.sqrt(np.dot(flat_filtered, flat_filtered) / flat_filtered.size))
            else:
                rms = 0.0
            # capture the filtered audio for the test's
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
            # No live processor, OR cosmetic-bar-only mode: use the raw
            # flat block for both RMS and peak (no extra allocation
            # needed, no filter chain cost).
            flat_filtered = flat
            rms = float(np.sqrt(np.dot(flat, flat) / flat.size)) if flat.size > 0 else 0.0
        # Allocation-free peak: max(abs(x)) is computed as max(max(x), -min(x))
        # so no temporary ``np.abs`` array is allocated per chunk.
        peak = max(float(flat_filtered.max()), -float(flat_filtered.min())) if flat_filtered.size > 0 else 0.0

        # compute test-quality metrics from RAW audio outside
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
    # only the writes to ``_monitor_level``, ``_monitor_peak``,
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
        # ``_test_raw_chunks`` holds the RAW audio ("before" WAV).
        # ``_test_filtered_chunks`` holds the FILTERED audio
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
            # append the filtered chunk (if captured) so
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
        # Push the DISPLAY values, not the raw chunk values. The
        # ``mic_level`` push event replaced the 10 Hz
        # ``microphone_test_get_level`` poll on the Microphone page, and
        # that poll returned the EMA-smoothed level scaled by
        # ``_LEVEL_DISPLAY_GAIN`` (see ``monitoring.get_level``). Pushing
        # the raw instantaneous RMS here made every push frame 8x smaller
        # than the poll value the UI was built against — the live meter
        # collapsed to ~0% for normal speech levels right after the
        # one-shot fallback poll seeded it with the scaled first read.
        # Mirror ``get_level`` exactly: same smoothed state, same gain,
        # same 1.0 cap.
        if _state._monitor_active and rms is not None and peak is not None:
            from .monitoring import _push_mic_level

            _push_mic_level(
                min(1.0, _state._monitor_level * _state._LEVEL_DISPLAY_GAIN),
                _state._monitor_peak,
                _state._monitor_active,
            )
