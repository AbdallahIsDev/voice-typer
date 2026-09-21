"""History DB schema init/migrations + write-connection helpers.

Deep migration rationale: docs/code-notes/backend.md#history-db-schema.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server.platform_utils import is_windows

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

#: Dedupes the one-time [HISTORY] initialized INFO line across writer +
_announced_db_paths: set[str] = set()

_CURRENT_SCHEMA_VERSION = 5

_MIGRATION_V2 = """
    ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;
    ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT '';
"""

# FTS5 virtual table + sync triggers; additive/idempotent; explicit
_MIGRATION_V3 = """
    BEGIN;
    CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts USING fts5(
        text,
        content='transcriptions',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS transcriptions_ai_fts AFTER INSERT ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_ad_fts AFTER DELETE ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_au_fts AFTER UPDATE ON transcriptions BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    INSERT INTO transcriptions_fts(rowid, text) SELECT id, text FROM transcriptions;
    COMMIT;
"""

# At-rest encryption. PLAIN migration for prior-state
_MIGRATION_V4 = """
    ALTER TABLE transcriptions ADD COLUMN text_is_encrypted INTEGER DEFAULT 0;
    DROP TRIGGER IF EXISTS transcriptions_ai_fts;
    DROP TRIGGER IF EXISTS transcriptions_ad_fts;
    DROP TRIGGER IF EXISTS transcriptions_au_fts;
    CREATE TRIGGER transcriptions_ai_fts AFTER INSERT ON transcriptions
    WHEN new.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER transcriptions_ad_fts AFTER DELETE ON transcriptions
    WHEN old.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER transcriptions_au_fts AFTER UPDATE ON transcriptions
    WHEN NEW.text_is_encrypted = 0 AND OLD.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
    END;
"""

# CJK trigram FTS. CONTRACT: CJK/fullwidth len>=3 → trigram MATCH; 1–2 char
_MIGRATION_V5 = """
    BEGIN;
    CREATE VIRTUAL TABLE IF NOT EXISTS transcriptions_fts_cjk USING fts5(
        text,
        content='transcriptions',
        content_rowid='id',
        tokenize='trigram'
    );
    CREATE TRIGGER IF NOT EXISTS transcriptions_ai_fts_cjk AFTER INSERT ON transcriptions
    WHEN new.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts_cjk(rowid, text) VALUES (new.id, new.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_ad_fts_cjk AFTER DELETE ON transcriptions
    WHEN old.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk, rowid, text) VALUES ('delete', old.id, old.text);
    END;
    CREATE TRIGGER IF NOT EXISTS transcriptions_au_fts_cjk AFTER UPDATE ON transcriptions
    WHEN NEW.text_is_encrypted = 0 AND OLD.text_is_encrypted = 0
    BEGIN
        INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk, rowid, text) VALUES ('delete', old.id, old.text);
        INSERT INTO transcriptions_fts_cjk(rowid, text) VALUES (new.id, new.text);
    END;
    INSERT INTO transcriptions_fts_cjk(transcriptions_fts_cjk) VALUES('rebuild');
    COMMIT;
"""

_MIGRATIONS = {
    2: _MIGRATION_V2,
    3: _MIGRATION_V3,
    4: _MIGRATION_V4,
    5: _MIGRATION_V5,
}

#: Min SQLite for FTS5 trigram (V5 CJK). Older libsqlite3 → skip migration
_SQLITE_TRIGRAM_MIN_VERSION = (3, 34, 0)


def sqlite_supports_trigram() -> bool:
    """Return True when the linked libsqlite3 has the FTS5 trigram tokenizer."""
    return sqlite3.sqlite_version_info >= _SQLITE_TRIGRAM_MIN_VERSION


def cjk_trigram_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True when the schema-V5 trigram CJK shadow table exists.
    ``transcriptions_fts_cjk`` reference: the migration skips table
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transcriptions_fts_cjk'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


#: Regex for ALTER TABLE ... ADD COLUMN so the runner can skip columns
_ADD_COLUMN_RE = re.compile(
    r"\bALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN\s+(\w+)",
    re.IGNORECASE,
)


def _add_column_name(stmt: str) -> str | None:
    """Return the column added by an ``ALTER TABLE ... ADD COLUMN`` statement."""
    match = _ADD_COLUMN_RE.search(stmt)
    return match.group(1) if match else None


def open_write_conn(db_path: Path) -> sqlite3.Connection:
    """Open and configure the writer thread's connection.
    SEC-007: on POSIX, tightens the DB file and its parent
    """
    # Ensure parent dir exists before open (SQLite cannot mkdir).
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("[HISTORY_DB] Could not create DB directory %s: %s", db_path.parent, e)
    # SEC-007: tighten dir permissions before the connection creates
    if not is_windows():
        try:
            os.chmod(db_path.parent, 0o700)
        except OSError as e:
            log.warning("[HISTORY_DB] Could not tighten dir perms: %s", e)
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        timeout=5.0,
    )
    # Safety net for external contention only (in-process contention
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-20000")  # 20 MB
    # secure_delete=ON zeros freed pages so dictated text is not
    conn.execute("PRAGMA secure_delete=ON")
    conn.row_factory = sqlite3.Row
    # SEC-007: chmod the DB file (and sidecar files if present).
    if not is_windows():
        for suffix in ("", "-wal", "-shm"):
            p = db_path.with_suffix(db_path.suffix + suffix) if suffix else db_path
            try:
                if p.exists():
                    os.chmod(p, 0o600)
            except OSError:
                pass
    # FK enforcement is per-connection (not DB-persistent). Best-effort:
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error as e:
        log.debug(
            "[HISTORY_DB] PRAGMA foreign_keys=ON failed (best-effort): %s",
            e,
        )
    return conn


def check_wal_mode(conn: sqlite3.Connection, db_path: Path) -> None:
    """Verify WAL mode is actually enabled."""
    try:
        cur = conn.execute("PRAGMA journal_mode=WAL")
        mode_row = cur.fetchone()
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY_DB] Could not set/check WAL mode (%s) at %s, "
            "app will work but writes may be slower and more contended.",
            e,
            db_path,
        )
        return
    mode = mode_row[0] if mode_row else ""
    if str(mode).lower() != "wal":
        log.warning(
            "[HISTORY_DB] WAL mode NOT enabled (got %r) at %s, "
            "app will work but writes may be slower and more contended.",
            mode,
            db_path,
        )
    # Re-chmod after WAL PRAGMA: -wal/-shm are created then, not during
    if not is_windows():
        for suffix in ("", "-wal", "-shm"):
            p = db_path.with_suffix(db_path.suffix + suffix) if suffix else db_path
            try:
                if p.exists():
                    os.chmod(p, 0o600)
            except OSError as chmod_exc:
                log.debug(
                    "[HISTORY_DB] chmod 0o600 on %s failed after WAL switch (best-effort): %s",
                    p,
                    chmod_exc,
                )


def init_schema(
    db: HistoryDB,
    conn: sqlite3.Connection,
    _is_recovery: bool = False,
) -> sqlite3.Connection:
    """Initialize the database schema and run migrations."""
    # Clear stale _init_error at entry so a prior failed recovery attempt
    with contextlib.suppress(Exception):
        db._init_error = None

    cursor = conn.cursor()

    # Close cursor on every exit path (migration fail / recovery / return).
    try:
        has_tables = cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        has_tables = True  # be conservative, don't touch auto_vacuum
    if not has_tables:
        try:
            cursor.execute("PRAGMA auto_vacuum=INCREMENTAL")
            log.info(
                "[HISTORY_DB] New DB: set PRAGMA auto_vacuum=INCREMENTAL "
                "(enables fast incremental_vacuum reclamation in "
                "apply_retention / clear_all)"
            )
        except sqlite3.Error as e:
            log.warning(
                "[HISTORY_DB] Could not set auto_vacuum=INCREMENTAL on "
                "new DB (%s), falling back to full-VACUUM reclamation",
                e,
            )

    # PRE-MIGRATION-BACKUP-ORDERING: backup BEFORE any write (incl. CREATE).
    fresh_db = False
    try:
        cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        current_version = int(row[0]) if row else 1
    except sqlite3.Error:
        # No schema_meta yet, brand-new DB (the CREATEs below make it).
        fresh_db = True
        current_version = 1

    if not fresh_db and current_version < _CURRENT_SCHEMA_VERSION:
        db._backup_before_migration(current_version)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            duration REAL DEFAULT 0,
            model TEXT DEFAULT '',
            device TEXT DEFAULT '',
            word_count INTEGER DEFAULT 0,
            char_count INTEGER DEFAULT 0,
            favorite INTEGER DEFAULT 0,
            language TEXT DEFAULT '',
            text_is_encrypted BOOLEAN DEFAULT 0
        )
    """)

    # Schema version tracking (must run BEFORE CREATE INDEX that
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    if fresh_db:
        # The fresh-DB branch could not read the version above (no
        cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        current_version = int(row[0]) if row else 1
    # Migration runner: executescript + explicit transaction; plain ALTER
    for version in range(current_version + 1, _CURRENT_SCHEMA_VERSION + 1):
        migration_sql = _MIGRATIONS.get(version)
        if not migration_sql:
            continue

        # V5 trigram gate: skip WITHOUT bumping version when SQLite lacks
        if version == 5 and not sqlite_supports_trigram():
            log.warning(
                "[HISTORY_DB] SQLite %s lacks the FTS5 trigram tokenizer "
                "(needs >= %s), skipping CJK index migration v5; CJK "
                "search falls back to the bounded LIKE path",
                sqlite3.sqlite_version,
                ".".join(str(part) for part in _SQLITE_TRIGRAM_MIN_VERSION),
            )
            break

        try:
            # Plain ALTER migrations: filter already-present columns, wrap
            needs_wrapper = "BEGIN;" not in migration_sql.upper()
            if needs_wrapper:
                cursor.execute("PRAGMA table_info(transcriptions)")
                pre_existing_cols = {row[1] for row in cursor.fetchall()}
                statements = [s.strip() for s in migration_sql.split(";") if s.strip()]
                kept: list[str] = []
                for stmt in statements:
                    col = _add_column_name(stmt)
                    if col is not None and col in pre_existing_cols:
                        log.info(
                            "[HISTORY_DB] Migration v%d: column %r "
                            "already exists, skipping ALTER "
                            "(partial-prior-state reconciliation)",
                            version,
                            col,
                        )
                        continue
                    kept.append(stmt)
                if kept:
                    wrapped_sql = "BEGIN;\n" + ";\n".join(kept) + ";\nCOMMIT;\n"
                    cursor.executescript(wrapped_sql)
                else:
                    log.info(
                        "[HISTORY_DB] Migration v%d: all statements "
                        "already applied, persisting version without "
                        "re-running any statement",
                        version,
                    )
            else:
                cursor.executescript(migration_sql)
            # Persist version after success (INSERT OR REPLACE).
            cursor.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
                (str(version),),
            )
            conn.commit()
            log.info(
                "[HISTORY_DB] Migrated schema to version %d (transactional, version persisted)",
                version,
            )
        except sqlite3.Error as e:
            # Rollback; version NOT bumped. Concurrent-writer duplicate
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            log.exception(
                "[HISTORY_DB] Migration v%d failed: %s (version NOT bumped; transaction rolled back; _init_error set)",
                version,
                e,
            )
            db._init_error = e
            with contextlib.suppress(Exception):
                cursor.close()
            return conn

    # Indexes AFTER migration so 'favorite' exists. Refresh columns post-
    cursor.execute("PRAGMA table_info(transcriptions)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp
        ON transcriptions(timestamp DESC)
    """)
    # Composite (timestamp DESC, id DESC) serves keyset pagination without
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp_id
        ON transcriptions(timestamp DESC, id DESC)
    """)
    if "favorite" in existing_columns:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorite
            ON transcriptions(favorite)
        """)
        # (favorite, timestamp ASC) for retention DELETE; IF NOT EXISTS is
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorite_timestamp
            ON transcriptions(favorite, timestamp ASC)
        """)
    else:
        log.warning(
            "[HISTORY_DB] Skipping idx_favorite creation: 'favorite' "
            "column missing (migration was rolled back or not yet "
            "applied). Next launch will retry.",
        )

    # Integrity check; skip on recovery to avoid infinite recursion.
    if not _is_recovery:
        new_conn = db._maybe_recover_from_corruption(conn)
        if new_conn is not None:
            # Corruption detected and a fresh DB was created.
            with contextlib.suppress(Exception):
                cursor.close()
            return init_schema(db, new_conn, _is_recovery=True)

    key = str(db.db_path)
    if key not in _announced_db_paths:
        # Emit the one-time INFO so the log stays clean when both the
        _announced_db_paths.add(key)
        log.info(
            "[HISTORY] History database initialized: %s (schema v%d)",
            db.db_path,
            _CURRENT_SCHEMA_VERSION,
        )
    else:
        # One-time INFO; re-entry stays silent (two-thread lazy construction).
        _announced_db_paths.add(key)
    with contextlib.suppress(Exception):
        cursor.close()
    return conn
