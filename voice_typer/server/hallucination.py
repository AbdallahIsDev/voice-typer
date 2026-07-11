"""Shared hallucination detection for ASR transcription results.

Extracts the duplicated hallucination detection logic from both
transcription.py and qwen_engine.py into a single module so that
both engines use identical rejection criteria.

SEC-009: Provides a safe logging helper for hallucination rejections
that gates detailed text logging behind the ``log_transcriptions``
config flag and applies PII redaction + truncation to 40 chars.
"""

import logging
import re

log = logging.getLogger(__name__)

# Maximum chars to log from hallucination text (SEC-009)
_HALLUCINATION_LOG_MAX_CHARS = 40

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
    peak: float | None = None,
    silence_pct: float | None = None,
    duration: float | None = None,
    first_segment_start: float | None = None,
    last_segment_end: float | None = None,
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


def log_hallucination_rejection(
    engine_tag: str,
    text: str,
    reason: str = "hallucination",
    *,
    log_transcriptions: bool = False,
) -> None:
    """SEC-009: Log a hallucination rejection with PII-safe output.

    When ``log_transcriptions`` is False (the default), only logs the
    character count and rejection reason — never the text content.
    When True, logs the text but applies PII redaction using the
    existing PIIRedactionFilter patterns and truncates to 40 chars
    (down from the previous 80).

    Parameters
    ----------
    engine_tag : str
        Engine identifier (e.g. "[PARAKEET]", "[QWEN]", "[TRANSCRIBE]").
    text : str
        The rejected transcription text.
    reason : str
        Short reason for the rejection (e.g. "hallucination", "non-English").
    log_transcriptions : bool
        Whether the user has enabled transcription logging.
    """
    char_count = len(text)
    if not log_transcriptions:
        # SEC-009: When logging is disabled, only log metadata — no text content
        log.warning(
            "%s Rejected likely %s (%d chars)",
            engine_tag, reason, char_count,
        )
        return

    # SEC-009: When logging is enabled, apply PII redaction and truncation
    safe_text = text[:_HALLUCINATION_LOG_MAX_CHARS]
    try:
        from voice_typer.server.security import PIIRedactionFilter
        _pii_filter = PIIRedactionFilter()
        # Create a temporary LogRecord to apply PII redaction
        record = logging.LogRecord(
            name=log.name, level=logging.WARNING, pathname="", lineno=0,
            msg=safe_text, args=(), exc_info=None,
        )
        _pii_filter.filter(record)
        safe_text = record.msg
    except Exception:
        # If PII filter fails, fall back to truncation only
        pass
    log.warning(
        "%s Rejected likely %s: %r",
        engine_tag, reason, safe_text,
    )
