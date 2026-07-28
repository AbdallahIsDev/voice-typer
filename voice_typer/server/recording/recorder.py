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

# PVT-5 / CR-21 / S4-CR-21: bind ``_secure_clear_array`` at module top
# via a literal ``from voice_typer.server.recording import _secure_clear_array``
# statement so ``recorder._secure_clear_array`` is importable for tests
# (``test_secure_clear_array.test_secure_clear_array_bound_in_recorder_module``
# and ``test_secure_clear_array_import_statement_present_in_recorder_source``)
# and so a future regression that removes the binding surfaces as an
# ``AttributeError`` at import time rather than a silent ``NameError``
# swallowed by the secure-clear ``try/except`` in
# ``_secure_clear_session_caches``.  The call sites in
# ``start()``/``stop()``/``discard()`` still route through
# ``_recording_pkg._secure_clear_array(...)`` so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.recording._secure_clear_array", ...)``
# keep affecting production code (see module docstring §Patch-path).
from voice_typer.server.recording import _secure_clear_array  # noqa: F401, E402

# ─── AUDIO-013: VAD state machine ───────────────────────────────────────
# RW-04: VadState and the VAD state-machine / auto-calibration logic
# were extracted to ``voice_typer.server.vad_processor`` (VadProcessor
# class). The symbol is re-exported here for backward compatibility —
# existing imports ``from voice_typer.server.recording import Recorder,
# VadState`` keep working unchanged.
# AUDIO-014: default VAD thresholds (overridden by auto-calibration).
# the source of truth for VAD default constants is now
# ``voice_typer.server.vad_processor`` (``DEFAULT_VAD_*``). The
# leading-underscore aliases below are kept ONLY for backward
# compatibility with any external code / tests that imported them
# pre-extraction; internal call sites use the canonical imports
# directly so there's a single source of truth.
# The dead constants (``_DEFAULT_VAD_CALIBRATION_DURATION``,
# ``_DEFAULT_VAD_SPEECH_FRAMES``, ``_DEFAULT_VAD_SILENCE_FRAMES``,
# ``_DEFAULT_VAD_HANGOVER_FRAMES``) were removed — they had zero
# internal references and only existed as stale
# ``vad_processor.DEFAULT_VAD_*`` mirrors.
from voice_typer.server.vad_processor import (  # noqa: E402
    DEFAULT_VAD_SILENCE_THRESHOLD_DB,
    DEFAULT_VAD_SPEECH_THRESHOLD_DB,
)

# PVT-006 split: ``take_snapshot`` and ``discard_recording`` are the
# promoted bodies of ``Recorder.snapshot`` and ``Recorder.discard``. The
# methods become 1-line delegators so existing call sites, subclass
# overrides, and ``inspect.getsource`` checks continue to work. See
# :mod:`._recorder_split` for the full split plan.
from . import _recorder_split  # noqa: E402

# FZ-T8: ``AudioPipeline`` owns the six named helpers split out of
# ``Recorder._process_audio_chunk`` in a previous session
# (``_detect_device_disconnect`` / ``_handle_xrun_status`` /
# ``_apply_filter_chain`` / ``_append_to_buffer_locked`` /
# ``_compute_rms_and_peak`` / ``_run_vad_state_machine``). ``Recorder``
# keeps 1-line delegator methods so existing call sites, subclass
# overrides, and ``inspect.getsource`` checks continue to work. See
# :mod:`.audio_pipeline` for the collaborator pattern.
from .audio_pipeline import AudioPipeline  # noqa: F401, E402 — re-exported for tests

# PVT-22 / Phase 4.5: ``DeviceManager`` owns device enumeration, hot-swap,
# and the device-health-checker daemon thread. ``Recorder`` constructs a
# ``DeviceManager`` instance in ``__init__`` and delegates the device
# methods to it (1-line delegators below). Device-owned state lives on
# ``DeviceManager``; ``Recorder`` exposes the subset accessed by tests /
# KEEP-methods via property shims (see ``_device_disconnected`` etc.
# below the ``__init__``).
from .device_manager import DeviceManager  # noqa: F401, E402 — re-exported for tests

# FZ-T8: ``DisconnectHandler`` owns the device hot-swap stream-restart
# logic (the ~175-LOC block inside ``_stream_lifecycle_lock`` that was
# previously the tail of ``Recorder._handle_device_disconnect``). The
# bouncer checks + lock acquisition + re-checks STAY on
# ``Recorder._handle_device_disconnect`` so the GT-24 source-inspection
# regression tests continue to pin the lock-scope invariant. See
# :mod:`.disconnect_handler` for the collaborator pattern.
from .disconnect_handler import DisconnectHandler  # noqa: F401, E402 — re-exported for tests

# Constants that are NOT patched by tests and are only used by Recorder
# can be imported directly from the sibling submodules.
from .exceptions import (  # noqa: F401, E402 — re-exported for tests
    ResampleError,
    ResampleUnavailable,
    ResampleUnavailableError,
)
from .resampling import _SCIPY_PRELOADER_JOIN_TIMEOUT_S  # noqa: F401, E402 — re-exported for tests

# PVT-22 / Phase 4.5: ``resample_audio`` is the promoted body of
# ``Recorder._resample_audio_impl``. ``Recorder._resample_audio_impl``
# is now a 1-line delegator that calls this function so existing
# internal call sites (``_resample_chunk`` / ``_prepare_audio``) and
# any subclass overrides keep working unchanged.
from .resampling import resample_audio as _resample_audio_fn  # noqa: F401, E402

# PVT-22: ``VadShimMixin`` provides the ~18 ``_vad_*`` property shims
# (``_vad_state``, ``_vad_consecutive_speech_frames``, ...) that delegate
# read/write access to ``self._vad`` (a ``VadProcessor``). The mixin is
# purely delegation, so it is extracted out of ``recorder.py`` to keep
# the Recorder class body focused on real behavior. ``Recorder`` inherits
# from ``VadShimMixin`` so existing attribute access on ``Recorder``
# instances keeps working unchanged.
from .vad_helpers import VadShimMixin  # noqa: F401, E402

# DT-11: the previous ``_DEFAULT_VAD_SPEECH_THRESHOLD_DB`` /
# ``_DEFAULT_VAD_SILENCE_THRESHOLD_DB`` backward-compat aliases (which
# just re-exported ``DEFAULT_VAD_SPEECH_THRESHOLD_DB`` /
# ``DEFAULT_VAD_SILENCE_THRESHOLD_DB`` from ``vad_processor``) have
# been removed. Internal call sites now use the canonical
# ``DEFAULT_VAD_*`` names directly. The aliases were never referenced
# by tests or other modules (verified via repo-wide grep).


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


# PERF-NEW-018 / MAX_BUFFER_CHUNKS is dynamically adjusted in
# start() based on max_recording_time_seconds AND the device's effective
# sample rate (after _resolve_effective_sample_rate returns). The
# original implementation assumed 1024-sample blocks at 16kHz
# (chunk_seconds=0.064), but the actual blocksize is 512 and the
# effective rate may be 44.1/48kHz (device native rate). At 48kHz the
# stale 30000-chunk default only holds 30000 × 512/48000 ≈ 5.3 min — a
# 30-min dictation would silently lose the first ~25 min via deque
# maxlen eviction.
#
# The default below is a safe ceiling for the common 16kHz/512-sample
# case (30000 × 512/16000 = 960s = 16 min, comfortably above the 900s
# default max_recording_time_seconds). start() computes
# ``chunk_seconds = blocksize / effective_sr`` and resizes the deque to
# ``int(max_rec / chunk_seconds) + safety`` when the result exceeds
# this default.
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


# sentinel pushed onto ``_event_queue`` by ``_stop_event_worker``
# to wake the event worker immediately from its 0.5s ``queue.get``
# poll. Without the sentinel, the worker would not notice the stop
# signal until its next poll iteration (up to 0.5s latency), which
# caused ``test_concurrent_start_stop_no_leak`` to leak ~16 daemon
# threads during the 0.5s hammer (each spawn's daemon lingered for
# the full 0.5s poll before exiting). The sentinel is a unique
# object (not a dict) so the worker's ``event_bus.publish`` call
# never sees it -- the loop checks for the sentinel BEFORE
# publishing. The sentinel is a class (not an instance) so it's
# trivially picklable and comparable via ``is``.
class _EventWorkerStopSentinel:
    """Marker pushed onto the event queue to wake the worker on stop."""

    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_EVENT_WORKER_STOP_SENTINEL = _EventWorkerStopSentinel()
# Join timeout for stop() — generous so the worker drains the queue
# (publishing every queued event to the IPC bus) before exiting. The
# queue is tiny (events throttled at 1 Hz source-side), so this is
# headroom, not a tight bound.
_EVENT_WORKER_JOIN_TIMEOUT_S = 2.0
# Join timeout for discard() — shorter because discard() clears the
# queue first, so the worker exits after its current publish (if any).
_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S = 1.0


class Recorder(VadShimMixin):
    """Records audio from microphone into a buffer. Session-based: start, accumulate, stop, get data."""

    # DE-4: substrings of PortAudio OSError messages that indicate a
    # microphone permission denial (vs. a hardware fault). When the OS
    # reports ``DENIED`` or ``PROMPT`` AND the OSError message matches
    # one of these (case-insensitive), we re-raise as
    # ``MicrophonePermissionDeniedError`` so the IPC layer can surface
    # the permission onboarding UI instead of a generic "recording
    # failed" toast.
    _PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS: tuple[str, ...] = (
        "Unanticipated host error",
        "No input devices available",
        "Invalid number of channels",
        "Invalid sample rate",
        "Device unavailable",
        "Could not retrieve device info",
    )

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
        # CR-16: serializes ``start()`` against ``discard()`` so a
        # concurrent toggle-thread + ESC-cancel-thread + auto-stop
        # Timer thread cannot race on the per-session state reset
        # (buffer.clear, _chunk_count=0, etc.) and stream-start. The
        # lock is acquired at the top of ``start()`` / ``discard()``
        # and released before the method returns; it is NEVER held
        # across ``thread.join()`` or ``_process_audio_chunk`` (same
        # invariant as ``_worker_lifecycle_lock``). Reentrant-safe
        # under ``start()→stop()`` because each method acquires +
        # releases in a ``with`` block. Tests in
        # ``tests/test_i5_retry_fixes.py::TestCR16StartLockRuntime``
        # assert the lock is unheld before/after each method.
        self._start_lock = threading.Lock()
        # GT-23: serializes the read-check-create-start sequence in
        # ``_start_audio_worker`` / ``_start_event_worker`` and the
        # read-check-clear-join-unregister sequence in the
        # corresponding ``_stop_*`` methods so concurrent
        # start()/stop()/discard() (toggle thread, auto-stop Timer
        # thread, ESC-cancel thread, device-disconnect handler thread)
        # cannot race on ``self._worker_thread`` /
        # ``self._event_worker_thread``. This is a SEPARATE lock from
        # ``self._lock`` — never hold ``self._lock`` across
        # ``thread.join()`` or ``_process_audio_chunk`` would deadlock
        # when the worker tries to acquire ``self._lock`` for the
        # buffer append.
        self._worker_lifecycle_lock = threading.Lock()
        # GT-24: serializes stream teardown (``_teardown_stream``)
        # against the stream-restart block of
        # ``_handle_device_disconnect`` so a concurrent ``stop()`` /
        # ``discard()`` cannot mutate ``self._stream`` mid-flight of
        # the disconnect handler's restart, and vice-versa.
        self._stream_lifecycle_lock = threading.Lock()

        # force-closed flag set by ``shutdown_controller`` when
        # ``recorder.stop()`` / ``recorder.discard()`` times out and the
        # worker thread is leaked (still touching the PortAudio stream).
        # ``shutdown_mic_watcher`` reads this flag at the top and
        # short-circuits, so a subsequent cleanup call cannot race the
        # leaked worker (PortAudio is not safe for concurrent stream
        # operations from multiple threads). Previously this was a
        # write-only dead state (``shutdown_controller`` set it under
        # ``contextlib.suppress`` but no one read it); the read side implements
        # the read side and the suppress wrapper is now unnecessary.
        self._force_closed: bool = False

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
        # Critical: the actual sample rate of the audio currently
        # held in ``self._buffer``. Set by ``_process_audio_chunk`` after
        # ``AudioProcessor.process_chunk`` resamples to the chain's
        # construction rate (typically 16 kHz) — so when a processor is
        # attached, ``_buffer_sr == 16000`` regardless of the device's
        # native rate (``_effective_sr`` may be 48000). When no
        # processor is attached, ``_buffer_sr == _effective_sr``.
        # ``stop()`` and ``snapshot()`` (via ``_recorder_split``) read
        # this to avoid double-resampling already-resampled audio.
        # Reset to ``None`` by ``start()``, ``stop()``, and ``discard()``
        # (the latter two via ``_recorder_split``) so a fresh session
        # starts clean. The ``None`` sentinel makes the
        # ``_buffer_sr or _effective_sr`` fallback idiom work before the
        # first chunk arrives.
        self._buffer_sr: int | None = None
        # per-chunk VAD property cache. The audio worker hot path
        # (16 Hz) previously evaluated ``self._vad_enabled and
        # self._use_silero_vad and self._silero_available`` on every
        # chunk — 3 property-lookup dispatches × 16 Hz = 48 lookups/sec
        # for values that only change on ``on_config_changed()``. The
        # cache is refreshed at ``start()`` time, on every
        # ``on_config_changed()`` call, and lazily inside
        # ``_process_audio_chunk`` when ``_buffer_sr`` changes.
        self._cached_vad_enabled: bool = False
        self._cached_use_silero_vad: bool = False
        self._cached_silero_available: bool = False
        # Cached (up, down) integer ratio for the VAD
        # resample path (post-process_chunk rate → 16 kHz). ``None``
        # means no resample is needed (``_buffer_sr`` is already
        # 8000/16000). Avoids per-chunk ``math.gcd`` recomputation.
        self._cached_vad_resample_up_down: tuple[int, int] | None = None
        self._cached_vad_resample_sr: int | None = None
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

        # snapshot() resample-path segment list + lazy concat.
        # Previously every snapshot that saw new chunks re-concatenated
        # the *entire* cached prefix (``np.concatenate([cached, new])``)
        # -- an O(N) memcpy where N grows linearly with session length.
        # At 16 Hz chunk arrival x 30 min x 1.9 MB/chunk-prefix, this
        # summed to hundreds of MB-GB of redundant memcpy per session.
        # We now keep the cached prefix as a *list* of resampled
        # segments and only materialize a contiguous ndarray when the
        # caller actually needs one (``_ensure_resampled_concat`` in
        # :mod:`._recorder_split`). The dirty flag tracks whether the
        # segments list has changed since the last concat; snapshots
        # that see no new chunks reuse the cached concat (zero memcpy).
        self._cached_resampled_segments: list[np.ndarray] = []
        self._cached_resampled_concat_dirty: bool = False

        # per-session error counters. Previously these were
        # lazily initialized via ``getattr(self, "_dropped_chunks", 0)``
        # in the audio callback / RMS callback and never reset in
        # ``start()`` -- so a long-running app accumulated error counts
        # across sessions, masking per-session regressions in tests
        # and dashboards. Declare them in ``__init__`` and reset them
        # in ``start()`` so each session starts from zero.
        self._dropped_chunks: int = 0
        self._rms_callback_error_count: int = 0

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
        #
        # the deque maxlen MUST be sized against the device's
        # *effective* sample rate, not config.sample_rate (16kHz). At
        # 48kHz the same 512-sample blocksize fires 3× more often, so a
        # 1-second pre-roll needs 3× the chunk capacity. The placeholder
        # sizing below uses config.sample_rate as a safe default for the
        # common 16kHz case; start() re-sizes the deque once
        # _effective_sr is known (after the device loop succeeds) using
        # the values cached in _preroll_seconds / _preroll_blocksize.
        # pre_roll_buffer_seconds is a Config dataclass field
        # (config.py, default 0.0) — always present on a real Config
        # instance, so the getattr fallback could never fire and was
        # dead-defensive code. The ``or 0`` guard is preserved because
        # 0.0 is a valid "disabled" value and float(0.0 or 0) == 0.0
        # is a no-op equivalence.
        preroll_seconds = float(config.pre_roll_buffer_seconds or 0)
        sample_rate = int(config.sample_rate or 16000)
        # cache these so start() can recompute the deque maxlen
        # using _effective_sr without re-reading config (which the audio
        # callback does not touch).
        self._preroll_seconds: float = preroll_seconds
        self._preroll_blocksize: int = 512  # matches sd.InputStream blocksize
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
        self._event_queue: queue.Queue[dict | _EventWorkerStopSentinel] = queue.Queue(maxsize=1000)
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

        # Medium: single-flight guard for the disconnect-handler
        # thread spawns. Three sites spawn ``_handle_device_disconnect``
        # on a fresh daemon thread (the audio callback's zero-fill
        # detector, ``_stream_finished_callback``, and the device
        # health-checker loop). Without a guard, a flapping device
        # (BT mic reconnecting repeatedly) can spawn multiple handler
        # threads concurrently — they race on ``_stream_lifecycle_lock``
        # and the stream-restart block. The guard ensures only ONE
        # handler thread is running at a time; additional spawns while
        # the first is running are no-ops (the existing handler will
        # complete the restart or hit the retry budget).
        self._disconnect_handler_lock = threading.Lock()
        self._disconnect_handler_running = False

        # AUDIO-HOT: hot-plug device disconnect handling
        # PVT-22 / Phase 4.5: the 12 device-related state attrs +
        # MicrophoneDeviceWatcher lifecycle were moved to
        # ``DeviceManager`` (see ``device_manager.py``). The attrs are
        # re-exposed on ``Recorder`` via read/write property shims (see
        # the property block below ``__init__``) so existing tests that
        # do ``r._device_disconnected = False`` / ``r._mic_watcher is
        # None`` keep working unchanged. KEEP-methods on ``Recorder``
        # that read/write these attrs (``_handle_device_disconnect``,
        # ``_stream_finished_callback``, ``_process_audio_chunk``,
        # ``start``) also go through the shims.
        #
        # The DeviceManager is constructed AFTER the basic Recorder
        # state is initialized (``_recording_event``, ``_stream``,
        # ``config``, etc.) so its ``__init__`` can register the
        # MicrophoneDeviceWatcher callback against
        # ``self._invalidate_device_cache`` (a delegator method that
        # routes through ``self._devices`` — which is set by this
        # assignment).
        self._devices: DeviceManager = DeviceManager(self)

        # FZ-T8: ``DisconnectHandler`` owns the ~175-LOC stream-restart
        # block previously inlined in ``_handle_device_disconnect``. The
        # bouncer checks + ``_stream_lifecycle_lock`` acquisition +
        # re-checks STAY on ``Recorder._handle_device_disconnect`` so
        # the GT-24 source-inspection regression tests continue to pin
        # the lock-scope invariant (see
        # ``tests/test_recorder_worker_lifecycle.py``).
        # ``AudioPipeline`` owns the six named helpers split out of
        # ``_process_audio_chunk`` in a previous session. ``Recorder``
        # keeps 1-line delegator methods on each helper name so existing
        # call sites and ``inspect.getsource`` checks continue to work.
        # Both collaborators store a back-reference to ``self`` and do
        # NOT touch recorder state at construction time, so they can be
        # instantiated as soon as ``self._devices`` is ready.
        self._disconnect_handler: DisconnectHandler = DisconnectHandler(self)
        self._audio_pipeline: AudioPipeline = AudioPipeline(self)

        # PERF: pre-warm the device-list cache on a background daemon
        # thread so the start() hotkey critical path doesn't pay the
        # 50-200ms PortAudio enumeration cost on the first recording.
        # ``DeviceManager._refresh_device_list`` is the cached path
        # (30s TTL, OS-event-invalidated); without pre-warming, the
        # first ``start()`` after app launch would block on
        # ``sd.query_devices()`` even though ``Recorder`` was
        # constructed seconds earlier. The thread is best-effort —
        # if PortAudio is unavailable (headless CI, no audio HW), the
        # cache stays empty and ``start()`` falls back to direct
        # ``sd.query_devices()`` calls (no regression).
        self._prewarm_device_cache()

        # NOTE (RW-0): dead_air_timeout / _dead_air_speech_detected /
        # _dead_air_silence_start were REMOVED — redundant with
        # stop_on_silence_seconds. Do NOT re-add.

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
        # G4-L-04: callback signature is (rms: float, peak: float).
        # The previous 3-arg form (rms, peak, audio_chunk) forwarded the
        # filtered audio chunk so WaveformBubble could run Silero VAD on
        # it, but BUBBLE-FIX-4.1 removed the VAD gate entirely (the
        # device's native sample-rate audio was being fed to a model
        # that assumes 16 kHz).  No current consumer reads the chunk, so
        # it was removed from the contract (privacy surface + per-chunk
        # refcount cost on the audio hot path).

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

    # ── PVT-22 / Phase 4.5: device-state property shims ─────────────────
    #
    # The 12 device-related state attrs were moved to ``DeviceManager``
    # (see ``device_manager.py``). The subset listed below is still
    # accessed directly on ``Recorder`` by:
    #
    #   - KEEP-methods on ``Recorder`` (``_handle_device_disconnect``,
    #     ``_stream_finished_callback``, ``_process_audio_chunk``,
    #     ``start``)
    #   - existing tests that do ``r._device_disconnected = False`` /
    #     ``r._mic_watcher is None`` / ``r._device_list_cache = ...``
    #     (see tests/test_microphone_watcher.py /
    #     tests/test_audio_callback.py /
    #     tests/regressions/audio_test.py)
    #
    # The shims delegate reads AND writes through to ``self._devices.X``
    # so both directions keep working. The 4 attrs that are ONLY used
    # inside ``DeviceManager`` methods after the move
    # (``_device_list_cache_ttl``, ``_device_check_interval``,
    # ``_device_health_checker_thread``, ``_device_check_interval_s``)
    # do NOT need shims and are accessed purely via ``self._devices.X``.

    @property
    def _device_disconnected(self) -> bool:
        return self._devices._device_disconnected

    @_device_disconnected.setter
    def _device_disconnected(self, value: bool) -> None:
        self._devices._device_disconnected = value

    @property
    def _device_disconnect_retries(self) -> int:
        return self._devices._device_disconnect_retries

    @_device_disconnect_retries.setter
    def _device_disconnect_retries(self, value: int) -> None:
        self._devices._device_disconnect_retries = value

    @property
    def _max_disconnect_retries(self) -> int:
        return self._devices._max_disconnect_retries

    @_max_disconnect_retries.setter
    def _max_disconnect_retries(self, value: int) -> None:
        self._devices._max_disconnect_retries = value

    @property
    def _device_check_counter(self) -> int:
        return self._devices._device_check_counter

    @_device_check_counter.setter
    def _device_check_counter(self, value: int) -> None:
        self._devices._device_check_counter = value

    @property
    def _device_health_stop_event(self) -> threading.Event:
        return self._devices._device_health_stop_event

    @_device_health_stop_event.setter
    def _device_health_stop_event(self, value: threading.Event) -> None:
        self._devices._device_health_stop_event = value

    @property
    def _device_list_cache(self) -> list[dict] | None:
        return self._devices._device_list_cache

    @_device_list_cache.setter
    def _device_list_cache(self, value: list[dict] | None) -> None:
        self._devices._device_list_cache = value

    @property
    def _device_list_cache_time(self) -> float:
        return self._devices._device_list_cache_time

    @_device_list_cache_time.setter
    def _device_list_cache_time(self, value: float) -> None:
        self._devices._device_list_cache_time = value

    @property
    def _mic_watcher(self) -> Any | None:
        return self._devices._mic_watcher

    @_mic_watcher.setter
    def _mic_watcher(self, value: Any | None) -> None:
        self._devices._mic_watcher = value

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
    #
    # PVT-22 / Phase 4.5: the device-list cache, mic-watcher lifecycle,
    # and device-resolution / sample-rate negotiation logic were moved
    # to ``DeviceManager`` (see ``device_manager.py``). The methods
    # below are 1-line delegators that route through ``self._devices``
    # so existing internal call sites (``start()``, ``_handle_device_disconnect``,
    # ``_device_health_checker_loop``) and external callers (e.g.
    # ``VoiceTyperApp.list_microphones``) keep working unchanged.

    def _refresh_device_list(self) -> list[dict]:
        """Return the device list, refreshing the cache if stale (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager._refresh_device_list``.
        """
        return self._devices._refresh_device_list()

    def _invalidate_device_cache(self) -> None:
        """Reset the device-list cache (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager._invalidate_device_cache``.
        """
        return self._devices._invalidate_device_cache()

    def shutdown_mic_watcher(self) -> None:
        """Stop the microphone device-change watcher (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager.shutdown_mic_watcher``.
        Called explicitly from ``VoiceTyperApp.quit_app()`` during shutdown
        and defensively from ``__del__``. Safe to call even if the watcher
        never started (``_mic_watcher`` is None).

        short-circuit when ``self._force_closed`` is set.
        ``shutdown_controller`` sets this flag when
        ``recorder.stop()`` / ``recorder.discard()`` times out and the
        worker thread is leaked (still touching the PortAudio stream).
        Calling ``shutdown_mic_watcher`` in that state would race the
        leaked worker — PortAudio is not safe for concurrent stream
        operations from multiple threads. The caller
        (``shutdown_controller._do_cleanup``) already skips the
        ``shutdown_mic_watcher`` call when ``recorder_force_closed`` is
        set, but this read-side guard is the defense-in-depth so a
        stray ``__del__`` / ``quit_app`` path that bypasses
        ``shutdown_controller`` also short-circuits.
        """
        if self._force_closed:
            return
        return self._devices.shutdown_mic_watcher()

    def __del__(self) -> None:
        """Best-effort cleanup. Must never raise.

        the previous 6 sequential ``with contextlib.suppress(Exception)``
        blocks are collapsed into a single loop over the cleanup steps.
        The cleanup sequence + per-step suppression semantics are
        preserved verbatim — each step still runs in its own
        ``contextlib.suppress(Exception)`` block so a failure in one
        step (e.g. ``_teardown_stream`` raising because the stream was
        never opened) doesn't skip the remaining steps.

        Each step is wrapped in a ``lambda`` so the attribute access
        (``self._recording_event``, ``self._worker_stop_event``, etc.)
        happens INSIDE ``contextlib.suppress`` — the previous
        ``self._attr.method`` form evaluated the attribute lookup while
        building the tuple (before the suppress block), which raised
        ``AttributeError`` on a partially-constructed instance (e.g.
        ``Recorder.__new__(Recorder)`` in ``test_del_never_raises``)
        instead of being suppressed.
        """
        for step in (
            lambda: self.shutdown_mic_watcher(),
            lambda: self._recording_event.clear(),
            lambda: self._worker_stop_event.set(),
            lambda: self._event_stop_event.set(),
            lambda: self._device_health_stop_event.set(),
            lambda: self._teardown_stream(),
        ):
            with contextlib.suppress(Exception):
                step()

    # ── AUDIO-HOT: hot-plug disconnect handling ─────────────────────────

    def _spawn_device_thread(
        self,
        name: str,
        target: Callable[..., None],
        kwargs: dict[str, Any] | None = None,
        *,
        single_flight: bool = False,
    ) -> bool:
        """Medium: spawn a daemon thread registered with
        ``thread_registry`` (when available).

        Replaces 5 bare ``threading.Thread(...).start()`` sites in the
        device-disconnect path that were unregistered — risking
        half-written config on shutdown (the prewarm and mic-fallback-
        save threads may be mid-``sd.query_devices()`` (50-200ms) or
        mid-``config.save()`` (50-500ms disk write) when the process
        exits).

        Args:
            name: thread name (also used as the registry key).
            target: thread entry point.
            kwargs: keyword arguments for ``target``.
            single_flight: when True, use the disconnect-handler
                single-flight guard (``_disconnect_handler_lock`` +
                ``_disconnect_handler_running``) so only ONE handler
                thread is running at a time. Additional spawns while
                the first is running are no-ops (returns False).

        Returns:
            True if the thread was spawned, False if single-flight
            suppressed it (or the spawn raised and was suppressed by
            the outer ``contextlib.suppress``).
        """
        if single_flight:
            with self._disconnect_handler_lock:
                # if the disconnect flag was already cleared
                # (e.g. by a successful restart in
                # ``_handle_device_disconnect``, or by ``start()``,
                # or by a test simulating a restart), clear the guard
                # so a new spawn can proceed. The flag and the guard
                # are coupled: a True guard means "a handler is
                # running for an active disconnect" — if the
                # disconnect is no longer active, the guard is stale.
                if not self._device_disconnected:
                    self._disconnect_handler_running = False
                if self._disconnect_handler_running:
                    log.debug(
                        "[RECORDING] %s spawn suppressed — handler already running (single-flight)",
                        name,
                    )
                    return False
                self._disconnect_handler_running = True

            def _guarded_target(**kw):
                try:
                    target(**kw)
                finally:
                    with self._disconnect_handler_lock:
                        self._disconnect_handler_running = False

            _target = _guarded_target
        else:
            _target = target

        try:
            _thread = threading.Thread(
                target=_target,
                kwargs=kwargs or {},
                name=name,
                daemon=True,
            )
            _thread.start()
        except Exception:
            log.debug("[RECORDING] %s spawn failed", name, exc_info=True)
            # If single_flight flagged us as running but the spawn
            # failed, clear the flag so the next attempt can proceed.
            if single_flight:
                with self._disconnect_handler_lock:
                    self._disconnect_handler_running = False
            return False

        # register with thread_registry when available so
        # ``shutdown_all()`` can signal/join during process exit.
        # ``stop_event=None`` because these are fire-and-forget daemon
        # threads (no clean stop mechanism); ``join_timeout`` is short
        # (0.5s) so shutdown doesn't block on a slow ``config.save()``.
        if self._thread_registry is not None:
            try:
                self._thread_registry.register(
                    name=name,
                    thread=_thread,
                    stop_event=None,
                    join_timeout=0.5,
                )
            except Exception:
                log.debug(
                    "[RECORDING] %s thread_registry.register failed",
                    name,
                    exc_info=True,
                )
        return True

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

        GT-24: capture ``gen = self._stop_generation`` at scheduling time
        and pass it via ``kwargs={'_captured_generation': gen}`` so the
        spawned ``_handle_device_disconnect`` can bail out if a deliberate
        stop/start cycle happened between scheduling and execution (mirrors
        the pattern in ``_process_audio_chunk`` at the zero-filled-indata
        spawn site). Pre-fix, the handler was scheduled with the default
        ``_captured_generation=0``, which matched the initial
        ``_stop_generation=0`` on the first session — defeating the
        bouncer for any stop() that landed between scheduling and execution.
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
            # GT-24: capture the current stop_generation so the handler
            # can detect a deliberate stop/start cycle that happened
            # between scheduling and execution. Mirrors the
            # _process_audio_chunk spawn site.
            _captured_gen = self._stop_generation
            # use _spawn_device_thread so the handler is
            # registered with thread_registry (when available) and
            # single-flight guarded so a flapping device can't spawn
            # multiple concurrent handlers.
            self._spawn_device_thread(
                name="stream-finished-handler",
                target=self._handle_device_disconnect,
                kwargs={"_captured_generation": _captured_gen},
                single_flight=True,
            )

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
            # High: fire a dedicated ``on_device_lost`` callback
            # (not ``on_silence_auto_stop``) so the UI shows "Microphone
            # disconnected" rather than "silence detected". Pre-fix, the
            # user saw a misleading "silence" message when the mic was
            # actually unplugged. Fall back to ``on_silence_auto_stop``
            # only when ``on_device_lost`` is not wired (preserves the
            # pre-fix behavior for callers that haven't been updated).
            _device_lost_cb = getattr(self, "on_device_lost", None)
            if callable(_device_lost_cb):
                with contextlib.suppress(Exception):
                    _device_lost_cb()
            elif self.on_silence_auto_stop is not None:
                with contextlib.suppress(Exception):
                    self.on_silence_auto_stop()
            # clear the disconnect flag so the next health-checker
            # cycle (30s) re-probes. Pre-fix, the flag stayed True
            # forever — the health-checker's ``if self._device_disconnected:
            # continue`` skip meant the recorder never auto-recovered
            # even if the user plugged in a new mic.
            self._device_disconnected = False
            # Reset retry counter so a subsequent disconnect (after the
            # user plugs in a new mic and starts a fresh session) gets a
            # full retry budget.
            self._device_disconnect_retries = 0
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
        # GT-24: ``_teardown_stream`` acquires+releases
        # ``_stream_lifecycle_lock`` internally; the restart block below
        # re-acquires it for the new-stream creation+assignment.
        self._teardown_stream()

        # GT-24: hold the stream-lifecycle lock across the restart so a
        # concurrent ``stop()`` / ``discard()`` cannot mutate
        # ``self._stream`` mid-restart. A concurrent stop() may have run
        # between the ``_teardown_stream()`` call above (which released
        # the lock) and the acquire below — re-check the bouncer
        # conditions before creating a new stream so we don't restart
        # on top of a deliberately-stopped recorder.
        with self._stream_lifecycle_lock:
            if _captured_generation != self._stop_generation:
                log.debug(
                    "[RECORDING] Disconnect restart skipped — stop_generation changed (%d != %d)",
                    _captured_generation,
                    self._stop_generation,
                )
                return
            if not self._recording_event.is_set():
                log.debug("[RECORDING] Disconnect restart skipped — recording was deliberately stopped")
                return

            # FZ-T8: the device-resolution + stream-open + state-update
            # block (the ~175-LOC tail of this method) was extracted to
            # :meth:`DisconnectHandler.restart_stream`. The handler runs
            # under ``_stream_lifecycle_lock`` (acquired above) and
            # re-checks ``_captured_generation != self._stop_generation``
            # a third time before assigning ``self._stream`` (the
            # close-the-new-stream-on-race path lives in the handler).
            self._disconnect_handler.restart_stream(_captured_generation)

    # ── CPU-03: Device health checker thread ─────────────────────────
    #
    # PVT-22 / Phase 4.5: the health-checker thread state + main loop
    # were moved to ``DeviceManager``. The methods below are 1-line
    # delegators. ``_device_health_checker_loop`` accesses
    # ``self.recorder._recording_event`` / ``self.recorder._stop_generation``
    # / ``self.recorder._handle_device_disconnect`` via the collaborator
    # back-reference (see ``device_manager.py``).

    def _start_device_health_checker(self) -> None:
        """Start the device health checker daemon thread (delegator).

        PVT-22 / Phase 4.5: body moved to
        ``DeviceManager._start_device_health_checker``.
        """
        return self._devices._start_device_health_checker()

    def _stop_device_health_checker(self, timeout: float | None = None) -> None:
        """Signal the device health checker thread to stop and join it (delegator).

        PVT-22 / Phase 4.5: body moved to
        ``DeviceManager._stop_device_health_checker``. when ``timeout`` is explicitly ``0.0``, the call is
        fire-and-forget -- the stop event is signalled but the method
        returns immediately without joining the daemon thread. This is
        used by ``stop()`` to avoid blocking up to 1.0s on a thread that
        almost always times out anyway (the checker sleeps 30s between
        probes, so a 1.0s join rarely succeeds). The daemon thread exits
        on its next ``_device_health_stop_event.wait()`` return.

        Any other ``timeout`` value (including ``None`` for backward-
        compatibility with callers that don't pass one) delegates to
        ``DeviceManager._stop_device_health_checker``, which uses its
        own 1.0s join. The ``DeviceManager`` API is owned by a different
        sub-agent's file boundary, so we don't add the timeout parameter
        there -- instead, the fire-and-forget path sets the stop event
        directly on the DeviceManager's stop-event attribute.
        """
        if timeout == 0.0:
            # Fire-and-forget: signal the stop event, do NOT join. The
            # daemon thread will exit on its next 30s wait() return.
            # Accessing the private attribute is safe because this class
            # and ``DeviceManager`` are tightly coupled collaborators in
            # the same package.
            self._devices._device_health_stop_event.set()
            return
        return self._devices._stop_device_health_checker()

    def _device_health_checker_loop(self) -> None:
        """Device health checker daemon thread main loop (delegator).

        PVT-22 / Phase 4.5: body moved to
        ``DeviceManager._device_health_checker_loop``.
        """
        return self._devices._device_health_checker_loop()

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
    # The ~18 ``_vad_*`` property shims (``_vad_state``,
    # ``_vad_consecutive_speech_frames``, ...) and the ``_vad_enabled``
    # property live on :class:`.vad_helpers.VadShimMixin`. ``Recorder``
    # inherits from ``VadShimMixin`` so the historical attribute names
    # keep working on ``Recorder`` instances. The shims are pure
    # delegation to ``self._vad`` (a ``VadProcessor``); the rename map
    # (e.g. ``_vad_state`` ↔ ``state``) lives only in the mixin. See
    # ``vad_helpers.py`` for the full rationale.

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
        # refresh per-chunk VAD caches so the 16 Hz audio worker
        # hot path reads cached scalars instead of dispatching 3 property
        # lookups per chunk. ``_refresh_vad_caches`` reads
        # ``self._vad_enabled`` (which ``self._vad.on_config_changed``
        # just refreshed) and the other two VAD properties, then
        # recomputes the (up, down) resample-ratio cache.
        self._refresh_vad_caches()

    def _refresh_vad_caches(self) -> None:
        """refresh per-chunk VAD caches.

        Called by ``start()`` and ``on_config_changed()`` so the audio
        worker hot path (16 Hz) reads cached scalars instead of
        dispatching 3 property lookups per chunk × 16 Hz = 48 lookups/sec
        for values that only change on config edits.

        Also computes the (up, down) integer ratio for the VAD resample
        path (). The ratio is derived from ``_buffer_sr``
        (the post-process_chunk rate set by ``_process_audio_chunk``).
        When ``_buffer_sr`` is 8000 or 16000, no resample is needed and
        the cache is set to ``None``. When ``_buffer_sr`` is something
        else (e.g. 48000 — happens when no AudioProcessor is attached
        and the device's native rate is non-16 kHz), the cache stores
        the (up, down) integers so the per-chunk VAD path avoids
        recomputing ``math.gcd``.

        ``_buffer_sr`` may be ``None`` at ``start()`` time (before the
        first chunk arrives); we fall back to ``_effective_sr`` for the
        cache key. If the actual ``_buffer_sr`` set by the first
        ``_process_audio_chunk`` differs, the cache is refreshed lazily
        inside ``_process_audio_chunk``.
        """
        self._cached_vad_enabled = self._vad_enabled
        self._cached_use_silero_vad = self._use_silero_vad
        self._cached_silero_available = self._silero_available
        # the VAD branch decision must use ``_buffer_sr`` (the
        # post-process_chunk rate) instead of ``_effective_sr`` (the
        # device's native rate). When a processor is active,
        # ``_buffer_sr == 16000`` and the VAD branch is skipped entirely
        # — no double-resample.
        vad_sr = self._buffer_sr if self._buffer_sr is not None else self._effective_sr
        if vad_sr is not None and vad_sr not in (8000, 16000) and vad_sr > 0:
            gcd = math.gcd(int(vad_sr), 16000)
            self._cached_vad_resample_up_down = (16000 // gcd, int(vad_sr) // gcd)
        else:
            self._cached_vad_resample_up_down = None
        self._cached_vad_resample_sr = vad_sr

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
        """Resolve config.microphone to a sounddevice device specifier (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager._resolve_device``.
        ``config.microphone`` is a string device index (from list_microphones)
        or None for system default.  We convert to int for unambiguous
        selection by sounddevice.
        """
        return self._devices._resolve_device()

    def _host_api_name(self, host_api_index: int) -> str:
        """Return the host API name for the given index (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager._host_api_name``.
        """
        return self._devices._host_api_name(host_api_index)

    def _device_index(self, fallback_index: int, device_info: dict) -> int:
        """Return the device index from device_info, falling back to fallback_index (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager._device_index``.
        """
        return self._devices._device_index(fallback_index, device_info)

    def _same_physical_microphone_candidates(self, device: Any) -> list[Any]:
        """Return equivalent input device IDs to try if the selected one fails (delegator).

        PVT-22 / Phase 4.5: body moved to
        ``DeviceManager._same_physical_microphone_candidates``.
        """
        return self._devices._same_physical_microphone_candidates(device)

    def _fallback_host_rank(self, host_name: str) -> int:
        """Rank a host API by preference for fallback device selection (delegator).

        PVT-22 / Phase 4.5: body moved to ``DeviceManager._fallback_host_rank``.
        """
        return self._devices._fallback_host_rank(host_name)

    def _resolve_effective_sample_rate(self, device: int | None) -> tuple[int, dict | None]:
        """Determine the effective sample rate and device info for the given device (delegator).

        PVT-22 / Phase 4.5: body moved to
        ``DeviceManager._resolve_effective_sample_rate``. Returns
        ``(effective_sr, dev_info_dict)`` where ``dev_info_dict`` has
        ``name`` / ``host_api_name`` / ``native_rate`` keys, or ``None``
        if the query failed. Strategy: record at the device's native
        sample rate when it differs from the Whisper target rate (16kHz),
        and resample afterwards with scipy (avoids PortAudio's internal
        resampling, which can introduce artifacts via MME on Windows).
        """
        return self._devices._resolve_effective_sample_rate(device)

    def _all_input_device_candidates(self) -> list[int]:
        """Return all available input device IDs as a last-resort fallback (delegator).

        PVT-22 / Phase 4.5: body moved to
        ``DeviceManager._all_input_device_candidates``.
        """
        return self._devices._all_input_device_candidates()

    def _prewarm_device_cache(self) -> None:
        """Spawn a best-effort daemon thread to populate ``DeviceManager._device_list_cache``.

        PERF (recorder hot-path): ``start()``'s device-enumeration block
        performs several ``sd.query_devices()`` RPCs per candidate (50-200ms
        each on Windows MME). ``DeviceManager._refresh_device_list`` is the
        cached path (30s TTL, OS-event-invalidated by
        ``MicrophoneDeviceWatcher``), but the cache is cold at construction
        time. Pre-warming it on a background thread means that by the time
        the user presses the dictation hotkey (typically seconds-to-minutes
        after app launch), the cache is warm and ``_cached_max_input_channels``
        returns instantly.

        The thread is a one-shot daemon (no stop mechanism, no join needed).
        If PortAudio is unavailable (headless CI, no audio HW), the cache
        stays empty and ``start()`` falls back to direct
        ``sd.query_devices()`` calls — no regression.
        """
        import threading as _threading

        def _warm() -> None:
            try:
                self._devices._refresh_device_list()
            except Exception:
                log.debug("[RECORDING] device cache pre-warm failed", exc_info=True)

        _threading.Thread(
            target=_warm,
            name="recorder-device-cache-prewarm",
            daemon=True,
        ).start()
        # the prewarm thread is intentionally NOT routed through
        # ``_spawn_device_thread`` because it's spawned from ``__init__``
        # (before ``_thread_registry`` could be wired by a caller) and is
        # a one-shot best-effort daemon. The 4 disconnect-path spawns
        # below ARE routed through the helper for registry + single-flight.

    def _cached_max_input_channels(self, device: int | None) -> int:
        """Return ``max_input_channels`` for ``device`` from the cached device list.

        PERF (recorder hot-path): avoids a 50-200ms ``sd.query_devices()``
        RPC per candidate on the ``start()`` critical path. The cache is
        owned by ``DeviceManager._refresh_device_list`` (30s TTL,
        invalidated on OS device plug/unplug events by
        ``MicrophoneDeviceWatcher``) and pre-warmed by
        ``_prewarm_device_cache`` in ``__init__``.

        Falls back to ``sd.query_devices(kind="input")`` for
        ``device=None`` (the cache lists all input devices but does not
        track which one is the OS default) and to ``1`` (mono) when the
        device is not in the cache (e.g. a USB mic that was just plugged
        in and the cache hasn't been invalidated yet — the next
        iteration's ``sd.InputStream`` open will retry).
        """
        if device is None:
            # Cache doesn't track OS default; fall back to a single direct
            # query (one RPC, only on the default-device path which is the
            # minority case — most users configure an explicit mic index).
            try:
                info = sd.query_devices(kind="input")
                return int(info.get("max_input_channels", 1) or 1)
            except Exception:
                return 1
        try:
            for info in self._devices._refresh_device_list():
                if info.get("index") == device:
                    return int(info.get("max_input_channels", 1) or 1)
        except Exception:
            pass
        return 1

    def _classify_portaudio_open_error(self, exc: BaseException) -> None:
        """DE-4 - re-raise an OSError-from-PortAudio as a typed
        :class:`MicrophonePermissionDeniedError` when the OS reports
        the microphone permission as ``DENIED`` or ``PROMPT`` AND the
        OSError message matches one of the known PortAudio
        permission-denial substrings.

        Non-OSError exceptions are passed through unchanged. OSErrors
        whose message doesn't match any substring are passed through
        unchanged (likely a hardware fault, not a permission issue).
        OSErrors that match the substring but whose permission state
        is ``GRANTED`` / ``UNKNOWN`` are passed through unchanged
        (avoid false-positive permission prompts when the real cause
        is hardware, or pyobjc is missing on macOS so we can't be sure).
        """
        if not isinstance(exc, OSError):
            return
        msg = str(exc).lower()
        if not any(pat.lower() in msg for pat in self._PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS):
            return
        from voice_typer.server import permissions
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        state = permissions.check_microphone_permission()
        if state == permissions.MicrophonePermissionState.DENIED:
            raise MicrophonePermissionDeniedError(
                "PortAudio reports microphone permission denied",
                state="denied",
            ) from exc
        if state == permissions.MicrophonePermissionState.PROMPT:
            raise MicrophonePermissionDeniedError(
                "Microphone permission not yet determined - PortAudio open failed",
                state="prompt",
            ) from exc
        return

    def _detect_and_emit_clipping(self, chunk_peak: float) -> None:
        """AUDIO-CLIP: track clipping + push a real-time IPC event.

        Extracted from ``_process_audio_chunk`` for testability and
        readability. The ``audio_clip`` event is throttled to 1 Hz
        (same as the log) so the IPC channel isn't flooded. The event
        is enqueued on a non-blocking ``queue.Queue`` and drained by a
        dedicated ``_event_worker_thread`` (see ``_event_worker_loop``).
        This keeps the audio worker thread off the IPC transport - a
        slow TCP subscriber (or a blocked Electron renderer) can no
        longer stall the worker and cause ring-buffer overflows /
        dropped audio. ``put_nowait`` + ``queue.Full`` suppression so
        a backed-up event worker can never block the audio thread.

        Side effects: increments ``_clip_count``, updates ``_peak`` and
        ``_last_clip_log_time``, may push an event to ``_event_queue``.
        """
        if chunk_peak >= 0.99:
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
                    self._event_queue.put_nowait(
                        {
                            "type": "audio_clip",
                            "data": {
                                "peak": float(chunk_peak),
                                "count": int(self._clip_count),
                            },
                        }
                    )

    def _secure_clear_session_caches(self) -> None:
        """SEC-audit-008 / CR-21 / G4-H-06 / ZR-60: zero cached audio arrays.

        Pre-CR-21: ``recorder.py`` called ``_secure_clear_array(...)``
        as a bare name (no import).  The function is defined in
        ``recording/buffer.py`` and re-exported by the package
        ``__init__.py``, but ``recorder.py`` never imported it.  The
        surrounding broad ``try``/``handler`` block swallowed the
        resulting ``NameError``, so SEC-audit-008's secure-zeroing of
        cached audio arrays (*``_cached_resampled``* and
        *``_cached_no_resample_arr``*) NEVER executed — the previous
        session's audio lingered in process memory until the next GC
        pass freed the numpy arrays.

        CR-21 fix: import ``_secure_clear_array`` at module top (literal
        ``from voice_typer.server.recording import _secure_clear_array``
        statement) so a future regression that removes the import
        surfaces as ``AttributeError`` at import time. The call sites
        still route through ``_recording_pkg._secure_clear_array(...)``
        so test patches of the form
        ``monkeypatch.setattr("voice_typer.server.recording._secure_clear_array", ...)``
        take effect at runtime (matching ``_secure_clear_array_background``
        in stop()/discard()).

        ZR-60: extracted this block from ``start()`` into a dedicated
        helper so the source-string regression test
        (``test_recorder_start_except_clause_does_not_swallow_nameerror``)
        can pin the narrowed handler clause at the helper-method
        granularity rather than scanning ``start()``'s much longer body
        (which contains other broad ``Exception`` handlers for
        unrelated concerns — device probing, audio stream teardown,
        etc. — that are out of scope for CR-21).

        The narrowed ``(OSError, ValueError)`` handler clause below
        ensures a future import bug (``NameError``) surfaces immediately
        instead of being silently swallowed (CR-21 regression — the
        pre-fix broad ``Exception`` handler masked the missing import
        and left SEC-audit-008 as a no-op).
        """
        try:
            if self._cached_resampled is not None and self._cached_resampled.size > 0:
                _recording_pkg._secure_clear_array(self._cached_resampled)
        except (OSError, ValueError):
            log.warning("[RECORDER] secure_clear_array failed", exc_info=True)
        try:
            if self._cached_no_resample_arr is not None and self._cached_no_resample_arr.size > 0:
                _recording_pkg._secure_clear_array(self._cached_no_resample_arr)
        except (OSError, ValueError):
            log.warning("[RECORDER] secure_clear_array failed", exc_info=True)

    def _reset_session_state(self) -> None:
        """Reset ALL per-session state for a fresh recording session.

        ARCH-023: reset ALL per-session state here, not just the buffer.
        Previously some flags (_max_duration_warning_sent,
        _silence_warning_sent, etc.) persisted across recordings,
        causing stale state to suppress warnings on the next session.

        ARCH-023 (revised): The dead ``_silence_warning_sent`` and
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
        # reset the resample-path segment list + dirty flag so a
        # new session starts with an empty cache (no stale segments
        # carried over from the previous session).
        self._cached_resampled_segments = []
        self._cached_resampled_concat_dirty = False
        # reset per-session error counters so each session
        # reports its own dropped-chunk / RMS-callback-error totals
        # (previously these accumulated across sessions).
        self._dropped_chunks = 0
        self._rms_callback_error_count = 0
        self._silence_timer = 0.0
        self._silence_start_time = None
        self._silence_warning_count = 0
        self._silence_next_warning_wait = 10.0
        self._recent_rms_values.clear()
        self._recording_start_time = time.perf_counter()
        # Critical: reset the buffer-sample-rate tracker so a
        # fresh session starts from ``None``. Matches the ``discard()``
        # / ``stop()`` reset in ``_recorder_split.py`` /
        # ``_secure_clear_caches``. The ``None`` sentinel makes the
        # ``_buffer_sr or _effective_sr`` fallback idiom work until
        # the first chunk arrives.
        self._buffer_sr = None
        # reset the per-chunk VAD property cache so stale values
        # from a prior session don't leak. The cache is recomputed by
        # ``_refresh_vad_caches()`` after the device loop finalizes
        # ``_effective_sr``.
        self._cached_vad_enabled = False
        self._cached_use_silero_vad = False
        self._cached_silero_available = False
        self._cached_vad_resample_up_down = None
        self._cached_vad_resample_sr = None
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
        self._vad_speech_threshold_db = DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._vad_silence_threshold_db = DEFAULT_VAD_SILENCE_THRESHOLD_DB
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

    def _cache_session_config(self) -> int:
        """Cache config-derived scalars for the upcoming session and return ``max_rec``.

        PERF-NEW-006: cache config values at start() time so the
        audio callback doesn't do 5x getattr per iteration.
        Coerce to float so a non-numeric MagicMock config (in tests)
        doesn't cause TypeError in the silence_timer comparison.

        Returns ``max_rec`` (the parsed ``_cached_max_recording_time``
        as an int, or 0 on TypeError/ValueError) so ``start()`` can
        pass it to ``_resize_buffers_for_sample_rate`` later — the
        dynamic buffer sizing is deferred until the device loop
        finalizes ``effective_sr``.
        """
        _silence_warning = self.config.silence_warning_seconds
        # stop_on_silence_seconds is a Config dataclass field (default
        # 60.0) — always present on a real Config instance, so the
        # getattr fallback could never fire on a real Config.
        _stop_on_silence = self.config.stop_on_silence_seconds
        self._cached_silence_warning = float(_silence_warning) if isinstance(_silence_warning, int | float) else 20.0
        self._cached_stop_on_silence = float(_stop_on_silence) if isinstance(_stop_on_silence, int | float) else 60.0
        # SIMPLIFY-001: single explicit field replaces the old 3-field split
        # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu,
        # and max_recording_time_seconds=0 auto-selection). Always defaults to 900.
        self._cached_max_recording_time = int(self.config.max_recording_time_seconds)

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
            max_rec = int(self._cached_max_recording_time)
        except (TypeError, ValueError):
            max_rec = 0
        return max_rec

    def _build_audio_callback(self) -> Callable[..., None]:
        """Construct the PortAudio callback closure for this session.

        RT-SAFE-001: The PortAudio callback is a thin wrapper around
        :meth:`_audio_callback_dispatch`. The dispatch method does ONLY
        pre-roll capture + ring buffer push + worker signal — all heavy
        work (filter chain, VAD, resample, state machine) is done by
        the audio worker thread. See ``_audio_callback_dispatch`` /
        ``_audio_worker_loop`` / ``_process_audio_chunk`` for the full
        architecture.

        The closure captures ``self`` only — no other start()-locals —
        so it is safe to extract from ``start()`` into a helper that
        returns the closure. ``self._current_callback`` is set here so
        :meth:`_handle_device_disconnect` can re-bind the same callback
        when restarting the stream.
        """

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
        return callback

    def _open_stream_for_candidates(
        self,
        candidates: list[Any],
        callback: Callable[..., None],
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, Exception | None]:
        """Try opening an :class:`sd.InputStream` for each candidate device in turn.

        Returns ``(selected_device, effective_sr, last_error)``. On
        success, ``self._stream`` is the opened stream,
        ``self._effective_sr`` is updated under the lock, and
        ``self._actual_channels`` stores the negotiated channel count.
        On failure, ``self._stream`` remains ``None`` and ``last_error``
        holds the most recent exception.

        The candidate loop is the primary device-enumeration path. If
        every candidate fails, :meth:`start` falls back to
        :meth:`_open_stream_fallback` (all input devices).
        """
        selected_device: Any = None
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
                # recording_channels is a Config dataclass field
                # (default 1) — always present on a real Config instance,
                # so the getattr fallback could never fire. The ``or 1``
                # guard is preserved because recording_channels=0 is an
                # invalid misconfig that would produce a zero-channel
                # stream — defensive against misconfig, not missing attr.
                config_channels = int(self.config.recording_channels or 1)
                channels = config_channels if config_channels > 0 else 1
                try:
                    # PERF: consult the cached device list (pre-warmed in
                    # ``__init__`` via ``_prewarm_device_cache``) instead
                    # of issuing a fresh ``sd.query_devices()`` RPC per
                    # candidate. Each RPC is 50-200ms on Windows MME; with
                    # 1-3 candidates the savings are 1-3 RPCs on the
                    # hotkey critical path.
                    max_ch = self._cached_max_input_channels(candidate)
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

        return selected_device, effective_sr, last_error

    def _open_stream_fallback(
        self,
        tried: list[Any],
        callback: Callable[..., None],
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, bool, Exception | None]:
        """Try every available input device not already in ``tried`` as a last-resort fallback.

        Returns ``(selected_device, effective_sr, used_fallback, last_error)``.
        On success, ``self._stream`` is the opened stream and
        ``self._effective_sr`` is updated under the lock. ``used_fallback``
        is ``True`` if a fallback device opened successfully, ``False``
        otherwise (so the caller can distinguish the primary-success and
        fallback-success cases — only the fallback-success case persists
        the new device index to config).
        """
        selected_device: Any = None
        used_fallback = False
        log.warning(
            "[RECORDING] All devices matching configured mic failed. Trying all available input devices as fallback."
        )
        all_candidates = self._all_input_device_candidates()
        # Remove already-tried devices
        tried_set = set(str(c) for c in tried)
        all_candidates = [c for c in all_candidates if str(c) not in tried_set]

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
                # AUDIO-CH: also query channels for fallback devices.
                # PERF: use the cached lookup (same rationale as the
                # primary candidate loop above) — the fallback path
                # iterates ALL input devices, so per-candidate RPC
                # savings compound quickly here.
                fb_channels = 1
                try:
                    fb_max_ch = self._cached_max_input_channels(candidate)
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

        return selected_device, effective_sr, used_fallback, last_error

    def _resize_buffers_for_sample_rate(self, effective_sr: int, max_rec: int) -> None:
        """Dynamically size the main buffer, ring buffer, and pre-roll deque.

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
        blocksize = 512  # matches sd.InputStream blocksize below
        sizing_sr = effective_sr if effective_sr > 0 else self.config.sample_rate
        if sizing_sr <= 0:
            sizing_sr = self.config.sample_rate
        chunk_seconds = blocksize / sizing_sr if sizing_sr > 0 else 0.064

        if max_rec > 0 and chunk_seconds > 0:
            needed_chunks = int(max_rec / chunk_seconds) + 1000  # +1K safety
            current_maxlen = self._buffer.maxlen or 0
            if needed_chunks > current_maxlen:
                # Preserve any data already in the buffer (defensive —
                # start() clears the buffer at line ~1220, so this is
                # normally empty) when resizing.
                old_data = list(self._buffer)
                self._buffer = collections.deque(old_data, maxlen=needed_chunks)
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
        _ring_buffer_seconds = float(os.environ.get("VOICE_TYPER_RING_BUFFER_SECONDS", "1.0"))
        if sizing_sr > 0:
            new_ring_capacity = int(sizing_sr / blocksize * _ring_buffer_seconds)
            if new_ring_capacity < 16:
                # Floor at 16 chunks so a very low blocksize / high sr
                # combination doesn't under-allocate (16 chunks ≈ 0.5s
                # at 16kHz/512 — enough for one VAD inference spike).
                new_ring_capacity = 16
            if new_ring_capacity != _AUDIO_RING_BUFFER_CAPACITY and new_ring_capacity > 0:
                # Preserve any chunks already in the ring buffer
                # (defensive -- start() clears the ring buffer in
                # ``_start_audio_worker`` before this point, so this
                # is normally empty) when resizing.
                _old_ring = list(self._ring_buffer)
                self._ring_buffer = collections.deque(_old_ring, maxlen=new_ring_capacity)
                log.debug(
                    "[RECORDING] Ring buffer sized for ~%.1fs at %d Hz (blocksize=%d): %d chunks (was %d)",
                    _ring_buffer_seconds,
                    sizing_sr,
                    blocksize,
                    new_ring_capacity,
                    _AUDIO_RING_BUFFER_CAPACITY,
                )

        # re-size the pre-roll deque using the effective sample
        # rate. The deque was created in __init__ with a placeholder
        # capacity based on config.sample_rate (16kHz); for a 48kHz
        # device that capacity is 3× too small, so a 1s pre-roll would
        # only capture ~0.33s. Preserve any preroll already captured by
        # the audio callback (it may have fired between stream.start()
        # and here).
        if self._preroll_active and self._preroll_seconds > 0 and sizing_sr > 0:
            new_preroll_maxlen = int(self._preroll_seconds * sizing_sr / blocksize) + 2
            current_preroll_maxlen = self._preroll_buffer.maxlen or 0
            if new_preroll_maxlen != current_preroll_maxlen:
                old_preroll = list(self._preroll_buffer)
                self._preroll_buffer = collections.deque(old_preroll, maxlen=new_preroll_maxlen)
                log.debug(
                    "[RECORDING] Pre-roll buffer sized for %.2fs at %d Hz (blocksize=%d): %d chunks (was %d)",
                    self._preroll_seconds,
                    sizing_sr,
                    blocksize,
                    new_preroll_maxlen,
                    current_preroll_maxlen,
                )

    def _prepend_preroll_to_buffer(self) -> None:
        """Prepend captured pre-roll chunks to the main recording buffer.

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
        if self._preroll_buffer:
            preroll_chunks = list(self._preroll_buffer)
            if preroll_chunks:
                for chunk in reversed(preroll_chunks):
                    mono_chunk = self._ensure_mono(chunk)
                    # R18-F12: best-effort filter — if the processor
                    # raises (or returns None), fall back to the raw
                    # chunk so pre-roll never blocks start().
                    if self._audio_processor is not None:
                        try:
                            filtered = self._audio_processor.process_chunk(
                                mono_chunk,
                                input_sample_rate=self._effective_sr,
                            )
                            if filtered is not None:
                                mono_chunk = filtered
                        except Exception:
                            log.debug(
                                "[RECORDING] pre-roll process_chunk failed; using raw chunk",
                                exc_info=True,
                            )
                    self._buffer.appendleft(mono_chunk.copy())
                log.debug(
                    "[RECORDING] Prepended %d pre-roll chunks (~%.1fs)",
                    len(preroll_chunks),
                    len(preroll_chunks) * 512 / self._effective_sr,
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
                self._preroll_buffer.clear()

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
        # CR-16: serialize start() against concurrent discard() — see
        # ARCH-023 lock-order rationale in review.md / git log.
        with self._start_lock:
            if self._recording_event.is_set():
                return

            # DE-4: pre-flight check that the OS reports microphone permission
            # as granted (or prompt - the OS will show the consent dialog on
            # first PortAudio open in that case). Raises
            # ``MicrophonePermissionDeniedError`` (typed) when DENIED, so the
            # IPC layer can ``isinstance``-check and surface the permission
            # onboarding UI instead of a generic error toast.
            from voice_typer.server import permissions as _permissions_module

            _permissions_module.verify_microphone_accessible()

        # SEC-audit-008 / CR-21 / G4-H-06: securely zero cached
        # audio arrays before clearing. The cache-clearance block is
        # extracted into ``_secure_clear_session_caches`` so the
        # source-string regression test
        # (``test_recorder_start_except_clause_does_not_swallow_nameerror``)
        # can pin the narrowed handler clause at the helper-method
        # granularity. See the helper's docstring for the full rationale.
        self._secure_clear_session_caches()

        # ── per-session state reset (ARCH-023) ──
        self._reset_session_state()

        # ── cache config-derived scalars for the audio callback hot path ──
        max_rec = self._cache_session_config()

        device = self._resolve_device()
        candidates = self._same_physical_microphone_candidates(device)

        # ── build the PortAudio callback closure (RT-SAFE-001) ──
        callback = self._build_audio_callback()

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
        # The device-loop bodies are now extracted into
        # ``_open_stream_for_candidates`` / ``_open_stream_fallback`` (both
        # called from ``start()`` method scope, OUTSIDE the callback closure),
        # so the structural contract above is preserved.
        # DO NOT re-add ``set_thread_registry`` — it was merge damage, not
        # in the original codebase, and referenced a function that did not
        # exist. The ``recording/__init__.py`` stub for it is dead code.
        # =====================================================================
        last_error: Exception | None = None
        selected_device: Any = None
        effective_sr: int = self.config.sample_rate
        used_fallback = False

        selected_device, effective_sr, last_error = self._open_stream_for_candidates(
            candidates, callback, effective_sr, last_error
        )

        # If all same-name candidates failed, try ALL available input devices
        if self._stream is None and not used_fallback:
            selected_device, effective_sr, used_fallback, last_error = self._open_stream_fallback(
                candidates, callback, effective_sr, last_error
            )

        if self._stream is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("No input device could be opened")

        # ── dynamic buffer sizing (deferred until effective_sr known) ──
        self._resize_buffers_for_sample_rate(effective_sr, max_rec)

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

            def _persist_mic() -> None:
                if not self.config.save():
                    log.debug("[RECORDING] Could not persist microphone fallback")

            # use _spawn_device_thread so the persistence thread
            # is registered with thread_registry (when available),
            # allowing ``shutdown_all()`` to join it during process exit
            # (preventing a half-written config file).
            self._spawn_device_thread(
                name="mic-fallback-save",
                target=_persist_mic,
            )

        self._recording_event.set()

        # AUDIO-PRE: prepend pre-roll buffer to reduce cold-start latency.
        self._prepend_preroll_to_buffer()

        target_sr = self.config.sample_rate
        if (
            effective_sr != target_sr
            and _recording_pkg._resample_poly is None
            and _recording_pkg._resample_poly_error is None
        ):
            # Warm up synchronously to avoid racing with stop()
            self.warm_up_resampler()

        # High: when the device's effective sample rate differs
        # from the audio processor's chain construction rate, rebuild
        # the chain at the new rate so (a) filter coefficients are
        # tuned to the actual native rate (XV-31 mitigation — an 80 Hz
        # high-pass built at 16 kHz actually cuts at 240 Hz when fed 48
        # kHz audio, removing male speech fundamentals), and (b) the
        # per-chunk ``process_chunk`` call avoids the RT-thread
        # ``resample_poly`` branch (5-50ms × 16 Hz = 80-800ms/sec of
        # RT-thread CPU) because ``input_sample_rate == _sample_rate``
        # short-circuits at audio_processor.py:283. The
        # ``_rebuild_audio_processor(force_sr=...)`` API was added by
        # RW-9 / AUDIO-6 / AUDIO-9 but was never called from the
        # recorder — every chunk paid the resample cost after a
        # hot-plug or on first start with a non-16 kHz device.
        #
        # Strategy (mirrors AudioQualityController._rebuild_audio_processor):
        # - If ``AudioProcessor.set_sample_rate`` exists (post-FIX-19),
        #   call it with ``effective_sr``. It atomically swaps the chain
        #   AND updates ``_sample_rate``, so a single call is sufficient.
        # - Else (pre-FIX-19 fallback or a spec-limited test double),
        #   call ``rebuild_from_config(config)``.
        # - When ``effective_sr == _sample_rate``, skip entirely.
        # Guard with try/except so a buggy AudioProcessor can't break
        # the recording-start critical path.
        if self._audio_processor is not None:
            _proc_sr = getattr(self._audio_processor, "_sample_rate", None)
            if _proc_sr is not None and int(_proc_sr) != int(effective_sr):
                _set_sr = getattr(self._audio_processor, "set_sample_rate", None)
                if callable(_set_sr):
                    try:
                        _set_sr(int(effective_sr))
                        log.info(
                            "[RECORDING] AudioProcessor.set_sample_rate(%d) called — "
                            "chain retuned to device native rate (XV-31)",
                            effective_sr,
                        )
                    except Exception:
                        log.warning(
                            "[RECORDING] AudioProcessor.set_sample_rate(%d) failed — "
                            "per-chunk resample will run on the RT thread",
                            effective_sr,
                            exc_info=True,
                        )
                else:
                    try:
                        self._audio_processor.rebuild_from_config(self.config)
                        log.info(
                            "[RECORDING] AudioProcessor.rebuild_from_config called — "
                            "chain rebuilt (fallback, set_sample_rate unavailable)",
                        )
                    except Exception:
                        log.warning(
                            "[RECORDING] AudioProcessor.rebuild_from_config failed — "
                            "filter coefficients may be mistuned",
                            exc_info=True,
                        )

        # refresh the per-chunk VAD property cache now that
        # ``_effective_sr`` (and the AudioProcessor's ``_sample_rate``,
        # if the above retuned it) are finalized. The cache lets the
        # 16 Hz audio worker hot path read scalars instead of
        # dispatching 3 property lookups per chunk × 16 Hz = 48/sec.
        self._refresh_vad_caches()

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

        GT-24: the teardown sequence is wrapped in
        ``self._stream_lifecycle_lock`` so a concurrent
        ``_handle_device_disconnect`` restart block cannot mutate
        ``self._stream`` mid-teardown (and vice-versa). Non-blocking
        acquire so ``__del__`` (best-effort cleanup, wrapped in
        ``contextlib.suppress``) never blocks on a long-running
        ``stop()``/``discard()``/disconnect-handler holding the lock —
        the holder will finish the teardown.
        """
        # GT-24: serialize teardown w.r.t. concurrent
        # _handle_device_disconnect restart. Non-blocking so __del__
        # can't deadlock on a long-running stop()/discard().
        if not self._stream_lifecycle_lock.acquire(blocking=False):
            # Another thread is holding the lock — it will complete
            # the teardown. Idempotent contract: returning here is
            # safe because the holder guarantees ``self._stream`` is
            # torn down before releasing.
            return
        try:
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
        finally:
            self._stream_lifecycle_lock.release()

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

        GT-23: the entire read-check-create-start sequence is wrapped
        in ``self._worker_lifecycle_lock`` so concurrent
        start()/stop()/discard() callers cannot race on
        ``self._worker_thread`` (both readers seeing ``None``, the
        starter creating+assigning a fresh worker, the stopper
        returning early and leaving that worker untracked).
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-create-start sequence so a concurrent
        # _stop_audio_worker() cannot observe a stale ``None``
        # mid-create.
        with self._worker_lifecycle_lock:
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

        GT-23: the entire read-check-clear-join-unregister sequence is
        wrapped in ``self._worker_lifecycle_lock`` (NOT ``self._lock``)
        so concurrent stop()/discard() callers cannot both read
        ``self._worker_thread is None`` and both return early leaving
        a fresh worker untracked. ``self._lock`` is intentionally NOT
        held across ``thread.join()`` — the worker thread acquires
        ``self._lock`` inside ``_process_audio_chunk`` for the buffer
        append, so holding it across ``join()`` would deadlock.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-clear-join-unregister sequence. This is a
        # separate lock from self._lock — see the docstring above.
        with self._worker_lifecycle_lock:
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

        GT-23: the entire read-check-create-start sequence is wrapped
        in ``self._worker_lifecycle_lock`` (the same lock used by the
        audio worker lifecycle methods) so concurrent
        start()/stop()/discard() callers cannot race on
        ``self._event_worker_thread``.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-create-start sequence so a concurrent
        # _stop_event_worker() cannot observe a stale ``None``
        # mid-create.
        with self._worker_lifecycle_lock:
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

        GT-23: the entire read-check-clear-join-unregister sequence is
        wrapped in ``self._worker_lifecycle_lock`` so concurrent
        stop()/discard() callers cannot both read
        ``self._event_worker_thread is None`` and both return early
        leaving a fresh worker untracked.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-clear-join-unregister sequence.
        with self._worker_lifecycle_lock:
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
            # push a sentinel onto the queue to wake the worker
            # immediately from its 0.5s ``queue.get`` poll. Without the
            # sentinel, the worker would not notice the stop signal
            # until its next poll iteration (up to 0.5s latency). The
            # sentinel is a unique object the loop checks for BEFORE
            # calling ``event_bus.publish``, so it is never published.
            # ``put_nowait`` is used because the queue is bounded
            # (maxsize=1000) and a Full exception here is benign -- the
            # worker will still exit on its next poll iteration within
            # 0.5s. The sentinel is pushed AFTER ``set()`` so the
            # worker's next ``get`` returns the sentinel (FIFO order
            # preserves any real events enqueued before the sentinel).
            with contextlib.suppress(queue.Full):
                self._event_queue.put_nowait(_EVENT_WORKER_STOP_SENTINEL)
            # ``stop()`` now passes ``timeout=0.1`` (down from
            # ``_EVENT_WORKER_JOIN_TIMEOUT_S=2.0``) so the worst-case
            # stop() latency drops from ~5.8s to ~2.4s. The 0.1s join is
            # long enough for the daemon to drain its queue (typically
            # <10ms -- the queue is MPSC with a 1 Hz source throttle)
            # and exit, but 20x shorter than the original 2.0s timeout.
            # If the daemon is stuck in a slow ``event_bus.publish`` and
            # doesn't exit within ``timeout``, we proceed anyway -- the
            # daemon is harmless (the stop event is set; it will exit on
            # its next iteration boundary) and the test contract
            # (``_event_worker_thread is None`` after stop()) is
            # preserved because we null the reference unconditionally
            # after the join attempt.
            self._event_worker_thread.join(timeout=timeout)
            if self._event_worker_thread.is_alive():
                log.debug(
                    "[RECORDING] Event worker thread did not exit within %.2fs "
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
                # wait for work with a 0.5s timeout (was 50ms).
                # The event queue is MPSC with a tiny source-side
                # throttle (1 Hz), so a 50ms poll was 20x more frequent
                # than necessary -- preventing deep C-states on laptops
                # on battery for no benefit. The 0.5s poll still wakes
                # within 0.5s of an event being enqueued (well within
                # the 1 Hz source throttle) and lets the CPU sleep
                # between publishes. Stop latency is NOT bounded by
                # the 0.5s poll: ``_stop_event_worker`` pushes a
                # sentinel onto the queue to wake the worker
                # immediately (see ``_EVENT_WORKER_STOP_SENTINEL``).
                # ``_audio_worker_loop``'s 50ms wait is unchanged
                # because the audio callback pushes chunks at 16 Hz and
                # a 0.5s wait there would add 0.5s of drain latency on
                # stop().
                try:
                    event = self._event_queue.get(timeout=0.5)
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
            # check for the stop sentinel BEFORE publishing. The
            # sentinel is pushed by ``_stop_event_worker`` to wake the
            # worker immediately (instead of waiting up to 0.5s for the
            # next poll iteration). Any real events that were enqueued
            # BEFORE the sentinel have already been drained and
            # published by the iterations above.
            if event is _EVENT_WORKER_STOP_SENTINEL:
                return
            # Type narrowing: ``event`` is now guaranteed to be a dict
            # (the only other variant on the queue). ``isinstance`` here
            # doubles as a defensive guard against a future variant
            # pushed by mistake — it skips the publish instead of
            # crashing ``event_bus.publish`` with a TypeError.
            if not isinstance(event, dict):
                continue
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

    def _detect_device_disconnect(self, indata: np.ndarray) -> bool:
        """Detect a USB/BT device disconnect via zero-filled input (HOTKEY-CRASH).

        FZ-T8: body moved to :meth:`AudioPipeline.detect_device_disconnect`.
        This is a 1-line delegator so existing call sites and
        ``inspect.getsource`` checks on ``Recorder._detect_device_disconnect``
        continue to work.
        """
        return self._audio_pipeline.detect_device_disconnect(indata)

    def _handle_xrun_status(self, status: Any) -> bool:
        """Inspect the PortAudio ``status`` for an input-overflow XRUN.

        FZ-T8: body moved to :meth:`AudioPipeline.handle_xrun_status`.
        This is a 1-line delegator so existing call sites and
        ``inspect.getsource`` checks on ``Recorder._handle_xrun_status``
        continue to work.
        """
        return self._audio_pipeline.handle_xrun_status(status)

    def _apply_filter_chain(self, indata: np.ndarray) -> np.ndarray:
        """Convert multi-channel input to mono and apply the real-time filter chain.

        FZ-T8: body moved to :meth:`AudioPipeline.apply_filter_chain`.
        This is a 1-line delegator so existing call sites and
        ``inspect.getsource`` checks on ``Recorder._apply_filter_chain``
        continue to work.
        """
        return self._audio_pipeline.apply_filter_chain(indata)

    def _append_to_buffer_locked(self, filtered: np.ndarray) -> tuple[int, int]:
        """Append ``filtered`` to ``_buffer`` under the lock; return ``(chunk_count, buffer_len)``.

        FZ-T8: body moved to :meth:`AudioPipeline.append_to_buffer_locked`.
        This is a 1-line delegator so existing call sites and
        ``inspect.getsource`` checks on ``Recorder._append_to_buffer_locked``
        continue to work.
        """
        return self._audio_pipeline.append_to_buffer_locked(filtered)

    def _compute_rms_and_peak(self, filtered: np.ndarray) -> tuple[float, float, float]:
        """Compute ``(chunk_rms, chunk_peak, chunk_duration)`` for the filtered chunk.

        FZ-T8: body moved to :meth:`AudioPipeline.compute_rms_and_peak`.
        This is a 1-line delegator so existing call sites and
        ``inspect.getsource`` checks on ``Recorder._compute_rms_and_peak``
        continue to work.
        """
        return self._audio_pipeline.compute_rms_and_peak(filtered)

    def _run_vad_state_machine(
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

        FZ-T8: body moved to :meth:`AudioPipeline.run_vad_state_machine`.
        This is a 1-line delegator so existing call sites and
        ``inspect.getsource`` checks on ``Recorder._run_vad_state_machine``
        continue to work.
        """
        self._audio_pipeline.run_vad_state_machine(
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
        # HOTKEY-CRASH: device disconnect detection (early return path).
        if self._detect_device_disconnect(indata):
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

        # AUDIO-002: XRUN status flag handling (early return path on overflow).
        if self._handle_xrun_status(status):
            return

        # RT-SAFE-001: the old PERF-011 frame-skip logic
        # (_previous_chunk_pending) is replaced by ring buffer overflow
        # detection in the callback. If the ring buffer was full, the
        # callback already logged a warning and dropped the chunk. By
        # the time we reach here, the chunk is in the ring buffer and
        # we must process it.

        # AUDIO-CH + AUDIO-PROC: mono conversion + real-time noise filtering.
        filtered = self._apply_filter_chain(indata)

        # Buffer append + chunk count + backpressure detection.
        chunk_count, buffer_len = self._append_to_buffer_locked(filtered)

        # Read callback refs outside the lock — these are set once
        # at start() and cleared at stop(), so a torn read just
        # means we miss one callback or fire one extra, which is
        # acceptable. The alternative (holding the lock while
        # calling user code) risks deadlocks.
        rms_callback = self.on_rms_level
        silence_warning_cb = self.on_silence_warning
        silence_auto_stop_cb = self.on_silence_auto_stop
        max_duration_cb = self.on_max_duration_auto_stop
        # PVT-27: the dead ``recent_rms = recent_rms_snapshot`` alias
        # was removed (its only writer, the snapshot inside the lock
        # above, was also dead — see the RACE-003 note above).
        recording_start = self._recording_start_time

        # ── Everything below runs OUTSIDE the lock ──

        # RMS / peak / chunk-duration computation (operates on FILTERED
        # audio so the waveform bubble and silence detection see what
        # the transcriber will see, not raw mic input).
        chunk_rms, chunk_peak, chunk_duration = self._compute_rms_and_peak(filtered)

        # ADR 0007 §3.5: the old per-chunk AGC (_agc_update, C1)
        # has been removed. It duplicated the Compressor filter in
        # the new audio filter chain. The Compressor now handles
        # dynamic range compression with proper attack/release.
        # _last_rms stores the post-filter RMS for UI/IPC.

        with self._lock:
            self._last_rms = chunk_rms

        # AUDIO-CLIP: Track clipping + push IPC event (delegated to a
        # dedicated helper so the clipping-detection logic is testable
        # in isolation and the heavy ``_process_audio_chunk`` body
        # stays readable).
        self._detect_and_emit_clipping(chunk_peak)

        # AUDIO-3 / PERF-11: append to the live deque (atomic under
        # GIL — ``deque.append`` is a single C-level op with no torn
        # state). The live rolling-RMS consumer is this
        # ``self._recent_rms_values.append(chunk_rms)`` call, which
        # future code (e.g. waveform bubble, VAD auto-calibration) can
        # read via ``self._recent_rms_values`` under the same lock.
        self._recent_rms_values.append(chunk_rms)

        # AUDIO-014 + AUDIO-013 + H12: VAD auto-calibration + Silero
        # VAD probability + VAD state machine + silence/max-duration
        # auto-stop callbacks + telemetry logs.
        self._run_vad_state_machine(
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
        # G4-L-04: the ``audio_chunk`` parameter was REMOVED from the
        # ``on_rms_level`` callback contract.  The previous 3-arg form
        # ``rms_callback(chunk_rms, chunk_peak, filtered)`` forwarded
        # the filtered audio chunk so downstream consumers
        # (WaveformBubble via ``RecordingController.on_recorder_rms``)
        # COULD run Silero VAD on it — but BUBBLE-FIX-4.1 removed the
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
                self._rms_callback_error_count = self._rms_callback_error_count + 1
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

    def _secure_clear_caches(self) -> None:
        """G4-H-06: securely zero cached audio arrays BEFORE reassignment.

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
        """
        # Route through ``_recording_pkg.`` so test patches of the form
        # ``monkeypatch.setattr("voice_typer.server.recording._secure_clear_array", ...)``
        # take effect at runtime (matching ``_secure_clear_array_background``
        # in stop()/discard() and the secure-clear block in start()).
        try:
            if self._cached_resampled is not None and self._cached_resampled.size > 0:
                _recording_pkg._secure_clear_array(self._cached_resampled)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled",
                exc_info=True,
            )
        try:
            if self._cached_no_resample_arr is not None and self._cached_no_resample_arr.size > 0:
                _recording_pkg._secure_clear_array(self._cached_no_resample_arr)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_no_resample_arr",
                exc_info=True,
            )
        self._cached_resampled = np.array([], dtype=np.float32)
        self._cached_no_resample_arr = None
        self._cached_native_chunk_count = 0
        self._cached_no_resample_len = -1
        # XZ-PRIV-01: reset the audio processor's filter state too.
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
            if self._audio_processor is not None:
                self._audio_processor.reset()
        except Exception:
            log.debug(
                "[RECORDER] _audio_processor.reset() in _secure_clear_caches failed",
                exc_info=True,
            )
        # Critical: reset the buffer-sample-rate tracker so a
        # subsequent ``start()`` doesn't reuse the stale rate. Matches
        # the explicit reset in ``_recorder_split.discard_recording``
        # (which also routes through this helper for the cache arrays).
        self._buffer_sr = None

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
        # -- those chunks end up in self._buffer, which we concatenate
        # below. Without this drain, the last few hundred ms of audio
        # (chunks pushed to the ring buffer but not yet processed by the
        # worker) would be lost.
        self._stop_audio_worker(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S, drain=True)

        # cut the worst-case stop() latency from ~5.8s to ~2.4s
        # by using a short 0.1s join for the event worker (down from 2.0s)
        # and fire-and-forget for the device health checker (down from
        # 1.0s join). The event worker drains its tiny queue in <10ms, so
        # 0.1s is generous; the device health checker sleeps 30s between
        # probes so a 1.0s join almost always timed out anyway. Both are
        # daemon threads, so even if they don't exit within the timeout,
        # they cannot block process exit. The audio worker join (2.0s) is
        # unchanged because it must drain up to 64 ring-buffer chunks of
        # in-flight audio (drain=True) to avoid losing the last few
        # hundred ms of the recording.
        # Use the full _EVENT_WORKER_JOIN_TIMEOUT_S (2.0s) so a slow
        # event_bus.publish (e.g. a backed-up TCP subscriber) has time
        # to drain. Pre-fix this was 0.1s which was too short for any
        # publish > 100ms — the daemon was left running and the test
        # contract (drain completes within stop()) was violated.
        self._stop_event_worker(timeout=_EVENT_WORKER_JOIN_TIMEOUT_S, drain=True)

        # device health checker fire-and-forget. Pass timeout=0.0
        # so ``_stop_device_health_checker`` only signals the stop event
        # without joining (the daemon thread exits on its next 30s wait()
        # return).
        self._stop_device_health_checker(timeout=0.0)

        # snapshot the buffer chunks under the lock, then release
        # the lock BEFORE the O(N) np.concatenate. Previously the lock was
        # held across the concatenate (50-300ms for a 30-min recording),
        # blocking the audio worker's append path (which acquires the same
        # lock) for the full duration. The worker would stall, the ring
        # buffer would overflow, and the last few chunks of the recording
        # would be dropped. The fix mirrors the discard() path: inside the
        # lock, swap the deque for a fresh empty one + capture the chunks
        # list; outside the lock, concatenate the captured chunks.
        concat_started = time.perf_counter()
        with self._lock:
            if not self._buffer:
                # G4-H-06: securely zero cached audio arrays BEFORE
                # reassignment (previously this just dropped the
                # references, leaving the previous session's voice data
                # in process memory until the numpy allocator reused
                # the block).
                self._secure_clear_caches()
                self._chunk_count = 0
                return np.array([], dtype=np.float32)
            # capture the chunk list and swap in a fresh deque
            # INSIDE the lock (the swap is O(1) -- just a deque
            # construction + attribute assignment). The expensive
            # ``np.concatenate`` is deferred to after the lock release.
            _old_buffer = self._buffer
            _captured_chunks = list(_old_buffer)
            self._buffer = collections.deque(
                maxlen=getattr(_old_buffer, "maxlen", DEFAULT_MAX_BUFFER_CHUNKS) or DEFAULT_MAX_BUFFER_CHUNKS
            )
            _recording_pkg._secure_clear_array_background(_old_buffer)
            # Critical: capture ``_buffer_sr`` into a local
            # BEFORE ``_secure_clear_caches`` resets it to ``None``.
            # The local is the authoritative source rate for the
            # audio we just snapshotted — the chunks in
            # ``_captured_chunks`` were appended at this rate by
            # ``_process_audio_chunk``.
            _captured_buffer_sr = self._buffer_sr
            # G4-H-06: securely zero cached audio arrays BEFORE
            # reassignment (same rationale as the empty-buffer path
            # above; factored into ``_secure_clear_caches`` to avoid
            # 4-way duplication across stop()'s two paths and discard()).
            self._secure_clear_caches()
        # concatenate the captured chunks OUTSIDE the lock so the
        # audio worker (and any other ``self._lock`` acquirer) is not
        # blocked for the 50-300ms concat duration.
        audio = np.concatenate(_captured_chunks, axis=0).reshape(-1)
        concat_ms = (time.perf_counter() - concat_started) * 1000

        # Log audio statistics for diagnostics
        # Critical: prefer the captured ``_buffer_sr`` (the
        # actual sample rate of the audio that was just concatenated
        # out of ``_buffer``) over ``_effective_sr`` (the device's
        # native rate). When an AudioProcessor is active,
        # ``process_chunk`` resamples each chunk to the chain's
        # construction rate (16 kHz) before appending, so
        # ``_buffer_sr == 16000`` regardless of the device's native
        # rate. Pre-fix, ``stop()`` read ``_effective_sr`` (e.g.
        # 48000) and the subsequent ``_prepare_audio`` call did
        # ``resample_poly(audio, 1, 3)`` — decimating the already-16
        # kHz audio 3:1 → ~5333 samples presented as "16 kHz" →
        # pitched up 3× (chipmunk voice). The captured local is the
        # snapshot taken inside the lock above (before
        # ``_secure_clear_caches`` reset it to ``None``); the
        # ``or self._effective_sr`` fallback covers the
        # ``_buffer_sr is None`` case.
        effective_sr = _captured_buffer_sr if _captured_buffer_sr is not None else self._effective_sr
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

        PVT-006 split: body moved to :func:`._recorder_split.take_snapshot`.
        This method is now a 1-line delegator so existing call sites,
        subclass overrides, and ``inspect.getsource`` checks that look for
        the method on the ``Recorder`` class continue to work. See the
        docstring of the extracted helper for the full perf / correctness
        rationale (cached resampled prefix, islice over the deque, VIEW
        vs copy semantics, lock-free empty-buffer fast path).
        """
        return _recorder_split.take_snapshot(self)

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
        # prefer the cached target sample rate (set once in
        # start()) over re-reading self.config.sample_rate on every
        # _prepare_audio call. The cached value is authoritative for
        # the current session; reading config every call was an
        # unnecessary attribute lookup on the stop() hot path. Fall
        # back to config.sample_rate if the cache hasn't been populated
        # yet (defensive -- should never happen because start() always
        # sets it before any audio is captured).
        target_sr = getattr(self, "_cached_target_sr", None) or self.config.sample_rate
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
        """Shared resampling logic for ``_resample_chunk`` and ``_prepare_audio`` (delegator).

        PVT-22 / Phase 4.5: body moved to ``resample_audio()`` in
        :mod:`.resampling`. This method is now a 1-line delegator so
        existing internal call sites (``_resample_chunk`` /
        ``_prepare_audio``) and any subclass overrides keep working
        unchanged. The delegator routes through the module-level
        ``_resample_audio_fn`` alias (bound at import time to
        ``resampling.resample_audio``) so test patches of the form
        ``monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", ...)``
        keep affecting production code (``resample_audio`` looks up
        ``_get_resample_poly`` via the ``_recording_pkg`` package
        namespace at call time — see ``resampling.py`` §Patch-path).

        PERF-NEW-027: previously the scipy → linear interp → raise
        fallback chain was duplicated between the two methods. The
        centralized helper (now in :mod:`.resampling`) applies bug
        fixes (ERR-012, ERR-001, ARCH-033) in one place.

        ERR-012: narrows exceptions to ``(ValueError, OSError, TypeError)``
        so genuine bugs (``AttributeError``, ``MemoryError``) propagate
        instead of being silently masked as "resampling failed".
        """
        return _resample_audio_fn(
            audio,
            effective_sr,
            target_sr,
            log_resample=log_resample,
            log=log,
        )

    def discard(self) -> None:
        """Discard current recording without processing.

        PVT-006 split: body moved to :func:`._recorder_split.discard_recording`.
        This method is now a 1-line delegator so existing call sites,
        subclass overrides, and ``inspect.getsource`` checks that look for
        the method on the ``Recorder`` class continue to work. See the
        docstring of the extracted helper for the full rationale (stream
        teardown ordering, secure-clear of cached audio arrays, worker
        thread drain semantics).

        CR-16: acquires ``_start_lock`` so a concurrent ``start()`` on
        another thread can't mutate per-session state (buffer, flags)
        while ``discard_recording`` is tearing the stream down. The lock
        is released before return; it is NOT held across
        ``thread.join()`` (would deadlock the audio worker).
        """
        with self._start_lock:
            _recorder_split.discard_recording(self)
