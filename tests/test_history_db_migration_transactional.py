"""
regression tests for transactional history DB migration.
ALTER's column is NOT added) and must NOT bump the schema version.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture
def history_db_module(monkeypatch):
    """Import the history_db module fresh for each test."""
    from voice_typer.server import history_db

    return history_db


def _make_v1_db(db_path) -> sqlite3.Connection:
    """Create a DB at schema v1 (no favorite/language columns, version=1)."""
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
    """migration must be atomic, all or nothing."""

    def test_migration_failure_rolls_back_and_does_not_bump_version(self, tmp_path, history_db_module):
        """A mid-migration sqlite3.Error must roll back ALL migration"""
        db_path = tmp_path / "test_history.db"
        # Set up a v1 DB (no favorite/language columns).
        setup_conn = _make_v1_db(db_path)
        setup_conn.close()

        failing_migration = """
            ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;
            INSERT INTO nonexistent_table VALUES (1);
            ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT '';
        """
        with patch.dict(history_db_module._MIGRATIONS, {2: failing_migration}):
            from voice_typer.server.history_db import HistoryDB

            db = HistoryDB(db_path=db_path)
            assert db._init_error is not None, (
                "Expected _init_db_schema to fail with the patched "
                "failing migration (INSERT into nonexistent table). "
                "If _init_error is None, the migration did not run or "
                "did not fail, the test setup is wrong."
            )
            assert isinstance(db._init_error, sqlite3.Error), (
                f"Expected sqlite3.Error from _init_db_schema, got {type(db._init_error).__name__}: {db._init_error}"
            )
            db.close()

        # Verify: the `favorite` column was NOT added (transaction
        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.row_factory = sqlite3.Row
        cursor = verify_conn.execute("PRAGMA table_info(transcriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "favorite" not in columns, (
            "CR-36 regression: `favorite` column was added despite the "
            "migration failing, the transaction did NOT roll back. "
            f"Columns: {columns}"
        )
        assert "language" not in columns, (
            "CR-36 regression: `language` column was added despite the "
            "migration failing, the transaction did NOT roll back. "
            f"Columns: {columns}"
        )

        # Verify: schema_meta.version is NOT bumped (still 1, not 2).
        cursor = verify_conn.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        version = int(row[0]) if row else 1
        assert version == 1, (
            "CR-36 regression: schema version was bumped to "
            f"{version} despite the migration failing, the version "
            "should remain at 1 so the next launch retries."
        )

        # Verify: indexes were NOT created (idx_favorite would fail
        cursor = verify_conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
        index_names = {row[0] for row in cursor.fetchall()}
        assert "idx_favorite" not in index_names, (
            "CR-36 regression: idx_favorite was created despite the "
            "migration failing, indexes must be inside the same "
            "transaction as the migration."
        )
        assert "idx_timestamp" not in index_names, (
            "CR-36 regression: idx_timestamp was created despite the "
            "migration failing, indexes must be inside the same "
            "transaction as the migration."
        )

        verify_conn.close()

    def test_migration_success_commits_version_and_indexes(self, tmp_path, history_db_module):
        """A successful migration commits the version AND the indexes"""
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
        """Calling _init_db_schema on an already-migrated DB is a no-op."""
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

        # Call _init_db_schema, should be a no-op.
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
