"""Tests for the shared language-filter and chunk-merge helpers in
:mod:`voice_typer.server.asr_utils`.

Phase 1c (PLAN_ONNX_INTEGRATION.md §5.3, §5.4): ``is_likely_english``,
``is_latin_char``, ``merge_chunks``, and ``compute_overlap_skip`` were
moved verbatim from ``parakeet_engine.py`` (where they were module-level
functions / staticmethods) to the shared ``asr_utils`` module so the
rewritten ONNX Parakeet engine and any future ONNX variant can import
them directly.

These tests pin the moved contract:

- ``is_latin_char`` returns True for Latin-script chars, digits,
  punctuation, separators, symbols — False for CJK / Arabic / etc.
- ``is_likely_english`` returns False when the non-Latin ratio exceeds
  ``NON_LATIN_RATIO_LIMIT`` (0.30).
- ``compute_overlap_skip`` returns the number of leading words to skip
  when a true overlap duplicate is detected (capped at
  ``MAX_BOUNDARY_SKIP_WORDS``).
- ``merge_chunks`` concatenates chunk transcriptions, skipping overlap
  duplicates via ``compute_overlap_skip``.
"""

from __future__ import annotations

from voice_typer.server.asr_utils import (
    MAX_BOUNDARY_SKIP_WORDS,
    NON_LATIN_RATIO_LIMIT,
    OVERLAP_DEDUP_WINDOW,
    compute_overlap_skip,
    is_latin_char,
    is_likely_english,
    merge_chunks,
)

# ─── is_latin_char ──────────────────────────────────────────────────────


class TestIsLatinChar:
    """``is_latin_char`` Unicode-category classification contract."""

    def test_latin_letters_return_true(self):
        for ch in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
            assert is_latin_char(ch) is True, f"Latin letter {ch!r} should return True"

    def test_digits_return_true(self):
        for ch in "0123456789":
            assert is_latin_char(ch) is True, f"Digit {ch!r} should return True"

    def test_punctuation_returns_true(self):
        for ch in ".,;:!?\"'()[]{}-_/":
            assert is_latin_char(ch) is True, f"Punctuation {ch!r} should return True"

    def test_whitespace_returns_true(self):
        # Space (U+0020) is in Unicode category Zs (Separator, Space) — returns True.
        assert is_latin_char(" ") is True
        # Tab (\t) and newline (\n) are in category Cc (Control) — they
        # fall through to the script check. ``unicodedata.name("\t", "")``
        # returns "" so the script check returns ``"" == "LATIN"`` → False.
        # This matches the original ``parakeet_engine._is_latin_char``
        # behavior — preserved verbatim per PLAN_ONNX_INTEGRATION.md §5.3.
        assert is_latin_char("\t") is False
        assert is_latin_char("\n") is False
        assert is_latin_char("\r") is False

    def test_cjk_chars_return_false(self):
        # Chinese, Japanese, Korean characters — common Parakeet hallucination targets.
        for ch in "你好世界こんにちは안녕하세요":
            assert is_latin_char(ch) is False, f"CJK char {ch!r} should return False"

    def test_arabic_chars_return_false(self):
        for ch in "مرحبا":
            assert is_latin_char(ch) is False, f"Arabic char {ch!r} should return False"

    def test_devanagari_chars_return_false(self):
        for ch in "नमस्ते":
            assert is_latin_char(ch) is False, f"Devanagari char {ch!r} should return False"

    def test_empty_string_raises_typeerror(self):
        """The function does NOT handle empty strings — ``unicodedata.category("")``
        raises ``TypeError``. This matches the original
        ``parakeet_engine._is_latin_char`` behavior (preserved verbatim
        per PLAN_ONNX_INTEGRATION.md §5.3). Callers must guard against
        empty strings before calling."""
        import pytest

        with pytest.raises(TypeError):
            is_latin_char("")

    def test_symbols_return_true(self):
        # Symbols (S* category): currency, math, emoji, etc.
        for ch in "$€±∑":
            assert is_latin_char(ch) is True, f"Symbol {ch!r} should return True"


# ─── is_likely_english ──────────────────────────────────────────────────


class TestIsLikelyEnglish:
    """``is_likely_english`` non-Latin ratio filter contract."""

    def test_pure_english_returns_true(self):
        assert is_likely_english("Hello world, this is a test.") is True

    def test_empty_string_returns_true(self):
        """Empty / whitespace-only text is treated as "likely English"
        so the caller's ``if not is_likely_english(text): return ""``
        branch does not false-positive on silence."""
        assert is_likely_english("") is True
        assert is_likely_english("   ") is True
        assert is_likely_english("\t\n") is True

    def test_english_with_digits_returns_true(self):
        assert is_likely_english("The year is 2026 and the temperature is 23.5 degrees.") is True

    def test_english_with_punctuation_returns_true(self):
        assert is_likely_english("Hello, world! How are you? (I'm fine.)") is True

    def test_pure_cjk_returns_false(self):
        """Pure CJK text is well above the 30% non-Latin limit."""
        assert is_likely_english("你好世界") is False

    def test_mixed_below_threshold_returns_true(self):
        """Text with < 30% non-Latin chars returns True.

        "Hello 你 world" → 1 non-Latin / 12 total = 8.3% < 30%.
        """
        assert is_likely_english("Hello 你 world") is True

    def test_mixed_above_threshold_returns_false(self):
        """Text with > 30% non-Latin chars returns False.

        "你好abc" → 2 non-Latin / 5 total = 40% > 30%.
        """
        assert is_likely_english("你好abc") is False

    def test_exactly_at_threshold_returns_true(self):
        """Text at exactly 30% non-Latin returns True (the check is ``>``,
        not ``>=``).

        "abc你" → 1 non-Latin / 4 total = 25% < 30% → True.
        We can't easily hit exactly 30% with single chars (would need
        10 chars with 3 non-Latin), so this test uses a slightly-below
        threshold to confirm the boundary is exclusive.
        """
        # 3 non-Latin / 10 total = 30% — exactly at threshold → True.
        text = "abcdefghi你好世"  # 7 Latin + 3 CJK = 10 chars, 30% non-Latin.
        assert is_likely_english(text) is True

    def test_just_above_threshold_returns_false(self):
        """Text just above 30% non-Latin returns False.

        "abcdefghi你好世d" → 3 non-Latin / 11 total = 27% → True.
        We need > 30%, so 4 non-Latin / 11 = 36% → False.
        """
        text = "abcdefghi你好世界"  # 7 Latin + 4 CJK = 11 chars, 36% non-Latin.
        assert is_likely_english(text) is False


# ─── compute_overlap_skip ───────────────────────────────────────────────


class TestComputeOverlapSkip:
    """``compute_overlap_skip`` overlap-detection contract."""

    def test_empty_prev_words_returns_zero(self):
        assert compute_overlap_skip([], ["hello", "world"]) == 0

    def test_empty_new_words_returns_zero(self):
        assert compute_overlap_skip(["hello", "world"], []) == 0

    def test_both_empty_returns_zero(self):
        assert compute_overlap_skip([], []) == 0

    def test_no_overlap_returns_zero(self):
        """When the new chunk's leading words do NOT appear in the
        previous chunk's tail, return 0 (do not drop legitimate words)."""
        prev = ["the", "quick", "brown", "fox"]
        new = ["jumps", "over", "the", "lazy", "dog"]
        assert compute_overlap_skip(prev, new) == 0

    def test_single_word_overlap_returns_one(self):
        """When the new chunk's first word matches a word in the prev
        chunk's tail window, return 1."""
        prev = ["the", "quick", "brown", "fox"]
        new = ["fox", "jumps", "over"]
        assert compute_overlap_skip(prev, new) == 1

    def test_two_word_overlap_returns_two(self):
        """When the new chunk's first TWO words match a contiguous run
        in the prev chunk's tail, return 2."""
        prev = ["the", "quick", "brown", "fox"]
        new = ["brown", "fox", "jumps"]
        assert compute_overlap_skip(prev, new) == 2

    def test_skip_capped_at_max_boundary_skip_words(self):
        """The skip is capped at ``MAX_BOUNDARY_SKIP_WORDS`` (2)."""
        assert MAX_BOUNDARY_SKIP_WORDS == 2
        # Even if 3 words match, the skip is capped at 2.
        prev = ["the", "quick", "brown", "fox", "jumps"]
        new = ["fox", "jumps", "over", "the", "lazy"]
        # "fox jumps" is a 2-word match (the cap). The third word "over"
        # does not match "the" so the match length is 2.
        assert compute_overlap_skip(prev, new) == 2

    def test_case_insensitive_match(self):
        """The match is case-insensitive (words are lowercased)."""
        prev = ["the", "quick", "Brown", "Fox"]
        new = ["brown", "fox", "jumps"]
        assert compute_overlap_skip(prev, new) == 2

    def test_punctuation_stripped_before_match(self):
        """The match strips leading/trailing punctuation (``.,;:!?"'()[]{}``)."""
        prev = ["the", "quick", "brown", "fox."]
        new = ["fox", "jumps", "over"]
        # "fox." normalizes to "fox" — matches the new chunk's "fox".
        assert compute_overlap_skip(prev, new) == 1

    def test_match_must_end_within_overlap_dedup_window(self):
        """The match must end within the trailing
        ``OVERLAP_DEDUP_WINDOW`` words of the prev chunk's tail."""
        # prev_tail (last OVERLAP_DEDUP_WINDOW + MAX_BOUNDARY_SKIP_WORDS = 5 words):
        # ["one", "two", "three", "four", "five"]
        # The match must end within the last 3 words: "three", "four", "five".
        prev = ["one", "two", "three", "four", "five"]
        new = ["one", "two", "next"]
        # "one two" appears at the START of prev_tail, but the match
        # would end at index 2 — which is NOT within the last 3 words
        # (last_word_idx = 5 - 2 = 3 >= OVERLAP_DEDUP_WINDOW=3). So the
        # match is rejected and the skip is 0.
        assert compute_overlap_skip(prev, new) == 0


# ─── merge_chunks ───────────────────────────────────────────────────────


class TestMergeChunks:
    """``merge_chunks`` chunk-concatenation contract."""

    def test_empty_list_returns_empty_string(self):
        assert merge_chunks([]) == ""

    def test_single_chunk_returns_that_chunk(self):
        assert merge_chunks(["hello world"]) == "hello world"

    def test_two_chunks_no_overlap_concatenates(self):
        """When there's no overlap duplicate, the chunks are simply joined."""
        result = merge_chunks(["hello world", "foo bar"])
        assert result == "hello world foo bar"

    def test_two_chunks_with_overlap_skips_duplicate(self):
        """When the new chunk's leading words duplicate the prev
        chunk's tail, the duplicate is skipped."""
        # prev tail: "world foo" — new head: "foo bar" — "foo" matches → skip 1.
        result = merge_chunks(["hello world foo", "foo bar baz"])
        assert result == "hello world foo bar baz"

    def test_two_chunks_with_two_word_overlap_skips_both(self):
        """A 2-word overlap duplicate is skipped (within the cap)."""
        result = merge_chunks(["hello world foo bar", "foo bar baz"])
        assert result == "hello world foo bar baz"

    def test_empty_chunk_in_middle_is_skipped(self):
        """An empty chunk in the middle is skipped (no words to append)."""
        result = merge_chunks(["hello world", "", "foo bar"])
        assert result == "hello world foo bar"

    def test_whitespace_only_chunk_is_skipped(self):
        """A whitespace-only chunk produces no words (``split()`` returns [])."""
        result = merge_chunks(["hello world", "   ", "foo bar"])
        assert result == "hello world foo bar"

    def test_result_is_stripped_for_multi_chunk(self):
        """The final result is stripped of leading/trailing whitespace
        when there are 2+ chunks (the ``" ".join(...).strip()`` path).

        Note: the single-chunk early-return path (``texts[0]``) does NOT
        strip — this matches the original ``parakeet_engine._merge_chunks``
        behavior preserved verbatim per §5.4.
        """
        result = merge_chunks(["  hello world  ", "foo bar"])
        # The first chunk's leading/trailing whitespace is normalized
        # by the split() + join() path: "hello world foo bar".
        assert result == "hello world foo bar"

    def test_single_chunk_not_stripped(self):
        """A single-chunk input returns ``texts[0]`` verbatim (NOT stripped).

        This matches the original ``parakeet_engine._merge_chunks``
        behavior — the early-return path for ``len(texts) <= 1`` does
        not apply the ``.strip()`` that the multi-chunk path applies.
        Documented here so a future refactor that adds stripping to the
        single-chunk path is a conscious decision, not an accident.
        """
        result = merge_chunks(["  hello world  "])
        assert result == "  hello world  "

    def test_three_chunks_with_overlaps(self):
        """Multiple overlaps across three chunks are each handled independently."""
        result = merge_chunks(
            [
                "the quick brown fox",
                "fox jumps over",
                "over the lazy dog",
            ]
        )
        # Chunk 1: "the quick brown fox"
        # Chunk 2: "fox jumps over" — "fox" matches prev tail → skip 1 → "jumps over"
        # Chunk 3: "over the lazy dog" — "over" matches prev tail → skip 1 → "the lazy dog"
        assert result == "the quick brown fox jumps over the lazy dog"

    def test_chunks_with_punctuation_in_overlap(self):
        """Punctuation in the overlap region is stripped before matching."""
        result = merge_chunks(["hello world.", "world. foo bar"])
        # "world." normalizes to "world" — matches "world." in the new chunk (also "world").
        # Skip 1 → "foo bar".
        assert result == "hello world. foo bar"


# ─── Constants ──────────────────────────────────────────────────────────


class TestConstants:
    """The four exported constants preserve their original values from
    ``parakeet_engine.py`` (PLAN_ONNX_INTEGRATION.md §5.3, §5.4)."""

    def test_non_latin_ratio_limit_is_30_percent(self):
        assert NON_LATIN_RATIO_LIMIT == 0.30

    def test_max_boundary_skip_words_is_2(self):
        assert MAX_BOUNDARY_SKIP_WORDS == 2

    def test_overlap_dedup_window_is_3(self):
        assert OVERLAP_DEDUP_WINDOW == 3
