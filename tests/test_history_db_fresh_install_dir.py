"""Regression: fresh-install history persistence when ``<config>/db/`` is absent."""

from __future__ import annotations

import shutil

import pytest
import voice_typer.server.history_db_internals.reader as reader_mod
import voice_typer.server.history_db_internals.schema as schema_mod
from voice_typer.server.history_db import HistoryDB


@pytest.fixture
def fresh_db_path(tmp_path):
    """A DB path whose parent directory deliberately does NOT exist."""
    return tmp_path / "db" / "history.db"


@pytest.fixture
def patch_platform(monkeypatch):
    """Install an ``is_windows`` override in BOTH history-db internal modules."""

    def _install(is_windows: bool) -> None:
        monkeypatch.setattr(schema_mod, "is_windows", lambda: is_windows)
        monkeypatch.setattr(reader_mod, "is_windows", lambda: is_windows)

    return _install


@pytest.mark.parametrize("platform_name", ["windows", "posix"])
def test_fresh_install_missing_db_dir_persists(fresh_db_path, patch_platform, platform_name):
    patch_platform(platform_name == "windows")
    assert not fresh_db_path.parent.exists()

    db = HistoryDB(db_path=fresh_db_path)
    try:
        assert fresh_db_path.parent.is_dir(), "open_write_conn must create the missing parent dir"
        assert db._init_error is None, f"writer init failed: {db._init_error}"
        row_id = db.add_transcription("fresh install text", duration=1.5, model="small.en", device="cpu")
        assert row_id > 0
        db.flush()
        assert db.get_latest_text() == "fresh install text"
    finally:
        db.close()


@pytest.mark.parametrize("platform_name", ["windows", "posix"])
def test_reader_recreates_deleted_db_dir(fresh_db_path, patch_platform, platform_name):
    """The reader's lazy connection creation mirrors the writer's mkdir."""
    patch_platform(platform_name == "windows")
    db = HistoryDB(db_path=fresh_db_path)
    try:
        db.add_transcription("before delete")
        db.flush()
    finally:
        db.close()
    shutil.rmtree(fresh_db_path.parent)
    assert not fresh_db_path.parent.exists()

    conn = reader_mod._get_read_conn(db)
    try:
        assert fresh_db_path.parent.is_dir(), "_get_read_conn must recreate the missing parent dir"
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
