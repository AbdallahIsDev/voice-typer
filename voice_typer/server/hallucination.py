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

# Known phrases that Whisper emits on near-silence audio.
KNOWN_LOW_AUDIO_HALLUCINATIONS = {
    # Multi-word phrases (original OBS / Whisper-decoding artifacts)
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
    # common single-token hallucinations
    "you",  # Whisper's #1 most-likely starter token
    "the",  # very common Whisper decoder artifact on silence
    "so",  # common filler-token hallucination
    "thanks",  # truncation of "thanks for watching"
    "music",  # Whisper hallucinates [Music] tags on noise
    "amara",  # amara.org subtitle watermark hallucination
}


def normalize_hallucination_key(text: str) -> str:
    """Normalize text for hallucination key lookup."""
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
    """Return True if the transcription is likely a hallucination from near-silence."""
    if not text:
        return False

    key = normalize_hallucination_key(text)
    if key not in KNOWN_LOW_AUDIO_HALLUCINATIONS:
        return False

    # Tier 1: simple check (always available)
    if rms < 0.01 and (silence_pct is None or silence_pct >= 95.0) and (duration is None or duration < 10.0):
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
    character count and rejection reason -- never the text content.
    When True, logs the text but applies PII redaction using the
    existing PIIRedactionFilter patterns and truncates to 40 chars
    (down from the previous 80).
    """
    char_count = len(text)
    if not log_transcriptions:
        # SEC-009: When logging is disabled, only log metadata -- no text content
        log.warning(
            "%s Rejected likely %s (%d chars)",
            engine_tag,
            reason,
            char_count,
        )
        return

    # SEC-009: When logging is enabled, apply PII redaction and truncation.
    try:
        from voice_typer.server.security import redact_pii

        safe_text = redact_pii(text)[:_HALLUCINATION_LOG_MAX_CHARS]
    except Exception:
        # M-49 / HU-14: no longer a silent ``except Exception: pass`` --
        log.debug(
            "PII redaction failed in log_hallucination_rejection; logging redacted marker only",
            exc_info=True,
        )
        safe_text = "<redaction-failed>"

    log.warning(
        "%s Rejected likely %s (%d chars): %s",
        engine_tag,
        reason,
        char_count,
        safe_text,
    )
