"""FR-27: regression tests for the FTS5 'rebuild' command after
``clear_all`` and ``apply_retention`` bulk deletes.

The previous implementation's DELETE fired the FTS5 trigger
``transcriptions_ad_fts`` which marks the rowid as deleted in the
``transcriptions_fts_idx`` delete-bitmap but does NOT zero the segment
data in ``transcriptions_fts_data``. ``VACUUM`` rebuilds the main DB
file but does NOT rebuild FTS5 shadow tables. After ``clear_all`` (or
after a large ``apply_retention`` sweep), dictated text remained
recoverable from ``transcriptions_fts_data`` via sqlite3 CLI or
forensic tools — defeating G4-M-05 / GDPR Art. 17 right-to-erasure.

The fix issues
``INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')``
inside the writer thread after the bulk DELETE + VACUUM. This rebuilds
the FTS5 segments from the (now-empty or reduced) content table,
dropping all shadow-table segment data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _fts5_data_size(db) -> int:
    """Return the total bytes in the FTS5 shadow-table segment data.

    ``transcriptions_fts_data`` holds the raw segment blobs. After a
    'rebuild' on an empty content table, this table is empty (or near-
    empty). After a clear_all WITHOUT a rebuild, this table retains
    the pre-clear segment data.
    """
    conn = db._get_read_conn()
    try:
        cur = conn.execute("SELECT COALESCE(SUM(length(block)), 0) FROM transcriptions_fts_data")
        return int(cur.fetchone()[0])
    except sqlite3.Error:
        # FTS5 shadow table doesn't exist (pre-V3 migration).
        return -1


def _fts5_row_count(db) -> int:
    """Return the number of FTS5-indexed rows (should match the content
    table's row count after a successful rebuild)."""
    conn = db._get_read_conn()
    try:
        cur = conn.execute("SELECT count(*) FROM transcriptions_fts")
        return int(cur.fetchone()[0])
    except sqlite3.Error:
        return -1


class TestClearAllFtsRebuild:
    """FR-27: ``clear_all`` rebuilds FTS5 segments from the (now-empty)
    content table, dropping all shadow-table segment data."""

    def test_clear_all_empties_fts5_shadow_data(self, db):
        # Insert rows so FTS5 has segment data to retain.
        for i in range(20):
            db.add_transcription(f"unique secret phrase number {i}")
        db.flush()
        # Sanity: FTS5 has data before clear.
        pre_size = _fts5_data_size(db)
        assert pre_size > 0, "expected non-empty FTS5 segment data after inserts"
        assert _fts5_row_count(db) == 20

        # Act: clear_all should rebuild FTS5.
        assert db.clear_all() is True

        # Force a checkpoint so the WAL is flushed (helps the size
        # assertion be deterministic).
        db.checkpoint(truncate=True)

        # Assert: FTS5 shadow-table data must be (near-)empty.
        post_size = _fts5_data_size(db)
        assert post_size < pre_size, (
            f"FR-27 violation: FTS5 segment data did not shrink after clear_all "
            f"(pre={pre_size}, post={post_size}). Dictated text remains recoverable "
            "from transcriptions_fts_data via forensic tools."
        )
        # Row count must be 0.
        assert _fts5_row_count(db) == 0

    def test_clear_all_emits_rebuild_command(self, db, monkeypatch):
        """The clear_all closure must issue the FTS5 'rebuild' command."""
        executed_sql: list[str] = []

        real_submit = db._submit_write

        class _SpyConn:
            """Wraps a real sqlite3.Connection so we can record every
            SQL statement. ``sqlite3.Connection`` does not allow
            setting ``execute`` / ``cursor`` as attributes (they're
            read-only slot wrappers), so we proxy via ``__getattr__``."""

            def __init__(self, real):
                self._real = real

            def cursor(self):
                return _SpyCursor(self._real.cursor())

            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql)
                return self._real.execute(sql, *args, **kwargs)

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

            def __getattr__(self, name):
                return getattr(self._real, name)

        class _SpyCursor:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql)
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

        def capturing_submit(fn, *, wait=True):
            def wrapped_fn(real_conn):
                spy = _SpyConn(real_conn)
                return fn(spy)

            return real_submit(wrapped_fn, wait=wait)

        monkeypatch.setattr(db, "_submit_write", capturing_submit)

        db.clear_all()

        # Look for the rebuild command in the executed SQL.
        rebuild_seen = any(
            "transcriptions_fts" in sql and "rebuild" in sql.lower() for sql in executed_sql
        )
        assert rebuild_seen, (
            "FR-27 violation: clear_all did not issue "
            "INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild'). "
            f"Executed SQL: {executed_sql}"
        )

    def test_clear_all_tolerates_missing_fts_table(self, tmp_path):
        """If the FTS5 table doesn't exist (pre-V3 schema), the rebuild
        command fails with sqlite3.Error and is logged at WARNING —
        clear_all still succeeds (returns True)."""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "nofrs.db")
        try:
            # Drop the FTS5 table + triggers to simulate a pre-V3 DB.
            write_conn = db._open_write_conn()
            try:
                write_conn.execute("DROP TRIGGER IF EXISTS transcriptions_ai_fts")
                write_conn.execute("DROP TRIGGER IF EXISTS transcriptions_ad_fts")
                write_conn.execute("DROP TRIGGER IF EXISTS transcriptions_au_fts")
                write_conn.execute("DROP TABLE IF EXISTS transcriptions_fts")
                write_conn.commit()
            finally:
                write_conn.close()

            db.add_transcription("hello")
            db.flush()
            # Must not raise.
            result = db.clear_all()
            assert result is True
        finally:
            db.close()


class TestApplyRetentionFtsRebuild:
    """FR-27: ``apply_retention`` rebuilds FTS5 segments after a bulk
    delete so deleted dictated text is not recoverable from the FTS5
    shadow tables."""

    def test_apply_retention_rebuilds_fts5_after_bulk_delete(self, db):
        # Insert 20 rows with old timestamps so retention will delete them.
        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert_old(conn):
            cursor = conn.cursor()
            for i in range(20):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                    (f"old secret phrase {i}", old_date),
                )
            conn.commit()

        db._submit_write(_do_insert_old, wait=True)
        db.flush()

        # Insert 5 recent rows that retention will keep.
        for i in range(5):
            db.add_transcription(f"recent phrase {i}")
        db.flush()

        pre_size = _fts5_data_size(db)
        assert pre_size > 0
        assert _fts5_row_count(db) == 25

        # Act: retention_days=1 deletes the 20 old rows.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 20

        db.checkpoint(truncate=True)

        # Assert: FTS5 row count must match the content table.
        assert _fts5_row_count(db) == 5

        # Assert: FTS5 shadow-table data must have shrunk (the deleted
        # rows' segment data was rebuilt away).
        post_size = _fts5_data_size(db)
        assert post_size < pre_size, (
            f"FR-27 violation: FTS5 segment data did not shrink after "
            f"apply_retention (pre={pre_size}, post={post_size}). Deleted "
            "dictated text remains recoverable from transcriptions_fts_data."
        )

    def test_apply_retention_no_rebuild_when_nothing_deleted(self, db, monkeypatch):
        """When apply_retention deletes nothing, the rebuild command
        is skipped (a no-op retention sweep has nothing to rebuild)."""
        executed_sql: list[str] = []
        real_submit = db._submit_write

        class _SpyConn:
            def __init__(self, real):
                self._real = real

            def cursor(self):
                return _SpyCursor(self._real.cursor())

            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql)
                return self._real.execute(sql, *args, **kwargs)

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

            def __getattr__(self, name):
                return getattr(self._real, name)

        class _SpyCursor:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql)
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._real, name)

        def capturing_submit(fn, *, wait=True):
            def wrapped_fn(real_conn):
                spy = _SpyConn(real_conn)
                return fn(spy)

            return real_submit(wrapped_fn, wait=wait)

        monkeypatch.setattr(db, "_submit_write", capturing_submit)

        # Empty DB → no rows to delete.
        deleted = db.apply_retention(retention_days=999)
        assert deleted == 0
        # The rebuild command must NOT have run.
        rebuild_seen = any(
            "transcriptions_fts" in sql and "rebuild" in sql.lower() for sql in executed_sql
        )
        assert not rebuild_seen, (
            "FR-27: apply_retention should NOT issue the FTS5 'rebuild' command "
            "when nothing was deleted (no-op sweep has nothing to rebuild)."
        )
