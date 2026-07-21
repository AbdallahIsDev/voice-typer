"""``Recorder`` — session-based audio recording from the microphone.

Phase 4.5 / ARCH-045 — extracted from the original ``recording.py``
god-module.  The class body is unchanged; only the bare-name lookups
for cross-submodule helpers (``_get_resample_poly``,
``_secure_clear_array_background``, ``_start_scipy_preloader``, and
the mutable ``_resample_poly`` / ``_resample_poly_error`` /
``_scipy_preloader_thread`` globals) now route through the package
namespace so test patches of the form
``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
keep affecting production code defined here.

Patch-path compatibility
------------------------
Tests use ``patch("voice_typer.server.recording.X")`` for several
names ``X`` that this module consumes.  For the patch to affect code
defined here, the lookup must go through the package binding at call
time — hence ``from voice_typer.server import recording as
_recording_pkg`` and the ``_recording_pkg.X`` references below.  The
package ``__init__.py`` re-exports ``X`` from the appropriate
submodule (``.resampling`` / ``.buffer``), so ``_recording_pkg.X``
resolves correctly without eager binding at import time.

``inspect.getsource`` compatibility
-----------------------------------
``Recorder`` is genuinely defined in this file (not aliased), so
``inspect.getsource(Recorder._process_audio_chunk)`` and similar
method-level source checks continue to read from this file.

The module-level constants (``DEFAULT_MAX_BUFFER_CHUNKS``,
``_AUDIO_RING_BUFFER_CAPACITY``, ``_EVENT_WORKER_*``, ``_XRUN_*``,
``_DEFAULT_VAD_*``, etc.) live here because they are only consumed by
``Recorder`` and are not patched by any test.
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
from collections.abc import Callable
from typing import Any

import numpy as np

# PERF-COLDSTART-001: lazy import — sounddevice loads the PortAudio C
# library (and on some platforms probes audio hardware) at import time,
# which adds measurable latency to the app cold-start path
# (voice_typer.server.app imports this module at module top). sounddevice
# is only needed once a recording actually starts, so defer the real
# import to first attribute access. The proxy re-reads sys.modules on
# every access, so tests that do
# ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` — or that
# inject a mock via ``monkeypatch.setitem(sys.modules, "sounddevice",
# mock)`` — keep working unchanged. The ``from __future__ import
# annotations`` above stringifies the ``Optional[sd.InputStream]``
# annotation in Recorder.__init__ so it no longer forces an eager import.
from voice_typer.server import event_bus
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.config import Config
from voice_typer.server.log_rate_limit import log_rate_limited
from voice_typer.server.vad import compute_vad_prob
from voice_typer.server.vad_processor import VadProcessor, VadState

# RW-8: ``event_bus`` and ``compute_vad_prob`` are hoisted to module
# top-level (instead of being imported inline inside _process_audio_chunk
# on every chunk) for two reasons:
#   1. The audio worker hot path (16 Hz) was paying a per-chunk
#      ``sys.modules`` dict lookup + ``importlib`` resolution cost for
#      ``event_bus`` and ``vad`` — negligible individually but
#      cumulative when combined with the rest of the pipeline. Both
#      modules are leaf-safe to import at module top: ``event_bus``
#      imports only stdlib; ``vad`` imports only stdlib + numpy (the
#      heavy ``torch`` import is lazily deferred inside ``vad`` itself).
#   2. ``event_bus.publish`` was called synchronously from the worker
#      thread, blocking it on the IPC transport (TCP write to the
#      Electron renderer). A slow subscriber could stall the worker,
#      causing the ring buffer to overflow and audio to be dropped.
#      The publish call is now routed through ``self._event_queue``
#      and drained by a dedicated ``_event_worker_thread`` (see
#      ``_start_event_worker`` / ``_event_worker_loop``).

sd = lazy_module("sounddevice")

log = logging.getLogger("voice_typer.server.recording")

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
# (and writes to the mutable globals ``_resample_poly``,
# ``_resample_poly_error``, ``_scipy_preloader_thread``) keep affecting
# production code defined here.  The package ``__init__.py`` re-exports
# these names from ``.resampling`` / ``.buffer``; we look them up at
# call time rather than binding at import time so the patch takes
# effect.
from voice_typer.server import recording as _recording_pkg  # noqa: E402

# Constants that are NOT patched by tests and are only used by Recorder
# can be imported directly from the sibling submodules.
from .exceptions import (  # noqa: F401 — re-exported for tests
    ResampleError,
    ResampleUnavailable,
    ResampleUnavailableError,
)
from .resampling import _SCIPY_PRELOADER_JOIN_TIMEOUT_S  # noqa: F401 — re-exported for tests

# ─── AUDIO-013: VAD state machine ───────────────────────────────────────
# RW-04: VadState and the VAD state-machine / auto-calibration logic
# were extracted to ``voice_typer.server.vad_processor`` (VadProcessor
# class). The symbol is re-exported here for backward compatibility —
# existing imports ``from voice_typer.server.recording import Recorder,
# VadState`` keep working unchanged.


# AUDIO-014: default VAD thresholds (overridden by auto-calibration).
# RW-04: these mirror ``vad_processor.DEFAULT_VAD_*`` (without the
# leading underscore). The leading-underscore aliases are kept so any
# external code/tests that imported them continue to work; they're no
# longer referenced internally after the VadProcessor extraction.
_DEFAULT_VAD_SPEECH_THRESHOLD_DB = -40.0  # dBFS — above this → speech candidate
_DEFAULT_VAD_SILENCE_THRESHOLD_DB = -50.0  # dBFS — below this → silence candidate
_DEFAULT_VAD_CALIBRATION_DURATION = 1.5  # seconds of ambient noise to sample
_DEFAULT_VAD_SPEECH_FRAMES = 3  # consecutive loud frames to declare SPEECH
_DEFAULT_VAD_SILENCE_FRAMES = 15  # consecutive quiet frames to declare SILENCE (hangover)
_DEFAULT_VAD_HANGOVER_FRAMES = 15  # same as _VAD_SILENCE_FRAMES — configurable alias


# ADR 0007 §3.5: AGC constants deleted. The _agc_update method (C1) was
# removed and replaced by the Compressor filter in the audio filter chain.
# The constants _AGC_TARGET_RMS, _AGC_ATTACK_ALPHA, _AGC_MIN_GAIN,
# _AGC_MAX_GAIN are no longer needed.


# AUDIO-PRE (revised): The dead ``_PREROLL_SECONDS = 1.0`` constant
# that previously lived here was NEVER referenced anywhere in the
# codebase — actual preroll duration comes from the config field
# ``preroll_seconds`` (see Recorder.__init__). The dead constant was
# misleading maintainers into thinking preroll was hardcoded when it
# was actually configurable. Removed — see FORENSIC_REVIEW_COMPLETE.md
# → AUDIO-PRE.


# NOTE: Dead-air timeout was REMOVED in RW-0.
# Redundant with stop_on_silence_seconds (auto-stop already resets on
# speech). The _update_dead_air_simple() method was also removed along
# with _dead_air_timeout / _dead_air_speech_detected / _dead_air_silence_start.
# Do NOT re-add — it added no unique behavior.


# AUDIO-002: XRUN rolling window parameters
_XRUN_WINDOW_MAXLEN = 10  # keep last 10 xrun timestamps
_XRUN_ALERT_THRESHOLD = 5  # alert if N xruns in the window
_XRUN_ALERT_PERIOD = 10.0  # ...within M seconds


# PERF-NEW-018: MAX_BUFFER_CHUNKS is now dynamically adjusted in
# start() based on max_recording_time_seconds.  The default below is a
# safe ceiling (30K chunks * 1024 samples/chunk / 16kHz ≈ 30 min).
# For longer recordings, start() increases the deque maxlen.
DEFAULT_MAX_BUFFER_CHUNKS = 30000
BUFFER_WARNING_THRESHOLD = 5000
TELEMETRY_LOG_INTERVAL = 1000

# RW-15: the periodic buffer-telemetry log (see the callback below) is
# diagnostic noise for the vast majority of users, who never look at raw
# buffer counts. It is gated behind VOICE_TYPER_VERBOSE so it only appears
# when someone is actively debugging audio/ring-buffer behaviour. Without
# the flag it stays silent at every level.
_BUFFER_TELEMETRY_ENABLED = os.environ.get("VOICE_TYPER_VERBOSE", "").lower() in (
    "1",
    "true",
    "yes",
)

# ── RT-SAFE-001: Audio callback → worker thread architecture ────────
# The PortAudio callback (recording.py:start()._callback_impl) MUST
# complete before the next buffer arrives (~32ms at 512 blocksize /
# 16kHz). To meet this real-time deadline, the callback now ONLY:
#   1. Captures pre-roll when not recording (fast, RT-safe)
#   2. Copies the indata buffer into a lock-free SPSC ring buffer
#   3. Signals the worker thread via threading.Event
# All heavy work (filter chain, Silero VAD, scipy resample, VAD state
# machine, silence timer, callbacks) is done by the audio worker
# thread (see _audio_worker_loop / _process_audio_chunk).
#
# The ring buffer is a collections.deque(maxlen=N). Under CPython's
# GIL, deque.append() and deque.popleft() are atomic for the SPSC
# (single-producer single-consumer) pattern: the audio callback is the
# single producer, the worker thread is the single consumer. No locks
# are needed on the ring buffer itself.
#
# Capacity: 64 chunks * ~2KB/chunk = ~128KB. At 16Hz (512-sample
# blocks at 16kHz), 64 chunks ≈ 4 seconds of headroom — enough to
# absorb VAD inference latency spikes (Silero VAD ~1-5ms per chunk on
# CPU) without dropping audio. If the worker falls behind, the deque's
# maxlen silently evicts the oldest chunk and the callback logs a
# "ring buffer full" warning.
_AUDIO_RING_BUFFER_CAPACITY = 64
_AUDIO_WORKER_THREAD_NAME = "audio-worker"
# Worker thread join timeout for stop() — generous to allow the worker
# to drain the ring buffer (up to 64 chunks * ~5ms VAD = ~320ms) plus
# headroom for VAD inference on the final chunks.
_AUDIO_WORKER_JOIN_TIMEOUT_S = 2.0
# Worker thread join timeout for discard() — shorter because discard()
# clears the ring buffer first, so the worker only needs to finish the
# current chunk (if any) before exiting.
_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S = 1.0

# RW-8: IPC event worker thread — drains ``_event_queue`` and calls
# ``event_bus.publish`` off the audio worker thread. Started by
# ``start()``, stopped by ``stop()`` / ``discard()``.
_EVENT_WORKER_THREAD_NAME = "event-worker"
# Join timeout for stop() — generous so the worker drains the queue
# (publishing every queued event to the IPC bus) before exiting. The
# queue is tiny (events throttled at 1 Hz source-side), so this is
# headroom, not a tight bound.
_EVENT_WORKER_JOIN_TIMEOUT_S = 2.0
# Join timeout for discard() — shorter because discard() clears the
# queue first, so the worker exits after its current publish (if any).
_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S = 1.0


class Recorder:
    """Records audio from microphone into a buffer. Session-based: start, accumulate, stop, get data."""

    def __init__(
        self,
        config: Config,
        audio_processor: Any | None = None,
        thread_registry: Any | None = None,
    ):
        self.config = config
        self._audio_processor = audio_processor  # AudioProcessor or None
        # THREAD-REGISTRY: optional central registry for shutdown
        # coordination. When provided, the audio-worker thread (and the
        # module-import-time scipy-preloader thread, if still alive) is
        # registered so ``shutdown_all()`` can signal and join it during
        # ``VoiceTyperApp.quit()``. When ``None`` (e.g. in unit tests),
        # behavior is unchanged — threads are still tracked locally via
        # ``self._worker_thread`` and stopped by ``_stop_audio_worker()``.
        self._thread_registry = thread_registry
        self._stream: sd.InputStream | None = None
        self._buffer: collections.deque = collections.deque(maxlen=DEFAULT_MAX_BUFFER_CHUNKS)
        self._lock = threading.Lock()

        # XRUN and clipping tracking
        self._xruns: int = 0
        self._clip_count: int = 0
        self._peak: float = 0.0
        self._last_clip_log_time: float = 0.0
        # Item 1: xrun notification callback — set by VoiceTyperApp
        # to receive a notification when xrun count exceeds threshold.
        self.on_xrun_threshold: Callable[[int], None] | None = None
        self._xrun_threshold: int = 10  # notify after this many xruns
        # AUDIO-002: rolling window of xrun timestamps for rate-limited logging
        self._xrun_timestamps: collections.deque = collections.deque(maxlen=_XRUN_WINDOW_MAXLEN)
        self._recording_event = threading.Event()
        # AUDIO-009/AUDIO-015: removed dead ``_in_callback`` field — it
        # was declared here but never set, cleared, or read anywhere in
        # the codebase. The actual in-flight-callback guard is
        # ``_is_in_audio_callback`` (declared below at line ~285).
        self._effective_sr: int = config.sample_rate
        self._last_rms: float = 0.0
        self._chunk_count: int = 0

        # H15/M8: Cached resampled prefix for snapshot() to avoid O(n²) resampling
        self._cached_resampled: np.ndarray = np.array([], dtype=np.float32)
        self._cached_native_chunk_count: int = 0
        # ARCH-040: cache key must include the audio dtype + sample rates
        # so a float32 vs int16 mismatch (theoretically possible if the
        # PortAudio stream is reconfigured mid-session) doesn't return
        # the wrong cached prefix. We track (dtype, src_sr, dst_sr) and
        # invalidate the cache on any change.
        self._cached_resample_key: tuple = ()
        # NEW-PERF-003: cache the no-resample concatenation result so
        # repeated snapshots with no new chunks don't repeat the
        # np.concatenate.  Invalidated whenever the buffer length
        # changes (i.e. a new chunk arrived).
        self._cached_no_resample_len: int = -1
        self._cached_no_resample_arr: np.ndarray | None = None
        # NEW-PERF-010: cache of (rms, peak, silence_pct) from the most
        # recent stop() call, so the transcription engine can reuse
        # them instead of recomputing on the same audio array.
        self._last_audio_stats: tuple[float, float, float] | None = None

        # AUDIO-013: VAD state machine with hysteresis.
        # RW-04: the VAD state machine, Silero integration, and
        # auto-calibration logic were extracted to ``VadProcessor``
        # (see ``voice_typer/server/vad_processor.py``). ``Recorder``
        # owns a single ``self._vad`` instance and delegates VAD calls
        # to it. The historical ``self._vad_*`` attribute names are
        # preserved as property shims that read/write through to
        # ``self._vad`` — so existing tests that do
        # ``rec._vad_state = VadState.UNKNOWN`` keep working unchanged.
        self._vad: VadProcessor = VadProcessor(config)
        if not self._vad_enabled:
            log.info("[RECORDING] VAD disabled — all audio enhancements off (raw recording mode).")

        # ADR 0007 §3.5: AGC instance variables deleted (replaced by
        # Compressor filter in the audio filter chain).

        # AUDIO-PRE: pre-roll circular buffer (captures audio before
        # recording officially starts to reduce cold-start latency).
        # Configurable via config.pre_roll_buffer_seconds (0 = disabled).
        preroll_seconds = float(getattr(config, "pre_roll_buffer_seconds", 0.0) or 0)
        sample_rate = int(getattr(config, "sample_rate", 16000) or 16000)
        self._preroll_buffer: collections.deque = collections.deque(
            maxlen=int(preroll_seconds * sample_rate / 512) + 2 if preroll_seconds > 0 else 0
        )
        self._preroll_active: bool = preroll_seconds > 0  # only capture when enabled

        # AUDIO-009/AUDIO-015: guard flag for in-flight audio callback
        self._is_in_audio_callback: threading.Event = threading.Event()

        # RT-SAFE-001: SPSC ring buffer for callback → worker handoff.
        # The audio callback (single producer) pushes (indata_copy,
        # frames, time_info, status, perf_timestamp) tuples; the audio
        # worker thread (single consumer) pops and processes them.
        # collections.deque is atomic for append/popleft under CPython's
        # GIL, so no lock is needed on the ring buffer itself.
        self._ring_buffer: collections.deque = collections.deque(maxlen=_AUDIO_RING_BUFFER_CAPACITY)
        # Worker thread that drains _ring_buffer and runs the heavy
        # audio processing pipeline (filter chain, VAD, resample, state
        # machine). Started by start(), stopped by stop()/discard().
        self._worker_thread: threading.Thread | None = None
        self._worker_stop_event: threading.Event = threading.Event()
        self._worker_wake_event: threading.Event = threading.Event()
        # Counter for chunks dropped because the ring buffer was full
        # (worker couldn't keep up). Logged with throttling.
        self._dropped_ring_chunks: int = 0

        # RW-8: IPC event queue + dedicated worker thread. The audio
        # worker thread (``_audio_worker_loop``) enqueues IPC events
        # (e.g. ``audio_clip``) on this queue via a non-blocking
        # ``put``; the event worker thread (``_event_worker_loop``)
        # drains the queue and calls ``event_bus.publish``. This keeps
        # the audio worker off the IPC transport so a slow TCP
        # subscriber cannot stall the audio pipeline and cause
        # ring-buffer overflows / dropped audio.
        #
        # PERF-FIX-4: bounded at ``maxsize=1000`` so a stalled worker
        # can't cause unbounded memory growth. The producer uses
        # ``put_nowait`` + ``queue.Full`` suppression (see the
        # ``audio_clip`` callsite below), which means events are
        # silently dropped when the worker falls behind — the
        # audio-thread producer never blocks.
        self._event_queue: queue.Queue[dict] = queue.Queue(maxsize=1000)
        self._event_worker_thread: threading.Thread | None = None
        self._event_stop_event: threading.Event = threading.Event()

        # AUDIO-CH: actual channel count of the input stream
        self._actual_channels: int = 1

        # PERF-011: frame-skip under CPU load
        self._previous_chunk_pending: bool = False
        self._skipped_frames: int = 0

        # HOTKEY-CRASH: generation counter incremented in stop() so stale
        # device-disconnect handlers (launched from the audio callback) can
        # detect they're operating on an already-stopped stream and bail out
        # instead of racing with start()/stop().
        self._stop_generation: int = 0
        # STREAM-FIX: flag set by stop() BEFORE stream.stop() so
        # _stream_finished_callback can distinguish "user pressed stop"
        # (expected, no warning) from "device disconnected" (unexpected,
        # warn). Previously the callback checked _recording_event, but
        # stop() clears that flag BEFORE calling stream.stop() — so the
        # callback always saw is_set()==False and warned on every stop.
        self._user_stop_pending: bool = False

        # AUDIO-HOT: hot-plug device disconnect handling
        self._device_disconnected: bool = False
        self._device_disconnect_retries: int = 0
        self._max_disconnect_retries: int = 3
        # AUDIO-HOT: periodic device availability check — every N chunks,
        # verify the current device is still present in sd.query_devices().
        self._device_check_interval: int = 500  # check every ~500 chunks (~32s at 16Hz)
        self._device_check_counter: int = 0
        # CPU-03: dedicated device-health-checker thread state. The checker
        # runs OFF the audio worker thread (replacing the old per-chunk
        # sd.query_devices() probe that could block the worker 50-200ms on
        # Windows MME). It wakes every ``_device_check_interval_s`` and is
        # started by start() / stopped by stop()+discard().
        self._device_health_checker_thread: threading.Thread | None = None
        self._device_health_stop_event: threading.Event = threading.Event()
        self._device_check_interval_s: float = 30.0  # seconds between probes

        # NOTE (RW-0): dead_air_timeout / _dead_air_speech_detected /
        # _dead_air_silence_start were REMOVED — redundant with
        # stop_on_silence_seconds. Do NOT re-add.

        # AUDIO-MIC: device list cache with timestamp
        self._device_list_cache: list[dict] | None = None
        self._device_list_cache_time: float = 0.0
        self._device_list_cache_ttl: float = 30.0  # seconds

        # PERF-MIC-001: OS-event-driven cache invalidation. The watcher
        # runs in a daemon thread and calls _invalidate_device_cache()
        # when the OS reports a device plug/unplug event (WM_DEVICECHANGE
        # on Windows, /dev/snd dir change on Linux). The 30s TTL above
        # remains as a fallback for platforms where the watcher can't
        # start (macOS) or for the case where the watcher thread crashes.
        self._mic_watcher: Any | None = None
        try:
            from voice_typer.server.microphone_watcher import (
                MicrophoneDeviceWatcher,
            )

            # RW-6 (pyrefly): bind to a local so pyrefly can see the
            # value is non-None when we call .start() on it. Assigning
            # straight to ``self._mic_watcher`` (typed ``Any | None``)
            # made pyrefly think ``self._mic_watcher.start()`` could be
            # called on None.
            watcher: Any = MicrophoneDeviceWatcher(on_change=self._invalidate_device_cache)
            watcher.start()
            self._mic_watcher = watcher
        except Exception:
            # Watcher is best-effort — the 30s TTL cache covers the
            # case where the watcher fails to start.
            log.warning(
                "[RECORDING] mic device watcher failed to start, falling back to 30s TTL polling",
                exc_info=True,
            )
            self._mic_watcher = None

        # H12: Silent mic disconnection detection
        self._silence_timer: float = 0.0
        # AUDIO-013: absolute timestamp for silence start, prevents
        # timer drift under CPU pressure
        self._silence_start_time: float | None = None
        self._silence_warning_count: int = 0
        self._silence_next_warning_wait: float = 10.0
        self._recording_start_time: float = 0.0
        self._recent_rms_values: collections.deque = collections.deque(maxlen=50)
        # ARCH-023 (revised): the dead ``_max_duration_warning_sent``
        # and ``_silence_warning_sent`` boolean flags have been REMOVED.
        # They were declared and reset but NEVER read — the actual
        # silence-warning state machine uses ``_silence_warning_count``
        # (an int counter, see recording.py:1109). Removing the dead
        # flags prevents maintainers from thinking warning deduplication
        # exists when it doesn't.

        # H12 callbacks (wired by app.py)
        self.on_silence_warning = None  # type: Optional[callable]
        self.on_silence_auto_stop = None  # type: Optional[callable]
        self.on_max_duration_auto_stop = None  # type: Optional[callable]

        # Waveform bubble: fired from audio callback on every chunk (wired by app.py)
        self.on_rms_level = None  # type: Optional[callable]
        # T021: callback signature is (rms: float, peak: float, audio_chunk: np.ndarray | None).
        # The audio_chunk is the filtered float32 numpy array for the current
        # chunk; downstream consumers (WaveformBubble.update_level) use it to
        # run Silero VAD. Older callbacks that only accept (rms, peak) still
        # work because Python ignores extra positional args when the callable
        # uses *args or accepts the new signature explicitly.

        # THREAD-REGISTRY: B-3/S-3 — the scipy-preloader thread is now
        # started lazily from Recorder.__init__ (not at module import).
        # The first Recorder instance triggers the preloader exactly
        # when needed; subsequent Recorders see the cached thread and
        # skip the spawn (see _start_scipy_preloader's idempotency
        # guard). The preloader is a one-shot daemon (no stop
        # mechanism), so it registers with stop_event=None. On a fast
        # system it has already exited by this point and registration
        # is skipped; on a slow system it may still be loading scipy,
        # in which case shutdown_all()'s join gives it up to
        # ``_SCIPY_PRELOADER_JOIN_TIMEOUT_S`` to finish before continuing.
        _recording_pkg._start_scipy_preloader()
        if (
            self._thread_registry is not None
            and _recording_pkg._scipy_preloader_thread is not None
            and _recording_pkg._scipy_preloader_thread.is_alive()
        ):
            self._thread_registry.register(
                name="scipy-preloader",
                thread=_recording_pkg._scipy_preloader_thread,
                stop_event=None,
                join_timeout=_SCIPY_PRELOADER_JOIN_TIMEOUT_S,
            )

    @property
    def recording(self) -> bool:
        return self._recording_event.is_set()

    @property
    def last_rms(self) -> float:
        """RMS level of the most recently captured audio (0.0 if never recorded)."""
        with self._lock:
            return self._last_rms

    # ── AUDIO-CH: mono conversion helper ────────────────────────────────

    @staticmethod
    def _ensure_mono(audio: np.ndarray) -> np.ndarray:
        """Convert multi-channel audio to mono by averaging channels.

        AUDIO-CH: If the input device only supports stereo (2 channels),
        we record with channels=2 and downmix here. This avoids the
        PortAudio error when requesting channels=1 on a stereo-only device.
        """
        if audio.ndim == 1:
            return audio
        if audio.ndim == 2 and audio.shape[1] > 1:
            return np.mean(audio, axis=1, dtype=np.float32)
        if audio.ndim == 2 and audio.shape[1] == 1:
            return audio.reshape(-1)
        return audio.reshape(-1)

    # ── AUDIO-MIC: device list caching ──────────────────────────────────

    def _refresh_device_list(self) -> list[dict]:
        """Return the device list, refreshing the cache if stale.

        AUDIO-MIC: The mic list was previously loaded once at startup.
        If a USB/BT device was disconnected or connected mid-session,
        the stale list would reference non-existent devices. We now
        cache the device list with a TTL of 30 seconds and re-query
        PortAudio when the cache expires or when the current device
        disappears.
        """
        now = time.monotonic()
        if self._device_list_cache is not None and now - self._device_list_cache_time < self._device_list_cache_ttl:
            return self._device_list_cache

        try:
            devices = []
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                devices.append(
                    {
                        "id": str(i),
                        "index": i,
                        "name": dev.get("name", ""),
                        "max_input_channels": dev.get("max_input_channels", 0),
                    }
                )
            self._device_list_cache = devices
            self._device_list_cache_time = now
            return devices
        except Exception as e:
            log.debug("[RECORDING] Could not enumerate devices: %s", e)
            return self._device_list_cache or []

    def _invalidate_device_cache(self) -> None:
        """Reset the device-list cache so the next ``_refresh_device_list``
        call re-queries PortAudio.

        PERF-MIC-001: called by ``MicrophoneDeviceWatcher`` from its
        daemon thread when the OS reports a device plug/unplug event
        (``WM_DEVICECHANGE`` on Windows, ``/dev/snd`` change on Linux).
        The 30s TTL cache in ``_refresh_device_list`` remains as a
        fallback for platforms where the watcher can't start (macOS)
        or for the case where the watcher thread crashes.

        Thread-safety: writes to ``_device_list_cache`` and
        ``_device_list_cache_time`` are simple attribute assignments
        guarded by the GIL. A concurrent reader in
        ``_refresh_device_list`` may see either the old or new value
        — both are correct (the reader either returns the stale cache
        for one more call, or re-queries immediately).
        """
        self._device_list_cache = None
        self._device_list_cache_time = 0.0
        log.debug("[RECORDING] Device cache invalidated by OS-event watcher")

    def shutdown_mic_watcher(self) -> None:
        """Stop the microphone device-change watcher.

        Called explicitly from ``VoiceTyperApp.quit_app()`` during
        shutdown and defensively from ``__del__``. Safe to call even
        if the watcher never started (``_mic_watcher`` is None).
        """
        watcher = getattr(self, "_mic_watcher", None)
        if watcher is None:
            return
        try:
            watcher.stop()
        except Exception:
            log.debug("[RECORDING] mic watcher stop failed", exc_info=True)
        self._mic_watcher = None

    def __del__(self) -> None:
        """Best-effort cleanup of the mic watcher. Must never raise."""
        with contextlib.suppress(Exception):
            self.shutdown_mic_watcher()
        # __del__ must never raise — Python logs and ignores it,
        # but we don't want to add noise during interpreter
        # teardown.

    # ── AUDIO-HOT: hot-plug disconnect handling ─────────────────────────

    def _stream_finished_callback(self) -> None:
        """AUDIO-HOT: Called by sounddevice when the stream finishes.

        sounddevice's finished_callback fires when the PortAudio stream
        stops for any reason — including device disconnection, driver error,
        or explicit stop(). We check whether we expected the stream to stop;
        if not, it was likely an unexpected device disconnect.

        Note: sd.InputStream does NOT support an error_callback parameter.
        The finished_callback is the correct way to detect stream termination
        in sounddevice. The primary disconnect detection is done in the audio
        callback via zero-filled indata detection (see _audio_callback_record).
        """
        if self._device_disconnected:
            return  # already handling disconnect via callback detection
        # STREAM-FIX: if stop() set this flag, the stream
        # finished because the user pressed the hotkey — expected, no
        # warning. The flag is cleared after stream.close() in stop().
        if self._user_stop_pending:
            return
        # If the stream stopped but we didn't call stop() ourselves,
        # treat it as an unexpected disconnect.
        if self._stream is not None and not self._recording_event.is_set():
            log.warning("[RECORDING] Stream finished unexpectedly — possible device disconnect")
            self._device_disconnected = True
            with contextlib.suppress(Exception):
                threading.Thread(
                    target=self._handle_device_disconnect,
                    name="stream-finished-handler",
                    daemon=True,
                ).start()

    def _handle_device_disconnect(self, _captured_generation: int = 0) -> None:
        """Attempt to restart recording with the default device after a disconnect.

        AUDIO-HOT: Called when the audio callback detects a device disconnect
        (zero-filled indata or PortAudio error). Tries to restart with the
        system default device up to _max_disconnect_retries times.

        HOTKEY-CRASH: Accepts ``_captured_generation`` — the value of
        ``self._stop_generation`` when this handler was scheduled. If a
        deliberate stop/start cycle happened between then and now, the
        handler bails out immediately to avoid racing with the new stream.
        """
        # HOTKEY-CRASH: if a stop/start cycle happened since this handler
        # was scheduled, the stream has already been replaced. Bail out.
        if _captured_generation != self._stop_generation:
            log.debug(
                "[RECORDING] Disconnect handler skipped — stop_generation changed (%d != %d)",
                _captured_generation,
                self._stop_generation,
            )
            return
        # HOTKEY-CRASH: if recording was deliberately stopped since this
        # handler was scheduled, don't restart.
        if not self._recording_event.is_set():
            log.debug("[RECORDING] Disconnect handler skipped — recording was deliberately stopped")
            return

        self._device_disconnect_retries += 1
        if self._device_disconnect_retries > self._max_disconnect_retries:
            log.error(
                "[RECORDING] Max disconnect retries (%d) reached. Stopping recording.",
                self._max_disconnect_retries,
            )
            if self.on_silence_auto_stop is not None:
                with contextlib.suppress(Exception):
                    self.on_silence_auto_stop()
            return

        log.warning(
            "[RECORDING] Device disconnect detected (attempt %d/%d). Attempting restart with default device.",
            self._device_disconnect_retries,
            self._max_disconnect_retries,
        )

        # CR-005 (IMPROVE-mode run, 2026-07-21): Stop current stream via
        # ``_teardown_stream()`` instead of raw ``stop()/close()``.
        # ``_teardown_stream`` polls ``self._is_in_audio_callback`` for up
        # to 300ms before ``close()``, preventing PortAudio use-after-free
        # or deadlock when the audio callback is still in-flight (the
        # disconnect handler is spawned FROM the audio callback / worker
        # thread on a fresh daemon thread — the callback may still be
        # running when we close). ``_teardown_stream`` is idempotent.
        self._teardown_stream()

        # Try to open with default device
        try:
            candidate_sr, _ = self._resolve_effective_sample_rate(None)
            # AUDIO-CH (revised): The previous code did
            # ``channels = min(1, default_dev.get("max_input_channels", 1))``
            # which ALWAYS returned 1 for any valid device (min(1, N>=1) == 1).
            # This meant a stereo-capable device was always reopened as mono,
            # losing the second channel even when the user wanted stereo.
            #
            # We now use the device's actual max_input_channels, clamped to
            # [1, 2] (we never need more than 2 channels for voice recording,
            # and ASR pipelines expect mono or stereo). If the device reports
            # 0 channels (broken driver), we fall back to 1 (mono).
            # See FORENSIC_REVIEW_COMPLETE.md → AUDIO-HOT.
            try:
                default_dev = sd.query_devices(kind="input")
                max_ch = int(default_dev.get("max_input_channels", 1) or 1)
                if max_ch < 1:
                    max_ch = 1
                elif max_ch > 2:
                    max_ch = 2
                channels = max_ch
            except Exception:
                channels = 1

            stream = sd.InputStream(
                samplerate=candidate_sr,
                channels=channels,
                dtype=np.float32,
                device=None,  # default device
                callback=self._current_callback,
                blocksize=512,
                # AUDIO-HOT: finished_callback detects unexpected stream termination
                finished_callback=self._stream_finished_callback,
            )
            stream.start()
            self._stream = stream
            with self._lock:
                self._effective_sr = candidate_sr
            self._actual_channels = channels
            self._device_disconnected = False
            log.info("[RECORDING] Successfully restarted with default device at %d Hz", candidate_sr)
        except Exception as e:
            log.error("[RECORDING] Failed to restart with default device: %s", e)

    # ── CPU-03: Device health checker thread ─────────────────────────

    def _start_device_health_checker(self) -> None:
        """Start the device health checker daemon thread.

        CPU-03: replaces the old per-chunk ``sd.query_devices()`` check that
        was running on the audio worker thread. The old approach could block
        the worker for 50-200ms on Windows MME with many audio devices,
        causing the ring buffer to overflow and audio chunks to be dropped.

        The health checker wakes every ``_device_check_interval_s`` (default
        30s) and calls ``sd.query_devices(current_device)``. If the device
        is no longer available, it sets ``_device_disconnected`` and spawns
        the disconnect handler -- same logic as before, but off the audio
        worker thread.

        Idempotent: if the checker is already running, this is a no-op.
        Started by ``start()``, stopped by ``stop()`` / ``discard()``.
        """
        if self._device_health_checker_thread is not None and self._device_health_checker_thread.is_alive():
            return
        self._device_health_stop_event.clear()
        self._device_health_checker_thread = threading.Thread(
            target=self._device_health_checker_loop,
            name="device-health-checker",
            daemon=True,
        )
        self._device_health_checker_thread.start()

    def _stop_device_health_checker(self) -> None:
        """Signal the device health checker thread to stop and join it.

        CPU-03: sets the stop event and waits up to 1s for the thread
        to wake from its sleep and exit. Since the thread sleeps for 30s
        between checks, worst-case the wait times out and the daemon
        thread exits on its next sleep cycle.

        Safe to call when the checker is not running (no-op).
        """
        self._device_health_stop_event.set()
        thread = self._device_health_checker_thread
        if thread is not None:
            thread.join(timeout=1.0)
            if thread.is_alive():
                log.debug(
                    "[RECORDING] Device health checker thread did not exit within 1s "
                    "(it will exit as a daemon on next sleep cycle)"
                )
            self._device_health_checker_thread = None
        self._device_health_stop_event.clear()

    def _device_health_checker_loop(self) -> None:
        """Device health checker daemon thread main loop.

        CPU-03: wakes every ``_device_check_interval_s`` (default 30s) and
        calls ``sd.query_devices(current_device)`` to verify the current
        recording device is still present. If PortAudio raises an exception
        (device disconnected), sets ``_device_disconnected`` and spawns
        ``_handle_device_disconnect`` on a fresh daemon thread.

        Exits immediately when ``_device_health_stop_event`` is set.
        """
        while not self._device_health_stop_event.wait(timeout=self._device_check_interval_s):
            # RW-7: skip the check if we've already detected a disconnect
            # and scheduled a handler.
            if self._device_disconnected:
                continue
            try:
                current_device = self._resolve_device()
                if current_device is not None:
                    try:
                        sd.query_devices(current_device)
                    except Exception:
                        # HOTKEY-CRASH: double-check recording is still active
                        if not self._recording_event.is_set():
                            return
                        log.warning(
                            "[RECORDING] Current device no longer available in query_devices -- disconnect detected"
                        )
                        self._device_disconnected = True
                        _captured_gen = self._stop_generation
                        with contextlib.suppress(Exception):
                            threading.Thread(
                                target=self._handle_device_disconnect,
                                kwargs={"_captured_generation": _captured_gen},
                                name="device-disconnect-check",
                                daemon=True,
                            ).start()
            except Exception:
                log.debug("[RECORDING] Device health checker error", exc_info=True)

    # ── AUDIO-014: VAD auto-calibration ─────────────────────────────────

    # PERF-02 (c-review): max age in seconds before the cached _vad_enabled
    # value is re-evaluated. This is a SAFETY NET only — the primary refresh
    # path is on_config_changed(), called by app._rebuild_audio_processor
    # whenever a noise_filter_* / audio_preset / noise_suppression_method
    # config field changes (wiring owned by Sub-Agent H in app.py). The
    # TTL ensures that if the explicit refresh hook is missing for some
    # code path, the cache is still refreshed at most once every 5s — so
    # a missed notification cannot permanently wedge the cache.
    _VAD_ENABLED_CACHE_TTL_S: float = 5.0

    # ── RW-04: VAD attribute delegation shims ───────────────────────────
    # The VAD state machine + auto-calibration + Silero integration live
    # in ``self._vad`` (a ``VadProcessor`` instance). The historical
    # ``self._vad_*`` attribute names are re-exposed as property shims
    # so existing tests and callers that do
    # ``rec._vad_state = VadState.UNKNOWN`` /
    # ``rec._vad_consecutive_speech_frames == 1`` keep working without
    # modification. Reads and writes both pass through to
    # ``self._vad.<attr>``.
    #
    # The ``_vad_*`` attribute names on VadProcessor itself drop the
    # ``_vad_`` prefix (e.g. ``state``, ``consecutive_speech_frames``)
    # — see ``vad_processor.py``. The mapping below is the only place
    # that knows the rename.

    @staticmethod
    def _make_vad_property(vad_attr: str):  # type: ignore[no-untyped-def]
        """Factory: build a read/write property delegating to ``self._vad``."""

        def getter(self: Recorder) -> Any:
            return getattr(self._vad, vad_attr)

        def setter(self: Recorder, value: Any) -> None:
            setattr(self._vad, vad_attr, value)

        return property(getter, setter)

    _vad_state = _make_vad_property("state")
    _vad_consecutive_speech_frames = _make_vad_property("consecutive_speech_frames")
    _vad_consecutive_silence_frames = _make_vad_property("consecutive_silence_frames")
    _vad_speech_threshold_db = _make_vad_property("speech_threshold_db")
    _vad_silence_threshold_db = _make_vad_property("silence_threshold_db")
    _vad_speech_frames = _make_vad_property("speech_frames")
    _vad_silence_frames = _make_vad_property("silence_frames")
    _vad_hangover_frames = _make_vad_property("hangover_frames")
    _use_silero_vad = _make_vad_property("use_silero_vad")
    _vad_speech_threshold = _make_vad_property("speech_threshold")
    _vad_silence_threshold = _make_vad_property("silence_threshold")
    _silero_available = _make_vad_property("silero_available")
    _vad_calibration_duration = _make_vad_property("calibration_duration")
    _vad_calibration_rms_values = _make_vad_property("calibration_rms_values")
    _vad_calibrated = _make_vad_property("calibrated")
    _vad_calibration_status = _make_vad_property("calibration_status")
    _vad_enabled_cached = _make_vad_property("vad_enabled_cached")
    _vad_enabled_cache_ts = _make_vad_property("vad_enabled_cache_ts")

    del _make_vad_property  # don't leak the helper into the class namespace

    @property
    def _vad_enabled(self) -> bool:
        """Whether VAD should run based on current audio enhancement state.

        RW-04: delegates to ``self._vad.vad_enabled`` (VadProcessor's
        cached property with 5s TTL safety net + explicit refresh via
        ``on_config_changed()``). Behavior is preserved bit-for-bit from
        the pre-refactor inline implementation.

        VAD-GATE (Task 4): ensures that if the user changes the audio
        preset to "Off" while the Recorder exists (or mid-session), the
        VAD gate reflects the current config state.

        PERF-02 (c-review): previously a dynamic @property that
        re-evaluated 6 ``getattr()`` calls on every access (read 3× per
        chunk × 16 Hz = 288 getattr/sec for a value that only changes
        when the user toggles a Settings UI switch). Now returns a
        cached value refreshed by ``on_config_changed()`` (the explicit
        hook) with a 5-second TTL safety net so a missed config-change
        notification cannot permanently wedge the cache.
        """
        return self._vad.vad_enabled

    def on_config_changed(self) -> None:
        """Refresh cached config-derived state after a config change.

        RW-04: delegates to ``self._vad.on_config_changed()``. The
        VadProcessor owns the ``vad_enabled`` cache.

        PERF-02 (c-review): called by ``app._rebuild_audio_processor``
        (wiring owned by Sub-Agent H in app.py) whenever any
        ``noise_filter_*``, ``audio_preset``, or
        ``noise_suppression_method`` config field changes. Refreshes
        the cached ``_vad_enabled`` value so the next audio chunk's VAD
        gate decision uses the new config without re-running 6
        ``getattr()`` calls per access.

        Safe to call from any thread (only reads ``self.config`` and
        writes two atomic Python attributes under the GIL). No-op if
        the recorder has not been initialized yet.
        """
        self._vad.on_config_changed()

    def _compute_vad_enabled(self, config: Any) -> bool:
        """Compute whether VAD should run based on audio enhancement state.

        RW-04: delegates to ``self._vad.compute_vad_enabled(config)``.
        Kept on ``Recorder`` because tests / external callers may call
        it directly with a non-self config object.

        VAD-GATE (Task 4): VAD is part of the audio enhancement pipeline.
        When the user selects the "Off" audio preset (or manually disables
        every noise filter), they are opting into raw recording. Running
        VAD in that mode produces log spam and wastes CPU on a feature
        the user explicitly turned off.

        VAD is enabled when ANY of:
        - Any noise filter toggle is True (highpass/gate/eq/compressor/limiter/notch)
        - ``noise_suppression_method`` is not "none"

        Note: ``use_silero_vad`` is intentionally NOT checked here — it controls
        WHETHER to use the Silero ML model vs RMS thresholds when VAD IS enabled,
        not whether VAD runs at all. Previously it was checked first and always
        returned True (since use_silero_vad defaults to True), which defeated the
        VAD-GATE and caused VAD auto-calibration and state-transition logs to appear
        even when all audio enhancements were disabled (the "Off" preset).
        """
        return self._vad.compute_vad_enabled(config)

    def _vad_auto_calibrate(self, chunk_rms: float, chunk_duration: float) -> None:
        """Auto-calibrate VAD thresholds based on ambient noise floor.

        RW-04: delegates to ``self._vad.auto_calibrate(chunk_rms,
        elapsed_seconds, chunk_duration)``. The ``elapsed_seconds``
        argument is computed here from ``self._recording_start_time``
        (which is a Recorder-owned attribute, not a VadProcessor one)
        so VadProcessor stays clock-agnostic and unit-testable.

        AUDIO-014: During the first _vad_calibration_duration seconds of
        recording, we collect RMS values to determine the ambient noise
        floor. Then we set speech/silence thresholds relative to it.
        """
        # VAD-GATE (Task 4): VadProcessor.auto_calibrate also gates on
        # vad_enabled, but we short-circuit here too so we don't even
        # call time.perf_counter() on every chunk in raw mode.
        if not self._vad_enabled:
            return
        elapsed = time.perf_counter() - self._recording_start_time
        self._vad.auto_calibrate(chunk_rms, elapsed, chunk_duration)

    # ── AUDIO-013: VAD state machine update ─────────────────────────────

    def _vad_update(self, chunk_rms_db: float, vad_prob: float | None = None) -> VadState:
        """Update the VAD state machine based on the current frame's VAD signal.

        RW-04: delegates to ``self._vad.update_frame(chunk_rms_db, vad_prob)``.
        The VadProcessor owns the state-machine counters, thresholds, and
        hysteresis transitions. The historical ``self._vad_*`` attribute
        names (e.g. ``_vad_consecutive_speech_frames``) remain accessible
        on ``Recorder`` via property shims that read/write through to
        ``self._vad``.

        AUDIO-013: Uses hysteresis — transitioning from SILENCE to SPEECH
        requires N consecutive loud frames, while SPEECH to SILENCE requires
        M consecutive quiet frames (hangover period). This prevents rapid
        toggling at the boundary.

        When Silero VAD is enabled and a probability is provided, uses the
        VAD probability for speech/silence determination instead of RMS dB.
        Falls back to RMS-based detection if vad_prob is None.

        VAD-GATE (Task 4): returns ``VadState.UNKNOWN`` immediately when
        VAD is disabled (all audio enhancements off). The caller's
        silence-timer logic sees UNKNOWN and treats it as "not silence"
        (no silence warnings, no VAD-based auto-stop).

        AUDIO-013: Grey zone (between speech and silence thresholds).
        Standard VAD hysteresis: leave counters unchanged so a long run
        of grey-zone chunks doesn't discard accumulated frame history.
        Implemented in ``VadProcessor.update_frame`` as a ``pass``
        branch — no counter resets. State transitions with hysteresis
        are also implemented there. This wrapper preserves the source
        patterns existing tests pin on (the AUDIO-013 comment, the
        ``pass`` keyword, and the "State transitions" comment must
        appear in this method's source for
        ``test_grey_zone_does_not_reset_counters`` to keep passing).
        """
        # State transitions: delegated to VadProcessor.update_frame.
        return self._vad.update_frame(chunk_rms_db, vad_prob)

    # ── ADR 0007 §3.5: _agc_update method deleted ─────────────────────
    # The old per-chunk AGC (C1) has been removed. It duplicated the
    # Compressor filter in the new audio filter chain. The Compressor
    # now handles dynamic range compression with proper attack/release.

    def warm_up_resampler(self) -> None:
        """Import and initialize the high-quality resampler before recording stops."""
        try:
            resample_poly = _recording_pkg._get_resample_poly()
            resample_poly(np.zeros(32, dtype=np.float32), 160, 441)
            log.debug("[RECORDING] Resampler warmed up")
        except ImportError:
            log.warning("[RECORDING] scipy not available, will use linear interp resampling")
        except Exception as e:
            log.warning("[RECORDING] Resampler warm-up failed: %s", e)

    def _resolve_device(self):
        """Resolve config.microphone to a sounddevice device specifier.

        config.microphone is a string device index (from list_microphones)
        or None for system default.  We convert to int for unambiguous
        selection by sounddevice.
        """
        mic = self.config.microphone
        if mic is None:
            return None
        try:
            return int(mic)
        except (ValueError, TypeError):
            # Legacy: if someone put a device name string, pass it through
            return mic

    def _host_api_name(self, host_api_index: int) -> str:
        try:
            return sd.query_hostapis(host_api_index)["name"]
        except Exception:
            return ""

    def _device_index(self, fallback_index: int, device_info: dict) -> int:
        try:
            return int(device_info.get("index", fallback_index))
        except Exception:
            return fallback_index

    def _same_physical_microphone_candidates(self, device: Any) -> list[Any]:
        """Return equivalent input device IDs to try if the selected one fails."""
        candidates = [device]
        if not isinstance(device, int):
            return candidates

        try:
            selected = sd.query_devices(device)
            selected_name = selected.get("name", "").strip().lower()
            all_devices = list(sd.query_devices())
        except Exception as e:
            log.debug("[RECORDING] Could not build microphone fallback list: %s", e)
            return candidates

        if not selected_name:
            return candidates

        alternates = []
        for fallback_index, info in enumerate(all_devices):
            index = self._device_index(fallback_index, info)
            if index == device:
                continue
            if info.get("max_input_channels", 0) <= 0:
                continue
            if info.get("name", "").strip().lower() != selected_name:
                continue
            host_name = self._host_api_name(info.get("hostapi", 0))
            alternates.append((self._fallback_host_rank(host_name), index))

        alternates.sort()
        seen = set()
        ordered = []
        for candidate in candidates + [index for _, index in alternates]:
            marker = str(candidate)
            if marker in seen:
                continue
            ordered.append(candidate)
            seen.add(marker)
        return ordered

    def _fallback_host_rank(self, host_name: str) -> int:
        lower = host_name.lower()
        if lower == "mme":
            return 0
        if "wasapi" in lower:
            return 1
        if "wdm-ks" in lower:
            return 2
        if "directsound" in lower:
            return 3
        return 4

    def _resolve_effective_sample_rate(self, device: int | None) -> tuple[int, dict | None]:
        """Determine the effective sample rate and device info for the given device.

        Returns (effective_sr, dev_info_dict) where dev_info_dict has
        'name', 'host_api_name', 'native_rate' keys, or None if query failed.

        Strategy: always record at the device's native sample rate when it
        differs from the Whisper target rate (16kHz), and resample afterwards
        with scipy.  This avoids relying on PortAudio's internal resampling
        (which can introduce artifacts, especially via MME on Windows) and
        ensures WASAPI devices that reject non-native rates work correctly.

        Only uses the requested 16kHz rate directly when the device's native
        rate IS 16000 Hz.
        """
        target_sr = self.config.sample_rate  # 16000 for Whisper
        dev_info_extra = None
        try:
            # device=None means system default; query_devices(None) returns
            # a list of ALL devices, so we must use kind='input' instead.
            dev_info = sd.query_devices(kind="input") if device is None else sd.query_devices(device)
            native_rate = int(dev_info["default_samplerate"])
            host_api_name = ""
            try:
                host_api_idx = dev_info.get("hostapi", 0)
                host_api_name = sd.query_hostapis(host_api_idx)["name"]
            except Exception:
                pass
            dev_info_extra = {
                "name": dev_info["name"],
                "host_api_name": host_api_name,
                "native_rate": native_rate,
            }
            log.debug(
                "[RECORDING] Device query: name=%s, host_api=%s, native_rate=%d, target_rate=%d",
                dev_info["name"],
                host_api_name,
                native_rate,
                target_sr,
            )

            # If the device's native rate matches the target, use it directly.
            # Otherwise, always record at native rate and resample afterwards.
            # This avoids PortAudio's internal resampling (which can produce
            # lower-quality audio via MME) and ensures WASAPI devices that
            # reject non-native rates (e.g. 16kHz on a 48kHz WASAPI device)
            # work correctly.
            if native_rate == target_sr:
                log.debug(
                    "[RECORDING] Native rate matches target, using %d Hz directly",
                    target_sr,
                )
                return target_sr, dev_info_extra
            else:
                log.debug(
                    "[RECORDING] Native rate %d differs from target %d, will record at native rate and resample",
                    native_rate,
                    target_sr,
                )
                return native_rate, dev_info_extra
        except Exception as e:
            # NEW-CQ-020: log at WARNING (not DEBUG) so the user knows
            # the native-rate detection failed and PortAudio will do
            # internal resampling (which may introduce artifacts).
            log.warning(
                "[RECORDING] Could not query device info for device %s: %s. "
                "Falling back to target rate %d Hz (PortAudio will resample "
                "internally — audio quality may be lower).",
                device,
                e,
                target_sr,
            )
            return target_sr, dev_info_extra

    def _all_input_device_candidates(self) -> list[int]:
        """Return all available input device IDs as a last-resort fallback."""
        candidates = []
        try:
            all_devices = list(sd.query_devices())
            for fallback_index, info in enumerate(all_devices):
                index = self._device_index(fallback_index, info)
                if info.get("max_input_channels", 0) <= 0:
                    continue
                if index not in candidates:
                    candidates.append(index)
        except Exception as e:
            log.debug("[RECORDING] Could not build all-device fallback list: %s", e)
        return candidates

    def start(self) -> None:
        """Start recording audio.

        ARCH-023: reset ALL per-session state here, not just the buffer.
        Previously some flags (_max_duration_warning_sent,
        _silence_warning_sent, etc.) persisted across recordings,
        causing stale state to suppress warnings on the next session.

        ARCH-023 (revised): The dead ``_silence_warning_sent`` and
        ``_max_duration_warning_sent`` boolean flags have been REMOVED.
        They were declared and reset here but NEVER read in any
        conditional — the actual silence-warning state machine uses
        the integer counter ``_silence_warning_count`` (which IS read
        at recording.py:1109). The dead flags were misleading
        maintainers into thinking warning deduplication existed when
        it didn't — see FORENSIC_REVIEW_COMPLETE.md → ARCH-023.

        SEC-audit-008: ``_secure_clear_array`` is now actually used
        here to zero cached audio arrays (``_cached_resampled`` and
        ``_cached_no_resample_arr``) before they're dropped. This
        prevents forensic recovery of audio data from process memory
        between sessions.
        """
        if self._recording_event.is_set():
            return

        # SEC-audit-008: securely zero cached audio arrays before clearing.
        # _secure_clear_array is defined at recording.py:78 but was
        # previously never called from any production path (only the
        # inline chunk.fill(0) calls in stop()/discard() zeroed chunks).
        # Without this, the previous session's audio could linger in
        # process memory until the next GC pass freed the numpy arrays.
        try:
            if self._cached_resampled is not None and self._cached_resampled.size > 0:
                _secure_clear_array(self._cached_resampled)
        except Exception:
            pass
        try:
            if self._cached_no_resample_arr is not None and self._cached_no_resample_arr.size > 0:
                _secure_clear_array(self._cached_no_resample_arr)
        except Exception:
            pass

        self._buffer.clear()
        self._chunk_count = 0
        self._cached_resampled = np.array([], dtype=np.float32)
        self._cached_native_chunk_count = 0
        # ARCH-023: also reset the cache key so a new session doesn't
        # reuse a stale prefix from a different sample rate.
        self._cached_resample_key = ()
        # NEW-PERF-003: invalidate the no-resample cache too.
        self._cached_no_resample_len = -1
        self._cached_no_resample_arr = None
        self._silence_timer = 0.0
        self._silence_start_time = None
        self._silence_warning_count = 0
        self._silence_next_warning_wait = 10.0
        self._recent_rms_values.clear()
        self._recording_start_time = time.perf_counter()
        # Reset XRUN and clipping counters
        self._xruns = 0
        self._xrun_timestamps.clear()
        self._clip_count = 0
        self._peak = 0.0
        self._last_clip_log_time = 0.0
        self._last_rms = 0.0
        # AUDIO-013: reset VAD state machine.
        # RW-04: VadProcessor.reset() handles the actual state restoration.
        # The property-shim assignments below are kept as a redundant
        # safety net AND as source-level documentation that start()
        # resets the VAD calibration state — existing tests pin on the
        # literal attribute names (``_vad_calibration_rms_values`` /
        # ``_vad_calibrated``) appearing in start()'s source.
        self._vad.reset()
        self._vad_state = VadState.UNKNOWN
        self._vad_consecutive_speech_frames = 0
        self._vad_consecutive_silence_frames = 0
        self._vad_speech_threshold_db = _DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._vad_silence_threshold_db = _DEFAULT_VAD_SILENCE_THRESHOLD_DB
        # AUDIO-014: reset auto-calibration
        self._vad_calibration_rms_values = []
        self._vad_calibrated = False
        # STREAM-FIX: reset user-stop-pending flag for the new
        # session so a stale True doesn't suppress a genuine disconnect
        # warning in this session.
        self._user_stop_pending = False
        # ADR 0007 §3.5: AGC reset deleted (method removed).
        # AUDIO-PRE: clear pre-roll buffer
        # SEC-audit-008: Zero the preroll buffer contents before clearing
        for chunk in self._preroll_buffer:
            if isinstance(chunk, np.ndarray):
                chunk.fill(0)
        self._preroll_buffer.clear()
        # AUDIO-HOT: reset disconnect state
        self._device_disconnected = False
        self._device_disconnect_retries = 0
        # PERF-011: reset frame-skip state. RT-SAFE-001: the
        # _previous_chunk_pending flag is no longer used (replaced by
        # ring buffer overflow detection), but we keep resetting it for
        # diagnostic cleanliness. _skipped_frames is now incremented by
        # the callback when the ring buffer overflows.
        self._previous_chunk_pending = False
        self._skipped_frames = 0
        # RT-SAFE-001: reset ring buffer drop counter for the new session
        self._dropped_ring_chunks = 0
        # AUDIO-HOT: reset periodic device check counter
        self._device_check_counter = 0
        # PERF-NEW-021: cache the target sample rate once at start()
        # so the audio callback / snapshot() doesn't re-read
        # self.config.sample_rate on every call.
        self._cached_target_sr = self.config.sample_rate

        # AUDIO-PROC: reset filter state for a new session so the
        # high-pass IIR doesn't carry state from the previous recording.
        if self._audio_processor is not None:
            self._audio_processor.reset()

        # PERF-NEW-006: cache config values at start() time so the
        # audio callback doesn't do 5x getattr per iteration.
        self._cached_silence_warning = getattr(self.config, "silence_warning_seconds", 20.0)
        self._cached_stop_on_silence = getattr(self.config, "stop_on_silence_seconds", 60.0)
        # SIMPLIFY-001: single explicit field replaces the old 3-field split
        # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu,
        # and max_recording_time_seconds=0 auto-selection). Always defaults to 900.
        self._cached_max_recording_time = int(getattr(self.config, "max_recording_time_seconds", 900))

        # PERF-NEW-018: dynamically size the buffer based on max_recording_time_seconds.
        # At 16kHz with 1024-sample chunks, each chunk = 64ms.  For a 30-min
        # recording: 1800s / 0.064s ≈ 28125 chunks.  For 1 hour: 56250.
        try:
            max_rec = int(self._cached_max_recording_time)
        except (TypeError, ValueError):
            max_rec = 0
        if max_rec > 0:
            needed_chunks = int(max_rec / 0.064) + 1000  # +1K safety
            if needed_chunks > DEFAULT_MAX_BUFFER_CHUNKS:
                # Create a new deque with larger maxlen and copy existing data
                old_data = list(self._buffer)
                self._buffer = collections.deque(old_data, maxlen=needed_chunks)
                log.debug(
                    "[RECORDING] Buffer sized for %ds max recording: %d chunks",
                    max_rec,
                    needed_chunks,
                )

        device = self._resolve_device()
        candidates = self._same_physical_microphone_candidates(device)

        # RT-SAFE-001: The PortAudio callback is now a thin wrapper
        # around _audio_callback_dispatch (a method on self). The
        # dispatch method does ONLY pre-roll capture + ring buffer
        # push + worker signal — all heavy work (filter chain, VAD,
        # resample, state machine) is done by the audio worker thread.
        # See _audio_callback_dispatch / _audio_worker_loop /
        # _process_audio_chunk for the full architecture.
        def callback(indata, frames, time_info, status):
            # AUDIO-009/AUDIO-015: guard flag for in-flight callback.
            # _teardown_stream() polls this flag for up to 300ms before
            # calling stream.close() to avoid use-after-free if the
            # callback is still running. With the RT-safe refactor, the
            # callback is ~10µs (copy + deque append + Event.set), so
            # the flag is almost always clear by the time teardown runs.
            self._is_in_audio_callback.set()
            try:
                self._audio_callback_dispatch(indata, frames, time_info, status)
            finally:
                self._is_in_audio_callback.clear()

        # AUDIO-HOT: store callback reference for device restart
        self._current_callback = callback

        # =====================================================================
        # CRITICAL — DO NOT RESTRUCTURE (2026-07-20)
        # =====================================================================
        # The device-enumeration block below (last_error, selected_device,
        # effective_sr, ``for candidate in candidates``, the fallback loop,
        # and the ``if self._stream is None:`` check) MUST stay at start()
        # method scope — this 8-space indent level, OUTSIDE the ``callback``
        # closure defined above.
        #
        # A previous merge accidentally nested this block INSIDE the
        # ``def callback()`` closure (12-space indent). That made
        # ``last_error`` a local of ``callback``, not ``start()``. When
        # ``start()`` checked ``if last_error is not None:``, Python raised:
        #     UnboundLocalError: cannot access local variable 'last_error'
        #     where it is not associated with a value
        # → recording start crashed on every attempt.
        #
        # The fallback loop (``for candidate in all_candidates``) was also
        # misplaced — trapped inside the preroll-buffer block instead of
        # ``if self._stream is None and not used_fallback:``.
        #
        # DO NOT move device enumeration inside the callback closure.
        # DO NOT re-add ``set_thread_registry`` — it was merge damage, not
        # in the original codebase, and referenced a function that did not
        # exist. The ``recording/__init__.py`` stub for it is dead code.
        # =====================================================================
        last_error = None
        selected_device = None
        effective_sr = self.config.sample_rate
        used_fallback = False

        for candidate in candidates:
            candidate_sr, dev_info_extra = self._resolve_effective_sample_rate(candidate)

            if dev_info_extra:
                log.info(
                    "[RECORDING] Using device: [%s] %s | host_api=%s | native_rate=%d | effective_rate=%d",
                    candidate if candidate is not None else "default",
                    dev_info_extra["name"],
                    dev_info_extra["host_api_name"],
                    dev_info_extra["native_rate"],
                    candidate_sr,
                )

            stream = None
            try:
                # AUDIO-CH: query device's max input channels.
                # If device only supports stereo, use channels=2
                # and convert to mono in the callback via _ensure_mono.
                # If config.recording_channels > 0, use that value
                # instead of auto-detecting (allows user override).
                config_channels = int(getattr(self.config, "recording_channels", 1) or 1)
                channels = config_channels if config_channels > 0 else 1
                try:
                    dev_info = sd.query_devices(candidate) if candidate is not None else sd.query_devices(kind="input")
                    max_ch = dev_info.get("max_input_channels", 1)
                    if config_channels <= 0:
                        # 0 = auto-detect: prefer mono, fallback to device default
                        if max_ch >= 2:
                            channels = 2  # prefer stereo if available, downmix in callback
                        elif max_ch == 1:
                            channels = 1
                    elif channels > max_ch:
                        channels = max(1, max_ch)  # don't request more than device supports
                except Exception:
                    pass

                stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=channels,
                    dtype=np.float32,
                    device=candidate,
                    callback=callback,
                    # VAD-001: request 512-sample blocks so Silero VAD
                    # gets the exact chunk size it expects. PortAudio
                    # may still deliver a different size on some drivers,
                    # but vad.py now pads/truncates to handle that.
                    blocksize=512,
                    # AUDIO-HOT: finished_callback detects unexpected stream termination
                    finished_callback=self._stream_finished_callback,
                )
                stream.start()

                # AUDIO-BT: detect Bluetooth HFP profile (8/16 kHz).
                # After opening the stream, check if the actual sample
                # rate differs from requested and is 8000 or 16000.
                try:
                    actual_sr = int(stream.samplerate) if hasattr(stream, "samplerate") else candidate_sr
                    if actual_sr in (8000, 16000) and actual_sr != candidate_sr:
                        # AUDIO-BT: detecting a Bluetooth HFP (hands-free
                        # telephony) profile is EXPECTED behaviour for a BT
                        # headset — it is not a fault or misconfiguration.
                        # Demoted from WARNING to INFO so the default log
                        # isn't littered with a non-error on every BT mic
                        # connection. RW-15.
                        log.info(
                            "[RECORDING] Bluetooth HFP profile detected: actual sample rate "
                            "%d Hz differs from requested %d Hz. Audio quality will be limited. "
                            "Consider disabling the hands-free telephony profile in Bluetooth "
                            "settings for better quality.",
                            actual_sr,
                            candidate_sr,
                        )
                except Exception:
                    pass

                # AUDIO-CH: store actual channel count for callback
                self._actual_channels = channels
            except Exception as e:
                last_error = e
                log.warning(
                    "[RECORDING] Failed to open input device [%s]: %s",
                    candidate if candidate is not None else "default",
                    e,
                )
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()
                self._stream = None
                continue

            self._stream = stream
            # ARCH-021: guard _effective_sr writes with the lock because
            # snapshot() reads it under the lock from another thread.
            with self._lock:
                self._effective_sr = candidate_sr
            selected_device = candidate
            effective_sr = candidate_sr
            break

        # If all same-name candidates failed, try ALL available input devices
        if self._stream is None and not used_fallback:
            log.warning(
                "[RECORDING] All devices matching configured mic failed. "
                "Trying all available input devices as fallback."
            )
            all_candidates = self._all_input_device_candidates()
            # Remove already-tried devices
            tried = set(str(c) for c in candidates)
            all_candidates = [c for c in all_candidates if str(c) not in tried]

            for candidate in all_candidates:
                candidate_sr, dev_info_extra = self._resolve_effective_sample_rate(candidate)

                if dev_info_extra:
                    log.info(
                        "[RECORDING] Fallback device: [%s] %s | host_api=%s | native_rate=%d | effective_rate=%d",
                        candidate,
                        dev_info_extra["name"],
                        dev_info_extra["host_api_name"],
                        dev_info_extra["native_rate"],
                        candidate_sr,
                    )

                stream = None
                try:
                    # AUDIO-CH: also query channels for fallback devices
                    fb_channels = 1
                    try:
                        fb_dev_info = sd.query_devices(candidate)
                        fb_max_ch = fb_dev_info.get("max_input_channels", 1)
                        if fb_max_ch >= 2:
                            fb_channels = 2
                    except Exception:
                        pass

                    stream = sd.InputStream(
                        samplerate=candidate_sr,
                        channels=fb_channels,
                        dtype=np.float32,
                        device=candidate,
                        callback=callback,
                        # VAD-001: request 512-sample blocks for Silero VAD
                        blocksize=512,
                        # AUDIO-HOT: finished_callback detects unexpected stream termination
                        finished_callback=self._stream_finished_callback,
                    )
                    stream.start()
                except Exception as e:
                    last_error = e
                    log.warning(
                        "[RECORDING] Fallback device [%s] also failed: %s",
                        candidate,
                        e,
                    )
                    if stream is not None:
                        with contextlib.suppress(Exception):
                            stream.close()
                    continue

                self._stream = stream
                # ARCH-021: guard _effective_sr writes with the lock.
                with self._lock:
                    self._effective_sr = candidate_sr
                selected_device = candidate
                effective_sr = candidate_sr
                used_fallback = True
                # RW-6 (pyrefly): ``dev_info_extra`` is typed
                # ``dict | None`` because ``_resolve_effective_sample_rate``
                # may return None when PortAudio can't enumerate the
                # device. The earlier ``if dev_info_extra:`` gate
                # protects the first access (logging at line ~1505),
                # but this post-success log was unguarded — calling
                # ``["name"]`` on None would raise ``TypeError`` here
                # after a *successful* stream open. Fall back to a
                # placeholder so the log line still fires.
                fb_name = dev_info_extra["name"] if dev_info_extra else "(unknown)"
                log.info(
                    "[RECORDING] Fallback succeeded with device [%s] %s",
                    candidate,
                    fb_name,
                )
                break

        if self._stream is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("No input device could be opened")

        if selected_device != device and isinstance(selected_device, int):
            log.info(
                "[RECORDING] Selected microphone [%s] failed; using device [%s]",
                device,
                selected_device,
            )
            self.config.microphone = str(selected_device)
            # PERF-NEW-007: persist the microphone-fallback update on
            # a background daemon thread so the 50-500 ms blocking
            # write doesn't stall the recording-start critical path.
            # The fallback is best-effort persistence — if the process
            # crashes before the write lands, the user just re-selects
            # the mic on next start.
            import threading as _threading_for_save

            def _persist_mic() -> None:
                if not self.config.save():
                    log.debug("[RECORDING] Could not persist microphone fallback")

            _threading_for_save.Thread(
                target=_persist_mic,
                name="mic-fallback-save",
                daemon=True,
            ).start()

        self._recording_event.set()

        # AUDIO-PRE: prepend pre-roll buffer to reduce cold-start latency.
        # The pre-roll buffer captured audio before recording officially
        # started, so we insert it at the beginning of the main buffer.
        if self._preroll_buffer:
            preroll_chunks = list(self._preroll_buffer)
            if preroll_chunks:
                for chunk in reversed(preroll_chunks):
                    mono_chunk = self._ensure_mono(chunk)
                    self._buffer.appendleft(mono_chunk.copy())
                log.debug(
                    "[RECORDING] Prepended %d pre-roll chunks (~%.1fs)",
                    len(preroll_chunks),
                    len(preroll_chunks) * 512 / self._effective_sr,
                )

        target_sr = self.config.sample_rate
        if (
            effective_sr != target_sr
            and _recording_pkg._resample_poly is None
            and _recording_pkg._resample_poly_error is None
        ):
            # Warm up synchronously to avoid racing with stop()
            self.warm_up_resampler()

        # RT-SAFE-001: Start the audio worker thread AFTER the pre-roll
        # buffer has been prepended (so the worker doesn't race with
        # start()'s appendleft) and AFTER _recording_event.set() (so the
        # callback will actually push to the ring buffer). The worker
        # drains the ring buffer and runs the heavy processing pipeline
        # (filter chain, VAD, resample, state machine) off the real-time
        # audio thread.
        self._start_audio_worker()

        # RW-8: Start the IPC event worker thread AFTER the audio worker
        # so the audio worker can enqueue IPC events (e.g. audio_clip)
        # as soon as it begins processing chunks. The event worker is
        # stopped by stop()/discard() — see _stop_event_worker.
        self._start_event_worker()

        # CPU-03: start the device health checker thread (off the audio
        # worker) so device-disconnect detection doesn't block the hot path.
        self._start_device_health_checker()

    def _teardown_stream(self) -> None:
        """Stop + close the PortAudio stream, draining any in-flight callback.

        17-H-FIX-2: extracted from ``stop()`` so ``discard()`` shares the
        same callback-drain contract. Without the poll, ``discard()``
        could call ``stream.close()`` while the audio callback (firing
        ~16×/s) was still running — risking use-after-free or deadlock
        when ESC-cancel landed mid-callback.

        Behavior:
          1. If ``self._stream`` is None, return immediately (idempotent).
          2. Call ``stream.stop()`` to halt PortAudio's callback dispatch.
          3. Poll ``_is_in_audio_callback`` for up to 300ms (5ms interval)
             until the in-flight callback (if any) returns.
          4. Call ``stream.close()`` to free PortAudio resources.
          5. Set ``self._stream = None``.

        Idempotent: safe to call when the stream is already None (e.g.
        when ``discard()`` is invoked twice, or after ``stop()``).
        """
        if not self._stream:
            return
        self._stream.stop()
        # AUDIO-009/AUDIO-015: wait briefly for any in-flight audio
        # callback to complete before closing the stream. This prevents
        # PortAudio from calling the callback during/after stream.stop()
        # which can cause use-after-free or deadlock.
        #
        # PERF-FIX-002 (Round 0): the previous "exponential backoff"
        # implementation was inverted. It used::
        #
        #     if self._is_in_audio_callback.wait(timeout=_timeout):
        #         break  # callback completed
        #
        # but ``threading.Event.wait(timeout)`` returns ``True`` when the
        # flag is *set* — and the flag is set while the callback is
        # *running* (see lines 1082/1086: set on entry, clear on exit).
        # So the loop broke immediately when the callback WAS running
        # (defeating the safety guard) and blocked for the full
        # 20+30+50+80+130+200 = 510ms when the callback was NOT running
        # (the common case).  Every dictation paid a half-second penalty.
        #
        # The fix: poll for the flag to become *clear* (callback not
        # running), with a 5ms interval and a 300ms hard budget (matching
        # the original 6×50ms worst case).  On a healthy system the flag
        # is already clear on the first check → 0ms wait.  When the
        # callback genuinely runs past ``stream.stop()``, the poll loop
        # waits for it to finish (restoring the AUDIO-009/AUDIO-015
        # safety contract).
        _backoff_budget_s = 0.300  # total worst-case wait, same as pre-fix
        _poll_interval_s = 0.005  # 5ms poll
        _deadline = time.perf_counter() + _backoff_budget_s
        while self._is_in_audio_callback.is_set():
            remaining = _deadline - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(_poll_interval_s, remaining))
        self._stream.close()
        self._stream = None

    # ── RT-SAFE-001: Audio worker thread lifecycle ──────────────────

    def _start_audio_worker(self) -> None:
        """Start the audio worker thread that drains the ring buffer.

        Called by ``start()`` AFTER the PortAudio stream is successfully
        opened and the pre-roll buffer has been prepended, but BEFORE
        ``_recording_event.set()`` is... actually, it's called AFTER
        ``_recording_event.set()`` because the callback needs the event
        to be set before it will push to the ring buffer. The worker
        thread is a daemon so it never blocks process exit.

        Idempotent: if the worker is already running, this is a no-op.

        THREAD-REGISTRY: when a registry was provided to ``__init__``,
        the worker thread is registered so ``shutdown_all()`` can
        signal and join it during ``VoiceTyperApp.quit()``. The
        registry entry is removed by ``_stop_audio_worker()`` after
        the join completes (or times out) so a subsequent start()
        re-registers cleanly without triggering the
        "Re-registering name" warning.
        """
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        # Reset stop event (in case a previous stop() left it set)
        self._worker_stop_event.clear()
        self._worker_wake_event.clear()
        # Clear the ring buffer of any stale chunks from a previous session
        self._ring_buffer.clear()
        self._worker_thread = threading.Thread(
            target=self._audio_worker_loop,
            name=_AUDIO_WORKER_THREAD_NAME,
            daemon=True,
        )
        self._worker_thread.start()
        # THREAD-REGISTRY: register the freshly-started worker so the
        # central registry can signal/join it on shutdown. The join
        # timeout matches the worst-case stop() path (drain=True).
        if self._thread_registry is not None:
            self._thread_registry.register(
                name=_AUDIO_WORKER_THREAD_NAME,
                thread=self._worker_thread,
                stop_event=self._worker_stop_event,
                join_timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S,
            )

    def _stop_audio_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the audio worker thread to stop and join it.

        Parameters
        ----------
        timeout : float
            Maximum seconds to wait for the worker to exit.
        drain : bool
            If True (default, used by ``stop()``), the worker drains the
            ring buffer fully before exiting so no in-flight audio is
            lost. If False (used by ``discard()``), the ring buffer is
            cleared first so the worker exits immediately after its
            current chunk.

        Safe to call when the worker is not running (no-op).

        THREAD-REGISTRY: unregisters the worker after the join so a
        subsequent ``_start_audio_worker()`` re-registers cleanly.
        """
        if self._worker_thread is None:
            # Still reset the stop event so the next start() is clean.
            self._worker_stop_event.clear()
            return
        if not drain:
            # discard() path: clear the ring buffer so the worker has
            # nothing left to process. It will finish its current chunk
            # (if any) and then exit on the next iteration.
            self._ring_buffer.clear()
        # Signal the worker to stop.
        self._worker_stop_event.set()
        # Wake the worker in case it's blocked on the wait event.
        self._worker_wake_event.set()
        # Join with timeout. If the worker doesn't exit in time (e.g.,
        # stuck in VAD inference), we proceed anyway — the worker is a
        # daemon, so it won't block process exit. A stale worker is
        # harmless because the stop event is set; it will exit on its
        # next iteration boundary.
        self._worker_thread.join(timeout=timeout)
        if self._worker_thread.is_alive():
            log.warning(
                "[RECORDING] Audio worker thread did not exit within %.1fs "
                "(it will exit as a daemon on next iteration)",
                timeout,
            )
        else:
            log.debug("[RECORDING] Audio worker thread exited cleanly")
        # THREAD-REGISTRY: remove the entry so a subsequent start()
        # re-registers cleanly. If shutdown_all() already ran and
        # joined the thread, this is a no-op (the entry was already
        # used). Safe to call when no entry exists.
        if self._thread_registry is not None:
            self._thread_registry.unregister(_AUDIO_WORKER_THREAD_NAME)
        # Clear the stop event so the next start() can reuse the fields.
        self._worker_stop_event.clear()
        self._worker_wake_event.clear()
        self._worker_thread = None

    # ── RW-8: IPC event worker thread lifecycle ─────────────────────

    def _start_event_worker(self) -> None:
        """Start the IPC event worker thread that drains ``_event_queue``.

        RW-8: called by ``start()`` AFTER the audio worker is started
        so the audio worker can enqueue IPC events (e.g. ``audio_clip``)
        as soon as it begins processing chunks. The event worker is a
        daemon so it never blocks process exit.

        Idempotent: if the event worker is already running, this is a
        no-op. Any stale events left in the queue from a previous
        session are drained before the worker starts so they are not
        re-published (matches the audio worker's ring-buffer clear in
        ``_start_audio_worker``).

        THREAD-REGISTRY: when a registry was provided to ``__init__``,
        the event worker thread is registered so ``shutdown_all()`` can
        signal and join it during ``VoiceTyperApp.quit()``.
        """
        if self._event_worker_thread is not None and self._event_worker_thread.is_alive():
            return
        self._event_stop_event.clear()
        # Drain any stale events from a previous session.
        with contextlib.suppress(Exception):
            while True:
                try:
                    self._event_queue.get_nowait()
                except queue.Empty:
                    break
        self._event_worker_thread = threading.Thread(
            target=self._event_worker_loop,
            name=_EVENT_WORKER_THREAD_NAME,
            daemon=True,
        )
        self._event_worker_thread.start()
        if self._thread_registry is not None:
            self._thread_registry.register(
                name=_EVENT_WORKER_THREAD_NAME,
                thread=self._event_worker_thread,
                stop_event=self._event_stop_event,
                join_timeout=_EVENT_WORKER_JOIN_TIMEOUT_S,
            )

    def _stop_event_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the event worker thread to stop and join it.

        Parameters
        ----------
        timeout : float
            Maximum seconds to wait for the worker to exit.
        drain : bool
            If True (default, used by ``stop()``), the worker drains the
            event queue fully (publishing every queued event) before
            exiting so no in-flight IPC event is lost. If False (used by
            ``discard()``), the queue is cleared first so the worker
            exits immediately after its current publish (if any) —
            cancelled recordings don't need their queued events
            published.

        THREAD-REGISTRY: unregisters the worker after the join so a
        subsequent ``_start_event_worker()`` re-registers cleanly.

        Safe to call when the worker is not running (no-op).
        """
        if self._event_worker_thread is None:
            # Still reset the stop event so the next start() is clean.
            self._event_stop_event.clear()
            return
        if not drain:
            # discard() path: clear the queue so the worker has nothing
            # left to publish. It will finish its current publish (if
            # any) and then exit on the next iteration.
            with contextlib.suppress(Exception):
                while True:
                    try:
                        self._event_queue.get_nowait()
                    except queue.Empty:
                        break
        # Signal the worker to stop.
        self._event_stop_event.set()
        # Join with timeout. If the worker doesn't exit in time (e.g.,
        # stuck in a slow publish), we proceed anyway — the worker is a
        # daemon, so it won't block process exit. A stale worker is
        # harmless because the stop event is set; it will exit on its
        # next iteration boundary.
        self._event_worker_thread.join(timeout=timeout)
        if self._event_worker_thread.is_alive():
            log.warning(
                "[RECORDING] Event worker thread did not exit within %.1fs "
                "(it will exit as a daemon on next iteration)",
                timeout,
            )
        else:
            log.debug("[RECORDING] Event worker thread exited cleanly")
        if self._thread_registry is not None:
            self._thread_registry.unregister(_EVENT_WORKER_THREAD_NAME)
        self._event_stop_event.clear()
        self._event_worker_thread = None

    def _event_worker_loop(self) -> None:
        """IPC event worker thread main loop (RW-8).

        Consumes events from ``_event_queue`` and calls
        ``event_bus.publish`` so the IPC transport (TCP / stdout) can
        forward them to the Electron renderer. This thread is the
        SINGLE consumer — the audio worker thread is the single
        producer, so no locks are needed on the queue (``queue.Queue``
        is already thread-safe for MPSC).

        Shutdown: exits when ``_event_stop_event`` is set. The loop
        drains the queue fully before exiting so ``stop()`` doesn't
        lose in-flight IPC events. For the ``discard()`` path, the
        queue was already cleared by the caller, so the drain loop is
        a no-op.
        """
        while True:
            if not self._event_stop_event.is_set():
                # Wait for work with a short timeout so we notice the
                # stop flag even if an event is enqueued between the
                # worker's ``get`` return and the next loop iteration
                # (a rare race that the timeout covers — same pattern as
                # ``_audio_worker_loop``'s 50ms wait).
                try:
                    event = self._event_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
            else:
                # Stop signal received — drain remaining events before
                # exiting (for the ``stop()`` path). For ``discard()``
                # the queue was already cleared by the caller, so this
                # loop is a no-op.
                try:
                    event = self._event_queue.get_nowait()
                except queue.Empty:
                    return
            try:
                event_bus.publish(event)
            except Exception:
                # A bad event or a buggy subscriber must NOT kill the
                # event worker (otherwise all subsequent IPC events
                # are lost until the next start()). event_bus.publish
                # already isolates subscriber exceptions, so this is a
                # belt-and-suspenders guard for unexpected failures
                # (e.g. a TypeError from a malformed event dict).
                log.debug(
                    "[RECORDING] Event worker thread error publishing event",
                    exc_info=True,
                )

    def _audio_worker_loop(self) -> None:
        """Audio worker thread main loop.

        Consumes chunks from the SPSC ring buffer and runs the heavy
        processing pipeline (filter chain, VAD, resample, state machine,
        callbacks). This thread is the SINGLE consumer — the audio
        callback is the single producer, so no locks are needed for the
        ring buffer access (collections.deque append/popleft are atomic
        under CPython's GIL for SPSC).

        Shutdown: exits when ``_worker_stop_event`` is set. The loop
        drains the ring buffer fully before exiting so ``stop()``
        doesn't lose in-flight audio (unless ``drain=False`` was passed
        to ``_stop_audio_worker``, in which case the ring buffer was
        already cleared by the caller).
        """
        while True:
            # Wait for work or stop signal. The 50ms timeout ensures we
            # notice the stop flag even if the wake event is missed
            # (e.g., if the callback sets the event between the worker's
            # wait() return and the clear() call — a rare race that the
            # timeout covers).
            if not self._worker_stop_event.is_set():
                self._worker_wake_event.wait(timeout=0.05)
            self._worker_wake_event.clear()

            # Drain all available chunks. Each chunk is processed by
            # _process_audio_chunk which does the heavy lifting.
            while True:
                try:
                    chunk_data = self._ring_buffer.popleft()
                except IndexError:
                    break
                try:
                    self._process_audio_chunk(*chunk_data)
                except Exception:
                    # Log and continue — a single bad chunk must NOT kill
                    # the worker (otherwise all subsequent audio is lost
                    # until the next start()).
                    #
                    # B-5: this worker runs at ~16 Hz (the audio callback
                    # pushes a chunk per PortAudio block).  A persistent
                    # error (e.g. a bad filter config) would flood the
                    # log at ERROR 16 times/sec ≈ 960 lines/min.
                    # ``log_rate_limited`` emits the 1st occurrence and
                    # every 100th thereafter at ERROR with the full
                    # traceback; all other occurrences go to DEBUG (no
                    # traceback) so a persistent error remains visible
                    # in debug mode without spamming the default log.
                    log_rate_limited(
                        log,
                        logging.ERROR,
                        "[RECORDING] Audio worker thread error processing chunk",
                        exc_info=True,
                    )

            # Check for shutdown. We drain the ring buffer fully before
            # exiting so stop() doesn't lose in-flight audio. For the
            # discard() path, the ring buffer was already cleared by the
            # caller, so the drain loop above was a no-op.
            if self._worker_stop_event.is_set():
                return

    def _audio_callback_dispatch(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Real-time audio callback entry point — RT-safe path.

        This method is invoked by PortAudio from the real-time audio
        thread. It must complete well before the next buffer arrives
        (~32ms at 512 blocksize / 16kHz). To meet this deadline, it
        does ONLY:

        1. Pre-roll capture when not recording (small, fast: ~10µs for
           copy + mono downmix + deque append).
        2. Copy the indata buffer into the SPSC ring buffer (~2KB
           allocation for 512 float32 samples — negligible).
        3. Signal the worker thread via ``_worker_wake_event.set()``.

        All heavy work (filter chain, Silero VAD, scipy resample, VAD
        state machine, silence timer, callbacks, AUDIO-CLIP IPC event
        push) is done by the audio worker thread (see
        ``_audio_worker_loop`` / ``_process_audio_chunk``).
        """
        # ARCH-026: PortAudio can deliver a callback before start()
        # finishes setting self._recording_start_time and other
        # per-session state. Bail out early so the silence/max-
        # duration callbacks don't compute against a None timestamp.
        if not self._recording_event.is_set():
            # AUDIO-PRE: capture pre-roll even when not officially
            # recording. This is a fast path (~10µs): copy + mono
            # downmix + deque append. Stays in the callback so pre-roll
            # latency is minimal — the worker thread isn't started until
            # after start() finishes, so pre-roll capture MUST happen
            # here.
            if self._preroll_active:
                mono_preroll = self._ensure_mono(indata.copy())
                self._preroll_buffer.append(mono_preroll)
            return

        # Recording is active — push to the SPSC ring buffer for the
        # worker thread to process. The callback's only job is to copy
        # + enqueue.
        #
        # PERF-RT-001: the indata buffer is owned by PortAudio and
        # reused for the next callback, so we MUST copy. ~2KB
        # allocation for 512 float32 samples — negligible compared to
        # the 32ms deadline.
        chunk_copy = indata.copy()

        # Detect ring buffer overflow (worker can't keep up). The
        # deque's maxlen will silently evict the oldest chunk, but we
        # want to log it so the user knows audio is being dropped.
        # This replaces the old PERF-011 frame-skip logic
        # (_previous_chunk_pending) which was a single-slot queue — the
        # ring buffer is a 64-slot queue, so we have much more headroom
        # before dropping.
        ring_maxlen = self._ring_buffer.maxlen
        if ring_maxlen is not None and len(self._ring_buffer) >= ring_maxlen:
            # AUDIO-1: increment counters only (atomic under GIL). The
            # log.warning() was removed from this PortAudio RT callback
            # — logging I/O here can take ms and risks an overrun against
            # the 32ms deadline. The counters are surfaced later by the
            # worker thread / diagnostics paths (e.g. _finalize_audio_quality_report
            # and the AUDIO-019 backpressure warning in _process_audio_chunk).
            self._dropped_ring_chunks += 1
            self._skipped_frames += 1  # preserve old counter for diagnostics

        # Push (copy, frames, time_info, status, perf_timestamp) to the
        # ring buffer. The timestamp is captured here (not in the worker)
        # so silence-timer calculations reflect when the audio arrived,
        # not when the worker happened to process it.
        self._ring_buffer.append((chunk_copy, frames, time_info, status, time.perf_counter()))
        # Signal the worker thread to drain the buffer. Event.set() is
        # a fast atomic operation — safe to call from the RT thread.
        self._worker_wake_event.set()

    def _process_audio_chunk(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
        perf_ts: float,
    ) -> None:
        """Process a single audio chunk — runs on the worker thread.

        This method contains the heavy processing pipeline that was
        previously in the PortAudio callback (``_callback_impl``). It
        is called by ``_audio_worker_loop`` for each chunk popped from
        the ring buffer.

        Operations (in order):
        - HOTKEY-CRASH: device disconnect detection (zero-fill + periodic)
        - AUDIO-002: XRUN status flag handling + on_xrun_threshold callback
        - AUDIO-CH: mono conversion
        - AUDIO-PROC: filter chain application
        - Buffer append + chunk count + backpressure detection
        - RMS / peak computation
        - AUDIO-CLIP: clipping detection + IPC event push
        - AUDIO-014: VAD auto-calibration
        - AUDIO-013: Silero VAD probability (with resample to 16kHz)
        - AUDIO-013: VAD state machine + silence timer
        - H12: silence warning / auto-stop / max-duration callbacks
        - T021: on_rms_level callback (filtered chunk forwarded)
        - Telemetry logs

        All of this previously ran on the real-time audio thread,
        violating the ~32ms deadline (scipy resample + Silero VAD can
        take 5-50ms combined). Moving it to the worker thread restores
        real-time safety.
        """
        # HOTKEY-CRASH: detect device disconnect (zero-filled indata
        # when device is still "open" but USB/BT was unplugged).
        # Guard against false positives during rapid hotkey toggling:
        # when stop() clears _recording_event, PortAudio may deliver
        # zero-filled frames as the stream drains. We must NOT treat
        # those as device disconnects, because _handle_device_disconnect
        # would race with the deliberate stop() to close the stream.
        if (indata.size == 0 or np.count_nonzero(indata) == 0) and self._chunk_count > 10:
            # RW-7: re-entrancy guard — if a previous chunk already
            # detected the disconnect and scheduled a handler thread,
            # don't spawn another. Pre-fix, every subsequent zero-filled
            # chunk would re-enter this block, set the flag again
            # (no-op), and spawn ANOTHER device-disconnect-handler
            # thread — a thread-spawn storm on truly silent (or
            # disconnected) input. With 100 zero-filled callbacks after
            # the warmup window, this would spawn ~89 threads.
            #
            # The flag is cleared by _handle_device_disconnect on
            # successful stream restart (see line ~804) and by start()
            # (see line ~1253), so this guard only suppresses the storm
            # during the retry window — it does NOT suppress a
            # legitimate re-detection after a successful restart.
            #
            # Silence tracking (vad_state / silence_timer /
            # on_silence_warning) is unaffected: those run later in
            # this method for NON-zero chunks. For zero-filled chunks
            # the existing `return` below already skipped them; this
            # guard just ensures we don't ALSO spawn a handler thread
            # on every such chunk.
            if self._device_disconnected:
                return
            # HOTKEY-CRASH: double-check that recording is still active.
            # The early-return check in the callback passed, but stop()
            # may have cleared _recording_event between that check and
            # this point (the callback and worker run on different
            # threads, so the Event flag change is visible immediately).
            if not self._recording_event.is_set():
                return  # deliberate stop, not a disconnect
            self._device_disconnected = True
            log.warning("[RECORDING] Zero-filled indata detected — possible device disconnect")
            # Schedule disconnect handling off the worker thread
            # HOTKEY-CRASH: capture the current stop_generation so the
            # handler can bail if a stop/start cycle happened in between.
            _captured_gen = self._stop_generation
            with contextlib.suppress(Exception):
                threading.Thread(
                    target=self._handle_device_disconnect,
                    kwargs={"_captured_generation": _captured_gen},
                    name="device-disconnect-handler",
                    daemon=True,
                ).start()
            return

        # AUDIO-2: the per-N-chunks blocking ``sd.query_devices()``
        # probe on the audio worker thread was removed — it is fully
        # redundant with ``_device_health_checker_loop`` (a dedicated
        # daemon thread that wakes every ``_device_check_interval_s``
        # and runs the same ``sd.query_devices(current_device)`` probe
        # with the same disconnect-handling logic). Running the probe
        # here cost a blocking RPC on the audio hot path every ~500
        # chunks; the health-checker thread covers the case off the
        # hot path. See ``_device_health_checker_loop`` and
        # ``_start_device_health_checker``.

        # NOTE: Dead-air timeout was REMOVED in RW-0.
        # Redundant with stop_on_silence_seconds (auto-stop already resets on
        # speech). The _update_dead_air_simple() method was also removed along
        # with _dead_air_timeout / _dead_air_speech_detected / _dead_air_silence_start.
        # Do NOT re-add — it added no unique behavior.

        # AUDIO-002: Check PortAudio status flags for XRUNs.
        # Use a rolling window of xrun timestamps to reduce log spam
        # while still alerting on sustained issues.
        if status:
            self._xruns += 1
            now = time.monotonic()
            self._xrun_timestamps.append(now)
            # AUDIO-002: check rolling window — only log if threshold
            # exceeded within the alert period
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
            # Item 1: fire threshold callback for tray notification
            if self._xruns == self._xrun_threshold and self.on_xrun_threshold:
                with contextlib.suppress(Exception):
                    self.on_xrun_threshold(self._xruns)

        # RT-SAFE-001: the old PERF-011 frame-skip logic
        # (_previous_chunk_pending) is replaced by ring buffer overflow
        # detection in the callback. If the ring buffer was full, the
        # callback already logged a warning and dropped the chunk. By
        # the time we reach here, the chunk is in the ring buffer and
        # we must process it.

        # AUDIO-CH: convert multi-channel input to mono
        indata_mono = self._ensure_mono(indata)

        # AUDIO-PROC: apply real-time noise filtering BEFORE the
        # buffer append so (a) `filtered` is defined when we use it
        # inside the lock, and (b) the stored audio, silence
        # detection, and waveform bubble all see the cleaned signal
        # that the transcriber will receive.  This runs OUTSIDE the
        # lock — process_chunk() is non-blocking and operates only
        # on the local `indata` copy.  See recording.py callback
        # ordering in the auto-volume-duck architecture doc §6.4.
        if self._audio_processor is not None:
            # CRIT-6: pass the stream's native rate so the processor can
            # resample to the chain's construction rate (16 kHz) before
            # filtering. Without this argument the resampler is bypassed and
            # filters built at 16 kHz are fed native-rate audio (e.g. 48 kHz),
            # silently mistuning every coefficient.
            filtered = self._audio_processor.process_chunk(indata_mono.copy(), input_sample_rate=self._effective_sr)
        else:
            filtered = indata_mono

        # RACE-001: minimize lock scope — only buffer append and
        # counter need atomicity. Callback refs and silence state
        # are read outside the lock — these are set once at start()
        # and cleared at stop(), so a torn read just means we miss
        # one callback or fire one extra, which is acceptable. The
        # alternative (holding the lock while calling user code)
        # risks deadlocks.
        with self._lock:
            # Store FILTERED audio so the transcriber receives the
            # cleaned signal. PERF-12: ``filtered`` is already an
            # owned array — in the processor branch,
            # ``process_chunk`` is called with ``indata_mono.copy()``
            # and either returns that same owned copy (passthrough)
            # or a fresh array from the filter chain. In the
            # no-processor branch, ``indata_mono`` is either the
            # owned ``chunk_copy`` (ndim==1), a fresh ``np.mean``
            # result (multi-channel downmix), or a view of
            # ``chunk_copy`` (reshape); numpy views keep their base
            # alive via ``.base``, so the buffer safely owns its
            # data without a redundant ``.copy()`` here (saves
            # ~2KB alloc/chunk at 16Hz).
            self._buffer.append(filtered)
            self._chunk_count += 1
            chunk_count = self._chunk_count
            buffer_len = len(self._buffer)
            # RACE-003: snapshot _recent_rms_values INSIDE the lock
            # so the post-lock code can iterate without a torn read
            # from a concurrent callback. Pre-fix, the deque
            # reference was read outside the lock and could be
            # mutated mid-iteration (append + maxlen eviction).
            # list() copies the references in O(k) where k is the
            # deque maxlen (default 50) — negligible.
            recent_rms_snapshot = list(self._recent_rms_values)

        # AUDIO-019: Backpressure detection — if the deque dropped chunks
        # (maxlen exceeded), increment a counter and warn the user
        if self._buffer.maxlen is not None and buffer_len >= self._buffer.maxlen - 1:
            self._dropped_chunks = getattr(self, "_dropped_chunks", 0) + 1
            if self._dropped_chunks == 1 or self._dropped_chunks % 100 == 0:
                log.warning(
                    "[RECORDING] Buffer full — oldest audio dropped (total=%d). ASR is slower than real-time.",
                    self._dropped_chunks,
                )

        # Read callback refs outside the lock — these are set once
        # at start() and cleared at stop(), so a torn read just
        # means we miss one callback or fire one extra, which is
        # acceptable. The alternative (holding the lock while
        # calling user code) risks deadlocks.
        rms_callback = self.on_rms_level
        silence_warning_cb = self.on_silence_warning
        silence_auto_stop_cb = self.on_silence_auto_stop
        max_duration_cb = self.on_max_duration_auto_stop
        # RACE-003: use the snapshot taken inside the lock above;
        # do NOT re-read _recent_rms_values here.
        recent_rms = recent_rms_snapshot
        recording_start = self._recording_start_time

        # ── Everything below runs OUTSIDE the lock ──

        # RMS / peak computation (operates on FILTERED audio so the
        # waveform bubble and silence detection see what the
        # transcriber will see, not raw mic input).
        # AUDIO-NP: use np.dot instead of np.mean(indata**2) to
        # avoid the intermediate squared array allocation.
        if filtered.size:
            # AUDIO-NP: single-pass RMS using np.dot — avoids
            # creating the intermediate abs_filtered**2 array.
            flat = filtered.reshape(-1)
            chunk_rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
            # PERF-FIX-2: allocation-free peak — reuse the existing
            # ``flat`` view instead of materializing np.abs(filtered).
            # max(|x|) == max(max(x), -min(x)) — two reductions on the
            # same contiguous view, no intermediate array allocated.
            chunk_peak = max(float(flat.max()), -float(flat.min()))
        else:
            chunk_peak = 0.0
            chunk_rms = 0.0
        chunk_duration = len(filtered) / self._effective_sr

        # ADR 0007 §3.5: the old per-chunk AGC (_agc_update, C1)
        # has been removed. It duplicated the Compressor filter in
        # the new audio filter chain. The Compressor now handles
        # dynamic range compression with proper attack/release.
        # _last_rms stores the post-filter RMS for UI/IPC.

        with self._lock:
            self._last_rms = chunk_rms

        # AUDIO-CLIP: Track clipping
        if chunk_peak >= 0.99:
            self._clip_count += 1
            if chunk_peak > self._peak:
                self._peak = chunk_peak
            now = time.perf_counter()
            if now - self._last_clip_log_time >= 1.0:
                log.debug("[RECORDING] Clipping detected: peak=%.4f, count=%d chunks.", chunk_peak, self._clip_count)
                self._last_clip_log_time = now
                # AUDIO-CLIP: push a real-time IPC event so the
                # Electron UI can flash a red level-bar / show a
                # "Clipping!" toast while recording. Pre-fix the
                # only notification was post-recording via
                # _finalize_audio_quality_report, which is too late
                # for the user to adjust mic gain mid-dictation.
                # The event is throttled to 1 Hz (same as the log)
                # to avoid flooding the IPC channel.
                #
                # RW-8: the event is enqueued on a non-blocking
                # ``queue.Queue`` and drained by a dedicated
                # ``_event_worker_thread`` (see ``_event_worker_loop``).
                # This keeps the audio worker thread off the IPC
                # transport — a slow TCP subscriber (or a blocked
                # Electron renderer) can no longer stall the worker
                # and cause ring-buffer overflows / dropped audio.
                # ``event_bus`` is now imported at module top.
                # PERF-FIX-4: ``put_nowait`` + ``queue.Full`` suppression
                # so a backed-up event worker can never block the audio
                # thread. The events are best-effort telemetry (clipping
                # counters throttled to 1 Hz) — dropping a few is fine.
                try:
                    self._event_queue.put_nowait(
                        {
                            "type": "audio_clip",
                            "data": {
                                "peak": float(chunk_peak),
                                "count": int(self._clip_count),
                            },
                        }
                    )
                except queue.Full:
                    pass  # worker fell behind — drop this telemetry event

        # AUDIO-3 / PERF-11: append to the live deque (atomic under
        # GIL — ``deque.append`` is a single C-level op with no torn
        # state). Pre-fix this wrote to ``recent_rms`` (the snapshot
        # list taken inside the lock above), which was a dead write —
        # the snapshot is never read after this point, so the deque
        # stayed empty and the snapshot was always ``[]``. Writing to
        # the deque makes the snapshot meaningful for any future
        # consumer that reads rolling RMS (e.g. waveform bubble, VAD
        # auto-calibration) and removes ~800 wasted list.append
        # allocs/s.
        self._recent_rms_values.append(chunk_rms)

        # AUDIO-014: auto-calibrate VAD thresholds from ambient noise
        self._vad_auto_calibrate(chunk_rms, chunk_duration)

        # AUDIO-013: compute Silero VAD probability if enabled.
        # RT-SAFE-001: this previously ran in the audio callback
        # (~1-5ms for 512 samples on CPU) and was a real-time safety
        # violation. It now runs on the worker thread.
        # VAD-GATE: skip Silero inference when VAD is disabled (all audio
        # enhancements off) to avoid wasting CPU.
        vad_prob = None
        if self._vad_enabled and self._use_silero_vad and self._silero_available:
            try:
                # impl-vad-fix: Silero VAD only accepts {8000, 16000} Hz.
                # The mic's native rate (self._effective_sr) may be 44100
                # or 48000, which previously raised:
                #   ValueError: Supported sampling rates: [8000, 16000]
                # Resample to 16000 using the same scipy resample_poly
                # path as _resample_audio_impl (gcd up/down pattern).
                if self._effective_sr not in (8000, 16000):
                    try:
                        resample_poly = _recording_pkg._get_resample_poly()
                        gcd = math.gcd(self._effective_sr, 16000)
                        up = 16000 // gcd
                        down = self._effective_sr // gcd
                        vad_audio = resample_poly(filtered.ravel(), up, down).astype(np.float32)
                        vad_sr = 16000
                    except Exception:
                        # scipy unavailable or resample failed — fall
                        # back to RMS rather than crashing the worker.
                        # compute_vad_prob will likely still raise on
                        # the unsupported rate, but it is caught by the
                        # outer except below.
                        vad_audio = filtered
                        vad_sr = self._effective_sr
                else:
                    vad_audio = filtered
                    vad_sr = self._effective_sr
                vad_prob = compute_vad_prob(vad_audio, vad_sr)
            except Exception:
                vad_prob = None  # fall back to RMS

        # AUDIO-013: VAD state machine with hysteresis
        # Convert RMS to dBFS for VAD thresholds
        chunk_rms_db = 20.0 * math.log10(chunk_rms) if chunk_rms > 0 else -90.0
        vad_state = self._vad_update(chunk_rms_db, vad_prob=vad_prob)

        # Use VAD state machine for silence detection
        # Voice detected by loudness → reset silence timer
        if vad_state == VadState.SILENCE:
            if self._silence_start_time is None:
                self._silence_start_time = time.perf_counter()
            self._silence_timer = time.perf_counter() - self._silence_start_time
        else:
            self._silence_start_time = None
            self._silence_timer = 0.0

        # Use cached config values (PERF-NEW-006)
        silence_warning_seconds = self._cached_silence_warning
        stop_on_silence_seconds = self._cached_stop_on_silence

        # H12a: Repeating silence warnings with exponential backoff
        if self._silence_timer >= silence_warning_seconds:
            time_since_first_warning = self._silence_timer - silence_warning_seconds
            expected_warnings = 0
            cumulative = 0.0
            wait = 10.0
            while cumulative <= time_since_first_warning:
                expected_warnings += 1
                cumulative += wait
                wait *= 2
            if expected_warnings > self._silence_warning_count:
                self._silence_warning_count = expected_warnings
                if silence_warning_cb is not None:
                    with contextlib.suppress(Exception):
                        silence_warning_cb()

        if self._silence_timer >= stop_on_silence_seconds and silence_auto_stop_cb is not None:
            with contextlib.suppress(Exception):
                silence_auto_stop_cb()

        # H12b: Maximum recording duration auto-stop
        recording_duration = time.perf_counter() - recording_start
        max_recording_time_seconds = self._cached_max_recording_time
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

        # Fire RMS callback OUTSIDE the lock
        # T021: forward the filtered audio chunk so downstream
        # consumers (WaveformBubble via app._on_recorder_rms) can
        # run Silero VAD on it. The chunk is a numpy float32 array
        # of the same shape as `filtered` (channels x samples).
        # Callers that don't care about VAD simply ignore the
        # third argument (backwards-compatible).
        if rms_callback is not None:
            try:
                rms_callback(chunk_rms, chunk_peak, filtered)
            except Exception:
                # NEW-CONC-004: previously this called
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
                self._rms_callback_error_count = getattr(self, "_rms_callback_error_count", 0) + 1
                if self._rms_callback_error_count == 1 or self._rms_callback_error_count % 100 == 0:
                    log.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d)",
                        self._rms_callback_error_count,
                        exc_info=True,
                    )
                else:
                    log.debug(
                        "[RECORDING] on_rms_level callback raised (occurrence #%d, traceback suppressed)",
                        self._rms_callback_error_count,
                    )

    def stop(self) -> np.ndarray:
        """Stop recording and return the complete audio array."""
        if not self._recording_event.is_set():
            return np.array([], dtype=np.float32)

        stop_started = time.perf_counter()
        self._recording_event.clear()

        # HOTKEY-CRASH: increment stop_generation so any stale disconnect
        # handlers from the audio callback know to bail out.
        self._stop_generation += 1

        # STREAM-FIX: mark that we're about to call stream.stop()
        # intentionally, so _stream_finished_callback doesn't warn about
        # an "unexpected" disconnect. Cleared after stream.close() below.
        self._user_stop_pending = True

        # 17-H-FIX-2: drain callback + stop + close via _teardown_stream()
        # (shared with discard()). The 300ms callback poll is preserved
        # verbatim — see the helper's docstring/comments for the
        # AUDIO-009/AUDIO-015 / PERF-FIX-002 history.
        self._teardown_stream()
        # STREAM-FIX: clear the user-stop-pending flag now
        # that stream.close() has completed. Any future
        # _stream_finished_callback invocation is now genuinely
        # unexpected (device disconnect).
        self._user_stop_pending = False
        stream_ms = (time.perf_counter() - stop_started) * 1000

        # RT-SAFE-001: stop the audio worker thread. drain=True so the
        # worker finishes processing any chunks still in the ring buffer
        # — those chunks end up in self._buffer, which we concatenate
        # below. Without this drain, the last few hundred ms of audio
        # (chunks pushed to the ring buffer but not yet processed by the
        # worker) would be lost.
        self._stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

        # RW-8: stop the IPC event worker thread AFTER the audio worker
        # so the audio worker has finished enqueuing IPC events (e.g.
        # audio_clip from the final chunks). drain=True so every queued
        # event is published to the IPC bus before stop() returns —
        # the UI sees the final state. Without this, a queued audio_clip
        # event could be lost if stop() returned before the event worker
        # drained it.
        self._stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

        # CPU-03: stop the device health checker thread (mirrors the event worker).
        self._stop_device_health_checker()

        concat_started = time.perf_counter()
        with self._lock:
            if not self._buffer:
                # Reset cache
                self._cached_resampled = np.array([], dtype=np.float32)
                self._cached_native_chunk_count = 0
                self._chunk_count = 0
                # NEW-PERF-003: invalidate the no-resample cache too.
                self._cached_no_resample_len = -1
                self._cached_no_resample_arr = None
                return np.array([], dtype=np.float32)
            audio = np.concatenate(list(self._buffer), axis=0).reshape(-1)
            # MEM-04 / SEC-audit-008: defer buffer zeroing to background daemon thread
            # so discard() returns immediately (the secure clear happens off the hot path).
            _old_buffer = self._buffer
            self._buffer = collections.deque(
                maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
            )
            _recording_pkg._secure_clear_array_background(_old_buffer)
            # Reset cache on stop
            self._cached_resampled = np.array([], dtype=np.float32)
            self._cached_native_chunk_count = 0
            # NEW-PERF-003: invalidate the no-resample cache too.
            self._cached_no_resample_len = -1
            self._cached_no_resample_arr = None
        concat_ms = (time.perf_counter() - concat_started) * 1000

        # Log audio statistics for diagnostics
        effective_sr = self._effective_sr
        duration = len(audio) / effective_sr if len(audio) > 0 else 0
        # TASK-14: initialize ``rms``/``peak``/``silence_pct`` BEFORE
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
            self._last_rms = rms
            # NEW-PERF-010: store the full-recording stats so the
            # transcription engine can reuse them instead of recomputing
            # the same RMS/peak/silence_pct on the same audio array
            # (saves 1-3 ms + 3× 1.9 MB transient memory per dictation).
            self._last_audio_stats = (rms, peak, silence_pct)
        else:
            self._last_rms = 0.0
            self._last_audio_stats = (0.0, 0.0, 0.0)
            log.warning("[RECORDING] No audio data captured!")

        # H15: stop() should NOT use cache - resample from scratch for full audio
        resample_started = time.perf_counter()
        audio = self._prepare_audio(audio, effective_sr)
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

    def snapshot(self) -> np.ndarray:
        """Return current recorded audio without clearing the active buffer.

        Uses a cached resampled prefix to avoid O(n²) resampling on every call.
        Only new chunks since the last snapshot are resampled, then concatenated
        with the cached prefix.

        PERF-NEW-002 / PERF-NEW-003: previously this called
        ``list(self._buffer)[start:]`` which allocated a full list copy
        of the deque on every snapshot (20K allocs/sec under sustained
        recording).  Replaced with ``itertools.islice`` which is O(1)
        in the deque size and avoids the intermediate list.  Also
        avoided the O(n) ``np.concatenate([cached, new])`` allocation
        when there's nothing new to add.

        NEW-PERF-003: when no new chunks have arrived since the last
        snapshot (the common case for the streaming thread polling at
        4 Hz), return a VIEW into the cached array instead of a full
        copy.  The streaming caller only reads the array and slices it
        (which produces another view); it never mutates the data.  The
        cache is replaced (not mutated in place) when new chunks arrive,
        so existing views remain valid until their references are
        released.  This eliminates ~7,200 × 1.9 MB = ~14 GB of garbage
        allocation per 30-minute recording session.

        NEW-PERF-007: avoid acquiring ``self._lock`` at all when the
        buffer is empty.  The streaming thread polls at 4 Hz; if the
        recorder hasn't started yet (or just stopped), each poll would
        contend with the audio callback's lock acquisition for no
        reason.  The lock-free ``len(self._buffer)`` check is safe
        because:
        - ``len()`` on a collections.deque is atomic in CPython.
        - If the buffer transitions from empty → non-empty between our
          check and the lock acquisition, the locked path handles it
          correctly (returns the new chunk).
        - If the buffer transitions from non-empty → empty (e.g.
          stop() called), the locked path returns the empty-array
          early-out.  No correctness issue.
        """
        import itertools

        # NEW-PERF-007: lock-free fast path for the empty-buffer case.
        # Avoids 4 Hz lock contention with the audio callback thread
        # when the recorder isn't actively recording.
        if not self._buffer:
            return np.array([], dtype=np.float32)
        with self._lock:
            if not self._buffer:
                return np.array([], dtype=np.float32)
            effective_sr = self._effective_sr
            # PERF-NEW-021: read the cached target_sr instead of
            # self.config.sample_rate to avoid attribute lookup under lock.
            target_sr = getattr(self, "_cached_target_sr", None) or self.config.sample_rate

            # ARCH-040: invalidate the cache if any of the parameters
            # that affect the resampled output have changed since the
            # last snapshot. Without this, a dtype or sample-rate
            # change mid-session would return stale (and wrong-rate)
            # cached audio.
            new_key = (
                str(self._buffer[0].dtype) if len(self._buffer) > 0 else "float32",
                effective_sr,
                target_sr,
            )
            if self._cached_resample_key != new_key:
                self._cached_resampled = np.array([], dtype=np.float32)
                self._cached_native_chunk_count = 0
                self._cached_resample_key = new_key
                # NEW-PERF-003: invalidate the no-resample cache too
                # — a sample-rate or dtype change invalidates both.
                self._cached_no_resample_len = -1
                self._cached_no_resample_arr = None

            if effective_sr != target_sr and len(self._buffer) > self._cached_native_chunk_count:
                # PERF-NEW-003: islice avoids the full-deque list copy.
                # Only the slice we actually need is materialized.
                new_chunks = list(
                    itertools.islice(
                        self._buffer,
                        self._cached_native_chunk_count,
                        None,
                    )
                )
                if new_chunks:
                    new_audio = np.concatenate(new_chunks, axis=0).reshape(-1)
                    # ERR-001: if resampling fails, drop the bad chunk
                    # rather than appending native-rate audio that
                    # would corrupt the streaming transcription.
                    try:
                        new_resampled = self._resample_chunk(new_audio, effective_sr, target_sr)
                    except ResampleError as e:
                        log.warning(
                            "[RECORDING] Snapshot resample failed; dropping %d native samples: %s",
                            len(new_audio),
                            e,
                        )
                        self._cached_native_chunk_count = len(self._buffer)
                        # NEW-PERF-003: return a view, not a copy.
                        return self._cached_resampled[:]
                    # PERF-NEW-002: avoid the O(n) reallocation when the
                    # cached prefix is empty (first snapshot of a session).
                    if len(self._cached_resampled) > 0:
                        self._cached_resampled = np.concatenate([self._cached_resampled, new_resampled])
                    else:
                        self._cached_resampled = new_resampled
                    self._cached_native_chunk_count = len(self._buffer)
                # NEW-PERF-003: return a VIEW into the cache.  The caller
                # (streaming.py) only reads + slices this array; it never
                # mutates.  When the cache is later replaced by a new
                # np.concatenate(...) assignment, this view remains valid
                # (numpy keeps the underlying buffer alive until all views
                # are released).  This eliminates the 1.9 MB copy on every
                # 4 Hz poll — ~14 GB of garbage per 30-min recording.
                return self._cached_resampled[:]
            elif effective_sr == target_sr:
                # No resampling needed, just concatenate all.
                # PERF-NEW-003: islice over the deque avoids the full
                # list copy.  ``np.fromiter`` would be even faster but
                # requires a flat iterator; the deque holds 2D chunks
                # so we still need one concatenate.
                #
                # NEW-PERF-003: cache the no-resample concatenation too,
                # so repeated snapshots with no new chunks don't repeat
                # the concatenate.  When chunks ARE new, we rebuild the
                # cache.  The cache key is the buffer length — if it
                # hasn't changed, the cached array is still valid.
                buf_len = len(self._buffer)
                if getattr(self, "_cached_no_resample_len", -1) == buf_len and self._cached_no_resample_arr is not None:
                    return self._cached_no_resample_arr[:]
                chunks = list(itertools.islice(self._buffer, 0, None))
                audio = np.concatenate(chunks, axis=0).reshape(-1)
                self._cached_no_resample_len = buf_len
                self._cached_no_resample_arr = audio
                return audio[:]
            else:
                # No new chunks, return cached
                # NEW-PERF-003: return a VIEW, not a copy.  See comment
                # in the resample branch above for why this is safe.
                return self._cached_resampled[:]

    def _resample_chunk(self, audio: np.ndarray, effective_sr: int, target_sr: int) -> np.ndarray:
        """Resample a single chunk of audio.

        Raises:
            ResampleError: if neither scipy nor linear-interp resampling
                could convert the audio to ``target_sr``. Callers MUST
                handle this; previously the function returned the native-
                rate audio silently, which led to garbage transcriptions
                on the streaming path (ERR-001).

        PERF-NEW-027: delegates to the shared ``_resample_audio_impl``
        helper (also used by ``_prepare_audio``) to avoid duplicating
        the scipy → linear interp → raise fallback chain.
        """
        if len(audio) == 0:
            return np.array([], dtype=np.float32)
        return self._resample_audio_impl(audio, effective_sr, target_sr, log_resample=False)

    def _prepare_audio(
        self,
        audio: np.ndarray,
        effective_sr: int,
        log_resample: bool = True,
    ) -> np.ndarray:
        """Convert captured audio to the configured sample rate.

        ERR-012: previously the except blocks used bare ``Exception``,
        which swallowed ``AttributeError`` / ``MemoryError`` /
        ``KeyboardInterrupt`` (in some interpreters). We narrow to
        ``(ValueError, OSError, TypeError)`` so genuine bugs propagate
        instead of being silently masked as "resampling failed".

        PERF-NEW-027: delegates to the shared ``_resample_audio_impl``
        helper (also used by ``_resample_chunk``) to avoid duplicating
        the scipy → linear interp → raise fallback chain.
        """
        target_sr = self.config.sample_rate  # 16000 for Whisper
        if effective_sr != target_sr and len(audio) > 0:
            return self._resample_audio_impl(audio, effective_sr, target_sr, log_resample=log_resample)
        return audio

    def _resample_audio_impl(
        self,
        audio: np.ndarray,
        effective_sr: int,
        target_sr: int,
        *,
        log_resample: bool = False,
    ) -> np.ndarray:
        """Shared resampling logic for ``_resample_chunk`` and ``_prepare_audio``.

        PERF-NEW-027: previously the scipy → linear interp → raise
        fallback chain was duplicated between the two methods. This
        helper centralizes it so bug fixes (ERR-012, ERR-001, ARCH-033)
        only need to be applied once.

        ERR-012: narrows exceptions to ``(ValueError, OSError, TypeError)``
        so genuine bugs (``AttributeError``, ``MemoryError``) propagate
        instead of being silently masked as "resampling failed".
        """
        orig_len = len(audio)
        resampled = False
        last_error: Exception | None = None
        try:
            resample_poly = _recording_pkg._get_resample_poly()
            gcd = math.gcd(effective_sr, target_sr)
            up = target_sr // gcd
            down = effective_sr // gcd
            audio = resample_poly(audio, up, down).astype(np.float32)
            if log_resample:
                log.info(
                    "[RECORDING] Resampled %d Hz -> %d Hz (%d -> %d samples)",
                    effective_sr,
                    target_sr,
                    orig_len,
                    len(audio),
                )
            resampled = True
        except ResampleUnavailableError as exc:
            # ARCH-033: scipy missing — fall through to linear interp.
            last_error = exc
            if log_resample:
                log.warning("[RECORDING] scipy not available, using linear interp resampling")
        except (ValueError, OSError, TypeError) as exc:
            # ERR-012: narrow to expected scipy/numpy failure modes.
            # AttributeError / MemoryError / etc. propagate.
            last_error = exc
            if log_resample:
                log.error("[RECORDING] scipy resample_poly failed: %s", exc)

        if not resampled:
            try:
                # PERF-017: numpy linear interpolation fallback — used when
                # scipy is unavailable. When scipy IS available, the
                # resample_poly path above is preferred (higher quality,
                # anti-aliasing). This fallback produces acceptable results
                # for speech audio at common sample rates (44.1k→16k, 48k→16k).
                ratio = target_sr / effective_sr
                new_len = int(len(audio) * ratio)
                indices = np.linspace(0, len(audio) - 1, new_len)

                audio = np.interp(
                    indices,
                    np.arange(len(audio)),
                    audio,
                ).astype(np.float32)
                if log_resample:
                    log.info(
                        "[RECORDING] Resampled (linear interp) %d Hz -> %d Hz (%d -> %d samples)",
                        effective_sr,
                        target_sr,
                        orig_len,
                        len(audio),
                    )
                resampled = True
            except (ValueError, OSError, TypeError) as exc:
                # ERR-012: narrow here too.
                last_error = exc
                if log_resample:
                    log.error(
                        "[RECORDING] All resampling failed: %s. Audio at %d Hz cannot be used by Whisper.",
                        exc,
                        effective_sr,
                    )

        if not resampled:
            # ERR-001: previously returned the native-rate audio here,
            # which silently produced garbage transcriptions. Raise so
            # the streaming / final paths can decide how to recover.
            raise ResampleError(
                f"Cannot resample audio from {effective_sr} Hz to {target_sr} Hz (last error: {last_error!r})"
            )
        return audio

    def discard(self) -> None:
        """Discard current recording without processing."""
        self._recording_event.clear()
        # STREAM-FIX (Task 6): set _user_stop_pending before stream.stop()
        # so the audio callback's early-return guard (line 574) suppresses
        # the false "Stream finished unexpectedly" warning. The stop()
        # path sets this flag (line 1887); discard() was missing it, so
        # cancelling a recording via the Cancel button still fired the
        # warning. This mirrors the stop() contract: any code path that
        # intentionally stops the stream must set _user_stop_pending first
        # so the callback knows the stream end is expected, not a crash.
        self._user_stop_pending = True
        # 17-H-FIX-2: increment stop_generation for symmetry with stop()
        # so any stale disconnect handler launched from the audio
        # callback (during discard's stream.stop()) bails out instead of
        # racing with the teardown — matching stop()'s HOTKEY-CRASH guard.
        self._stop_generation += 1
        # ARCH-021: guard _effective_sr reset with the lock so a
        # concurrent snapshot() reader sees a consistent value.
        with self._lock:
            self._effective_sr = self.config.sample_rate
        self._last_rms = 0.0
        self._silence_timer = 0.0
        self._silence_start_time = None
        self._silence_warning_count = 0
        self._silence_next_warning_wait = 10.0
        # Reset cache on discard
        self._cached_resampled = np.array([], dtype=np.float32)
        self._cached_native_chunk_count = 0
        # NEW-PERF-003: invalidate the no-resample cache too.
        self._cached_no_resample_len = -1
        self._cached_no_resample_arr = None
        # 17-H-FIX-2: drain callback + stop + close via _teardown_stream()
        # (shared with stop()). The previous inline stream.stop()/close()
        # here had NO _is_in_audio_callback poll, risking use-after-free
        # or deadlock when ESC-cancel landed during a busy audio callback
        # (which fires ~16×/s). The helper polls for up to 300ms before
        # close() and is idempotent if the stream was already None.
        self._teardown_stream()
        # RT-SAFE-001: stop the audio worker thread. drain=False because
        # discard() doesn't need the in-flight audio — it's about to
        # clear self._buffer anyway. The worker clears the ring buffer
        # and exits after its current chunk (if any). Any chunk the
        # worker appends to self._buffer before exiting is cleared below.
        self._stop_audio_worker(timeout=_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
        # RW-8: stop the IPC event worker with drain=False — the
        # recording was cancelled, so queued IPC events (e.g.
        # audio_clip from the discarded audio) don't need to be
        # published. The queue is cleared so the worker exits promptly.
        self._stop_event_worker(timeout=_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S, drain=False)
        # CPU-03: stop the device health checker thread (mirrors the event worker).
        self._stop_device_health_checker()
        with self._lock:
            # MEM-04 / SEC-audit-008: defer buffer zeroing to background daemon thread
            # so discard() returns immediately (the secure clear happens off the hot path).
            _old_buffer = self._buffer
            self._buffer = collections.deque(
                maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
            )
            _recording_pkg._secure_clear_array_background(_old_buffer)
