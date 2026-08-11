"""Tests for ``voice_typer.server.hallucination``.

Covers:
  - SEC-009 / CR-87: ``log_hallucination_rejection`` uses the
    ``security.redact_pii`` helper (not a synthetic ``LogRecord`` +
    ``PIIRedactionFilter`` dance) and applies identical redaction
    patterns for emails, phone numbers, SSNs, and credit-card-like
    numbers.
  - SEC-009: ``log_transcriptions=False`` emits metadata only — never
    the rejected text — and ``log_transcriptions=True`` emits the
    redacted + truncated text.
  - CR-87: truncation to ``_HALLUCINATION_LOG_MAX_CHARS`` (40) still
    happens after redaction, so a PII pattern that straddles the
    truncation boundary is fully redacted before truncation (no partial
    PII leak).
  - CR-87 / HU-14: when ``redact_pii`` raises at runtime (defensive
    import + call site), the log falls back to a constant
    ``<redaction-failed>`` sentinel + the char count — NEVER the raw
    (even truncated) text, mirroring the transcription.py segment-log
    contract.
  - The hallucination detection helpers
    (``should_reject_low_audio_hallucination``,
    ``normalize_hallucination_key``, ``KNOWN_LOW_AUDIO_HALLUCINATIONS``)
    are smoke-tested so this file is a faithful single-module test
    suite (no cross-module imports beyond ``hallucination`` /
    ``security``).
"""

import logging
from unittest.mock import patch

import pytest
from voice_typer.server.hallucination import (
    _HALLUCINATION_LOG_MAX_CHARS,
    KNOWN_LOW_AUDIO_HALLUCINATIONS,
    log_hallucination_rejection,
    normalize_hallucination_key,
    should_reject_low_audio_hallucination,
)
from voice_typer.server.security import redact_pii as _redact_pii

LOGGER_NAME = "voice_typer.server.hallucination"


# SEC-009 / : PII redaction parity with PIIRedactionFilter ────────


@pytest.mark.parametrize(
    "text, marker",
    [
        ("user@example.com emailed me", "[EMAIL]"),
        ("call 555-123-4567 now", "[PHONE]"),
        ("SSN: 123-45-6789 here", "[SSN]"),
        ("CC: 4111111111111111 charged", "[CC]"),
    ],
    ids=["email", "phone", "ssn", "credit-card"],
)
def test_cr87_log_transcriptions_true_applies_same_redaction_as_filter(caplog, text, marker):
    """CR-87: when ``log_transcriptions=True``, the rejected text is
    redacted using ``security.redact_pii`` — the same patterns as
    ``PIIRedactionFilter``. Each PII category must be replaced with its
    redaction token before the text reaches the log record.
    """
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection("[TEST]", text, reason="hallucination", log_transcriptions=True)
    # The redaction token must appear in the log
    assert marker in caplog.text, f"expected redaction token {marker!r} in log for input {text!r}; got: {caplog.text!r}"
    # The raw PII must NOT appear in the log
    # (Strip the redaction marker from the input before checking —
    # e.g. the literal string "[EMAIL]" could appear in both.)
    raw_pii = text.replace(marker, "")
    for pii_fragment in ["user@example.com", "555-123-4567", "123-45-6789", "4111111111111111"]:
        if pii_fragment in raw_pii:
            assert pii_fragment not in caplog.text, (
                f"raw PII {pii_fragment!r} must not appear in log; got: {caplog.text!r}"
            )


def test_cr87_uses_redact_pii_helper_not_logrecord(caplog):
    """CR-87: the implementation must call ``security.redact_pii`` and
    must NOT construct a ``logging.LogRecord`` + ``PIIRedactionFilter``
    instance. Verified by patching ``redact_pii`` and asserting the
    patch is invoked; the old code path (LogRecord + filter) would not
    call ``redact_pii``.
    """
    with (
        patch("voice_typer.server.security.redact_pii", return_value="[REDACTED]") as mock_redact,
        caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
    ):
        log_hallucination_rejection(
            "[TEST]",
            "user@example.com",
            reason="hallucination",
            log_transcriptions=True,
        )
    assert mock_redact.called, (
        "redact_pii helper must be called (CR-87); the LogRecord + PIIRedactionFilter construction path was removed"
    )
    # The patched return value must appear in the log
    assert "[REDACTED]" in caplog.text


def test_cr87_truncation_to_40_chars_after_redaction(caplog):
    """CR-87: the logged text is truncated to
    ``_HALLUCINATION_LOG_MAX_CHARS`` (40) AFTER redaction. The redaction
    tokens themselves are short (≤7 chars), so a long input with no PII
    is cut to 40 chars.

    The input must be a realistic long transcription-style phrase (with
    spaces) rather than a long run of a single alphanumeric char. A
    200-char run of ``"a"`` would match ``redact_pii``'s 20+ char bare-
    token secret pattern (``_KEY_PATTERNS[-1]`` in
    ``voice_typer.server._secrets``) and be redacted wholesale to
    ``"***"``, which would defeat the test's purpose of verifying
    truncation on no-PII text. Spaces break up the run so the bare-token
    pattern doesn't fire and the text reaches the truncation step intact.
    """
    # 225 chars (45-char phrase × 5) — no PII, no 20+ char bare-token run.
    long_text = "the quick brown fox jumps over the lazy dog. " * 5
    expected_truncated = long_text[:_HALLUCINATION_LOG_MAX_CHARS]
    # Sanity check the test input itself: redact_pii must be a no-op here
    # (otherwise the test is asserting truncation on text that doesn't
    # survive redaction, which was the original bug).
    assert _redact_pii(long_text) == long_text, (
        "test input should survive redact_pii unchanged; if this fails the "
        "test input accidentally matches a PII/secret pattern"
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection("[TEST]", long_text, reason="hallucination", log_transcriptions=True)
    # Find the WARNING record (avoid picking up other log lines)
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "expected a WARNING log record"
    # The formatted message should contain the truncated text
    msg = warning_records[-1].getMessage()
    # The input has no PII, so redact_pii returns it unchanged.
    # After truncation, the first 40 chars should appear verbatim.
    assert expected_truncated in msg, (
        f"expected first {_HALLUCINATION_LOG_MAX_CHARS} chars of long_text "
        f"(truncated from {len(long_text)}) in log message; got: {msg!r}"
    )
    # Chars beyond the truncation limit must NOT appear (truncation happened).
    # Check a slice just past the boundary so the assertion isn't trivially
    # satisfied by a single missing char.
    overflow_slice = long_text[_HALLUCINATION_LOG_MAX_CHARS : _HALLUCINATION_LOG_MAX_CHARS + 10]
    assert overflow_slice not in msg, (
        f"truncation to {_HALLUCINATION_LOG_MAX_CHARS} failed; got text "
        f"beyond the limit ({overflow_slice!r}) in: {msg!r}"
    )


def test_cr87_pii_at_truncation_boundary_is_fully_redacted(caplog):
    """CR-87 regression guard: if a PII pattern straddles the 40-char
    truncation boundary, redacting FIRST then truncating must replace
    the entire PII with the redaction token — no partial PII leak.

    Pre-CR-87 (truncate-then-redact): the email would be split at char
    40, the regex wouldn't match the partial fragment, and the partial
    email prefix would be logged verbatim.
    """
    # Position the email so that char 40 falls in the middle of it.
    # 30 chars of padding + "user@example.com" (16 chars) = 46 chars.
    # Under truncate-first: chars 0-39 keep "user@example.com"[:10] =
    # "user@examp" — leaking the email username. Under redact-first:
    # the full email becomes "[EMAIL]" (7 chars), then truncation
    # keeps 40 chars total — no leak.
    text = "x" * 30 + "user@example.com"
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection("[TEST]", text, reason="hallucination", log_transcriptions=True)
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records
    msg = warning_records[-1].getMessage()
    # The full email must NOT appear in the log
    assert "user@example.com" not in msg, f"PII must be fully redacted before truncation; got: {msg!r}"
    # The redaction token MUST appear (proving redaction ran)
    assert "[EMAIL]" in msg, f"redaction token [EMAIL] must appear for input email; got: {msg!r}"


def test_cr87_fallback_to_sentinel_if_redact_pii_raises(caplog):
    """CR-87 / HU-14: if ``security.redact_pii`` raises at runtime
    (e.g. import failure, regex engine error), the helper logs a
    constant ``<redaction-failed>`` sentinel + the char count — NEVER
    the raw text, even truncated.

    HU-14 regression: the pre-fix fallback was truncation-only
    (``text[:_HALLUCINATION_LOG_MAX_CHARS]``). A 40-char window can
    still contain a full email address, phone number, or SSN fragment,
    so truncation is NOT redaction — the fallback must not leak PII
    when ``log_transcriptions=True`` and the redaction engine is
    broken.
    """
    # Patch redact_pii to raise — simulates a broken security module.
    secret_text = "user@example.com with secret content"
    with (
        patch(
            "voice_typer.server.security.redact_pii",
            side_effect=RuntimeError("simulated redact_pii failure"),
        ),
        caplog.at_level(logging.WARNING, logger=LOGGER_NAME),
    ):
        # Should NOT raise
        log_hallucination_rejection(
            "[TEST]",
            secret_text,
            reason="hallucination",
            log_transcriptions=True,
        )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records
    msg = warning_records[-1].getMessage()
    # The sentinel must appear in the log.
    assert "<redaction-failed>" in msg, f"redaction-failure sentinel must be logged; got: {msg!r}"
    # The raw text must NOT appear — not even a truncated fragment.
    assert "user@example.com" not in msg, f"raw PII must NOT be logged on redaction failure; got: {msg!r}"
    assert "secret content" not in msg, f"raw text must NOT be logged on redaction failure; got: {msg!r}"
    # The char count is still surfaced for triage.
    assert str(len(secret_text)) in msg, f"char count must still be logged; got: {msg!r}"


# ─── SEC-009: gating behavior (log_transcriptions flag) ───────────────────


def test_log_transcriptions_false_emits_no_text(caplog):
    """SEC-009: with ``log_transcriptions=False`` (default), only
    metadata (engine tag, reason, char count) is logged — never the
    rejected text content."""
    secret_text = "this is secret content that must not appear"
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection("[TEST]", secret_text, reason="hallucination", log_transcriptions=False)
    assert "secret content" not in caplog.text, (
        f"text content must not appear in log when log_transcriptions=False; got: {caplog.text!r}"
    )
    # Metadata must appear
    assert "Rejected likely hallucination" in caplog.text
    assert "chars" in caplog.text
    assert str(len(secret_text)) in caplog.text


def test_log_transcriptions_false_default_argument(caplog):
    """SEC-009: ``log_transcriptions`` defaults to False, so callers
    that don't pass it get the privacy-safe behavior."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection("[TEST]", "secret default-path text")
    assert "secret default-path text" not in caplog.text
    assert "chars" in caplog.text


def test_log_transcriptions_true_emits_redacted_text(caplog):
    """SEC-009: with ``log_transcriptions=True``, the (redacted,
    truncated) text is logged so the user can debug rejections."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection(
            "[TEST]",
            "thanks for watching",
            reason="hallucination",
            log_transcriptions=True,
        )
    assert "Rejected likely hallucination" in caplog.text
    # The non-PII text should appear (it's 19 chars, under the limit)
    assert "thanks for watching" in caplog.text


def test_engine_tag_and_reason_appear_in_log(caplog):
    """Custom engine tags and reasons are surfaced in the log line."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        log_hallucination_rejection(
            "[PARAKEET]",
            "thanks for watching",
            reason="non-English",
            log_transcriptions=False,
        )
    assert "[PARAKEET]" in caplog.text
    assert "non-English" in caplog.text


def test_truncation_limit_constant():
    """SEC-009: the truncation limit is 40 chars (down from a prior
    80-char limit). Lock the constant so a future bump is a deliberate
    API change."""
    assert _HALLUCINATION_LOG_MAX_CHARS == 40


# ─── hallucination detection helpers (smoke tests) ────────────────────────


def test_normalize_hallucination_key_strips_punctuation_and_lowercases():
    """``normalize_hallucination_key`` produces a canonical key for
    lookup so "Thanks for watching!" and "thanks for watching" match."""
    assert normalize_hallucination_key("Thanks for watching!") == "thanks for watching"
    assert normalize_hallucination_key("BYE.") == "bye"
    assert normalize_hallucination_key("  Subscribe  ") == "subscribe"


def test_known_hallucinations_are_normalized_keys():
    """Every entry in ``KNOWN_LOW_AUDIO_HALLUCINATIONS`` must be in the
    canonical normalized form so direct key lookup works."""
    for entry in KNOWN_LOW_AUDIO_HALLUCINATIONS:
        assert normalize_hallucination_key(entry) == entry, (
            f"KNOWN_LOW_AUDIO_HALLUCINATIONS entry {entry!r} is not in normalized form"
        )


def test_should_reject_low_audio_hallucination_empty_text():
    """Empty/None text is never rejected."""
    assert should_reject_low_audio_hallucination("", rms=0.0) is False


def test_should_reject_low_audio_hallucination_unknown_phrase():
    """Unknown phrases (not in the known set) are never rejected,
    even at zero RMS."""
    assert should_reject_low_audio_hallucination("the quick brown fox", rms=0.0001) is False


def test_should_reject_low_audio_hallucination_simple_tier():
    """Tier 1: known phrase + very low RMS (<0.001) + no silence info
    → rejected."""
    assert should_reject_low_audio_hallucination("thanks for watching", rms=0.0005) is True


def test_should_reject_low_audio_hallucination_simple_tier_with_silence_pct():
    """Tier 1: known phrase + very low RMS + high silence_pct → rejected."""
    assert should_reject_low_audio_hallucination("bye", rms=0.0005, silence_pct=95.0) is True


def test_should_reject_low_audio_hallucination_simple_tier_low_silence_pct():
    """Tier 1: known phrase + very low RMS but LOW silence_pct → NOT
    rejected (silence info must corroborate the low-RMS signal)."""
    assert should_reject_low_audio_hallucination("bye", rms=0.0005, silence_pct=10.0) is False


def test_should_reject_low_audio_hallucination_extended_tier_rejects():
    """Tier 2: long audio + low RMS + high silence + short segment span
    → rejected."""
    assert (
        should_reject_low_audio_hallucination(
            "thanks for watching",
            rms=0.002,
            silence_pct=60.0,
            duration=45.0,
            first_segment_start=1.0,
            last_segment_end=3.0,
        )
        is True
    )


def test_should_reject_low_audio_hallucination_extended_tier_no_reject_short_audio():
    """Tier 2: short audio (<30s) doesn't trigger the extended check
    even with all other conditions met."""
    assert (
        should_reject_low_audio_hallucination(
            "thanks for watching",
            rms=0.002,
            silence_pct=60.0,
            duration=20.0,  # < 30s threshold
            first_segment_start=1.0,
            last_segment_end=3.0,
        )
        is False
    )


def test_should_reject_low_audio_hallucination_extended_tier_no_reject_long_span():
    """Tier 2: long segment span (>5s) means real audio content, not a
    hallucination → not rejected."""
    assert (
        should_reject_low_audio_hallucination(
            "thanks for watching",
            rms=0.002,
            silence_pct=60.0,
            duration=45.0,
            first_segment_start=1.0,
            last_segment_end=10.0,  # span = 9s > 5s
        )
        is False
    )
