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

Nothing in this package is part of the public API — callers should always
import from :mod:`voice_typer.server.history_db`.
"""
