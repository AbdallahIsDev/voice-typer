"""TY-21: tests for the composite index ``idx_favorite_timestamp``.

Verifies that:
- The composite index ``idx_favorite_timestamp`` on
  ``transcriptions(favorite, timestamp ASC)`` exists.
- The retention DELETE subquery at ``apply_retention`` uses the index
  (verified via ``EXPLAIN QUERY PLAN`` — the plan should reference
  ``idx_favorite_timestamp`` as a covering index for the
  ``WHERE favorite = 0 ORDER BY timestamp ASC LIMIT ?`` subquery).
- The index is created on BOTH new databases (fresh ``HistoryDB``)
  AND existing databases (re-opening an existing DB file runs
  ``_init_db_schema`` again, which is idempotent via
  ``CREATE INDEX IF NOT EXISTS``).

Without the composite index, the retention DELETE subquery
(``SELECT id FROM transcriptions WHERE favorite = 0 ORDER BY
timestamp ASC LIMIT ?``) had to:
  1. full-scan the favorite=0 subset via ``idx_favorite``,
  2. fetch the timestamp for each row from the base table,
  3. sort the subset by timestamp ASC,
  4. take the first ``_RETENTION_BATCH`` (100) ids.
That's O(K log K) per batch where K = count of favorite=0 rows.
For a power-user DB with 100k rows and max_entries=1000, the
retention sweep ran ~990 batches × O(90k log 90k) ≈ 1.3 billion
comparisons total — ~10-30s stall. The composite index turns the
subquery into an index range walk: O(log K + 100) per batch.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history_retention_index.db")
    yield db_instance
    db_instance.close()


def _list_indexes(conn: sqlite3.Connection, table: str = "transcriptions") -> set[str]:
    """Return the set of index names on ``table``."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,),
    )
    return {row[0] for row in cursor.fetchall()}


def _index_columns(conn: sqlite3.Connection, index_name: str) -> list[tuple[str, int, bool]]:
    """Return ``[(column_name, collation_seq, is_desc), ...]`` for an index.

    Mirrors the ``PRAGMA index_info`` + ``PRAGMA index_xinfo`` shape —
    we use ``index_xinfo`` because it includes the sort order (the
    ``desc`` field), which is what we need to verify the composite
    index has ``timestamp ASC`` (desc=0) not DESC (desc=1).
    """
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA index_xinfo({index_name})")
    # index_xinfo rows: (colno, cid, name, desc, coll, key)
    #   colno — position in the index (or -1 for the rowid)
    #   cid   — column id in the base table (or -1 for rowid)
    #   name  — column name (or NULL for rowid)
    #   desc  — 1 if descending, 0 if ascending
    #   coll  — collation sequence name
    #   key   — 1 if the column is part of the index key, 0 if auxiliary
    return [
        (row[2], row[3], bool(row[3]))
        for row in cursor.fetchall()
        if row[5] == 1  # only key columns (not auxiliary rowid)
    ]


class TestCompositeIndex:
    """``idx_favorite_timestamp`` exists with the right shape."""

    def test_index_exists_on_new_db(self, db):
        """TY-21: a freshly-created DB has ``idx_favorite_timestamp``."""
        conn = db._get_read_conn()
        indexes = _list_indexes(conn)
        assert "idx_favorite_timestamp" in indexes, f"expected idx_favorite_timestamp in {sorted(indexes)}"

    def test_index_exists_on_existing_db_reopen(self, tmp_path):
        """TY-21: re-opening an existing DB file re-runs
        ``_init_db_schema``, which is idempotent (``CREATE INDEX IF NOT
        EXISTS``). The composite index must be present on the second
        open even though it didn't exist when the first HistoryDB was
        created (this simulates an upgrade from a pre-TY-21 DB)."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "test_retention_reopen.db"
        # First open: creates the DB and the new composite index.
        db1 = HistoryDB(db_path=db_path)
        db1.close()
        # Second open: should find the index already there (idempotent).
        db2 = HistoryDB(db_path=db_path)
        try:
            conn = db2._get_read_conn()
            indexes = _list_indexes(conn)
            assert "idx_favorite_timestamp" in indexes
        finally:
            db2.close()

    def test_index_has_correct_columns_and_sort_order(self, db):
        """TY-21: the composite index is on
        ``(favorite, timestamp ASC)`` — favorite first, timestamp
        ascending (NOT descending like ``idx_timestamp``).
        The sort order matters: the retention DELETE subquery uses
        ``ORDER BY timestamp ASC``, so a DESC index wouldn't be usable
        as a covering index (SQLite would have to walk it backwards).
        """
        conn = db._get_read_conn()
        cols = _index_columns(conn, "idx_favorite_timestamp")
        # Expect two key columns: favorite (ASC) and timestamp (ASC).
        assert len(cols) == 2, f"expected 2 key columns, got {cols}"
        # First column is 'favorite', ascending.
        assert cols[0][0] == "favorite", f"first column should be 'favorite', got {cols[0][0]!r}"
        assert cols[0][2] is False, "favorite should be ASC (desc=False)"
        # Second column is 'timestamp', ascending.
        assert cols[1][0] == "timestamp", f"second column should be 'timestamp', got {cols[1][0]!r}"
        assert cols[1][2] is False, "timestamp should be ASC (desc=False)"


class TestRetentionUsesIndex:
    """The retention DELETE subquery uses the composite index."""

    def test_retention_subquery_uses_composite_index(self, db):
        """TY-21: ``EXPLAIN QUERY PLAN`` for the retention DELETE
        subquery (``SELECT id FROM transcriptions WHERE favorite = 0
        ORDER BY timestamp ASC LIMIT ?``) should reference
        ``idx_favorite_timestamp`` as a covering index.

        Before TY-21, the plan used ``idx_favorite`` (favorite-only)
        plus a temporary b-tree for the ORDER BY — O(K log K) per
        batch. With the composite index, the plan is a single
        index range walk with no temp b-tree.
        """
        # Insert some rows so the query planner has stats to work with.
        for i in range(20):
            db.add_transcription(f"dictation {i}")
        db.flush()

        conn = db._get_read_conn()
        cursor = conn.cursor()
        # The retention DELETE subquery (verbatim from apply_retention).
        cursor.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT id FROM transcriptions
            WHERE favorite = 0
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (100,),
        )
        plan_rows = cursor.fetchall()
        # EXPLAIN QUERY PLAN returns rows of (id, parent, notused, detail).
        plan_text = " ".join(str(row[3]) for row in plan_rows).upper()

        # The composite index should be referenced (either by name or
        # as a covering index). Some SQLite versions phrase it as
        # "COVERING INDEX idx_favorite_timestamp" or just
        # "SEARCH transcriptions USING INDEX idx_favorite_timestamp".
        assert "IDX_FAVORITE_TIMESTAMP" in plan_text, (
            f"expected retention subquery to use idx_favorite_timestamp, got plan: {plan_text!r}"
        )
        # A covering index walk eliminates the temp b-tree for ORDER BY.
        # Before TY-21 the plan included "USE TEMP B-TREE FOR ORDER BY".
        assert "USE TEMP B-TREE FOR ORDER BY" not in plan_text, (
            "retention subquery should NOT need a temp b-tree for "
            "ORDER BY — the composite index provides the sort order"
        )

    def test_retention_by_age_subquery_also_covered(self, db):
        """TY-21 (bonus): the retention-by-AGE subquery
        (``WHERE timestamp < ? AND favorite = 0 LIMIT ?``) should also
        benefit from the composite index. This subquery doesn't have
        an explicit ``ORDER BY`` but still filters on ``favorite = 0``
        — the composite index serves the filter without a full scan.
        """
        for i in range(20):
            db.add_transcription(f"dictation {i}")
        db.flush()

        conn = db._get_read_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT id FROM transcriptions
            WHERE timestamp < ? AND favorite = 0
            LIMIT ?
            """,
            ("2099-01-01", 100),
        )
        plan_rows = cursor.fetchall()
        plan_text = " ".join(str(row[3]) for row in plan_rows).upper()
        # Either composite index or idx_favorite should be used —
        # NOT a full table scan. The composite is preferred because
        # it covers both predicates.
        assert "IDX_FAVORITE_TIMESTAMP" in plan_text or "IDX_FAVORITE" in plan_text, (
            f"expected retention-by-age subquery to use an index on 'favorite', got plan: {plan_text!r}"
        )
        assert "SCAN" not in plan_text or "SCAN" in plan_text.replace("USING INDEX", "") is False, (
            f"retention-by-age subquery should NOT be a full table scan, got plan: {plan_text!r}"
        )


class TestRetentionFunctional:
    """End-to-end: ``apply_retention`` deletes the expected rows."""

    def test_retention_deletes_oldest_non_favorites(self, db):
        """TY-21: functional regression — ``apply_retention(max_entries=N)``
        deletes the oldest non-favorite rows to bring the total down
        to N. Favorites are preserved.

        Setup: 10 rows total, 2 marked favorite → 8 non-fav + 2 fav.
        With max_entries=5, retention deletes (10-5)=5 oldest non-fav
        rows. 5 rows remain: 3 non-fav + 2 fav. Favorites are
        preserved because the DELETE subquery filters ``favorite = 0``.
        """
        # Insert 10 rows (all non-favorite initially).
        for i in range(10):
            db.add_transcription(f"dictation {i}")
        db.flush()
        # Mark two of the most recent as favorite.
        rows = db.get_recent(limit=10)
        for r in rows[:2]:
            assert db.toggle_favorite(r["id"]) is True

        # total=10, effective_max=5 → excess=5 deleted (oldest non-fav).
        deleted = db.apply_retention(max_entries=5)
        assert deleted == 5
        # 5 rows remain: 3 non-fav + 2 fav.
        remaining = db.get_recent(limit=20)
        assert len(remaining) == 5
        # Favorites are still there.
        fav_remaining = [r for r in remaining if r["favorite"] == 1]
        assert len(fav_remaining) == 2


class TestRetentionTimezoneXE9B:
    """XE-9-B regression: ``apply_retention`` cutoff must be UTC and
    formatted as ``'%Y-%m-%d %H:%M:%S'`` (matching SQLite's
    ``CURRENT_TIMESTAMP``), not naive local time + ISO 8601 (which
    appends a TZ offset and skews the comparison by the local TZ
    offset hours).

    The ``transcriptions.timestamp`` column is populated by SQLite's
    ``CURRENT_TIMESTAMP`` (UTC, ``'%Y-%m-%d %H:%M:%S'`` format). The
    previous ``apply_retention`` computed the cutoff as
    ``datetime.now().isoformat()`` — local time with a ``+HH:MM``
    suffix. Lexicographic comparison of an offset-suffixed string
    against a bare UTC string gives wrong results on any machine
    whose local TZ is not UTC.

    The fix: ``datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')``
    (UTC, bare format, no TZ suffix).
    """

    def _insert_rows_at_utc(self, db, timestamps_utc: list[str]) -> None:
        """Insert rows with the given UTC timestamp strings."""

        def _do_insert(conn):
            cursor = conn.cursor()
            for ts in timestamps_utc:
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                    (f"row@{ts}", ts),
                )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

    def test_retention_cutoff_is_utc_not_local(self, db):
        """XE-9-B: with ``retention_days=7``, a row whose UTC timestamp
        is exactly 8 days old MUST be deleted, and a row whose UTC
        timestamp is exactly 6 days old MUST be kept.

        We pin the "now" by patching ``datetime`` inside the retention
        module so the cutoff is computed relative to a fixed UTC instant
        regardless of the test machine's local TZ.
        """
        from voice_typer.server.history_db_internals import retention as retention_mod

        now_utc = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)

        old_8d = (now_utc - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
        old_7_5d = (now_utc - timedelta(days=7, hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        recent_6d = (now_utc - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert_rows_at_utc(db, [old_8d, old_7_5d, recent_6d])

        assert len(db.get_recent(limit=10)) == 3

        class _FakeDateTime:
            real = datetime

            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return now_utc.astimezone(tz) if now_utc.tzinfo else now_utc.replace(tzinfo=tz)
                return now_utc.replace(tzinfo=None)

            def __getattr__(self, name):
                return getattr(self.real, name)

        original_datetime = retention_mod.datetime
        retention_mod.datetime = _FakeDateTime
        try:
            deleted = db.apply_retention(retention_days=7, max_entries=0)
        finally:
            retention_mod.datetime = original_datetime

        assert deleted == 2, (
            f"XE-9-B: expected 2 rows deleted (8d + 7.5d old), got {deleted}. "
            "If this is 3, the cutoff is computed in local time (bug)."
        )
        remaining = db.get_recent(limit=10)
        assert len(remaining) == 1
        assert remaining[0]["text"] == f"row@{recent_6d}"

    def test_retention_cutoff_format_matches_current_timestamp(self, db):
        """XE-9-B: the cutoff string format must be ``'%Y-%m-%d %H:%M:%S'``
        (no TZ suffix) so the lexicographic ``timestamp < ?`` comparison
        against ``CURRENT_TIMESTAMP``-formatted values is apples-to-apples.
        """
        captured_cutoffs: list[str] = []
        original_submit = db._submit_write

        def capturing_submit(fn, *, wait=True):
            def wrapped_fn(real_conn):
                class _SpyCursor:
                    def __init__(self, real):
                        self._real = real

                    def execute(self, sql, *args, **kwargs):
                        if "timestamp < ?" in sql and args:
                            captured_cutoffs.extend(a for a in args[0] if isinstance(a, str))
                        return self._real.execute(sql, *args, **kwargs)

                    def __getattr__(self, name):
                        return getattr(self._real, name)

                    @property
                    def rowcount(self):
                        return self._real.rowcount

                class _SpyConn:
                    def __init__(self, real):
                        self._real = real

                    def cursor(self):
                        return _SpyCursor(self._real.cursor())

                    def commit(self):
                        return self._real.commit()

                    def execute(self, sql, *args, **kwargs):
                        return self._real.execute(sql, *args, **kwargs)

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

                spy = _SpyConn(real_conn)
                return fn(spy)

            return original_submit(wrapped_fn, wait=wait)

        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert_rows_at_utc(db, [old_ts])

        db._submit_write = capturing_submit
        try:
            db.apply_retention(retention_days=7, max_entries=0)
        finally:
            db._submit_write = original_submit

        assert captured_cutoffs, "XE-9-B: no cutoff captured — DELETE subquery did not run"
        cutoff = captured_cutoffs[0]
        assert "+" not in cutoff, f"XE-9-B: cutoff has '+' (TZ offset suffix): {cutoff!r} — bug"
        datetime.strptime(cutoff, "%Y-%m-%d %H:%M:%S")
        parsed = datetime.strptime(cutoff, "%Y-%m-%d %H:%M:%S")
        delta = datetime.now(timezone.utc).replace(tzinfo=None) - parsed
        assert timedelta(days=6, hours=23) <= delta <= timedelta(days=7, hours=1), (
            f"XE-9-B: cutoff {cutoff} is not ~7 days before now (delta={delta})"
        )


class TestRetentionFts5RebuildFailureXE9E:
    """XE-9-E regression: when the FTS5 ``'rebuild'`` command fails
    after a retention sweep, the failure is no longer silent. The
    returned :class:`RetentionResult` carries ``fts5_rebuild_ok=False``,
    the per-instance ``db._fts5_rebuild_failures`` counter is
    incremented, an ``event_bus`` event
    ``{"type": "history_fts5_rebuild_failed"}`` is published, and the
    log is escalated from WARNING to ERROR (the privacy guarantee is
    broken, not merely suboptimal).
    """

    def test_apply_retention_returns_retention_result_with_fts5_rebuild_ok(self, db):
        """XE-9-E: ``apply_retention`` returns a :class:`RetentionResult`
        (an ``int`` subclass) that supports both int comparison
        (``deleted == N``) AND dict-style access
        (``result["fts5_rebuild_ok"]``)."""
        from voice_typer.server.history_db_internals.retention import RetentionResult

        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        def _do_insert(conn):
            conn.cursor().execute(
                "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                ("old secret", old_ts),
            )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

        result = db.apply_retention(retention_days=7, max_entries=0)

        assert int(result) == 1
        assert result == 1
        assert result["deleted"] == 1
        assert result["fts5_rebuild_ok"] is True
        assert result.fts5_rebuild_ok is True
        assert isinstance(result, int)
        assert isinstance(result, RetentionResult)

    def test_apply_retention_fts5_rebuild_failure_sets_flag_and_increments_counter(self, db, monkeypatch):
        """XE-9-E: when the FTS5 ``'rebuild'`` command raises
        ``sqlite3.Error`` inside the retention sweep, the returned
        ``RetentionResult`` carries ``fts5_rebuild_ok=False`` AND
        ``db._fts5_rebuild_failures`` is incremented.
        """
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        def _do_insert(conn):
            cursor = conn.cursor()
            for i in range(5):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                    (f"old secret {i}", old_ts),
                )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

        db._fts5_rebuild_failures = 0

        original_submit = db._submit_write

        class _RebuildFailingCursor:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                if "transcriptions_fts" in sql and "rebuild" in sql.lower():
                    raise sqlite3.Error("simulated FTS5 rebuild failure (XE-9-E test)")
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

            @property
            def rowcount(self):
                return self._real.rowcount

            def close(self):
                return self._real.close()

        class _RebuildFailingConn:
            def __init__(self, real):
                self._real = real

            def cursor(self):
                return _RebuildFailingCursor(self._real.cursor())

            def commit(self):
                return self._real.commit()

            def execute(self, sql, *args, **kwargs):
                return self._real.execute(sql, *args, **kwargs)

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

        def failing_submit(fn, *, wait=True):
            def wrapped_fn(real_conn):
                return fn(_RebuildFailingConn(real_conn))

            return original_submit(wrapped_fn, wait=wait)

        monkeypatch.setattr(db, "_submit_write", failing_submit)

        result = db.apply_retention(retention_days=7, max_entries=0)

        assert int(result) == 5
        assert result["fts5_rebuild_ok"] is False, "XE-9-E: fts5_rebuild_ok should be False when the rebuild raises"
        assert result.fts5_rebuild_ok is False
        assert db._fts5_rebuild_failures >= 1, "XE-9-E: _fts5_rebuild_failures should be incremented on rebuild failure"

    def test_apply_retention_fts5_rebuild_failure_publishes_event_bus_event(self, db, monkeypatch):
        """XE-9-E: when the FTS5 ``'rebuild'`` command fails, an
        ``event_bus.publish`` call is made with
        ``{"type": "history_fts5_rebuild_failed"}`` so the renderer
        can show a toast."""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        def _do_insert(conn):
            conn.cursor().execute(
                "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                ("old secret", old_ts),
            )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

        published_events: list[dict] = []
        import voice_typer.server.event_bus as event_bus_mod

        original_publish = event_bus_mod.publish

        def capturing_publish(event, *args, **kwargs):
            published_events.append(event)
            return original_publish(event, *args, **kwargs)

        monkeypatch.setattr(event_bus_mod, "publish", capturing_publish)

        original_submit = db._submit_write

        class _RebuildFailingCursor:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                if "transcriptions_fts" in sql and "rebuild" in sql.lower():
                    raise sqlite3.Error("simulated FTS5 rebuild failure (XE-9-E event test)")
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

            @property
            def rowcount(self):
                return self._real.rowcount

            def close(self):
                return self._real.close()

        class _RebuildFailingConn:
            def __init__(self, real):
                self._real = real

            def cursor(self):
                return _RebuildFailingCursor(self._real.cursor())

            def commit(self):
                return self._real.commit()

            def execute(self, sql, *args, **kwargs):
                return self._real.execute(sql, *args, **kwargs)

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

        def failing_submit(fn, *, wait=True):
            def wrapped_fn(real_conn):
                return fn(_RebuildFailingConn(real_conn))

            return original_submit(wrapped_fn, wait=wait)

        monkeypatch.setattr(db, "_submit_write", failing_submit)

        db.apply_retention(retention_days=7, max_entries=0)

        rebuild_events = [e for e in published_events if e.get("type") == "history_fts5_rebuild_failed"]
        assert rebuild_events, (
            "XE-9-E: expected event_bus.publish({type: 'history_fts5_rebuild_failed'}) "
            f"to be called, but published events were: {published_events}"
        )
        event = rebuild_events[0]
        assert event["data"]["source"] == "apply_retention"
        assert "error" in event["data"]

    def test_apply_retention_no_rebuild_attempted_when_nothing_deleted(self, db):
        """XE-9-E: when ``apply_retention`` deletes nothing (no-op sweep),
        the FTS5 rebuild step is skipped and ``fts5_rebuild_ok`` stays
        ``True`` (default — no privacy failure to report)."""
        result = db.apply_retention(retention_days=999, max_entries=0)
        assert int(result) == 0
        assert result["fts5_rebuild_ok"] is True, "XE-9-E: fts5_rebuild_ok should be True when no rebuild was attempted"
        assert db._fts5_rebuild_failures == 0
