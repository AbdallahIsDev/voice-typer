"""Thread-local read-connection pool and periodic prune daemon."""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from typing import TYPE_CHECKING

from voice_typer.server.platform_utils import is_windows

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)


def _get_read_conn(db: HistoryDB) -> sqlite3.Connection:
    """Each reader thread gets its own connection (stored in
    ``threading.local()``). ``PRAGMA query_only=1`` enforces
    write through this connection, SQLite would reject it. In WAL
    mode, readers never block the writer and the writer never
    blocks readers.
    SEC-007: directory + file permission tightening (0o700 / 0o600
    on POSIX) is owned by the writer's ``open_write_conn`` (single
    source of truth), readers inherit the already-tightened file
    new-reader creation.
    page cache (``PRAGMA cache_size=-2000``, ). When the
    previously each reader set ``cache_size=-20000`` (20 MB).
    With 5-8 reader threads (IPC handlers + tray + dictation
    sufficient. The writer keeps the 20 MB cache (see
    """
    # if the read-conn generation bumped (corruption
    cached_gen = getattr(db._read_local, "gen", 0)
    if hasattr(db._read_local, "conn") and db._read_local.conn is not None and cached_gen != db._read_conn_generation:
        with contextlib.suppress(sqlite3.Error):
            db._read_local.conn.close()
        db._read_local.conn = None
    if not hasattr(db._read_local, "conn") or db._read_local.conn is None:
        # Mirror open_write_conn: the parent directory must exist before
        try:
            db.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("[HISTORY_DB] Could not create DB directory %s: %s", db.db_path.parent, e)
        if not is_windows():
            try:
                os.chmod(db.db_path.parent, 0o700)
            except OSError as e:
                log.warning("[HISTORY_DB] Could not tighten dir perms: %s", e)
        conn = sqlite3.connect(
            str(db.db_path),
            check_same_thread=False,
            timeout=5.0,
        )
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # 2 MB page cache for readers (was -20000 / 20 MB).
        conn.execute("PRAGMA query_only=1")
        # Don't force WAL here, the writer already set it on the
        conn.row_factory = sqlite3.Row
        db._read_local.conn = conn
        # stamp the generation so a later corruption
        db._read_local.gen = db._read_conn_generation
        with db._connections_lock:
            db._all_read_connections.append((threading.get_ident(), conn))
            # Opportunistic GC: close connections whose owning
            db._prune_dead_read_connections_locked()
    return db._read_local.conn


def _prune_dead_read_connections_locked(db: HistoryDB) -> None:
    """Close read connections whose owning thread has exited."""
    if not db._all_read_connections:
        return
    # Build the set of alive thread idents. threading.enumerate()
    alive_idents = {t.ident for t in threading.enumerate() if t.is_alive()}
    # The current thread is always alive (we're running on it).
    alive_idents.add(threading.get_ident())
    kept: list[tuple[int, sqlite3.Connection]] = []
    for ident, conn in db._all_read_connections:
        if ident in alive_idents:
            kept.append((ident, conn))
        else:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            # Drop a debug log so operators can see the pruning
            log.debug(
                "[HISTORY_DB] Pruned dead-thread read connection (thread_ident=%s); released ~2 MB page cache.",
                ident,
            )
    db._all_read_connections = kept


def _get_conn(db: HistoryDB) -> sqlite3.Connection:
    """Backwards-compat alias for ``_get_read_conn``."""
    return db._get_read_conn()


def _start_read_conn_prune_thread(db: HistoryDB) -> None:
    """Start a daemon thread that periodically prunes dead read"""
    if db._read_conn_prune_thread is not None and db._read_conn_prune_thread.is_alive():
        return
    db._read_conn_prune_stop_event = threading.Event()
    db._read_conn_prune_thread = threading.Thread(
        target=db._periodic_read_conn_prune_loop,
        name="HistoryDBReadConnPrune",
        daemon=True,
    )
    db._read_conn_prune_thread.start()


def _stop_read_conn_prune_thread(db: HistoryDB) -> None:
    """Stop the periodic prune daemon (called by close())."""
    evt = db._read_conn_prune_stop_event
    thread = db._read_conn_prune_thread
    if evt is not None:
        evt.set()
    if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
        thread.join(timeout=2.0)
    db._read_conn_prune_thread = None
    db._read_conn_prune_stop_event = None


def _periodic_read_conn_prune_loop(db: HistoryDB) -> None:
    """The periodic prune loop body. Runs on a daemon thread."""
    # Lazy import so the constant tracks monkeypatches on the
    from voice_typer.server import history_db as _hd

    evt = db._read_conn_prune_stop_event
    if evt is None:
        return
    while not evt.is_set():
        # Re-read the interval each iteration so a test patching
        interval = _hd._READ_CONN_PRUNE_INTERVAL_S
        # Wait for the interval or the stop signal, whichever first.
        if evt.wait(timeout=interval):
            return
        try:
            with db._connections_lock:
                db._prune_dead_read_connections_locked()
        except Exception:
            log.debug(
                "[HISTORY_DB] periodic read-conn prune failed (non-fatal)",
                exc_info=True,
            )
