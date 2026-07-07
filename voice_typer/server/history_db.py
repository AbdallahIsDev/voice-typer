"""SQLite database for storing transcription history.

Rewritten with:
- WAL mode for concurrent read/write
- Connection pooling via thread-local connections
- Thread-safe operations
- Schema migration support
- Favorites support
- Retention policy (auto-delete old entries)

ERR-013: Sentinel contract. Every public method returns a fixed sentinel
on error, matching the *success-shape* of the method's normal return:

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

import sqlite3
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

log = logging.getLogger(__name__)

_CURRENT_SCHEMA_VERSION = 2
_MAX_SEARCH_QUERY_CHARS = 200


class HistoryDBError(RuntimeError):
    """Raised by HistoryDB methods on unrecoverable failures.

    ERR-013: previously every method returned a different sentinel
    (``[]``, ``None``, ``False``, ``-1``, ``{}``) which forced callers
    to know each method's specific sentinel. Methods now log the
    underlying error and return the documented sentinel; callers that
    need to distinguish "empty result" from "operation failed" can
    catch this exception via the ``raise_on_error`` parameter.
    """

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
        # Track ALL connections across threads so close() + __del__
        # can clean them up, preventing ResourceWarning on GC.
        self._all_connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
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
            if not is_windows():
                try:
                    self.db_path.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(self.db_path.parent, 0o700)
                except OSError:
                    pass
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            # DB-LOCK-FIX-001 (Round 1): lowered from 5000ms to 1000ms.
            # The previous 5000ms value matched the user-reported ~5.5s
            # stall exactly — a held write lock would block the caller
            # for the full 5s before raising SQLITE_BUSY (which was then
            # swallowed and logged as "database is locked"). With the
            # retry helper (_exec_with_retry) and chunked apply_retention
            # (DB-LOCK-FIX-002), 1000ms is enough for transient
            # contention while failing fast enough to retry.
            conn.execute("PRAGMA busy_timeout=1000")
            conn.execute("PRAGMA cache_size=-20000")  # 20 MB
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._connections_lock:
                self._all_connections.append(conn)
            # SEC-007: chmod the DB file (and sidecar files if present).
            if not is_windows():
                for suffix in ("", "-wal", "-shm"):
                    p = self.db_path.with_suffix(self.db_path.suffix + suffix) if suffix else self.db_path
                    try:
                        if p.exists():
                            os.chmod(p, 0o600)
                    except OSError:
                        pass
        return self._local.conn

    def _exec_with_retry(
        self,
        fn: Any,
        *,
        max_attempts: int = 5,
        base_delay: float = 0.05,
    ) -> Any:
        """Execute a DB write function with retry on SQLITE_BUSY/SQLITE_LOCKED.

        DB-LOCK-FIX-001 (Round 1): previously, any SQLITE_BUSY (after the
        full ``busy_timeout`` wait) was caught by the caller's broad
        ``except Exception`` and logged as a hard failure — losing the
        row from history and stalling the user for 5+ seconds. This
        helper wraps the write in a retry loop with exponential backoff
        (50, 100, 200, 400 ms) so transient contention from concurrent
        writers (apply_retention sweep, IPC delete/toggle, external
        Defender scan) recovers gracefully instead of failing.

        Total worst-case wait per call: ``busy_timeout`` (1000ms) +
        50+100+200+400 ms = ~1.75s, vs the previous 5s + hard failure.
        On the common case (no contention) there is zero overhead — the
        first attempt succeeds and no retry occurs.
        """
        last_err: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise  # Not a lock error — re-raise immediately
                last_err = e
                if attempt == max_attempts - 1:
                    raise  # Final attempt failed — re-raise
                # Exponential backoff: 50, 100, 200, 400 ms
                time.sleep(base_delay * (2 ** attempt))
        # Should be unreachable, but satisfy type checker
        if last_err:
            raise last_err

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

    def __del__(self):
        """Ensure all connections are closed on GC to prevent ResourceWarning."""
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        """Close ALL tracked connections across all threads."""
        # Close the current thread's connection first (if any).
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None
        # Then close all other connections tracked across threads.
        with self._connections_lock:
            for conn in self._all_connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_connections.clear()

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

            def _do_insert() -> int:
                conn = self._get_conn()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO transcriptions
                    (text, duration, model, device, word_count, char_count, language)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (text, duration, model, device, word_count, char_count, language))
                conn.commit()
                row_id = cursor.lastrowid
                if row_id is None:
                    return -1
                log.debug("Added transcription %d: %d chars", row_id, char_count)
                return row_id

            # DB-LOCK-FIX-001: retry on SQLITE_BUSY/LOCKED with backoff
            row_id = self._exec_with_retry(_do_insert)
            assert row_id is not None
            return row_id
        except Exception as e:
            log.error("[HISTORY] Failed to add transcription: %s", e)
            return -1

    def get_recent(
        self, limit: int = 50, offset: int = 0, *, raise_on_error: bool = False,
    ) -> list[dict]:
        """Get recent transcriptions with offset-based pagination.

        ERR-013: when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``[]``. This lets the
        IPC layer distinguish "empty result" from "operation failed"
        and surface a proper error to the renderer.
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return []

    def search(
        self, query: str, limit: int = 50, offset: int = 0, *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Search transcriptions by text with offset-based pagination.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return []

    def delete(self, transcription_id: int, *, raise_on_error: bool = False) -> bool:
        """Delete a transcription by ID.

        ERR-013: when ``raise_on_error=True``, failures raise
        ``HistoryDBError`` instead of returning ``False``. Without this,
        the IPC layer cannot tell "row didn't exist" from "DB error".
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return False

    def restore(
        self,
        record: dict,
        *,
        raise_on_error: bool = False,
    ) -> int:
        """Re-insert a previously-deleted transcription record.

        NEW-UX-004: supports the Undo-delete toast in the renderer.
        ``record`` should be the dict shape returned by ``get_recent``
        (id is ignored — a new row with a new id is inserted).

        Returns the new row id, or -1 on failure.
        """
        try:
            text = str(record.get("text", ""))
            duration = float(record.get("duration", 0) or 0)
            model = str(record.get("model", "") or "")
            device = str(record.get("device", "") or "")
            language = str(record.get("language", "") or "")
            word_count = int(record.get("word_count", 0) or len(text.split()))
            char_count = int(record.get("char_count", 0) or len(text))
            favorite = 1 if record.get("favorite") else 0

            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO transcriptions
                (text, duration, model, device, word_count, char_count, language, favorite)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (text, duration, model, device, word_count, char_count, language, favorite))
            conn.commit()
            new_id = cursor.lastrowid
            if new_id is None:
                return -1
            log.info("[HISTORY] Restored transcription as id=%d (%d chars)", new_id, char_count)
            assert new_id is not None
            return new_id
        except Exception as e:
            log.error("[HISTORY] Failed to restore transcription: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return -1

    def clear_all(self, *, raise_on_error: bool = False) -> bool:
        """Clear all transcriptions.

        ERR-013: see ``delete`` for ``raise_on_error`` semantics.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM transcriptions")
            conn.commit()
            log.info("[HISTORY] Cleared all transcriptions")
            return True
        except Exception as e:
            log.error("[HISTORY] Failed to clear transcriptions: %s", e)
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return False

    def toggle_favorite(
        self, transcription_id: int, *, raise_on_error: bool = False,
    ) -> bool:
        """Toggle the favorite status of a transcription.

        ERR-013: see ``delete`` for ``raise_on_error`` semantics.
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return False

    def get_favorites(
        self, limit: int = 50, offset: int = 0, *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """Get favorited transcriptions with offset-based pagination.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return []

    def apply_retention(self, retention_days: int = 0, max_entries: int = 0, retention_count: int = 0) -> int:
        """Apply retention policy: delete old entries.

        Returns the number of deleted entries.

        DEAD-012: retention_count is wired as a fallback for max_entries.
        If max_entries is not set but retention_count is, use it.

        DB-LOCK-FIX-002 (Round 1): the previous implementation held a
        single write transaction open across DELETE + SELECT COUNT +
        DELETE before committing. On a DB with many old rows, this held
        the write lock for seconds — blocking the Transcription thread's
        ``add_transcription`` call for the full ``busy_timeout`` (5s,
        now 1s) and causing the user-reported "database is locked"
        5.5s stall. The fix: chunk deletes into batches of 100, committing
        after each batch so the write lock is released between batches
        and other writers can interleave.
        """
        # DEAD-012: wire retention_count as fallback for max_entries
        effective_max = max_entries or retention_count
        # DB-LOCK-FIX-002: batch size for chunked deletes.
        _RETENTION_BATCH = 100

        deleted = 0
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            if retention_days > 0:
                cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
                # Chunked: delete in batches of _RETENTION_BATCH, committing
                # after each batch so the write lock is released between
                # batches and other writers (add_transcription, IPC
                # delete/toggle) can interleave.
                while True:
                    cursor.execute(
                        "DELETE FROM transcriptions WHERE id IN ("
                        "  SELECT id FROM transcriptions"
                        "  WHERE timestamp < ? AND favorite = 0"
                        "  LIMIT ?"
                        ")", (cutoff, _RETENTION_BATCH),
                    )
                    batch_deleted = cursor.rowcount
                    if batch_deleted == 0:
                        break
                    deleted += batch_deleted
                    conn.commit()  # release write lock between batches

            if effective_max > 0:
                # Keep favorites + the most recent non-favorite entries.
                # Chunked: delete in batches of _RETENTION_BATCH.
                while True:
                    cursor.execute("SELECT COUNT(*) FROM transcriptions")
                    total = cursor.fetchone()[0]
                    if total <= effective_max:
                        break
                    excess = min(total - effective_max, _RETENTION_BATCH)
                    cursor.execute("""
                        DELETE FROM transcriptions
                        WHERE id IN (
                            SELECT id FROM transcriptions
                            WHERE favorite = 0
                            ORDER BY timestamp ASC
                            LIMIT ?
                        )
                    """, (excess,))
                    batch_deleted = cursor.rowcount
                    if batch_deleted == 0:
                        break
                    deleted += batch_deleted
                    conn.commit()  # release write lock between batches

            if deleted:
                log.info("[HISTORY_DB] Retention policy deleted %d entries", deleted)
        except Exception as e:
            log.error("[HISTORY] Failed to apply retention: %s", e)
            # ERR-013: apply_retention is called from a background
            # retention sweep, not from an IPC handler, so it preserves
            # the legacy "return 0 deleted" sentinel. The retention
            # sweep logs the error and moves on.
        return deleted

    def get_stats(self, *, raise_on_error: bool = False) -> dict:
        """Get statistics about transcriptions.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return {}

    def get_today_stats(self, *, raise_on_error: bool = False) -> dict:
        """Get statistics for today's transcriptions.

        ERR-013: see ``get_recent`` for ``raise_on_error`` semantics.
        """
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
            if raise_on_error:
                raise HistoryDBError(str(e)) from e
            return {"count": 0, "chars": 0, "word_count": 0, "duration": 0}
