"""Thread-local read-connection pool and periodic prune daemon.

Extracted from the once-monolithic ``history_db.py`` (wave 1 split).
The functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read/write the instance's attributes (the
thread-local ``_read_local``, the ``_all_read_connections`` list, the
``_connections_lock``, the ``_read_conn_generation`` counter, and the
prune-thread handles) via the passed-in reference.

Free functions:

- :func:`_get_read_conn` — get (or lazily create) a thread-local
  READ-ONLY connection. PRAGMA ``query_only=1`` enforces read-only
  access at the SQLite layer.
- :func:`_prune_dead_read_connections_locked` — close read
  connections whose owning thread has exited (must be called with
  ``_connections_lock`` held).
- :func:`_periodic_read_conn_prune_loop` — the daemon-thread body
  that periodically calls ``_prune_dead_read_connections_locked``
  every ``_READ_CONN_PRUNE_INTERVAL_S`` seconds.
- :func:`_start_read_conn_prune_thread` — start the prune daemon
  (idempotent).
- :func:`_stop_read_conn_prune_thread` — signal + join the prune
  daemon (best-effort, called by ``HistoryDB.close``).
- :func:`_get_conn` — backwards-compat alias for ``_get_read_conn``.

The ``_READ_CONN_PRUNE_INTERVAL_S`` constant continues to live on
:mod:`voice_typer.server.history_db` so existing test monkeypatches
(e.g. ``monkeypatch.setattr(history_db, "_READ_CONN_PRUNE_INTERVAL_S",
0.1)``) keep working unchanged. The prune loop reads it from that
module namespace on each iteration via a lazy import.
"""

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
    """Get a thread-local READ-ONLY connection.

    IMPL-A: each reader thread gets its own connection (stored in
    ``threading.local()``). ``PRAGMA query_only=1`` enforces
    read-only access at the SQLite layer — even if a bug tried to
    write through this connection, SQLite would reject it. In WAL
    mode, readers never block the writer and the writer never
    blocks readers.

    SEC-007: directory + file permission tightening (0o700 / 0o600
    on POSIX) is owned by the writer's ``open_write_conn`` (single
    source of truth) — readers inherit the already-tightened file
    perms and don't repeat the mkdir/chmod churn on every
    new-reader creation.

    Memory management: each read connection carries a 2 MB SQLite
    page cache (``PRAGMA cache_size=-2000``, ). When the
    owning thread exits, its ``threading.local()`` storage is GC'd
    but the connection itself stays alive (held by
    ``_all_read_connections``) until ``close()`` runs. To avoid
    unbounded memory growth across long-running app sessions with
    thread pool churn, ``_prune_dead_read_connections_locked`` is
    called on each new-connection creation: it walks the list,
    closes connections whose owning thread has exited, and drops
    them from the list. The pruning is O(n) but runs only on
    first-call-per-thread (not on every read), so the amortized
    cost is negligible.

    previously each reader set ``cache_size=-20000`` (20 MB).
    With 5-8 reader threads (IPC handlers + tray + dictation
    pipeline), peak page-cache memory was 120-180 MB for a DB
    typically < 50 MB. Reads are indexed lookups + small
    aggregations; the working set is tiny, so a 2 MB cache is
    sufficient. The writer keeps the 20 MB cache (see
    ``schema.open_write_conn``) for batch INSERTs and VACUUM.
    """
    # if the read-conn generation bumped (corruption
    # recovery renamed the DB and invalidated all read conns),
    # close the stale thread-local conn and reconnect. We can
    # only close THIS thread's conn here; other threads will
    # detect the mismatch on their next ``_get_read_conn`` call.
    cached_gen = getattr(db._read_local, "gen", 0)
    if hasattr(db._read_local, "conn") and db._read_local.conn is not None and cached_gen != db._read_conn_generation:
        with contextlib.suppress(sqlite3.Error):
            db._read_local.conn.close()
        db._read_local.conn = None
    if not hasattr(db._read_local, "conn") or db._read_local.conn is None:
        # Mirror open_write_conn: the parent directory must exist before
        # SQLite can open the file, on EVERY platform — a reader thread
        # may be the first to touch a fresh install whose ``<config>/db/``
        # has not been created yet. Idempotent.
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
        # Reads are indexed lookups + small aggregations; the 2 MB
        # working set is sufficient and bounds peak reader-thread
        # memory (5-8 readers × 2 MB = 10-16 MB vs the previous
        # 120-180 MB). The writer keeps -20000 for batch INSERTs.
        conn.execute("PRAGMA cache_size=-2000")  # 2 MB
        # Enforce read-only at the SQLite layer.
        conn.execute("PRAGMA query_only=1")
        # Don't force WAL here — the writer already set it on the
        # DB file; readers inherit whatever journal mode the DB
        # file is in. Forcing WAL on a read-only connection on a
        # network FS could fail.
        conn.row_factory = sqlite3.Row
        db._read_local.conn = conn
        # stamp the generation so a later corruption
        # recovery bump is detectable (see
        # ``_maybe_recover_from_corruption``).
        db._read_local.gen = db._read_conn_generation
        with db._connections_lock:
            db._all_read_connections.append((threading.get_ident(), conn))
            # Opportunistic GC: close connections whose owning
            # thread has exited. This is the only place we prune
            # (we don't run a background reaper), so we run it on
            # every new-connection creation to keep the list
            # bounded. The check is cheap (one threading.enumerate()
            # call + a list filter).
            db._prune_dead_read_connections_locked()
    return db._read_local.conn


def _prune_dead_read_connections_locked(db: HistoryDB) -> None:
    """Close read connections whose owning thread has exited.

    Must be called with ``self._connections_lock`` held. Walks
    ``_all_read_connections`` and closes any connection whose
    ``thread_ident`` is not in the set of currently-alive threads
    (per ``threading.enumerate``). The current thread's ident is
    always treated as live (we're running on it). This bounds
    memory growth: without pruning, each dead reader thread's
    2 MB page cache (, was 20 MB) would persist until
    ``close()`` ran.

    Note: threads created via C extensions (not via
    ``threading.Thread``) won't appear in ``threading.enumerate()``,
    so their connections won't be pruned. This is acceptable —
    Voice Typer's reader threads (IPC handlers, tray, dictation
    pipeline, tests) are all ``threading.Thread`` instances.
    """
    if not db._all_read_connections:
        return
    # Build the set of alive thread idents. threading.enumerate()
    # returns Thread objects for all non-daemon threads and all
    # daemon threads created via the threading module. A thread
    # that has just exited may still appear here for a brief
    # window, but the next pruning pass will catch it.
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
            # in action (helpful for diagnosing memory issues in
            # long-running sessions). Use debug (not info) to
            # avoid spamming the log under normal churn.
            log.debug(
                "[HISTORY_DB] Pruned dead-thread read connection (thread_ident=%s); released ~2 MB page cache (AB-27).",
                ident,
            )
    db._all_read_connections = kept


def _get_conn(db: HistoryDB) -> sqlite3.Connection:
    """Backwards-compat alias for ``_get_read_conn``.

    IMPL-A: previously this returned a writable thread-local
    connection. It now returns a read-only connection. Existing
    callers that used it for schema introspection (SELECTs,
    PRAGMAs) continue to work; callers that used it for direct
    INSERTs must move to ``_submit_write`` or the public write
    methods.
    """
    return db._get_read_conn()


def _start_read_conn_prune_thread(db: HistoryDB) -> None:
    """Start a daemon thread that periodically prunes dead read
    connections from ``_all_read_connections``.

    Pre-fix, ``_prune_dead_read_connections_locked`` only fired when
    a NEW connection was created on a thread that didn't already
    have one — purely reactive. If N threads each created a read
    connection then died, and NO new thread created a connection
    afterward, the N dead-thread connections (each 2 MB page cache
    post-) sat in ``_all_read_connections`` until the next
    ``_get_read_conn`` call from a fresh thread.

    The periodic prune walks the list every
    ``_READ_CONN_PRUNE_INTERVAL_S`` (60s, module-level so tests can
    monkeypatch it) and closes connections whose owning thread has
    exited. This bounds the leak window to 60s regardless of
    new-thread read-conn churn.

    Idempotent — if a prune thread is already running, the call is
    a no-op. Tests can shorten the interval by patching
    ``history_db._READ_CONN_PRUNE_INTERVAL_S`` and then calling
    ``_stop_read_conn_prune_thread()`` / ``_start_read_conn_prune_thread()``
    to restart the worker so it picks up the new value.
    """
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
    """Stop the periodic prune daemon (called by close()).

    Signals the stop event, joins the worker thread (so it has
    fully exited before we return — prevents a race where close()
    closes a connection the prune worker is about to walk), and
    clears the ``_read_conn_prune_thread`` /
    ``_read_conn_prune_stop_event`` attributes so callers can
    observe that pruning has stopped.
    """
    evt = db._read_conn_prune_stop_event
    thread = db._read_conn_prune_thread
    if evt is not None:
        evt.set()
    if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
        thread.join(timeout=2.0)
    db._read_conn_prune_thread = None
    db._read_conn_prune_stop_event = None


def _periodic_read_conn_prune_loop(db: HistoryDB) -> None:
    """The periodic prune loop body. Runs on a daemon thread.

    Reads ``_READ_CONN_PRUNE_INTERVAL_S`` from the MODULE namespace
    (not the class) on each iteration so tests can patch
    ``history_db._READ_CONN_PRUNE_INTERVAL_S`` and have the change
    take effect without restarting the worker — although the
    existing tests restart the worker anyway for determinism.
    """
    # Lazy import so the constant tracks monkeypatches on the
    # ``history_db`` module namespace (the loop reads it on every
    # iteration; an eager top-of-module import would freeze the
    # pre-patch value).
    from voice_typer.server import history_db as _hd

    evt = db._read_conn_prune_stop_event
    if evt is None:
        return
    while not evt.is_set():
        # Re-read the interval each iteration so a test patching
        # ``history_db._READ_CONN_PRUNE_INTERVAL_S`` is honored
        # without requiring a worker restart.
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
