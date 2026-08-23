"""Regression: fresh-install history persistence when ``<config>/db/`` is absent.

On a fresh install no legacy root ``history.db`` exists, so the O2
legacy-DB migration (``history_db._maybe_migrate_legacy_db``) never
creates the ``db/`` subdirectory. ``open_write_conn`` used to run its
parent-directory ``mkdir`` only on POSIX, so on Windows the first
``HistoryDB()`` construction failed with SQLite "unable to open database
file", the writer refused every write, and transcription history was
silently lost for the whole session. Existing installs only worked
because an upgrade-era migration had already created the directory.

These tests pin the fixed contract on BOTH platform branches (patched,
so the suite passes on any host): constructing a ``HistoryDB`` whose
parent directory does not exist yet must initialize cleanly, persist a
transcription, and read it back via ``get_latest_text``. The reader-side
lazy connection creation mirrors the writer and is pinned too.
"""

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
    """Install an ``is_windows`` override in BOTH history-db internal modules.

    The production modules import ``is_windows`` by name at module scope,
    so each submodule's binding must be patched for the platform branch
    under test to be simulated faithfully.
    """

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
    """The reader's lazy connection creation mirrors the writer's mkdir.

    After the directory disappears mid-session (e.g. deleted while the app
    runs), a new thread-local read connection must restore the parent
    directory and open a usable connection instead of raising
    "unable to open database file".
    """
    patch_platform(platform_name == "windows")
    db = HistoryDB(db_path=fresh_db_path)
    try:
        db.add_transcription("before delete")
        db.flush()
    finally:
        # close() is idempotent; it releases the file handles so the
        # directory can be removed on Windows too.
        db.close()
    shutil.rmtree(fresh_db_path.parent)
    assert not fresh_db_path.parent.exists()

    conn = reader_mod._get_read_conn(db)
    try:
        assert fresh_db_path.parent.is_dir(), "_get_read_conn must recreate the missing parent dir"
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
