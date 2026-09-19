"""same ``ratio > 0.20`` threshold as VACUUM inside ``apply_retention``."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_ab25_history.db")
    yield db_instance
    db_instance.close()


def _spy_submit_write(db, monkeypatch, executed_sql: list[str]):
    """pattern used by ``tests/test_history_db_fts5_rebuild.py``."""
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


def _rebuild_seen(executed_sql: list[str]) -> bool:
    return any("transcriptions_fts" in sql and "rebuild" in sql.lower() for sql in executed_sql)


def _optimize_seen(executed_sql: list[str]) -> bool:
    return any("transcriptions_fts" in sql and "optimize" in sql.lower() for sql in executed_sql)


class TestAb25FtsRebuildGate:
    """AB-25: ``apply_retention`` only rebuilds FTS5 when ``ratio > 0.20``."""

    def test_apply_retention_skips_fts5_rebuild_when_ratio_below_threshold(self, db, monkeypatch):
        """A small delete (<20% of rows) must NOT issue the FTS5 'rebuild'"""
        # Insert 20 recent rows (retention_days will not delete them).
        for i in range(20):
            db.add_transcription(f"recent phrase {i}")
        db.flush()

        # Insert 1 OLD row that retention_days=1 will delete.
        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert_old(conn):
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                ("old secret phrase", old_date),
            )
            conn.commit()

        db._submit_write(_do_insert_old, wait=True)
        db.flush()

        executed_sql: list[str] = []
        _spy_submit_write(db, monkeypatch, executed_sql)

        # Act: retention_days=1 deletes the 1 old row out of 21 total.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 1, f"expected 1 row deleted, got {deleted}"

        # VACUUM and FTS5 'rebuild' must BOTH be skipped (ratio < 0.20).
        assert not _rebuild_seen(executed_sql), (
            "AB-25 violation: apply_retention issued the FTS5 'rebuild' "
            f"command for a small delete (1/21 ≈ 4.8% < 20% threshold). "
            f"Executed SQL: {executed_sql}"
        )
        # VACUUM must also be skipped (sanity check, pre-existing behavior).
        vacuum_seen = any(sql.strip().upper() == "VACUUM" for sql in executed_sql)
        assert not vacuum_seen, f"VACUUM should also be skipped at ratio < 0.20. Executed SQL: {executed_sql}"

    def test_apply_retention_runs_fts5_optimize_when_ratio_below_threshold(self, db, monkeypatch):
        """A small delete (<20% of rows) must issue the cheap FTS5"""
        # Insert 20 recent rows (retention_days will not delete them).
        for i in range(20):
            db.add_transcription(f"recent phrase {i}")
        db.flush()

        # Insert 1 OLD row that retention_days=1 will delete.
        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert_old(conn):
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                ("old secret phrase", old_date),
            )
            conn.commit()

        db._submit_write(_do_insert_old, wait=True)
        db.flush()

        executed_sql: list[str] = []
        _spy_submit_write(db, monkeypatch, executed_sql)

        # Act: retention_days=1 deletes the 1 old row out of 21 total.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 1, f"expected 1 row deleted, got {deleted}"

        # The cheap 'optimize' MUST have been issued (privacy hardening
        assert _optimize_seen(executed_sql), (
            "apply_retention did NOT issue the FTS5 'optimize' command for "
            f"a small delete (1/21 ≈ 4.8% < 20% threshold). "
            f"Executed SQL: {executed_sql}"
        )
        # ...while the O(N) 'rebuild' must NOT fire (gate preserved).
        assert not _rebuild_seen(executed_sql), (
            "apply_retention issued the FTS5 'rebuild' command for a "
            f"small delete (1/21 ≈ 4.8% < 20% threshold), the O(N) gate "
            f"must be preserved. Executed SQL: {executed_sql}"
        )

    def test_apply_retention_runs_fts5_rebuild_when_ratio_above_threshold(self, db, monkeypatch):
        """A large delete (>20% of rows) MUST still issue the FTS5 'rebuild'"""
        # Insert 5 recent rows (retention will keep them).
        for i in range(5):
            db.add_transcription(f"recent phrase {i}")
        db.flush()

        # Insert 20 OLD rows that retention_days=1 will delete.
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

        executed_sql: list[str] = []
        _spy_submit_write(db, monkeypatch, executed_sql)

        # Act: retention_days=1 deletes 20 of 25 rows.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 20, f"expected 20 rows deleted, got {deleted}"

        # FTS5 'rebuild' MUST have been issued ( privacy guarantee).
        assert _rebuild_seen(executed_sql), (
            "AB-25 regression: apply_retention did NOT issue the FTS5 "
            "'rebuild' command for a large delete (20/25 = 80% > 20% "
            "threshold). The FR-27 privacy guarantee (deleted dictated "
            "text not recoverable from transcriptions_fts_data) is broken. "
            f"Executed SQL: {executed_sql}"
        )

    def test_apply_retention_skips_fts5_rebuild_when_nothing_deleted(self, db, monkeypatch):
        """When apply_retention deletes nothing, the rebuild command is"""
        # Empty DB → nothing to delete.
        executed_sql: list[str] = []
        _spy_submit_write(db, monkeypatch, executed_sql)

        deleted = db.apply_retention(retention_days=999)
        assert deleted == 0

        assert not _rebuild_seen(executed_sql), (
            "apply_retention should NOT issue the FTS5 'rebuild' command when nothing was deleted."
        )

    def test_apply_retention_at_boundary_ratio_just_above_threshold(self, db, monkeypatch):
        """Boundary check: ratio slightly above 0.20 must trigger rebuild."""
        # 3 recent + 2 old = 5 total; delete 2 → ratio = 0.40.
        for i in range(3):
            db.add_transcription(f"recent phrase {i}")
        db.flush()

        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert_old(conn):
            cursor = conn.cursor()
            for i in range(2):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                    (f"old secret phrase {i}", old_date),
                )
            conn.commit()

        db._submit_write(_do_insert_old, wait=True)
        db.flush()

        executed_sql: list[str] = []
        _spy_submit_write(db, monkeypatch, executed_sql)

        deleted = db.apply_retention(retention_days=1)
        assert deleted == 2

        assert _rebuild_seen(executed_sql), (
            "AB-25: apply_retention should issue FTS5 'rebuild' at "
            f"ratio=0.40 (> 0.20 threshold). Executed SQL: {executed_sql}"
        )
