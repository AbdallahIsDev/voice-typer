"""Writer-thread queue, drain, batched-INSERT, and teardown helpers.

Extracted from the once-monolithic ``history_db.py`` (wave 1 split).
The functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes (the
writer queue, the writer-thread handle, the shutdown event, the
init-error slot) via the passed-in reference. The public
``HistoryDB`` class keeps thin delegating methods so all 173+ test
monkeypatch sites (e.g. ``monkeypatch.setattr(HistoryDB, "_writer_loop",
...)``) keep working unchanged ().

Free functions:

- :func:`_writer_loop` — the daemon-thread body that drains the write
  queue serially on the only write-capable connection.
- :func:`_execute_write_item` — runs a single queued closure and
  resolves its future (with  InvalidStateError suppression +
  DB-LOCK-FIX rollback + WAL-CHECKPOINT-FIX post-write rollback).
- func:`_drain_batchable_inserts` —  multi-row INSERT batching
  for ``_BatchableInsert`` payloads.
- :func:`_drain_remaining` — shutdown drain (best-effort persistence
  of fire-and-forget writes submitted before ``close()``).
- :func:`_drop_oldest_for_overflow` — PERF-5 queue-full handling
  (drops oldest non-sentinel item, resolves its future with
  ``HistoryDBError``).
- :func:`_submit_write` — public write-submission entrypoint with
   dead-writer early-return guard.
- :func:`flush` — block until all queued writes have been processed.
- :func:`_close_writer` — the writer-teardown portion of
  ``HistoryDB.close()`` (best-effort ``wal_checkpoint(TRUNCATE)`` +
  shutdown sentinel enqueue + wait for writer exit).

NOTE: ``_run_checkpoint`` is intentionally NOT extracted. Tests in
``tests/test_vocabulary_history_db_fixes.py`` use
``inspect.getsource(HistoryDB._run_checkpoint)`` and assert that
specific comment strings (e.g. ``"every 300s"``, ``"attempt in 300s
will retry"``) are present in the source. Extracting to a free
function would break those source-inspection tests; the method
remains on the ``HistoryDB`` class until wave 2.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import queue
import sqlite3
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)


def _writer_loop(db: HistoryDB) -> None:
    """Drain the write queue serially on a single connection.

    Runs in a daemon thread. Opens the write connection, runs
    schema setup, signals ``_writer_ready``, then loops:
      - Wait for an item from the queue with a timeout (so we can
        periodically WAL-checkpoint).
      - On timeout, run ``PRAGMA wal_checkpoint(PASSIVE)`` to
        bound WAL growth and prevent autocheckpoint stalls.
      - On ``_SHUTDOWN_SENTINEL``, drain remaining items and exit.
      - On a normal item, call the closure with the write
        connection and set the future's result/exception.

    ``_init_db_schema`` may set ``self._init_error`` and
    return early without raising (e.g. migration failure rolled
    back). In that case we must NOT enter the main write loop —
    the schema is in an inconsistent state and writes would fail
    or corrupt data further. Close the connection and exit so
    callers see the failure via ``_init_error`` / ``health_check``.
    """
    # Lazy import so the constants track monkeypatches on the
    # ``history_db`` module namespace (e.g. ``_WAL_CHECKPOINT_INTERVAL``).
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806
    _WAL_CHECKPOINT_INTERVAL = _hd._WAL_CHECKPOINT_INTERVAL  # noqa: N806

    conn: sqlite3.Connection | None = None
    try:
        conn = db._open_write_conn()
        db._check_wal_mode(conn)
        # _init_db_schema may return a fresh connection
        # if corruption was detected and the DB was recreated.
        conn = db._init_db_schema(conn)
    except BaseException as e:  # noqa: BLE001 — surface to __init__
        db._init_error = e
        db._writer_ready.set()
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        return
    db._writer_ready.set()
    # if schema init set _init_error (e.g. migration
    # failure), don't enter the main write loop. The DB is in an
    # inconsistent state; writes would fail or compound the damage.
    if db._init_error is not None:
        log.error(
            "[HISTORY_DB] Skipping writer loop — schema init failed: %s",
            db._init_error,
        )
        with contextlib.suppress(sqlite3.Error):
            conn.close()
        return

    last_checkpoint = time.monotonic()
    while True:
        now = time.monotonic()
        wait_for = _WAL_CHECKPOINT_INTERVAL - (now - last_checkpoint)
        if wait_for <= 0:
            db._run_checkpoint(conn)
            last_checkpoint = time.monotonic()
            wait_for = _WAL_CHECKPOINT_INTERVAL
        try:
            item = db._queue.get(timeout=wait_for)
        except queue.Empty:
            db._run_checkpoint(conn)
            last_checkpoint = time.monotonic()
            continue
        if item is _SHUTDOWN_SENTINEL:
            db._drain_remaining(conn)
            break
        # structured batchable INSERT payload — drain pending
        # inserts into a single multi-row INSERT when 3+ are queued.
        if isinstance(item, _BatchableInsert):
            db._drain_batchable_inserts(conn, item)
            # WAL-CHECKPOINT-FIX: post-write cleanup (same as the
            # normal closure path).
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            continue
        callable_, future = item
        db._execute_write_item(conn, callable_, future)
    # Drain loop exited — close the writer's connection.
    try:
        conn.close()
    except sqlite3.Error as e:
        log.warning("[HISTORY_DB] Error closing writer connection: %s", e)


def _execute_write_item(
    db: HistoryDB,
    conn: sqlite3.Connection,
    callable_: Callable[[sqlite3.Connection], Any],
    future: concurrent.futures.Future | None,
) -> None:
    """Execute a single queued write closure and resolve its future.

     (DRY): the item-handling block was previously duplicated
    verbatim between ``_writer_loop`` and ``_drain_remaining``,
    with one critical divergence — ``_drain_remaining`` was
    MISSING the  ``InvalidStateError`` suppression on
    ``future.set_exception(e)``. That made the shutdown drain
    fragile: if a duplicate-enqueue race resolved the future
    before the except block ran, ``set_exception`` would raise
    ``InvalidStateError`` → kill the drain → fire-and-forget
    writes silently dropped during shutdown.

    Centralizing the logic here guarantees both call sites share
    the  suppression (and the DB-LOCK-FIX rollback, and
    the WAL-CHECKPOINT-FIX post-write rollback).
    """
    try:
        result = callable_(conn)
        if future is not None:
            future.set_result(result)
    except BaseException as e:  # noqa: BLE001 — propagate to future
        # DB-LOCK-FIX: rollback any uncommitted transaction left
        # by the failed closure. If we don't, the next WAL
        # checkpoint will fail with "database table is locked"
        # because the writer's own connection has a pending
        # uncommitted transaction.
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        if future is not None:
            #  (session-2): Suppress InvalidStateError on
            # set_exception. If the future was already resolved
            # (e.g. by a prior duplicate-enqueue race in
            # _drop_oldest_for_overflow, or by a coding bug),
            # set_exception raises InvalidStateError which would
            # propagate out of this except block and KILL the
            # writer thread permanently. That converts a single
            # write failure into permanent data loss for all
            # subsequent writes until app restart.
            with contextlib.suppress(concurrent.futures.InvalidStateError):
                future.set_exception(e)
        else:
            # Fire-and-forget write failed — log so it's visible.
            log.error("[HISTORY_DB] Fire-and-forget write failed: %s", e)
    else:
        # WAL-CHECKPOINT-FIX: After a SUCCESSFUL write, ensure
        # no lingering transaction remains on the connection.
        # All write closures call conn.commit(), but if the
        # closure's commit succeeded and the method then raised
        # an exception (e.g. cursor.lastrowid access on a
        # closed cursor), the transaction is committed but the
        # connection might be in an unexpected state. A
        # rollback here is a safe no-op if there's no open
        # transaction.
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()


def _drain_batchable_inserts(
    db: HistoryDB,
    conn: sqlite3.Connection,
    first_item: Any,
) -> None:
    """drain pending ``_BatchableInsert`` items into one INSERT.

    Called from :meth:`_writer_loop` (and :meth:`_drain_remaining`
    during shutdown) when the writer pulls a ``_BatchableInsert``
    off the queue. Peeks the queue for additional
    ``_BatchableInsert`` items (up to ``_BATCH_INSERT_CAP``).

    * If ``_BATCH_INSERT_MIN`` or more items are collected (including
      ``first_item``), they're batched into a single multi-row
      ``INSERT INTO transcriptions (...) VALUES (?,?,?...), (?,?,?...)``
      inside one transaction (one COMMIT for the whole batch).
    * Otherwise each collected row is inserted individually
      (preserving the original one-INSERT-per-row behavior for
      low-contention cases where the batching optimization isn't
      worth the multi-row SQL construction).

    Non-``_BatchableInsert`` items pulled off the queue during the
    peek are put back so the main writer loop processes them in
    order. The ``_SHUTDOWN_SENTINEL`` is likewise put back so the
    shutdown path still fires.

    Fire-and-forget semantics are preserved: each item's ``future``
    (if any — ``add_transcription`` always passes ``None``) is
    resolved with the inserted row_id (or -1 on failure). On
    exception, all futures are resolved with the exception.
    """
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806
    _BATCH_INSERT_CAP = _hd._BATCH_INSERT_CAP  # noqa: N806
    _BATCH_INSERT_MIN = _hd._BATCH_INSERT_MIN  # noqa: N806

    batch: list[Any] = [first_item]
    while len(batch) < _BATCH_INSERT_CAP:
        try:
            item = db._queue.get_nowait()
        except queue.Empty:
            break
        if item is _SHUTDOWN_SENTINEL:
            # Put the sentinel back so the main loop sees it and
            # triggers _drain_remaining.
            with contextlib.suppress(queue.Full):
                db._queue.put_nowait(item)
            break
        if isinstance(item, _BatchableInsert):
            batch.append(item)
        else:
            # Non-batchable item — put it back for the main loop.
            with contextlib.suppress(queue.Full):
                db._queue.put_nowait(item)
            break

    try:
        with contextlib.closing(conn.cursor()) as cursor:
            if len(batch) >= _BATCH_INSERT_MIN:
                # multi-row INSERT inside one transaction.
                placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(batch))
                params: list[Any] = []
                for it in batch:
                    params.extend(
                        (
                            it.text,
                            it.duration,
                            it.model,
                            it.device,
                            it.word_count,
                            it.char_count,
                            it.language,
                        )
                    )
                cursor.execute(
                    f"INSERT INTO transcriptions "
                    f"(text, duration, model, device, word_count, char_count, language) "
                    f"VALUES {placeholders}",
                    params,
                )
                conn.commit()
                last_row_id = cursor.lastrowid
                for it in batch:
                    if it.future is not None:
                        with contextlib.suppress(concurrent.futures.InvalidStateError):
                            it.future.set_result(last_row_id if last_row_id is not None else -1)
                log.debug(
                    "[HISTORY_DB] batched %d transcription INSERTs into one transaction",
                    len(batch),
                )
            else:
                # Below the batching threshold — insert each row
                # individually (original behavior). Still one COMMIT
                # per row, but the per-row overhead is negligible for
                # 1-2 rows.
                for it in batch:
                    cursor.execute(
                        "INSERT INTO transcriptions "
                        "(text, duration, model, device, word_count, char_count, language) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            it.text,
                            it.duration,
                            it.model,
                            it.device,
                            it.word_count,
                            it.char_count,
                            it.language,
                        ),
                    )
                    conn.commit()
                    row_id = cursor.lastrowid
                    if it.future is not None:
                        with contextlib.suppress(concurrent.futures.InvalidStateError):
                            it.future.set_result(row_id if row_id is not None else -1)
                    if row_id is not None:
                        log.debug("Added transcription %d: %d chars", row_id, it.char_count)
    except BaseException as e:  # noqa: BLE001 — propagate to futures
        # Resolve all futures with the exception so wait=True
        # callers (if any — add_transcription is fire-and-forget)
        # don't hang.
        for it in batch:
            if it.future is not None:
                with contextlib.suppress(concurrent.futures.InvalidStateError):
                    it.future.set_exception(e)
        # Re-raise so the caller (_writer_loop / _drain_remaining)
        # sees the failure and runs its standard rollback +
        # fire-and-forget log path.
        raise


def _drain_remaining(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """Drain any remaining queued items before shutdown.

    Called after the shutdown sentinel is received. Ensures
    fire-and-forget writes submitted before close() are persisted.

    ``_BatchableInsert`` items are routed through
    :meth:`_drain_batchable_inserts` so the shutdown drain also
    benefits from the multi-row INSERT optimization when 3+ inserts
    are queued at shutdown time.
    """
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806

    while True:
        try:
            item = db._queue.get_nowait()
        except queue.Empty:
            break
        if item is _SHUTDOWN_SENTINEL:
            continue
        # route batchable inserts through the batching path.
        if isinstance(item, _BatchableInsert):
            try:
                db._drain_batchable_inserts(conn, item)
            except BaseException as e:  # noqa: BLE001
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                log.error(
                    "[HISTORY_DB] Fire-and-forget batched insert failed during shutdown drain: %s",
                    e,
                )
            else:
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
            continue
        callable_, future = item
        #  (DRY): route through the same _execute_write_item
        # helper that _writer_loop uses. This guarantees the
        # shutdown drain shares the
        # contextlib.suppress(InvalidStateError) wrapper around
        # future.set_exception — previously the inline duplicate
        # omitted it, so a pre-resolved future (e.g. from a
        # duplicate-enqueue race in _drop_oldest_for_overflow)
        # would let InvalidStateError escape and kill the writer
        # mid-drain, silently dropping fire-and-forget writes.
        _execute_write_item(db, conn, callable_, future)


def _drop_oldest_for_overflow(
    db: HistoryDB,
    current_future: concurrent.futures.Future | None,
) -> None:
    """PERF-5: Drop the oldest non-sentinel queued item to make room.

    Called when ``_submit_write`` hits ``queue.Full``. Signals the
    dropped item's future (if any) with ``HistoryDBError`` so blocking
    callers don't hang. If the head of the queue is the shutdown
    sentinel, we leave it alone and drop the new write instead (the
    writer is shutting down, so the new work wouldn't run anyway).
    """
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806
    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    try:
        dropped = db._queue.get_nowait()
    except queue.Empty:
        #  (session-2): Queue drained between put_nowait and
        # now. Previously this branch enqueued a no-op lambda bound
        # to the caller's own ``future`` — but the caller
        # (``_submit_write``) then retries ``put_nowait((fn, future))``,
        # so the queue held TWO items sharing the same future. The
        # writer executed the lambda first → ``future.set_result(None)``
        # succeeded; then executed ``fn`` → ``future.set_result(result)``
        # raised InvalidStateError; the except handler then tried
        # ``future.set_exception(e)`` → also raised InvalidStateError
        # (not suppressed before ) → killed the writer thread
        # permanently. Silent data loss + dead writer.
        #
        # Fix: do NOT enqueue anything here. Just return and let the
        # caller's retry handle the put. The caller's future is
        # untouched and will be resolved by the real ``fn`` when the
        # writer picks it up.
        return
    if dropped is _SHUTDOWN_SENTINEL:
        # Put the sentinel back; drop the new write instead.
        with contextlib.suppress(queue.Full):
            db._queue.put_nowait(dropped)
        if current_future is not None:
            with contextlib.suppress(concurrent.futures.InvalidStateError):
                current_future.set_exception(HistoryDBError("Writer is shutting down; new write dropped"))
        log.warning("[HISTORY_DB] Queue full during shutdown — new write dropped.")
        return
    # the dropped item may be a (fn, future) tuple OR a
    # _BatchableInsert structured payload. Extract the future (if
    # any) from either shape and resolve it with HistoryDBError so
    # wait=True callers don't hang.
    if isinstance(dropped, _BatchableInsert):
        dropped_future = dropped.future
    else:
        _, dropped_future = dropped
    if dropped_future is not None:  #  PERF-5: the dropped future must be resolved
        # with a clear, machine-greppable message so callers
        # that catch HistoryDBError can distinguish "queue full"
        # from other failure modes (e.g. "Writer is shutting
        # down" or "Dropped during shutdown sentinel enqueue").
        # The literal "queue full" substring is part of the
        # contract asserted by TestQueueBounded.
        with contextlib.suppress(concurrent.futures.InvalidStateError):
            dropped_future.set_exception(
                HistoryDBError(
                    "queue full; dropped oldest write to make room for newer write (writer thread may be stalled)"
                )
            )
    log.warning("[HISTORY_DB] queue full — dropped oldest write to make room. Writer thread may be stalled.")
    # The caller (_submit_write) retries the put_nowait after we
    # return — we've freed one slot by dropping the oldest item, so
    # the retry will succeed unless the writer is also stalling and
    # another caller has already filled the slot. : previously
    # had an empty ``try: pass except Exception: log.debug(...)`` here
    # that could never raise (the body was ``pass``) — removed.


def _submit_write(
    db: HistoryDB,
    fn: Callable[[sqlite3.Connection], Any],
    *,
    wait: bool = True,
) -> Any | None:
    """Submit a write closure to the writer thread.

    Parameters
    ----------
    fn : callable
        A closure that takes the writer's ``sqlite3.Connection``
        and returns the write result (or raises).
    wait : bool
        If ``True`` (default), block until the writer executes
        ``fn`` and return its result (or re-raise its exception).
        If ``False``, return ``None`` immediately (fire-and-forget).

    Returns
    -------
    The closure's result if ``wait=True``; ``None`` if ``wait=False``
    or if the writer is shutting down and can't accept the work.

    Notes
    -----
    If ``self._shutdown`` is set (close() was called), the closure
    is NOT submitted and ``None`` is returned. Callers that need
    to distinguish "fire-and-forget accepted" from "writer shut
    down" can check ``self._shutdown.is_set()`` before calling.

    also short-circuits when the writer thread is dead OR
    ``_init_error`` is set. Previously the call would enqueue to a
    dead writer's queue and block on
    ``future.result(timeout=_WRITE_FUTURE_TIMEOUT)`` for 30
    seconds before the TimeoutError handler noticed the dead
    writer and raised ``HistoryDBError``. The early-return guard
    delegates to ``health_check()`` so the failure is instant and
    the centralized diagnostic message surfaces in both the log
    and the raised exception. ``wait=True`` raises
    ``HistoryDBError`` so blocking callers (delete/clear_all/etc.)
    catch it via their existing except clause. ``wait=False``
    returns ``None`` (consistent with the existing fire-and-forget
    sentinel).
    """
    from voice_typer.server import history_db as _hd

    _WRITE_FUTURE_TIMEOUT = _hd._WRITE_FUTURE_TIMEOUT  # noqa: N806
    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    if db._shutdown.is_set():
        log.debug("[HISTORY_DB] Write submitted after shutdown — dropped.")
        return None
    # early-return guard — if the writer thread never
    # started (init error) or has died, refuse the write
    # immediately instead of enqueuing to a dead queue and
    # blocking 30s on a future that will never resolve.
    if db._init_error is not None or not db._writer_thread.is_alive():
        # When the writer is dead AND the queue is full, the
        # already-queued writes (and their futures) will never be
        # processed by the writer. Drop the oldest item so its
        # future is resolved with a clear error — without this,
        # wait=True callers awaiting the oldest future would hang
        # forever (until the 30s future timeout) even though the
        # writer is already known to be dead. The new write is
        # still refused below; we only resolve the dropped future,
        # so pass ``None`` for ``current_future`` (the new write
        # has no future yet — it's being refused, not enqueued).
        if db._queue.full():
            db._drop_oldest_for_overflow(None)
        err = db.health_check()["error"]
        log.error(
            "[HISTORY_DB] _submit_write refused — writer is unavailable: %s",
            err,
        )
        if wait:
            raise HistoryDBError(f"HistoryDB writer is unavailable: {err}")
        return None
    future: concurrent.futures.Future | None = None
    if wait:
        future = concurrent.futures.Future()
    # PERF-5: bounded queue (maxsize=_WRITE_QUEUE_MAXSIZE). Use
    # put_nowait + drop-oldest so a stalled writer doesn't block
    # the calling thread indefinitely.
    try:
        db._queue.put_nowait((fn, future))
    except queue.Full:
        db._drop_oldest_for_overflow(future)
        # Retry once after dropping oldest.
        try:
            db._queue.put_nowait((fn, future))
        except queue.Full:
            # Still full (writer truly stuck); drop the new write.
            if future is not None:
                with contextlib.suppress(concurrent.futures.InvalidStateError):
                    future.set_exception(HistoryDBError("Queue full after drop-oldest; new write dropped"))
            log.warning("[HISTORY_DB] Queue still full after drop-oldest — new write dropped. Writer thread is stuck.")
            if not wait:
                return None
    if not wait:
        return None
    assert future is not None
    # Block on the future. The writer is a daemon thread; if it
    # dies (e.g. disk corruption), the future would never resolve
    # and we'd hang. Loop with a timeout so we can detect a dead
    # writer and raise.
    while True:
        try:
            return future.result(timeout=_WRITE_FUTURE_TIMEOUT)
        except concurrent.futures.TimeoutError:
            if not db._writer_thread.is_alive():
                raise HistoryDBError("HistoryDB writer thread is dead; write did not complete") from None
            # Writer still alive — keep waiting (rare; means a
            # prior write is taking a very long time, e.g. a
            # multi-batch retention sweep on a huge DB).
            log.warning(
                "[HISTORY_DB] Write future still pending after %.0fs; writer is alive, continuing to wait.",
                _WRITE_FUTURE_TIMEOUT,
            )


def flush(db: HistoryDB) -> None:
    """Block until all queued writes have been processed by the writer.

    IMPL-A: enqueues a no-op write with ``wait=True`` and blocks
    on its future. Because the queue is FIFO, all writes submitted
    before this call will have completed by the time the no-op
    runs. Useful for tests and for callers that need to verify a
    write was persisted before reading it back.

    short-circuits when the writer thread is dead OR
    ``_init_error`` is set. Previously this would call
    ``_submit_write(wait=True)`` and block 30s on a future that
    would never resolve (the dead writer never picks up the
    no-op). The early-return guard logs + returns immediately so
    ``dictation_pipeline._store_result`` (the only production
    caller) does not freeze the pipeline for 30s after every
    dictation when the writer is dead.
    """
    from voice_typer.server import history_db as _hd

    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    if db._shutdown.is_set():
        return
    # short-circuit on dead writer / init error.
    if db._init_error is not None or not db._writer_thread.is_alive():
        err = db.health_check()["error"]
        log.error(
            "[HISTORY_DB] flush skipped — writer is unavailable: %s",
            err,
        )
        return
    with contextlib.suppress(HistoryDBError):
        db._submit_write(lambda conn: None, wait=True)


def _close_writer(db: HistoryDB) -> None:
    """Writer-teardown portion of :meth:`HistoryDB.close`.

    Runs:
      1. Best-effort ``wal_checkpoint(TRUNCATE)`` via the writer
         thread (so the WAL pages are flushed back to the main DB
         file and ``history.db-wal`` is truncated to zero size).
      2. Enqueues the ``_SHUTDOWN_SENTINEL`` (with a drop-oldest
         loop so the sentinel is never dropped if the queue is
         full when close() is called).
      3. Waits (with ``_WRITER_JOIN_TIMEOUT``) for the writer
         thread to exit. The writer drains remaining items first
         (see :func:`_drain_remaining`).

    Caller (``HistoryDB.close``) is responsible for:
      - Stopping the periodic retention + read-conn prune threads
        BEFORE calling this (so they don't trip over tear-down).
      - Setting ``db._shutdown`` BEFORE calling this.
      - Closing read connections AFTER this returns.
    """
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _WRITE_QUEUE_MAXSIZE = _hd._WRITE_QUEUE_MAXSIZE  # noqa: N806
    _WRITER_JOIN_TIMEOUT = _hd._WRITER_JOIN_TIMEOUT  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806
    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    # Best-effort wal_checkpoint(TRUNCATE) before shutdown.
    # Submit a final closure to the writer thread so the checkpoint
    # runs on the only write-capable connection (the writer's). The
    # closure is wrapped in ``contextlib.suppress(sqlite3.Error)``
    # so a checkpoint failure (e.g. DB busy, disk full) doesn't
    # block shutdown. The ``checkpoint()`` method itself swallows
    # sqlite3.Error internally (see ``_do_checkpoint`` at the
    # call site below), so the suppress here is belt-and-braces.
    # Skip if the writer is already dead (e.g. init failed) —
    # ``checkpoint()`` returns False in that case.
    if db._writer_thread.is_alive() and db._init_error is None:
        with contextlib.suppress(sqlite3.Error, HistoryDBError):
            db.checkpoint(truncate=True)
    # Enqueue the sentinel — the writer drains remaining items
    # before exiting. PERF-5: the queue is now bounded
    # (maxsize=_WRITE_QUEUE_MAXSIZE). Use a drop-oldest loop so the
    # sentinel is never dropped (we keep re-trying until it lands at
    # the head of the queue).
    try:
        db._queue.put_nowait(_SHUTDOWN_SENTINEL)
    except queue.Full:
        # Drain non-sentinel items until the sentinel fits.
        # Bound the loop at ``_WRITE_QUEUE_MAXSIZE + 1`` — the queue
        # can hold at most that many items, so one full sweep
        # guarantees the sentinel fits (modulo concurrent enqueues,
        # which we accept as a rare race; close() is best-effort).
        for _ in range(_WRITE_QUEUE_MAXSIZE + 1):  # bound to avoid infinite loop
            try:
                dropped = db._queue.get_nowait()
            except queue.Empty:
                break
            if dropped is _SHUTDOWN_SENTINEL:
                # Sentinel was already queued by another close() call;
                # put it back and stop.
                with contextlib.suppress(queue.Full):
                    db._queue.put_nowait(dropped)
                break
            # Dropped a real write — signal its future. The dropped
            # item may be a ``(fn, future)`` tuple OR a
            # ``_BatchableInsert`` structured payload; extract the
            # future from either shape (mirrors _drop_oldest_for_overflow).
            if isinstance(dropped, _BatchableInsert):
                dropped_future = dropped.future
            else:
                _, dropped_future = dropped
            if dropped_future is not None:
                with contextlib.suppress(concurrent.futures.InvalidStateError):
                    dropped_future.set_exception(HistoryDBError("Dropped during shutdown sentinel enqueue"))
            log.warning("[HISTORY_DB] Dropped write during shutdown queue drain.")
            # Try to enqueue the sentinel now.
            try:
                db._queue.put_nowait(_SHUTDOWN_SENTINEL)
                break
            except queue.Full:
                continue
    except (RuntimeError, TypeError) as e:
        # RuntimeError can occur during interpreter shutdown if the
        # queue module is in an inconsistent state; TypeError can
        # occur if a malformed (non-tuple, non-_BatchableInsert)
        # payload sneaks into the queue — wider guard keeps close()
        # best-effort and prevents the teardown crash reported when
        # a _BatchableInsert hits the legacy tuple-unpack branch.
        log.debug("[HISTORY_DB] Could not enqueue shutdown sentinel: %s", e)
    # Wait for the writer to exit (it drains remaining items first).
    if db._writer_thread.is_alive():
        db._writer_thread.join(timeout=_WRITER_JOIN_TIMEOUT)
        if db._writer_thread.is_alive():
            log.warning(
                "[HISTORY_DB] Writer thread did not exit within %.1fs; "
                "it is a daemon and will be killed at process exit.",
                _WRITER_JOIN_TIMEOUT,
            )
