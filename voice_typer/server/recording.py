"""Session-based audio recording."""

from __future__ import annotations

import collections
import enum
import logging
import math
import threading
import time
from typing import Any, Callable, Optional

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
from voice_typer.server._lazy_import import lazy_module

sd = lazy_module("sounddevice")

from voice_typer.server.config import Config

log = logging.getLogger(__name__)


# ─── AUDIO-013: VAD state machine ───────────────────────────────────────


class VadState(enum.Enum):
    """VAD state-machine states with hysteresis transitions.

    SILENCE → SPEECH requires ``_vad_speech_frames`` consecutive loud frames.
    SPEECH → SILENCE requires ``_vad_silence_frames`` consecutive quiet frames.
    UNKNOWN is the initial state before enough frames have been observed.
    """
    SILENCE = "silence"
    SPEECH = "speech"
    UNKNOWN = "unknown"


# AUDIO-014: default VAD thresholds (overridden by auto-calibration)
_DEFAULT_VAD_SPEECH_THRESHOLD_DB = -40.0  # dBFS — above this → speech candidate
_DEFAULT_VAD_SILENCE_THRESHOLD_DB = -50.0  # dBFS — below this → silence candidate
_DEFAULT_VAD_CALIBRATION_DURATION = 1.5    # seconds of ambient noise to sample
_DEFAULT_VAD_SPEECH_FRAMES = 3   # consecutive loud frames to declare SPEECH
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


# AUDIO-DEAD: dead-air timeout — auto-stop recording after N seconds
# of continuous silence. Prevents indefinitely long recordings when the
# user walks away from the mic. Configurable via config.dead_air_timeout
# (0 = disabled). Default is 30 seconds of silence after speech was detected.
_DEFAULT_DEAD_AIR_TIMEOUT = 30.0  # seconds of silence before auto-stop


# AUDIO-002: XRUN rolling window parameters
_XRUN_WINDOW_MAXLEN = 10     # keep last 10 xrun timestamps
_XRUN_ALERT_THRESHOLD = 5    # alert if N xruns in the window
_XRUN_ALERT_PERIOD = 10.0    # ...within M seconds


class ResampleError(RuntimeError):
    """Raised when audio cannot be resampled to the target sample rate.

    ERR-001: Previously the resample fallback returned the native-rate
    audio silently, which produced garbage transcriptions because the
    streaming path assumed the configured sample rate. Callers must
    catch this exception and decide how to handle the failure (skip
    the chunk, abort the dictation, or notify the user).
    """


def _secure_clear_array(arr: np.ndarray) -> None:
    """SEC-audit-008: Securely clear a numpy array's contents before deallocation.

    Fills the array with zeros using ``np.fill()`` to prevent forensic
    recovery of audio data from process memory.  Call this before
    ``del`` or before the array goes out of scope when the array
    contains sensitive audio data (voice recordings that may contain
    PII).

    Parameters
    ----------
    arr : np.ndarray
        Numpy array to zero out in-place.
    """
    try:
        arr.fill(0)
    except Exception:
        pass  # best-effort; some array types may not support fill

# PERF-NEW-018: MAX_BUFFER_CHUNKS is now dynamically adjusted in
# start() based on max_recording_seconds.  The default below is a
# safe ceiling (30K chunks * 1024 samples/chunk / 16kHz ≈ 30 min).
# For longer recordings, start() increases the deque maxlen.
DEFAULT_MAX_BUFFER_CHUNKS = 30000
BUFFER_WARNING_THRESHOLD = 5000
TELEMETRY_LOG_INTERVAL = 1000

_resample_poly = None
_resample_poly_error: Exception | None = None
# AUDIO-003: track when the error was cached so we can retry after a timeout
_resample_poly_error_time: float = 0.0
_RESAMPLE_RETRY_INTERVAL = 300.0  # Retry every 5 minutes
_resample_poly_lock = threading.Lock()


# PERF-001: eagerly preload scipy.signal.resample_poly at module import
# so the first recording doesn't block 200-800ms on the import.  This
# runs in a background daemon thread to avoid slowing down module
# import for callers that don't record (e.g. the IPC server's
# get_status handler).  If scipy isn't installed, the error is cached
# and the lazy path in _get_resample_poly raises it on first use.
def _preload_resample_poly() -> None:
    """Background preloader for scipy.signal.resample_poly."""
    try:
        from scipy.signal import resample_poly  # noqa: F401
        _get_resample_poly()
    except Exception:
        # Error will be cached by _get_resample_poly on first real use.
        pass


threading.Thread(
    target=_preload_resample_poly,
    name="scipy-preloader",
    daemon=True,
).start()


class ResampleUnavailable(RuntimeError):
    """Raised when scipy.signal.resample_poly is unavailable.

    ARCH-033: the 3-tier fallback (scipy → linear interp → native)
    previously failed silently at each tier. We now raise this typed
    exception at the scipy tier so the caller knows the high-quality
    path is unavailable and can decide whether to use linear interp.
    """


def _get_resample_poly():
    """Load scipy's resampler once so imports do not happen on F2 stop.

    ARCH-033: raises ``ResampleUnavailable`` (a typed exception) when
    scipy is missing, instead of the bare ``ImportError``. Callers
    that want to fall back to linear interp can catch this type.
    """
    global _resample_poly, _resample_poly_error, _resample_poly_error_time
    if _resample_poly is not None:
        return _resample_poly
    if _resample_poly_error is not None:
        # AUDIO-003: retry after timeout instead of memoizing forever
        if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
            raise _resample_poly_error
        # Retry — clear the cached error
        _resample_poly_error = None

    with _resample_poly_lock:
        if _resample_poly is not None:
            return _resample_poly
        if _resample_poly_error is not None:
            # AUDIO-003: retry after timeout instead of memoizing forever
            if time.monotonic() - _resample_poly_error_time < _RESAMPLE_RETRY_INTERVAL:
                raise _resample_poly_error
            # Retry — clear the cached error
            _resample_poly_error = None
        try:
            from scipy.signal import resample_poly
        except ImportError as exc:
            # ARCH-033: wrap in a typed exception so callers can catch
            # without inspecting the ImportError message.
            typed = ResampleUnavailable(
                f"scipy.signal.resample_poly unavailable: {exc}"
            )
            _resample_poly_error = typed
            _resample_poly_error_time = time.monotonic()
            raise typed from exc
        _resample_poly = resample_poly
        return _resample_poly


class Recorder:
    """Records audio from microphone into a buffer. Session-based: start, accumulate, stop, get data."""

    def __init__(self, config: Config, audio_processor: Optional[Any] = None):
        self.config = config
        self._audio_processor = audio_processor  # AudioProcessor or None
        self._stream: Optional[sd.InputStream] = None
        self._buffer: collections.deque = collections.deque(maxlen=DEFAULT_MAX_BUFFER_CHUNKS)
        self._lock = threading.Lock()

        # XRUN and clipping tracking
        self._xruns: int = 0
        self._clip_count: int = 0
        self._peak: float = 0.0
        self._last_clip_log_time: float = 0.0
        # Item 1: xrun notification callback — set by VoiceTyperApp
        # to receive a notification when xrun count exceeds threshold.
        self.on_xrun_threshold: Optional[Callable[[int], None]] = None
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
        self._cached_no_resample_arr: "np.ndarray | None" = None
        # NEW-PERF-010: cache of (rms, peak, silence_pct) from the most
        # recent stop() call, so the transcription engine can reuse
        # them instead of recomputing on the same audio array.
        self._last_audio_stats: "tuple[float, float, float] | None" = None

        # AUDIO-013: VAD state machine with hysteresis
        self._vad_state: VadState = VadState.UNKNOWN
        self._vad_consecutive_speech_frames: int = 0
        self._vad_consecutive_silence_frames: int = 0
        self._vad_speech_threshold_db: float = _DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._vad_silence_threshold_db: float = _DEFAULT_VAD_SILENCE_THRESHOLD_DB
        self._vad_speech_frames: int = _DEFAULT_VAD_SPEECH_FRAMES
        self._vad_silence_frames: int = _DEFAULT_VAD_SILENCE_FRAMES
        self._vad_hangover_frames: int = _DEFAULT_VAD_HANGOVER_FRAMES
        # AUDIO-013: Silero VAD integration — when use_silero_vad is
        # enabled in config, the recording callback uses Silero VAD
        # probability instead of RMS dB thresholds for the state machine.
        # impl-vad-fix: ADR 0007 §4.1 changed the config.py default to
        # True. The getattr fallback here must match, otherwise removing
        # the attribute from a Config dataclass instance (e.g. in tests
        # or partial configs) silently disables VAD even though the
        # documented default is True.
        self._use_silero_vad: bool = getattr(config, "use_silero_vad", True)
        self._vad_speech_threshold: float = getattr(config, "vad_speech_threshold", 0.5)
        self._vad_silence_threshold: float = getattr(config, "vad_silence_threshold", 0.3)
        self._silero_available: bool = False
        if self._use_silero_vad:
            try:
                from voice_typer.server.vad import is_available as _vad_is_available
                self._silero_available = _vad_is_available()
                if not self._silero_available:
                    log.warning("[RECORDING] use_silero_vad=True but Silero VAD unavailable — falling back to RMS")
            except Exception:
                self._silero_available = False

        # AUDIO-014: auto-calibration state
        self._vad_calibration_duration: float = _DEFAULT_VAD_CALIBRATION_DURATION
        self._vad_calibration_rms_values: list[float] = []
        self._vad_calibrated: bool = False

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

        # AUDIO-HOT: hot-plug device disconnect handling
        self._device_disconnected: bool = False
        self._device_disconnect_retries: int = 0
        self._max_disconnect_retries: int = 3
        # AUDIO-HOT: periodic device availability check — every N chunks,
        # verify the current device is still present in sd.query_devices().
        self._device_check_interval: int = 500  # check every ~500 chunks (~32s at 16Hz)
        self._device_check_counter: int = 0

        # AUDIO-DEAD: dead-air timeout — tracks continuous silence duration.
        # After the user has spoken at least once (speech_detected=True),
        # if silence persists for dead_air_timeout seconds, auto-stop.
        self._dead_air_timeout: float = float(
            config.dead_air_timeout if config.dead_air_timeout is not None else _DEFAULT_DEAD_AIR_TIMEOUT
        )
        self._dead_air_silence_start: float = 0.0  # timestamp when current silence began
        self._dead_air_speech_detected: bool = False  # has speech been detected this session?

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
        self._mic_watcher: Optional[Any] = None
        try:
            from voice_typer.server.microphone_watcher import (
                MicrophoneDeviceWatcher,
            )
            self._mic_watcher = MicrophoneDeviceWatcher(
                on_change=self._invalidate_device_cache
            )
            self._mic_watcher.start()
        except Exception:
            # Watcher is best-effort — the 30s TTL cache covers the
            # case where the watcher fails to start.
            log.warning(
                "[RECORDING] mic device watcher failed to start, "
                "falling back to 30s TTL polling",
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
        if (
            self._device_list_cache is not None
            and now - self._device_list_cache_time < self._device_list_cache_ttl
        ):
            return self._device_list_cache

        try:
            devices = []
            for i, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                devices.append({
                    "id": str(i),
                    "index": i,
                    "name": dev.get("name", ""),
                    "max_input_channels": dev.get("max_input_channels", 0),
                })
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
        log.debug(
            "[RECORDING] Device cache invalidated by OS-event watcher"
        )

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
            log.debug(
                "[RECORDING] mic watcher stop failed", exc_info=True
            )
        self._mic_watcher = None

    def __del__(self) -> None:
        """Best-effort cleanup of the mic watcher. Must never raise."""
        try:
            self.shutdown_mic_watcher()
        except Exception:
            # __del__ must never raise — Python logs and ignores it,
            # but we don't want to add noise during interpreter
            # teardown.
            pass

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
        # If the stream stopped but we didn't call stop() ourselves,
        # treat it as an unexpected disconnect.
        if self._stream is not None and not self._recording_event.is_set():
            log.warning(
                "[RECORDING] Stream finished unexpectedly — possible device disconnect"
            )
            self._device_disconnected = True
            try:
                threading.Thread(
                    target=self._handle_device_disconnect,
                    name="stream-finished-handler",
                    daemon=True,
                ).start()
            except Exception:
                pass

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
            log.debug("[RECORDING] Disconnect handler skipped — stop_generation changed (%d != %d)",
                      _captured_generation, self._stop_generation)
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
                try:
                    self.on_silence_auto_stop()
                except Exception:
                    pass
            return

        log.warning(
            "[RECORDING] Device disconnect detected (attempt %d/%d). "
            "Attempting restart with default device.",
            self._device_disconnect_retries, self._max_disconnect_retries,
        )

        # Stop current stream
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

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

    # ── AUDIO-014: VAD auto-calibration ─────────────────────────────────

    def _vad_auto_calibrate(self, chunk_rms: float, chunk_duration: float) -> None:
        """Auto-calibrate VAD thresholds based on ambient noise floor.

        AUDIO-014: During the first _vad_calibration_duration seconds of
        recording, we collect RMS values to determine the ambient noise
        floor. Then we set speech/silence thresholds relative to it.
        """
        if self._vad_calibrated:
            return

        self._vad_calibration_rms_values.append(chunk_rms)

        elapsed = time.perf_counter() - self._recording_start_time
        if elapsed < self._vad_calibration_duration:
            return  # still collecting samples

        if not self._vad_calibration_rms_values:
            self._vad_calibrated = True
            return

        # Compute noise floor from collected samples
        noise_rms = float(np.median(self._vad_calibration_rms_values))
        # Convert to dBFS (approximately)
        noise_db = 20.0 * math.log10(noise_rms) if noise_rms > 0 else -90.0

        # Set thresholds relative to noise floor
        self._vad_silence_threshold_db = noise_db + 6.0   # 6 dB above noise → silence
        self._vad_speech_threshold_db = noise_db + 18.0    # 18 dB above noise → speech
        self._vad_calibrated = True

        log.info(
            "[RECORDING] VAD auto-calibrated: noise_floor=%.1f dBFS, "
            "silence_threshold=%.1f dBFS, speech_threshold=%.1f dBFS",
            noise_db, self._vad_silence_threshold_db, self._vad_speech_threshold_db,
        )

    # ── AUDIO-013: VAD state machine update ─────────────────────────────

    def _vad_update(self, chunk_rms_db: float, vad_prob: float | None = None) -> VadState:
        """Update the VAD state machine based on the current frame's VAD signal.

        AUDIO-013: Uses hysteresis — transitioning from SILENCE to SPEECH
        requires N consecutive loud frames, while SPEECH to SILENCE requires
        M consecutive quiet frames (hangover period). This prevents rapid
        toggling at the boundary.

        When Silero VAD is enabled and a probability is provided, uses the
        VAD probability for speech/silence determination instead of RMS dB.
        Falls back to RMS-based detection if vad_prob is None.
        """
        if vad_prob is not None and self._use_silero_vad and self._silero_available:
            # Silero VAD path — use probability thresholds
            is_loud = vad_prob >= self._vad_speech_threshold
            is_quiet = vad_prob < self._vad_silence_threshold
        else:
            # RMS dB path — traditional threshold-based detection
            is_loud = chunk_rms_db >= self._vad_speech_threshold_db
            is_quiet = chunk_rms_db < self._vad_silence_threshold_db

        if is_loud:
            self._vad_consecutive_speech_frames += 1
            self._vad_consecutive_silence_frames = 0
        elif is_quiet:
            self._vad_consecutive_silence_frames += 1
            self._vad_consecutive_speech_frames = 0
        else:
            # AUDIO-013: Grey zone (between speech and silence thresholds).
            # Standard VAD hysteresis: leave counters unchanged so a long
            # run of grey-zone chunks doesn't discard accumulated frame
            # history. Resetting both counters here would cause spurious
            # state-machine stalls when audio hovers near the threshold
            # boundary (e.g. low-volume speech or breathy silence).
            # Pre-fix this block reset both counters, contradicting the
            # comment — the code now matches the comment.
            pass

        # State transitions with hysteresis
        old_state = self._vad_state
        if self._vad_state == VadState.UNKNOWN:
            if is_loud and self._vad_consecutive_speech_frames >= self._vad_speech_frames:
                self._vad_state = VadState.SPEECH
            elif is_quiet and self._vad_consecutive_silence_frames >= self._vad_silence_frames:
                self._vad_state = VadState.SILENCE
        elif self._vad_state == VadState.SILENCE:
            if self._vad_consecutive_speech_frames >= self._vad_speech_frames:
                self._vad_state = VadState.SPEECH
        elif self._vad_state == VadState.SPEECH:
            if self._vad_consecutive_silence_frames >= self._vad_hangover_frames:
                self._vad_state = VadState.SILENCE

        if self._vad_state != old_state:
            log.debug(
                "[RECORDING] VAD: %s -> %s (rms_db=%.1f, speech_frames=%d, silence_frames=%d)",
                old_state.value, self._vad_state.value, chunk_rms_db,
                self._vad_consecutive_speech_frames, self._vad_consecutive_silence_frames,
            )

        # AUDIO-DEAD: track dead-air timeout
        if self._vad_state == VadState.SPEECH:
            self._dead_air_speech_detected = True
            self._dead_air_silence_start = 0.0  # reset silence timer
        elif self._vad_state == VadState.SILENCE and self._dead_air_silence_start == 0.0:
            self._dead_air_silence_start = time.monotonic()

        return self._vad_state

    # ── ADR 0007 §3.5: _agc_update method deleted ─────────────────────
    # The old per-chunk AGC (C1) has been removed. It duplicated the
    # Compressor filter in the new audio filter chain. The Compressor
    # now handles dynamic range compression with proper attack/release.

    def warm_up_resampler(self) -> None:
        """Import and initialize the high-quality resampler before recording stops."""
        try:
            resample_poly = _get_resample_poly()
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

    def _resolve_effective_sample_rate(self, device: Optional[int]) -> tuple[int, Optional[dict]]:
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
            if device is None:
                dev_info = sd.query_devices(kind="input")
            else:
                dev_info = sd.query_devices(device)
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
                "[RECORDING] Device query: name=%s, host_api=%s, "
                "native_rate=%d, target_rate=%d",
                dev_info["name"], host_api_name, native_rate, target_sr,
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
                    "[RECORDING] Native rate %d differs from target %d, "
                    "will record at native rate and resample",
                    native_rate, target_sr,
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
                device, e, target_sr,
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
        # AUDIO-013: reset VAD state machine
        self._vad_state = VadState.UNKNOWN
        self._vad_consecutive_speech_frames = 0
        self._vad_consecutive_silence_frames = 0
        self._vad_speech_threshold_db = _DEFAULT_VAD_SPEECH_THRESHOLD_DB
        self._vad_silence_threshold_db = _DEFAULT_VAD_SILENCE_THRESHOLD_DB
        # AUDIO-014: reset auto-calibration
        self._vad_calibration_rms_values = []
        self._vad_calibrated = False
        # ADR 0007 §3.5: AGC reset deleted (method removed).
        # AUDIO-PRE: clear pre-roll buffer
        # SEC-audit-008: Zero the preroll buffer contents before clearing
        for chunk in self._preroll_buffer:
            if isinstance(chunk, np.ndarray):
                chunk.fill(0)
        self._preroll_buffer.clear()
        # AUDIO-HOT: reset disconnect state
        self._device_disconnected = False
        # AUDIO-DEAD: reset dead-air tracking
        self._dead_air_silence_start = 0.0
        self._dead_air_speech_detected = False
        self._device_disconnect_retries = 0
        # PERF-011: reset frame-skip state
        self._previous_chunk_pending = False
        self._skipped_frames = 0
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
        self._cached_silence_warning = getattr(self.config, 'silence_warning_seconds', 20.0)
        self._cached_silence_auto_stop = getattr(self.config, 'silence_auto_stop_seconds', 120.0)
        self._cached_max_recording = getattr(self.config, 'max_recording_seconds', 0)
        self._cached_device = str(getattr(self.config, 'device', 'cuda'))
        try:
            max_rec_raw = int(self._cached_max_recording)
        except (TypeError, ValueError):
            max_rec_raw = 0
        if max_rec_raw == 0:
            if self._cached_device == 'cuda':
                self._cached_max_recording = getattr(self.config, 'max_recording_seconds_gpu', 1200)
            else:
                self._cached_max_recording = getattr(self.config, 'max_recording_seconds_cpu', 600)

        # PERF-NEW-018: dynamically size the buffer based on max_recording_seconds.
        # At 16kHz with 1024-sample chunks, each chunk = 64ms.  For a 30-min
        # recording: 1800s / 0.064s ≈ 28125 chunks.  For 1 hour: 56250.
        try:
            max_rec = int(self._cached_max_recording)
        except (TypeError, ValueError):
            max_rec = 0
        if max_rec > 0:
            needed_chunks = int(max_rec / 0.064) + 1000  # +1K safety
            if needed_chunks > DEFAULT_MAX_BUFFER_CHUNKS:
                # Create a new deque with larger maxlen and copy existing data
                old_data = list(self._buffer)
                self._buffer = collections.deque(old_data, maxlen=needed_chunks)
                log.info(
                    "[RECORDING] Buffer sized for %ds max recording: %d chunks",
                    max_rec, needed_chunks,
                )

        device = self._resolve_device()
        candidates = self._same_physical_microphone_candidates(device)

        def callback(indata, frames, time_info, status):
            # AUDIO-009/AUDIO-015: guard flag for in-flight callback
            self._is_in_audio_callback.set()
            try:
                _callback_impl(indata, frames, time_info, status)
            finally:
                self._is_in_audio_callback.clear()

        def _callback_impl(indata, frames, time_info, status):
            # ARCH-026: PortAudio can deliver a callback before start()
            # finishes setting self._recording_start_time and other
            # per-session state. Bail out early so the silence/max-
            # duration callbacks don't compute against a None timestamp.
            if not self._recording_event.is_set():
                # AUDIO-PRE: capture pre-roll even when not officially recording
                if self._preroll_active:
                    mono_preroll = self._ensure_mono(indata.copy())
                    self._preroll_buffer.append(mono_preroll)
                return

            # HOTKEY-CRASH: detect device disconnect (zero-filled indata
            # when device is still "open" but USB/BT was unplugged).
            # Guard against false positives during rapid hotkey toggling:
            # when stop() clears _recording_event, PortAudio may deliver
            # zero-filled frames as the stream drains. We must NOT treat
            # those as device disconnects, because _handle_device_disconnect
            # would race with the deliberate stop() to close the stream.
            if not indata.any() and self._chunk_count > 10:
                # HOTKEY-CRASH: double-check that recording is still active.
                # The early-return check at the top of this callback passed,
                # but stop() may have cleared _recording_event between that
                # check and this point (both run on the same audio thread,
                # but the Event flag change is visible immediately).
                if not self._recording_event.is_set():
                    return  # deliberate stop, not a disconnect
                self._device_disconnected = True
                log.warning("[RECORDING] Zero-filled indata detected — possible device disconnect")
                # Schedule disconnect handling off the audio thread
                # HOTKEY-CRASH: capture the current stop_generation so the
                # handler can bail if a stop/start cycle happened in between.
                _captured_gen = self._stop_generation
                try:
                    threading.Thread(
                        target=self._handle_device_disconnect,
                        kwargs={"_captured_generation": _captured_gen},
                        name="device-disconnect-handler",
                        daemon=True,
                    ).start()
                except Exception:
                    pass
                return

            # AUDIO-HOT: periodic device availability check — verify
            # the current device is still present in sd.query_devices().
            # This catches cases where PortAudio doesn't deliver zeros
            # but the device is already gone (e.g. USB unplug on some
            # drivers). Runs every ~500 chunks to avoid per-chunk overhead.
            self._device_check_counter += 1
            if self._device_check_counter >= self._device_check_interval:
                self._device_check_counter = 0
                try:
                    current_device = self._resolve_device()
                    if current_device is not None:
                        try:
                            sd.query_devices(current_device)
                        except Exception:
                            # HOTKEY-CRASH: double-check recording is still active
                            if not self._recording_event.is_set():
                                return
                            log.warning("[RECORDING] Current device no longer available "
                            "in query_devices — disconnect detected")
                            self._device_disconnected = True
                            _captured_gen = self._stop_generation
                            try:
                                threading.Thread(
                                    target=self._handle_device_disconnect,
                                    kwargs={"_captured_generation": _captured_gen},
                                    name="device-disconnect-check",
                                    daemon=True,
                                ).start()
                            except Exception:
                                pass
                            return
                except Exception:
                    pass

            # AUDIO-DEAD: check dead-air timeout — if the user has spoken
            # at least once but silence has persisted for longer than the
            # configured timeout, auto-stop the recording. This prevents
            # indefinitely long recordings when the user walks away.
            if (self._dead_air_timeout > 0
                    and self._dead_air_speech_detected
                    and self._dead_air_silence_start > 0.0):
                silence_duration = time.monotonic() - self._dead_air_silence_start
                if silence_duration >= self._dead_air_timeout:
                    log.info(
                        "[RECORDING] Dead-air timeout reached (%.1fs >= %.1fs) — auto-stopping",
                        silence_duration, self._dead_air_timeout,
                    )
                    # Trigger auto-stop off the audio thread
                    if self.on_silence_auto_stop is not None:
                        try:
                            threading.Thread(
                                target=self.on_silence_auto_stop,
                                name="dead-air-timeout",
                                daemon=True,
                            ).start()
                        except Exception:
                            pass
                    return

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
                        status, self._xruns, recent_count, _XRUN_ALERT_PERIOD,
                    )
                # Item 1: fire threshold callback for tray notification
                if self._xruns == self._xrun_threshold and self.on_xrun_threshold:
                    try:
                        self.on_xrun_threshold(self._xruns)
                    except Exception:
                        pass

            # PERF-011: frame-skip under CPU load — if the previous
            # chunk is still being processed, skip this one to prevent
            # buffer bloat.
            if self._previous_chunk_pending:
                self._skipped_frames += 1
                if self._skipped_frames == 1 or self._skipped_frames % 100 == 0:
                    log.warning(
                        "[RECORDING] Skipping frame under CPU load (skipped=%d)",
                        self._skipped_frames,
                    )
                return
            self._previous_chunk_pending = True

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
                filtered = self._audio_processor.process_chunk(indata_mono.copy())
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
                # Store FILTERED audio so the transcriber receives
                # the cleaned signal. The .copy() is needed because
                # the filter chain may return the same array (passthrough
                # mode), and the buffer must own its data.
                self._buffer.append(filtered.copy())
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
            if buffer_len >= self._buffer.maxlen - 1:
                self._dropped_chunks = getattr(self, '_dropped_chunks', 0) + 1
                if self._dropped_chunks == 1 or self._dropped_chunks % 100 == 0:
                    log.warning(
                        "[RECORDING] Buffer full — oldest audio dropped (total=%d). "
                        "ASR is slower than real-time.",
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
            silence_timer = self._silence_timer
            silence_warning_count = self._silence_warning_count
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
                abs_filtered = np.abs(filtered)
                chunk_peak = float(abs_filtered.max())
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
                    log.warning(
                        "[RECORDING] Clipping detected: peak=%.4f, count=%d chunks. Reduce mic gain.",
                        chunk_peak, self._clip_count
                    )
                    self._last_clip_log_time = now
                    # AUDIO-CLIP: push a real-time IPC event so the
                    # Electron UI can flash a red level-bar / show a
                    # "Clipping!" toast while recording. Pre-fix the
                    # only notification was post-recording via
                    # _finalize_audio_quality_report, which is too late
                    # for the user to adjust mic gain mid-dictation.
                    # The event is throttled to 1 Hz (same as the log)
                    # to avoid flooding the IPC channel.
                    try:
                        from voice_typer.server.ipc_server import _push_event_now
                        _push_event_now({
                            "type": "audio_clip",
                            "data": {
                                "peak": float(chunk_peak),
                                "count": int(self._clip_count),
                            },
                        })
                    except Exception:
                        pass

            recent_rms.append(chunk_rms)

            # AUDIO-014: auto-calibrate VAD thresholds from ambient noise
            self._vad_auto_calibrate(chunk_rms, chunk_duration)

            # AUDIO-013: compute Silero VAD probability if enabled.
            # This runs in the audio callback (~1ms for 512 samples on CPU)
            # and is only used when use_silero_vad=True in config.
            vad_prob = None
            if self._use_silero_vad and self._silero_available:
                try:
                    from voice_typer.server.vad import compute_vad_prob
                    # impl-vad-fix: Silero VAD only accepts {8000, 16000} Hz.
                    # The mic's native rate (self._effective_sr) may be 44100
                    # or 48000, which previously raised:
                    #   ValueError: Supported sampling rates: [8000, 16000]
                    # Resample to 16000 using the same scipy resample_poly
                    # path as _resample_audio_impl (gcd up/down pattern).
                    if self._effective_sr not in (8000, 16000):
                        try:
                            resample_poly = _get_resample_poly()
                            gcd = math.gcd(self._effective_sr, 16000)
                            up = 16000 // gcd
                            down = self._effective_sr // gcd
                            vad_audio = resample_poly(
                                filtered.ravel(), up, down
                            ).astype(np.float32)
                            vad_sr = 16000
                        except Exception:
                            # scipy unavailable or resample failed — fall
                            # back to RMS rather than crashing the audio
                            # callback. compute_vad_prob will likely still
                            # raise on the unsupported rate, but it is
                            # caught by the outer except below.
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

            # PERF-011: clear frame-skip flag after processing
            self._previous_chunk_pending = False

            # Use cached config values (PERF-NEW-006)
            silence_warning_seconds = self._cached_silence_warning
            silence_auto_stop_seconds = self._cached_silence_auto_stop

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
                        try:
                            silence_warning_cb()
                        except Exception:
                            pass

            if self._silence_timer >= silence_auto_stop_seconds:
                if silence_auto_stop_cb is not None:
                    try:
                        silence_auto_stop_cb()
                    except Exception:
                        pass

            # H12b: Maximum recording duration auto-stop
            recording_duration = time.perf_counter() - recording_start
            max_recording_seconds = self._cached_max_recording
            if recording_duration >= max_recording_seconds:
                if max_duration_cb is not None:
                    try:
                        max_duration_cb()
                    except Exception:
                        pass

            if chunk_count == BUFFER_WARNING_THRESHOLD:
                log.warning(
                    "[RECORDING] Buffer is large (5k chunks, ~5 min). "
                    "Consider stopping recording."
                )
            if chunk_count % TELEMETRY_LOG_INTERVAL == 0:
                log.info(
                    "[RECORDING] Buffer telemetry: chunks=%d, buffer_count=%d",
                    chunk_count, buffer_len,
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
                    self._rms_callback_error_count = getattr(
                        self, "_rms_callback_error_count", 0
                    ) + 1
                    if (
                        self._rms_callback_error_count == 1
                        or self._rms_callback_error_count % 100 == 0
                    ):
                        log.debug(
                            "[RECORDING] on_rms_level callback raised "
                            "(occurrence #%d)",
                            self._rms_callback_error_count,
                            exc_info=True,
                        )
                    else:
                        log.debug(
                            "[RECORDING] on_rms_level callback raised "
                            "(occurrence #%d, traceback suppressed)",
                            self._rms_callback_error_count,
                        )

        # AUDIO-HOT: store callback reference for device restart
        self._current_callback = callback

        last_error = None
        selected_device = None
        effective_sr = self.config.sample_rate
        used_fallback = False

        for candidate in candidates:
            candidate_sr, dev_info_extra = self._resolve_effective_sample_rate(candidate)

            if dev_info_extra:
                log.info(
                    "[RECORDING] Using device: [%s] %s | host_api=%s | "
                    "native_rate=%d | effective_rate=%d",
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
                if config_channels > 0:
                    channels = config_channels
                else:
                    channels = 1
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
                    actual_sr = int(stream.samplerate) if hasattr(stream, 'samplerate') else candidate_sr
                    if actual_sr in (8000, 16000) and actual_sr != candidate_sr:
                        log.warning(
                            "[RECORDING] Bluetooth HFP profile detected: actual sample rate "
                            "%d Hz differs from requested %d Hz. Audio quality will be limited. "
                            "Consider disabling the hands-free telephony profile in Bluetooth "
                            "settings for better quality.",
                            actual_sr, candidate_sr,
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
                    try:
                        stream.close()
                    except Exception:
                        pass
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
                        "[RECORDING] Fallback device: [%s] %s | host_api=%s | "
                        "native_rate=%d | effective_rate=%d",
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
                        candidate, e,
                    )
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                    continue

                self._stream = stream
                # ARCH-021: guard _effective_sr writes with the lock.
                with self._lock:
                    self._effective_sr = candidate_sr
                selected_device = candidate
                effective_sr = candidate_sr
                used_fallback = True
                log.info(
                    "[RECORDING] Fallback succeeded with device [%s] %s",
                    # pyrefly: ignore [unsupported-operation]
                    candidate, dev_info_extra["name"],
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
                try:
                    self.config.save()
                except Exception as e:
                    log.debug("[RECORDING] Could not persist microphone fallback: %s", e)
            _threading_for_save.Thread(
                target=_persist_mic, name="mic-fallback-save", daemon=True,
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
                log.info(
                    "[RECORDING] Prepended %d pre-roll chunks (~%.1fs)",
                    len(preroll_chunks), len(preroll_chunks) * 512 / self._effective_sr,
                )

        target_sr = self.config.sample_rate
        if effective_sr != target_sr and _resample_poly is None and _resample_poly_error is None:
            # Warm up synchronously to avoid racing with stop()
            self.warm_up_resampler()

    def stop(self) -> np.ndarray:
        """Stop recording and return the complete audio array."""
        if not self._recording_event.is_set():
            return np.array([], dtype=np.float32)

        stop_started = time.perf_counter()
        self._recording_event.clear()

        # HOTKEY-CRASH: increment stop_generation so any stale disconnect
        # handlers from the audio callback know to bail out.
        self._stop_generation += 1

        # AUDIO-009/AUDIO-015: wait briefly for any in-flight audio
        # callback to complete before closing the stream. This prevents
        # PortAudio from calling the callback during/after stream.stop()
        # which can cause use-after-free or deadlock.
        #
        # HOTKEY-CRASH: use a retry loop with backoff (up to ~300ms total)
        # instead of a single 50ms wait. Under rapid hotkey toggling, the
        # callback might still be in-flight when we try to close the stream.
        if self._stream:
            self._stream.stop()
            # Wait for the in-flight callback to finish, with retries.
            for _wait_attempt in range(6):
                if self._is_in_audio_callback.wait(timeout=0.05):
                    break  # callback completed
            self._stream.close()
            self._stream = None
        stream_ms = (time.perf_counter() - stop_started) * 1000

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
            # SEC-audit-008: Zero the buffer contents before clearing to prevent
            # forensic recovery of audio data from process memory
            for chunk in self._buffer:
                if isinstance(chunk, np.ndarray):
                    chunk.fill(0)
            self._buffer.clear()
            # Reset cache on stop
            self._cached_resampled = np.array([], dtype=np.float32)
            self._cached_native_chunk_count = 0
            # NEW-PERF-003: invalidate the no-resample cache too.
            self._cached_no_resample_len = -1
            self._cached_no_resample_arr = None
        concat_ms = (time.perf_counter() - concat_started) * 1000

        # Log audio statistics for diagnostics
        stats_started = time.perf_counter()
        effective_sr = self._effective_sr
        duration = len(audio) / effective_sr if len(audio) > 0 else 0
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
            log.info(
                "[RECORDING] Audio captured: duration=%.1fs, effective_sr=%d, "
                "samples=%d, RMS=%.6f, peak=%.6f, silence_pct=%.1f%%",
                duration, effective_sr, len(audio), rms, peak, silence_pct,
            )
            if rms < 0.001:
                log.warning(
                    "[RECORDING] Near-silence detected! (RMS=%.6f) "
                    "Microphone may not be capturing audio.",
                    rms,
                )
        else:
            self._last_rms = 0.0
            self._last_audio_stats = (0.0, 0.0, 0.0)
            log.warning("[RECORDING] No audio data captured!")
        stats_ms = (time.perf_counter() - stats_started) * 1000

        # H15: stop() should NOT use cache - resample from scratch for full audio
        resample_started = time.perf_counter()
        audio = self._prepare_audio(audio, effective_sr)
        resample_ms = (time.perf_counter() - resample_started) * 1000

        # AUDIO-PROC: post-capture spectral noise reduction (offline,
        # safe to block).  Runs AFTER resampling so noisereduce
        # operates on the final 16 kHz audio.  ~200 ms for 30 s audio.
        post_capture_ms = 0.0
        # ADR 0007 §3.8: post-capture noisereduce removed. The real-time
        # NoiseSuppressor filter in the chain handles denoising. The
        # old process_full_audio() call is removed because:
        # 1. It only ran in stop(), so the streaming path missed it.
        # 2. The "first 0.5s is silence" assumption was fragile.
        # 3. noisereduce is no longer a dependency.

        total_ms = (time.perf_counter() - stop_started) * 1000
        log.info(
            "[RECORDING] Stop timing: stream=%.1fms, concat=%.1fms, "
            "stats=%.1fms, resample=%.1fms, post_capture=%.1fms, total=%.1fms",
            stream_ms, concat_ms, stats_ms, resample_ms, post_capture_ms, total_ms,
        )

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
                            "[RECORDING] Snapshot resample failed; dropping "
                            "%d native samples: %s",
                            len(new_audio), e,
                        )
                        self._cached_native_chunk_count = len(self._buffer)
                        # NEW-PERF-003: return a view, not a copy.
                        return self._cached_resampled[:]
                    # PERF-NEW-002: avoid the O(n) reallocation when the
                    # cached prefix is empty (first snapshot of a session).
                    if len(self._cached_resampled) > 0:
                        self._cached_resampled = np.concatenate(
                            [self._cached_resampled, new_resampled]
                        )
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
                if (
                    getattr(self, "_cached_no_resample_len", -1) == buf_len
                    and self._cached_no_resample_arr is not None
                ):
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
            resample_poly = _get_resample_poly()
            gcd = math.gcd(effective_sr, target_sr)
            up = target_sr // gcd
            down = effective_sr // gcd
            audio = resample_poly(audio, up, down).astype(np.float32)
            if log_resample:
                log.info(
                    "[RECORDING] Resampled %d Hz -> %d Hz (%d -> %d samples)",
                    effective_sr, target_sr, orig_len, len(audio),
                )
            resampled = True
        except ResampleUnavailable as exc:
            # ARCH-033: scipy missing — fall through to linear interp.
            last_error = exc
            if log_resample:
                log.warning(
                    "[RECORDING] scipy not available, using linear interp resampling"
                )
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
                # pyrefly: ignore [bad-assignment]
                audio = np.interp(
                    indices, np.arange(len(audio)), audio,
                ).astype(np.float32)
                if log_resample:
                    log.info(
                        "[RECORDING] Resampled (linear interp) %d Hz -> %d Hz "
                        "(%d -> %d samples)",
                        effective_sr, target_sr, orig_len, len(audio),
                    )
                resampled = True
            except (ValueError, OSError, TypeError) as exc:
                # ERR-012: narrow here too.
                last_error = exc
                if log_resample:
                    log.error(
                        "[RECORDING] All resampling failed: %s. "
                        "Audio at %d Hz cannot be used by Whisper.",
                        exc, effective_sr,
                    )

        if not resampled:
            # ERR-001: previously returned the native-rate audio here,
            # which silently produced garbage transcriptions. Raise so
            # the streaming / final paths can decide how to recover.
            raise ResampleError(
                f"Cannot resample audio from {effective_sr} Hz to "
                f"{target_sr} Hz (last error: {last_error!r})"
            )
        return audio

    def discard(self) -> None:
        """Discard current recording without processing."""
        self._recording_event.clear()
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
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            # SEC-audit-008: Zero the buffer contents before clearing to prevent
            # forensic recovery of audio data from process memory
            for chunk in self._buffer:
                if isinstance(chunk, np.ndarray):
                    chunk.fill(0)
            self._buffer.clear()
