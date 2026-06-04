"""SQLite database for storing transcription history."""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class HistoryDB:
    """SQLite database for storing transcription history."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from voice_typer.config import _config_dir
            db_path = _config_dir() / "history.db"
        
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
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
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_timestamp 
                    ON transcriptions(timestamp DESC)
                """)
                conn.commit()
                log.info("History database initialized: %s", self.db_path)
        except Exception as e:
            log.error("Failed to initialize history database: %s", e)
    
    def add_transcription(
        self,
        text: str,
        duration: float = 0,
        model: str = "",
        device: str = "",
    ) -> int:
        """Add a transcription to the database."""
        try:
            word_count = len(text.split())
            char_count = len(text)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO transcriptions 
                    (text, duration, model, device, word_count, char_count)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (text, duration, model, device, word_count, char_count))
                conn.commit()
                row_id = cursor.lastrowid
                log.debug("Added transcription %d: %d chars", row_id, char_count)
                return row_id
        except Exception as e:
            log.error("Failed to add transcription: %s", e)
            return -1
    
    def get_recent(self, limit: int = 50) -> list[dict]:
        """Get recent transcriptions."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM transcriptions 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            log.error("Failed to get recent transcriptions: %s", e)
            return []
    
    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search transcriptions by text."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM transcriptions 
                    WHERE text LIKE ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (f"%{query}%", limit))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            log.error("Failed to search transcriptions: %s", e)
            return []
    
    def delete(self, transcription_id: int) -> bool:
        """Delete a transcription by ID."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM transcriptions WHERE id = ?",
                    (transcription_id,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            log.error("Failed to delete transcription: %s", e)
            return False
    
    def clear_all(self) -> bool:
        """Clear all transcriptions."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM transcriptions")
                conn.commit()
                log.info("Cleared all transcriptions")
                return True
        except Exception as e:
            log.error("Failed to clear transcriptions: %s", e)
            return False
    
    def get_stats(self) -> dict:
        """Get statistics about transcriptions."""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
            log.error("Failed to get stats: %s", e)
            return {}
    
    def get_today_stats(self) -> dict:
        """Get statistics for today's transcriptions."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 
                        COUNT(*) as count,
                        SUM(char_count) as chars
                    FROM transcriptions
                    WHERE DATE(timestamp) = DATE('now')
                """)
                row = cursor.fetchone()
                return {
                    "count": row[0] or 0,
                    "chars": row[1] or 0,
                }
        except Exception as e:
            log.error("Failed to get today stats: %s", e)
            return {"count": 0, "chars": 0}