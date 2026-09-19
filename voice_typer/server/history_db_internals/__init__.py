"""``_WAL_CHECKPOINT_INTERVAL``, ``_ENCRYPTION_BACKFILL_BATCH``,
This package exists to split the once-monolithic ``history_db.py`` into
focused submodules. The public API (``HistoryDB`` class, ``HistoryDBError``)
  submission, flush, and writer teardown (``_close_writer``). Extracted
  from ``HistoryDB`` so the writer logic is testable in isolation; the
  public class keeps thin delegating methods (``_writer_loop``,
  ``_execute_write_item``, ``_drain_batchable_inserts``,
  ``_drain_remaining``, ``_drop_oldest_for_overflow``, ``_submit_write``,
  ``flush``, ``_close_writer``) so the 173+ test monkeypatch sites
  (``monkeypatch.setattr(HistoryDB, "_writer_loop", ...)``) keep
  from ``HistoryDB``; the public class keeps thin delegating methods
  (``_get_read_conn``, ``_prune_dead_read_connections_locked``,
  ``_periodic_read_conn_prune_loop``, ``_start_read_conn_prune_thread``,
  ``_stop_read_conn_prune_thread``, ``_get_conn``).
  ``fts5_rebuild_failed`` flag persistence. The public class keeps thin
  delegating methods (``_init_encryption``, ``encryption_status``,
  ``_has_encrypted_rows``, ``_has_plaintext_rows``,
  ``_enqueue_backfill_step``, ``_encrypt_backfill_step``,
  ``_enqueue_reindex_step``, ``_reindex_encrypted_fts_step``,
  ``_mark_fts5_rebuild_failed``).
  ``history_db._secure_copy_db_file``), legacy root→``db/`` relocation,
  salvage, and the user-facing ``history_corrupted`` notification. The
  public class keeps thin delegating methods (``_backup_before_migration``,
  ``_maybe_recover_from_corruption``, ``_try_iterdump_recovery``,
  ``_apply_recovered_inserts``, ``_notify_corruption_recovered``).
  submit these free functions via ``db._submit_write(...)`` so the
  ``_wrap_write`` decorators, docstrings, cache invalidation, and
  ``raise_on_error`` semantics are unchanged.
  holds the writer-thread maintenance sweeps ``_run_checkpoint``
  (periodic passive WAL checkpoint) and ``_fts5_startup_rebuild``
  (launch-time FTS5 ``'rebuild'`` gated by the persisted failure flag);
``_WRITE_FUTURE_TIMEOUT``, ``DB_SUBDIR``, ``_INSERT_TRANSCRIPTIONS_RE``)
back through the ``history_db`` facade namespace at call time, so tests
"""
