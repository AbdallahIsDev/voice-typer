"""History init stays single-call and silent on re-entry."""

from __future__ import annotations

import logging
import sqlite3

import pytest


@pytest.fixture()
def _clean_history_guards():
    from voice_typer.server.history_db_internals import encryption, schema, writer as _writer

    schema._announced_db_paths.clear()
    encryption._initialized_db_paths.clear()
    _writer._announced_fts5_skip_paths.clear()
    encryption._reset_encryption_initialized_paths()
    yield
    schema._announced_db_paths.clear()
    encryption._initialized_db_paths.clear()
    _writer._announced_fts5_skip_paths.clear()


def _memory_conn():
    conn = sqlite3.connect(":memory:")
    return conn


def test_schema_second_init_is_silent(caplog, tmp_path, _clean_history_guards):
    from voice_typer.server.history_db_internals import schema

    db_path = tmp_path / "history.db"

    def _make_db():
        from voice_typer.server.history_db import HistoryDB

        db = HistoryDB.__new__(HistoryDB)
        db.db_path = db_path
        db._init_error = None
        db._fts5_rebuild_ran = False
        return db

    db = _make_db()
    conn = _memory_conn()
    try:
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db_internals.schema"):
            schema.init_schema(db, conn)
            first_infos = [
                r
                for r in caplog.records
                if r.levelno == logging.INFO and "History database initialized" in r.getMessage()
            ]
            assert len(first_infos) == 1
            caplog.clear()
            schema.init_schema(db, conn)
            repeats = [r for r in caplog.records if "History database initialized" in r.getMessage()]
            assert repeats == []
    finally:
        with __import__("contextlib").suppress(Exception):
            conn.close()


def test_encryption_second_init_is_silent(tmp_path, caplog, _clean_history_guards, monkeypatch):
    from voice_typer.server.history_db_internals import encryption

    db_path = tmp_path / "history.db"

    class _Db:
        pass

    db = _Db()
    db.db_path = db_path
    db._encryption_status = "disabled"
    db._fts5_rebuild_ran = False
    db._has_encrypted_rows = lambda conn: False  # noqa: E731
    db._has_plaintext_rows = lambda conn: False  # noqa: E731
    db._enqueue_backfill_step = lambda: None  # noqa: E731
    db._enqueue_reindex_step = lambda: None  # noqa: E731

    from voice_typer.server import _text_crypto

    monkeypatch.setattr(_text_crypto, "resolve_dek", lambda has_enc: None)
    monkeypatch.setattr(_text_crypto, "encryption_status", lambda dek, has_enc: "disabled")

    conn = _memory_conn()
    try:
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db_internals.encryption"):
            encryption._init_encryption(db, conn)
            assert any("at-rest encryption status" in r.getMessage() for r in caplog.records)
            caplog.clear()
            encryption._init_encryption(db, conn)
            assert [r for r in caplog.records if "at-rest encryption status" in r.getMessage()] == []
    finally:
        with __import__("contextlib").suppress(Exception):
            conn.close()


def test_fts5_skip_second_is_silent(tmp_path, caplog, _clean_history_guards):
    from voice_typer.server.history_db_internals import writer as _writer

    db_path = tmp_path / "history.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '0')")
        conn.commit()

        class _Db:
            pass

        db = _Db()
        db.db_path = db_path
        db._fts5_rebuild_ran = False

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.history_db_internals.writer"):
            _writer._fts5_startup_rebuild(db, conn)
            first = [r for r in caplog.records if "FTS5 startup rebuild succeeded" in r.getMessage()]
            assert len(first) == 1
            caplog.clear()
            _writer._fts5_startup_rebuild(db, conn)
            assert [r for r in caplog.records if "FTS5 startup rebuild succeeded" in r.getMessage()] == []
    finally:
        with __import__("contextlib").suppress(Exception):
            conn.close()
