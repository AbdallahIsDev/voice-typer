"""Recorder: session-based microphone recording.

Split bodies live on collaborators; Recorder keeps 1-line delegators for
call sites, subclass overrides, and getsource. Patch owning submodules
(C-ARCH-2). NOTE: see docs/code-notes/recording.md#package-layout
"""

from __future__ import annotations

import collections
import contextlib
import logging
import math  # noqa: F401  # re-exported for tests (recorder.math)
import threading
import time
from typing import Any

from voice_typer.server._audio_constants import (  # noqa: F401  # SILERO_VAD_SAMPLE_RATES / _AUDIO_BLOCKSIZE re-exported for tests
    _AUDIO_BLOCKSIZE,
    SILERO_VAD_SAMPLE_RATES,
    WHISPER_SAMPLE_RATE,
)
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.config import Config

# re-exported for tests / back-compat; construction use lives on ``RecorderInitMixin`` now.
from voice_typer.server.vad_processor import VadProcessor, VadState  # noqa: F401

# lazy proxies: this module must NOT import numpy / sounddevice at import time.
sd = lazy_module("sounddevice")
np = lazy_module("numpy")

log = logging.getLogger("voice_typer.server.recording")

# C-ARCH-2: owning-module import keeps recorder.X test-importable.
from voice_typer.server.recording import _secure_clear_array  # noqa: F401, E402

from . import _recorder_split  # noqa: E402

# Collaborators (re-exported for tests).
from .audio_pipeline import AudioPipeline  # noqa: F401, E402, re-exported for tests
from .capture import AudioCallbackDispatcher  # noqa: F401, E402, re-exported for tests
from .device_manager import DeviceManager  # noqa: F401, E402, re-exported for tests
from .device_prewarm import DevicePrewarm  # noqa: F401, E402, re-exported for tests
from .disconnect_handler import DisconnectHandler  # noqa: F401, E402, re-exported for tests
from .exceptions import (  # noqa: F401, E402, re-exported for tests
    ResampleError,
    ResampleUnavailable,
    ResampleUnavailableError,
)
from .format import ensure_mono, prepare_audio, resample_chunk  # noqa: F401, E402, re-exported for tests
from .recorder_init import RecorderInitMixin  # noqa: F401, E402
from .resampling import (  # noqa: E402, re-exported for tests (post-comment import)
    _SCIPY_PRELOADER_JOIN_TIMEOUT_S,  # noqa: F401, re-exported for tests
    warm_up_resampler,  # noqa: F401, re-exported for tests / delegation
)
from .session_state import SessionState  # noqa: F401, E402, re-exported for tests
from .stream_lifecycle import StreamLifecycle  # noqa: F401, E402, re-exported for tests
from .vad_helpers import (  # noqa: F401, E402
    refresh_vad_caches as _refresh_vad_caches_fn,  # noqa: F401, E402
)

# Source-pinned: teardown lock + RT ring/worker literals stay on Recorder methods.

# PERF: start() resizes beyond this when sample-rate×max-time needs more room.
DEFAULT_MAX_BUFFER_CHUNKS = 30000
from .audio_pipeline import (  # noqa: E402, F401, re-exported for back-compat
    BUFFER_WARNING_THRESHOLD,
    TELEMETRY_LOG_INTERVAL,
)

# RT callback contract: only preroll + SPSC ring append + Event signal (~32ms).
_AUDIO_RING_BUFFER_CAPACITY = 64

_RING_OVERFLOW_WARN_INTERVAL_S = 5.0

_AUDIO_WORKER_THREAD_NAME = "audio-worker"
# stop() join is generous (ring drain + final VAD); discard() shorter.
_AUDIO_WORKER_JOIN_TIMEOUT_S = 2.0
_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S = 1.0

_EVENT_WORKER_THREAD_NAME = "event-worker"


# Wake event worker from 0.5s poll on stop (avoids lingering daemons).
class _EventWorkerStopSentinel:
    """Marker pushed onto the event queue to wake the worker on stop."""

    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_EVENT_WORKER_STOP_SENTINEL = _EventWorkerStopSentinel()
# stop() join timeout is generous (drain the queue; queue is tiny, 1 Hz events);
_EVENT_WORKER_JOIN_TIMEOUT_S = 2.0
_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S = 1.0


class Recorder(RecorderInitMixin):
    """Records audio from microphone into a buffer. Session-based: start, accumulate, stop, get data."""

    # PortAudio permission-denial substrings; classify re-raises as typed error.
    _PORTAUDIO_PERMISSION_DENIED_SUBSTRINGS: tuple[str, ...] = (
        "Unanticipated host error",
        "No input devices available",
        "Invalid number of channels",
        "Invalid sample rate",
        "Device unavailable",
        "Could not retrieve device info",
    )

    # Flapping-BT-mic detection state, assigned by ``SessionState.init_session_state``;
    _restart_timestamps: collections.deque
    _flapping_max_restarts: int
    _flapping_window_seconds: float

    # Preloader thread spawned by ``RecorderInitMixin._register_scipy_preloader``;
    _scipy_preloader_thread: threading.Thread | None

    def __init__(
        self,
        config: Config,
        audio_processor: Any | None = None,
        thread_registry: Any | None = None,
    ):
        """Construct via RecorderInitMixin helpers (same order as historical __init__)."""
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
        # Device / disconnect-handler state declarations + collaborator
        self._setup_device_state_and_collaborators()
        self._init_silence_detection()
        self._register_scipy_preloader()

    @property
    def recording(self) -> bool:
        return self._recording_event.is_set()

    @property
    def _xruns(self) -> int:
        """Read-only bridge to the AudioPipeline-owned xrun counter."""
        return self._audio_pipeline._xruns

    @property
    def last_rms(self) -> float:
        """RMS level of the most recently captured audio (0.0 if never recorded)."""
        # STATE-OWNERSHIP: the buffer lock lives on the owning ``AudioPipeline``.
        with self._audio_pipeline._lock:
            return self._last_rms

    def shutdown_mic_watcher(self) -> None:
        """Stop the mic device-change watcher (delegator to ``DeviceManager``);"""
        if self._force_closed:
            return
        return self._devices.shutdown_mic_watcher()

    def __del__(self) -> None:
        """Best-effort cleanup; must never raise. Each attribute access happens"""
        for step in (
            lambda: self.shutdown_mic_watcher(),
            lambda: self._recording_event.clear(),
            lambda: self._worker_stop_event.set(),
            lambda: self._event_stop_event.set(),
            lambda: self._devices._device_health_stop_event.set(),
            lambda: self._teardown_stream(),
        ):
            with contextlib.suppress(Exception):
                step()

    # ``_handle_device_disconnect`` (bouncers + BT-aware retry policy + restart

    def _spawn_device_thread(self, *args: Any, **kwargs: Any) -> bool:
        """Spawn a daemon device-path thread (delegator; body on ``DisconnectHandler.spawn_device_thread``)."""
        return self._disconnect_handler.spawn_device_thread(self, *args, **kwargs)

    def _stream_finished_callback(self) -> None:
        """PortAudio ``finished_callback`` entry point (delegator; body on
        ``DisconnectHandler.stream_finished_callback_body``)."""
        self._disconnect_handler.stream_finished_callback_body(self)

    def _handle_device_disconnect(self, _captured_generation: int = 0) -> None:
        """Attempt to restart recording with the default device after a disconnect."""
        # HOTKEY-CRASH: a stop/start cycle since scheduling replaced the stream.
        if _captured_generation != self._stop_generation:
            log.debug(
                "[RECORDING] Disconnect handler skipped, stop_generation changed (%d != %d)",
                _captured_generation,
                self._stop_generation,
            )
            return
        # HOTKEY-CRASH: recording was deliberately stopped, don't restart.
        if not self._recording_event.is_set():
            log.debug("[RECORDING] Disconnect handler skipped, recording was deliberately stopped")
            return

        self._devices._device_disconnect_retries += 1
        # BT HFP/HSP mode-switch retry policy: headsets take 1-3s to switch from
        _device_info = self._devices._build_device_info_for_retry_policy()
        _effective_max_retries = self._devices._get_max_retries_for_device(_device_info)
        _retry_sleep = self._devices._get_retry_sleep_for_device(_device_info)
        if self._devices._device_disconnect_retries > _effective_max_retries:
            log.error(
                "[RECORDING] Max disconnect retries (%d) reached. Stopping recording.",
                _effective_max_retries,
            )
            # ``on_device_lost`` so the UI shows "Microphone disconnected", not
            _device_lost_cb = getattr(self, "on_device_lost", None)
            if callable(_device_lost_cb):
                with contextlib.suppress(Exception):
                    _device_lost_cb()
            elif self.on_silence_auto_stop is not None:
                with contextlib.suppress(Exception):
                    self.on_silence_auto_stop()
            # Clear the flag (health-checker re-probes) + reset the retry
            self._devices._device_disconnected = False
            self._devices._device_disconnect_retries = 0
            return

        log.warning(
            "[RECORDING] Device disconnect detected (attempt %d/%d). Attempting restart with default device.",
            self._devices._device_disconnect_retries,
            _effective_max_retries,
        )

        if _retry_sleep > 0.0 and self._devices._device_disconnect_retries > 1:
            time.sleep(_retry_sleep)

        # ``_teardown_stream`` polls ``self._is_in_audio_callback`` for up to
        self._teardown_stream(force=True)

        # Hold the stream-lifecycle lock across the restart so a concurrent
        with self._stream_lifecycle_lock:
            if _captured_generation != self._stop_generation:
                log.debug(
                    "[RECORDING] Disconnect restart skipped, stop_generation changed (%d != %d)",
                    _captured_generation,
                    self._stop_generation,
                )
                return
            if not self._recording_event.is_set():
                log.debug("[RECORDING] Disconnect restart skipped, recording was deliberately stopped")
                return

            # Restart tail; the handler re-checks the generation a third time
            self._disconnect_handler.restart_stream(_captured_generation)

    def _stop_device_health_checker(self, timeout: float | None = None) -> None:
        """Signal the device health checker to stop and join it (delegator; body
        on ``DeviceManager.stop_device_health_checker``)."""
        self._devices.stop_device_health_checker(timeout)

    # PERF-02 (c-review): max age (s) before cached ``_vad_enabled`` is
    _VAD_ENABLED_CACHE_TTL_S: float = 5.0

    def on_config_changed(self) -> None:
        """Refresh cached config-derived state after a config change (PERF-02):
        delegates to ``self._vad.on_config_changed()`` (VadProcessor owns the
        ``vad_enabled`` cache). Safe from any thread; no-op before init."""
        self._vad.on_config_changed()
        # Refresh per-chunk VAD caches so the 16 Hz worker hot path reads cached
        _refresh_vad_caches_fn(self)

    def warm_up_resampler(self) -> None:
        """Import and initialize the high-quality resampler before recording stops;"""
        warm_up_resampler(self)

    # Device-resolution / stream-open / SessionState / StreamLifecycle pure

    def _prewarm_device_cache(self) -> None:
        """Spawn a best-effort daemon thread to prewarm the device cache
        (delegator; body on ``DevicePrewarm.prewarm_device_cache``)."""
        self._device_prewarm.prewarm_device_cache()

    def _prewarm_input_stream(self, *, timeout_s: float = 2.0) -> None:
        """Briefly open + start + stop + close an InputStream to warm PortAudio
        (delegator; body on ``DevicePrewarm.prewarm_input_stream``)."""
        self._device_prewarm.prewarm_input_stream(timeout_s=timeout_s)

    def _cached_max_input_channels(self, device: int | None) -> int:
        """Return ``max_input_channels`` for ``device`` from the cached device
        list (delegator; body on ``DevicePrewarm.cached_max_input_channels``)."""
        return self._device_prewarm.cached_max_input_channels(device)

    def _classify_portaudio_open_error(self, exc: BaseException) -> None:
        """Re-raise PortAudio OSErrors as typed permission errors (delegator;
        body on ``DevicePrewarm.classify_portaudio_open_error``)."""
        self._device_prewarm.classify_portaudio_open_error(exc)

    def _secure_clear_session_caches(self) -> None:
        """zero cached audio arrays before they are dropped."""
        try:
            if self._cached_resampled is not None and self._cached_resampled.size > 0:
                _secure_clear_array(self._cached_resampled)
        except (OSError, ValueError):
            log.warning("[RECORDER] secure_clear_array failed", exc_info=True)
        try:
            if self._cached_no_resample_arr is not None and self._cached_no_resample_arr.size > 0:
                _secure_clear_array(self._cached_no_resample_arr)
        except (OSError, ValueError):
            log.warning("[RECORDER] secure_clear_array failed", exc_info=True)
        # its own defensive zeroing (belt-and-suspenders vs racing snapshots).
        try:
            for seg in self._cached_resampled_segments:
                if seg is not None and seg.size > 0:
                    _secure_clear_array(seg)
        except (OSError, ValueError):
            log.warning(
                "[RECORDER] secure_clear_array failed for _cached_resampled_segments",
                exc_info=True,
            )
        self._cached_resampled_segments = []
        self._cached_resampled_concat_dirty = False

    def start(self) -> None:
        """Start recording audio."""
        # Serialize start() against concurrent discard().
        with self._start_lock:
            if self._recording_event.is_set():
                return

            # Pre-flight: raises ``MicrophonePermissionDeniedError`` (typed) on
            from voice_typer.server import permissions as _permissions_module

            _permissions_module.verify_microphone_accessible()

        _recorder_split.start_recording(self)

    def _teardown_stream(self, *, force: bool = False) -> None:
        """Stop + close the PortAudio stream, draining any in-flight callback."""
        if not self._stream_lifecycle_lock.acquire(blocking=False):
            return
        try:
            self._stream_lifecycle.teardown_stream_body(self, force=force)
        finally:
            self._stream_lifecycle_lock.release()

    def _start_audio_worker(self) -> None:
        """Start the audio worker thread that drains the ring buffer.

        Body lives on ``AudioCallbackDispatcher.start_audio_worker_body``; the
        ``with self._worker_lifecycle_lock:`` block STAYS HERE (pin:
        ``test_start_audio_worker_holds_lock``; behavioral: RuntimeError-narrowed
        join guard, stale-worker fresh-events). Stale-alive contract: when the
        prior worker is alive AND its stop event is set, replace both events with
        fresh ones and clear ``_worker_thread`` so the body starts a fresh worker.
        """
        with self._worker_lifecycle_lock:
            pre_thread = self._worker_thread
            # Stale-alive detection: wait briefly for the prior worker to exit
            if pre_thread is not None and pre_thread.is_alive() and self._worker_stop_event.is_set():
                # ``RuntimeError: cannot join thread before it is started`` —
                with contextlib.suppress(RuntimeError):
                    pre_thread.join(timeout=_AUDIO_WORKER_JOIN_TIMEOUT_S)
                self._worker_stop_event = threading.Event()
                self._worker_wake_event = threading.Event()
                self._worker_thread = None
            self._capture.start_audio_worker_body(self)

    def _stop_audio_worker(self, *, timeout: float, drain: bool = True) -> None:
        """Signal the audio worker thread to stop and join it.

        Body lives on ``AudioCallbackDispatcher.stop_audio_worker_body``; the
        ``_worker_lifecycle_lock`` block STAYS HERE (lock literal present,
        self-lock literal absent). Stale-alive contract: the body clears the stop
        event and nulls ``_worker_thread`` unconditionally; if the worker is STILL
        alive after the join timeout both are wrong, so restore the ref + event.
        """
        with self._worker_lifecycle_lock:
            pre_thread = self._worker_thread
            self._capture.stop_audio_worker_body(self, timeout=timeout, drain=drain)
            # Restore BOTH the thread reference AND the stop event for the
            if pre_thread is not None and pre_thread.is_alive():
                self._worker_thread = pre_thread
                self._worker_stop_event.set()

    # Deleted pure delegators ``_start_event_worker`` / ``_stop_event_worker`` /

    def _audio_callback_dispatch(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Real-time audio callback entry point. RT-safe path.

        Body lives on ``AudioCallbackDispatcher.dispatch_callback_body``. The
        RT-safe literals ``_ring_buffer.append`` and ``_worker_wake_event`` STAY
        HERE (pin: ``test_callback_does_not_do_heavy_processing``). The helper
        returns ``None`` for the pre-roll / early-bailout path, else a 5-tuple.
        """
        payload = self._capture.dispatch_callback_body(self, indata, frames, time_info, status)
        if payload is None:
            return  # pre-roll / early-bailout path
        self._ring_buffer.append(payload)
        self._worker_wake_event.set()

    # deleted pure delegators to ``AudioPipeline`` (disconnect detection, xrun

    def _process_audio_chunk(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
        perf_ts: float,
    ) -> None:
        """Process a single audio chunk, runs on the worker thread."""
        self._capture.surface_ring_overflow_warning(self)
        self._audio_pipeline.process_audio_chunk(indata, frames, time_info, status, perf_ts)

    # deleted pure delegator ``_surface_ring_overflow_warning`` —

    def _note_buffer_capacity_eviction(self, samples: int) -> None:
        """Counter hook for capacity-driven buffer evictions: ``append_to_buffer_locked``"""
        # STATE-OWNERSHIP: the counter lives on the owning ``AudioPipeline``.
        self._audio_pipeline._total_buffered_samples -= int(samples)

    # deleted pure delegator ``_secure_clear_caches``, the bulk secure-clear

    def stop(self) -> np.ndarray:
        """Stop recording and return the complete audio array."""
        return _recorder_split.stop_recording(self)

    def snapshot(self) -> np.ndarray:
        """Return current recorded audio without clearing the active buffer."""
        return _recorder_split.take_snapshot(self)

    @property
    def current_duration_seconds(self) -> float:
        """Approximate duration (in seconds) of audio currently in the buffer."""
        if not self._audio_pipeline._buffer:
            return 0.0
        # STATE-OWNERSHIP: ``_buffer_sr`` / ``_total_buffered_samples`` live on
        sr = self._audio_pipeline._buffer_sr or self._effective_sr
        if not sr:
            return 0.0
        # O(1) scalar read, maintained under the pipeline's ``_lock`` by
        return self._audio_pipeline._total_buffered_samples / sr

    # deleted pure delegators ``_resample_chunk`` / ``_prepare_audio`` /

    def discard(self) -> None:
        """Discard current recording without processing."""
        with self._start_lock:
            # Without this guard, a discard() landing between ``start()``'s gate
            if not self._recording_event.is_set() and self._worker_thread is None and self._event_worker_thread is None:
                return
            _recorder_split.discard_recording(self)
