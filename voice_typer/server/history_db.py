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
                                                     (controlled by
                                                     _WAL_CHECKPOINT_INTERVAL)

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

Sentinel contract. Every public method returns a fixed
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
import functools
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

from voice_typer.server.branding import APP_NAME
from voice_typer.server.history_db_internals.retention import RetentionResult
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

_MAX_SEARCH_QUERY_CHARS = 200

# hard upper bound on the total time a blocking _submit_write
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

# maximum number of transcription rows bundled into a single
# multi-row INSERT. SQLite's default ``SQLITE_MAX_VARIABLE_NUMBER`` is
# 999 (or 32766 on newer builds); 7 placeholder columns × 100 rows =
# 700 placeholders — well under the conservative 999 bound. Capping the
# batch size also bounds the peak memory used by the parameter list and
# the WAL frame count of a single transaction.
_BATCH_INSERT_CAP = 100

# minimum number of pending _BatchableInsert items required to
# trigger the multi-row INSERT path. Below this threshold each row is
# inserted individually (the per-transaction overhead saving doesn't
# justify the multi-row SQL construction for 1-2 rows).
#
# lowered from 3 to 1 so even single-row insertions use the
# multi-row INSERT path (one INSERT + one COMMIT per batch). The
# original threshold of 3 meant that for typical user dictation (one
# phrase, then a pause), the queue drained to 1 item every time and
# the batching optimization never engaged. With MIN=1, the multi-row
# path is taken for batches of 1+; for 2-row batches this collapses
# two separate INSERT+COMMIT cycles into one (1 COMMIT instead of 2).
# Per-row overhead is identical for 1-row batches (both paths do 1
# INSERT + 1 COMMIT), so the change is a pure simplification with no
# regression for the single-row case.
_BATCH_INSERT_MIN = 1

# TTL (seconds) for the get_history_count cache.
_HISTORY_COUNT_CACHE_TTL_S = 60.0

# Interval (seconds) at which the periodic read-conn prune daemon
# walks ``_all_read_connections`` and closes connections whose owning
# thread has exited. Defined at module level (not as a class
# attribute) so tests can monkeypatch ``history_db._READ_CONN_PRUNE_INTERVAL_S``
# and have the prune thread pick up the new value on the next restart.
_READ_CONN_PRUNE_INTERVAL_S: float = 60.0

# TTL (seconds) for the ``get_today_stats`` cache.
#
# ``get_today_stats`` runs an aggregating scan
# (``SELECT COUNT(*), SUM(char_count), SUM(word_count), SUM(duration)
# FROM transcriptions WHERE timestamp >= DATE('now') AND timestamp <
# DATE('now', '+1 day')``) on every call. The Dashboard refreshes on
# every ``transcription_final`` event; at the rate_limiter's 1
# call/sec/client cap, this was continuous background CPU on the reader
# thread during active dictation.
#
# The cache mirrors the ``get_history_count`` 60s pattern but with a
# 15s TTL and STRICTER invalidation — invalidated on EVERY mutation
# that could change today's stats (add/delete/clear/restore/retention),
# including fire-and-forget ``add_transcription`` (today's stats grow
# by 1 per dictation and the user wants to see them update live, so we
# invalidate immediately rather than serving a stale-by-1 count).
_TODAY_STATS_CACHE_TTL_S = 15.0

# maximum characters of ``text`` returned in list responses.
_HISTORY_TEXT_PREVIEW_LENGTH = 500

# hard upper bound on the ``limit`` parameter for the public list
# methods (get_recent / search / get_favorites). Prevents a single
# IPC call from materialising an unbounded result set (each row
# carries up to _HISTORY_TEXT_PREVIEW_LENGTH chars of text plus 10
# metadata fields — a hostile or buggy caller passing limit=10**9
# would otherwise OOM the renderer). Callers asking for more than
# this get silently clamped; the renderer paginates via cursor
# parameters (before_timestamp / before_id) for deep reads.
_MAX_LIST_LIMIT = 500

# regex used by ``HistoryDB._try_iterdump_recovery`` to
# filter iterdump() output and keep only ``INSERT INTO transcriptions``
# statements (the user-data rows). Schema rows (``schema_meta``,
# ``sqlite_sequence``) and FTS5 shadow-table rows
# (``transcriptions_fts`` and its ``_*_`` shadow tables) are
# intentionally excluded — the fresh DB's schema init recreates the
# schema, and replaying ``schema_meta`` would PRIMARY KEY-conflict
# with the version row that ``init_schema`` writes.
#
# iterdump() emits statements of the form::
#
#     INSERT INTO "transcriptions" VALUES(1, 'text', ...);
#     INSERT INTO "schema_meta" VALUES('version','3');
#     INSERT INTO "transcriptions_fts" VALUES(...);
#
# The ``"?`` allows for the optional double-quote that iterdump
# emits around the table name; ``\b`` ensures ``transcriptions_fts``
# is NOT matched (``s`` and ``_`` are both word chars, so there's
# no word boundary between them).
_INSERT_TRANSCRIPTIONS_RE = re.compile(
    r'^INSERT\s+INTO\s+"?transcriptions"?\b',
    re.IGNORECASE,
)


class _BatchableInsert:
    """structured payload for batchable transcription INSERTs.

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

    previously every method returned a different sentinel
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
from voice_typer.server.history_db_internals.schema import (  # noqa: E402,F401 — backward-compat re-export so tests reading history_db._MIGRATIONS / _CURRENT_SCHEMA_VERSION keep working
    _CURRENT_SCHEMA_VERSION,
    _MIGRATION_V2,
    _MIGRATION_V3,
    _MIGRATIONS,
)


def _secure_copy_db_file(src: Path, dst: Path) -> None:
    """symlink-safe, fsync-on-write binary file copy.

    Replaces ``shutil.copy2`` in ``_backup_before_migration`` (and
    other DB-sidecar backup paths). ``shutil.copy2`` follows symlinks on
    BOTH source and destination:

    - If ``src`` is a symlink planted by an attacker, ``copy2`` reads
      the symlink TARGET's content (info disclosure).
    - If ``dst`` is a symlink planted by an attacker, ``copy2`` writes
      THROUGH it to the symlink target (backup hijack / data destruction).

    This helper refuses both: on POSIX it opens ``src`` with
    ``O_RDONLY | O_NOFOLLOW`` and ``dst`` with
    ``O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW`` (mode ``0o600``);
    on Windows it rejects reparse points via ``os.lstat`` before
    falling back to a regular binary copy. After the copy it
    ``fsync``s the destination fd so the backup is durable.
    """
    import shutil

    if not is_windows():
        # POSIX: O_NOFOLLOW on both source and destination.
        src_fd = -1
        dst_fd = -1
        try:
            src_fd = os.open(str(src), os.O_RDONLY | os.O_NOFOLLOW)
            dst_fd = os.open(
                str(dst),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(src_fd, "rb", closefd=False) as f_src, os.fdopen(dst_fd, "wb", closefd=False) as f_dst:
                shutil.copyfileobj(f_src, f_dst)
                f_dst.flush()
                os.fsync(f_dst.fileno())
        finally:
            if src_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(src_fd)
            if dst_fd != -1:
                with contextlib.suppress(OSError):
                    os.close(dst_fd)
    else:
        # Windows: O_NOFOLLOW is not supported. Reject reparse points
        # explicitly via os.lstat (mirrors ``_secure_read_text``'s
        # Windows branch) and fall back to a regular binary copy with
        # fsync.
        for p in (src, dst):
            try:
                attrs = getattr(os.lstat(str(p)), "st_file_attributes", 0) or 0
            except (AttributeError, OSError):
                attrs = 0
            if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise OSError(f"Refusing to follow reparse point during copy: {p}")
        with open(src, "rb") as f_src, open(dst, "wb") as f_dst:
            shutil.copyfileobj(f_src, f_dst)
            f_dst.flush()
            os.fsync(f_dst.fileno())


# Search / LIKE / FTS5 helpers + row projection live in
# ``voice_typer.server.history_db_internals.search``. They are
# re-exported here under their original (underscore-prefixed) names so
# existing callers (and tests that import ``history_db._is_fts_compatible_query``
# etc.) keep working unchanged. The re-exported callables are the SAME
# function objects that ``history_db_internals.search.search`` /
# ``get_recent`` / ``get_favorites`` call internally, so monkeypatching
# the module-level helper via ``history_db._is_fts_compatible_query`` is
# NOT observed by the delegating methods — callers that need to
# monkeypatch should target ``history_db_internals.search`` directly.
# (No existing test monkeypatches these helpers at the module level;
# they only call them directly, which works through the re-export.)
from voice_typer.server.history_db_internals.search import (  # noqa: E402,F401 — backward-compat re-export
    is_fts_compatible_query as _is_fts_compatible_query,
)


def _wrap_write(failure_value, fail_verb, writer_label):
    """Decorator: encapsulate the dual-except ``raise_on_error`` boilerplate.

    Applied to write methods (``delete`` / ``restore`` / ``clear_all`` /
    ``toggle_favorite``) that call ``_submit_write`` and may raise
    ``HistoryDBError`` when the writer thread is unavailable.

    - ``HistoryDBError`` (writer unavailable): re-raise if
      ``raise_on_error``, else log ``"Writer unavailable for
      {writer_label}"`` and return ``failure_value``.
    - Other ``Exception``: re-raise as ``HistoryDBError`` if
      ``raise_on_error``, else log ``"Failed to {fail_verb}"`` and
      return ``failure_value``.

    ``failure_value`` may be a callable (factory) so mutable sentinels
    (``[]`` / ``{}``) are freshly constructed on each failure return —
    matching the per-call literal the inline code previously used.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            raise_on_error = kwargs.pop("raise_on_error", False)
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                if isinstance(e, HistoryDBError):
                    if raise_on_error:
                        raise
                    log.error("[HISTORY] Writer unavailable for %s", writer_label)
                    return failure_value() if callable(failure_value) else failure_value
                log.error("[HISTORY] Failed to %s: %s", fail_verb, e)
                if raise_on_error:
                    raise HistoryDBError(str(e)) from e
                return failure_value() if callable(failure_value) else failure_value

        return wrapper

    return decorator


def _wrap_read(failure_value, fail_verb):
    """Decorator: encapsulate the single-except ``raise_on_error`` boilerplate.

    Applied to read methods (``get_recent`` / ``search`` / ``get_favorites`` /
    ``get_today_stats``) that use ``_get_read_conn``.

    - Any ``Exception``: re-raise as ``HistoryDBError`` if
      ``raise_on_error``, else log ``"Failed to {fail_verb}"`` and
      return ``failure_value``.

    ``failure_value`` may be a callable (factory) so mutable sentinels
    are freshly constructed on each failure return.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            raise_on_error = kwargs.pop("raise_on_error", False)
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                log.error("[HISTORY] Failed to %s: %s", fail_verb, e)
                if raise_on_error:
                    raise HistoryDBError(str(e)) from e
                return failure_value() if callable(failure_value) else failure_value

        return wrapper

    return decorator


# module-level WeakSet tracking all live HistoryDB instances. Tests
# that construct HistoryDB via ``_MockApp`` helpers frequently leak the
# instance (and its ``HistoryDBWriter`` daemon thread) because the test
# fixture only calls ``IPCServer.stop()``, which does NOT close
# ``app.history_db``. On Windows the accumulated daemon threads eventually
# trip a native limit and crash the whole pytest process mid-suite ().
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
        # generation counter bumped on corruption-recovery
        # read-connection invalidation. Each thread-local read
        # connection remembers the generation it was opened at; if
        # the counter bumps (because the corrupt DB was renamed and a
        # fresh DB opened), the next ``_get_read_conn`` call closes
        # the stale conn and opens a new one on the fresh file.
        # Without this, POSIX open FDs would keep pointing at the
        # renamed (corrupt) file and readers would return stale data.
        self._read_conn_generation: int = 0
        # Write queue: items are (callable, future) tuples, OR
        # _BatchableInsert instances (), OR the _SHUTDOWN_SENTINEL
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
        # re-entrancy guard for apply_retention. The periodic
        # retention scheduler spawns a daemon thread that calls
        # apply_retention on a fixed interval; if a previous run is
        # still in flight (e.g. a multi-batch VACUUM on a huge DB),
        # the next tick acquires this lock non-blocking and skips
        # rather than queueing a second concurrent sweep.
        self._retention_lock = threading.Lock()
        # stop event for the periodic retention thread. Set by
        # close() (and by re-scheduling) to ask the daemon loop to exit.
        self._retention_stop_event: threading.Event | None = None
        # handle to the periodic retention daemon thread (for
        # join-on-close).
        self._retention_thread: threading.Thread | None = None
        # TTL cache for ``get_history_count``.
        self._history_count_cache: int | None = None
        self._history_count_cache_ts: float = 0.0
        self._history_count_cache_lock = threading.Lock()
        # TTL cache for ``get_today_stats``. See
        # ``_TODAY_STATS_CACHE_TTL_S`` for the rationale (15s TTL,
        # strict invalidation on every mutation). The cache stores a
        # COPY of the stats dict so callers can mutate the returned
        # dict without corrupting the cached value (see
        # ``test_cache_returns_independent_dict_copy``).
        self._today_stats_cache: dict | None = None
        self._today_stats_cache_ts: float = 0.0
        self._today_stats_cache_lock = threading.Lock()
        # per-instance counter of FTS5 'rebuild' failures after
        # ``apply_retention`` / ``clear_all`` bulk deletes. Incremented
        # each time the FTS5 ``'rebuild'`` command raises a
        # ``sqlite3.Error`` — those failures leave deleted dictated
        # text recoverable from ``transcriptions_fts_data`` (GDPR
        # Art. 17 /  violation), so the counter is surfaced in
        # diagnostics and paired with an ``event_bus`` event so the
        # renderer can show a toast.
        self._fts5_rebuild_failures: int = 0
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
        self._read_conn_prune_stop_event: threading.Event | None = None
        self._read_conn_prune_thread: threading.Thread | None = None
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
        # register in the module-level WeakSet so the test conftest
        # can close leaked instances after each test (prevents the daemon
        # writer thread from accumulating across the full pytest run and
        # crashing the process on Windows via native thread-limit exhaustion).
        _LIVE_INSTANCES.add(self)
        # start the periodic prune daemon (best-effort —
        # failures are logged + swallowed so a healthy DB never fails
        # to construct just because the prune thread couldn't start).
        with contextlib.suppress(Exception):
            self._start_periodic_read_conn_prune()

    # ──────────────────────────────────────────────────────────────
    # Periodic read-conn prune
    # ──────────────────────────────────────────────────────────────

    def _start_read_conn_prune_thread(self) -> None:
        """Start the periodic read-conn prune daemon.

        Delegates to
        :func:`voice_typer.server.history_db_internals.reader._start_read_conn_prune_thread`.
        """
        from voice_typer.server.history_db_internals import reader

        reader._start_read_conn_prune_thread(self)

    # Back-compat alias for the previous name (kept so external code
    # and any in-flight branches that referenced the verbose name keep
    # working). New callers should use ``_start_read_conn_prune_thread``.
    _start_periodic_read_conn_prune = _start_read_conn_prune_thread

    def _stop_read_conn_prune_thread(self) -> None:
        """Stop the periodic read-conn prune daemon (called by close()).

        Delegates to
        :func:`voice_typer.server.history_db_internals.reader._stop_read_conn_prune_thread`.
        """
        from voice_typer.server.history_db_internals import reader

        reader._stop_read_conn_prune_thread(self)

    # Back-compat alias for the previous name.
    _stop_periodic_read_conn_prune = _stop_read_conn_prune_thread

    def _periodic_read_conn_prune_loop(self) -> None:
        """Periodic prune loop body (runs on the prune daemon thread).

        Delegates to
        :func:`voice_typer.server.history_db_internals.reader._periodic_read_conn_prune_loop`.
        """
        from voice_typer.server.history_db_internals import reader

        reader._periodic_read_conn_prune_loop(self)

    # ──────────────────────────────────────────────────────────────
    # Writer thread
    # ──────────────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        """Drain the write queue serially on a single connection.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._writer_loop`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._writer_loop(self)

    def _execute_write_item(
        self,
        conn: sqlite3.Connection,
        callable_: Callable[[sqlite3.Connection], Any],
        future: concurrent.futures.Future | None,
    ) -> None:
        """Execute a single queued write closure and resolve its future.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._execute_write_item`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._execute_write_item(self, conn, callable_, future)

    def _drain_batchable_inserts(
        self,
        conn: sqlite3.Connection,
        first_item: _BatchableInsert,
    ) -> None:
        """Drain pending ``_BatchableInsert`` items into one INSERT.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._drain_batchable_inserts`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._drain_batchable_inserts(self, conn, first_item)

    def _drain_remaining(self, conn: sqlite3.Connection) -> None:
        """Drain any remaining queued items before shutdown.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._drain_remaining`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._drain_remaining(self, conn)

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
                # pages) to avoid flooding the log at the checkpoint
                # cadence (_WAL_CHECKPOINT_INTERVAL). Tiny checkpoints
                # (e.g. 20 pages) are silent — the WAL is healthy, no
                # action needed.
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
            # attempt (after _WAL_CHECKPOINT_INTERVAL) will retry.
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
        , SEC-007 POSIX file/dir permissions).

        also set ``PRAGMA foreign_keys=ON`` on the returned
        connection. The current schema has no FK constraints so this is a
        no-op today, but it is a latent footgun if FKs are added later —
        SQLite defaults to ``foreign_keys=OFF`` for backward compat with
        pre-2004 schemas, silently allowing orphaned child rows. Setting
        it here (per-connection PRAGMA, NOT database-persistent) means
        every writer connection opts in regardless of what future schema
        migrations add. Readers don't need this (FK enforcement is
        write-path only); the existing reader connection helpers in
        ``schema.py`` are left unchanged.
        """
        from voice_typer.server.history_db_internals.schema import open_write_conn

        conn = open_write_conn(self.db_path)
        # opt into FK enforcement. Per-connection PRAGMA —
        # must be set on every new connection (NOT database-persistent).
        # Wrapped in try/except so a read-only FS / locked DB doesn't
        # abort connection setup (the FK setting is a hardening extra,
        # not a correctness requirement for the current schema).
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error as e:
            log.debug(
                "[HISTORY_DB] PRAGMA foreign_keys=ON failed (best-effort): %s",
                e,
            )
        return conn

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
        integrity-check rationale (, , , FIX).

        After schema init succeeds (``self._init_error is None``), runs
        :meth:`_fts5_startup_rebuild` once on the writer connection.
        This bounds the worst-case exposure window for any failed
        delete / clear_all / apply_retention rebuilds in the previous
        session to "between launches" — on every launch the FTS5
        segment data is rebuilt from the current content table, so
        lingering dictated text from a previously-failed delete is
        cleared. Skipped on migration failure (``_init_error`` set)
        because the schema is in an inconsistent state and the FTS5
        table may not exist.
        """
        from voice_typer.server.history_db_internals.schema import init_schema

        new_conn = init_schema(self, conn, _is_recovery=_is_recovery)
        # Startup FTS5 sweep — best-effort, must not raise (a failure
        # here is logged at WARNING and swallowed so the app still
        # starts). Only run when schema init succeeded: on migration
        # failure the FTS5 table may not exist and the schema is in an
        # inconsistent state.
        if self._init_error is None:
            with contextlib.suppress(Exception):
                self._fts5_startup_rebuild(new_conn)
        return new_conn

    def _fts5_startup_rebuild(self, conn: sqlite3.Connection) -> None:
        """Best-effort FTS5 ``'rebuild'`` gated by a persisted failure flag.

        The ``delete``, ``clear_all``, and ``apply_retention`` paths
        each issue the FTS5 ``'rebuild'`` (or ``'optimize'`` for
        per-row deletes) command after their bulk DELETEs to zero
        dictated text out of ``transcriptions_fts_data`` (GDPR Art.
        17 right-to-erasure). But that rebuild is wrapped in a
        tolerant ``try/except sqlite3.Error`` — if it fails
        (transient FTS5 error, disk full), the failure is logged
        and swallowed (no raise, no rollback), incrementing
        ``self._fts5_rebuild_failures`` and publishing an
        ``event_bus`` event. The segment data from the failed delete
        lingers in ``transcriptions_fts_data``, recoverable via
        forensic tools, until FTS5's background compaction happens
        to merge that segment (days or weeks later).

        This startup sweep bounds the worst-case exposure window to
        "between launches": on HistoryDB construction (after the
        schema is initialized), we run ``'rebuild'`` ONCE when
        needed. If the previous session's delete-time rebuild
        failed, this sweep clears the lingering segment data on the
        next launch.

        Gating: the previous implementation ran the O(N) ``'rebuild'``
        on EVERY launch — 100-500ms of cold-start latency even when
        the previous session had no FTS5 failure. Now a
        ``fts5_rebuild_failed`` flag is persisted in
        ``schema_meta``:

        - flag = ``'1'``: a previous delete/clear_all/retention
          rebuild FAILED → run startup rebuild to clear the
          lingering FTS5 segment data.
        - flag = ``'0'``: previous startup rebuild succeeded and no
          failure has been recorded since → SKIP the O(N) rebuild.
        - flag absent (NULL): never set before (fresh DB or first
          launch after this fix landed) → run rebuild once to
          establish a clean baseline, then set flag to ``'0'``.

        On successful rebuild the flag is set to ``'0'`` so
        subsequent launches skip. On a failed delete/clear_all
        rebuild (in this module) or a failed retention rebuild
        (in ``retention.py``), the flag is set to ``'1'`` so the
        next launch retries.

        Best-effort: a failure here is logged at WARNING (not
        ERROR — a startup sweep failure is not actionable
        mid-session; the next session will retry) and swallowed —
        the app must still start. Tolerant of older DBs that
        haven't yet run the V3 migration (no
        ``transcriptions_fts`` table) — the ``sqlite3.Error``
        raised by "no such table" is caught and logged at WARNING.
        """
        # Read the persisted fts5_rebuild_failed flag from
        # schema_meta. The schema_meta table is created by
        # init_schema (CREATE TABLE IF NOT EXISTS) BEFORE this
        # method is called, so the SELECT is always safe.
        try:
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute("SELECT value FROM schema_meta WHERE key = 'fts5_rebuild_failed'")
                row = cursor.fetchone()
                flag_value = row[0] if row is not None else None
        except sqlite3.Error as e:
            # If schema_meta itself is unreadable, fall through to
            # running the rebuild — best-effort, mirrors the
            # pre-flag behavior.
            log.debug(
                "[HISTORY] Could not read fts5_rebuild_failed flag from schema_meta: %s — running rebuild",
                e,
            )
            flag_value = None

        # Steady-state skip: flag is explicitly '0' (previous
        # rebuild succeeded, no failure recorded since). Avoids the
        # O(N) rebuild on every launch. We can't also skip when the
        # flag is NULL: a fresh DB has no flag row yet, and the
        # existing tests (and the GDPR guarantee) require the
        # rebuild to run at least once on a fresh DB to establish a
        # clean baseline post-V3-migration.
        if flag_value == "0":
            log.debug(
                "[HISTORY] FTS5 startup rebuild succeeded (skipped — previous rebuild "
                "succeeded, no failure recorded since)"
            )
            return

        try:
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")
            conn.commit()
            # Persist the success state so subsequent launches skip
            # the O(N) rebuild. INSERT OR REPLACE upserts the flag
            # row (created here on first launch, updated on every
            # successful retry after a prior failure).
            with contextlib.suppress(sqlite3.Error):
                with contextlib.closing(conn.cursor()) as flag_cursor:
                    flag_cursor.execute(
                        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '0')"
                    )
                conn.commit()
            log.debug("[HISTORY] FTS5 startup rebuild succeeded")
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY] FTS5 startup rebuild failed: %s — segments from failed deletes may persist",
                e,
            )
            # Persist the failure state so the next launch retries.
            # Best-effort: a failure here (e.g. disk full) means we
            # can't record the flag, but the in-memory
            # ``_fts5_rebuild_failures`` counter is NOT incremented
            # for the startup path (only the delete/clear_all/
            # retention paths increment it) — the startup sweep is
            # best-effort and a failure to persist the flag just
            # means the next launch re-runs the rebuild (the safe
            # default).
            with contextlib.suppress(sqlite3.Error):
                with contextlib.closing(conn.cursor()) as flag_cursor:
                    flag_cursor.execute(
                        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '1')"
                    )
                conn.commit()

    def _mark_fts5_rebuild_failed(self, conn: sqlite3.Connection) -> None:
        """Persist the ``fts5_rebuild_failed`` flag so the next launch
        retries the FTS5 startup rebuild.

        Called from the tolerant ``except sqlite3.Error`` branches in
        ``delete`` (after a failed per-row ``'optimize'``) and
        ``clear_all`` (after a failed ``'rebuild'``). The retention
        path (``retention.py``) sets the same flag via the same
        schema_meta key — paired change in that module.

        Best-effort: a failure to persist the flag (e.g. disk full)
        is swallowed at DEBUG — the in-memory
        ``self._fts5_rebuild_failures`` counter is still incremented
        by the caller, so the failure is observable via diagnostics
        even if the persisted flag isn't updated.
        """
        try:
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '1')")
            conn.commit()
        except sqlite3.Error as e:
            log.debug(
                "[HISTORY_DB] Could not persist fts5_rebuild_failed flag to schema_meta: %s "
                "(in-memory counter still incremented; next launch may skip the startup rebuild)",
                e,
            )

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

        the copy uses ``_secure_copy_db_file``
        (``O_NOFOLLOW`` on both source and destination, ``0o600`` on
        the destination, ``fsync`` after write). This replaces the
        previous ``shutil.copy2`` call which followed symlinks on
        BOTH source and destination (a symlink-planting attacker
        could redirect the backup to an arbitrary file or read an
        arbitrary file's content into the backup location) and had
        no ``fsync``. The destination is created with mode ``0o600``
        on POSIX so the backup is not world-readable (the main DB
        file is also ``0o600``).
        """
        try:
            bak_main = self.db_path.with_name(f"{self.db_path.name}.pre-migration-v{current_version}.bak")
            # copy the main DB file via the secure helper
            # (O_NOFOLLOW on src+dst, 0o600 on dst, fsync).
            if self.db_path.exists():
                _secure_copy_db_file(self.db_path, bak_main)
            # Copy the -wal and -shm sidecars if they exist (WAL mode).
            # These hold uncheckpointed pages that would otherwise be
            # lost — including them makes the backup a complete
            # restorable snapshot. : routed through the same
            # symlink-safe helper.
            for sidecar in ("-wal", "-shm"):
                src = self.db_path.with_name(self.db_path.name + sidecar)
                if src.exists():
                    dst = bak_main.with_name(bak_main.name + sidecar)
                    _secure_copy_db_file(src, dst)
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
        """run ``PRAGMA quick_check``; if the result is
        anything other than ``("ok",)``, rename the corrupt DB file
        (and its WAL/SHM sidecars) to ``history.db.corrupt-<timestamp>``
        and return a fresh connection on a new (empty) DB file.

        Returns ``None`` if the DB is healthy. Returns a new
        connection if corruption was detected and recovery succeeded.
        Sets ``self._init_error`` and returns ``None`` if recovery
        failed (e.g. the rename or reopen raised).

        The caller is responsible for re-running schema init on the
        returned connection (the fresh DB has no tables yet).

        after renaming the corrupt DB, attempts to recover
        user-data rows via ``iterdump()`` and replays them on the
        fresh DB. Also publishes a ``history_corrupted`` event via
        ``event_bus`` so the renderer can surface a toast to the user.
        If ``iterdump()`` fails (severe corruption), the rename +
        fresh-DB path still runs as a fallback (``recovered_count=0``).
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
        # invalidate all existing read connections BEFORE renaming.
        # On POSIX, renaming the corrupt DB file doesn't affect
        # already-open file descriptors — readers would keep reading
        # stale/garbage data from the renamed file, so we close every
        # tracked read conn and bump the generation counter so each
        # reader thread's next ``_get_read_conn`` call detects the
        # mismatch, closes its stale thread-local conn, and reconnects
        # to the fresh DB file. On Windows, closing first is MANDATORY
        # for a different reason: an open SQLite handle locks the file,
        # so ``os.rename`` of the corrupt DB (below) silently fails
        # with WinError 32 — the corrupt-renamed file never appears and
        # ``_try_iterdump_recovery`` finds nothing (recovered_count=0).
        # Closing readers before the rename makes recovery work on both
        # platforms. We can't clear other threads' ``_read_local.conn``
        # directly, but the generation check handles it lazily.
        with self._connections_lock:
            for _ident, rconn in self._all_read_connections:
                with contextlib.suppress(sqlite3.Error):
                    rconn.close()
            self._all_read_connections.clear()
            self._read_conn_generation += 1
        # Also clear the current thread's stale read conn (if any)
        # so any subsequent read on this thread reopens immediately.
        if hasattr(self._read_local, "conn") and self._read_local.conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._read_local.conn.close()
            self._read_local.conn = None
            self._read_local.gen = self._read_conn_generation
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
        # BEFORE opening the fresh DB, attempt to recover
        # user-data INSERTs from the now-renamed corrupt file. The
        # corrupt file is at ``corrupt_main``; we open it read-only
        # so we can't compound the corruption by writing to it.
        recovered_inserts = self._try_iterdump_recovery(corrupt_main)
        # Open a fresh connection on a new (empty) DB file.
        try:
            new_conn = self._open_write_conn()
            self._check_wal_mode(new_conn)
        except sqlite3.Error as e:
            self._init_error = e
            # Even if the fresh DB can't be opened, still emit the
            # corruption event so the user is notified.
            self._notify_corruption_recovered(corrupt_main, 0)
            return None
        # replay the recovered INSERTs on the fresh DB.
        # If no INSERTs were recovered (severe corruption or empty
        # DB), this is a no-op and the fresh DB stays empty.
        recovered_count = 0
        if recovered_inserts:
            recovered_count = self._apply_recovered_inserts(new_conn, recovered_inserts)
        # emit the history_corrupted event + tray notify.
        self._notify_corruption_recovered(corrupt_main, recovered_count)
        return new_conn

    def _try_iterdump_recovery(self, old_db_path: Path) -> list[str]:
        """attempt to recover INSERT statements from a
        corrupt DB via ``connection.iterdump()``.

        Opens the corrupt DB in read-only mode (``?mode=ro`` URI) so
        we can't compound the corruption by writing to the known-bad
        file. Iterates the dump and returns the list of
        ``INSERT INTO transcriptions ...`` statements.

        Schema statements (CREATE TABLE / CREATE INDEX), schema-meta
        rows, FTS5 shadow-table rows, and ``sqlite_sequence`` rows
        are filtered out — the fresh DB's ``init_schema`` recreates
        the schema, and replaying ``schema_meta`` would PRIMARY
        KEY-conflict with the version row ``init_schema`` writes.

        Returns an empty list if the corrupt DB file doesn't exist,
        can't be opened read-only, or ``iterdump()`` raises (severe
        corruption). The caller must handle the empty-list case by
        falling back to the rename + fresh-DB path (which is what
        ``_maybe_recover_from_corruption`` does).
        """
        inserts: list[str] = []
        if not old_db_path.exists():
            log.warning(
                "[HISTORY_DB] iterdump recovery: corrupt DB file does not exist: %s",
                old_db_path,
            )
            return inserts
        # Build the read-only URI. ``Path.as_uri()`` URL-encodes
        # special chars (spaces, etc.) and produces a proper
        # ``file:///`` URI on both POSIX and Windows.
        try:
            uri = old_db_path.resolve().as_uri() + "?mode=ro"
        except (OSError, ValueError) as e:
            log.warning(
                "[HISTORY_DB] iterdump recovery: could not resolve path %s: %s",
                old_db_path,
                e,
            )
            return inserts
        try:
            ro_conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] iterdump recovery: could not open corrupt DB read-only (%s): %s",
                old_db_path,
                e,
            )
            return inserts
        try:
            for stmt in ro_conn.iterdump():
                # iterdump yields strings like:
                #   INSERT INTO "transcriptions" VALUES(1, 'text', ...);
                #   INSERT INTO "schema_meta" VALUES('version','3');
                #   INSERT INTO "transcriptions_fts" VALUES(...);
                # Keep only INSERT INTO transcriptions (user data).
                stripped = stmt.lstrip()
                if _INSERT_TRANSCRIPTIONS_RE.match(stripped):
                    inserts.append(stmt)
        except sqlite3.Error as e:
            # Severe corruption: iterdump raised mid-iteration.
            # Return whatever we have so far (may be partial) rather
            # than discarding everything — partial recovery is
            # strictly better than no recovery.
            log.warning(
                "[HISTORY_DB] iterdump recovery: iterdump() raised mid-iteration "
                "(severe corruption, returning %d partial statements): %s",
                len(inserts),
                e,
            )
            return inserts
        finally:
            with contextlib.suppress(sqlite3.Error):
                ro_conn.close()
        log.info(
            "[HISTORY_DB] iterdump recovery: recovered %d INSERT statements from %s",
            len(inserts),
            old_db_path,
        )
        return inserts

    def _apply_recovered_inserts(
        self,
        conn: sqlite3.Connection,
        inserts: list[str],
    ) -> int:
        """replay iterdump-recovered INSERT statements on
        the fresh DB.

        The fresh DB's schema is not yet set up at this point
        (``init_schema``'s recursive ``_is_recovery=True`` call runs
        AFTER this method returns), so we run ``init_schema``
        ourselves first. The later recursive call is a no-op because
        all CREATE statements use ``IF NOT EXISTS`` and
        ``schema_meta`` already has ``version=_CURRENT_SCHEMA_VERSION``.

        The INSERTs are applied via ``executescript`` so a single bad
        statement (e.g. a row that violates a constraint) doesn't
        roll back all the others — partial recovery is preferable to
        no recovery for user dictation history.

        Returns the actual number of rows in the ``transcriptions``
        table after the attempt (may be less than ``len(inserts)``
        if some statements failed).
        """
        # Set up the schema on the fresh connection so the INSERTs
        # can target the transcriptions table. Wrapped broadly
        # because init_schema may interact with self._init_error /
        # _backup_before_migration in ways we don't want to crash on
        # during best-effort recovery.
        try:
            from voice_typer.server.history_db_internals.schema import (
                init_schema as _init_schema,
            )

            _init_schema(self, conn, _is_recovery=True)
        except Exception as e:  # noqa: BLE001 — best-effort recovery
            log.warning(
                "[HISTORY_DB] iterdump recovery: could not initialize schema for replay (skipping %d INSERTs): %s",
                len(inserts),
                e,
            )
            return 0
        # Apply the INSERTs. ``executescript`` issues a COMMIT first
        # (clearing any pending transaction from init_schema), then
        # runs each statement. The FTS5 AFTER-INSERT trigger fires
        # for each row and populates ``transcriptions_fts``
        # automatically, so we don't need to replay FTS rows.
        try:
            script = "\n".join(inserts)
            conn.executescript(script)
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] iterdump recovery: executescript failed (partial recovery may have occurred): %s",
                e,
            )
        # Count actual rows in the fresh DB. This is more accurate
        # than ``len(inserts)`` because some INSERTs may have failed
        # (e.g. constraint violations on duplicate ids).
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM transcriptions")
            row = cursor.fetchone()
            count = int(row[0]) if row else 0
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] iterdump recovery: could not count recovered rows: %s",
                e,
            )
            return 0
        if count > 0:
            log.info(
                "[HISTORY_DB] iterdump recovery: %d row(s) recovered into fresh DB",
                count,
            )
        else:
            log.info(
                "[HISTORY_DB] iterdump recovery: no rows recovered (all INSERTs failed or empty source)",
            )
        return count

    def _notify_corruption_recovered(
        self,
        corrupt_main: Path,
        recovered_count: int,
    ) -> None:
        """surface the corruption event to the user.

        Logs a WARNING-level message naming the backup file's
        location and the number of rows recovered, then publishes a
        ``history_corrupted`` event via ``event_bus`` so the renderer
        can show a toast/notification. If ``self._app.tray.notify``
        is wired (set by the app shell), also calls it for a native
        OS notification.

        All notifications are best-effort: if ``event_bus.publish``
        or ``tray.notify`` raises, the recovery path must still
        succeed (the fresh DB has already been created and populated).
        """
        log.warning(
            "[HISTORY_DB] History database was corrupted and has been "
            "backed up to %s. Recovered %d row(s) via iterdump.",
            corrupt_main,
            recovered_count,
        )
        # Best-effort event_bus publication. Wrapped broadly because
        # the event_bus import or the publish call may fail (e.g.
        # circular import during early init, or a malformed event
        # payload); none of those should crash the recovery path.
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "history_corrupted",
                    "data": {
                        "path": str(corrupt_main),
                        "db_path": str(self.db_path),
                        "recovered_count": recovered_count,
                    },
                }
            )
        except Exception as e:  # noqa: BLE001 — best-effort notification
            log.warning(
                "[HISTORY_DB] event_bus.publish(history_corrupted) failed (best-effort, recovery continues): %s",
                e,
            )
        # Best-effort tray notification. ``self._app`` is set by the
        # app shell (not by HistoryDB.__init__) — use getattr so the
        # attribute-missing case during early init is handled.
        app = getattr(self, "_app", None)
        if app is None:
            return
        tray = getattr(app, "tray", None)
        if tray is None:
            return
        notify = getattr(tray, "notify", None)
        if notify is None:
            return
        try:
            notify(
                APP_NAME,
                f"History database was corrupted and backed up. {recovered_count} row(s) recovered.",
            )
        except Exception as e:  # noqa: BLE001 — best-effort notification
            log.warning(
                "[HISTORY_DB] tray.notify failed (best-effort, recovery continues): %s",
                e,
            )

    # ──────────────────────────────────────────────────────────────
    # Read connections
    # ──────────────────────────────────────────────────────────────

    def _get_read_conn(self) -> sqlite3.Connection:
        """Get a thread-local READ-ONLY connection.

        Delegates to
        :func:`voice_typer.server.history_db_internals.reader._get_read_conn`.
        """
        from voice_typer.server.history_db_internals import reader

        return reader._get_read_conn(self)

    def _prune_dead_read_connections_locked(self) -> None:
        """Close dead-thread read connections.

        Delegates to
        :func:`voice_typer.server.history_db_internals.reader._prune_dead_read_connections_locked`.
        """
        from voice_typer.server.history_db_internals import reader

        reader._prune_dead_read_connections_locked(self)

    def _get_conn(self) -> sqlite3.Connection:
        """Backwards-compat alias for ``_get_read_conn``.

        Delegates to
        :func:`voice_typer.server.history_db_internals.reader._get_conn`.
        """
        from voice_typer.server.history_db_internals import reader

        return reader._get_conn(self)

    # ──────────────────────────────────────────────────────────────
    # Write submission
    # ──────────────────────────────────────────────────────────────

    def _drop_oldest_for_overflow(self, current_future: concurrent.futures.Future | None) -> None:
        """Drop oldest non-sentinel queued item to make room.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._drop_oldest_for_overflow`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._drop_oldest_for_overflow(self, current_future)

    def _submit_write(
        self,
        fn: Callable[[sqlite3.Connection], Any],
        *,
        wait: bool = True,
    ) -> Any | None:
        """Submit a write closure to the writer thread.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._submit_write`.
        """
        from voice_typer.server.history_db_internals import writer

        return writer._submit_write(self, fn, wait=wait)

    def flush(self) -> None:
        """Block until all queued writes have been processed.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer.flush`.
        """
        from voice_typer.server.history_db_internals import writer

        writer.flush(self)

    def _close_writer(self) -> None:
        """Writer-teardown portion of :meth:`close`.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._close_writer`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._close_writer(self)

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────

    def __del__(self):
        """Close read connections on GC to prevent ResourceWarning.

        Lifecycle note: does NOT call ``close()`` (which joins the writer thread
        with a 10s timeout). If a HistoryDB instance was GC'd while the
        writer was stuck mid-VACUUM or blocked by an antivirus-locked
        WAL, the GC pause blocked for up to 10s. The writer is a daemon
        thread and will exit at process termination regardless; here we
        only signal ``_shutdown`` so its inner loop exits on the next
        iteration, and close the read connections (the ResourceWarning
        we actually care about). ``close()`` (called explicitly by the
        app shutdown path) still does the full writer drain + join.
        """
        with contextlib.suppress(Exception):
            # Signal the writer to exit on its next iteration. The
            # writer is a daemon, so even if it never sees this signal
            # it will be killed at process exit.
            self._shutdown.set()
            # Close the calling thread's read connection (thread-local).
            if hasattr(self._read_local, "conn") and self._read_local.conn is not None:
                with contextlib.suppress(Exception):
                    self._read_local.conn.close()
                self._read_local.conn = None
            # Close all other threads' read connections. Take the lock
            # so we don't race with ``_get_read_conn`` on another thread
            # — but never block on it (a re-entrant GC during
            # ``_get_read_conn`` could otherwise deadlock).
            if not self._connections_lock.acquire(blocking=False):
                return
            try:
                for _ident, conn in self._all_read_connections:
                    with contextlib.suppress(Exception):
                        conn.close()
                self._all_read_connections.clear()
            finally:
                self._connections_lock.release()

    def close(self):
        """Shut down the writer thread and close all connections.

        IMPL-A: sends the shutdown sentinel, waits (with timeout) for
        the writer to drain remaining items and exit, then closes all
        read connections. Idempotent — safe to call multiple times.

        also signals + joins the periodic retention thread
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
        # stop the periodic retention thread BEFORE setting
        # _shutdown so its inner loop sees a clean stop_event signal
        # and exits without trying to call apply_retention (which
        # would no-op on a shutdown DB but would still log noise).
        self._stop_periodic_retention()
        # Stop the periodic read-conn prune daemon before tearing down
        # connections — otherwise the worker could walk _all_read_connections
        # mid-tear-down and trip over a half-closed connection. Also
        # clears the thread / event attributes so callers observing
        # ``_read_conn_prune_thread is None`` after ``close()`` see the
        # quiesced state.
        self._stop_read_conn_prune_thread()
        if self._shutdown.is_set():
            # Already closed — just make sure read conns are gone.
            with self._connections_lock:
                for _ident, conn in self._all_read_connections:
                    with contextlib.suppress(sqlite3.Error):
                        conn.close()
                self._all_read_connections.clear()
            return
        self._shutdown.set()
        # Writer-teardown (best-effort wal_checkpoint(TRUNCATE), shutdown
        # sentinel enqueue with drop-oldest loop, writer-thread join) is
        # delegated to ``history_db_internals.writer._close_writer`` so
        # this method stays focused on lifecycle orchestration. The
        # delegated helper reads ``_SHUTDOWN_SENTINEL`` /
        # ``_WRITE_QUEUE_MAXSIZE`` / ``_WRITER_JOIN_TIMEOUT`` /
        # ``HistoryDBError`` from this module's namespace (lazy import
        # inside the helper so monkeypatches on this module keep working).
        self._close_writer()
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
        # early-return guard — if the writer thread never
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
            # invalidate the today-stats cache at enqueue time.
            # Unlike ``_invalidate_history_count_cache`` (which skips
            # fire-and-forget ``add_transcription`` because a stale-by-1
            # TOTAL is fine), today's stats must reflect each new
            # dictation as soon as the Dashboard refreshes. The cache is
            # invalidated BEFORE the writer thread commits the INSERT —
            # there is a brief race window where a concurrent reader
            # could re-populate the cache with the pre-INSERT count, but
            # the 15s TTL bounds the staleness and the next
            # ``transcription_final`` refresh re-checks the cache.
            self._invalidate_today_stats_cache()
            # Placeholder row_id — callers that check ``> 0`` see success.
            return 1
        except Exception as e:
            log.error("[HISTORY] Failed to enqueue add_transcription: %s", e)
            return -1

    @_wrap_write(False, "delete transcription", "delete")
    def delete(self, transcription_id: int, *, raise_on_error: bool = False) -> bool:
        """Delete a transcription by ID.

        when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``False``. Without this,
        the IPC layer cannot tell "row didn't exist" from "DB error".

        After the row DELETE + commit, issue the FTS5
        ``'optimize'`` command so the segment data in
        ``transcriptions_fts_data`` is purged of the deleted row's
        dictated text. The FTS5 AFTER DELETE trigger (schema.py:90-92)
        only marks the rowid as deleted in the delete-bitmap — the
        segment data (containing the dictated text) remains physically
        present and is recoverable via forensic tools until FTS5's
        background compaction happens to merge that segment (days or
        weeks later). For a user who dictates a password / medical note
        / financial data and then deletes that single transcription via
        the History UI, the text is NOT gone without this optimize — a
        direct GDPR Art. 17 violation.

        The per-delete command was downgraded from ``'rebuild'`` (O(N)
        — drops and rebuilds ALL segments from the content table) to
        ``'optimize'`` (runs the FTS5 optimizer until the index is
        optimal — typically 3-4x faster than ``'rebuild'`` on a
        multi-thousand-row DB because it only does the merge work
        needed to consolidate segments and apply the delete-bitmap).
        The user-visible MATCH-query correctness is already preserved
        by the AFTER DELETE trigger (the deleted rowid is immediately
        hidden from search results); the ``'optimize'`` call provides
        the forensic-recovery guarantee (deleted dictated text is
        purged from ``transcriptions_fts_data``) without paying the
        full O(N) cost on every single-row delete. The periodic
        retention tick (``retention.py``) still runs a full ``'rebuild'``
        after bulk sweeps with >20% deletion ratio, providing the
        ultimate safety net.

        The optimize is wrapped in a tolerant ``try/except sqlite3.Error``
        (matching the retention.py / clear_all pattern) so a transient
        FTS5 error does not break the row delete (which already
        committed). The optimize is best-effort privacy hardening — if
        it fails, the row is still gone from the content table (so the
        user's intent is honored), only the FTS5 segment data lingers
        (the same state as before this fix — and bounded to "between
        launches" by the AP-17 startup rebuild sweep).
        """

        def _do_delete(conn: sqlite3.Connection) -> bool:
            with contextlib.closing(conn.cursor()) as cursor:
                cursor.execute("DELETE FROM transcriptions WHERE id = ?", (transcription_id,))
                conn.commit()
                deleted = cursor.rowcount > 0
                if not deleted:
                    return False
                # Purge the deleted row's dictated
                # text from ``transcriptions_fts_data`` (the FTS5
                # shadow segment table). The AFTER DELETE trigger
                # at schema.py:90-92 only marks the rowid as
                # deleted in the delete-bitmap — the segment data
                # survives until background compaction (days/weeks
                # later). ``'optimize'`` runs the FTS5 optimizer
                # until the index is optimal, which both
                # consolidates segments AND applies the
                # delete-bitmap to the merged output so the deleted
                # text is purged. ``'optimize'`` is preferred over
                # ``'rebuild'`` here because:
                #   - ``'rebuild'`` is O(N) (drops and rebuilds ALL
                #     segments from the content table).
                #   - ``'optimize'`` is typically 3-4x faster
                #     (only does the merge work needed to
                #     consolidate, not a full rebuild).
                # The MATCH-query correctness is already preserved
                # by the trigger; this call is purely for the
                # forensic-recovery guarantee. The periodic
                # retention tick (retention.py) still runs a full
                # ``'rebuild'`` after bulk sweeps as a safety net.
                # Tolerant: a transient FTS5 error must not break
                # the row delete (which already committed).
                try:
                    cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('optimize')")
                    conn.commit()
                except sqlite3.Error as optimize_exc:
                    log.warning(
                        "[HISTORY_DB] FTS5 'optimize' after delete(id=%d) "
                        "FAILED: %s — dictated text may linger in "
                        "transcriptions_fts_data until the next "
                        "periodic retention sweep or the AP-17 "
                        "startup rebuild.",
                        transcription_id,
                        optimize_exc,
                    )
                    # Best-effort: increment the per-instance
                    # failure counter so observability surfaces
                    # chronic FTS5 optimize failures (mirrors the
                    # retention.py / clear_all pattern).
                    with contextlib.suppress(Exception):
                        self._fts5_rebuild_failures = getattr(self, "_fts5_rebuild_failures", 0) + 1
                    # Persist the fts5_rebuild_failed flag so the
                    # next launch's startup rebuild runs (clearing
                    # the lingering segment data). Best-effort: a
                    # failure to persist is swallowed inside
                    # _mark_fts5_rebuild_failed.
                    with contextlib.suppress(Exception):
                        self._mark_fts5_rebuild_failed(conn)
                return True

        result = self._submit_write(_do_delete, wait=True)
        if result is None:
            # Writer shut down — treat as failure.
            return False
        if result:
            # invalidate the count cache.
            self._invalidate_history_count_cache()
            # invalidate the today-stats cache (a delete
            # changes today's count/chars/words/duration if the
            # deleted row was from today).
            self._invalidate_today_stats_cache()
        return bool(result)

    @_wrap_write(-1, "restore transcription", "restore")
    def restore(
        self,
        record: dict,
        *,
        raise_on_error: bool = False,
    ) -> int:
        """Re-insert a previously-deleted transcription record.

        supports the Undo-delete toast in the renderer.
        ``record`` should be the dict shape returned by ``get_recent``
        (id is ignored — a new row with a new id is inserted).

        Returns the new row id, or -1 on failure.
        """
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
            # invalidate the count cache.
            self._invalidate_history_count_cache()
            # invalidate the today-stats cache (a restore
            # adds a new row whose timestamp is ``now``, which
            # affects today's count/chars/words/duration).
            self._invalidate_today_stats_cache()
        return int(result)

    @_wrap_write(False, "clear transcriptions", "clear_all")
    def clear_all(self, *, raise_on_error: bool = False) -> bool:
        """Clear all transcriptions.

        IMPL-A: chunked DELETE (100 rows per batch, commit per batch)
        running inside the writer thread. Chunking prevents the WAL
        from growing unboundedly during a huge clear and lets external
        readers see progress. The previous single-transaction DELETE
        held the write lock for the full scan.

        after the chunked DELETE completes, ``VACUUM`` runs
        in the writer thread to reclaim the freed pages so the DB file
        shrinks. Without this, ``clear_all`` leaves the file at its
        pre-clear size (SQLite keeps free pages for reuse) and the
        user's dictated text remains recoverable from the file via
        forensic tools even after a "clear all" — a privacy concern
        for the GDPR delete path.

         (the remaining half): after VACUUM, the FTS5
        ``'rebuild'`` command is issued so the FTS5 shadow-table
        segment data (``transcriptions_fts_data``) is also rebuilt
        from the (now-empty) content table. ``VACUUM`` rebuilds the
        main DB file but does NOT rebuild FTS5 shadow tables; without
        this step, dictated text remained recoverable from
        ``transcriptions_fts_data`` via sqlite3 CLI or forensic tools
        — defeating  / GDPR Art. 17 right-to-erasure. The
        rebuild is wrapped in a tolerant ``try/except sqlite3.Error``
        matching the pattern in
        :func:`voice_typer.server.history_db_internals.retention.apply_retention`
        so an older DB (pre-V3 migration, no FTS table yet) doesn't
        crash the clear path. : on failure the privacy
        guarantee is broken, so the failure is logged at ERROR,
        ``self._fts5_rebuild_failures`` is incremented, and an
        ``event_bus`` event ``{"type": "history_fts5_rebuild_failed"}``
        is published so the renderer can show a toast.

        see ``delete`` for ``raise_on_error`` semantics.
        """

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
            # VACUUM reclaims the freed pages so the DB
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
            # rebuild FTS5 segments from the (now-empty)
            # content table. The DELETE trigger
            # ``transcriptions_ad_fts`` only marks rowids as
            # deleted in the FTS5 delete-bitmap; the segment data
            # in ``transcriptions_fts_data`` survives both the
            # trigger delete and ``VACUUM``. Without this rebuild,
            # dictated text remained recoverable from
            # ``transcriptions_fts_data`` via forensic tools —
            # defeating  / GDPR Art. 17. Wrapped in a
            # tolerant try/except so an older DB (pre-V3
            # migration, no FTS table yet) doesn't crash the
            # clear path. The pattern matches the one in
            # ``retention.apply_retention`` ( mirrors this
            # in ``delete()``).
            try:
                fts_cursor = conn.cursor()
                try:
                    fts_cursor.execute("INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')")
                    conn.commit()
                    log.info("[HISTORY_DB] FTS5 segments rebuilt after clear_all")
                finally:
                    fts_cursor.close()
            except sqlite3.Error as e:
                # escalate from WARNING to ERROR — the
                # GDPR Art. 17 /  privacy guarantee is
                # broken (deleted dictated text remains
                # recoverable from ``transcriptions_fts_data``
                # via forensic tools), not merely "suboptimal".
                log.error(
                    "[HISTORY_DB] FTS5 'rebuild' after clear_all FAILED: %s "
                    "(FTS5 shadow-table segment data may persist — deleted "
                    "dictated text remains recoverable; manual re-index advised)",
                    e,
                )
                # observable metric — increment the
                # per-instance failure counter so diagnostics
                # handlers can surface it to the user.
                try:
                    self._fts5_rebuild_failures = self._fts5_rebuild_failures + 1
                except Exception:  # noqa: BLE001 — best-effort metric
                    log.debug(
                        "[HISTORY_DB] could not increment _fts5_rebuild_failures counter",
                        exc_info=True,
                    )
                # Persist the fts5_rebuild_failed flag so the
                # next launch's startup rebuild runs (clearing the
                # lingering segment data). Best-effort: a failure
                # to persist is swallowed inside
                # _mark_fts5_rebuild_failed.
                with contextlib.suppress(Exception):
                    self._mark_fts5_rebuild_failed(conn)
                # best-effort event_bus publication so
                # the renderer can show a toast. Wrapped broadly
                # because the event_bus import or the publish
                # call may fail (e.g. circular import during
                # early init); none of those should crash the
                # clear path which has already done the chunked
                # DELETEs + VACUUM.
                try:
                    from voice_typer.server import event_bus

                    event_bus.publish(
                        {
                            "type": "history_fts5_rebuild_failed",
                            "data": {
                                "db_path": str(self.db_path),
                                "deleted": 0,  # clear_all doesn't track count
                                "error": str(e),
                                "source": "clear_all",
                            },
                        }
                    )
                except Exception as publish_exc:  # noqa: BLE001
                    log.warning(
                        "[HISTORY_DB] event_bus.publish(history_fts5_rebuild_failed) "
                        "failed (best-effort, clear_all continues): %s",
                        publish_exc,
                    )
            log.info("[HISTORY] Cleared all transcriptions")
            return True

        result = self._submit_write(_do_clear_all, wait=True)
        if result is None:
            return False
        if result:
            # invalidate the count cache.
            self._invalidate_history_count_cache()
            # invalidate the today-stats cache (clear_all
            # deletes today's rows too — today's stats must drop to
            # 0/0/0/0 on the next read).
            self._invalidate_today_stats_cache()
        return bool(result)

    @_wrap_write(False, "toggle favorite", "toggle_favorite")
    def toggle_favorite(
        self,
        transcription_id: int,
        *,
        raise_on_error: bool = False,
    ) -> bool:
        """Toggle the favorite status of a transcription.

        see ``delete`` for ``raise_on_error`` semantics.
        """

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

    def apply_retention(
        self,
        retention_days: int = 0,
        max_entries: int = 0,
        retention_count: int = 0,
    ) -> "RetentionResult":
        """Apply retention policy: delete old entries.

        Returns a :class:`RetentionResult` — an ``int`` subclass whose
        value is the number of deleted entries and whose
        ``fts5_rebuild_ok`` attribute / ``["fts5_rebuild_ok"]`` item
        reports whether the post-sweep FTS5 ``'rebuild'`` command
        succeeded (). The ``int`` return contract is preserved
        so existing callers (``deleted == 20``, ``if deleted > 0``)
        work unchanged.

        Delegates to
        :func:`voice_typer.server.history_db_internals.retention.apply_retention`.
        See that function for the full rationale ( UTC cutoff
        fix,  fallback wiring, IMPL-A chunked deletes on the
        writer thread,  conditional VACUUM,  FTS5 rebuild,
         count-cache invalidation,  sentinel-on-error
        contract).
        """
        from voice_typer.server.history_db_internals.retention import (
            RetentionResult,
            apply_retention,
        )

        result = apply_retention(
            self,
            retention_days=retention_days,
            max_entries=max_entries,
            retention_count=retention_count,
        )
        # ``RetentionResult`` is an ``int`` subclass; ``int`` is the
        # documented return type for backward compat, but the actual
        # object exposes ``.fts5_rebuild_ok`` / ``["fts5_rebuild_ok"]``
        # so callers that care about the privacy guarantee can detect
        # a failed FTS5 rebuild.
        _ = RetentionResult  # re-export alias for type-checkers
        return result

    # ──────────────────────────────────────────────────────────────
    # Periodic retention scheduling ()
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
        """spawn a daemon thread that periodically calls ``apply_retention``.

        Delegates to
        :func:`voice_typer.server.history_db_internals.retention.schedule_periodic_retention`.
        The free function takes ``self`` (the HistoryDB instance) so it
        can mutate ``_retention_stop_event`` / ``_retention_thread`` and
        call back into ``apply_retention`` / ``_stop_periodic_retention``.
        See the delegated function for the full rationale (
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
        """signal the periodic retention thread to stop and join it.

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

    @_wrap_read([], "get recent transcriptions")
    def get_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        raise_on_error: bool = False,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Get recent transcriptions with offset-based pagination.

        when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``[]``. This lets the
        IPC layer distinguish "empty result" from "operation failed"
        and surface a proper error to the renderer.

        the ``text`` column is projected to a 500-char preview
        via ``SUBSTR(text, 1, 500)`` to keep list responses under the
        1 MiB WS frame cap. Two new fields are added per row:
        ``text_truncated`` (bool) and ``text_full_length`` (int).

        keyset pagination: when ``before_timestamp`` AND ``before_id``
        are both supplied, the WHERE clause restricts to rows strictly
        older than ``(before_timestamp, before_id)`` in (timestamp DESC,
        id DESC) order — i.e. ``timestamp < ? OR (timestamp = ? AND
        id < ?)``. This is O(log N) per page via ``idx_timestamp_id``,
        whereas OFFSET is O(offset) (SQLite still scans & discards
        ``offset`` rows). Callers paginating past the first page
        should pass the (timestamp, id) of the last row of the
        previous page. When either cursor value is ``None`` the
        OFFSET fallback is used (backward-compatible with the
        pre-cursor contract), but ``offset`` must be < 1000 — deeper
        pagination requires cursor mode.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_recent`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_recent(
            self,
            limit,
            offset,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

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

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_latest_text`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_latest_text(self)

    @_wrap_read([], "search transcriptions")
    def search(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        *,
        raise_on_error: bool = False,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Search transcriptions by text with offset-based pagination.

        see ``get_recent`` for ``raise_on_error`` and cursor-pagination
        (``before_timestamp`` / ``before_id``) semantics.

        FTS5 is used for any query that yields at least one tokenizable
        character (``_is_fts_compatible_query``). For empty queries and
        queries consisting solely of separator characters (e.g. ``%`` or
        ``_``), we fall back to the pre- LIKE path so literal
        wildcards still match — preserving the contract pinned by
        ``test_search_treats_like_wildcards_as_literals`` and
        ``test_empty_query_returns_all_rows``. ``_sanitize_fts_query``
        wraps each whitespace-separated token in double quotes so the
        user's input is treated as a literal phrase rather than FTS5
        MATCH syntax (e.g. ``foo*`` matches the literal token ``foo*``,
        not a prefix query).

        On the no-cursor path the FTS5 ``LIMIT`` is pushed INTO the FTS
        subquery so FTS5 only materialises the rowids that will actually
        be returned, rather than the full match set — see
        :func:`voice_typer.server.history_db_internals.search.search`
        for details.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.search`.
        """
        from voice_typer.server.history_db_internals import search

        return search.search(
            self,
            query,
            limit,
            offset,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

    @_wrap_read([], "get favorites")
    def get_favorites(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        raise_on_error: bool = False,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Get favorited transcriptions with offset-based pagination.

        see ``get_recent`` for ``raise_on_error`` and cursor-pagination
        (``before_timestamp`` / ``before_id``) semantics.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_favorites`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_favorites(
            self,
            limit,
            offset,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

    @_wrap_read(lambda: {"count": 0, "chars": 0, "word_count": 0, "duration": 0}, "get today stats")
    def get_today_stats(self, *, raise_on_error: bool = False) -> dict:
        """Get statistics for today's transcriptions.

        see ``get_recent`` for ``raise_on_error`` semantics.

        a 15s TTL cache (``_TODAY_STATS_CACHE_TTL_S``) wraps the
        aggregating scan so the Dashboard's per-``transcription_final``
        refresh (capped at 1 call/sec/client by the rate_limiter)
        doesn't re-scan on every refresh. The cache is invalidated by
        EVERY mutation that could change today's stats
        (add/delete/clear/restore/retention), so a stale-by-N result is
        never served after a write. The returned dict is a shallow copy
        so callers can mutate it without corrupting the cached value.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_today_stats`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_today_stats(self)

    def _invalidate_today_stats_cache(self) -> None:
        """drop the cached today-stats dict.

        Called by every mutation that could change today's stats
        (``add_transcription``, ``delete``, ``clear_all``, ``restore``,
        ``apply_retention``). Unlike ``_invalidate_history_count_cache``
        (which skips invalidation on fire-and-forget
        ``add_transcription`` because a stale-by-1 total is fine), the
        today-stats cache is invalidated on EVERY mutation — today's
        stats grow by 1 per dictation and the user wants to see them
        update live.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.invalidate_today_stats_cache`.
        """
        from voice_typer.server.history_db_internals import search

        search.invalidate_today_stats_cache(self)

    # ──────────────────────────────────────────────────────────────
    #  on-demand full-text + total-count accessors
    # ──────────────────────────────────────────────────────────────

    def get_transcription_text(
        self,
        transcription_id: int,
        *,
        raise_on_error: bool = False,
    ) -> dict:
        """return the FULL ``text`` of a single transcription row.

        Companion to the 500-char ``text`` preview returned by
        ``get_recent`` / ``search`` / ``get_favorites``.
        Returns ``{"id": int, "text": str}`` (empty string if not found).

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_transcription_text`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_transcription_text(
            self,
            transcription_id,
            raise_on_error=raise_on_error,
        )

    def get_history_count(self, *, raise_on_error: bool = False) -> int:
        """return the total number of transcription rows.

        ``SELECT COUNT(*) FROM transcriptions`` is O(N) in SQLite.
        Caching pattern mirrors ``service/model.py:get_model_status``:
        a 60s TTL with immediate invalidation on
        delete/clear_all/restore/apply_retention via
        ``_invalidate_history_count_cache``. Fire-and-forget
        ``add_transcription`` does NOT invalidate — the count grows
        by 1 per dictation, and a 60s-stale-by-N count is fine for a
        "Total Dictations" stat card.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_history_count`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_history_count(self, raise_on_error=raise_on_error)

    def _invalidate_history_count_cache(self) -> None:
        """drop the cached total-count int.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.invalidate_history_count_cache`.
        """
        from voice_typer.server.history_db_internals import search

        search.invalidate_history_count_cache(self)

    # ──────────────────────────────────────────────────────────────
    # Maintenance & diagnostics
    # ──────────────────────────────────────────────────────────────

    def checkpoint(self, truncate: bool = True) -> bool:
        """run ``PRAGMA wal_checkpoint(TRUNCATE)`` (or
        ``RESTART``) on the writer thread.

        Used by GDPR delete/export paths to ensure all WAL content is
        checkpointed back to the main DB file before file-level
        operations (e.g. ``os.unlink`` of ``history.db``). Without
        this, dictated text remains recoverable from the
        ``history.db-wal`` sidecar file even after the main DB file
        is deleted — see

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
        """return a health status dict for diagnostics.

        Returns
        -------
        ``{"ok": bool, "error": str | None}``

        - ``ok`` is ``True`` only if the writer thread is alive AND
          ``_init_error`` is ``None`` (no schema init failure) AND
          ``_writer_ready`` is set (schema init has finished). This is
          the minimum viable health signal: a dead writer, a failed
          migration, or a still-running init means writes will
          silently fail.
        - ``error`` is a human-readable string describing the
          failure, or ``None`` if healthy.

        Callers (e.g. the IPC ``get_diagnostics`` handler) can expose
        this to the renderer so the user sees a clear "history DB is
        unavailable" message instead of silently-failed writes.

        The ``_writer_ready`` check is critical: ``__init__`` returns
        to the caller after at most ``_WRITER_READY_TIMEOUT`` (30s)
        even if the writer thread hasn't finished schema init. Without
        this check, ``health_check`` would return ``{ok: True}``
        during that 30s init window — readers connecting before the
        schema exists would get "no such table" errors, and writes
        would queue up but never run. Surfacing "still initializing"
        lets callers (e.g. the IPC layer) back off or show a
        "warming up" message instead of treating the DB as healthy.
        """
        if self._init_error is not None:
            return {"ok": False, "error": str(self._init_error)}
        if not self._writer_thread.is_alive():
            return {"ok": False, "error": "history DB writer thread is not alive"}
        if not self._writer_ready.is_set():
            return {
                "ok": False,
                "error": "history DB schema initialization still in progress (writer not ready)",
            }
        return {"ok": True, "error": None}
