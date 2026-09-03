"""Tests for the ``history_db_internals.lifecycle`` extraction.

Pins the delegation contract of the lifecycle cluster: the facade
methods must stay wired to the internals functions (same function
objects, same module-attribute call path) so monkeypatching the
internals module keeps working, and the ``__del__`` non-blocking sweep
must preserve the writer-death semantics (no close(), no join).
"""

import sqlite3
import threading

import pytest
from voice_typer.server.history_db import HistoryDB
from voice_typer.server.history_db_internals import lifecycle


@pytest.fixture
def db(tmp_path):
    instance = HistoryDB(db_path=tmp_path / "lifecycle_test.db")
    yield instance
    instance.close()


class TestFacadeDelegatesToLifecycle:
    """The facade lifecycle methods must call the internals module."""

    def test_close_delegates_to_lifecycle_module(self, db, monkeypatch):
        calls = []
        original = lifecycle.close_db

        def spy(target):
            calls.append(target)
            return original(target)

        monkeypatch.setattr(lifecycle, "close_db", spy)
        db.close()
        assert calls == [db]

    def test_health_check_delegates_to_lifecycle_module(self, db, monkeypatch):
        monkeypatch.setattr(lifecycle, "health_check", lambda target: {"ok": "patched"})
        assert db.health_check() == {"ok": "patched"}


class TestInitializeState:
    """``initialize_state`` must produce the full instance attribute set."""

    def test_initialize_state_writes_all_declared_attributes(self, tmp_path):
        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "manual.db"
        lifecycle.initialize_state(instance)
        for attr in (
            "_read_local",
            "_all_read_connections",
            "_connections_lock",
            "_read_conn_generation",
            "_queue",
            "_writer_ready",
            "_shutdown",
            "_init_error",
            "_retention_lock",
            "_retention_stop_event",
            "_retention_thread",
            "_history_count_cache",
            "_history_count_cache_ts",
            "_history_count_cache_lock",
            "_today_stats_cache",
            "_today_stats_cache_ts",
            "_today_stats_cache_lock",
            "_fts5_rebuild_failures",
            "_fts5_rebuild_ran",
            "_fts_reindex_watermark",
            "_encryption_status",
            "_read_conn_prune_stop_event",
            "_read_conn_prune_thread",
        ):
            assert hasattr(instance, attr), f"missing attribute after initialize_state: {attr}"
        assert instance._history_count_cache is None
        assert instance._init_error is None
        assert instance._encryption_status == "disabled"
        assert instance._queue.maxsize > 0


class TestGcCloseReadConnections:
    """Non-blocking sweep used by ``__del__``."""

    def test_closes_tracked_connections_and_never_blocks(self, tmp_path):
        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "gc.db"
        lifecycle.initialize_state(instance)
        tracked = sqlite3.connect(":memory:")
        other = sqlite3.connect(":memory:")
        instance._all_read_connections.append((threading.get_ident(), tracked))
        instance._all_read_connections.append((threading.get_ident() + 1, other))
        instance._read_local.conn = sqlite3.connect(":memory:")
        local_conn = instance._read_local.conn

        lifecycle.gc_close_read_connections(instance)

        assert instance._all_read_connections == []
        with pytest.raises(sqlite3.ProgrammingError):
            tracked.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            other.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            local_conn.execute("SELECT 1")

    def test_skips_sweep_when_lock_is_held_elsewhere(self, tmp_path):
        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "locked.db"
        lifecycle.initialize_state(instance)
        tracked = sqlite3.connect(":memory:")
        instance._all_read_connections.append((threading.get_ident(), tracked))
        assert instance._connections_lock.acquire(blocking=False)
        try:
            lifecycle.gc_close_read_connections(instance)
            # sweep skipped (lock held elsewhere) — connection untouched
            tracked.execute("SELECT 1")
            assert instance._all_read_connections != []
        finally:
            instance._connections_lock.release()
            tracked.close()

    def test_close_read_connections_closes_everything(self, tmp_path):
        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "close_all.db"
        lifecycle.initialize_state(instance)
        tracked = sqlite3.connect(":memory:")
        instance._all_read_connections.append((threading.get_ident(), tracked))
        lifecycle.close_read_connections(instance)
        assert instance._all_read_connections == []
        with pytest.raises(sqlite3.ProgrammingError):
            tracked.execute("SELECT 1")


class TestWaitForWriterReady:
    """Post-start handshake must surface init errors without raising."""

    def test_returns_after_ready_and_registers_instance(self, tmp_path):
        from voice_typer.server import history_db as facade

        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "ready.db"
        lifecycle.initialize_state(instance)
        stop = threading.Event()
        instance._writer_thread = threading.Thread(target=stop.wait, args=(10,), daemon=True)
        instance._writer_thread.start()
        instance._writer_ready.set()
        instance._start_periodic_read_conn_prune = lambda: None
        try:
            assert instance not in facade._LIVE_INSTANCES
            lifecycle.wait_for_writer_ready(instance)
            assert instance in facade._LIVE_INSTANCES
        finally:
            stop.set()
            facade._LIVE_INSTANCES.discard(instance)

    def test_logs_init_error_without_raising(self, tmp_path, caplog):
        import logging

        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "err.db"
        lifecycle.initialize_state(instance)
        instance._writer_thread = threading.Thread(target=lambda: None, daemon=True)
        instance._writer_ready.set()
        instance._init_error = RuntimeError("schema init failed")
        instance._start_periodic_read_conn_prune = lambda: None
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.history_db_internals.lifecycle"):
            lifecycle.wait_for_writer_ready(instance)
        assert any("Writer thread initialization failed" in r.getMessage() for r in caplog.records)


class TestHealthCheck:
    """The three failure states must surface distinct errors."""

    def _bare(self, tmp_path):
        instance = HistoryDB.__new__(HistoryDB)
        instance.db_path = tmp_path / "health.db"
        lifecycle.initialize_state(instance)
        return instance

    def test_init_error_reported(self, tmp_path):
        instance = self._bare(tmp_path)
        instance._init_error = RuntimeError("migration failed")
        result = lifecycle.health_check(instance)
        assert result["ok"] is False
        assert "migration failed" in result["error"]

    def test_dead_writer_reported(self, tmp_path):
        instance = self._bare(tmp_path)
        instance._writer_thread = threading.Thread(target=lambda: None, daemon=True)
        instance._writer_thread.start()
        instance._writer_thread.join(timeout=5)
        result = lifecycle.health_check(instance)
        assert result["ok"] is False
        assert "not alive" in result["error"]

    def test_not_ready_reported(self, tmp_path):
        instance = self._bare(tmp_path)
        stop = threading.Event()
        instance._writer_thread = threading.Thread(target=stop.wait, args=(10,), daemon=True)
        instance._writer_thread.start()
        try:
            result = lifecycle.health_check(instance)
        finally:
            stop.set()
        assert result["ok"] is False
        assert "still in progress" in result["error"]

    def test_healthy(self, tmp_path):
        instance = self._bare(tmp_path)
        stop = threading.Event()
        instance._writer_thread = threading.Thread(target=stop.wait, args=(10,), daemon=True)
        instance._writer_thread.start()
        instance._writer_ready.set()
        try:
            result = lifecycle.health_check(instance)
        finally:
            stop.set()
        assert result == {"ok": True, "error": None}


class TestCrudSubmitOrchestration:
    """Caller-side submit_* helpers preserve the facade orchestration."""

    def test_submit_delete_invalidates_caches_on_success(self, db, monkeypatch):
        from voice_typer.server.history_db_internals import crud_writes

        db.add_transcription("to delete")
        db.flush()
        rows = db.get_recent(limit=10)
        assert rows
        invalidated = []
        monkeypatch.setattr(db, "_invalidate_history_count_cache", lambda: invalidated.append("count"))
        monkeypatch.setattr(db, "_invalidate_today_stats_cache", lambda: invalidated.append("today"))
        assert crud_writes.submit_delete(db, rows[0]["id"]) is True
        assert invalidated == ["count", "today"]

    def test_submit_delete_returns_false_when_writer_down(self, db):
        from voice_typer.server.history_db_internals import crud_writes

        db._shutdown.set()
        assert crud_writes.submit_delete(db, 123) is False

    def test_submit_toggle_favorite_false_when_writer_down(self, db):
        from voice_typer.server.history_db_internals import crud_writes

        db._shutdown.set()
        assert crud_writes.submit_toggle_favorite(db, 123) is False

    def test_submit_clear_all_returns_false_when_writer_down(self, db):
        from voice_typer.server.history_db_internals import crud_writes

        db._shutdown.set()
        assert crud_writes.submit_clear_all(db) is False

    def test_submit_restore_parses_and_inserts(self, db):
        from voice_typer.server.history_db_internals import crud_writes

        new_id = crud_writes.submit_restore(
            db,
            {"text": "restored text", "duration": 1.5, "model": "m", "device": "d"},
        )
        assert new_id > 0
        rows = db.get_recent(limit=10)
        assert any(r["text"] == "restored text" for r in rows)
