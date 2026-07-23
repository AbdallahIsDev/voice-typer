"""Tests for the FTS5-wired ``HistoryDB.search`` path.

These tests pin the contract that ``search()`` actually reads the
``transcriptions_fts`` virtual table (maintained by MIGRATION_V3's
AFTER INSERT/UPDATE/DELETE triggers) instead of doing a linear
``WHERE text LIKE ?`` table scan. They also pin the LIKE fallback for
queries that the FTS5 ``unicode61`` tokenizer cannot serve (empty
queries, separator-only queries like ``%`` / ``_``).
"""

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path and a few seeded rows."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "fts5_test.db")
    db_instance.add_transcription("The quick brown fox")
    db_instance.add_transcription("Hello world")
    db_instance.add_transcription("snake_case_token")
    db_instance.add_transcription("snake case token")
    db_instance.add_transcription("Progress is 100% complete")
    db_instance.add_transcription("plain text without percent")
    db_instance.flush()
    yield db_instance
    db_instance.close()


class TestFts5SearchWiring:
    """``search()`` must read the FTS5 index, not do a LIKE table scan."""

    def test_fts5_virtual_table_exists(self, db):
        """MIGRATION_V3 must have created the ``transcriptions_fts`` table."""
        conn = db._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions_fts'")
        assert cur.fetchone() is not None

    def test_fts5_table_has_rows(self, db):
        """The AFTER INSERT trigger must have populated the FTS5 index."""
        conn = db._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transcriptions_fts")
        assert cur.fetchone()[0] == 6

    def test_search_finds_single_token(self, db):
        """A single-token FTS5 query must find the matching row."""
        results = db.search("quick")
        assert [r["text"] for r in results] == ["The quick brown fox"]

    def test_search_finds_token_in_multiline_text(self, db):
        """FTS5 tokenizes on whitespace; a query for one token matches any
        row whose tokenization includes that token."""
        results = db.search("world")
        assert [r["text"] for r in results] == ["Hello world"]

    def test_search_tokenizes_snake_case_as_three_tokens(self, db):
        """The ``unicode61`` tokenizer treats ``_`` as a separator, so
        ``snake_case_token`` tokenizes to ``snake``, ``case``, ``token``.
        A phrase query for the literal ``snake_case_token`` (sanitized
        to ``"snake_case_token"``) must still match because FTS5
        re-tokenizes the phrase and matches the three-token sequence."""
        results = db.search("snake_case_token")
        texts = [r["text"] for r in results]
        # Both "snake_case_token" (3-token sequence) and
        # "snake case token" (also 3-token sequence) match.
        assert "snake_case_token" in texts
        assert "snake case token" in texts

    def test_search_numeric_token(self, db):
        """A numeric token like ``100`` is tokenizable and must match."""
        results = db.search("100")
        assert [r["text"] for r in results] == ["Progress is 100% complete"]

    def test_search_multi_token_query_is_phrase_and(self, db):
        """``_sanitize_fts_query`` splits on whitespace and quotes each
        token, producing an implicit AND. A two-token query must match
        only rows containing BOTH tokens."""
        results = db.search("quick fox")
        assert [r["text"] for r in results] == ["The quick brown fox"]

    def test_search_no_match_returns_empty_list(self, db):
        """A query that matches no tokens returns an empty list — the
        sentinel for list-returning methods (ERR-013)."""
        results = db.search("nonexistenttok")
        assert results == []

    def test_search_literal_wildcard_percent_via_like_fallback(self, db):
        """A query consisting solely of ``%`` cannot be served by FTS5
        (it produces zero tokens). The LIKE fallback must return only
        rows containing a literal ``%`` character."""
        results = db.search("%")
        assert [r["text"] for r in results] == ["Progress is 100% complete"]

    def test_search_literal_wildcard_underscore_via_like_fallback(self, db):
        """A query consisting solely of ``_`` cannot be served by FTS5.
        The LIKE fallback must return only rows containing a literal
        ``_`` character."""
        results = db.search("_")
        assert [r["text"] for r in results] == ["snake_case_token"]

    def test_search_empty_query_returns_all_rows(self, db):
        """An empty query falls back to LIKE with pattern ``%%`` which
        matches every row."""
        results = db.search("")
        assert len(results) == 6

    def test_search_preserves_order_by_timestamp_desc(self, db, tmp_path):
        """Results must be ordered by ``timestamp DESC`` (newest first)
        — the same ordering contract as the pre-FTS5 LIKE path.

        We use explicit timestamps (via a writer closure) rather than
        ``add_transcription`` + ``time.sleep`` because the latter only
        has SECOND resolution: two rows added in the same second get
        the same timestamp and the ORDER BY tie-break is
        implementation-defined."""
        from datetime import datetime, timedelta

        from voice_typer.server.history_db import HistoryDB

        db2 = HistoryDB(db_path=tmp_path / "ordered.db")
        try:
            base = datetime.now()
            timestamps = [
                (base - timedelta(seconds=20)).isoformat(),
                (base - timedelta(seconds=10)).isoformat(),
                (base - timedelta(seconds=0)).isoformat(),
            ]
            texts = ["oldest entry", "middle entry", "newest entry"]

            def _do_insert(conn):
                cur = conn.cursor()
                for ts, txt in zip(timestamps, texts):
                    cur.execute(
                        "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                        (txt, ts),
                    )
                conn.commit()

            db2._submit_write(_do_insert, wait=True)

            results = db2.search("entry")
            assert [r["text"] for r in results] == [
                "newest entry",
                "middle entry",
                "oldest entry",
            ]
        finally:
            db2.close()

    def test_search_respects_limit_and_offset(self, db):
        """Pagination via LIMIT/OFFSET must work on the FTS5 path."""
        results_page1 = db.search("snake", limit=1, offset=0)
        results_page2 = db.search("snake", limit=1, offset=1)
        assert len(results_page1) == 1
        assert len(results_page2) == 1
        assert results_page1[0]["text"] != results_page2[0]["text"]
        # Both rows contain "snake"
        assert "snake" in results_page1[0]["text"]
        assert "snake" in results_page2[0]["text"]


class TestFts5HelpersAreUsed:
    """``_is_fts_compatible_query`` and ``_sanitize_fts_query`` must
    no longer be dead code — they are now wired into ``search()``."""

    def test_is_fts_compatible_query_returns_true_for_word(self):
        from voice_typer.server.history_db import _is_fts_compatible_query

        assert _is_fts_compatible_query("hello") is True

    def test_is_fts_compatible_query_returns_false_for_separator_only(self):
        from voice_typer.server.history_db import _is_fts_compatible_query

        assert _is_fts_compatible_query("%") is False
        assert _is_fts_compatible_query("_") is False
        assert _is_fts_compatible_query("") is False

    def test_sanitize_fts_query_wraps_token_in_quotes(self):
        from voice_typer.server.history_db import _sanitize_fts_query

        # A bare token is wrapped in double quotes (FTS5 phrase syntax).
        assert _sanitize_fts_query("hello") == '"hello"'

    def test_sanitize_fts_query_escapes_embedded_quotes(self):
        from voice_typer.server.history_db import _sanitize_fts_query

        # An embedded double quote is doubled (SQL string-literal style).
        sanitized = _sanitize_fts_query('foo"bar')
        assert sanitized == '"foo""bar"'

    def test_sanitize_fts_query_multi_token_is_phrase_and(self):
        from voice_typer.server.history_db import _sanitize_fts_query

        # Two whitespace-separated tokens become two quoted phrases,
        # which FTS5 treats as an implicit AND.
        assert _sanitize_fts_query("hello world") == '"hello" "world"'

    def test_sanitize_fts_query_treats_star_as_literal(self):
        """``foo*`` is wrapped as ``"foo*"`` — the ``*`` is a literal
        character inside the phrase, NOT an FTS5 prefix query."""
        from voice_typer.server.history_db import _sanitize_fts_query

        assert _sanitize_fts_query("foo*") == '"foo*"'

    def test_sanitize_fts_query_empty_returns_empty_phrase(self):
        """Edge case: an empty tokens list returns ``""`` (an empty
        FTS5 phrase). The caller is responsible for checking
        ``_is_fts_compatible_query`` first, but this guard prevents a
        MATCH syntax error if the contract is violated."""
        from voice_typer.server.history_db import _sanitize_fts_query

        assert _sanitize_fts_query("") == '""'


class TestFts5SearchErrorHandling:
    """``search()`` must continue to honor the ERR-013 sentinel contract
    (return ``[]`` on failure, raise ``HistoryDBError`` when
    ``raise_on_error=True``)."""

    def test_search_returns_empty_list_on_read_conn_failure(self, db, monkeypatch):
        """A read-conn failure must return ``[]`` (the list-returning
        sentinel) when ``raise_on_error=False``."""

        def _boom():
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(db, "_get_read_conn", _boom)
        assert db.search("quick") == []

    def test_search_raises_history_db_error_when_raise_on_error(self, db, monkeypatch):
        """A read-conn failure must raise ``HistoryDBError`` when
        ``raise_on_error=True`` so the IPC layer can distinguish
        "empty result" from "operation failed"."""
        from voice_typer.server.history_db import HistoryDBError

        def _boom():
            raise RuntimeError("locked")

        monkeypatch.setattr(db, "_get_read_conn", _boom)
        with pytest.raises(HistoryDBError):
            db.search("quick", raise_on_error=True)


class TestFts5SearchTriggersSync:
    """MIGRATION_V3's AFTER INSERT/UPDATE/DELETE triggers must keep the
    FTS5 index in sync with the ``transcriptions`` table."""

    def test_delete_removes_row_from_fts_index(self, db, tmp_path):
        """After ``delete(id)``, the FTS5 index must NOT contain the
        deleted row's rowid — otherwise search() would return a
        dangling JOIN result."""
        from voice_typer.server.history_db import HistoryDB

        db2 = HistoryDB(db_path=tmp_path / "delete_sync.db")
        try:
            db2.add_transcription("findme before delete")
            db2.add_transcription("keepme")
            db2.flush()

            # Find the "findme before delete" row by text (don't rely
            # on get_recent ordering — within the same timestamp
            # second, the tie-break is implementation-defined).
            all_rows = db2.get_recent(limit=10)
            findme = next(r for r in all_rows if r["text"] == "findme before delete")

            # Confirm FTS5 sees it before delete
            conn = db2._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM transcriptions_fts WHERE rowid = ?",
                (findme["id"],),
            )
            assert cur.fetchone()[0] == 1

            assert db2.delete(findme["id"]) is True

            # After delete, FTS5 must NOT contain the rowid
            cur.execute(
                "SELECT COUNT(*) FROM transcriptions_fts WHERE rowid = ?",
                (findme["id"],),
            )
            assert cur.fetchone()[0] == 0

            # And search() must NOT return the deleted row
            assert db2.search("findme") == []
        finally:
            db2.close()

    def test_clear_all_empties_fts_index(self, db):
        """After ``clear_all()``, the FTS5 index must be empty."""
        assert db.clear_all() is True
        conn = db._get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transcriptions_fts")
        assert cur.fetchone()[0] == 0
        # search() must return [] for any query
        assert db.search("quick") == []
        assert db.search("") == []


if __name__ == "__main__":
    # Allow running this test file directly for quick local iteration.
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
