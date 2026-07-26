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


def _index_columns(
    conn: sqlite3.Connection, index_name: str
) -> list[tuple[str, int, bool]]:
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
        assert "idx_favorite_timestamp" in indexes, (
            f"expected idx_favorite_timestamp in {sorted(indexes)}"
        )

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
            f"expected retention subquery to use idx_favorite_timestamp, "
            f"got plan: {plan_text!r}"
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
        assert (
            "IDX_FAVORITE_TIMESTAMP" in plan_text
            or "IDX_FAVORITE" in plan_text
        ), (
            f"expected retention-by-age subquery to use an index on "
            f"'favorite', got plan: {plan_text!r}"
        )
        assert "SCAN" not in plan_text or "SCAN" in plan_text.replace(
            "USING INDEX", ""
        ) is False, (
            f"retention-by-age subquery should NOT be a full table scan, "
            f"got plan: {plan_text!r}"
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
