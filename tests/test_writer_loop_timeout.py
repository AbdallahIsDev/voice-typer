"""the hard-deadline fix: regression tests for the hard total deadline wired into the
``HistoryDB._submit_write`` blocking retry loop.

Pre-the hard-deadline fix: ``_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0`` was defined at
``voice_typer/server/history_db.py:85`` but never referenced by any
production code path. The blocking write retry loop in
``voice_typer/server/history_db_internals/writer.py:_submit_write``
was a bare ``while True`` that re-waited ``_WRITE_FUTURE_TIMEOUT``
(30s) on every TimeoutError for as long as the writer thread was
*alive*. A writer that was alive but never made progress (e.g. a
multi-batch retention sweep on a huge DB locked by an external
process, antivirus, or a deadlocked SQLite WAL) would loop forever
between 30s per-retry waits — hanging the IPC handler thread
indefinitely.

The fix ADDS a deadline check at the top of every loop iteration:
``if time.monotonic() - loop_start >= _WRITE_FUTURE_TOTAL_TIMEOUT:
raise HistoryDBError(...)``. The per-retry timeout (30s) is preserved
(no behavior change for successful slow writes < 60s); the deadline
only ADDS an upper bound.

These tests pin:
  1. The constant values (60.0 total, 30.0 per-retry) — guards
     against accidental tightening.
  2. The deadline fires when the writer is alive but stuck — using
     monkeypatched small values for test speed (real 60s deadline
     would make the test take a full minute).
  3. Successful writes are NOT affected by the deadline — a write
     that completes well within the deadline returns its result
     immediately.
"""

from __future__ import annotations

import logging
import time

import pytest

# ── constant pins ────────────────────────────────────────────────────


class TestWriteFutureTotalTimeoutConstant:
    """the hard-deadline fix: ``_WRITE_FUTURE_TOTAL_TIMEOUT`` is the documented hard cap
    on the cumulative time ``_submit_write`` will wait for a single
    blocking write to complete.

    The audit (review.md §the hard-deadline fix) found this constant was defined but
    NEVER referenced. After the hard-deadline fix it IS referenced by ``_submit_write``
    in ``history_db_internals/writer.py``. These tests pin the value
    so a future contributor doesn't accidentally tighten it (which
    would abort legitimate slow writes) or loosen it (which would
    re-introduce the indefinite hang).
    """

    def test_total_timeout_constant_is_60_seconds(self):
        """The hard cap is 60s — 2× the per-retry 30s timeout.

        Per the comment at ``history_db.py:75-84``: "60s is 2× the
        per-retry timeout — generous enough that a legitimate slow
        write (large retention sweep) is never aborted prematurely,
        but short enough that a truly stuck writer surfaces a clear
        error to the caller instead of hanging the IPC handler
        thread indefinitely."
        """
        from voice_typer.server.history_db import _WRITE_FUTURE_TOTAL_TIMEOUT

        assert _WRITE_FUTURE_TOTAL_TIMEOUT == 60.0, (
            "_WRITE_FUTURE_TOTAL_TIMEOUT must remain 60.0 (2× the per-retry "
            "30s timeout). Tightening would abort legitimate slow writes; "
            "loosening would re-introduce the the hard-deadline fix indefinite-hang bug."
        )

    def test_per_retry_timeout_constant_is_30_seconds(self):
        """The per-retry timeout remains 30s.

        The the hard-deadline fix deadline fix ADDS a total cap; it does NOT change
        the per-retry wait. Pinning 30.0 here ensures future
        contributors don't accidentally tighten the per-retry wait
        (which would cause false positives on legitimate slow writes
        like a multi-batch retention sweep on a huge DB).
        """
        from voice_typer.server.history_db import _WRITE_FUTURE_TIMEOUT

        assert _WRITE_FUTURE_TIMEOUT == 30.0, (
            "_WRITE_FUTURE_TIMEOUT must remain 30.0 (the per-retry wait). "
            "the hard-deadline fix only ADDS a total deadline; it must not regress the "
            "per-retry semantics."
        )

    def test_total_timeout_is_greater_than_per_retry(self):
        """Structural invariant: the total deadline must exceed the
        per-retry timeout, otherwise the deadline would fire on the
        very first iteration (before any retry could succeed)."""
        from voice_typer.server.history_db import (
            _WRITE_FUTURE_TIMEOUT,
            _WRITE_FUTURE_TOTAL_TIMEOUT,
        )

        assert _WRITE_FUTURE_TOTAL_TIMEOUT > _WRITE_FUTURE_TIMEOUT, (
            "Total deadline must be > per-retry timeout; otherwise the "
            "deadline would fire before the first retry, aborting every "
            "write that didn't complete in the first wait window."
        )


# ── deadline-fires-on-stuck-writer ───────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_writer_loop_timeout.db")
    yield db_instance
    db_instance.close()


class TestSubmitWriteTotalDeadlineFires:
    """the hard-deadline fix: ``_submit_write`` aborts after ``_WRITE_FUTURE_TOTAL_TIMEOUT``
    when the writer is alive but stuck (never resolves the future).

    We simulate a stuck writer by replacing ``db._execute_write_item``
    with a no-op that pulls items off the queue but never resolves
    their futures — the writer thread is alive and draining the
    queue, but no future ever completes. Pre-the hard-deadline fix this would loop
    forever between 30s per-retry waits; post-the hard-deadline fix it raises
    ``HistoryDBError`` after the total deadline.

    For test speed, both timeouts are monkeypatched to small values
    (real values are 30s/60s — testing those would take a full
    minute). The deadline logic is independent of the constant
    values, so any positive numbers exercise the same code path.
    """

    def test_aborts_after_total_deadline_not_loop_forever(self, db, monkeypatch):
        from voice_typer.server import history_db as history_db_mod
        from voice_typer.server.history_db import HistoryDBError

        # Tighten both timeouts so the test runs in ~0.5s instead of 60s.
        # _WRITE_FUTURE_TOTAL_TIMEOUT=0.5 means the deadline fires after
        # ~0.5s of cumulative waiting (a few 0.1s per-retry iterations).
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TIMEOUT", 0.1)
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.5)

        # Replace _execute_write_item with a no-op that does NOT
        # resolve the future. This forces the writer thread to drain
        # the queue (proving it's "alive") but never complete the
        # write (proving it's "stuck"). The 3-arg signature matches
        # the bound-method call site `db._execute_write_item(conn,
        # callable_, future)` in writer.py's _writer_loop.
        def _stuck_execute_write_item(conn, callable_, future):  # noqa: ARG001
            # Intentionally do NOT call future.set_result / set_exception.
            # This is the "stuck writer" simulation: the writer pulls
            # the write off the queue and runs the closure-less no-op,
            # but the future never resolves.
            return None

        monkeypatch.setattr(db, "_execute_write_item", _stuck_execute_write_item)

        # Sanity: writer is alive (the deadline must NOT short-circuit
        # via the dead-writer guard, which is a separate code path
        # tested in test_history_db_writer_death.py).
        assert db._writer_thread.is_alive(), (
            "writer thread must be alive for the the hard-deadline fix deadline test — "
            "the dead-writer guard is a separate code path."
        )

        start = time.monotonic()
        with pytest.raises(HistoryDBError) as exc_info:
            db._submit_write(lambda conn: None, wait=True)
        elapsed = time.monotonic() - start

        # The deadline must fire after ~0.5s (the monkeypatched
        # _WRITE_FUTURE_TOTAL_TIMEOUT). 5s is a generous upper bound
        # that absorbs scheduling jitter on a slow CI runner without
        # masking a regression to the 60s pre-the hard-deadline fix behavior.
        assert elapsed < 5.0, (
            f"_submit_write took {elapsed:.1f}s on a stuck-but-alive "
            "writer — expected ~0.5s (the monkeypatched "
            "_WRITE_FUTURE_TOTAL_TIMEOUT). Pre-the hard-deadline fix this looped "
            "forever between 30s per-retry waits."
        )
        # The error message must surface the total-deadline context
        # so callers (and operators reading voice-typer.log) can
        # distinguish a stuck-writer abort from a dead-writer abort.
        msg = str(exc_info.value)
        assert "total deadline" in msg, (
            f"HistoryDBError message must mention 'total deadline' so the "
            f"stuck-writer abort is distinguishable from the dead-writer "
            f"abort ('HistoryDB writer thread is dead; ...'). Got: {msg}"
        )

    def test_deadline_logs_warning_before_raising(self, db, monkeypatch, caplog):
        """the hard-deadline fix: the deadline-fire path must log a WARNING before
        raising ``HistoryDBError``.

        The warning is the operator-visible signal that a writer was
        alive but stuck — without it, the only diagnostic would be
        the raised exception (which is caught by the IPC handler's
        existing except clause and may not surface in the log).
        """
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
        """the hard-deadline fix: the deadline check is additive —
        it does NOT replace the dead-writer short-circuit.

        When the writer thread is dead, the existing guard at
        the top of ``_submit_write`` raises ``HistoryDBError`` BEFORE
        enqueuing. The deadline check inside the retry loop is never
        reached. This test verifies the two paths are independent:
        killing the writer must surface the dead-writer message
        ('HistoryDB writer thread is dead; write did not complete'),
        NOT the the hard-deadline fix stuck-writer message ('total deadline ...').
        """
        from voice_typer.server import history_db as history_db_mod
        from voice_typer.server.history_db import HistoryDBError

        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TIMEOUT", 0.1)
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.5)

        # Simulate a dead writer (pattern from
        # test_history_db_writer_death.py::_kill_writer).
        db._init_error = RuntimeError("simulated writer death (the hard-deadline fix test)")
        db._writer_thread.is_alive = lambda: False  # type: ignore[method-assign]

        with pytest.raises(HistoryDBError) as exc_info:
            db._submit_write(lambda conn: None, wait=True)

        # The dead-writer message must surface, NOT the hard-deadline fix
        # stuck-writer message. This proves the two code paths are
        # independent and the hard-deadline fix deadline doesn't shadow it.
        msg = str(exc_info.value)
        assert "writer is unavailable" in msg or "writer thread is dead" in msg, (
            f"dead-writer message must surface when the writer is "
            f"dead, not the the hard-deadline fix stuck-writer message. Got: {msg}"
        )
        assert "total deadline" not in msg, (
            "the hard-deadline fix deadline message must NOT fire on a dead writer — the "
            "early-return guard must short-circuit before the retry "
            f"loop is entered. Got: {msg}"
        )


# ── successful writes not affected ───────────────────────────────────


class TestSubmitWriteSuccessfulNotAffected:
    """the hard-deadline fix: the deadline must NOT interfere with writes that complete
    within the deadline.

    A write that completes in well under the deadline (the normal
    case — most writes complete in <100ms) must return its result
    immediately. The deadline check at the top of the loop sees
    ``elapsed = 0`` on the first iteration and skips, then
    ``future.result(timeout=30s)`` returns immediately on success.

    These tests use the REAL (60s) deadline — if the deadline check
    had a bug that fired prematurely (e.g. checked ``>= 0`` instead
    of ``>= _WRITE_FUTURE_TOTAL_TIMEOUT``), these tests would raise
    HistoryDBError instead of returning the result.
    """

    def test_successful_write_returns_result_immediately(self, db):
        """A write that completes immediately returns its result —
        the deadline check on the first iteration (elapsed=0 < 60s)
        skips, and ``future.result(timeout=30s)`` returns the closure's
        return value."""
        result = db._submit_write(lambda conn: "ok", wait=True)
        assert result == "ok"

    def test_successful_write_does_not_block_near_deadline(self, db, monkeypatch):
        """Even when ``_WRITE_FUTURE_TOTAL_TIMEOUT`` is set very small
        (simulating a tight deadline), a write that completes immediately
        must still succeed — the deadline check at the top of the first
        iteration sees ``elapsed = 0`` and skips, then the future
        resolves successfully before any retry.

        This proves the deadline is ADDITIVE (only aborts on stuck
        writers) and not PREEMPTIVE (does not abort writes that
        would have succeeded)."""
        from voice_typer.server import history_db as history_db_mod

        # Tiny deadline (1ms). A write that completes immediately
        # still succeeds because the FIRST deadline check passes
        # (elapsed = 0 < 0.001s) and the future resolves before any
        # retry/timeout.
        monkeypatch.setattr(history_db_mod, "_WRITE_FUTURE_TOTAL_TIMEOUT", 0.001)

        result = db._submit_write(lambda conn: "still-ok", wait=True)
        assert result == "still-ok"

    def test_flush_completes_normally_when_writer_healthy(self, db):
        """``flush`` (which calls ``_submit_write(wait=True)`` with a
        no-op closure) must complete normally when the writer is
        healthy. This exercises the deadline path through the public
        ``flush`` API (the only production caller of ``_submit_write``
        with ``wait=True`` from outside the write methods)."""
        # flush() is wrapped in contextlib.suppress(HistoryDBError)
        # so it never raises — but it must complete in well under the
        # 60s deadline. If the deadline check had a bug that fired
        # prematurely, flush would still complete (because of the
        # suppress) but would take ~60s. The timing assertion catches
        # that regression.
        start = time.monotonic()
        db.flush()
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, (
            f"flush took {elapsed:.1f}s on a healthy writer — expected "
            "<5s. If this approaches 60s, the the hard-deadline fix deadline check is "
            "firing prematurely on successful writes."
        )
