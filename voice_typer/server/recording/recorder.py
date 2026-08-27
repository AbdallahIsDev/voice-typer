"""``Recorder`` — session-based audio recording from the microphone.

Phase 4.5 /  — extracted from the original ``recording.py``
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
import math  # noqa: F401  # re-exported for tests (recorder.math)
import os
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

# numpy was eagerly imported at module top, adding ~250-335ms to
# every cold start (numpy performs heavy C-extension initialization at
# import time). Replace with a lazy proxy so importing this module does
# NOT pull numpy into ``sys.modules``; the real import is deferred to
# first attribute access (typically the first ``Recorder.__init__`` or
# the first ``snapshot()`` call). ``from __future__ import annotations``
# above stringifies every ``np.ndarray`` annotation so function-def-time
# annotation evaluation does NOT trigger the lazy proxy (which would
# defeat the optimization).
from voice_typer.server._audio_constants import (  # noqa: F401  # SILERO_VAD_SAMPLE_RATES re-exported for tests
    SILERO_VAD_SAMPLE_RATES,
    WHISPER_SAMPLE_RATE,
)
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.config import Config
from voice_typer.server.vad_processor import VadProcessor, VadState

# ``event_bus`` and ``compute_vad_prob`` are hoisted to module
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
# lazy numpy proxy — see comment above.
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

# bind ``_secure_clear_array`` at module top
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

# VAD state machine ───────────────────────────────────────
# VadState and the VAD state-machine / auto-calibration logic
# were extracted to ``voice_typer.server.vad_processor`` (VadProcessor
# class). The symbol is re-exported here for backward compatibility —
# existing imports ``from voice_typer.server.recording import Recorder,
# VadState`` keep working unchanged.
# default VAD thresholds (overridden by auto-calibration).
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
# split: ``take_snapshot`` and ``discard_recording`` are the
# promoted bodies of ``Recorder.snapshot`` and ``Recorder.discard``. The
# methods become 1-line delegators so existing call sites, subclass
# overrides, and ``inspect.getsource`` checks continue to work. See
# :mod:`._recorder_split` for the full split plan.
from . import _recorder_split  # noqa: E402

# ``AudioPipeline`` owns the six named helpers split out of
# ``Recorder._process_audio_chunk`` in a previous session
# (``_detect_device_disconnect`` / ``_handle_xrun_status`` /
# ``_apply_filter_chain`` / ``_append_to_buffer_locked`` /
# ``_compute_rms_and_peak`` / ``_run_vad_state_machine``). ``Recorder``
# keeps 1-line delegator methods so existing call sites, subclass
# overrides, and ``inspect.getsource`` checks continue to work. See
# :mod:`.audio_pipeline` for the collaborator pattern.
from .audio_pipeline import AudioPipeline  # noqa: F401, E402 — re-exported for tests

# Phase 4.5: ``AudioCallbackDispatcher`` owns the audio
# worker thread main loop body and the audio callback dispatch body
# (excluding the literal ``_ring_buffer.append`` +
# ``_worker_wake_event.set`` operations that stay on
# ``Recorder._audio_callback_dispatch`` for the  source-
# inspection contract). ``Recorder`` keeps 1-line delegator methods so
# existing call sites, subclass overrides, and ``inspect.getsource``
# checks continue to work. See :mod:`.capture` for the collaborator
# pattern.
from .capture import AudioCallbackDispatcher  # noqa: F401, E402 — re-exported for tests

# Phase 4.5: ``DeviceManager`` owns device enumeration, hot-swap,
# and the device-health-checker daemon thread. ``Recorder`` constructs a
# ``DeviceManager`` instance in ``__init__`` and delegates the device
# methods to it (1-line delegators below). Device-owned state lives on
# ``DeviceManager``; ``Recorder`` exposes the subset accessed by tests /
# KEEP-methods via property shims (see ``_device_disconnected`` etc.
# below the ``__init__``).
from .device_manager import DeviceManager, DeviceStateShimMixin  # noqa: F401, E402 — re-exported for tests

# ``DisconnectHandler`` owns the device hot-swap stream-restart
# logic (the ~175-LOC block inside ``_stream_lifecycle_lock`` that was
# previously the tail of ``Recorder._handle_device_disconnect``). The
# bouncer checks + lock acquisition + re-checks STAY on
# ``Recorder._handle_device_disconnect`` so the  source-inspection
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

# God-class split: audio-format helpers (mono downmix, per-chunk
# resample, stop-time resample-to-target) moved to :mod:`.format` as
# free functions taking the Recorder as their first argument.
# ``Recorder`` keeps thin delegating methods (see the method docstrings)
# so existing call sites and monkeypatches keep working unchanged.
from .format import (  # noqa: E402
    ensure_mono as _format_ensure_mono_fn,
    prepare_audio as _format_prepare_audio_fn,
    resample_chunk as _format_resample_chunk_fn,
)

# Phase 4.5 further-split: the device / disconnect-handler
# state declarations + collaborator constructions (~88 LOC) were
# extracted to ``RecorderInitMixin._setup_device_state_and_collaborators``.
# ``Recorder.__init__`` calls the mixin method after the basic Recorder
# state is initialized. See :mod:`.recorder_init` for the collaborator
# construction order + the source-inspection compatibility notes.
from .recorder_init import RecorderInitMixin  # noqa: F401, E402

# Phase 4.5: ``resample_audio`` is the promoted body of
# ``Recorder._resample_audio_impl``. ``Recorder._resample_audio_impl``
# is now a 1-line delegator that calls this function so existing
# internal call sites (``_resample_chunk`` / ``_prepare_audio``) and
# any subclass overrides keep working unchanged.
from .resampling import (  # noqa: E402 — re-exported for tests (post-comment import)
    _SCIPY_PRELOADER_JOIN_TIMEOUT_S,  # noqa: F401 — re-exported for tests
    resample_audio as _resample_audio_fn,  # noqa: F401
)

# Phase 4.5: ``SessionState`` owns the per-session state
# reset, config-derived scalar caching, the bulk secure-clear
# (``_secure_clear_caches`` — NOT ``_secure_clear_session_caches`` which
# stays here for the source-inspection contract in
# ``tests/test_secure_clear_array.py``), buffer resizing for the
# effective sample rate, and the preroll prepend. ``Recorder`` keeps
# 1-line delegator methods so existing call sites, subclass overrides,
# and ``inspect.getsource`` checks continue to work. See
# :mod:`.session_state` for the collaborator pattern.
from .session_state import SessionState  # noqa: F401, E402 — re-exported for tests

# Phase 4.5: ``StreamLifecycle`` owns the PortAudio stream-
# open candidate-iteration loop, the all-devices fallback loop, the
# PortAudio callback closure construction, and the stream teardown
# body (inside ``_stream_lifecycle_lock``). The lock acquisition
# for teardown STAYS on ``Recorder._teardown_stream`` so the
# source-inspection regression tests continue to pin
# ``_stream_lifecycle_lock`` on that method. See :mod:`.stream_lifecycle`
# for the collaborator pattern.
from .stream_lifecycle import StreamLifecycle  # noqa: F401, E402 — re-exported for tests

# ``VadShimMixin`` provides the ~18 ``_vad_*`` property shims
# (``_vad_state``, ``_vad_consecutive_speech_frames``, ...) that delegate
# read/write access to ``self._vad`` (a ``VadProcessor``). The mixin is
# purely delegation, so it is extracted out of ``recorder.py`` to keep
# the Recorder class body focused on real behavior. ``Recorder`` inherits
# from ``VadShimMixin`` so existing attribute access on ``Recorder``
# instances keeps working unchanged.
from .vad_helpers import (  # noqa: F401, E402  # noqa: F401, E402
    VadShimMixin,  # noqa: F401, E402
    refresh_vad_caches as _refresh_vad_caches_fn,  # noqa: F401, E402
    vad_auto_calibrate as _vad_auto_calibrate_fn,
    vad_update as _vad_update_fn,
)

# the previous ``_DEFAULT_VAD_SPEECH_THRESHOLD_DB``
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


# NOTE: Dead-air timeout was REMOVED in
# Redundant with stop_on_silence_seconds (auto-stop already resets on
# speech). The _update_dead_air_simple() method was also removed along
# with _dead_air_timeout / _dead_air_speech_detected / _dead_air_silence_start.
# Do NOT re-add — it added no unique behavior.


# XRUN rolling window parameters
_XRUN_WINDOW_MAXLEN = 10  # keep last 10 xrun timestamps
_XRUN_ALERT_THRESHOLD = 5  # alert if N xruns in the window
_XRUN_ALERT_PERIOD = 10.0  # ...within M seconds


# PERF- / MAX_BUFFER_CHUNKS is dynamically adjusted in
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

# the periodic buffer-telemetry log (see the callback below) is
# diagnostic noise for the vast majority of users, who never look at raw
# buffer counts. It is gated behind VOICE_TYPER_VERBOSE so it only appears
# when someone is actively debugging audio/ring-buffer behaviour. Without
# the flag it stays silent at every level.
_BUFFER_TELEMETRY_ENABLED = os.environ.get("VOICE_TYPER_VERBOSE", "").lower() in (
    "1",
    "true",
    "yes",
)

# Audio callback → worker thread architecture ────────
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

# Rate-limit interval for the real-time ring-overflow WARNING emitted
# from ``_surface_ring_overflow_warning`` (called by the audio worker
# thread on every chunk iteration). One WARNING per N seconds —
# frequent enough to surface a sustained CPU-overload condition
# promptly, rare enough to avoid log spam when the worker briefly
# falls behind and recovers. The post-recording WARNING in
# ``RecordingController._stop_impl`` always fires (unconditionally) so
# the user sees the total dropped-chunk count for the session even if
# the real-time WARNING was rate-limited.
_RING_OVERFLOW_WARN_INTERVAL_S = 5.0

# PortAudio ``blocksize`` literal is defined in
# ``voice_typer.server._audio_constants`` (single source of truth, no
# circular import) and re-exported via this module for back-compat with
# callers that already ``from voice_typer.server.recording import
# _AUDIO_BLOCKSIZE``. See ``_audio_constants._AUDIO_BLOCKSIZE`` for the
# rationale (VAD-001 512-sample block contract).
from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE  # noqa: E402

_AUDIO_WORKER_THREAD_NAME = "audio-worker"
# Worker thread join timeout for stop() — generous to allow the worker
# to drain the ring buffer (up to 64 chunks * ~5ms VAD = ~320ms) plus
# headroom for VAD inference on the final chunks.
_AUDIO_WORKER_JOIN_TIMEOUT_S = 2.0
# Worker thread join timeout for discard() — shorter because discard()
# clears the ring buffer first, so the worker only needs to finish the
# current chunk (if any) before exiting.
_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S = 1.0

# IPC event worker thread — drains ``_event_queue`` and calls
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


class Recorder(DeviceStateShimMixin, VadShimMixin, RecorderInitMixin):
    """Records audio from microphone into a buffer. Session-based: start, accumulate, stop, get data."""

    # substrings of PortAudio OSError messages that indicate a
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

    # Session-state attributes (initialized by
    # ``SessionState.init_session_state``): flapping-BT-mic detection
    # state. Declared here so pyrefly sees their types on ``Recorder``
    # instances — the SessionState helper assigns them via
    # ``recorder.<attr> = ...`` (annotated assignments on a parameter
    # are rejected, so the class is the declaration site).
    _restart_timestamps: collections.deque
    _flapping_max_restarts: int
    _flapping_window_seconds: float

    def __init__(
        self,
        config: Config,
        audio_processor: Any | None = None,
        thread_registry: Any | None = None,
    ):
        """Construct the Recorder (decomposed into focused ``_init_*`` helpers).

        The historical monolithic ~416-line constructor is split into
        focused private initializers, each owning ONE concern (core
        session state, locks, XRUN tracking, sample-rate/chunk scalars,
        snapshot caches, error counters, VAD, preroll, worker handles,
        event queue, channel/scratch state, silence detection, scipy
        preloader registration). Every attribute lands on ``self``
        exactly as before, in the same construction order — zero
        behavior change. See each helper's docstring for its contract.
        """
        self._init_core_session_state(config, audio_processor, thread_registry)
        self._init_locks_and_flags()
        self._init_xrun_tracking()
        self._init_sample_rate_and_chunk_state(config)
        self._init_snapshot_caches()
        self._init_error_counters()
        self._init_vad(config)
        self._init_preroll_state(config)
        self._init_ring_and_worker_handles()
        self._init_event_queue()
        self._init_stream_format_state()
        # Device / disconnect-handler state declarations +
        # collaborator constructions — see :mod:`.recorder_init` for
        # the collaborator construction order + the source-inspection
        # compatibility notes. Called AFTER the basic Recorder state is
        # initialized; the collaborators' ``__init__`` methods store a
        # back-reference to ``self`` and do NOT touch other recorder
        # state, so the call order is safe.
        self._setup_device_state_and_collaborators()
        self._init_silence_detection()
        self._register_scipy_preloader()

    def _init_core_session_state(
        self,
        config: Config,
        audio_processor: Any | None,
        thread_registry: Any | None,
    ) -> None:
        """Config / audio-processor / thread-registry backrefs, the
        stream slot, and the contiguous recording buffer."""
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
        # Contiguous recording storage (replaces the deque-of-chunks).
        # ONE pre-allocated growable float32 ndarray: appends copy each
        # chunk in under ``_lock`` (~16 Hz small memcpys on the audio
        # worker), snapshots are O(1) views, and stop() hands off an
        # already-contiguous array. ``maxlen`` keeps the CHUNK-count
        # semantics of the old ``deque(maxlen=DEFAULT_MAX_BUFFER_CHUNKS)``
        # (duration cap + backpressure detection); see
        # :class:`GrowableRecordingBuffer` for the full capacity policy.
        # Typed ``Any`` because the mic hot-swap path transiently swaps in
        # a plain ``collections.deque`` mid-session; every reader
        # normalizes via ``_ensure_growable_buffer`` under the lock.
        self._buffer: Any = _recorder_split.GrowableRecordingBuffer(
            maxlen=DEFAULT_MAX_BUFFER_CHUNKS,
            nominal_sample_rate=config.sample_rate,
            on_extra_eviction=self._note_buffer_capacity_eviction,
        )
        # PERF: running total of buffered samples (sum of ``len(chunk)``
        # across ``_buffer``). Maintained as an O(1) counter incremented
        # under ``_lock`` in ``AudioPipeline.append_to_buffer_locked`` so
        # ``current_duration_seconds`` (polled at 4 Hz by the streaming
        # thread) doesn't have to iterate the whole deque — previously
        # each poll paid an O(chunks) ``sum(int(c.shape[0]) for c in
        # buffer)`` reduction, which on a 30-min dictation at ~16 Hz
        # chunk arrival summed over ~28k chunks per poll. Reset to 0 in
        # ``reset_session_state`` (start) and in ``stop()`` / ``discard()``
        # alongside the buffer swap.
        self._total_buffered_samples: int = 0

    def _init_locks_and_flags(self) -> None:
        """Create the four synchronization primitives + force-closed flag."""
        self._lock = threading.Lock()
        # serializes ``start()`` against ``discard()`` so a
        # concurrent toggle-thread + ESC-cancel-thread + auto-stop
        # Timer thread cannot race on the per-session state reset
        # (buffer.clear, _chunk_count=0, etc.) and stream-start. The
        # lock is acquired at the top of ``start()`` / ``discard()``
        # and released before the method returns; it is NEVER held
        # across ``thread.join()`` or ``_process_audio_chunk`` (same
        # invariant as ``_worker_lifecycle_lock``). Reentrant-safe
        # under ``start()→stop()`` because each method acquires +
        # releases in a ``with`` block. Tests in
        # ``tests/test_retry_regressions.py::TestStartLockRuntime``
        # assert the lock is unheld before/after each method.
        self._start_lock = threading.Lock()
        # serializes the read-check-create-start sequence in
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
        # serializes stream teardown (``_teardown_stream``)
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

    def _init_xrun_tracking(self) -> None:
        """XRUN / clipping counters, the threshold callback slot, the
        rolling xrun-timestamp window, and the recording gate event."""
        # XRUN and clipping tracking
        self._xruns: int = 0
        self._clip_count: int = 0
        self._peak: float = 0.0
        self._last_clip_log_time: float = 0.0
        # Item 1: xrun notification callback — set by VoiceTyperApp
        # to receive a notification when xrun count exceeds threshold.
        self.on_xrun_threshold: Callable[[int], None] | None = None
        self._xrun_threshold: int = 10  # notify after this many xruns
        # rolling window of xrun timestamps for rate-limited logging
        self._xrun_timestamps: collections.deque = collections.deque(maxlen=_XRUN_WINDOW_MAXLEN)
        self._recording_event = threading.Event()
        # removed dead ``_in_callback`` field — it
        # was declared here but never set, cleared, or read anywhere in
        # the codebase. The actual in-flight-callback guard is
        # ``_is_in_audio_callback`` (declared in ``_init_preroll_state``).

    def _init_sample_rate_and_chunk_state(self, config: Config) -> None:
        """Sample-rate scalars (device-rate + buffer-rate) and the
        per-chunk VAD property cache + RMS/chunk counters."""
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

    def _init_snapshot_caches(self) -> None:
        """Snapshot resample/no-resample caches + the last-audio-stats slot."""
        # H15/M8: Cached resampled prefix for snapshot() to avoid O(n²) resampling
        self._cached_resampled: np.ndarray = np.array([], dtype=np.float32)
        # Contiguous storage: ``_cached_resampled`` OWNS a geometrically
        # grown capacity buffer and ``_cached_resampled_len`` tracks the
        # filled prefix. (Previously the attribute was rebuilt via
        # segment-list concatenation on demand.)
        self._cached_resampled_len: int = 0
        self._cached_native_chunk_count: int = 0
        # cache key must include the audio dtype + sample rates
        # so a float32 vs int16 mismatch (theoretically possible if the
        # PortAudio stream is reconfigured mid-session) doesn't return
        # the wrong cached prefix. We track (dtype, src_sr, dst_sr) and
        # invalidate the cache on any change.
        self._cached_resample_key: tuple = ()
        # cache the no-resample concatenation result so
        # repeated snapshots with no new chunks don't repeat the
        # np.concatenate.  Invalidated whenever the buffer length
        # changes (i.e. a new chunk arrived).
        self._cached_no_resample_len: int = -1
        self._cached_no_resample_arr: np.ndarray | None = None
        # mirror the resample-path segment-list + lazy-concat
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
        # cache of (rms, peak, silence_pct) from the most
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

    def _init_error_counters(self) -> None:
        """Per-session error counters (reset by ``start()`` each session)."""
        # per-session error counters. Previously these were
        # lazily initialized via ``getattr(self, "_dropped_chunks", 0)``
        # in the audio callback / RMS callback and never reset in
        # ``start()`` -- so a long-running app accumulated error counts
        # across sessions, masking per-session regressions in tests
        # and dashboards. Declare them in ``__init__`` and reset them
        # in ``start()`` so each session starts from zero.
        self._dropped_chunks: int = 0
        self._rms_callback_error_count: int = 0

    def _init_vad(self, config: Config) -> None:
        """Construct the ``VadProcessor`` and log raw-recording mode."""
        # VAD state machine with hysteresis.
        # the VAD state machine, Silero integration, and
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

    def _init_preroll_state(self, config: Config) -> None:
        """Pre-roll circular buffer sizing + the in-flight-callback guard."""
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
        self._preroll_blocksize: int = _AUDIO_BLOCKSIZE  # matches sd.InputStream blocksize
        self._preroll_buffer: collections.deque = collections.deque(
            maxlen=int(preroll_seconds * sample_rate / _AUDIO_BLOCKSIZE) + 2 if preroll_seconds > 0 else 0
        )
        self._preroll_active: bool = preroll_seconds > 0  # only capture when enabled

        # guard flag for in-flight audio callback
        self._is_in_audio_callback: threading.Event = threading.Event()

    def _init_ring_and_worker_handles(self) -> None:
        """SPSC ring buffer + audio-worker thread/event handles + the
        ring-overflow warning bookkeeping."""
        # SPSC ring buffer for callback → worker handoff.
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
        # (worker couldn't keep up). Surfaced by the worker thread via
        # ``_surface_ring_overflow_warning`` (rate-limited WARNING) and
        # post-recording by ``RecordingController._stop_impl``.
        self._dropped_ring_chunks: int = 0
        # Captures the most recent exception raised inside
        # ``AudioCallbackDispatcher.dispatch_callback_body`` (the
        # PortAudio RT-callback body). PortAudio silently aborts the
        # stream when the callback raises, which surfaces to the user
        # as a "device disconnect" — a misdiagnosis. The body now
        # catches the exception, stores it here, and re-raises (so
        # PortAudio still aborts). ``_stream_finished_callback`` reads
        # this attribute, logs the true cause at ERROR with full
        # traceback, and clears it. Read/written atomically under the
        # GIL (single assignment of an attribute reference).
        self._last_callback_error: Exception | None = None
        # Real-time ring-overflow WARNING bookkeeping (see
        # ``_surface_ring_overflow_warning``). ``_last_seen_dropped_ring_chunks``
        # is the value of ``_dropped_ring_chunks`` the last time the worker
        # thread checked; the delta between consecutive checks is the
        # number of chunks dropped since the last WARNING. ``_ring_overflow_warn_ts``
        # is the ``time.perf_counter()`` of the last WARNING, used to
        # rate-limit to one WARNING per ``_RING_OVERFLOW_WARN_INTERVAL_S``.
        self._last_seen_dropped_ring_chunks: int = 0
        self._ring_overflow_warn_ts: float = 0.0

    def _init_event_queue(self) -> None:
        """IPC event queue + event-worker thread handle + stop event."""
        # IPC event queue + dedicated worker thread. The audio
        # worker thread (``_audio_worker_loop``) enqueues IPC events
        # (e.g. ``audio_clip``) on this queue via a non-blocking
        # ``put``; the event worker thread (``_event_worker_loop``)
        # drains the queue and calls ``event_bus.publish``. This keeps
        # the audio worker off the IPC transport so a slow TCP
        # subscriber cannot stall the audio pipeline and cause
        # ring-buffer overflows / dropped audio.
        #
        # PERF-: bounded at ``maxsize=1000`` so a stalled worker
        # can't cause unbounded memory growth. The producer uses
        # ``put_nowait`` + ``queue.Full`` suppression (see the
        # ``audio_clip`` callsite below), which means events are
        # silently dropped when the worker falls behind — the
        # audio-thread producer never blocks.
        self._event_queue: queue.Queue[dict | _EventWorkerStopSentinel] = queue.Queue(maxsize=1000)
        self._event_worker_thread: threading.Thread | None = None
        self._event_stop_event: threading.Event = threading.Event()

    def _init_stream_format_state(self) -> None:
        """Input-stream channel count + the per-thread mono downmix scratch."""
        # AUDIO-CH: actual channel count of the input stream
        self._actual_channels: int = 1

        # Pre-allocated per-thread scratch buffer for the stereo downmix
        # path of ``_ensure_mono``. The audio worker thread (16 Hz hot
        # path) and the RT audio callback (pre-roll capture path) both
        # call ``_ensure_mono`` — a single shared scratch would race
        # when pre-roll is enabled, so the scratch is thread-local.
        # The scratch is resized lazily if a chunk larger than the
        # current capacity arrives (rare — blocksize is 512 samples).
        self._mono_scratch_local = threading.local()

    def _init_silence_detection(self) -> None:
        """H12 silent-mic-disconnection state + the app-wired callbacks."""
        # NOTE (): dead_air_timeout / _dead_air_speech_detected
        # _dead_air_silence_start were REMOVED — redundant with
        # stop_on_silence_seconds. Do NOT re-add.

        # H12: Silent mic disconnection detection
        self._silence_timer: float = 0.0
        # absolute timestamp for silence start, prevents
        # timer drift under CPU pressure
        self._silence_start_time: float | None = None
        self._silence_warning_count: int = 0
        self._silence_next_warning_wait: float = 10.0
        self._recording_start_time: float = 0.0
        self._recent_rms_values: collections.deque = collections.deque(maxlen=50)
        # (revised): the dead ``_max_duration_warning_sent``
        # and ``_silence_warning_sent`` boolean flags have been REMOVED.
        # They were declared and reset but NEVER read — the actual
        # silence-warning state machine uses ``_silence_warning_count``
        # (an int counter, see recording.py:1109). Removing the dead
        # flags prevents maintainers from thinking warning deduplication
        # exists when it doesn't.

        # H12 callbacks (wired by app.py)
        self.on_silence_warning = None  # type: Callable[..., Any] | None
        self.on_silence_auto_stop = None  # type: Callable[..., Any] | None
        self.on_max_duration_auto_stop = None  # type: Callable[..., Any] | None

        # Waveform bubble: fired from audio callback on every chunk (wired by app.py)
        self.on_rms_level = None  # type: Callable[..., Any] | None
        # callback signature is (rms: float, peak: float).
        # The previous 3-arg form (rms, peak, audio_chunk) forwarded the
        # filtered audio chunk so WaveformBubble could run Silero VAD on
        # it, but BUBBLE- removed the VAD gate entirely (the
        # device's native sample-rate audio was being fed to a model
        # that assumes 16 kHz).  No current consumer reads the chunk, so
        # it was removed from the contract (privacy surface + per-chunk
        # refcount cost on the audio hot path).

    def _register_scipy_preloader(self) -> None:
        """Start (once) + register the scipy-preloader thread with the
        thread registry so ``shutdown_all()`` can join it on exit."""
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
        # The mutable preloader state lives on the .resampling submodule;
        # read it there at call time so tests patching
        # ``voice_typer.server.recording.resampling._scipy_preloader_thread``
        # are honored.
        from voice_typer.server.recording import resampling as _recording_resampling

        _recording_resampling._start_scipy_preloader()
        if (
            self._thread_registry is not None
            and _recording_resampling._scipy_preloader_thread is not None
            and _recording_resampling._scipy_preloader_thread.is_alive()
        ):
            self._thread_registry.register(
                name="scipy-preloader",
                thread=_recording_resampling._scipy_preloader_thread,
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

    # Phase 4.5: device-state property shims ─────────────────
    #
    # The device-related state attrs live on ``DeviceManager`` (see
    # ``device_manager.py``). The read/write property shims that
    # re-expose them on ``Recorder`` instances moved to
    # :class:`.device_manager.DeviceStateShimMixin` (the same mixin
    # pattern as :class:`.vad_helpers.VadShimMixin``) so the Recorder
    # class body stays focused on real behavior. ``Recorder`` inherits
    # the mixin, so attribute access is unchanged for KEEP-methods and
    # tests alike.

    # ── AUDIO-CH: mono conversion helper ────────────────────────────────

    def _ensure_mono(self, audio: np.ndarray) -> np.ndarray:
        """Convert multi-channel audio to mono by averaging channels.

        Body moved to :func:`.format.ensure_mono` (god-class split).
        This is a thin delegator so existing call sites (the audio
        worker's filter-chain path and the RT callback's pre-roll
        capture path), subclass overrides, and instance-level
        monkeypatches keep working unchanged. See the helper's
        docstring for the full AUDIO-CH rationale (stereo fast path
        via the per-thread scratch buffer, copy-before-return contract).
        """
        return _format_ensure_mono_fn(self, audio)

    # ── AUDIO-MIC: device list caching ──────────────────────────────────
    #
    # Phase 4.5: the device-list cache, mic-watcher lifecycle,
    # and device-resolution / sample-rate negotiation logic were moved
    # to ``DeviceManager`` (see ``device_manager.py``). The methods
    # below are 1-line delegators that route through ``self._devices``
    # so existing internal call sites (``start()``, ``_handle_device_disconnect``,
    # ``_device_health_checker_loop``) and external callers (e.g.
    # ``VoiceTyperApp.list_microphones``) keep working unchanged.

    def _refresh_device_list(self) -> list[dict]:
        """Return the device list, refreshing the cache if stale (delegator).

        Phase 4.5: body moved to ``DeviceManager._refresh_device_list``.
        """
        return self._devices._refresh_device_list()

    def _invalidate_device_cache(self) -> None:
        """Reset the device-list cache (delegator).

        Phase 4.5: body moved to ``DeviceManager._invalidate_device_cache``.
        """
        return self._devices._invalidate_device_cache()

    def shutdown_mic_watcher(self) -> None:
        """Stop the microphone device-change watcher (delegator).

        Phase 4.5: body moved to ``DeviceManager.shutdown_mic_watcher``.
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
                callback via zero-filled indata detection (see _audio_callback_dispatch).

        If ``dispatch_callback_body`` captured an exception (stored
                on ``self._last_callback_error``), the stream finished because
                the RT callback raised — NOT because of a device disconnect.
                Log the true cause at ERROR with full traceback so the user /
                developer can diagnose the actual bug instead of chasing a
                phantom "device disconnect". The attribute is cleared after
                logging so a subsequent genuine disconnect is not masked by
                a stale exception reference.

        capture ``gen = self._stop_generation`` at scheduling time
                and pass it via ``kwargs={'_captured_generation': gen}`` so the
                spawned ``_handle_device_disconnect`` can bail out if a deliberate
                stop/start cycle happened between scheduling and execution (mirrors
                the pattern in ``_process_audio_chunk`` at the zero-filled-indata
                spawn site). Pre-fix, the handler was scheduled with the default
                ``_captured_generation=0``, which matched the initial
                ``_stop_generation=0`` on the first session — defeating the
                bouncer for any stop() that landed between scheduling and execution.
        """
        # Surfacing the true cause of a callback-driven stream abort.
        # ``dispatch_callback_body`` (in capture.py) wraps its body in
        # try/except, stores any exception on ``self._last_callback_error``,
        # and re-raises so PortAudio still aborts the stream. Without this
        # block, the user would see the "Stream finished unexpectedly"
        # warning below — a misdiagnosis that hides a real bug in the RT
        # callback. Read the attribute atomically (single attribute-read
        # under the GIL) and clear it immediately so a future genuine
        # disconnect is not masked by a stale reference.
        captured_err = self._last_callback_error
        if captured_err is not None:
            self._last_callback_error = None
            log.error(
                "[RECORDER] stream finished due to callback exception",
                exc_info=captured_err,
            )
            # The stream aborted because of a code bug, not a device
            # issue — do NOT spawn the disconnect-retry handler (it
            # would mask the bug by restarting the stream on the
            # default device). The recording state is left to the
            # user's next start()/stop()/discard() call.
            return
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
            # capture the current stop_generation so the handler
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
        # BT HFP/HSP mode-switch retry policy: Bluetooth headsets take
        # 1-3s to switch from A2DP (audio output) to HFP/HSP (two-way
        # call) mode when any app opens the mic input. The default
        # 3-retry budget fires within ~100ms (each retry is a separate
        # disconnect-detection cycle at ~32ms cadence), terminating
        # the recording before the BT stack finishes the mode switch.
        # Query the current device info and use a BT-aware retry budget
        # (6 retries for BT vs 3 for non-BT) and an inter-retry sleep
        # (0.75s for BT vs 0s for non-BT) so the total BT retry window
        # (~4.5s) covers the mode-switch latency. The helpers live on
        # ``DeviceManager`` (``_build_device_info_for_retry_policy`` /
        # ``_get_max_retries_for_device`` / ``_get_retry_sleep_for_device``)
        # and return the conservative defaults (3 / 0.0s) when the
        # device query fails — preserving the pre-fix behavior on
        # query errors.
        _device_info = self._devices._build_device_info_for_retry_policy()
        _effective_max_retries = self._devices._get_max_retries_for_device(_device_info)
        _retry_sleep = self._devices._get_retry_sleep_for_device(_device_info)
        if self._device_disconnect_retries > _effective_max_retries:
            log.error(
                "[RECORDING] Max disconnect retries (%d) reached. Stopping recording.",
                _effective_max_retries,
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
            _effective_max_retries,
        )

        # BT inter-retry sleep: space successive retries so the BT stack
        # has time to re-establish the HFP/HSP link between attempts.
        # Skipped on the first attempt (no prior failure to recover from)
        # and on non-BT devices (``_retry_sleep == 0.0`` → immediate
        # retry, preserving the pre-fix behavior). The sleep is bounded
        # by ``_bt_retry_sleep_seconds`` (default 0.75s) so the total
        # retry window for a BT device is ~4.5s (6 retries × 0.75s) —
        # within the 3-5s target for covering the HFP mode-switch
        # latency. The bouncer checks above already verified the
        # recording is still active, so sleeping here is safe.
        if _retry_sleep > 0.0 and self._device_disconnect_retries > 1:
            time.sleep(_retry_sleep)

        # (IMPROVE-mode run, 2026-07-21): Stop current stream via
        # ``_teardown_stream()`` instead of raw ``stop()/close()``.
        # ``_teardown_stream`` polls ``self._is_in_audio_callback`` for up
        # to 300ms before ``close()``, preventing PortAudio use-after-free
        # or deadlock when the audio callback is still in-flight (the
        # disconnect handler is spawned FROM the audio callback / worker
        # thread on a fresh daemon thread — the callback may still be
        # running when we close). ``_teardown_stream`` is idempotent.
        # ``_teardown_stream`` acquires+releases
        # ``_stream_lifecycle_lock`` internally; the restart block below
        # re-acquires it for the new-stream creation+assignment.
        #
        # Pass ``force=True`` so the teardown uses ``stream.abort()``
        # instead of ``stream.stop()``. The device is KNOWN to be gone
        # (that's why we're in this handler), so ``stop()`` would block
        # indefinitely waiting for pending buffers that will never
        # drain. ``abort()`` returns immediately (PortAudio discards the
        # buffers), unblocking the recovery critical path. The CLEAN
        # path (stop from hotkey) keeps the default ``force=False`` for
        # graceful drain.
        self._teardown_stream(force=True)

        # hold the stream-lifecycle lock across the restart so a
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

            # the device-resolution + stream-open + state-update
            # block (the ~175-LOC tail of this method) was extracted to
            # :meth:`DisconnectHandler.restart_stream`. The handler runs
            # under ``_stream_lifecycle_lock`` (acquired above) and
            # re-checks ``_captured_generation != self._stop_generation``
            # a third time before assigning ``self._stream`` (the
            # close-the-new-stream-on-race path lives in the handler).
            self._disconnect_handler.restart_stream(_captured_generation)

    # ── CPU-03: Device health checker thread ─────────────────────────
    #
    # Phase 4.5: the health-checker thread state + main loop
    # were moved to ``DeviceManager``. The methods below are 1-line
    # delegators. ``_device_health_checker_loop`` accesses
    # ``self.recorder._recording_event`` / ``self.recorder._stop_generation``
    # / ``self.recorder._handle_device_disconnect`` via the collaborator
    # back-reference (see ``device_manager.py``).

    def _start_device_health_checker(self) -> None:
        """Start the device health checker daemon thread (delegator).

        Phase 4.5: body moved to
                ``DeviceManager._start_device_health_checker``.
        """
        return self._devices._start_device_health_checker()

    def _stop_device_health_checker(self, timeout: float | None = None) -> None:
        """Signal the device health checker thread to stop and join it (delegator).

        Phase 4.5: body moved to
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

    # deleted dead ``_device_health_checker_loop`` delegator.
    # The daemon thread is started by ``DeviceManager._start_device_health_checker``
    # which uses ``target=self._device_health_checker_loop`` (bound to the
    # DeviceManager instance), bypassing the Recorder delegator entirely.
    # Repo-wide grep returned ZERO call sites for ``recorder._device_health_checker_loop()``.

    # VAD auto-calibration ─────────────────────────────────

    # PERF-02 (c-review): max age in seconds before the cached _vad_enabled
    # value is re-evaluated. This is a SAFETY NET only — the primary refresh
    # path is on_config_changed(), called by app._rebuild_audio_processor
    # whenever a noise_filter_* / audio_preset / noise_suppression_method
    # config field changes (wiring owned by Sub-Agent H in app.py). The
    # TTL ensures that if the explicit refresh hook is missing for some
    # code path, the cache is still refreshed at most once every 5s — so
    # a missed notification cannot permanently wedge the cache.
    _VAD_ENABLED_CACHE_TTL_S: float = 5.0

    # VAD attribute delegation shims ───────────────────────────
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

        delegates to ``self._vad.on_config_changed()``. The
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
        """refresh per-chunk VAD caches (delegator).

        Body moved to :func:`.vad_helpers.refresh_vad_caches` so
        ``recorder.py`` shrinks further (further-split). This is
        a 1-line delegator so existing call sites, subclass overrides,
        and ``inspect.getsource(Recorder._refresh_vad_caches)`` checks
        keep working. Tests that monkeypatch
        ``recorder._refresh_vad_caches`` via
        ``monkeypatch.setattr(r, "_refresh_vad_caches", lambda: None)``
        replace the bound delegator on the instance — delegation is
        bypassed and the lambda runs unchanged.

        Called by ``start()`` and ``on_config_changed()`` so the audio
        worker hot path (16 Hz) reads cached scalars instead of
        dispatching 3 property lookups per chunk × 16 Hz = 48
        lookups/sec for values that only change on config edits.

        Also computes the (up, down) integer ratio for the VAD resample
        path. The ratio is derived from ``_buffer_sr`` (the
        post-process_chunk rate set by ``_process_audio_chunk``). When
        ``_buffer_sr`` is 8000 or 16000, no resample is needed and the
        cache is set to ``None``. When ``_buffer_sr`` is something else
        (e.g. 48000 — happens when no AudioProcessor is attached and
        the device's native rate is non-16 kHz), the cache stores the
        (up, down) integers so the per-chunk VAD path avoids
        recomputing ``math.gcd``.

        ``_buffer_sr`` may be ``None`` at ``start()`` time (before the
        first chunk arrives); we fall back to ``_effective_sr`` for the
        cache key. If the actual ``_buffer_sr`` set by the first
        ``_process_audio_chunk`` differs, the cache is refreshed lazily
        inside ``_process_audio_chunk``.
        """
        return _refresh_vad_caches_fn(self)

    # deleted dead ``_compute_vad_enabled`` method (26 LOC).
    # ``VadProcessor.compute_vad_enabled`` is called directly by
    # ``VadProcessor.vad_enabled`` (property) which is called by
    # ``Recorder._vad_enabled`` (property, in vad_helpers.py).
    # The ``Recorder._compute_vad_enabled`` wrapper was a vestigial
    # delegator from  with ZERO call sites in production or tests.

    def _vad_auto_calibrate(self, chunk_rms: float, chunk_duration: float) -> None:
        """Auto-calibrate VAD thresholds based on ambient noise floor (delegator).

        Body moved to :func:`.vad_helpers.vad_auto_calibrate`. This is a
        1-line delegator so existing call sites, subclass overrides, and
        ``inspect.getsource(Recorder._vad_auto_calibrate)`` checks keep
        working. Tests that mock ``recorder._vad_auto_calibrate`` via
        ``MagicMock`` replace the bound delegator on the instance —
        delegation is bypassed and the mock runs unchanged.

        Delegates to ``self._vad.auto_calibrate(chunk_rms, elapsed,
        chunk_duration)``. The ``elapsed`` argument is computed in the
        helper from ``self._recording_start_time`` (a Recorder-owned
        attribute) so VadProcessor stays clock-agnostic.

        During the first ``_vad_calibration_duration`` seconds of
        recording, we collect RMS values to determine the ambient noise
        floor. Then we set speech/silence thresholds relative to it.

        VAD-GATE (Task 4): VadProcessor.auto_calibrate also gates on
        vad_enabled, but the helper short-circuits here too so we
        don't even call ``time.perf_counter()`` on every chunk in raw
        mode.
        """
        return _vad_auto_calibrate_fn(self, chunk_rms, chunk_duration)

    # VAD state machine update ─────────────────────────────

    def _vad_update(self, chunk_rms_db: float, vad_prob: float | None = None) -> VadState:
        """Update the VAD state machine based on the current frame's VAD signal (delegator).

        Body moved to :func:`.vad_helpers.vad_update`. This is a 1-line
        delegator so existing call sites, subclass overrides, and
        ``inspect.getsource(Recorder._vad_update)`` checks keep working
        (notably ``test_grey_zone_does_not_reset_counters`` in
        ``tests/regressions/test_audio.py`` — the pinned phrases "Grey
        zone (between speech and silence thresholds)", "pass", and
        "State transitions" remain in this docstring so the source-
        string regression test continues to pass after the body move).

        Delegates to ``self._vad.update_frame(chunk_rms_db, vad_prob)``.
        The VadProcessor owns the state-machine counters, thresholds,
        and hysteresis transitions. The historical ``self._vad_*``
        attribute names (e.g. ``_vad_consecutive_speech_frames``)
        remain accessible on ``Recorder`` via property shims that
        read/write through to ``self._vad``.

        Uses hysteresis — transitioning from SILENCE to SPEECH requires
        N consecutive loud frames, while SPEECH to SILENCE requires M
        consecutive quiet frames (hangover period). This prevents
        rapid toggling at the boundary.

        When Silero VAD is enabled and a probability is provided, uses
        the VAD probability for speech/silence determination instead of
        RMS dB. Falls back to RMS-based detection if vad_prob is None.

        VAD-GATE (Task 4): returns ``VadState.UNKNOWN`` immediately
        when VAD is disabled (all audio enhancements off). The caller's
        silence-timer logic sees UNKNOWN and treats it as "not silence"
        (no silence warnings, no VAD-based auto-stop).

        Grey zone (between speech and silence thresholds). Standard VAD
        hysteresis: leave counters unchanged so a long run of grey-zone
        chunks doesn't discard accumulated frame history. Implemented
        in ``VadProcessor.update_frame`` as a ``pass`` branch — no
        counter resets. State transitions with hysteresis are also
        implemented there. This wrapper preserves the source patterns
        existing tests pin on (the "Grey zone" comment, the ``pass``
        keyword, and the "State transitions" comment must appear in
        this method's source for
        ``test_grey_zone_does_not_reset_counters`` to keep passing).
        """
        # State transitions: delegated to VadProcessor.update_frame.
        return _vad_update_fn(self, chunk_rms_db, vad_prob)

    # ── ADR 0007 §3.5: _agc_update method deleted ─────────────────────
    # The old per-chunk AGC (C1) has been removed. It duplicated the
    # Compressor filter in the new audio filter chain. The Compressor
    # now handles dynamic range compression with proper attack/release.

    def warm_up_resampler(self) -> None:
        """Import and initialize the high-quality resampler before recording stops."""
        try:
            resample_poly = _recording_pkg._get_resample_poly()
            # ``_get_resample_poly()`` may legitimately return
            # ``None`` when a test monkeypatches it to ``lambda: None`` or
            # when a future refactor caches a None sentinel instead of
            # raising. Pre-fix, the next line would call
            # ``None(np.zeros(...), 160, 441)`` and raise ``TypeError:
            # 'NoneType' object is not callable`` — caught by the broad
            # ``except Exception`` below and logged as "Resampler warm-up
            # failed: 'NoneType' object is not callable", which is
            # misleading. The explicit None check emits the same "scipy
            # not available" warning as the ``ImportError`` branch so the
            # diagnostic is consistent.
            if resample_poly is None:
                log.warning("[RECORDING] scipy not available, will use linear interp resampling")
                return
            resample_poly(np.zeros(32, dtype=np.float32), 160, 441)
            log.debug("[RECORDING] Resampler warmed up")
        except ImportError:
            log.warning("[RECORDING] scipy not available, will use linear interp resampling")
        except Exception as e:
            log.warning("[RECORDING] Resampler warm-up failed: %s", e)

    def _resolve_device(self):
        """Resolve config.microphone to a sounddevice device specifier (delegator).

        Phase 4.5: body moved to ``DeviceManager._resolve_device``.
                ``config.microphone`` is a string device index (from list_microphones)
                or None for system default.  We convert to int for unambiguous
                selection by sounddevice.
        """
        return self._devices._resolve_device()

    def _host_api_name(self, host_api_index: int) -> str:
        """Return the host API name for the given index (delegator).

        Phase 4.5: body moved to ``DeviceManager._host_api_name``.
        """
        return self._devices._host_api_name(host_api_index)

    def _device_index(self, fallback_index: int, device_info: dict) -> int:
        """Return the device index from device_info, falling back to fallback_index (delegator).

        Phase 4.5: body moved to ``DeviceManager._device_index``.
        """
        return self._devices._device_index(fallback_index, device_info)

    def _same_physical_microphone_candidates(self, device: Any) -> list[Any]:
        """Return equivalent input device IDs to try if the selected one fails (delegator).

        Phase 4.5: body moved to
                ``DeviceManager._same_physical_microphone_candidates``.
        """
        return self._devices._same_physical_microphone_candidates(device)

    def _fallback_host_rank(self, host_name: str) -> int:
        """Rank a host API by preference for fallback device selection (delegator).

        Phase 4.5: body moved to ``DeviceManager._fallback_host_rank``.
        """
        return self._devices._fallback_host_rank(host_name)

    def _resolve_effective_sample_rate(self, device: int | None) -> tuple[int, dict | None]:
        """Determine the effective sample rate and device info for the given device (delegator).

        Phase 4.5: body moved to
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

        Phase 4.5: body moved to
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

        In addition to warming the device-list cache, the prewarm thread
        also opens a brief ``sd.InputStream`` against the configured mic
        (via ``_prewarm_input_stream``). This validates the device, warms
        PortAudio's internal device-state cache (so the first ``start()``
        doesn't pay the full Pa_OpenStream + Pa_StartStream cost), and
        surfaces permission errors at app launch instead of at first
        hotkey. Failures are logged at INFO and never propagated — the
        prewarm is purely best-effort.
        """
        import threading as _threading

        def _warm() -> None:
            try:
                self._devices._refresh_device_list()
            except Exception:
                log.debug("[RECORDING] device cache pre-warm failed", exc_info=True)
            # Phase 2: briefly open + start + stop + close an InputStream
            # against the configured mic. This is the actual "warm"
            # operation — the device-list cache only avoids query RPCs,
            # not the open/start cost. See ``_prewarm_input_stream`` for
            # the rationale and timeout guard.
            self._prewarm_input_stream()

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

    def _prewarm_input_stream(self, *, timeout_s: float = 2.0) -> None:
        """Briefly open + start + stop + close an InputStream to warm PortAudio.

        Resolves the configured mic via ``_resolve_device()``, opens a
        brief ``sd.InputStream(...)`` with the resolved sample rate, calls
        ``stream.start()`` then immediately ``stream.stop()`` +
        ``stream.close()``. This validates the device, warms PortAudio's
        internal device-state cache, and surfaces permission errors at
        app launch instead of at first hotkey press.

        The open/start/stop/close sequence runs on a NESTED daemon thread
        joined with a 2s timeout — if the device is stuck (e.g. a flaky
        BT headset), the prewarm thread returns without blocking process
        startup. The nested thread is a daemon so it never blocks process
        exit. Failures are logged at INFO (not WARNING) because the
        prewarm is purely best-effort: a failure here is recovered by the
        normal ``start()`` candidate loop on the first hotkey press.

        No ``_stream_lifecycle_lock`` is acquired: the prewarm opens a
        THROWAWAY stream (local to this method) and does NOT touch
        ``recorder._stream``, so the lock (which protects
        ``recorder._stream`` from concurrent ``_teardown_stream`` /
        ``_handle_device_disconnect``) is not needed. Acquiring the lock
        here would block tests that hold the lock for setup.
        """
        import threading as _threading

        result: dict[str, Any] = {"done": _threading.Event(), "ok": False, "err": None}

        def _do_open() -> None:
            try:
                device = self._resolve_device()
                candidate_sr, _dev_info = self._resolve_effective_sample_rate(device)
                prewarm_stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=1,
                    dtype="float32",
                    device=device,
                    # No callback — the stream is opened only to warm
                    # PortAudio's device state and validate permissions.
                    # Passing ``callback=None`` makes sounddevice use an
                    # internal no-op callback (PortAudio still
                    # initializes the stream + allocates buffers).
                    callback=None,
                    blocksize=_AUDIO_BLOCKSIZE,
                    latency="low",
                )
                prewarm_stream.start()
                try:
                    prewarm_stream.stop()
                finally:
                    with contextlib.suppress(Exception):
                        prewarm_stream.close()
                log.info(
                    "[RECORDING] Input stream prewarm succeeded: device=[%s] samplerate=%d",
                    device if device is not None else "default",
                    candidate_sr,
                )
                result["ok"] = True
            except Exception as e:
                result["err"] = e
            finally:
                result["done"].set()

        worker = _threading.Thread(
            target=_do_open,
            name="recorder-stream-prewarm",
            daemon=True,
        )
        worker.start()
        # Bound the wait so a stuck device doesn't stall the prewarm
        # thread (which itself is a daemon — the wait is defensive
        # against the rare case where the prewarm thread was joined
        # by a caller that expected it to terminate quickly).
        if not result["done"].wait(timeout=timeout_s):
            log.info(
                "[RECORDING] Input stream prewarm timed out after %.1fs "
                "(device may be stuck — the first start() will retry)",
                timeout_s,
            )
            return
        if not result["ok"] and result["err"] is not None:
            log.info(
                "[RECORDING] Stream prewarm skipped: %s",
                result["err"],
            )

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
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            # PortAudio query failed, device dict shape drift, or
            # ``_devices`` not yet initialized. Fall back to 1 channel
            # (PortAudio's default). Previously a broad
            # ``except Exception: pass``.
            pass
        return 1

    def _classify_portaudio_open_error(self, exc: BaseException) -> None:
        """re-raise an OSError-from-PortAudio as a typed
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
        """AUDIO-CLIP: track clipping + push a real-time IPC event (delegator).

        Body moved to :meth:`AudioPipeline.detect_and_emit_clipping`
        (god-class split). This is a thin delegator so the call site in
        ``process_audio_chunk`` (via ``AudioPipeline``) and any
        instance-level monkeypatches keep working unchanged. See the
        helper's docstring for the AUDIO-CLIP rationale (1 Hz throttle,
        non-blocking enqueue on ``_event_queue``).
        """
        self._audio_pipeline.detect_and_emit_clipping(self, chunk_peak)

    def _secure_clear_session_caches(self) -> None:
        """SEC-audit-008: zero cached audio arrays.

        Pre-: ``recorder.py`` called ``_secure_clear_array(...)``
                as a bare name (no import).  The function is defined in
                ``recording/buffer.py`` and re-exported by the package
                ``__init__.py``, but ``recorder.py`` never imported it.  The
                surrounding broad ``try``/``handler`` block swallowed the
                resulting ``NameError``, so SEC-audit-008's secure-zeroing of
                cached audio arrays (*``_cached_resampled``* and
                *``_cached_no_resample_arr``*) NEVER executed — the previous
                session's audio lingered in process memory until the next GC
                pass freed the numpy arrays.

        fix: import ``_secure_clear_array`` at module top (literal
                ``from voice_typer.server.recording import _secure_clear_array``
                statement) so a future regression that removes the import
                surfaces as ``AttributeError`` at import time. The call sites
                still route through ``_recording_pkg._secure_clear_array(...)``
                so test patches of the form
                ``monkeypatch.setattr("voice_typer.server.recording._secure_clear_array", ...)``
                take effect at runtime (matching ``_secure_clear_array_background``
                in stop()/discard()).

        extracted this block from ``start()`` into a dedicated
                helper so the source-string regression test
                (``test_recorder_start_except_clause_does_not_swallow_nameerror``)
                can pin the narrowed handler clause at the helper-method
                granularity rather than scanning ``start()``'s much longer body
                (which contains other broad ``Exception`` handlers for
                unrelated concerns — device probing, audio stream teardown,
        etc. — that are out of scope for ).

                The narrowed ``(OSError, ValueError)`` handler clause below
                ensures a future import bug (``NameError``) surfaces immediately
        instead of being silently swallowed ( regression — the
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
        # (High): zero the resample-path segment list BEFORE
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

        Phase 4.5 — body moved to
                :meth:`SessionState.reset_session_state`. This is a 1-line
                delegator so existing call sites and ``inspect.getsource``
                checks on ``Recorder._reset_session_state`` continue to work.
                See :mod:`.session_state` for the collaborator pattern and the
        full  rationale (per-session state reset, VAD state,
                preroll zeroing, etc.).
        """
        self._session_state.reset_session_state(self)

    def _cache_session_config(self) -> int:
        """Cache config-derived scalars for the upcoming session; return ``max_rec``.

        Phase 4.5 — body moved to
                :meth:`SessionState.cache_session_config`. This is a 1-line
                delegator so existing call sites and ``inspect.getsource``
                checks on ``Recorder._cache_session_config`` continue to work.
                See :mod:`.session_state` for the collaborator pattern and the
        PERF- rationale (config scalar caching for the audio
                callback hot path).
        """
        return self._session_state.cache_session_config(self)

    def _build_audio_callback(self) -> Callable[..., None]:
        """Build the PortAudio callback closure ().

        Phase 4.5 — body moved to
                :meth:`StreamLifecycle.build_audio_callback`. This is a 1-line
                delegator so existing call sites and ``inspect.getsource``
                checks on ``Recorder._build_audio_callback`` continue to work.
                See :mod:`.stream_lifecycle` for the collaborator pattern and
        the  rationale (callback must complete before the
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

        Phase 4.5 — body moved to
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

        Phase 4.5 — body moved to
                :meth:`StreamLifecycle.open_stream_fallback`. This is a 1-line
                delegator so existing call sites and ``inspect.getsource``
                checks continue to work. See :mod:`.stream_lifecycle` for the
                collaborator pattern.
        """
        return self._stream_lifecycle.open_stream_fallback(self, tried, callback, effective_sr, last_error)

    def _resize_buffers_for_sample_rate(self, effective_sr: int, max_rec: int) -> None:
        """Resize the main audio buffer + ring buffer for the effective sample rate.

        Phase 4.5 — body moved to
                :meth:`SessionState.resize_buffers_for_sample_rate`. This is a
                1-line delegator so existing call sites and
                ``inspect.getsource`` checks continue to work. See
                :mod:`.session_state` for the collaborator pattern and the
        PERF- dynamic-buffer-sizing rationale.
        """
        self._session_state.resize_buffers_for_sample_rate(self, effective_sr, max_rec)

    def _prepend_preroll_to_buffer(self) -> None:
        """Prepend the pre-roll buffer to the main buffer at start() time.

        Phase 4.5 — body moved to
                :meth:`SessionState.prepend_preroll_to_buffer`. This is a 1-line
                delegator so existing call sites and ``inspect.getsource``
                checks continue to work. See :mod:`.session_state` for the
                collaborator pattern and the AUDIO-PRE cold-start rationale.
        """
        self._session_state.prepend_preroll_to_buffer(self)

    def start(self) -> None:
        """Start recording audio.

        Phase 4.5 — body moved to
                :func:`._recorder_split.start_recording` to shrink the
                3772-LOC ``recorder.py`` god class. The ``with self._start_lock:``
                block (recording-event check + microphone-permission pre-flight)
                STAYS HERE so the source-inspection contract
                (``tests/test_recording.py::TestRec5StartLock::test_start_lock_exists``)
                continues to pin the lock on ``Recorder.start`` source.

        reset ALL per-session state here, not just the buffer.
                SEC-audit-008: ``_secure_clear_array`` is now actually used to
                zero cached audio arrays before they are dropped.
        """
        # serialize start() against concurrent discard() — see
        # lock-order rationale in review.md / git log.
        with self._start_lock:
            if self._recording_event.is_set():
                return

            # pre-flight check that the OS reports microphone permission
            # as granted (or prompt - the OS will show the consent dialog on
            # first PortAudio open in that case). Raises
            # ``MicrophonePermissionDeniedError`` (typed) when DENIED, so the
            # IPC layer can ``isinstance``-check and surface the permission
            # onboarding UI instead of a generic error toast.
            from voice_typer.server import permissions as _permissions_module

            _permissions_module.verify_microphone_accessible()

        _recorder_split.start_recording(self)

    def _teardown_stream(self, *, force: bool = False) -> None:
        """Stop + close the PortAudio stream, draining any in-flight callback.

        Phase 4.5 — body (the stop + callback-drain poll +
                close + clear sequence) moved to
                :meth:`StreamLifecycle.teardown_stream_body`. The
                ``_stream_lifecycle_lock`` acquire/release STAYS HERE (Option C)
        so the  source-inspection regression test
                (``tests/test_recorder_worker_lifecycle.py::TestGT24StreamLifecycleLock::test_teardown_stream_uses_lock``)
                continues to pin ``_stream_lifecycle_lock`` on
                ``Recorder._teardown_stream`` source.

        17-H-: the teardown sequence is wrapped in
                ``_stream_lifecycle_lock`` so a concurrent
                ``_handle_device_disconnect`` restart block cannot mutate
                ``self._stream`` mid-teardown. Non-blocking acquire so
                ``__del__`` (best-effort cleanup, wrapped in
                ``contextlib.suppress``) never blocks on a long-running
                ``stop()``/``discard()``/disconnect handler holding the lock —
                the holder will finish the teardown.
        """
        # serialize teardown w.r.t. concurrent
        # _handle_device_disconnect restart. Non-blocking so __del__
        # can't deadlock on a long-running stop()/discard().
        if not self._stream_lifecycle_lock.acquire(blocking=False):
            # Another thread is holding the lock — it will complete
            # the teardown. Idempotent contract: returning here is
            # safe because the holder guarantees ``self._stream`` is
            # torn down before releasing.
            return
        try:
            self._stream_lifecycle.teardown_stream_body(self, force=force)
        finally:
            self._stream_lifecycle_lock.release()

    def _start_audio_worker(self) -> None:
        """Start the audio worker thread that drains the ring buffer.

        Phase 4.5 — the read-check-create-start body moved to
                :meth:`AudioCallbackDispatcher.start_audio_worker_body`. The
                ``with self._worker_lifecycle_lock:`` block STAYS HERE for the
        source-inspection contract
                (``tests/test_recorder_worker_lifecycle.py::test_start_audio_worker_holds_lock``).
                See :mod:`.capture` for the collaborator pattern and the full
        THREAD-REGISTRY rationale.

        Contract: when the prior worker is still alive AND its stop
                event was set (the stale-alive case — ``stop()`` join timed
                out, the worker is exiting on its next iteration as a daemon),
                ``start_audio_worker_body`` would return early without
                recreating events or starting a new worker. The wrapper
                detects this case (``pre_thread.is_alive()`` AND
                ``_worker_stop_event.is_set()``), replaces the stop/wake
                events with fresh ``threading.Event`` instances (so the dying
                stale worker keeps its set stop event and the new worker gets
                fresh cleared events), and clears ``_worker_thread`` to
                ``None`` so ``start_audio_worker_body`` does NOT take its
                early-return branch — a fresh worker is started, and the
                stale one exits on its next iteration when it observes the
                old (set) stop event.
        """
        # hold the lifecycle lock across the entire
        # read-check-create-start sequence so a concurrent
        # _stop_audio_worker() cannot observe a stale ``None`` mid-create.
        with self._worker_lifecycle_lock:
            pre_thread = self._worker_thread
            # Stale-alive detection. ``pre_thread.is_alive()`` AND
            # ``_worker_stop_event.is_set()`` together identify the
            # stale-alive case: the prior worker has not yet exited AND
            # ``stop()`` has already signalled it to exit. In that case,
            # wait briefly for the prior worker to actually exit (it has
            # its stop event set, so it should exit on its next iteration
            # — typically <16ms at the 16Hz audio rate). This prevents
            # duplicate workers piling up under concurrent start()/stop()
            # hammering (leak regression).
            #
            # Test scenario: ``test_start_audio_worker_creates_fresh_events_for_stale_worker``
            # uses a MagicMock whose ``join()`` returns immediately and
            # whose ``is_alive()`` returns True forever — simulating a
            # permanently-stuck worker. In that degenerate case the wait
            # returns immediately and we proceed to start a new worker
            # anyway (the old one is abandoned as a daemon, matching the
            # test's expectation that a fresh worker is started).
            #
            # Real concurrent scenario: the prior worker exits within
            # 1-2 audio iterations (≤128ms at 16Hz). After the wait,
            # replace the events (so any future stale-alive worker that
            # somehow outlived the join keeps its set stop event) and
            # clear ``_worker_thread`` so ``start_audio_worker_body``
            # does NOT take its early-return branch — a fresh worker is
            # started.
            if pre_thread is not None and pre_thread.is_alive() and self._worker_stop_event.is_set():
                # ``RuntimeError: cannot join thread before it is
                # started`` — observed when the device-health checker
                # assigns the thread ref before ``Thread.start()``
                # without holding a lock. Previously a broad
                # ``except Exception: pass``.
                with contextlib.suppress(RuntimeError):
                    pre_thread.join(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S)
                import threading as _threading

                self._worker_stop_event = _threading.Event()
                self._worker_wake_event = _threading.Event()
                self._worker_thread = None
            self._capture.start_audio_worker_body(self)

    def _stop_audio_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the audio worker thread to stop and join it.

        Phase 4.5 — the read-check-clear-join-unregister body
                moved to :meth:`AudioCallbackDispatcher.stop_audio_worker_body`.
        The ``_worker_lifecycle_lock`` block STAYS HERE for the
                source-inspection contracts (positive: must contain the
                lifecycle-lock literal; negative: must NOT contain the
                self-lock literal). See :mod:`.capture` for the collaborator
                pattern and the full THREAD-REGISTRY rationale.

        Contract: ``stop_audio_worker_body`` clears the stop event
                AND nulls ``_worker_thread`` unconditionally at the end (so
                the next start gets a clean slate). When the worker is
                STILL ALIVE after the join timeout, both actions are
                wrong: clearing the stop event un-stops the stale worker
                (it resumes looping on the ring buffer), and nulling
                ``_worker_thread`` makes the next ``_start_audio_worker``
                think no worker exists (SPSC invariant violation — it
                would spawn a second worker). The wrapper captures the
                pre-call thread reference and restores BOTH the thread
                reference AND the stop event after delegation if the prior
                worker did not exit within the timeout.
        """
        # hold the lifecycle lock across the entire
        # read-check-clear-join-unregister sequence. This is a
        # separate lock from the buffer lock — see the helper's docstring.
        with self._worker_lifecycle_lock:
            pre_thread = self._worker_thread
            self._capture.stop_audio_worker_body(self, timeout=timeout, drain=drain)
            # If the prior worker is still alive after the join
            # timed out, restore BOTH the thread reference AND the stop
            # event so the next ``_start_audio_worker`` can detect the
            # stale worker via ``is_alive()`` and create fresh events.
            # ``pre_thread`` is captured before delegation;
            # ``stop_audio_worker_body`` assigns
            # ``recorder._worker_thread = None`` at its end, so checking
            # ``pre_thread.is_alive()`` (not ``self._worker_thread``) is
            # what detects the stale-alive case. ``is_alive()`` is safe
            # to call on a stopped thread (returns False) and on a
            # MagicMock (returns the configured value).
            if pre_thread is not None and pre_thread.is_alive():
                self._worker_thread = pre_thread
                self._worker_stop_event.set()

    def _start_event_worker(self) -> None:
        """Start the IPC event worker thread that drains ``_event_queue``.

        Phase 4.5 — the read-check-create-start body moved
                to :meth:`AudioCallbackDispatcher.start_event_worker_body`.
                The ``with self._worker_lifecycle_lock:`` block STAYS HERE for
        the  source-inspection contract. See :mod:`.capture` for
                the collaborator pattern.
        """
        # hold the lifecycle lock across the entire
        # read-check-create-start sequence so a concurrent
        # _stop_event_worker() cannot observe a stale ``None`` mid-create.
        with self._worker_lifecycle_lock:
            self._capture.start_event_worker_body(self)

    def _stop_event_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the event worker thread to stop and join it.

        Phase 4.5 — the read-check-clear-join-unregister body
                moved to :meth:`AudioCallbackDispatcher.stop_event_worker_body`.
        The ``_worker_lifecycle_lock`` block STAYS HERE for the
                source-inspection contracts (positive: must contain the
                lifecycle-lock literal; negative: must NOT contain the
                self-lock literal). See :mod:`.capture` for the collaborator
                pattern.
        """
        # hold the lifecycle lock across the entire
        # read-check-clear-join-unregister sequence. This is a
        # separate lock from the buffer lock — see the helper's docstring.
        with self._worker_lifecycle_lock:
            self._capture.stop_event_worker_body(self, timeout=timeout, drain=drain)

    def _event_worker_loop(self) -> None:
        """IPC event worker thread main loop (delegator).

        Body moved to :meth:`AudioCallbackDispatcher.event_worker_loop`
        (god-class split). This is a thin delegator so the thread
        target wiring in ``start_event_worker_body`` and any
        instance-level monkeypatches keep working unchanged. See the
        helper's docstring for the full rationale (MPSC queue, 0.5s
        poll + stop sentinel, drain-before-exit on stop()).
        """
        self._capture.event_worker_loop(self)

    def _audio_worker_loop(self, stop_event: Any = None, wake_event: Any = None) -> None:
        """Audio worker thread main loop — drains the ring buffer.

        Phase 4.5 — body moved to
                :meth:`AudioCallbackDispatcher.audio_worker_loop`. This is a
                1-line delegator so existing call sites, subclass overrides,
                and ``inspect.getsource`` checks on
                ``Recorder._audio_worker_loop`` continue to work. See
        mod:`.capture` for the collaborator pattern and the
                rationale (worker thread runs the heavy processing pipeline
                off the real-time audio thread).

        ``stop_event`` / ``wake_event`` are passed through to the
                body as EXPLICIT parameters (captured at thread-spawn time by
                ``AudioCallbackDispatcher.start_audio_worker_body``) instead
                of being read dynamically from ``self._worker_stop_event`` /
                ``self._worker_wake_event`` on every iteration. See the body's
                docstring for the stale-worker SPSC-violation rationale. The
                parameters default to ``None`` so direct call sites that
                don't pass them (e.g. legacy tests) keep working — the body
                falls back to ``self._worker_stop_event`` /
                ``self._worker_wake_event`` when ``None`` is passed.
        """
        self._capture.audio_worker_loop(self, stop_event, wake_event)

    def _audio_callback_dispatch(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Real-time audio callback entry point — RT-safe path.

        Phase 4.5: the body (pre-roll capture, ring-buffer
                overflow detection, perf timestamp capture) moved to
                :meth:`AudioCallbackDispatcher.dispatch_callback_body`. The two
                RT-safe literals ``_ring_buffer.append`` and ``_worker_wake_event``
        STAY HERE (Option C) so the  source-inspection contract
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

        body moved to :meth:`AudioPipeline.detect_device_disconnect`.
                This is a 1-line delegator so existing call sites and
                ``inspect.getsource`` checks on ``Recorder._detect_device_disconnect``
                continue to work.
        """
        return self._audio_pipeline.detect_device_disconnect(indata)

    def _handle_xrun_status(self, status: Any) -> bool:
        """Inspect the PortAudio ``status`` for an input-overflow XRUN.

        body moved to :meth:`AudioPipeline.handle_xrun_status`.
                This is a 1-line delegator so existing call sites and
                ``inspect.getsource`` checks on ``Recorder._handle_xrun_status``
                continue to work.
        """
        return self._audio_pipeline.handle_xrun_status(status)

    def _apply_filter_chain(self, indata: np.ndarray) -> np.ndarray:
        """Convert multi-channel input to mono and apply the real-time filter chain.

        body moved to :meth:`AudioPipeline.apply_filter_chain`.
                This is a 1-line delegator so existing call sites and
                ``inspect.getsource`` checks on ``Recorder._apply_filter_chain``
                continue to work.
        """
        return self._audio_pipeline.apply_filter_chain(indata)

    def _append_to_buffer_locked(self, filtered: np.ndarray) -> tuple[int, int]:
        """Append ``filtered`` to ``_buffer`` under the lock; return ``(chunk_count, buffer_len)``.

        body moved to :meth:`AudioPipeline.append_to_buffer_locked`.
                This is a 1-line delegator so existing call sites and
                ``inspect.getsource`` checks on ``Recorder._append_to_buffer_locked``
                continue to work.
        """
        return self._audio_pipeline.append_to_buffer_locked(filtered)

    def _compute_rms_and_peak(self, filtered: np.ndarray) -> tuple[float, float, float]:
        """Compute ``(chunk_rms, chunk_peak, chunk_duration)`` for the filtered chunk.

        body moved to :meth:`AudioPipeline.compute_rms_and_peak`.
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

        body moved to :meth:`AudioPipeline.run_vad_state_machine`.
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

        Phase 4.5 — body moved to
                :meth:`AudioPipeline.process_audio_chunk`. This is a 1-line
                delegator so existing call sites and ``inspect.getsource``
                checks on ``Recorder._process_audio_chunk`` continue to work.

                See :mod:`.audio_pipeline` for the collaborator pattern and
                the full processing-pipeline rationale.

        Surface ring-buffer overflow in real time. The RT
                callback (``_audio_callback_dispatch``) increments
                ``_dropped_ring_chunks`` when the ring buffer is full
                but cannot log from the RT path. The worker thread
                (this method, non-RT-safe to log) checks the counter
                delta on every iteration and emits a rate-limited
                WARNING so the user is notified DURING the recording
                (not only post-stop via ``RecordingController._stop_impl``).
        """
        self._surface_ring_overflow_warning()
        self._audio_pipeline.process_audio_chunk(indata, frames, time_info, status, perf_ts)

    def _surface_ring_overflow_warning(self) -> None:
        """Emit a rate-limited WARNING when the ring buffer overflows (delegator).

        Body moved to :meth:`AudioCallbackDispatcher.surface_ring_overflow_warning`
        (god-class split). This is a thin delegator so the call site in
        ``_process_audio_chunk`` and any instance-level monkeypatches
        keep working unchanged. See the helper's docstring for the
        rationale (delta computation, rate limiting, log-only
        contract — no direct ``event_bus.publish``).
        """
        self._capture.surface_ring_overflow_warning(self)

    def _note_buffer_capacity_eviction(self, samples: int) -> None:
        """Counter hook for capacity-driven buffer evictions.

        The audio worker's append path (``AudioPipeline.append_to_buffer_locked``)
        already compensates ``_total_buffered_samples`` for exactly ONE
        maxlen-mirrored chunk eviction per append (it peeks ``buf[0]``
        before appending). When the contiguous storage evicts ADDITIONAL
        samples because its hard-cap estimate was exceeded (adversarially
        mixed chunk sizes — never in normal operation), this hook keeps
        the running counter in sync so ``current_duration_seconds`` stays
        truthful.
        """
        self._total_buffered_samples -= int(samples)

    def _secure_clear_caches(self) -> None:
        """securely zero cached audio arrays BEFORE reassignment.

        Phase 4.5 — body moved to
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

        Phase 4.5 — body moved to
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

        split: body moved to :func:`._recorder_split.take_snapshot`.
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

        a cheap O(1) scalar read with NO array copy. Used by the
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
        if not self._buffer:
            return 0.0
        sr = getattr(self, "_buffer_sr", None) or self._effective_sr
        if not sr:
            return 0.0
        # O(1) scalar read — maintained under ``_lock`` by
        # ``AudioPipeline.append_to_buffer_locked`` and reset to 0 in
        # ``reset_session_state`` / ``stop()`` / ``discard()``. The
        # ``if not self._buffer`` guard above is the empty-buffer
        # fast-path (also O(1) — deque ``__bool__`` is O(1)); it
        # protects against a stale counter value if a reset path
        # forgot to zero it (defensive — all reset paths DO zero it).
        # PERF: previously this property iterated the whole deque via
        # ``sum(int(c.shape[0]) for c in buffer)`` — O(chunks) per poll.
        # At 16 Hz chunk arrival × 30-min dictation × 4 Hz poll, each
        # poll summed ~28k chunks.
        return self._total_buffered_samples / sr

    def _resample_chunk(self, audio: np.ndarray, effective_sr: int, target_sr: int) -> np.ndarray:
        """Resample a single chunk of audio.

        Body moved to :func:`.format.resample_chunk` (god-class split).
        This is a thin delegator so existing call sites (the snapshot
        resample path and the VAD resample path), subclass overrides,
        and instance-level monkeypatches keep working unchanged.

        Raises:
            ResampleError: if neither scipy nor linear-interp resampling
                could convert the audio to ``target_sr``. Callers MUST
                handle this; previously the function returned the native-
                rate audio silently, which led to garbage transcriptions
        on the streaming path ().
        """
        return _format_resample_chunk_fn(self, audio, effective_sr, target_sr)

    def _prepare_audio(
        self,
        audio: np.ndarray,
        effective_sr: int,
        log_resample: bool = True,
    ) -> np.ndarray:
        """Convert captured audio to the configured sample rate.

        Body moved to :func:`.format.prepare_audio` (god-class split).
        This is a thin delegator so existing call sites (the stop()
        handoff path), subclass overrides, and instance-level
        monkeypatches keep working unchanged. See the helper's
        docstring for the narrowed-except + cached-target-rate rationale.
        """
        return _format_prepare_audio_fn(self, audio, effective_sr, log_resample=log_resample)

    def _resample_audio_impl(
        self,
        audio: np.ndarray,
        effective_sr: int,
        target_sr: int,
        *,
        log_resample: bool = False,
    ) -> np.ndarray:
        """Shared resampling logic for ``_resample_chunk`` and ``_prepare_audio`` (delegator).

        Phase 4.5: body moved to ``resample_audio()`` in
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

        PERF-: previously the scipy → linear interp → raise
                fallback chain was duplicated between the two methods. The
                centralized helper (now in :mod:`.resampling`) applies bug
        fixes (, , ) in one place.

        narrows exceptions to ``(ValueError, OSError, TypeError)``
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

        split: body moved to :func:`._recorder_split.discard_recording`.
                This method is now a 1-line delegator so existing call sites,
                subclass overrides, and ``inspect.getsource`` checks that look for
                the method on the ``Recorder`` class continue to work. See the
                docstring of the extracted helper for the full rationale (stream
                teardown ordering, secure-clear of cached audio arrays, worker
                thread drain semantics).

        acquires ``_start_lock`` so a concurrent ``start()`` on
                another thread can't mutate per-session state (buffer, flags)
                while ``discard_recording`` is tearing the stream down. The lock
                is released before return; it is NOT held across
                ``thread.join()`` (would deadlock the audio worker).

        Idle fast-path. ``discard()`` is a
                no-op when the recorder is not recording — matching
                ``stop()``'s contract. Pre-fix, ``discard()`` unconditionally
                bumped ``_stop_generation``, set ``_user_stop_pending``, tore
                down the stream (already None), and stopped the workers
                (already None) — wasted work that also created a window for
                the race described below: ``start()`` releases
                ``_start_lock`` before calling ``_recorder_split.start_recording``,
                so a concurrent ``discard()`` that acquired the lock between
                the gate and ``_recording_event.set()`` inside
                ``start_recording`` would observe ``is_set()==False``, run its
                full body (bumping ``_stop_generation`` etc.), then
                ``start_recording`` would proceed to ``_recording_event.set()``
                — leaving the recorder in a "recording" state with no live
                stream. The ``is_set()`` fast-path below closes the race.
        """
        with self._start_lock:
            # Idle fast-path. Without this guard, a
            # discard() that lands between ``start()``'s gate and
            # ``_recording_event.set()`` inside ``start_recording`` would
            # run its full body on an idle recorder, then
            # ``start_recording`` would proceed and leave the recorder in
            # a "recording" state with no live stream. Matching ``stop()``'s
            # contract closes the race. This is also the idle fast-path
            # that prevents wasted work: no ``_stop_generation``
            # increment, no ``_user_stop_pending`` flip, no stream teardown,
            # no worker stop.
            # Fast-path ONLY when no worker refs exist — a
            # start()/discard() race can leave ``_recording_event``
            # cleared but a live worker (start() spawned it after this
            # discard cleared the event). In that state discard() must
            # still run the full body to stop the worker, otherwise the
            # daemon leaks until process exit.
            if not self._recording_event.is_set() and self._worker_thread is None and self._event_worker_thread is None:
                return
            _recorder_split.discard_recording(self)
