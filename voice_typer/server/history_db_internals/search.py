"""Search / read helpers for :class:`HistoryDB`.

Extracted from the once-monolithic ``history_db.py`` (wave 2 split). The
functions in this module are free functions that take the
:class:`~voice_typer.server.history_db.HistoryDB` instance (``db``)
instead of ``self`` — they read the instance's attributes (the
thread-local read connection pool, the today-stats / history-count TTL
caches) via the passed-in reference.

Free functions:

- :func:`get_recent` — paginated recent transcriptions.
- :func:`get_latest_text` — most recent transcription text.
- :func:`search` — FTS5 / LIKE search.
- :func:`get_favorites` — paginated favorited transcriptions.
- :func:`get_today_stats` — today's count/chars/words/duration (cached).
- :func:`invalidate_today_stats_cache` — drop the cached today-stats dict.
- :func:`get_transcription_text` — full text of a single row.
- :func:`get_history_count` — total row count (cached).
- :func:`invalidate_history_count_cache` — drop the cached total-count int.

Module-level helpers (re-exported by
:mod:`voice_typer.server.history_db`):

- :func:`prepare_like_search_pattern` — bounded LIKE pattern.
- :func:`is_fts_compatible_query` — heuristic for FTS5 fallback.
- :func:`sanitize_fts_query` — escape FTS5 special chars.
- :func:`project_text_row` — post-process a SQLite row for list responses.
"""

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


# ──────────────────────────────────────────────────────────────
# Search / LIKE / FTS5 helpers
# ──────────────────────────────────────────────────────────────


def prepare_like_search_pattern(query: str) -> str:
    """Build a bounded LIKE pattern where user wildcards stay literal."""
    # ``_MAX_SEARCH_QUERY_CHARS`` lives on the history_db module so tests
    # can monkeypatch it. Lazy import so monkeypatches are observed.
    from voice_typer.server import history_db as _hd

    capped_query = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    escaped_query = capped_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped_query}%"


def is_fts_compatible_query(query: str) -> bool:
    """Return True if the (capped) query can be served by the FTS5 index.

    FTS5's ``unicode61`` tokenizer treats ``%``, ``_``, and most
    punctuation as separators. A query consisting ONLY of separator
    characters produces zero tokens and either raises a syntax error or
    silently matches nothing. For such queries we fall back to LIKE so
    users can still find rows containing literal ``%`` / ``_``
    characters.
    """
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    # ``\W`` matches [^a-zA-Z0-9_] in ASCII mode, but with re.UNICODE
    # (the default in Py3) it matches any non-word character. We also
    # explicitly strip ``_`` because ``\w`` includes underscore.
    stripped = re.sub(r"[\W_]+", "", capped, flags=re.UNICODE)
    return bool(stripped)


def sanitize_fts_query(query: str) -> str:
    """Escape FTS5 special characters so user input is treated as literals.

    FTS5 MATCH syntax treats ``*``, ``"``, ``(``, ``)``, ``:``, ``^``,
    ``{``, ``}`` and a few others as syntax. A user typing ``foo*``
    expects a substring/literal match, not an FTS5 prefix query. We wrap
    each whitespace-separated token in double quotes (FTS5 "phrase"
    syntax) so the token is treated as a literal string.
    """
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    tokens = capped.split()
    if not tokens:
        # Shouldn't happen (caller checks is_fts_compatible_query), but
        # guard anyway: an empty MATCH is a syntax error.
        return '""'
    # Wrap each token in double quotes. Escape any embedded double
    # quotes by doubling them (SQL string-literal style).
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
    return d


# ──────────────────────────────────────────────────────────────
# Public read methods
# ──────────────────────────────────────────────────────────────


def get_recent(
    db: HistoryDB,
    limit: int = 50,
    offset: int = 0,
    *,
    before_timestamp: str | None = None,
    before_id: int | None = None,
) -> list[dict]:
    """Get recent transcriptions with offset-based pagination.

    Keyset pagination: when ``before_timestamp`` AND ``before_id`` are
    both supplied, the WHERE clause restricts to rows strictly older
    than ``(before_timestamp, before_id)`` in (timestamp DESC, id DESC)
    order. This is O(log N) per page via ``idx_timestamp``, whereas
    OFFSET is O(offset).
    """
    from voice_typer.server import history_db as _hd

    limit = min(max(limit, 1), _hd._MAX_LIST_LIMIT)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        use_cursor = before_timestamp is not None and before_id is not None
        if use_cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    SUBSTR(text, 1, ?) AS text,
                    LENGTH(text) AS text_full_length,
                    timestamp,
                    duration,
                    model,
                    device,
                    word_count,
                    char_count,
                    favorite,
                    language
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
            cursor.execute(
                """
                SELECT
                    id,
                    SUBSTR(text, 1, ?) AS text,
                    LENGTH(text) AS text_full_length,
                    timestamp,
                    duration,
                    model,
                    device,
                    word_count,
                    char_count,
                    favorite,
                    language
                FROM transcriptions
                ORDER BY timestamp DESC, id DESC
                LIMIT ? OFFSET ?
            """,
                (_hd._HISTORY_TEXT_PREVIEW_LENGTH, limit, offset),
            )
        rows = cursor.fetchall()
    return [project_text_row(row) for row in rows]


def get_latest_text(db: HistoryDB) -> str:
    """Return the most recent transcription text, or ``""`` if DB empty.

    Order by the autoincrement PK (DESC), not ``timestamp DESC``:
    ``timestamp`` defaults to ``CURRENT_TIMESTAMP``, so transcriptions
    written within the same second tie and the "latest" becomes
    ambiguous. The PK is monotonic.
    """
    try:
        conn = db._get_read_conn()
        with contextlib.closing(conn.cursor()) as cur:
            cur.execute("SELECT text FROM transcriptions ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        return row[0] if row else ""
    except Exception as e:
        log.error("[HISTORY] Failed to get latest transcription: %s", e)
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
    """Search transcriptions by text with offset-based pagination.

    FTS5 is used for any query that yields at least one tokenizable
    character (``is_fts_compatible_query``). For empty queries and
    queries consisting solely of separator characters (e.g. ``%`` or
    ``_``), we fall back to the pre-FTS5 LIKE path so literal wildcards
    still match.
    """
    from voice_typer.server import history_db as _hd

    limit = min(max(limit, 1), _hd._MAX_LIST_LIMIT)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
        use_cursor = before_timestamp is not None and before_id is not None
        if capped and is_fts_compatible_query(capped):
            fts_query = sanitize_fts_query(capped)
            if use_cursor:
                cursor.execute(
                    """
                    SELECT
                        t.id,
                        SUBSTR(t.text, 1, ?) AS text,
                        LENGTH(t.text) AS text_full_length,
                        t.timestamp,
                        t.duration,
                        t.model,
                        t.device,
                        t.word_count,
                        t.char_count,
                        t.favorite,
                        t.language
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
                cursor.execute(
                    """
                    SELECT
                        t.id,
                        SUBSTR(t.text, 1, ?) AS text,
                        LENGTH(t.text) AS text_full_length,
                        t.timestamp,
                        t.duration,
                        t.model,
                        t.device,
                        t.word_count,
                        t.char_count,
                        t.favorite,
                        t.language
                    FROM transcriptions t
                    JOIN transcriptions_fts AS f ON f.rowid = t.id
                    WHERE transcriptions_fts MATCH ?
                    ORDER BY t.timestamp DESC, t.id DESC
                    LIMIT ? OFFSET ?
                """,
                    (_hd._HISTORY_TEXT_PREVIEW_LENGTH, fts_query, limit, offset),
                )
        else:
            # LIKE fallback.
            pattern = prepare_like_search_pattern(query)
            if use_cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        SUBSTR(text, 1, ?) AS text,
                        LENGTH(text) AS text_full_length,
                        timestamp,
                        duration,
                        model,
                        device,
                        word_count,
                        char_count,
                        favorite,
                        language
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
                cursor.execute(
                    """
                    SELECT
                        id,
                        SUBSTR(text, 1, ?) AS text,
                        LENGTH(text) AS text_full_length,
                        timestamp,
                        duration,
                        model,
                        device,
                        word_count,
                        char_count,
                        favorite,
                        language
                    FROM transcriptions
                    WHERE text LIKE ? ESCAPE '\\'
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ? OFFSET ?
                """,
                    (_hd._HISTORY_TEXT_PREVIEW_LENGTH, pattern, limit, offset),
                )
        rows = cursor.fetchall()
    return [project_text_row(row) for row in rows]


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
                """
                SELECT
                    id,
                    SUBSTR(text, 1, ?) AS text,
                    LENGTH(text) AS text_full_length,
                    timestamp,
                    duration,
                    model,
                    device,
                    word_count,
                    char_count,
                    favorite,
                    language
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
            cursor.execute(
                """
                SELECT
                    id,
                    SUBSTR(text, 1, ?) AS text,
                    LENGTH(text) AS text_full_length,
                    timestamp,
                    duration,
                    model,
                    device,
                    word_count,
                    char_count,
                    favorite,
                    language
                FROM transcriptions
                WHERE favorite = 1
                ORDER BY timestamp DESC, id DESC
                LIMIT ? OFFSET ?
            """,
                (_hd._HISTORY_TEXT_PREVIEW_LENGTH, limit, offset),
            )
        rows = cursor.fetchall()
    return [project_text_row(row) for row in rows]


def get_today_stats(db: HistoryDB) -> dict:
    """Get statistics for today's transcriptions.

    A 15s TTL cache (``_TODAY_STATS_CACHE_TTL_S``) wraps the aggregating
    scan so the Dashboard's per-``transcription_final`` refresh doesn't
    re-scan on every refresh. The cache is invalidated by EVERY mutation
    that could change today's stats. The returned dict is a shallow copy
    so callers can mutate it without corrupting the cached value.
    """
    from voice_typer.server import history_db as _hd

    # check the cache first.
    now = time.monotonic()
    with db._today_stats_cache_lock:
        if db._today_stats_cache is not None and (now - db._today_stats_cache_ts) < _hd._TODAY_STATS_CACHE_TTL_S:
            # Return a shallow copy so callers can mutate the returned
            # dict without corrupting the cached value.
            return dict(db._today_stats_cache)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        # Sargable predicate. ``DATE(timestamp) = DATE('now')`` applies
        # a function to every row's ``timestamp`` column, so SQLite
        # cannot use ``idx_timestamp``. The range form lets the query
        # planner use the index.
        cursor.execute("""
            SELECT
                COUNT(*) as count,
                SUM(char_count) as chars,
                SUM(word_count) as word_count,
                SUM(duration) as duration
            FROM transcriptions
            WHERE timestamp >= DATE('now')
              AND timestamp < DATE('now', '+1 day')
        """)
        row = cursor.fetchone()
    result = {
        "count": row[0] or 0,
        "chars": row[1] or 0,
        "word_count": row[2] or 0,
        "duration": row[3] or 0,
    }
    # store the result in the cache (under the lock so a concurrent
    # invalidator doesn't race the write).
    with db._today_stats_cache_lock:
        db._today_stats_cache = result
        db._today_stats_cache_ts = time.monotonic()
    # Return a shallow copy on the cache-miss path too.
    return dict(result)


def invalidate_today_stats_cache(db: HistoryDB) -> None:
    """Drop the cached today-stats dict.

    Called by every mutation that could change today's stats
    (``add_transcription``, ``delete``, ``clear_all``, ``restore``,
    ``apply_retention``). Unlike :func:`invalidate_history_count_cache`
    (which skips invalidation on fire-and-forget
    ``add_transcription`` because a stale-by-1 total is fine), the
    today-stats cache is invalidated on EVERY mutation — today's
    stats grow by 1 per dictation and the user wants to see them
    update live.
    """
    with db._today_stats_cache_lock:
        db._today_stats_cache = None
        db._today_stats_cache_ts = 0.0


def get_transcription_text(
    db: HistoryDB,
    transcription_id: int,
    *,
    raise_on_error: bool = False,
) -> dict:
    """Return the FULL ``text`` of a single transcription row.

    Companion to the 500-char ``text`` preview returned by
    ``get_recent`` / ``search`` / ``get_favorites``. Returns
    ``{"id": int, "text": str}`` (empty string if not found).
    """
    try:
        conn = db._get_read_conn()
        with contextlib.closing(conn.cursor()) as cursor:
            cursor.execute(
                "SELECT text FROM transcriptions WHERE id = ?",
                (transcription_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return {"id": transcription_id, "text": ""}
        return {"id": transcription_id, "text": row[0] or ""}
    except Exception as e:
        log.error(
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
    """Return the total number of transcription rows.

    ``SELECT COUNT(*) FROM transcriptions`` is O(N) in SQLite. A 60s TTL
    cache wraps it with immediate invalidation on
    delete/clear_all/restore/apply_retention. Fire-and-forget
    ``add_transcription`` does NOT invalidate — the count grows by 1 per
    dictation, and a 60s-stale-by-N count is fine for a "Total
    Dictations" stat card.
    """
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
        log.error("[HISTORY] Failed to get history count: %s", e)
        if raise_on_error:
            from voice_typer.server.history_db import HistoryDBError

            raise HistoryDBError(str(e)) from e
        return 0


def invalidate_history_count_cache(db: HistoryDB) -> None:
    """Drop the cached total-count int.

    Called by ``delete``, ``clear_all``, ``restore``, and
    ``apply_retention``. Fire-and-forget ``add_transcription`` does NOT
    invalidate — the count grows by 1 per dictation, and a 60s-stale-by-N
    count is fine for a "Total Dictations" stat card.
    """
    with db._history_count_cache_lock:
        db._history_count_cache = None
        db._history_count_cache_ts = 0.0
