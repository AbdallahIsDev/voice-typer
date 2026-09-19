"""Tests for the FTS5-wired ``HistoryDB.search`` path."""

import pytest

from tests.fixtures.history_test_helpers import history_plaintext_mode  # noqa: F401


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
        conn = db._get_read_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions_fts'")
        assert cur.fetchone() is not None

    def test_fts5_table_has_rows(self, db):
        """The AFTER INSERT trigger must have populated the FTS5 index."""
        conn = db._get_read_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transcriptions_fts")
        assert cur.fetchone()[0] == 6

    def test_search_finds_single_token(self, db):
        """A single-token FTS5 query must find the matching row."""
        results = db.search("quick")
        assert [r["text"] for r in results] == ["The quick brown fox"]

    def test_search_finds_token_in_multiline_text(self, db):
        """FTS5 tokenizes on whitespace; a query for one token matches any"""
        results = db.search("world")
        assert [r["text"] for r in results] == ["Hello world"]

    def test_search_tokenizes_snake_case_as_three_tokens(self, db):
        """The ``unicode61`` tokenizer treats ``_`` as a separator, so"""
        results = db.search("snake_case_token")
        texts = [r["text"] for r in results]
        assert "snake_case_token" in texts
        assert "snake case token" in texts

    def test_search_numeric_token(self, db):
        """A numeric token like ``100`` is tokenizable and must match."""
        results = db.search("100")
        assert [r["text"] for r in results] == ["Progress is 100% complete"]

    def test_search_multi_token_query_is_phrase_and(self, db):
        """``_sanitize_fts_query`` splits on whitespace and quotes each"""
        results = db.search("quick fox")
        assert [r["text"] for r in results] == ["The quick brown fox"]

    def test_search_no_match_returns_empty_list(self, db):
        """A query that matches no tokens returns an empty list, the"""
        results = db.search("nonexistenttok")
        assert results == []

    def test_search_literal_wildcard_percent_via_like_fallback(self, db):
        """A query consisting solely of ``%`` cannot be served by FTS5"""
        results = db.search("%")
        assert [r["text"] for r in results] == ["Progress is 100% complete"]

    def test_search_literal_wildcard_underscore_via_like_fallback(self, db):
        """A query consisting solely of ``_`` cannot be served by FTS5."""
        results = db.search("_")
        assert [r["text"] for r in results] == ["snake_case_token"]

    def test_search_separator_only_queries_take_like_fallback(self, db, monkeypatch):
        """Every non-empty separator-only query (punctuation, wildcards)"""
        import voice_typer.server.history_db_internals.search as search_mod

        calls = []
        real_prepare = search_mod.prepare_like_search_pattern

        def _spy(query):
            calls.append(query)
            return real_prepare(query)

        monkeypatch.setattr(search_mod, "prepare_like_search_pattern", _spy)
        assert db.search("%")[0]["text"] == "Progress is 100% complete"
        assert db.search("!!!") == []  # no rows contain '!!!', but the path ran
        assert calls == ["%", "!!!"]

    def test_search_empty_query_returns_all_rows(self, db):
        """An empty query falls back to LIKE with pattern ``%%`` which"""
        results = db.search("")
        assert len(results) == 6

    def test_search_preserves_order_by_timestamp_desc(self, db, tmp_path):
        """Results must be ordered by ``timestamp DESC`` (newest first)"""
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
                # Both lists have exactly 3 elements; strict=True guards
                for ts, txt in zip(timestamps, texts, strict=True):
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
    """``_is_fts_compatible_query`` and ``_sanitize_fts_query`` must"""

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
        assert _sanitize_fts_query("hello world") == '"hello" "world"'

    def test_sanitize_fts_query_treats_star_as_literal(self):
        """``foo*`` is wrapped as ``\"foo*\"``, the ``*`` is a literal"""
        from voice_typer.server.history_db import _sanitize_fts_query

        assert _sanitize_fts_query("foo*") == '"foo*"'

    def test_sanitize_fts_query_empty_returns_empty_phrase(self):
        """
        Edge case: an empty tokens list returns ``""`` (an empty
        MATCH syntax error if the contract is violated.
        """
        from voice_typer.server.history_db import _sanitize_fts_query

        assert _sanitize_fts_query("") == '""'


class TestFts5SearchErrorHandling:
    """``search()`` must continue to honor the ERR-013 sentinel contract"""

    def test_search_returns_empty_list_on_read_conn_failure(self, db, monkeypatch):
        """A read-conn failure must return ``[]`` (the list-returning"""

        def _boom():
            raise RuntimeError("disk I/O error")

        monkeypatch.setattr(db, "_get_read_conn", _boom)
        assert db.search("quick") == []

    def test_search_raises_history_db_error_when_raise_on_error(self, db, monkeypatch):
        """A read-conn failure must raise ``HistoryDBError`` when"""
        from voice_typer.server.history_db import HistoryDBError

        def _boom():
            raise RuntimeError("locked")

        monkeypatch.setattr(db, "_get_read_conn", _boom)
        with pytest.raises(HistoryDBError):
            db.search("quick", raise_on_error=True)


class TestFts5SearchTriggersSync:
    """MIGRATION_V3's AFTER INSERT/UPDATE/DELETE triggers must keep the"""

    def test_delete_removes_row_from_fts_index(self, db, tmp_path):
        """dangling JOIN result."""
        from voice_typer.server.history_db import HistoryDB

        db2 = HistoryDB(db_path=tmp_path / "delete_sync.db")
        try:
            db2.add_transcription("findme before delete")
            db2.add_transcription("keepme")
            db2.flush()

            # Find the "findme before delete" row by text (don't rely
            all_rows = db2.get_recent(limit=10)
            findme = next(r for r in all_rows if r["text"] == "findme before delete")

            # Confirm FTS5 sees it before delete
            conn = db2._get_read_conn()
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
        conn = db._get_read_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transcriptions_fts")
        assert cur.fetchone()[0] == 0
        assert db.search("quick") == []
        assert db.search("") == []

    def test_delete_rebuilds_fts5_shadow_tables_xe_9_a(self, tmp_path):
        """``transcriptions_fts_data`` (the FTS5 shadow segment table)."""
        from voice_typer.server.history_db import HistoryDB

        db2 = HistoryDB(db_path=tmp_path / "delete_rebuild_xe_9_a.db")
        try:
            secret_text = "superscrete_password_12345_xe_9_a"
            db2.add_transcription(secret_text)
            db2.add_transcription("keepme around")
            db2.flush()

            # Find the secret row's id.
            all_rows = db2.get_recent(limit=10)
            secret_row = next(r for r in all_rows if r["text"] == secret_text)

            # Verify the FTS5 shadow segment data contains the secret
            conn = db2._get_read_conn()
            cur = conn.cursor()
            cur.execute("SELECT block FROM transcriptions_fts_data")
            blocks_before = b"\n".join(b for (b,) in cur.fetchall())
            assert b"xe" in blocks_before or b"superscrete" in blocks_before, (
                "Sanity check failed: secret_text token not found in any "
                "transcriptions_fts_data block before delete, test setup "
                "is invalid (FTS5 may have segmented differently)."
            )

            # Delete the secret row.
            assert db2.delete(secret_row["id"]) is True

            cur.execute("SELECT block FROM transcriptions_fts_data")
            blocks_after = b"\n".join(b for (b,) in cur.fetchall())
            assert secret_text.encode() not in blocks_after, (
                "XE-9-A regression: secret_text is still present in "
                "transcriptions_fts_data after delete(id), the FTS5 "
                "'rebuild' command was not issued (or failed silently). "
                "The deleted row's dictated text is recoverable via "
                "forensic tools, a GDPR Art. 17 violation."
            )

            # And the FTS5 index itself must no longer return the row.
            assert db2.search("superscrete") == []
        finally:
            db2.close()


if __name__ == "__main__":
    # Allow running this test file directly for quick local iteration.
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
