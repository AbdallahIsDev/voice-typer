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
"""

import base64
import io
import logging
import threading
import time

from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Monitor session state ────────────────────────────────────────────

_monitor_lock = threading.Lock()
_monitor_stream: Optional[object] = None  # sounddevice.InputStream
_monitor_active: bool = False
_monitor_level: float = 0.0   # smoothed RMS (0-1)
_monitor_peak: float = 0.0    # smoothed peak (0-1)
_monitor_sample_rate: int = 16000
_monitor_mic_id: Optional[str] = None  # device this stream is on

# ── Audio processor for filtering the live level bar ───────────────
# When set, audio from the callback is run through this processor's
# process_chunk() before computing RMS/peak so the level bar reflects
# the effect of noise filters in real-time.
_level_processor: Optional[object] = None  # AudioProcessor instance

# ── Test recording state (uses the SAME stream) ─────────────────────

_test_mode: bool = False
_test_chunks: list[np.ndarray] = []
_test_raw_chunks: list[np.ndarray] = []  # separate copy for before/after comparison
_test_start_time: float = 0.0
_test_duration: float = 10.0
_test_filters: dict = {}
_test_auto_stop_timer: Optional[threading.Timer] = None

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
            "level": min(1.0, _monitor_level * 5),
            "peak": _monitor_peak,
            "active": _monitor_active,
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
        from voice_typer.server.audio_processor import (
            AudioProcessor,
            AudioProcessorConfig,
        )
        ap_config = AudioProcessorConfig(
            enabled=True,
            highpass=config_dict.get("noise_filter_highpass", True),
            highpass_cutoff_hz=float(
                config_dict.get("noise_filter_highpass_cutoff_hz", 80.0)
            ),
            noise_gate=config_dict.get("noise_filter_gate", True),
            noise_gate_threshold=float(
                config_dict.get("noise_filter_gate_threshold", 0.015)
            ),
            rnnoise=config_dict.get("noise_filter_rnnoise", False),
            post_capture=False,  # not used for live level
        )
        _level_processor = AudioProcessor(ap_config, sample_rate=_monitor_sample_rate)
        log.info(
            "[LEVEL-MON] Level processor updated: highpass=%s, gate=%s, rnnoise=%s",
            ap_config.highpass, ap_config.noise_gate, ap_config.rnnoise,
        )
    except Exception as exc:
        log.warning("[LEVEL-MON] Failed to create level processor: %s", exc)
        _level_processor = None


def start_monitoring(mic_id: Optional[str] = None) -> dict:
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

    # Close old stream (if any) without holding the lock
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
            try:
                device = int(mic_id)
            except (ValueError, TypeError):
                pass

        try:
            if device is None:
                dev_info = sd.query_devices(kind="input")
            else:
                dev_info = sd.query_devices(device)
            native_rate = int(dev_info["default_samplerate"])
        except Exception:
            native_rate = 16000

        _monitor_sample_rate = native_rate
        _monitor_level = 0.0
        _monitor_peak = 0.0

        def callback(indata, frames, time_info, status):
            global _monitor_level, _monitor_peak, _monitor_active, _test_mode, _test_chunks
            if status:
                log.debug("[LEVEL-MON] PortAudio status: %s", status)
            with _monitor_lock:
                if not _monitor_active:
                    return
                flat = indata.ravel()
                if len(flat) > 0:
                    # Apply noise filters to the level bar audio if a
                    # processor is active, so the bar reflects what the
                    # user hears after filtering, not the raw mic input.
                    if _level_processor is not None:
                        filtered = _level_processor.process_chunk(
                            indata.reshape(-1, 1)
                        )
                        flat_filtered = filtered.ravel()
                        abs_flat = np.abs(flat_filtered)
                        rms = float(np.sqrt(np.mean(flat_filtered ** 2)))
                    else:
                        abs_flat = np.abs(flat)
                        rms = float(np.sqrt(np.mean(flat ** 2)))
                    peak = float(abs_flat.max())
                    # Smooth with exponential moving average
                    _monitor_level = (_monitor_level * 0.6) + (rms * 0.4)
                    _monitor_peak = max(_monitor_peak * 0.8, peak)
                else:
                    _monitor_level *= 0.85
                    _monitor_peak *= 0.85

                # If a test recording is active, also accumulate audio
                if _test_mode:
                    # Track quality metrics from RAW audio (not filtered)
                    # so the quality report reflects the true mic input
                    # independent of any active filter settings.
                    raw_rms_for_quality = float(np.sqrt(np.mean(np.square(flat.astype(np.float64)))))
                    raw_peak_for_quality = float(np.abs(flat).max())
                    _test_chunks.append(indata.copy())
                    _test_raw_chunks.append(indata.copy())
                    _test_rms_history.append(raw_rms_for_quality)
                    _test_peak_history.append(raw_peak_for_quality)
                    if raw_rms_for_quality < 0.0005:
                        _test_silence_blocks += 1
                    if raw_peak_for_quality > 0.95:
                        _test_clip_count += 1

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

            log.info(
                "[LEVEL-MON] Monitoring started: mic=%s, sr=%d",
                mic_id or "default", native_rate,
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

    with _monitor_lock:
        if not _monitor_active:
            return {"success": True, "message": "Not monitoring"}
        _monitor_active = False
        stream = _monitor_stream
        _monitor_stream = None
        _monitor_level = 0.0
        _monitor_peak = 0.0
        _monitor_mic_id = None

    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            log.debug("[LEVEL-MON] Stream close: %s", exc)

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
    mic_id: Optional[str] = None,
    duration: float = 10.0,
    filters: Optional[dict] = None,
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
    global _test_mode, _test_chunks, _test_raw_chunks, _test_start_time, _test_duration, _test_filters, _test_auto_stop_timer, _monitor_mic_id

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
            _test_chunks = []
            _test_raw_chunks = []
            _test_start_time = time.perf_counter()
            _test_duration = max(1.0, min(30.0, duration))
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
                _monitor_mic_id or "default", _test_duration,
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
        _test_chunks = []
        _test_raw_chunks = []
        _test_start_time = time.perf_counter()
        _test_duration = max(1.0, min(30.0, duration))
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
            _monitor_mic_id or "default", _test_duration,
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
    global _test_mode, _test_auto_stop_timer, _test_chunks, _test_raw_chunks, _test_start_time, _test_filters
    global _test_peak_history, _test_rms_history, _test_clip_count, _test_silence_blocks

    # Cancel the auto-stop timer if it hasn't fired yet
    timer = _test_auto_stop_timer
    if timer is not None:
        timer.cancel()
        _test_auto_stop_timer = None

    with _monitor_lock:
        was_active = _test_mode
        sr = _monitor_sample_rate
        chunks = list(_test_chunks)
        raw_chunks = list(_test_raw_chunks)
        filters = dict(_test_filters)
        peak_hist = list(_test_peak_history)
        rms_hist = list(_test_rms_history)
        clip_count = _test_clip_count
        silence_blocks = _test_silence_blocks

        # Clear test state
        _test_mode = False
        _test_chunks = []
        _test_raw_chunks = []
        _test_start_time = 0.0
        _test_filters.clear()
        _test_peak_history = []
        _test_rms_history = []
        _test_clip_count = 0
        _test_silence_blocks = 0

    if not was_active and not chunks:
        return {
            "success": False,
            "audio_base64": "",
            "raw_audio_base64": "",
            "duration_ms": 0,
            "sample_rate": 16000,
            "message": "No test running",
            "quality": {},
        }

    if not chunks:
        return {
            "success": True,
            "audio_base64": "",
            "raw_audio_base64": "",
            "duration_ms": 0,
            "sample_rate": sr,
            "message": "No audio captured",
            "quality": {},
        }

    # Concatenate all chunks into a single float32 array
    try:
        audio = np.concatenate(chunks, axis=0).reshape(-1)
        raw_audio = np.concatenate(raw_chunks, axis=0).reshape(-1)
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

    duration_ms = int(len(audio) / sr * 1000)

    # ── Compute quality metrics from raw audio ──────────────────────
    raw_abs = np.abs(raw_audio)
    raw_rms = float(np.sqrt(np.mean(np.square(raw_audio.astype(np.float64)))))
    raw_peak = float(raw_abs.max())
    
    quality = {
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
    if filters and filters.get("noise_filter_enabled", True):
        try:
            from voice_typer.server.audio_processor import (
                AudioProcessor,
                AudioProcessorConfig,
            )
            ap_config = AudioProcessorConfig(
                enabled=filters.get("noise_filter_enabled", True),
                highpass=filters.get("noise_filter_highpass", True),
                highpass_cutoff_hz=float(
                    filters.get("noise_filter_highpass_cutoff_hz", 80.0)
                ),
                noise_gate=filters.get("noise_filter_gate", True),
                noise_gate_threshold=float(
                    filters.get("noise_filter_gate_threshold", 0.003)
                ),
                rnnoise=filters.get("noise_filter_rnnoise", False),
                post_capture=filters.get("noise_filter_post_capture", True),
            )
            processor = AudioProcessor(ap_config, sample_rate=sr)

            block_size = 1024
            processed_parts = []
            for i in range(0, len(audio), block_size):
                block = audio[i:i + block_size]
                processed_parts.append(processor.process_chunk(block))
            processed = np.concatenate(processed_parts)

            processed = processor.process_full_audio(processed)

            if len(processed) > 0:
                log.info(
                    "[LEVEL-MON] Applied real-time chain + post_capture=%s: "
                    "highpass=%s, gate=%s, rnnoise=%s",
                    ap_config.post_capture,
                    ap_config.highpass,
                    ap_config.noise_gate,
                    ap_config.rnnoise,
                )
                audio = processed
        except Exception as exc:
            log.warning(
                "[LEVEL-MON] Filter application failed (using raw audio): %s", exc
            )

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
        from voice_typer.server.ipc_server import _push_event_now

        _push_event_now({
            "type": "microphone_test_complete",
            "data": {"duration": _test_duration},
        })
    except Exception:
        pass


def _cancel_test_locked() -> bool:
    """Cancel test state under the lock.

    Returns True if a test was actually active, False otherwise.
    """
    global _test_mode, _test_chunks, _test_raw_chunks, _test_filters, _test_start_time, _test_auto_stop_timer
    global _test_peak_history, _test_rms_history, _test_clip_count, _test_silence_blocks

    # Stop auto-stop timer if running
    timer = _test_auto_stop_timer
    if timer is not None:
        timer.cancel()

    with _monitor_lock:
        if not _test_mode and not _test_chunks:
            return False
        was_active = _test_mode
        _test_mode = False
        _test_chunks = []
        _test_raw_chunks = []
        _test_start_time = 0.0
        _test_filters.clear()
        _test_peak_history = []
        _test_rms_history = []
        _test_clip_count = 0
        _test_silence_blocks = 0
        return was_active
