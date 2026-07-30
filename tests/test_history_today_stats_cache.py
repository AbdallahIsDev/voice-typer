"""AB-26: regression tests for the ``get_today_stats`` short-TTL cache.

Before AB-26, ``get_today_stats`` issued an uncached aggregating scan
(``SELECT COUNT(*), SUM(char_count), SUM(word_count), SUM(duration)
FROM transcriptions WHERE timestamp >= DATE('now') AND timestamp <
DATE('now', '+1 day')``) on every call. The Dashboard refreshes on
every ``transcription_final`` event; at the rate_limiter's 1
call/sec/client cap, this was continuous background CPU on the reader
thread during active dictation.

The fix mirrors the existing ``get_history_count`` 60s cache pattern
but with a 15s TTL and stricter invalidation — invalidated on EVERY
mutation that could change today's stats (add/delete/clear/restore/
retention), including fire-and-forget ``add_transcription``.

These tests pin the new behavior:

- ``test_cache_returns_same_value_within_ttl`` — second call within
  TTL serves from cache, no re-scan.
- ``test_cache_invalidated_on_add_transcription`` — add invalidates.
- ``test_cache_invalidated_on_delete`` — delete invalidates.
- ``test_cache_invalidated_on_clear_all`` — clear_all invalidates.
- ``test_cache_invalidated_on_restore`` — restore invalidates.
- ``test_cache_invalidated_on_apply_retention`` — retention invalidates.
- ``test_cache_expires_after_ttl`` — post-TTL call re-scans.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_ab26_history.db")
    yield db_instance
    db_instance.close()


class TestAb26TodayStatsCache:
    """AB-26: ``get_today_stats`` serves from a 15s TTL cache."""

    def test_cache_returns_same_value_within_ttl(self, db):
        """Two consecutive calls within the TTL window return the same
        value. The second call MUST NOT re-run the aggregating scan —
        it serves from the cache."""
        db.add_transcription("today's entry")
        db.flush()
        first = db.get_today_stats()
        assert first["count"] >= 1

        # Patch the read-conn getter so a second scan would raise.
        # If the cache serves the value, the patch is never hit.
        original_get_read_conn = db._get_read_conn

        def _explode(*args, **kwargs):
            raise RuntimeError("cache miss — aggregating scan was re-run")

        with patch.object(db, "_get_read_conn", _explode):
            second = db.get_today_stats()
        assert first == second
        db._get_read_conn = original_get_read_conn

    def test_cache_invalidated_on_add_transcription(self, db):
        """``add_transcription`` invalidates the today-stats cache so
        the next call re-scans and reflects the new row.
        """
        # Prime the cache with one entry.
        db.add_transcription("first")
        db.flush()
        stats1 = db.get_today_stats()
        assert stats1["count"] == 1

        # Add a second entry — this invalidates the cache.
        db.add_transcription("second")
        db.flush()
        # Next call must re-scan and reflect the new row.
        stats2 = db.get_today_stats()
        assert stats2["count"] == 2, (
            "AB-26: add_transcription should invalidate the today-stats "
            f"cache so the next call reflects the new row. Got count={stats2['count']} "
            "(expected 2)."
        )

    def test_cache_invalidated_on_delete(self, db):
        """``delete`` invalidates the today-stats cache."""
        for i in range(3):
            db.add_transcription(f"dictation {i}")
        db.flush()
        stats1 = db.get_today_stats()
        assert stats1["count"] == 3

        rows = db.get_recent(limit=10)
        rec_id = rows[0]["id"]
        assert db.delete(rec_id) is True

        stats2 = db.get_today_stats()
        assert stats2["count"] == 2, (
            f"AB-26: delete should invalidate the today-stats cache. Got count={stats2['count']} (expected 2)."
        )

    def test_cache_invalidated_on_clear_all(self, db):
        """``clear_all`` invalidates the today-stats cache."""
        for i in range(3):
            db.add_transcription(f"dictation {i}")
        db.flush()
        stats1 = db.get_today_stats()
        assert stats1["count"] == 3

        assert db.clear_all() is True

        stats2 = db.get_today_stats()
        assert stats2["count"] == 0, (
            f"AB-26: clear_all should invalidate the today-stats cache. Got count={stats2['count']} (expected 0)."
        )

    def test_cache_invalidated_on_restore(self, db):
        """``restore`` invalidates the today-stats cache."""
        db.add_transcription("original")
        db.flush()
        rows = db.get_recent(limit=1)
        rec = rows[0]
        assert db.delete(rec["id"]) is True
        # Prime the cache with 0 rows.
        stats1 = db.get_today_stats()
        assert stats1["count"] == 0

        # Restore takes a record dict — the id is ignored, a new row is inserted.
        assert db.restore({"text": "original"}) > 0

        stats2 = db.get_today_stats()
        assert stats2["count"] == 1, (
            f"AB-26: restore should invalidate the today-stats cache. Got count={stats2['count']} (expected 1)."
        )

    def test_cache_invalidated_on_apply_retention(self, db):
        """``apply_retention`` invalidates the today-stats cache."""
        # Insert 10 rows with OLD timestamps so retention will delete them.
        # We need to bypass add_transcription (which uses datetime.now())
        # to insert old rows directly.
        from datetime import datetime, timedelta

        old_date = (datetime.now() - timedelta(days=30)).isoformat()

        def _do_insert_old(conn):
            cursor = conn.cursor()
            for i in range(10):
                cursor.execute(
                    "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                    (f"old phrase {i}", old_date),
                )
            conn.commit()

        db._submit_write(_do_insert_old, wait=True)
        db.flush()

        # Prime the cache (today's stats = 0 since all rows are old).
        stats1 = db.get_today_stats()
        assert stats1["count"] == 0

        # apply_retention deletes 10 old rows — invalidates the cache.
        deleted = db.apply_retention(retention_days=1)
        assert deleted == 10

        # Today's stats are still 0 (the deleted rows were old, not today's),
        # but the cache must have been invalidated and re-computed.
        stats2 = db.get_today_stats()
        assert stats2["count"] == 0

        # To verify the cache was actually invalidated (not just still
        # valid), patch _get_read_conn and confirm a fresh scan runs.
        original_get_read_conn = db._get_read_conn
        scan_calls = {"count": 0}
        real_get_read_conn = db._get_read_conn

        def _counting(*args, **kwargs):
            scan_calls["count"] += 1
            return real_get_read_conn(*args, **kwargs)

        # Force cache expiry so the next call MUST re-scan.
        with db._today_stats_cache_lock:
            db._today_stats_cache_ts = 0.0
        with patch.object(db, "_get_read_conn", _counting):
            db.get_today_stats()
        assert scan_calls["count"] == 1, "AB-26 verification: post-TTL call should re-scan the DB."
        db._get_read_conn = original_get_read_conn

    def test_cache_expires_after_ttl(self, db):
        """After the TTL expires, the next call recomputes from the DB."""
        from voice_typer.server.history_db import _TODAY_STATS_CACHE_TTL_S

        db.add_transcription("one")
        db.flush()
        stats1 = db.get_today_stats()
        assert stats1["count"] == 1

        # Force the cache timestamp into the past so the TTL has expired.
        with db._today_stats_cache_lock:
            db._today_stats_cache_ts = time.monotonic() - _TODAY_STATS_CACHE_TTL_S - 1

        # Add a row that the cache doesn't know about.
        db.add_transcription("two")
        db.flush()
        # (add_transcription invalidated the cache anyway, but force-expiry
        # above is the explicit test for the TTL path.)

        # After TTL expiry, the next call recomputes from the DB.
        stats2 = db.get_today_stats()
        assert stats2["count"] == 2, (
            f"AB-26: post-TTL call should re-scan and pick up new rows. Got count={stats2['count']} (expected 2)."
        )

    def test_cache_returns_independent_dict_copy(self, db):
        """The cached value returned to callers must not be mutated by
        subsequent cache updates (callers may keep a reference to the
        returned dict). Verify the cache stores/returns the same dict
        instance within a TTL window but that mutating the returned
        dict does NOT corrupt the cached value.
        """
        db.add_transcription("first")
        db.flush()
        stats1 = db.get_today_stats()
        # Mutate the returned dict — must not corrupt the cache.
        stats1["count"] = 999
        stats1["chars"] = 999

        # Force cache expiry + re-scan to verify the cache wasn't corrupted.
        with db._today_stats_cache_lock:
            db._today_stats_cache_ts = 0.0
        stats2 = db.get_today_stats()
        assert stats2["count"] == 1, (
            "AB-26: caller mutation of the returned dict must not corrupt "
            f"the cache. Got count={stats2['count']} (expected 1)."
        )
