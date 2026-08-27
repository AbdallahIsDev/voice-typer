"""Test-recording public API for the level_monitor package.

Contains the ad-hoc microphone-test recording API (``start_test_recording``,
``stop_test_recording``, ``cancel_test_recording``, ``is_test_active``,
``update_test_filters``) plus the internal helpers
(``_do_auto_stop_test``, ``_cancel_test_locked``, ``_reset_test_chunks``,
``_secure_clear_test_chunks``) that the monitoring / worker submodules
also call into.

The test recording uses the SAME PortAudio InputStream as the continuous
level monitor (see :mod:`.monitoring`) — opening a second stream on the
same device triggers a Windows MME device-conflict error. When
``_test_mode`` is True, the level worker (see :mod:`.worker`) also
appends each chunk to ``_test_raw_chunks`` (RAW audio — "before" WAV)
and, when a live filter processor is active, to ``_test_filtered_chunks``
(FILTERED audio — "after" WAV).
"""

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

from ._state import _state

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger("voice_typer.server.level_monitor")


def _secure_clear_test_chunks(*deques: collections.deque) -> None:
    """securely zero the np.ndarray chunks in the test
    deques on the background worker thread.

    Wraps :func:`voice_typer.server.recording._secure_clear_array_background`
    in a lazy import + try/except so a failure to import the recording
    package (e.g. optional-dep missing in a stripped-down test env)
    doesn't break ``stop_test_recording``. The secure-clear is
    best-effort: if the background worker is unavailable, the deques
    fall through to GC, which still reclaims the memory (just without
    the explicit ``chunk.fill(0)`` that prevents residual data lingering
    in the process heap until the next allocation reuses the page).

    Parameters
    ----------
    *deques
        The deques whose ``np.ndarray`` elements should be zeroed.
        The deques themselves are NOT mutated — the caller is
        expected to ``.clear()`` or replace them after this call.
        We pass a snapshot of the deque's contents to the worker
        (via ``collections.deque(list(d))``) so a subsequent
        ``.clear()`` on the original deque doesn't race the worker.
    """
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
            # fresh deque so the worker iterates the snapshot even if
            # the caller ``.clear()``s the original deque before the
            # worker picks it up. This preserves the deque identity +
            # maxlen the caller needs ( invariant) while still
            # letting the worker zero the chunks.
            snapshot = collections.deque(list(d))
            _secure_clear_array_background(snapshot)
        except Exception:
            log.debug(
                "[LEVEL-MON] secure clear of test chunks failed for one deque (best-effort; GC will reclaim)",
                exc_info=True,
            )


# ── Test-WAV disk transport ─────────────────────────────────────────
#
# The completed test's WAV payloads are ~0.9 MB each (10 s @ 44.1/48 kHz
# mono 16-bit) and the IPC transports cap a single outbound frame at
# 1 MiB (WS ``_MAX_FRAME_BYTES`` / TCP ``_TCP_MAX_OUTBOUND_BYTES``).
# Returning BOTH WAVs as base64 (~1.2 MB each → >2.4 MB total) exceeded
# the cap: the frame was silently dropped, the renderer timed out, and
# a perfectly recorded 10 s test produced no result.
#
# Transport contract now: stop writes each WAV ONCE under the user's
# config dir (`<config>/mic-test-recordings/`) and returns small JSON
# metadata ({"path", "bytes"}); the renderer fetches the bytes via the
# chunked ``microphone_test_read_audio`` IPC command (each chunk well
# under the frame cap). At most ONE completed test's files exist on
# disk: ``start_test_recording`` purges leftovers.
_TEST_RECORDINGS_DIRNAME = "mic-test-recordings"


def _test_recordings_dir() -> Path:
    """Return (and create) the mic-test recordings dir under the config dir."""
    from voice_typer.server.config_internals.paths import _config_dir

    d = Path(_config_dir()) / _TEST_RECORDINGS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _purge_test_recordings() -> None:
    """Best-effort delete of leftover test WAVs from previous tests.

    Keep-only-latest: a fresh test invalidates prior recordings (they
    were already delivered/fetched by the renderer, or superseded).
    Never raises — a locked file must not break the new test.
    """
    try:
        d = _test_recordings_dir()
        for f in d.glob("*.wav"):
            try:
                f.unlink()
            except OSError:
                log.debug("[LEVEL-MON] could not unlink leftover test WAV: %s", f)
    except Exception:
        log.debug("[LEVEL-MON] test-recording purge failed", exc_info=True)


def _write_test_wav(buf: io.BytesIO, kind: str) -> dict | None:
    """Write *buf*'s WAV bytes to a unique file; return {"path","bytes"}.

    Returns None when the payload is empty (nothing to persist). The
    random suffix prevents collisions if a previous run's files couldn't
    be purged. 0o600 perms on POSIX keep biometric voice data private;
    Windows ACLs inherit the (already tightened) config-dir defaults.
    """
    data = buf.getvalue()
    if not data:
        return None
    d = _test_recordings_dir()
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
    """Return a base64 slice [offset, offset+length) of a test WAV file.

    SECURITY: *path* must resolve INSIDE the mic-test-recordings dir —
    this endpoint hands raw file bytes back over IPC, so absolute paths,
    traversal (``..``), symlinks pointing elsewhere, and any other file
    are rejected with ``success: False`` rather than leaked.
    """
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
        # multiple of 3 bytes. The renderer assembles a file by joining the
        # per-slice base64 strings; independently-encoded fragments carry
        # their own "=" padding, and joining fragments whose byte sizes are
        # not multiples of 3 produces corrupted audio (padding appearing
        # mid-stream). 256*1024 % 3 == 1 — clamp to the nearest lower
        # multiple of 3 so interior slices never carry padding.
        length -= length % 3
        offset = max(0, int(offset))
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
    """(Re) create the bounded test-chunk deques under the right capacity.

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
    sr = _state._monitor_sample_rate
    # Chunks arrive at ``sr / 512`` per second (512-sample blocks @ sr).
    # +1 fudge so a duration that lands exactly on a block boundary
    # never drops the final chunk before stop_test_recording reads it.
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


# ── Public API: test recording ──────────────────────────────────────


def is_test_active() -> bool:
    """Return True if a microphone test is currently recording.

    Returns:
        True if test mode is active and recording audio.
    """
    with _state._monitor_lock:
        return _state._test_mode


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
    with _state._monitor_lock:
        if _state._test_mode:
            return {
                "success": False,
                "message": "Test already running",
                "duration": duration,
            }

        # Keep-only-latest disk transport: a new test invalidates any
        # previous test's WAV files (see module docstring of the
        # transport block). Best-effort; runs before recording starts.
        _purge_test_recordings()

        # Ensure the monitor is running on the correct device
        if not _state._monitor_active or _state._monitor_mic_id != mic_id:
            # We must release the lock before calling start_monitoring
            # (which also acquires the lock).  Start monitoring, then
            # re-check state under the lock.
            pass  # handled below the lock
        else:
            # Monitor is already active on the right device — set test mode
            _state._test_mode = True
            _state._test_start_time = time.perf_counter()
            _state._test_duration = max(1.0, min(30.0, duration))
            # (re)create bounded deques sized to this start's
            # duration + device sample rate. Must run AFTER _test_duration
            # is finalized above (the helper reads it). We are still
            # holding _monitor_lock here, so pass locked=True.
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
                "[LEVEL-MON] Test recording started: mic=%s, duration=%.1fs",
                _state._monitor_mic_id or "default",
                _state._test_duration,
            )
            return {
                "success": True,
                "message": "Recording test...",
                "duration": _state._test_duration,
                "sample_rate": sr,
            }

    # Monitor not running or on wrong device — start/restart it
    # (outside the lock since start_monitoring acquires its own lock).
    # Local import to avoid a top-level circular dependency.
    from .monitoring import start_monitoring

    mon_result = start_monitoring(mic_id=mic_id)
    if not mon_result.get("success"):
        return {
            "success": False,
            "message": mon_result.get("message", "Failed to start monitor"),
            "duration": duration,
        }

    # Monitor is now running on the correct device — set test mode
    with _state._monitor_lock:
        if _state._test_mode:
            return {
                "success": False,
                "message": "Test already running",
                "duration": duration,
            }
        _state._test_mode = True
        _state._test_start_time = time.perf_counter()
        _state._test_duration = max(1.0, min(30.0, duration))
        # (re)create bounded deques sized to this start's
        # duration + device sample rate. Must run AFTER _test_duration
        # is finalized above (the helper reads it). Still holding
        # _monitor_lock here, so pass locked=True.
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
            "[LEVEL-MON] Test recording started: mic=%s, duration=%.1fs",
            _state._monitor_mic_id or "default",
            _state._test_duration,
        )
        return {
            "success": True,
            "message": "Recording test...",
            "duration": _state._test_duration,
            "sample_rate": sr,
        }


def stop_test_recording() -> dict:
    """Stop the test recording and return the captured audio as base64 WAV.

    Returns:
        dict with success, audio_base64, duration_ms, sample_rate, message.
    """
    # Cancel the auto-stop timer under ``_monitor_lock``: the
    # auto-stop timer thread (``_do_auto_stop_test``) also acquires
    # the lock, as do ``cancel_test_recording`` and
    # ``_cancel_test_locked``. Holding the lock here avoids the race
    # where the timer thread fires ``_do_auto_stop_test`` between
    # the ``is not None`` check and the ``cancel()`` call (which
    # would leave a stray reference and a duplicate stop callback).
    with _state._monitor_lock:
        timer = _state._test_auto_stop_timer
        if timer is not None:
            timer.cancel()
            _state._test_auto_stop_timer = None

    with _state._monitor_lock:
        was_active = _state._test_mode
        sr = _state._monitor_sample_rate
        # Snapshot the three test-chunk buffers:
        # - ``_test_chunks``: backward-compat shim, always empty in
        #   production (kept for tests outside this module that append
        #   to it directly).
        # - ``_test_raw_chunks``: RAW audio — source for the "before" WAV.
        # - ``_test_filtered_chunks``: FILTERED audio (post-``process_chunk``)
        #   captured by the worker — source for the "after" WAV when
        #   populated, eliminating the 7-70s synchronous re-filter that
        # previously blocked the IPC thread at stop time ().
        raw_chunks = list(_state._test_raw_chunks)
        filtered_chunks = list(_state._test_filtered_chunks)
        filters = dict(_state._test_filters)
        # Dead ``list(_test_peak_history)`` expression removed
        # (the value was discarded immediately — peak history is
        # consumed via the dedicated level-monitor callback, not here).
        rms_hist = list(_state._test_rms_history)
        clip_count = _state._test_clip_count
        silence_blocks = _state._test_silence_blocks

        # Clear test state
        # use .clear() (NOT reassignment to []) so the
        # bounded deque + its maxlen are preserved across the test.
        # A plain ``_test_chunks = []`` would clobber the deque back
        # to an unbounded list and reintroduce the leak.
        #
        # BEFORE ``.clear()``, hand the chunks off to
        # ``_secure_clear_test_chunks`` so the np.ndarray buffers
        # containing potentially-biometric voice data are zeroed on
        # the background worker. This matches the recording.py
        # ``_secure_clear_array_background`` pattern used for the
        # dictation buffer. The helper takes a SNAPSHOT of each
        # deque's contents so the subsequent ``.clear()`` doesn't
        # race the worker.
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
    # this module that append to it directly) and is NOT a source of audio.
    # Only ``_test_raw_chunks`` ("before" WAV) and ``_test_filtered_chunks"
    # ("after" WAV) are sources. If both are empty, return "No audio
    # captured" — even if the legacy shim has data (test_stop_returns_
    # no_audio_when_only_test_chunks_populated relies on this).
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
            "audio_file": None,
            "raw_audio_file": None,
            "duration_ms": 0,
            "sample_rate": sr,
            "message": f"Audio processing failed: {exc}",
            "quality": {},
        }

    # Build ``audio`` (the "after" WAV) from
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

    # annotate ``quality`` as ``dict[str, Any]`` so that
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
    # skip the post-hoc filter when ``filtered_chunks`` was
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

    # ── Persist WAVs to disk (file-reference IPC transport) ─────────
    # See the module-top transport block: base64-in-one-frame exceeded
    # the 1 MiB IPC cap and silently dropped the whole result.
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

    audio_file = _write_test_wav(buf, "filtered")
    raw_audio_file = _write_test_wav(raw_buf, "raw")

    log.info(
        "[LEVEL-MON] Test stopped: %.1fs recorded — wrote raw(before)=%d bytes + filtered(after)=%d bytes WAV to %s/",
        duration_ms / 1000,
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
    with _state._monitor_lock:
        if not _state._test_mode:
            return
        # Merge new settings into existing test filters so individual
        # toggles don't reset unrelated settings back to defaults.
        _state._test_filters.update(filters_dict)
        log.debug(
            "[LEVEL-MON] Test filters updated in-flight: %s",
            {k: v for k, v in _state._test_filters.items() if k.startswith("noise_filter_")},
        )


def cancel_test_recording() -> dict:
    """Cancel an in-progress test recording without returning audio."""
    # Cancel the auto-stop timer under ``_monitor_lock`` (mirrors
    # ``stop_test_recording``): the auto-stop timer thread
    # (``_do_auto_stop_test``) also acquires the lock. Holding the
    # lock here avoids the race where the timer thread fires
    # ``_do_auto_stop_test`` between the ``is not None`` check and
    # the ``cancel()`` call (which would leave a stray timer
    # reference and a duplicate stop callback).
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


# ── Internal helpers ────────────────────────────────────────────────


def _do_auto_stop_test() -> None:
    """Auto-stop callback fired by the threading.Timer.

    Stops the test recording (clears test mode) and notifies the
    frontend via push event so it can call stop_test_recording() to
    retrieve the audio.
    """
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
        # this is load-bearing — if the publish fails, the
        # frontend UI hangs in "test running" state forever with no
        # indication the test completed. Log a warning so the user can
        # diagnose why the mic test isn't completing.
        log.warning(
            "[LEVEL-MON] failed to publish microphone_test_complete event",
            exc_info=True,
        )

    # G-PERF-RELIABILITY: do NOT clear chunks on auto-stop.
    # The frontend ``stop_test_recording`` IPC handler runs
    # AFTER auto-stop fires to fetch the audio; if auto-stop
    # cleared the chunks, the retrieval would return an empty
    # audio payload and the user would see a "test failed" toast
    # despite the test having run to completion. The chunks are
    # now cleared by ``stop_test_recording`` (after the
    # frontend retrieves) and by ``cancel_test_recording`` (the
    # user-cancel path), so the bounded deque (``maxlen == 3s of
    # audio at 16kHz``) caps the lingering memory at one test's
    # worth — the intended fix.


def _cancel_test_locked() -> bool:
    """Cancel test state under the lock.

    Returns True if a test was actually active, False otherwise.

    Note: despite the name, this function acquires ``_monitor_lock``
    itself (it does NOT require the caller to hold it). The auto-stop
    timer cancel is performed INSIDE the ``with _monitor_lock:`` block
    so that all mutation sites of ``_test_auto_stop_timer`` are
    protected against the race where the timer thread fires
    ``_do_auto_stop_test`` between an ``is not None`` check and the
    matching ``cancel()`` call. The redundant double-cancel from
    ``cancel_test_recording`` (which also cancels under the lock
    before calling this function) is a harmless no-op: by the time
    we reach here, ``_test_auto_stop_timer`` is already ``None`` (or
    the timer thread fired and cleared it), so the ``is not None``
    guard short-circuits.
    """
    with _state._monitor_lock:
        # Stop auto-stop timer if running (under the lock to close
        # the third mutation-site race).
        timer = _state._test_auto_stop_timer
        if timer is not None:
            timer.cancel()
            _state._test_auto_stop_timer = None

        if not _state._test_mode and not _state._test_chunks and not _state._test_filtered_chunks:
            return False
        was_active = _state._test_mode
        _state._test_mode = False
        # .clear() preserves the bounded deque (and its maxlen).
        # reassigning to [] would make it an unbounded list again.
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
