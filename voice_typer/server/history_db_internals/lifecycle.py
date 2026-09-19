"""Lifecycle helpers for :class:`~voice_typer.server.history_db.HistoryDB`."""

from __future__ import annotations

import contextlib
import logging
import queue
import sqlite3
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)


def initialize_state(db: HistoryDB) -> None:
    """Set up every mutable instance attribute before the writer starts."""
    # Lazy reads so the values track monkeypatches on the
    from voice_typer.server import history_db as _hd

    # Thread-local read-only connections (one per reader thread).
    db._read_local = threading.local()
    # Track ALL read connections across threads so close() + __del__
    db._all_read_connections = []
    db._connections_lock = threading.Lock()
    # generation counter bumped on corruption-recovery
    db._read_conn_generation = 0
    # Write queue: items are (callable, future) tuples, OR
    db._queue = queue.Queue(maxsize=_hd._WRITE_QUEUE_MAXSIZE)
    # Signaled by the writer thread once schema init succeeds (or
    db._writer_ready = threading.Event()
    # Set by close() to refuse new write submissions.
    db._shutdown = threading.Event()
    # If the writer thread failed during schema init, the exception
    db._init_error = None
    # re-entrancy guard for apply_retention. The periodic
    db._retention_lock = threading.Lock()
    # stop event for the periodic retention thread. Set by
    db._retention_stop_event = None
    # handle to the periodic retention daemon thread (for
    db._retention_thread = None
    # TTL cache for ``get_history_count``.
    db._history_count_cache = None
    db._history_count_cache_ts = 0.0
    db._history_count_cache_lock = threading.Lock()
    # TTL cache for ``get_today_stats``. See
    db._today_stats_cache = None
    db._today_stats_cache_ts = 0.0
    db._today_stats_cache_lock = threading.Lock()
    # per-instance counter of FTS5 'rebuild' failures after
    db._fts5_rebuild_failures = 0
    # True when this session's startup FTS5 'rebuild' actually ran
    db._fts5_rebuild_ran = False
    # Ascending-id watermark for ``_reindex_encrypted_fts_step`` —
    db._fts_reindex_watermark = 0
    # At-rest-encryption status, one of "active" / "disabled" /
    db._encryption_status = "disabled"
    # periodic prune daemon for ``_all_read_connections``.
    db._read_conn_prune_stop_event = None
    db._read_conn_prune_thread = None


def wait_for_writer_ready(db: HistoryDB) -> None:
    """Wait for the writer thread's schema-init handshake, then register."""
    from voice_typer.server import history_db as _hd

    if not db._writer_ready.wait(timeout=_hd._WRITER_READY_TIMEOUT):
        log.error(
            "[HISTORY_DB] Writer thread did not signal ready within %.1fs (db=%s); writes will fail until it recovers.",
            _hd._WRITER_READY_TIMEOUT,
            db.db_path,
        )
    if db._init_error is not None:
        log.error(
            "[HISTORY_DB] Writer thread initialization failed: %s",
            db._init_error,
        )
    # register in the module-level WeakSet so the test conftest
    _hd._LIVE_INSTANCES.add(db)
    # start the periodic prune daemon (best-effort —
    with contextlib.suppress(Exception):
        db._start_periodic_read_conn_prune()


def close_read_connections(db: HistoryDB) -> None:
    """Close every tracked read connection (current thread + all threads)."""
    # Close the current thread's read connection first (if any).
    if hasattr(db._read_local, "conn") and db._read_local.conn is not None:
        with contextlib.suppress(sqlite3.Error):
            db._read_local.conn.close()
        db._read_local.conn = None
    # Then close all other read connections tracked across threads.
    with db._connections_lock:
        for _ident, conn in db._all_read_connections:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        db._all_read_connections.clear()


def gc_close_read_connections(db: HistoryDB) -> None:
    """Non-blocking read-connection teardown for ``HistoryDB.__del__``."""
    if hasattr(db._read_local, "conn") and db._read_local.conn is not None:
        with contextlib.suppress(Exception):
            db._read_local.conn.close()
        db._read_local.conn = None
    if not db._connections_lock.acquire(blocking=False):
        return
    try:
        for _ident, conn in db._all_read_connections:
            with contextlib.suppress(Exception):
                conn.close()
        db._all_read_connections.clear()
    finally:
        db._connections_lock.release()


def close_db(db: HistoryDB) -> None:
    """Shut down the writer thread and close all connections."""
    # stop the periodic retention thread BEFORE setting
    db._stop_periodic_retention()
    # Stop the periodic read-conn prune daemon before tearing down
    db._stop_read_conn_prune_thread()
    if db._shutdown.is_set():
        # Already closed, just make sure read conns are gone.
        close_read_connections(db)
        return
    db._shutdown.set()
    # Writer-teardown (best-effort wal_checkpoint(TRUNCATE), shutdown
    db._close_writer()
    close_read_connections(db)


def health_check(db: HistoryDB) -> dict:
    """Return a health status dict for diagnostics."""
    if db._init_error is not None:
        return {"ok": False, "error": str(db._init_error)}
    if not db._writer_thread.is_alive():
        return {"ok": False, "error": "history DB writer thread is not alive"}
    if not db._writer_ready.is_set():
        return {
            "ok": False,
            "error": "history DB schema initialization still in progress (writer not ready)",
        }
    return {"ok": True, "error": None}
