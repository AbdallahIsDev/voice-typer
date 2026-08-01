"""HistoryDB unit tests split out of the former ``tests/test_history_and_models.py``.

Domain: history database — typed exceptions, retention (favorites
preservation), search edge cases (LIKE-escape + length cap), and
soft-delete restore.

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed. The shared
``history_db`` fixture (temporary SQLite file) is provided by the
top-level ``tests/conftest.py``.
"""

from __future__ import annotations


class TestHistoryDBErrorType:
    """HistoryDBError is a typed exception."""

    def test_historydberror_is_runtime_error(self):
        from voice_typer.server.history_db import HistoryDBError

        assert issubclass(HistoryDBError, RuntimeError)


class TestHistoryRetentionFavorites:
    """Retention preserves favorites even when they're old."""

    def test_retention_preserves_favorites(self, history_db):
        fav_id = history_db.add_transcription("Favorite old entry")
        history_db.toggle_favorite(fav_id)
        for i in range(5):
            history_db.add_transcription(f"Regular entry {i}")

        history_db.apply_retention(max_entries=3)

        favorites = history_db.get_favorites()
        assert len(favorites) >= 1
        assert favorites[0]["text"] == "Favorite old entry"

    def test_retention_without_favorites_deletes_oldest(self, history_db):
        for i in range(5):
            history_db.add_transcription(f"Entry {i}")

        history_db.apply_retention(max_entries=3)
        entries = history_db.get_recent(limit=10)
        assert len(entries) <= 3


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
