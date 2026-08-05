"""Tests for ``_correct_whisper_phrases`` compiled-regex pattern cache.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5 / TC-15). Verbatim mechanical move — same test names +
assertions, only the file location changed.
"""

from __future__ import annotations


class TestPhrasePatternCache:
    """_correct_whisper_phrases caches compiled regex patterns."""

    def test_pattern_is_cached(self):
        from voice_typer.server import text_cleanup

        text_cleanup._phrase_pattern_cache.clear()

        p1 = text_cleanup._get_compiled_phrase_pattern("test phrase")
        p2 = text_cleanup._get_compiled_phrase_pattern("test phrase")

        assert p1 is p2
        assert "test phrase" in text_cleanup._phrase_pattern_cache

    def test_distinct_phrases_get_distinct_patterns(self):
        from voice_typer.server import text_cleanup

        text_cleanup._phrase_pattern_cache.clear()
        p1 = text_cleanup._get_compiled_phrase_pattern("alpha")
        p2 = text_cleanup._get_compiled_phrase_pattern("beta")
        assert p1 is not p2
