"""Duration-aware voice-activity filtering policy for transcription.

WHY THIS EXISTS: every dictation paid for TWO full voice-activity scans
of the same audio — the capture-side Silero monitor (live level meter,
silence auto-stop, streaming boundaries) and then faster-whisper's own
bundled Silero pass (``vad_filter=True``) re-scanning the complete
utterance after stop, including a full-audio concat plus one decode per
speech chunk. The capture-side scan is load-bearing (recording control)
and stays; THIS module decides, per dictation, whether the engine-side
scan can be skipped because the audio is already known-clean.

Policy (all thresholds are module constants, tuned conservatively):

- Master switch OFF (``vad_filter_enabled=False``) → raw audio,
  ``vad_filter=False`` everywhere. The model-testing mode: plain voice
  in, plain transcription out, no silence processing.
- SHORT audio (``< SHORT_MAX_S``): never trim, engine filter stays ON.
  Trimming 200 ms off a 1 s "yes" saves nothing and concentrates all
  cutoff risk exactly where a lost word destroys the dictation —
  today's single-filter behavior is already optimal here.
- LONG audio (``> LONG_MIN_S``): never trim, engine filter stays ON.
  The engine needs its filter for >30 s segmentation and bounded
  per-call memory; bypassing it would be a downgrade.
- MEDIUM audio with low silence + healthy level: numpy edge-trim (a
  view slice — zero-copy) + ``vad_filter=False``. This is the win:
  one cheap trim replaces the full second Silero scan and the
  multi-chunk decodes.
- Everything uncertain (no stats, high silence, near-silence level,
  trim would cut too much): today's behavior (no trim, filter ON).

The trim touches ONLY leading/trailing sub-threshold frames — interior
pauses are never dropped, so timestamp joining is unaffected. Segment
timestamps from a trimmed decode shift by the trimmed lead-in; the
hallucination gate consumes post-trim duration (call sites recompute
it), keeping its comparisons consistent.
"""

from __future__ import annotations

import logging
import math

import numpy as np

log = logging.getLogger(__name__)

# ── Policy thresholds ─────────────────────────────────────────────

#: Below this duration: never trim (1-2 word safety). The engine's own
#: filter stays ON — identical to historical behavior.
SHORT_MAX_S = 2.0
#: Above this duration: never trim, engine filter stays ON (the engine
#: needs its filter for >30 s segmentation and bounded memory).
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
#: hallucination gate owns that case).
RMS_FLOOR = 0.001
#: Amplitude floor mirroring the stats computation in
#: ``transcription_result`` (samples below this count as silence).
_SILENCE_AMP_FLOOR = 0.001


def _compute_stats(audio: np.ndarray) -> tuple[float, float, float]:
    """Single-pass (rms, peak, silence_pct) — same formulas as the
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
    """Return ``(view, leading_cut)`` with leading/trailing sub-threshold
    frames removed.

    Never copies (basic slice of a 1-D array) and never drops interior
    audio. ``leading_cut`` is the number of removed leading samples so
    timestamped consumers can compensate. Returns ``(audio, 0)`` — the
    input unchanged — when: the array is empty, the peak is
    non-finite/non-positive, nothing is below threshold, or any safety
    guard trips (fraction cap, minimum remaining).
    """
    n = int(audio.size)
    if n == 0:
        return audio, 0
    peak = float(np.max(np.abs(audio)))
    if not math.isfinite(peak) or peak <= 0.0:
        return audio, 0
    threshold = peak * (10.0 ** (-top_db / 20.0))
    above = np.abs(audio) >= threshold
    if not bool(np.any(above)):
        return audio, 0  # all silence — the hallucination gate owns this
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
    """Decide ``(audio_to_decode, use_engine_vad_filter, trim_offset_s)``.

    ``audio_stats`` is the ``(rms, peak, silence_pct)`` tuple the
    recorder already computed (or ``None`` — computed here in one
    pass). ``vad_enabled`` is the ``vad_filter_enabled`` config switch
    (missing config → behave as enabled = historical behavior).

    ``trim_offset_s`` is how many seconds of leading silence were cut;
    timestamped consumers must add it back (word timings are relative
    to the passed audio).
    """
    if not isinstance(audio, np.ndarray):
        # Defensive: some callers pass array-likes (lists). Normalize
        # once here so ``.size``/slicing below never crash; float32
        # matches the recorder's native dtype (no precision change).
        audio = np.asarray(audio, dtype=np.float32)
    if not vad_enabled:
        return audio, False, 0.0
    n = int(audio.size)
    if n == 0:
        return audio, True, 0.0
    duration = n / float(sample_rate)
    if duration < SHORT_MAX_S or duration > LONG_MIN_S:
        # Short: trimming risks words for ~zero gain. Long: the engine
        # needs its filter for segmentation. Both keep today behavior.
        return audio, True, 0.0
    if audio_stats is not None:
        rms, _peak, silence_pct = audio_stats
    else:
        rms, _peak, silence_pct = _compute_stats(audio)
    if silence_pct > SILENCE_PCT_MAX or rms < RMS_FLOOR:
        return audio, True, 0.0
    trimmed, leading_cut = trim_edge_silence(audio, sample_rate)
    if leading_cut == 0 and trimmed.shape == audio.shape:
        # Already clean edges — nothing for either trimmer to do, so
        # skip the engine scan too.
        return audio, False, 0.0
    return np.ascontiguousarray(trimmed), False, leading_cut / float(sample_rate)
