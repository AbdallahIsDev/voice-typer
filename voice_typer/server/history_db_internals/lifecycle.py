"""Lifecycle helpers for :class:`~voice_typer.server.history_db.HistoryDB`.

Extracted from the once-monolithic ``history_db.py`` so the public class
stays a thin wiring surface. This module owns the stateful lifecycle
stages that operate on the instance's own attributes:

- :func:`initialize_state` — attribute setup performed by ``__init__``
  before the writer thread starts.
- :func:`wait_for_writer_ready` — post-start handshake with the writer
  thread (ready wait, init-error surfacing, live-instance registration,
  read-conn prune daemon start).
- :func:`close_db` — full teardown orchestration used by ``close()``.
- :func:`close_read_connections` — read-connection teardown shared by
  the close paths.
- :func:`health_check` — writer-thread health snapshot for diagnostics.

Module constants (``_WRITER_READY_TIMEOUT``) and the module-level
``_LIVE_INSTANCES`` WeakSet are read through the ``history_db`` facade
namespace at call time (lazy ``_hd.<NAME>`` reads), so tests that
monkeypatch them on the facade keep working. Cross-calls into other
``HistoryDB`` surface (``_stop_periodic_retention``,
``_stop_read_conn_prune_thread``, ``_close_writer``) go through
``db.<method>(...)`` so instance/class-level monkeypatches keep working.
"""

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
    """Set up every mutable instance attribute before the writer starts.

    Body of :meth:`HistoryDB.__init__` (state-setup portion). The caller
    resolves ``db.db_path`` (including the legacy-DB migration) before
    calling this, then starts the writer thread afterwards.
    """
    # Lazy reads so the values track monkeypatches on the
    # ``history_db`` module namespace.
    from voice_typer.server import history_db as _hd

    # Thread-local read-only connections (one per reader thread).
    db._read_local = threading.local()
    # Track ALL read connections across threads so close() + __del__
    # can clean them up, preventing ResourceWarning on GC. Each
    # entry is a ``(thread_ident, connection)`` tuple — the
    # thread_ident lets ``_prune_dead_read_connections_locked``
    # detect when the owning thread has exited and close its
    # connection (releasing the ~20 MB SQLite page cache) instead
    # of letting it leak for the lifetime of the HistoryDB. Without
    # this pruning, IPC handler threads, tray thread, dictation
    # pipeline thread, and test threads would each accumulate a
    # 20 MB read connection that's never released until close().
    db._all_read_connections = []
    db._connections_lock = threading.Lock()
    # generation counter bumped on corruption-recovery
    # read-connection invalidation. Each thread-local read
    # connection remembers the generation it was opened at; if
    # the counter bumps (because the corrupt DB was renamed and a
    # fresh DB opened), the next ``_get_read_conn`` call closes
    # the stale conn and opens a new one on the fresh file.
    # Without this, POSIX open FDs would keep pointing at the
    # renamed (corrupt) file and readers would return stale data.
    db._read_conn_generation = 0
    # Write queue: items are (callable, future) tuples, OR
    # _BatchableInsert instances, OR the _SHUTDOWN_SENTINEL
    # to ask the writer to exit. ``future`` is None for
    # fire-and-forget writes (e.g. add_transcription).
    # PERF-5: bound the queue so a stalled writer thread can't let
    # the in-memory queue grow without limit. On queue.Full we drop
    # the oldest non-sentinel item and log a warning. See
    # ``_WRITE_QUEUE_MAXSIZE`` for the bound's rationale (~5 minutes
    # of fire-and-forget add_transcription writes at 30/s).
    db._queue = queue.Queue(maxsize=_hd._WRITE_QUEUE_MAXSIZE)
    # Signaled by the writer thread once schema init succeeds (or
    # fails). __init__ waits on this so subsequent reads see the
    # schema.
    db._writer_ready = threading.Event()
    # Set by close() to refuse new write submissions.
    db._shutdown = threading.Event()
    # If the writer thread failed during schema init, the exception
    # is stored here so __init__ can log it.
    db._init_error = None
    # re-entrancy guard for apply_retention. The periodic
    # retention scheduler spawns a daemon thread that calls
    # apply_retention on a fixed interval; if a previous run is
    # still in flight (e.g. a multi-batch VACUUM on a huge DB),
    # the next tick acquires this lock non-blocking and skips
    # rather than queueing a second concurrent sweep.
    db._retention_lock = threading.Lock()
    # stop event for the periodic retention thread. Set by
    # close() (and by re-scheduling) to ask the daemon loop to exit.
    db._retention_stop_event = None
    # handle to the periodic retention daemon thread (for
    # join-on-close).
    db._retention_thread = None
    # TTL cache for ``get_history_count``.
    db._history_count_cache = None
    db._history_count_cache_ts = 0.0
    db._history_count_cache_lock = threading.Lock()
    # TTL cache for ``get_today_stats``. See
    # ``_TODAY_STATS_CACHE_TTL_S`` for the rationale (15s TTL,
    # strict invalidation on every mutation). The cache stores a
    # COPY of the stats dict so callers can mutate the returned
    # dict without corrupting the cached value (see
    # ``test_cache_returns_independent_dict_copy``).
    db._today_stats_cache = None
    db._today_stats_cache_ts = 0.0
    db._today_stats_cache_lock = threading.Lock()
    # per-instance counter of FTS5 'rebuild' failures after
    # ``apply_retention`` / ``clear_all`` bulk deletes. Incremented
    # each time the FTS5 ``'rebuild'`` command raises a
    # ``sqlite3.Error`` — those failures leave deleted dictated
    # text recoverable from ``transcriptions_fts_data`` (GDPR
    # Art. 17 violation), so the counter is surfaced in
    # diagnostics and paired with an ``event_bus`` event so the
    # renderer can show a toast.
    db._fts5_rebuild_failures = 0
    # True when this session's startup FTS5 'rebuild' actually ran
    # (schema_meta flag NULL or '1'). A rebuild re-tokenizes from the
    # content table, so rows that were already encrypted at rest end
    # up with CIPHERTEXT tokens in the index — ``_init_encryption``
    # responds by queueing a decrypt-aware re-index (see
    # ``_reindex_encrypted_fts_step``) to restore the invariant
    # (FTS shadow tables stay plaintext-tokenized).
    db._fts5_rebuild_ran = False
    # Ascending-id watermark for ``_reindex_encrypted_fts_step`` —
    # lets the decrypt-aware FTS re-index resume across its bounded
    # batches without re-processing rows or holding their ids.
    db._fts_reindex_watermark = 0
    # At-rest-encryption status — one of "active" / "disabled" /
    # "key-unavailable" (see :meth:`encryption_status`). Set by
    # ``_init_encryption`` on the writer thread after schema init;
    # "disabled" is the pre-resolution default so a HistoryDB whose
    # writer never gets that far (init failure) reports the same
    # state as plaintext mode.
    db._encryption_status = "disabled"
    # periodic prune daemon for ``_all_read_connections``.
    # Pre-fix, ``_prune_dead_read_connections_locked`` was REACTIVE
    # — only fired when a NEW connection was created on a thread
    # that didn't already have one. If N threads each created a
    # read connection, then died, and NO new thread created a
    # connection afterward, the N dead-thread connections (each
    # 2 MB page cache post-; 20 MB pre-) sat in
    # ``_all_read_connections`` until the next ``_get_read_conn``
    # call from a fresh thread. The periodic prune walks the list
    # every 60s and closes connections whose owning thread has
    # exited, bounding the leak window to 60s regardless of new
    # read-conn churn.
    db._read_conn_prune_stop_event = None
    db._read_conn_prune_thread = None


def wait_for_writer_ready(db: HistoryDB) -> None:
    """Wait for the writer thread's schema-init handshake, then register.

    Body of :meth:`HistoryDB.__init__` (post-start portion): waits on
    ``_writer_ready`` with the facade's ``_WRITER_READY_TIMEOUT``,
    surfaces the writer's init error, registers the instance in the
    facade's ``_LIVE_INSTANCES`` WeakSet (so the test conftest can close
    leaked instances), and starts the periodic read-conn prune daemon
    (best-effort).
    """
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
    # can close leaked instances after each test (prevents the daemon
    # writer thread from accumulating across the full pytest run and
    # crashing the process on Windows via native thread-limit exhaustion).
    _hd._LIVE_INSTANCES.add(db)
    # start the periodic prune daemon (best-effort —
    # failures are logged + swallowed so a healthy DB never fails
    # to construct just because the prune thread couldn't start).
    with contextlib.suppress(Exception):
        db._start_periodic_read_conn_prune()


def close_read_connections(db: HistoryDB) -> None:
    """Close every tracked read connection (current thread + all threads).

    Shared teardown used by :func:`close_db` and its idempotent branch.
    Each entry in ``_all_read_connections`` is a ``(thread_ident,
    connection)`` tuple; we unpack to close the connection regardless of
    which thread originally owned it (close() can be called from any
    thread).
    """
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
    """Non-blocking read-connection teardown for ``HistoryDB.__del__``.

    Same effect as :func:`close_read_connections` but never blocks: the
    connections lock is acquired with ``blocking=False`` and the sweep
    is skipped if another thread holds it (a re-entrant GC during
    ``_get_read_conn`` could otherwise deadlock). Closes the current
    thread's thread-local connection first.
    """
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
    """Shut down the writer thread and close all connections.

    Body of :meth:`HistoryDB.close`. IMPL-A: sends the shutdown
    sentinel, waits (with timeout) for the writer to drain remaining
    items and exit, then closes all read connections. Idempotent — safe
    to call multiple times. Also signals + joins the periodic retention
    thread (if scheduled) and the periodic read-conn prune daemon so
    close() fully quiesces the HistoryDB's daemon threads.
    """
    # stop the periodic retention thread BEFORE setting
    # _shutdown so its inner loop sees a clean stop_event signal
    # and exits without trying to call apply_retention (which
    # would no-op on a shutdown DB but would still log noise).
    db._stop_periodic_retention()
    # Stop the periodic read-conn prune daemon before tearing down
    # connections — otherwise the worker could walk _all_read_connections
    # mid-tear-down and trip over a half-closed connection. Also
    # clears the thread / event attributes so callers observing
    # ``_read_conn_prune_thread is None`` after ``close()`` see the
    # quiesced state.
    db._stop_read_conn_prune_thread()
    if db._shutdown.is_set():
        # Already closed — just make sure read conns are gone.
        close_read_connections(db)
        return
    db._shutdown.set()
    # Writer-teardown (best-effort wal_checkpoint(TRUNCATE), shutdown
    # sentinel enqueue with drop-oldest loop, writer-thread join) is
    # delegated to ``history_db_internals.writer._close_writer`` so the
    # facade method stays focused on lifecycle orchestration. The
    # delegated helper reads ``_SHUTDOWN_SENTINEL`` /
    # ``_WRITE_QUEUE_MAXSIZE`` / ``_WRITER_JOIN_TIMEOUT`` /
    # ``HistoryDBError`` from the facade module's namespace (lazy import
    # inside the helper so monkeypatches on the facade keep working).
    db._close_writer()
    close_read_connections(db)


def health_check(db: HistoryDB) -> dict:
    """Return a health status dict for diagnostics.

    Body of :meth:`HistoryDB.health_check`. Returns
    ``{"ok": bool, "error": str | None}``:

    - ``ok`` is ``True`` only if the writer thread is alive AND
      ``_init_error`` is ``None`` AND ``_writer_ready`` is set.
    - ``error`` is a human-readable string describing the failure, or
      ``None`` if healthy.

    The ``_writer_ready`` check is critical: ``__init__`` returns after
    at most ``_WRITER_READY_TIMEOUT`` even if the writer thread hasn't
    finished schema init — surfacing "still initializing" lets callers
    back off or show a "warming up" message instead of treating the DB
    as healthy.
    """
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
