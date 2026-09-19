"""Parity test: ONNX Parakeet transcription vs torch baseline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

# Skip the entire module if onnx_asr isn't installed, the parity test
pytest.importorskip("onnx_asr")
pytest.importorskip("onnxruntime")

from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "audio" / "parakeet_parity.wav"

_EXPECTED_TEXT = "the quick brown fox jumps over the lazy dog"

# Edit-distance threshold: 10% of the expected word count, rounded up.
_EDIT_DISTANCE_THRESHOLD = max(1, len(_EXPECTED_TEXT.split()) // 10)


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein word-edit distance between *a* and *b*."""
    a_words = a.lower().split()
    b_words = b.lower().split()
    if len(a_words) < len(b_words):
        return _edit_distance(b, a)
    if len(b_words) == 0:
        return len(a_words)
    prev_row = list(range(len(b_words) + 1))
    for i, a_word in enumerate(a_words):
        curr_row = [i + 1]
        for j, b_word in enumerate(b_words):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (a_word != b_word)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _parakeet_model_downloaded() -> bool:
    """Return True if the Parakeet ONNX model is in the local HF cache."""
    try:
        return ParakeetEngine._is_cached()  # type: ignore[attr-defined]
    except Exception:
        return False


_PARAKEET_MODEL_DOWNLOADED = _parakeet_model_downloaded()


@pytest.mark.skipif(
    not _FIXTURE_WAV.exists(),
    reason=f"Parakeet parity fixture WAV not found at {_FIXTURE_WAV}",
)
@pytest.mark.skipif(
    not _PARAKEET_MODEL_DOWNLOADED,
    reason="Parakeet ONNX model not in HF cache, download via the Models page first",
)
class TestParakeetOnnxParity:
    """Parity test: ONNX transcription vs torch baseline."""

    def test_short_audio_matches_baseline_within_threshold(self):
        """Transcribe the fixture WAV via the ONNX backend and verify"""
        # Load the fixture WAV as a 16 kHz mono float32 numpy array.
        import wave

        with wave.open(str(_FIXTURE_WAV), "rb") as wf:
            assert wf.getframerate() == 16000, f"Fixture WAV must be 16 kHz mono (got {wf.getframerate()} Hz)"
            assert wf.getnchannels() == 1, f"Fixture WAV must be mono (got {wf.getnchannels()} channels)"
            raw = wf.readframes(wf.getnframes())
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        engine = ParakeetEngine(device="cpu", language="en")
        try:
            assert engine.load() is True, "Parakeet load() failed, check onnx_asr install + model cache"
            text = engine.transcribe(audio)
        finally:
            engine.unload()

        # Edit distance must be within threshold.
        distance = _edit_distance(text, _EXPECTED_TEXT)
        assert distance <= _EDIT_DISTANCE_THRESHOLD, (
            f"ONNX transcription edit distance {distance} exceeds threshold "
            f"{_EDIT_DISTANCE_THRESHOLD} vs baseline {_EXPECTED_TEXT!r}. "
            f"Got: {text!r}"
        )

    def test_empty_audio_returns_empty_string(self):
        """Sanity: empty audio → empty string (no model crash)."""
        engine = ParakeetEngine(device="cpu", language="en")
        try:
            assert engine.load() is True
            assert engine.transcribe(np.array([], dtype=np.float32)) == ""
        finally:
            engine.unload()

    def test_short_silence_returns_empty_or_low_text(self):
        """Sanity: 1s of silence → empty string or very short text"""
        silence = np.zeros(16000, dtype=np.float32)
        engine = ParakeetEngine(device="cpu", language="en")
        try:
            assert engine.load() is True
            text = engine.transcribe(silence)
            assert len(text.split()) <= 3, f"Expected empty/short text for silence, got: {text!r}"
        finally:
            engine.unload()


class TestEditDistanceHelper:
    """Sanity tests for the ``_edit_distance`` helper itself."""

    def test_identical_strings_distance_zero(self):
        assert _edit_distance("hello world", "hello world") == 0

    def test_one_word_substitution(self):
        assert _edit_distance("hello world", "hello there") == 1

    def test_one_word_insertion(self):
        assert _edit_distance("hello world", "hello big world") == 1

    def test_completely_different(self):
        # 2 vs 2 words, all different → 2 substitutions.
        assert _edit_distance("foo bar", "baz qux") == 2

    def test_case_insensitive(self):
        assert _edit_distance("Hello World", "hello world") == 0
