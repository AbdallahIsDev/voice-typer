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
                                                     every 300s

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
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

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
#   _CLEAR_ALL_BATCH_SIZE — chunk size for the bulk DELETEs inside
#   ``clear_all``; each batch commits so the WAL doesn't grow
#   unboundedly and external readers see progress. (The retention
#   sweep's ``_RETENTION_BATCH`` chunk size now lives in
#   ``history_db_internals.retention``.)
_WAL_CHECKPOINT_INTERVAL = 300.0  # 5 minutes — keeps WAL small with negligible overhead
_WRITE_FUTURE_TIMEOUT = 30.0
_WRITER_JOIN_TIMEOUT = 10.0
_WRITER_READY_TIMEOUT = 30.0
# clear_all uses a larger batch than retention because it unconditionally
# deletes every row — chunking only exists to let external readers see
# progress and to bound WAL growth between commits. SQLite's default
# ``wal_autocheckpoint=1000`` pages already bounds WAL size, so a 1000-row
# batch (the query takes a single LIMIT arg, well under SQLite's 999-
# placeholder default) is safe and 10x faster than the previous 100-row
# batch on power-user databases with 50K+ rows.
_CLEAR_ALL_BATCH_SIZE = 1000

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

# ER-78: maximum number of transcription rows bundled into a single
# multi-row INSERT. SQLite's default ``SQLITE_MAX_VARIABLE_NUMBER`` is
# 999 (or 32766 on newer builds); 7 placeholder columns × 100 rows =
# 700 placeholders — well under the conservative 999 bound. Capping the
# batch size also bounds the peak memory used by the parameter list and
# the WAL frame count of a single transaction.
_BATCH_INSERT_CAP = 100

# ER-78: minimum number of pending _BatchableInsert items required to
# trigger the multi-row INSERT path. Below this threshold each row is
# inserted individually (the per-transaction overhead saving doesn't
# justify the multi-row SQL construction for 1-2 rows).
_BATCH_INSERT_MIN = 3

# TY-20: TTL (seconds) for the get_history_count cache.
_HISTORY_COUNT_CACHE_TTL_S = 60.0

# TY-8: maximum characters of ``text`` returned in list responses.
_HISTORY_TEXT_PREVIEW_LENGTH = 500


class _BatchableInsert:
    """ER-78: structured payload for batchable transcription INSERTs.

    Instead of enqueuing a closure that does its own INSERT+COMMIT (one
    transaction per row — the original behavior), ``add_transcription``
    enqueues this structured payload. The writer thread peeks the queue;
    if ``_BATCH_INSERT_MIN`` or more such items are pending, they're
    drained into a single multi-row INSERT inside one transaction:
    ``INSERT INTO transcriptions (...) VALUES (?,?,?...), (?,?,?...)``.

    Fire-and-forget semantics are preserved: ``future`` is ``None`` for
    ``add_transcription`` (the transcription pipeline never waits on
    the DB write). The field is present so the same batching path can
    serve a future caller that does want the row_id back.

    The class uses ``__slots__`` to minimize per-row memory overhead
    (the queue can hold thousands of these under bursty dictation).
    """

    __slots__ = (
        "text",
        "duration",
        "model",
        "device",
        "word_count",
        "char_count",
        "language",
        "future",
    )

    def __init__(
        self,
        *,
        text: str,
        duration: float,
        model: str,
        device: str,
        word_count: int,
        char_count: int,
        language: str,
        future: concurrent.futures.Future | None = None,
    ) -> None:
        self.text = text
        self.duration = duration
        self.model = model
        self.device = device
        self.word_count = word_count
        self.char_count = char_count
        self.language = language
        self.future = future


class HistoryDBError(RuntimeError):
    """Raised by HistoryDB methods on unrecoverable failures.

    ERR-013: previously every method returned a different sentinel
    (``[]``, ``None``, ``False``, ``-1``, ``{}``) which forced callers
    to know each method's specific sentinel. Methods now log the
    underlying error and return the documented sentinel; callers that
    need to distinguish "empty result" from "operation failed" can
    catch this exception via the ``raise_on_error`` parameter.
    """


# Schema-init / migration logic lives in
# ``voice_typer.server.history_db_internals.schema``. The migration
# SQL strings, the migrations dict, and the current schema version
# constant are re-exported here so existing callers (and tests that
# monkey-patch ``history_db._MIGRATIONS`` / read
# ``history_db._CURRENT_SCHEMA_VERSION``) keep working unchanged —
# the re-exported ``_MIGRATIONS`` is the SAME dict object that
# ``history_db_internals.schema.init_schema`` reads, so in-place
# mutation (e.g. ``unittest.mock.patch.dict``) is observed by the
# schema initializer.


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


def _project_text_row(row: sqlite3.Row | tuple) -> dict:
    """TY-8: post-process a SQLite row from get_recent/search/get_favorites."""
    d = dict(row)
    full_length = d.get("text_full_length")
    if full_length is None:
        full_length_int = 0
        truncated = False
    else:
        full_length_int = int(full_length)
        truncated = full_length_int > _HISTORY_TEXT_PREVIEW_LENGTH
    d["text_truncated"] = truncated
    d["text_full_length"] = full_length_int
    return d


# FT-2: module-level WeakSet tracking all live HistoryDB instances. Tests
# that construct HistoryDB via ``_MockApp`` helpers frequently leak the
# instance (and its ``HistoryDBWriter`` daemon thread) because the test
# fixture only calls ``IPCServer.stop()``, which does NOT close
# ``app.history_db``. On Windows the accumulated daemon threads eventually
# trip a native limit and crash the whole pytest process mid-suite (FT-2).
# ``tests/conftest.py`` iterates this set after each test and calls
# ``close()`` on any still-alive instance.
_LIVE_INSTANCES: "weakref.WeakSet[HistoryDB]" = weakref.WeakSet()


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
        # can clean them up, preventing ResourceWarning on GC. Each
        # entry is a ``(thread_ident, connection)`` tuple — the
        # thread_ident lets ``_prune_dead_read_connections_locked``
        # detect when the owning thread has exited and close its
        # connection (releasing the ~20 MB SQLite page cache) instead
        # of letting it leak for the lifetime of the HistoryDB. Without
        # this pruning, IPC handler threads, tray thread, dictation
        # pipeline thread, and test threads would each accumulate a
        # 20 MB read connection that's never released until close().
        self._all_read_connections: list[tuple[int, sqlite3.Connection]] = []
        self._connections_lock = threading.Lock()
        # Write queue: items are (callable, future) tuples, OR
        # _BatchableInsert instances (ER-78), OR the _SHUTDOWN_SENTINEL
        # to ask the writer to exit. ``future`` is None for
        # fire-and-forget writes (e.g. add_transcription).
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
        # ER-36: re-entrancy guard for apply_retention. The periodic
        # retention scheduler spawns a daemon thread that calls
        # apply_retention on a fixed interval; if a previous run is
        # still in flight (e.g. a multi-batch VACUUM on a huge DB),
        # the next tick acquires this lock non-blocking and skips
        # rather than queueing a second concurrent sweep.
        self._retention_lock = threading.Lock()
        # ER-36: stop event for the periodic retention thread. Set by
        # close() (and by re-scheduling) to ask the daemon loop to exit.
        self._retention_stop_event: threading.Event | None = None
        # ER-36: handle to the periodic retention daemon thread (for
        # join-on-close).
        self._retention_thread: threading.Thread | None = None
        # TY-20: TTL cache for ``get_history_count``.
        self._history_count_cache: int | None = None
        self._history_count_cache_ts: float = 0.0
        self._history_count_cache_lock = threading.Lock()
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
        # FT-2: register in the module-level WeakSet so the test conftest
        # can close leaked instances after each test (prevents the daemon
        # writer thread from accumulating across the full pytest run and
        # crashing the process on Windows via native thread-limit exhaustion).
        _LIVE_INSTANCES.add(self)

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
            # ER-78: structured batchable INSERT payload — drain pending
            # inserts into a single multi-row INSERT when 3+ are queued.
            if isinstance(item, _BatchableInsert):
                self._drain_batchable_inserts(conn, item)
                # WAL-CHECKPOINT-FIX: post-write cleanup (same as the
                # normal closure path).
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                continue
            callable_, future = item
            self._execute_write_item(conn, callable_, future)
        # Drain loop exited — close the writer's connection.
        try:
            conn.close()
        except sqlite3.Error as e:
            log.warning("[HISTORY_DB] Error closing writer connection: %s", e)

    def _execute_write_item(
        self,
        conn: sqlite3.Connection,
        callable_: Callable[[sqlite3.Connection], Any],
        future: concurrent.futures.Future | None,
    ) -> None:
        """Execute a single queued write closure and resolve its future.

        AC-68 (DRY): the item-handling block was previously duplicated
        verbatim between ``_writer_loop`` and ``_drain_remaining``,
        with one critical divergence — ``_drain_remaining`` was
        MISSING the PVT-005 ``InvalidStateError`` suppression on
        ``future.set_exception(e)``. That made the shutdown drain
        fragile: if a duplicate-enqueue race resolved the future
        before the except block ran, ``set_exception`` would raise
        ``InvalidStateError`` → kill the drain → fire-and-forget
        writes silently dropped during shutdown.

        Centralizing the logic here guarantees both call sites share
        the PVT-005 suppression (and the DB-LOCK-FIX rollback, and
        the WAL-CHECKPOINT-FIX post-write rollback).
        """
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

    def _drain_batchable_inserts(
        self,
        conn: sqlite3.Connection,
        first_item: _BatchableInsert,
    ) -> None:
        """ER-78: drain pending ``_BatchableInsert`` items into one INSERT.

        Called from :meth:`_writer_loop` (and :meth:`_drain_remaining`
        during shutdown) when the writer pulls a ``_BatchableInsert``
        off the queue. Peeks the queue for additional
        ``_BatchableInsert`` items (up to ``_BATCH_INSERT_CAP``).

        * If ``_BATCH_INSERT_MIN`` or more items are collected (including
          ``first_item``), they're batched into a single multi-row
          ``INSERT INTO transcriptions (...) VALUES (?,?,?...), (?,?,?...)``
          inside one transaction (one COMMIT for the whole batch).
        * Otherwise each collected row is inserted individually
          (preserving the original one-INSERT-per-row behavior for
          low-contention cases where the batching optimization isn't
          worth the multi-row SQL construction).

        Non-``_BatchableInsert`` items pulled off the queue during the
        peek are put back so the main writer loop processes them in
        order. The ``_SHUTDOWN_SENTINEL`` is likewise put back so the
        shutdown path still fires.

        Fire-and-forget semantics are preserved: each item's ``future``
        (if any — ``add_transcription`` always passes ``None``) is
        resolved with the inserted row_id (or -1 on failure). On
        exception, all futures are resolved with the exception.
        """
        batch: list[_BatchableInsert] = [first_item]
        while len(batch) < _BATCH_INSERT_CAP:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _SHUTDOWN_SENTINEL:
                # Put the sentinel back so the main loop sees it and
                # triggers _drain_remaining.
                with contextlib.suppress(queue.Full):
                    self._queue.put_nowait(item)
                break
            if isinstance(item, _BatchableInsert):
                batch.append(item)
            else:
                # Non-batchable item — put it back for the main loop.
                with contextlib.suppress(queue.Full):
                    self._queue.put_nowait(item)
                break

        try:
            with contextlib.closing(conn.cursor()) as cursor:
                if len(batch) >= _BATCH_INSERT_MIN:
                    # ER-78: multi-row INSERT inside one transaction.
                    placeholders = ",".join(["(?, ?, ?, ?, ?, ?, ?)"] * len(batch))
                    params: list[Any] = []
                    for it in batch:
                        params.extend(
                            (
                                it.text,
                                it.duration,
                                it.model,
                                it.device,
                                it.word_count,
                                it.char_count,
                                it.language,
                            )
                        )
                    cursor.execute(
                        f"INSERT INTO transcriptions "
                        f"(text, duration, model, device, word_count, char_count, language) "
                        f"VALUES {placeholders}",
                        params,
                    )
                    conn.commit()
                    last_row_id = cursor.lastrowid
                    for it in batch:
                        if it.future is not None:
                            with contextlib.suppress(concurrent.futures.InvalidStateError):
                                it.future.set_result(last_row_id if last_row_id is not None else -1)
                    log.debug(
                        "[HISTORY_DB] batched %d transcription INSERTs into one transaction",
                        len(batch),
                    )
                else:
                    # Below the batching threshold — insert each row
                    # individually (original behavior). Still one COMMIT
                    # per row, but the per-row overhead is negligible for
                    # 1-2 rows.
                    for it in batch:
                        cursor.execute(
                            "INSERT INTO transcriptions "
                            "(text, duration, model, device, word_count, char_count, language) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                it.text,
                                it.duration,
                                it.model,
                                it.device,
                                it.word_count,
                                it.char_count,
                                it.language,
                            ),
                        )
                        conn.commit()
                        row_id = cursor.lastrowid
                        if it.future is not None:
                            with contextlib.suppress(concurrent.futures.InvalidStateError):
                                it.future.set_result(row_id if row_id is not None else -1)
                        if row_id is not None:
                            log.debug("Added transcription %d: %d chars", row_id, it.char_count)
        except BaseException as e:  # noqa: BLE001 — propagate to futures
            # Resolve all futures with the exception so wait=True
            # callers (if any — add_transcription is fire-and-forget)
            # don't hang.
            for it in batch:
                if it.future is not None:
                    with contextlib.suppress(concurrent.futures.InvalidStateError):
                        it.future.set_exception(e)
            # Re-raise so the caller (_writer_loop / _drain_remaining)
            # sees the failure and runs its standard rollback +
            # fire-and-forget log path.
            raise

    def _drain_remaining(self, conn: sqlite3.Connection) -> None:
        """Drain any remaining queued items before shutdown.

        Called after the shutdown sentinel is received. Ensures
        fire-and-forget writes submitted before close() are persisted.

        ER-78: ``_BatchableInsert`` items are routed through
        :meth:`_drain_batchable_inserts` so the shutdown drain also
        benefits from the multi-row INSERT optimization when 3+ inserts
        are queued at shutdown time.
        """
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _SHUTDOWN_SENTINEL:
                continue
            # ER-78: route batchable inserts through the batching path.
            if isinstance(item, _BatchableInsert):
                try:
                    self._drain_batchable_inserts(conn, item)
                except BaseException as e:  # noqa: BLE001
                    with contextlib.suppress(sqlite3.Error):
                        conn.rollback()
                    log.error(
                        "[HISTORY_DB] Fire-and-forget batched insert failed during shutdown drain: %s",
                        e,
                    )
                else:
                    with contextlib.suppress(sqlite3.Error):
                        conn.rollback()
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
                # pages) to avoid flooding the log every 300s. Tiny
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
            # attempt in 300s will retry.
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

        Delegates to :func:`voice_typer.server.history_db_internals.schema.open_write_conn`.
        See that function for the full rationale (WAL mode, synchronous
        level, busy_timeout, cache_size, ``secure_delete=ON`` for
        G4-M-04, SEC-007 POSIX file/dir permissions).
        """
        from voice_typer.server.history_db_internals.schema import open_write_conn

        return open_write_conn(self.db_path)

    def _check_wal_mode(self, conn: sqlite3.Connection) -> None:
        """Verify WAL mode is actually enabled.

        Delegates to
        :func:`voice_typer.server.history_db_internals.schema.check_wal_mode`.
        Logs a warning if SQLite silently fell back to rollback-journal
        mode (network filesystems, antivirus locks, read-only FS).
        """
        from voice_typer.server.history_db_internals.schema import check_wal_mode

        check_wal_mode(conn, self.db_path)

    def _init_db_schema(
        self,
        conn: sqlite3.Connection,
        _is_recovery: bool = False,
    ) -> sqlite3.Connection:
        """Initialize the database schema and run migrations.

        Delegates to
        :func:`voice_typer.server.history_db_internals.schema.init_schema`.
        The free function takes ``self`` (the HistoryDB instance) so it
        can call back into ``_backup_before_migration`` and
        ``_maybe_recover_from_corruption`` (which still live on this
        class) and so it can set ``self._init_error`` on migration
        failure. Returns the connection to use (may be a fresh one if
        corruption was detected and the DB was recreated). Callers must
        use the returned connection, not the one they passed in.

        See the delegated function for the full migration / index /
        integrity-check rationale (G4-CR-02, G4-CR-03, G4-M-03, FIX).
        """
        from voice_typer.server.history_db_internals.schema import init_schema

        return init_schema(self, conn, _is_recovery=_is_recovery)

    def _backup_before_migration(self, current_version: int) -> None:
        """Best-effort copy of the DB (and ``-wal``/``-shm``
        sidecars) to ``history.db.pre-migration-v<from>.bak`` before a
        migration runs.

        Best-effort: if the copy fails (disk full, permissions,
        cross-device), log + continue — DO NOT block the migration on
        backup failure. The user's history is valuable, but blocking
        the schema migration on a backup failure would leave the app
        in a worse state (stuck on the old schema) than simply
        proceeding without a backup.

        Single-slot naming: ``history.db.pre-migration-v<from>.bak``
        (NOT timestamped). A second migration run would skip the
        backup entirely because ``current_version ==
        _CURRENT_SCHEMA_VERSION`` (the backup is only taken when
        ``current_version < _CURRENT_SCHEMA_VERSION``, checked in the
        caller). Even if the same version were migrated twice (e.g.
        a v3 -> v4 migration followed by a v3 -> v4 retry after a
        failure), the second backup would overwrite the first —
        acceptable because the first backup was of the same DB state.

        The copy uses ``shutil.copy2`` (preserves mtime/mode) which
        is the closest Python equivalent to the Rust
        ``atomic_copy`` helper (``src-tauri/src/migrate.rs:476``).
        ``copy2`` is NOT atomic (it reads + writes), but for a
        best-effort pre-migration backup the simplicity outweighs
        atomicity — a crash mid-copy leaves a partial .bak file,
        which the user can detect by size and discard.
        """
        import shutil

        try:
            bak_main = self.db_path.with_name(f"{self.db_path.name}.pre-migration-v{current_version}.bak")
            # Copy the main DB file. ``copy2`` preserves mtime/mode.
            if self.db_path.exists():
                shutil.copy2(str(self.db_path), str(bak_main))
            # Copy the -wal and -shm sidecars if they exist (WAL mode).
            # These hold uncheckpointed pages that would otherwise be
            # lost — including them makes the backup a complete
            # restorable snapshot.
            for sidecar in ("-wal", "-shm"):
                src = self.db_path.with_name(self.db_path.name + sidecar)
                if src.exists():
                    dst = bak_main.with_name(bak_main.name + sidecar)
                    shutil.copy2(str(src), str(dst))
            log.info(
                "[HISTORY_DB] Pre-migration backup created: %s (from schema v%d)",
                bak_main.name,
                current_version,
            )
        except OSError as e:
            # Best-effort: do NOT block the migration on backup
            # failure. The user's history is more valuable than the
            # backup — a stuck migration would leave the app on the
            # old schema, which is worse than proceeding without a
            # backup.
            log.warning(
                "[HISTORY_DB] Pre-migration backup FAILED (continuing with migration anyway): %s",
                e,
            )

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

        Memory management: each read connection carries a 20 MB SQLite
        page cache (``PRAGMA cache_size=-20000``). When the owning
        thread exits, its ``threading.local()`` storage is GC'd but
        the connection itself stays alive (held by
        ``_all_read_connections``) until ``close()`` runs. To avoid
        unbounded memory growth across long-running app sessions with
        thread pool churn, ``_prune_dead_read_connections_locked`` is
        called on each new-connection creation: it walks the list,
        closes connections whose owning thread has exited, and drops
        them from the list. The pruning is O(n) but runs only on
        first-call-per-thread (not on every read), so the amortized
        cost is negligible.
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
                self._all_read_connections.append((threading.get_ident(), conn))
                # Opportunistic GC: close connections whose owning
                # thread has exited. This is the only place we prune
                # (we don't run a background reaper), so we run it on
                # every new-connection creation to keep the list
                # bounded. The check is cheap (one threading.enumerate()
                # call + a list filter).
                self._prune_dead_read_connections_locked()
        return self._read_local.conn

    def _prune_dead_read_connections_locked(self) -> None:
        """Close read connections whose owning thread has exited.

        Must be called with ``self._connections_lock`` held. Walks
        ``_all_read_connections`` and closes any connection whose
        ``thread_ident`` is not in the set of currently-alive threads
        (per ``threading.enumerate``). The current thread's ident is
        always treated as live (we're running on it). This bounds
        memory growth: without pruning, each dead reader thread's
        20 MB page cache would persist until ``close()`` ran.

        Note: threads created via C extensions (not via
        ``threading.Thread``) won't appear in ``threading.enumerate()``,
        so their connections won't be pruned. This is acceptable —
        Voice Typer's reader threads (IPC handlers, tray, dictation
        pipeline, tests) are all ``threading.Thread`` instances.
        """
        if not self._all_read_connections:
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
        for ident, conn in self._all_read_connections:
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
                    "[HISTORY_DB] Pruned dead-thread read connection (thread_ident=%s); released ~20 MB page cache.",
                    ident,
                )
        self._all_read_connections = kept

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
        # ER-78: the dropped item may be a (fn, future) tuple OR a
        # _BatchableInsert structured payload. Extract the future (if
        # any) from either shape and resolve it with HistoryDBError so
        # wait=True callers don't hang.
        if isinstance(dropped, _BatchableInsert):
            dropped_future = dropped.future
        else:
            _, dropped_future = dropped
        if dropped_future is not None:  # CR-78 / PERF-5: the dropped future must be resolved
            # with a clear, machine-greppable message so callers
            # that catch HistoryDBError can distinguish "queue full"
            # from other failure modes (e.g. "Writer is shutting
            # down" or "Dropped during shutdown sentinel enqueue").
            # The literal "queue full" substring is part of the
            # contract asserted by TestQueueBounded.
            with contextlib.suppress(concurrent.futures.InvalidStateError):
                dropped_future.set_exception(
                    HistoryDBError(
                        "queue full; dropped oldest write to make room for newer write (writer thread may be stalled)"
                    )
                )
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

        ER-36: also signals + joins the periodic retention thread
        (if :meth:`schedule_periodic_retention` was called) so close()
        fully quiesces the HistoryDB's daemon threads.

        Before sending the shutdown sentinel, submit a final
        write closure to the writer thread that runs
        ``PRAGMA wal_checkpoint(TRUNCATE)`` and waits for it. This
        flushes all WAL pages back to the main DB file and truncates
        ``history.db-wal`` to zero size, so a clean shutdown leaves no
        uncheckpointed WAL residue (which can be ~21 MB after 24h of
        dictation at 30 entries/min × ~500 bytes/entry). Idempotent
        with the GDPR-export checkpoint at ``service.py:846`` (which
        is the same PRAGMA, called explicitly before the zip is
        built). Wrapped in ``contextlib.suppress(sqlite3.Error)`` so
        a checkpoint failure doesn't block shutdown — the WAL will be
        checkpointed on the next launch anyway.
        """
        # ER-36: stop the periodic retention thread BEFORE setting
        # _shutdown so its inner loop sees a clean stop_event signal
        # and exits without trying to call apply_retention (which
        # would no-op on a shutdown DB but would still log noise).
        self._stop_periodic_retention()
        if self._shutdown.is_set():
            # Already closed — just make sure read conns are gone.
            with self._connections_lock:
                for _ident, conn in self._all_read_connections:
                    with contextlib.suppress(sqlite3.Error):
                        conn.close()
                self._all_read_connections.clear()
            return
        self._shutdown.set()
        # Best-effort wal_checkpoint(TRUNCATE) before shutdown.
        # Submit a final closure to the writer thread so the checkpoint
        # runs on the only write-capable connection (the writer's). The
        # closure is wrapped in ``contextlib.suppress(sqlite3.Error)``
        # so a checkpoint failure (e.g. DB busy, disk full) doesn't
        # block shutdown. The ``checkpoint()`` method itself swallows
        # sqlite3.Error internally (see ``_do_checkpoint`` at the
        # call site below), so the suppress here is belt-and-braces.
        # Skip if the writer is already dead (e.g. init failed) —
        # ``checkpoint()`` returns False in that case.
        if self._writer_thread.is_alive() and self._init_error is None:
            with contextlib.suppress(sqlite3.Error, HistoryDBError):
                self.checkpoint(truncate=True)
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
        # Each entry is a ``(thread_ident, connection)`` tuple; we
        # unpack to close the connection regardless of which thread
        # originally owned it (close() can be called from any thread).
        with self._connections_lock:
            for _ident, conn in self._all_read_connections:
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
        # FR-10: early-return guard — if the writer thread never
        # started (init error) or died, return -1 immediately instead
        # of silently enqueuing to a dead writer's queue.
        if self._init_error is not None or not self._writer_thread.is_alive():
            log.error(
                "[HISTORY_DB] add_transcription refused — writer is unavailable: %s",
                self.health_check()["error"],
            )
            return -1
        try:
            word_count = len(text.split())
            char_count = len(text)
            if self._shutdown.is_set():
                log.debug("[HISTORY_DB] add_transcription submitted after shutdown — dropped.")
                return -1
            item = _BatchableInsert(
                text=text,
                duration=duration,
                model=model,
                device=device,
                word_count=word_count,
                char_count=char_count,
                language=language,
                future=None,  # fire-and-forget
            )
            # PERF-5: bounded queue (maxsize=_WRITE_QUEUE_MAXSIZE). Use
            # put_nowait + drop-oldest so a stalled writer doesn't block
            # the calling thread indefinitely. We can't reuse
            # _submit_write here because it enqueues (callable, future)
            # tuples — _BatchableInsert is its own queue item shape.
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._drop_oldest_for_overflow(None)
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    log.warning("[HISTORY_DB] Queue still full after drop-oldest — add_transcription dropped.")
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
                with contextlib.closing(conn.cursor()) as cursor:
                    cursor.execute("DELETE FROM transcriptions WHERE id = ?", (transcription_id,))
                    conn.commit()
                    return cursor.rowcount > 0

            result = self._submit_write(_do_delete, wait=True)
            if result is None:
                # Writer shut down — treat as failure.
                return False
            if result:
                # TY-20: invalidate the count cache.
                self._invalidate_history_count_cache()
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
                with contextlib.closing(conn.cursor()) as cursor:
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
            if result and result > 0:
                # TY-20: invalidate the count cache.
                self._invalidate_history_count_cache()
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
                with contextlib.closing(conn.cursor()) as cursor:
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
            if result:
                # TY-20: invalidate the count cache.
                self._invalidate_history_count_cache()
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
                with contextlib.closing(conn.cursor()) as cursor:
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

        Delegates to
        :func:`voice_typer.server.history_db_internals.retention.apply_retention`.
        See that function for the full rationale (DEAD-012 fallback
        wiring, IMPL-A chunked deletes on the writer thread, G4-M-05
        conditional VACUUM, TY-20 count-cache invalidation, ERR-013
        sentinel-on-error contract).
        """
        from voice_typer.server.history_db_internals.retention import apply_retention

        return apply_retention(
            self,
            retention_days=retention_days,
            max_entries=max_entries,
            retention_count=retention_count,
        )

    # ──────────────────────────────────────────────────────────────
    # Periodic retention scheduling (ER-36)
    # ──────────────────────────────────────────────────────────────

    def schedule_periodic_retention(
        self,
        interval_s: float = 600.0,
        app: Any = None,
        *,
        retention_days: int = 0,
        max_entries: int = 0,
        retention_count: int = 0,
    ) -> None:
        """ER-36: spawn a daemon thread that periodically calls ``apply_retention``.

        Delegates to
        :func:`voice_typer.server.history_db_internals.retention.schedule_periodic_retention`.
        The free function takes ``self`` (the HistoryDB instance) so it
        can mutate ``_retention_stop_event`` / ``_retention_thread`` and
        call back into ``apply_retention`` / ``_stop_periodic_retention``.
        See the delegated function for the full rationale (ER-36
        re-entrancy guard, ThreadRegistry registration, idempotent
        re-scheduling).
        """
        from voice_typer.server.history_db_internals.retention import schedule_periodic_retention

        schedule_periodic_retention(
            self,
            interval_s=interval_s,
            app=app,
            retention_days=retention_days,
            max_entries=max_entries,
            retention_count=retention_count,
        )

    def _stop_periodic_retention(self) -> None:
        """ER-36: signal the periodic retention thread to stop and join it.

        Delegates to
        :func:`voice_typer.server.history_db_internals.retention.stop_periodic_retention`.
        Called by :meth:`close` and by :meth:`schedule_periodic_retention`
        (to support idempotent re-scheduling). Best-effort — if the
        thread doesn't exit within 2s (e.g. stuck in a long VACUUM), it
        is left to die as a daemon at process exit.
        """
        from voice_typer.server.history_db_internals.retention import stop_periodic_retention

        stop_periodic_retention(self)

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

        TY-8: the ``text`` column is projected to a 500-char preview
        via ``SUBSTR(text, 1, 500)`` to keep list responses under the
        1 MiB WS frame cap. Two new fields are added per row:
        ``text_truncated`` (bool) and ``text_full_length`` (int).
        """
        try:
            conn = self._get_read_conn()
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        SUBSTR(text, 1, ?) AS text,
                        LENGTH(text) AS text_full_length,
                        timestamp,
                        duration,
                        model,
                        device,
                        word_count,
                        char_count,
                        favorite,
                        language
                    FROM transcriptions
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """,
                    (_HISTORY_TEXT_PREVIEW_LENGTH, limit, offset),
                )
                rows = cursor.fetchall()
            return [_project_text_row(row) for row in rows]
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
            with contextlib.closing(conn.cursor()) as cur:
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

        FTS5 is used for any query that yields at least one tokenizable
        character (``_is_fts_compatible_query``). For empty queries and
        queries consisting solely of separator characters (e.g. ``%`` or
        ``_``), we fall back to the pre-CR-49 LIKE path so literal
        wildcards still match — preserving the contract pinned by
        ``test_search_treats_like_wildcards_as_literals`` and
        ``test_empty_query_returns_all_rows``. ``_sanitize_fts_query``
        wraps each whitespace-separated token in double quotes so the
        user's input is treated as a literal phrase rather than FTS5
        MATCH syntax (e.g. ``foo*`` matches the literal token ``foo*``,
        not a prefix query).
        """
        try:
            conn = self._get_read_conn()
            with contextlib.closing(conn.cursor()) as cursor:
                capped = query[:_MAX_SEARCH_QUERY_CHARS]
                if capped and _is_fts_compatible_query(capped):
                    fts_query = _sanitize_fts_query(capped)
                    cursor.execute(
                        """
                        SELECT
                            t.id,
                            SUBSTR(t.text, 1, ?) AS text,
                            LENGTH(t.text) AS text_full_length,
                            t.timestamp,
                            t.duration,
                            t.model,
                            t.device,
                            t.word_count,
                            t.char_count,
                            t.favorite,
                            t.language
                        FROM transcriptions t
                        JOIN transcriptions_fts AS f ON f.rowid = t.id
                        WHERE transcriptions_fts MATCH ?
                        ORDER BY t.timestamp DESC
                        LIMIT ? OFFSET ?
                    """,
                        (_HISTORY_TEXT_PREVIEW_LENGTH, fts_query, limit, offset),
                    )
                else:
                    # LIKE fallback.
                    pattern = _prepare_like_search_pattern(query)
                    cursor.execute(
                        """
                        SELECT
                            id,
                            SUBSTR(text, 1, ?) AS text,
                            LENGTH(text) AS text_full_length,
                            timestamp,
                            duration,
                            model,
                            device,
                            word_count,
                            char_count,
                            favorite,
                            language
                        FROM transcriptions
                        WHERE text LIKE ? ESCAPE '\\'
                        ORDER BY timestamp DESC
                        LIMIT ? OFFSET ?
                    """,
                        (_HISTORY_TEXT_PREVIEW_LENGTH, pattern, limit, offset),
                    )
                rows = cursor.fetchall()
            return [_project_text_row(row) for row in rows]
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
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        SUBSTR(text, 1, ?) AS text,
                        LENGTH(text) AS text_full_length,
                        timestamp,
                        duration,
                        model,
                        device,
                        word_count,
                        char_count,
                        favorite,
                        language
                    FROM transcriptions
                    WHERE favorite = 1
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """,
                    (_HISTORY_TEXT_PREVIEW_LENGTH, limit, offset),
                )
                rows = cursor.fetchall()
            return [_project_text_row(row) for row in rows]
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
            with contextlib.closing(conn.cursor()) as cursor:
                # Sargable predicate. ``DATE(timestamp) = DATE('now')`` applies
                # a function to every row's ``timestamp`` column, so SQLite
                # cannot use ``idx_timestamp`` and falls back to a full table
                # scan. The range form ``timestamp >= DATE('now') AND
                # timestamp < DATE('now', '+1 day')`` lets the query planner
                # use the index. ``timestamp`` is stored as an ISO-8601 string
                # (``datetime.now().isoformat()``), so lexicographic comparison
                # against the date-only ``DATE('now')`` boundary is correct:
                # any ISO-8601 timestamp from today sorts after "YYYY-MM-DD"
                # (today's midnight) and before "YYYY-MM-DD" of tomorrow.
                cursor.execute("""
                    SELECT
                        COUNT(*) as count,
                        SUM(char_count) as chars,
                        SUM(word_count) as word_count,
                        SUM(duration) as duration
                    FROM transcriptions
                    WHERE timestamp >= DATE('now')
                      AND timestamp < DATE('now', '+1 day')
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
    # TY-8 / TY-20: on-demand full-text + total-count accessors
    # ──────────────────────────────────────────────────────────────

    def get_transcription_text(
        self,
        transcription_id: int,
        *,
        raise_on_error: bool = False,
    ) -> dict:
        """TY-8: return the FULL ``text`` of a single transcription row.

        Companion to the 500-char ``text`` preview returned by
        ``get_recent`` / ``search`` / ``get_favorites``.
        Returns ``{"id": int, "text": str}`` (empty string if not found).
        """
        try:
            conn = self._get_read_conn()
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute(
                    "SELECT text FROM transcriptions WHERE id = ?",
                    (transcription_id,),
                )
                row = cursor.fetchone()
            if row is None:
                return {"id": transcription_id, "text": ""}
            return {"id": transcription_id, "text": row[0] or ""}
        except Exception as e:
            log.error(
                "[HISTORY] Failed to get transcription text for id=%s: %s",
                transcription_id,
                e,
            )
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return {"id": transcription_id, "text": ""}

    def get_history_count(self, *, raise_on_error: bool = False) -> int:
        """TY-20: return the total number of transcription rows.

        ``SELECT COUNT(*) FROM transcriptions`` is O(N) in SQLite.
        Caching pattern mirrors ``service/model.py:get_model_status``:
        a 60s TTL with immediate invalidation on
        delete/clear_all/restore/apply_retention via
        ``_invalidate_history_count_cache``. Fire-and-forget
        ``add_transcription`` does NOT invalidate — the count grows
        by 1 per dictation, and a 60s-stale-by-N count is fine for a
        "Total Dictations" stat card.
        """
        now = time.monotonic()
        with self._history_count_cache_lock:
            if (
                self._history_count_cache is not None
                and (now - self._history_count_cache_ts) < _HISTORY_COUNT_CACHE_TTL_S
            ):
                return self._history_count_cache
        try:
            conn = self._get_read_conn()
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute("SELECT COUNT(*) FROM transcriptions")
                row = cursor.fetchone()
            count = int(row[0]) if row is not None else 0
            with self._history_count_cache_lock:
                self._history_count_cache = count
                self._history_count_cache_ts = time.monotonic()
            return count
        except Exception as e:
            log.error("[HISTORY] Failed to get history count: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return 0

    def _invalidate_history_count_cache(self) -> None:
        """TY-20: drop the cached total-count int."""
        with self._history_count_cache_lock:
            self._history_count_cache = None
            self._history_count_cache_ts = 0.0

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
