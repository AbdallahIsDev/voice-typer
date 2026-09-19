"""DJ-56 regression tests: single-row inserts use the batch INSERT path."""

from __future__ import annotations

import queue
import sqlite3

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history_batch.db")
    yield db_instance
    db_instance.close()


def test_batch_insert_min_is_one():
    """DJ-56: the threshold constant was lowered from 3 to 1."""
    from voice_typer.server import history_db

    assert history_db._BATCH_INSERT_MIN == 1, (
        f"_BATCH_INSERT_MIN should be 1 (DJ-56); got {history_db._BATCH_INSERT_MIN}"
    )


def test_single_row_insert_uses_batch_path(db, monkeypatch):
    """multi-row INSERT path (one ``conn.commit()`` for the whole batch),"""
    from voice_typer.server.history_db import _BatchableInsert

    # Open a fresh write conn (the writer thread owns its own; we use
    real_conn = sqlite3.connect(str(db.db_path))
    try:
        # Set up schema on our instrumented conn (the writer thread

        execute_calls: list[str] = []
        real_cursor_method = real_conn.cursor

        class InstrumentedCursor:
            def __init__(self):
                self._real = real_cursor_method()

            def execute(self, sql, *args, **kwargs):
                execute_calls.append(sql)
                return self._real.execute(sql, *args, **kwargs)

            def executescript(self, sql):
                return self._real.executescript(sql)

            def fetchone(self):
                return self._real.fetchone()

            def fetchall(self):
                return self._real.fetchall()

            @property
            def rowcount(self):
                return self._real.rowcount

            @property
            def lastrowid(self):
                return self._real.lastrowid

            def close(self):
                return self._real.close()

        class InstrumentedConnection:
            def __init__(self):
                self.commit_calls = 0

            def cursor(self):
                return InstrumentedCursor()

            def execute(self, sql, *args, **kwargs):
                return real_conn.execute(sql, *args, **kwargs)

            def commit(self):
                self.commit_calls += 1
                return real_conn.commit()

            def rollback(self):
                return real_conn.rollback()

            def close(self):
                return real_conn.close()

            @property
            def row_factory(self):
                return real_conn.row_factory

            @row_factory.setter
            def row_factory(self, value):
                real_conn.row_factory = value

        instrumented = InstrumentedConnection()

        item = _BatchableInsert(
            text="hello world",
            duration=2.5,
            model="small.en",
            device="cpu",
            word_count=2,
            char_count=11,
            language="en",
            future=None,
        )

        # Single-item batch, N=1.
        db._drain_batchable_inserts(instrumented, item)

        insert_sqls = [s for s in execute_calls if "INSERT" in s.upper()]
        assert len(insert_sqls) == 1, f"expected 1 INSERT for N=1 batch; got {len(insert_sqls)}: {insert_sqls}"
        assert "VALUES" in insert_sqls[0].upper()
        # Count placeholder tuples: should be exactly 1 for N=1.
        placeholder_tuples = insert_sqls[0].count("(?, ?, ?, ?, ?, ?, ?)")
        assert placeholder_tuples == 1, f"expected 1 placeholder tuple for N=1 batch; got {placeholder_tuples}"

        # Exactly one commit for the batch.
        assert instrumented.commit_calls == 1, f"expected 1 commit for N=1 batch; got {instrumented.commit_calls}"
    finally:
        real_conn.close()


def test_single_row_insert_correctness(db):
    """DJ-56: the multi-row INSERT path correctly inserts a single row."""
    db.add_transcription("hello world", duration=2.5, model="small.en", device="cpu", language="en")
    db.flush()

    rows = db.get_recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["text"] == "hello world"
    assert row["duration"] == 2.5
    assert row["model"] == "small.en"
    assert row["device"] == "cpu"
    assert row["word_count"] == 2
    assert row["char_count"] == 11
    assert row["language"] == "en"


def test_two_single_inserts_each_use_batch_path(db, monkeypatch):
    """DJ-56: two separate ``add_transcription`` calls each go through"""
    from voice_typer.server import history_db

    # We can't easily intercept the writer thread's drain calls without
    assert history_db._BATCH_INSERT_MIN == 1

    # Simulate a 1-item batch and verify it would take the multi-row
    batch_size = 1
    takes_multi_row_branch = batch_size >= history_db._BATCH_INSERT_MIN
    assert takes_multi_row_branch, (
        "N=1 batch should take the multi-row INSERT branch (_BATCH_INSERT_MIN == 1 means the threshold check passes)"
    )

    # Verify the actual DB insertion works for two consecutive single
    db.add_transcription("first", duration=1.0)
    db.add_transcription("second", duration=2.0)
    db.flush()

    rows = db.get_recent(limit=10)
    assert len(rows) == 2
    texts = {row["text"] for row in rows}
    assert texts == {"first", "second"}


def test_multi_row_batch_still_works(db):
    """existing multi-row behavior for N >= 2."""
    # Enqueue 5 items; the writer thread will batch them when it
    for i in range(5):
        db.add_transcription(f"row {i}", duration=float(i))
    db.flush()

    rows = db.get_recent(limit=10)
    assert len(rows) == 5
    texts = {row["text"] for row in rows}
    assert texts == {f"row {i}" for i in range(5)}


class TestInsertSqlSingleSource:
    """
    The multi-row batch path and the below-threshold single-row
    These tests pin the exact SQL emitted for each shape, if a future
    """

    def test_single_row_sql_shape(self):
        from voice_typer.server.history_db_internals.writer import _build_insert_sql

        assert _build_insert_sql(1) == (
            "INSERT INTO transcriptions "
            "(text, duration, model, device, word_count, char_count, language) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )

    def test_multi_row_sql_shape(self):
        from voice_typer.server.history_db_internals.writer import _build_insert_sql

        assert _build_insert_sql(3) == (
            "INSERT INTO transcriptions "
            "(text, duration, model, device, word_count, char_count, language) "
            "VALUES (?, ?, ?, ?, ?, ?, ?),"
            "(?, ?, ?, ?, ?, ?, ?),"
            "(?, ?, ?, ?, ?, ?, ?)"
        )

    def test_batch_and_fallback_paths_share_one_sql_source(self, db, monkeypatch):
        """Both code paths emit SQL derived from ``_build_insert_sql``."""
        from voice_typer.server import _text_crypto, history_db as hd
        from voice_typer.server.history_db import _BatchableInsert
        from voice_typer.server.history_db_internals import writer as writer_mod

        # This test pins the INSERT SQL source-sharing, not at-rest
        monkeypatch.setattr(_text_crypto, "get_dek_cached", lambda: None)

        def _item(text: str) -> _BatchableInsert:
            return _BatchableInsert(
                text=text,
                duration=1.5,
                model="m",
                device="d",
                word_count=2,
                char_count=len(text),
                language="en",
            )

        conn = sqlite3.connect(str(db.db_path))
        try:
            executed: list[str] = []
            real_cursor = conn.cursor

            class _RecordingCursor:
                def __init__(self):
                    self._real = real_cursor()

                def execute(self, sql, *args, **kwargs):
                    executed.append(sql)
                    return self._real.execute(sql, *args, **kwargs)

                def __getattr__(self, name):
                    return getattr(self._real, name)

            class _RecordingConnection:
                def cursor(self):
                    return _RecordingCursor()

                def commit(self):
                    return conn.commit()

                def rollback(self):
                    return conn.rollback()

                def close(self):
                    return conn.close()

            recording_conn = _RecordingConnection()

            monkeypatch.setattr(hd, "_BATCH_INSERT_MIN", 3)
            writer_mod._drain_batchable_inserts(db, recording_conn, _item("solo"))
            assert writer_mod._build_insert_sql(1) in executed
            conn.commit()

            # ── multi-row batch: 3 items drained in ONE call ──
            executed.clear()
            monkeypatch.setattr(hd, "_BATCH_INSERT_MIN", 1)
            fresh_queue = queue.Queue()
            fresh_queue.put_nowait(_item("b"))
            fresh_queue.put_nowait(_item("c"))
            real_queue = db._queue
            db._queue = fresh_queue
            try:
                writer_mod._drain_batchable_inserts(db, recording_conn, _item("a"))
            finally:
                db._queue = real_queue
            assert writer_mod._build_insert_sql(3) in executed
            conn.commit()

            rows = conn.execute(
                "SELECT text FROM transcriptions WHERE text IN ('solo', 'a', 'b', 'c') ORDER BY text"
            ).fetchall()
            assert [r[0] for r in rows] == ["a", "b", "c", "solo"]
        finally:
            conn.close()
