"""AB-27: regression tests for the per-connection SQLite ``cache_size``
PRAGMA.

Before AB-27, both the writer connection (``schema.open_write_conn``)
AND every thread-local read-only connection
(``HistoryDB._get_read_conn``) set ``PRAGMA cache_size=-20000`` = 20 MB.
With 5-8 reader threads (IPC handlers + tray + dictation pipeline),
peak page-cache memory was 120-180 MB for a DB typically < 50 MB.

The fix keeps the writer at -20000 (20 MB) for batch INSERTs and
VACUUM, and drops readers to -2000 (2 MB). Reads are indexed lookups
+ small aggregations; the working set is tiny.

These tests pin the new behavior:

- ``test_reader_connection_uses_2mb_cache`` — read conn has cache_size
  in the [-3000, -1000] range (i.e. ~2 MB; SQLite stores the *negative*
  value as a kibibyte budget).
- ``test_writer_connection_uses_20mb_cache`` — write conn keeps -20000.
- ``test_reader_cache_size_lower_than_writer`` — sanity: reader < writer.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_ab27_history.db")
    yield db_instance
    db_instance.close()


def _get_pragma_int(conn, pragma: str) -> int:
    """Read a PRAGMA value as an int. ``PRAGMA cache_size`` returns a row
    with one column."""
    cur = conn.execute(f"PRAGMA {pragma}")
    row = cur.fetchone()
    return int(row[0])


class TestAb27ReaderCacheSize:
    """AB-27: readers use 2 MB cache, writer uses 20 MB cache."""

    def test_reader_connection_uses_2mb_cache(self, db):
        """``_get_read_conn`` must set ``cache_size=-2000`` (2 MB).

        SQLite returns the negative value back from ``PRAGMA cache_size``
        as the kibibyte budget — so we expect -2000 ± a small tolerance
        (SQLite may round internally).
        """
        conn = db._get_read_conn()
        cache_size = _get_pragma_int(conn, "cache_size")
        # The PRAGMA should round-trip the -2000 we set. Allow a small
        # tolerance in case SQLite adjusts to a page boundary.
        assert -3000 <= cache_size <= -1000, (
            f"AB-27: reader connection should use cache_size≈-2000 (2 MB). Got cache_size={cache_size}."
        )
        # Specifically: it must NOT be -20000 (the pre-AB-27 value).
        assert cache_size != -20000, (
            "AB-27 regression: reader connection still uses -20000 (20 MB). "
            "Readers should use -2000 (2 MB) to avoid 120-180 MB idle RAM."
        )

    def test_writer_connection_uses_20mb_cache(self, tmp_path):
        """``schema.open_write_conn`` must keep ``cache_size=-20000`` (20 MB).

        The writer needs the larger cache for batch INSERTs and VACUUM.
        """
        from voice_typer.server.history_db_internals.schema import open_write_conn

        conn = open_write_conn(tmp_path / "test_ab27_writer.db")
        try:
            cache_size = _get_pragma_int(conn, "cache_size")
            assert cache_size == -20000, (
                f"AB-27: writer connection should keep cache_size=-20000 (20 MB). Got cache_size={cache_size}."
            )
        finally:
            conn.close()

    def test_reader_cache_size_lower_than_writer(self, db, tmp_path):
        """Sanity: reader cache_size must be smaller (more negative
        magnitude... actually less negative) than writer cache_size.

        Reader: -2000 (2 MB). Writer: -20000 (20 MB). So reader should
        be GREATER than writer (both negative).
        """
        from voice_typer.server.history_db_internals.schema import open_write_conn

        reader_conn = db._get_read_conn()
        reader_cache = _get_pragma_int(reader_conn, "cache_size")

        writer_conn = open_write_conn(tmp_path / "test_ab27_compare.db")
        try:
            writer_cache = _get_pragma_int(writer_conn, "cache_size")
        finally:
            writer_conn.close()

        # Both are negative; reader is -2000, writer is -20000.
        # Reader magnitude (2000) < writer magnitude (20000).
        assert abs(reader_cache) < abs(writer_cache), (
            "AB-27: reader cache_size magnitude should be SMALLER than "
            f"writer's. reader={reader_cache}, writer={writer_cache}."
        )

    def test_reader_query_only_enforced(self, db):
        """Sanity: reader connection is still read-only (PRAGMA
        query_only=1). AB-27 only changes the cache_size, not the
        read-only enforcement.
        """
        conn = db._get_read_conn()
        query_only = _get_pragma_int(conn, "query_only")
        assert query_only == 1, f"AB-27: reader connection must keep PRAGMA query_only=1. Got query_only={query_only}."

    def test_reader_still_returns_correct_results(self, db):
        """Functional check: reducing the reader cache to 2 MB must NOT
        break indexed SELECTs or small aggregations. Run a few
        representative queries and verify they return correct results.
        """
        # Insert 50 rows (enough to exceed a 2 MB page cache on a tiny
        # DB, but small enough that the test runs fast).
        for i in range(50):
            db.add_transcription(f"dictation number {i}")
        db.flush()

        # Indexed SELECT — uses idx_timestamp under the hood.
        recent = db.get_recent(limit=10)
        assert len(recent) == 10, f"AB-27: get_recent should return 10 rows. Got {len(recent)}."

        # Aggregating scan — get_today_stats runs SUM(char_count) etc.
        stats = db.get_today_stats()
        assert stats["count"] == 50, f"AB-27: get_today_stats count should be 50. Got {stats['count']}."
        assert stats["chars"] > 0, "AB-27: get_today_stats chars should be > 0."
        assert stats["word_count"] > 0, "AB-27: get_today_stats word_count should be > 0."

        # FTS5 search — uses the transcriptions_fts virtual table.
        results = db.search("dictation", limit=50)
        assert len(results) == 50, f"AB-27: search should return 50 rows. Got {len(results)}."

        # get_history_count — COUNT(*).
        count = db.get_history_count()
        assert count == 50, f"AB-27: get_history_count should be 50. Got {count}."
