"""Regression tests: transcription word-drop bug for long recordings."""

import inspect
import threading
from unittest.mock import MagicMock

import numpy as np
from voice_typer.server.parakeet_engine import ParakeetEngine
from voice_typer.server.qwen_engine import QwenEngine


class TestParakeetNoMaxNewTokensCap:
    """RC-1: max_new_tokens=256 silently truncated dense chunks."""

    def test_transcribe_segment_has_no_max_new_tokens_256(self):
        """The ``_transcribe_segment`` method must NOT pass"""
        src = inspect.getsource(ParakeetEngine._transcribe_segment)
        assert "max_new_tokens=256" not in src, (
            "_transcribe_segment must not use max_new_tokens=256, it silently "
            "truncates dense 25s chunks. Remove the cap or raise to >=1024."
        )

    def test_no_separate_unlocked_segment_method(self):
        """Post-ONNX: there is no separate ``_transcribe_segment_unlocked``"""
        assert not hasattr(ParakeetEngine, "_transcribe_segment_unlocked"), (
            "ParakeetEngine must NOT define a separate _transcribe_segment_unlocked "
            "method, the ONNX backend has a single _transcribe_segment method "
            "used by both the GPU path and the GPU→CPU fallback path. A separate "
            "unlocked method would re-introduce the RC-1 risk of one path "
            "carrying the max_new_tokens=256 cap while the other doesn't."
        )


class TestParakeetNoAllowanceSkip:
    """RC-3: _compute_overlap_skip returned 1 with no overlap, dropping legitimate words."""

    def test_no_overlap_returns_zero(self):
        """When there is no true overlap, skip MUST be 0, not 1."""
        skip = ParakeetEngine._compute_overlap_skip(
            ["alpha", "bravo"],
            ["charlie", "delta"],  # no shared words
        )
        assert skip == 0

    def test_actual_overlap_returns_match_length(self):
        """When there IS true overlap, skip equals the match length (unchanged behavior)."""
        skip = ParakeetEngine._compute_overlap_skip(["alpha", "bravo", "charlie"], ["bravo", "charlie", "delta"])
        assert skip == 2  # "bravo charlie" matches the tail of prev

    def test_merge_chunks_preserves_word_count_across_many_boundaries(self):
        """Merging 15 chunks of distinct text must preserve ~100% of words."""
        chunks = [f"chunk {i} has unique words alpha beta gamma" for i in range(15)]
        engine = ParakeetEngine.__new__(ParakeetEngine)
        result = engine._merge_chunks(chunks)
        result_words = result.split()
        # Each chunk has 7 words; 15 chunks = 105 words. Allow small tolerance
        assert len(result_words) >= 100, (
            f"Lost {105 - len(result_words)} words across 15 boundaries, "
            f"expected >=100, got {len(result_words)}. Result: {result!r}"
        )


class TestQwenChunking:
    """RC-4: Qwen engine had no chunking for long audio."""

    def test_qwen_chunks_long_audio(self):
        """QwenEngine.transcribe must split audio > 30s into chunks."""
        engine = QwenEngine.__new__(QwenEngine)
        engine._model = MagicMock()
        engine._lock = threading.RLock()
        engine._inference_event = threading.Event()
        engine._inference_cond = threading.Condition(engine._lock)
        engine._active_inference = 0
        engine._INFERENCE_BATCH_SIZE = 1
        engine._abort_event = threading.Event()
        engine.language = "en"
        engine.device = "cpu"
        mock_transcription = MagicMock()
        mock_transcription.text = "chunk text"
        engine._model.transcribe.return_value = [mock_transcription]

        # 2 minutes of audio at 16kHz
        audio = np.zeros(16000 * 120, dtype=np.float32)
        engine.transcribe(audio)

        # Must be called multiple times (chunked), not once
        assert engine._model.transcribe.call_count > 1, (
            "QwenEngine must chunk long audio instead of passing the entire array, "
            f"got {engine._model.transcribe.call_count} calls"
        )

    def test_qwen_short_audio_not_chunked(self):
        """QwenEngine.transcribe must NOT chunk audio <= 30s (single call)."""
        engine = QwenEngine.__new__(QwenEngine)
        engine._model = MagicMock()
        engine._lock = threading.RLock()
        engine._inference_event = threading.Event()
        engine._inference_cond = threading.Condition(engine._lock)
        engine._active_inference = 0
        engine._INFERENCE_BATCH_SIZE = 1
        engine._abort_event = threading.Event()
        engine.language = "en"
        engine.device = "cpu"
        mock_transcription = MagicMock()
        mock_transcription.text = "short text"
        engine._model.transcribe.return_value = [mock_transcription]

        # 10 seconds of audio at 16kHz
        audio = np.zeros(16000 * 10, dtype=np.float32)
        engine.transcribe(audio)

        assert engine._model.transcribe.call_count == 1, (
            f"Short audio must not be chunked, expected 1 call, got {engine._model.transcribe.call_count}"
        )

    def test_qwen_split_audio_covers_full_array(self):
        """_split_audio must cover the entire audio array (no tail drop)."""
        # 90 seconds of audio, 30s chunks, 3s overlap → step = 27s
        audio = np.arange(16000 * 90, dtype=np.float32)
        chunks = QwenEngine._split_audio(audio, 30, 3)

        # All chunks concatenated (with overlap removal) must reconstruct the audio
        assert len(chunks[-1]) > 0
        # The last chunk's last sample should be the audio's last sample
        assert chunks[-1][-1] == audio[-1], (
            "Last chunk must include the tail of the audio, otherwise words at "
            "the end of long recordings are silently dropped."
        )
