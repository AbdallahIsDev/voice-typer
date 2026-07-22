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
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

_CURRENT_SCHEMA_VERSION = 3
_MAX_SEARCH_QUERY_CHARS = 200

# CR-27: hard upper bound on the total time a blocking _submit_write
# caller will wait for the writer thread to execute its closure. The
# per-retry timeout is _WRITE_FUTURE_TIMEOUT (30s); without a hard cap,
# the retry loop below could wait forever as long as the writer thread
# was merely *alive* (e.g. a multi-batch retention sweep on a huge DB
# that never makes progress because of an external SQLite lock). 60s is
# 2× the per-retry timeout — generous enough that a legitimate slow
# write (large retention sweep) is never aborted prematurely, but short
# enough that a truly stuck writer surfaces a clear error to the caller
# instead of hanging the IPC handler thread indefinitely.
_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0

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

# PERF-5: maximum number of pending write closures enqueued on the
# writer thread's queue. Bounded so a stalled writer (disk full, antivirus
# lock, deadlocked external process) cannot cause the in-memory queue to
# grow unboundedly and exhaust memory. 10000 is ~5 minutes of fire-and-
# forget add_transcription writes at 30/s. When the bound is hit, the
# oldest non-sentinel queued item is dropped (and its future, if any, is
# resolved with ``HistoryDBError`` so wait=True callers don't hang).
# Exposed as a module-level constant so tests can pin the documented
# bound and reference it as the contract for the drop-oldest path.
_WRITE_QUEUE_MAXSIZE = 10000

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

# CR-49 / M-61: FTS5 full-text search index.
#
# Previously `search()` did a `WHERE text LIKE ?` table scan — O(n) on
# the full transcriptions table. For a user with thousands of history
# rows this is several hundred milliseconds per keystroke in the search
# box. The FTS5 virtual table brings this down to O(log n + match count)
# and gives proper tokenization (case-insensitive, Unicode-aware,
# prefix queries via `query*`).
#
# The migration is intentionally additive:
#   - CREATE VIRTUAL TABLE IF NOT EXISTS — safe to re-run on every
#     schema init (existing FTS table is left untouched).
#   - Triggers keep the FTS table in sync with INSERT/UPDATE/DELETE on
#     `transcriptions`. They are created with `IF NOT EXISTS` so the
#     migration is idempotent.
#   - The `INSERT INTO transcriptions_fts(rowid, text) SELECT id, text
#     FROM transcriptions` backfill is safe to re-run because the FTS
#     table is empty on the first migration (and a re-run after a
#     successful migration is a no-op: `transcriptions_fts` already
#     contains every rowid, so the reinsert just overwrites the same
#     row). The backfill is wrapped in its own transaction so a partial
#     failure (e.g. disk full) doesn't leave the FTS table half-populated
#     AND the schema_meta version bumped.
#
# G4-CR-03: the entire migration runs inside an explicit BEGIN / COMMIT.
# Previously each migration statement ran in its own implicit
# transaction (Python sqlite3 autocommit-off semantics), so a crash
# mid-migration could leave the schema half-migrated with the version
# number already bumped. The explicit transaction ensures the schema
# version is only persisted if every statement in the migration
# succeeded.
_MIGRATION_V3 = """
    BEGIN;
    CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5(
        text,
        content='transcriptions',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS transcriptions_ai_fts AFTER INSERT ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_ad_fts AFTER DELETE ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_au_fts AFTER UPDATE ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    INSERT INTO transcriptions_fts(rowid, text) SELECT id, text FROM transcriptions;
    COMMIT;
"""

_MIGRATIONS = {
    2: _MIGRATION_V2,
    3: _MIGRATION_V3,
}


def _prepare_like_search_pattern(query: str) -> str:
    """Build a bounded LIKE pattern where user wildcards stay literal."""
    capped_query = query[:_MAX_SEARCH_QUERY_CHARS]
    escaped_query = capped_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped_query}%"


def _is_fts_compatible_query(query: str) -> bool:
    """CR-49: return True if the (capped) query can be served by the FTS5 index.

    FTS5's ``unicode61`` tokenizer treats ``%``, ``_``, and most
    punctuation as *separators*. A query consisting ONLY of separator
    characters produces zero tokens and either raises a syntax error
    (e.g. ``%``) or silently matches nothing (e.g. ``_``). For such
    queries we fall back to LIKE so users can still find rows containing
    literal ``%`` / ``_`` characters (matches the pre-CR-49 behavior
    pinned by ``test_search_treats_like_wildcards_as_literals``).

    Heuristic: strip every non-word character (Unicode-aware) and check
    if anything remains. If yes, FTS5 will produce at least one token
    and can serve the query. If no, fall back to LIKE.
    """
    capped = query[:_MAX_SEARCH_QUERY_CHARS]
    # \W matches [^a-zA-Z0-9_] in ASCII mode, but with re.UNICODE (the
    # default in Py3) it matches any non-word character. We also
    # explicitly strip ``_`` because ``\w`` includes underscore.
    stripped = re.sub(r"[\W_]+", "", capped, flags=re.UNICODE)
    return bool(stripped)


def _sanitize_fts_query(query: str) -> str:
    """CR-49: escape FTS5 special characters so user input is treated as literals.

    FTS5 MATCH syntax treats ``*``, ``"``, ``(``, ``)``, ``:``, ``^``,
    ``{``, ``}`` and a few others as syntax. A user typing ``foo*``
    expects a substring/literal match, not an FTS5 prefix query. We wrap
    each whitespace-separated token in double quotes (FTS5 "phrase"
    syntax) so the token is treated as a literal string. This means:

    - ``foo`` → ``"foo"`` (exact-token match)
    - ``foo*`` → ``"foo*"`` (literal ``foo*``, no prefix expansion)
    - ``hello world`` → ``"hello" "world"`` (implicit AND of two tokens)
    - ``100%`` → ``"100%"`` (but ``%`` is a separator, so the actual
      token FTS5 sees is ``100``; the query still works)

    The caller is responsible for checking ``_is_fts_compatible_query``
    first — this function assumes the query has at least one
    FTS5-tokenizable character.
    """
    capped = query[:_MAX_SEARCH_QUERY_CHARS]
    tokens = capped.split()
    if not tokens:
        # Shouldn't happen (caller checks _is_fts_compatible_query), but
        # guard anyway: an empty MATCH is a syntax error.
        return '""'
    # Wrap each token in double quotes. Escape any embedded double
    # quotes by doubling them (SQL string-literal style).
    quoted = []
    for tok in tokens:
        escaped_tok = tok.replace('"', '""')
        quoted.append(f'"{escaped_tok}"')
    return " ".join(quoted)


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
        # PERF-5: bound the queue so a stalled writer thread can't let
        # the in-memory queue grow without limit. On queue.Full we drop
        # the oldest non-sentinel item and log a warning. See
        # ``_WRITE_QUEUE_MAXSIZE`` for the bound's rationale (~5 minutes
        # of fire-and-forget add_transcription writes at 30/s).
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_WRITE_QUEUE_MAXSIZE)
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

        G4-CR-03: ``_init_db_schema`` may set ``self._init_error`` and
        return early without raising (e.g. migration failure rolled
        back). In that case we must NOT enter the main write loop —
        the schema is in an inconsistent state and writes would fail
        or corrupt data further. Close the connection and exit so
        callers see the failure via ``_init_error`` / ``health_check``.
        """
        conn: sqlite3.Connection | None = None
        try:
            conn = self._open_write_conn()
            self._check_wal_mode(conn)
            # G4-M-03: _init_db_schema may return a fresh connection
            # if corruption was detected and the DB was recreated.
            conn = self._init_db_schema(conn)
        except BaseException as e:  # noqa: BLE001 — surface to __init__
            self._init_error = e
            self._writer_ready.set()
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            return
        self._writer_ready.set()
        # G4-CR-03: if schema init set _init_error (e.g. migration
        # failure), don't enter the main write loop. The DB is in an
        # inconsistent state; writes would fail or compound the damage.
        if self._init_error is not None:
            log.error(
                "[HISTORY_DB] Skipping writer loop — schema init failed: %s",
                self._init_error,
            )
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            return

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
                    # PVT-005 (session-2): Suppress InvalidStateError on
                    # set_exception. If the future was already resolved
                    # (e.g. by a prior duplicate-enqueue race in
                    # _drop_oldest_for_overflow, or by a coding bug),
                    # set_exception raises InvalidStateError which would
                    # propagate out of this except block and KILL the
                    # writer thread permanently. That converts a single
                    # write failure into permanent data loss for all
                    # subsequent writes until app restart.
                    with contextlib.suppress(concurrent.futures.InvalidStateError):
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
          - ``secure_delete=ON`` — G4-M-04: overwrite deleted rows
            with zeros so dictated text is not recoverable from free
            pages.

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
        # G4-M-04: secure_delete=ON overwrites deleted rows with zeros
        # before freeing the page, so dictated text is not recoverable
        # from free pages by an attacker with filesystem access. This
        # complements the GDPR delete path (which unlinks the DB file
        # entirely) by ensuring that in-place deletes (clear_all,
        # apply_retention, delete by id) don't leave plaintext in free
        # pages that could be carved out with a hex editor. Tradeoff:
        # deletes are slightly slower (extra I/O to zero the page).
        # Acceptable for transcription history where privacy outweighs
        # throughput. Note: this PRAGMA is database-persistent — once
        # set, it applies to all connections on this DB file.
        conn.execute("PRAGMA secure_delete=ON")
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

    def _init_db_schema(
        self,
        conn: sqlite3.Connection,
        _is_recovery: bool = False,
    ) -> sqlite3.Connection:
        """Initialize the database schema and run migrations.

        IMPL-A: previously this method called ``self._get_conn()``;
        now it takes the writer's connection as a parameter so it can
        run on the writer thread.

        G4-CR-02: after each successful migration iteration, the
        schema version is persisted via ``INSERT OR REPLACE INTO
        schema_meta``. Previously the version was read but never
        written, so migrations re-ran on every launch (the V3 FTS5
        backfill re-scanned every row each startup).

        G4-CR-03: each migration is wrapped in an explicit
        ``BEGIN; … COMMIT;`` transaction (via ``executescript``). On
        ``sqlite3.Error``, the transaction is rolled back and
        ``self._init_error`` is set so the writer thread surfaces the
        failure to ``__init__`` and skips the main write loop. The
        per-statement try/except that previously swallowed errors
        (allowing a partial migration to leave the schema
        half-migrated) is removed — a partial migration now fails
        loudly and rolls back ALL changes (including DDL ALTERs,
        which SQLite would otherwise auto-commit between statements).

        G4-M-03: at the end of a successful init, ``PRAGMA
        quick_check`` is run. If the result is anything other than
        ``("ok",)``, the corrupt DB is renamed to
        ``history.db.corrupt-<timestamp>`` and a fresh DB is created.
        The ``_is_recovery`` flag prevents infinite recursion if the
        fresh DB also fails the integrity check.

        FIX (preserved from prior version): schema/metadata BEFORE
        indexes that depend on migrated columns. The original code ran
        CREATE INDEX idx_favorite ON transcriptions(favorite) BEFORE
        the migration code. On an existing database created without
        the 'favorite' column, CREATE INDEX would fail with "no such
        column: favorite". Fix: create the table first, then run
        schema versioning + migrations, then create indexes.

        Returns the connection to use (may be a fresh one if
        corruption was detected and the DB was recreated). Callers
        must use the returned connection, not the one they passed in.
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

        # G4-CR-02/G4-CR-03: run each migration in an explicit
        # ``BEGIN; … COMMIT;`` transaction via ``executescript``.
        # ``executescript`` is used for BOTH migration shapes:
        #
        # 1. Trigger-bearing migrations (e.g. _MIGRATION_V3 with
        #    ``CREATE TRIGGER ... BEGIN ... END;``) carry their own
        #    ``BEGIN;…COMMIT;`` and CANNOT be naively split on ``;``
        #    (the inner statement terminators inside BEGIN/END would
        #    be misinterpreted as end-of-statement).
        #
        # 2. Plain ALTER/CREATE migrations (e.g. _MIGRATION_V2) are
        #    wrapped in ``BEGIN;…COMMIT;`` so the whole migration is
        #    atomic. Without the wrapper, SQLite's DDL auto-commit
        #    behavior would persist each ALTER individually — a
        #    mid-migration failure would leave the schema
        #    half-migrated with no way to roll back the already-
        #    committed ALTERs.
        #
        # On ``sqlite3.Error``: rollback the transaction, set
        # ``_init_error``, and return early. The version is NOT
        # bumped — the next launch retries from the pre-migration
        # version. The per-statement try/except that previously
        # swallowed errors (CR-32) is removed because it allowed
        # partial migrations to silently corrupt the schema.
        for version in range(current_version + 1, _CURRENT_SCHEMA_VERSION + 1):
            migration_sql = _MIGRATIONS.get(version)
            if not migration_sql:
                continue

            try:
                # Wrap plain migrations (no embedded BEGIN;) in an
                # explicit transaction. Migrations that already carry
                # their own BEGIN;…COMMIT; (e.g. _MIGRATION_V3) are
                # passed through unchanged.
                needs_wrapper = "BEGIN;" not in migration_sql.upper()
                if needs_wrapper:
                    wrapped_sql = "BEGIN;\n" + migration_sql + "\nCOMMIT;\n"
                else:
                    wrapped_sql = migration_sql
                cursor.executescript(wrapped_sql)
                # G4-CR-02: persist the version after each successful
                # migration iteration so the next launch doesn't
                # re-run it. ``INSERT OR REPLACE`` handles both the
                # initial insert and subsequent updates.
                cursor.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
                    (str(version),),
                )
                conn.commit()
                log.info(
                    "[HISTORY_DB] Migrated schema to version %d (transactional, version persisted)",
                    version,
                )
            except sqlite3.Error as e:
                # G4-CR-03: rollback any partial migration. The
                # version is NOT bumped — the next launch retries.
                # Surface the error to ``__init__`` via ``_init_error``
                # so the writer thread skips the main write loop.
                #
                # G4-CR-02 compat: if the error is "duplicate column
                # name" (columns already exist from a prior partial
                # migration that didn't persist the version), treat
                # the migration as effectively complete — the columns
                # are there, the intent is satisfied. Bump the version
                # so the next launch doesn't retry.
                err_msg = str(e).lower()
                if "duplicate column name" in err_msg:
                    log.info(
                        "[HISTORY_DB] Migration v%d: columns already "
                        "exist (duplicate column name) — treating as "
                        "complete and persisting version",
                        version,
                    )
                    cursor.execute(
                        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
                        (str(version),),
                    )
                    conn.commit()
                    continue
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                log.error(
                    "[HISTORY_DB] Migration v%d failed: %s "
                    "(version NOT bumped; transaction rolled back; "
                    "_init_error set)",
                    version,
                    e,
                )
                self._init_error = e
                return conn

        # Create indexes AFTER migration so 'favorite' column exists.
        # G4-CR-03: refresh existing_columns post-migration and guard
        # idx_favorite creation so a rolled-back migration (which
        # returns early above) doesn't crash the whole init. The
        # index on timestamp is safe to create unconditionally —
        # 'timestamp' is in the original CREATE TABLE.
        cursor.execute("PRAGMA table_info(transcriptions)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON transcriptions(timestamp DESC)
        """)
        if "favorite" in existing_columns:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_favorite
                ON transcriptions(favorite)
            """)
        else:
            log.warning(
                "[HISTORY_DB] Skipping idx_favorite creation: 'favorite' "
                "column missing (migration was rolled back or not yet "
                "applied). Next launch will retry.",
            )

        # G4-M-03: integrity check at the end of schema init. Skip
        # on recovery to prevent infinite recursion if the fresh DB
        # also fails the check (in which case _init_error is set on
        # the second failure and the writer exits).
        if not _is_recovery:
            new_conn = self._maybe_recover_from_corruption(conn)
            if new_conn is not None:
                # Corruption detected and a fresh DB was created.
                # Re-run schema init on the fresh connection.
                return self._init_db_schema(new_conn, _is_recovery=True)

        log.info(
            "[HISTORY] History database initialized: %s (schema v%d)",
            self.db_path,
            _CURRENT_SCHEMA_VERSION,
        )
        return conn

    def _maybe_recover_from_corruption(
        self,
        conn: sqlite3.Connection,
    ) -> sqlite3.Connection | None:
        """G4-M-03: run ``PRAGMA quick_check``; if the result is
        anything other than ``("ok",)``, rename the corrupt DB file
        (and its WAL/SHM sidecars) to ``history.db.corrupt-<timestamp>``
        and return a fresh connection on a new (empty) DB file.

        Returns ``None`` if the DB is healthy. Returns a new
        connection if corruption was detected and recovery succeeded.
        Sets ``self._init_error`` and returns ``None`` if recovery
        failed (e.g. the rename or reopen raised).

        The caller is responsible for re-running schema init on the
        returned connection (the fresh DB has no tables yet).
        """
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as e:
            log.error(
                "[HISTORY_DB] PRAGMA quick_check raised: %s (treating as corruption and attempting recovery)",
                e,
            )
            # Fall through to the recovery path — we can't verify
            # integrity, so assume the worst and rename.
            rows = [("quick_check raised", str(e))]

        if len(rows) == 1 and rows[0][0] == "ok":
            return None  # healthy

        log.error(
            "[HISTORY_DB] Integrity check failed: %s. Renaming corrupt DB and creating a fresh one.",
            rows,
        )
        # Close the corrupt connection so we can rename the file.
        # Suppress errors — the connection may already be in a bad
        # state.
        with contextlib.suppress(sqlite3.Error):
            conn.close()
        # Rename the corrupt DB and its WAL/SHM sidecar files.
        timestamp = int(time.time())
        corrupt_suffix = f".corrupt-{timestamp}"
        corrupt_main = self.db_path.with_name(self.db_path.name + corrupt_suffix)
        for sidecar in ("", "-wal", "-shm"):
            src = self.db_path.with_name(self.db_path.name + sidecar)
            if src.exists():
                dst = corrupt_main.with_name(corrupt_main.name + sidecar)
                with contextlib.suppress(OSError):
                    src.rename(dst)
        log.warning(
            "[HISTORY_DB] Renamed corrupt DB to %s",
            corrupt_main,
        )
        # Open a fresh connection on a new (empty) DB file.
        try:
            new_conn = self._open_write_conn()
            self._check_wal_mode(new_conn)
            return new_conn
        except sqlite3.Error as e:
            self._init_error = e
            return None

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

    def _drop_oldest_for_overflow(self, current_future: concurrent.futures.Future | None) -> None:
        """PERF-5: Drop the oldest non-sentinel queued item to make room.

        Called when ``_submit_write`` hits ``queue.Full``. Signals the
        dropped item's future (if any) with ``HistoryDBError`` so blocking
        callers don't hang. If the head of the queue is the shutdown
        sentinel, we leave it alone and drop the new write instead (the
        writer is shutting down, so the new work wouldn't run anyway).
        """
        try:
            dropped = self._queue.get_nowait()
        except queue.Empty:
            # PVT-005 (session-2): Queue drained between put_nowait and
            # now. Previously this branch enqueued a no-op lambda bound
            # to the caller's own ``future`` — but the caller
            # (``_submit_write``) then retries ``put_nowait((fn, future))``,
            # so the queue held TWO items sharing the same future. The
            # writer executed the lambda first → ``future.set_result(None)``
            # succeeded; then executed ``fn`` → ``future.set_result(result)``
            # raised InvalidStateError; the except handler then tried
            # ``future.set_exception(e)`` → also raised InvalidStateError
            # (not suppressed before PVT-005) → killed the writer thread
            # permanently. Silent data loss + dead writer.
            #
            # Fix: do NOT enqueue anything here. Just return and let the
            # caller's retry handle the put. The caller's future is
            # untouched and will be resolved by the real ``fn`` when the
            # writer picks it up.
            return
        if dropped is _SHUTDOWN_SENTINEL:
            # Put the sentinel back; drop the new write instead.
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(dropped)
            if current_future is not None:
                with contextlib.suppress(concurrent.futures.InvalidStateError):
                    current_future.set_exception(HistoryDBError("Writer is shutting down; new write dropped"))
            log.warning("[HISTORY_DB] Queue full during shutdown — new write dropped.")
            return
        # Dropped a real (fn, future) tuple. Signal its future.
        dropped_fn, dropped_future = dropped
        if dropped_future is not None:
            try:
                # CR-78 / PERF-5: the dropped future must be resolved
                # with a clear, machine-greppable message so callers
                # that catch HistoryDBError can distinguish "queue full"
                # from other failure modes (e.g. "Writer is shutting
                # down" or "Dropped during shutdown sentinel enqueue").
                # The literal "queue full" substring is part of the
                # contract asserted by TestQueueBounded.
                dropped_future.set_exception(
                    HistoryDBError(
                        "queue full; dropped oldest write to make room for newer write (writer thread may be stalled)"
                    )
                )
            except concurrent.futures.InvalidStateError:
                pass
        log.warning("[HISTORY_DB] queue full — dropped oldest write to make room. Writer thread may be stalled.")
        # The caller (_submit_write) retries the put_nowait after we
        # return — we've freed one slot by dropping the oldest item, so
        # the retry will succeed unless the writer is also stalling and
        # another caller has already filled the slot. CR-78: previously
        # had an empty ``try: pass except Exception: log.debug(...)`` here
        # that could never raise (the body was ``pass``) — removed.

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
        # PERF-5: bounded queue (maxsize=_WRITE_QUEUE_MAXSIZE). Use
        # put_nowait + drop-oldest so a stalled writer doesn't block
        # the calling thread indefinitely.
        try:
            self._queue.put_nowait((fn, future))
        except queue.Full:
            self._drop_oldest_for_overflow(future)
            # Retry once after dropping oldest.
            try:
                self._queue.put_nowait((fn, future))
            except queue.Full:
                # Still full (writer truly stuck); drop the new write.
                if future is not None:
                    with contextlib.suppress(concurrent.futures.InvalidStateError):
                        future.set_exception(HistoryDBError("Queue full after drop-oldest; new write dropped"))
                log.warning(
                    "[HISTORY_DB] Queue still full after drop-oldest — new write dropped. Writer thread is stuck."
                )
                if not wait:
                    return None
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
        # before exiting. PERF-5: the queue is now bounded
        # (maxsize=_WRITE_QUEUE_MAXSIZE). Use a drop-oldest loop so the
        # sentinel is never dropped (we keep re-trying until it lands at
        # the head of the queue).
        try:
            self._queue.put_nowait(_SHUTDOWN_SENTINEL)
        except queue.Full:
            # Drain non-sentinel items until the sentinel fits.
            # Bound the loop at ``_WRITE_QUEUE_MAXSIZE + 1`` — the queue
            # can hold at most that many items, so one full sweep
            # guarantees the sentinel fits (modulo concurrent enqueues,
            # which we accept as a rare race; close() is best-effort).
            for _ in range(_WRITE_QUEUE_MAXSIZE + 1):  # bound to avoid infinite loop
                try:
                    dropped = self._queue.get_nowait()
                except queue.Empty:
                    break
                if dropped is _SHUTDOWN_SENTINEL:
                    # Sentinel was already queued by another close() call;
                    # put it back and stop.
                    with contextlib.suppress(queue.Full):
                        self._queue.put_nowait(dropped)
                    break
                # Dropped a real write — signal its future.
                dropped_fn, dropped_future = dropped
                if dropped_future is not None:
                    with contextlib.suppress(concurrent.futures.InvalidStateError):
                        dropped_future.set_exception(HistoryDBError("Dropped during shutdown sentinel enqueue"))
                log.warning("[HISTORY_DB] Dropped write during shutdown queue drain.")
                # Try to enqueue the sentinel now.
                try:
                    self._queue.put_nowait(_SHUTDOWN_SENTINEL)
                    break
                except queue.Full:
                    continue
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

        G4-M-05: after the chunked DELETE completes, ``VACUUM`` runs
        in the writer thread to reclaim the freed pages so the DB file
        shrinks. Without this, ``clear_all`` leaves the file at its
        pre-clear size (SQLite keeps free pages for reuse) and the
        user's dictated text remains recoverable from the file via
        forensic tools even after a "clear all" — a privacy concern
        for the GDPR delete path.

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
                # Final commit to close any open transaction started
                # by the last DELETE (which matched 0 rows but still
                # auto-opened a transaction in Python's sqlite3 module).
                # VACUUM requires no open transaction.
                conn.commit()
                # G4-M-05: VACUUM reclaims the freed pages so the DB
                # file shrinks and deleted text is not recoverable
                # from free pages. Runs inside the writer thread so
                # it serializes with other writes. VACUUM requires
                # exclusive access — readers will block briefly.
                try:
                    conn.execute("VACUUM")
                    log.info("[HISTORY_DB] VACUUM completed after clear_all")
                except sqlite3.Error as e:
                    # VACUUM failure is non-fatal — the rows are
                    # already deleted; only space reclamation failed.
                    log.warning("[HISTORY_DB] VACUUM after clear_all failed: %s", e)
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

        G4-M-05: after the retention sweep, ``VACUUM`` runs only if
        more than 20% of rows were deleted — this avoids the VACUUM
        cost (which requires exclusive access and briefly blocks
        readers) for small sweeps while still reclaiming space after
        large purges.
        """
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

    # ──────────────────────────────────────────────────────────────
    # Maintenance & diagnostics
    # ──────────────────────────────────────────────────────────────

    def checkpoint(self, truncate: bool = True) -> bool:
        """G4-M-06: run ``PRAGMA wal_checkpoint(TRUNCATE)`` (or
        ``RESTART``) on the writer thread.

        Used by GDPR delete/export paths to ensure all WAL content is
        checkpointed back to the main DB file before file-level
        operations (e.g. ``os.unlink`` of ``history.db``). Without
        this, dictated text remains recoverable from the
        ``history.db-wal`` sidecar file even after the main DB file
        is deleted — see G4-CR-04.

        Parameters
        ----------
        truncate : bool
            If ``True`` (default), run ``wal_checkpoint(TRUNCATE)``
            which checkpoints all frames back to the main DB file and
            then truncates the WAL file to zero size. This is the
            mode callers want before unlinking the DB file. If
            ``False``, run ``wal_checkpoint(RESTART)`` which
            checkpoints but leaves the WAL in a restartable state
            (useful before a clean shutdown that will resume writing).

        Returns
        -------
        ``True`` if the checkpoint completed without error, ``False``
        otherwise (writer unavailable, checkpoint failed). The
        caller (e.g. GDPR delete) should treat ``False`` as "WAL may
        still contain data; do not unlink until next attempt".
        """
        try:

            def _do_checkpoint(conn: sqlite3.Connection) -> bool:
                mode = "TRUNCATE" if truncate else "RESTART"
                try:
                    # wal_checkpoint returns (busy, log, checkpointed)
                    # where busy=0 means no writer was active. We don't
                    # retry on busy=1 because the writer thread IS the
                    # only writer; a busy result here means an external
                    # process holds the lock, which the caller can't
                    # resolve by retrying.
                    result = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
                    if result is not None:
                        log.debug(
                            "[HISTORY_DB] wal_checkpoint(%s): busy=%s, log=%s, checkpointed=%s",
                            mode,
                            result[0] if len(result) > 0 else "?",
                            result[1] if len(result) > 1 else "?",
                            result[2] if len(result) > 2 else "?",
                        )
                    return True
                except sqlite3.Error as e:
                    log.warning(
                        "[HISTORY_DB] wal_checkpoint(%s) failed: %s",
                        mode,
                        e,
                    )
                    return False

            result = self._submit_write(_do_checkpoint, wait=True)
            if result is None:
                # Writer shut down — can't checkpoint.
                return False
            return bool(result)
        except HistoryDBError as e:
            log.error("[HISTORY] Writer unavailable for checkpoint: %s", e)
            return False
        except Exception as e:
            log.error("[HISTORY] Failed to checkpoint: %s", e)
            return False

    def health_check(self) -> dict:
        """G4-DI-10: return a health status dict for diagnostics.

        Returns
        -------
        ``{"ok": bool, "error": str | None}``

        - ``ok`` is ``True`` only if the writer thread is alive AND
          ``_init_error`` is ``None`` (no schema init failure). This
          is the minimum viable health signal: a dead writer or a
          failed migration means writes will silently fail.
        - ``error`` is a human-readable string describing the
          failure, or ``None`` if healthy.

        Callers (e.g. the IPC ``get_diagnostics`` handler) can expose
        this to the renderer so the user sees a clear "history DB is
        unavailable" message instead of silently-failed writes.
        """
        if self._init_error is not None:
            return {"ok": False, "error": str(self._init_error)}
        if not self._writer_thread.is_alive():
            return {"ok": False, "error": "history DB writer thread is not alive"}
        return {"ok": True, "error": None}
