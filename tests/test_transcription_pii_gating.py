"""Regression tests for XZ-PRIV-04 — per-segment DEBUG log PII gating.

``voice_typer/server/transcription.py::_transcribe_unlocked`` previously
called ``log.debug("[TRANSCRIBE] Segment: [%.1fs - %.1fs] %s", start,
end, seg.text.strip())`` *unconditionally*. The raw segment text —
which is the user's dictated speech — landed in ``voice-typer.log``
even when the user had NOT opted in via ``config.log_transcriptions``.

The fix mirrors the contract already used by
``hallucination.py::log_hallucination_rejection``:
  (a) only emit the segment DEBUG log when the user has opted in via
      ``config.log_transcriptions``;
  (b) route the text through ``security.redact_pii`` so any structured
      PII patterns (email/phone/IBAN/SSN/CC) are stripped before the
      text reaches the rotating log file.

These tests pin both behaviors so a future refactor cannot silently
reintroduce the unconditional raw-text DEBUG log.
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def mock_faster_whisper(monkeypatch):
    """Mock faster_whisper so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 1
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


def _make_engine_with_model(config: object | None = None):
    """Build a TranscriptionEngine with a mocked whisper model.

    ``config`` is wired through to ``engine.config`` so tests can flip
    the ``log_transcriptions`` flag.
    """
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cuda", config=config)
    engine._device = "cuda"
    engine._compute_type = "float16"
    mock_model = MagicMock()
    engine._model = mock_model
    return engine, mock_model


class _FakeConfig:
    """Minimal Config stub with a settable ``log_transcriptions`` flag."""

    def __init__(self, *, log_transcriptions: bool = False) -> None:
        self.log_transcriptions = log_transcriptions
        # Required by downstream code paths that probe for consent.
        self.huggingface_consent = True


PII_SAMPLE_TEXT = "Call me at user@example.com or +1 (415) 555-2671"


class TestSegmentDebugLogPiiGating:
    """XZ-PRIV-04: segment DEBUG log must be gated + PII-redacted."""

    def test_segment_debug_log_not_emitted_when_log_transcriptions_false(self, caplog):
        """When ``config.log_transcriptions`` is False (the default), the
        raw segment text MUST NOT appear in the DEBUG log."""
        engine, mock_model = _make_engine_with_model(config=_FakeConfig(log_transcriptions=False))
        mock_model.transcribe.return_value = (
            [MagicMock(text=PII_SAMPLE_TEXT, start=0.0, end=1.0)],
            MagicMock(language="en", language_probability=1.0),
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            engine.transcribe_with_fallback(audio)

        # The raw segment text must not appear in any DEBUG record.
        segment_logs = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "[TRANSCRIBE] Segment" in r.getMessage()
        ]
        assert segment_logs == [], (
            "Segment DEBUG log emitted despite log_transcriptions=False — "
            f"got: {[r.getMessage() for r in segment_logs]}"
        )

    def test_segment_debug_log_emitted_when_log_transcriptions_true(self, caplog):
        """When the user opts in via ``log_transcriptions=True``, the
        segment DEBUG log IS emitted — but with PII redacted."""
        engine, mock_model = _make_engine_with_model(config=_FakeConfig(log_transcriptions=True))
        mock_model.transcribe.return_value = (
            [MagicMock(text=PII_SAMPLE_TEXT, start=0.0, end=1.0)],
            MagicMock(language="en", language_probability=1.0),
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            engine.transcribe_with_fallback(audio)

        segment_logs = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "[TRANSCRIBE] Segment" in r.getMessage()
        ]
        assert len(segment_logs) == 1, (
            f"Expected exactly one segment DEBUG log when log_transcriptions=True; got {len(segment_logs)}"
        )
        msg = segment_logs[0].getMessage()
        # PII must be redacted — the raw email and phone must NOT appear.
        assert "user@example.com" not in msg, f"Raw email leaked into DEBUG log: {msg!r}"
        assert "+1 (415) 555-2671" not in msg, f"Raw phone leaked into DEBUG log: {msg!r}"
        # Redaction tokens SHOULD appear (proves redact_pii ran).
        assert "[EMAIL]" in msg, f"Email not redacted to [EMAIL] token: {msg!r}"
        assert "[PHONE]" in msg, f"Phone not redacted to [PHONE] token: {msg!r}"

    def test_segment_debug_log_skipped_when_config_is_none(self, caplog):
        """When ``engine.config`` is None (e.g. benchmark path), the
        segment DEBUG log MUST NOT emit — same as ``log_transcriptions=False``."""
        engine, mock_model = _make_engine_with_model(config=None)
        mock_model.transcribe.return_value = (
            [MagicMock(text=PII_SAMPLE_TEXT, start=0.0, end=1.0)],
            MagicMock(language="en", language_probability=1.0),
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            engine.transcribe_with_fallback(audio)

        segment_logs = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "[TRANSCRIBE] Segment" in r.getMessage()
        ]
        assert segment_logs == [], (
            f"Segment DEBUG log emitted despite config=None — got: {[r.getMessage() for r in segment_logs]}"
        )

    def test_transcription_result_unchanged_by_gating(self):
        """The gating fix must NOT alter the transcription result — only
        the log output. The returned text must still contain the original
        (un-redacted) PII so the user's dictated text is preserved."""
        engine, mock_model = _make_engine_with_model(config=_FakeConfig(log_transcriptions=False))
        mock_model.transcribe.return_value = (
            [MagicMock(text=PII_SAMPLE_TEXT, start=0.0, end=1.0)],
            MagicMock(language="en", language_probability=1.0),
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        result = engine.transcribe_with_fallback(audio)

        # The returned text is the user's dictated speech — PII is
        # preserved in the result, only the LOG is redacted.
        assert result == PII_SAMPLE_TEXT
