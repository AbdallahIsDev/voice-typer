"""History search edge-case tests.

Extracted from the original ``tests/test_history_and_models.py`` catch-all
(Epic EC-25 / Entry #23 test-file split). This module pins the behavior of
:meth:`voice_typer.server.history_db.HistoryDB.search` — specifically the
LIKE-escape handling for ``%`` and ``_`` characters and the length cap on
the query string.

Test names + assertions are preserved verbatim from the original file;
only the file boundary moved.
"""

from __future__ import annotations


class TestSearchHistoryEdgeCases:
    """HistoryDB.search edge cases: LIKE-escape and length cap."""

    def test_empty_query_returns_all(self, history_db):
        history_db.add_transcription("First entry")
        history_db.add_transcription("Second entry")
        history_db.flush()
        results = history_db.search("")
        assert len(results) >= 2

    def test_extremely_long_query_does_not_crash(self, history_db):
        history_db.add_transcription("hello world")
        history_db.flush()
        huge = "a" * 10_000_000
        results = history_db.search(huge)
        assert results == []

    def test_literal_percent_in_query_matches_only_exact_text(self, history_db):
        history_db.add_transcription("Progress is 100% complete")
        history_db.add_transcription("Progress is 1000 complete")
        history_db.flush()
        results = history_db.search("100%")
        assert [row["text"] for row in results] == ["Progress is 100% complete"]

    def test_literal_underscore_in_query_matches_only_exact_text(self, history_db):
        history_db.add_transcription("snake_case_token")
        history_db.add_transcription("snakeXcaseXtoken")
        history_db.flush()
        results = history_db.search("snake_case_token")
        assert [row["text"] for row in results] == ["snake_case_token"]
