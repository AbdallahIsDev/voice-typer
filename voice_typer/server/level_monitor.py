"""Continuous microphone level monitoring + ad-hoc test recording.

Opens a single sounddevice InputStream that serves TWO purposes:
  1. Continuous level monitoring — computes RMS/peak on every chunk so
     the frontend can show a live level bar at all times.
  2. Microphone test recording — when a test is active, the same callback
     also appends chunks to a test buffer.  When the test ends, the
     accumulated audio is encoded as WAV and returned.

By using ONE stream for both roles, we eliminate the PortAudio device
conflict that occurred on Windows when two separate sd.InputStream
instances tried to open the same device simultaneously (MME host API
only allows one open stream per device).

Thread safety: uses a threading.Lock to protect shared state; the audio
callback writes under the lock, and get_level() / stop_test_recording()
read under the lock.

Resource usage: 512-sample blocks at device native rate.  Test audio is
stored as a list of numpy arrays in memory (max ~30 s of float32 mono).

RT-SAFE-001 (c-review PERF-03): the PortAudio callback previously ran
the FULL filter chain (may include RNNoise, 5–50 ms per chunk on CPU),
allocated squared + abs arrays for RMS/peak, and appended
``indata.copy()`` to two test lists — all under ``_monitor_lock``.
That violated the ~32 ms PortAudio deadline whenever the level monitor
was active. The callback now does ONLY ``deque.append((indata.copy(),
status))`` + ``Event.set()`` (~10 µs). All heavy work runs on a
dedicated worker thread (``_level_worker_loop``) that drains the ring
buffer under ``_monitor_lock`` — the same pattern used by
``recording.py``'s audio callback since RT-SAFE-001.
"""

import base64
import collections
import contextlib
import io
import logging
import threading
import time
import types
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# ── Monitor session state ────────────────────────────────────────────

_monitor_lock = threading.Lock()
# TASK-14: ``Optional[object]`` made every downstream ``stream.stop()``
# / ``stream.close()`` call raise ``Object of class `object` has no
# attribute ...``.  ``Any`` matches the actual runtime type
# (``sounddevice.InputStream``) which is too heavy to import here and
# has no inline stubs.
_monitor_stream: Any | None = None  # sounddevice.InputStream
_monitor_active: bool = False
_monitor_level: float = 0.0  # smoothed RMS (0-1)
_monitor_peak: float = 0.0  # smoothed peak (0-1)
_monitor_sample_rate: int = 16000
_monitor_mic_id: str | None = None  # device this stream is on

# ── Audio processor for filtering the live level bar ───────────────
# When set, audio from the callback is run through this processor's
# process_chunk() before computing RMS/peak so the level bar reflects
# the effect of noise filters in real-time.
# TASK-14: same as ``_monitor_stream`` — ``Optional[object]`` rejects
# ``.process_chunk()`` / ``.cancel()`` calls below.  Use ``Any`` to
# match the runtime ``AudioProcessor`` type.
_level_processor: Any | None = None  # AudioProcessor instance

# ── RT-SAFE-001 (c-review PERF-03): SPSC ring buffer + worker thread ──
# The PortAudio callback (single producer) pushes (indata_copy, status)
# tuples to this deque; the level worker thread (single consumer) pops
# them and runs the heavy processing pipeline (filter chain, RMS/peak
# smoothing, test-chunk accumulation + quality metrics). The callback
# does NOT acquire ``_monitor_lock`` — only ``deque.append`` (atomic
# under CPython's GIL for SPSC) and ``Event.set()`` (~10 µs total).
# Without this refactor, RNNoise could spend 5–50 ms per chunk in the
# callback and miss the ~32 ms PortAudio deadline whenever the level
# monitor was active.
_LEVEL_RING_BUFFER_CAPACITY: int = 64  # ~4s of audio at 16 Hz block rate
_level_ring_buffer: collections.deque = collections.deque(maxlen=_LEVEL_RING_BUFFER_CAPACITY)
_level_worker_thread: threading.Thread | None = None
_level_worker_stop_event: threading.Event = threading.Event()
_level_worker_wake_event: threading.Event = threading.Event()
# Counter for chunks dropped because the ring buffer was full (worker
# couldn't keep up). Logged with throttling. Same pattern as
# recording.py's RT-SAFE-001 _dropped_ring_chunks.
_dropped_level_chunks: int = 0
# XV-58: timestamp (``time.monotonic()``) of the last throttled log
# emission for ``_dropped_level_chunks``. The worker thread logs the
# counter every 5s (if >0) and resets both the counter and this
# timestamp. Initial value of 0.0 guarantees the first drop event
# (whenever it happens) triggers a log immediately.
_last_drop_log_time: float = 0.0

# ── Test recording state (uses the SAME stream) ─────────────────────
# MEM-02: ``_test_chunks`` / ``_test_raw_chunks`` are bounded
# ``collections.deque`` (NOT plain ``list``).  The maxlen is derived
# from the CURRENT sample rate (the stream runs at the device's native
# rate, which can be 16000 / 44100 / 48000, NOT a fixed constant)
# and the currently-requested test duration, so a forgotten
# ``stop_test_recording()`` (e.g. IPC client crash mid-test) cannot
# accumulate unbounded audio.  The API-level [1,30]s cap is still
# enforced at ``start_test_recording``; the deque enforces it at the
# data-structure level too.  Because ``deque(maxlen=N)`` silently
# drops the OLDEST entry on overflow, even multiple unattended tests
# bounded by a long-lived backend can only ever hold ~one test's worth
# of audio (worst-case ~11 MB at 48 kHz / 30 s — strictly bounded).
# ``_reset_test_chunks`` (below) (re)creates both deques with the
# correct per-start maxlen; we never reassign them to ``[]``.
_TEST_MAX_CHUNKS_CAP: int = int(30 * 48000 / 512) + 1  # absolute hard cap (~2813)
_test_mode: bool = False
# XV-54 / PVT-013 resolution: the ORIGINAL data-duplication that
# XV-54 reported (worker thread appending every chunk to BOTH
# ``_test_chunks`` and ``_test_raw_chunks`` with IDENTICAL raw data) is
# eliminated — a grep for ``_test_chunks.append`` in this module returns
# ZERO hits. ``_test_chunks`` is retained as a backward-compat shim
# ONLY because external test files outside this module's scope
# (specifically ``tests/test_level_monitor.py::TestXV54OnlyRawChunksPopulated``
# and ``tests/test_g_perf_reliability_fixes.py::TestLevelMonitorTestChunkBounds``)
# reference it directly via ``lm._test_chunks.clear() / .append() /
# .maxlen / len(...)``. Removing the symbol here would break those tests.
# The shim is bounded + cleared alongside ``_test_raw_chunks`` (MEM-02)
# so it cannot leak.
#
# PVT-013: to eliminate the 7-70s synchronous filter-chain re-run that
# previously blocked the IPC thread at ``stop_test_recording`` time, the
# worker ALSO appends the FILTERED audio (the post-``process_chunk``
# output used for the live RMS/peak bar) to ``_test_filtered_chunks``.
# At stop time, when ``_test_filtered_chunks`` is populated, the returned
# ``audio`` ("after" WAV) is concatenated directly from it and the
# post-hoc filter is SKIPPED — no re-filter, no IPC-thread block.
#
# Tradeoff vs XV-54's literal Fix ("store only _test_raw_chunks; derive
# audio from raw_audio.copy()"): when a live processor is active, TWO
# per-chunk buffers are stored (raw + filtered) instead of one. PVT-013
# (High — production-blocking 7-70s latency) wins over XV-54 (Medium —
# 2x peak test-audio memory, ~11 MB at 48 kHz / 30 s). The two buffers
# hold DIFFERENT data (raw vs filtered), so XV-54's specific concern
# (IDENTICAL duplication in ``_test_chunks`` + ``_test_raw_chunks``) is
# still resolved — ``_test_chunks`` remains empty in production.
# When no live processor is active, ``_test_filtered_chunks`` is NOT
# populated and stop falls back to ``raw_audio.copy()`` + post-hoc
# filter (existing behavior), so the no-filter path keeps 1x storage.
_test_chunks: collections.deque[np.ndarray] = collections.deque(maxlen=_TEST_MAX_CHUNKS_CAP)
_test_raw_chunks: collections.deque[np.ndarray] = collections.deque(maxlen=_TEST_MAX_CHUNKS_CAP)
_test_filtered_chunks: collections.deque[np.ndarray] = collections.deque(maxlen=_TEST_MAX_CHUNKS_CAP)
_test_start_time: float = 0.0
_test_duration: float = 10.0
_test_filters: dict = {}
_test_auto_stop_timer: threading.Timer | None = None


def _reset_test_chunks(locked: bool) -> None:
    """(Re)create the bounded test-chunk deques under the right capacity.

    The maxlen is computed from the CURRENT device sample rate
    (``_monitor_sample_rate`` — NOT a constant, because the stream runs
    at the device native rate) and the CURRENT requested duration
    (``_test_duration``, already clamped to [1,30] by the caller).

    Args:
        locked: True if the caller already holds ``_monitor_lock``.
            If False, this helper acquires the lock itself before
            reassigning the module globals (required because the
            globals are rebound to NEW deque objects).

    NOTE: callers must ensure ``_test_duration`` and
    ``_monitor_sample_rate`` are set to their final values BEFORE
    calling this (``start_test_recording`` does exactly that).
    """
    global _test_chunks, _test_raw_chunks, _test_filtered_chunks
    global _monitor_sample_rate, _test_duration
    sr = _monitor_sample_rate
    # Chunks arrive at ``sr / 512`` per second (512-sample blocks @ sr).
    # +1 fudge so a duration that lands exactly on a block boundary
    # never drops the final chunk before stop_test_recording reads it.
    cap = int(_test_duration * sr / 512) + 1
    if cap < 1:
        cap = 1
    new_chunks = collections.deque(maxlen=cap)
    new_raw = collections.deque(maxlen=cap)
    new_filtered = collections.deque(maxlen=cap)

    if locked:
        _test_chunks = new_chunks
        _test_raw_chunks = new_raw
        _test_filtered_chunks = new_filtered
    else:
        with _monitor_lock:
            _test_chunks = new_chunks
            _test_raw_chunks = new_raw
            _test_filtered_chunks = new_filtered


# Quality metrics accumulated during test
_test_peak_history: list[float] = []
_test_rms_history: list[float] = []
_test_clip_count: int = 0
_test_silence_blocks: int = 0


# ── Public API: monitoring ──────────────────────────────────────────


def is_monitoring() -> bool:
    """Return True if the continuous level monitor is active.

    Returns:
        True if the level monitor stream is currently running.
    """
    with _monitor_lock:
        return _monitor_active


def get_level() -> dict:
    """Return the current audio level from the monitor.

    Returns:
        dict with keys:
            - "level": float (0-1) — current RMS level, scaled.
            - "peak": float (0-1) — peak level since last call.
            - "active": bool — whether the monitor stream is running.
    """
    with _monitor_lock:
        return {
            # MULT-8: increased from *5 to *8 so low-level ambient sounds
            # (ambient noise, mic taps) produce a visible bar response.
            # The bubble's visualizer uses the same *8 multiplier in
            # rmsToNorm().
            "level": min(1.0, _monitor_level * 8),
            "peak": _monitor_peak,
            "active": _monitor_active,
        }


def get_level_diagnostics() -> dict:
    """Return runtime diagnostics for the level monitor (XV-58).

    Exposes ``_dropped_level_chunks`` (chunks dropped because the worker
    thread couldn't keep up with the PortAudio callback rate) plus ring
    buffer fill state, for telemetry / debugging. The counter is reset
    every 5s by ``_level_worker_loop`` after logging, so this snapshot
    is point-in-time (drops since the last 5s log emission).

    No existing IPC handler wires this through to the frontend (the
    ``level_monitor_*`` IPC family is start/stop/status only); the
    function is exposed for future diagnostics-IPC use and for in-process
    callers (e.g. ``service.py`` could surface it in
    ``level_monitor_status``).

    Returns:
        dict with keys:
            - ``dropped_level_chunks`` (int): chunks dropped since the
              last 5s throttled log.
            - ``ring_buffer_capacity`` (int): configured max capacity.
            - ``ring_buffer_len`` (int): current fill level.
            - ``monitor_active`` (bool): whether the monitor stream is
              running.
    """
    # ``_dropped_level_chunks`` is incremented in the PortAudio callback
    # (RT thread) and reset in the worker thread (here, via the 5s log);
    # ``int`` read is atomic under CPython's GIL, so no lock is needed
    # for a point-in-time snapshot.
    return {
        "dropped_level_chunks": _dropped_level_chunks,
        "ring_buffer_capacity": _LEVEL_RING_BUFFER_CAPACITY,
        "ring_buffer_len": len(_level_ring_buffer),
        "monitor_active": _monitor_active,
    }


def update_level_processor(config_dict: dict) -> None:
    """Create or update the audio processor for the live level bar.

    When enabled, the level monitor's callback will run audio through
    this processor before computing RMS/peak so the level bar reflects
    the active noise filters in real-time (high-pass, noise gate,
    RNNoise — but not post-capture which is offline-only).

    Args:
        config_dict: dict with noise_filter_enabled, noise_filter_highpass,
            noise_filter_gate, noise_filter_rnnoise keys, etc.
    """
    global _level_processor

    if not config_dict.get("noise_filter_enabled", True):
        _level_processor = None
        log.debug("[LEVEL-MON] Level processor disabled")
        return

    try:
        # ADR 0007: AudioProcessor takes a config-like object (anything
        # with ``noise_filter_*`` attributes). The dict already has the
        # right keys; ``types.SimpleNamespace`` exposes them as attrs.
        # ``build_chain()`` reads each field via ``getattr(..., default)``
        # so missing keys fall back to ADR 0007 defaults.
        from voice_typer.server.audio_processor import AudioProcessor

        ap_config = types.SimpleNamespace(**config_dict)
        _level_processor = AudioProcessor(ap_config, sample_rate=_monitor_sample_rate)
        log.info(
            "[LEVEL-MON] Level processor updated: highpass=%s, gate=%s, method=%s",
            config_dict.get("noise_filter_highpass", True),
            config_dict.get("noise_filter_gate", True),
            config_dict.get("noise_suppression_method", "rnnoise"),
        )
    except Exception as exc:
        log.warning("[LEVEL-MON] Failed to create level processor: %s", exc)
        _level_processor = None


def start_monitoring(mic_id: str | None = None) -> dict:
    """Start continuous real-time audio level monitoring.

    If monitoring is already active, this is a no-op unless `mic_id`
    differs from the current device — in that case the old stream is
    stopped and a new one is opened on the requested device.

    Args:
        mic_id: Device index string (e.g. "3") or None for system default.

    Returns:
        dict with {"success": bool, "message": str, "sample_rate": int}.
    """
    import sounddevice as sd

    global _monitor_stream, _monitor_active, _monitor_sample_rate, _monitor_level, _monitor_peak, _monitor_mic_id

    with _monitor_lock:
        # Already running on the same device — no-op
        if _monitor_active and _monitor_mic_id == mic_id:
            return {
                "success": True,
                "message": "Already monitoring",
                "sample_rate": _monitor_sample_rate,
            }

        # Already running on a DIFFERENT device — restart
        if _monitor_active:
            old_stream = _monitor_stream
            _monitor_stream = None
            _monitor_active = False
            _monitor_level = 0.0
            _monitor_peak = 0.0
            _monitor_mic_id = None
            # Close old stream outside the lock to avoid blocking
        else:
            old_stream = None

    # Close old stream (if any) without holding the lock.
    # TASK-14: the ``if old_stream is not None`` guard was previously
    # fused into the trailing comment, so it was never executed and
    # pyrefly reported ``Object of class `NoneType` has no attribute
    # `stop`/`close``` at the unconditional call sites below.  Split
    # the guard onto its own line so it actually runs.
    if old_stream is not None:
        try:
            old_stream.stop()
            old_stream.close()
        except Exception as exc:
            log.debug("[LEVEL-MON] Close old stream: %s", exc)

    # Open new stream
    with _monitor_lock:
        device = None
        if mic_id is not None:
            with contextlib.suppress(ValueError, TypeError):
                device = int(mic_id)

        try:
            dev_info_raw = sd.query_devices(kind="input") if device is None else sd.query_devices(device)
            # TASK-14: ``query_devices`` is overloaded to return either a
            # ``dict`` (single device) or a ``DeviceList`` (tuple).  The
            # ``default_samplerate`` key only exists on the dict form, so
            # narrow before indexing.
            native_rate = int(dev_info_raw["default_samplerate"]) if isinstance(dev_info_raw, dict) else 16000
        except Exception:
            native_rate = 16000

        _monitor_sample_rate = native_rate
        _monitor_level = 0.0
        _monitor_peak = 0.0

        def callback(indata, frames, time_info, status):
            # RT-SAFE-001 (c-review PERF-03): the PortAudio callback runs
            # on the real-time audio thread and must complete in well
            # under the ~32 ms PortAudio deadline (512-sample blocks at
            # 16 kHz = 32 ms per chunk; on 44.1/48 kHz devices the
            # deadline is even tighter). To meet this deadline the
            # callback does ONLY:
            #
            #   1. ``indata.copy()`` — allocates a ~2 KB float32 buffer
            #      for 512 samples (negligible).
            #   2. ``deque.append`` — atomic under CPython's GIL for
            #      SPSC, ~1 µs.
            #   3. ``Event.set()`` — wakes the worker thread, ~1 µs.
            #
            # All heavy work (filter chain via ``_level_processor``,
            # ``np.abs`` / ``np.sqrt(np.mean(...))`` for RMS/peak, test
            # chunk accumulation + quality metrics) runs on the
            # dedicated worker thread ``_level_worker_loop``. Without
            # this refactor, RNNoise could spend 5–50 ms per chunk in
            # the callback and miss the deadline whenever the level
            # monitor was active.
            #
            # The ``status`` flag (PortAudio xrun / underflow) is
            # forwarded to the worker so log throttling / xrun tracking
            # can happen off the RT thread.
            global _dropped_level_chunks
            try:
                _level_ring_buffer.append((indata.copy(), status))
            except Exception:
                # deque.append only raises on capacity-overflow if a
                # maxlen isn't set — but we set one, so this is purely
                # defensive. Don't let a callback error kill the stream.
                log.debug("[LEVEL-MON] ring buffer append failed", exc_info=True)
                return
            if len(_level_ring_buffer) >= _LEVEL_RING_BUFFER_CAPACITY:
                # Ring buffer full — worker can't keep up. Drop the
                # oldest chunk to make room (deque with maxlen already
                # does this, but we want to count drops for telemetry).
                # NOTE: deque(maxlen=N) silently drops the OLDEST entry
                # on overflow, so the append above already succeeded
                # and the buffer is at capacity. We just count.
                _dropped_level_chunks += 1
            _level_worker_wake_event.set()

        try:
            stream = sd.InputStream(
                samplerate=native_rate,
                channels=1,
                dtype=np.float32,
                device=device,
                callback=callback,
                blocksize=512,
            )
            stream.start()
            _monitor_stream = stream
            _monitor_active = True
            _monitor_mic_id = mic_id

            # RT-SAFE-001 (c-review PERF-03): start the dedicated worker
            # thread that drains ``_level_ring_buffer`` and runs the
            # heavy filter chain + RMS/peak + test-chunk accumulation.
            # Idempotent: if a worker from a previous ``start_monitoring``
            # call is still alive (e.g. test fixtures that reset module
            # state without calling ``stop_monitoring``), reuse it —
            # the worker checks ``_monitor_active`` inside its loop.
            _ensure_level_worker_running()

            log.info(
                "[LEVEL-MON] Monitoring started: mic=%s, sr=%d",
                mic_id or "default",
                native_rate,
            )
            return {
                "success": True,
                "message": "Monitoring active",
                "sample_rate": native_rate,
            }
        except Exception as exc:
            log.warning("[LEVEL-MON] Failed to start monitoring: %s", exc)
            _monitor_stream = None
            _monitor_active = False
            _monitor_mic_id = None
            return {"success": False, "message": str(exc), "sample_rate": native_rate}


def stop_monitoring() -> dict:
    """Stop the continuous level monitor stream.

    Also cancels any in-progress test recording.

    Returns:
        dict with {"success": bool, "message": str}.
    """
    global _monitor_stream, _monitor_active, _monitor_level, _monitor_peak, _monitor_mic_id

    # Cancel any active test first
    _cancel_test_locked()

    already_stopped = False
    stream = None
    with _monitor_lock:
        if not _monitor_active:
            # RT-SAFE-001 (c-review PERF-03): monitoring was already
            # stopped. Remember this so we can still stop a possibly
            # leaked worker thread — but that must happen OUTSIDE the lock
            # (see below), because the worker acquires ``_monitor_lock``
            # while draining queued chunks; joining it while we hold the
            # lock could stall up to the join timeout.
            already_stopped = True
        else:
            _monitor_active = False
            stream = _monitor_stream
            _monitor_stream = None
            _monitor_level = 0.0
            _monitor_peak = 0.0
            _monitor_mic_id = None

    if already_stopped:
        # RT-SAFE-001 (c-review PERF-03): stop the (possibly leaked) worker
        # thread OUTSIDE the lock so the join can't stall against the
        # worker's own ``_monitor_lock`` acquisition. Safe to call
        # repeatedly.
        _stop_level_worker()
        return {"success": True, "message": "Not monitoring"}

    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.debug("[LEVEL-MON] Stream close: %s", exc)

    # RT-SAFE-001 (c-review PERF-03): now that the stream is closed (no
    # more callbacks will fire), stop the worker thread so it doesn't
    # spin waiting for chunks that will never arrive.
    _stop_level_worker()

    log.info("[LEVEL-MON] Monitoring stopped")
    return {"success": True, "message": "Monitoring stopped"}


# ── Public API: test recording ──────────────────────────────────────


def is_test_active() -> bool:
    """Return True if a microphone test is currently recording.

    Returns:
        True if test mode is active and recording audio.
    """
    with _monitor_lock:
        return _test_mode


def start_test_recording(
    mic_id: str | None = None,
    duration: float = 10.0,
    filters: dict | None = None,
) -> dict:
    """Start a microphone test recording using the existing monitor stream.

    If the monitor is running on a different device than `mic_id`, the
    stream is restarted on the requested device.  If the monitor is not
    running at all, it is started first.

    The monitor's callback accumulates audio chunks into a test buffer
    until `stop_test_recording()` or `cancel_test_recording()` is called,
    or until the auto-stop timer fires.

    Args:
        mic_id: Device index string or None for system default.
        duration: Recording duration in seconds (default 10, max 30).
        filters: Optional dict of audio enhancement filter overrides.

    Returns:
        dict with {"success": bool, "message": str, "duration": float,
                   "sample_rate": int}.
    """
    global _test_mode, _test_chunks, _test_raw_chunks, _test_start_time
    global _test_duration, _test_filters, _test_auto_stop_timer, _monitor_mic_id

    with _monitor_lock:
        if _test_mode:
            return {
                "success": False,
                "message": "Test already running",
                "duration": duration,
            }

        # Ensure the monitor is running on the correct device
        if not _monitor_active or _monitor_mic_id != mic_id:
            # We must release the lock before calling start_monitoring
            # (which also acquires the lock).  Start monitoring, then
            # re-check state under the lock.
            pass  # handled below the lock
        else:
            # Monitor is already active on the right device — set test mode
            _test_mode = True
            _test_start_time = time.perf_counter()
            _test_duration = max(1.0, min(30.0, duration))
            # MEM-02: (re)create bounded deques sized to this start's
            # duration + device sample rate. Must run AFTER _test_duration
            # is finalized above (the helper reads it). We are still
            # holding _monitor_lock here, so pass locked=True.
            _reset_test_chunks(locked=True)
            _test_filters = dict(filters) if filters else {}
            _test_peak_history = []
            _test_rms_history = []
            _test_clip_count = 0
            _test_silence_blocks = 0
            sr = _monitor_sample_rate

            _test_auto_stop_timer = threading.Timer(_test_duration, _do_auto_stop_test)
            _test_auto_stop_timer.daemon = True
            _test_auto_stop_timer.start()

            log.info(
                "[LEVEL-MON] Test recording started: mic=%s, duration=%.1fs",
                _monitor_mic_id or "default",
                _test_duration,
            )
            return {
                "success": True,
                "message": "Recording test...",
                "duration": _test_duration,
                "sample_rate": sr,
            }

    # Monitor not running or on wrong device — start/restart it
    # (outside the lock since start_monitoring acquires its own lock)
    mon_result = start_monitoring(mic_id=mic_id)
    if not mon_result.get("success"):
        return {
            "success": False,
            "message": mon_result.get("message", "Failed to start monitor"),
            "duration": duration,
        }

    # Monitor is now running on the correct device — set test mode
    with _monitor_lock:
        if _test_mode:
            return {
                "success": False,
                "message": "Test already running",
                "duration": duration,
            }
        _test_mode = True
        _test_start_time = time.perf_counter()
        _test_duration = max(1.0, min(30.0, duration))
        # MEM-02: (re)create bounded deques sized to this start's
        # duration + device sample rate. Must run AFTER _test_duration
        # is finalized above (the helper reads it). Still holding
        # _monitor_lock here, so pass locked=True.
        _reset_test_chunks(locked=True)
        _test_filters = dict(filters) if filters else {}
        _test_peak_history = []
        _test_rms_history = []
        _test_clip_count = 0
        _test_silence_blocks = 0
        sr = _monitor_sample_rate

        _test_auto_stop_timer = threading.Timer(_test_duration, _do_auto_stop_test)
        _test_auto_stop_timer.daemon = True
        _test_auto_stop_timer.start()

        log.info(
            "[LEVEL-MON] Test recording started: mic=%s, duration=%.1fs",
            _monitor_mic_id or "default",
            _test_duration,
        )
        return {
            "success": True,
            "message": "Recording test...",
            "duration": _test_duration,
            "sample_rate": sr,
        }


def stop_test_recording() -> dict:
    """Stop the test recording and return the captured audio as base64 WAV.

    Returns:
        dict with success, audio_base64, duration_ms, sample_rate, message.
    """
    global _test_mode, _test_auto_stop_timer, _test_chunks, _test_raw_chunks, _test_filtered_chunks
    global _test_start_time, _test_filters
    global _test_peak_history, _test_rms_history, _test_clip_count, _test_silence_blocks

    # Cancel the auto-stop timer if it hasn't fired yet
    timer = _test_auto_stop_timer
    if timer is not None:
        timer.cancel()
        _test_auto_stop_timer = None

    with _monitor_lock:
        was_active = _test_mode
        sr = _monitor_sample_rate
        # Snapshot the three test-chunk buffers:
        # - ``_test_chunks``: backward-compat shim, always empty in
        #   production (kept for tests outside this module that append
        #   to it directly).
        # - ``_test_raw_chunks``: RAW audio — source for the "before" WAV.
        # - ``_test_filtered_chunks``: FILTERED audio (post-``process_chunk``)
        #   captured by the worker — source for the "after" WAV when
        #   populated, eliminating the 7-70s synchronous re-filter that
        #   previously blocked the IPC thread at stop time (PVT-013).
        raw_chunks = list(_test_raw_chunks)
        filtered_chunks = list(_test_filtered_chunks)
        filters = dict(_test_filters)
        list(_test_peak_history)
        rms_hist = list(_test_rms_history)
        clip_count = _test_clip_count
        silence_blocks = _test_silence_blocks

        # Clear test state
        # MEM-02: use .clear() (NOT reassignment to []) so the
        # bounded deque + its maxlen are preserved across the test.
        # A plain ``_test_chunks = []`` would clobber the deque back
        # to an unbounded list and reintroduce the leak.
        _test_mode = False
        _test_chunks.clear()
        _test_raw_chunks.clear()
        _test_filtered_chunks.clear()
        _test_start_time = 0.0
        _test_filters.clear()
        _test_peak_history = []
        _test_rms_history = []
        _test_clip_count = 0
        _test_silence_blocks = 0

    # ``_test_chunks`` is a backward-compat shim (kept for tests outside
    # this module that append to it directly) and is NOT a source of audio.
    # Only ``_test_raw_chunks`` ("before" WAV) and ``_test_filtered_chunks"
    # ("after" WAV) are sources. If both are empty, return "No audio
    # captured" — even if the legacy shim has data (test_stop_returns_
    # no_audio_when_only_test_chunks_populated relies on this).
    if not was_active and not raw_chunks and not filtered_chunks:
        return {
            "success": False,
            "audio_base64": "",
            "raw_audio_base64": "",
            "duration_ms": 0,
            "sample_rate": 16000,
            "message": "No test running",
            "quality": {},
        }

    if not raw_chunks and not filtered_chunks:
        return {
            "success": True,
            "audio_base64": "",
            "raw_audio_base64": "",
            "duration_ms": 0,
            "sample_rate": sr,
            "message": "No audio captured",
            "quality": {},
        }

    # Build ``raw_audio`` (the "before" WAV) from ``_test_raw_chunks``.
    # Fall back to ``filtered_chunks`` (rare — raw buffer empty but
    # filtered populated) so a valid WAV is always produced when any
    # audio exists. ``_test_chunks`` is NOT used (legacy shim).
    try:
        if raw_chunks:
            raw_audio = np.concatenate(raw_chunks, axis=0).reshape(-1)
        else:
            raw_audio = np.concatenate(filtered_chunks, axis=0).reshape(-1)
    except Exception as exc:
        log.warning("[LEVEL-MON] Chunk concatenation failed: %s", exc)
        return {
            "success": False,
            "audio_base64": "",
            "raw_audio_base64": "",
            "duration_ms": 0,
            "sample_rate": sr,
            "message": f"Audio processing failed: {exc}",
            "quality": {},
        }

    # PVT-013: Build ``audio`` (the "after" WAV) from
    # ``_test_filtered_chunks`` when the worker populated it. This is
    # the audio that already went through the live ``_level_processor``
    # filter chain during recording — concatenating it directly avoids
    # the 7-70s synchronous re-filter that previously ran here. The
    # post-hoc filter block below is SKIPPED in this case (would
    # double-filter). Fallback: ``raw_audio.copy()`` — the post-hoc
    # filter then runs on it (existing behavior, for the no-live-
    # processor path).
    if filtered_chunks:
        try:
            audio = np.concatenate(filtered_chunks, axis=0).reshape(-1)
        except Exception as exc:
            log.warning("[LEVEL-MON] Filtered chunk concatenation failed: %s", exc)
            audio = raw_audio.copy()
    else:
        audio = raw_audio.copy()

    duration_ms = int(len(audio) / sr * 1000)

    # ── Compute quality metrics from raw audio ──────────────────────
    raw_abs = np.abs(raw_audio)
    raw_rms = float(np.sqrt(np.mean(np.square(raw_audio.astype(np.float32)))))
    raw_peak = float(raw_abs.max())

    # TASK-14: annotate ``quality`` as ``dict[str, Any]`` so that
    # downstream assignments like ``quality["detected_issues"] = [...str]``
    # do not trigger bad-assignment.  Without the annotation pyrefly
    # infers the dict's value type from the literal (str | bool | int |
    # float) and then rejects the ``list[str]`` assignment below.
    quality: dict[str, Any] = {
        "volume_level": "good" if raw_rms > 0.002 else ("low" if raw_rms > 0.0005 else "very_low"),
        "volume_rms": round(raw_rms, 6),
        "peak_level": round(raw_peak, 4),
        "noise_level": "low" if raw_rms < 0.005 else ("moderate" if raw_rms < 0.02 else "high"),
        "has_voice": raw_peak > 0.05,
        "has_clipping": clip_count > 0,
        "clipping_blocks": clip_count,
        "total_blocks": len(rms_hist) if rms_hist else 0,
        "silence_ratio": round(silence_blocks / max(1, len(rms_hist)), 4),
        "avg_rms": round(float(np.mean(rms_hist) if rms_hist else 0), 6),
        "peak_rms": round(float(np.max(rms_hist) if rms_hist else 0), 6),
    }

    # Detected issues list
    detected_issues = []
    if quality["noise_level"] == "high":
        detected_issues.append("High background noise")
    elif quality["noise_level"] == "moderate":
        detected_issues.append("Moderate background noise")
    if quality["has_clipping"]:
        detected_issues.append("Audio clipping detected")
    if quality["volume_level"] == "very_low":
        detected_issues.append("Volume too low — speak closer to the microphone")
    elif quality["volume_level"] == "low":
        detected_issues.append("Volume is low — consider raising input gain")
    if not quality["has_voice"]:
        detected_issues.append("No voice detected — try speaking during the test")
    quality["detected_issues"] = detected_issues

    # Estimate transcription quality (0-100)
    est_score = 100
    if quality["noise_level"] == "high":
        est_score -= 30
    elif quality["noise_level"] == "moderate":
        est_score -= 10
    if quality["has_clipping"]:
        est_score -= 20
    if quality["volume_level"] == "very_low":
        est_score -= 40
    elif quality["volume_level"] == "low":
        est_score -= 15
    if not quality["has_voice"]:
        est_score = 0
    # Add some RMS-based score
    if raw_rms < 0.0005:
        est_score = max(0, est_score - 30)
    elif raw_rms > 0.1:
        est_score = max(0, est_score - 10)
    quality["estimated_transcription_quality"] = max(0, min(100, est_score))

    # ── Apply audio enhancement filters ─────────────────────────────
    # PVT-013: skip the post-hoc filter when ``filtered_chunks`` was
    # already populated by the worker (the live ``_level_processor``
    # already filtered each chunk during recording). Running it again
    # here would double-filter AND reintroduce the 7-70s synchronous
    # block on the IPC thread. Only run when we fell back to
    # ``raw_audio.copy()`` (no live processor was active during the
    # test) and the user requested filters via ``_test_filters``.
    if not filtered_chunks and filters and filters.get("noise_filter_enabled", True):
        try:
            # ADR 0007: AudioProcessor takes a config-like object directly.
            # ``process_full_audio()`` was removed (post-capture denoise
            # deleted per ADR 0007 §3.8); only ``process_chunk()`` remains,
            # which is what we already call per-block below.
            from voice_typer.server.audio_processor import AudioProcessor

            ap_config = types.SimpleNamespace(**filters)
            processor = AudioProcessor(ap_config, sample_rate=sr)

            block_size = 1024
            processed_parts = []
            for i in range(0, len(audio), block_size):
                block = audio[i : i + block_size]
                processed_parts.append(processor.process_chunk(block))
            non_null = [p for p in processed_parts if p is not None]
            processed = np.concatenate(non_null) if non_null else audio

            if len(processed) > 0:
                log.info(
                    "[LEVEL-MON] Applied filter chain: highpass=%s, gate=%s, method=%s",
                    filters.get("noise_filter_highpass", True),
                    filters.get("noise_filter_gate", True),
                    filters.get("noise_suppression_method", "rnnoise"),
                )
                audio = processed
        except Exception as exc:
            log.warning("[LEVEL-MON] Filter application failed (using raw audio): %s", exc)

    # ── Encode processed audio as WAV ───────────────────────────────
    audio_int16 = (audio * 32767).astype(np.int16)
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())
    audio_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    # ── Encode raw audio as WAV for before/after comparison ─────────
    raw_int16 = (raw_audio * 32767).astype(np.int16)
    raw_buf = io.BytesIO()
    with wave.open(raw_buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(raw_int16.tobytes())
    raw_b64 = base64.b64encode(raw_buf.getvalue()).decode("ascii")

    log.info(
        "[LEVEL-MON] Test stopped: %.1fs recorded, %d+%d bytes WAV",
        duration_ms / 1000,
        len(raw_buf.getvalue()),
        len(buf.getvalue()),
    )

    return {
        "success": True,
        "audio_base64": audio_b64,
        "raw_audio_base64": raw_b64,
        "duration_ms": duration_ms,
        "sample_rate": sr,
        "message": f"Recorded {duration_ms / 1000:.1f}s of audio",
        "quality": quality,
    }


def update_test_filters(filters_dict: dict) -> None:
    """Update the active test recording's filter settings in real-time.

    When the user toggles a noise filter while a test is recording, this
    function updates the ``_test_filters`` dict so that
    ``stop_test_recording()`` applies the latest settings instead of the
    ones captured at test start.

    If no test is active, this is a no-op.

    Args:
        filters_dict: dict of noise_filter_* settings (same shape as the
            ``filters`` param passed to ``start_test_recording()``).
    """
    with _monitor_lock:
        if not _test_mode:
            return
        # Merge new settings into existing test filters so individual
        # toggles don't reset unrelated settings back to defaults.
        _test_filters.update(filters_dict)
        log.debug(
            "[LEVEL-MON] Test filters updated in-flight: %s",
            {k: v for k, v in _test_filters.items() if k.startswith("noise_filter_")},
        )


def cancel_test_recording() -> dict:
    """Cancel an in-progress test recording without returning audio."""
    global _test_mode, _test_auto_stop_timer

    timer = _test_auto_stop_timer
    if timer is not None:
        timer.cancel()
        _test_auto_stop_timer = None

    was_active = _cancel_test_locked()

    log.info("[LEVEL-MON] Test cancelled")
    if not was_active:
        return {"success": True, "message": "No test running"}
    return {"success": True, "message": "Test cancelled"}


# ── Internal helpers ────────────────────────────────────────────────


def _do_auto_stop_test():
    """Auto-stop callback fired by the threading.Timer.

    Stops the test recording (clears test mode) and notifies the
    frontend via push event so it can call stop_test_recording() to
    retrieve the audio.
    """
    global _test_mode, _test_auto_stop_timer

    with _monitor_lock:
        if not _test_mode:
            return
        _test_mode = False
        _test_auto_stop_timer = None

    log.info("[LEVEL-MON] Auto-stop: test ended")

    # Notify the frontend
    try:
        from voice_typer.server import event_bus

        event_bus.publish(
            {
                "type": "microphone_test_complete",
                "data": {"duration": _test_duration},
            }
        )
    except Exception:
        # NF-R20-2: this is load-bearing — if the publish fails, the
        # frontend UI hangs in "test running" state forever with no
        # indication the test completed. Log a warning so the user can
        # diagnose why the mic test isn't completing.
        log.warning(
            "[LEVEL-MON] failed to publish microphone_test_complete event",
            exc_info=True,
        )


def _cancel_test_locked() -> bool:
    """Cancel test state under the lock.

    Returns True if a test was actually active, False otherwise.
    """
    global _test_mode, _test_chunks, _test_raw_chunks, _test_filtered_chunks, _test_filters, _test_start_time, _test_auto_stop_timer
    global _test_peak_history, _test_rms_history, _test_clip_count, _test_silence_blocks

    # Stop auto-stop timer if running
    timer = _test_auto_stop_timer
    if timer is not None:
        timer.cancel()

    with _monitor_lock:
        if not _test_mode and not _test_chunks and not _test_filtered_chunks:
            return False
        was_active = _test_mode
        _test_mode = False
        # MEM-02: .clear() preserves the bounded deque (and its maxlen).
        # reassigning to [] would make it an unbounded list again.
        _test_chunks.clear()
        _test_raw_chunks.clear()
        _test_filtered_chunks.clear()
        _test_start_time = 0.0
        _test_filters.clear()
        _test_peak_history = []
        _test_rms_history = []
        _test_clip_count = 0
        _test_silence_blocks = 0
        return was_active


# ── RT-SAFE-001 (c-review PERF-03): level worker thread ──────────────


def _ensure_level_worker_running() -> None:
    """Start the level worker thread if it isn't already running.

    Idempotent: if a worker from a previous ``start_monitoring`` call is
    still alive (e.g. test fixtures that reset module state without
    calling ``stop_monitoring``), reuse it — the worker checks
    ``_monitor_active`` inside its loop. Called from ``start_monitoring``
    after the stream is opened + ``_monitor_active`` is set.

    The worker is a daemon so it never blocks process exit; ``stop_monitoring``
    signals it via ``_level_worker_stop_event`` for clean shutdown.
    """
    global _level_worker_thread
    if _level_worker_thread is not None and _level_worker_thread.is_alive():
        # Worker still alive from a previous start_monitoring call —
        # reuse it. Clear the stop event in case stop_monitoring was
        # called and then start_monitoring was called again.
        _level_worker_stop_event.clear()
        return
    _level_worker_stop_event.clear()
    _level_worker_wake_event.clear()
    _level_worker_thread = threading.Thread(
        target=_level_worker_loop,
        name="level-monitor-worker",
        daemon=True,
    )
    _level_worker_thread.start()


def _stop_level_worker() -> None:
    """Signal the level worker thread to stop and join it (best-effort).

    Called from ``stop_monitoring``. Safe to call when the worker isn't
    running (no-op). Joins with a short timeout so a stuck worker
    doesn't block the caller — the worker is a daemon so it'll exit
    when the process does.
    """
    global _level_worker_thread
    thread = _level_worker_thread
    if thread is None:
        return
    _level_worker_stop_event.set()
    _level_worker_wake_event.set()  # wake the worker so it sees the stop
    if thread is not threading.current_thread():
        thread.join(timeout=1.0)
    _level_worker_thread = None
    # Clear the stop event so the next _ensure_level_worker_running call
    # can reuse the (now-stopped) thread slot for a fresh worker.
    _level_worker_stop_event.clear()


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
    """
    global _dropped_level_chunks, _last_drop_log_time
    while True:
        # Wait for work or stop signal. 50 ms timeout ensures we notice
        # the stop flag even if the wake event is missed (same pattern
        # as recording.py's _audio_worker_loop).
        if not _level_worker_stop_event.is_set():
            _level_worker_wake_event.wait(timeout=0.05)
        _level_worker_wake_event.clear()

        # Drain all available chunks. Each chunk is processed by
        # _process_level_chunk which does the heavy lifting.
        while True:
            try:
                chunk_data = _level_ring_buffer.popleft()
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
        if _dropped_level_chunks > 0:
            now = time.monotonic()
            if (now - _last_drop_log_time) >= 5.0:
                dropped = _dropped_level_chunks
                _dropped_level_chunks = 0
                _last_drop_log_time = now
                log.warning(
                    "[LEVEL-MON] %d audio chunks dropped in the last ~5s "
                    "(worker thread couldn't keep up with the PortAudio "
                    "callback rate; consider disabling RNNoise or reducing "
                    "the filter chain cost)",
                    dropped,
                )

        if _level_worker_stop_event.is_set():
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
    global _monitor_level, _monitor_peak, _monitor_active, _test_mode, _test_chunks
    global _test_filtered_chunks, _test_silence_blocks, _test_clip_count

    if status:
        log.debug("[LEVEL-MON] PortAudio status: %s", status)

    # XV-55: snapshot shared state under the lock (quick). The heavy
    # computation below reads these but doesn't write them; re-checking
    # ``_monitor_active`` and ``_test_mode`` under the lock at write
    # time guards against a concurrent stop_monitoring() /
    # stop_test_recording() that flips the flags while we're computing.
    with _monitor_lock:
        active = _monitor_active
        test_mode = _test_mode
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
        processor = _level_processor
        if processor is not None:
            filtered = processor.process_chunk(indata.reshape(-1, 1))
            # ``process_chunk`` may return ``None`` to pass-through
            # (e.g. when the filter chain is disabled at runtime).
            flat_filtered = filtered.ravel() if filtered is not None else flat
            abs_flat = np.abs(flat_filtered)
            rms = float(np.sqrt(np.mean(flat_filtered**2)))
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
            abs_flat = np.abs(flat)
            rms = float(np.sqrt(np.mean(flat**2)))
        peak = float(abs_flat.max())

        # XV-55: compute test-quality metrics from RAW audio outside
        # the lock too (np.sqrt/mean/square on a 512-sample block is
        # cheap but still RT-relevant under load).
        if test_mode:
            raw_rms_for_quality = float(np.sqrt(np.mean(np.square(flat.astype(np.float32)))))
            raw_peak_for_quality = float(np.abs(flat).max())

    # -- Shared-state writes UNDER the lock (quick) --
    # XV-55: only the writes to ``_monitor_level``, ``_monitor_peak``,
    # ``_test_raw_chunks`` (append), ``_test_filtered_chunks`` (append),
    # and the quality-metric lists are lock-protected. These are all
    # O(1) -- the heavy work is done.
    with _monitor_lock:
        if not _monitor_active:
            return  # monitor stopped while we were computing
        if len(flat) > 0:
            # Smooth with exponential moving average
            if rms is not None:
                _monitor_level = (_monitor_level * 0.6) + (rms * 0.4)
            if peak is not None:
                _monitor_peak = max(_monitor_peak * 0.8, peak)
        else:
            _monitor_level *= 0.85
            _monitor_peak *= 0.85

        # If a test recording is active, also accumulate audio.
        # XV-54: ``_test_raw_chunks`` holds the RAW audio ("before" WAV).
        # PVT-013: ``_test_filtered_chunks`` holds the FILTERED audio
        # ("after" WAV) — populated only when a live processor was
        # active for this chunk. ``_test_chunks`` is NOT populated
        # (kept as a backward-compat shim).
        if _test_mode and len(flat) > 0:
            # Track quality metrics from RAW audio (not filtered)
            # so the quality report reflects the true mic input
            # independent of any active filter settings.
            if raw_rms_for_quality is not None:
                _test_raw_chunks.append(indata.copy())
                _test_rms_history.append(raw_rms_for_quality)
            # PVT-013: append the filtered chunk (if captured) so
            # stop_test_recording can build the "after" WAV without
            # re-running the filter chain synchronously.
            if filtered_chunk_for_test is not None:
                _test_filtered_chunks.append(filtered_chunk_for_test)
            if raw_peak_for_quality is not None:
                _test_peak_history.append(raw_peak_for_quality)
                if raw_rms_for_quality is not None and raw_rms_for_quality < 0.0005:
                    _test_silence_blocks += 1
                if raw_peak_for_quality > 0.95:
                    _test_clip_count += 1
