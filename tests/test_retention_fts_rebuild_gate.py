"""AB-25: regression tests for gating the FTS5 'rebuild' command on the
same ``ratio > 0.20`` threshold as VACUUM inside ``apply_retention``.

Before AB-25 the rebuild ran whenever ``deleted > 0`` — even a 1-row
delete out of 50 000 triggered a full O(N) FTS5 re-index on every
10-minute periodic-retention tick. The FTS5 delete-bitmap (populated
by the ``transcriptions_ad_fts`` trigger) already hides deleted rows
from MATCH results, so the only thing the rebuild reclaims is segment
data in ``transcriptions_fts_data`` — which is only worth the O(N) cost
after a large purge.

These tests pin the new behavior:

- ``test_apply_retention_skips_fts5_rebuild_when_ratio_below_threshold``
  — a small delete (<20% of rows) must NOT issue the 'rebuild' command.
- ``test_apply_retention_runs_fts5_rebuild_when_ratio_above_threshold``
  — a large delete (>20% of rows) MUST still issue the 'rebuild'
  command (the existing FR-27 privacy guarantee is preserved).
- ``test_apply_retention_skips_fts5_rebuild_when_nothing_deleted``
  — the no-op sweep case (pre-existing, mirrors the FR-27 test).
"""

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
    """Patch ``db._submit_write`` so every SQL statement issued on the
    writer connection is appended to ``executed_sql``. Mirrors the
    pattern used by ``tests/test_history_db_fts5_rebuild.py``.
    """
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


class TestAb25FtsRebuildGate:
    """AB-25: ``apply_retention`` only rebuilds FTS5 when ``ratio > 0.20``."""

    def test_apply_retention_skips_fts5_rebuild_when_ratio_below_threshold(self, db, monkeypatch):
        """A small delete (<20% of rows) must NOT issue the FTS5 'rebuild'
        command. The delete-bitmap trigger already hides the deleted
        rows from MATCH results; the only thing 'rebuild' would
        reclaim is segment data, which isn't worth an O(N) re-index
        for a handful of deletes.
        """
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
        # ratio = 1/21 ≈ 4.8% — well below the 20% gate.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 1, f"expected 1 row deleted, got {deleted}"

        # VACUUM and FTS5 'rebuild' must BOTH be skipped (ratio < 0.20).
        assert not _rebuild_seen(executed_sql), (
            "AB-25 violation: apply_retention issued the FTS5 'rebuild' "
            f"command for a small delete (1/21 ≈ 4.8% < 20% threshold). "
            f"Executed SQL: {executed_sql}"
        )
        # VACUUM must also be skipped (sanity check — pre-existing behavior).
        vacuum_seen = any(sql.strip().upper() == "VACUUM" for sql in executed_sql)
        assert not vacuum_seen, f"VACUUM should also be skipped at ratio < 0.20. Executed SQL: {executed_sql}"

    def test_apply_retention_runs_fts5_rebuild_when_ratio_above_threshold(self, db, monkeypatch):
        """A large delete (>20% of rows) MUST still issue the FTS5 'rebuild'
        command. This preserves the FR-27 privacy guarantee: deleted
        dictated text must not remain recoverable from
        ``transcriptions_fts_data`` after a large purge.
        """
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
        # ratio = 20/25 = 80% — well above the 20% gate.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 20, f"expected 20 rows deleted, got {deleted}"

        # FTS5 'rebuild' MUST have been issued (FR-27 privacy guarantee).
        assert _rebuild_seen(executed_sql), (
            "AB-25 regression: apply_retention did NOT issue the FTS5 "
            "'rebuild' command for a large delete (20/25 = 80% > 20% "
            "threshold). The FR-27 privacy guarantee (deleted dictated "
            "text not recoverable from transcriptions_fts_data) is broken. "
            f"Executed SQL: {executed_sql}"
        )

    def test_apply_retention_skips_fts5_rebuild_when_nothing_deleted(self, db, monkeypatch):
        """When apply_retention deletes nothing, the rebuild command is
        skipped (a no-op sweep has nothing to rebuild). This is the
        pre-existing FR-27 behavior — preserved by AB-25.
        """
        # Empty DB → nothing to delete.
        executed_sql: list[str] = []
        _spy_submit_write(db, monkeypatch, executed_sql)

        deleted = db.apply_retention(retention_days=999)
        assert deleted == 0

        assert not _rebuild_seen(executed_sql), (
            "apply_retention should NOT issue the FTS5 'rebuild' command when nothing was deleted."
        )

    def test_apply_retention_at_boundary_ratio_just_above_threshold(self, db, monkeypatch):
        """Boundary check: ratio slightly above 0.20 must trigger rebuild.

        Uses 5 rows total, deletes 2 (ratio = 0.40 > 0.20). Verifies
        the gate is ``>`` not ``>=`` and that small-N cases still work.
        """
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

        # ratio = 2/5 = 40% > 20% → rebuild MUST fire.
        assert _rebuild_seen(executed_sql), (
            "AB-25: apply_retention should issue FTS5 'rebuild' at "
            f"ratio=0.40 (> 0.20 threshold). Executed SQL: {executed_sql}"
        )
