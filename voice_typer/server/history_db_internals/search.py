"""History search (FTS router + LIKE fallback)."""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.history_db import HistoryDB

log = logging.getLogger(__name__)

# Shared list projection
_LIST_COLUMNS_SQL = """id,
    SUBSTR(text, 1, ?) AS text,
    LENGTH(text) AS text_full_length,
    text_is_encrypted,
    timestamp,
    duration,
    model,
    device,
    word_count,
    char_count,
    favorite,
    language"""

_LIST_COLUMNS_T_SQL = """t.id,
    SUBSTR(t.text, 1, ?) AS text,
    LENGTH(t.text) AS text_full_length,
    t.text_is_encrypted,
    t.timestamp,
    t.duration,
    t.model,
    t.device,
    t.word_count,
    t.char_count,
    t.favorite,
    t.language"""


# Search / LIKE / FTS5 helpers


def prepare_like_search_pattern(query: str) -> str:
    """Build a bounded LIKE pattern where user wildcards stay literal."""
    # ``_MAX_SEARCH_QUERY_CHARS`` lives on the history_db module so tests
    from voice_typer.server import history_db as _hd

    capped_query = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    escaped_query = capped_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped_query}%"


def is_fts_compatible_query(query: str) -> bool:
    """True when FTS5 can serve the query; separator-only queries fall back to LIKE."""
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    # ``\W`` matches [^a-zA-Z0-9_] in ASCII mode, but with re.UNICODE
    stripped = re.sub(r"[\W_]+", "", capped, flags=re.UNICODE)
    return bool(stripped)


# Scripts unicode61 cannot substring-match (CJK runs index as one token);
_CJK_WIDE_CODEPOINT_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),
    (0x3000, 0x303F),
    (0x3040, 0x30FF),
    (0x3130, 0x318F),
    (0x31F0, 0x31FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xAC00, 0xD7AF),
    (0xFF00, 0xFFEF),
    (0x20000, 0x2FA1F),
)


def has_cjk_or_wide_chars(query: str) -> bool:
    """True when the query needs the LIKE path for CJK/fullwidth substring semantics."""
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    return any(lo <= codepoint <= hi for codepoint in map(ord, capped) for lo, hi in _CJK_WIDE_CODEPOINT_RANGES)


# Trigram indexes only 3-char substrings; shorter CJK queries stay on LIKE.
_TRIGRAM_MIN_QUERY_CHARS = 3


def _build_trigram_phrase(query: str) -> str:
    """Build the FTS5 MATCH phrase for the trigram CJK path."""
    return '"' + query.replace('"', '""') + '"'


def is_trigram_cjk_query(query: str) -> bool:
    """True when the query should use the trigram CJK index."""
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    return len(capped) >= _TRIGRAM_MIN_QUERY_CHARS and has_cjk_or_wide_chars(capped)


def sanitize_fts_query(query: str) -> str:
    """Escape FTS5 syntax so user input matches as literals."""
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    tokens = capped.split()
    if not tokens:
        # Shouldn't happen (caller checks is_fts_compatible_query), but
        return '""'
    # Wrap each token in double quotes. Escape any embedded double
    quoted = []
    for tok in tokens:
        escaped_tok = tok.replace('"', '""')
        quoted.append(f'"{escaped_tok}"')
    return " ".join(quoted)


def project_text_row(row: sqlite3.Row | tuple) -> dict:
    """Post-process a SQLite row from get_recent/search/get_favorites."""
    from voice_typer.server import history_db as _hd

    d = dict(row)
    full_length = d.get("text_full_length")
    if full_length is None:
        full_length_int = 0
        truncated = False
    else:
        full_length_int = int(full_length)
        truncated = full_length_int > _hd._HISTORY_TEXT_PREVIEW_LENGTH
    d["text_truncated"] = truncated
    d["text_full_length"] = full_length_int
    d.pop("text_is_encrypted", None)
    return d


def _finalize_text_rows(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """Project rows and decrypt flagged-encrypted text in Python."""
    from voice_typer.server import _text_crypto, history_db as _hd

    # Capture the encryption flag from the RAW rows before projection —
    out: list[dict] = []
    flagged_ids: list[int] = []
    for row in rows:
        raw = dict(row)
        if raw.get("text_is_encrypted"):
            flagged_ids.append(int(raw["id"]))
        out.append(project_text_row(row))
    if not flagged_ids:
        # Common case (plaintext mode): zero extra work.
        return out
    dek = _text_crypto.get_dek_cached()
    flagged_set = set(flagged_ids)
    if dek is None:
        _text_crypto.log_key_unavailable_error()
        placeholder = _text_crypto.DECRYPTION_FAILED_PLACEHOLDER
        for d in out:
            if d["id"] in flagged_set:
                d["text"] = placeholder
                d["text_full_length"] = len(placeholder)
                d["text_truncated"] = False
        return out
    # Re-fetch the FULL ciphertext for the flagged rows only (the list
    full_texts: dict[int, str] = {}
    try:
        with contextlib.closing(conn.cursor()) as cursor:
            placeholders = ",".join(["?"] * len(flagged_ids))
            cursor.execute(
                f"SELECT id, text FROM transcriptions WHERE id IN ({placeholders})",
                flagged_ids,
            )
            full_texts = {int(r[0]): r[1] for r in cursor.fetchall()}
    except sqlite3.Error as e:
        log.warning(
            "[HISTORY] re-fetching encrypted row texts for decryption failed: %s",
            e,
        )
    preview_len = _hd._HISTORY_TEXT_PREVIEW_LENGTH
    for d in out:
        if d["id"] in flagged_set:
            plaintext = _text_crypto.decrypt_text(full_texts.get(d["id"], ""), dek)
            d["text"] = plaintext[:preview_len]
            d["text_full_length"] = len(plaintext)
            d["text_truncated"] = len(plaintext) > preview_len
    return out


# Public read methods


def _assert_bounded_offset(offset: int) -> None:
    """Shared deep-OFFSET guard for every list path."""
    assert offset < 1000, (
        f"OFFSET pagination requires offset < 1000 (got {offset}); "
        "use cursor pagination (before_timestamp + before_id) for deeper pages"
    )


def get_recent(
    db: HistoryDB,
    limit: int = 50,
    offset: int = 0,
    *,
    before_timestamp: str | None = None,
    before_id: int | None = None,
) -> list[dict]:
    """Get recent transcriptions with offset-based pagination."""
    from voice_typer.server import history_db as _hd

    limit = min(max(limit, 1), _hd._MAX_LIST_LIMIT)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        use_cursor = before_timestamp is not None and before_id is not None
        if use_cursor:
            cursor.execute(
                f"""
                SELECT
                    {_LIST_COLUMNS_SQL}
                FROM transcriptions
                WHERE timestamp < ? OR (timestamp = ? AND id < ?)
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
            """,
                (
                    _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                    before_timestamp,
                    before_timestamp,
                    before_id,
                    limit,
                ),
            )
        else:
            _assert_bounded_offset(offset)
            cursor.execute(
                f"""
                SELECT
                    {_LIST_COLUMNS_SQL}
                FROM transcriptions
                ORDER BY timestamp DESC, id DESC
                LIMIT ? OFFSET ?
            """,
                (_hd._HISTORY_TEXT_PREVIEW_LENGTH, limit, offset),
            )
        rows = cursor.fetchall()
    return _finalize_text_rows(conn, rows)


def get_latest_text(db: HistoryDB) -> str:
    """Return the most recent transcription text, or ``""`` if DB empty."""
    try:
        conn = db._get_read_conn()
        with contextlib.closing(conn.cursor()) as cur:
            cur.execute("SELECT text, text_is_encrypted FROM transcriptions ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        if row is None:
            return ""
        if row[1]:
            return _decrypt_full_text(row[0] or "")
        return row[0] if row else ""
    except Exception as e:
        log.exception("[HISTORY] Failed to get latest transcription: %s", e)
        return ""


def search(
    db: HistoryDB,
    query: str,
    limit: int = 50,
    offset: int = 0,
    *,
    before_timestamp: str | None = None,
    before_id: int | None = None,
) -> list[dict]:
    """Search transcriptions by text with offset-based pagination."""
    from voice_typer.server import history_db as _hd

    limit = min(max(limit, 1), _hd._MAX_LIST_LIMIT)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
        use_cursor = before_timestamp is not None and before_id is not None
        # CJK / fullwidth queries: the unicode61 index cannot substring-
        use_fts = bool(capped) and is_fts_compatible_query(capped) and not has_cjk_or_wide_chars(capped)
        use_trigram_cjk = bool(capped) and not use_fts and is_trigram_cjk_query(capped)
        if use_trigram_cjk:
            # Availability gate: on SQLite builds without the trigram
            from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

            use_trigram_cjk = cjk_trigram_table_exists(conn)
        if use_trigram_cjk:
            trigram_query = _build_trigram_phrase(capped)
            if use_cursor:
                # Cursor path: no LIMIT push-down (the cursor WHERE
                cursor.execute(
                    f"""
                    SELECT
                        {_LIST_COLUMNS_T_SQL}
                    FROM transcriptions t
                    JOIN transcriptions_fts_cjk AS f ON f.rowid = t.id
                    WHERE transcriptions_fts_cjk MATCH ?
                      AND (t.timestamp < ? OR (t.timestamp = ? AND t.id < ?))
                    ORDER BY t.timestamp DESC, t.id DESC
                    LIMIT ?
                """,
                    (
                        _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                        trigram_query,
                        before_timestamp,
                        before_timestamp,
                        before_id,
                        limit,
                    ),
                )
            else:
                # No-cursor path: push LIMIT (+ OFFSET) into the trigram
                _assert_bounded_offset(offset)
                fts_subquery_limit = limit + offset
                cursor.execute(
                    f"""
                    SELECT
                        {_LIST_COLUMNS_T_SQL}
                    FROM (
                        SELECT rowid
                        FROM transcriptions_fts_cjk
                        WHERE transcriptions_fts_cjk MATCH ?
                        ORDER BY rowid DESC
                        LIMIT ?
                    ) AS f
                    JOIN transcriptions t ON t.id = f.rowid
                    ORDER BY t.timestamp DESC, t.id DESC
                    LIMIT ? OFFSET ?
                """,
                    (
                        _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                        trigram_query,
                        fts_subquery_limit,
                        limit,
                        offset,
                    ),
                )
        elif use_fts:
            fts_query = sanitize_fts_query(capped)
            if use_cursor:
                # Cursor path: cannot push LIMIT into FTS because the
                cursor.execute(
                    f"""
                    SELECT
                        {_LIST_COLUMNS_T_SQL}
                    FROM transcriptions t
                    JOIN transcriptions_fts AS f ON f.rowid = t.id
                    WHERE transcriptions_fts MATCH ?
                      AND (t.timestamp < ? OR (t.timestamp = ? AND t.id < ?))
                    ORDER BY t.timestamp DESC, t.id DESC
                    LIMIT ?
                """,
                    (
                        _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                        fts_query,
                        before_timestamp,
                        before_timestamp,
                        before_id,
                        limit,
                    ),
                )
            else:
                # No-cursor path: push LIMIT (+ OFFSET) into the FTS
                _assert_bounded_offset(offset)
                fts_subquery_limit = limit + offset
                cursor.execute(
                    f"""
                    SELECT
                        {_LIST_COLUMNS_T_SQL}
                    FROM (
                        SELECT rowid
                        FROM transcriptions_fts
                        WHERE transcriptions_fts MATCH ?
                        ORDER BY rowid DESC
                        LIMIT ?
                    ) AS f
                    JOIN transcriptions t ON t.id = f.rowid
                    ORDER BY t.timestamp DESC, t.id DESC
                    LIMIT ? OFFSET ?
                """,
                    (
                        _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                        fts_query,
                        fts_subquery_limit,
                        limit,
                        offset,
                    ),
                )
        else:
            # LIKE fallback.
            pattern = prepare_like_search_pattern(query)
            if use_cursor:
                cursor.execute(
                    f"""
                    SELECT
                        {_LIST_COLUMNS_SQL}
                    FROM transcriptions
                    WHERE text LIKE ? ESCAPE '\\'
                      AND (timestamp < ? OR (timestamp = ? AND id < ?))
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                """,
                    (
                        _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                        pattern,
                        before_timestamp,
                        before_timestamp,
                        before_id,
                        limit,
                    ),
                )
            else:
                # LIKE fallback, OFFSET branch: same bounded-offset
                _assert_bounded_offset(offset)
                cursor.execute(
                    f"""
                    SELECT
                        {_LIST_COLUMNS_SQL}
                    FROM transcriptions
                    WHERE text LIKE ? ESCAPE '\\'
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ? OFFSET ?
                """,
                    (_hd._HISTORY_TEXT_PREVIEW_LENGTH, pattern, limit, offset),
                )
        rows = cursor.fetchall()
    return _finalize_text_rows(conn, rows)


def get_favorites(
    db: HistoryDB,
    limit: int = 50,
    offset: int = 0,
    *,
    before_timestamp: str | None = None,
    before_id: int | None = None,
) -> list[dict]:
    """Get favorited transcriptions with offset-based pagination."""
    from voice_typer.server import history_db as _hd

    limit = min(max(limit, 1), _hd._MAX_LIST_LIMIT)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        use_cursor = before_timestamp is not None and before_id is not None
        if use_cursor:
            cursor.execute(
                f"""
                SELECT
                    {_LIST_COLUMNS_SQL}
                FROM transcriptions
                WHERE favorite = 1
                  AND (timestamp < ? OR (timestamp = ? AND id < ?))
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
            """,
                (
                    _hd._HISTORY_TEXT_PREVIEW_LENGTH,
                    before_timestamp,
                    before_timestamp,
                    before_id,
                    limit,
                ),
            )
        else:
            # Same bounded-offset contract as ``get_recent`` / ``search``:
            _assert_bounded_offset(offset)
            cursor.execute(
                f"""
                SELECT
                    {_LIST_COLUMNS_SQL}
                FROM transcriptions
                WHERE favorite = 1
                ORDER BY timestamp DESC, id DESC
                LIMIT ? OFFSET ?
            """,
                (_hd._HISTORY_TEXT_PREVIEW_LENGTH, limit, offset),
            )
        rows = cursor.fetchall()
    return _finalize_text_rows(conn, rows)


def get_today_stats(db: HistoryDB) -> dict:
    """Get statistics for today's transcriptions."""
    from voice_typer.server import history_db as _hd

    # check the cache first.
    now = time.monotonic()
    with db._today_stats_cache_lock:
        if db._today_stats_cache is not None and (now - db._today_stats_cache_ts) < _hd._TODAY_STATS_CACHE_TTL_S:
            # Return a shallow copy so callers can mutate the returned
            return dict(db._today_stats_cache)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        # Sargable predicate. ``DATE(timestamp) = DATE('now')`` applies
        cursor.execute("""
            SELECT
                COUNT(*) as count,
                SUM(char_count) as chars,
                SUM(word_count) as word_count,
                SUM(duration) as duration
            FROM transcriptions
            WHERE timestamp >= DATETIME('now', 'localtime', 'start of day', 'utc')
              AND timestamp < DATETIME('now', 'localtime', 'start of day', '+1 day', 'utc')
        """)
        row = cursor.fetchone()
    result = {
        "count": row[0] or 0,
        "chars": row[1] or 0,
        "word_count": row[2] or 0,
        "duration": row[3] or 0,
    }
    # store the result in the cache (under the lock so a concurrent
    with db._today_stats_cache_lock:
        db._today_stats_cache = result
        db._today_stats_cache_ts = time.monotonic()
    # Return a shallow copy on the cache-miss path too.
    return dict(result)


def invalidate_today_stats_cache(db: HistoryDB) -> None:
    """Drop the cached today-stats dict."""
    with db._today_stats_cache_lock:
        db._today_stats_cache = None
        db._today_stats_cache_ts = 0.0


def _decrypt_full_text(blob: str) -> str:
    """Decrypt a single flagged row's full text (never raises)."""
    from voice_typer.server import _text_crypto

    dek = _text_crypto.get_dek_cached()
    if dek is None:
        _text_crypto.log_key_unavailable_error()
        return _text_crypto.DECRYPTION_FAILED_PLACEHOLDER
    return _text_crypto.decrypt_text(blob, dek)


def get_transcription_text(
    db: HistoryDB,
    transcription_id: int,
    *,
    raise_on_error: bool = False,
) -> dict:
    """Return the FULL ``text`` of a single transcription row."""
    try:
        conn = db._get_read_conn()
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute(
                "SELECT text, text_is_encrypted FROM transcriptions WHERE id = ?",
                (transcription_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return {"id": transcription_id, "text": ""}
        text = row[0] or ""
        if row[1]:
            text = _decrypt_full_text(text)
        return {"id": transcription_id, "text": text}
    except Exception as e:
        log.exception(
            "[HISTORY] Failed to get transcription text for id=%s: %s",
            transcription_id,
            e,
        )
        if raise_on_error:
            from voice_typer.server.history_db import HistoryDBError

            raise HistoryDBError(str(e)) from e
        return {"id": transcription_id, "text": ""}


def get_history_count(
    db: HistoryDB,
    *,
    raise_on_error: bool = False,
) -> int:
    """Return the total number of transcription rows."""
    from voice_typer.server import history_db as _hd

    now = time.monotonic()
    with db._history_count_cache_lock:
        if db._history_count_cache is not None and (now - db._history_count_cache_ts) < _hd._HISTORY_COUNT_CACHE_TTL_S:
            return db._history_count_cache
    try:
        conn = db._get_read_conn()
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute("SELECT COUNT(*) FROM transcriptions")
            row = cursor.fetchone()
        count = int(row[0]) if row is not None else 0
        with db._history_count_cache_lock:
            db._history_count_cache = count
            db._history_count_cache_ts = time.monotonic()
        return count
    except Exception as e:
        log.exception("[HISTORY] Failed to get history count: %s", e)
        if raise_on_error:
            from voice_typer.server.history_db import HistoryDBError

            raise HistoryDBError(str(e)) from e
        return 0


def invalidate_history_count_cache(db: HistoryDB) -> None:
    """Drop the cached total-count int."""
    with db._history_count_cache_lock:
        db._history_count_cache = None
        db._history_count_cache_ts = 0.0
