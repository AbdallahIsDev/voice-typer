"""Microphone test-recording helpers."""

from __future__ import annotations

import collections
import contextlib
import io
import logging
import os
import threading
import time
import types
import uuid
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from voice_typer.server._audio_constants import AUDIO_LOW_VOLUME_RMS, AUDIO_SILENCE_RMS
from voice_typer.server.duration import format_duration

from ._state import _state

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger("voice_typer.server.level_monitor")


# Volume bands share the dictation path's boundaries (see
MIC_TEST_GOOD_VOLUME_RMS = AUDIO_LOW_VOLUME_RMS
MIC_TEST_VERY_LOW_VOLUME_RMS = AUDIO_SILENCE_RMS

# Background-noise bands apply to the noise FLOOR (the quietest
_MIC_TEST_NOISE_LOW_RMS = 0.005
_MIC_TEST_NOISE_HIGH_RMS = 0.05

# Voice presence: a peak above this fraction of full scale in a block
_MIC_TEST_VOICE_PEAK = 0.05
# ... but one loud transient (click/pop) in an otherwise silent test
_MIC_TEST_VOICE_MAX_SILENCE_RATIO = 0.95
_MIC_TEST_VOICE_MIN_NON_SILENT_BLOCKS = 3


def _secure_clear_test_chunks(*deques: collections.deque) -> None:
    """securely zero the np.ndarray chunks in the test"""
    try:
        from voice_typer.server.recording import _secure_clear_array_background
    except Exception:
        log.debug(
            "[LEVEL-MON] _secure_clear_array_background unavailable; "
            "skipping secure clear of test chunks (GC will reclaim)",
            exc_info=True,
        )
        return
    for d in deques:
        if not d:
            continue
        try:
            # Wrap a SNAPSHOT of the deque's current contents in a
            snapshot = collections.deque(list(d))
            _secure_clear_array_background(snapshot)
        except Exception:
            log.debug(
                "[LEVEL-MON] secure clear of test chunks failed for one deque (best-effort; GC will reclaim)",
                exc_info=True,
            )


# The completed test's WAV payloads are ~0.9 MB each (10 s @ 44.1/48 kHz
_TEST_RECORDINGS_DIRNAME = "mic-test-recordings"

# Mic-test WAV disk TTL: auto-delete a test's persisted WAVs this many
MIC_TEST_RECORDING_TTL_SEC = 300

# Live per-file expiry timers (threading.Timer, daemon like
_test_recording_expiry_timers: set[threading.Timer] = set()


def _test_recordings_dir() -> Path:
    """Return (and create) the mic-test recordings dir under the config dir."""
    from voice_typer.server.config_internals.paths import _config_dir

    d = Path(_config_dir()) / _TEST_RECORDINGS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        _delete_expired_recordings()
    return d


def _remove_recordings_dir_if_empty() -> None:
    """Best-effort remove of the recordings dir when it holds no files."""
    try:
        from voice_typer.server.config_internals.paths import _config_dir

        with contextlib.suppress(OSError):
            (Path(_config_dir()) / _TEST_RECORDINGS_DIRNAME).rmdir()
    except Exception:
        log.debug("[LEVEL-MON] recordings-dir remove failed", exc_info=True)


def _purge_test_recordings() -> None:
    """Best-effort delete of leftover test WAVs from previous tests."""
    try:
        d = _test_recordings_dir()
        for pattern in ("*.wav", "*.wav.tmp"):
            for f in d.glob(pattern):
                try:
                    f.unlink()
                except OSError:
                    log.debug("[LEVEL-MON] could not unlink leftover test WAV: %s", f)
        _remove_recordings_dir_if_empty()
    except Exception:
        log.debug("[LEVEL-MON] test-recording purge failed", exc_info=True)


def _delete_test_recording_paths(paths) -> None:
    """Best-effort unlink of exactly the given persisted WAV paths."""
    try:
        targets = [str(p) for p in (paths or []) if p]
        if not targets:
            return
        deleted = 0
        for p in targets:
            try:
                Path(p).unlink()
                deleted += 1
            except FileNotFoundError:
                continue
            except OSError:
                log.debug("[LEVEL-MON] could not unlink expired test WAV: %s", p)
        log.debug(
            "[LEVEL-MON] expired mic-test WAV delete: %d/%d file(s) removed",
            deleted,
            len(targets),
        )
        _remove_recordings_dir_if_empty()
    except Exception:
        log.debug("[LEVEL-MON] expired mic-test WAV delete failed", exc_info=True)


def _schedule_test_recording_expiry(paths, ttl_sec: float = MIC_TEST_RECORDING_TTL_SEC) -> threading.Timer | None:
    """Schedule best-effort deletion of exactly *paths* after *ttl_sec*.

    Mirrors the _test_auto_stop_timer daemon pattern. Never raises.
    """
    try:
        targets = [str(p) for p in (paths or []) if p]
        if not targets:
            return None
        timer: threading.Timer | None = None

        def _fire() -> None:
            try:
                _delete_test_recording_paths(targets)
            finally:
                with contextlib.suppress(Exception):
                    _test_recording_expiry_timers.discard(timer)

        timer = threading.Timer(ttl_sec, _fire)
        timer.daemon = True
        _test_recording_expiry_timers.add(timer)
        timer.start()
        log.debug(
            "[LEVEL-MON] scheduled mic-test WAV expiry in %ss for %d file(s)",
            ttl_sec,
            len(targets),
        )
        return timer
    except Exception:
        log.debug("[LEVEL-MON] failed to schedule mic-test WAV expiry", exc_info=True)
        return None


def _delete_expired_recordings(max_age_sec: float = MIC_TEST_RECORDING_TTL_SEC) -> int:
    """Best-effort unlink of mic-test WAVs older than *max_age_sec* by mtime."""
    try:
        from voice_typer.server.config_internals.paths import _config_dir

        d = Path(_config_dir()) / _TEST_RECORDINGS_DIRNAME
        if not d.is_dir():
            return 0
        now = time.time()
        deleted = 0
        for pattern in ("*.wav", "*.wav.tmp"):
            try:
                files = list(d.glob(pattern))
            except OSError:
                continue
            for f in files:
                try:
                    if now - f.stat().st_mtime > max_age_sec:
                        f.unlink()
                        deleted += 1
                except FileNotFoundError:
                    continue
                except OSError:
                    log.debug("[LEVEL-MON] could not unlink expired test WAV: %s", f)
        if deleted:
            log.debug("[LEVEL-MON] expired mic-test WAV sweep removed %d file(s)", deleted)
        _remove_recordings_dir_if_empty()
        return deleted
    except Exception:
        log.debug("[LEVEL-MON] expired mic-test WAV sweep failed", exc_info=True)
        return 0


def _write_test_wav(buf: io.BytesIO, kind: str) -> dict | None:
    """Write *buf*'s WAV bytes to a unique file; return {"path","bytes"}.

    Returns None when the payload is empty (nothing to persist). The
    """
    data = buf.getvalue()
    if not data:
        return None
    d = _test_recordings_dir()
    # Re-ensure: the TTL sweep inside _test_recordings_dir() may have
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"test-{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.wav"
    tmp = path.with_suffix(".wav.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    from voice_typer.server.platform_utils import is_windows

    if not is_windows():
        # POSIX only: keep biometric voice data owner-readable.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    return {"path": str(path), "bytes": len(data)}


def read_test_recording_slice(path: str, offset: int, length: int) -> dict:
    """Return a base64 slice [offset, offset+length) of a test WAV file."""
    import base64 as _b64

    try:
        requested = Path(path)
        root = _test_recordings_dir().resolve()
        resolved = requested.resolve()
        if resolved.parent != root or resolved.suffix.lower() != ".wav":
            return {
                "success": False,
                "data_b64": "",
                "bytes_read": 0,
                "total_bytes": 0,
                "eof": True,
                "message": "path outside microphone-test recordings",
            }
        if not resolved.is_file():
            return {
                "success": False,
                "data_b64": "",
                "bytes_read": 0,
                "total_bytes": 0,
                "eof": True,
                "message": "recording not found",
            }
        total = resolved.stat().st_size
        length = max(0, min(int(length), 256 * 1024))
        # BASE64-SAFE SLICING INVARIANT: every NON-FINAL slice must be a
        length -= length % 3
        offset = max(0, int(offset))
        remaining = total - offset
        if remaining <= 0:
            return {
                "success": True,
                "data_b64": "",
                "bytes_read": 0,
                "total_bytes": total,
                "eof": True,
                "message": "ok",
            }
        if length == 0:
            # Requests of 1-2 bytes align down to a 0-byte slice, which
            length = 3 if remaining >= 3 else remaining
        with open(resolved, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(length)
        return {
            "success": True,
            "data_b64": _b64.b64encode(chunk).decode("ascii"),
            "bytes_read": len(chunk),
            "total_bytes": total,
            "eof": offset + len(chunk) >= total,
            "message": "ok",
        }
    except Exception as exc:
        log.warning("[LEVEL-MON] read_test_recording_slice failed: %s", exc)
        return {
            "success": False,
            "data_b64": "",
            "bytes_read": 0,
            "total_bytes": 0,
            "eof": True,
            "message": type(exc).__name__,
        }


def _reset_test_chunks(locked: bool) -> None:
    """(Re) create the bounded test-chunk deques under the right capacity."""
    sr = _state._monitor_sample_rate
    # Capacity bound: chunks arrive at ``sr / blocksize`` per second where
    cap = int(_state._test_duration * sr / 512) + 1
    if cap < 1:
        cap = 1
    new_chunks = collections.deque(maxlen=cap)
    new_raw = collections.deque(maxlen=cap)
    new_filtered = collections.deque(maxlen=cap)

    if locked:
        _state._test_chunks = new_chunks
        _state._test_raw_chunks = new_raw
        _state._test_filtered_chunks = new_filtered
    else:
        with _state._monitor_lock:
            _state._test_chunks = new_chunks
            _state._test_raw_chunks = new_raw
            _state._test_filtered_chunks = new_filtered


def is_test_active() -> bool:
    """Return True if a microphone test is currently recording.

    Returns:
    """
    with _state._monitor_lock:
        return _state._test_mode


def _begin_test_locked(duration: float, filters: dict | None) -> dict:
    """Arm test mode under ``_monitor_lock`` (caller holds the lock).

    Shared by both ``start_test_recording`` paths: the monitor already on
    the right device, and the restart path after ``start_monitoring``.
    """
    _state._test_mode = True
    _state._test_start_time = time.perf_counter()
    _state._test_duration = max(1.0, min(30.0, duration))
    # (re)create bounded deques sized to this start's duration
    _reset_test_chunks(locked=True)
    _state._test_filters = dict(filters) if filters else {}
    _state._test_peak_history.clear()
    _state._test_rms_history.clear()
    _state._test_clip_count = 0
    _state._test_silence_blocks = 0
    sr = _state._monitor_sample_rate

    _state._test_auto_stop_timer = threading.Timer(
        _state._test_duration,
        _do_auto_stop_test,
    )
    _state._test_auto_stop_timer.daemon = True
    _state._test_auto_stop_timer.start()

    log.info(
        "[LEVEL-MON] Test recording started: mic=%s | duration%s",
        _state._monitor_mic_id or "default",
        format_duration(_state._test_duration),
    )
    return {
        "success": True,
        "message": "Recording test...",
        "duration": _state._test_duration,
        "sample_rate": sr,
    }


def start_test_recording(
    mic_id: str | None = None,
    duration: float = 10.0,
    filters: dict | None = None,
) -> dict:
    """Start a microphone test recording using the existing monitor stream."""
    with _state._monitor_lock:
        if _state._test_mode:
            return {
                "success": False,
                "message": "Test already running",
                "duration": duration,
            }

        # Keep-only-latest disk transport: a new test invalidates any
        _purge_test_recordings()

        # Ensure the monitor is running on the correct device
        if not _state._monitor_active or _state._monitor_mic_id != mic_id:
            # We must release the lock before calling start_monitoring
            pass  # handled below the lock
        else:
            # Monitor is already active on the right device.
            return _begin_test_locked(duration, filters)

    # Monitor not running or on wrong device, start/restart it
    from .monitoring import start_monitoring

    mon_result = start_monitoring(mic_id=mic_id)
    if not mon_result.get("success"):
        return {
            "success": False,
            "message": mon_result.get("message", "Failed to start monitor"),
            "duration": duration,
        }

    # Monitor is now running on the correct device.
    with _state._monitor_lock:
        if _state._test_mode:
            return {
                "success": False,
                "message": "Test already running",
                "duration": duration,
            }
        return _begin_test_locked(duration, filters)


def stop_test_recording() -> dict:
    """Stop the test recording and return the captured audio as base64 WAV.

    Returns:
    """
    # Cancel the auto-stop timer under ``_monitor_lock``: the
    with _state._monitor_lock:
        timer = _state._test_auto_stop_timer
        if timer is not None:
            timer.cancel()
            _state._test_auto_stop_timer = None

    with _state._monitor_lock:
        was_active = _state._test_mode
        sr = _state._monitor_sample_rate
        # Snapshot the three test-chunk buffers:
        raw_chunks = list(_state._test_raw_chunks)
        filtered_chunks = list(_state._test_filtered_chunks)
        filters = dict(_state._test_filters)
        # Dead ``list(_test_peak_history)`` expression removed
        rms_hist = list(_state._test_rms_history)
        clip_count = _state._test_clip_count
        silence_blocks = _state._test_silence_blocks

        # Clear test state
        _secure_clear_test_chunks(
            _state._test_raw_chunks,
            _state._test_filtered_chunks,
            _state._test_chunks,
        )
        _state._test_mode = False
        _state._test_chunks.clear()
        _state._test_raw_chunks.clear()
        _state._test_filtered_chunks.clear()
        _state._test_start_time = 0.0
        _state._test_filters.clear()
        _state._test_peak_history.clear()
        _state._test_rms_history.clear()
        _state._test_clip_count = 0
        _state._test_silence_blocks = 0

    # ``_test_chunks`` is a backward-compat shim (kept for tests outside
    if not was_active and not raw_chunks and not filtered_chunks:
        return {
            "success": False,
            "audio_file": None,
            "raw_audio_file": None,
            "duration_ms": 0,
            "sample_rate": 16000,
            "message": "No test running",
            "quality": {},
        }

    if not raw_chunks and not filtered_chunks:
        return {
            "success": True,
            "audio_file": None,
            "raw_audio_file": None,
            "duration_ms": 0,
            "sample_rate": sr,
            "message": "No audio captured",
            "quality": {},
        }

    # Build ``raw_audio`` (the "before" WAV) from ``_test_raw_chunks``.
    try:
        if raw_chunks:
            raw_audio = np.concatenate(raw_chunks, axis=0).reshape(-1)
        else:
            raw_audio = np.concatenate(filtered_chunks, axis=0).reshape(-1)
    except Exception as exc:
        log.warning("[LEVEL-MON] Chunk concatenation failed: %s", exc)
        return {
            "success": False,
            "audio_file": None,
            "raw_audio_file": None,
            "duration_ms": 0,
            "sample_rate": sr,
            "message": f"Audio processing failed: {exc}",
            "quality": {},
        }

    # Build ``audio`` (the "after" WAV) from
    if filtered_chunks:
        try:
            audio = np.concatenate(filtered_chunks, axis=0).reshape(-1)
        except Exception as exc:
            log.warning("[LEVEL-MON] Filtered chunk concatenation failed: %s", exc)
            audio = raw_audio.copy()
    else:
        audio = raw_audio.copy()

    duration_ms = int(len(audio) / sr * 1000)

    raw_abs = np.abs(raw_audio)
    raw_rms = float(np.sqrt(np.mean(np.square(raw_audio.astype(np.float32)))))
    raw_peak = float(raw_abs.max())
    total_blocks = len(rms_hist) if rms_hist else 0
    silence_ratio_value = round(silence_blocks / max(1, total_blocks), 4)
    non_silent_blocks = total_blocks - silence_blocks
    if total_blocks > 0:
        # Voice requires a loud peak AND a non-trivial non-silent share:
        has_voice = (
            raw_peak > _MIC_TEST_VOICE_PEAK
            and silence_ratio_value < _MIC_TEST_VOICE_MAX_SILENCE_RATIO
            and non_silent_blocks >= _MIC_TEST_VOICE_MIN_NON_SILENT_BLOCKS
        )
    else:
        # No per-block history to assess the share from (e.g. chunks
        has_voice = raw_peak > _MIC_TEST_VOICE_PEAK

    # Background noise is graded on the noise FLOOR (quietest ~32 ms
    noise_floor = float(min(rms_hist)) if rms_hist else raw_rms
    if noise_floor < _MIC_TEST_NOISE_LOW_RMS:
        noise_level = "low"
    elif noise_floor < _MIC_TEST_NOISE_HIGH_RMS:
        noise_level = "moderate"
    else:
        noise_level = "high"
    if has_voice and noise_level == "high":
        # A sustained voice signal dominates the total energy: the
        noise_level = "moderate"

    # annotate ``quality`` as ``dict[str, Any]`` so that
    quality: dict[str, Any] = {
        "volume_level": (
            "good"
            if raw_rms >= MIC_TEST_GOOD_VOLUME_RMS
            else ("very_low" if raw_rms < MIC_TEST_VERY_LOW_VOLUME_RMS else "low")
        ),
        "volume_rms": round(raw_rms, 6),
        "peak_level": round(raw_peak, 4),
        "noise_level": noise_level,
        "has_voice": has_voice,
        "has_clipping": clip_count > 0,
        "clipping_blocks": clip_count,
        "total_blocks": total_blocks,
        "silence_ratio": silence_ratio_value,
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
        detected_issues.append("Volume too low, speak closer to the microphone")
    elif quality["volume_level"] == "low":
        detected_issues.append("Volume is low, consider raising input gain")
    if not quality["has_voice"]:
        detected_issues.append("No voice detected, try speaking during the test")
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
    # Inaudible input is already charged once via the very_low -40
    if raw_rms > 0.1:
        est_score = max(0, est_score - 10)
    quality["estimated_transcription_quality"] = max(0, min(100, est_score))

    # skip the post-hoc filter when ``filtered_chunks`` was
    if not filtered_chunks and filters and filters.get("noise_filter_enabled", True):
        try:
            # ADR 0007: AudioProcessor takes a config-like object directly.
            from voice_typer.server.audio_processor import AudioProcessor

            ap_config = types.SimpleNamespace(**filters)
            processor = AudioProcessor(ap_config, sample_rate=sr, quiet=True)

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

    # See the module-top transport block: base64-in-one-frame exceeded
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())

    # Raw ("before") WAV for before/after comparison.
    raw_int16 = (raw_audio * 32767).astype(np.int16)
    raw_buf = io.BytesIO()
    with wave.open(raw_buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(raw_int16.tobytes())

    # The test-chunk state was already cleared above, so a persist
    try:
        audio_file = _write_test_wav(buf, "filtered")
        raw_audio_file = _write_test_wav(raw_buf, "raw")
    except Exception as exc:
        log.warning(
            "[LEVEL-MON] Test persist failed:%s not recorded: %s",
            format_duration(duration_ms / 1000),
            exc,
        )
        return {
            "success": False,
            "audio_file": None,
            "raw_audio_file": None,
            "duration_ms": duration_ms,
            "sample_rate": sr,
            "message": f"Failed to persist test recording: {type(exc).__name__}",
            "quality": quality,
        }

    # Disk TTL: auto-delete exactly these uuid paths TTL seconds after
    _schedule_test_recording_expiry(
        [ref["path"] for ref in (audio_file, raw_audio_file) if isinstance(ref, dict) and ref.get("path")]
    )

    log.info(
        "[LEVEL-MON] Test stopped:%s recorded, wrote raw(before)=%d bytes + filtered(after)=%d bytes WAV to %s/",
        format_duration(duration_ms / 1000),
        len(raw_buf.getvalue()),
        len(buf.getvalue()),
        _TEST_RECORDINGS_DIRNAME,
    )

    return {
        "success": True,
        "audio_file": audio_file,
        "raw_audio_file": raw_audio_file,
        "duration_ms": duration_ms,
        "sample_rate": sr,
        "message": f"Recorded{format_duration(duration_ms / 1000)} of audio",
        "quality": quality,
    }


def update_test_filters(filters_dict: dict) -> None:
    """Update the active test recording's filter settings in real-time."""
    with _state._monitor_lock:
        if not _state._test_mode:
            return
        # Merge new settings into existing test filters so individual
        _state._test_filters.update(filters_dict)
        log.debug(
            "[LEVEL-MON] Test filters updated in-flight: %s",
            {k: v for k, v in _state._test_filters.items() if k.startswith("noise_filter_")},
        )


def cancel_test_recording() -> dict:
    """Cancel an in-progress test recording without returning audio."""
    # Cancel the auto-stop timer under ``_monitor_lock`` (mirrors
    with _state._monitor_lock:
        timer = _state._test_auto_stop_timer
        if timer is not None:
            timer.cancel()
            _state._test_auto_stop_timer = None

    was_active = _cancel_test_locked()

    log.info("[LEVEL-MON] Test cancelled")
    if not was_active:
        return {"success": True, "message": "No test running"}
    return {"success": True, "message": "Test cancelled"}


def _do_auto_stop_test() -> None:
    """Auto-stop callback fired by the threading.Timer."""
    with _state._monitor_lock:
        if not _state._test_mode:
            return
        _state._test_mode = False
        _state._test_auto_stop_timer = None

    log.info("[LEVEL-MON] Auto-stop: test ended")

    # Notify the frontend
    try:
        from voice_typer.server import event_bus

        event_bus.publish(
            {
                "type": "microphone_test_complete",
                "data": {"duration": _state._test_duration},
            },
        )
    except Exception:
        # this is load-bearing, if the publish fails, the
        log.warning(
            "[LEVEL-MON] failed to publish microphone_test_complete event",
            exc_info=True,
        )

    # G-PERF-RELIABILITY: do NOT clear chunks on auto-stop.


def _cancel_test_locked() -> bool:
    """Cancel test state under the lock.

    Returns True if a test was actually active, False otherwise.
    """
    with _state._monitor_lock:
        # Stop auto-stop timer if running (under the lock to close
        timer = _state._test_auto_stop_timer
        if timer is not None:
            timer.cancel()
            _state._test_auto_stop_timer = None

        if (
            not _state._test_mode
            and not _state._test_chunks
            and not _state._test_raw_chunks
            and not _state._test_filtered_chunks
        ):
            return False
        was_active = _state._test_mode
        _state._test_mode = False
        # .clear() preserves the bounded deque (and its maxlen).
        _secure_clear_test_chunks(_state._test_raw_chunks, _state._test_filtered_chunks, _state._test_chunks)
        _state._test_chunks.clear()
        _state._test_raw_chunks.clear()
        _state._test_filtered_chunks.clear()
        _state._test_start_time = 0.0
        _state._test_filters.clear()
        _state._test_peak_history.clear()
        _state._test_rms_history.clear()
        _state._test_clip_count = 0
        _state._test_silence_blocks = 0
        return was_active
