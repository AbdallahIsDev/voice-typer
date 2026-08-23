"""Tests for the dictation confidence (quality summary) surface.

Covers the three seams of the compact quality summary that reaches the
renderer's last-text preview via the ``transcription_final`` push event:

1. :func:`voice_typer.server.transcription.build_quality_summary` — the
   pure helper that folds the per-segment ``avg_logprob`` /
   ``no_speech_prob`` stats into a small float dict.
2. :meth:`TranscriptionEngine._transcribe_unlocked` — populates
   ``last_quality_summary`` from the stats it already collects and
   resets it per run so a stale summary can't leak.
3. The dictation pipeline — captures the summary off the active engine
   after the transcribe call and attaches it to the
   ``transcription_final`` event payload (omitting it when absent).

All heavy imports (faster_whisper / ctranslate2 / torch) are mocked so
the tests run headless on any platform.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    """Mock faster_whisper + ctranslate2 so no real model is needed."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 0
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


# ── build_quality_summary ────────────────────────────────────────────────


class TestBuildQualitySummary:
    def test_computes_mean_min_max_and_segment_count(self):
        from voice_typer.server.transcription import build_quality_summary

        summary = build_quality_summary(
            [-0.4, -0.8, -0.6],
            [0.02, 0.5, 0.1],
        )
        assert summary is not None
        assert summary["mean_logprob"] == pytest.approx(-0.6)
        assert summary["min_logprob"] == pytest.approx(-0.8)
        assert summary["no_speech_prob_max"] == pytest.approx(0.5)
        assert summary["segments"] == 3

    def test_returns_none_when_no_numeric_stats(self):
        from voice_typer.server.transcription import build_quality_summary

        assert build_quality_summary([], []) is None

    def test_partial_stats_produce_partial_dict(self):
        """An engine reporting only logprobs (no no_speech_prob) must not
        fabricate a zero no_speech_prob_max."""
        from voice_typer.server.transcription import build_quality_summary

        only_logprob = build_quality_summary([-0.3], [])
        assert only_logprob == {"mean_logprob": -0.3, "min_logprob": -0.3, "segments": 1}

        only_no_speech = build_quality_summary([], [0.7])
        assert only_no_speech == {"no_speech_prob_max": 0.7}


# ── TranscriptionEngine.last_quality_summary ─────────────────────────────


def _make_loaded_engine(config=None):
    """Build a TranscriptionEngine with a mocked loaded model."""
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="tiny.en", device="cpu", config=config)
    engine._model = MagicMock()
    return engine


def _segment(text="hello world", avg_logprob=-0.5, no_speech_prob=0.1):
    seg = MagicMock()
    seg.text = text
    seg.start = 0
    seg.end = 100
    seg.avg_logprob = avg_logprob
    seg.no_speech_prob = no_speech_prob
    return seg


class TestEngineQualitySummary:
    def test_transcribe_populates_quality_summary(self, monkeypatch):
        engine = _make_loaded_engine()
        info = MagicMock()
        info.language = "en"
        info.language_probability = 0.9
        engine._model.transcribe.return_value = (
            [_segment("hello there", avg_logprob=-0.4, no_speech_prob=0.05)],
            info,
        )
        # Focus: the summary, not the hallucination gate.
        monkeypatch.setattr(
            engine,
            "_should_reject_low_audio_hallucination",
            lambda **kwargs: False,
        )

        audio = np.zeros(16000, dtype=np.float32)
        result = engine._transcribe_unlocked(audio)

        assert result == "hello there"
        summary = engine.last_quality_summary
        assert summary is not None
        assert summary["mean_logprob"] == pytest.approx(-0.4)
        assert summary["min_logprob"] == pytest.approx(-0.4)
        assert summary["no_speech_prob_max"] == pytest.approx(0.05)
        assert summary["segments"] == 1

    def test_summary_reset_between_runs(self, monkeypatch):
        """A second transcription with NO numeric stats must not leak the
        first run's summary (stale-confidence bug guard)."""
        engine = _make_loaded_engine()
        info = MagicMock()
        info.language = "en"
        info.language_probability = 0.9

        engine._model.transcribe.return_value = (
            [_segment("first run", avg_logprob=-0.2)],
            info,
        )
        monkeypatch.setattr(
            engine,
            "_should_reject_low_audio_hallucination",
            lambda **kwargs: False,
        )
        engine._transcribe_unlocked(np.zeros(16000, dtype=np.float32))
        assert engine.last_quality_summary is not None

        # Second run: segments carry no numeric probs (e.g. an abort cut
        # the loop short before any numeric segment arrived).
        empty_seg = MagicMock()
        empty_seg.text = ""
        empty_seg.start = 0
        empty_seg.end = 10
        empty_seg.avg_logprob = None
        empty_seg.no_speech_prob = None
        engine._model.transcribe.return_value = ([empty_seg], info)
        engine._transcribe_unlocked(np.zeros(16000, dtype=np.float32))

        assert engine.last_quality_summary is None

    def test_init_starts_as_none(self):
        engine = _make_loaded_engine()
        assert engine.last_quality_summary is None


# ── pipeline capture + transcription_final payload ───────────────────────


def _make_pipeline(app):
    from voice_typer.server.dictation_pipeline import DictationPipeline

    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    return pipeline


class TestPipelineQualityPayload:
    def test_store_result_attaches_quality_to_event(self, monkeypatch):
        app = MagicMock()
        app.config.history_enabled = False
        app.config.crash_recovery_enabled = False
        app.config.log_transcriptions = False

        published: list[dict] = []

        def fake_publish(msg: dict) -> bool:
            published.append(msg)
            return True

        monkeypatch.setattr("voice_typer.server.event_bus.publish", fake_publish)

        pipeline = _make_pipeline(app)
        pipeline._quality_summary = {
            "mean_logprob": -1.7,
            "min_logprob": -2.2,
            "no_speech_prob_max": 0.65,
            "segments": 2,
        }
        pipeline._store_result("unclear speech")

        assert len(published) == 1
        data = published[0]["data"]
        assert data["text"] == "unclear speech"
        assert data["quality"]["mean_logprob"] == pytest.approx(-1.7)
        assert data["quality"]["no_speech_prob_max"] == pytest.approx(0.65)

    def test_store_result_omits_quality_when_absent(self, monkeypatch):
        """Engines without confidence stats (or streaming cycles) leave
        the payload unchanged — no empty-object sentinel in the wire
        format."""
        app = MagicMock()
        app.config.history_enabled = False
        app.config.crash_recovery_enabled = False
        app.config.log_transcriptions = False

        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        pipeline = _make_pipeline(app)
        pipeline._store_result("clean text")

        assert len(published) == 1
        assert published[0]["data"] == {"text": "clean text"}


class TestTranscribeStepCapture:
    def test_captures_engine_summary_on_batch_path(self):
        """``_transcribe`` reads ``last_quality_summary`` off the active
        engine right after ``transcribe_with_fallback`` and stores it on
        the pipeline for the storage step."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        engine = MagicMock()
        engine.is_loaded = True
        engine.transcribe_with_fallback.return_value = "some words"
        engine.last_quality_summary = {"mean_logprob": -0.3}
        app.models.active_transcriber.return_value = engine
        registry = app.models.registry
        registry.active_name = "whisper"
        registry.busy_context.return_value.__enter__ = lambda s: None
        registry.busy_context.return_value.__exit__ = lambda s, *a: None
        app.recording.pop_streaming_session.return_value = None
        app.models.touch_active_model = MagicMock()
        pipeline._app = app
        pipeline._cycle_id = "cycle-1"
        pipeline._audio = np.zeros(1600, dtype=np.float32)
        pipeline._audio_stats = (0.1, 0.2, 3.0)
        pipeline._recorded_rms = 0.1
        pipeline._duration = 0.1
        pipeline._device_info = ""

        pipeline._transcribe()

        assert pipeline._quality_summary == {"mean_logprob": -0.3}

    def test_missing_engine_attribute_leaves_none(self):
        """A backend without ``last_quality_summary`` (Parakeet / Qwen /
        cloud engines, or older test stubs) keeps the summary ``None``
        instead of raising."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        engine = MagicMock(spec=["is_loaded", "transcribe_with_fallback", "device_info"])
        engine.is_loaded = True
        engine.transcribe_with_fallback.return_value = "words"
        engine.device_info = "cpu"
        app.models.active_transcriber.return_value = engine
        registry = app.models.registry
        registry.active_name = "parakeet"
        registry.busy_context.return_value.__enter__ = lambda s: None
        registry.busy_context.return_value.__exit__ = lambda s, *a: None
        app.recording.pop_streaming_session.return_value = None
        app.models.touch_active_model = MagicMock()
        pipeline._app = app
        pipeline._cycle_id = "cycle-2"
        pipeline._audio = np.zeros(1600, dtype=np.float32)
        pipeline._audio_stats = (0.1, 0.2, 3.0)
        pipeline._recorded_rms = 0.1
        pipeline._duration = 0.1
        pipeline._device_info = ""

        pipeline._transcribe()

        assert pipeline._quality_summary is None
