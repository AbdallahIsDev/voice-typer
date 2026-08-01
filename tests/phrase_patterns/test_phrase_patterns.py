"""Phrase-pattern cache tests split out of the former ``tests/test_history_and_models.py``.

Domain: text_cleanup phrase-pattern compilation —
``_get_compiled_phrase_pattern`` memoises compiled regex objects so
repeated calls with the same phrase return the SAME Pattern object
(not a fresh compile).

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
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
