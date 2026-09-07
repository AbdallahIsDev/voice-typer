"""Retention sweep + periodic scheduling helpers.

Extracted from the once-monolithic ``history_db.py`` ( split). The
functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes (the
writer queue, the retention lock/stop-event, the count cache) via the
passed-in reference.

Free functions:

- :func:`apply_retention` — runs the chunked retention sweep on the
  writer thread, then conditionally VACUUMs and hardens the FTS5
  index (``'optimize'`` for small sweeps, ``'rebuild'`` after large
  purges).
- :func:`schedule_periodic_retention` — spawns the daemon thread that
  periodically calls ``apply_retention``.
- :func:`stop_periodic_retention` — signals + joins the periodic
  retention thread (best-effort).
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

# IMPL-A: writer-thread tuning constants.
#   _RETENTION_BATCH — chunk size for the bulk DELETEs inside
#   ``apply_retention``. Each batch commits so the WAL doesn't grow
#   unboundedly and external readers see progress.
_RETENTION_BATCH = 100


def _rebuild_fts(
    conn: sqlite3.Connection,
    db: HistoryDB | None = None,
    *,
    source: str = "apply_retention",
    deleted: int | None = None,
    command: Literal["rebuild", "optimize"] = "rebuild",
) -> bool:
    """Issue an FTS5 ``'rebuild'`` / ``'optimize'`` command and surface the outcome.

    Extracted from the in-line ``try/except sqlite3.Error`` block that
    previously lived only inside :func:`apply_retention`. The FTS5
    AFTER DELETE trigger (``schema.py:_MIGRATION_V3``) only marks the
    rowid as deleted in the FTS5 delete-bitmap — the segment data in
    ``transcriptions_fts_data`` (containing the dictated plaintext)
    survives the trigger delete AND ``VACUUM`` (VACUUM rebuilds the
    main DB file but does NOT rebuild FTS5 shadow tables). The
    ``'rebuild'`` command drops all segments and rebuilds them from
    the (now-reduced) content table, so deleted dictated text is no
    longer recoverable from ``transcriptions_fts_data`` via forensic
    tools. The ``'optimize'`` form is the cheap sibling: it runs the
    FTS5 optimizer (consolidating segments AND applying the
    delete-bitmap to the merged output) without an O(N) re-index, at
    typically 3-4x less cost than ``'rebuild'``.

    This helper exists so the SAME observability (per-instance
    ``db._fts5_rebuild_failures`` counter +
    ``event_bus.publish({"type": "history_fts5_rebuild_failed"})``
    toast) is applied to EVERY call site that issues the rebuild —
    :func:`apply_retention` here, plus (intended)
    :meth:`voice_typer.server.history_db.HistoryDB.delete` and
    :meth:`voice_typer.server.history_db.HistoryDB.clear_all` in
    ``history_db.py``. The single-row ``delete`` path and the
    ``clear_all`` path both must invoke the rebuild so a user who
    deletes a single sensitive transcription (e.g. one containing a
    dictated password) leaves that text unrecoverable. ``clear_all``
    already issued the rebuild; the helper centralizes the logic so
    the per-row ``delete`` path can reuse it.

    Parameters
    ----------
    conn
        The writer-thread's ``sqlite3.Connection``. The command is
        issued via ``conn.cursor().execute(...)`` (NOT ``conn.execute``
        directly) so callers that mock the cursor's ``execute`` to
        simulate FTS5 failures see the failure — ``conn.execute``
        bypasses the cursor mock and the simulated failure is silently
        swallowed by the real connection.
    db
        The :class:`~voice_typer.server.history_db.HistoryDB` instance
        (or any duck-typed object exposing ``_fts5_rebuild_failures``
        and ``db_path``). ``None`` is tolerated for unit-test doubles
        that don't expose these attributes (the failure counter and
        event_bus publish are skipped).
    source
        Short string identifying the call site (``"apply_retention"`` /
        ``"delete"`` / ``"clear_all"``). Surfaced in the
        ``history_fts5_rebuild_failed`` event_bus payload and the
        log message so the renderer / diagnostics can tell which path
        failed.
    deleted
        Optional deleted-row count for the event_bus payload. ``None``
        when the call site doesn't track this (e.g. ``clear_all``).
    command
        ``"rebuild"`` (default) or ``"optimize"``. ``"rebuild"`` drops
        all segments and re-indexes from the content table (O(N), the
        ultimate safety net after large purges). ``"optimize"`` runs
        the FTS5 optimizer only (typically 3-4x cheaper; used for
        small retention sweeps and per-row deletes where the O(N)
        rebuild would burn on every tick).

    Returns
    -------
    bool
        ``True`` if the rebuild succeeded (or the FTS5 table doesn't
        exist — pre-V3 DB — treated as success since there is nothing
        to rebuild). ``False`` if the rebuild failed (the privacy
        guarantee is broken; callers may surface this via the
        ``RetentionResult.fts5_rebuild_ok`` attribute or their own
        return shape).

    The command is wrapped in a tolerant ``try/except sqlite3.Error``
    so an older DB (pre-V3 migration, no FTS table yet) doesn't crash
    the calling path. On failure the privacy guarantee is broken, so
    the failure is logged at ``ERROR``, the per-instance
    ``db._fts5_rebuild_failures`` counter is incremented, and an
    ``event_bus`` event ``{"type": "history_fts5_rebuild_failed"}``
    is published so the renderer can surface a toast.

    The rebuild is issued via ``conn.cursor().execute(...)`` (NOT
    ``conn.execute(...)`` directly) so callers that mock the cursor's
    ``execute`` to simulate FTS5 failures (see
    ``tests/test_history_retention_index.py::TestRetentionFts5RebuildFailure``)
    see the failure — ``conn.execute`` bypasses the cursor mock and
    the simulated failure is silently swallowed by the real
    connection. The cursor is closed in a ``finally`` block so the
    cursor-close contract is preserved (matches the
    ``clear_all`` pattern at ``history_db.py:1901-1908``).

    Note: ``'rebuild'`` is O(N) (drops and rebuilds ALL segments from
    the content table). For the single-row delete path this adds
    latency proportional to the total row count — but the privacy
    guarantee (deleted text is actually unrecoverable) is more
    important than latency. The per-row ``delete`` path in
    ``history_db.py`` uses the cheaper ``'optimize'`` form; the
    periodic retention tick runs a full ``'rebuild'`` after large
    sweeps (ratio > 0.20) as the ultimate safety net, and now issues a
    single ``'optimize'`` for small sweeps (0 < ratio <= 0.20) so
    sub-threshold deletes still get privacy hardening without an O(N)
    re-index every 10 minutes.
    """
    fts_cursor = conn.cursor()
    try:
        # Both FTS5 shadow indexes (unicode61 ``transcriptions_fts`` AND
        # the trigram CJK index ``transcriptions_fts_cjk``) must be kept
        # in lockstep: the dictated plaintext lives in BOTH shadow
        # tables, so the GDPR erasure guarantee covers both. The CJK
        # command is gated on table existence (SQLite without the
        # trigram tokenizer never got the V5 migration).
        from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

        fts_cursor.execute(f"INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('{command}')")
        if cjk_trigram_table_exists(conn):
            fts_cursor.execute(f"INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk) VALUES('{command}')")
        conn.commit()
        action = "rebuilt" if command == "rebuild" else "optimized"
        if deleted is not None:
            log.info(
                "[HISTORY_DB] FTS5 segments %s after %s (deleted %d rows)",
                action,
                source,
                deleted,
            )
        else:
            log.info(
                "[HISTORY_DB] FTS5 segments %s after %s",
                action,
                source,
            )
        return True
    except sqlite3.Error as e:
        # escalate from WARNING to ERROR — the GDPR Art. 17 /
        # privacy guarantee is broken (deleted dictated text
        # remains recoverable from ``transcriptions_fts_data`` via
        # forensic tools), not merely "suboptimal".
        log.exception(
            "[HISTORY_DB] FTS5 '%s' after %s FAILED: %s "
            "(FTS5 shadow-table segment data may persist — deleted "
            "dictated text remains recoverable; manual re-index advised)",
            command,
            source,
            e,
        )
        # observable metric — increment the per-instance failure
        # counter so diagnostics / IPC ``get_diagnostics`` handlers
        # can surface it to the user. ``getattr`` default keeps this
        # safe if the HistoryDB instance was constructed by an older
        # code path that didn't initialize the counter.
        if db is not None:
            try:
                current = getattr(db, "_fts5_rebuild_failures", 0)
                db._fts5_rebuild_failures = current + 1
            except Exception:  # noqa: BLE001 — best-effort metric
                log.debug(
                    "[HISTORY_DB] could not increment _fts5_rebuild_failures counter",
                    exc_info=True,
                )
        # best-effort event_bus publication so the renderer can show
        # a toast. Wrapped broadly because the event_bus import or
        # the publish call may fail (e.g. circular import during early
        # init); none of those should crash the calling path which
        # has already done its row deletes.
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "history_fts5_rebuild_failed",
                    "data": {
                        "db_path": str(getattr(db, "db_path", "")) if db is not None else "",
                        "deleted": deleted if deleted is not None else 0,
                        "error": str(e),
                        "source": source,
                    },
                }
            )
        except Exception as publish_exc:  # noqa: BLE001
            log.warning(
                "[HISTORY_DB] event_bus.publish(history_fts5_rebuild_failed) failed (best-effort, %s continues): %s",
                source,
                publish_exc,
            )
        return False
    finally:
        # cursor-close contract: always close the cursor we
        # opened above, even on the success path (the
        # long-lived ``cursor`` from the top of
        # ``_do_retention`` is closed in that function's own
        # ``finally``; ``_rebuild_fts`` creates its OWN cursor
        # so it owns its lifecycle). ``contextlib.suppress`` because the cursor may
        # already be closed by the ``conn.commit()`` path on some
        # SQLite builds, and a double-close is harmless.
        with contextlib.suppress(Exception):
            fts_cursor.close()


class RetentionResult(int):
    """an int subclass exposing the FTS5 rebuild status.

    The value IS the deleted-row count (so existing callers that do
    ``deleted = apply_retention(...)`` / ``assert deleted == 20`` /
    ``if deleted > 0:`` work unchanged because ``RetentionResult`` is
    a real ``int``). It ALSO exposes the FTS5 rebuild outcome so callers
    that care about the privacy guarantee can detect when the post-sweep
    FTS5 'rebuild' command failed and dictated text may still be
    recoverable from ``transcriptions_fts_data``:

    >>> r = apply_retention(db, retention_days=7)
    >>> r == 250          # int comparison still works
    True
    >>> r["deleted"]      # dict-style access
    250
    >>> r["fts5_rebuild_ok"]
    True
    >>> r.fts5_rebuild_ok  # attribute access
    True

    The dual nature is deliberate: changing the return type to a plain
    dict would silently break every existing caller (and 6 tests) that
    treat the result as an int. Subclassing int preserves the contract
    while adding the new privacy signal.

    Note: ``int`` subclasses cannot use nonempty ``__slots__`` (Python
    raises ``TypeError: nonempty __slots__ not supported for subtype
    of 'int'``), so instances of this class carry a ``__dict__`` for
    the ``fts5_rebuild_ok`` attribute. The class is constructed rarely
    (once per ``apply_retention`` call) so the per-instance ``__dict__``
    overhead is negligible.
    """

    fts5_rebuild_ok: bool

    def __new__(cls, value: int, fts5_rebuild_ok: bool = True) -> RetentionResult:
        instance = super().__new__(cls, value)
        instance.fts5_rebuild_ok = fts5_rebuild_ok
        return instance

    def __getitem__(self, key: str) -> Any:  # noqa: D401
        if key == "deleted":
            return int(self)
        if key == "fts5_rebuild_ok":
            return self.fts5_rebuild_ok
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "deleted":
            return int(self)
        if key == "fts5_rebuild_ok":
            return self.fts5_rebuild_ok
        return default

    def keys(self):
        return ["deleted", "fts5_rebuild_ok"]

    def __contains__(self, key: object) -> bool:
        return key in ("deleted", "fts5_rebuild_ok")

    def __repr__(self) -> str:
        return f"RetentionResult(deleted={int(self)!r}, fts5_rebuild_ok={self.fts5_rebuild_ok!r})"


def apply_retention(
    db: HistoryDB,
    retention_days: int = 0,
    max_entries: int = 0,
    retention_count: int = 0,
) -> RetentionResult:
    """Apply retention policy: delete old entries.

        Returns a :class:`RetentionResult` (an ``int`` subclass) whose value
        is the number of deleted entries and whose ``fts5_rebuild_ok``
        attribute / ``["fts5_rebuild_ok"]`` item reports whether the
        post-sweep FTS5 ``'rebuild'`` command succeeded.

    the cutoff is computed in **UTC** (matching
        ``CURRENT_TIMESTAMP`` semantics) and formatted as
        ``'%Y-%m-%d %H:%M:%S'`` so the lexicographic string comparison
        against ``transcriptions.timestamp`` matches the format used by
        SQLite's own timestamp functions. The previous code used naive
        ``datetime.now()`` (local time) + ``.isoformat()`` which produced
        a timezone-offset-suffixed string — on a machine whose local TZ
        is ahead of UTC, rows up to ``TZ_offset_hours`` newer than the
        true cutoff were incorrectly deleted.

    retention_count is wired as a fallback for max_entries.
        If max_entries is not set but retention_count is, use it.

        IMPL-A: runs inside the writer thread. Chunked deletes (100
        rows per batch, commit per batch) prevent the WAL from growing
        unboundedly and let external readers see progress.

    after the retention sweep, ``VACUUM`` runs only if
        more than 20% of rows were deleted — this avoids the VACUUM
        cost (which requires exclusive access and briefly blocks
        readers) for small sweeps while still reclaiming space after
        large purges.

    FTS5 hardening runs whenever rows were actually deleted:
        the full ``'rebuild'`` fires above the same ``ratio > 0.20``
        threshold (O(N) re-index — the privacy safety net after a
        large purge), while small sweeps issue a single ``'optimize'``
        (cheap, idempotent) so sub-threshold deletes still purge
        segment data from ``transcriptions_fts_data`` without an O(N)
        re-index on every 10-minute tick.

    if the FTS5 ``'rebuild'`` command fails after a sweep,
        the failure is no longer silent — it is logged at ``ERROR``
        level (the privacy guarantee is broken, not merely suboptimal),
        ``db._fts5_rebuild_failures`` is incremented (observable in
        diagnostics), and an ``event_bus`` event
        ``{"type": "history_fts5_rebuild_failed"}`` is published so the
        renderer can surface a toast. The returned ``RetentionResult``
        carries ``fts5_rebuild_ok=False`` so programmatic callers can
        detect the privacy failure and retry / surface their own UI.
    """
    # Local import to avoid a module-load circular dependency:
    # history_db.py imports from history_db_internals at module load
    # time, so we defer the HistoryDBError import until first call.
    from voice_typer.server.history_db import HistoryDBError

    # wire retention_count as fallback for max_entries
    effective_max = max_entries or retention_count
    deleted = 0
    # tracks whether the FTS5 'rebuild' step succeeded. The
    # flag is updated inside the writer-thread closure via ``nonlocal``
    # and surfaced on the returned ``RetentionResult``. Defaults to
    # True (no rebuild attempted == no privacy failure).
    fts5_rebuild_ok = True
    try:

        def _do_retention(conn: sqlite3.Connection) -> int:
            nonlocal deleted, fts5_rebuild_ok
            cursor = conn.cursor()

            # capture initial count to decide whether
            # to VACUUM. Computed before any deletes so the
            # ratio reflects the true scope of the sweep.
            cursor.execute("SELECT COUNT(*) FROM transcriptions")
            initial_count = cursor.fetchone()[0]

            # Predict whether VACUUM (or incremental_vacuum) will fire
            # AFTER the chunked DELETEs. If so, toggle
            # ``PRAGMA secure_delete=OFF``
            # BEFORE the DELETEs to avoid redundant zeroing I/O
            # (~200MB wasted on a 50K-row DB). The subsequent
            # VACUUM rewrites the file (old pages not in the new
            # file) or incremental_vacuum removes the free pages
            # from the file — either way the deleted text is not
            # recoverable, providing the equivalent privacy
            # guarantee. ``secure_delete`` is per-connection in
            # modern SQLite, but toggling it on the writer is safe
            # regardless: readers don't DELETE, so their
            # secure_delete setting is irrelevant.
            #
            # For new DBs created with ``PRAGMA auto_vacuum=INCREMENTAL``
            # (set in ``init_schema``), use ``PRAGMA incremental_vacuum(100)``
            # instead of full ``VACUUM`` — incremental_vacuum reclaims
            # free pages incrementally without rewriting the entire
            # file (no exclusive lock, ~100x faster on large DBs).
            # For existing DBs (auto_vacuum=NONE), keep the full
            # VACUUM-at-20% path as fallback.
            auto_vacuum_row = cursor.execute("PRAGMA auto_vacuum").fetchone()
            auto_vacuum_mode = int(auto_vacuum_row[0]) if auto_vacuum_row else 0
            use_incremental_vacuum = auto_vacuum_mode == 2  # INCREMENTAL

            predicted_deletes = 0
            if retention_days > 0:
                cutoff_predict = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                cur = cursor.execute(
                    "SELECT COUNT(*) FROM transcriptions WHERE timestamp < ? AND favorite = 0",
                    (cutoff_predict,),
                )
                predicted_deletes = max(predicted_deletes, int(cur.fetchone()[0]))
            if effective_max > 0 and initial_count > effective_max:
                predicted_deletes = max(predicted_deletes, initial_count - effective_max)
            will_reclaim = initial_count > 0 and predicted_deletes > 0 and (predicted_deletes / initial_count) > 0.20
            secure_delete_toggled = False
            if will_reclaim:
                with contextlib.suppress(sqlite3.Error):
                    cursor.execute("PRAGMA secure_delete=OFF")
                    secure_delete_toggled = True

            try:
                if retention_days > 0:
                    # compute the cutoff in UTC and format as
                    # ``'%Y-%m-%d %H:%M:%S'`` to exactly match the format
                    # SQLite uses for ``CURRENT_TIMESTAMP``. The previous
                    # code used naive ``datetime.now()`` (local time) and
                    # ``.isoformat()`` (which appends a TZ offset like
                    # ``+02:00``), so the comparison against the
                    # UTC-stamped ``transcriptions.timestamp`` column was
                    # wrong by the local TZ offset — on a machine whose
                    # local TZ is ahead of UTC, rows up to
                    # ``TZ_offset_hours`` newer than the true cutoff were
                    # incorrectly deleted. Using UTC + the bare
                    # ``'%Y-%m-%d %H:%M:%S'`` format (no TZ suffix) makes
                    # the lexicographic ``timestamp < ?`` comparison
                    # apples-to-apples with ``CURRENT_TIMESTAMP``.
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
                    while True:
                        cursor.execute(
                            "DELETE FROM transcriptions WHERE id IN ("
                            "  SELECT id FROM transcriptions"
                            "  WHERE timestamp < ? AND favorite = 0"
                            "  LIMIT ?"
                            ")",
                            (cutoff, _RETENTION_BATCH),
                        )
                        batch_deleted = cursor.rowcount
                        if batch_deleted == 0:
                            break
                        deleted += batch_deleted
                        conn.commit()  # release write lock between batches

                if effective_max > 0:
                    # Compute ``total`` once before the loop and decrement
                    # by ``batch_deleted`` per iteration. Previously this
                    # block re-ran ``SELECT COUNT(*) FROM transcriptions``
                    # on every iteration — O(N^2) total (one full COUNT
                    # scan per batch). For a power-user DB with 50K rows
                    # and max_entries=1000, that's 490 COUNT scans; each
                    # COUNT is O(N) on the favorite=0 subset, so the total
                    # cost was O(N^2/batch_size).
                    cursor.execute("SELECT COUNT(*) FROM transcriptions")
                    total = cursor.fetchone()[0]
                    while total > effective_max:
                        excess = min(total - effective_max, _RETENTION_BATCH)
                        cursor.execute(
                            """
                            DELETE FROM transcriptions
                            WHERE id IN (
                                SELECT id FROM transcriptions
                                WHERE favorite = 0
                                ORDER BY timestamp ASC
                                LIMIT ?
                            )
                        """,
                            (excess,),
                        )
                        batch_deleted = cursor.rowcount
                        if batch_deleted == 0:
                            break
                        deleted += batch_deleted
                        total -= batch_deleted
                        conn.commit()  # release write lock between batches

                # Close any open transaction before VACUUM (the last
                # DELETE with 0 rows still auto-opened a transaction).
                conn.commit()

                # Reclaim free pages only if >20% of rows were deleted.
                # This avoids the reclamation cost for small sweeps (e.g.
                # daily retention that deletes a handful of rows)
                # while still reclaiming space after large purges.
                if deleted > 0 and initial_count > 0:
                    ratio = deleted / initial_count
                    if ratio > 0.20:
                        reclaim_ok = False
                        if use_incremental_vacuum:
                            # ``incremental_vacuum`` reclaims up to N
                            # free pages without
                            # rewriting the entire file (no exclusive
                            # lock). 100 pages is fast (~1ms) and
                            # sufficient for typical retention sweeps;
                            # the next sweep reclaims more if needed.
                            try:
                                cursor.execute("PRAGMA incremental_vacuum(100)")
                                reclaim_ok = True
                                log.info(
                                    "[HISTORY_DB] incremental_vacuum(100) "
                                    "completed after retention (deleted "
                                    "%d/%d rows, %.0f%%)",
                                    deleted,
                                    initial_count,
                                    ratio * 100,
                                )
                            except sqlite3.Error as e:
                                log.warning(
                                    "[HISTORY_DB] incremental_vacuum after "
                                    "retention failed: %s (falling back to "
                                    "full VACUUM)",
                                    e,
                                )
                        if not reclaim_ok:
                            # Full VACUUM fallback (existing DBs without
                            # auto_vacuum=INCREMENTAL, OR
                            # incremental_vacuum failed).
                            try:
                                conn.execute("VACUUM")
                                reclaim_ok = True
                                log.info(
                                    "[HISTORY_DB] VACUUM completed after retention (deleted %d/%d rows, %.0f%%)",
                                    deleted,
                                    initial_count,
                                    ratio * 100,
                                )
                            except sqlite3.Error as e:
                                # If we toggled secure_delete=OFF, a
                                # VACUUM failure is a privacy regression:
                                # deleted text may be recoverable from
                                # free pages (no zeroing happened).
                                # Escalate to ERROR so the user can
                                # investigate. The next retention tick
                                # will retry VACUUM.
                                if secure_delete_toggled:
                                    log.exception(
                                        "[HISTORY_DB] VACUUM after retention "
                                        "FAILED with secure_delete=OFF: %s — "
                                        "deleted text may be recoverable from "
                                        "free pages until the next successful "
                                        "VACUUM (privacy regression).",
                                        e,
                                    )
                                else:
                                    log.warning(
                                        "[HISTORY_DB] VACUUM after retention failed: %s",
                                        e,
                                    )
                        # rebuild FTS5 segments after a bulk retention
                        # delete. The DELETE trigger
                        # ``transcriptions_ad_fts`` only marks rowids as
                        # deleted in the FTS5 delete-bitmap; the segment data
                        # in ``transcriptions_fts_data`` survives both the
                        # trigger delete and ``VACUUM``/``incremental_vacuum``
                        # (both rebuild/reclaim the main DB file but do NOT
                        # rebuild FTS5 shadow tables). After a large retention
                        # sweep, dictated text remained recoverable from
                        # ``transcriptions_fts_data`` via forensic tools —
                        # defeating  / GDPR Art. 17. The
                        # ``'rebuild'`` command drops all segments and
                        # rebuilds them from the (now-reduced) content
                        # table, so deleted dictated text is no longer
                        # recoverable. Wrapped in a tolerant try/except so
                        # an older DB (pre-V3 migration, no FTS table yet)
                        # doesn't crash the retention path. Only runs when
                        # we actually deleted rows (a no-op retention sweep
                        # has nothing to rebuild and would just churn the
                        # FTS index).
                        #
                        # the rebuild is gated by the SAME ``ratio > 0.20``
                        # threshold as VACUUM. Below that threshold, the FTS5
                        # delete-bitmap trigger already hides deleted rows
                        # from MATCH results — the only thing ``'rebuild'``
                        # would reclaim is segment data in
                        # ``transcriptions_fts_data``, which isn't worth an
                        # O(N) re-index for a handful of deletes (the
                        # 10-minute periodic-retention tick would otherwise
                        # burn O(N) on every tick for ~1-row deletes).
                        # Above the threshold, the rebuild MUST fire to
                        # preserve the  privacy guarantee (deleted
                        # dictated text must not remain recoverable from
                        # ``transcriptions_fts_data`` via forensic tools
                        # after a large purge).
                        # Below the threshold (but with rows actually
                        # deleted), the cheaper ``'optimize'`` command is
                        # issued instead: it consolidates segments and
                        # applies the delete-bitmap to the merged output
                        # (so deleted text is purged from the shadow
                        # table) at a cost proportional to the pending
                        # merges — NOT an O(N) re-index — closing the
                        # sub-threshold privacy gap without burning on
                        # every 10-minute tick.
                        # FTS5 hardening is invoked via the
                        # centralized ``_rebuild_fts`` helper so the
                        # observability (per-instance failure counter +
                        # ``event_bus`` toast) is identical across every
                        # call site (``apply_retention`` here, plus
                        # ``history_db.py::delete`` / ``clear_all``).
                        if not _rebuild_fts(
                            conn,
                            db,
                            source="apply_retention",
                            deleted=deleted,
                        ):
                            fts5_rebuild_ok = False
                    else:
                        # Small sweep (0 < ratio <= 0.20): single
                        # ``'optimize'`` — see the comment above.
                        if not _rebuild_fts(
                            conn,
                            db,
                            source="apply_retention",
                            deleted=deleted,
                            command="optimize",
                        ):
                            fts5_rebuild_ok = False
            finally:
                # Restore ``secure_delete=ON`` unconditionally (even on
                # exception) so subsequent per-row deletes on this
                # connection re-zero pages.
                # If the chunked DELETEs + reclamation raised, we
                # still want secure_delete=ON for the rest of the
                # connection's lifetime (the next retention tick will
                # retry).
                if secure_delete_toggled:
                    with contextlib.suppress(sqlite3.Error):
                        cursor.execute("PRAGMA secure_delete=ON")
                # close the long-lived cursor opened at the top of
                # ``_do_retention``.
                # ``_rebuild_fts`` opens/closes its OWN cursor, so on
                # the rebuild/optimize path this ``finally`` is the
                # only place the long-lived cursor is closed.
                # ``contextlib.suppress`` because a double-close is
                # harmless on SQLite builds that auto-close cursors
                # after ``conn.commit()``.
                with contextlib.suppress(Exception):
                    cursor.close()

            if deleted:
                log.info(
                    "[HISTORY_DB] Retention policy deleted %d entries",
                    deleted,
                )
            return deleted

        result = db._submit_write(_do_retention, wait=True)
        if result is None:
            # Writer unavailable — no rebuild was attempted, so the
            # fts5_rebuild_ok flag (still True) is correct: there is
            # no privacy failure to report (the deletes never ran).
            return RetentionResult(0, fts5_rebuild_ok=fts5_rebuild_ok)
        if result and result > 0:
            # invalidate the count cache.
            db._invalidate_history_count_cache()
            # invalidate the today-stats cache. apply_retention
            # deletes rows by age (``retention_days``) or by count
            # (``max_entries``). An age-based delete CAN drop today's
            # rows if ``retention_days=0`` (delete everything older
            # than 0 days = delete everything), and a count-based
            # delete drops the OLDEST rows first — which would only
            # affect today's stats if today's rows are the oldest
            # (unlikely but possible after a DB re-import). Either way
            # the cache must be invalidated so the next read reflects
            # the post-retention row set.
            db._invalidate_today_stats_cache()
        # surface the FTS5 rebuild outcome on the returned
        # ``RetentionResult``. ``int(result)`` would strip the
        # subclass, so we explicitly construct a new RetentionResult
        # carrying the (possibly False) fts5_rebuild_ok flag.
        return RetentionResult(int(result), fts5_rebuild_ok=fts5_rebuild_ok)
    except HistoryDBError:
        log.exception("[HISTORY] Writer unavailable for apply_retention")
        # apply_retention is called from a background
        # retention sweep, not from an IPC handler, so it preserves
        # the legacy "return 0 deleted" sentinel. The retention
        # sweep logs the error and moves on.
        return RetentionResult(0, fts5_rebuild_ok=fts5_rebuild_ok)
    except Exception as e:
        log.exception("[HISTORY] Failed to apply retention: %s", e)
        return RetentionResult(0, fts5_rebuild_ok=fts5_rebuild_ok)


def schedule_periodic_retention(
    db: HistoryDB,
    interval_s: float = 600.0,
    app: Any = None,
    *,
    retention_days: int = 0,
    max_entries: int = 0,
    retention_count: int = 0,
) -> None:
    """spawn a daemon thread that periodically calls ``apply_retention``.

        Before this method existed, ``apply_retention`` only ran once
        at startup (from ``startup_sequence._apply_retention_bg``). On
        a long dictation session (8h at ~1 transcription/minute ≈ 480
        new rows), the DB grew monotonically because the next
        ``apply_retention`` (and the conditional ``VACUUM`` that
        reclaims disk space) only fired on the NEXT app launch.

        This method spawns a daemon thread that loops:

        1. ``db._retention_stop_event.wait(timeout=interval_s)`` —
           blocks for ``interval_s`` seconds (or until stop is signaled).
        2. If the stop event fired (close() was called), exit.
        3. Try to acquire ``db._retention_lock`` non-blocking. If a
           previous retention is still running (e.g. a multi-batch
           ``VACUUM`` on a huge DB took longer than ``interval_s``),
           skip this tick and wait for the next one. This is the
    re-entrancy guard required by
        4. Resolve retention parameters from ``app.config`` if ``app``
           is provided (preferred — picks up config changes the user
           made at runtime), else use the keyword arguments.
        5. Call ``db.apply_retention(...)`` inside the lock.

        The thread is registered with ``app._thread_registry`` (when
        available) so the central shutdown coordinator can signal +
        join it. ``close()`` also signals the local stop event and
        joins with a 2s timeout as a fallback (so the thread exits
        even if the app has no ThreadRegistry).

        Parameters
        ----------
        interval_s : float
            Seconds between retention sweeps. Default 600s (10 min) —
    matches the  recommendation. The first sweep fires
            after ``interval_s`` seconds (NOT immediately), because
            ``startup_sequence`` already runs ``apply_retention`` once
            at startup; running it again immediately would duplicate
            that work.
        app : object, optional
            The ``VoiceTyperApp`` instance. Used to look up
            ``app.config.history_retention_days``,
            ``app.config.history_max_entries``,
            ``app.config.history_retention_count``, and
            ``app._thread_registry``. If ``None``, the keyword
            arguments below are used as static defaults.
        retention_days, max_entries, retention_count : int
            Static fallback values used when ``app`` is None or when
            ``app.config`` doesn't expose the corresponding attribute.
            Default 0 (no retention — caller must supply real values
            either via ``app`` or via these keyword args).

        Notes
        -----
        Calling this method while a periodic retention is already
        running stops the previous thread (signals + joins) before
        spawning the new one. This makes the method idempotent and
        safe to call from ``startup_sequence`` even if the app
        restarts in place (e.g. after a config reload).

        The actual wiring (calling this method from
        ``startup_sequence``) lives in the startup sequence itself; this
        method just exposes the API.
    """
    # Stop any existing periodic retention thread before spawning a
    # new one — idempotent re-scheduling.
    stop_periodic_retention(db)

    stop_event = threading.Event()
    db._retention_stop_event = stop_event

    def _periodic_retention_loop() -> None:
        """inner loop — wait, skip-if-busy, run, repeat."""
        while not stop_event.wait(timeout=interval_s):
            if db._shutdown.is_set() or stop_event.is_set():
                break
            # Re-entrancy guard: skip this tick if a previous
            # retention sweep is still running. ``acquire(blocking=False)``
            # returns False immediately if the lock is held.
            if not db._retention_lock.acquire(blocking=False):
                log.debug(
                    "[HISTORY_DB] periodic retention tick skipped — previous run still active (interval_s=%.1f)",
                    interval_s,
                )
                continue
            try:
                # Resolve retention parameters from app.config
                # (preferred — picks up runtime config changes)
                # or fall back to the static kwargs.
                days = retention_days
                max_ent = max_entries
                ret_count = retention_count
                if app is not None:
                    cfg = getattr(app, "config", None)
                    if cfg is not None:
                        days = int(getattr(cfg, "history_retention_days", days))
                        max_ent = int(getattr(cfg, "history_max_entries", max_ent))
                        ret_count = int(
                            getattr(
                                cfg,
                                "history_retention_count",
                                ret_count,
                            )
                        )
                db.apply_retention(
                    retention_days=days,
                    max_entries=max_ent,
                    retention_count=ret_count,
                )
                # Per-row FTS5 ``'optimize'`` flush: a previous revision
                # called ``db._flush_pending_fts_optimize(wait=False)``
                # here, but that method was never implemented — the call
                # was silently swallowed by ``contextlib.suppress``. The
                # GDPR Art. 17 right-to-erasure safety net is already
                # covered by ``apply_retention``'s post-sweep FTS5
                # ``'rebuild'`` (see ``_rebuild_fts5``) plus the
                # ``_fts5_startup_rebuild`` sweep on next launch.
            except Exception:
                log.warning(
                    "[HISTORY_DB] periodic retention run failed",
                    exc_info=True,
                )
            finally:
                db._retention_lock.release()

    thread = threading.Thread(
        target=_periodic_retention_loop,
        name="HistoryDBPeriodicRetention",
        daemon=True,
    )
    db._retention_thread = thread
    thread.start()

    # Register with ThreadRegistry if available on app — this lets
    # the central shutdown coordinator signal + join the thread
    # alongside the other app-owned daemon threads.
    registry = getattr(app, "_thread_registry", None) if app is not None else None
    if registry is not None:
        try:
            # Lazy import to avoid any chance of circular import
            # (history_db is imported very early in app startup).
            from voice_typer.server.thread_registry import ThreadRegistry  # noqa: F401

            registry.register(
                name="history-periodic-retention",
                thread=thread,
                stop_event=stop_event,
                join_timeout=2.0,
            )
        except Exception:
            log.debug(
                "[HISTORY_DB] could not register periodic retention thread with ThreadRegistry",
                exc_info=True,
            )


def stop_periodic_retention(db: HistoryDB) -> None:
    """signal the periodic retention thread to stop and join it.

    Called by :meth:`HistoryDB.close` and by
    :meth:`HistoryDB.schedule_periodic_retention` (to support
    idempotent re-scheduling). Best-effort — if the thread doesn't
    exit within 2s (e.g. stuck in a long VACUUM), it is left to die
    as a daemon at process exit.
    """
    stop_event = db._retention_stop_event
    thread = db._retention_thread
    if stop_event is not None:
        with contextlib.suppress(Exception):
            stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
        if thread.is_alive():
            log.debug(
                "[HISTORY_DB] periodic retention thread did not exit "
                "within 2s — it is a daemon and will exit at process shutdown."
            )
    db._retention_thread = None
    db._retention_stop_event = None
