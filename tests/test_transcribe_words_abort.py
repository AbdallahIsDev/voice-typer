"""Tests for SI-3: ``_transcribe_words_unlocked`` abort check."""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def mock_faster_whisper(monkeypatch):
    """Mock faster_whisper + ctranslate2 so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 0
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


def _make_segment(words_list, seg_index=0):
    """Build a mock segment with ``.words`` (list of word-like objects)."""
    seg = MagicMock()
    seg.start = float(seg_index)
    seg.end = float(seg_index + 1)
    seg.words = words_list
    return seg


def _make_word(text, start, end):
    w = MagicMock()
    w.word = f" {text}"
    w.start = start
    w.end = end
    return w


class TestTranscribeWordsAbort:
    """SI-3: ``_transcribe_words_unlocked`` honors ``_abort_event``."""

    def test_abort_set_before_loop_breaks_immediately(self):
        """When ``_abort_event`` is already set when the loop starts,"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        mock_model = MagicMock()
        seg0 = _make_segment(
            [_make_word("hello", 0.0, 0.5), _make_word("world", 0.5, 1.0)],
            seg_index=0,
        )
        seg1 = _make_segment([_make_word("again", 0.0, 0.5)], seg_index=1)
        mock_model.transcribe.return_value = ([seg0, seg1], MagicMock())
        engine._model = mock_model

        # Pre-set abort so the very first iteration's check fires.
        engine._abort_event.set()

        words = engine.transcribe_words(np.zeros(16000, dtype=np.float32))
        assert words == [], f"abort pre-set should have produced zero words, got {words!r}"

    def test_abort_set_mid_loop_breaks_early(self):
        """loop breaks early: only the words from segments consumed BEFORE"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        mock_model = MagicMock()

        # Five segments, each with one distinctive word.
        segments = [_make_segment([_make_word(f"word{i}", 0.0, 0.5)], seg_index=i) for i in range(5)]

        def fake_transcribe(*args, **kwargs):
            # Generator that sets the abort event immediately before
            def gen():
                for i, seg in enumerate(segments):
                    if i == 2:
                        engine._abort_event.set()
                    yield seg

            return gen(), MagicMock()

        mock_model.transcribe.side_effect = fake_transcribe
        engine._model = mock_model

        words = engine.transcribe_words(np.zeros(16000, dtype=np.float32))
        word_texts = [w.word for w in words]
        # First two segments' words were consumed before abort fired.
        assert "word0" in word_texts, f"word0 should be present (consumed before abort), got {word_texts!r}"
        assert "word1" in word_texts, f"word1 should be present (consumed before abort), got {word_texts!r}"
        # Segment 2+ must NOT have been processed, abort fired first.
        assert "word2" not in word_texts, f"abort should have stopped the loop before segment 2, got {word_texts!r}"
        assert "word3" not in word_texts
        assert "word4" not in word_texts
        # The break should have produced a SHORT list (not all 5 words).
        assert len(words) == 2, f"expected exactly 2 words before abort fired, got {len(words)}: {word_texts!r}"

    def test_abort_not_set_processes_all_segments(self):
        """Happy path: when ``_abort_event`` is never set, all segments"""
        from voice_typer.server.streaming import WordTiming
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        mock_model = MagicMock()
        seg0 = _make_segment(
            [_make_word("hello", 0.25, 0.75), _make_word("world", 0.8, 1.2)],
            seg_index=0,
        )
        seg1 = _make_segment([_make_word("again", 0.0, 0.4)], seg_index=1)
        seg2 = _make_segment([_make_word("done", 0.1, 0.3)], seg_index=2)
        mock_model.transcribe.return_value = ([seg0, seg1, seg2], MagicMock())
        engine._model = mock_model
        # Ensure abort is NOT set (default state, but be explicit).
        assert not engine._abort_event.is_set()

        words = engine.transcribe_words(
            np.zeros(16000, dtype=np.float32),
            offset_seconds=5.0,
        )
        # All three segments' words, offset applied.
        assert words == [
            WordTiming(word="hello", start_seconds=5.25, end_seconds=5.75),
            WordTiming(word="world", start_seconds=5.8, end_seconds=6.2),
            WordTiming(word="again", start_seconds=5.0, end_seconds=5.4),
            WordTiming(word="done", start_seconds=5.1, end_seconds=5.3),
        ]

    def test_abort_event_is_threading_event_instance(self):
        """Guard against accidental refactors that change the abort"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        assert isinstance(engine._abort_event, threading.Event)

    def test_abort_set_via_request_abort_method(self):
        """End-to-end: ``request_abort()`` (called by the dictation"""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en", device="cpu")
        mock_model = MagicMock()
        segments = [_make_segment([_make_word(f"w{i}", 0.0, 0.5)], seg_index=i) for i in range(4)]

        def fake_transcribe(*args, **kwargs):
            def gen():
                for i, seg in enumerate(segments):
                    if i == 2:
                        # Simulate the watchdog firing request_abort()
                        engine.request_abort()
                    yield seg

            return gen(), MagicMock()

        mock_model.transcribe.side_effect = fake_transcribe
        engine._model = mock_model

        words = engine.transcribe_words(np.zeros(16000, dtype=np.float32))
        word_texts = [w.word for w in words]
        assert "w0" in word_texts
        assert "w1" in word_texts
        assert "w2" not in word_texts, (
            f"request_abort() should have stopped the loop before segment 2, got {word_texts!r}"
        )
        assert "w3" not in word_texts
