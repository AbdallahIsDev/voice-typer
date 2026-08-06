"""Tests for the combined-alternation regex cache in ``text_cleanup``.

The former per-phrase ``_phrase_pattern_cache`` / ``_get_compiled_phrase_pattern``
LRU cache was removed (dead on the production hot path — the hot path now
goes through ``_get_phrases_regex``). These tests were rewritten to cover
the live cache contract:

* ``_get_phrases_regex`` returns the SAME compiled ``re.Pattern`` object
  across calls when ``_active_phrases`` has not been replaced.
* Replacing ``_active_phrases`` with a new list object (as
  ``configure_corrections`` does) invalidates the cache so the next call
  rebuilds the regex from the new list.

The cache is keyed on the list object's IDENTITY (``cached_list is
_active_phrases``), not ``id()``, to avoid the id-reuse hazard where a
GC'd list's address is reused for a new list with different contents.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5 / TC-15).
"""

from __future__ import annotations

import re


class TestPhraseRegexCache:
    """``_get_phrases_regex`` memoises the combined-alternation regex."""

    def test_pattern_is_cached(self):
        """Two calls with the same ``_active_phrases`` list return the
        same ``re.Pattern`` object (no rebuild)."""
        from voice_typer.server import text_cleanup

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
            # throwaway list (the cached_list is ``is``-compared, so
            # restoring ``saved`` already invalidates it — but reset
            # explicitly for clarity).
            text_cleanup._phrases_re_cache = (None, None, {})

    def test_cache_invalidates_on_list_replace(self):
        """Replacing ``_active_phrases`` with a new list object rebuilds
        the regex on the next call (identity-based invalidation)."""
        from voice_typer.server import text_cleanup

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

    def test_empty_phrase_list_returns_none(self):
        """An empty ``_active_phrases`` short-circuits to ``(None, {})``."""
        from voice_typer.server import text_cleanup

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
