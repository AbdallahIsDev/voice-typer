"""VAD method bodies for the Recorder pipeline."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES, WHISPER_SAMPLE_RATE

if TYPE_CHECKING:
    from voice_typer.server.vad_processor import VadState


def refresh_vad_caches(recorder: Any) -> None:
    """``_buffer_sr`` (post-process_chunk rate). None at start(); first chunk"""
    recorder._cached_vad_enabled = recorder._vad.vad_enabled
    recorder._cached_use_silero_vad = recorder._vad.use_silero_vad
    recorder._cached_silero_available = recorder._vad.silero_available
    # Branch on _buffer_sr (post-processor rate), not _effective_sr —
    vad_sr = (
        recorder._audio_pipeline._buffer_sr
        if recorder._audio_pipeline._buffer_sr is not None
        else recorder._effective_sr
    )
    if vad_sr is not None and vad_sr not in SILERO_VAD_SAMPLE_RATES and vad_sr > 0:
        gcd = math.gcd(int(vad_sr), WHISPER_SAMPLE_RATE)
        recorder._cached_vad_resample_up_down = (
            WHISPER_SAMPLE_RATE // gcd,
            int(vad_sr) // gcd,
        )
    else:
        recorder._cached_vad_resample_up_down = None
    recorder._cached_vad_resample_sr = vad_sr


def vad_auto_calibrate(recorder: Any, chunk_rms: float, chunk_duration: float) -> None:
    """Auto-calibrate VAD thresholds from the ambient noise floor."""
    if not recorder._cached_vad_enabled:
        return
    elapsed = time.perf_counter() - recorder._recording_start_time
    recorder._vad.auto_calibrate(chunk_rms, elapsed, chunk_duration)


def vad_update(
    recorder: Any,
    chunk_rms_db: float,
    vad_prob: float | None = None,
) -> VadState:
    """Advance the VAD state machine one frame via VadProcessor.update_frame.

    Returns VadState.UNKNOWN when VAD is disabled (caller treats as not-silence).
    """
    return recorder._vad.update_frame(chunk_rms_db, vad_prob)
