"""Shared hallucination detection for ASR transcription results.

Extracts the duplicated hallucination detection logic from both
transcription.py and qwen_engine.py into a single module so that
both engines use identical rejection criteria.
"""

import re
from typing import Optional

# Known phrases that Whisper emits on near-silence audio
KNOWN_LOW_AUDIO_HALLUCINATIONS = {
    "thanks for watching",
    "thank you for watching",
    "see you next time",
    "bye",
    "thank you",
    "subscribe",
    "like and subscribe",
    "please subscribe",
    "thanks for listening",
    "thank you for listening",
}


def normalize_hallucination_key(text: str) -> str:
    """Normalize text for hallucination key lookup.

    Strips all non-alphanumeric characters except spaces, lowercases,
    and trims whitespace so that variations like "Thanks for watching!"
    and "thanks for watching" match the same key.
    """
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def should_reject_low_audio_hallucination(
    text: str,
    rms: float,
    *,
    peak: Optional[float] = None,
    silence_pct: Optional[float] = None,
    duration: Optional[float] = None,
    first_segment_start: Optional[float] = None,
    last_segment_end: Optional[float] = None,
) -> bool:
    """Return True if the transcription is likely a hallucination from near-silence.

    Uses a two-tier check:
    1. **Simple** (used by QwenEngine): known phrase + very low RMS.
    2. **Extended** (used by TranscriptionEngine): known phrase + low RMS
       + high silence percentage + short segment span on long audio.

    Callers that only have ``text`` and ``rms`` can use the simple check
    by omitting the optional keyword arguments.
    """
    if not text:
        return False

    key = normalize_hallucination_key(text)
    if key not in KNOWN_LOW_AUDIO_HALLUCINATIONS:
        return False

    # Tier 1: simple check (always available)
    if rms < 0.001:
        if silence_pct is not None and silence_pct > 90.0:
            return True
        # Very low RMS without silence info — still suspicious for known hallucination
        if silence_pct is None and rms < 0.001:
            return True

    # Tier 2: extended check (requires segment timing info)
    if (
        duration is not None
        and duration >= 30.0
        and rms < 0.005
        and silence_pct is not None
        and silence_pct >= 50.0
        and first_segment_start is not None
        and first_segment_start <= 3.0
        and last_segment_end is not None
    ):
        segment_span = max(0.0, last_segment_end - first_segment_start)
        if segment_span <= 5.0:
            return True

    return False
