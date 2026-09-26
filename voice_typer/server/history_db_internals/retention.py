"""Retention sweep + periodic scheduling helpers."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Final, Literal, get_args

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

# Writer-thread tuning constants.
_RETENTION_BATCH = 100

# FTS5 special commands cannot be bound as parameters, so the command text is
# interpolated into the statement. The runtime allowlist below is the single
# source of truth (derived from the annotation) and gates every call.
Fts5Command = Literal["rebuild", "optimize"]
_FTS5_COMMANDS: Final[frozenset[str]] = frozenset(get_args(Fts5Command))


def _rebuild_fts(
    conn: sqlite3.Connection,
    db: HistoryDB | None = None,
    *,
    source: str = "apply_retention",
    deleted: int | None = None,
    command: Fts5Command = "rebuild",
) -> bool:
    """Issue an FTS5 ``'rebuild'`` / ``'optimize'`` command and surface the outcome."""
    if command not in _FTS5_COMMANDS:
        raise ValueError(f"unsupported FTS5 command: {command!r}")
    fts_cursor = conn.cursor()
    try:
        # Both FTS5 shadow indexes (unicode61 ``transcriptions_fts`` AND
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
        # escalate from WARNING to ERROR, the GDPR Art. 17 /
        log.exception(
            "[HISTORY_DB] FTS5 '%s' after %s FAILED: %s "
            "(FTS5 shadow-table segment data may persist, deleted "
            "dictated text remains recoverable; manual re-index advised)",
            command,
            source,
            e,
        )
        # observable metric, increment the per-instance failure
        if db is not None:
            try:
                current = getattr(db, "_fts5_rebuild_failures", 0)
                db._fts5_rebuild_failures = current + 1
            except Exception:  # noqa: BLE001, best-effort metric
                log.debug(
                    "[HISTORY_DB] could not increment _fts5_rebuild_failures counter",
                    exc_info=True,
                )
        # best-effort event_bus publication so the renderer can show
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
        with contextlib.suppress(Exception):
            fts_cursor.close()


class RetentionResult(int):
    """an int subclass exposing the FTS5 rebuild status."""

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
    """
    # Local import to avoid a module-load circular dependency:
    from voice_typer.server.history_db import HistoryDBError

    # wire retention_count as fallback for max_entries
    effective_max = max_entries or retention_count
    deleted = 0
    # tracks whether the FTS5 'rebuild' step succeeded. The
    fts5_rebuild_ok = True
    try:

        def _do_retention(conn: sqlite3.Connection) -> int:
            nonlocal deleted, fts5_rebuild_ok
            cursor = conn.cursor()

            # capture initial count to decide whether
            cursor.execute("SELECT COUNT(*) FROM transcriptions")
            initial_count = cursor.fetchone()[0]

            # Predict whether VACUUM (or incremental_vacuum) will fire
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
                conn.commit()

                # Reclaim free pages only if >20% of rows were deleted.
                if deleted > 0 and initial_count > 0:
                    ratio = deleted / initial_count
                    if ratio > 0.20:
                        reclaim_ok = False
                        if use_incremental_vacuum:
                            # ``incremental_vacuum`` reclaims up to N
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
                                if secure_delete_toggled:
                                    log.exception(
                                        "[HISTORY_DB] VACUUM after retention "
                                        "FAILED with secure_delete=OFF: %s, "
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
                        if not _rebuild_fts(
                            conn,
                            db,
                            source="apply_retention",
                            deleted=deleted,
                        ):
                            fts5_rebuild_ok = False
                    else:
                        # Small sweep (0 < ratio <= 0.20): single
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
                if secure_delete_toggled:
                    with contextlib.suppress(sqlite3.Error):
                        cursor.execute("PRAGMA secure_delete=ON")
                # close the long-lived cursor opened at the top of
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
            # Writer unavailable, no rebuild was attempted, so the
            return RetentionResult(0, fts5_rebuild_ok=fts5_rebuild_ok)
        if result and result > 0:
            # invalidate the count cache.
            db._invalidate_history_count_cache()
            # invalidate the today-stats cache. apply_retention
            db._invalidate_today_stats_cache()
        # surface the FTS5 rebuild outcome on the returned
        return RetentionResult(int(result), fts5_rebuild_ok=fts5_rebuild_ok)
    except HistoryDBError:
        log.exception("[HISTORY] Writer unavailable for apply_retention")
        # apply_retention is called from a background
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
           is provided (preferred, picks up config changes the user
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
            The ``LausuApp`` instance. Used to look up
            ``app.config.history_retention_days``,
            ``app.config.history_max_entries``,
            ``app.config.history_retention_count``, and
            ``app._thread_registry``. If ``None``, the keyword
            arguments below are used as static defaults.
        retention_days, max_entries, retention_count : int
            Static fallback values used when ``app`` is None or when
            ``app.config`` doesn't expose the corresponding attribute.
            Default 0 (no retention, caller must supply real values
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
    stop_periodic_retention(db)

    stop_event = threading.Event()
    db._retention_stop_event = stop_event

    def _periodic_retention_loop() -> None:
        """inner loop, wait, skip-if-busy, run, repeat."""
        while not stop_event.wait(timeout=interval_s):
            if db._shutdown.is_set() or stop_event.is_set():
                break
            # Re-entrancy guard: skip this tick if a previous
            if not db._retention_lock.acquire(blocking=False):
                log.debug(
                    "[HISTORY_DB] periodic retention tick skipped, previous run still active (interval_s=%.1f)",
                    interval_s,
                )
                continue
            try:
                # Resolve retention parameters from app.config
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

    # Register with ThreadRegistry if available on app, this lets
    registry = getattr(app, "_thread_registry", None) if app is not None else None
    if registry is not None:
        try:
            # Lazy import to avoid any chance of circular import
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
    """signal the periodic retention thread to stop and join it."""
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
                "within 2s, it is a daemon and will exit at process shutdown."
            )
    db._retention_thread = None
    db._retention_stop_event = None
