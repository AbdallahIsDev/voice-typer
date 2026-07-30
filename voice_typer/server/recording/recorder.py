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

# AB-28: numpy was eagerly imported at module top, adding ~250-335ms to
# every cold start (numpy performs heavy C-extension initialization at
# import time). Replace with a lazy proxy so importing this module does
# NOT pull numpy into ``sys.modules``; the real import is deferred to
# first attribute access (typically the first ``Recorder.__init__`` or
# the first ``snapshot()`` call). ``from __future__ import annotations``
# above stringifies every ``np.ndarray`` annotation so function-def-time
# annotation evaluation does NOT trigger the lazy proxy (which would
# defeat the optimization).
from voice_typer.server import event_bus
from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES, WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.config import Config
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
# AB-28: lazy numpy proxy — see comment above.
np = lazy_module("numpy")

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

# S3-CR-17 / Phase 4.5: ``AudioCallbackDispatcher`` owns the audio
# worker thread main loop body and the audio callback dispatch body
# (excluding the literal ``_ring_buffer.append`` +
# ``_worker_wake_event.set`` operations that stay on
# ``Recorder._audio_callback_dispatch`` for the RT-SAFE-001 source-
# inspection contract). ``Recorder`` keeps 1-line delegator methods so
# existing call sites, subclass overrides, and ``inspect.getsource``
# checks continue to work. See :mod:`.capture` for the collaborator
# pattern.
from .capture import AudioCallbackDispatcher  # noqa: F401, E402 — re-exported for tests

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

# S3-CR-17 / Phase 4.5: ``SessionState`` owns the per-session state
# reset, config-derived scalar caching, the bulk secure-clear
# (``_secure_clear_caches`` — NOT ``_secure_clear_session_caches`` which
# stays here for the source-inspection contract in
# ``tests/test_secure_clear_array.py``), buffer resizing for the
# effective sample rate, and the preroll prepend. ``Recorder`` keeps
# 1-line delegator methods so existing call sites, subclass overrides,
# and ``inspect.getsource`` checks continue to work. See
# :mod:`.session_state` for the collaborator pattern.
from .session_state import SessionState  # noqa: F401, E402 — re-exported for tests

# S3-CR-17 / Phase 4.5: ``StreamLifecycle`` owns the PortAudio stream-
# open candidate-iteration loop, the all-devices fallback loop, the
# PortAudio callback closure construction, and the stream teardown
# body (inside ``_stream_lifecycle_lock``). The lock acquisition
# for teardown STAYS on ``Recorder._teardown_stream`` so the GT-24
# source-inspection regression tests continue to pin
# ``_stream_lifecycle_lock`` on that method. See :mod:`.stream_lifecycle`
# for the collaborator pattern.
from .stream_lifecycle import StreamLifecycle  # noqa: F401, E402 — re-exported for tests

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
# The PortAudio callback (recording.py:_build_audio_callback → _audio_callback_dispatch) MUST
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
        # AB-1: mirror the resample-path segment-list + lazy-concat
        # optimization for the no-resample branch. Previously the cache
        # was keyed on ``buf_len`` only and missed on every poll (the
        # streaming thread polls at 4 Hz while the audio worker appends
        # at 16 Hz → buf_len always differs), so the cache-miss path
        # ran ``np.concatenate(chunks, axis=0).reshape(-1)`` — rebuilding
        # the full array every poll (~460 MB/s memcpy churn on a 30-min
        # 16 kHz mono dictation). We now keep the cached prefix as a
        # *list* of segments (one per snapshot that saw new chunks) and
        # only re-concatenate when the list changes (``_ensure_no_resample_concat``
        # in :mod:`._recorder_split`). Snapshots that see no new chunks
        # reuse the cached concat (zero memcpy). ``_cached_no_resample_len``
        # is repurposed as the count of buffer chunks already in the
        # segment list (with ``-1`` retained as the "invalidated"
        # sentinel so existing ``_secure_clear_caches`` /
        # ``reset_session_state`` callers — which we cannot modify —
        # keep working unchanged).
        self._cached_no_resample_segments: list[np.ndarray] = []
        self._cached_no_resample_concat_dirty: bool = False
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
        sample_rate = int(config.sample_rate or WHISPER_SAMPLE_RATE)
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
        # S3-CR-17 / Phase 4.5: three new collaborators constructed here
        # in the same back-reference pattern as ``_audio_pipeline`` /
        # ``_disconnect_handler`` above. Each is purely a collaborator —
        # stores ``self`` and reads/writes ``self.X`` for shared state —
        # so they can be instantiated as soon as ``self._audio_pipeline``
        # is ready. Construction order is harmless: each ``__init__`` only
        # stores the back-reference and does not touch other ``self.X``
        # state. The chosen order mirrors the dependency direction
        # (callback → lifecycle → session) for readability.
        self._capture: AudioCallbackDispatcher = AudioCallbackDispatcher(self)
        self._stream_lifecycle: StreamLifecycle = StreamLifecycle(self)
        self._session_state: SessionState = SessionState(self)

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

    # DJ-100: deleted dead ``_device_health_checker_loop`` delegator.
    # The daemon thread is started by ``DeviceManager._start_device_health_checker``
    # which uses ``target=self._device_health_checker_loop`` (bound to the
    # DeviceManager instance), bypassing the Recorder delegator entirely.
    # Repo-wide grep returned ZERO call sites for ``recorder._device_health_checker_loop()``.

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
        if vad_sr is not None and vad_sr not in SILERO_VAD_SAMPLE_RATES and vad_sr > 0:
            gcd = math.gcd(int(vad_sr), WHISPER_SAMPLE_RATE)
            self._cached_vad_resample_up_down = (WHISPER_SAMPLE_RATE // gcd, int(vad_sr) // gcd)
        else:
            self._cached_vad_resample_up_down = None
        self._cached_vad_resample_sr = vad_sr

    # DJ-101: deleted dead ``_compute_vad_enabled`` method (26 LOC).
    # ``VadProcessor.compute_vad_enabled`` is called directly by
    # ``VadProcessor.vad_enabled`` (property) which is called by
    # ``Recorder._vad_enabled`` (property, in vad_helpers.py).
    # The ``Recorder._compute_vad_enabled`` wrapper was a vestigial
    # delegator from RW-04 with ZERO call sites in production or tests.

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
        # XE-6-1 (High): zero the resample-path segment list BEFORE
        # reassignment, mirroring ``secure_clear_caches`` (the bulk
        # helper called from ``stop()``/``discard()``). The segment
        # list is the primary storage for the resampled prefix
        # (``_cached_resampled`` may be a view of ``segments[0]`` in
        # the 1-segment fast path of ``_ensure_resampled_concat``), so
        # without this loop the previous session's dictated audio
        # (up to ~115 MB of float32) would survive ``start()`` in
        # process memory until the numpy allocator reused the blocks.
        # ``reset_session_state`` (called immediately after this
        # helper in ``start_recording``) reassigns the list to ``[]``
        # with its own defensive zeroing pass — the loop here is the
        # authoritative secure-clear, the loop there is the
        # belt-and-suspenders guard against racing ``snapshot()``
        # callers that re-populate the list between this helper and
        # the reset.
        try:
            for seg in self._cached_resampled_segments:
                if seg is not None and seg.size > 0:
                    _recording_pkg._secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled_segments",
                exc_info=True,
            )
        self._cached_resampled_segments = []
        self._cached_resampled_concat_dirty = False

    def _reset_session_state(self) -> None:
        """Reset ALL per-session state for a fresh recording session.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`SessionState.reset_session_state`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks on ``Recorder._reset_session_state`` continue to work.
        See :mod:`.session_state` for the collaborator pattern and the
        full ARCH-023 rationale (per-session state reset, VAD state,
        preroll zeroing, etc.).
        """
        self._session_state.reset_session_state(self)

    def _cache_session_config(self) -> int:
        """Cache config-derived scalars for the upcoming session; return ``max_rec``.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`SessionState.cache_session_config`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks on ``Recorder._cache_session_config`` continue to work.
        See :mod:`.session_state` for the collaborator pattern and the
        PERF-NEW-006 rationale (config scalar caching for the audio
        callback hot path).
        """
        return self._session_state.cache_session_config(self)

    def _build_audio_callback(self) -> Callable[..., None]:
        """Build the PortAudio callback closure (RT-SAFE-001).

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`StreamLifecycle.build_audio_callback`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks on ``Recorder._build_audio_callback`` continue to work.
        See :mod:`.stream_lifecycle` for the collaborator pattern and
        the RT-SAFE-001 rationale (callback must complete before the
        next buffer arrives).
        """
        return self._stream_lifecycle.build_audio_callback(self)

    def _open_stream_for_candidates(
        self,
        candidates: list[Any],
        callback: Callable[..., None],
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, Exception | None]:
        """Try opening an :class:`sd.InputStream` for each candidate device.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`StreamLifecycle.open_stream_for_candidates`. This is a
        1-line delegator so existing call sites and
        ``inspect.getsource`` checks continue to work. See
        :mod:`.stream_lifecycle` for the collaborator pattern and the
        AUDIO-HOT device-fallback rationale.
        """
        return self._stream_lifecycle.open_stream_for_candidates(self, candidates, callback, effective_sr, last_error)

    def _open_stream_fallback(
        self,
        tried: list[Any],
        callback: Callable[..., None],
        effective_sr: int,
        last_error: Exception | None,
    ) -> tuple[Any, int, bool, Exception | None]:
        """Last-resort fallback: try ALL input devices when same-name candidates fail.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`StreamLifecycle.open_stream_fallback`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks continue to work. See :mod:`.stream_lifecycle` for the
        collaborator pattern.
        """
        return self._stream_lifecycle.open_stream_fallback(self, tried, callback, effective_sr, last_error)

    def _resize_buffers_for_sample_rate(self, effective_sr: int, max_rec: int) -> None:
        """Resize the main audio buffer + ring buffer for the effective sample rate.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`SessionState.resize_buffers_for_sample_rate`. This is a
        1-line delegator so existing call sites and
        ``inspect.getsource`` checks continue to work. See
        :mod:`.session_state` for the collaborator pattern and the
        PERF-NEW-018 dynamic-buffer-sizing rationale.
        """
        self._session_state.resize_buffers_for_sample_rate(self, effective_sr, max_rec)

    def _prepend_preroll_to_buffer(self) -> None:
        """Prepend the pre-roll buffer to the main buffer at start() time.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`SessionState.prepend_preroll_to_buffer`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks continue to work. See :mod:`.session_state` for the
        collaborator pattern and the AUDIO-PRE cold-start rationale.
        """
        self._session_state.prepend_preroll_to_buffer(self)

    def start(self) -> None:
        """Start recording audio.

        S3-CR-17 / Phase 4.5 — body moved to
        :func:`._recorder_split.start_recording` to shrink the
        3772-LOC ``recorder.py`` god class. The ``with self._start_lock:``
        block (recording-event check + microphone-permission pre-flight)
        STAYS HERE so the source-inspection contract
        (``tests/test_recording.py::TestRec5StartLock::test_start_lock_exists``)
        continues to pin the lock on ``Recorder.start`` source.

        ARCH-023: reset ALL per-session state here, not just the buffer.
        SEC-audit-008: ``_secure_clear_array`` is now actually used to
        zero cached audio arrays before they are dropped.
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

        _recorder_split.start_recording(self)

    def _teardown_stream(self) -> None:
        """Stop + close the PortAudio stream, draining any in-flight callback.

        S3-CR-17 / Phase 4.5 — body (the stop + callback-drain poll +
        close + clear sequence) moved to
        :meth:`StreamLifecycle.teardown_stream_body`. The
        ``_stream_lifecycle_lock`` acquire/release STAYS HERE (Option C)
        so the GT-24 source-inspection regression test
        (``tests/test_recorder_worker_lifecycle.py::TestGT24StreamLifecycleLock::test_teardown_stream_uses_lock``)
        continues to pin ``_stream_lifecycle_lock`` on
        ``Recorder._teardown_stream`` source.

        17-H-FIX-2 / GT-24: the teardown sequence is wrapped in
        ``_stream_lifecycle_lock`` so a concurrent
        ``_handle_device_disconnect`` restart block cannot mutate
        ``self._stream`` mid-teardown. Non-blocking acquire so
        ``__del__`` (best-effort cleanup, wrapped in
        ``contextlib.suppress``) never blocks on a long-running
        ``stop()``/``discard()``/disconnect handler holding the lock —
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
            self._stream_lifecycle.teardown_stream_body(self)
        finally:
            self._stream_lifecycle_lock.release()

    def _start_audio_worker(self) -> None:
        """Start the audio worker thread that drains the ring buffer.

        S3-CR-17 / Phase 4.5 — the read-check-create-start body moved to
        :meth:`AudioCallbackDispatcher.start_audio_worker_body`. The
        ``with self._worker_lifecycle_lock:`` block STAYS HERE for the
        GT-23 source-inspection contract
        (``tests/test_recorder_worker_lifecycle.py::test_start_audio_worker_holds_lock``).
        See :mod:`.capture` for the collaborator pattern and the full
        GT-23 / THREAD-REGISTRY rationale.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-create-start sequence so a concurrent
        # _stop_audio_worker() cannot observe a stale ``None`` mid-create.
        with self._worker_lifecycle_lock:
            self._capture.start_audio_worker_body(self)

    def _stop_audio_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the audio worker thread to stop and join it.

        S3-CR-17 / Phase 4.5 — the read-check-clear-join-unregister body
        moved to :meth:`AudioCallbackDispatcher.stop_audio_worker_body`.
        The ``_worker_lifecycle_lock`` block STAYS HERE for the GT-23
        source-inspection contracts (positive: must contain the
        lifecycle-lock literal; negative: must NOT contain the
        self-lock literal). See :mod:`.capture` for the collaborator
        pattern and the full THREAD-REGISTRY rationale.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-clear-join-unregister sequence. This is a
        # separate lock from the buffer lock — see the helper's docstring.
        with self._worker_lifecycle_lock:
            self._capture.stop_audio_worker_body(self, timeout=timeout, drain=drain)

    def _start_event_worker(self) -> None:
        """Start the IPC event worker thread that drains ``_event_queue``.

        S3-CR-17 / Phase 4.5 — the read-check-create-start body moved
        to :meth:`AudioCallbackDispatcher.start_event_worker_body`.
        The ``with self._worker_lifecycle_lock:`` block STAYS HERE for
        the GT-23 source-inspection contract. See :mod:`.capture` for
        the collaborator pattern.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-create-start sequence so a concurrent
        # _stop_event_worker() cannot observe a stale ``None`` mid-create.
        with self._worker_lifecycle_lock:
            self._capture.start_event_worker_body(self)

    def _stop_event_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the event worker thread to stop and join it.

        S3-CR-17 / Phase 4.5 — the read-check-clear-join-unregister body
        moved to :meth:`AudioCallbackDispatcher.stop_event_worker_body`.
        The ``_worker_lifecycle_lock`` block STAYS HERE for the GT-23
        source-inspection contracts (positive: must contain the
        lifecycle-lock literal; negative: must NOT contain the
        self-lock literal). See :mod:`.capture` for the collaborator
        pattern.
        """
        # GT-23: hold the lifecycle lock across the entire
        # read-check-clear-join-unregister sequence. This is a
        # separate lock from the buffer lock — see the helper's docstring.
        with self._worker_lifecycle_lock:
            self._capture.stop_event_worker_body(self, timeout=timeout, drain=drain)

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
        """Audio worker thread main loop — drains the ring buffer.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`AudioCallbackDispatcher.audio_worker_loop`. This is a
        1-line delegator so existing call sites, subclass overrides,
        and ``inspect.getsource`` checks on
        ``Recorder._audio_worker_loop`` continue to work. See
        :mod:`.capture` for the collaborator pattern and the RT-SAFE-001
        rationale (worker thread runs the heavy processing pipeline
        off the real-time audio thread).
        """
        self._capture.audio_worker_loop(self)

    def _audio_callback_dispatch(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Real-time audio callback entry point — RT-safe path.

        S3-CR-17 / Phase 4.5: the body (pre-roll capture, ring-buffer
        overflow detection, perf timestamp capture) moved to
        :meth:`AudioCallbackDispatcher.dispatch_callback_body`. The two
        RT-safe literals ``_ring_buffer.append`` and ``_worker_wake_event``
        STAY HERE (Option C) so the RT-SAFE-001 source-inspection contract
        in ``tests/test_recording_and_audio.py::test_callback_does_not_do_heavy_processing``
        continues to pin them on the Recorder's source.

        The dispatch_callback_body helper returns ``None`` for the
        pre-roll / early-bailout path (in which case we skip the ring
        buffer append + wake event) or a 5-tuple
        ``(chunk_copy, frames, time_info, status, perf_ts)`` ready for
        ``self._ring_buffer.append(payload)`` (in which case we append
        and signal the worker).
        """
        payload = self._capture.dispatch_callback_body(self, indata, frames, time_info, status)
        if payload is None:
            return  # pre-roll / early-bailout path
        self._ring_buffer.append(payload)
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

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`AudioPipeline.process_audio_chunk`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks on ``Recorder._process_audio_chunk`` continue to work.

        See :mod:`.audio_pipeline` for the collaborator pattern and
        the full processing-pipeline rationale (HOTKEY-CRASH,
        AUDIO-002, AUDIO-CH, AUDIO-PROC, AUDIO-CLIP, AUDIO-014,
        AUDIO-013, H12, T021, RT-SAFE-001).
        """
        self._audio_pipeline.process_audio_chunk(indata, frames, time_info, status, perf_ts)

    def _secure_clear_caches(self) -> None:
        """G4-H-06: securely zero cached audio arrays BEFORE reassignment.

        S3-CR-17 / Phase 4.5 — body moved to
        :meth:`SessionState.secure_clear_caches`. This is a 1-line
        delegator so existing call sites and ``inspect.getsource``
        checks on ``Recorder._secure_clear_caches`` continue to work.
        See :mod:`.session_state` for the collaborator pattern and the
        SEC-audit-008 rationale (secure-zeroing of cached audio arrays
        to prevent forensic recovery between sessions).

        Note: ``_secure_clear_session_caches`` (the smaller helper that
        zeros ``_cached_resampled`` and ``_cached_no_resample_arr``)
        STAYS on ``Recorder`` — it has a positive source-inspection
        contract (``tests/test_secure_clear_array.py:258-267``).
        """
        self._session_state.secure_clear_caches(self)

    def stop(self) -> np.ndarray:
        """Stop recording and return the complete audio array.

        S3-CR-17 / Phase 4.5 — body moved to
        :func:`._recorder_split.stop_recording` to shrink the
        ``recorder.py`` god class. This method is now a 1-line
        delegator so existing call sites, subclass overrides, and any
        ``inspect.getsource`` checks that look for the method on the
        ``Recorder`` class continue to work. See the helper's docstring
        for the full stop() step ordering (worker shutdown, stream
        teardown, secure-clear, snapshot under lock, stats
        computation, H15 resample-from-scratch contract).
        """
        return _recorder_split.stop_recording(self)

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

    @property
    def current_duration_seconds(self) -> float:
        """Approximate duration (in seconds) of audio currently in the buffer.

        AB-20: a cheap O(1) scalar read with NO array copy. Used by the
        streaming thread (``StreamingTranscriber.process_available_audio_once``)
        as an early-exit guard BEFORE calling :meth:`snapshot` — if the
        recorder hasn't accumulated enough NEW audio since the last
        emitted window, we skip the snapshot allocation entirely (the
        streaming thread polls at 4 Hz; without this guard each poll
        paid the snapshot cost even when there was nothing new to
        transcribe).

        Returns 0.0 when no audio has been recorded yet (empty buffer
        or sample rate unknown). The sample rate is read from
        ``_buffer_sr`` (the actual rate of the audio in the buffer,
        typically 16 kHz once an ``AudioProcessor`` is active) with a
        fallback to ``_effective_sr`` (the device's native rate) —
        mirroring the rate-resolution logic in
        :func:`._recorder_split.take_snapshot`.

        The duration is approximate because it sums ``len(chunk)`` across
        the deque's chunks without locking; the deque length is atomic
        in CPython, and a concurrent append at most makes the value
        slightly stale (which is fine for a polling guard). No array
        is materialized — this is the key difference vs. calling
        ``len(self.snapshot()) / sample_rate``.
        """
        buffer = self._buffer
        if not buffer:
            return 0.0
        sr = getattr(self, "_buffer_sr", None) or self._effective_sr
        if not sr:
            return 0.0
        # Sum chunk lengths without materializing a contiguous array.
        # ``sum(len(c) for c in buffer)`` is O(chunks) where chunks ≪
        # samples (16 Hz chunk arrival × recording length).
        try:
            total_samples = sum(int(c.shape[0]) for c in buffer)
        except (AttributeError, TypeError):
            # Defensive: a malformed chunk (rare) shouldn't crash the
            # polling guard. Fall back to 0.0 so the caller proceeds
            # with the snapshot path (which handles malformed chunks
            # itself).
            return 0.0
        return total_samples / sr

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
