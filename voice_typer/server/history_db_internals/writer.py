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
- :func:`_run_checkpoint` — periodic passive WAL checkpoint (bounds WAL
  growth; clears lingering transactions first).
- :func:`_fts5_startup_rebuild` — best-effort FTS5 ``'rebuild'`` sweep
  on launch, gated by the persisted ``fts5_rebuild_failed`` flag.
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

NOTE: ``_run_checkpoint`` and ``_fts5_startup_rebuild`` were extracted
here in a later split (they previously remained on the ``HistoryDB``
class because ``tests/test_history_db_wal_checkpoint_interval.py`` /
``tests/test_vocabulary_history_db_fixes.py`` pinned their bodies via
``inspect.getsource(HistoryDB._run_checkpoint)`` — those pins have been
retargeted to :func:`_run_checkpoint` below). Both run exclusively on
the writer thread's connection, which is why they live in this module;
the public class keeps thin delegating methods so class-level
monkeypatch sites (e.g. ``monkeypatch.setattr(HistoryDB,
"_fts5_startup_rebuild", ...)``) keep working unchanged.
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
    # At-rest encryption: resolve the DEK (one keyring read, bounded by
    # the 5s keyring-I/O timeout isolation) and kick the plaintext→
    # ciphertext backfill BEFORE readiness is signaled. Running it here
    # — rather than after the ready signal — makes the encryption state
    # deterministic the moment ``HistoryDB()`` returns: no reader can
    # observe a flagged row with an UNRESOLVED key (which would surface
    # a bogus "<decryption failed>" placeholder during the keyring
    # read). The backfill itself is a queued writer item (never blocks
    # startup); only the single DEK load sits in the init window, and
    # it is skipped entirely on migration failure.
    if db._init_error is None:
        with contextlib.suppress(Exception):
            db._init_encryption(conn)
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


def _run_checkpoint(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """Run a passive WAL checkpoint to bound WAL growth.

    PASSIVE mode doesn't block — it checkpoints as much as
    possible without forcing readers/writers to wait. Called every
    ``_WAL_CHECKPOINT_INTERVAL`` seconds by the writer thread.

    WAL-CHECKPOINT-FIX: Always rollback any lingering uncommitted
    transaction BEFORE running the checkpoint. If a write closure
    failed after ``conn.commit()`` but before the exception handler
    reached ``conn.rollback()``, or if a closure raised mid-execution
    without committing, the writer's own connection has a pending
    uncommitted transaction. ``PRAGMA wal_checkpoint(PASSIVE)`` on
    the same connection then fails with "database table is locked"
    — not because another thread holds the lock, but because the
    writer's own connection hasn't released it yet.

    The rollback is a no-op when there is no open transaction, so
    it's safe to call unconditionally.
    """
    # Lazy import so the interval tracks monkeypatches on the
    # ``history_db`` module namespace (e.g. _WAL_CHECKPOINT_INTERVAL).
    from voice_typer.server import history_db as _hd

    wal_checkpoint_interval = _hd._WAL_CHECKPOINT_INTERVAL  # noqa: N806

    # WAL-CHECKPOINT-FIX: clear any lingering transaction from
    # a prior write before checkpointing. The rollback is safe
    # because all write closures commit before returning, so
    # any open transaction here is an unexpected/transient state.
    try:
        conn.rollback()
        result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if result is not None:
            status, pages_checkpointed, total_pages = result
            # Only log when a non-trivial checkpoint happens (>= 100
            # pages) to avoid flooding the log at the checkpoint
            # cadence (_WAL_CHECKPOINT_INTERVAL). Tiny checkpoints
            # (e.g. 20 pages) are silent — the WAL is healthy, no
            # action needed.
            # status: 0=ok, 1=partial(active readers), 2=full(needs restart)
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
        # This can happen when an external process (e.g. antivirus
        # scan) holds a lock on the WAL file. The next checkpoint
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
    """Best-effort FTS5 ``'rebuild'`` gated by a persisted failure flag.

    The ``delete``, ``clear_all``, and ``apply_retention`` paths
    each issue the FTS5 ``'rebuild'`` (or ``'optimize'`` for
    per-row deletes) command after their bulk DELETEs to zero
    dictated text out of ``transcriptions_fts_data`` (GDPR Art.
    17 right-to-erasure). But that rebuild is wrapped in a
    tolerant ``try/except sqlite3.Error`` — if it fails
    (transient FTS5 error, disk full), the failure is logged
    and swallowed (no raise, no rollback), incrementing
    ``db._fts5_rebuild_failures`` and publishing an
    ``event_bus`` event. The segment data from the failed delete
    lingers in ``transcriptions_fts_data``, recoverable via
    forensic tools, until FTS5's background compaction happens
    to merge that segment (days or weeks later).

    This startup sweep bounds the worst-case exposure window to
    "between launches": on HistoryDB construction (after the
    schema is initialized), we run ``'rebuild'`` ONCE when
    needed. If the previous session's delete-time rebuild
    failed, this sweep clears the lingering segment data on the
    next launch.

    Gating: the previous implementation ran the O(N) ``'rebuild'``
    on EVERY launch — 100-500ms of cold-start latency even when
    the previous session had no FTS5 failure. Now a
    ``fts5_rebuild_failed`` flag is persisted in
    ``schema_meta``:

    - flag = ``'1'``: a previous delete/clear_all/retention
      rebuild FAILED → run startup rebuild to clear the
      lingering FTS5 segment data.
    - flag = ``'0'``: previous startup rebuild succeeded and no
      failure has been recorded since → SKIP the O(N) rebuild.
    - flag absent (NULL): never set before (fresh DB or first
      launch after this fix landed) → run rebuild once to
      establish a clean baseline, then set flag to ``'0'``.

    On successful rebuild the flag is set to ``'0'`` so
    subsequent launches skip. On a failed delete/clear_all
    rebuild (in the CRUD write path) or a failed retention rebuild
    (in ``retention.py``), the flag is set to ``'1'`` so the
    next launch retries.

    Best-effort: a failure here is logged at WARNING (not
    ERROR — a startup sweep failure is not actionable
    mid-session; the next session will retry) and swallowed —
    the app must still start. Tolerant of older DBs that
    haven't yet run the V3 migration (no
    ``transcriptions_fts`` table) — the ``sqlite3.Error``
    raised by "no such table" is caught and logged at WARNING.
    """
    # Read the persisted fts5_rebuild_failed flag from
    # schema_meta. The schema_meta table is created by
    # init_schema (CREATE TABLE IF NOT EXISTS) BEFORE this
    # function is called, so the SELECT is always safe.
    try:
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute("SELECT value FROM schema_meta WHERE key = 'fts5_rebuild_failed'")
            row = cursor.fetchone()
            flag_value = row[0] if row is not None else None
    except sqlite3.Error as e:
        # If schema_meta itself is unreadable, fall through to
        # running the rebuild — best-effort, mirrors the
        # pre-flag behavior.
        log.debug(
            "[HISTORY] Could not read fts5_rebuild_failed flag from schema_meta: %s — running rebuild",
            e,
        )
        flag_value = None

    # Steady-state skip: flag is explicitly '0' (previous
    # rebuild succeeded, no failure recorded since). Avoids the
    # O(N) rebuild on every launch. We can't also skip when the
    # flag is NULL: a fresh DB has no flag row yet, and the
    # existing tests (and the GDPR guarantee) require the
    # rebuild to run at least once on a fresh DB to establish a
    # clean baseline post-V3-migration.
    if flag_value == "0":
        log.debug(
            "[HISTORY] FTS5 startup rebuild succeeded (skipped — previous rebuild succeeded, no failure recorded since)"
        )
        return

    try:
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")
        conn.commit()
        # Persist the success state so subsequent launches skip
        # the O(N) rebuild. INSERT OR REPLACE upserts the flag
        # row (created here on first launch, updated on every
        # successful retry after a prior failure).
        with contextlib.suppress(sqlite3.Error):
            with contextlib.closing(conn.cursor()) as flag_cursor:
                flag_cursor.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '0')"
                )
            conn.commit()
        log.debug("[HISTORY] FTS5 startup rebuild succeeded")
        # A rebuild re-tokenizes every row from the CONTENT table —
        # rows that are encrypted at rest get CIPHERTEXT tokens in
        # the index, breaking FTS search for them. Remember that the
        # rebuild ran so ``_init_encryption`` (which resolves the DEK
        # right after this) can queue the decrypt-aware re-index.
        db._fts5_rebuild_ran = True
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] FTS5 startup rebuild failed: %s — segments from failed deletes may persist",
            e,
        )
        # Persist the failure state so the next launch retries.
        # Best-effort: a failure here (e.g. disk full) means we
        # can't record the flag, but the in-memory
        # ``_fts5_rebuild_failures`` counter is NOT incremented
        # for the startup path (only the delete/clear_all/
        # retention paths increment it) — the startup sweep is
        # best-effort and a failure to persist the flag just
        # means the next launch re-runs the rebuild (the safe
        # default).
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


def _encrypt_batch_rows(
    cursor: sqlite3.Cursor,
    batch: list[Any],
    last_row_id: int | None,
) -> None:
    """Flip freshly-inserted rows from plaintext to ciphertext in place.

    At-rest-encryption write side (ADR §2/§6; cipher module
    ``voice_typer/server/_text_crypto.py``). The rows were just inserted
    with PLAINTEXT and ``text_is_encrypted=0`` so the AFTER-INSERT FTS
    trigger indexed the plaintext tokens; this helper UPDATEs each row to
    ``encrypt_text(text)`` + ``text_is_encrypted=1``. The guarded
    AFTER-UPDATE trigger (see ``schema._MIGRATION_V4``) skips the FTS
    delete+reinsert for that flag flip, so the plaintext tokens stay in
    the index and full-text search keeps matching encrypted rows.

    Row ids: a multi-row INSERT under AUTOINCREMENT assigns consecutive
    rowids and ``cursor.lastrowid`` is the LAST one (verified in-sandbox;
    the single writer thread guarantees no interleaving), so row ``i`` of
    ``n`` has id ``last_row_id - n + 1 + i``. Each UPDATE's rowcount is
    checked — a zero means the id arithmetic went stale (should be
    impossible), which is logged loudly rather than silently encrypting
    the wrong row.

    Runs inside the caller's transaction: the INSERT and the encryption
    UPDATEs commit atomically, so readers never observe the intermediate
    plaintext row.
    """
    from voice_typer.server import _text_crypto

    dek = _text_crypto.get_dek_cached()
    if dek is None or last_row_id is None:
        # No DEK (keyring unavailable / key-loss / not yet resolved):
        # the rows stay plaintext with flag 0 — byte-identical to the
        # pre-encryption behavior (zero regression when the keychain is
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
                "id=%d (expected 1) — row may remain PLAINTEXT on disk",
                cursor.rowcount,
                row_id,
            )


# Single source for the transcription INSERT SQL (multi-row batch path
# and the below-threshold single-row fallback both build their statement
# from these pieces, so the column list and placeholder shape cannot
# drift between the two paths).
_INSERT_SQL_COLUMNS = "(text, duration, model, device, word_count, char_count, language)"
_INSERT_SQL_ROW_PLACEHOLDERS = "(?, ?, ?, ?, ?, ?, ?)"


def _build_insert_sql(row_count: int) -> str:
    """Build the transcription INSERT statement for ``row_count`` rows.

    ``row_count == 1`` yields the exact statement the single-row
    fallback executes; ``row_count >= 2`` yields the multi-row form
    used by the batching path. One helper = one authoritative SQL shape
    for both call sites.
    """
    values = ",".join([_INSERT_SQL_ROW_PLACEHOLDERS] * row_count)
    return f"INSERT INTO transcriptions {_INSERT_SQL_COLUMNS} VALUES {values}"


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
                # trigger indexes it); when a DEK is cached, the rows are
                # flipped to ciphertext by _encrypt_batch_rows before the
                # single COMMIT below — readers never see the intermediate
                # plaintext state.
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
                # Below the batching threshold — insert each row
                # individually (original behavior). Still one COMMIT
                # per row, but the per-row overhead is negligible for
                # 1-2 rows.
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
    _WRITE_FUTURE_TOTAL_TIMEOUT = _hd._WRITE_FUTURE_TOTAL_TIMEOUT  # noqa: N806
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
    #
    # Hard total deadline: even with the per-retry timeout above,
    # a writer that is *alive* but never makes progress (e.g. a
    # multi-batch retention sweep on a huge DB locked by an
    # external process, antivirus, or a deadlocked SQLite WAL)
    # would loop forever between the 30s per-retry waits. The
    # ``_WRITE_FUTURE_TOTAL_TIMEOUT`` (60s = 2x the per-retry
    # timeout) caps the cumulative wait so the IPC handler thread
    # surfaces a clear ``HistoryDBError`` instead of hanging
    # indefinitely. The per-retry timeout is preserved (no
    # behavior change for successful slow writes < 60s); the
    # deadline only ADDS an upper bound.
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
