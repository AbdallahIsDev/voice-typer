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
from pathlib import Path

import pytest

from tests.fixtures.history_test_helpers import history_plaintext_mode  # noqa: F401


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


class TestHistoryDBSchema:
    def test_schema_has_transcriptions_table(self, db):
        conn = db._get_read_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions'")
        assert cursor.fetchone() is not None

    def test_schema_has_favorite_column(self, db):
        conn = db._get_read_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "favorite" in columns

    def test_schema_has_language_column(self, db):
        conn = db._get_read_conn()
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
    def test_get_today_stats(self, db):
        db.add_transcription("Today's entry")
        db.flush()
        stats = db.get_today_stats()
        assert stats["count"] >= 1


class TestHistoryDBWALMode:
    def test_uses_wal_mode(self, db):
        conn = db._get_read_conn()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"


class TestHistoryDBForeignKeysPragma:
    """XZ-R11-11: the writer connection must set ``PRAGMA foreign_keys=ON``.

    The current schema has no FK constraints, so this is a no-op today —
    but if a future migration adds FKs, the default-OFF behavior would
    silently allow orphaned child rows. The PRAGMA is per-connection
    (NOT database-persistent), so it must be set on every new writer
    connection. We verify by directly invoking ``_open_write_conn`` and
    reading back the PRAGMA value.
    """

    def test_writer_connection_has_foreign_keys_on(self, tmp_path):
        from voice_typer.server.history_db import HistoryDB

        db_instance = HistoryDB(db_path=tmp_path / "test_fk.db")
        try:
            # Wait for the writer thread to finish init so the
            # connection helper is callable.
            db_instance.flush()
            conn = db_instance._open_write_conn()
            try:
                cur = conn.execute("PRAGMA foreign_keys")
                row = cur.fetchone()
                assert row is not None
                # SQLite returns 0/1 for boolean PRAGMAs.
                assert row[0] == 1, (
                    f"XZ-R11-11: expected PRAGMA foreign_keys=1 (ON) on the "
                    f"writer connection; got {row[0]}. Future schema migrations "
                    f"adding FK constraints would silently allow orphaned rows."
                )
            finally:
                conn.close()
        finally:
            db_instance.close()


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
                    "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, ?)",
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
                    "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 1)",
                    (f"favorite {i}", old_date),
                )
            for i in range(5):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp, favorite) VALUES (?, ?, 0)",
                    (f"non-favorite {i}", old_date),
                )
            conn.commit()

        db._submit_write(_do_insert, wait=True)

        deleted = db.apply_retention(retention_days=1, max_entries=0)
        assert deleted == 5  # only non-favorites deleted

        # Verify favorites remain.
        conn = db._get_read_conn()
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
                        "INSERT INTO transcriptions (text, favorite) VALUES (?, 0)",
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
            r for r in caplog.records if "locked" in r.getMessage().lower() or "busy" in r.getMessage().lower()
        ]
        assert lock_errors == [], f"Expected no lock errors, but got: {[r.getMessage() for r in lock_errors]}"

        # All rows should be present.
        entries = db.get_recent(limit=n_threads * n_per_thread + 10)
        assert len(entries) == n_threads * n_per_thread, f"Expected {n_threads * n_per_thread} rows, got {len(entries)}"

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
            r for r in caplog.records if "locked" in r.getMessage().lower() or "busy" in r.getMessage().lower()
        ]
        assert lock_errors == [], f"Expected no lock errors, but got: {[r.getMessage() for r in lock_errors]}"

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
                r for r in caplog.records if r.levelno == logging.WARNING and "WAL mode NOT enabled" in r.getMessage()
            ]
            assert len(warnings) >= 1, (
                "Expected a 'WAL mode NOT enabled' warning when PRAGMA returns "
                f"non-WAL; got: {[r.getMessage() for r in caplog.records]}"
            )
            msg = warnings[0].getMessage()
            assert "delete" in msg, f"Warning should mention the actual mode ('delete'); got: {msg}"
            db_path_str = str(tmp_path / "wal_check.db")
            # SEC-009: the PII log filter replaces the home-dir prefix
            # with ``~`` in rendered messages, so accept both the full
            # path and the home-shortened form (the filename is always
            # preserved).
            assert db_path_str in msg or db_path_str.replace(str(Path.home()), "~") in msg, (
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
            r for r in caplog.records if r.levelno == logging.WARNING and "WAL mode NOT enabled" in r.getMessage()
        ]
        assert warnings == [], (
            f"No WAL warning expected when WAL is actually enabled; got: {[r.getMessage() for r in warnings]}"
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
        assert not db._writer_thread.is_alive(), "Writer thread should exit after close()"

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


class TestQueueBounded:
    """PERF-5: the write queue is bounded (maxsize=_WRITE_QUEUE_MAXSIZE)
    and drop-oldest logic resolves dropped futures with HistoryDBError
    so wait=True callers don't hang when the writer thread stalls.

    Without this bound, a stalled writer (disk full, antivirus lock,
    deadlocked external process) would cause the queue to grow
    unboundedly and exhaust memory.
    """

    def test_write_queue_is_bounded(self, tmp_path):
        """The write queue must have a maxsize of 10000 (the documented
        _WRITE_QUEUE_MAXSIZE constant)."""
        from voice_typer.server.history_db import (
            _WRITE_QUEUE_MAXSIZE,
            HistoryDB,
        )

        db = HistoryDB(tmp_path / "test.db")
        try:
            assert db._queue.maxsize == _WRITE_QUEUE_MAXSIZE
            # Pin the constant to 10000 — if it changes, this test
            # forces a deliberate review of the drop-oldest behavior.
            assert _WRITE_QUEUE_MAXSIZE == 10000
        finally:
            db.close()

    def test_dropped_oldest_write_resolves_future_with_error(self, tmp_path, monkeypatch):
        """PERF-5: when the queue is full and a new write is submitted,
        the OLDEST item is dropped. If the OLDEST item had a future
        (wait=True), it is resolved with HistoryDBError so the caller
        doesn't hang.

        Strategy: replace ``_writer_loop`` with a stub that signals
        ready then exits immediately — the writer thread dies, so the
        queue is never drained (simulating a stalled writer). We then
        fill the queue, submit one more write, and verify the OLDEST
        future raises HistoryDBError.
        """
        import concurrent.futures
        import queue as queue_mod

        from voice_typer.server.history_db import HistoryDB, HistoryDBError

        # Stall the writer: signals ready then exits. The writer thread
        # dies, so the queue is never drained.
        def _stalled_writer_loop(self):
            self._writer_ready.set()
            return  # writer exits immediately — queue never drains

        monkeypatch.setattr(HistoryDB, "_writer_loop", _stalled_writer_loop)

        db = HistoryDB(db_path=tmp_path / "stalled.db")
        try:
            # The writer thread should have exited (stalled).
            assert not db._writer_thread.is_alive(), (
                "Writer thread should have exited after _stalled_writer_loop returned"
            )

            # Manually enqueue the OLDEST write with a future. This
            # simulates a wait=True write that is blocked on its future.
            oldest_future = concurrent.futures.Future()
            db._queue.put((lambda conn: "oldest", oldest_future))

            # Fill the rest of the queue with fire-and-forget writes
            # (future=None) until it's full.
            maxsize = db._queue.maxsize
            for _ in range(maxsize - 1):
                db._queue.put_nowait((lambda conn: None, None))

            assert db._queue.full(), "Queue should be full after filling"

            # Submit one more write — this triggers drop-oldest. Use
            # wait=False so the test doesn't block on a future.
            db._submit_write(lambda conn: None, wait=False)

            # The OLDEST future should now be resolved with HistoryDBError
            # (rather than hanging forever as it would before PERF-5).
            with pytest.raises(HistoryDBError, match="queue full"):
                oldest_future.result(timeout=2.0)
        finally:
            # Drain the queue so close() doesn't block on the full
            # bounded queue (close() uses put with a timeout, which
            # would otherwise wait _WRITER_JOIN_TIMEOUT seconds).
            while True:
                try:
                    db._queue.get_nowait()
                except queue_mod.Empty:
                    break
            db.close()


# regression tests ────────────────────────────────────────


class TestPreMigrationBackup:
    """PI-10: ``_init_db_schema`` must create a pre-migration backup
    (``history.db.pre-migration-v<from>.bak``) before running any
    migration. If a future migration has a logic bug that silently
    corrupts rows, the user can restore from this backup.

    The backup is best-effort: if the copy fails (disk full,
    permissions), the migration proceeds anyway (the user's history is
    more valuable than the backup; a stuck migration would leave the
    app on the old schema, which is worse).

    These tests pin the new behavior so a future refactor that drops
    the backup step doesn't silently regress PI-10.
    """

    def test_pre_migration_backup_created_on_v1_to_v3_migration(self, tmp_path):
        """PI-10: a v1 DB migrated to v3 must produce a
        ``history.db.pre-migration-v1.bak`` file before the migration
        runs.

        Strategy: set up a v1 DB (no favorite/language columns) with
        a few rows of user data. Open it with HistoryDB (which
        triggers the migration v1 -> v2 -> v3). Assert the .bak file
        exists and contains the v1 schema (no favorite column) —
        proving the backup was taken BEFORE the migration ran.
        """
        import sqlite3

        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "test_history.db"

        # Set up a v1 DB with user data.
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("""
            CREATE TABLE transcriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                duration REAL DEFAULT 0,
                model TEXT DEFAULT '',
                device TEXT DEFAULT '',
                word_count INTEGER DEFAULT 0,
                char_count INTEGER DEFAULT 0
            )
        """)
        setup_conn.execute("""
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        setup_conn.execute("INSERT INTO schema_meta (key, value) VALUES ('version', '1')")
        setup_conn.execute("INSERT INTO transcriptions (text) VALUES ('pre-migration data')")
        setup_conn.commit()
        setup_conn.close()

        # Open with HistoryDB — triggers the v1 -> v2 -> v3 migration.
        db = HistoryDB(db_path=db_path)
        try:
            assert db._init_error is None, f"Expected migration to succeed; got _init_error={db._init_error}"
        finally:
            db.close()

        # The .bak file must exist and contain the v1 schema (no
        # favorite column) — proving the backup was taken BEFORE the
        # migration ran.
        bak_path = db_path.with_name(f"{db_path.name}.pre-migration-v1.bak")
        assert bak_path.exists(), (
            "PI-10 regression: pre-migration backup file should exist "
            f"after a v1 -> v3 migration. Files in dir: "
            f"{[p.name for p in db_path.parent.iterdir()]}"
        )
        bak_conn = sqlite3.connect(str(bak_path))
        try:
            bak_conn.row_factory = sqlite3.Row
            # The .bak must be at the OLD schema (no favorite column).
            cursor = bak_conn.execute("PRAGMA table_info(transcriptions)")
            columns = {row[1] for row in cursor.fetchall()}
            assert "favorite" not in columns, (
                "PI-10 regression: pre-migration backup should be at the "
                "OLD schema (v1, no favorite column), but the backup has "
                f"the favorite column. Columns: {columns}"
            )
            # The .bak must contain the user data we inserted before
            # the migration.
            cursor = bak_conn.execute("SELECT text FROM transcriptions")
            rows = [row[0] for row in cursor.fetchall()]
            assert "pre-migration data" in rows, (
                f"PI-10 regression: pre-migration backup should contain the pre-migration user data. Rows: {rows}"
            )
            # The .bak's schema_meta.version must be 1 (the OLD version).
            cursor = bak_conn.execute("SELECT value FROM schema_meta WHERE key = 'version'")
            row = cursor.fetchone()
            assert row is not None and int(row[0]) == 1, (
                "PI-10 regression: pre-migration backup should record "
                f"the OLD schema version (1). Got: {row[0] if row else None}"
            )
        finally:
            bak_conn.close()

    def test_pre_migration_backup_taken_before_any_schema_write(self, tmp_path):
        """PRE-MIGRATION-BACKUP-ORDERING: the backup must be taken before
        ANY write to the DB in ``init_schema`` — not merely before the
        migration loop. Previously the ``CREATE TABLE IF NOT EXISTS``
        statements ran first; they are no-ops on an existing DB, but the
        ordering was one future pre-migration write away from silently
        folding new-schema writes into the "old-version" backup.

        Strategy: build a v1 DB that is MISSING the ``transcriptions``
        table (simulating a partially-initialized old DB). Under the
        fixed ordering the backup is taken before the CREATE TABLE
        writes, so the .bak must NOT contain a ``transcriptions`` table.
        Under the old ordering the CREATE would have created the table
        and the backup would contain it.
        """
        import sqlite3

        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "test_history_partial.db"

        # A v1 DB with ONLY schema_meta (no transcriptions table).
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("""
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        setup_conn.execute("INSERT INTO schema_meta (key, value) VALUES ('version', '1')")
        setup_conn.commit()
        setup_conn.close()

        db = HistoryDB(db_path=db_path)
        try:
            assert db._init_error is None, f"Expected init to succeed; got _init_error={db._init_error}"
        finally:
            db.close()

        bak_path = db_path.with_name(f"{db_path.name}.pre-migration-v1.bak")
        assert bak_path.exists(), (
            "PRE-MIGRATION-BACKUP-ORDERING regression: pre-migration backup "
            f"should exist. Files in dir: {[p.name for p in db_path.parent.iterdir()]}"
        )
        bak_conn = sqlite3.connect(str(bak_path))
        try:
            cursor = bak_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions'")
            assert cursor.fetchone() is None, (
                "PRE-MIGRATION-BACKUP-ORDERING regression: the backup must be "
                "taken BEFORE any CREATE TABLE write — it captured a "
                "'transcriptions' table that only exists post-write."
            )
        finally:
            bak_conn.close()

    def test_no_pre_migration_backup_when_already_at_current_version(self, tmp_path):
        """PI-10: opening a DB that's already at the current schema
        version must NOT create a pre-migration backup (the migration
        loop is empty, so there's nothing to back up).

        This guards against the backup step running unconditionally on
        every launch, which would accumulate stale .bak files (or
        overwrite a legitimate pre-migration .bak with an identical
        post-migration copy).
        """
        import sqlite3

        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "test_history.db"

        # Set up a fully-migrated DB at the current schema version.
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("""
            CREATE TABLE transcriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                duration REAL DEFAULT 0,
                model TEXT DEFAULT '',
                device TEXT DEFAULT '',
                word_count INTEGER DEFAULT 0,
                char_count INTEGER DEFAULT 0,
                favorite INTEGER DEFAULT 0,
                language TEXT DEFAULT ''
            )
        """)
        setup_conn.execute("""
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # _CURRENT_SCHEMA_VERSION was moved to history_db_internals.schema
        # during the  /  decomposition; history_db.py no longer
        # re-exports it. Import from the canonical location.
        from voice_typer.server.history_db_internals.schema import (
            _CURRENT_SCHEMA_VERSION,
        )

        setup_conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(_CURRENT_SCHEMA_VERSION),),
        )
        setup_conn.commit()
        setup_conn.close()

        db = HistoryDB(db_path=db_path)
        try:
            assert db._init_error is None
        finally:
            db.close()

        # No .bak file should exist (no migration ran).
        bak_files = list(db_path.parent.glob("*.pre-migration-v*.bak"))
        assert bak_files == [], (
            "PI-10 regression: no pre-migration backup should be created "
            "when the DB is already at the current schema version. "
            f"Found: {[p.name for p in bak_files]}"
        )


class TestCloseWalCheckpoint:
    """PI-11: ``close()`` must run ``PRAGMA wal_checkpoint(TRUNCATE)``
    before sending the shutdown sentinel to the writer thread. This
    flushes all WAL pages back to the main DB file and truncates
    ``history.db-wal`` to zero size, so a clean shutdown leaves no
    uncheckpointed WAL residue.

    The checkpoint is best-effort: if it fails (e.g. DB busy), close()
    proceeds anyway. Wrapped in ``contextlib.suppress(sqlite3.Error)``.

    These tests pin the new behavior so a future refactor that drops
    the close()-time checkpoint doesn't silently regress PI-11.
    """

    def test_close_runs_wal_checkpoint_truncate(self, tmp_path, monkeypatch):
        """PI-11: close() must call self.checkpoint(truncate=True) before
        sending the shutdown sentinel.

        Strategy: monkeypatch ``checkpoint`` to record its call. After
        close(), assert it was called exactly once with
        ``truncate=True``.
        """
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "wal_close.db")
        checkpoint_calls: list[bool] = []
        original_checkpoint = db.checkpoint

        def _spy_checkpoint(truncate: bool = True) -> bool:
            checkpoint_calls.append(truncate)
            return original_checkpoint(truncate=truncate)

        monkeypatch.setattr(db, "checkpoint", _spy_checkpoint)

        db.close()

        assert len(checkpoint_calls) == 1, (
            f"PI-11 regression: close() should call self.checkpoint() exactly once. Got {len(checkpoint_calls)} calls."
        )
        assert checkpoint_calls[0] is True, (
            "PI-11 regression: close() should call self.checkpoint("
            "truncate=True) to flush + truncate the WAL. Got "
            f"truncate={checkpoint_calls[0]}"
        )

    def test_close_does_not_block_on_checkpoint_failure(self, tmp_path, monkeypatch):
        """PI-11: if the checkpoint raises sqlite3.Error, close() must
        NOT block — the suppress wrapper swallows the error and
        proceeds to send the shutdown sentinel.

        Without the suppress, a checkpoint failure (e.g. DB busy,
        disk full) would prevent the writer thread from being shut
        down, leaking a daemon thread and the writer's connection.
        """
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "wal_fail.db")

        def _failing_checkpoint(truncate: bool = True) -> bool:
            import sqlite3

            raise sqlite3.Error("simulated checkpoint failure")

        monkeypatch.setattr(db, "checkpoint", _failing_checkpoint)

        # close() must NOT raise — the sqlite3.Error is suppressed.
        db.close()
        # The writer thread must have exited despite the checkpoint
        # failure (proves close() proceeded to send the sentinel).
        assert not db._writer_thread.is_alive(), (
            "PI-11 regression: writer thread should exit after close() even if the pre-shutdown checkpoint fails"
        )

    def test_close_truncates_wal_file(self, tmp_path):
        """PI-11: end-to-end — after close(), the ``-wal`` sidecar file
        must be either absent or zero-size (TRUNCATE mode zeros it).

        We insert some rows (which generate WAL pages), close, and
        assert the WAL file is gone or empty. Pre-PI-11, the WAL file
        could contain hundreds of KB of uncheckpointed pages after a
        clean shutdown.
        """
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "wal_truncate.db"
        db = HistoryDB(db_path=db_path)
        # Insert rows to generate WAL pages.
        for i in range(50):
            db.add_transcription(f"entry {i} " * 20)  # ~120 bytes each
        db.flush()

        wal_path = db_path.with_name(db_path.name + "-wal")
        # The WAL file should exist and be non-empty after the writes
        # (before close()).
        assert wal_path.exists(), "Pre-close: WAL file should exist after writes (WAL mode is on)"

        db.close()

        # After close(), the WAL file must be either absent or
        # zero-size (TRUNCATE mode zeros it; a subsequent open may
        # recreate it as zero-size or delete it).
        if wal_path.exists():
            assert wal_path.stat().st_size == 0, (
                "PI-11 regression: WAL file should be truncated to zero "
                f"size after close(). Size: {wal_path.stat().st_size}"
            )


class TestO2DbSubdirMigration:
    """O2: ``history.db`` moved from the config-dir root into ``db/``.

    ``HistoryDB.__init__`` (default path) now resolves
    ``<config_dir>/db/history.db`` and runs a one-time migration of a
    legacy root-located ``history.db`` (and its ``-wal``/``-shm``
    sidecars) into ``db/``.
    """

    def _redirect_config_dir(self, monkeypatch, tmp_path: Path) -> Path:
        from voice_typer.server import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
        return tmp_path

    def test_default_path_resolves_under_db_subdir(self, monkeypatch, tmp_path):
        from voice_typer.server.history_db import DB_SUBDIR, HistoryDB

        config_dir = self._redirect_config_dir(monkeypatch, tmp_path)
        db = HistoryDB()
        try:
            assert db.db_path == config_dir / DB_SUBDIR / "history.db"
            assert db.db_path.parent == config_dir / "db"
        finally:
            db.close()

    def test_legacy_root_db_is_migrated_into_db_subdir(self, monkeypatch, tmp_path):
        from voice_typer.server.history_db import HistoryDB

        config_dir = self._redirect_config_dir(monkeypatch, tmp_path)
        legacy = config_dir / "history.db"
        legacy.write_bytes(b"legacy-db-bytes")

        db = HistoryDB()
        try:
            # The legacy file moved into db/.
            assert not legacy.exists(), "legacy root history.db must be migrated away"
            assert db.db_path == config_dir / "db" / "history.db"
            assert db.db_path.exists()
        finally:
            db.close()

    def test_legacy_wal_and_shm_sidecars_are_migrated(self, monkeypatch, tmp_path):
        from voice_typer.server.history_db import HistoryDB

        config_dir = self._redirect_config_dir(monkeypatch, tmp_path)
        (config_dir / "history.db").write_bytes(b"db")
        (config_dir / "history.db-wal").write_bytes(b"wal")
        (config_dir / "history.db-shm").write_bytes(b"shm")

        db = HistoryDB()
        try:
            assert not (config_dir / "history.db-wal").exists()
            assert not (config_dir / "history.db-shm").exists()
            assert (config_dir / "db" / "history.db-wal").exists()
            assert (config_dir / "db" / "history.db-shm").exists()
        finally:
            db.close()

    def test_no_migration_when_new_db_already_exists(self, monkeypatch, tmp_path):
        from voice_typer.server.history_db import HistoryDB

        config_dir = self._redirect_config_dir(monkeypatch, tmp_path)
        (config_dir / "db").mkdir()
        (config_dir / "db" / "history.db").write_bytes(b"newer-db")
        # A stale legacy file exists too — must NOT be clobbered or moved.
        legacy = config_dir / "history.db"
        legacy.write_bytes(b"stale-legacy")

        db = HistoryDB()
        try:
            assert (config_dir / "db" / "history.db").read_bytes() == b"newer-db"
            assert legacy.exists(), "stale legacy file must be left alone when db/ is populated"
        finally:
            db.close()

    def test_migration_is_idempotent(self, monkeypatch, tmp_path):
        from voice_typer.server.history_db import HistoryDB

        config_dir = self._redirect_config_dir(monkeypatch, tmp_path)
        (config_dir / "history.db").write_bytes(b"db")

        db1 = HistoryDB()
        db1.close()
        db2 = HistoryDB()
        try:
            assert db2.db_path == config_dir / "db" / "history.db"
        finally:
            db2.close()
