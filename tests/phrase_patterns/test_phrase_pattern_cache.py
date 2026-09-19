"""Tests for the combined-alternation regex cache in ``text_cleanup``."""

from __future__ import annotations

import re


class TestPhraseRegexCache:
    """``_get_phrases_regex`` memoises the combined-alternation regex."""

    def test_pattern_is_cached(self):
        """Two calls with the same ``_active_phrases`` list return the"""
        from voice_typer.server.text_cleanup import _engine as text_cleanup

        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = [("alpha", "A"), ("beta", "B")]
            # Reset the cache so we know the first call builds it.
            text_cleanup._phrases_re_cache = (None, None, {})
            p1, lookup1 = text_cleanup._get_phrases_regex()
            p2, lookup2 = text_cleanup._get_phrases_regex()
            assert p1 is p2
            assert lookup1 is lookup2
            assert isinstance(p1, re.Pattern)
            assert lookup1 == {"alpha": "A", "beta": "B"}
        finally:
            text_cleanup._active_phrases = saved
            # Invalidate the cache so subsequent tests don't see our
            text_cleanup._phrases_re_cache = (None, None, {})

    def test_cache_invalidates_on_list_replace(self):
        """Replacing ``_active_phrases`` with a new list object rebuilds"""
        from voice_typer.server.text_cleanup import _engine as text_cleanup

        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = [("alpha", "A")]
            text_cleanup._phrases_re_cache = (None, None, {})
            p1, _ = text_cleanup._get_phrases_regex()

            text_cleanup._active_phrases = [("beta", "B")]
            p2, lookup2 = text_cleanup._get_phrases_regex()

            assert p1 is not p2
            assert lookup2 == {"beta": "B"}
        finally:
            text_cleanup._active_phrases = saved
            text_cleanup._phrases_re_cache = (None, None, {})

    def test_empty_phrase_list_returns_none(self):
        """An empty ``_active_phrases`` short-circuits to ``(None, {})``."""
        from voice_typer.server.text_cleanup import _engine as text_cleanup

        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = []
            text_cleanup._phrases_re_cache = (None, None, {})
            pattern, lookup = text_cleanup._get_phrases_regex()
            assert pattern is None
            assert lookup == {}
        finally:
            text_cleanup._active_phrases = saved
            text_cleanup._phrases_re_cache = (None, None, {})
