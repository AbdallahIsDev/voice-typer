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
import re
import sqlite3
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voice_typer.server.history_db_internals import (
    corruption_recovery,
    crud_writes,
    encryption,
    lifecycle,
    reader,
    retention,
    schema,
    search,
    writer,
)
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

    IMPL-A single-writer architecture: a dedicated writer thread owns
    the only write-capable connection and drains a bounded queue of
    write closures serially; reads use thread-local read-only
    connections (WAL — readers never block the writer).
    ``add_transcription`` is fire-and-forget; other write methods block
    on a ``Future`` so callers see the result.

    Method bodies live in ``history_db_internals.*`` (free functions
    taking the instance); this class keeps the public/patch surface —
    every delegate below reads its implementation through the module
    attribute at call time, so monkeypatching keeps working.
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
        # Stateful attribute setup lives in
        # ``history_db_internals.lifecycle.initialize_state`` (it reads
        # ``_WRITE_QUEUE_MAXSIZE`` through this module's namespace at
        # call time so facade monkeypatches keep working).
        lifecycle.initialize_state(self)
        # Start the writer thread last — it signals _writer_ready once
        # the schema is set up.
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="HistoryDBWriter",
            daemon=True,
        )
        self._writer_thread.start()
        lifecycle.wait_for_writer_ready(self)

    # Section: Periodic read-conn prune

    def _start_read_conn_prune_thread(self) -> None:
        """Start the prune daemon — see internals.reader._start_read_conn_prune_thread."""
        reader._start_read_conn_prune_thread(self)

    # Back-compat alias for the previous name (kept so external code
    # and any in-flight branches that referenced the verbose name keep
    # working). New callers should use ``_start_read_conn_prune_thread``.
    _start_periodic_read_conn_prune = _start_read_conn_prune_thread

    def _stop_read_conn_prune_thread(self) -> None:
        """Stop the prune daemon — see internals.reader._stop_read_conn_prune_thread."""
        reader._stop_read_conn_prune_thread(self)

    # Back-compat alias for the previous name.
    _stop_periodic_read_conn_prune = _stop_read_conn_prune_thread

    def _periodic_read_conn_prune_loop(self) -> None:
        """Prune loop body (daemon thread) — see internals.reader._periodic_read_conn_prune_loop."""
        reader._periodic_read_conn_prune_loop(self)

    # Section: Writer thread

    def _writer_loop(self) -> None:
        """Drain the write queue serially — see internals.writer._writer_loop."""
        writer._writer_loop(self)

    def _execute_write_item(
        self,
        conn: sqlite3.Connection,
        callable_: Callable[[sqlite3.Connection], Any],
        future: concurrent.futures.Future | None,
    ) -> None:
        """Execute one write closure, resolve its future — see internals.writer._execute_write_item."""
        writer._execute_write_item(self, conn, callable_, future)

    def _drain_batchable_inserts(
        self,
        conn: sqlite3.Connection,
        first_item: _BatchableInsert,
    ) -> None:
        """Drain batchable INSERTs into one transaction — see internals.writer._drain_batchable_inserts."""
        writer._drain_batchable_inserts(self, conn, first_item)

    def _drain_remaining(self, conn: sqlite3.Connection) -> None:
        """Drain queued items before shutdown — see internals.writer._drain_remaining."""
        writer._drain_remaining(self, conn)

    def _run_checkpoint(self, conn: sqlite3.Connection) -> None:
        """Passive WAL checkpoint on the checkpoint-interval cadence — see internals.writer._run_checkpoint."""
        writer._run_checkpoint(self, conn)

    def _open_write_conn(self) -> sqlite3.Connection:
        """Open the writer connection — see internals.schema.open_write_conn."""
        return schema.open_write_conn(self.db_path)

    def _check_wal_mode(self, conn: sqlite3.Connection) -> None:
        """Verify WAL mode is enabled (warn on fallback) — see internals.schema.check_wal_mode."""
        schema.check_wal_mode(conn, self.db_path)

    def _init_db_schema(
        self,
        conn: sqlite3.Connection,
        _is_recovery: bool = False,
    ) -> sqlite3.Connection:
        """Initialize the schema + migrations, then run the FTS5 startup sweep.

        Delegates to
        :func:`voice_typer.server.history_db_internals.schema.init_schema`
        (which returns the connection to use — possibly a fresh one after
        corruption recovery). When init succeeds, the best-effort
        :meth:`_fts5_startup_rebuild` runs once on the writer connection
        (bounded re-exposure for failed rebuilds of the previous
        session; failures are swallowed so the app still starts). The
        FTS5 gate intentionally stays in the facade method — the
        corruption-recovery path (``_apply_recovered_inserts``) calls
        ``init_schema`` directly with ``_is_recovery=True`` and must NOT
        trigger an extra startup sweep.
        """
        new_conn = schema.init_schema(self, conn, _is_recovery=_is_recovery)
        if self._init_error is None:
            with contextlib.suppress(Exception):
                self._fts5_startup_rebuild(new_conn)
        return new_conn

    def _fts5_startup_rebuild(self, conn: sqlite3.Connection) -> None:
        """Best-effort FTS5 'rebuild' on a persisted failure flag — see internals.writer._fts5_startup_rebuild."""
        writer._fts5_startup_rebuild(self, conn)

    # Section: At-rest encryption (see docs/adr/XZ-R11-04-at-rest-encryption.md)

    def _init_encryption(self, conn: sqlite3.Connection) -> None:
        """Resolve the DEK once per process + kick the backfill — see internals.encryption._init_encryption."""
        encryption._init_encryption(self, conn)

    def encryption_status(self) -> str:
        """At-rest-encryption state of this DB — see internals.encryption.encryption_status."""
        return encryption.encryption_status(self)

    def _has_encrypted_rows(self, conn: sqlite3.Connection) -> bool:
        """Report whether any row is flagged encrypted — see internals.encryption._has_encrypted_rows."""
        return encryption._has_encrypted_rows(self, conn)

    def _has_plaintext_rows(self, conn: sqlite3.Connection) -> bool:
        """Report whether any non-empty row is still plaintext — see internals.encryption._has_plaintext_rows."""
        return encryption._has_plaintext_rows(self, conn)

    def _enqueue_backfill_step(self) -> None:
        """Queue one backfill batch — see internals.encryption._enqueue_backfill_step."""
        encryption._enqueue_backfill_step(self)

    def _encrypt_backfill_step(self, conn: sqlite3.Connection) -> int:
        """Encrypt one bounded backfill batch — see internals.encryption._encrypt_backfill_step."""
        return encryption._encrypt_backfill_step(self, conn)

    def _enqueue_reindex_step(self) -> None:
        """Queue one bounded decrypt-aware FTS re-index batch — see internals.encryption._enqueue_reindex_step."""
        encryption._enqueue_reindex_step(self)

    def _reindex_encrypted_fts_step(self, conn: sqlite3.Connection) -> int:
        """Restore plaintext FTS tokens for encrypted rows — see internals.encryption._reindex_encrypted_fts_step."""
        return encryption._reindex_encrypted_fts_step(self, conn)

    def _mark_fts5_rebuild_failed(self, conn: sqlite3.Connection) -> None:
        """Persist the fts5_rebuild_failed flag — see internals.encryption._mark_fts5_rebuild_failed."""
        encryption._mark_fts5_rebuild_failed(self, conn)

    def _backup_before_migration(self, current_version: int) -> None:
        """Best-effort pre-migration backup — see internals.corruption_recovery._backup_before_migration."""
        corruption_recovery._backup_before_migration(self, current_version)

    def _maybe_recover_from_corruption(
        self,
        conn: sqlite3.Connection,
    ) -> sqlite3.Connection | None:
        """Corruption gate + fresh DB — see internals.corruption_recovery._maybe_recover_from_corruption."""
        return corruption_recovery._maybe_recover_from_corruption(self, conn)

    def _try_iterdump_recovery(self, old_db_path: Path) -> list[str]:
        """Recover user-data INSERTs from the corrupt DB — see internals.corruption_recovery._try_iterdump_recovery."""
        return corruption_recovery._try_iterdump_recovery(self, old_db_path)

    def _apply_recovered_inserts(
        self,
        conn: sqlite3.Connection,
        inserts: list[str],
    ) -> int:
        """Replay recovered INSERTs on the fresh DB — see internals.corruption_recovery._apply_recovered_inserts."""
        return corruption_recovery._apply_recovered_inserts(self, conn, inserts)

    def _notify_corruption_recovered(
        self,
        corrupt_main: Path,
        recovered_count: int,
    ) -> None:
        """Surface the corruption event to the user — see internals.corruption_recovery._notify_corruption_recovered."""
        corruption_recovery._notify_corruption_recovered(self, corrupt_main, recovered_count)

    # Section: Read connections

    def _get_read_conn(self) -> sqlite3.Connection:
        """Get a thread-local READ-ONLY connection — see internals.reader._get_read_conn."""
        return reader._get_read_conn(self)

    def _prune_dead_read_connections_locked(self) -> None:
        """Close dead-thread read connections — see internals.reader._prune_dead_read_connections_locked."""
        reader._prune_dead_read_connections_locked(self)

    def _get_conn(self) -> sqlite3.Connection:
        """Backwards-compat alias for ``_get_read_conn`` — delegates to internals.reader._get_conn."""
        return reader._get_conn(self)

    # Section: Write submission

    def _drop_oldest_for_overflow(self, current_future: concurrent.futures.Future | None) -> None:
        """Drop oldest queued item to make room — see internals.writer._drop_oldest_for_overflow."""
        writer._drop_oldest_for_overflow(self, current_future)

    def _submit_write(
        self,
        fn: Callable[[sqlite3.Connection], Any],
        *,
        wait: bool = True,
    ) -> Any | None:
        """submit a write closure to the writer thread — delegates to internals.writer._submit_write."""
        return writer._submit_write(self, fn, wait=wait)

    def flush(self) -> None:
        """block until all queued writes have been processed — delegates to internals.writer.flush."""
        writer.flush(self)

    def _close_writer(self) -> None:
        """Writer-teardown portion of :meth:`close` — delegates to internals.writer._close_writer."""
        writer._close_writer(self)

    # Section: Lifecycle

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

        Delegates to
        :func:`voice_typer.server.history_db_internals.lifecycle.close_db`
        (retention-thread + prune-daemon stop, shutdown sentinel, writer
        drain + join, read-connection teardown). Idempotent — safe to
        call multiple times.
        """
        lifecycle.close_db(self)

    # Section: Public write methods

    def add_transcription(
        self,
        text: str,
        duration: float = 0,
        model: str = "",
        device: str = "",
        language: str = "",
    ) -> int:
        """Add a transcription (fire-and-forget): enqueue + placeholder row_id.

        Delegates to
        :func:`voice_typer.server.history_db_internals.crud_writes.add_transcription`.
        """
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

        ``raise_on_error=True`` raises ``HistoryDBError`` instead of
        returning ``False``. Delegates to
        :func:`voice_typer.server.history_db_internals.crud_writes.submit_delete`.
        """
        return crud_writes.submit_delete(self, transcription_id)

    @_wrap_write(-1, "restore transcription", "restore")
    def restore(
        self,
        record: dict,
        *,
        raise_on_error: bool = False,
    ) -> int:
        """Re-insert a previously-deleted transcription record.

        Supports the Undo-delete toast in the renderer; ``record`` is
        the dict shape returned by ``get_recent``. Delegates to
        :func:`voice_typer.server.history_db_internals.crud_writes.submit_restore`.
        """
        return crud_writes.submit_restore(self, record)

    @_wrap_write(False, "clear transcriptions", "clear_all")
    def clear_all(self, *, raise_on_error: bool = False) -> bool:
        """Clear all transcriptions (GDPR Art. 17 irreversible wipe).

        Delegates to
        :func:`voice_typer.server.history_db_internals.crud_writes.submit_clear_all`.
        """
        return crud_writes.submit_clear_all(self)

    @_wrap_write(False, "toggle favorite", "toggle_favorite")
    def toggle_favorite(self, transcription_id: int, *, raise_on_error: bool = False) -> bool:
        """Toggle the favorite status of a transcription.

        Delegates to
        :func:`voice_typer.server.history_db_internals.crud_writes.submit_toggle_favorite`.
        """
        return crud_writes.submit_toggle_favorite(self, transcription_id)

    def apply_retention(
        self,
        retention_days: int = 0,
        max_entries: int = 0,
        retention_count: int = 0,
    ) -> "RetentionResult":
        """Apply retention policy: delete old entries.

        Returns a :class:`RetentionResult` — an ``int`` subclass whose
        value is the number of deleted entries and whose
        ``fts5_rebuild_ok`` attribute reports whether the post-sweep
        FTS5 ``'rebuild'`` succeeded. Delegates to
        :func:`voice_typer.server.history_db_internals.retention.apply_retention`.
        """
        return retention.apply_retention(
            self,
            retention_days=retention_days,
            max_entries=max_entries,
            retention_count=retention_count,
        )

    # Section: Periodic retention scheduling ()

    def schedule_periodic_retention(
        self,
        interval_s: float = 600.0,
        app: Any = None,
        *,
        retention_days: int = 0,
        max_entries: int = 0,
        retention_count: int = 0,
    ) -> None:
        """Spawn the periodic retention daemon thread — see internals.retention.schedule_periodic_retention."""
        retention.schedule_periodic_retention(
            self,
            interval_s=interval_s,
            app=app,
            retention_days=retention_days,
            max_entries=max_entries,
            retention_count=retention_count,
        )

    def _stop_periodic_retention(self) -> None:
        """Signal + join the periodic retention thread — see internals.retention.stop_periodic_retention."""
        retention.stop_periodic_retention(self)

    # Section: Public read methods

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
        """Get recent transcriptions with pagination.

        ``raise_on_error=True`` raises ``HistoryDBError`` instead of
        returning ``[]``; rows carry a 500-char ``text`` preview plus
        ``text_truncated`` / ``text_full_length``. Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_recent`.
        """
        return search.get_recent(self, limit, offset, before_timestamp=before_timestamp, before_id=before_id)

    def get_latest_text(self) -> str:
        """Return the most recent transcription text, or ``""`` if DB is empty.

        Ordered by the autoincrement PK (DESC); call ``flush()`` after
        ``add_transcription()`` to guarantee the row is committed.
        Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_latest_text`.
        """
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
        """Search transcriptions by text with pagination.

        Delegates to
        :func:`voice_typer.server.history_db_internals.search.search`
        (FTS5 for tokenizable queries, LIKE fallback for separator-only
        and CJK/fullwidth queries).
        """
        return search.search(self, query, limit, offset, before_timestamp=before_timestamp, before_id=before_id)

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
        """get favorited transcriptions with pagination — delegates to internals.search.get_favorites."""
        return search.get_favorites(self, limit, offset, before_timestamp=before_timestamp, before_id=before_id)

    @_wrap_read(lambda: {"count": 0, "chars": 0, "word_count": 0, "duration": 0}, "get today stats")
    def get_today_stats(self, *, raise_on_error: bool = False) -> dict:
        """get statistics for today's transcriptions (15s TTL cache) — delegates to internals.search.get_today_stats."""
        return search.get_today_stats(self)

    def _invalidate_today_stats_cache(self) -> None:
        """Drop the cached today-stats dict — see internals.search.invalidate_today_stats_cache."""
        search.invalidate_today_stats_cache(self)

    # Section: on-demand full-text + total-count accessors

    def get_transcription_text(
        self,
        transcription_id: int,
        *,
        raise_on_error: bool = False,
    ) -> dict:
        """Return the FULL ``text`` of a single transcription row.

        Companion to the 500-char ``text`` preview in list responses;
        returns ``{"id": int, "text": str}``. Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_transcription_text`.
        """
        return search.get_transcription_text(self, transcription_id, raise_on_error=raise_on_error)

    def get_history_count(self, *, raise_on_error: bool = False) -> int:
        """Return the total number of transcription rows (60s TTL cache).

        Invalidated on delete/clear_all/restore/apply_retention but NOT
        on fire-and-forget ``add_transcription``. Delegates to
        :func:`voice_typer.server.history_db_internals.search.get_history_count`.
        """
        return search.get_history_count(self, raise_on_error=raise_on_error)

    def _invalidate_history_count_cache(self) -> None:
        """drop the cached total-count int — delegates to internals.search.invalidate_history_count_cache."""
        search.invalidate_history_count_cache(self)

    # Section: Maintenance & diagnostics

    def checkpoint(self, truncate: bool = True) -> bool:
        """Run ``PRAGMA wal_checkpoint(TRUNCATE|RESTART)`` on the writer thread.

        Used by GDPR delete/export paths so all WAL content is
        checkpointed back to the main DB file before file-level
        operations. Delegates to
        :func:`voice_typer.server.history_db_internals.crud_writes.submit_checkpoint`.
        """
        return crud_writes.submit_checkpoint(self, truncate)

    def health_check(self) -> dict:
        """Return a health status dict for diagnostics.

        ``{"ok": bool, "error": str | None}`` — ``ok`` is True only if
        the writer thread is alive, schema init succeeded, and init has
        finished. Delegates to
        :func:`voice_typer.server.history_db_internals.lifecycle.health_check`.
        """
        return lifecycle.health_check(self)
