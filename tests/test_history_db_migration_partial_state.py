"""Regression test for partial-prior-state V2 migration reconciliation.

Background: ``_MIGRATION_V2`` adds two columns (``favorite`` and
``language``) via two ``ALTER TABLE ADD COLUMN`` statements. A previous
run could add ONE of the columns but fail (process killed, disk full)
before the schema version was persisted. On the next launch the verbatim
re-run hit "duplicate column name" on the already-added column, and the
old "duplicate column name" handler bumped the version unconditionally —
leaving the NOT-yet-added column missing forever.

The fix in :mod:`voice_typer.server.history_db_internals.schema`
pre-filters ``ALTER TABLE ADD COLUMN`` statements whose column already
exists, runs the remaining statements in a single transaction, and only
bumps the version when all remaining statements succeed.

These tests pin the reconciliation:

1. ``test_partial_prior_state_favorite_exists_language_missing`` — the
   core scenario: ``favorite`` present, ``language`` missing, version=1.
   After init, BOTH columns exist and the version is current.

2. ``test_partial_prior_state_both_columns_exist_version_not_bumped`` —
   both columns present but version=1 (prior run added both, crashed
   before bumping). After init, version is current and no ALTERs re-run.

3. ``test_clean_v1_db_migrates_both_columns`` — sanity: a clean v1 DB
   (no favorite/language) gets both columns and the current version.
"""

from __future__ import annotations

import sqlite3

from voice_typer.server.history_db import HistoryDB
from voice_typer.server.history_db_internals.schema import (
    _CURRENT_SCHEMA_VERSION,
    _add_column_name,
)


def _columns(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute("PRAGMA table_info(transcriptions)")
    return {row[1] for row in cursor.fetchall()}


def _version(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'")
    row = cursor.fetchone()
    return int(row[0]) if row else 1


def _make_base_v1_db(db_path) -> None:
    """Create a v1 DB (no favorite/language columns, version=1)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
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
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO schema_meta (key, value) VALUES ('version', '1')")
    conn.commit()
    conn.close()


def _add_column(conn: sqlite3.Connection, name: str, ddl: str) -> None:
    conn.execute(f"ALTER TABLE transcriptions ADD COLUMN {name} {ddl}")
    conn.commit()


class TestPartialPriorStateMigration:
    """The V2 migration must reconcile a partial-prior state, not abort."""

    def test_partial_prior_state_favorite_exists_language_missing(self, tmp_path):
        """favorite present, language missing, version=1 -> both present, version current.

        Simulates a prior run that added ``favorite`` but crashed before
        adding ``language`` and before persisting the version bump. The
        old handler bumped the version on "duplicate column name" and
        left ``language`` missing forever; the fix skips the already-
        present ``favorite`` ALTER and runs only the ``language`` ALTER.
        """
        db_path = tmp_path / "partial_state.db"
        _make_base_v1_db(db_path)
        # Simulate the partial-prior state: favorite added, language not.
        seed_conn = sqlite3.connect(str(db_path))
        _add_column(seed_conn, "favorite", "INTEGER DEFAULT 0")
        seed_conn.close()

        db = HistoryDB(db_path=db_path)
        assert db._init_error is None, (
            f"Expected migration to succeed via reconciliation, but _init_error is set: {db._init_error}"
        )
        db.close()

        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        cols = _columns(verify)
        assert "favorite" in cols, f"favorite missing after migration: {cols}"
        assert "language" in cols, (
            "language missing after migration — the partial-prior-state "
            f"reconciliation did NOT add the missing column: {cols}"
        )
        assert _version(verify) == _CURRENT_SCHEMA_VERSION, (
            f"version not bumped to current ({_CURRENT_SCHEMA_VERSION}): got {_version(verify)}"
        )
        verify.close()

    def test_partial_prior_state_both_columns_exist_version_not_bumped(self, tmp_path):
        """Both columns present, version=1 -> version bumped, no ALTERs re-run.

        Simulates a prior run that added BOTH columns but crashed before
        persisting the version bump. The fix detects both columns are
        present, skips all ALTERs, and just bumps the version.
        """
        db_path = tmp_path / "both_cols_no_version.db"
        _make_base_v1_db(db_path)
        seed_conn = sqlite3.connect(str(db_path))
        _add_column(seed_conn, "favorite", "INTEGER DEFAULT 0")
        _add_column(seed_conn, "language", "TEXT DEFAULT ''")
        seed_conn.close()

        db = HistoryDB(db_path=db_path)
        assert db._init_error is None, (
            f"Expected no-op migration (columns already present), but _init_error is set: {db._init_error}"
        )
        db.close()

        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        cols = _columns(verify)
        assert "favorite" in cols
        assert "language" in cols
        assert _version(verify) == _CURRENT_SCHEMA_VERSION, (
            f"version not bumped when both columns already existed: got {_version(verify)}"
        )
        verify.close()

    def test_clean_v1_db_migrates_both_columns(self, tmp_path):
        """A clean v1 DB (no favorite/language) migrates both columns."""
        db_path = tmp_path / "clean_v1.db"
        _make_base_v1_db(db_path)

        db = HistoryDB(db_path=db_path)
        assert db._init_error is None, f"Expected clean migration, but _init_error is set: {db._init_error}"
        db.close()

        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        cols = _columns(verify)
        assert "favorite" in cols, f"favorite missing on clean v1 DB: {cols}"
        assert "language" in cols, f"language missing on clean v1 DB: {cols}"
        assert _version(verify) == _CURRENT_SCHEMA_VERSION
        verify.close()


class TestAddColumnNameHelper:
    """The regex helper that drives ALTER filtering."""

    def test_extracts_alter_add_column_name(self):
        stmt = "ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0"
        assert _add_column_name(stmt) == "favorite"

    def test_extracts_language_column(self):
        stmt = "  ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT ''  "
        assert _add_column_name(stmt) == "language"

    def test_returns_none_for_non_alter(self):
        assert _add_column_name("INSERT INTO foo VALUES (1)") is None
        assert _add_column_name("CREATE INDEX idx ON transcriptions(favorite)") is None
        assert _add_column_name("SELECT * FROM transcriptions") is None

    def test_case_insensitive(self):
        stmt = "alter table transcriptions add column my_col text"
        assert _add_column_name(stmt) == "my_col"
