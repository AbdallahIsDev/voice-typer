"""At-rest-encryption lifecycle for transcription history."""

from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

#: Guards the ONE-SHOT per-DB-path INFO logs in ``_init_encryption``
_initialized_db_paths: set[str] = set()

#: Plain-language meanings for each at-rest-encryption status, appended
_ENCRYPTION_STATUS_MEANINGS: dict[str, str] = {
    "active": "new history rows are encrypted",
    "key-unavailable": "encrypted rows exist but no key is available;"
    " reads show a placeholder, new writes stay plaintext",
    "disabled": "no key and no encrypted rows; history is stored as plaintext",
}


def _reset_encryption_initialized_paths() -> None:
    """Test seam, clear the per-path log-guard set."""
    _initialized_db_paths.clear()


def _init_encryption(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """Resolve the DEK once per process and kick the backfill."""
    # Lazy import so the batch size tracks monkeypatches on the
    from voice_typer.server import _text_crypto, history_db as _hd

    batch_size = _hd._ENCRYPTION_BACKFILL_BATCH  # noqa: N806

    # Whether this path's init already ran in this process. The
    key = str(db.db_path)
    is_first_init = key not in _initialized_db_paths
    if is_first_init:
        _initialized_db_paths.add(key)

    try:
        has_encrypted = db._has_encrypted_rows(conn)
        dek = _text_crypto.resolve_dek(has_encrypted)
        db._encryption_status = _text_crypto.encryption_status(dek, has_encrypted)
        if is_first_init:
            log.info(
                "[HISTORY] at-rest encryption status: %s (%s)",
                db._encryption_status,
                _ENCRYPTION_STATUS_MEANINGS.get(db._encryption_status, "unrecognized status"),
            )
        else:
            # Idempotent re-entry stays silent: the repeat line doubled
            pass
        if dek is not None and db._has_plaintext_rows(conn):
            # Legacy rows exist alongside the active key, encrypt
            if is_first_init:
                log.info(
                    "[HISTORY] scheduling background encryption of existing plaintext history rows (batches of %d)",
                    batch_size,
                )
            db._enqueue_backfill_step()
        if dek is not None and db._fts5_rebuild_ran and db._has_encrypted_rows(conn):
            # The startup FTS5 'rebuild' re-tokenized the content
            if is_first_init:
                log.info(
                    "[HISTORY] startup FTS5 rebuild re-tokenized encrypted rows "
                    "with ciphertext, scheduling decrypt-aware re-index"
                )
            db._enqueue_reindex_step()
    except Exception as e:  # noqa: BLE001, crypto must never kill the writer
        log.warning(
            "[HISTORY] at-rest-encryption initialization failed (%s), history continues in plaintext mode",
            type(e).__name__,
        )


def encryption_status(db: HistoryDB) -> str:
    """Return the at-rest-encryption state of this HistoryDB."""
    return db._encryption_status


def _has_encrypted_rows(db: HistoryDB, conn: sqlite3.Connection) -> bool:
    """Return True when at least one row is flagged encrypted."""
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute("SELECT 1 FROM transcriptions WHERE text_is_encrypted = 1 LIMIT 1")
        return cursor.fetchone() is not None


def _has_plaintext_rows(db: HistoryDB, conn: sqlite3.Connection) -> bool:
    """Return True when at least one non-empty row is still plaintext."""
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute("SELECT 1 FROM transcriptions WHERE text_is_encrypted = 0 AND text <> '' LIMIT 1")
        return cursor.fetchone() is not None


def _enqueue_backfill_step(db: HistoryDB) -> None:
    """Queue one bounded plaintext→ciphertext backfill batch (fire-and-forget)."""
    with contextlib.suppress(queue.Full):
        db._queue.put_nowait((db._encrypt_backfill_step, None))


def _encrypt_backfill_step(db: HistoryDB, conn: sqlite3.Connection) -> int:
    """Encrypt up to ``_ENCRYPTION_BACKFILL_BATCH`` plaintext rows.

    Returns the number of rows encrypted in this step.
    """
    from voice_typer.server import _text_crypto, history_db as _hd

    batch_size = _hd._ENCRYPTION_BACKFILL_BATCH  # noqa: N806

    dek = _text_crypto.get_dek_cached()
    if dek is None:
        return 0
    try:
        with contextlib.closing(conn.cursor()) as cursor:
            rows = cursor.execute(
                "SELECT id, text FROM transcriptions "
                "WHERE text_is_encrypted = 0 AND text <> '' "
                "ORDER BY id ASC LIMIT ?",
                (batch_size,),
            ).fetchall()
            for row_id, text in rows:
                cipher = _text_crypto.encrypt_text(text, dek)
                # The ``AND text_is_encrypted = 0`` guard makes the
                cursor.execute(
                    "UPDATE transcriptions SET text = ?, text_is_encrypted = 1 WHERE id = ? AND text_is_encrypted = 0",
                    (cipher, row_id),
                )
            conn.commit()
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] history-encryption backfill batch failed (%s), will resume on next launch",
            e,
        )
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        return 0
    encrypted = len(rows)
    if encrypted > 0:
        log.debug(
            "[HISTORY] encrypted %d existing history row(s) at rest",
            encrypted,
        )
    if encrypted >= batch_size:
        # More plaintext rows remain, yield to foreground writes
        db._enqueue_backfill_step()
    return encrypted


def _enqueue_reindex_step(db: HistoryDB) -> None:
    """Queue one bounded decrypt-aware FTS re-index batch (fire-and-forget)."""
    with contextlib.suppress(queue.Full):
        db._queue.put_nowait((db._reindex_encrypted_fts_step, None))


def _reindex_encrypted_fts_step(db: HistoryDB, conn: sqlite3.Connection) -> int:
    """Restore plaintext FTS tokens for encrypted rows after a 'rebuild'.

    Returns the number of rows re-indexed in this step.
    """
    from voice_typer.server import _text_crypto, history_db as _hd

    batch_size = _hd._ENCRYPTION_BACKFILL_BATCH  # noqa: N806

    dek = _text_crypto.get_dek_cached()
    if dek is None:
        return 0
    watermark = db._fts_reindex_watermark
    try:
        with contextlib.closing(conn.cursor()) as cursor:
            rows = cursor.execute(
                "SELECT id, text FROM transcriptions WHERE text_is_encrypted = 1 AND id > ? ORDER BY id ASC LIMIT ?",
                (watermark, batch_size),
            ).fetchall()
            for row_id, ciphertext in rows:
                # 'delete' with the ciphertext that the rebuild
                from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

                plaintext = _text_crypto.decrypt_text(ciphertext, dek)
                cursor.execute(
                    "INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', ?, ?)",
                    (row_id, ciphertext),
                )
                cursor.execute(
                    "INSERT INTO transcriptions_fts(rowid, text) VALUES (?, ?)",
                    (row_id, plaintext),
                )
                if cjk_trigram_table_exists(conn):
                    cursor.execute(
                        "INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk, rowid, text)"
                        " VALUES ('delete', ?, ?)",
                        (row_id, ciphertext),
                    )
                    cursor.execute(
                        "INSERT INTO transcriptions_fts_cjk(rowid, text) VALUES (?, ?)",
                        (row_id, plaintext),
                    )
                watermark = row_id
            conn.commit()
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] decrypt-aware FTS re-index batch failed (%s), "
            "encrypted rows may stay unsearchable until the next rebuild",
            e,
        )
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        return 0
    db._fts_reindex_watermark = watermark
    if len(rows) >= batch_size:
        db._enqueue_reindex_step()
    elif rows:
        log.debug(
            "[HISTORY] decrypt-aware FTS re-index complete through id=%d",
            watermark,
        )
    return len(rows)


def _mark_fts5_rebuild_failed(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """Persist the ``fts5_rebuild_failed`` flag so the next launch"""
    try:
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '1')")
        conn.commit()
    except sqlite3.Error as e:
        log.debug(
            "[HISTORY_DB] Could not persist fts5_rebuild_failed flag to schema_meta: %s "
            "(in-memory counter still incremented; next launch may skip the startup rebuild)",
            e,
        )
