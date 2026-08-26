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
import queue
import re
import sqlite3
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voice_typer.server.history_db_internals.retention import RetentionResult

log = logging.getLogger(__name__)


# O2: the SQLite history database lives under a dedicated ``db/``
# subdir of the config dir (alongside ``logs/``, ``crashes/``, etc.),
# so ``history.db`` + its ``-wal``/``-shm`` sidecars + corrupt-quarantine
# + pre-migration backups no longer clutter the config-dir root. The
# legacy root-located file is migrated once (see
# :func:`_maybe_migrate_legacy_db`).
DB_SUBDIR = "db"

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

# At-rest encryption: number of pre-existing plaintext rows converted to
# ciphertext per background backfill step (schema v4+). Each step is a
# queued writer item that re-enqueues itself between batches, so a huge
# legacy DB never starves foreground dictation writes; the backfill is
# idempotent by the ``text_is_encrypted`` flag and resumes across
# launches. 100 rows ≈ a few ms of AES-256-GCM — imperceptible per batch.
_ENCRYPTION_BACKFILL_BATCH = 100

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
import voice_typer.server.history_db_internals.search as _search_helpers  # noqa: E402,F401 — backward-compat re-export

# DB file-safety helpers (secure copy, legacy relocation, corruption
# recovery) live in
# ``voice_typer.server.history_db_internals.corruption_recovery``. They
# are re-exported here under their original names so existing callers
# (and tests that import / monkeypatch
# ``history_db._secure_copy_db_file`` etc.) keep working unchanged.
# ``corruption_recovery._backup_before_migration`` reads
# ``_hd._secure_copy_db_file`` through THIS module's namespace at call
# time, so a facade-level monkeypatch of the copy helper is still
# observed by the backup path.
from voice_typer.server.history_db_internals.corruption_recovery import (  # noqa: E402,F401 — backward-compat re-export
    _maybe_migrate_legacy_db,
    _maybe_move_legacy_sidecar,
    _secure_copy_db_file,
)
from voice_typer.server.history_db_internals.schema import (  # noqa: E402,F401 — backward-compat re-export so tests reading history_db._MIGRATIONS / _CURRENT_SCHEMA_VERSION keep working
    _CURRENT_SCHEMA_VERSION,
    _MIGRATION_V2,
    _MIGRATION_V3,
    _MIGRATION_V4,
    _MIGRATIONS,
)

_is_fts_compatible_query = _search_helpers.is_fts_compatible_query
_has_cjk_or_wide_chars = _search_helpers.has_cjk_or_wide_chars
_prepare_like_search_pattern = _search_helpers.prepare_like_search_pattern
_project_text_row = _search_helpers.project_text_row
_sanitize_fts_query = _search_helpers.sanitize_fts_query


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

            config_dir = _config_dir()
            # O2: one-time migration of the legacy root-located DB (and
            # its -wal / -shm sidecars) into ``db/`` BEFORE the new
            # location is resolved, so the writer thread opens the moved
            # file (not a freshly-created empty one).
            _maybe_migrate_legacy_db(config_dir)
            db_path = config_dir / DB_SUBDIR / "history.db"

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
        # True when this session's startup FTS5 'rebuild' actually ran
        # (schema_meta flag NULL or '1'). A rebuild re-tokenizes from the
        # content table, so rows that were already encrypted at rest end
        # up with CIPHERTEXT tokens in the index — ``_init_encryption``
        # responds by queueing a decrypt-aware re-index (see
        # ``_reindex_encrypted_fts_step``) to restore the §6 invariant
        # (FTS shadow tables stay plaintext-tokenized).
        self._fts5_rebuild_ran: bool = False
        # Ascending-id watermark for ``_reindex_encrypted_fts_step`` —
        # lets the decrypt-aware FTS re-index resume across its bounded
        # batches without re-processing rows or holding their ids.
        self._fts_reindex_watermark: int = 0
        # At-rest-encryption status — one of "active" / "disabled" /
        # "key-unavailable" (see :meth:`encryption_status`). Set by
        # ``_init_encryption`` on the writer thread after schema init;
        # "disabled" is the pre-resolution default so a HistoryDB whose
        # writer never gets that far (init failure) reports the same
        # state as plaintext mode.
        self._encryption_status: str = "disabled"
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
        """Run a passive WAL checkpoint every ``_WAL_CHECKPOINT_INTERVAL`` seconds.

        PASSIVE mode doesn't block — it checkpoints as much as
        possible without forcing readers/writers to wait. Called by the
        writer thread on its queue-wait timeout cadence. Before the
        checkpoint, any lingering uncommitted transaction is rolled back
        (WAL-CHECKPOINT-FIX) so the writer's own connection can never
        make ``PRAGMA wal_checkpoint(PASSIVE)`` fail with "database table
        is locked".

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._run_checkpoint`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._run_checkpoint(self, conn)

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

        Runs ONCE per launch on the writer connection when the persisted
        ``fts5_rebuild_failed`` schema_meta flag is not ``'0'`` — bounds
        the worst-case exposure window for failed delete/clear_all/
        retention rebuilds (lingering dictated text in
        ``transcriptions_fts_data``, GDPR Art. 17) to "between launches".
        On success the flag is set to ``'0'`` so subsequent launches skip
        the O(N) rebuild; on failure it is set to ``'1'`` so the next
        launch retries. Best-effort: failures are logged at WARNING and
        swallowed — the app must still start.

        Delegates to
        :func:`voice_typer.server.history_db_internals.writer._fts5_startup_rebuild`.
        """
        from voice_typer.server.history_db_internals import writer

        writer._fts5_startup_rebuild(self, conn)

    # ──────────────────────────────────────────────────────────────
    # At-rest encryption (see docs/adr/XZ-R11-04-at-rest-encryption.md)
    # ──────────────────────────────────────────────────────────────

    def _init_encryption(self, conn: sqlite3.Connection) -> None:
        """Resolve the DEK once per process and kick the backfill.

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._init_encryption`.
        """
        from voice_typer.server.history_db_internals import encryption

        encryption._init_encryption(self, conn)

    def encryption_status(self) -> str:
        """Return the at-rest-encryption state of this HistoryDB.

        One of ``"active"`` / ``"disabled"`` / ``"key-unavailable"``.
        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption.encryption_status`.
        """
        from voice_typer.server.history_db_internals import encryption

        return encryption.encryption_status(self)

    def _has_encrypted_rows(self, conn: sqlite3.Connection) -> bool:
        """Return True when at least one row is flagged encrypted.

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._has_encrypted_rows`.
        """
        from voice_typer.server.history_db_internals import encryption

        return encryption._has_encrypted_rows(self, conn)

    def _has_plaintext_rows(self, conn: sqlite3.Connection) -> bool:
        """Return True when at least one non-empty row is still plaintext.

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._has_plaintext_rows`.
        """
        from voice_typer.server.history_db_internals import encryption

        return encryption._has_plaintext_rows(self, conn)

    def _enqueue_backfill_step(self) -> None:
        """Queue one bounded plaintext→ciphertext backfill batch (fire-and-forget).

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._enqueue_backfill_step`.
        """
        from voice_typer.server.history_db_internals import encryption

        encryption._enqueue_backfill_step(self)

    def _encrypt_backfill_step(self, conn: sqlite3.Connection) -> int:
        """Encrypt up to ``_ENCRYPTION_BACKFILL_BATCH`` plaintext rows.

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._encrypt_backfill_step`.
        """
        from voice_typer.server.history_db_internals import encryption

        return encryption._encrypt_backfill_step(self, conn)

    def _enqueue_reindex_step(self) -> None:
        """Queue one bounded decrypt-aware FTS re-index batch (fire-and-forget).

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._enqueue_reindex_step`.
        """
        from voice_typer.server.history_db_internals import encryption

        encryption._enqueue_reindex_step(self)

    def _reindex_encrypted_fts_step(self, conn: sqlite3.Connection) -> int:
        """Restore plaintext FTS tokens for encrypted rows after a 'rebuild'.

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._reindex_encrypted_fts_step`.
        """
        from voice_typer.server.history_db_internals import encryption

        return encryption._reindex_encrypted_fts_step(self, conn)

    def _mark_fts5_rebuild_failed(self, conn: sqlite3.Connection) -> None:
        """Persist the ``fts5_rebuild_failed`` flag so the next launch
        retries the FTS5 startup rebuild.

        Delegates to
        :func:`voice_typer.server.history_db_internals.encryption._mark_fts5_rebuild_failed`.
        """
        from voice_typer.server.history_db_internals import encryption

        encryption._mark_fts5_rebuild_failed(self, conn)

    def _backup_before_migration(self, current_version: int) -> None:
        """Best-effort copy of the DB (+ sidecars) before a migration runs.

        Delegates to
        :func:`voice_typer.server.history_db_internals.corruption_recovery._backup_before_migration`.
        """
        from voice_typer.server.history_db_internals import corruption_recovery

        corruption_recovery._backup_before_migration(self, current_version)

    def _maybe_recover_from_corruption(
        self,
        conn: sqlite3.Connection,
    ) -> sqlite3.Connection | None:
        """``PRAGMA quick_check`` gate → rename corrupt DB → fresh DB.

        Delegates to
        :func:`voice_typer.server.history_db_internals.corruption_recovery._maybe_recover_from_corruption`.
        """
        from voice_typer.server.history_db_internals import corruption_recovery

        return corruption_recovery._maybe_recover_from_corruption(self, conn)

    def _try_iterdump_recovery(self, old_db_path: Path) -> list[str]:
        """Recover ``INSERT INTO transcriptions`` statements via iterdump().

        Delegates to
        :func:`voice_typer.server.history_db_internals.corruption_recovery._try_iterdump_recovery`.
        """
        from voice_typer.server.history_db_internals import corruption_recovery

        return corruption_recovery._try_iterdump_recovery(self, old_db_path)

    def _apply_recovered_inserts(
        self,
        conn: sqlite3.Connection,
        inserts: list[str],
    ) -> int:
        """Replay iterdump-recovered INSERT statements on the fresh DB.

        Delegates to
        :func:`voice_typer.server.history_db_internals.corruption_recovery._apply_recovered_inserts`.
        """
        from voice_typer.server.history_db_internals import corruption_recovery

        return corruption_recovery._apply_recovered_inserts(self, conn, inserts)

    def _notify_corruption_recovered(
        self,
        corrupt_main: Path,
        recovered_count: int,
    ) -> None:
        """Surface the corruption event to the user (log/event/tray).

        Delegates to
        :func:`voice_typer.server.history_db_internals.corruption_recovery._notify_corruption_recovered`.
        """
        from voice_typer.server.history_db_internals import corruption_recovery

        corruption_recovery._notify_corruption_recovered(self, corrupt_main, recovered_count)

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
        placeholder row_id (always 1) — the transcription pipeline
        never waits on the DB write. Returns -1 if the writer is
        unavailable. Callers that need the actual row_id should call
        ``flush()`` then read it back via ``get_recent``.
        """
        from voice_typer.server.history_db_internals import crud_writes

        # early-return guard — if the writer thread never
        # started (init error) or died, return -1 immediately instead
        # of silently enqueuing to a dead writer's queue.
        if self._init_error is not None or not self._writer_thread.is_alive():
            log.error(
                "[HISTORY_DB] add_transcription refused — writer is unavailable: %s",
                self.health_check()["error"],
            )
            return -1
        return crud_writes.add_transcription(
            self,
            text,
            duration=duration,
            model=model,
            device=device,
            language=language,
        )

    @_wrap_write(False, "delete transcription", "delete")
    def delete(self, transcription_id: int, *, raise_on_error: bool = False) -> bool:
        """Delete a transcription by ID.

        when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``False``. Without this,
        the IPC layer cannot tell "row didn't exist" from "DB error".

        After the row DELETE + commit, a best-effort FTS5 ``'optimize'``
        purges the deleted row's dictated text from
        ``transcriptions_fts_data`` (GDPR Art. 17 forensic-recovery
        guarantee) — see
        :func:`voice_typer.server.history_db_internals.crud_writes.delete_row`
        for the full rationale. Returns ``False`` if the row didn't exist
        or the writer is unavailable; invalidates both caches on success.
        """
        from voice_typer.server.history_db_internals import crud_writes

        result = self._submit_write(lambda conn: crud_writes.delete_row(self, conn, transcription_id), wait=True)
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

        At-rest encryption: mirrors the add_transcription write path —
        the row is inserted with PLAINTEXT (so the AFTER-INSERT FTS
        trigger indexes it) and then flipped to ciphertext +
        ``text_is_encrypted=1`` in the same transaction when a DEK is
        cached; without a DEK the row stays plaintext (flag 0).

        Returns the new row id, or -1 on failure.

        The row-level body runs on the writer thread via
        :func:`voice_typer.server.history_db_internals.crud_writes.restore_row`.
        """
        from voice_typer.server.history_db_internals import crud_writes

        text = str(record.get("text", ""))
        duration = float(record.get("duration", 0) or 0)
        model = str(record.get("model", "") or "")
        device = str(record.get("device", "") or "")
        language = str(record.get("language", "") or "")
        word_count = int(record.get("word_count", 0) or len(text.split()))
        char_count = int(record.get("char_count", 0) or len(text))
        favorite = 1 if record.get("favorite") else 0

        result = self._submit_write(
            lambda conn: crud_writes.restore_row(
                self,
                conn,
                text=text,
                duration=duration,
                model=model,
                device=device,
                language=language,
                word_count=word_count,
                char_count=char_count,
                favorite=favorite,
            ),
            wait=True,
        )
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

        Chunked DELETE + VACUUM + FTS5 ``'rebuild'`` on the writer
        thread, so cleared text is not recoverable from the file or from
        the FTS5 shadow tables (GDPR Art. 17) — see
        :func:`voice_typer.server.history_db_internals.crud_writes.clear_all_rows`
        for the full rationale. see ``delete`` for ``raise_on_error``
        semantics.
        """
        from voice_typer.server.history_db_internals import crud_writes

        result = self._submit_write(lambda conn: crud_writes.clear_all_rows(self, conn), wait=True)
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

        The row-level body runs on the writer thread via
        :func:`voice_typer.server.history_db_internals.crud_writes.toggle_favorite_row`.
        """
        from voice_typer.server.history_db_internals import crud_writes

        result = self._submit_write(
            lambda conn: crud_writes.toggle_favorite_row(self, conn, transcription_id), wait=True
        )
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
        succeeded. The ``int`` return contract is preserved
        so existing callers (``deleted == 20``, ``if deleted > 0``)
        work unchanged.

        Delegates to
        :func:`voice_typer.server.history_db_internals.retention.apply_retention`.
        See that function for the full rationale (UTC cutoff fix,
        IMPL-A chunked deletes on the writer thread, conditional
        VACUUM, FTS5 rebuild, cache invalidation, sentinel-on-error
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
        ``HistoryDBError`` instead of returning ``[]`` — the IPC layer
        can then distinguish "empty result" from "operation failed".

        Rows carry a 500-char ``text`` preview plus ``text_truncated``
        (bool) / ``text_full_length`` (int). Keyset pagination: pass
        BOTH ``before_timestamp`` AND ``before_id`` (the last row of the
        previous page) for O(log N) deep paging; otherwise OFFSET is
        used and must stay < 1000.

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

        ADR-0010 §8.1 / DP6. Ordered by the autoincrement PK (DESC) —
        same-second timestamps tie, so the PK is the only correct "most
        recent" signal. If you just called ``add_transcription()``, call
        ``flush()`` first to guarantee the row is committed.

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

        FTS5 serves tokenizable queries (tokens quoted as literal
        phrases); separator-only and CJK/fullwidth queries fall back to
        LIKE so literal wildcards still match and CJK substring search
        works — see
        :func:`voice_typer.server.history_db_internals.search.search`
        for the full rationale.
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

        see ``get_recent`` for ``raise_on_error`` semantics. A 15s TTL
        cache (invalidated by EVERY mutation) wraps the aggregating
        scan; the returned dict is a shallow copy — see
        :func:`voice_typer.server.history_db_internals.search.get_today_stats`.
        """
        from voice_typer.server.history_db_internals import search

        return search.get_today_stats(self)

    def _invalidate_today_stats_cache(self) -> None:
        """drop the cached today-stats dict.

        Unlike ``_invalidate_history_count_cache`` (which skips
        fire-and-forget ``add_transcription``), this is invalidated on
        EVERY mutation — today's stats must update live. Delegates to
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

        60s TTL cache, invalidated immediately on
        delete/clear_all/restore/apply_retention; fire-and-forget
        ``add_transcription`` does NOT invalidate (a stale-by-1 count is
        fine for the "Total Dictations" stat card). Delegates to
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
        """run ``PRAGMA wal_checkpoint(TRUNCATE)`` (or ``RESTART``) on the
        writer thread.

        Used by GDPR delete/export paths to ensure all WAL content is
        checkpointed back to the main DB file before file-level
        operations (e.g. ``os.unlink`` of ``history.db``). ``truncate``
        (default) additionally truncates the WAL to zero size; the
        closure body is
        :func:`voice_typer.server.history_db_internals.crud_writes.checkpoint_wal`.

        Returns
        -------
        ``True`` if the checkpoint completed without error, ``False``
        otherwise (writer unavailable, checkpoint failed). The caller
        should treat ``False`` as "WAL may still contain data; do not
        unlink until next attempt".
        """
        from voice_typer.server.history_db_internals import crud_writes

        try:
            result = self._submit_write(lambda conn: crud_writes.checkpoint_wal(self, conn, truncate), wait=True)
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
