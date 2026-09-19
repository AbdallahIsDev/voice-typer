"""Tests for the data-store save and write performance fixes."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest


class TestSchedulePeriodicRetention:
    """ER-36: ``HistoryDB.schedule_periodic_retention`` API contract."""

    def test_spawns_daemon_thread_that_calls_apply_retention(self, tmp_path, monkeypatch):
        """periodically calls ``apply_retention``."""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "sched.db")
        try:
            called_event = threading.Event()
            call_count = {"n": 0}

            def _spy_apply_retention(*args, **kwargs):
                call_count["n"] += 1
                called_event.set()
                # Don't actually run the retention sweep, we just want
                return 0

            monkeypatch.setattr(db, "apply_retention", _spy_apply_retention)

            # Spawn the periodic thread with a 50ms interval.
            db.schedule_periodic_retention(
                interval_s=0.05,
                app=None,
                max_entries=10,  # static fallback (app is None)
            )

            # Verify a daemon thread was spawned.
            assert db._retention_thread is not None, "schedule_periodic_retention should set _retention_thread"
            assert db._retention_thread.daemon is True, (
                "periodic retention thread must be a daemon so it doesn't block process exit"
            )
            assert db._retention_thread.name == "HistoryDBPeriodicRetention"

            # Wait for the first tick (50ms interval + slack).
            assert called_event.wait(timeout=5.0), (
                "periodic retention thread did not call apply_retention "
                "within 5s, the daemon-thread loop is not running."
            )
            assert call_count["n"] >= 1
        finally:
            db.close()

    def test_close_stops_periodic_retention_thread(self, tmp_path, monkeypatch):
        """``close()`` must signal + join the periodic retention thread."""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "sched_close.db")
        # Stub apply_retention so the loop doesn't do real DB work.
        monkeypatch.setattr(db, "apply_retention", lambda **kw: 0)
        db.schedule_periodic_retention(interval_s=0.05, app=None, max_entries=10)
        thread = db._retention_thread
        assert thread is not None
        # Give the thread a moment to enter its wait loop.
        time.sleep(0.1)
        assert thread.is_alive()

        db.close()

        # The thread should have exited within close()'s 2s join.
        assert not thread.is_alive(), (
            "periodic retention thread was still alive after close(), "
            "close() must signal the stop_event and join the thread."
        )

    def test_reentrancy_guard_skips_concurrent_retention(self, tmp_path, monkeypatch):
        """ER-36: if a previous retention is still running when the next"""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "sched_reent.db")
        try:
            call_count = {"n": 0}
            call_lock = threading.Lock()
            in_retention = threading.Event()
            release_retention = threading.Event()

            def _blocking_apply_retention(**kwargs):
                with call_lock:
                    call_count["n"] += 1
                in_retention.set()
                # Block so the next tick fires while we're still running.
                release_retention.wait(timeout=2.0)
                return 0

            monkeypatch.setattr(db, "apply_retention", _blocking_apply_retention)

            db.schedule_periodic_retention(interval_s=0.05, app=None, max_entries=10)

            # Wait for the first retention to start.
            assert in_retention.wait(timeout=5.0)
            # Let several tick intervals pass while the first retention
            time.sleep(0.3)
            # DURING the blocking period, only ONE call should have
            with call_lock:
                n_during_blocking = call_count["n"]
            assert n_during_blocking == 1, (
                f"ER-36 re-entrancy guard failed, apply_retention was "
                f"called {n_during_blocking} times during the 0.3s "
                f"blocking window (50ms interval → ~6 ticks should "
                f"have fired, but only 1 call should have run; the rest "
                f"should have been skipped by the lock)."
            )
            # Release the first retention.
            release_retention.set()
            # Give the first retention time to release the lock.
            time.sleep(0.1)
        finally:
            release_retention.set()
            db.close()

    def test_registers_with_thread_registry_when_app_provides_one(self, tmp_path, monkeypatch):
        """When ``app._thread_registry`` is present, the periodic"""
        from voice_typer.server.history_db import HistoryDB
        from voice_typer.server.thread_registry import ThreadRegistry

        db = HistoryDB(db_path=tmp_path / "sched_registry.db")
        try:
            registry = ThreadRegistry()
            app_stub = type(
                "AppStub",
                (),
                {"_thread_registry": registry, "config": None},
            )()
            monkeypatch.setattr(db, "apply_retention", lambda **kw: 0)
            db.schedule_periodic_retention(interval_s=0.05, app=app_stub, max_entries=10)
            try:
                assert "history-periodic-retention" in registry.list_all()
            finally:
                db.close()
        finally:
            db.close()


class TestConfigSaveBackupSkip:
    """ER-53: ``_save_locked`` skips the backup read+write when the"""

    @pytest.fixture(autouse=True)
    def _isolated_config_dir(self, tmp_config_dir, monkeypatch):
        """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
        from voice_typer.server.config import _reset_config_dir_cache
        from voice_typer.server.config_internals import paths as _paths_mod

        # Reset the lru_cache BEFORE replacing the binding, the reset
        _reset_config_dir_cache()
        monkeypatch.setattr(_paths_mod, "_config_dir", lambda: tmp_config_dir)
        yield

    def test_backup_read_skipped_on_identical_resave(self, tmp_path, monkeypatch):
        """The second of two identical saves must NOT read ``config.json``"""
        from voice_typer.server.config import Config

        cfg = Config(hotkey="<f3>")
        assert cfg.save() is True
        config_file = tmp_path / "config.json"
        assert config_file.exists()

        # Spy on Path.read_bytes to count how many times config.json is
        original_read_bytes = Path.read_bytes
        read_count = {"n": 0}

        def _counting_read_bytes(self, *args, **kwargs):
            if self == config_file:
                read_count["n"] += 1
            return original_read_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)

        # Second save with IDENTICAL content, _last_saved_bytes matches.
        assert cfg.save() is True

        # The backup read should have been skipped (read_count == 0).
        assert read_count["n"] == 0, (
            "ER-53 regression: second identical save still read "
            f"config.json {read_count['n']} times for the backup "
            "check. _last_saved_bytes should short-circuit the entire "
            "backup block."
        )

        # (a bytes object, non-None). We don't compare the exact bytes
        assert cfg._last_saved_bytes is not None
        assert isinstance(cfg._last_saved_bytes, bytes)

    def test_backup_runs_when_content_changes(self, tmp_path, monkeypatch):
        """When content changes between saves, the backup block runs and"""
        from voice_typer.server.config import Config

        cfg = Config(hotkey="<f3>")
        assert cfg.save() is True
        config_file = tmp_path / "config.json"
        bak_file = tmp_path / "config.json.bak"
        # First save: no prior config.json → no .bak written.
        assert not bak_file.exists()

        # Change a field and save, _last_saved_bytes != new content →
        cfg.hotkey = "<f4>"
        assert cfg.save() is True

        assert bak_file.exists(), (
            "ER-53: backup block should run when content changes, config.json.bak should exist after the second save."
        )
        # The .bak should hold the PREVIOUS content (hotkey=<f3>).
        bak_data = json.loads(bak_file.read_text())
        assert bak_data["hotkey"] == "<f3>"
        # And config.json should hold the NEW content.
        new_data = json.loads(config_file.read_text())
        assert new_data["hotkey"] == "<f4>"

    def test_first_save_does_not_backup(self, tmp_path):
        """The very first save (no prior config.json) never writes a"""
        from voice_typer.server.config import Config

        cfg = Config(hotkey="<f3>")
        assert cfg.save() is True
        assert not (tmp_path / "config.json.bak").exists()


class TestHistoryDBMultiRowInsertBatching:
    """ER-78: 3+ pending ``add_transcription`` calls are batched into a"""

    def _make_execute_counting_db(self, tmp_path, monkeypatch):
        """Build a HistoryDB whose writer connection counts INSERT"""
        from voice_typer.server.history_db import HistoryDB

        insert_calls: list[str] = []
        real_open = HistoryDB._open_write_conn

        class ExecuteCountingProxy:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, parameters=()):
                sql_str = str(sql).strip()
                # Normalize whitespace so the multi-row INSERT (one
                if "INSERT INTO TRANSCRIPTIONS (" in sql_str.upper():
                    insert_calls.append(sql_str)
                return self._real.execute(sql, parameters)

            def cursor(self):
                return ExecuteCountingCursor(self._real.cursor(), insert_calls)

            def commit(self):
                return self._real.commit()

            def close(self):
                return self._real.close()

            def rollback(self):
                return self._real.rollback()

            @property
            def row_factory(self):
                return self._real.row_factory

            @row_factory.setter
            def row_factory(self, v):
                self._real.row_factory = v

            def __getattr__(self, name):
                return getattr(self._real, name)

        class ExecuteCountingCursor:
            def __init__(self, real, insert_calls):
                self._real = real
                self._insert_calls = insert_calls

            def execute(self, sql, parameters=()):
                sql_str = str(sql).strip()
                # See ExecuteCountingProxy.execute, the space+paren
                if "INSERT INTO TRANSCRIPTIONS (" in sql_str.upper():
                    self._insert_calls.append(sql_str)
                return self._real.execute(sql, parameters)

            def fetchone(self):
                return self._real.fetchone()

            def fetchall(self):
                return self._real.fetchall()

            @property
            def lastrowid(self):
                return self._real.lastrowid

            @property
            def rowcount(self):
                return self._real.rowcount

            def close(self):
                return self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        def patched_open(self):
            return ExecuteCountingProxy(real_open(self))

        monkeypatch.setattr(HistoryDB, "_open_write_conn", patched_open)
        db = HistoryDB(db_path=tmp_path / "batched.db")
        return db, insert_calls

    def test_three_pending_inserts_are_batched_into_one_multi_row_insert(self, tmp_path, monkeypatch):
        """Submit 3 add_transcription calls in rapid succession (without"""
        db, insert_calls = self._make_execute_counting_db(tmp_path, monkeypatch)
        try:
            # Submit 3 inserts in rapid succession. The writer thread
            db.add_transcription("first", duration=1.0, model="m1")
            db.add_transcription("second", duration=2.0, model="m2")
            db.add_transcription("third", duration=3.0, model="m3")
            db.flush()

            # At least one batch should have been executed and all 3 rows must be persisted.
            assert len(insert_calls) in (1, 2), (
                f"ER-78: expected 1-2 batched INSERTs for 3 pending rows, "
                f"got {len(insert_calls)} INSERT calls. SQL: {insert_calls}"
            )
            placeholder_tuple = "(?, ?, ?, ?, ?, ?, ?)"
            total_tuples = sum(sql.count(placeholder_tuple) for sql in insert_calls)
            assert total_tuples == 3, (
                f"ER-78: expected total of 3 placeholder tuples across batches, got {total_tuples}. SQL: {insert_calls}"
            )
            # At least one batch must be multi-row (proves batching is active).
            assert any(sql.count(placeholder_tuple) > 1 for sql in insert_calls), (
                f"ER-78: expected at least one multi-row INSERT (>1 tuple), "
                f"got single-row batches only. SQL: {insert_calls}"
            )

            # Verify all 3 rows actually landed in the DB.
            recent = db.get_recent(limit=10)
            texts = {row["text"] for row in recent}
            assert texts == {"first", "second", "third"}, f"batched INSERT didn't persist all rows; got texts={texts}"
        finally:
            db.close()

    def test_single_insert_below_threshold_is_not_batched(self, tmp_path, monkeypatch):
        """A single add_transcription (no other pending inserts) should"""
        db, insert_calls = self._make_execute_counting_db(tmp_path, monkeypatch)
        try:
            db.add_transcription("only one", duration=1.0, model="m1")
            db.flush()

            assert len(insert_calls) == 1, f"expected 1 INSERT for a single add_transcription, got {len(insert_calls)}"
            sql = insert_calls[0]
            placeholder_tuple = "(?, ?, ?, ?, ?, ?, ?)"
            assert sql.count(placeholder_tuple) == 1, (
                f"single-row INSERT should have 1 placeholder tuple, got {sql.count(placeholder_tuple)}. SQL: {sql}"
            )
        finally:
            db.close()

    def test_batched_inserts_preserve_all_row_data(self, tmp_path, monkeypatch):
        """The multi-row INSERT must persist each row's text, duration,"""
        db, _ = self._make_execute_counting_db(tmp_path, monkeypatch)
        try:
            db.add_transcription(
                "hello world",
                duration=1.5,
                model="small.en",
                device="cpu",
                language="en",
            )
            db.add_transcription(
                "second text here",
                duration=2.5,
                model="base",
                device="cuda",
                language="fr",
            )
            db.add_transcription(
                "third utterance today",
                duration=3.5,
                model="tiny",
                device="cpu",
                language="de",
            )
            db.flush()

            recent = db.get_recent(limit=10)
            by_text = {row["text"]: row for row in recent}
            assert by_text["hello world"]["duration"] == 1.5
            assert by_text["hello world"]["model"] == "small.en"
            assert by_text["hello world"]["device"] == "cpu"
            assert by_text["hello world"]["language"] == "en"
            assert by_text["hello world"]["word_count"] == 2
            assert by_text["hello world"]["char_count"] == len("hello world")

            assert by_text["second text here"]["duration"] == 2.5
            assert by_text["second text here"]["model"] == "base"
            assert by_text["second text here"]["language"] == "fr"
            assert by_text["second text here"]["word_count"] == 3

            assert by_text["third utterance today"]["duration"] == 3.5
            assert by_text["third utterance today"]["model"] == "tiny"
            assert by_text["third utterance today"]["language"] == "de"
        finally:
            db.close()


class TestSecureAtomicWriteDurability:
    """ER-80: ``durability=False`` skips both fsyncs (file + parent dir)."""

    def test_durability_false_skips_fsync(self, tmp_path, monkeypatch):
        """With ``durability=False``, neither the file-data fsync nor"""
        from voice_typer.server import secure_file_io

        fsync_count = {"n": 0}

        def _counting_fsync(fd):
            fsync_count["n"] += 1

        monkeypatch.setattr(os, "fsync", _counting_fsync)

        target = tmp_path / "nondurable.json"
        secure_file_io._secure_atomic_write(target, '{"hello": "world"}', durability=False)

        assert fsync_count["n"] == 0, (
            f"ER-80: durability=False should skip BOTH fsyncs, but os.fsync was called {fsync_count['n']} times."
        )
        assert target.exists()
        assert json.loads(target.read_text()) == {"hello": "world"}

    def test_durability_true_calls_fsync(self, tmp_path, monkeypatch):
        """With the default ``durability=True``, fsync is called at"""
        from voice_typer.server import secure_file_io

        fsync_count = {"n": 0}

        def _counting_fsync(fd):
            fsync_count["n"] += 1

        monkeypatch.setattr(os, "fsync", _counting_fsync)

        target = tmp_path / "durable.json"
        secure_file_io._secure_atomic_write(target, '{"hello": "world"}', durability=True)

        assert fsync_count["n"] >= 1, (
            "ER-80: durability=True (the default) should call fsync at least once (file data); got 0 calls."
        )

    def test_durability_default_is_true(self, tmp_path, monkeypatch):
        """existing call sites (which don't pass the kwarg) preserve their"""
        import inspect

        from voice_typer.server import secure_file_io

        sig = inspect.signature(secure_file_io._secure_atomic_write)
        durability_param = sig.parameters["durability"]
        assert durability_param.default is True, (
            "ER-80: durability parameter must default to True so "
            "existing call sites (Config.save, credential_store) keep "
            "their fsync-based durability guarantee."
        )

    def test_durability_false_still_atomic(self, tmp_path, monkeypatch):
        """``durability=False`` must still be atomic, os.replace is"""
        from voice_typer.server import secure_file_io

        target = tmp_path / "concurrent.json"
        contents = [f'{{"thread": {i}, "write": {j}}}' for i in range(3) for j in range(10)]

        def _write(content):
            secure_file_io._secure_atomic_write(target, content, durability=False)

        threads = [threading.Thread(target=_write, args=(c,), daemon=True) for c in contents]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert target.exists()
        data = json.loads(target.read_text())
        assert "thread" in data and "write" in data
