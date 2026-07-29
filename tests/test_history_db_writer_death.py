"""FR-10: regression tests for dead-writer / init-error detection in
``history_db.add_transcription``, ``history_db._submit_write``, and
``history_db.flush``.

The previous implementation only gated on ``self._shutdown.is_set()``.
When the writer thread died during schema init (migration failure sets
``_init_error``, or corruption recovery failure) or mid-loop, the
writer thread exited but ``_shutdown`` was never set. ``add_transcription``
enqueued a ``_BatchableInsert`` to the dead writer's queue and returned
placeholder ``1`` — the INSERT never executed. The subsequent ``flush()``
call blocked on ``future.result(timeout=_WRITE_FUTURE_TIMEOUT)`` = 30s
before the TimeoutError handler noticed the dead writer and raised
``HistoryDBError``. Every subsequent dictation repeated: instant enqueue
→ 30s flush hang → HistoryDBError caught → NO notification (notify-once
flag).

The fix adds an early-return guard in all three methods that delegates
to ``health_check()`` so the failure is instant and surfaces a clear
``log.error`` line. ``_submit_write(wait=True)`` raises ``HistoryDBError``
so blocking callers (delete/clear_all/etc.) catch it via their existing
except clause. ``add_transcription`` returns ``-1`` and ``flush`` returns
immediately (both consistent with their existing failure sentinels).
"""

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
    """Force the writer thread into the "dead" state without setting _shutdown.

    We patch ``_writer_thread.is_alive`` to return ``False`` and set
    ``_init_error`` so the FR-10 guard's two conditions are exercised
    independently of the writer's actual liveness (which would require
    a real crash mid-loop).
    """
    db._init_error = RuntimeError("simulated writer death (FR-10 test)")
    # Replace ``is_alive`` so the guard sees a dead thread.
    db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]


# ── add_transcription ────────────────────────────────────────────────────


class TestAddTranscriptionDeadWriter:
    """FR-10: ``add_transcription`` returns -1 immediately when the
    writer is dead (instead of enqueuing to a dead queue and returning
    a misleading success placeholder)."""

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
            "writer is dead — that would silently leak memory and mislead callers"
        )


# ── _submit_write ────────────────────────────────────────────────────────


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
        """The pre-FR-10 implementation would block 30s on
        ``future.result(timeout=_WRITE_FUTURE_TIMEOUT)`` before raising.
        The early-return guard must make the failure path instant."""
        _kill_writer(db)
        start = time.monotonic()
        with contextlib.suppress(Exception):
            db._submit_write(lambda conn: None, wait=True)
        elapsed = time.monotonic() - start
        # 5s is a generous upper bound — the bug was a 30s hang.
        assert elapsed < 5.0, (
            f"_submit_write took {elapsed:.1f}s on a dead writer — expected "
            "instant failure (FR-10 early-return guard). Pre-FR-10 this took ~30s."
        )


# ── flush ────────────────────────────────────────────────────────────────


class TestFlushDeadWriter:
    """FR-10: ``flush`` returns immediately (no 30s hang) when the
    writer is dead."""

    def test_flush_does_not_block_when_writer_dead(self, db):
        _kill_writer(db)
        start = time.monotonic()
        db.flush()
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, (
            f"flush took {elapsed:.1f}s on a dead writer — expected instant "
            "no-op (FR-10 early-return guard). Pre-FR-10 this took ~30s."
        )

    def test_flush_does_not_raise_when_writer_dead(self, db):
        """flush() is wrapped in ``contextlib.suppress(HistoryDBError)``
        internally so callers (e.g. dictation_pipeline._store_result)
        see it as a no-op even when the writer is dead."""
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


# ── health_check wiring ─────────────────────────────────────────────────


class TestHealthCheckWiring:
    """FR-10: the failure path delegates to ``health_check()`` so the
    diagnostic surface is centralized and the IPC diagnostics handler
    can call ``health_check()`` directly to surface the same signal."""

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
        """The ``_submit_write`` failure log must include the
        ``health_check`` error message (so the centralized diagnostic
        is what surfaces, not a separate ad-hoc string)."""
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
    """FR-10 + FR-28: when ``add_transcription`` returns ``<= 0``
    (writer dead), ``dictation_pipeline._store_result`` raises a
    RuntimeError that's caught by the existing except clause and
    triggers the notify-once tray message — instead of silently
    treating the placeholder as success."""

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
        # notify-once flag must now be True.
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
        """FR-28: when ``history_enabled`` is False, add_transcription
        is NOT called at all."""
        pipeline, app = self._make_pipeline(history_enabled=False)
        pipeline._store_result("hello")
        app.history_db.add_transcription.assert_not_called()
        app.history_db.flush.assert_not_called()
        app.tray.notify.assert_not_called()
