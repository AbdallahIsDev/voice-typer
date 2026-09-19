"""Regression tests for XZ-PRIV-04: per-segment DEBUG log PII gating."""

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
    """Build a TranscriptionEngine with a mocked whisper model."""
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
        """When ``config.log_transcriptions`` is False (the default), the"""
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
            f"Segment DEBUG log emitted despite log_transcriptions=False, got: {[r.getMessage() for r in segment_logs]}"
        )

    def test_segment_debug_log_emitted_when_log_transcriptions_true(self, caplog):
        """When the user opts in via ``log_transcriptions=True``, the"""
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
        # PII must be redacted, the raw email and phone must NOT appear.
        assert "user@example.com" not in msg, f"Raw email leaked into DEBUG log: {msg!r}"
        assert "+1 (415) 555-2671" not in msg, f"Raw phone leaked into DEBUG log: {msg!r}"
        # Redaction tokens SHOULD appear (proves redact_pii ran).
        assert "[EMAIL]" in msg, f"Email not redacted to [EMAIL] token: {msg!r}"
        assert "[PHONE]" in msg, f"Phone not redacted to [PHONE] token: {msg!r}"

    def test_segment_debug_log_skipped_when_config_is_none(self, caplog):
        """When ``engine.config`` is None (e.g. benchmark path), the"""
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
            f"Segment DEBUG log emitted despite config=None, got: {[r.getMessage() for r in segment_logs]}"
        )

    def test_transcription_result_unchanged_by_gating(self):
        """The gating fix must NOT alter the transcription result, only"""
        engine, mock_model = _make_engine_with_model(config=_FakeConfig(log_transcriptions=False))
        mock_model.transcribe.return_value = (
            [MagicMock(text=PII_SAMPLE_TEXT, start=0.0, end=1.0)],
            MagicMock(language="en", language_probability=1.0),
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        result = engine.transcribe_with_fallback(audio)

        # The returned text is the user's dictated speech, PII is
        assert result == PII_SAMPLE_TEXT


class TestResultModuleRedactionFailureFallback:
    """HU-13 / AP-11 twin: ``transcription_result.transcribe_unlocked``"""

    @staticmethod
    def _make_engine(config):
        import threading
        import types
        from unittest.mock import MagicMock

        seg = types.SimpleNamespace(
            start=0.0,
            end=1.0,
            text=PII_SAMPLE_TEXT,
            avg_logprob=-0.5,
            no_speech_prob=0.01,
        )
        info = types.SimpleNamespace(language="en", language_probability=1.0)
        engine = types.SimpleNamespace(
            _model=MagicMock(),
            config=config,
            beam_size=1,
            best_of=1,
            language="en",
            condition_on_previous_text=False,
            _abort_event=threading.Event(),
        )
        engine._model.transcribe.return_value = (iter([seg]), info)
        return engine

    def test_redaction_failure_emits_marker_not_raw_text(self, caplog, monkeypatch):
        """HU-13: when ``redact_pii`` raises (regex bug / security-module"""
        import voice_typer.server.security as security_mod
        from voice_typer.server.transcription_result import transcribe_unlocked

        engine = self._make_engine(config=_FakeConfig(log_transcriptions=True))

        def _boom(_text: str) -> str:
            raise RuntimeError("redaction engine exploded")

        monkeypatch.setattr(security_mod, "redact_pii", _boom)
        monkeypatch.setattr(
            "voice_typer.server.transcription_result.should_reject_low_audio_hallucination",
            lambda *args, **kwargs: False,
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            result = transcribe_unlocked(engine, audio)

        # The transcription result must be unaffected by the log fix.
        assert result == PII_SAMPLE_TEXT
        # The raw dictated text must not appear anywhere in the log.
        assert PII_SAMPLE_TEXT not in caplog.text, (
            f"HU-13: raw dictated text leaked into the log on redaction failure: {caplog.text!r}"
        )
        # The fail-closed marker must be present (warning level).
        assert "redaction-engine-failed" in caplog.text, (
            "HU-13: expected <redaction-engine-failed> marker warning on redact_pii failure"
        )
        # NO DEBUG segment log may be emitted with a truncated fallback copy.
        segment_debug = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "[TRANSCRIBE] Segment" in r.getMessage()
        ]
        assert segment_debug == [], (
            f"HU-13: segment DEBUG log must be skipped on redaction failure; "
            f"got {[r.getMessage() for r in segment_debug]}"
        )

    def test_redaction_success_still_logs_redacted_segment(self, caplog):
        """HU-13 guard against over-fixing: when ``redact_pii`` works,"""
        from voice_typer.server.transcription_result import transcribe_unlocked

        engine = self._make_engine(config=_FakeConfig(log_transcriptions=True))

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            transcribe_unlocked(engine, audio)

        segment_logs = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "[TRANSCRIBE] Segment" in r.getMessage()
        ]
        assert len(segment_logs) == 1, (
            f"Expected exactly one segment DEBUG log when log_transcriptions=True; got {len(segment_logs)}"
        )
        msg = segment_logs[0].getMessage()
        assert "user@example.com" not in msg, f"Raw email leaked into DEBUG log: {msg!r}"
        assert "[EMAIL]" in msg, f"Email not redacted to [EMAIL] token: {msg!r}"

    def test_redaction_import_failure_skips_segment_log_entirely(self, caplog, monkeypatch):
        """HU-13: when the redaction engine cannot even be IMPORTED"""
        import voice_typer.server.security as security_mod
        from voice_typer.server.transcription_result import transcribe_unlocked

        engine = self._make_engine(config=_FakeConfig(log_transcriptions=True))

        # Deleting the package attribute makes the hoisted
        monkeypatch.delattr(security_mod, "redact_pii")
        monkeypatch.setattr(
            "voice_typer.server.transcription_result.should_reject_low_audio_hallucination",
            lambda *args, **kwargs: False,
        )

        audio = np.full(16000 * 1, 0.05, dtype=np.float32)
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            result = transcribe_unlocked(engine, audio)

        assert result == PII_SAMPLE_TEXT
        assert PII_SAMPLE_TEXT not in caplog.text
        segment_logs = [
            r for r in caplog.records if r.levelno == logging.DEBUG and "[TRANSCRIBE] Segment" in r.getMessage()
        ]
        assert segment_logs == [], (
            f"HU-13: segment DEBUG log must not emit when redaction engine is unavailable; "
            f"got {[r.getMessage() for r in segment_logs]}"
        )
