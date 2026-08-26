"""Internal helpers for :mod:`voice_typer.server.history_db`.

This package exists to split the once-monolithic ``history_db.py`` into
focused submodules. The public API (``HistoryDB`` class, ``HistoryDBError``)
still lives in :mod:`voice_typer.server.history_db`; the modules here are
free-function helpers called by the public class via thin delegating
methods.

Submodules:

- :mod:`voice_typer.server.history_db_internals.schema` — connection
  setup, schema initialization, migrations, corruption recovery hooks.
- :mod:`voice_typer.server.history_db_internals.writer` — writer-thread
  queue draining, batched INSERT, drop-oldest overflow handling, write
  submission, flush, and writer teardown (``_close_writer``). Extracted
  from ``HistoryDB`` so the writer logic is testable in isolation; the
  public class keeps thin delegating methods (``_writer_loop``,
  ``_execute_write_item``, ``_drain_batchable_inserts``,
  ``_drain_remaining``, ``_drop_oldest_for_overflow``, ``_submit_write``,
  ``flush``, ``_close_writer``) so the 173+ test monkeypatch sites
  (``monkeypatch.setattr(HistoryDB, "_writer_loop", ...)``) keep
  working unchanged.
- :mod:`voice_typer.server.history_db_internals.reader` — thread-local
  read-connection pool, periodic dead-thread prune daemon. Extracted
  from ``HistoryDB``; the public class keeps thin delegating methods
  (``_get_read_conn``, ``_prune_dead_read_connections_locked``,
  ``_periodic_read_conn_prune_loop``, ``_start_read_conn_prune_thread``,
  ``_stop_read_conn_prune_thread``, ``_get_conn``).
- :mod:`voice_typer.server.history_db_internals.encryption` — at-rest
  encryption lifecycle: DEK resolution on the writer thread, bounded
  plaintext→ciphertext backfill, decrypt-aware FTS re-index, and the
  ``fts5_rebuild_failed`` flag persistence. The public class keeps thin
  delegating methods (``_init_encryption``, ``encryption_status``,
  ``_has_encrypted_rows``, ``_has_plaintext_rows``,
  ``_enqueue_backfill_step``, ``_encrypt_backfill_step``,
  ``_enqueue_reindex_step``, ``_reindex_encrypted_fts_step``,
  ``_mark_fts5_rebuild_failed``).
- :mod:`voice_typer.server.history_db_internals.corruption_recovery` —
  DB file-safety: symlink-safe secure copy (re-exported as
  ``history_db._secure_copy_db_file``), legacy root→``db/`` relocation,
  pre-migration backup, corruption detection/recovery with iterdump row
  salvage, and the user-facing ``history_corrupted`` notification. The
  public class keeps thin delegating methods (``_backup_before_migration``,
  ``_maybe_recover_from_corruption``, ``_try_iterdump_recovery``,
  ``_apply_recovered_inserts``, ``_notify_corruption_recovered``).
- :mod:`voice_typer.server.history_db_internals.crud_writes` — writer-
  thread bodies of the CRUD write paths (add/delete/restore/clear_all/
  toggle_favorite). The decorated public methods stay on the class and
  submit these free functions via ``db._submit_write(...)`` so the
  ``_wrap_write`` decorators, docstrings, cache invalidation, and
  ``raise_on_error`` semantics are unchanged.
- :mod:`voice_typer.server.history_db_internals.writer` additionally
  holds the writer-thread maintenance sweeps ``_run_checkpoint``
  (periodic passive WAL checkpoint) and ``_fts5_startup_rebuild``
  (launch-time FTS5 ``'rebuild'`` gated by the persisted failure flag);
  both run exclusively on the writer connection.

All functions read mutable module constants (e.g.
``_WAL_CHECKPOINT_INTERVAL``, ``_ENCRYPTION_BACKFILL_BATCH``,
``_WRITE_FUTURE_TIMEOUT``, ``DB_SUBDIR``, ``_INSERT_TRANSCRIPTIONS_RE``)
back through the ``history_db`` facade namespace at call time, so tests
that monkeypatch those constants on the facade keep working.

Nothing in this package is part of the public API — callers should always
import from :mod:`voice_typer.server.history_db`.
"""
