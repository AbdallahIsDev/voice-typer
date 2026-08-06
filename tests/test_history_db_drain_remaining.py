"""PVT-005 regression tests for ``HistoryDB._drain_remaining``.

The pre-fix ``_drain_remaining`` had a byte-for-byte inline duplicate of
the item-handling logic that ``_writer_loop`` had already centralized
into ``_execute_write_item``. The duplicate diverged in one critical
way: it called ``future.set_exception(e)`` WITHOUT the
``contextlib.suppress(concurrent.futures.InvalidStateError)`` wrapper
that ``_execute_write_item`` (PVT-005) added.

Why that matters: if a queued write's future was already resolved
(e.g. by a duplicate-enqueue race in ``_drop_oldest_for_overflow``, or
by a coding bug, or by a closure that resolved it directly), then
``future.set_exception(e)`` raises ``InvalidStateError``. Without the
suppress wrapper, that exception:

1. Escapes the except block in ``_drain_remaining``.
2. Propagates up through ``_drain_remaining`` and ``_writer_loop``.
3. Kills the writer thread mid-shutdown-drain.
4. Silently drops every remaining fire-and-forget write that was
   queued behind the offending item — because the writer thread is
   now dead and the remaining items are never persisted.

The fix routes the shutdown drain through ``_execute_write_item``, the
same helper ``_writer_loop`` uses (DRY). This guarantees the
PVT-005 ``InvalidStateError`` suppression is shared between both call
sites, so a pre-resolved future is gracefully tolerated instead of
killing the drain.
"""

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
    """Return a Future already resolved with a result.

    Calling ``set_exception`` on this future raises
    ``InvalidStateError`` — which is exactly the scenario that exposed
    the PVT-005 regression in ``_drain_remaining``.
    """
    future: concurrent.futures.Future = concurrent.futures.Future()
    future.set_result("pre-resolved")
    return future


def _failing_write_closure(conn: sqlite3.Connection) -> None:
    """A write closure that always raises.

    Forces ``_execute_write_item``'s except branch to invoke
    ``future.set_exception(e)`` — which is the call that raises
    ``InvalidStateError`` against the pre-resolved future.
    """
    raise RuntimeError("simulated write failure (PVT-005 regression test)")


class TestDrainRemainingPreResolvedFuture:
    """PVT-005: ``_drain_remaining`` must tolerate a pre-resolved future."""

    def test_direct_call_does_not_raise_invalid_state_error(self, db, tmp_path):
        """Direct unit test: invoking ``_drain_remaining`` with a
        queued write whose future is already resolved must NOT raise
        ``InvalidStateError``.

        This is the deterministic core of the regression test. We
        stop the writer thread first (via ``close()``) so it cannot
        grab the test item from the queue before our direct call —
        which gives ``_drain_remaining`` exclusive ownership of the
        item and forces it down the PVT-005 code path.
        """
        # Stop the writer thread so it doesn't drain the test item
        # before our direct _drain_remaining call. close() also drains
        # any items enqueued during fixture setup (none in this case).
        db.close()

        future = _make_pre_resolved_future()
        db._queue.put_nowait((_failing_write_closure, future))

        # Open a fresh write-capable connection — the writer's
        # connection was closed by close(). The failing closure never
        # touches the connection (it raises immediately), so any
        # connection works.
        conn = sqlite3.connect(str(db.db_path), check_same_thread=False)
        try:
            # Pre-fix: this raises InvalidStateError from the inline
            # duplicate's ``future.set_exception(e)``. Post-fix:
            # _execute_write_item suppresses it.
            db._drain_remaining(conn)
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.close()

        # The future remains in its pre-resolved state — the
        # set_exception call was suppressed (not applied) because the
        # future was already resolved.
        assert future.result() == "pre-resolved", (
            "Future state was mutated by _drain_remaining — expected the "
            "pre-resolved result to be preserved (set_exception suppressed)."
        )

    def test_close_with_pre_resolved_future_in_drain_path(self, db):
        """Integration test: enqueuing a write whose future is
        pre-resolved, then calling ``close()``, must NOT let
        ``InvalidStateError`` escape the writer thread.

        Reproduces the user-facing shutdown-drain scenario:
        1. Block the writer thread on a slow closure so the test item
           stays in the queue (instead of being processed by the
           normal ``_writer_loop`` path).
        2. Manually set ``_shutdown`` and enqueue the
           ``_SHUTDOWN_SENTINEL`` AHEAD of the test item — so when the
           writer resumes, it receives the sentinel first and calls
           ``_drain_remaining`` (which then processes the test item),
           rather than processing the test item via the normal
           ``_execute_write_item`` path in ``_writer_loop``.
        3. Enqueue the test item (pre-resolved future + failing
           closure).
        4. Release the blocking closure. The writer finishes it,
           receives the sentinel, and calls ``_drain_remaining``.
        5. Install ``threading.excepthook`` to capture any uncaught
           exception in the writer thread. Pre-fix,
           ``InvalidStateError`` escapes and kills the writer thread;
           the excepthook captures it. Post-fix, no exception escapes.
        """
        from voice_typer.server.history_db import _SHUTDOWN_SENTINEL

        captured_exceptions: list[BaseException] = []
        original_excepthook = threading.excepthook

        def capture_excepthook(args: threading.ExceptHookArgs) -> None:
            captured_exceptions.append(args.exc_value)

        threading.excepthook = capture_excepthook
        try:
            # 1. Block the writer thread on a slow closure. The writer
            #    will be stuck inside _execute_write_item for this
            #    closure until we set release_event.
            release_event = threading.Event()

            def blocking_closure(conn: sqlite3.Connection) -> None:
                release_event.wait(timeout=5.0)

            db._queue.put_nowait((blocking_closure, None))
            # Give the writer a moment to pick up the blocking closure.
            time.sleep(0.2)

            # 2. Set _shutdown and enqueue the sentinel AHEAD of the
            #    test item so _drain_remaining is the path that
            #    processes the test item.
            db._shutdown.set()
            db._queue.put_nowait(_SHUTDOWN_SENTINEL)

            # 3. Enqueue the test item with a pre-resolved future.
            future = _make_pre_resolved_future()
            db._queue.put_nowait((_failing_write_closure, future))

            # 4. Release the blocking closure so the writer can move on.
            release_event.set()

            # Wait for the writer thread to exit. It should process the
            # sentinel, call _drain_remaining (which processes the test
            # item), then exit. Pre-fix, the writer dies inside
            # _drain_remaining when InvalidStateError escapes.
            db._writer_thread.join(timeout=5.0)
            assert not db._writer_thread.is_alive(), (
                "Writer thread did not exit within 5s — likely stuck. "
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
        # reason (no other uncaught exceptions either). We allow
        # RuntimeError because that's what _failing_write_closure raises
        # — but it should be caught by _execute_write_item, never
        # escape. If it escaped, _drain_remaining is broken.
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
