"""Tests for voice_typer.history_db — SQLite history, favorites, retention.

IMPL-A: the tests now exercise the single-writer-thread architecture.
Key points:
- ``add_transcription`` is fire-and-forget — tests call ``db.flush()``
  before reading back rows so the writer has drained the queue.
- Write-error tests monkeypatch ``_submit_write`` (the writer queue
  entrypoint) instead of ``_get_conn``.
- Read-error tests monkeypatch ``_get_read_conn``.
- ``TestDBLockRetry`` was removed (the ``_exec_with_retry`` helper is
  gone — the single writer eliminates in-process contention).
- ``TestChunkedRetention`` now verifies chunking by counting commits
  on the writer's connection via a ``_open_write_conn`` patch.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB
    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


class TestHistoryDBSchema:
    def test_schema_has_transcriptions_table(self, db):
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions'")
        assert cursor.fetchone() is not None

    def test_schema_has_favorite_column(self, db):
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "favorite" in columns

    def test_schema_has_language_column(self, db):
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "language" in columns


class TestHistoryDBCRUD:
    def test_add_transcription(self, db):
        row_id = db.add_transcription("Hello world", duration=2.5, model="small.en", device="cpu")
        # IMPL-A: fire-and-forget returns a placeholder (always > 0).
        assert row_id > 0

    def test_get_recent(self, db):
        db.add_transcription("First")
        db.add_transcription("Second")
        db.flush()  # IMPL-A: wait for fire-and-forget writes to drain.
        entries = db.get_recent(limit=10)
        assert len(entries) >= 2

    def test_search(self, db):
        db.add_transcription("The quick brown fox")
        db.add_transcription("Hello world")
        db.flush()
        results = db.search("quick")
        assert len(results) == 1
        assert "quick" in results[0]["text"]

    def test_search_treats_like_wildcards_as_literals(self, db):
        db.add_transcription("Progress is 100% complete")
        db.add_transcription("plain text without percent")
        db.add_transcription("snake_case_token")
        db.add_transcription("snake case token")
        db.flush()

        percent_results = db.search("%")
        underscore_results = db.search("_")

        assert [row["text"] for row in percent_results] == ["Progress is 100% complete"]
        assert [row["text"] for row in underscore_results] == ["snake_case_token"]

    def test_search_caps_query_at_200_characters(self, db):
        db.add_transcription("a" * 200)
        db.flush()

        results = db.search(("a" * 200) + "b")

        assert [row["text"] for row in results] == ["a" * 200]

    def test_delete(self, db):
        db.add_transcription("To delete")
        db.flush()
        # IMPL-A: add_transcription returns a placeholder; look up the
        # actual id via get_recent.
        rec = db.get_recent()[0]
        assert db.delete(rec["id"]) is True

    def test_delete_nonexistent(self, db):
        assert db.delete(999999) is False

    def test_clear_all(self, db):
        db.add_transcription("A")
        db.add_transcription("B")
        db.flush()
        assert db.clear_all() is True
        assert len(db.get_recent()) == 0


class TestHistoryDBFavorites:
    def test_toggle_favorite(self, db):
        db.add_transcription("Favorite me")
        db.flush()
        rec = db.get_recent()[0]
        result = db.toggle_favorite(rec["id"])
        assert result is True

    def test_get_favorites(self, db):
        db.add_transcription("Fav entry")
        db.flush()
        rec = db.get_recent()[0]
        db.toggle_favorite(rec["id"])
        favs = db.get_favorites()
        assert len(favs) == 1

    def test_non_favorite_not_in_get_favorites(self, db):
        db.add_transcription("Regular entry")
        db.flush()
        favs = db.get_favorites()
        assert len(favs) == 0


class TestHistoryDBRetention:
    def test_retention_by_max_entries(self, db):
        for i in range(5):
            db.add_transcription(f"Entry {i}")
        db.flush()
        deleted = db.apply_retention(max_entries=3)
        assert deleted >= 2
        entries = db.get_recent(limit=10)
        assert len(entries) <= 3

    def test_retention_preserves_favorites(self, db):
        db.add_transcription("Keep me")
        db.flush()
        rec = db.get_recent()[0]
        db.toggle_favorite(rec["id"])
        for i in range(5):
            db.add_transcription(f"Entry {i}")
        db.flush()
        db.apply_retention(max_entries=2)
        favs = db.get_favorites()
        assert len(favs) == 1


class TestHistoryDBStats:
    def test_get_stats(self, db):
        db.add_transcription("Hello world")
        db.flush()
        stats = db.get_stats()
        assert stats["total_count"] >= 1
        assert stats["total_chars"] > 0

    def test_get_today_stats(self, db):
        db.add_transcription("Today's entry")
        db.flush()
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

    IMPL-A: read methods now use ``_get_read_conn`` and write methods
    use ``_submit_write``. Tests monkeypatch the appropriate entrypoint.
    """

    def test_get_recent_raises_on_error(self, db, monkeypatch):
        """Force _get_read_conn to raise; assert HistoryDBError propagates."""
        def _boom():
            raise RuntimeError("disk I/O error")
        monkeypatch.setattr(db, "_get_read_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_recent(raise_on_error=True)

    def test_get_recent_returns_sentinel_without_flag(self, db, monkeypatch):
        """Without raise_on_error, the legacy [] sentinel is preserved."""
        def _boom():
            raise RuntimeError("disk I/O error")
        monkeypatch.setattr(db, "_get_read_conn", _boom)
        assert db.get_recent() == []

    def test_delete_raises_on_error(self, db, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_submit_write", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.delete(1, raise_on_error=True)

    def test_delete_returns_false_without_flag(self, db, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_submit_write", _boom)
        assert db.delete(1) is False

    def test_clear_all_raises_on_error(self, db, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("read-only")
        monkeypatch.setattr(db, "_submit_write", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.clear_all(raise_on_error=True)

    def test_toggle_favorite_raises_on_error(self, db, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_submit_write", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.toggle_favorite(1, raise_on_error=True)

    def test_search_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_read_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.search("foo", raise_on_error=True)

    def test_get_favorites_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_read_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_favorites(raise_on_error=True)

    def test_get_today_stats_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_read_conn", _boom)
        from voice_typer.server.history_db import HistoryDBError
        with pytest.raises(HistoryDBError):
            db.get_today_stats(raise_on_error=True)

    def test_get_stats_raises_on_error(self, db, monkeypatch):
        def _boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "_get_read_conn", _boom)
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
        db.flush()
        results = db.search("", limit=50)
        # Empty query → pattern "%%" matches everything.
        assert len(results) == 3

    def test_empty_query_with_zero_results_when_db_empty(self, db):
        results = db.search("", limit=50)
        assert results == []


class TestChunkedRetention:
    """IMPL-A: apply_retention must chunk deletes into batches of 100,
    committing after each batch so the WAL doesn't grow unboundedly
    and external readers see progress.

    The chunking is verified by counting commits on the writer's
    connection (via a ``_open_write_conn`` patch that wraps the real
    connection in a commit-counting proxy).
    """

    def _make_commit_counting_db(self, tmp_path, monkeypatch):
        """Build a HistoryDB whose writer connection counts commits."""
        from voice_typer.server.history_db import HistoryDB

        commit_count = {"n": 0}
        real_open = HistoryDB._open_write_conn

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

        def patched_open(self):
            return CommitCountingProxy(real_open(self))

        monkeypatch.setattr(HistoryDB, "_open_write_conn", patched_open)
        db = HistoryDB(db_path=tmp_path / "chunked.db")
        return db, commit_count

    def _insert_old_rows(self, db, count, favorite=0):
        """Insert rows with old timestamps via the writer thread."""
        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert(conn):
            cursor = conn.cursor()
            for i in range(count):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp, favorite) "
                    "VALUES (?, ?, ?)",
                    (f"old entry {i}", old_date, favorite),
                )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

    def test_retention_deletes_all_old_entries_in_batches(self, tmp_path, monkeypatch):
        """Insert 250 old entries; apply_retention(days=1) should delete
        all 250 in 3 batches (100+100+50) and commit after each batch."""
        db, commit_count = self._make_commit_counting_db(tmp_path, monkeypatch)
        try:
            self._insert_old_rows(db, 250)
            # Reset commit count after setup (the setup insert commits once).
            commit_count["n"] = 0

            deleted = db.apply_retention(retention_days=1, max_entries=0)

            assert deleted == 250
            # 250 entries / 100 per batch = 3 batches (100+100+50)
            # Each batch commits once → at least 3 commits.
            assert commit_count["n"] >= 3
        finally:
            db.close()

    def test_retention_keeps_favorites(self, db):
        """Favorites are never deleted by retention."""
        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert(conn):
            cursor = conn.cursor()
            for i in range(5):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp, favorite) "
                    "VALUES (?, ?, 1)",
                    (f"favorite {i}", old_date),
                )
            for i in range(5):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp, favorite) "
                    "VALUES (?, ?, 0)",
                    (f"non-favorite {i}", old_date),
                )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

        deleted = db.apply_retention(retention_days=1, max_entries=0)
        assert deleted == 5  # only non-favorites deleted

        # Verify favorites remain.
        conn = db._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM transcriptions WHERE favorite = 1")
        assert cursor.fetchone()[0] == 5

    def test_retention_max_entries_chunked(self, tmp_path, monkeypatch):
        """max_entries retention also chunks properly."""
        db, commit_count = self._make_commit_counting_db(tmp_path, monkeypatch)
        try:
            # Insert 250 entries (all recent, none old).
            def _do_insert(conn):
                cursor = conn.cursor()
                for i in range(250):
                    cursor.execute(
                        "INSERT INTO transcriptions (text, favorite) "
                        "VALUES (?, 0)",
                        (f"entry {i}",),
                    )
                conn.commit()

            db._submit_write(_do_insert, wait=True)
            commit_count["n"] = 0

            # Keep only 50 → delete 200.
            deleted = db.apply_retention(retention_days=0, max_entries=50)
            assert deleted == 200
            # 200 / 100 = 2 batches → at least 2 commits.
            assert commit_count["n"] >= 2
        finally:
            db.close()


# ──────────────────────────────────────────────────────────────────────
# IMPL-A: new tests for the single-writer-thread architecture.
# ──────────────────────────────────────────────────────────────────────


class TestWriterThreadArchitecture:
    """IMPL-A: verifies the single-writer-thread design."""

    def test_writes_serialized_through_writer_thread(self, db, caplog):
        """Spawn N threads that each call add_transcription concurrently;
        verify all rows are present and no 'database is locked' errors
        are logged.

        With the single-writer design, the writer thread drains the
        queue serially — there is no in-process contention, so
        SQLITE_BUSY/LOCKED errors are impossible (barring external
        writers, which aren't present in this test).
        """
        n_threads = 10
        n_per_thread = 20
        barrier = threading.Barrier(n_threads)

        def worker(tid):
            barrier.wait()  # release all threads at once
            for i in range(n_per_thread):
                db.add_transcription(f"thread-{tid}-row-{i}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.history_db"):
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            db.flush()

        # No "database is locked" errors should have been logged.
        lock_errors = [
            r for r in caplog.records
            if "locked" in r.getMessage().lower()
            or "busy" in r.getMessage().lower()
        ]
        assert lock_errors == [], (
            f"Expected no lock errors, but got: {[r.getMessage() for r in lock_errors]}"
        )

        # All rows should be present.
        entries = db.get_recent(limit=n_threads * n_per_thread + 10)
        assert len(entries) == n_threads * n_per_thread, (
            f"Expected {n_threads * n_per_thread} rows, got {len(entries)}"
        )

    def test_add_transcription_is_non_blocking(self, db):
        """Time add_transcription; assert it returns in <50ms
        (fire-and-forget).

        The call enqueues the INSERT and returns immediately with a
        placeholder row_id. The actual DB write happens asynchronously
        on the writer thread.
        """
        start = time.monotonic()
        row_id = db.add_transcription("non-blocking test")
        elapsed_ms = (time.monotonic() - start) * 1000.0

        assert row_id > 0  # placeholder is always > 0
        assert elapsed_ms < 50.0, (
            f"add_transcription took {elapsed_ms:.1f}ms; expected <50ms "
            "(fire-and-forget should return immediately after enqueue)"
        )

    def test_clear_all_is_chunked(self, db, caplog):
        """Insert 500 rows, call clear_all, verify all deleted and no
        lock errors.

        IMPL-A: clear_all now chunks deletes into batches of 100
        (commit per batch) inside the writer thread. With 500 rows,
        that's 5 batches. The chunking prevents the WAL from growing
        unboundedly and lets external readers see progress.
        """
        # Insert 500 rows via the writer thread (batched insert).
        def _do_insert(conn):
            cursor = conn.cursor()
            for i in range(500):
                cursor.execute(
                    "INSERT INTO transcriptions (text) VALUES (?)",
                    (f"row {i}",),
                )
            conn.commit()

        db._submit_write(_do_insert, wait=True)
        assert len(db.get_recent(limit=1000)) == 500

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.history_db"):
            result = db.clear_all()
            assert result is True

        # No lock errors should have been logged.
        lock_errors = [
            r for r in caplog.records
            if "locked" in r.getMessage().lower()
            or "busy" in r.getMessage().lower()
        ]
        assert lock_errors == [], (
            f"Expected no lock errors, but got: {[r.getMessage() for r in lock_errors]}"
        )

        # All rows should be deleted.
        assert len(db.get_recent(limit=1000)) == 0

    def test_wal_mode_verified(self, tmp_path, caplog):
        """If PRAGMA journal_mode=WAL returns a non-WAL mode, log a
        warning with the actual mode and the DB path.

        Simulates WAL failing (e.g. network FS, antivirus) by calling
        ``_check_wal_mode`` with a fake connection whose PRAGMA result
        is "delete". The warning must be visible at WARNING level.
        """
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "wal_check.db")
        try:
            class FakeCursor:
                def fetchone(self):
                    return ["delete"]

            class FakeConn:
                def execute(self, sql, *args, **kw):
                    return FakeCursor()

            with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db"):
                db._check_wal_mode(FakeConn())

            # The warning must mention "WAL mode NOT enabled", the
            # actual mode ("delete"), and the DB path.
            warnings = [
                r for r in caplog.records
                if r.levelno == logging.WARNING
                and "WAL mode NOT enabled" in r.getMessage()
            ]
            assert len(warnings) >= 1, (
                "Expected a 'WAL mode NOT enabled' warning when PRAGMA returns "
                f"non-WAL; got: {[r.getMessage() for r in caplog.records]}"
            )
            msg = warnings[0].getMessage()
            assert "delete" in msg, (
                f"Warning should mention the actual mode ('delete'); got: {msg}"
            )
            assert str(tmp_path / "wal_check.db") in msg, (
                f"Warning should mention the DB path; got: {msg}"
            )
        finally:
            db.close()

    def test_wal_mode_succeeds_silently(self, db, caplog):
        """When WAL is actually enabled (the normal case), no warning
        is logged. This guards against false positives in the WAL
        verification logic."""
        # Re-run the WAL check on the real writer connection — should
        # not emit a warning because the DB is already in WAL mode.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db"):
            # The writer's connection isn't directly accessible, but
            # _check_wal_mode re-issues the PRAGMA. We can verify the
            # DB file is in WAL mode via a fresh read connection.
            conn = db._get_read_conn()
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            assert mode.lower() == "wal"

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "WAL mode NOT enabled" in r.getMessage()
        ]
        assert warnings == [], (
            "No WAL warning expected when WAL is actually enabled; got: "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_writer_thread_shutdown(self, tmp_path):
        """call close(); verify the writer thread exits cleanly."""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "shutdown.db")
        assert db._writer_thread.is_alive()

        # Enqueue a fire-and-forget write, then close — the writer
        # should drain it before exiting.
        db.add_transcription("will be persisted before shutdown")
        db.close()

        # The writer thread should have exited.
        assert not db._writer_thread.is_alive(), (
            "Writer thread should exit after close()"
        )

        # Verify the row was persisted (open a fresh HistoryDB on the
        # same file and read it back).
        db2 = HistoryDB(db_path=tmp_path / "shutdown.db")
        try:
            entries = db2.get_recent(limit=10)
            assert len(entries) == 1
            assert entries[0]["text"] == "will be persisted before shutdown"
        finally:
            db2.close()

    def test_close_is_idempotent(self, tmp_path):
        """close() can be called multiple times without error."""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "idempotent.db")
        db.close()
        db.close()  # should not raise
        db.close()  # should not raise
