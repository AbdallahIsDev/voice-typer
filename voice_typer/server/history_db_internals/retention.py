"""Retention sweep + periodic scheduling helpers.

Extracted from the once-monolithic ``history_db.py`` (DT-23 split). The
functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes (the
writer queue, the retention lock/stop-event, the count cache) via the
passed-in reference.

Free functions:

- :func:`apply_retention` — runs the chunked retention sweep on the
  writer thread, then conditionally VACUUMs.
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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

# IMPL-A: writer-thread tuning constants.
#   _RETENTION_BATCH — chunk size for the bulk DELETEs inside
#   ``apply_retention``. Each batch commits so the WAL doesn't grow
#   unboundedly and external readers see progress.
_RETENTION_BATCH = 100


def apply_retention(
    db: HistoryDB,
    retention_days: int = 0,
    max_entries: int = 0,
    retention_count: int = 0,
) -> int:
    """Apply retention policy: delete old entries.

    Returns the number of deleted entries.

    DEAD-012: retention_count is wired as a fallback for max_entries.
    If max_entries is not set but retention_count is, use it.

    IMPL-A: runs inside the writer thread. Chunked deletes (100
    rows per batch, commit per batch) prevent the WAL from growing
    unboundedly and let external readers see progress.

    G4-M-05: after the retention sweep, ``VACUUM`` runs only if
    more than 20% of rows were deleted — this avoids the VACUUM
    cost (which requires exclusive access and briefly blocks
    readers) for small sweeps while still reclaiming space after
    large purges.
    """
    # Local import to avoid a module-load circular dependency:
    # history_db.py imports from history_db_internals at module load
    # time, so we defer the HistoryDBError import until first call.
    from voice_typer.server.history_db import HistoryDBError

    # DEAD-012: wire retention_count as fallback for max_entries
    effective_max = max_entries or retention_count
    deleted = 0
    try:

        def _do_retention(conn: sqlite3.Connection) -> int:
            nonlocal deleted
            cursor = conn.cursor()

            # G4-M-05: capture initial count to decide whether
            # to VACUUM. Computed before any deletes so the
            # ratio reflects the true scope of the sweep.
            cursor.execute("SELECT COUNT(*) FROM transcriptions")
            initial_count = cursor.fetchone()[0]

            if retention_days > 0:
                cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
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

            # G4-M-05: VACUUM only if >20% of rows were deleted.
            # This avoids the VACUUM cost for small sweeps (e.g.
            # daily retention that deletes a handful of rows)
            # while still reclaiming space after large purges.
            if deleted > 0 and initial_count > 0:
                ratio = deleted / initial_count
                if ratio > 0.20:
                    try:
                        conn.execute("VACUUM")
                        log.info(
                            "[HISTORY_DB] VACUUM completed after retention (deleted %d/%d rows, %.0f%%)",
                            deleted,
                            initial_count,
                            ratio * 100,
                        )
                    except sqlite3.Error as e:
                        log.warning(
                            "[HISTORY_DB] VACUUM after retention failed: %s",
                            e,
                        )
                # FR-27: rebuild FTS5 segments after a bulk retention
                # delete. The DELETE trigger
                # ``transcriptions_ad_fts`` only marks rowids as
                # deleted in the FTS5 delete-bitmap; the segment data
                # in ``transcriptions_fts_data`` survives both the
                # trigger delete and ``VACUUM`` (VACUUM rebuilds the
                # main DB file but does NOT rebuild FTS5 shadow
                # tables). After a large retention sweep, dictated
                # text remained recoverable from
                # ``transcriptions_fts_data`` via forensic tools —
                # defeating G4-M-05 / GDPR Art. 17. The
                # ``'rebuild'`` command drops all segments and
                # rebuilds them from the (now-reduced) content
                # table, so deleted dictated text is no longer
                # recoverable. Wrapped in a tolerant try/except so
                # an older DB (pre-V3 migration, no FTS table yet)
                # doesn't crash the retention path. Only runs when
                # we actually deleted rows (a no-op retention sweep
                # has nothing to rebuild and would just churn the
                # FTS index).
                try:
                    cursor.execute(
                        "INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')"
                    )
                    conn.commit()
                    log.info(
                        "[HISTORY_DB] FTS5 segments rebuilt after retention "
                        "(deleted %d rows)",
                        deleted,
                    )
                except sqlite3.Error as e:
                    log.warning(
                        "[HISTORY_DB] FTS5 'rebuild' after retention failed: %s "
                        "(FTS5 shadow-table segment data may persist — manual re-index advised)",
                        e,
                    )

            if deleted:
                log.info(
                    "[HISTORY_DB] Retention policy deleted %d entries",
                    deleted,
                )
            return deleted

        result = db._submit_write(_do_retention, wait=True)
        if result is None:
            return 0
        if result and result > 0:
            # TY-20: invalidate the count cache.
            db._invalidate_history_count_cache()
        return int(result)
    except HistoryDBError:
        log.error("[HISTORY] Writer unavailable for apply_retention")
        # ERR-013: apply_retention is called from a background
        # retention sweep, not from an IPC handler, so it preserves
        # the legacy "return 0 deleted" sentinel. The retention
        # sweep logs the error and moves on.
        return 0
    except Exception as e:
        log.error("[HISTORY] Failed to apply retention: %s", e)
        return 0


def schedule_periodic_retention(
    db: HistoryDB,
    interval_s: float = 600.0,
    app: Any = None,
    *,
    retention_days: int = 0,
    max_entries: int = 0,
    retention_count: int = 0,
) -> None:
    """ER-36: spawn a daemon thread that periodically calls ``apply_retention``.

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
       re-entrancy guard required by ER-36.
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
        matches the ER-36 recommendation. The first sweep fires
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
    ``startup_sequence``) is owned by ER-FIX-F; this method just
    exposes the API.
    """
    # Stop any existing periodic retention thread before spawning a
    # new one — idempotent re-scheduling.
    stop_periodic_retention(db)

    stop_event = threading.Event()
    db._retention_stop_event = stop_event

    def _periodic_retention_loop() -> None:
        """ER-36: inner loop — wait, skip-if-busy, run, repeat."""
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
    """ER-36: signal the periodic retention thread to stop and join it.

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
