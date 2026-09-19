"""QwenEngine chunk-seam dedup tests."""

from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_engine(model_path: str = "/fake/qwen/model", **kwargs):
    from voice_typer.server.qwen_engine import QwenEngine

    return QwenEngine(model_path=model_path, **kwargs)


def _mock_asr_result(text: str) -> MagicMock:
    """Build a mock ASRTranscription-shaped object with a ``.text`` attr."""
    result = MagicMock(name="asr_result")
    result.text = text
    return result


def _make_audio(seconds: float = 65.0, sample_rate: int = 16000) -> np.ndarray:
    """Build a deterministic non-silent audio array long enough to trigger chunking."""
    n = int(seconds * sample_rate)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    return (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _transcribe_chunks(chunk_texts: list[str], seconds: float | None = None):
    """Drive the engine's chunked path with per-chunk mocked transcriptions."""
    if seconds is None:
        seconds = 40.0 if len(chunk_texts) == 2 else 65.0
    engine = _make_engine()
    mock_model = MagicMock(name="qwen_model")
    mock_model.transcribe.side_effect = [[_mock_asr_result(text)] for text in chunk_texts]
    engine._model = mock_model
    return engine.transcribe(_make_audio(seconds=seconds))


class TestQwenSeamMergeDelegation:
    """Seam-dedup behaviour after routing through the shared helper."""

    def test_no_overlap_returns_unchanged(self):
        assert _transcribe_chunks(["hello world", "foo bar baz"]) == ("hello world foo bar baz")

    def test_2word_overlap_removed(self):
        prev = "I went to the store"
        curr = "the store is closed"
        assert _transcribe_chunks([prev, curr]) == "I went to the store is closed"

    def test_1word_overlap_removed(self):
        prev = "end of the"
        curr = "the sentence continues"
        assert _transcribe_chunks([prev, curr]) == "end of the sentence continues"

    def test_full_duplicate_chunk_dropped(self):
        """A chunk fully duplicating the predecessor's tail adds nothing."""
        assert _transcribe_chunks(["the end of the story", "the story"]) == ("the end of the story")

    def test_mixed_case_and_punctuation_overlap_removed(self):
        """Case/punctuation differences no longer defeat the dedup."""
        prev = "so this is The End."
        curr = "the end of the recording"
        assert _transcribe_chunks([prev, curr]) == ("so this is The End. of the recording")

    def test_punctuation_only_difference_still_matches(self):
        """Identical words with different trailing punctuation dedup."""
        prev = "the end."
        curr = "end. of the sentence"
        assert _transcribe_chunks([prev, curr]) == "the end. of the sentence"

    def test_no_match_when_nothing_overlaps(self):
        assert _transcribe_chunks(["alpha beta gamma", "delta epsilon zeta"]) == ("alpha beta gamma delta epsilon zeta")

    def test_skip_cap_matches_shared_constant(self):
        """A 3-word exact overlap drops at most"""
        from voice_typer.server.asr_utils import MAX_BOUNDARY_SKIP_WORDS

        assert MAX_BOUNDARY_SKIP_WORDS == 2
        assert _transcribe_chunks(["one two three four", "two three four five"]) == ("one two three four four five")


class TestTranscribeChunkedDedup:
    """``_transcribe_chunked`` end-to-end dedup behaviour (3-chunk audio)."""

    def test_duplicate_at_boundary_is_removed(self):
        """Two boundaries with overlapping text, duplicates removed at both."""
        result = _transcribe_chunks(
            [
                "the quick brown fox",
                "brown fox jumps over the lazy dog",
                "the lazy dog barked loudly",
            ]
        )
        assert result == ("the quick brown fox jumps over the lazy dog dog barked loudly")

    def test_no_duplicate_passes_through_unchanged(self):
        """When chunks have no overlapping text, dedup is a no-op."""
        result = _transcribe_chunks(["hello world", "foo bar baz", "qux quux corge"])
        assert result == "hello world foo bar baz qux quux corge"

    def test_full_duplicate_chunk_skipped_without_advancing_prev(self):
        """
        A chunk whose entire transcription duplicates prev tail is skipped.
        Critically, the skipped chunk must not participate in the next
        """
        result = _transcribe_chunks(
            [
                "the end of the story",
                "the story",  # fully duplicates "the story" tail → skipped
                "the story continues here",
            ]
        )
        assert result == "the end of the story continues here"

    def test_first_chunk_never_deduped(self):
        """The first chunk has no predecessor, appended verbatim."""
        result = _transcribe_chunks(["alpha beta gamma", "gamma delta epsilon"], seconds=40.0)
        assert result == "alpha beta gamma delta epsilon"

    def test_hallucination_rejected_chunk_does_not_advance_prev(self):
        """A hallucination-rejected chunk must not update the comparison base."""
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("the quick brown fox")],
            [_mock_asr_result("thanks for watching")],  # hallucination
            [_mock_asr_result("brown fox jumps away")],
        ]
        engine._model = mock_model

        # All-zero audio → RMS = 0, so the hallucination filter's
        audio = np.zeros(int(65.0 * 16000), dtype=np.float32)

        result = engine.transcribe(audio)

        assert result == "the quick brown fox jumps away"

    def test_three_chunks_with_cascading_overlap(self):
        """Stress test: 3 chunks where each pair overlaps."""
        result = _transcribe_chunks(
            [
                "a b c d e",
                "d e f g h",  # 2-word overlap ("d e")
                "f g h i j",  # 3-word overlap → capped at 2 → "h" remains
            ]
        )
        assert result == "a b c d e f g h h i j"


class TestSharedHelperRouting:
    """Pin that Qwen has no private dedup fork left behind."""

    def test_dedup_overlap_fork_is_gone(self):
        from voice_typer.server.qwen_engine import QwenEngine

        assert not hasattr(QwenEngine, "_dedup_overlap")

    def test_transcribe_chunked_calls_shared_merge_chunks(self, monkeypatch):
        import voice_typer.server.qwen_engine as qwen_module

        calls: list[list[str]] = []

        def _spy_merge_chunks(texts):
            calls.append(list(texts))
            return "merged"

        monkeypatch.setattr(qwen_module, "merge_chunks", _spy_merge_chunks)

        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("first chunk")],
            [_mock_asr_result("second chunk")],
        ]
        engine._model = mock_model
        result = engine.transcribe(_make_audio(seconds=40.0))

        assert result == "merged"
        assert calls == [["first chunk", "second chunk"]]

    def test_qwen_local_dedup_constant_is_gone(self):
        """The engine-local N=3 knob was removed with its fork."""
        import voice_typer.server.qwen_engine as qwen_module

        assert not hasattr(qwen_module, "_QWEN_OVERLAP_DEDUP_WORDS")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
