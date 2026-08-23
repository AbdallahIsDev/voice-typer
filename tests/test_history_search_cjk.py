"""Tests for CJK / fullwidth substring search in ``HistoryDB.search``.

The FTS5 index tokenizes with ``unicode61``, which indexes a contiguous
CJK run (Chinese/Japanese/Korean text has no whitespace word boundaries)
as a SINGLE token — so a phrase-wrapped MATCH only finds rows where the
entire run equals the query, and searching "你好" never matched
"今天你好吗".

The contract pinned here: any query containing a character from the
CJK / fullwidth codepoint ranges is routed to the bounded LIKE path,
which gives true substring semantics for every query length (1-char
included) across Chinese, Japanese kana/kanji, Hangul, and fullwidth
forms. Latin-only queries keep taking the FTS5 path, unchanged.
"""

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB seeded with multi-script transcription rows."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "cjk_search_test.db")
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


class TestCjkWideCharDetection:
    """``has_cjk_or_wide_chars`` must detect every supported range."""

    @pytest.mark.parametrize(
        "query",
        [
            "你好",  # U+4E00-9FFF unified ideographs
            "𠀀",  # U+20000 astral extension B
            "ひらがな",  # U+3040-309F hiragana
            "カタカナ",  # U+30A0-30FF katakana
            "ｱｲｳ",  # U+FF66-FF9F halfwidth katakana
            "한국어",  # U+AC00-D7AF hangul syllables
            "ㄱ",  # U+1100 hangul jamo
            "、。",  # U+3000-303F CJK punctuation
            "ＡＢＣ！",  # U+FF00-FFEF fullwidth forms
            "abc你好",  # mixed script
        ],
    )
    def test_detects_cjk_ranges(self, query):
        from voice_typer.server.history_db_internals.search import has_cjk_or_wide_chars

        assert has_cjk_or_wide_chars(query) is True

    @pytest.mark.parametrize(
        "query",
        ["hello world", "café naïve", "100%", "", "snake_case"],
    )
    def test_latin_and_punctuation_only_queries_are_not_cjk(self, query):
        from voice_typer.server.history_db_internals.search import has_cjk_or_wide_chars

        assert has_cjk_or_wide_chars(query) is False

    def test_detection_respects_query_cap(self, monkeypatch):
        """Only the first ``_MAX_SEARCH_QUERY_CHARS`` chars are scanned."""
        from voice_typer.server import history_db as hd
        from voice_typer.server.history_db_internals.search import has_cjk_or_wide_chars

        latin_pad = "a" * hd._MAX_SEARCH_QUERY_CHARS
        assert has_cjk_or_wide_chars(latin_pad + "你") is False
        assert has_cjk_or_wide_chars(latin_pad[: hd._MAX_SEARCH_QUERY_CHARS - 1] + "你b") is True


class TestChineseSubstringSearch:
    def test_two_char_query_matches_containing_row(self, db):
        """The headline case: "你好" must find "今天你好吗"."""
        texts = [r["text"] for r in db.search("你好")]
        assert "今天你好吗" in texts
        assert "你好" in texts
        assert "こんにちは世界" not in texts

    def test_single_char_query(self, db):
        texts = [r["text"] for r in db.search("好")]
        assert "今天你好吗" in texts
        assert "你好" in texts
        assert "東京タワーへ行く" not in texts

    def test_multi_char_query_spanning_word_boundary(self, db):
        """CJK has no spaces; a query may span what Latin thinking calls
        a word boundary. "天你" sits across "今天" + "你好"."""
        assert [r["text"] for r in db.search("天你")] == ["今天你好吗"]

    def test_fullwidth_punctuation_query(self, db):
        assert [r["text"] for r in db.search("。再见")] == ["你好。再见！"]
        assert [r["text"] for r in db.search("！")] == ["你好。再见！"]

    def test_no_match_returns_empty_list(self, db):
        assert db.search("再見") == []  # traditional 見 vs simplified 见


class TestJapaneseSubstringSearch:
    def test_kana_query(self, db):
        assert [r["text"] for r in db.search("タワー")] == ["東京タワーへ行く"]

    def test_kanji_query(self, db):
        assert [r["text"] for r in db.search("東京")] == ["東京タワーへ行く"]

    def test_kanji_kana_mixed_query(self, db):
        assert [r["text"] for r in db.search("東京タワー")] == ["東京タワーへ行く"]


class TestHangulSubstringSearch:
    def test_two_char_hangul_substring(self, db):
        assert [r["text"] for r in db.search("녕하")] == ["안녕하세요 반갑습니다"]

    def test_syllable_block_query(self, db):
        texts = [r["text"] for r in db.search("하세요")]
        assert texts == ["안녕하세요 반갑습니다"]


class TestMixedScriptQuery:
    def test_contiguous_mixed_fragment_matches(self, db):
        """A mixed-script query takes the LIKE path: the whole capped
        query is one literal substring pattern."""
        assert [r["text"] for r in db.search("with 你好")] == ["Meeting notes with 你好 mixed"]

    def test_mixed_query_without_match_returns_empty(self, db):
        assert db.search("notes 你好") == []

    def test_cjk_query_with_percent_stays_literal(self, db):
        """LIKE wildcards in the query stay escaped — "100%折" matches
        the literal percent row, not an unbounded wildcard pattern."""
        assert [r["text"] for r in db.search("100%折")] == ["价格是100%折扣"]
        assert db.search("你好%") == []


class TestCjkSearchOrderingAndPagination:
    def test_results_ordered_newest_first_with_limit_offset(self, tmp_path):
        from datetime import datetime, timedelta

        from voice_typer.server.history_db import HistoryDB

        db2 = HistoryDB(db_path=tmp_path / "cjk_ordered.db")
        try:
            base = datetime.now()
            timestamps = [
                (base - timedelta(seconds=20)).isoformat(),
                (base - timedelta(seconds=10)).isoformat(),
                (base - timedelta(seconds=0)).isoformat(),
            ]
            texts = ["最旧的你好吗", "中间你好行", "最新你好条"]

            def _do_insert(conn):
                cur = conn.cursor()
                for ts, txt in zip(timestamps, texts, strict=True):
                    cur.execute(
                        "INSERT INTO transcriptions (text, timestamp) VALUES (?, ?)",
                        (txt, ts),
                    )
                conn.commit()

            db2._submit_write(_do_insert, wait=True)

            results = db2.search("你好")
            assert [r["text"] for r in results] == ["最新你好条", "中间你好行", "最旧的你好吗"]

            page1 = db2.search("你好", limit=1, offset=0)
            page2 = db2.search("你好", limit=1, offset=1)
            assert [r["text"] for r in page1] == ["最新你好条"]
            assert [r["text"] for r in page2] == ["中间你好行"]
        finally:
            db2.close()

    def test_cursor_pagination_on_cjk_query(self, db, tmp_path):
        """The cursor path (before_timestamp + before_id) must work on
        the CJK LIKE branch exactly like the FTS branch."""
        recent = db.get_recent(limit=10)
        anchor = recent[0]
        results = db.search(
            "你好",
            before_timestamp=anchor["timestamp"],
            before_id=anchor["id"],
        )
        assert all(r["id"] < anchor["id"] or r["timestamp"] < anchor["timestamp"] for r in results)


class TestLatinBehaviorUnchanged:
    def test_latin_query_still_hits_fts_path(self, db):
        assert [r["text"] for r in db.search("quick")] == ["The quick brown fox"]

    def test_latin_phrase_and_semantics_unchanged(self, db):
        assert [r["text"] for r in db.search("brown fox")] == ["The quick brown fox"]
        assert db.search("quick 世界") == []

    def test_empty_query_returns_all_rows(self, db):
        assert len(db.search("")) == 9

    def test_separator_only_query_still_like_fallback(self, db):
        assert [r["text"] for r in db.search("%")] == ["价格是100%折扣"]


if __name__ == "__main__":
    # Allow running this test file directly for quick local iteration.
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
