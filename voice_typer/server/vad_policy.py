"""VAD policy thresholds."""

from __future__ import annotations

import logging
import math

import numpy as np

log = logging.getLogger(__name__)


#: Below this duration: never trim (1-2 word safety). The engine's own
SHORT_MAX_S = 2.0
#: Above this duration: never trim, engine filter stays ON (the engine
LONG_MIN_S = 30.0
#: Trim only audio this clean (percent of sub-threshold samples).
SILENCE_PCT_MAX = 15.0
#: Frames this far below peak count as edge silence.
TRIM_TOP_DB = 40.0
#: Never cut more than this fraction of the duration (per side).
MAX_TRIM_FRACTION = 0.25
#: Refuse any trim that would leave less than this much audio.
MIN_REMAINING_S = 0.5
#: Below this RMS the input is near-silence: don't trim (the
RMS_FLOOR = 0.001
#: Amplitude floor mirroring the stats computation in
_SILENCE_AMP_FLOOR = 0.001


def _compute_stats(audio: np.ndarray) -> tuple[float, float, float]:
    """Single-pass (rms, peak, silence_pct), same formulas as the
    transcription invade path, so policy inputs match logged stats."""
    abs_audio = np.abs(audio)
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
    peak = float(np.max(abs_audio)) if audio.size else 0.0
    silence_pct = float(np.sum(abs_audio < _SILENCE_AMP_FLOOR) / max(1, audio.size) * 100)
    return rms, peak, silence_pct


def trim_edge_silence(
    audio: np.ndarray,
    sample_rate: int,
    *,
    top_db: float = TRIM_TOP_DB,
    max_fraction: float = MAX_TRIM_FRACTION,
    min_remaining_s: float = MIN_REMAINING_S,
) -> tuple[np.ndarray, int]:
    """Return ``(view, leading_cut)`` with leading/trailing sub-threshold"""
    n = int(audio.size)
    if n == 0:
        return audio, 0
    peak = float(np.max(np.abs(audio)))
    if not math.isfinite(peak) or peak <= 0.0:
        return audio, 0
    threshold = peak * (10.0 ** (-top_db / 20.0))
    above = np.abs(audio) >= threshold
    if not bool(np.any(above)):
        return audio, 0  # all silence, the hallucination gate owns this
    first = int(np.argmax(above))
    last = int(n - 1 - np.argmax(above[::-1]))
    max_trim = int(n * max_fraction)
    first = min(first, max_trim)
    last = max(last, n - 1 - max_trim)
    if last <= first:
        return audio, 0
    if (last + 1 - first) < min_remaining_s * sample_rate:
        return audio, 0
    return audio[first : last + 1], first


def decide_vad_filter(
    audio: np.ndarray,
    sample_rate: int,
    audio_stats: tuple[float, float, float] | None,
    vad_enabled: bool,
) -> tuple[np.ndarray, bool, float]:
    """Decide ``(audio_to_decode, use_engine_vad_filter, trim_offset_s)``."""
    if not isinstance(audio, np.ndarray):
        # Defensive: some callers pass array-likes (lists). Normalize
        audio = np.asarray(audio, dtype=np.float32)
    if not vad_enabled:
        return audio, False, 0.0
    n = int(audio.size)
    if n == 0:
        return audio, True, 0.0
    duration = n / float(sample_rate)
    if duration < SHORT_MAX_S or duration > LONG_MIN_S:
        # Short: trimming risks words for ~zero gain. Long: the engine
        return audio, True, 0.0
    if audio_stats is not None:
        rms, _peak, silence_pct = audio_stats
    else:
        rms, _peak, silence_pct = _compute_stats(audio)
    if silence_pct > SILENCE_PCT_MAX or rms < RMS_FLOOR:
        return audio, True, 0.0
    trimmed, leading_cut = trim_edge_silence(audio, sample_rate)
    if leading_cut == 0 and trimmed.shape == audio.shape:
        # Already clean edges, nothing for either trimmer to do, so
        return audio, False, 0.0
    return np.ascontiguousarray(trimmed), False, leading_cut / float(sample_rate)
