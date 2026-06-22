"""Tests for voice_typer.history_db — SQLite history, favorites, retention."""

import pytest
from pathlib import Path


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB
    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


class TestHistoryDBSchema:
    def test_schema_has_transcriptions_table(self, db):
        import sqlite3
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions'")
        assert cursor.fetchone() is not None

    def test_schema_has_favorite_column(self, db):
        import sqlite3
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "favorite" in columns

    def test_schema_has_language_column(self, db):
        import sqlite3
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "language" in columns


class TestHistoryDBCRUD:
    def test_add_transcription(self, db):
        row_id = db.add_transcription("Hello world", duration=2.5, model="small.en", device="cpu")
        assert row_id > 0

    def test_get_recent(self, db):
        db.add_transcription("First")
        db.add_transcription("Second")
        entries = db.get_recent(limit=10)
        assert len(entries) >= 2

    def test_search(self, db):
        db.add_transcription("The quick brown fox")
        db.add_transcription("Hello world")
        results = db.search("quick")
        assert len(results) == 1
        assert "quick" in results[0]["text"]

    def test_search_treats_like_wildcards_as_literals(self, db):
        db.add_transcription("Progress is 100% complete")
        db.add_transcription("plain text without percent")
        db.add_transcription("snake_case_token")
        db.add_transcription("snake case token")

        percent_results = db.search("%")
        underscore_results = db.search("_")

        assert [row["text"] for row in percent_results] == ["Progress is 100% complete"]
        assert [row["text"] for row in underscore_results] == ["snake_case_token"]

    def test_search_caps_query_at_200_characters(self, db):
        db.add_transcription("a" * 200)

        results = db.search(("a" * 200) + "b")

        assert [row["text"] for row in results] == ["a" * 200]

    def test_delete(self, db):
        row_id = db.add_transcription("To delete")
        assert db.delete(row_id) is True

    def test_delete_nonexistent(self, db):
        assert db.delete(999999) is False

    def test_clear_all(self, db):
        db.add_transcription("A")
        db.add_transcription("B")
        assert db.clear_all() is True
        assert len(db.get_recent()) == 0


class TestHistoryDBFavorites:
    def test_toggle_favorite(self, db):
        row_id = db.add_transcription("Favorite me")
        result = db.toggle_favorite(row_id)
        assert result is True

    def test_get_favorites(self, db):
        row_id = db.add_transcription("Fav entry")
        db.toggle_favorite(row_id)
        favs = db.get_favorites()
        assert len(favs) == 1

    def test_non_favorite_not_in_get_favorites(self, db):
        db.add_transcription("Regular entry")
        favs = db.get_favorites()
        assert len(favs) == 0


class TestHistoryDBRetention:
    def test_retention_by_max_entries(self, db):
        for i in range(5):
            db.add_transcription(f"Entry {i}")
        deleted = db.apply_retention(max_entries=3)
        assert deleted >= 2
        entries = db.get_recent(limit=10)
        assert len(entries) <= 3

    def test_retention_preserves_favorites(self, db):
        row_id = db.add_transcription("Keep me")
        db.toggle_favorite(row_id)
        for i in range(5):
            db.add_transcription(f"Entry {i}")
        db.apply_retention(max_entries=2)
        favs = db.get_favorites()
        assert len(favs) == 1


class TestHistoryDBStats:
    def test_get_stats(self, db):
        db.add_transcription("Hello world")
        stats = db.get_stats()
        assert stats["total_count"] >= 1
        assert stats["total_chars"] > 0

    def test_get_today_stats(self, db):
        db.add_transcription("Today's entry")
        stats = db.get_today_stats()
        assert stats["count"] >= 1


class TestHistoryDBWALMode:
    def test_uses_wal_mode(self, db):
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"


class TestErr013RaiseOnError:
    """ERR-013: history_db methods must raise HistoryDBError when
    ``raise_on_error=True`` so the IPC layer can distinguish "empty
    result" from "operation failed".

    Without this, a DB error returns the same sentinel as a successful
    query that found nothing (e.g. ``[]``), and the renderer cannot
    tell the user what went wrong.
    """

    def test_get_recent_raises_on_error(self, db, monkeypatch):
        """Force _get_conn to raise; assert HistoryDBError propagates."""
        def _boom():
            raise RuntimeError("disk I/O error")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_recent(raise_on_error=True)

    def test_get_recent_returns_sentinel_without_flag(self, db, monkeypatch):
        """Without raise_on_error, the legacy [] sentinel is preserved."""
        def _boom():
            raise RuntimeError("disk I/O error")
        monkeypatch.setattr(db, "_get_conn", _boom)
        assert db.get_recent() == []

    def test_delete_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.delete(1, raise_on_error=True)

    def test_delete_returns_false_without_flag(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        assert db.delete(1) is False

    def test_clear_all_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("read-only")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.clear_all(raise_on_error=True)

    def test_toggle_favorite_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.toggle_favorite(1, raise_on_error=True)

    def test_search_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.search("foo", raise_on_error=True)

    def test_get_favorites_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_favorites(raise_on_error=True)

    def test_get_today_stats_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_today_stats(raise_on_error=True)

    def test_get_stats_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_stats(raise_on_error=True)


class TestSearchEmptyQuery:
    """TEST-031: search_history with query="" exercises a different
    code branch (no % wrap needed) but was untested. Verify it returns
    all rows instead of crashing or returning nothing.
    """

    def test_empty_query_returns_all_rows(self, db):
        db.add_transcription("alpha")
        db.add_transcription("beta")
        db.add_transcription("gamma")
        results = db.search("", limit=50)
        # Empty query → pattern "%%" matches everything.
        assert len(results) == 3

    def test_empty_query_with_zero_results_when_db_empty(self, db):
        results = db.search("", limit=50)
        assert results == []
