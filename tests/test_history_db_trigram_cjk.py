"""Trigram CJK index tests (schema V5).

The unicode61 index cannot substring-match CJK scripts (a contiguous
CJK run is ONE token), so every CJK query historically fell back to a
full-table LIKE scan (the documented CJK history-search finding in
``review.md``). Schema V5 adds a
SECOND FTS5 index (``transcriptions_fts_cjk``, ``tokenize='trigram'``)
consulted ONLY for queries containing CJK/fullwidth characters.

Contracts pinned here:

1. Queries with a CJK/fullwidth char, length >= 3, take the trigram
   MATCH path (indexed — verified by monkeypatching the LIKE fallback
   to raise and asserting search still works).
2. 1-2 char CJK queries keep the LIKE path (the trigram tokenizer only
   indexes 3-char substrings, so a shorter MATCH would SILENTLY match
   nothing — verified against SQLite 3.50).
3. Results are identical to the LIKE path (same rows, same order, same
   pagination, same literal wildcard semantics).
4. Both shadow indexes stay in lockstep at every GDPR rebuild/optimize
   site (delete / clear_all / retention / startup rebuild).
"""

from __future__ import annotations

import pytest

from tests.fixtures.history_test_helpers import history_plaintext_mode  # noqa: F401


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB seeded with multi-script transcription rows."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "trigram_cjk_test.db")
    db_instance.add_transcription("今天你好吗")
    db_instance.add_transcription("你好")
    db_instance.add_transcription("こんにちは世界")
    db_instance.add_transcription("東京タワーへ行く")
    db_instance.add_transcription("안녕하세요 반갑습니다")
    db_instance.add_transcription("Meeting notes with 你好 mixed")
    db_instance.add_transcription("价格是100%折扣")
    db_instance.add_transcription("你好。再见！")
    db_instance.add_transcription("The quick brown fox")
    db_instance.flush()
    yield db_instance
    db_instance.close()


def _fts_cjk_table_exists(db) -> bool:
    import sqlite3

    conn = sqlite3.connect(str(db.db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions_fts_cjk'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


class TestSchemaV5Migration:
    def test_trigram_table_created_on_fresh_db(self, db):
        assert _fts_cjk_table_exists(db), "schema V5 must create the transcriptions_fts_cjk trigram index"

    def test_migration_backfills_existing_rows(self, db):
        """The V5 backfill ('rebuild') must index pre-existing rows — a
        CJK query must find rows inserted before the migration conceptually ran."""
        texts = [r["text"] for r in db.search("你好吗")]
        assert "今天你好吗" in texts

    def test_v4_db_migrates_to_v5_and_keeps_data(self, tmp_path):
        """A V4 database (pre-trigram) migrates to V5 on open and its
        rows become searchable via the new index."""
        import sqlite3

        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "v4_legacy.db"
        db = HistoryDB(db_path=db_path)
        db.add_transcription(" migration probe 你好吗 row")
        db.flush()
        # Force the version back to 4 to simulate a DB created before V5.
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("DROP TABLE transcriptions_fts_cjk")
            conn.execute("DROP TRIGGER IF EXISTS transcriptions_ai_fts_cjk")
            conn.execute("DROP TRIGGER IF EXISTS transcriptions_ad_fts_cjk")
            conn.execute("DROP TRIGGER IF EXISTS transcriptions_au_fts_cjk")
            conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', '4')")
            conn.commit()
        finally:
            conn.close()
        db.close()

        db2 = HistoryDB(db_path=db_path)
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                version = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()[0]
            finally:
                conn.close()
            assert version == "5"
            texts = [r["text"] for r in db2.search("你好吗")]
            assert any("migration probe" in t for t in texts)
        finally:
            db2.close()


class TestTrigramRouter:
    def test_is_trigram_cjk_query_long_cjk(self):
        from voice_typer.server.history_db_internals.search import is_trigram_cjk_query

        assert is_trigram_cjk_query("你好吗") is True
        assert is_trigram_cjk_query("タワーへ") is True
        assert is_trigram_cjk_query("with 你好 mixed") is True

    def test_short_cjk_queries_stay_on_like(self):
        from voice_typer.server.history_db_internals.search import is_trigram_cjk_query

        assert is_trigram_cjk_query("你好") is False  # 2 chars
        assert is_trigram_cjk_query("好") is False  # 1 char
        assert is_trigram_cjk_query("再见") is False  # 2 chars
        assert is_trigram_cjk_query("好！") is False  # 2 chars

    def test_latin_queries_never_take_trigram_path(self):
        from voice_typer.server.history_db_internals.search import is_trigram_cjk_query

        assert is_trigram_cjk_query("quick brown") is False
        assert is_trigram_cjk_query("%") is False

    def test_long_cjk_query_does_not_touch_like_path(self, db, monkeypatch):
        """The indexed-path proof: the LIKE fallback must never run for a
        trigram-eligible query."""
        import voice_typer.server.history_db_internals.search as search_mod

        def _boom(query):
            raise AssertionError("LIKE fallback ran for a trigram-eligible CJK query")

        monkeypatch.setattr(search_mod, "prepare_like_search_pattern", _boom)
        texts = [r["text"] for r in db.search("你好吗")]
        assert "今天你好吗" in texts


class TestTrigramResultsMatchLikeContract:
    def test_three_char_substring(self, db):
        texts = [r["text"] for r in db.search("你好吗")]
        assert texts == ["今天你好吗"]

    def test_kana_and_kanji(self, db):
        assert [r["text"] for r in db.search("タワーへ行く")] == ["東京タワーへ行く"]
        assert [r["text"] for r in db.search("東京タワー")] == ["東京タワーへ行く"]

    def test_hangul(self, db):
        assert [r["text"] for r in db.search("하세요 반갑")] == ["안녕하세요 반갑습니다"]

    def test_wildcards_are_literal_characters(self, db):
        assert [r["text"] for r in db.search("100%折扣")] == ["价格是100%折扣"]
        assert db.search("你好%吗") == []

    def test_trigram_and_like_agree_on_results(self, db):
        """For every trigram-path query, results must equal the true
        substring semantic: every stored row containing the query as a
        literal substring, newest first (the contract the LIKE path
        delivered historically)."""
        queries = ["你好吗", "タワー", "하세요 반갑습니다", "100%折扣", "with 你好 mixed"]
        all_rows = db.get_recent(limit=100)
        for q in queries:
            expected = [r["text"] for r in all_rows if q in r["text"]]
            via_trigram = [r["text"] for r in db.search(q)]
            assert via_trigram == expected, f"trigram disagrees with substring semantics for {q!r}"

    def test_ordering_and_offset_pagination(self, db):
        page1 = db.search("你好", limit=1, offset=0)
        # 2-char query → LIKE path; verify pagination works there...
        assert len(page1) == 1
        # ...and the trigram path honors the same ORDER BY contract.
        page_t1 = db.search("你好吗", limit=1, offset=0)
        assert len(page_t1) == 1
        assert page_t1[0]["text"] == "今天你好吗"

    def test_cursor_pagination_on_trigram_query(self, db):
        recent = db.get_recent(limit=10)
        anchor = recent[0]
        results = db.search(
            "你好吗",
            before_timestamp=anchor["timestamp"],
            before_id=anchor["id"],
        )
        assert all(r["id"] < anchor["id"] or r["timestamp"] < anchor["timestamp"] for r in results)

    def test_no_match_returns_empty(self, db):
        assert db.search("再見了断") == []  # traditional chars not in corpus


class TestEncryptionInterplay:
    def test_encrypted_rows_still_searchable_via_trigram(self, tmp_path, monkeypatch):
        """ADR §6: FTS shadow tables stay plaintext-tokenized. A row
        encrypted at rest must remain searchable on BOTH indexes (the
        INSERT trigger indexes the plaintext before the flag flips, and
        the flag-flip UPDATE is a no-op for FTS)."""
        from voice_typer.server import _text_crypto

        dek = _text_crypto._dek.generate_dek()
        monkeypatch.setattr(_text_crypto._dek, "load_dek", lambda: dek)
        _text_crypto.reset_dek_cache()
        try:
            from voice_typer.server.history_db import HistoryDB

            db = HistoryDB(db_path=tmp_path / "trigram_encrypted.db")
            try:
                assert db.encryption_status() == "active"
                db.add_transcription("加密你好吗测试内容")
                db.flush()
                texts = [r["text"] for r in db.search("你好吗")]
                assert "加密你好吗测试内容" in texts
            finally:
                db.close()
        finally:
            _text_crypto.reset_dek_cache()


class TestGdprLockstep:
    def test_delete_purges_both_shadow_indexes(self, db):
        """After delete(id), the trigram index must no longer surface
        the deleted row (the trigger hides it; the optimize purges the
        segment data — this asserts the SEARCH contract)."""
        rows = db.search("今天你好吗")
        target_id = rows[0]["id"]
        assert db.delete(target_id) is True
        assert db.search("今天你好吗") == []

    def test_clear_all_purges_both_indexes(self, db):
        db.clear_all()
        assert db.search("你好吗") == []
        assert db.search("quick") == []

    def test_retention_and_clear_paths_cover_trigram_table(self):
        """The GDPR erasure sweep sites must issue their command to BOTH
        shadow indexes (the dictated plaintext lives in both shadow
        tables). Source-pinned: _rebuild_fts (retention), clear_all and
        the per-row delete path (crud_writes), and the startup rebuild
        sweep (writer) must all reference the trigram table."""
        import inspect

        from voice_typer.server.history_db_internals import crud_writes, retention, writer

        for func, name in (
            (retention._rebuild_fts, "retention._rebuild_fts"),
            (crud_writes.clear_all_rows, "crud_writes clear_all"),
            (crud_writes.delete_row, "crud_writes delete"),
            (writer._fts5_startup_rebuild, "writer._fts5_startup_rebuild"),
        ):
            try:
                src = inspect.getsource(func)
            except TypeError:
                src = inspect.getsource(func.__call__)  # type: ignore[misc]
            assert "transcriptions_fts_cjk" in src, (
                f"{name} does not issue its FTS command to the trigram CJK "
                "index — GDPR erasure and index liveness drift out of lockstep"
            )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
