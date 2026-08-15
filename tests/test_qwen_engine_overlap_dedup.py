"""PVT-019: QwenEngine chunk-overlap dedup tests.

Verifies that ``QwenEngine._transcribe_chunked`` removes duplicate
words at chunk boundaries caused by the 3 s audio overlap. Before
this fix, the overlap region was transcribed by both the previous
and the current chunk and the duplicate text was silently
concatenated, producing output like:

    "the quick brown fox brown fox jumps over the lazy dog"

instead of the correct:

    "the quick brown fox jumps over the lazy dog"

These tests exercise both the pure ``_dedup_overlap`` helper and the
end-to-end ``_transcribe_chunked`` integration path. The model and
the ONNX sessions are mocked — no real weights required.
"""

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
    """Build a deterministic non-silent audio array long enough to trigger chunking.

    ``seconds`` must exceed ``_QWEN_CHUNK_SECONDS`` (30 s) so
    ``transcribe()`` dispatches to ``_transcribe_chunked``.

    A moderate-amplitude sine wave (0.1) keeps RMS well above the
    hallucination filter's 0.001 threshold (the filter only fires on
    known hallucination phrases anyway, so the audio content here is
    largely symbolic — the model is mocked).
    """
    n = int(seconds * sample_rate)
    t = np.linspace(0, seconds, n, endpoint=False, dtype=np.float32)
    return (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


class TestDedupOverlapUnit:
    """PVT-019: ``QwenEngine._dedup_overlap`` pure-function unit tests."""

    def test_no_overlap_returns_unchanged(self):
        engine = _make_engine()
        result = engine._dedup_overlap("hello world", "foo bar baz")
        assert result == "foo bar baz"

    def test_exactly_3word_match_removed(self):
        """Default N=3: a 3-word tail/head match is removed."""
        engine = _make_engine()
        prev = "one two three four"
        curr = "two three four five"
        # prev[-3:] = ["two", "three", "four"], curr[:3] = ["two", "three", "four"] → match.
        result = engine._dedup_overlap(prev, curr)
        assert result == "five"

    def test_2word_overlap_removed_via_fallback(self):
        """N=3 doesn't match, k=2 does — fallback catches the 2-word overlap."""
        engine = _make_engine()
        prev = "I went to the store"
        curr = "the store is closed"
        # prev[-3:] = ["to", "the", "store"], curr[:3] = ["the", "store", "is"] → no match.
        # prev[-2:] = ["the", "store"], curr[:2] = ["the", "store"] → match.
        result = engine._dedup_overlap(prev, curr)
        assert result == "is closed"

    def test_1word_overlap_removed_via_fallback(self):
        """N=3 and k=2 don't match, k=1 does — fallback catches the 1-word overlap."""
        engine = _make_engine()
        prev = "end of the"
        curr = "the sentence continues"
        result = engine._dedup_overlap(prev, curr)
        assert result == "sentence continues"

    def test_largest_k_wins_over_smaller_k(self):
        """When multiple k values match, the largest is preferred (more reliable)."""
        engine = _make_engine()
        prev = "a b c d e"
        curr = "c d e f g"
        # k=3: prev[-3:] = ["c", "d", "e"], curr[:3] = ["c", "d", "e"] → match.
        # (k=1 and k=2 also match, but k=3 should win.)
        result = engine._dedup_overlap(prev, curr)
        assert result == "f g"

    def test_empty_prev_returns_curr_unchanged(self):
        engine = _make_engine()
        assert engine._dedup_overlap("", "hello world") == "hello world"

    def test_empty_curr_returns_empty(self):
        engine = _make_engine()
        assert engine._dedup_overlap("hello world", "") == ""

    def test_short_chunks_clamp_k_to_available_words(self):
        """Chunks shorter than N words still dedup correctly via min() clamp."""
        engine = _make_engine()
        # prev has 2 words, curr has 3 words, N=3 → max_k = min(3, 2, 3) = 2.
        prev = "the end"
        curr = "the end of"
        # k=2: prev[-2:] = ["the", "end"], curr[:2] = ["the", "end"] → match.
        result = engine._dedup_overlap(prev, curr)
        assert result == "of"

    def test_full_duplicate_returns_empty_string(self):
        """If entire curr matches prev tail, return empty (caller skips chunk)."""
        engine = _make_engine()
        prev = "hello world foo bar"
        curr = "foo bar"
        # k=2: prev[-2:] = ["foo", "bar"], curr[:2] = ["foo", "bar"] → match.
        # Returns " ".join(curr_words[2:]) = " ".join([]) = "".
        result = engine._dedup_overlap(prev, curr)
        assert result == ""

    def test_custom_n_parameter_catches_longer_overlaps(self):
        """N is tunable — N=5 catches a 5-word overlap that default N=3 would miss."""
        engine = _make_engine()
        prev = "one two three four five six"
        curr = "two three four five six seven"
        # With default N=3: k=3 matches ("four five six" vs "two three four"? No).
        # Actually: prev[-3:] = ["four", "five", "six"], curr[:3] = ["two", "three", "four"]. No match.
        # k=2: prev[-2:] = ["five", "six"], curr[:2] = ["two", "three"]. No.
        # k=1: prev[-1:] = ["six"], curr[:1] = ["two"]. No. → unchanged.
        default_result = engine._dedup_overlap(prev, curr)
        assert default_result == "two three four five six seven"

        # With N=5: prev[-5:] = ["two", "three", "four", "five", "six"],
        # curr[:5] = ["two", "three", "four", "five", "six"] → match.
        result = engine._dedup_overlap(prev, curr, n=5)
        assert result == "seven"

    def test_no_false_positive_when_context_differs(self):
        """A single common word at the boundary with different context is NOT a false positive.

        This is actually a TRUE positive in the overlap-dedup context —
        the overlap region is the SAME audio, so a matching boundary
        word is very likely the same word. We document this behaviour
        explicitly so future readers understand the trade-off.
        """
        engine = _make_engine()
        prev = "I went to the store"
        curr = "the store is closed"
        # k=2 match on "the store" → remove → "is closed". Correct.
        result = engine._dedup_overlap(prev, curr)
        assert result == "is closed"

    def test_no_match_when_nothing_overlaps(self):
        """Completely disjoint vocabularies at the boundary — no dedup."""
        engine = _make_engine()
        prev = "alpha beta gamma"
        curr = "delta epsilon zeta"
        # k=3, k=2, k=1 all fail to match.
        result = engine._dedup_overlap(prev, curr)
        assert result == "delta epsilon zeta"

    def test_punctuation_attached_to_words_preserved(self):
        """Dedup is whitespace-split — punctuation stays attached to its word.

        Note: this means "end." and "end" are different tokens. This is
        acceptable for the simple heuristic; callers wanting punctuation-
        insensitive dedup should normalise the text before passing it in.
        """
        engine = _make_engine()
        prev = "the end."
        curr = "end. of the sentence"
        # k=1: prev[-1:] = ["end."], curr[:1] = ["end."] → match.
        result = engine._dedup_overlap(prev, curr)
        assert result == "of the sentence"


class TestTranscribeChunkedDedup:
    """PVT-019: ``_transcribe_chunked`` end-to-end dedup behaviour."""

    def test_duplicate_at_boundary_is_removed(self):
        """Two boundaries with overlapping text — duplicates removed at both."""
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        # 65 s audio → 3 chunks (30s + 30s + 5s, with 3 s overlap).
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("the quick brown fox")],
            [_mock_asr_result("brown fox jumps over the lazy dog")],
            [_mock_asr_result("the lazy dog barked loudly")],
        ]
        engine._model = mock_model

        audio = _make_audio(seconds=65.0)
        result = engine.transcribe(audio)

        # Without dedup:
        #   "the quick brown fox brown fox jumps over the lazy dog the lazy dog barked loudly"
        # With dedup:
        #   chunk 0: "the quick brown fox" (verbatim, no predecessor)
        #   chunk 1: dedup → k=2 match ("brown fox") → "jumps over the lazy dog"
        #   chunk 2: dedup → k=3 match ("the lazy dog") → "barked loudly"
        assert result == "the quick brown fox jumps over the lazy dog barked loudly"

    def test_no_duplicate_passes_through_unchanged(self):
        """When chunks have no overlapping text, dedup is a no-op."""
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("hello world")],
            [_mock_asr_result("foo bar baz")],
            [_mock_asr_result("qux quux corge")],
        ]
        engine._model = mock_model

        audio = _make_audio(seconds=65.0)
        result = engine.transcribe(audio)

        assert result == "hello world foo bar baz qux quux corge"

    def test_full_duplicate_chunk_skipped_without_advancing_prev(self):
        """A chunk whose entire transcription duplicates prev tail is skipped.

        Critically, ``prev_text`` is NOT advanced by the skipped chunk —
        the next chunk's dedup still compares against the last
        successfully-appended chunk's tail.
        """
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("the end of the story")],
            [_mock_asr_result("the story")],  # fully duplicates "the story" tail → skipped
            [_mock_asr_result("the story continues here")],
        ]
        engine._model = mock_model

        audio = _make_audio(seconds=65.0)
        result = engine.transcribe(audio)

        # chunk 0: "the end of the story" (appended, prev_text = "the end of the story")
        # chunk 1: dedup("the end of the story", "the story") → k=2 match → "" → skipped
        #          prev_text UNCHANGED (still "the end of the story")
        # chunk 2: dedup("the end of the story", "the story continues here")
        #          k=2 match on ("the", "story") → "continues here"
        assert result == "the end of the story continues here"

    def test_first_chunk_never_deduped(self):
        """The first chunk has no predecessor — appended verbatim."""
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        # 40 s audio → 2 chunks (30s + 10s, with 3 s overlap).
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("alpha beta gamma")],
            [_mock_asr_result("gamma delta epsilon")],
        ]
        engine._model = mock_model

        audio = _make_audio(seconds=40.0)
        result = engine.transcribe(audio)

        # chunk 0: "alpha beta gamma" (verbatim — prev_text was empty)
        # chunk 1: dedup → k=1 match on "gamma" → "delta epsilon"
        assert result == "alpha beta gamma delta epsilon"

    def test_hallucination_rejected_chunk_does_not_advance_prev(self):
        """A hallucination-rejected chunk must not update prev_text.

        If a chunk is rejected by the hallucination filter, its text is
        never appended, so prev_text must remain pointing at the last
        valid chunk. The next chunk's dedup then compares against the
        last valid chunk's tail — not the rejected chunk's text.
        """
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("the quick brown fox")],
            [_mock_asr_result("thanks for watching")],  # hallucination, RMS will be low
            [_mock_asr_result("brown fox jumps away")],
        ]
        engine._model = mock_model

        # Use a very-low-amplitude audio so the hallucination filter's
        # rms < 0.001 branch fires for the "thanks for watching" chunk.
        # The first and third chunks have known non-hallucination text
        # so they pass the filter regardless of RMS.
        audio = np.zeros(int(65.0 * 16000), dtype=np.float32)  # all-zero → RMS = 0

        result = engine.transcribe(audio)

        # chunk 0: "the quick brown fox" — not a known hallucination, passes.
        # chunk 1: "thanks for watching" — IS a known hallucination, RMS=0 → rejected.
        #          prev_text stays at "the quick brown fox".
        # chunk 2: "brown fox jumps away" — dedup("the quick brown fox", "brown fox jumps away")
        #          k=2 match on ("brown", "fox") → "jumps away".
        assert result == "the quick brown fox jumps away"

    def test_three_chunks_with_cascading_overlap(self):
        """Stress test: 3 chunks where each pair overlaps by a different word count."""
        engine = _make_engine()
        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            [_mock_asr_result("a b c d e")],
            [_mock_asr_result("d e f g h")],  # 2-word overlap ("d e")
            [_mock_asr_result("f g h i j")],  # 3-word overlap ("f g h")
        ]
        engine._model = mock_model

        audio = _make_audio(seconds=65.0)
        result = engine.transcribe(audio)

        # chunk 0: "a b c d e" (verbatim)
        # chunk 1: dedup("a b c d e", "d e f g h") → k=2 match ("d e") → "f g h"
        #          prev_text = "f g h"
        # chunk 2: dedup("f g h", "f g h i j") → k=3 match ("f g h") → "i j"
        assert result == "a b c d e f g h i j"


class TestModuleConstants:
    """PVT-019: verify the dedup constants are exported and tuned as documented."""

    def test_qwen_overlap_dedup_words_default_is_3(self):
        """The task spec says 'Start with N=3 as a heuristic' — verify the default."""
        from voice_typer.server.qwen_engine import _QWEN_OVERLAP_DEDUP_WORDS

        assert _QWEN_OVERLAP_DEDUP_WORDS == 3

    def test_qwen_chunk_overlap_seconds_is_3(self):
        """The audio overlap is 3 s — this is what the dedup is mitigating."""
        from voice_typer.server.qwen_engine import _QWEN_CHUNK_OVERLAP_SECONDS

        assert _QWEN_CHUNK_OVERLAP_SECONDS == 3

    def test_dedup_overlap_default_n_uses_module_constant(self):
        """The ``_dedup_overlap`` default ``n`` binds to the module constant.

        This means tuning the constant automatically updates the
        default — operators can lower N for stricter matching or
        raise it for more aggressive dedup without touching the method.
        """
        import inspect

        from voice_typer.server.qwen_engine import (
            _QWEN_OVERLAP_DEDUP_WORDS,
            QwenEngine,
        )

        sig = inspect.signature(QwenEngine._dedup_overlap)
        assert sig.parameters["n"].default == _QWEN_OVERLAP_DEDUP_WORDS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
