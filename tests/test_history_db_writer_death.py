"""``history_db.flush``."""

from __future__ import annotations

import contextlib
import logging
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _kill_writer(db) -> None:
    """Force the writer thread into the \"dead\" state without setting _shutdown."""
    db._init_error = RuntimeError("simulated writer death (FR-10 test)")
    # Replace ``is_alive`` so the guard sees a dead thread.
    db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]


class TestAddTranscriptionDeadWriter:
    """writer is dead (instead of enqueuing to a dead queue and returning"""

    def test_returns_neg1_when_init_error_set(self, db, caplog):
        db._init_error = RuntimeError("migration failed")
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.history_db"):
            result = db.add_transcription("hello")
        assert result == -1
        assert any(
            "add_transcription refused" in r.getMessage() and "writer is unavailable" in r.getMessage()
            for r in caplog.records
        ), "expected an ERROR log about the refused add_transcription"

    def test_returns_neg1_when_writer_thread_dead(self, db):
        _kill_writer(db)
        # Even with no init_error, a dead writer thread must short-circuit.
        db._init_error = None
        db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]
        result = db.add_transcription("hello")
        assert result == -1

    def test_does_not_enqueue_when_writer_dead(self, db):
        _kill_writer(db)
        before = db._queue.qsize()
        db.add_transcription("hello")
        after = db._queue.qsize()
        assert after == before, (
            "add_transcription must NOT enqueue a _BatchableInsert when the "
            "writer is dead, that would silently leak memory and mislead callers"
        )


class TestSubmitWriteDeadWriter:
    """FR-10: ``_submit_write`` refuses instantly when the writer is dead."""

    def test_raises_historydberror_when_wait_true_and_writer_dead(self, db):
        from voice_typer.server.history_db import HistoryDBError

        _kill_writer(db)
        with pytest.raises(HistoryDBError):
            db._submit_write(lambda conn: None, wait=True)

    def test_returns_none_when_wait_false_and_writer_dead(self, db):
        _kill_writer(db)
        result = db._submit_write(lambda conn: None, wait=False)
        assert result is None

    def test_does_not_block_for_30_seconds(self, db):
        """``future.result(timeout=_WRITE_FUTURE_TIMEOUT)`` before raising."""
        _kill_writer(db)
        start = time.monotonic()
        with contextlib.suppress(Exception):
            db._submit_write(lambda conn: None, wait=True)
        elapsed = time.monotonic() - start
        # 5s is a generous upper bound, the bug was a 30s hang.
        assert elapsed < 5.0, (
            f"_submit_write took {elapsed:.1f}s on a dead writer, expected "
            "instant failure (FR-10 early-return guard). Pre-FR-10 this took ~30s."
        )


class TestFlushDeadWriter:
    """FR-10: ``flush`` returns immediately (no 30s hang) when the"""

    def test_flush_does_not_block_when_writer_dead(self, db):
        _kill_writer(db)
        start = time.monotonic()
        db.flush()
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, (
            f"flush took {elapsed:.1f}s on a dead writer, expected instant "
            "no-op (FR-10 early-return guard). Pre-FR-10 this took ~30s."
        )

    def test_flush_does_not_raise_when_writer_dead(self, db):
        """flush() is wrapped in ``contextlib.suppress(HistoryDBError)``"""
        _kill_writer(db)
        # Must not raise.
        db.flush()

    def test_flush_logs_error_when_writer_dead(self, db, caplog):
        _kill_writer(db)
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.history_db"):
            db.flush()
        assert any(
            "flush skipped" in r.getMessage() and "writer is unavailable" in r.getMessage() for r in caplog.records
        )


class TestHealthCheckWiring:
    """diagnostic surface is centralized and the IPC diagnostics handler"""

    def test_health_check_reports_init_error(self, db):
        db._init_error = RuntimeError("schema init failed")
        result = db.health_check()
        assert result["ok"] is False
        assert "schema init failed" in result["error"]

    def test_health_check_reports_dead_writer(self, db):
        db._init_error = None
        db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]
        result = db.health_check()
        assert result["ok"] is False
        assert "writer thread is not alive" in result["error"]

    def test_health_check_ok_on_healthy_db(self, db):
        result = db.health_check()
        assert result["ok"] is True
        assert result["error"] is None

    def test_submit_write_failure_uses_health_check_message(self, db, caplog):
        """``health_check`` error message (so the centralized diagnostic"""
        from voice_typer.server.history_db import HistoryDBError

        db._init_error = RuntimeError("a very specific init error")
        db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.history_db"), pytest.raises(HistoryDBError):
            db._submit_write(lambda conn: None, wait=True)
        assert any("a very specific init error" in r.getMessage() for r in caplog.records), (
            "expected the health_check error message in the _submit_write log"
        )


# ─_dictation_pipeline_ notification wiring ──────────────────────────────


class TestDictationPipelineHistoryFailNotification:
    """FR-10 + FR-28: when ``add_transcription`` returns ``<= 0``"""

    def _make_pipeline(self, history_enabled=True):
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        app.config.history_enabled = history_enabled
        app.config.model_size = "tiny.en"
        app.config.device = "cpu"
        app.config.crash_recovery_enabled = False
        # History DB mock: simulate a dead writer by returning -1.
        app.history_db.add_transcription.return_value = -1
        app.history_db.flush = MagicMock()
        app.tray.notify = MagicMock()
        # Default notify-once flag to False (not yet notified).
        app._history_fail_notified = False
        pipeline._app = app
        pipeline._duration = 1.0
        return pipeline, app

    def test_store_result_notifies_when_add_returns_neg1(self):
        pipeline, app = self._make_pipeline(history_enabled=True)
        pipeline._store_result("hello")
        app.history_db.add_transcription.assert_called_once()
        # flush must NOT be called (add_transcription short-circuited).
        app.history_db.flush.assert_not_called()
        # Tray notify must have been called (notify-once).
        app.tray.notify.assert_called_once()
        assert app._history_fail_notified is True

    def test_store_result_does_not_notify_again_after_first_failure(self):
        pipeline, app = self._make_pipeline(history_enabled=True)
        # First failure: notifies.
        pipeline._store_result("hello")
        assert app.tray.notify.call_count == 1
        # Second failure: notify-once flag is set, so no second notify.
        pipeline._store_result("world")
        assert app.tray.notify.call_count == 1

    def test_store_result_skips_history_when_disabled(self):
        """FR-28: when ``history_enabled`` is False, add_transcription"""
        pipeline, app = self._make_pipeline(history_enabled=False)
        pipeline._store_result("hello")
        app.history_db.add_transcription.assert_not_called()
        app.history_db.flush.assert_not_called()
        app.tray.notify.assert_not_called()


# __del__ must not join the writer thread ──────────────────────


class TestDelDoesNotJoinWriter:
    """joins the writer thread with a 10s timeout)."""

    def test_del_does_not_call_close(self):
        """The source of ``__del__`` must not invoke ``self.close()``."""
        import inspect

        from voice_typer.server.history_db import HistoryDB

        src = inspect.getsource(HistoryDB.__del__)
        # ``self.close()`` would join the writer thread (10s timeout).
        assert "self.close()" not in src, (
            "FR-31 regression: __del__ must NOT call self.close(), that "
            "joins the writer thread with a 10s timeout and can freeze GC. "
            "Use _shutdown.set() + close read connections instead."
        )

    def test_del_does_not_join_writer_thread(self):
        """The source of ``__del__`` must not call"""
        import inspect

        from voice_typer.server.history_db import HistoryDB

        src = inspect.getsource(HistoryDB.__del__)
        assert "_writer_thread.join" not in src, (
            "FR-31 regression: __del__ must NOT join the writer thread, "
            "it can block GC for up to 10s if the writer is stuck."
        )

    def test_del_signals_shutdown_and_closes_read_conns(self):
        """``__del__`` must set ``_shutdown`` and close ``_all_read_connections``."""
        import inspect

        from voice_typer.server.history_db import HistoryDB

        src = inspect.getsource(HistoryDB.__del__)
        assert "_shutdown.set()" in src, (
            "FR-31: __del__ must signal _shutdown so the writer exits on "
            "its next iteration (it is a daemon and will be killed at "
            "process exit regardless)."
        )
        assert "_all_read_connections" in src, (
            "FR-31: __del__ must close _all_read_connections to suppress ResourceWarning on GC."
        )

    def test_del_does_not_block_when_writer_is_stuck(self, db, monkeypatch):
        """We patch ``_writer_thread.join`` to raise (so any accidental"""
        import time as _time

        def _boom_join(*a, **kw):
            raise AssertionError("FR-31: __del__ must not call _writer_thread.join (simulated stuck-writer scenario).")

        monkeypatch.setattr(db._writer_thread, "join", _boom_join)
        start = _time.monotonic()
        db.__del__()
        elapsed = _time.monotonic() - start
        assert elapsed < 1.0, (
            f"FR-31 regression: __del__ took {elapsed:.2f}s, expected "
            "sub-second (no writer join). Pre-FR-31 this blocked up to 10s."
        )

    def test_del_safe_on_partially_constructed_instance(self):
        """``__del__`` must not raise even on a partially-constructed"""
        from voice_typer.server.history_db import HistoryDB

        instance = HistoryDB.__new__(HistoryDB)
        # __del__ must not raise even though none of the instance attrs exist.
        instance.__del__()
