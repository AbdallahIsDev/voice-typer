"""Parity test: ONNX Parakeet transcription vs torch baseline.

Verifies the ONNX Runtime backend produces text within an edit-distance
threshold of the pre-migration torch/transformers baseline. This test
requires the real ``onnx_asr`` + ``onnxruntime`` packages AND a
downloaded Parakeet ONNX model — it skips cleanly otherwise (no error,
no fixture setup).

PLAN_ONNX_INTEGRATION.md §8.2 (Phase 1b gate):
    > Parakeet transcribes a known WAV fixture within an edit-distance
    > threshold of the torch baseline (parity).

The fixture path is ``tests/fixtures/audio/parakeet_parity.wav``. The
expected baseline text is pinned in ``_EXPECTED_TEXT`` below. The edit-
distance threshold is generous (10% of the expected word count) to
account for minor decoder differences between the torch and ONNX TDT
implementations.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

# Skip the entire module if onnx_asr isn't installed — the parity test
# requires the real ONNX backend (no mocks). The model must also be
# downloaded (see ``_PARAKEET_MODEL_DOWNLOADED``).
pytest.importorskip("onnx_asr")
pytest.importorskip("onnxruntime")

from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402

# ─── Fixture ────────────────────────────────────────────────────────────

_FIXTURE_WAV = Path(__file__).parent / "fixtures" / "audio" / "parakeet_parity.wav"

# Expected baseline transcription (torch/transformers). Pinned from a
# known-good run; the ONNX backend must come within edit-distance
# threshold of this string. If the fixture WAV changes, recompute this
# with the torch baseline and update.
_EXPECTED_TEXT = "the quick brown fox jumps over the lazy dog"

# Edit-distance threshold: 10% of the expected word count, rounded up.
# TDT decoding can produce minor differences in tokenization that don't
# affect the perceived transcription quality.
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


# ─── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not _FIXTURE_WAV.exists(),
    reason=f"Parakeet parity fixture WAV not found at {_FIXTURE_WAV}",
)
@pytest.mark.skipif(
    not _PARAKEET_MODEL_DOWNLOADED,
    reason="Parakeet ONNX model not in HF cache — download via the Models page first",
)
class TestParakeetOnnxParity:
    """Parity test: ONNX transcription vs torch baseline."""

    def test_short_audio_matches_baseline_within_threshold(self):
        """Transcribe the fixture WAV via the ONNX backend and verify
        the result is within ``_EDIT_DISTANCE_THRESHOLD`` word-edits of
        the pinned torch baseline ``_EXPECTED_TEXT``."""
        # Load the fixture WAV as a 16 kHz mono float32 numpy array.
        # We don't use ``soundfile`` (optional dep) — use the same
        # ``wave`` + manual decode path the recorder uses.
        import wave

        with wave.open(str(_FIXTURE_WAV), "rb") as wf:
            assert wf.getframerate() == 16000, f"Fixture WAV must be 16 kHz mono (got {wf.getframerate()} Hz)"
            assert wf.getnchannels() == 1, f"Fixture WAV must be mono (got {wf.getnchannels()} channels)"
            raw = wf.readframes(wf.getnframes())
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        engine = ParakeetEngine(device="cpu", language="en")
        try:
            assert engine.load() is True, "Parakeet load() failed — check onnx_asr install + model cache"
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
        """Sanity: 1s of silence → empty string or very short text
        (the hallucination filter should suppress spurious output)."""
        silence = np.zeros(16000, dtype=np.float32)
        engine = ParakeetEngine(device="cpu", language="en")
        try:
            assert engine.load() is True
            text = engine.transcribe(silence)
            # Either empty or very short (hallucination filter may or
            # may not fire depending on the model's silence behavior).
            assert len(text.split()) <= 3, f"Expected empty/short text for silence, got: {text!r}"
        finally:
            engine.unload()


# ─── Edit-distance helper tests (always run — no fixture/model needed) ──


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
