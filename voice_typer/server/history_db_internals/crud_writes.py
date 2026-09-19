"""CRUD write closures for transcription rows (add/delete/restore/clear/toggle)."""

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
    """Add a transcription to the write queue (fire-and-forget)."""
    # ``history_db`` module namespace.
    from voice_typer.server import history_db as _hd

    _BatchableInsert = _hd._BatchableInsert  # noqa: N806

    # early-return guard, if the writer thread never
    if db._init_error is not None or not db._writer_thread.is_alive():
        log.error(
            "[HISTORY_DB] add_transcription refused, writer is unavailable: %s",
            db.health_check()["error"],
        )
        return -1

    try:
        word_count = len(text.split())
        char_count = len(text)
        if db._shutdown.is_set():
            log.debug("[HISTORY_DB] add_transcription submitted after shutdown, dropped.")
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
        try:
            db._queue.put_nowait(item)
        except queue.Full:
            db._drop_oldest_for_overflow(None)
            try:
                db._queue.put_nowait(item)
            except queue.Full:
                log.warning("[HISTORY_DB] Queue still full after drop-oldest, add_transcription dropped.")
                return -1
        # invalidate the today-stats cache at enqueue time.
        db._invalidate_today_stats_cache()
        # Placeholder row_id, callers that check ``> 0`` see success.
        return 1
    except Exception as e:
        log.exception("[HISTORY] Failed to enqueue add_transcription: %s", e)
        return -1


def delete_row(db: HistoryDB, conn: sqlite3.Connection, transcription_id: int) -> bool:
    """Delete one transcription row + purge its FTS5 segment data."""
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute("DELETE FROM transcriptions WHERE id = ?", (transcription_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        if not deleted:
            return False
        # Purge the deleted row's dictated
        try:
            # Both FTS5 shadow indexes (unicode61 + trigram CJK) in
            from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

            cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('optimize')")
            if cjk_trigram_table_exists(conn):
                cursor.execute("INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk) VALUES('optimize')")
            conn.commit()
        except sqlite3.Error as optimize_exc:
            log.warning(
                "[HISTORY_DB] FTS5 'optimize' after delete(id=%d) "
                "FAILED: %s, dictated text may linger in "
                "transcriptions_fts_data until the next "
                "periodic retention sweep or the startup rebuild.",
                transcription_id,
                optimize_exc,
            )
            # Best-effort: increment the per-instance
            with contextlib.suppress(Exception):
                db._fts5_rebuild_failures = getattr(db, "_fts5_rebuild_failures", 0) + 1
            # Persist the fts5_rebuild_failed flag so the
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
    """Re-insert a previously-deleted transcription record."""
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
    """Clear every transcription row (chunked) + VACUUM + FTS5 rebuild."""
    # Lazy read so the batch size tracks monkeypatches on the
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
    conn.commit()
    # VACUUM reclaims the freed pages so the DB
    try:
        conn.execute("VACUUM")
        log.info("[HISTORY_DB] VACUUM completed after clear_all")
    except sqlite3.Error as e:
        # VACUUM failure is non-fatal, the rows are
        log.warning("[HISTORY_DB] VACUUM after clear_all failed: %s", e)
    # rebuild FTS5 segments from the (now-empty)
    try:
        fts_cursor = conn.cursor()
        try:
            # Both FTS5 shadow indexes (unicode61 + trigram CJK) in
            from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

            fts_cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")
            if cjk_trigram_table_exists(conn):
                fts_cursor.execute("INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk) VALUES('rebuild')")
            conn.commit()
            log.info("[HISTORY_DB] FTS5 segments rebuilt after clear_all")
        finally:
            fts_cursor.close()
    except sqlite3.Error as e:
        # escalate from WARNING to ERROR, the
        log.exception(
            "[HISTORY_DB] FTS5 'rebuild' after clear_all FAILED: %s "
            "(FTS5 shadow-table segment data may persist, deleted "
            "dictated text remains recoverable; manual re-index advised)",
            e,
        )
        # observable metric, increment the
        try:
            db._fts5_rebuild_failures = db._fts5_rebuild_failures + 1
        except Exception:  # noqa: BLE001, best-effort metric
            log.debug(
                "[HISTORY_DB] could not increment _fts5_rebuild_failures counter",
                exc_info=True,
            )
        # Persist the fts5_rebuild_failed flag so the
        with contextlib.suppress(Exception):
            db._mark_fts5_rebuild_failed(conn)
        # best-effort event_bus publication so
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
    """Toggle the favorite status of one transcription row."""
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute(
            "UPDATE transcriptions SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (transcription_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def submit_delete(db: HistoryDB, transcription_id: int) -> bool:
    """Submit a delete request; invalidate caches on success."""
    result = db._submit_write(lambda conn: delete_row(db, conn, transcription_id), wait=True)
    if result is None:
        return False
    if result:
        db._invalidate_history_count_cache()
        db._invalidate_today_stats_cache()
    return bool(result)


def submit_clear_all(db: HistoryDB) -> bool:
    """Submit a clear-all request; invalidate caches on success."""
    result = db._submit_write(lambda conn: clear_all_rows(db, conn), wait=True)
    if result is None:
        return False
    if result:
        db._invalidate_history_count_cache()
        db._invalidate_today_stats_cache()
    return bool(result)


def submit_toggle_favorite(db: HistoryDB, transcription_id: int) -> bool:
    """Submit a favorite-flip request."""
    result = db._submit_write(lambda conn: toggle_favorite_row(db, conn, transcription_id), wait=True)
    if result is None:
        return False
    return bool(result)


def checkpoint_wal(db: HistoryDB, conn: sqlite3.Connection, truncate: bool) -> bool:
    """Run ``wal_checkpoint(TRUNCATE|RESTART)`` on the writer connection."""
    mode = "TRUNCATE" if truncate else "RESTART"
    try:
        # wal_checkpoint returns (busy, log, checkpointed)
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
    """Parse + submit a restore request; invalidate caches on success."""
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
        db._invalidate_today_stats_cache()
    return int(result)


def submit_checkpoint(db: HistoryDB, truncate: bool) -> bool:
    """Submit a ``wal_checkpoint`` closure; map failures to ``False``."""
    from voice_typer.server import history_db as _hd

    HistoryDBError = _hd.HistoryDBError  # noqa: N806

    try:
        result = db._submit_write(lambda conn: checkpoint_wal(db, conn, truncate), wait=True)
        if result is None:
            # Writer shut down, can't checkpoint.
            return False
        return bool(result)
    except HistoryDBError as e:
        log.exception("[HISTORY] Writer unavailable for checkpoint: %s", e)
        return False
    except Exception as e:
        log.exception("[HISTORY] Failed to checkpoint: %s", e)
        return False
