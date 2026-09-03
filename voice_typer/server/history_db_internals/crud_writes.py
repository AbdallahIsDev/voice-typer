"""CRUD write closures for transcription rows (add/delete/restore/clear/toggle).

Extracted from the once-monolithic ``history_db.py``. The public
``HistoryDB`` write methods (``add_transcription``, ``delete``,
``restore``, ``clear_all``, ``toggle_favorite``) stay on the class with
their ``_wrap_write`` decorators, docstrings, argument parsing,
cache-invalidation, and ``raise_on_error`` semantics intact; this module
holds the bodies that run INSIDE the writer thread — submitted by the
methods via ``db._submit_write(...)`` — as free functions taking the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``) and
the writer connection (``conn``).

Cross-calls into other ``HistoryDB`` surface (the FTS rebuild-failure
flag helper, cache invalidation, overflow handling, health check) go
through ``db.<method>(...)`` so class-level monkeypatches keep working.
Module constants referenced here (``_CLEAR_ALL_BATCH_SIZE``,
``_BatchableInsert``) are read through the ``history_db`` facade
namespace AT CALL TIME (lazy ``_hd.<NAME>`` reads), so tests that
monkeypatch them on the facade keep working.

Free functions:

- :func:`add_transcription` — fire-and-forget enqueue of a bounded
  ``_BatchableInsert`` payload (placeholder row-id contract), including
  the writer-liveness guard.
- :func:`submit_restore` — caller-side orchestration for
  ``HistoryDB.restore``: record parsing, writer submission, cache
  invalidation.
- :func:`submit_checkpoint` — caller-side orchestration for
  ``HistoryDB.checkpoint``: writer submission + error-to-``False``
  mapping.
- :func:`delete_row` — row DELETE + FTS5 ``'optimize'`` forensic purge
  (GDPR Art. 17).
- :func:`restore_row` — re-insert a previously-deleted record
  (insert-plaintext + flag-flip when a DEK is cached).
- :func:`clear_all_rows` — chunked DELETE + VACUUM + FTS5 ``'rebuild'``
  with failure escalation.
- :func:`toggle_favorite_row` — favorite flip.
- :func:`checkpoint_wal` — ``wal_checkpoint`` body on the writer
  connection.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)


def add_transcription(
    db: HistoryDB,
    text: str,
    duration: float = 0,
    model: str = "",
    device: str = "",
    language: str = "",
) -> int:
    """Add a transcription to the write queue (fire-and-forget).

    Body of :meth:`HistoryDB.add_transcription`. Returns the placeholder
    row id (1) on successful enqueue, or -1 when the writer cannot
    accept the work.
    """  # Lazy reads so the payload shape tracks monkeypatches on the
    # ``history_db`` module namespace.
    from voice_typer.server import history_db as _hd

    _BatchableInsert = _hd._BatchableInsert  # noqa: N806

    # early-return guard — if the writer thread never
    # started (init error) or died, return -1 immediately instead
    # of silently enqueuing to a dead writer's queue.
    if db._init_error is not None or not db._writer_thread.is_alive():
        log.error(
            "[HISTORY_DB] add_transcription refused — writer is unavailable: %s",
            db.health_check()["error"],
        )
        return -1

    try:
        word_count = len(text.split())
        char_count = len(text)
        if db._shutdown.is_set():
            log.debug("[HISTORY_DB] add_transcription submitted after shutdown — dropped.")
            return -1
        item = _BatchableInsert(
            text=text,
            duration=duration,
            model=model,
            device=device,
            word_count=word_count,
            char_count=char_count,
            language=language,
            future=None,  # fire-and-forget
        )
        # PERF-5: bounded queue (maxsize=_WRITE_QUEUE_MAXSIZE). Use
        # put_nowait + drop-oldest so a stalled writer doesn't block
        # the calling thread indefinitely. We can't reuse
        # _submit_write here because it enqueues (callable, future)
        # tuples — _BatchableInsert is its own queue item shape.
        try:
            db._queue.put_nowait(item)
        except queue.Full:
            db._drop_oldest_for_overflow(None)
            try:
                db._queue.put_nowait(item)
            except queue.Full:
                log.warning("[HISTORY_DB] Queue still full after drop-oldest — add_transcription dropped.")
                return -1
        # invalidate the today-stats cache at enqueue time.
        # Unlike ``_invalidate_history_count_cache`` (which skips
        # fire-and-forget ``add_transcription`` because a stale-by-1
        # TOTAL is fine), today's stats must reflect each new
        # dictation as soon as the Dashboard refreshes. The cache is
        # invalidated BEFORE the writer thread commits the INSERT —
        # there is a brief race window where a concurrent reader
        # could re-populate the cache with the pre-INSERT count, but
        # the 15s TTL bounds the staleness and the next
        # ``transcription_final`` refresh re-checks the cache.
        db._invalidate_today_stats_cache()
        # Placeholder row_id — callers that check ``> 0`` see success.
        return 1
    except Exception as e:
        log.exception("[HISTORY] Failed to enqueue add_transcription: %s", e)
        return -1


def delete_row(db: HistoryDB, conn: sqlite3.Connection, transcription_id: int) -> bool:
    """Delete one transcription row + purge its FTS5 segment data.

    Body of the inner ``_do_delete`` closure of
    :meth:`HistoryDB.delete`. After the row DELETE + commit, issue the
    FTS5 ``'optimize'`` command so the segment data in
    ``transcriptions_fts_data`` is purged of the deleted row's
    dictated text. The FTS5 AFTER DELETE trigger (schema.py:90-92)
    only marks the rowid as deleted in the delete-bitmap — the
    segment data (containing the dictated text) remains physically
    present and is recoverable via forensic tools until FTS5's
    background compaction happens to merge that segment (days or
    weeks later). For a user who dictates a password / medical note
    / financial data and then deletes that single transcription via
    the History UI, the text is NOT gone without this optimize — a
    direct GDPR Art. 17 violation.

    The per-delete command was downgraded from ``'rebuild'`` (O(N)
    — drops and rebuilds ALL segments from the content table) to
    ``'optimize'`` (runs the FTS5 optimizer until the index is
    optimal — typically 3-4x faster than ``'rebuild'`` on a
    multi-thousand-row DB because it only does the merge work
    needed to consolidate segments and apply the delete-bitmap).
    The user-visible MATCH-query correctness is already preserved
    by the AFTER DELETE trigger (the deleted rowid is immediately
    hidden from search results); the ``'optimize'`` call provides
    the forensic-recovery guarantee without paying the full O(N)
    cost on every single-row delete. The periodic retention tick
    (``retention.py``) still runs a full ``'rebuild'`` after bulk
    sweeps with >20% deletion ratio, providing the ultimate safety
    net.

    The optimize is wrapped in a tolerant ``try/except sqlite3.Error``
    (matching the retention.py / clear_all pattern) so a transient
    FTS5 error does not break the row delete (which already
    committed). The optimize is best-effort privacy hardening — if
    it fails, the row is still gone from the content table, only the
    FTS5 segment data lingers — bounded to "between launches" by the
    startup rebuild sweep.
    """
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute("DELETE FROM transcriptions WHERE id = ?", (transcription_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        if not deleted:
            return False
        # Purge the deleted row's dictated
        # text from ``transcriptions_fts_data`` (the FTS5
        # shadow segment table). The AFTER DELETE trigger
        # at schema.py:90-92 only marks the rowid as
        # deleted in the delete-bitmap — the segment data
        # survives until background compaction (days/weeks
        # later). ``'optimize'`` runs the FTS5 optimizer
        # until the index is optimal, which both
        # consolidates segments AND applies the
        # delete-bitmap to the merged output so the deleted
        # text is purged. ``'optimize'`` is preferred over
        # ``'rebuild'`` here because:
        #   - ``'rebuild'`` is O(N) (drops and rebuilds ALL
        #     segments from the content table).
        #   - ``'optimize'`` is typically 3-4x faster
        #     (only does the merge work needed to
        #     consolidate, not a full rebuild).
        # The MATCH-query correctness is already preserved
        # by the trigger; this call is purely for the
        # forensic-recovery guarantee. The periodic
        # retention tick (retention.py) still runs a full
        # ``'rebuild'`` after bulk sweeps as a safety net.
        # Tolerant: a transient FTS5 error must not break
        # the row delete (which already committed).
        try:
            cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('optimize')")
            conn.commit()
        except sqlite3.Error as optimize_exc:
            log.warning(
                "[HISTORY_DB] FTS5 'optimize' after delete(id=%d) "
                "FAILED: %s — dictated text may linger in "
                "transcriptions_fts_data until the next "
                "periodic retention sweep or the startup rebuild.",
                transcription_id,
                optimize_exc,
            )
            # Best-effort: increment the per-instance
            # failure counter so observability surfaces
            # chronic FTS5 optimize failures (mirrors the
            # retention.py / clear_all pattern).
            with contextlib.suppress(Exception):
                db._fts5_rebuild_failures = getattr(db, "_fts5_rebuild_failures", 0) + 1
            # Persist the fts5_rebuild_failed flag so the
            # next launch's startup rebuild runs (clearing
            # the lingering segment data). Best-effort: a
            # failure to persist is swallowed inside
            # _mark_fts5_rebuild_failed.
            with contextlib.suppress(Exception):
                db._mark_fts5_rebuild_failed(conn)
        return True


def restore_row(
    db: HistoryDB,
    conn: sqlite3.Connection,
    *,
    text: str,
    duration: float,
    model: str,
    device: str,
    language: str,
    word_count: int,
    char_count: int,
    favorite: int,
) -> int:
    """Re-insert a previously-deleted transcription record.

    Body of the inner ``_do_restore`` closure of
    :meth:`HistoryDB.restore`. At-rest encryption mirrors the
    add_transcription write path — the row is inserted with PLAINTEXT
    (so the AFTER-INSERT FTS trigger indexes it) and then flipped to
    ciphertext + ``text_is_encrypted=1`` in the same transaction when a
    DEK is cached; without a DEK the row stays plaintext (flag 0).

    Returns the new row id, or -1 on failure.
    """
    from voice_typer.server import _text_crypto

    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute(
            """
                    INSERT INTO transcriptions
                (text, duration, model, device, word_count, char_count, language, favorite)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (text, duration, model, device, word_count, char_count, language, favorite),
        )
        new_id = cursor.lastrowid
        if new_id is None:
            conn.commit()
            return -1
        dek = _text_crypto.get_dek_cached()
        if dek is not None:
            # Flag-flip UPDATE inside the same transaction: the
            # guarded AFTER-UPDATE FTS trigger skips the index,
            # keeping the plaintext tokens the INSERT just indexed.
            cursor.execute(
                "UPDATE transcriptions SET text = ?, text_is_encrypted = 1 WHERE id = ?",
                (_text_crypto.encrypt_text(text, dek), new_id),
            )
        conn.commit()
        log.info(
            "[HISTORY] Restored transcription as id=%d (%d chars)",
            new_id,
            char_count,
        )
        return new_id


def clear_all_rows(db: HistoryDB, conn: sqlite3.Connection) -> bool:
    """Clear every transcription row (chunked) + VACUUM + FTS5 rebuild.

    Body of the inner ``_do_clear_all`` closure of
    :meth:`HistoryDB.clear_all`.

    IMPL-A: chunked DELETE (commit per batch)
    running inside the writer thread. Chunking prevents the WAL
    from growing unboundedly during a huge clear and lets external
    readers see progress. The previous single-transaction DELETE
    held the write lock for the full scan.

    After the chunked DELETE completes, ``VACUUM`` runs
    in the writer thread to reclaim the freed pages so the DB file
    shrinks. Without this, ``clear_all`` leaves the file at its
    pre-clear size (SQLite keeps free pages for reuse) and the
    user's dictated text remains recoverable from the file via
    forensic tools even after a "clear all" — a privacy concern
    for the GDPR delete path.

    The other half: after VACUUM, the FTS5
    ``'rebuild'`` command is issued so the FTS5 shadow-table
    segment data (``transcriptions_fts_data``) is also rebuilt
    from the (now-empty) content table. ``VACUUM`` rebuilds the
    main DB file but does NOT rebuild FTS5 shadow tables; without
    this step, dictated text remained recoverable from
    ``transcriptions_fts_data`` via sqlite3 CLI or forensic tools
    — defeating GDPR Art. 17 right-to-erasure. The
    rebuild is wrapped in a tolerant ``try/except sqlite3.Error``
    matching the pattern in
    :func:`voice_typer.server.history_db_internals.retention.apply_retention`
    so an older DB (pre-V3 migration, no FTS table yet) doesn't
    crash the clear path. On failure the privacy
    guarantee is broken, so the failure is logged at ERROR,
    ``db._fts5_rebuild_failures`` is incremented, and an
    ``event_bus`` event ``{"type": "history_fts5_rebuild_failed"}``
    is published so the renderer can show a toast.
    """
    # Lazy read so the batch size tracks monkeypatches on the
    # ``history_db`` module namespace.
    from voice_typer.server import history_db as _hd

    batch_size = _hd._CLEAR_ALL_BATCH_SIZE  # noqa: N806

    with contextlib.closing(conn.cursor()) as cursor:
        while True:
            cursor.execute(
                "DELETE FROM transcriptions WHERE id IN (  SELECT id FROM transcriptions LIMIT ?)",
                (batch_size,),
            )
            batch_deleted = cursor.rowcount
            if batch_deleted == 0:
                break
            conn.commit()  # release write lock between batches
    # Final commit to close any open transaction started
    # by the last DELETE (which matched 0 rows but still
    # auto-opened a transaction in Python's sqlite3 module).
    # VACUUM requires no open transaction.
    conn.commit()
    # VACUUM reclaims the freed pages so the DB
    # file shrinks and deleted text is not recoverable
    # from free pages. Runs inside the writer thread so
    # it serializes with other writes. VACUUM requires
    # exclusive access — readers will block briefly.
    try:
        conn.execute("VACUUM")
        log.info("[HISTORY_DB] VACUUM completed after clear_all")
    except sqlite3.Error as e:
        # VACUUM failure is non-fatal — the rows are
        # already deleted; only space reclamation failed.
        log.warning("[HISTORY_DB] VACUUM after clear_all failed: %s", e)
    # rebuild FTS5 segments from the (now-empty)
    # content table. The DELETE trigger
    # ``transcriptions_ad_fts`` only marks rowids as
    # deleted in the FTS5 delete-bitmap; the segment data
    # in ``transcriptions_fts_data`` survives both the
    # trigger delete and ``VACUUM``. Without this rebuild,
    # dictated text remained recoverable from
    # ``transcriptions_fts_data`` via forensic tools —
    # defeating GDPR Art. 17. Wrapped in a
    # tolerant try/except so an older DB (pre-V3
    # migration, no FTS table yet) doesn't crash the
    # clear path. The pattern matches the one in
    # ``retention.apply_retention`` (mirrors this
    # in ``delete()``).
    try:
        fts_cursor = conn.cursor()
        try:
            fts_cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")
            conn.commit()
            log.info("[HISTORY_DB] FTS5 segments rebuilt after clear_all")
        finally:
            fts_cursor.close()
    except sqlite3.Error as e:
        # escalate from WARNING to ERROR — the
        # GDPR Art. 17 privacy guarantee is
        # broken (deleted dictated text remains
        # recoverable from ``transcriptions_fts_data``
        # via forensic tools), not merely "suboptimal".
        log.exception(
            "[HISTORY_DB] FTS5 'rebuild' after clear_all FAILED: %s "
            "(FTS5 shadow-table segment data may persist — deleted "
            "dictated text remains recoverable; manual re-index advised)",
            e,
        )
        # observable metric — increment the
        # per-instance failure counter so diagnostics
        # handlers can surface it to the user.
        try:
            db._fts5_rebuild_failures = db._fts5_rebuild_failures + 1
        except Exception:  # noqa: BLE001 — best-effort metric
            log.debug(
                "[HISTORY_DB] could not increment _fts5_rebuild_failures counter",
                exc_info=True,
            )
        # Persist the fts5_rebuild_failed flag so the
        # next launch's startup rebuild runs (clearing the
        # lingering segment data). Best-effort: a failure
        # to persist is swallowed inside
        # _mark_fts5_rebuild_failed.
        with contextlib.suppress(Exception):
            db._mark_fts5_rebuild_failed(conn)
        # best-effort event_bus publication so
        # the renderer can show a toast. Wrapped broadly
        # because the event_bus import or the publish
        # call may fail (e.g. circular import during
        # early init); none of those should crash the
        # clear path which has already done the chunked
        # DELETEs + VACUUM.
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "history_fts5_rebuild_failed",
                    "data": {
                        "db_path": str(db.db_path),
                        "deleted": 0,  # clear_all doesn't track count
                        "error": str(e),
                        "source": "clear_all",
                    },
                }
            )
        except Exception as publish_exc:  # noqa: BLE001
            log.warning(
                "[HISTORY_DB] event_bus.publish(history_fts5_rebuild_failed) "
                "failed (best-effort, clear_all continues): %s",
                publish_exc,
            )
    log.info("[HISTORY] Cleared all transcriptions")
    return True


def toggle_favorite_row(db: HistoryDB, conn: sqlite3.Connection, transcription_id: int) -> bool:
    """Toggle the favorite status of one transcription row.

    Body of the inner ``_do_toggle`` closure of
    :meth:`HistoryDB.toggle_favorite`.
    """
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute(
            "UPDATE transcriptions SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (transcription_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def submit_delete(db: HistoryDB, transcription_id: int) -> bool:
    """Submit a delete request; invalidate caches on success.

    Caller-side orchestration of :meth:`HistoryDB.delete` (which keeps
    its ``_wrap_write`` decorator + ``raise_on_error`` semantics). The
    row-level body runs on the writer thread via :func:`delete_row`.
    Returns ``False`` when the writer is shut down or the row didn't
    exist; invalidates both caches on success.
    """
    result = db._submit_write(lambda conn: delete_row(db, conn, transcription_id), wait=True)
    if result is None:
        return False
    if result:
        db._invalidate_history_count_cache()
        db._invalidate_today_stats_cache()
    return bool(result)


def submit_clear_all(db: HistoryDB) -> bool:
    """Submit a clear-all request; invalidate caches on success.

    Caller-side orchestration of :meth:`HistoryDB.clear_all` (which
    keeps its ``_wrap_write`` decorator + ``raise_on_error``
    semantics). The row-level body (chunked DELETE + VACUUM + FTS5
    ``'rebuild'``) runs on the writer thread via :func:`clear_all_rows`.
    """
    result = db._submit_write(lambda conn: clear_all_rows(db, conn), wait=True)
    if result is None:
        return False
    if result:
        db._invalidate_history_count_cache()
        db._invalidate_today_stats_cache()
    return bool(result)


def submit_toggle_favorite(db: HistoryDB, transcription_id: int) -> bool:
    """Submit a favorite-flip request.

    Caller-side orchestration of :meth:`HistoryDB.toggle_favorite`
    (which keeps its ``_wrap_write`` decorator + ``raise_on_error``
    semantics). The row-level body runs on the writer thread via
    :func:`toggle_favorite_row`.
    """
    result = db._submit_write(lambda conn: toggle_favorite_row(db, conn, transcription_id), wait=True)
    if result is None:
        return False
    return bool(result)


def checkpoint_wal(db: HistoryDB, conn: sqlite3.Connection, truncate: bool) -> bool:
    """Run ``wal_checkpoint(TRUNCATE|RESTART)`` on the writer connection.

    Body of the inner ``_do_checkpoint`` closure of
    :meth:`HistoryDB.checkpoint`. Returns ``True`` when the checkpoint
    completed without error; a ``sqlite3.Error`` is logged at WARNING and
    reported as ``False``.
    """
    mode = "TRUNCATE" if truncate else "RESTART"
    try:
        # wal_checkpoint returns (busy, log, checkpointed)
        # where busy=0 means no writer was active. We don't
        # retry on busy=1 because the writer thread IS the
        # only writer; a busy result here means an external
        # process holds the lock, which the caller can't
        # resolve by retrying.
        result = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        if result is not None:
            log.debug(
                "[HISTORY_DB] wal_checkpoint(%s): busy=%s, log=%s, checkpointed=%s",
                mode,
                result[0] if len(result) > 0 else "?",
                result[1] if len(result) > 1 else "?",
                result[2] if len(result) > 2 else "?",
            )
        return True
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] wal_checkpoint(%s) failed: %s",
            mode,
            e,
        )
        return False


def submit_restore(db: HistoryDB, record: dict) -> int:
    """Parse + submit a restore request; invalidate caches on success.

    Caller-side orchestration of :meth:`HistoryDB.restore` (which keeps
    its ``_wrap_write`` decorator + ``raise_on_error`` semantics).
    ``record`` is the dict shape returned by ``get_recent`` (id is
    ignored — a new row with a new id is inserted). Parsing stays on
    the CALLER thread so a malformed record raises through the same
    error path as before the split; the row-level body runs on the
    writer thread via :func:`restore_row`.

    At-rest encryption: mirrors the add_transcription write path — the
    row is inserted with PLAINTEXT (so the AFTER-INSERT FTS trigger
    indexes it) and then flipped to ciphertext + ``text_is_encrypted=1``
    in the same transaction when a DEK is cached; without a DEK the row
    stays plaintext (flag 0).

    Returns the new row id, or -1 on failure.
    """
    text = str(record.get("text", ""))
    duration = float(record.get("duration", 0) or 0)
    model = str(record.get("model", "") or "")
    device = str(record.get("device", "") or "")
    language = str(record.get("language", "") or "")
    word_count = int(record.get("word_count", 0) or len(text.split()))
    char_count = int(record.get("char_count", 0) or len(text))
    favorite = 1 if record.get("favorite") else 0

    result = db._submit_write(
        lambda conn: restore_row(
            db,
            conn,
            text=text,
            duration=duration,
            model=model,
            device=device,
            language=language,
            word_count=word_count,
            char_count=char_count,
            favorite=favorite,
        ),
        wait=True,
    )
    if result is None:
        return -1
    if result and result > 0:
        # invalidate the count cache.
        db._invalidate_history_count_cache()
        # invalidate the today-stats cache (a restore
        # adds a new row whose timestamp is ``now``, which
        # affects today's count/chars/words/duration).
        db._invalidate_today_stats_cache()
    return int(result)


def submit_checkpoint(db: HistoryDB, truncate: bool) -> bool:
    """Submit a ``wal_checkpoint`` closure; map failures to ``False``.

    Caller-side orchestration of :meth:`HistoryDB.checkpoint` (used by
    GDPR delete/export paths). ``truncate=True`` additionally truncates
    the WAL to zero size. Returns ``True`` when the checkpoint completed
    without error, ``False`` otherwise (writer unavailable, checkpoint
    failed) — the caller should treat ``False`` as "WAL may still
    contain data; do not unlink until next attempt".
    """
    from voice_typer.server import history_db as _hd

    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    try:
        result = db._submit_write(lambda conn: checkpoint_wal(db, conn, truncate), wait=True)
        if result is None:
            # Writer shut down — can't checkpoint.
            return False
        return bool(result)
    except HistoryDBError as e:
        log.exception("[HISTORY] Writer unavailable for checkpoint: %s", e)
        return False
    except Exception as e:
        log.exception("[HISTORY] Failed to checkpoint: %s", e)
        return False
