"""``HistoryDB._submit_write`` blocking retry loop."""

from __future__ import annotations

import logging
import time

import pytest


class TestWriteFutureTotalTimeoutConstant:
    """the hard-deadline fix: ``_WRITE_FUTURE_TOTAL_TIMEOUT`` is the documented hard cap"""

    def test_total_timeout_constant_is_60_seconds(self):
        """The hard cap is 60s, 2× the per-retry 30s timeout."""
        from voice_typer.server.history_db import _WRITE_FUTURE_TOTAL_TIMEOUT

        assert _WRITE_FUTURE_TOTAL_TIMEOUT == 60.0, (
            "_WRITE_FUTURE_TOTAL_TIMEOUT must remain 60.0 (2× the per-retry "
            "30s timeout). Tightening would abort legitimate slow writes; "
            "loosening would re-introduce the the hard-deadline fix indefinite-hang bug."
        )

    def test_per_retry_timeout_constant_is_30_seconds(self):
        """The per-retry timeout remains 30s."""
        from voice_typer.server.history_db import _WRITE_FUTURE_TIMEOUT

        assert _WRITE_FUTURE_TIMEOUT == 30.0, (
            "_WRITE_FUTURE_TIMEOUT must remain 30.0 (the per-retry wait). "
            "the hard-deadline fix only ADDS a total deadline; it must not regress the "
            "per-retry semantics."
        )

    def test_total_timeout_is_greater_than_per_retry(self):
        """very first iteration (before any retry could succeed)."""
        from voice_typer.server.history_db import (
            _WRITE_FUTURE_TIMEOUT,
            _WRITE_FUTURE_TOTAL_TIMEOUT,
        )

        assert _WRITE_FUTURE_TOTAL_TIMEOUT > _WRITE_FUTURE_TIMEOUT, (
            "Total deadline must be > per-retry timeout; otherwise the "
            "deadline would fire before the first retry, aborting every "
            "write that didn't complete in the first wait window."
        )


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_writer_loop_timeout.db")
    yield db_instance
    db_instance.close()


class TestSubmitWriteTotalDeadlineFires:
    """the hard-deadline fix: ``_submit_write`` aborts after ``_WRITE_FUTURE_TOTAL_TIMEOUT``"""

    def test_aborts_after_total_deadline_not_loop_forever(self, db, monkeypatch):
        from voice_typer.server import history_db as history_db_mod
        from voice_typer.server.history_db import HistoryDBError

        # Tighten both timeouts so the test runs in ~0.5s instead of 60s.
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TIMEOUT", 0.1)
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.5)

        # Replace _execute_write_item with a no-op that does NOT
        def _stuck_execute_write_item(conn, callable_, future):  # noqa: ARG001
            # Intentionally do NOT call future.set_result / set_exception.
            return None

        monkeypatch.setattr(db, "_execute_write_item", _stuck_execute_write_item)

        # Sanity: writer is alive (the deadline must NOT short-circuit
        assert db._writer_thread.is_alive(), (
            "writer thread must be alive for the the hard-deadline fix deadline test, "
            "the dead-writer guard is a separate code path."
        )

        start = time.monotonic()
        with pytest.raises(HistoryDBError) as exc_info:
            db._submit_write(lambda conn: None, wait=True)
        elapsed = time.monotonic() - start

        # The deadline must fire after ~0.5s (the monkeypatched
        assert elapsed < 5.0, (
            f"_submit_write took {elapsed:.1f}s on a stuck-but-alive "
            "writer, expected ~0.5s (the monkeypatched "
            "_WRITE_FUTURE_TOTAL_TIMEOUT). Pre-the hard-deadline fix this looped "
            "forever between 30s per-retry waits."
        )
        # The error message must surface the total-deadline context
        msg = str(exc_info.value)
        assert "total deadline" in msg, (
            f"HistoryDBError message must mention 'total deadline' so the "
            f"stuck-writer abort is distinguishable from the dead-writer "
            f"abort ('HistoryDB writer thread is dead; ...'). Got: {msg}"
        )

    def test_deadline_logs_warning_before_raising(self, db, monkeypatch, caplog):
        """the hard-deadline fix: the deadline-fire path must log a WARNING before"""
        from voice_typer.server import history_db as history_db_mod
        from voice_typer.server.history_db import HistoryDBError

        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TIMEOUT", 0.05)
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.2)

        def _stuck_execute_write_item(conn, callable_, future):  # noqa: ARG001
            return None

        monkeypatch.setattr(db, "_execute_write_item", _stuck_execute_write_item)

        with (
            caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db_internals.writer"),
            pytest.raises(HistoryDBError),
        ):
            db._submit_write(lambda conn: None, wait=True)

        assert any("total deadline" in r.getMessage() and "stuck" in r.getMessage() for r in caplog.records), (
            "expected a WARNING log mentioning 'total deadline' and 'stuck' "
            "before the HistoryDBError is raised; got records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_deadline_does_not_fire_when_writer_dead(self, db, monkeypatch):
        """the hard-deadline fix: the deadline check is additive —"""
        from voice_typer.server import history_db as history_db_mod
        from voice_typer.server.history_db import HistoryDBError

        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TIMEOUT", 0.1)
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.5)

        db._init_error = RuntimeError("simulated writer death (the hard-deadline fix test)")
        db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]

        with pytest.raises(HistoryDBError) as exc_info:
            db._submit_write(lambda conn: None, wait=True)

        # The dead-writer message must surface, NOT the hard-deadline fix
        msg = str(exc_info.value)
        assert "writer is unavailable" in msg or "writer thread is dead" in msg, (
            f"dead-writer message must surface when the writer is "
            f"dead, not the the hard-deadline fix stuck-writer message. Got: {msg}"
        )
        assert "total deadline" not in msg, (
            "the hard-deadline fix deadline message must NOT fire on a dead writer, the "
            "early-return guard must short-circuit before the retry "
            f"loop is entered. Got: {msg}"
        )


class TestSubmitWriteSuccessfulNotAffected:
    """the hard-deadline fix: the deadline must NOT interfere with writes that complete"""

    def test_successful_write_returns_result_immediately(self, db):
        """A write that completes immediately returns its result —"""
        result = db._submit_write(lambda conn: "ok", wait=True)
        assert result == "ok"

    def test_successful_write_does_not_block_near_deadline(self, db, monkeypatch):
        """Even when ``_WRITE_FUTURE_TOTAL_TIMEOUT`` is set very small"""
        from voice_typer.server import history_db as history_db_mod

        # Tiny deadline (1ms). A write that completes immediately
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.001)

        result = db._submit_write(lambda conn: "still-ok", wait=True)
        assert result == "still-ok"

    def test_flush_completes_normally_when_writer_healthy(self, db):
        """no-op closure) must complete normally when the writer is"""
        start = time.monotonic()
        db.flush()
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, (
            f"flush took {elapsed:.1f}s on a healthy writer, expected "
            "<5s. If this approaches 60s, the the hard-deadline fix deadline check is "
            "firing prematurely on successful writes."
        )
