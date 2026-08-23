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
- :func:`has_cjk_or_wide_chars` — CJK / fullwidth script detection.
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


# Codepoint ranges whose scripts the FTS5 ``unicode61`` tokenizer cannot
# substring-match. unicode61 has no word-boundary concept for these
# scripts (Chinese/Japanese/Korean text has no spaces), so a contiguous
# CJK run in a transcription is indexed as ONE token. A phrase-wrapped
# MATCH therefore only finds rows where the ENTIRE run equals the query
# — searching "你好" never matches "今天你好吗". Queries containing any
# character from these ranges are routed to the bounded LIKE scan
# instead, which gives true substring semantics for every query length
# (1-char included).
#
# Range set:
#   U+1100–U+11FF   Hangul Jamo (decomposed syllables)
#   U+3000–U+303F   CJK Symbols and Punctuation (、。 「」 …)
#   U+3040–U+30FF   Hiragana + Katakana
#   U+3130–U+318F   Hangul Compatibility Jamo
#   U+31F0–U+31FF   Katakana Phonetic Extensions
#   U+3400–U+4DBF   CJK Unified Ideographs Extension A
#   U+4E00–U+9FFF   CJK Unified Ideographs
#   U+F900–U+FAFF   CJK Compatibility Ideographs
#   U+AC00–U+D7AF   Hangul Syllables
#   U+FF00–U+FFEF   Halfwidth and Fullwidth Forms (！ａｂｃ ｱｲｳ …)
#   U+20000–U+2FA1F CJK Ideograph Extensions B–F + Compat Supplement
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
    """Return True if the (capped) query contains CJK / fullwidth chars.

    Such queries bypass the FTS5 index (see
    :data:`_CJK_WIDE_CODEPOINT_RANGES` for why) and are served by the
    LIKE path, which matches raw substrings regardless of script or
    length. The scan cost is bounded the same way as every other LIKE
    fallback: one pass over the transcriptions table with the
    ``(timestamp DESC, id DESC)`` index serving the ORDER BY and the
    caller's LIMIT bounding the result set.
    """
    from voice_typer.server import history_db as _hd

    capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
    return any(lo <= codepoint <= hi for codepoint in map(ord, capped) for lo, hi in _CJK_WIDE_CODEPOINT_RANGES)


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
    order. This is O(log N) per page via ``idx_timestamp_id``, whereas
    OFFSET is O(offset).

    OFFSET guard: ``offset`` must be < 1000. Deep OFFSET pagination is
    O(offset) on SQLite (it scans and discards ``offset`` rows before
    returning the first result), so callers paginating past the first
    ~1000 rows MUST switch to cursor pagination
    (``before_timestamp`` + ``before_id``). The assert is intentional —
    it surfaces deep-OFFSET callers loudly so they migrate rather than
    silently degrading on large DBs.
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
            # Guard against deep-OFFSET pagination. SQLite must scan and
            # discard ``offset`` rows before returning the first result;
            # at offset=10K on a 500K-row DB this was measured at ~594ms.
            # Callers needing deeper pagination must use cursor
            # pagination (before_timestamp + before_id), which is
            # O(log N) per page via idx_timestamp_id.
            assert offset < 1000, (
                f"OFFSET pagination requires offset < 1000 (got {offset}); "
                "use cursor pagination (before_timestamp + before_id) for deeper pages"
            )
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

    CJK / fullwidth queries (``has_cjk_or_wide_chars``) are ALSO routed
    to the LIKE path: the ``unicode61`` tokenizer indexes a contiguous
    CJK run as a single token, so a phrase-wrapped MATCH only matches
    whole runs — searching "你好" would never find "今天你好吗". The LIKE
    scan gives true substring semantics for every query length; its cost
    is bounded by the same ORDER BY + LIMIT contract as the FTS path
    (the ``(timestamp DESC, id DESC)`` index serves the ordering, and
    the caller's LIMIT/OFFSET bounds the result set). Latin-only search
    behavior is unchanged.

    Mixed-script queries containing at least one CJK/fullwidth character
    take the LIKE path too: the whole capped query becomes one literal
    substring pattern, consistent with the existing separator-only
    fallback semantics.

    FTS5 LIMIT push-down: on the no-cursor path, the ``LIMIT`` (and
    ``OFFSET`` when present) is pushed INTO the FTS5 subquery so FTS5
    only materialises the rowids that will actually be returned, rather
    than the full match set. On a query with many matches this cuts the
    JOIN+sort working set from N_matches to ``limit + offset``. The
    cursor path cannot use push-down because the cursor WHERE clause
    filters by ``(timestamp, id)``, not rowid — pushing LIMIT into FTS
    there could starve the cursor filter and return fewer than
    ``limit`` rows.

    OFFSET guard: ``offset`` must be < 1000 on the OFFSET (non-cursor)
    branch, matching :func:`get_recent`. This bounds the FTS subquery
    LIMIT to ``limit + offset <= 500 + 999 = 1499`` so the push-down
    stays effective.
    """
    from voice_typer.server import history_db as _hd

    limit = min(max(limit, 1), _hd._MAX_LIST_LIMIT)
    conn = db._get_read_conn()
    with contextlib.closing(conn.cursor()) as cursor:
        capped = query[: _hd._MAX_SEARCH_QUERY_CHARS]
        use_cursor = before_timestamp is not None and before_id is not None
        # CJK / fullwidth queries bypass FTS5 (unicode61 cannot
        # substring-match those scripts) and take the LIKE path below.
        use_fts = bool(capped) and is_fts_compatible_query(capped) and not has_cjk_or_wide_chars(capped)
        if use_fts:
            fts_query = sanitize_fts_query(capped)
            if use_cursor:
                # Cursor path: cannot push LIMIT into FTS because the
                # cursor WHERE filters by (timestamp, id), not rowid.
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
                # No-cursor path: push LIMIT (+ OFFSET) into the FTS
                # subquery so FTS5 only materialises the rowids that
                # will actually be returned. The outer ORDER BY
                # re-sorts by (timestamp DESC, id DESC) — rowid DESC is
                # a close approximation since id is autoincrement and
                # timestamp defaults to CURRENT_TIMESTAMP, but not
                # identical for same-second ties, so the outer sort is
                # still required.
                assert offset < 1000, (
                    f"OFFSET pagination requires offset < 1000 (got {offset}); "
                    "use cursor pagination (before_timestamp + before_id) for deeper pages"
                )
                fts_subquery_limit = limit + offset
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

    Timezone handling: ``timestamp`` is stored as UTC
    (``CURRENT_TIMESTAMP``). The boundaries are computed in LOCAL time
    (``DATETIME('now', 'localtime', 'start of day')`` — the user's
    calendar midnight) and then converted back to UTC via the trailing
    ``'utc'`` modifier so the lexicographic comparison against the
    UTC-stored ``timestamp`` column is correct. Without the local→UTC
    round-trip, a user in UTC+8 dictating at 9pm local (1pm UTC) would
    see "today" stats roll over at midnight UTC (4am local) instead of
    local midnight.
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
        # planner use the index. Boundaries are computed in LOCAL time
        # then converted to UTC (see docstring).
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
