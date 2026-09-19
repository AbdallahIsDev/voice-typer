"""DJ-18 regression tests: every cursor in history_db is closed."""

from __future__ import annotations

import contextlib
import sqlite3
from unittest.mock import MagicMock

import pytest


class TrackedCursor:
    """Wraps a real ``sqlite3.Cursor`` and records ``close()`` calls."""

    def __init__(self, real_cursor: sqlite3.Cursor, tracker: TrackedConnection) -> None:
        self._real = real_cursor
        self._tracker = tracker
        self.closed = False

    def execute(self, sql, *args, **kwargs):
        return self._real.execute(sql, *args, **kwargs)

    def executescript(self, sql_script):
        return self._real.executescript(sql_script)

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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        self.closed = True
        self._tracker.cursors_closed += 1
        return self._real.close()


class TrackedConnection:
    """Wraps a real ``sqlite3.Connection`` so its cursors are tracked."""

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self._real = real_conn
        self.cursors_created = 0
        self.cursors_closed = 0

    def cursor(self) -> TrackedCursor:
        self.cursors_created += 1
        return TrackedCursor(self._real.cursor(), self)

    def execute(self, sql, *args, **kwargs):
        return self._real.execute(sql, *args, **kwargs)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def close(self):
        return self._real.close()

    @property
    def row_factory(self):
        return self._real.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._real.row_factory = value

    def all_cursors_closed(self) -> bool:
        """Return True iff every cursor created was also closed."""
        return self.cursors_created == self.cursors_closed


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history_cursor.db")
    yield db_instance
    db_instance.close()


@pytest.mark.parametrize(
    "method,kwargs,setup",
    [
        ("get_recent", {"limit": 10}, lambda db: _add_and_flush(db, "x")),
        ("get_latest_text", {}, lambda db: _add_and_flush(db, "x")),
        ("search", {"query": "x"}, lambda db: _add_and_flush(db, "x")),
        ("get_favorites", {}, lambda db: _add_and_flush(db, "x")),
        ("get_today_stats", {}, lambda db: _add_and_flush(db, "x")),
        ("get_transcription_text", {"transcription_id": 1}, lambda db: _add_and_flush(db, "x")),
        ("get_history_count", {}, lambda db: _add_and_flush(db, "x")),
    ],
)
def test_read_method_closes_cursor(db, monkeypatch, method, kwargs, setup):
    """Each read method must close the cursor it opens."""
    setup(db)

    # Replace the thread-local read conn with a TrackedConnection.
    real_conn = db._get_read_conn()
    tracked = TrackedConnection(real_conn)
    monkeypatch.setattr(db, "_get_read_conn", lambda: tracked)

    getattr(db, method)(**kwargs)

    assert tracked.cursors_created >= 1, f"{method} did not create a cursor"
    assert tracked.all_cursors_closed(), (
        f"{method} leaked cursors: created={tracked.cursors_created}, closed={tracked.cursors_closed}"
    )


@pytest.mark.parametrize(
    "method,kwargs,setup",
    [
        ("delete", {"transcription_id": 1}, lambda db: _add_and_flush(db, "hello")),
        ("restore", {"record": {"text": "restored"}}, lambda db: None),
        ("clear_all", {}, lambda db: _add_and_flush(db, "hello")),
        ("toggle_favorite", {"transcription_id": 1}, lambda db: _add_and_flush(db, "hello")),
    ],
)
def test_write_method_closes_cursor(db, monkeypatch, method, kwargs, setup):
    """Each write method's inner closure must close its cursor."""
    setup(db)

    real_conn = db._open_write_conn() if not db._shutdown.is_set() else None
    if real_conn is None:
        pytest.skip("writer already shut down")
    tracked = TrackedConnection(real_conn)
    try:

        def capture(fn, *, wait=True):
            return fn(tracked)

        monkeypatch.setattr(db, "_submit_write", capture)
        getattr(db, method)(**kwargs)
    finally:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(db, "_shutdown", type(db._shutdown)())
        # Close our temporary tracked underlying connection.
        import contextlib

        with contextlib.suppress(sqlite3.Error):
            real_conn.close()

    assert tracked.cursors_created >= 1, f"{method} did not create a cursor"
    assert tracked.all_cursors_closed(), (
        f"{method} leaked cursors: created={tracked.cursors_created}, closed={tracked.cursors_closed}"
    )


def test_drain_batchable_inserts_closes_cursor(db):
    """``_drain_batchable_inserts`` must close its cursor even on the"""
    from voice_typer.server.history_db import _BatchableInsert

    real_conn = db._open_write_conn() if not db._shutdown.is_set() else None
    if real_conn is None:
        pytest.skip("writer already shut down")
    tracked = TrackedConnection(real_conn)
    try:
        item = _BatchableInsert(
            text="single",
            duration=0.0,
            model="",
            device="",
            word_count=1,
            char_count=6,
            language="",
            future=None,
        )
        db._drain_batchable_inserts(tracked, item)
    finally:
        with contextlib.suppress(sqlite3.Error):
            real_conn.close()

    assert tracked.cursors_created >= 1
    assert tracked.all_cursors_closed(), (
        f"_drain_batchable_inserts leaked cursors: created={tracked.cursors_created}, closed={tracked.cursors_closed}"
    )


def test_drain_batchable_inserts_closes_cursor_on_exception(db, monkeypatch):
    """``_drain_batchable_inserts`` must close the cursor even when the"""
    from voice_typer.server.history_db import _BatchableInsert

    real_conn = db._open_write_conn() if not db._shutdown.is_set() else None
    if real_conn is None:
        pytest.skip("writer already shut down")
    tracked = TrackedConnection(real_conn)

    # Force the multi-row INSERT path to raise by making the real
    real_cursor_method = tracked._real.cursor

    class FailingCursor(TrackedCursor):
        def execute(self, sql, *args, **kwargs):
            if "INSERT" in sql.upper():
                raise sqlite3.OperationalError("simulated insert failure")
            return super().execute(sql, *args, **kwargs)

    def failing_cursor():
        tracked.cursors_created += 1
        return FailingCursor(real_cursor_method(), tracked)

    monkeypatch.setattr(tracked, "cursor", failing_cursor)
    try:
        item = _BatchableInsert(
            text="bad",
            duration=0.0,
            model="",
            device="",
            word_count=1,
            char_count=3,
            language="",
            future=None,
        )
        with pytest.raises(sqlite3.OperationalError):
            db._drain_batchable_inserts(tracked, item)
    finally:
        with contextlib.suppress(sqlite3.Error):
            real_conn.close()

    assert tracked.cursors_created >= 1
    assert tracked.all_cursors_closed(), (
        f"_drain_batchable_inserts leaked cursors on exception: "
        f"created={tracked.cursors_created}, closed={tracked.cursors_closed}"
    )


def test_apply_retention_closes_cursor(db):
    """``apply_retention`` (retention.py) must close its cursor (DJ-18)."""
    # Seed the DB with rows so retention has something to delete.
    for _ in range(5):
        db.add_transcription("row")
    db.flush()

    real_conn = db._open_write_conn() if not db._shutdown.is_set() else None
    if real_conn is None:
        pytest.skip("writer already shut down")
    tracked = TrackedConnection(real_conn)
    try:

        def capture(fn, *, wait=True):
            return fn(tracked)

        # ``apply_retention`` ultimately calls ``db._submit_write``
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(db, "_submit_write", capture)
            db.apply_retention(max_entries=2)
    finally:
        with contextlib.suppress(sqlite3.Error):
            real_conn.close()

    assert tracked.cursors_created >= 1
    assert tracked.all_cursors_closed(), (
        f"apply_retention leaked cursors: created={tracked.cursors_created}, closed={tracked.cursors_closed}"
    )


def test_init_schema_closes_cursor(tmp_path):
    """``schema.init_schema`` must close its cursor (DJ-18)."""
    from voice_typer.server.history_db_internals import schema

    db_path = tmp_path / "test_schema.db"
    real_conn = sqlite3.connect(str(db_path))
    tracked = TrackedConnection(real_conn)
    db_mock = MagicMock()
    db_mock.db_path = db_path
    db_mock._backup_before_migration = MagicMock()
    db_mock._maybe_recover_from_corruption = MagicMock(return_value=None)
    try:
        schema.init_schema(db_mock, tracked)
    finally:
        with contextlib.suppress(sqlite3.Error):
            real_conn.close()

    assert tracked.cursors_created >= 1
    assert tracked.all_cursors_closed(), (
        f"init_schema leaked cursors: created={tracked.cursors_created}, closed={tracked.cursors_closed}"
    )


def _add_and_flush(db, text: str) -> None:
    """Add a transcription and wait for the writer to drain it."""
    db.add_transcription(text)
    db.flush()
