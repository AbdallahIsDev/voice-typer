"""Combined-alternation regex cache tests for ``text_cleanup``.

Domain: text_cleanup phrase-pattern compilation. The former per-phrase
``_get_compiled_phrase_pattern`` LRU cache was removed (dead on the
production hot path); the live path is now ``_get_phrases_regex``,
which builds a single ``re.compile(r"(?:p1|p2|...)", re.IGNORECASE)``
alternation and caches it keyed on the ``_active_phrases`` list object's
identity (``cached_list is _active_phrases``).

These tests verify the live cache contract: repeated calls with the same
``_active_phrases`` list return the SAME ``re.Pattern`` object (not a
fresh compile), and replacing the list invalidates the cache.
"""

from __future__ import annotations

import re


class TestPhraseRegexCache:
    """``_get_phrases_regex`` memoises the combined-alternation regex."""

    def test_pattern_is_cached(self):
        """Two calls with the same ``_active_phrases`` list return the
        same ``re.Pattern`` object (no rebuild)."""
        from voice_typer.server.text_cleanup import _engine as text_cleanup

        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = [("alpha", "A"), ("beta", "B")]
            text_cleanup._phrases_re_cache = (None, None, {})
            p1, lookup1 = text_cleanup._get_phrases_regex()
            p2, lookup2 = text_cleanup._get_phrases_regex()
            assert p1 is p2
            assert lookup1 is lookup2
            assert isinstance(p1, re.Pattern)
            assert lookup1 == {"alpha": "A", "beta": "B"}
        finally:
            text_cleanup._active_phrases = saved
            text_cleanup._phrases_re_cache = (None, None, {})

    def test_cache_invalidates_on_list_replace(self):
        """Replacing ``_active_phrases`` with a new list object rebuilds
        the regex on the next call (identity-based invalidation)."""
        from voice_typer.server.text_cleanup import _engine as text_cleanup

        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = [("alpha", "A")]
            text_cleanup._phrases_re_cache = (None, None, {})
            p1, _ = text_cleanup._get_phrases_regex()

            # Replace with a NEW list object — different identity, so
            # the cache must rebuild.
            text_cleanup._active_phrases = [("beta", "B")]
            p2, lookup2 = text_cleanup._get_phrases_regex()

            assert p1 is not p2
            assert lookup2 == {"beta": "B"}
        finally:
            text_cleanup._active_phrases = saved
            text_cleanup._phrases_re_cache = (None, None, {})
