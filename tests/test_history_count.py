"""TY-20: tests for ``HistoryDB.get_history_count`` + cache invalidation.

Verifies that:
- ``get_history_count()`` returns the correct total row count.
- The 60s TTL cache returns the same value within the cache window
  without re-running ``SELECT COUNT(*)``.
- The cache is invalidated immediately on ``delete`` / ``clear_all``
  / ``restore`` / ``apply_retention`` — explicit user actions that
  change the row count by more than 1.
- ``add_transcription`` does NOT invalidate the cache (fire-and-forget
  writes; a 60s-stale-by-N count is fine for a "Total Dictations"
  stat card).

The Dashboard previously used ``get_history({limit: 200})`` for the
"Total Dictations" stat — once the user had > 200 dictations, the
stat capped at 200 forever. The new ``get_history_count`` IPC runs
``SELECT COUNT(*) FROM transcriptions`` (cached for 60s).
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history_count.db")
    yield db_instance
    db_instance.close()


class TestGetHistoryCount:
    """``get_history_count`` returns the correct total row count."""

    def test_empty_db_returns_zero(self, db):
        """TY-20: a fresh DB has zero rows → count is 0."""
        assert db.get_history_count() == 0

    def test_returns_correct_count_after_inserts(self, db):
        """TY-20: after N fire-and-forget inserts + flush, count is N."""
        for i in range(5):
            db.add_transcription(f"dictation {i}")
        db.flush()
        assert db.get_history_count() == 5

    def test_returns_correct_count_after_delete(self, db):
        """TY-20: after deleting one row, count reflects the deletion."""
        for i in range(3):
            db.add_transcription(f"dictation {i}")
        db.flush()
        rows = db.get_recent(limit=10)
        rec_id = rows[0]["id"]
        assert db.delete(rec_id) is True
        assert db.get_history_count() == 2

    def test_returns_correct_count_after_clear_all(self, db):
        """TY-20: after clear_all, count is 0."""
        for i in range(5):
            db.add_transcription(f"dictation {i}")
        db.flush()
        assert db.clear_all() is True
        assert db.get_history_count() == 0

    def test_returns_correct_count_after_restore(self, db):
        """TY-20: after restoring a row, count increases by 1."""
        db.add_transcription("original")
        db.flush()
        rows = db.get_recent(limit=1)
        rec = rows[0]
        assert db.delete(rec["id"]) is True
        assert db.get_history_count() == 0
        # Restore takes a record dict — the id is ignored, a new row is inserted.
        assert db.restore({"text": "original"}) > 0
        assert db.get_history_count() == 1

    def test_returns_correct_count_after_apply_retention(self, db):
        """TY-20: after a retention sweep deletes rows, count reflects it."""
        for i in range(10):
            db.add_transcription(f"dictation {i}")
        db.flush()
        # max_entries=5 → 5 rows deleted by retention.
        deleted = db.apply_retention(max_entries=5)
        assert deleted == 5
        assert db.get_history_count() == 5


class TestHistoryCountCache:
    """The 60s TTL cache avoids repeated COUNT(*) scans."""

    def test_cache_returns_same_value_within_ttl(self, db):
        """TY-20: two consecutive calls within the TTL window return
        the same value. The second call MUST NOT re-run COUNT(*) —
        it serves from the cache."""
        db.add_transcription("one")
        db.flush()
        first = db.get_history_count()
        # Patch the read-conn getter so a second COUNT(*) would raise.
        # If the cache serves the value, the patch is never hit.
        original_get_read_conn = db._get_read_conn

        def _explode(*args, **kwargs):
            raise RuntimeError("cache miss — COUNT(*) was re-run")

        with patch.object(db, "_get_read_conn", _explode):
            second = db.get_history_count()
        assert first == second == 1
        # Restore for the fixture teardown path.
        db._get_read_conn = original_get_read_conn

    def test_cache_invalidated_on_delete(self, db):
        """TY-20: ``delete`` invalidates the cache so the next call
        recomputes (rather than serving the stale pre-delete count)."""
        for i in range(3):
            db.add_transcription(f"dictation {i}")
        db.flush()
        # Prime the cache.
        assert db.get_history_count() == 3
        rows = db.get_recent(limit=10)
        rec_id = rows[0]["id"]
        assert db.delete(rec_id) is True
        # After invalidation, the next call must recompute.
        assert db.get_history_count() == 2

    def test_cache_invalidated_on_clear_all(self, db):
        """TY-20: ``clear_all`` invalidates the cache."""
        for i in range(3):
            db.add_transcription(f"dictation {i}")
        db.flush()
        assert db.get_history_count() == 3
        assert db.clear_all() is True
        # Without invalidation, this would still return 3 (stale).
        assert db.get_history_count() == 0

    def test_cache_invalidated_on_apply_retention(self, db):
        """TY-20: ``apply_retention`` invalidates the cache."""
        for i in range(10):
            db.add_transcription(f"dictation {i}")
        db.flush()
        assert db.get_history_count() == 10
        deleted = db.apply_retention(max_entries=5)
        assert deleted == 5
        # Without invalidation, this would still return 10 (stale).
        assert db.get_history_count() == 5

    def test_add_transcription_does_not_invalidate_cache(self, db):
        """TY-20: fire-and-forget ``add_transcription`` does NOT
        invalidate the cache. The 60s TTL window tolerates a
        stale-by-N count for the "Total Dictations" stat card —
        invalidating on every dictation would defeat the cache.

        This test verifies the cache is still warm (would raise on a
        cache miss) after an ``add_transcription`` call.
        """
        db.add_transcription("first")
        db.flush()
        # Prime the cache.
        assert db.get_history_count() == 1

        original_get_read_conn = db._get_read_conn

        def _explode(*args, **kwargs):
            raise RuntimeError("cache miss after add_transcription")

        # add_transcription is fire-and-forget; flush drains the queue.
        db.add_transcription("second")
        db.flush()

        with patch.object(db, "_get_read_conn", _explode):
            # The cache is still warm — add_transcription didn't invalidate.
            # The cached value is 1 (stale by 1) — that's the documented
            # behavior. Within the 60s TTL window, the count may lag the
            # true row count by the number of dictations since the last
            # cache fill.
            cached = db.get_history_count()
        assert cached == 1, (
            "add_transcription should NOT invalidate the cache; the "
            "cached value may be stale by N (here N=1) within the TTL window"
        )
        db._get_read_conn = original_get_read_conn

    def test_cache_expires_after_ttl(self, db):
        """TY-20: after the TTL expires, the next call recomputes."""
        from voice_typer.server.history_db import _HISTORY_COUNT_CACHE_TTL_S

        db.add_transcription("one")
        db.flush()
        assert db.get_history_count() == 1
        # Force the cache timestamp into the past so the TTL has expired.
        with db._history_count_cache_lock:
            db._history_count_cache_ts = (
                time.monotonic() - _HISTORY_COUNT_CACHE_TTL_S - 1
            )
        # Add a row that the cache doesn't know about.
        db.add_transcription("two")
        db.flush()
        # After TTL expiry, the next call recomputes from the DB.
        assert db.get_history_count() == 2
