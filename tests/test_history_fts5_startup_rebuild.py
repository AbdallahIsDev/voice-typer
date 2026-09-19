"""AP-17: regression tests for the FTS5 startup rebuild sweep."""

from __future__ import annotations

import logging
import sqlite3

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _fts5_data_size(conn: sqlite3.Connection) -> int:
    """Return the total bytes in the FTS5 shadow-table segment data."""
    try:
        cur = conn.execute("SELECT COALESCE(SUM(length(block)), 0) FROM transcriptions_fts_data")
        return int(cur.fetchone()[0])
    except sqlite3.Error:
        # FTS5 shadow table doesn't exist (pre-V3 migration).
        return -1


class _FlakyConn:
    """Wraps a real ``sqlite3.Connection`` so we can inject a failure"""

    def __init__(self, real, rebuild_should_fail):
        self._real = real
        self._rebuild_should_fail = rebuild_should_fail
        self.rebuild_attempts: list[bool] = []

    def cursor(self):
        return _FlakyCursor(self._real.cursor(), self)

    def execute(self, sql, *args, **kwargs):
        # Fail on either per-delete ``'optimize'`` (the per-row
        if "VALUES('optimize')" in sql or "VALUES('rebuild')" in sql:
            self.rebuild_attempts.append(True)
            if self._rebuild_should_fail():
                raise sqlite3.OperationalError("simulated FTS5 rebuild failure (AP-17 test)")
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


class _FlakyCursor:
    def __init__(self, real, parent):
        self._real = real
        self._parent = parent

    def execute(self, sql, *args, **kwargs):
        if "VALUES('optimize')" in sql or "VALUES('rebuild')" in sql:
            self._parent.rebuild_attempts.append(True)
            if self._parent._rebuild_should_fail():
                raise sqlite3.OperationalError("simulated FTS5 rebuild failure (AP-17 test)")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestFts5StartupRebuild:
    """AP-17: ``_fts5_startup_rebuild`` runs on every launch and bounds"""

    def test_startup_sweep_runs_on_construction(self, db, monkeypatch):
        """The startup sweep must be called during HistoryDB construction"""
        from voice_typer.server.history_db import HistoryDB

        # The ``db`` fixture was already constructed, so its startup
        startup_calls: list[int] = []
        real_startup = HistoryDB._fts5_startup_rebuild

        def spy_startup(self_db, conn):
            startup_calls.append(1)
            return real_startup(self_db, conn)

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", spy_startup)

        db2 = HistoryDB(db_path=db.db_path)
        try:
            assert len(startup_calls) >= 1, (
                "AP-17 violation: _fts5_startup_rebuild was not called during "
                "HistoryDB construction, the startup sweep does not run on "
                "launch, so lingering FTS5 segment data from failed deletes "
                "is NOT bounded to 'between launches'."
            )
        finally:
            db2.close()

    def test_startup_sweep_clears_lingering_segments_after_failed_delete(self, tmp_path, monkeypatch):
        """AP-17 core regression: when ``delete()``'s FTS5 ``'rebuild'``"""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "history.db"

        db1 = HistoryDB(db_path=db_path)
        try:
            rid = db1.add_transcription("top secret dictated text AP-17")
            db1.flush()
            assert rid > 0, "add_transcription must return a positive row id"

            read_conn = db1._get_read_conn()
            pre_insert_size = _fts5_data_size(read_conn)
            assert pre_insert_size > 0, "expected non-empty FTS5 segment data after insert, test setup is broken"

            # Wire a flaky _submit_write so the delete closure's FTS5
            rebuild_fail_state = {"failed_once": False}
            real_submit = db1._submit_write

            def rebuild_should_fail():
                if not rebuild_fail_state["failed_once"]:
                    rebuild_fail_state["failed_once"] = True
                    return True
                return False

            def flaky_submit(fn, *, wait=True):
                def wrapped(real_conn):
                    return fn(_FlakyConn(real_conn, rebuild_should_fail))

                return real_submit(wrapped, wait=wait)

            monkeypatch.setattr(db1, "_submit_write", flaky_submit)

            assert db1.delete(rid) is True, (
                "delete must return True even when the post-delete FTS5 rebuild fails, the row delete already committed"
            )

            # The rebuild was attempted exactly once during the delete
            assert rebuild_fail_state["failed_once"] is True, (
                "the delete-time FTS5 rebuild did not fail as expected, test setup is broken"
            )

            # Capture the FTS5 segment-data size AFTER the failed
            db1.checkpoint(truncate=True)
            post_delete_size = _fts5_data_size(read_conn)
            assert post_delete_size >= pre_insert_size, (
                "expected the failed-delete FTS5 segment data to still "
                f"be present (size={post_delete_size}, pre_insert="
                f"{pre_insert_size}), test setup is broken"
            )
        finally:
            db1.close()

        # Spy the startup sweep so we can assert it was called. The spy
        startup_calls: list[int] = []
        real_startup = HistoryDB._fts5_startup_rebuild

        def spy_startup(self_db, conn):
            startup_calls.append(1)
            return real_startup(self_db, conn)

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", spy_startup)

        db2 = HistoryDB(db_path=db_path)
        try:
            # Assert: the startup sweep was called during db2
            assert len(startup_calls) >= 1, (
                "AP-17 violation: _fts5_startup_rebuild was not called "
                "during HistoryDB construction, the startup sweep does "
                "not run on launch, so lingering FTS5 segment data from "
                "the failed delete in session 1 is NOT cleared."
            )

            # Force a checkpoint so the WAL is flushed (deterministic
            db2.checkpoint(truncate=True)

            # Assert: the FTS5 shadow-table segment data was cleared by
            read_conn = db2._get_read_conn()
            post_sweep_size = _fts5_data_size(read_conn)
            assert post_sweep_size < post_delete_size, (
                f"AP-17 violation: FTS5 segment data did not shrink after "
                f"the startup sweep (post_delete={post_delete_size}, "
                f"post_sweep={post_sweep_size}). The deleted "
                "transcription's dictated text remains recoverable from "
                "transcriptions_fts_data via forensic tools, the "
                "startup sweep did not clear the lingering segments "
                "from session 1's failed delete."
            )

            cur = read_conn.execute("SELECT count(*) FROM transcriptions")
            content_count = int(cur.fetchone()[0])
            assert content_count == 0, f"expected empty content table after delete, got {content_count}"

            # Assert: the FTS5 index row count matches the content
            cur = read_conn.execute("SELECT count(*) FROM transcriptions_fts")
            fts_count = int(cur.fetchone()[0])
            assert fts_count == 0, f"expected empty FTS5 index after startup sweep, got {fts_count}"
        finally:
            db2.close()

    def test_startup_sweep_failure_is_swallowed(self, tmp_path, monkeypatch, caplog):
        """AP-17: the startup sweep is best-effort. If the rebuild fails"""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "history.db"

        db1 = HistoryDB(db_path=db_path)
        db1.add_transcription("seed text")
        db1.flush()
        db1.close()

        # Patch _fts5_startup_rebuild to raise sqlite3.Error directly
        def failing_startup(self_db, conn):
            raise sqlite3.OperationalError("simulated startup-sweep failure (AP-17 test)")

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", failing_startup)

        # Constructing HistoryDB must NOT raise even though the startup
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db"):
            db2 = HistoryDB(db_path=db_path)
        try:
            # The HistoryDB instance must be usable for normal
            assert db2.add_transcription("post-failure text") > 0
            db2.flush()
            recent = db2.get_recent(limit=10)
            assert len(recent) >= 1
        finally:
            db2.close()

    def test_startup_sweep_succeeds_silently_at_debug_level(self, tmp_path, monkeypatch, caplog):
        """AP-17: on success, the startup sweep logs at DEBUG (not INFO"""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "history.db"

        startup_calls: list[int] = []
        real_startup = HistoryDB._fts5_startup_rebuild

        def spy_startup(self_db, conn):
            startup_calls.append(1)
            return real_startup(self_db, conn)

        monkeypatch.setattr(HistoryDB, "_fts5_startup_rebuild", spy_startup)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db_internals.writer"):
            db = HistoryDB(db_path=db_path)
        try:
            assert len(startup_calls) >= 1
            # The DEBUG success message must be present.
            debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
            assert any("FTS5 startup rebuild succeeded" in m for m in debug_messages), (
                "expected DEBUG log 'FTS5 startup rebuild succeeded' on a "
                f"successful sweep; got debug messages: {debug_messages}"
            )
            warning_messages = [
                r.getMessage()
                for r in caplog.records
                if r.levelno >= logging.WARNING and "startup rebuild" in r.getMessage().lower()
            ]
            assert warning_messages == [], (
                f"expected no WARNING/ERROR for the startup sweep on a fresh DB; got: {warning_messages}"
            )
        finally:
            db.close()
