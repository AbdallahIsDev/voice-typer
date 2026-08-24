"""split from tests/test_feature_hardening_regressions.py (L445-650).

Source marker: ``tests/test_new_cq030_parakeet_merge.py``.

Regression tests for NEW-CQ-030 / parakeet_engine._merge_chunks.

Old behaviour skipped ``int(len(words) * 0.12)`` words at every chunk
boundary — silently dropping up to 3 legitimate words per 25-word chunk
even when the boundary contained no overlap duplicates.

New behaviour:
- Skips at most ``_MAX_BOUNDARY_SKIP_WORDS`` (2) words at a boundary.
- Only skips a multi-word run when those words actually appear at the
  tail of the previous chunk (true overlap duplicate).
- When no overlap duplicate is detected, skip is 0 — no words
  from the new chunk's head are dropped.  Boundary hallucinations are
  filtered upstream by ``should_reject_low_audio_hallucination``.
- Never scales skip with chunk length.

Class/method names, assertion logic, and imports below are preserved
verbatim from the original monolith — only file location has changed.

NOTE: ``TestSourceCheck`` (which statically inspects ``recording.py``
for the NEW-CONC-004 RMS suppression logic) is included here per the
split plan — it was originally placed between
``TestRmsCallbackErrorSuppression`` and ``TestMergeChunksRegression``
in the monolith, and the split assigns it to this file.
"""

# === Source: tests/test_new_cq030_parakeet_merge.py ===

from __future__ import annotations

import pytest
from voice_typer.server.parakeet_engine import (
    _MAX_BOUNDARY_SKIP_WORDS,
    _OVERLAP_DEDUP_WINDOW,
    ParakeetEngine,
)


@pytest.fixture
def engine_no_model() -> ParakeetEngine:
    """Construct a ParakeetEngine without loading the model.

    ``_merge_chunks`` and ``_compute_overlap_skip`` are pure string
    operations and do not touch the model, so a model-less instance is
    safe for these tests.
    """
    # Bypass __init__ which would try to import torch / load weights.
    eng = ParakeetEngine.__new__(ParakeetEngine)
    return eng


def engine_no_global_chunks_safe(eng):
    """Helper used by test_empty_chunk_skipped (kept simple)."""
    return eng._merge_chunks(["alpha", "", "bravo"])


def engine_no_global_chunks_safe_2(eng, a, b):
    result = eng._merge_chunks([a, b])
    assert "jumps" in result.split(), f"single-word chunk lost: {result!r}"
    return result


class TestSourceCheck:
    """Static check: the recording.py source must implement the
    suppression logic."""

    def test_source_has_suppression_logic(self):
        import inspect

        from voice_typer.server import recording

        source = inspect.getsource(recording)
        assert "_rms_callback_error_count" in source, (
            "recording.py must track _rms_callback_error_count to "
            "suppress traceback formatting after the first occurrence"
        )
        assert "% 100 == 0" in source, "recording.py must re-log with exc_info every 100th occurrence"
        assert "traceback suppressed" in source, (
            "recording.py must log a 'traceback suppressed' message for intermediate occurrences"
        )


class TestMergeChunksRegression:
    """the merge must not silently drop legitimate words."""

    def test_single_chunk_returned_as_is(self, engine_no_model):
        result = engine_no_model._merge_chunks(["hello world"])
        assert result == "hello world"

    def test_empty_list_returns_empty(self, engine_no_model):
        assert engine_no_model._merge_chunks([]) == ""

    def test_no_overlap_no_large_skip(self, engine_no_model):
        """Two chunks with no shared boundary words must NOT lose words
        via the old 12% ratio.  Previously this dropped 3 words from a
        25-word second chunk.

        with the allowance removed, NO words from chunk_b's head
        may be dropped.
        """
        chunk_a = "the quick brown fox jumps over the lazy dog"
        chunk_b = "and now for something completely different here we go now"
        result = engine_no_model._merge_chunks([chunk_a, chunk_b])
        # All of chunk_a must appear.
        assert chunk_a in result
        # no words from chunk_b's head may be dropped.
        b_words = chunk_b.split()
        # Find where chunk_b content starts in result.
        result_words = result.split()
        # Last len(chunk_a) words should be the start of chunk_b (no
        # allowance skip with the  fix).
        # Easier: ensure every word of chunk_b is present in order.
        b_idx = 0
        b_to_find = b_words
        result_idx = 0
        while b_idx < len(b_to_find) and result_idx < len(result_words):
            if result_words[result_idx] == b_to_find[b_idx]:
                b_idx += 1
            result_idx += 1
        assert b_idx == len(b_to_find), (
            f"Lost chunk_b words after merge: only matched {b_idx} of {len(b_to_find)} in result={result!r}"
        )

    def test_explicit_overlap_dedup(self, engine_no_model):
        """When the model literally re-transcribes the tail of chunk_a
        as the head of chunk_b, the duplicate words must be removed.
        """
        chunk_a = "the quick brown fox jumps over"
        chunk_b = "fox jumps over the lazy dog"
        # "fox jumps over" is the overlap run (3 words but only 2 fit in
        # _MAX_BOUNDARY_SKIP_WORDS — so 2 are skipped).
        result = engine_no_model._merge_chunks([chunk_a, chunk_b])
        # The result should contain "the quick brown fox jumps over the lazy dog"
        # OR drop "fox jumps" and keep "over the lazy dog" — at most 2 skipped.
        result_words = result.split()
        # Verify no word is duplicated beyond what existed in inputs.
        # Specifically, "fox" and "jumps" should not appear twice.
        assert result_words.count("fox") <= 1, f"fox duplicated: {result!r}"
        assert result_words.count("jumps") <= 1, f"jumps duplicated: {result!r}"
        # The non-overlap tail "the lazy dog" must survive.
        for word in ("the", "lazy", "dog"):
            assert word in result_words, f"{word!r} lost: {result!r}"

    def test_skip_never_exceeds_cap(self, engine_no_model):
        """Even with a 50-word chunk (which under the old ratio would
        skip 6 words), skip must stay at the cap.
        """
        chunk_a = "alpha bravo charlie delta echo"
        # 50 words, none overlapping chunk_a
        chunk_b = " ".join(f"w{i}" for i in range(50))
        result = engine_no_model._merge_chunks([chunk_a, chunk_b])
        result_words = result.split()
        # chunk_a contributes 5 words; chunk_b contributes 50 words
        # (: no allowance skip when no overlap is detected).
        assert len(result_words) >= 5 + 50, (
            f"Too many words lost: result has {len(result_words)} words, expected at least 55. Result: {result!r}"
        )

    def test_punctuation_insensitive_overlap(self, engine_no_model):
        """Overlap detection should ignore trailing punctuation."""
        chunk_a = "i went to the store"
        chunk_b = "Store, then i came back home"
        result = engine_no_model._merge_chunks([chunk_a, chunk_b])
        result_words = result.split()
        # "store" / "Store," must not be duplicated.
        store_count = sum(1 for w in result_words if w.strip(",.!?").lower() == "store")
        assert store_count == 1, f"store duplicated: {result!r}"
        # The non-overlap content must survive.
        for word in ("then", "came", "back", "home"):
            assert word in result_words, f"{word!r} lost: {result!r}"

    def test_three_chunks_chain(self, engine_no_model):
        """Multiple boundaries must each apply the dedup independently."""
        chunk_a = "alpha bravo charlie delta"
        chunk_b = "delta echo foxtrot golf"
        chunk_c = "golf hotel india juliett"
        result = engine_no_model._merge_chunks([chunk_a, chunk_b, chunk_c])
        result_words = result.split()
        # No word should appear more than once across boundaries.
        for w in ("delta", "golf"):
            assert result_words.count(w) == 1, f"{w!r} duplicated in 3-chain: {result!r}"

    def test_empty_chunk_skipped(self, engine_no_model):
        """An empty intermediate chunk must not blow up the merge."""
        engine_no_global_chunks_safe(engine_no_model)

    def test_short_new_chunk_returns_at_least_one_word(self, engine_no_model):
        """A new chunk with only 1 word must not be entirely skipped."""
        chunk_a = "the quick brown fox"
        chunk_b = "jumps"
        engine_no_global_chunks_safe_2(engine_no_model, chunk_a, chunk_b)


class TestComputeOverlapSkip:
    """Direct unit tests for the helper that decides how many leading
    words of a new chunk to skip."""

    def test_no_overlap_returns_zero_skip(self, engine_no_model):
        """When no overlap is detected, skip MUST be 0 — do not drop legitimate words.

        Regression for the previous 'allowance' of 1 word per
        boundary silently dropped up to 14 words per 5-minute recording
        (one per chunk boundary) even when the model did not re-transcribe
        any overlap text.  Boundary hallucinations are filtered upstream
        by should_reject_low_audio_hallucination.
        """
        # Two completely different word sets, new chunk has >1 word.
        skip = engine_no_model._compute_overlap_skip(["alpha", "bravo"], ["charlie", "delta"])
        assert skip == 0  # no allowance — do not drop legitimate words

    def test_single_word_new_chunk_no_allowance(self, engine_no_model):
        skip = engine_no_model._compute_overlap_skip(["alpha", "bravo"], ["charlie"])
        assert skip == 0  # don't drop the only word

    def test_explicit_two_word_overlap(self, engine_no_model):
        skip = engine_no_model._compute_overlap_skip(["alpha", "bravo", "charlie"], ["bravo", "charlie", "delta"])
        assert skip == 2

    def test_capped_at_two_even_if_more_overlap(self, engine_no_model):
        """Even if 3 overlap words exist, skip is capped."""
        skip = engine_no_model._compute_overlap_skip(
            ["alpha", "bravo", "charlie", "delta"],
            ["bravo", "charlie", "delta", "echo"],
        )
        assert skip == _MAX_BOUNDARY_SKIP_WORDS

    def test_punctuation_insensitive(self, engine_no_model):
        skip = engine_no_model._compute_overlap_skip(
            ["i", "went", "to", "the", "store"],
            ["Store,", "then", "came", "back"],
        )
        assert skip == 1  # the punctuation-cased "store" matches

    def test_empty_inputs_safe(self, engine_no_model):
        assert engine_no_model._compute_overlap_skip([], ["a", "b"]) == 0
        assert engine_no_model._compute_overlap_skip(["a", "b"], []) == 0
        assert engine_no_model._compute_overlap_skip([], []) == 0

    def test_cap_constant_sanity(self):
        assert _MAX_BOUNDARY_SKIP_WORDS == 2
        assert _OVERLAP_DEDUP_WINDOW == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
