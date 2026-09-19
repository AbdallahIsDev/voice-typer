"""Availability guards for the schema-V5 trigram CJK index."""

from __future__ import annotations

import sqlite3

import pytest

from tests.fixtures.history_test_helpers import history_plaintext_mode  # noqa: F401


@pytest.fixture
def old_sqlite(monkeypatch):
    """Simulate a distro-linked SQLite without the trigram tokenizer."""
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 31, 0))
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.31.0")


class TestTrigramHelpers:
    def test_supports_trigram_false_when_patched_old(self, old_sqlite):
        from voice_typer.server.history_db_internals.schema import sqlite_supports_trigram

        assert sqlite_supports_trigram() is False

    def test_supports_trigram_true_on_modern_sqlite(self):
        from voice_typer.server.history_db_internals.schema import sqlite_supports_trigram

        if sqlite3.sqlite_version_info >= (3, 34, 0):
            assert sqlite_supports_trigram() is True

    def test_table_exists_false_for_missing_table(self, tmp_path):
        from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

        conn = sqlite3.connect(str(tmp_path / "empty.db"))
        try:
            assert cjk_trigram_table_exists(conn) is False
        finally:
            conn.close()

    def test_table_exists_true_for_present_table(self, tmp_path):
        from voice_typer.server.history_db_internals.schema import cjk_trigram_table_exists

        conn = sqlite3.connect(str(tmp_path / "present.db"))
        try:
            conn.execute("CREATE TABLE transcriptions_fts_cjk (x)")
            assert cjk_trigram_table_exists(conn) is True
        finally:
            conn.close()


class TestMigrationSkippedOnOldSQLite:
    def test_v5_skipped_version_stays_below_5(self, old_sqlite, tmp_path):
        """unicode61 schema is fully applied."""
        from voice_typer.server.history_db import HistoryDB
        from voice_typer.server.history_db_internals import schema as schema_mod

        db = HistoryDB(db_path=tmp_path / "old-sqlite.db")
        try:
            conn = sqlite3.connect(str(db.db_path))
            try:
                row = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
                assert row is not None, "schema_meta version row must exist after open"
                assert int(row[0]) == 4, f"version must stay at 4 (V5 skipped), got {row[0]}"
                assert not schema_mod.cjk_trigram_table_exists(conn), (
                    "the trigram CJK table must NOT exist when V5 was skipped"
                )
                # The unicode61 FTS index (V3/V4) must still be present.
                fts = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions_fts'"
                ).fetchone()
                assert fts is not None, "the unicode61 FTS index must still exist"
            finally:
                conn.close()
        finally:
            db.close()

    def test_search_degrades_to_like_without_trigram_table(self, old_sqlite, tmp_path):
        """With V5 skipped, CJK queries must still return correct rows via"""
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB(db_path=tmp_path / "old-sqlite-search.db")
        try:
            db.add_transcription("今天你好吗")
            db.add_transcription("The quick brown fox")
            db.flush()
            # 3-char CJK query: trigram-eligible by the router heuristic —
            texts = [r["text"] for r in db.search("你好吗")]
            assert any("今天你好吗" in t for t in texts), f"CJK LIKE fallback broken: {texts}"
            # Latin queries keep the unicode61 FTS path, unchanged.
            latin = [r["text"] for r in db.search("quick brown")]
            assert any("The quick brown fox" in t for t in latin), f"FTS path broken: {latin}"
        finally:
            db.close()

    def test_modern_sqlite_still_migrates_to_v5(self, tmp_path):
        """Sanity: without the patch, a fresh DB reaches version 5 and"""
        from voice_typer.server.history_db import HistoryDB
        from voice_typer.server.history_db_internals import schema as schema_mod

        if not schema_mod.sqlite_supports_trigram():
            pytest.skip("linked SQLite genuinely lacks the trigram tokenizer")
        db = HistoryDB(db_path=tmp_path / "modern.db")
        try:
            conn = sqlite3.connect(str(db.db_path))
            try:
                row = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
                assert int(row[0]) == 5
                assert schema_mod.cjk_trigram_table_exists(conn)
            finally:
                conn.close()
        finally:
            db.close()
