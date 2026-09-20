"""Domain: history database, typed exceptions, retention (favorites"""

from __future__ import annotations

import sqlite3

import pytest


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


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "perf_fixes_test.db")
    yield db_instance
    db_instance.close()


class TestTimestampIdCoveringIndex:
    """``idx_timestamp_id`` must exist after schema init."""

    def test_idx_timestamp_id_exists_after_init(self, db):
        """The composite covering index must be created by init_schema."""
        conn = db._get_read_conn()
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_timestamp_id'").fetchall()
        assert len(rows) == 1, (
            f"idx_timestamp_id index must exist after schema init; found indexes: {[r[0] for r in rows]}"
        )

    def test_idx_timestamp_id_is_composite_timestamp_id(self, db):
        """The index must be on (timestamp DESC, id DESC)."""
        conn = db._get_read_conn()
        # PRAGMA index_info gives column indices into the table; we need
        rows = conn.execute("PRAGMA index_xinfo('idx_timestamp_id')").fetchall()
        # Each row: (seqno, cid, name, desc, coll, key)
        key_cols = [(r[2], r[3]) for r in rows if r[5] == 1]
        assert key_cols == [("timestamp", 1), ("id", 1)], (
            f"idx_timestamp_id must be ON (timestamp DESC, id DESC); got key cols: {key_cols}"
        )

    def test_idx_timestamp_id_is_idempotent_rebuild(self, tmp_path):
        """Re-running schema init on an existing DB must not error and"""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "idempotent.db"
        db1 = HistoryDB(db_path=db_path)
        db1.add_transcription("first")
        db1.flush()
        db1.close()
        # Re-open, init_schema runs again on the existing DB file.
        db2 = HistoryDB(db_path=db_path)
        try:
            conn = db2._get_read_conn()
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_timestamp_id'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            db2.close()


class TestOffsetGuard:
    """``get_recent``, ``search`` and ``get_favorites`` must reject deep"""

    @staticmethod
    def _seed_rows(db, n):
        for i in range(n):
            db.add_transcription(f"entry {i}")
        db.flush()

    def test_get_recent_offset_under_1000_works(self, db):
        """OFFSET < 1000 must work (the guard is < 1000, not <= 1000)."""
        self._seed_rows(db, 5)
        rows = db.get_recent(limit=1, offset=4)
        assert len(rows) == 1
        assert rows[0]["text"] == "entry 0"  # oldest, last in DESC order

    def test_get_recent_offset_1000_raises_assertion(self, db):
        """OFFSET == 1000 must raise (forces cursor migration)."""
        from voice_typer.server.history_db import HistoryDBError

        self._seed_rows(db, 1)
        with pytest.raises(HistoryDBError, match="offset <= 999"):
            db.get_recent(limit=1, offset=1000, raise_on_error=True)

    def test_get_recent_offset_above_1000_raises_assertion(self, db):
        """OFFSET > 1000 must raise."""
        from voice_typer.server.history_db import HistoryDBError

        self._seed_rows(db, 1)
        with pytest.raises(HistoryDBError, match="offset <= 999"):
            db.get_recent(limit=1, offset=5000, raise_on_error=True)

    def test_get_recent_cursor_path_bypasses_offset_guard(self, db):
        """Cursor pagination (before_timestamp + before_id) must NOT"""
        self._seed_rows(db, 10)
        first_page = db.get_recent(limit=5)
        last_row = first_page[-1]
        # Cursor past the first 5 rows, no offset, no assertion.
        second_page = db.get_recent(
            limit=5,
            before_timestamp=last_row["timestamp"],
            before_id=last_row["id"],
        )
        assert len(second_page) == 5

    def test_search_offset_1000_raises_assertion(self, db):
        """``search`` OFFSET path must also reject deep OFFSET."""
        from voice_typer.server.history_db import HistoryDBError

        db.add_transcription("hello world")
        db.flush()
        with pytest.raises(HistoryDBError, match="offset <= 999"):
            db.search("hello", limit=1, offset=1000, raise_on_error=True)

    def test_search_cursor_path_bypasses_offset_guard(self, db):
        """``search`` cursor path must NOT be subject to the OFFSET guard."""
        for i in range(10):
            db.add_transcription(f"hello {i}")
        db.flush()
        first_page = db.search("hello", limit=5)
        last_row = first_page[-1]
        second_page = db.search(
            "hello",
            limit=5,
            before_timestamp=last_row["timestamp"],
            before_id=last_row["id"],
        )
        assert len(second_page) == 5

    def test_get_favorites_offset_1000_raises_assertion(self, db):
        """``get_favorites`` OFFSET path must reject deep OFFSET too —"""
        from voice_typer.server.history_db import HistoryDBError

        row_id = db.add_transcription("fav entry")
        db.flush()
        db.toggle_favorite(row_id)
        db.flush()
        with pytest.raises(HistoryDBError, match="offset <= 999"):
            db.get_favorites(limit=1, offset=1000, raise_on_error=True)
        with pytest.raises(HistoryDBError, match="offset <= 999"):
            db.get_favorites(limit=1, offset=10_000_000, raise_on_error=True)

    def test_get_favorites_shallow_offset_still_works(self, db):
        """The new guard must not change shallow-offset behavior."""
        for i in range(5):
            db.add_transcription(f"fav {i}")
        db.flush()
        for rec in db.get_recent(limit=50):
            db.toggle_favorite(rec["id"])
        db.flush()
        rows = db.get_favorites(limit=1, offset=4)
        assert len(rows) == 1
        assert rows[0]["text"] == "fav 0"  # oldest, last in DESC order

    def test_get_favorites_cursor_path_bypasses_offset_guard(self, db):
        """``get_favorites`` cursor path must NOT be subject to the"""
        for i in range(10):
            db.add_transcription(f"fav {i}")
        db.flush()
        for rec in db.get_recent(limit=50):
            db.toggle_favorite(rec["id"])
        db.flush()
        first_page = db.get_favorites(limit=5)
        last_row = first_page[-1]
        second_page = db.get_favorites(
            limit=5,
            before_timestamp=last_row["timestamp"],
            before_id=last_row["id"],
        )
        assert len(second_page) == 5

    def test_get_recent_like_path_offset_guard_via_search(self, db):
        """``search``'s LIKE-fallback OFFSET branch is guarded by the"""
        from voice_typer.server.history_db import HistoryDBError

        db.add_transcription("plain text entry")
        db.flush()
        with pytest.raises(HistoryDBError, match="offset <= 999"):
            db.search("%", limit=1, offset=1000, raise_on_error=True)


class TestFtsLimitPushDown:
    """``search`` must push LIMIT into the FTS5 subquery on the no-cursor path."""

    @pytest.fixture
    def seeded_db(self, tmp_path):
        """Create a DB with many FTS matches for 'commonword'."""
        from voice_typer.server.history_db import HistoryDB

        db_instance = HistoryDB(db_path=tmp_path / "fts_pushdown.db")
        for i in range(100):
            db_instance.add_transcription(f"commonword entry number {i}")
        db_instance.flush()
        yield db_instance
        db_instance.close()

    def test_search_returns_correct_results_with_pushdown(self, seeded_db):
        """The push-down must not change the visible results, top-N by"""
        results = seeded_db.search("commonword", limit=10)
        assert len(results) == 10
        # Results must be ordered by id DESC (autoincrement, all same
        ids = [r["id"] for r in results]
        assert ids == sorted(ids, reverse=True), f"Results must be in id DESC order; got {ids}"

    def test_search_respects_limit_with_pushdown(self, seeded_db):
        """LIMIT must be honoured even when FTS has many more matches."""
        results = seeded_db.search("commonword", limit=5)
        assert len(results) == 5

    def test_search_respects_offset_with_pushdown(self, seeded_db):
        """OFFSET must work with the push-down, page 2 returns the"""
        page1 = seeded_db.search("commonword", limit=5, offset=0)
        page2 = seeded_db.search("commonword", limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        page1_ids = {r["id"] for r in page1}
        page2_ids = {r["id"] for r in page2}
        assert page1_ids.isdisjoint(page2_ids), "Page 1 and page 2 must not overlap"

    def test_search_returns_all_matches_when_limit_exceeds_matches(self, seeded_db):
        """If FTS has fewer matches than ``limit``, return all matches."""
        results = seeded_db.search("commonword", limit=500)
        assert len(results) == 100

    def test_search_cursor_path_still_works(self, seeded_db):
        """Cursor pagination must still work on the FTS path (no push-down)."""
        page1 = seeded_db.search("commonword", limit=10)
        last = page1[-1]
        page2 = seeded_db.search(
            "commonword",
            limit=10,
            before_timestamp=last["timestamp"],
            before_id=last["id"],
        )
        assert len(page2) == 10
        # All page-2 ids must be < last page-1 id (cursor goes older).
        assert all(r["id"] < last["id"] for r in page2)

    def test_search_order_preserved_with_explicit_timestamps(self, tmp_path):
        """The push-down must preserve the (timestamp DESC, id DESC)"""
        from datetime import datetime, timedelta

        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "ordered.db")
        try:
            base = datetime.now()

            # Insert 20 rows where id and timestamp are CORRELATED
            def _do_insert(conn):
                cur = conn.cursor()
                for i in range(1, 21):
                    ts = (base - timedelta(seconds=20 - i)).isoformat()
                    cur.execute(
                        "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                        (f"unique_token entry {i}", ts),
                    )
                conn.commit()

            db._submit_write(_do_insert, wait=True)
            results = db.search("unique_token", limit=10)
            assert len(results) == 10
            # Newest 10 entries: ids 20, 19, ..., 11.
            ids = [r["id"] for r in results]
            assert ids == list(range(20, 10, -1)), f"Expected ids 20..11 DESC; got {ids}"
        finally:
            db.close()


class TestDelegationSplit:
    """history_db.py must delegate to history_db_internals.search."""

    def test_module_level_helpers_are_reexported_from_search(self):
        """``history_db._is_fts_compatible_query`` etc. must be the SAME"""
        from voice_typer.server import history_db
        from voice_typer.server.history_db_internals import search

        assert history_db._is_fts_compatible_query is search.is_fts_compatible_query
        assert history_db._prepare_like_search_pattern is search.prepare_like_search_pattern
        assert history_db._sanitize_fts_query is search.sanitize_fts_query
        assert history_db._project_text_row is search.project_text_row

    def test_reexported_helpers_behave_identically(self):
        """The re-exported helpers must produce identical results to the"""
        from voice_typer.server import history_db
        from voice_typer.server.history_db_internals import search

        assert history_db._is_fts_compatible_query("hello") is True
        assert history_db._is_fts_compatible_query("%") is False
        assert history_db._sanitize_fts_query("hello") == '"hello"'
        assert history_db._prepare_like_search_pattern("a%b") == r"%a\%b%"
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT 1 as id, 'x' as text, 5 as text_full_length").fetchone()
        projected = history_db._project_text_row(row)
        # _HISTORY_TEXT_PREVIEW_LENGTH is 500, so 5 is not truncated.
        assert projected["text_truncated"] is False
        assert projected["text_full_length"] == 5
        # Same object produces same result.
        assert search.project_text_row(row) == projected

    def test_get_recent_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.get_recent`` must call"""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}

        original = search.get_recent

        def _spy(db_ref, *args, **kwargs):
            called["count"] += 1
            return original(db_ref, *args, **kwargs)

        monkeypatch.setattr(search, "get_recent", _spy)
        try:
            db.add_transcription("x")
            db.flush()
            db.get_recent(limit=5)
        finally:
            monkeypatch.setattr(search, "get_recent", original)
        assert called["count"] == 1, "get_recent must delegate to history_db_internals.search.get_recent"

    def test_search_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.search`` must delegate to"""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.search

        def _spy(db_ref, *args, **kwargs):
            called["count"] += 1
            return original(db_ref, *args, **kwargs)

        monkeypatch.setattr(search, "search", _spy)
        try:
            db.add_transcription("hello")
            db.flush()
            db.search("hello")
        finally:
            monkeypatch.setattr(search, "search", original)
        assert called["count"] == 1

    def test_get_today_stats_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.get_today_stats`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.get_today_stats

        def _spy(db_ref):
            called["count"] += 1
            return original(db_ref)

        monkeypatch.setattr(search, "get_today_stats", _spy)
        try:
            db.get_today_stats()
        finally:
            monkeypatch.setattr(search, "get_today_stats", original)
        assert called["count"] == 1

    def test_get_history_count_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.get_history_count`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.get_history_count

        def _spy(db_ref, **kwargs):
            called["count"] += 1
            return original(db_ref, **kwargs)

        monkeypatch.setattr(search, "get_history_count", _spy)
        try:
            db.get_history_count()
        finally:
            monkeypatch.setattr(search, "get_history_count", original)
        assert called["count"] == 1

    def test_get_favorites_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.get_favorites`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.get_favorites

        def _spy(db_ref, *args, **kwargs):
            called["count"] += 1
            return original(db_ref, *args, **kwargs)

        monkeypatch.setattr(search, "get_favorites", _spy)
        try:
            db.get_favorites()
        finally:
            monkeypatch.setattr(search, "get_favorites", original)
        assert called["count"] == 1

    def test_get_latest_text_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.get_latest_text`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.get_latest_text

        def _spy(db_ref):
            called["count"] += 1
            return original(db_ref)

        monkeypatch.setattr(search, "get_latest_text", _spy)
        try:
            db.get_latest_text()
        finally:
            monkeypatch.setattr(search, "get_latest_text", original)
        assert called["count"] == 1

    def test_get_transcription_text_delegates_to_search_module(self, db, monkeypatch):
        """``HistoryDB.get_transcription_text`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.get_transcription_text

        def _spy(db_ref, transcription_id, **kwargs):
            called["count"] += 1
            return original(db_ref, transcription_id, **kwargs)

        monkeypatch.setattr(search, "get_transcription_text", _spy)
        try:
            db.get_transcription_text(1)
        finally:
            monkeypatch.setattr(search, "get_transcription_text", original)
        assert called["count"] == 1

    def test_invalidate_today_stats_cache_delegates(self, db, monkeypatch):
        """``_invalidate_today_stats_cache`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.invalidate_today_stats_cache

        def _spy(db_ref):
            called["count"] += 1
            original(db_ref)

        monkeypatch.setattr(search, "invalidate_today_stats_cache", _spy)
        try:
            db._invalidate_today_stats_cache()
        finally:
            monkeypatch.setattr(search, "invalidate_today_stats_cache", original)
        assert called["count"] == 1

    def test_invalidate_history_count_cache_delegates(self, db, monkeypatch):
        """``_invalidate_history_count_cache`` must delegate."""
        from voice_typer.server.history_db_internals import search

        called = {"count": 0}
        original = search.invalidate_history_count_cache

        def _spy(db_ref):
            called["count"] += 1
            original(db_ref)

        monkeypatch.setattr(search, "invalidate_history_count_cache", _spy)
        try:
            db._invalidate_history_count_cache()
        finally:
            monkeypatch.setattr(search, "invalidate_history_count_cache", original)
        assert called["count"] == 1


class TestTodayStatsTimezoneQueryPreserved:
    """The timezone-aware ``DATETIME('now', 'localtime', 'start of day', 'utc')``"""

    def test_today_stats_uses_timezone_aware_query(self, db, monkeypatch):
        """``DATETIME('now', 'localtime', 'start of day', 'utc')`` query,"""
        db.add_transcription("today's entry")
        db.flush()
        # Invalidate cache so the next call actually runs the SQL.
        db._invalidate_today_stats_cache()

        # Wrap _get_read_conn so the returned connection's cursor
        real_get_read_conn = db._get_read_conn
        executed_sqls: list[str] = []

        class _CapturingCursor:
            def __init__(self, real_cursor):
                self._real = real_cursor

            def execute(self, sql, *args, **kwargs):
                executed_sqls.append(sql)
                return self._real.execute(sql, *args, **kwargs)

            def fetchone(self, *a, **kw):
                return self._real.fetchone(*a, **kw)

            def fetchall(self, *a, **kw):
                return self._real.fetchall(*a, **kw)

            def close(self):
                return self._real.close()

        class _CapturingConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def cursor(self):
                return _CapturingCursor(self._real.cursor())

            def execute(self, sql, *args, **kwargs):
                return self._real.execute(sql, *args, **kwargs)

        def _wrapped_get_read_conn():
            return _CapturingConn(real_get_read_conn())

        monkeypatch.setattr(db, "_get_read_conn", _wrapped_get_read_conn)
        db.get_today_stats()

        # Find the today-stats SQL among the captured calls.
        today_stats_sqls = [s for s in executed_sqls if "COUNT" in s and "transcriptions" in s]
        assert today_stats_sqls, (
            f"get_today_stats must execute a COUNT(*) FROM transcriptions query; captured SQLs: {executed_sqls}"
        )
        sql = today_stats_sqls[0]
        assert "DATETIME('now', 'localtime', 'start of day', 'utc')" in sql, (
            "get_today_stats must use the timezone-aware "
            "DATETIME('now', 'localtime', 'start of day', 'utc') query, "
            f"not the old DATE('now') UTC-only query. Got: {sql}"
        )
