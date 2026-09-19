"""History writer-thread helpers."""

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

#: Guards the one-time ``[HISTORY] FTS5 startup rebuild succeeded
_announced_fts5_skip_paths: set[str] = set()


def _reset_fts5_skip_paths() -> None:
    """Test seam, clear the per-path FTS5-skip log guard."""
    _announced_fts5_skip_paths.clear()


def _writer_loop(db: HistoryDB) -> None:
    """History DB writer-thread internals (queue drain, WAL, FTS rebuild)."""
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
        conn = db._init_db_schema(conn)
    except BaseException as e:  # noqa: BLE001, surface to __init__
        db._init_error = e
        db._writer_ready.set()
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        return
    # At-rest encryption: resolve the DEK (one keyring read, bounded by
    if db._init_error is None:
        with contextlib.suppress(Exception):
            db._init_encryption(conn)
    db._writer_ready.set()
    # if schema init set _init_error (e.g. migration
    if db._init_error is not None:
        log.error(
            "[HISTORY_DB] Skipping writer loop, schema init failed: %s",
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
        # structured batchable INSERT payload, drain pending
        if isinstance(item, _BatchableInsert):
            db._drain_batchable_inserts(conn, item)
            # WAL-CHECKPOINT-FIX: post-write cleanup (same as the
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            continue
        callable_, future = item
        db._execute_write_item(conn, callable_, future)
    # Drain loop exited. Close the writer's connection.
    try:
        conn.close()
    except sqlite3.Error as e:
        log.warning("[HISTORY_DB] Error closing writer connection: %s", e)


def _run_checkpoint(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """PASSIVE mode doesn't block, it checkpoints as much as
    possible without forcing readers/writers to wait. Called every
    ``_WAL_CHECKPOINT_INTERVAL`` seconds by the writer thread.
    """
    # ``history_db`` module namespace (e.g. _WAL_CHECKPOINT_INTERVAL).
    from voice_typer.server import history_db as _hd

    wal_checkpoint_interval = _hd._WAL_CHECKPOINT_INTERVAL  # noqa: N806

    # WAL-CHECKPOINT-FIX: clear any lingering transaction from
    try:
        conn.rollback()
        result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if result is not None:
            status, pages_checkpointed, total_pages = result
            # cadence (_WAL_CHECKPOINT_INTERVAL). Tiny checkpoints
            if pages_checkpointed >= 100:
                if status == 0:
                    log.debug(
                        "[HISTORY_DB] WAL checkpointed %d pages",
                        pages_checkpointed,
                    )
                else:
                    log.debug(
                        "[HISTORY_DB] WAL checkpoint partial: %d/%d pages (status=%d)",
                        pages_checkpointed,
                        total_pages,
                        status,
                    )
    except sqlite3.OperationalError as e:
        # attempt (after _WAL_CHECKPOINT_INTERVAL) will retry.
        log.debug(
            "[HISTORY_DB] WAL checkpoint skipped (will retry in %.0fs): %s",
            wal_checkpoint_interval,
            e,
        )
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] WAL checkpoint failed unexpectedly: %s",
            e,
        )


def _fts5_startup_rebuild(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """The ``delete``, ``clear_all``, and ``apply_retention`` paths
    each issue the FTS5 ``'rebuild'`` (or ``'optimize'`` for
    """
    # Read the persisted fts5_rebuild_failed flag from
    try:
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute("SELECT value FROM schema_meta WHERE key = 'fts5_rebuild_failed'")
            row = cursor.fetchone()
            flag_value = row[0] if row is not None else None
    except sqlite3.Error as e:
        # If schema_meta itself is unreadable, fall through to
        log.debug(
            "[HISTORY] Could not read fts5_rebuild_failed flag from schema_meta: %s, running rebuild",
            e,
        )
        flag_value = None

    # Steady-state skip: flag is explicitly '0' (previous
    if flag_value == "0":
        key = str(getattr(db, "db_path", ""))
        if key not in _announced_fts5_skip_paths:
            _announced_fts5_skip_paths.add(key)
            log.debug(
                "[HISTORY] FTS5 startup rebuild succeeded "
                "(skipped, previous rebuild succeeded, no failure recorded since)"
            )
        return

    try:
        with contextlib.closing(conn.cursor()) as cursor:
            # Both FTS5 shadow indexes (unicode61 + trigram CJK) in
            from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

            cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")
            if cjk_trigram_table_exists(conn):
                cursor.execute("INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk) VALUES('rebuild')")
        conn.commit()
        # Persist the success state so subsequent launches skip
        with contextlib.suppress(sqlite3.Error):
            with contextlib.closing(conn.cursor()) as flag_cursor:
                flag_cursor.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '0')"
                )
            conn.commit()
        log.debug("[HISTORY] FTS5 startup rebuild succeeded")
        # A rebuild re-tokenizes every row from the CONTENT table —
        db._fts5_rebuild_ran = True
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] FTS5 startup rebuild failed: %s, segments from failed deletes may persist",
            e,
        )
        # Persist the failure state so the next launch retries.
        with contextlib.suppress(sqlite3.Error):
            with contextlib.closing(conn.cursor()) as flag_cursor:
                flag_cursor.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '1')"
                )
            conn.commit()


def _execute_write_item(
    db: HistoryDB,
    conn: sqlite3.Connection,
    callable_: Callable[[sqlite3.Connection], Any],
    future: concurrent.futures.Future | None,
) -> None:
    """verbatim between ``_writer_loop`` and ``_drain_remaining``,
    with one critical divergence, ``_drain_remaining`` was
    """
    try:
        result = callable_(conn)
        if future is not None:
            future.set_result(result)
    except BaseException as e:  # noqa: BLE001, propagate to future
        # DB-LOCK-FIX: rollback any uncommitted transaction left
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        if future is not None:
            # (session-2): Suppress InvalidStateError on
            with contextlib.suppress(concurrent.futures.InvalidStateError):
                future.set_exception(e)
        else:
            # Fire-and-forget write failed, log so it's visible.
            log.exception("[HISTORY_DB] Fire-and-forget write failed: %s", e)
    else:
        # WAL-CHECKPOINT-FIX: After a SUCCESSFUL write, ensure
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()


def _encrypt_batch_rows(
    cursor: sqlite3.Cursor,
    batch: list[Any],
    last_row_id: int | None,
) -> None:
    """At-rest-encryption write side (ADR §2/§6; cipher module
    ``voice_typer/server/_text_crypto.py``). The rows were just inserted
    """
    from voice_typer.server import _text_crypto

    dek = _text_crypto.get_dek_cached()
    if dek is None or last_row_id is None:
        # absent, C-DATA-1 / ADR §9.1).
        return
    n = len(batch)
    for i, it in enumerate(batch):
        row_id = last_row_id - n + 1 + i
        cipher = _text_crypto.encrypt_text(it.text, dek)
        cursor.execute(
            "UPDATE transcriptions SET text = ?, text_is_encrypted = 1 WHERE id = ?",
            (cipher, row_id),
        )
        if cursor.rowcount != 1:
            log.error(
                "[HISTORY_DB] encryption flag-flip UPDATE matched %d rows for "
                "id=%d (expected 1), row may remain PLAINTEXT on disk",
                cursor.rowcount,
                row_id,
            )


# Single source for the transcription INSERT SQL (multi-row batch path
_INSERT_SQL_COLUMNS = "(text, duration, model, device, word_count, char_count, language)"
_INSERT_SQL_ROW_PLACEHOLDERS = "(?, ?, ?, ?, ?, ?, ?)"


def _build_insert_sql(row_count: int) -> str:
    """``row_count == 1`` yields the exact statement the single-row
    fallback executes; ``row_count >= 2`` yields the multi-row form
    """
    values = ",".join([_INSERT_SQL_ROW_PLACEHOLDERS] * row_count)
    return f"INSERT INTO transcriptions {_INSERT_SQL_COLUMNS} VALUES {values}"


def _drain_batchable_inserts(
    db: HistoryDB,
    conn: sqlite3.Connection,
    first_item: Any,
) -> None:
    """Called from :meth:`_writer_loop` (and :meth:`_drain_remaining`
    during shutdown) when the writer pulls a ``_BatchableInsert``
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
            with contextlib.suppress(queue.Full):
                db._queue.put_nowait(item)
            break
        if isinstance(item, _BatchableInsert):
            batch.append(item)
        else:
            # Non-batchable item, put it back for the main loop.
            with contextlib.suppress(queue.Full):
                db._queue.put_nowait(item)
            break

    try:
        with contextlib.closing(conn.cursor()) as cursor:
            if len(batch) >= _BATCH_INSERT_MIN:
                # multi-row INSERT inside one transaction.
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
                # The INSERT always carries PLAINTEXT (the FTS AFTER-INSERT
                cursor.execute(
                    _build_insert_sql(len(batch)),
                    params,
                )
                last_row_id = cursor.lastrowid
                _encrypt_batch_rows(cursor, batch, last_row_id)
                conn.commit()
                for it in batch:
                    if it.future is not None:
                        with contextlib.suppress(concurrent.futures.InvalidStateError):
                            it.future.set_result(last_row_id if last_row_id is not None else -1)
                log.debug(
                    "[HISTORY_DB] batched %d transcription INSERTs into one transaction",
                    len(batch),
                )
            else:
                # Below the batching threshold, insert each row
                for it in batch:
                    cursor.execute(
                        _build_insert_sql(1),
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
                    row_id = cursor.lastrowid
                    _encrypt_batch_rows(cursor, [it], row_id)
                    conn.commit()
                    if it.future is not None:
                        with contextlib.suppress(concurrent.futures.InvalidStateError):
                            it.future.set_result(row_id if row_id is not None else -1)
                    if row_id is not None:
                        log.debug("Added transcription %d: %d chars", row_id, it.char_count)
    except BaseException as e:  # noqa: BLE001, propagate to futures
        # Resolve all futures with the exception so wait=True
        for it in batch:
            if it.future is not None:
                with contextlib.suppress(concurrent.futures.InvalidStateError):
                    it.future.set_exception(e)
        # Re-raise so the caller (_writer_loop / _drain_remaining)
        raise


def _drain_remaining(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """Called after the shutdown sentinel is received. Ensures
    fire-and-forget writes submitted before close() are persisted.
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
                log.exception(
                    "[HISTORY_DB] Fire-and-forget batched insert failed during shutdown drain: %s",
                    e,
                )
            else:
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
            continue
        callable_, future = item
        # (DRY): route through the same _execute_write_item
        _execute_write_item(db, conn, callable_, future)


def _drop_oldest_for_overflow(
    db: HistoryDB,
    current_future: concurrent.futures.Future | None,
) -> None:
    """Queue-full path: signal the dropped future with HistoryDBError.

    Called when ``_submit_write`` hits ``queue.Full``.
    """
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806
    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    try:
        dropped = db._queue.get_nowait()
    except queue.Empty:
        # (session-2): Queue drained between put_nowait and
        return
    if dropped is _SHUTDOWN_SENTINEL:
        # Put the sentinel back; drop the new write instead.
        with contextlib.suppress(queue.Full):
            db._queue.put_nowait(dropped)
        if current_future is not None:
            with contextlib.suppress(concurrent.futures.InvalidStateError):
                current_future.set_exception(HistoryDBError("Writer is shutting down; new write dropped"))
        log.warning("[HISTORY_DB] Queue full during shutdown, new write dropped.")
        return
    # the dropped item may be a (fn, future) tuple OR a
    if isinstance(dropped, _BatchableInsert):
        dropped_future = dropped.future
    else:
        _, dropped_future = dropped
    if dropped_future is not None:  #  PERF-5: the dropped future must be resolved
        # with a clear, machine-greppable message so callers
        with contextlib.suppress(concurrent.futures.InvalidStateError):
            dropped_future.set_exception(
                HistoryDBError(
                    "queue full; dropped oldest write to make room for newer write (writer thread may be stalled)"
                )
            )
    log.warning("[HISTORY_DB] queue full, dropped oldest write to make room. Writer thread may be stalled.")
    # The caller (_submit_write) retries the put_nowait after we


def _submit_write(
    db: HistoryDB,
    fn: Callable[[sqlite3.Connection], Any],
    *,
    wait: bool = True,
) -> Any | None:
    """Parameters
    ----------
    """
    from voice_typer.server import history_db as _hd

    _WRITE_FUTURE_TIMEOUT = _hd._WRITE_FUTURE_TIMEOUT  # noqa: N806
    _WRITE_FUTURE_TOTAL_TIMEOUT = _hd._WRITE_FUTURE_TOTAL_TIMEOUT  # noqa: N806
    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    if db._shutdown.is_set():
        log.debug("[HISTORY_DB] Write submitted after shutdown, dropped.")
        return None
    # early-return guard, if the writer thread never
    if db._init_error is not None or not db._writer_thread.is_alive():
        # When the writer is dead AND the queue is full, the
        if db._queue.full():
            db._drop_oldest_for_overflow(None)
        err = db.health_check()["error"]
        log.error(
            "[HISTORY_DB] _submit_write refused, writer is unavailable: %s",
            err,
        )
        if wait:
            raise HistoryDBError(f"HistoryDB writer is unavailable: {err}")
        return None
    future: concurrent.futures.Future | None = None
    if wait:
        future = concurrent.futures.Future()
    # PERF-5: bounded queue (maxsize=_WRITE_QUEUE_MAXSIZE). Use
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
            log.warning("[HISTORY_DB] Queue still full after drop-oldest, new write dropped. Writer thread is stuck.")
            if not wait:
                return None
    if not wait:
        return None
    assert future is not None
    # Block on the future. The writer is a daemon thread; if it
    loop_start = time.monotonic()
    while True:
        if time.monotonic() - loop_start >= _WRITE_FUTURE_TOTAL_TIMEOUT:
            log.warning(
                "[HISTORY_DB] Write future total deadline exceeded "
                "(%.0fs); writer is alive but stuck \u2014 aborting wait.",
                _WRITE_FUTURE_TOTAL_TIMEOUT,
            )
            raise HistoryDBError(
                f"HistoryDB write did not complete within "
                f"{_WRITE_FUTURE_TOTAL_TIMEOUT:.0f}s total deadline "
                f"(writer is alive but stuck)"
            )
        try:
            return future.result(timeout=_WRITE_FUTURE_TIMEOUT)
        except concurrent.futures.TimeoutError:
            if not db._writer_thread.is_alive():
                raise HistoryDBError("HistoryDB writer thread is dead; write did not complete") from None
            # Writer still alive. Keep waiting (rare; means a
            log.warning(
                "[HISTORY_DB] Write future still pending after %.0fs; writer is alive, continuing to wait.",
                _WRITE_FUTURE_TIMEOUT,
            )


def flush(db: HistoryDB) -> None:
    """IMPL-A: enqueues a no-op write with ``wait=True`` and blocks
    on its future. Because the queue is FIFO, all writes submitted
    """
    from voice_typer.server import history_db as _hd

    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    if db._shutdown.is_set():
        return
    # short-circuit on dead writer / init error.
    if db._init_error is not None or not db._writer_thread.is_alive():
        err = db.health_check()["error"]
        log.error(
            "[HISTORY_DB] flush skipped, writer is unavailable: %s",
            err,
        )
        return
    with contextlib.suppress(HistoryDBError):
        db._submit_write(lambda conn: None, wait=True)


def _close_writer(db: HistoryDB) -> None:
    """1. Best-effort ``wal_checkpoint(TRUNCATE)`` via the writer
    thread (so the WAL pages are flushed back to the main DB
    """
    from voice_typer.server import history_db as _hd

    _SHUTDOWN_SENTINEL = _hd._SHUTDOWN_SENTINEL  # noqa: N806
    _WRITE_QUEUE_MAXSIZE = _hd._WRITE_QUEUE_MAXSIZE  # noqa: N806
    _WRITER_JOIN_TIMEOUT = _hd._WRITER_JOIN_TIMEOUT  # noqa: N806
    _BatchableInsert = _hd._BatchableInsert  # noqa: N806
    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    # Best-effort wal_checkpoint(TRUNCATE) before shutdown.
    if db._writer_thread.is_alive() and db._init_error is None:
        with contextlib.suppress(sqlite3.Error, HistoryDBError):
            db.checkpoint(truncate=True)
    # before exiting. PERF-5: the queue is now bounded
    try:
        db._queue.put_nowait(_SHUTDOWN_SENTINEL)
    except queue.Full:
        # Drain non-sentinel items until the sentinel fits.
        for _ in range(_WRITE_QUEUE_MAXSIZE + 1):  # bound to avoid infinite loop
            try:
                dropped = db._queue.get_nowait()
            except queue.Empty:
                break
            if dropped is _SHUTDOWN_SENTINEL:
                # Sentinel was already queued by another close() call;
                with contextlib.suppress(queue.Full):
                    db._queue.put_nowait(dropped)
                break
            # Dropped a real write, signal its future. The dropped
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
