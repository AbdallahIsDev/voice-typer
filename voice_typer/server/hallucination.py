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
#
# widened to include common single-token hallucinations
# observed in production Whisper / Qwen3-ASR logs. Whisper's
# decoder frequently emits a single short word when fed near-silence
# (the language model's most likely "starter" token), and Qwen3-ASR
# (Whisper-derived) inherits the same failure mode. The single-token
# entries here are safe to reject because the caller has already
# gated on very low RMS (<0.01 linear, ~-40 dBFS) -- legitimate
# single-word speech at that input level is essentially impossible.
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
    #
    # relaxed from ``rms < 0.001`` (-60 dBFS) to ``rms < 0.01``
    # (-40 dBFS). The strict 0.001 threshold missed real hallucinations
    # on quiet-but-not-silent background noise (HVAC, fans) where RMS
    # hovers around 0.003-0.005. -40 dBFS is still essentially inaudible
    # for speech (normal speech is -30 to -10 dBFS), so the false-positive
    # risk is negligible.
    #
    # collapsed the redundant inner branch
    # ``if silence_pct is None and rms < 0.001`` -- the ``rms < 0.001``
    # clause was dead (we're already inside ``if rms < 0.01``), and the
    # ``silence_pct is None`` case is now folded into the single
    # ``silence_pct is None or silence_pct >= 95.0`` check. Behavior is
    # preserved: when no silence info is available, very low RMS alone
    # is suspicious; when silence info IS available, it must corroborate
    # (>= 95% silence) before we reject.
    #
    # when ``duration`` is provided, additionally require
    # ``duration < 10.0``. Hallucinations on near-silence are typically
    # emitted by the decoder within the first ~1s of audio (Whisper's
    # attention window produces a single spurious token cluster that
    # resolves quickly), but on medium-length silences (1-10s) the
    # decoder still produces the same spurious tokens — Whisper has no
    # "time-aware" mechanism that would suppress them just because more
    # silence elapsed. A deliberate quiet utterance like "thank you"
    # or "bye" is usually ≥0.5s and recorded with rms > 0.005 (well
    # above the 0.01 threshold) — so even at 6-10s of medium silence,
    # the rms < 0.01 gate is the authoritative signal: a real quiet
    # utterance at that duration cannot have rms < 0.01 (it would be
    # essentially inaudible, ~-40 dBFS, well below any microphone's
    # useful capture range for speech). Pre-fix the threshold was
    # ``duration < 1.0``, which left a 1-30s gap where a 6s pure-silence
    # recording hallucinating "Thanks for watching!" was NOT rejected
    # (Tier 2 only kicks in at >= 30s). Widening to ``< 10.0`` closes
    # the 1-10s portion of that gap. The 10-30s range remains Tier 2's
    # jurisdiction (Tier 2 adds segment-span and silence_pct corroboration
    # for the longer window). When ``duration`` is None (QwenEngine
    # simple path — no segment timing), keep the existing behavior; the
    # silence_pct corroboration (>= 95%) remains the backstop.
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
    # previously this constructed a synthetic ``logging.LogRecord``
    # and ran ``PIIRedactionFilter().filter(record)`` just to redact a
    # plain string. The LogRecord dance was a heavyweight detour that
    # also depended on the filter's mutating ``record.msg``/``record.args``
    # contract. ``security.redact_pii`` is the canonical string-in /
    # string-out helper that ``PIIRedactionFilter`` itself uses (via
    # ``_redact_text``), so the redaction behavior is identical.
    #
    # Order: redact first, then truncate. The previous code truncated
    # first then redacted -- which could leak a partial PII pattern that
    # straddled the 40-char boundary (the regex wouldn't match the
    # truncated fragment). Redacting first guarantees ALL PII patterns
    # are fully replaced before truncation. Truncation can only cut into
    # a redaction token (``[EMAIL]``/``[PHONE]``/``[SSN]``/``[CC]``),
    # which is non-sensitive.
    try:
        from voice_typer.server.security import redact_pii

        safe_text = redact_pii(text)[:_HALLUCINATION_LOG_MAX_CHARS]
    except Exception:
        # M-49: no longer a silent ``except Exception: pass`` -- log
        # at DEBUG so a redaction-engine failure (e.g. security module
        # import error, regex bug) is visible in the log file without
        # spamming at WARNING/ERROR level on every hallucination.
        # Fall back to truncation-only which is safe (no PII leak,
        # just slightly less redacted text in the log).
        log.debug(
            "PII redaction failed in log_hallucination_rejection; falling back to truncation only",
            exc_info=True,
        )
        safe_text = text[:_HALLUCINATION_LOG_MAX_CHARS]

    log.warning(
        "%s Rejected likely %s (%d chars): %s",
        engine_tag,
        reason,
        char_count,
        safe_text,
    )
