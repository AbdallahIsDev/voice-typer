"""PVT-005 regression tests for ``HistoryDB._drain_remaining``."""

from __future__ import annotations

import concurrent.futures
import contextlib
import sqlite3
import threading
import time

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path; close it after each test."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _make_pre_resolved_future() -> concurrent.futures.Future:
    """Return a Future already resolved with a result."""
    future: concurrent.futures.Future = concurrent.futures.Future()
    future.set_result("pre-resolved")
    return future


def _failing_write_closure(conn: sqlite3.Connection) -> None:
    """A write closure that always raises."""
    raise RuntimeError("simulated write failure (PVT-005 regression test)")


class TestDrainRemainingPreResolvedFuture:
    """PVT-005: ``_drain_remaining`` must tolerate a pre-resolved future."""

    def test_direct_call_does_not_raise_invalid_state_error(self, db, tmp_path):
        """queued write whose future is already resolved must NOT raise"""
        # Stop the writer thread so it doesn't drain the test item
        db.close()

        future = _make_pre_resolved_future()
        db._queue.put_nowait((_failing_write_closure, future))

        # Open a fresh write-capable connection, the writer's
        conn = sqlite3.connect(str(db.db_path), check_same_thread=False)
        try:
            # Pre-fix: this raises InvalidStateError from the inline
            db._drain_remaining(conn)
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

        assert future.result() == "pre-resolved", (
            "Future state was mutated by _drain_remaining, expected the "
            "pre-resolved result to be preserved (set_exception suppressed)."
        )

    def test_close_with_pre_resolved_future_in_drain_path(self, db):
        """Integration test: enqueuing a write whose future is"""
        from voice_typer.server.history_db import _SHUTDOWN_SENTINEL

        captured_exceptions: list[BaseException] = []
        original_excepthook = threading.excepthook

        def capture_excepthook(args: threading.ExceptHookArgs) -> None:
            captured_exceptions.append(args.exc_value)

        threading.excepthook = capture_excepthook
        try:
            # 1. Block the writer thread on a slow closure. The writer
            release_event = threading.Event()

            def blocking_closure(conn: sqlite3.Connection) -> None:
                release_event.wait(timeout=5.0)

            db._queue.put_nowait((blocking_closure, None))
            # Give the writer a moment to pick up the blocking closure.
            time.sleep(0.2)

            db._shutdown.set()
            db._queue.put_nowait(_SHUTDOWN_SENTINEL)

            # 3. Enqueue the test item with a pre-resolved future.
            future = _make_pre_resolved_future()
            db._queue.put_nowait((_failing_write_closure, future))

            # 4. Release the blocking closure so the writer can move on.
            release_event.set()

            db._writer_thread.join(timeout=5.0)
            assert not db._writer_thread.is_alive(), (
                "Writer thread did not exit within 5s, likely stuck. "
                "This indicates _drain_remaining or _writer_loop hung."
            )
        finally:
            threading.excepthook = original_excepthook

        # 5. Assert no InvalidStateError escaped the writer thread.
        invalid_state_errors = [e for e in captured_exceptions if isinstance(e, concurrent.futures.InvalidStateError)]
        assert not invalid_state_errors, (
            "InvalidStateError escaped from _drain_remaining during "
            "close() shutdown drain (PVT-005 regression): "
            f"{invalid_state_errors!r}"
        )

        # Sanity-check that the writer thread exited for the expected
        escaped_runtime_errors = [
            e for e in captured_exceptions if isinstance(e, RuntimeError) and "simulated write failure" in str(e)
        ]
        assert not escaped_runtime_errors, (
            "The failing closure's RuntimeError escaped _drain_remaining "
            "(should have been caught by _execute_write_item's except "
            f"branch): {escaped_runtime_errors!r}"
        )

        # The future should remain in its pre-resolved state.
        assert future.result() == "pre-resolved"
