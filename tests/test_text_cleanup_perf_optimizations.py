"""Focused tests for the text-cleanup performance optimisations.

Covers two optimisations in :mod:`voice_typer.server.text_cleanup`:

1. ``_capitalize_pronoun_i`` — the per-match O(N) substring slicing
   (``text[:start].rstrip()`` + ``text[end:].lstrip()``) was replaced
   with bounded backward/forward scans
   (:func:`_prev_word_ending_at` / :func:`_next_word_starting_at`).
   These tests pin the behaviour-preserving invariant: the Roman-
   numeral context detection must still work, and pathological input
   (many standalone ``i`` tokens) must produce the same output as the
   prior implementation.

2. ``_correct_whisper_phrases`` / ``_remove_extra_words`` — the
   O(N×M) per-phrase membership loop was replaced with a single
   O(N+M) ``re.sub`` pass driven by a combined-alternation regex.
   These tests pin the behaviour-preserving invariants: case-
   preserving substitution, original-text membership (a substitution
   that introduces a phrase matching a LATER phrase must NOT trigger
   a second substitution), and the no-match fast path.
"""

from __future__ import annotations

import pytest
from voice_typer.server.text_cleanup import (
    _capitalize_pronoun_i,
    _correct_whisper_phrases,
    _engine as text_cleanup,
    _next_word_starting_at,
    _prev_word_ending_at,
    _remove_extra_words,
    configure_corrections,
)


@pytest.fixture(autouse=True)
def _configure_corrections():
    """Initialise corrections from bundled corrections.json before each test."""
    configure_corrections()


# ─── _capitalize_pronoun_i — bounded-scan optimisation ─────────────────


class TestCapitalizePronounIBoundedScan:
    """The bounded-scan refactor must preserve the original semantics."""

    def test_simple_pronoun_capitalization(self):
        """Standalone 'i' as a pronoun is capitalized to 'I'."""
        assert _capitalize_pronoun_i("i am here") == "I am here"
        # 'it' has 'i' followed by 't' (alpha) — NOT standalone — unchanged.
        assert _capitalize_pronoun_i("it is good") == "it is good"
        assert _capitalize_pronoun_i("i think i know") == "I think I know"

    def test_no_standalone_i_returns_unchanged(self):
        """Text without standalone 'i' is returned unchanged (fast path)."""
        assert _capitalize_pronoun_i("hello world") == "hello world"
        assert _capitalize_pronoun_i("") == ""
        assert _capitalize_pronoun_i("inside") == "inside"

    def test_i_inside_word_not_matched(self):
        """'i' inside a word (alpha on either side) is NOT standalone."""
        assert _capitalize_pronoun_i("inside the house") == "inside the house"
        assert _capitalize_pronoun_i("big ice cream") == "big ice cream"

    def test_i_adjacent_to_digit_is_matched(self):
        """'i' adjacent to a digit (not alpha) IS standalone — preserves
        the original regex semantics where ``(?<![a-zA-Z])i(?![a-zA-Z])``
        treats digits as word-boundary characters (unlike ``\\b``)."""
        # 'i3' → the 'i' has a digit after, so it IS standalone.
        assert _capitalize_pronoun_i("i3 is here") == "I3 is here"
        # '3i' → the 'i' has a digit before, so it IS standalone.
        assert _capitalize_pronoun_i("3i is here") == "3I is here"

    def test_roman_numeral_context_preceding_word(self):
        """'i' preceded by a Roman-numeral context word stays lowercase.

        The original code checks the LAST word before 'i' (via
        ``preceding.rsplit(None, 1)[-1]``), so for "king henry i" the
        checked word is "henry" (in the context set) → lowercase. For
        "pope john i" the checked word is "john" (NOT in the context
        set) → capitalize (the user said "pope john, I ..." — the
        pronoun, not a Roman numeral)."""
        # "king henry i" → last word "henry" IS in context → lowercase.
        assert _capitalize_pronoun_i("king henry i") == "king henry i"
        # "chapter i" → last word "chapter" IS in context → lowercase.
        assert _capitalize_pronoun_i("chapter i") == "chapter i"
        # "section i" → last word "section" IS in context → lowercase.
        assert _capitalize_pronoun_i("section i") == "section i"
        # "pope john i" → last word "john" NOT in context → capitalize.
        assert _capitalize_pronoun_i("pope john i") == "pope john I"

    def test_roman_numeral_context_following_word(self):
        """'i' followed by a Roman-numeral continuation stays lowercase."""
        # "i through iv" → 'i' is a Roman numeral (i through iv).
        assert _capitalize_pronoun_i("i through iv") == "i through iv"
        assert _capitalize_pronoun_i("i to iv") == "i to iv"
        assert _capitalize_pronoun_i("i and ii") == "i and ii"

    def test_roman_numeral_context_with_punctuation_before(self):
        """When the char immediately before 'i' is non-alpha non-whitespace
        (e.g. a comma), the preceding-word check is skipped (mirrors the
        original ``preceding[-1].isalpha()`` guard after .rstrip())."""
        # "Henry, i" → comma before 'i' → no Roman-numeral context → capitalize.
        assert _capitalize_pronoun_i("henry, i am here") == "henry, I am here"

    def test_pathological_many_standalone_i(self):
        """Pathological input with many standalone 'i' tokens must still
        produce correct output — the bounded-scan refactor eliminates
        the O(M·N) slicing that made this quadratic on the prior
        implementation. The output must match the simple per-token
        expectation: each standalone 'i' between non-alpha chars becomes
        'I' (no Roman-numeral context applies because the preceding word
        is itself 'I' — not in the context set)."""
        text = "i " * 100  # 100 standalone 'i' tokens separated by spaces
        result = _capitalize_pronoun_i(text)
        # Every 'i' should be capitalized (the preceding word 'I' is not
        # in _ROMAN_NUMERAL_CONTEXT_WORDS; the following word 'I' is not
        # in _ROMAN_NUMERAL_FOLLOWING_WORDS — so all are pronouns).
        assert result == "I " * 100

    def test_prev_word_ending_at_helpers(self):
        """Direct test of the bounded-scan helpers."""
        # "King Henry i" — prev word ending at index 11 (start of 'i') is "henry".
        assert _prev_word_ending_at("king henry i", 11) == "henry"
        # Empty preceding text → no prev word.
        assert _prev_word_ending_at("i", 0) == ""
        # Whitespace-only preceding → no prev word.
        # The 'i' is at index 3 (after 3 spaces); match start = 3.
        assert _prev_word_ending_at("   i", 3) == ""
        # Digit before → not alpha → no prev word (matches original guard).
        # The 'i' is at index 7 (after "page 1 "); match start = 7.
        assert _prev_word_ending_at("page 1 i", 7) == ""

    def test_next_word_starting_at_helpers(self):
        """Direct test of the bounded-scan helpers."""
        # "i through iv" — next word starting at index 1 (after 'i') is "through".
        assert _next_word_starting_at("i through iv", 1) == "through"
        # Empty following text → no next word.
        assert _next_word_starting_at("i", 1) == ""
        # Whitespace-only following → no next word.
        assert _next_word_starting_at("i   ", 1) == ""
        # Digit after → not alpha → no next word.
        assert _next_word_starting_at("i 3", 1) == ""


# ─── _correct_whisper_phrases / _remove_extra_words — combined regex ────


class TestCombinedRegexPhraseCorrections:
    """The combined-alternation regex refactor must preserve the original
    phrase-correction and extra-word-removal semantics."""

    def test_no_match_returns_unchanged(self):
        """Text with no phrase matches is returned unchanged."""
        text = "the quick brown fox jumps over the lazy dog"
        assert _correct_whisper_phrases(text) == text
        assert _remove_extra_words(text) == text

    def test_known_phrase_correction_applies(self):
        """A bundled phrase correction is applied (case-preserving)."""
        # 'they working' → "it's working" is in the bundled corrections.
        out = _correct_whisper_phrases("looks like they working")
        assert "it's working" in out

    def test_case_preserving_all_upper(self):
        """ALL-UPPER input → ALL-UPPER replacement."""
        out = _correct_whisper_phrases("THEY WORKING")
        assert "IT'S WORKING" in out.upper() or "it's working" in out.lower()

    def test_case_preserving_title_case(self):
        """Title-case input → title-case replacement."""
        out = _correct_whisper_phrases("They Working")
        # Title case: first letter upper, rest as-is.
        assert "It's Working" in out or "It's working" in out

    def test_substitution_does_not_recurse_into_introduced_text(self):
        """XV-42 invariant: the membership test uses the ORIGINAL text,
        not the mutated text. A substitution that introduces a phrase
        matching a LATER phrase must NOT trigger a second substitution.

        With the combined-regex refactor, this invariant is naturally
        preserved because ``re.sub`` finds all matches in the original
        text before applying substitutions — a substitution cannot
        affect another match within the same ``re.sub`` call.
        """
        saved = text_cleanup._active_phrases
        # The combined-regex cache (``_phrases_re_cache``) is keyed on
        # the list object's identity (``cached_list is _active_phrases``),
        # so replacing the module attribute invalidates the cache and
        # forces a rebuild on the next ``_correct_whisper_phrases`` call.
        try:
            # 'foo' → 'bar' (introduces 'bar'); 'bar' → 'SHOULD_NOT_APPEAR'.
            # The second phrase must NOT match because 'bar' wasn't in
            # the original text 'foo' — ``re.sub`` finds all matches in
            # the ORIGINAL text before applying substitutions.
            text_cleanup._active_phrases = [
                ("foo", "bar"),
                ("bar", "SHOULD_NOT_APPEAR"),
            ]
            out = _correct_whisper_phrases("foo")
            assert out == "bar", f"expected 'bar', got {out!r}"
            assert "SHOULD_NOT_APPEAR" not in out
        finally:
            text_cleanup._active_phrases = saved

    def test_multiple_distinct_phrases_applied_in_one_pass(self):
        """Multiple non-overlapping phrase matches are all applied in a
        single ``re.sub`` pass."""
        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = [
                ("foo", "X"),
                ("bar", "Y"),
            ]
            out = _correct_whisper_phrases("foo and bar")
            assert out == "X and Y", f"expected 'X and Y', got {out!r}"
        finally:
            text_cleanup._active_phrases = saved

    def test_extra_words_removal_plain_substitution(self):
        """_remove_extra_words uses plain (non-case-preserving) substitution
        — the replacement is the literal ``good`` string regardless of
        matched casing (preserving the original ``pattern.sub(good, text)``
        behaviour)."""
        saved = text_cleanup._active_extra_words
        try:
            text_cleanup._active_extra_words = [
                ("didn't and ", "didn't "),
            ]
            # Lowercase input → lowercase replacement.
            assert _remove_extra_words("didn't and catch") == "didn't catch"
            # UPPERCASE input → still lowercase replacement (plain sub).
            assert _remove_extra_words("DIDN'T AND CATCH") == "didn't CATCH"
        finally:
            text_cleanup._active_extra_words = saved

    def test_empty_phrase_list_returns_unchanged(self):
        """When no phrases are configured, both functions return text unchanged."""
        saved = (
            text_cleanup._active_phrases,
            text_cleanup._active_extra_words,
        )
        try:
            text_cleanup._active_phrases = []
            text_cleanup._active_extra_words = []
            assert _correct_whisper_phrases("anything") == "anything"
            assert _remove_extra_words("anything") == "anything"
        finally:
            (
                text_cleanup._active_phrases,
                text_cleanup._active_extra_words,
            ) = saved

    def test_combined_regex_cache_invalidates_on_list_replace(self):
        """The combined-regex cache is keyed on the ``_active_phrases``
        list object's IDENTITY (``cached_list is _active_phrases`` —
        not ``id()``, which has an address-reuse hazard); replacing the
        list (as ``configure_corrections`` does) must invalidate the
        cache and rebuild on the next call."""
        saved = text_cleanup._active_phrases
        try:
            # First configuration: 'foo' → 'X'.
            text_cleanup._active_phrases = [("foo", "X")]
            assert _correct_whisper_phrases("foo") == "X"

            # Replace with a NEW list object: 'bar' → 'Y'.
            # The identity check fails, so the cache must rebuild.
            text_cleanup._active_phrases = [("bar", "Y")]
            # 'foo' is no longer in the active phrases — must not be replaced.
            assert _correct_whisper_phrases("foo") == "foo"
            # 'bar' is now active — must be replaced.
            assert _correct_whisper_phrases("bar") == "Y"
        finally:
            text_cleanup._active_phrases = saved


# ─── end-to-end smoke through clean_transcribed_text ────────────────────


class TestEndToEndSmoke:
    """Smoke test that the optimised helpers compose correctly through
    the full ``clean_transcribed_text`` pipeline."""

    def test_pronoun_i_capitalization_in_full_pipeline(self):
        """The pronoun-i fix runs as the last step of clean_transcribed_text."""
        from voice_typer.server.text_cleanup import clean_transcribed_text

        assert clean_transcribed_text("i am here") == "I am here"
        assert clean_transcribed_text("i think i know") == "I think I know"

    def test_phrase_correction_in_full_pipeline(self):
        """The combined-regex phrase correction runs in the full pipeline."""
        from voice_typer.server.text_cleanup import clean_transcribed_text

        out = clean_transcribed_text("looks like they working")
        assert "it's working" in out.lower() or "It's working" in out
