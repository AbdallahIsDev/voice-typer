"""Session-based audio recording."""

import collections
import logging
import math
import threading
import time
from typing import Optional, List, Any, Callable

import numpy as np
import sounddevice as sd

from voice_typer.server.config import Config

log = logging.getLogger(__name__)

# PERF-NEW-018: MAX_BUFFER_CHUNKS is now dynamically adjusted in
# start() based on max_recording_seconds.  The default below is a
# safe ceiling (30K chunks * 1024 samples/chunk / 16kHz ≈ 30 min).
# For longer recordings, start() increases the deque maxlen.
DEFAULT_MAX_BUFFER_CHUNKS = 30000
BUFFER_WARNING_THRESHOLD = 5000
TELEMETRY_LOG_INTERVAL = 1000

_resample_poly = None
_resample_poly_error: Exception | None = None
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


def _get_resample_poly():
    """Load scipy's resampler once so imports do not happen on F2 stop."""
    global _resample_poly, _resample_poly_error
    if _resample_poly is not None:
        return _resample_poly
    if _resample_poly_error is not None:
        raise _resample_poly_error

    with _resample_poly_lock:
        if _resample_poly is not None:
            return _resample_poly
        if _resample_poly_error is not None:
            raise _resample_poly_error
        try:
            from scipy.signal import resample_poly
        except ImportError as exc:
            _resample_poly_error = exc
            raise
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
        self._recording_event = threading.Event()
        self._effective_sr: int = config.sample_rate
        self._last_rms: float = 0.0
        self._chunk_count: int = 0

        # H15/M8: Cached resampled prefix for snapshot() to avoid O(n²) resampling
        self._cached_resampled: np.ndarray = np.array([], dtype=np.float32)
        self._cached_native_chunk_count: int = 0

        # H12: Silent mic disconnection detection
        self._silence_timer: float = 0.0
        self._silence_warning_count: int = 0
        self._silence_next_warning_wait: float = 10.0
        self._recording_start_time: float = 0.0
        self._recent_rms_values: collections.deque = collections.deque(maxlen=50)

        # H12 callbacks (wired by app.py)
        self.on_silence_warning = None  # type: Optional[callable]
        self.on_silence_auto_stop = None  # type: Optional[callable]
        self.on_max_duration_auto_stop = None  # type: Optional[callable]

        # Waveform bubble: fired from audio callback on every chunk (wired by app.py)
        self.on_rms_level = None  # type: Optional[callable]

    @property
    def recording(self) -> bool:
        return self._recording_event.is_set()

    @property
    def last_rms(self) -> float:
        """RMS level of the most recently captured audio (0.0 if never recorded)."""
        with self._lock:
            return self._last_rms

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
            log.warning("[RECORDING] Could not query device info: %s", e)
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
        """Start recording audio."""
        if self._recording_event.is_set():
            return

        self._buffer.clear()
        self._chunk_count = 0
        self._cached_resampled = np.array([], dtype=np.float32)
        self._cached_native_chunk_count = 0
        self._silence_timer = 0.0
        self._silence_warning_count = 0
        self._silence_next_warning_wait = 10.0
        self._recent_rms_values.clear()
        self._recording_start_time = time.perf_counter()
        # Reset XRUN and clipping counters
        self._xruns = 0
        self._clip_count = 0
        self._peak = 0.0
        self._last_clip_log_time = 0.0

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
            # AUDIO-002: Check PortAudio status flags for XRUNs.
            # PERF-NEW-008: rate-limit the warning log so a sustained
            # xrun condition doesn't write 16 disk lines/sec on the
            # audio thread.  Log the first occurrence immediately, then
            # at most once every 5 seconds.  The cumulative xrun count
            # is still incremented on every occurrence so the final
            # stats are accurate.
            if status:
                self._xruns += 1
                now = time.monotonic()
                last = getattr(self, "_last_xrun_log_ts", 0.0)
                if now - last >= 5.0 or self._xruns == 1:
                    log.warning(
                        "[RECORDING] PortAudio status flag: %s (xrun_count=%d)",
                        status, self._xruns,
                    )
                    self._last_xrun_log_ts = now
                # Item 1: fire threshold callback for tray notification
                if self._xruns == self._xrun_threshold and self.on_xrun_threshold:
                    try:
                        self.on_xrun_threshold(self._xruns)
                    except Exception:
                        pass

            # Item 5: minimize lock scope. Only buffer append + counter
            # need the lock. RMS computation, silence detection, clipping
            # tracking, and callback invocations run outside the lock
            # because they operate on the local `indata` copy, not on
            # shared mutable state.
            with self._lock:
                # Store FILTERED audio so the transcriber receives
                # the cleaned signal.
                self._buffer.append(filtered.copy())
                self._chunk_count += 1
                chunk_count = self._chunk_count
                buffer_len = len(self._buffer)
                # Capture callback refs + silence state under lock
                rms_callback = self.on_rms_level
                silence_warning_cb = self.on_silence_warning
                silence_auto_stop_cb = self.on_silence_auto_stop
                max_duration_cb = self.on_max_duration_auto_stop
                recent_rms = self._recent_rms_values
                silence_timer = self._silence_timer
                silence_warning_count = self._silence_warning_count
                recording_start = self._recording_start_time

            # ── Everything below runs OUTSIDE the lock ──

            # AUDIO-PROC: apply real-time noise filtering before any
            # analysis or buffering so the stored audio and the
            # silence/bubble metrics reflect the cleaned signal that
            # the transcriber will receive.
            if self._audio_processor is not None:
                filtered = self._audio_processor.process_chunk(indata.copy())
            else:
                filtered = indata

            # RMS / peak computation (operates on FILTERED audio so the
            # waveform bubble and silence detection see what the
            # transcriber will see, not raw mic input).
            chunk_rms = float(np.sqrt(np.mean(np.square(filtered), dtype=np.float64)))
            chunk_peak = float(np.max(np.abs(filtered))) if filtered.size else 0.0
            chunk_duration = len(filtered) / self._effective_sr

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

            recent_rms.append(chunk_rms)

            # Voice detected by loudness → reset silence timer
            if chunk_rms > 0.005 or chunk_peak > 0.01:
                self._silence_timer = 0.0
                recent_rms.clear()
            else:
                if len(recent_rms) >= 10:
                    rms_std = float(np.std(list(recent_rms)))
                    if rms_std < 0.001:
                        self._silence_timer += chunk_duration
                    else:
                        self._silence_timer = 0.0
                else:
                    self._silence_timer += chunk_duration

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
            if rms_callback is not None:
                try:
                    rms_callback(chunk_rms, chunk_peak)
                except Exception:
                    log.debug("[RECORDING] on_rms_level callback raised", exc_info=True)

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
                stream = sd.InputStream(
                    samplerate=candidate_sr,
                    channels=1,
                    dtype=np.float32,
                    device=candidate,
                    callback=callback,
                )
                stream.start()
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
                    stream = sd.InputStream(
                        samplerate=candidate_sr,
                        channels=1,
                        dtype=np.float32,
                        device=candidate,
                        callback=callback,
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

        stream_started = time.perf_counter()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        stream_ms = (time.perf_counter() - stream_started) * 1000

        concat_started = time.perf_counter()
        with self._lock:
            if not self._buffer:
                # Reset cache
                self._cached_resampled = np.array([], dtype=np.float32)
                self._cached_native_chunk_count = 0
                self._chunk_count = 0
                return np.array([], dtype=np.float32)
            audio = np.concatenate(list(self._buffer), axis=0).reshape(-1)
            self._buffer.clear()
            # Reset cache on stop
            self._cached_resampled = np.array([], dtype=np.float32)
            self._cached_native_chunk_count = 0
        concat_ms = (time.perf_counter() - concat_started) * 1000

        # Log audio statistics for diagnostics
        stats_started = time.perf_counter()
        effective_sr = self._effective_sr
        duration = len(audio) / effective_sr if len(audio) > 0 else 0
        if len(audio) > 0:
            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
            peak = float(np.max(np.abs(audio)))
            silence_pct = float(np.sum(np.abs(audio) < 0.001) / audio.size * 100)
            self._last_rms = rms
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
        if self._audio_processor is not None and len(audio) > 0:
            post_capture_started = time.perf_counter()
            audio = self._audio_processor.process_full_audio(audio)
            post_capture_ms = (time.perf_counter() - post_capture_started) * 1000

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
        """
        import itertools
        with self._lock:
            if not self._buffer:
                return np.array([], dtype=np.float32)
            effective_sr = self._effective_sr
            target_sr = self.config.sample_rate

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
                    new_resampled = self._resample_chunk(new_audio, effective_sr, target_sr)
                    # PERF-NEW-002: avoid the O(n) reallocation when the
                    # cached prefix is empty (first snapshot of a session).
                    if len(self._cached_resampled) > 0:
                        self._cached_resampled = np.concatenate(
                            [self._cached_resampled, new_resampled]
                        )
                    else:
                        self._cached_resampled = new_resampled
                    self._cached_native_chunk_count = len(self._buffer)
                return self._cached_resampled.copy()
            elif effective_sr == target_sr:
                # No resampling needed, just concatenate all.
                # PERF-NEW-003: islice over the deque avoids the full
                # list copy.  ``np.fromiter`` would be even faster but
                # requires a flat iterator; the deque holds 2D chunks
                # so we still need one concatenate.
                chunks = list(itertools.islice(self._buffer, 0, None))
                audio = np.concatenate(chunks, axis=0).reshape(-1)
                return audio
            else:
                # No new chunks, return cached
                return self._cached_resampled.copy()

    def _resample_chunk(self, audio: np.ndarray, effective_sr: int, target_sr: int) -> np.ndarray:
        """Resample a single chunk of audio."""
        if len(audio) == 0:
            return np.array([], dtype=np.float32)
        resampled = False
        try:
            resample_poly = _get_resample_poly()
            gcd = math.gcd(effective_sr, target_sr)
            up = target_sr // gcd
            down = effective_sr // gcd
            audio = resample_poly(audio, up, down).astype(np.float32)
            resampled = True
        except ImportError:
            pass
        except Exception:
            pass

        if not resampled:
            try:
                ratio = target_sr / effective_sr
                new_len = int(len(audio) * ratio)
                indices = np.linspace(0, len(audio) - 1, new_len)
                audio = np.interp(
                    indices, np.arange(len(audio)), audio,
                ).astype(np.float32)
                resampled = True
            except Exception:
                pass

        if not resampled:
            return audio  # Return as-is, will be caught later
        return audio

    def _prepare_audio(
        self,
        audio: np.ndarray,
        effective_sr: int,
        log_resample: bool = True,
    ) -> np.ndarray:
        """Convert captured audio to the configured sample rate."""
        target_sr = self.config.sample_rate  # 16000 for Whisper
        if effective_sr != target_sr and len(audio) > 0:
            orig_len = len(audio)
            resampled = False
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
            except ImportError:
                log.warning(
                    "[RECORDING] scipy not available, using linear interp resampling"
                )
            except Exception as e:
                log.error("[RECORDING] scipy resample_poly failed: %s", e)

            if not resampled:
                # Fallback: simple linear interpolation resampling
                try:
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
                except Exception as e:
                    log.error(
                        "[RECORDING] All resampling failed: %s. "
                        "Audio at %d Hz cannot be used by Whisper.",
                        e, effective_sr,
                    )

            if not resampled:
                raise RuntimeError(
                    f"Cannot resample audio from {effective_sr} Hz to "
                    f"{target_sr} Hz. Check scipy installation and audio format."
                )

        return audio

    def discard(self) -> None:
        """Discard current recording without processing."""
        self._recording_event.clear()
        self._effective_sr = self.config.sample_rate
        self._last_rms = 0.0
        self._silence_timer = 0.0
        self._silence_warning_count = 0
        self._silence_next_warning_wait = 10.0
        # Reset cache on discard
        self._cached_resampled = np.array([], dtype=np.float32)
        self._cached_native_chunk_count = 0
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._buffer.clear()
