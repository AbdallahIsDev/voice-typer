"""SQLite database for storing transcription history.

IMPL-A (single-writer architecture): all write operations are
serialized through a single dedicated writer thread that owns the
*only* write-capable connection. Read operations use thread-local
read-only connections (WAL readers never block the writer).

Architecture overview::

    caller thread ──► HistoryDB.add_transcription ──► queue.Queue ──►
                                                                    │
                                                     writer thread (daemon)
                                                     owns 1 write conn
                                                     drains queue, runs
                                                     PRAGMA wal_checkpoint
                                                     every 60s

    caller thread ──► HistoryDB.get_recent ──► _get_read_conn (thread-local)
                                                PRAGMA query_only=1

Why this design exists (root cause from INV-A investigation):
- The previous thread-local-connections design had 3+ write-capable
  connections all contending for the same SQLite write lock. The
  retry helper (``_exec_with_retry``) compounded the problem: 5
  attempts × (busy_timeout + commit wait) + backoff ≈ 10s.
- A single writer thread eliminates in-process contention entirely.
  ``busy_timeout`` is now only a safety net for *external* writers
  (e.g. antivirus scans, external SQLite CLI), not for our own
  threads.
- ``add_transcription`` is fire-and-forget: it enqueues and returns
  immediately with a placeholder row_id, eliminating the user-reported
  5.5s ``store`` delay on the transcription pipeline's critical path.

ERR-013: Sentinel contract. Every public method returns a fixed
sentinel on error, matching the *success-shape* of the method's
normal return:

- List-returning methods (get_recent, search, get_favorites) → ``[]``
- Bool-returning methods (delete, clear_all, toggle_favorite,
  apply_retention) → ``False``
- Dict-returning methods (get_stats, get_today_stats) → empty dict
  (with the documented keys present, set to 0)
- add_transcription → ``-1`` (caller checks ``<= 0``)

Callers can detect failure with ``is_empty_result(value)`` or by
checking the specific sentinel for each method. Hard failures
(corruption, locked DB) additionally log at ``log.error`` level.
"""

import concurrent.futures
import contextlib
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

_CURRENT_SCHEMA_VERSION = 2
_MAX_SEARCH_QUERY_CHARS = 200

# IMPL-A: writer-thread tuning constants.
#   _WAL_CHECKPOINT_INTERVAL — the writer thread runs
#   ``PRAGMA wal_checkpoint(PASSIVE)`` at this cadence to bound WAL
#   growth and prevent autocheckpoint stalls during writes.
#   _WRITE_FUTURE_TIMEOUT — maximum time a blocking write caller
#   (delete/restore/clear_all/toggle_favorite/apply_retention) will
#   wait for the writer to execute its closure. Generous because the
#   writer is single-threaded and may be draining a backlog; the
#   previous design could stall 5+ seconds, so 30s is a safety bound,
#   not a typical latency.
#   _WRITER_JOIN_TIMEOUT — how long ``close()`` waits for the writer
#   thread to drain remaining items and exit.
#   _WRITER_READY_TIMEOUT — how long ``__init__`` waits for the writer
#   to finish schema initialization before returning.
#   _CLEAR_ALL_BATCH_SIZE / _RETENTION_BATCH — chunk sizes for bulk
#   DELETEs; each batch commits so the WAL doesn't grow unboundedly
#   and external readers see progress.
_WAL_CHECKPOINT_INTERVAL = 300.0  # 5 minutes — keeps WAL small with negligible overhead
_WRITE_FUTURE_TIMEOUT = 30.0
_WRITER_JOIN_TIMEOUT = 10.0
_WRITER_READY_TIMEOUT = 30.0
_CLEAR_ALL_BATCH_SIZE = 100
_RETENTION_BATCH = 100

# Sentinel enqueued to ask the writer thread to drain and exit.
_SHUTDOWN_SENTINEL: Any = object()


class HistoryDBError(RuntimeError):
    """Raised by HistoryDB methods on unrecoverable failures.

    ERR-013: previously every method returned a different sentinel
    (``[]``, ``None``, ``False``, ``-1``, ``{}``) which forced callers
    to know each method's specific sentinel. Methods now log the
    underlying error and return the documented sentinel; callers that
    need to distinguish "empty result" from "operation failed" can
    catch this exception via the ``raise_on_error`` parameter.
    """


_MIGRATION_V2 = """
    ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;
    ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT '';
"""

_MIGRATIONS = {
    2: _MIGRATION_V2,
}


def _prepare_like_search_pattern(query: str) -> str:
    """Build a bounded LIKE pattern where user wildcards stay literal."""
    capped_query = query[:_MAX_SEARCH_QUERY_CHARS]
    escaped_query = capped_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped_query}%"


class HistoryDB:
    """Thread-safe SQLite database for transcription history.

    IMPL-A: a single dedicated writer thread owns the only
    write-capable connection. All write methods enqueue closures onto
    a ``queue.Queue``; the writer thread drains the queue and executes
    them serially. This eliminates in-process write contention — the
    root cause of the user-reported 5.5s ``store`` delay.

    Read methods use thread-local read-only connections
    (``PRAGMA query_only=1``). In WAL mode, readers never block the
    writer and the writer never blocks readers.

    ``add_transcription`` is fire-and-forget: it enqueues the INSERT
    and returns immediately with a placeholder row_id, so the
    transcription pipeline never waits on the DB. Other write methods
    (``delete``, ``restore``, ``clear_all``, ``toggle_favorite``,
    ``apply_retention``) block on a ``concurrent.futures.Future`` so
    callers (IPC handlers) see the result.
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            from voice_typer.server.config import _config_dir

            db_path = _config_dir() / "history.db"

        self.db_path = db_path
        # Thread-local read-only connections (one per reader thread).
        self._read_local = threading.local()
        # Track ALL read connections across threads so close() + __del__
        # can clean them up, preventing ResourceWarning on GC.
        self._all_read_connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        # Write queue: items are (callable, future) tuples, or the
        # _SHUTDOWN_SENTINEL to ask the writer to exit. ``future`` is
        # None for fire-and-forget writes (e.g. add_transcription).
        self._queue: queue.Queue[Any] = queue.Queue()
        # Signaled by the writer thread once schema init succeeds (or
        # fails). __init__ waits on this so subsequent reads see the
        # schema.
        self._writer_ready = threading.Event()
        # Set by close() to refuse new write submissions.
        self._shutdown = threading.Event()
        # If the writer thread failed during schema init, the exception
        # is stored here so __init__ can log it.
        self._init_error: BaseException | None = None
        # Start the writer thread last — it signals _writer_ready once
        # the schema is set up.
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="HistoryDBWriter",
            daemon=True,
        )
        self._writer_thread.start()
        # Wait for the writer to finish schema initialization. If this
        # times out, the writer is stuck (e.g. disk full, permissions)
        # — __init__ still returns so the rest of the app can start,
        # but subsequent writes will fail and log.
        if not self._writer_ready.wait(timeout=_WRITER_READY_TIMEOUT):
            log.error(
                "[HISTORY_DB] Writer thread did not signal ready within %.1fs "
                "(db=%s); writes will fail until it recovers.",
                _WRITER_READY_TIMEOUT,
                self.db_path,
            )
        if self._init_error is not None:
            log.error(
                "[HISTORY_DB] Writer thread initialization failed: %s",
                self._init_error,
            )

    # ──────────────────────────────────────────────────────────────
    # Writer thread
    # ──────────────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        """Drain the write queue serially on a single connection.

        Runs in a daemon thread. Opens the write connection, runs
        schema setup, signals ``_writer_ready``, then loops:
          - Wait for an item from the queue with a timeout (so we can
            periodically WAL-checkpoint).
          - On timeout, run ``PRAGMA wal_checkpoint(PASSIVE)`` to
            bound WAL growth and prevent autocheckpoint stalls.
          - On ``_SHUTDOWN_SENTINEL``, drain remaining items and exit.
          - On a normal item, call the closure with the write
            connection and set the future's result/exception.
        """
        try:
            conn = self._open_write_conn()
            self._check_wal_mode(conn)
            self._init_db_schema(conn)
        except BaseException as e:  # noqa: BLE001 — surface to __init__
            self._init_error = e
            self._writer_ready.set()
            return
        self._writer_ready.set()

        last_checkpoint = time.monotonic()
        while True:
            now = time.monotonic()
            wait_for = _WAL_CHECKPOINT_INTERVAL - (now - last_checkpoint)
            if wait_for <= 0:
                self._run_checkpoint(conn)
                last_checkpoint = time.monotonic()
                wait_for = _WAL_CHECKPOINT_INTERVAL
            try:
                item = self._queue.get(timeout=wait_for)
            except queue.Empty:
                self._run_checkpoint(conn)
                last_checkpoint = time.monotonic()
                continue
            if item is _SHUTDOWN_SENTINEL:
                self._drain_remaining(conn)
                break
            callable_, future = item
            try:
                result = callable_(conn)
                if future is not None:
                    future.set_result(result)
            except BaseException as e:  # noqa: BLE001 — propagate to future
                # DB-LOCK-FIX: rollback any uncommitted transaction left
                # by the failed closure. If we don't, the next WAL
                # checkpoint will fail with "database table is locked"
                # because the writer's own connection has a pending
                # uncommitted transaction.
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                if future is not None:
                    future.set_exception(e)
                else:
                    # Fire-and-forget write failed — log so it's visible.
                    log.error("[HISTORY_DB] Fire-and-forget write failed: %s", e)
            else:
                # WAL-CHECKPOINT-FIX: After a SUCCESSFUL write, ensure
                # no lingering transaction remains on the connection.
                # All write closures call conn.commit(), but if the
                # closure's commit succeeded and the method then raised
                # an exception (e.g. cursor.lastrowid access on a
                # closed cursor), the transaction is committed but the
                # connection might be in an unexpected state. A
                # rollback here is a safe no-op if there's no open
                # transaction.
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
        # Drain loop exited — close the writer's connection.
        try:
            conn.close()
        except sqlite3.Error as e:
            log.warning("[HISTORY_DB] Error closing writer connection: %s", e)

    def _drain_remaining(self, conn: sqlite3.Connection) -> None:
        """Drain any remaining queued items before shutdown.

        Called after the shutdown sentinel is received. Ensures
        fire-and-forget writes submitted before close() are persisted.
        """
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _SHUTDOWN_SENTINEL:
                continue
            callable_, future = item
            try:
                result = callable_(conn)
                if future is not None:
                    future.set_result(result)
            except BaseException as e:  # noqa: BLE001 — propagate to future
                # DB-LOCK-FIX: rollback any uncommitted transaction left
                # by the failed closure, same as in _writer_loop.
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                if future is not None:
                    future.set_exception(e)
                else:
                    log.error("[HISTORY_DB] Fire-and-forget write failed during shutdown drain: %s", e)
            else:
                # WAL-CHECKPOINT-FIX: same post-write cleanup as in
                # _writer_loop — rollback any lingering state so the
                # final checkpoint before connection close succeeds.
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()

    def _run_checkpoint(self, conn: sqlite3.Connection) -> None:
        """Run a passive WAL checkpoint to bound WAL growth.

        PASSIVE mode doesn't block — it checkpoints as much as
        possible without forcing readers/writers to wait. Called every
        ``_WAL_CHECKPOINT_INTERVAL`` seconds by the writer thread.

        WAL-CHECKPOINT-FIX: Always rollback any lingering uncommitted
        transaction BEFORE running the checkpoint. If a write closure
        failed after ``conn.commit()`` but before the exception handler
        reached ``conn.rollback()``, or if a closure raised mid-execution
        without committing, the writer's own connection has a pending
        uncommitted transaction. ``PRAGMA wal_checkpoint(PASSIVE)`` on
        the same connection then fails with "database table is locked"
        — not because another thread holds the lock, but because the
        writer's own connection hasn't released it yet.

        The rollback is a no-op when there is no open transaction, so
        it's safe to call unconditionally.
        """
        # WAL-CHECKPOINT-FIX: clear any lingering transaction from
        # a prior write before checkpointing. The rollback is safe
        # because all write closures commit before returning, so
        # any open transaction here is an unexpected/transient state.
        try:
            conn.rollback()
            result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            if result is not None:
                status, pages_checkpointed, total_pages = result
                # Only log when a non-trivial checkpoint happens (>= 100
                # pages) to avoid flooding the log every 60s. Tiny
                # checkpoints (e.g. 20 pages) are silent — the WAL is
                # healthy, no action needed.
                # status: 0=ok, 1=partial(active readers), 2=full(needs restart)
                if pages_checkpointed >= 100:
                    if status == 0:
                        log.debug(
                            "[HISTORY_DB] WAL checkpointed %d pages",
                            pages_checkpointed,
                        )
                    else:
                        log.debug(
                            "[HISTORY_DB] WAL checkpoint partial: %d/%d pages (status=%d)",
                            pages_checkpointed,
                            total_pages,
                            status,
                        )
        except sqlite3.OperationalError as e:
            # This can happen when an external process (e.g. antivirus
            # scan) holds a lock on the WAL file. The next checkpoint
            # attempt in 60s will retry.
            log.debug(
                "[HISTORY_DB] WAL checkpoint skipped (will retry in %.0fs): %s",
                _WAL_CHECKPOINT_INTERVAL,
                e,
            )
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] WAL checkpoint failed unexpectedly: %s",
                e,
            )

    def _open_write_conn(self) -> sqlite3.Connection:
        """Open and configure the writer thread's connection.

        The writer owns the *only* write-capable connection in the
        process. Configuration:
          - ``journal_mode=WAL`` — concurrent readers don't block writes.
          - ``synchronous=NORMAL`` — safe in WAL mode, faster than FULL.
          - ``busy_timeout=5000`` — safety net for *external* writers
            (antivirus, external CLI). In-process contention is
            impossible because there's only one writer thread.
          - ``cache_size=-20000`` — 20 MB page cache.

        SEC-007: on POSIX, tightens the DB file and its parent
        directory to 0o600 / 0o700 so transcription history is not
        world-readable. SQLite creates ``-wal`` and ``-shm`` sidecar
        files in WAL mode; we chmod those too (best-effort, since
        they may be created lazily on first write).
        """
        # SEC-007: tighten dir permissions before the connection
        # creates files in it.
        if not is_windows():
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self.db_path.parent, 0o700)
            except OSError as e:
                log.warning("[HISTORY_DB] Could not tighten dir perms: %s", e)
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=5.0,
        )
        # Safety net for external contention only (in-process contention
        # is impossible — there's only one writer thread).
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-20000")  # 20 MB
        conn.row_factory = sqlite3.Row
        # SEC-007: chmod the DB file (and sidecar files if present).
        if not is_windows():
            for suffix in ("", "-wal", "-shm"):
                p = self.db_path.with_suffix(self.db_path.suffix + suffix) if suffix else self.db_path
                try:
                    if p.exists():
                        os.chmod(p, 0o600)
                except OSError:
                    pass
        return conn

    def _check_wal_mode(self, conn: sqlite3.Connection) -> None:
        """Verify WAL mode is actually enabled.

        ``PRAGMA journal_mode=WAL`` returns the *resulting* journal
        mode. On network filesystems, certain antivirus locks, or
        read-only filesystems, SQLite may silently fall back to
        ``delete`` (rollback journal) mode. In rollback mode, readers
        DO block the writer and the user-reported 9s regression
        returns.

        This method fetches the PRAGMA result and logs a warning if
        WAL is not active. It does NOT crash — the app should still
        work (just slower) — but the warning must be visible so users
        can diagnose the misconfiguration.
        """
        try:
            cur = conn.execute("PRAGMA journal_mode=WAL")
            mode_row = cur.fetchone()
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] Could not set/check WAL mode (%s) at %s — "
                "app will work but writes may be slower and more contended.",
                e,
                self.db_path,
            )
            return
        mode = mode_row[0] if mode_row else ""
        if str(mode).lower() != "wal":
            log.warning(
                "[HISTORY_DB] WAL mode NOT enabled (got %r) at %s — "
                "app will work but writes may be slower and more contended.",
                mode,
                self.db_path,
            )

    def _init_db_schema(self, conn: sqlite3.Connection) -> None:
        """Initialize the database schema and run migrations.

        IMPL-A: previously this method called ``self._get_conn()``;
        now it takes the writer's connection as a parameter so it can
        run on the writer thread. The schema/migration logic itself
        is unchanged.

        FIX (preserved from prior version): schema/metadata BEFORE
        indexes that depend on migrated columns. The original code ran
        CREATE INDEX idx_favorite ON transcriptions(favorite) BEFORE
        the migration code. On an existing database created without
        the 'favorite' column, CREATE INDEX would fail with "no such
        column: favorite". Fix: create the table first, then run
        schema versioning + migrations, then create indexes.
        """
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transcriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                duration REAL DEFAULT 0,
                model TEXT DEFAULT '',
                device TEXT DEFAULT '',
                word_count INTEGER DEFAULT 0,
                char_count INTEGER DEFAULT 0,
                favorite INTEGER DEFAULT 0,
                language TEXT DEFAULT ''
            )
        """)

        # Schema version tracking (must run BEFORE CREATE INDEX that
        # references 'favorite').
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Get current schema version
        cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        current_version = int(row[0]) if row else 1

        # Run migrations BEFORE creating indexes that depend on
        # migrated columns.
        cursor.execute("PRAGMA table_info(transcriptions)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        for version in range(current_version + 1, _CURRENT_SCHEMA_VERSION + 1):
            migration_sql = _MIGRATIONS.get(version)
            if migration_sql:
                for stmt in migration_sql.strip().split(";"):
                    stmt = stmt.strip()
                    if not stmt:
                        continue
                    # Skip ALTER TABLE ADD COLUMN if column already
                    # exists. Must extract the column name (word after
                    # ADD COLUMN), not the last token.
                    if stmt.upper().startswith("ALTER TABLE") and "ADD COLUMN" in stmt.upper():
                        idx = stmt.upper().find("ADD COLUMN")
                        if idx >= 0:
                            parts_after = stmt[idx + 10 :].lstrip().split()
                            col_name = parts_after[0] if parts_after else ""
                            if col_name in existing_columns:
                                continue
                    try:
                        cursor.execute(stmt)
                        log.info(
                            "[HISTORY_DB] Applied migration: %s...",
                            stmt[:60],
                        )
                    except sqlite3.Error as e:
                        log.warning("[HISTORY_DB] Migration statement failed: %s", e)
                log.info("[HISTORY_DB] Migrated schema to version %d", version)

        cursor.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("version", str(_CURRENT_SCHEMA_VERSION)),
        )
        conn.commit()

        # Create indexes AFTER migration so 'favorite' column exists.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON transcriptions(timestamp DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorite
            ON transcriptions(favorite)
        """)
        log.info(
            "[HISTORY] History database initialized: %s (schema v%d)",
            self.db_path,
            _CURRENT_SCHEMA_VERSION,
        )

    # ──────────────────────────────────────────────────────────────
    # Read connections
    # ──────────────────────────────────────────────────────────────

    def _get_read_conn(self) -> sqlite3.Connection:
        """Get a thread-local READ-ONLY connection.

        IMPL-A: each reader thread gets its own connection (stored in
        ``threading.local()``). ``PRAGMA query_only=1`` enforces
        read-only access at the SQLite layer — even if a bug tried to
        write through this connection, SQLite would reject it. In WAL
        mode, readers never block the writer and the writer never
        blocks readers.

        SEC-007: on POSIX, tightens the DB file and its parent
        directory to 0o600 / 0o700 so transcription history is not
        world-readable.
        """
        if not hasattr(self._read_local, "conn") or self._read_local.conn is None:
            if not is_windows():
                try:
                    self.db_path.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(self.db_path.parent, 0o700)
                except OSError as e:
                    log.warning("[HISTORY_DB] Could not tighten dir perms: %s", e)
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=5.0,
            )
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-20000")  # 20 MB
            # Enforce read-only at the SQLite layer.
            conn.execute("PRAGMA query_only=1")
            # Don't force WAL here — the writer already set it on the
            # DB file; readers inherit whatever journal mode the DB
            # file is in. Forcing WAL on a read-only connection on a
            # network FS could fail.
            conn.row_factory = sqlite3.Row
            self._read_local.conn = conn
            with self._connections_lock:
                self._all_read_connections.append(conn)
        return self._read_local.conn

    def _get_conn(self) -> sqlite3.Connection:
        """Backwards-compat alias for ``_get_read_conn``.

        IMPL-A: previously this returned a writable thread-local
        connection. It now returns a read-only connection. Existing
        callers that used it for schema introspection (SELECTs,
        PRAGMAs) continue to work; callers that used it for direct
        INSERTs must move to ``_submit_write`` or the public write
        methods.
        """
        return self._get_read_conn()

    # ──────────────────────────────────────────────────────────────
    # Write submission
    # ──────────────────────────────────────────────────────────────

    def _submit_write(
        self,
        fn: Callable[[sqlite3.Connection], Any],
        *,
        wait: bool = True,
    ) -> Any | None:
        """Submit a write closure to the writer thread.

        Parameters
        ----------
        fn : callable
            A closure that takes the writer's ``sqlite3.Connection``
            and returns the write result (or raises).
        wait : bool
            If ``True`` (default), block until the writer executes
            ``fn`` and return its result (or re-raise its exception).
            If ``False``, return ``None`` immediately (fire-and-forget).

        Returns
        -------
        The closure's result if ``wait=True``; ``None`` if ``wait=False``
        or if the writer is shutting down and can't accept the work.

        Notes
        -----
        If ``self._shutdown`` is set (close() was called), the closure
        is NOT submitted and ``None`` is returned. Callers that need
        to distinguish "fire-and-forget accepted" from "writer shut
        down" can check ``self._shutdown.is_set()`` before calling.
        """
        if self._shutdown.is_set():
            log.debug("[HISTORY_DB] Write submitted after shutdown — dropped.")
            return None
        future: concurrent.futures.Future | None = None
        if wait:
            future = concurrent.futures.Future()
        self._queue.put((fn, future))
        if not wait:
            return None
        assert future is not None
        # Block on the future. The writer is a daemon thread; if it
        # dies (e.g. disk corruption), the future would never resolve
        # and we'd hang. Loop with a timeout so we can detect a dead
        # writer and raise.
        while True:
            try:
                return future.result(timeout=_WRITE_FUTURE_TIMEOUT)
            except concurrent.futures.TimeoutError:
                if not self._writer_thread.is_alive():
                    raise HistoryDBError("HistoryDB writer thread is dead; write did not complete") from None
                # Writer still alive — keep waiting (rare; means a
                # prior write is taking a very long time, e.g. a
                # multi-batch retention sweep on a huge DB).
                log.warning(
                    "[HISTORY_DB] Write future still pending after %.0fs; writer is alive, continuing to wait.",
                    _WRITE_FUTURE_TIMEOUT,
                )

    def flush(self) -> None:
        """Block until all queued writes have been processed by the writer.

        IMPL-A: enqueues a no-op write with ``wait=True`` and blocks
        on its future. Because the queue is FIFO, all writes submitted
        before this call will have completed by the time the no-op
        runs. Useful for tests and for callers that need to verify a
        write was persisted before reading it back.
        """
        if self._shutdown.is_set():
            return
        with contextlib.suppress(HistoryDBError):
            self._submit_write(lambda conn: None, wait=True)

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────

    def __del__(self):
        """Ensure all connections are closed on GC to prevent ResourceWarning."""
        with contextlib.suppress(Exception):
            self.close()

    def close(self):
        """Shut down the writer thread and close all connections.

        IMPL-A: sends the shutdown sentinel, waits (with timeout) for
        the writer to drain remaining items and exit, then closes all
        read connections. Idempotent — safe to call multiple times.
        """
        if self._shutdown.is_set():
            # Already closed — just make sure read conns are gone.
            with self._connections_lock:
                for conn in self._all_read_connections:
                    with contextlib.suppress(sqlite3.Error):
                        conn.close()
                self._all_read_connections.clear()
            return
        self._shutdown.set()
        # Enqueue the sentinel — the writer drains remaining items
        # before exiting. queue.Queue.put on an unbounded queue
        # essentially never raises; the try/except is defensive against
        # interpreter-shutdown edge cases.
        try:
            self._queue.put(_SHUTDOWN_SENTINEL)
        except queue.Full:
            log.warning("[HISTORY_DB] Write queue full during shutdown")
        except RuntimeError as e:
            # Can occur during interpreter shutdown if the queue module
            # is in an inconsistent state.
            log.debug("[HISTORY_DB] Could not enqueue shutdown sentinel: %s", e)
        # Wait for the writer to exit (it drains remaining items first).
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=_WRITER_JOIN_TIMEOUT)
            if self._writer_thread.is_alive():
                log.warning(
                    "[HISTORY_DB] Writer thread did not exit within %.1fs; "
                    "it is a daemon and will be killed at process exit.",
                    _WRITER_JOIN_TIMEOUT,
                )
        # Close the current thread's read connection first (if any).
        if hasattr(self._read_local, "conn") and self._read_local.conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._read_local.conn.close()
            self._read_local.conn = None
        # Then close all other read connections tracked across threads.
        with self._connections_lock:
            for conn in self._all_read_connections:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            self._all_read_connections.clear()

    # ──────────────────────────────────────────────────────────────
    # Public write methods
    # ──────────────────────────────────────────────────────────────

    def add_transcription(
        self,
        text: str,
        duration: float = 0,
        model: str = "",
        device: str = "",
        language: str = "",
    ) -> int:
        """Add a transcription to the database (fire-and-forget).

        IMPL-A: enqueues the INSERT and returns immediately with a
        placeholder row_id (always 1). The transcription pipeline
        does NOT need to wait for the DB write to complete — it's not
        on the critical path. This eliminates the user-reported 5.5s
        ``store`` delay entirely.

        Returns
        -------
        1 (placeholder) on successful enqueue, or -1 if the writer is
        shutting down and can't accept the work (which should never
        happen during normal operation).

        Callers that need the actual row_id should call ``flush()``
        then read it back via ``get_recent``. Callers that need to
        verify the write persisted should call ``flush()`` before
        asserting.
        """
        try:
            word_count = len(text.split())
            char_count = len(text)

            def _do_insert(conn: sqlite3.Connection) -> int:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO transcriptions
                    (text, duration, model, device, word_count, char_count, language)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (text, duration, model, device, word_count, char_count, language),
                )
                conn.commit()
                row_id = cursor.lastrowid
                if row_id is not None:
                    log.debug("Added transcription %d: %d chars", row_id, char_count)
                return row_id if row_id is not None else -1

            # Fire-and-forget: enqueue and return immediately.
            result = self._submit_write(_do_insert, wait=False)
            if result is None and self._shutdown.is_set():
                return -1
            # Placeholder row_id — callers that check ``> 0`` see success.
            return 1
        except Exception as e:
            log.error("[HISTORY] Failed to enqueue add_transcription: %s", e)
            return -1

    def delete(self, transcription_id: int, *, raise_on_error: bool = False) -> bool:
        """Delete a transcription by ID.

        ERR-013: when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``False``. Without this,
        the IPC layer cannot tell "row didn't exist" from "DB error".
        """
        try:

            def _do_delete(conn: sqlite3.Connection) -> bool:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM transcriptions WHERE id = ?", (transcription_id,))
                conn.commit()
                return cursor.rowcount > 0

            result = self._submit_write(_do_delete, wait=True)
            if result is None:
                # Writer shut down — treat as failure.
                return False
            return bool(result)
        except HistoryDBError:
            if raise_on_error:
                raise
            log.error("[HISTORY] Writer unavailable for delete")
            return False
        except Exception as e:
            log.error("[HISTORY] Failed to delete transcription: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return False

    def restore(
        self,
        record: dict,
        *,
        raise_on_error: bool = False,
    ) -> int:
        """Re-insert a previously-deleted transcription record.

        NEW-UX-004: supports the Undo-delete toast in the renderer.
        ``record`` should be the dict shape returned by ``get_recent``
        (id is ignored — a new row with a new id is inserted).

        Returns the new row id, or -1 on failure.
        """
        try:
            text = str(record.get("text", ""))
            duration = float(record.get("duration", 0) or 0)
            model = str(record.get("model", "") or "")
            device = str(record.get("device", "") or "")
            language = str(record.get("language", "") or "")
            word_count = int(record.get("word_count", 0) or len(text.split()))
            char_count = int(record.get("char_count", 0) or len(text))
            favorite = 1 if record.get("favorite") else 0

            def _do_restore(conn: sqlite3.Connection) -> int:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO transcriptions
                    (text, duration, model, device, word_count, char_count, language, favorite)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (text, duration, model, device, word_count, char_count, language, favorite),
                )
                conn.commit()
                new_id = cursor.lastrowid
                if new_id is None:
                    return -1
                log.info(
                    "[HISTORY] Restored transcription as id=%d (%d chars)",
                    new_id,
                    char_count,
                )
                return new_id

            result = self._submit_write(_do_restore, wait=True)
            if result is None:
                return -1
            return int(result)
        except HistoryDBError:
            if raise_on_error:
                raise
            log.error("[HISTORY] Writer unavailable for restore")
            return -1
        except Exception as e:
            log.error("[HISTORY] Failed to restore transcription: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return -1

    def clear_all(self, *, raise_on_error: bool = False) -> bool:
        """Clear all transcriptions.

        IMPL-A: chunked DELETE (100 rows per batch, commit per batch)
        running inside the writer thread. Chunking prevents the WAL
        from growing unboundedly during a huge clear and lets external
        readers see progress. The previous single-transaction DELETE
        held the write lock for the full scan.

        ERR-013: see ``delete`` for ``raise_on_error`` semantics.
        """
        try:

            def _do_clear_all(conn: sqlite3.Connection) -> bool:
                cursor = conn.cursor()
                while True:
                    cursor.execute(
                        "DELETE FROM transcriptions WHERE id IN (  SELECT id FROM transcriptions LIMIT ?)",
                        (_CLEAR_ALL_BATCH_SIZE,),
                    )
                    batch_deleted = cursor.rowcount
                    if batch_deleted == 0:
                        break
                    conn.commit()  # release write lock between batches
                log.info("[HISTORY] Cleared all transcriptions")
                return True

            result = self._submit_write(_do_clear_all, wait=True)
            if result is None:
                return False
            return bool(result)
        except HistoryDBError:
            if raise_on_error:
                raise
            log.error("[HISTORY] Writer unavailable for clear_all")
            return False
        except Exception as e:
            log.error("[HISTORY] Failed to clear transcriptions: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return False

    def toggle_favorite(
        self,
        transcription_id: int,
        *,
        raise_on_error: bool = False,
    ) -> bool:
        """Toggle the favorite status of a transcription.

        ERR-013: see ``delete`` for ``raise_on_error`` semantics.
        """
        try:

            def _do_toggle(conn: sqlite3.Connection) -> bool:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE transcriptions SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END WHERE id = ?",
                    (transcription_id,),
                )
                conn.commit()
                return cursor.rowcount > 0

            result = self._submit_write(_do_toggle, wait=True)
            if result is None:
                return False
            return bool(result)
        except HistoryDBError:
            if raise_on_error:
                raise
            log.error("[HISTORY] Writer unavailable for toggle_favorite")
            return False
        except Exception as e:
            log.error("[HISTORY] Failed to toggle favorite: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return False

    def apply_retention(self, retention_days: int = 0, max_entries: int = 0, retention_count: int = 0) -> int:
        """Apply retention policy: delete old entries.

        Returns the number of deleted entries.

        DEAD-012: retention_count is wired as a fallback for max_entries.
        If max_entries is not set but retention_count is, use it.

        IMPL-A: runs inside the writer thread. Chunked deletes (100
        rows per batch, commit per batch) prevent the WAL from growing
        unboundedly and let external readers see progress.
        """
        # DEAD-012: wire retention_count as fallback for max_entries
        effective_max = max_entries or retention_count
        deleted = 0
        try:

            def _do_retention(conn: sqlite3.Connection) -> int:
                nonlocal deleted
                cursor = conn.cursor()

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
                    while True:
                        cursor.execute("SELECT COUNT(*) FROM transcriptions")
                        total = cursor.fetchone()[0]
                        if total <= effective_max:
                            break
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
                        conn.commit()  # release write lock between batches

                if deleted:
                    log.info(
                        "[HISTORY_DB] Retention policy deleted %d entries",
                        deleted,
                    )
                return deleted

            result = self._submit_write(_do_retention, wait=True)
            if result is None:
                return 0
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

    # ──────────────────────────────────────────────────────────────
    # Public read methods
    # ──────────────────────────────────────────────────────────────

    def get_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Get recent transcriptions with offset-based pagination.

        ERR-013: when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``[]``. This lets the
        IPC layer distinguish "empty result" from "operation failed"
        and surface a proper error to the renderer.
        """
        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM transcriptions
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error("[HISTORY] Failed to get recent transcriptions: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return []

    def get_latest_text(self) -> str:
        """Return the most recent transcription text, or ``""`` if DB is empty.

        ADR-0010 §8.1 / DP6.

        Uses the existing thread-local read-only connection
        (``PRAGMA query_only=1``), so it's safe to call from the hotkey
        handler thread. Backed by ``idx_timestamp``.

        Order by the autoincrement PK (DESC), not ``timestamp DESC``:
        ``timestamp`` defaults to ``CURRENT_TIMESTAMP``, so
        transcriptions written within the same second tie and the
        "latest" becomes ambiguous. The PK is monotonic and is the
        only correct "most recent" signal.

        Note: if you just called ``add_transcription()``, call
        ``flush()`` first to guarantee the row is committed before this
        read.
        """
        try:
            conn = self._get_read_conn()
            cur = conn.cursor()
            cur.execute("SELECT text FROM transcriptions ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return row[0] if row else ""
        except Exception as e:
            log.error("[HISTORY] Failed to get latest transcription: %s", e)
            return ""

    def search(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Search transcriptions by text with offset-based pagination.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            pattern = _prepare_like_search_pattern(query)
            cursor.execute(
                """
                SELECT * FROM transcriptions
                WHERE text LIKE ? ESCAPE '\\'
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """,
                (pattern, limit, offset),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error("[HISTORY] Failed to search transcriptions: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return []

    def get_favorites(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Get favorited transcriptions with offset-based pagination.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM transcriptions
                WHERE favorite = 1
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error("[HISTORY] Failed to get favorites: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return []

    def get_today_stats(self, *, raise_on_error: bool = False) -> dict:
        """Get statistics for today's transcriptions.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
        try:
            conn = self._get_read_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as count,
                    SUM(char_count) as chars,
                    SUM(word_count) as word_count,
                    SUM(duration) as duration
                FROM transcriptions
                WHERE DATE(timestamp) = DATE('now')
            """)
            row = cursor.fetchone()
            return {
                "count": row[0] or 0,
                "chars": row[1] or 0,
                "word_count": row[2] or 0,
                "duration": row[3] or 0,
            }
        except Exception as e:
            log.error("[HISTORY] Failed to get today stats: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return {"count": 0, "chars": 0, "word_count": 0, "duration": 0}
