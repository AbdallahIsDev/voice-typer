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


class TestHistoryDbRaisesOnErrorWhenFlagSet:
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


class TestDBLockRetry:
    """DB-LOCK-FIX-001 (Round 1): add_transcription must retry on
    SQLITE_BUSY/SQLITE_LOCKED with exponential backoff instead of
    failing hard after the busy_timeout wait.

    These tests verify the retry helper (_exec_with_retry) recovers
    from transient lock errors and re-raises non-lock errors immediately.
    """

    def test_retry_recovers_from_transient_lock(self, db, monkeypatch):
        """First two attempts raise SQLITE_BUSY; third succeeds."""
        import sqlite3
        import voice_typer.server.history_db as hdb_mod

        call_count = {"n": 0}

        def flaky_fn():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        # Patch time.sleep to avoid real delays in tests
        monkeypatch.setattr(hdb_mod.time, "sleep", lambda _: None)
        result = db._exec_with_retry(flaky_fn, max_attempts=5, base_delay=0.001)
        assert result == "success"
        assert call_count["n"] == 3

    def test_retry_raises_after_max_attempts(self, db, monkeypatch):
        """All attempts raise SQLITE_BUSY → re-raises after max_attempts."""
        import sqlite3
        import voice_typer.server.history_db as hdb_mod

        def always_locked():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(hdb_mod.time, "sleep", lambda _: None)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db._exec_with_retry(always_locked, max_attempts=3, base_delay=0.001)

    def test_retry_does_not_retry_non_lock_errors(self, db):
        """Non-lock OperationalErrors are re-raised immediately (no retry)."""
        import sqlite3

        call_count = {"n": 0}

        def non_lock_error():
            call_count["n"] += 1
            raise sqlite3.OperationalError("no such table: foo")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._exec_with_retry(non_lock_error, max_attempts=5, base_delay=0.001)
        assert call_count["n"] == 1  # no retry

    def test_retry_zero_overhead_on_success(self, db):
        """Common case: first attempt succeeds, no retry."""
        call_count = {"n": 0}

        def success_fn():
            call_count["n"] += 1
            return "ok"

        result = db._exec_with_retry(success_fn, max_attempts=5, base_delay=0.001)
        assert result == "ok"
        assert call_count["n"] == 1

    def test_add_transcription_retries_on_lock(self, db, monkeypatch):
        """End-to-end: add_transcription recovers from a transient lock.
        Uses a CursorProxy wrapper to inject flaky behavior on the
        first INSERT attempt (sqlite3.Cursor attributes are read-only
        so direct monkeypatch is impossible)."""
        import sqlite3
        import voice_typer.server.history_db as hdb_mod

        real_conn = db._get_conn()
        call_count = {"n": 0}

        class CursorProxy:
            """Transparent proxy that intercepts execute() to inject errors."""
            def __init__(self, real):
                self._real = real
            def execute(self, sql, parameters=()):
                if "INSERT" in sql.upper():
                    call_count["n"] += 1
                    if call_count["n"] < 2:
                        raise sqlite3.OperationalError("database is locked")
                return self._real.execute(sql, parameters)
            def fetchone(self):
                return self._real.fetchone()
            def fetchall(self):
                return self._real.fetchall()
            @property
            def lastrowid(self):
                return self._real.lastrowid
            @property
            def rowcount(self):
                return self._real.rowcount
            def close(self):
                return self._real.close()

        class ConnectionProxy:
            def __init__(self, real):
                self._real = real
            def execute(self, sql, parameters=()):
                return self._real.execute(sql, parameters)
            def cursor(self):
                return CursorProxy(self._real.cursor())
            def commit(self):
                return self._real.commit()
            def close(self):
                return self._real.close()
            @property
            def row_factory(self):
                return self._real.row_factory
            @row_factory.setter
            def row_factory(self, v):
                self._real.row_factory = v

        proxy = ConnectionProxy(real_conn)
        monkeypatch.setattr(db, "_get_conn", lambda: proxy)
        monkeypatch.setattr(hdb_mod.time, "sleep", lambda _: None)

        row_id = db.add_transcription("test text")
        assert row_id > 0
        assert call_count["n"] >= 2  # at least one retry


class TestChunkedRetention:
    """DB-LOCK-FIX-002 (Round 1): apply_retention must chunk deletes into
    batches of 100, committing after each batch so the write lock is
    released between batches and other writers can interleave.
    """

    def _make_commit_counting_proxy(self, real_conn):
        """Wrap a real sqlite3.Connection in a proxy that counts commit() calls.
        sqlite3.Connection attributes are read-only, so we use __getattr__
        delegation."""
        commit_count = {"n": 0}

        class CommitCountingProxy:
            def __init__(self, real):
                self._real = real
            def execute(self, sql, parameters=()):
                return self._real.execute(sql, parameters)
            def cursor(self):
                return self._real.cursor()
            def commit(self):
                commit_count["n"] += 1
                return self._real.commit()
            def close(self):
                return self._real.close()
            @property
            def row_factory(self):
                return self._real.row_factory
            @row_factory.setter
            def row_factory(self, v):
                self._real.row_factory = v
            def __getattr__(self, name):
                return getattr(self._real, name)

        return CommitCountingProxy(real_conn), commit_count

    def test_retention_deletes_all_old_entries_in_batches(self, db, monkeypatch):
        """Insert 250 old entries; apply_retention(days=1) should delete
        all 250 in 3 batches (100+100+50) and release the lock between
        each batch (verifiable via commit count)."""
        from datetime import datetime, timedelta

        # Insert 250 non-favorite entries with old timestamps using the REAL conn
        old_date = (datetime.now() - timedelta(days=30)).isoformat()
        real_conn = db._get_conn()
        cursor = real_conn.cursor()
        for i in range(250):
            cursor.execute(
                "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                (f"old entry {i}", old_date),
            )
        real_conn.commit()

        # Now swap in the counting proxy
        proxy, commit_count = self._make_commit_counting_proxy(real_conn)
        monkeypatch.setattr(db, "_get_conn", lambda: proxy)

        deleted = db.apply_retention(retention_days=1, max_entries=0)

        assert deleted == 250
        # 250 entries / 100 per batch = 3 batches (100+100+50)
        # Each batch commits once → at least 3 commits
        assert commit_count["n"] >= 3

    def test_retention_keeps_favorites(self, db):
        """Favorites are never deleted by retention."""
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=30)).isoformat()
        conn = db._get_conn()
        cursor = conn.cursor()
        # Insert 5 favorite + 5 non-favorite old entries
        for i in range(5):
            cursor.execute(
                "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 1)",
                (f"favorite {i}", old_date),
            )
        for i in range(5):
            cursor.execute(
                "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                (f"non-favorite {i}", old_date),
            )
        conn.commit()

        deleted = db.apply_retention(retention_days=1, max_entries=0)
        assert deleted == 5  # only non-favorites deleted

        # Verify favorites remain
        cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE favorite = 1")
        assert cursor.fetchone()[0] == 5

    def test_retention_max_entries_chunked(self, db, monkeypatch):
        """max_entries retention also chunks properly."""
        real_conn = db._get_conn()
        cursor = real_conn.cursor()
        # Insert 250 entries (all recent, none old)
        for i in range(250):
            cursor.execute(
                "INSERT INTO transcriptions (text, favorite) VALUES (?, 0)",
                (f"entry {i}",),
            )
        real_conn.commit()

        proxy, commit_count = self._make_commit_counting_proxy(real_conn)
        monkeypatch.setattr(db, "_get_conn", lambda: proxy)

        # Keep only 50 → delete 200
        deleted = db.apply_retention(retention_days=0, max_entries=50)
        assert deleted == 200
        # 200 / 100 = 2 batches → at least 2 commits
        assert commit_count["n"] >= 2
