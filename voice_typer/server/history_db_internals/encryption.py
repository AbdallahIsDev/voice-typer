"""At-rest-encryption lifecycle for transcription history.

Extracted from the once-monolithic ``history_db.py``. The functions in
this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes (the
resolved encryption status, the decrypt-aware FTS re-index watermark,
the write queue) via the passed-in reference. Cross-calls into other
``HistoryDB`` surface go through ``db.<method>(...)`` so class-level
monkeypatches keep working. The public ``HistoryDB`` class keeps thin
delegating methods (``_init_encryption``, ``encryption_status``,
``_has_encrypted_rows``, ``_has_plaintext_rows``,
``_enqueue_backfill_step``, ``_encrypt_backfill_step``,
``_enqueue_reindex_step``, ``_reindex_encrypted_fts_step``,
``_mark_fts5_rebuild_failed``).

Module-level constants referenced here (``_ENCRYPTION_BACKFILL_BATCH``)
stay defined on the ``history_db`` facade and are read through its
namespace AT CALL TIME (lazy ``_hd.<CONST>`` reads), so tests that
monkeypatch ``history_db._ENCRYPTION_BACKFILL_BATCH`` keep working.

Free functions:

- :func:`_init_encryption` — resolve the DEK once per process (writer
  thread, before readiness is signaled) and kick the plaintext→
  ciphertext backfill / decrypt-aware re-index.
- :func:`encryption_status` — report the resolved at-rest-encryption
  state (``"active"`` / ``"disabled"`` / ``"key-unavailable"``).
- :func:`_has_encrypted_rows` / :func:`_has_plaintext_rows` — row-flag
  probes used by :func:`_init_encryption`.
- :func:`_enqueue_backfill_step` / :func:`_encrypt_backfill_step` —
  bounded, idempotent, resumable background encryption of legacy
  plaintext rows.
- :func:`_enqueue_reindex_step` / :func:`_reindex_encrypted_fts_step` —
  bounded decrypt-aware repair of the FTS index after a startup
  ``'rebuild'`` re-tokenized ciphertext.
- :func:`_mark_fts5_rebuild_failed` — persist the
  ``fts5_rebuild_failed`` schema_meta flag so the next launch's startup
  rebuild retries (also called by the delete/clear_all failure paths).
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

#: Guards the ONE-SHOT per-DB-path INFO logs in ``_init_encryption``
#: (the status line + the backfill schedule line).  ``_init_encryption``
#: runs on every ``HistoryDB`` construction — the lazy ``app.history_db``
#: property creates one, and corruption recovery / GDPR-delete re-creation
#: spin up a writer thread that calls it again.  Without this guard the
#: status + backfill-schedule lines repeat within milliseconds (and the
#: backfill step was queued TWICE, producing duplicate "Fire-and-forget
#: write failed" errors when the batch encryption failed).  Mirrors the
#: ``_announced_db_paths`` dedup in ``history_db_internals.schema``.
#:
#: Unlike the schema init which is fully idempotent, encryption state
#: CAN change between constructions (DEK cache reset, keyring toggled),
#: so ``_init_encryption`` still runs the full logic every time — this
#: set only suppresses the INFO-log surplus and the duplicate backfill
#: enqueue.  Tests that need a fresh encryption session for the same
#: path can call :func:`_reset_encryption_initialized_paths`.
_initialized_db_paths: set[str] = set()


def _reset_encryption_initialized_paths() -> None:
    """Test seam — clear the per-path log-guard set.

    Called by tests between HistoryDB instances that share a db_path
    but expect to see the encryption-init logs fire again (or to
    trigger a second backfill from ``_init_encryption``).
    """
    _initialized_db_paths.clear()


def _init_encryption(db: HistoryDB, conn: sqlite3.Connection) -> None:
    """Resolve the DEK once per process and kick the backfill.

    Runs on the writer thread BEFORE ``_writer_ready`` is signaled
    (see ``history_db_internals.writer._writer_loop``), so the
    encryption state is deterministic the moment ``HistoryDB()``
    returns — no reader can observe a flagged row while the key is
    still unresolved. The one keyring read is bounded by the
    existing 5s keyring-I/O timeout isolation; the BACKFILL itself
    is a queued writer item and never blocks startup. Never raises
    (failures are logged; the DB falls back to the documented
    plaintext behavior).

    Key-loss policy (stricter than ADR §9, per review): a DEK is
    generated only when the keyring is available AND no encrypted
    rows exist. When encrypted rows exist but the DEK cannot be
    loaded, the status becomes ``"key-unavailable"`` — reads return
    the ``"<decryption failed>"`` placeholder, NEW writes stay
    plaintext (flag 0), and the DEK is NEVER regenerated (a fresh
    key could not decrypt the existing rows).
    """
    # Lazy import so the batch size tracks monkeypatches on the
    # ``history_db`` module namespace (e.g. _ENCRYPTION_BACKFILL_BATCH).
    from voice_typer.server import _text_crypto, history_db as _hd

    batch_size = _hd._ENCRYPTION_BACKFILL_BATCH  # noqa: N806

    # Whether this path's init already ran in this process. The
    # ENCRYPTION RESOLUTION still runs every time (the DEK / keyring
    # state can legitimately change between HistoryDB constructions —
    # e.g. keyring wiped after encrypted rows exist → the second
    # instance must report "key-unavailable"), but the INFO logs and
    # the backfill/reindex ENQUEUE are one-shot per path so a second
    # construction for the same DB does not produce duplicate log lines
    # (and a duplicate backfill step, which surfaced as repeated
    # "Fire-and-forget write failed" errors when the batch failed).
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
                "[HISTORY] at-rest encryption status: %s",
                db._encryption_status,
            )
        else:
            log.debug(
                "[HISTORY] at-rest encryption status: %s (already initialized, repeat)",
                db._encryption_status,
            )
        if dek is not None and db._has_plaintext_rows(conn):
            # Legacy rows exist alongside the active key — encrypt
            # them in bounded background batches (never blocks
            # startup: the step is a queued writer item that
            # re-enqueues itself between batches). The ENQUEUE runs on
            # EVERY construction (a later instance may be the one with
            # a working keyring / DEK — e.g. "cleartext DB opened with
            # keyring later backfills"); only the INFO log is one-shot.
            if is_first_init:
                log.info(
                    "[HISTORY] scheduling background encryption of existing plaintext history rows (batches of %d)",
                    batch_size,
                )
            db._enqueue_backfill_step()
        if dek is not None and db._fts5_rebuild_ran and db._has_encrypted_rows(conn):
            # The startup FTS5 'rebuild' re-tokenized the content
            # table, so encrypted rows now carry CIPHERTEXT tokens in
            # the index. Restore the §6 invariant (FTS stays
            # plaintext-tokenized) with a decrypt-aware re-index.
            if is_first_init:
                log.info(
                    "[HISTORY] startup FTS5 rebuild re-tokenized encrypted rows "
                    "with ciphertext — scheduling decrypt-aware re-index"
                )
            db._enqueue_reindex_step()
    except Exception as e:  # noqa: BLE001 — crypto must never kill the writer
        log.warning(
            "[HISTORY] at-rest-encryption initialization failed (%s) — history continues in plaintext mode",
            type(e).__name__,
        )


def encryption_status(db: HistoryDB) -> str:
    """Return the at-rest-encryption state of this HistoryDB.

    One of:

    - ``"active"`` — a DEK is available; new rows are encrypted and
      flagged rows decrypt transparently on read.
    - ``"disabled"`` — no DEK and nothing encrypted (keyring
      unavailable on first run): behavior is byte-identical to the
      pre-encryption plaintext mode (zero-regression guarantee).
    - ``"key-unavailable"`` — encrypted rows exist but the DEK
      cannot be loaded (keyring wiped/unavailable): reads return
      the ``"<decryption failed>"`` placeholder, new writes stay
      plaintext, and the DEK is never regenerated in this state.
    """
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
    """Queue one bounded plaintext→ciphertext backfill batch (fire-and-forget).

    Enqueued from the writer thread itself (init + the tail of each
    step), so the batches serialize with normal writes in FIFO
    order — a batch can never race a live INSERT. Queue-full is
    swallowed: the remaining rows simply stay plaintext until the
    next launch (the backfill is idempotent — it selects by flag).
    """
    with contextlib.suppress(queue.Full):
        db._queue.put_nowait((db._encrypt_backfill_step, None))


def _encrypt_backfill_step(db: HistoryDB, conn: sqlite3.Connection) -> int:
    """Encrypt up to ``_ENCRYPTION_BACKFILL_BATCH`` plaintext rows.

    Idempotent (rows are selected by ``text_is_encrypted = 0``) and
    resumable: when a full batch is processed, the next batch is
    re-enqueued so the writer thread yields to foreground writes
    between batches. The UPDATE is the flag-flip form guarded in the
    ``au_fts`` trigger, so the FTS index keeps the plaintext tokens
    these rows were originally indexed with — search stays correct
    before, during, and after the backfill.

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
                # UPDATE idempotent even if a stale duplicate step
                # is queued twice.
                cursor.execute(
                    "UPDATE transcriptions SET text = ?, text_is_encrypted = 1 WHERE id = ? AND text_is_encrypted = 0",
                    (cipher, row_id),
                )
            conn.commit()
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] history-encryption backfill batch failed (%s) — will resume on next launch",
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
        # More plaintext rows remain — yield to foreground writes
        # and continue in the next queued step.
        db._enqueue_backfill_step()
    return encrypted


def _enqueue_reindex_step(db: HistoryDB) -> None:
    """Queue one bounded decrypt-aware FTS re-index batch (fire-and-forget)."""
    with contextlib.suppress(queue.Full):
        db._queue.put_nowait((db._reindex_encrypted_fts_step, None))


def _reindex_encrypted_fts_step(db: HistoryDB, conn: sqlite3.Connection) -> int:
    """Restore plaintext FTS tokens for encrypted rows after a 'rebuild'.

    The FTS5 ``'rebuild'`` command drops all segments and re-tokenizes
    from the CONTENT table — for a row whose ``text`` column holds
    ciphertext that means the index now contains ciphertext tokens,
    so full-text search no longer matches the row's real words. This
    step repairs the invariant (ADR §6: FTS shadow tables stay
    plaintext-tokenized) for up to ``_ENCRYPTION_BACKFILL_BATCH``
    encrypted rows per invocation:

    1. issue the FTS5 ``'delete'`` command with the row's CIPHERTEXT
       (exactly what the rebuild indexed — a token match, so the
       delete is safe), then
    2. re-INSERT the DECRYPTED plaintext so the row is searchable
       again.

    Progression uses an ascending-id watermark (rows never lose the
    encrypted flag mid-run), so each batch resumes where the
    previous one stopped and the step terminates when a short batch
    is seen — no repeated work, no unbounded memory. Only runs when
    a DEK is cached (in key-loss mode there is no plaintext to
    index — search over those rows is already degraded by design).
    Re-enqueues itself while full batches remain, so it never
    starves foreground writes.

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
                # indexed (token match — removes exactly those
                # tokens), then insert the decrypted plaintext.
                cursor.execute(
                    "INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', ?, ?)",
                    (row_id, ciphertext),
                )
                cursor.execute(
                    "INSERT INTO transcriptions_fts(rowid, text) VALUES (?, ?)",
                    (row_id, _text_crypto.decrypt_text(ciphertext, dek)),
                )
                watermark = row_id
            conn.commit()
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] decrypt-aware FTS re-index batch failed (%s) — "
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
    """Persist the ``fts5_rebuild_failed`` flag so the next launch
    retries the FTS5 startup rebuild.

    Called from the tolerant ``except sqlite3.Error`` branches in
    ``delete`` (after a failed per-row ``'optimize'``) and
    ``clear_all`` (after a failed ``'rebuild'``). The retention
    path (``retention.py``) sets the same flag via the same
    schema_meta key — paired change in that module.

    Best-effort: a failure to persist the flag (e.g. disk full)
    is swallowed at DEBUG — the in-memory
    ``db._fts5_rebuild_failures`` counter is still incremented
    by the caller, so the failure is observable via diagnostics
    even if the persisted flag isn't updated.
    """
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
