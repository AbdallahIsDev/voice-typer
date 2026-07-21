"""CR-36: regression tests for transactional history DB migration.

The previous ``_init_db_schema`` implementation ran each migration
statement in a per-statement ``try/except`` that only logged a warning
and CONTINUED. After the loop, ``schema_meta.version`` was
unconditionally bumped to ``_CURRENT_SCHEMA_VERSION`` and committed.
A partial migration (e.g., disk full mid-ALTER) left the schema in an
inconsistent state that's never self-healing.

The fix wraps the entire migration + index creation + version bump in
an explicit ``BEGIN; … COMMIT;`` transaction. On ANY ``sqlite3.Error``
mid-migration, the transaction rolls back and the version is NOT
bumped — the next launch retries.

These tests pin the new transactional behavior:

1. ``test_migration_failure_rolls_back_and_does_not_bump_version`` —
   a mid-migration failure must roll back ALL changes (the first
   ALTER's column is NOT added) and must NOT bump the schema version.
   The previous implementation would have left the first column in
   place AND bumped the version, leaving the schema in an inconsistent
   state.

2. ``test_migration_success_commits_version_and_indexes`` — a
   successful migration commits the version AND the indexes inside
   the same transaction.

3. ``test_idempotent_migration_on_already_migrated_db`` — calling
   ``_init_db_schema`` on an already-migrated DB is a no-op (the
   migration loop range is empty).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture
def history_db_module(monkeypatch):
    """Import the history_db module fresh for each test.

    We need to be able to mutate ``_MIGRATIONS`` and ``_CURRENT_SCHEMA_VERSION``
    without leaking state across tests, so we import the module and yield
    it directly.
    """
    from voice_typer.server import history_db

    return history_db


def _make_v1_db(db_path) -> sqlite3.Connection:
    """Create a DB at schema v1 (no favorite/language columns, version=1).

    This simulates an existing DB created before the v2 migration
    (which adds favorite + language columns) was introduced.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            duration REAL DEFAULT 0,
            model TEXT DEFAULT '',
            device TEXT DEFAULT '',
            word_count INTEGER DEFAULT 0,
            char_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO schema_meta (key, value) VALUES ('version', '1')")
    conn.commit()
    return conn


class TestMigrationTransactionality:
    """CR-36: migration must be atomic — all or nothing."""

    def test_migration_failure_rolls_back_and_does_not_bump_version(self, tmp_path, history_db_module):
        """A mid-migration sqlite3.Error must roll back ALL migration
        changes AND must NOT bump the schema version.

        The previous implementation caught the error per-statement and
        continued, then bumped the version unconditionally — leaving
        the schema in an inconsistent state (some columns added,
        version bumped, no retry on next launch).
        """
        db_path = tmp_path / "test_history.db"
        # Set up a v1 DB (no favorite/language columns).
        setup_conn = _make_v1_db(db_path)
        setup_conn.close()

        # Patch _MIGRATIONS to include a failing statement AFTER the
        # first ALTER TABLE. The first ALTER adds the `favorite`
        # column; the second statement fails (insert into nonexistent
        # table). With the OLD code, the `favorite` column would be
        # committed individually (DDL autocommits in default isolation
        # mode) and the version would be bumped to 2. With the CR-36
        # fix, the entire transaction rolls back: `favorite` column is
        # NOT added and version is NOT bumped.
        failing_migration = """
            ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;
            INSERT INTO nonexistent_table VALUES (1);
            ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT '';
        """
        with patch.dict(history_db_module._MIGRATIONS, {2: failing_migration}):
            from voice_typer.server.history_db import HistoryDB

            db = HistoryDB(db_path=db_path)
            # HistoryDB.__init__ waits for _writer_ready — by then, the
            # writer thread has either succeeded or failed at
            # _init_db_schema. With the patched failing migration, it
            # should have failed.
            assert db._init_error is not None, (
                "Expected _init_db_schema to fail with the patched "
                "failing migration (INSERT into nonexistent table). "
                "If _init_error is None, the migration did not run or "
                "did not fail — the test setup is wrong."
            )
            assert isinstance(db._init_error, sqlite3.Error), (
                f"Expected sqlite3.Error from _init_db_schema, got {type(db._init_error).__name__}: {db._init_error}"
            )
            db.close()

        # Verify: the `favorite` column was NOT added (transaction
        # rolled back). With the OLD code, this column would be
        # present (DDL autocommitted before the failing INSERT).
        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.row_factory = sqlite3.Row
        cursor = verify_conn.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "favorite" not in columns, (
            "CR-36 regression: `favorite` column was added despite the "
            "migration failing — the transaction did NOT roll back. "
            f"Columns: {columns}"
        )
        assert "language" not in columns, (
            "CR-36 regression: `language` column was added despite the "
            "migration failing — the transaction did NOT roll back. "
            f"Columns: {columns}"
        )

        # Verify: schema_meta.version is NOT bumped (still 1, not 2).
        # With the OLD code, the version would be bumped to 2 even
        # though the migration failed.
        cursor = verify_conn.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        version = int(row[0]) if row else 1
        assert version == 1, (
            "CR-36 regression: schema version was bumped to "
            f"{version} despite the migration failing — the version "
            "should remain at 1 so the next launch retries."
        )

        # Verify: indexes were NOT created (idx_favorite would fail
        # anyway because the column doesn't exist, but the transaction
        # also rolled back idx_timestamp which doesn't depend on any
        # migrated column).
        cursor = verify_conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
        index_names = {row[0] for row in cursor.fetchall()}
        assert "idx_favorite" not in index_names, (
            "CR-36 regression: idx_favorite was created despite the "
            "migration failing — indexes must be inside the same "
            "transaction as the migration."
        )
        assert "idx_timestamp" not in index_names, (
            "CR-36 regression: idx_timestamp was created despite the "
            "migration failing — indexes must be inside the same "
            "transaction as the migration."
        )

        verify_conn.close()

    def test_migration_success_commits_version_and_indexes(self, tmp_path, history_db_module):
        """A successful migration commits the version AND the indexes
        inside the same transaction.

        This test verifies the happy path is not broken by the
        transactional wrapping.
        """
        db_path = tmp_path / "test_history.db"
        setup_conn = _make_v1_db(db_path)
        setup_conn.close()

        # Use the default _MIGRATIONS (which adds favorite + language).
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        db._init_db_schema(conn)
        conn.close()
        db.close()

        # Verify: columns added.
        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.row_factory = sqlite3.Row
        cursor = verify_conn.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "favorite" in columns
        assert "language" in columns

        # Verify: version bumped to _CURRENT_SCHEMA_VERSION.
        cursor = verify_conn.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        assert row is not None
        assert int(row[0]) == history_db_module._CURRENT_SCHEMA_VERSION

        # Verify: indexes created.
        cursor = verify_conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
        index_names = {row[0] for row in cursor.fetchall()}
        assert "idx_favorite" in index_names
        assert "idx_timestamp" in index_names

        verify_conn.close()

    def test_idempotent_migration_on_already_migrated_db(self, tmp_path, history_db_module):
        """Calling _init_db_schema on an already-migrated DB is a no-op.

        The migration loop range is empty (current_version ==
        _CURRENT_SCHEMA_VERSION), so no migration statements run, no
        transaction is needed, and the indexes are created (IF NOT
        EXISTS) idempotently.
        """
        db_path = tmp_path / "test_history.db"
        # Create a fully-migrated DB directly.
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("""
            CREATE TABLE transcriptions (
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
        setup_conn.execute("""
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        setup_conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(history_db_module._CURRENT_SCHEMA_VERSION),),
        )
        setup_conn.execute("CREATE INDEX idx_timestamp ON transcriptions(timestamp DESC)")
        setup_conn.execute("CREATE INDEX idx_favorite ON transcriptions(favorite)")
        setup_conn.commit()
        setup_conn.close()

        # Call _init_db_schema — should be a no-op.
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        # Should not raise.
        db._init_db_schema(conn)
        conn.close()
        db.close()

        # Verify: version unchanged.
        verify_conn = sqlite3.connect(str(db_path))
        cursor = verify_conn.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        assert row is not None
        assert int(row[0]) == history_db_module._CURRENT_SCHEMA_VERSION
        verify_conn.close()
