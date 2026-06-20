"""SQLite database for storing transcription history.

Rewritten with:
- WAL mode for concurrent read/write
- Connection pooling via thread-local connections
- Thread-safe operations
- Schema migration support
- Favorites support
- Retention policy (auto-delete old entries)
"""

import sqlite3
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CURRENT_SCHEMA_VERSION = 2
_MAX_SEARCH_QUERY_CHARS = 200

_MIGRATION_V2 = """
    ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;
    ALTER TABLE transcriptions ADD COLUMN language TEXT DEFAULT '';
"""

_MIGRATIONS = {
    2: _MIGRATION_V2,
}


def _prepare_like_search_pattern(query: str) -> str:
    """Build a bounded LIKE pattern where user wildcards stay literal."""
    capped_query = query[:_MAX_SEARCH_QUERY_CHARS]
    escaped_query = (
        capped_query
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped_query}%"


class HistoryDB:
    """Thread-safe SQLite database for transcription history."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from voice_typer.server.config import _config_dir
            db_path = _config_dir() / "history.db"

        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local database connection.

        PERF-002: each thread gets its own connection (stored in
        ``threading.local()``) so writes from the transcription thread
        don't block reads from the IPC thread.  WAL mode +
        ``synchronous=NORMAL`` gives good crash safety with low write
        latency.  ``busy_timeout=5000`` lets a writer wait briefly for
        a concurrent reader/writer to finish rather than failing
        immediately.  ``cache_size=-20000`` allocates a 20 MB page
        cache (negative value = kilobytes) so common reads stay in
        memory.

        SEC-007: on POSIX, tightens the DB file and its parent
        directory to 0o600 / 0o700 so transcription history is not
        world-readable.  SQLite creates ``-wal`` and ``-shm`` sidecar
        files in WAL mode; we chmod those too (best-effort, since
        they may be created lazily on first write).
        """
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            # SEC-007: tighten dir permissions before the connection
            # creates files in it.
            if sys.platform != "win32":
                try:
                    self.db_path.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(self.db_path.parent, 0o700)
                except OSError:
                    pass
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA cache_size=-20000")  # 20 MB
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            # SEC-007: chmod the DB file (and sidecar files if present).
            if sys.platform != "win32":
                for suffix in ("", "-wal", "-shm"):
                    p = self.db_path.with_suffix(self.db_path.suffix + suffix) if suffix else self.db_path
                    try:
                        if p.exists():
                            os.chmod(p, 0o600)
                    except OSError:
                        pass
        return self._local.conn

    def _init_db(self):
        """Initialize the database schema and run migrations."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # ═══ FIXED ORDER: schema/metadata BEFORE indexes that depend on migrated columns ═══
        #
        # BUG: The original code ran CREATE INDEX idx_favorite ON transcriptions(favorite)
        # BEFORE the migration code. On an existing database created without the 'favorite'
        # column, CREATE INDEX would fail with "no such column: favorite" because the
        # column didn't exist yet.
        #
        # FIX: Create the table first, then run schema versioning + migrations, then
        # create indexes. This ensures 'favorite' column exists before idx_favorite
        # tries to reference it.

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
                language TEXT DEFAULT ''
            )
        """)

        # Schema version tracking (must run BEFORE CREATE INDEX that references 'favorite')
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Get current schema version
        cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
        row = cursor.fetchone()
        current_version = int(row[0]) if row else 1

        # Run migrations BEFORE creating indexes that depend on migrated columns
        cursor.execute("PRAGMA table_info(transcriptions)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        for version in range(current_version + 1, _CURRENT_SCHEMA_VERSION + 1):
            migration_sql = _MIGRATIONS.get(version)
            if migration_sql:
                for stmt in migration_sql.strip().split(";"):
                    stmt = stmt.strip()
                    if not stmt:
                        continue
                    # Skip ALTER TABLE ADD COLUMN if column already exists
                    # Must extract the column name (word after ADD COLUMN), not the last token
                    if stmt.upper().startswith("ALTER TABLE") and "ADD COLUMN" in stmt.upper():
                        idx = stmt.upper().find("ADD COLUMN")
                        if idx >= 0:
                            parts_after = stmt[idx + 10:].lstrip().split()
                            col_name = parts_after[0] if parts_after else ""
                            if col_name in existing_columns:
                                continue
                    try:
                        cursor.execute(stmt)
                        log.info("[HISTORY_DB] Applied migration: %s...", stmt[:60])
                    except Exception as e:
                        log.warning("[HISTORY_DB] Migration statement failed: %s", e)
                log.info("[HISTORY_DB] Migrated schema to version %d", version)

        cursor.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("version", str(_CURRENT_SCHEMA_VERSION)),
        )
        conn.commit()

        # Create indexes AFTER migration so 'favorite' column exists
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON transcriptions(timestamp DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_favorite
            ON transcriptions(favorite)
        """)
        log.info("[HISTORY] History database initialized: %s (schema v%d)", self.db_path, _CURRENT_SCHEMA_VERSION)

    def close(self):
        """Close the thread-local connection."""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    def add_transcription(
        self,
        text: str,
        duration: float = 0,
        model: str = "",
        device: str = "",
        language: str = "",
    ) -> int:
        """Add a transcription to the database."""
        try:
            word_count = len(text.split())
            char_count = len(text)

            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO transcriptions
                (text, duration, model, device, word_count, char_count, language)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (text, duration, model, device, word_count, char_count, language))
            conn.commit()
            row_id = cursor.lastrowid
            log.debug("Added transcription %d: %d chars", row_id, char_count)
            return row_id
        except Exception as e:
            log.error("[HISTORY] Failed to add transcription: %s", e)
            return -1

    def get_recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get recent transcriptions with offset-based pagination."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM transcriptions
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error("[HISTORY] Failed to get recent transcriptions: %s", e)
            return []

    def search(self, query: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Search transcriptions by text with offset-based pagination."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            pattern = _prepare_like_search_pattern(query)
            cursor.execute("""
                SELECT * FROM transcriptions
                WHERE text LIKE ? ESCAPE '\\'
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, (pattern, limit, offset))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error("[HISTORY] Failed to search transcriptions: %s", e)
            return []

    def delete(self, transcription_id: int) -> bool:
        """Delete a transcription by ID."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM transcriptions WHERE id = ?",
                (transcription_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            log.error("[HISTORY] Failed to delete transcription: %s", e)
            return False

    def clear_all(self) -> bool:
        """Clear all transcriptions."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM transcriptions")
            conn.commit()
            log.info("[HISTORY] Cleared all transcriptions")
            return True
        except Exception as e:
            log.error("[HISTORY] Failed to clear transcriptions: %s", e)
            return False

    def toggle_favorite(self, transcription_id: int) -> bool:
        """Toggle the favorite status of a transcription."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE transcriptions SET favorite = CASE WHEN favorite = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (transcription_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            log.error("[HISTORY] Failed to toggle favorite: %s", e)
            return False

    def get_favorites(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get favorited transcriptions with offset-based pagination."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM transcriptions
                WHERE favorite = 1
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            log.error("[HISTORY] Failed to get favorites: %s", e)
            return []

    def apply_retention(self, retention_days: int = 0, max_entries: int = 0, retention_count: int = 0) -> int:
        """Apply retention policy: delete old entries.

        Returns the number of deleted entries.

        DEAD-012: retention_count is wired as a fallback for max_entries.
        If max_entries is not set but retention_count is, use it.
        """
        # DEAD-012: wire retention_count as fallback for max_entries
        effective_max = max_entries or retention_count

        deleted = 0
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            if retention_days > 0:
                cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
                cursor.execute(
                    "DELETE FROM transcriptions WHERE timestamp < ? AND favorite = 0",
                    (cutoff,)
                )
                deleted += cursor.rowcount

            if effective_max > 0:
                # Keep favorites + the most recent non-favorite entries
                cursor.execute("SELECT COUNT(*) FROM transcriptions")
                total = cursor.fetchone()[0]
                if total > effective_max:
                    excess = total - effective_max
                    cursor.execute("""
                        DELETE FROM transcriptions
                        WHERE id IN (
                            SELECT id FROM transcriptions
                            WHERE favorite = 0
                            ORDER BY timestamp ASC
                            LIMIT ?
                        )
                    """, (excess,))
                    deleted += cursor.rowcount

            if deleted:
                conn.commit()
                log.info("[HISTORY_DB] Retention policy deleted %d entries", deleted)
        except Exception as e:
            log.error("[HISTORY] Failed to apply retention: %s", e)
        return deleted

    def get_stats(self) -> dict:
        """Get statistics about transcriptions."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_count,
                    SUM(char_count) as total_chars,
                    SUM(word_count) as total_words,
                    SUM(duration) as total_duration,
                    AVG(char_count) as avg_chars
                FROM transcriptions
            """)
            row = cursor.fetchone()
            return {
                "total_count": row[0] or 0,
                "total_chars": row[1] or 0,
                "total_words": row[2] or 0,
                "total_duration": row[3] or 0,
                "avg_chars": row[4] or 0,
            }
        except Exception as e:
            log.error("[HISTORY] Failed to get stats: %s", e)
            return {}

    def get_today_stats(self) -> dict:
        """Get statistics for today's transcriptions."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as count,
                    SUM(char_count) as chars,
                    SUM(word_count) as word_count,
                    SUM(duration) as duration
                FROM transcriptions
                WHERE DATE(timestamp) = DATE('now')
            """)
            row = cursor.fetchone()
            return {
                "count": row[0] or 0,
                "chars": row[1] or 0,
                "word_count": row[2] or 0,
                "duration": row[3] or 0,
            }
        except Exception as e:
            log.error("[HISTORY] Failed to get today stats: %s", e)
            return {"count": 0, "chars": 0, "word_count": 0, "duration": 0}
