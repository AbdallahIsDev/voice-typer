"""Integration tests: at-rest encryption of dictated history text.

Exercises ``HistoryDB`` end-to-end with the encryption key sourced from
a faked OS keyring (the sandbox has ``keyring.backends.fail.Keyring`` —
the fake-injection pattern is copied from ``tests/test_credential_store.py``):

- encrypt-on-write: raw DB rows hold ``enc:v1:`` ciphertext + flag 1,
  while every read API returns decrypted PLAINTEXT,
- FTS5 search still matches an encrypted row's plaintext terms (the
  guarded triggers keep plaintext tokens in the index),
- key-loss policy: keyring wiped after encrypted rows exist → reads
  return "<decryption failed>", NEW writes stay plaintext (flag 0), and
  the DEK is NOT regenerated,
- schema v3 → v4 migration: a legacy DB gains the flag column and its
  rows are backfilled to ciphertext in bounded batches,
- restore() round-trip with encryption on,
- plaintext mode parity: with no usable keyring the behavior is
  byte-identical to the pre-encryption contract (flag 0, plaintext,
  status "disabled").

The process-global DEK + keyring caches are reset around EVERY test so
the encryption state never leaks into other test files.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import _text_crypto, credential_store

#: Raw-SQL helper constants.
_FLAG_SQL = "SELECT text, text_is_encrypted FROM transcriptions WHERE id = ?"


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_caches():
    """Reset the process-global DEK + keyring caches around each test.

    Critical for suite isolation: without the post-test reset a DEK
    resolved here would leak into plaintext-mode tests in other files
    (the cache lives on the ``_text_crypto`` module, shared by every
    HistoryDB instance in the process).
    """
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()
    yield
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()


def _make_fake_keyring(monkeypatch, *, available: bool):
    """Install a dict-backed fake keyring; return the backing store."""
    store: dict[tuple[str, str], str] = {}

    class _FakeBackend:
        name = "FakeKeyring"

        def get_password(self, service, username):
            return store.get((service, username))

        def set_password(self, service, username, password):
            store[(service, username)] = password

        def delete_password(self, service, username):
            store.pop((service, username), None)

    fake_keyring_module = MagicMock()
    fake_keyring_module.get_keyring.return_value = _FakeBackend()
    fake_keyring_module.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring_module.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring_module.delete_password.side_effect = lambda s, u: store.pop((s, u), None)

    monkeypatch.setitem(sys.modules, "keyring", fake_keyring_module)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    if available:
        monkeypatch.setattr(credential_store, "_probe_keyring", lambda: (True, "FakeKeyring", None))
    else:
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (False, "fail", "no usable keyring backend (test)"),
        )
    credential_store._reset_keyring_cache()
    return store


@pytest.fixture
def keyring_available(monkeypatch):
    """Fake OS keyring with a working backend (encryption can activate)."""
    return _make_fake_keyring(monkeypatch, available=True)


@pytest.fixture
def keyring_unavailable(monkeypatch):
    """Fake headless-Linux keyring: no usable backend at all."""
    return _make_fake_keyring(monkeypatch, available=False)


@pytest.fixture
def encrypted_db(tmp_path, keyring_available):
    """A HistoryDB with an active DEK, closed after the test."""
    from voice_typer.server.history_db import HistoryDB

    db = HistoryDB(db_path=tmp_path / "enc_history.db")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def plaintext_db(tmp_path, keyring_unavailable):
    """A HistoryDB in plaintext mode (no usable keyring)."""
    from voice_typer.server.history_db import HistoryDB

    db = HistoryDB(db_path=tmp_path / "plain_history.db")
    try:
        yield db
    finally:
        db.close()


# ── Helpers ──────────────────────────────────────────────────────────────


def _raw_row(db, row_id: int) -> tuple:
    """Read ``(text, text_is_encrypted)`` straight from the DB file."""
    conn = sqlite3.connect(str(db.db_path))
    try:
        return conn.execute(_FLAG_SQL, (row_id,)).fetchone()
    finally:
        conn.close()


def _all_raw_rows(db) -> list[tuple]:
    conn = sqlite3.connect(str(db.db_path))
    try:
        return conn.execute("SELECT id, text, text_is_encrypted FROM transcriptions ORDER BY id").fetchall()
    finally:
        conn.close()


def _wait_for_backfill(db, expected_encrypted: int, timeout_s: float = 10.0) -> None:
    """Poll the raw DB until ``expected_encrypted`` rows carry flag 1."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        encrypted = sum(1 for _, _, flag in _all_raw_rows(db) if flag)
        if encrypted >= expected_encrypted:
            return
        time.sleep(0.05)
    encrypted = sum(1 for _, _, flag in _all_raw_rows(db) if flag)
    pytest.fail(f"backfill did not complete: {encrypted}/{expected_encrypted} rows encrypted")


def _first_row_id(db) -> int:
    rows = db.get_recent(limit=1)
    assert rows, "expected at least one row"
    return rows[0]["id"]


# ── Encrypt-on-write ─────────────────────────────────────────────────────


class TestEncryptOnWrite:
    def test_raw_row_is_ciphertext_but_reads_are_plaintext(self, encrypted_db):
        db = encrypted_db
        db.add_transcription("secret hello world", duration=1.0, model="small.en")
        db.flush()

        row_id = _first_row_id(db)
        raw_text, flag = _raw_row(db, row_id)
        assert flag == 1, "row must be flagged encrypted"
        assert raw_text.startswith("enc:v1:")
        assert "secret" not in raw_text

        # Every read API decrypts transparently.
        recent = db.get_recent(limit=10)
        assert recent[0]["text"] == "secret hello world"
        assert recent[0]["text_full_length"] == len("secret hello world")
        assert recent[0]["text_truncated"] is False
        assert "text_is_encrypted" not in recent[0], "response shape must not leak the flag"
        assert db.get_transcription_text(row_id)["text"] == "secret hello world"
        assert db.get_latest_text() == "secret hello world"

    def test_batch_insert_path_encrypts_every_row(self, encrypted_db):
        """The multi-row INSERT path (writer._drain_batchable_inserts)."""
        db = encrypted_db
        for i in range(5):
            db.add_transcription(f"batched secret number {i}")
        db.flush()

        rows = _all_raw_rows(db)
        assert len(rows) == 5
        for _row_id, text, flag in rows:
            assert flag == 1
            assert text.startswith("enc:v1:")
        assert [r["text"] for r in db.get_recent(limit=10)] == [f"batched secret number {i}" for i in range(4, -1, -1)]

    def test_long_text_preview_truncation_uses_plaintext_length(self, encrypted_db):
        db = encrypted_db
        long_text = "z" * 2048
        db.add_transcription(long_text)
        db.flush()

        row_id = _first_row_id(db)
        _, flag = _raw_row(db, row_id)
        assert flag == 1
        row = db.get_recent(limit=1)[0]
        # Preview fields must describe the PLAINTEXT, not the ciphertext.
        assert row["text"] == "z" * 500
        assert row["text_full_length"] == 2048
        assert row["text_truncated"] is True
        assert db.get_transcription_text(row_id)["text"] == long_text

    def test_status_active_and_dek_persisted(self, encrypted_db, keyring_available):
        db = encrypted_db
        db.add_transcription("x")
        db.flush()
        assert db.encryption_status() == "active"
        from voice_typer.server.credential_store._schema import (
            DATA_ENCRYPTION_KEY_USERNAME,
            KEYRING_SERVICE_NAME,
        )

        assert (KEYRING_SERVICE_NAME, DATA_ENCRYPTION_KEY_USERNAME) in keyring_available


# ── FTS search over encrypted rows ───────────────────────────────────────


class TestFtsSearchOverEncryptedRows:
    def test_fts_matches_plaintext_terms_of_encrypted_row(self, encrypted_db):
        db = encrypted_db
        db.add_transcription("the quick brown fox jumps over the lazy dog")
        db.add_transcription("unrelated content entirely")
        db.flush()

        results = db.search("quick")
        assert len(results) == 1
        assert results[0]["text"] == "the quick brown fox jumps over the lazy dog"
        # ...and the matching row is genuinely encrypted at rest.
        assert _raw_row(db, results[0]["id"])[1] == 1

    def test_fts_survives_favorite_toggle_on_encrypted_row(self, encrypted_db):
        """The guarded AFTER-UPDATE trigger must not corrupt the index.

        A favorite toggle on an encrypted row has an UNCHANGED flag —
        without the strict ``NEW=0 AND OLD=0`` guard the FTS5 'delete'
        would run with ciphertext tokens and corrupt the index
        ("database disk image is malformed").
        """
        db = encrypted_db
        db.add_transcription("favorite encryption target words")
        db.flush()
        row_id = _first_row_id(db)

        assert db.toggle_favorite(row_id) is True
        db.flush()
        assert db.search("encryption")[0]["id"] == row_id
        assert db.get_favorites(limit=10)[0]["id"] == row_id

    def test_delete_encrypted_row_keeps_db_healthy(self, encrypted_db):
        """The guarded AFTER-DELETE trigger skips token removal for
        encrypted rows; the JOIN filters the stale rowid and the DB
        stays consistent."""
        db = encrypted_db
        db.add_transcription("deletable encrypted content")
        db.flush()
        row_id = _first_row_id(db)

        assert db.delete(row_id) is True
        db.flush()
        assert db.search("encrypted") == []
        # Integrity must survive the guarded delete.
        conn = sqlite3.connect(str(db.db_path))
        try:
            assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            conn.close()

    def test_search_pagination_decrypts_every_page(self, encrypted_db):
        db = encrypted_db
        for i in range(7):
            db.add_transcription(f"page row {i} with keyword")
        db.flush()
        page1 = db.search("keyword", limit=3)
        page2 = db.search("keyword", limit=3, offset=3)
        # Same-second timestamps → (timestamp DESC, id DESC) == id DESC.
        assert [r["text"] for r in page1] == [f"page row {i} with keyword" for i in range(6, 3, -1)]
        assert [r["text"] for r in page2] == [f"page row {i} with keyword" for i in range(3, 0, -1)]


def _wait_for_search(db, term: str, expected: int, timeout_s: float = 10.0) -> None:
    """Poll ``db.search(term)`` until it returns ``expected`` results."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = len(db.search(term))
        if last >= expected:
            return
        time.sleep(0.05)
    pytest.fail(f"search({term!r}) never reached {expected} results (last: {last})")


# ── Key-loss policy ──────────────────────────────────────────────────────


class TestKeyLossPolicy:
    def test_keyring_wiped_after_encrypted_rows_exist(self, tmp_path, monkeypatch):
        """Encrypted rows + missing DEK → placeholder reads, plaintext
        new writes, NO regeneration."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "keyloss.db"

        # Session 1: encryption active, rows written.
        store = _make_fake_keyring(monkeypatch, available=True)
        db = HistoryDB(db_path=db_path)
        db.add_transcription("precious secret one")
        db.add_transcription("precious secret two")
        db.flush()
        db.close()
        assert db.encryption_status() == "active"

        # The user wipes their keychain.
        store.clear()
        _text_crypto.reset_dek_cache()
        credential_store._reset_keyring_cache()

        # Session 2: keyring still works, but the DEK is gone.
        db2 = HistoryDB(db_path=db_path)
        try:
            assert db2.encryption_status() == "key-unavailable"

            # Reads return the placeholder — never ciphertext.
            rows = db2.get_recent(limit=10)
            assert len(rows) == 2
            assert all(r["text"] == _text_crypto.DECRYPTION_FAILED_PLACEHOLDER for r in rows)
            assert db2.get_latest_text() == _text_crypto.DECRYPTION_FAILED_PLACEHOLDER
            assert db2.get_transcription_text(rows[0]["id"])["text"] == _text_crypto.DECRYPTION_FAILED_PLACEHOLDER

            # New writes stay PLAINTEXT (flag 0) — no further data loss.
            db2.add_transcription("written after key loss")
            db2.flush()
            plain_rows = [
                (row_id, text, flag) for row_id, text, flag in _all_raw_rows(db2) if text == "written after key loss"
            ]
            assert len(plain_rows) == 1
            assert plain_rows[0][2] == 0

            # The DEK was NOT regenerated (nothing re-stored).
            assert store == {}
            # Metadata stays readable so the user can delete rows.
            assert rows[0]["model"] is not None
        finally:
            db2.close()

    def test_keyring_unavailable_after_encrypted_rows_exist(self, tmp_path, monkeypatch):
        """Keyring backend down entirely → same key-unavailable state."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "keyloss_backend.db"

        _make_fake_keyring(monkeypatch, available=True)
        db = HistoryDB(db_path=db_path)
        db.add_transcription("secret under a working keyring")
        db.flush()
        db.close()

        # Session 2: backend unavailable (e.g. D-Bus gone).
        _make_fake_keyring(monkeypatch, available=False)
        _text_crypto.reset_dek_cache()
        credential_store._reset_keyring_cache()

        db2 = HistoryDB(db_path=db_path)
        try:
            assert db2.encryption_status() == "key-unavailable"
            assert db2.get_recent(limit=10)[0]["text"] == _text_crypto.DECRYPTION_FAILED_PLACEHOLDER
        finally:
            db2.close()


# ── Schema migration v3 → v4 + backfill ──────────────────────────────────


def _build_legacy_v3_db(db_path: Path, rows: list[str]) -> None:
    """Create a pre-encryption schema-v3 database with plaintext rows."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE transcriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                duration REAL DEFAULT 0,
                model TEXT DEFAULT '',
                device TEXT DEFAULT '',
                word_count INTEGER DEFAULT 0,
                char_count INTEGER DEFAULT 0,
                favorite INTEGER DEFAULT 0,
                language TEXT DEFAULT ''
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta (key, value) VALUES ('version', '3');
            CREATE VIRTUAL TABLE transcriptions_fts USING fts5(
                text,
                content='transcriptions',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TRIGGER transcriptions_ai_fts AFTER INSERT ON transcriptions BEGIN
                INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
            END;
            CREATE TRIGGER transcriptions_ad_fts AFTER DELETE ON transcriptions BEGIN
                INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
            END;
            CREATE TRIGGER transcriptions_au_fts AFTER UPDATE ON transcriptions BEGIN
                INSERT INTO transcriptions_fts(transcriptions_fts, rowid, text) VALUES ('delete', old.id, old.text);
                INSERT INTO transcriptions_fts(rowid, text) VALUES (new.id, new.text);
            END;
            """
        )
        for row in rows:
            conn.execute(
                "INSERT INTO transcriptions (text) VALUES (?)",
                (row,),
            )
        conn.commit()
    finally:
        conn.close()


class TestMigrationAndBackfill:
    def test_v3_db_gains_flag_column_and_backfills(self, tmp_path, keyring_available):
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "legacy_v3.db"
        _build_legacy_v3_db(db_path, ["legacy plaintext alpha", "legacy plaintext beta"])

        db = HistoryDB(db_path=db_path)
        try:
            # Migration: column exists, version bumped to 4.
            conn = sqlite3.connect(str(db_path))
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(transcriptions)")}
                version = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()[0]
                triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
            finally:
                conn.close()
            assert "text_is_encrypted" in cols
            assert version == "4"
            # The guarded triggers replaced the unguarded v3 set.
            assert {
                "transcriptions_ai_fts",
                "transcriptions_ad_fts",
                "transcriptions_au_fts",
            } <= triggers

            # Background backfill encrypts the legacy rows.
            _wait_for_backfill(db, expected_encrypted=2)
            for _, text, flag in _all_raw_rows(db):
                assert flag == 1
                assert text.startswith("enc:v1:")

            # Reads return the original plaintext; search still matches.
            assert [r["text"] for r in db.get_recent(limit=10)] == [
                "legacy plaintext beta",
                "legacy plaintext alpha",
            ]
            assert len(db.search("alpha")) == 1
            assert db.encryption_status() == "active"
        finally:
            db.close()

    def test_backfill_is_bounded_batch_and_resumes(self, tmp_path, keyring_available, monkeypatch):
        """More than one batch: the step re-enqueues itself and finishes."""
        from voice_typer.server import history_db as hd
        from voice_typer.server.history_db import HistoryDB

        monkeypatch.setattr(hd, "_ENCRYPTION_BACKFILL_BATCH", 3)

        db_path = tmp_path / "legacy_bulk.db"
        _build_legacy_v3_db(db_path, [f"legacy row {i}" for i in range(8)])

        db = HistoryDB(db_path=db_path)
        try:
            _wait_for_backfill(db, expected_encrypted=8)
            assert all(flag == 1 for _, _, flag in _all_raw_rows(db))
            assert len(db.search("legacy")) == 8
        finally:
            db.close()

    def test_migration_without_keyring_stays_plaintext(self, tmp_path, keyring_unavailable):
        """A legacy DB opened where no keyring exists: migration runs
        (column + triggers), the backfill is skipped, rows stay readable."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "legacy_nokeyring.db"
        _build_legacy_v3_db(db_path, ["stays plaintext"])

        db = HistoryDB(db_path=db_path)
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(transcriptions)")}
            finally:
                conn.close()
            assert "text_is_encrypted" in cols
            assert db.encryption_status() == "disabled"
            assert db.get_recent(limit=10)[0]["text"] == "stays plaintext"
            assert _all_raw_rows(db)[0][2] == 0
        finally:
            db.close()


# ── Decrypt-aware FTS re-index after a startup rebuild ───────────────────


class TestFtsRebuildReindex:
    def test_startup_rebuild_restores_plaintext_tokens(self, tmp_path, keyring_available):
        """A startup FTS5 'rebuild' (flag='1' from a prior failed rebuild)
        re-tokenizes encrypted rows with ciphertext; the decrypt-aware
        re-index must restore plaintext tokens so search works again."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "reindex.db"

        # Session 1: encrypted rows exist.
        db = HistoryDB(db_path=db_path)
        db.add_transcription("reindex target words for search")
        db.flush()
        assert len(db.search("reindex")) == 1
        db.close()

        # Simulate a prior session's failed delete-time rebuild → the
        # next launch's startup sweep runs a full FTS5 'rebuild'.
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '1')")
            conn.commit()
        finally:
            conn.close()

        # Session 2: startup rebuild indexes CIPHERTEXT tokens, then the
        # decrypt-aware re-index restores the plaintext tokens.
        db2 = HistoryDB(db_path=db_path)
        try:
            assert db2.encryption_status() == "active"
            _wait_for_search(db2, "reindex", expected=1)
            assert db2.search("target")[0]["text"] == "reindex target words for search"
            # Integrity is preserved throughout.
            conn = sqlite3.connect(str(db_path))
            try:
                assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                conn.close()
        finally:
            db2.close()

    def test_startup_rebuild_without_dek_skips_reindex(self, tmp_path, monkeypatch):
        """Key-loss mode: the re-index has no plaintext to insert and is
        skipped — search stays degraded, nothing corrupts."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "reindex_nokey.db"

        store = _make_fake_keyring(monkeypatch, available=True)
        db = HistoryDB(db_path=db_path)
        db.add_transcription("lost key rebuild target")
        db.flush()
        db.close()

        # Keychain wiped + failure flag set for the next launch.
        store.clear()
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('fts5_rebuild_failed', '1')")
            conn.commit()
        finally:
            conn.close()

        _text_crypto.reset_dek_cache()
        credential_store._reset_keyring_cache()
        db2 = HistoryDB(db_path=db_path)
        try:
            assert db2.encryption_status() == "key-unavailable"
            assert db2.get_recent(limit=10)[0]["text"] == _text_crypto.DECRYPTION_FAILED_PLACEHOLDER
            conn = sqlite3.connect(str(db_path))
            try:
                assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                conn.close()
        finally:
            db2.close()


# ── restore() round-trip ─────────────────────────────────────────────────


class TestRestoreWithEncryption:
    def test_restore_reencrypts_and_round_trips(self, encrypted_db):
        db = encrypted_db
        db.add_transcription("restorable secret text", duration=2.0)
        db.flush()
        row = db.get_recent(limit=1)[0]
        assert db.delete(row["id"]) is True
        db.flush()
        assert db.get_recent(limit=10) == []

        new_id = db.restore(row)
        assert new_id > 0
        db.flush()

        raw_text, flag = _raw_row(db, new_id)
        assert flag == 1, "restored row must be encrypted at rest"
        assert raw_text.startswith("enc:v1:")
        assert db.get_transcription_text(new_id)["text"] == "restorable secret text"
        # FTS still matches the restored row's terms.
        assert db.search("restorable")[0]["id"] == new_id

    def test_restore_in_plaintext_mode_stays_plaintext(self, plaintext_db):
        db = plaintext_db
        db.add_transcription("plain restore target")
        db.flush()
        row = db.get_recent(limit=1)[0]
        assert db.delete(row["id"]) is True
        db.flush()

        new_id = db.restore(row)
        db.flush()
        raw_text, flag = _raw_row(db, new_id)
        assert flag == 0
        assert raw_text == "plain restore target"
        assert db.get_transcription_text(new_id)["text"] == "plain restore target"


# ── Plaintext-mode parity (zero-regression guarantee) ────────────────────


class TestPlaintextModeParity:
    def test_no_keyring_behaves_exactly_as_before(self, plaintext_db):
        db = plaintext_db
        db.add_transcription("plain hello", duration=1.5, model="small.en", language="en")
        db.flush()

        row_id = _first_row_id(db)
        raw_text, flag = _raw_row(db, row_id)
        assert flag == 0, "no keyring → rows stay plaintext"
        assert raw_text == "plain hello"

        assert db.encryption_status() == "disabled"
        row = db.get_recent(limit=1)[0]
        assert row["text"] == "plain hello"
        assert row["text_full_length"] == len("plain hello")
        assert row["text_truncated"] is False
        assert db.get_latest_text() == "plain hello"
        assert db.get_transcription_text(row_id)["text"] == "plain hello"
        assert len(db.search("hello")) == 1

    def test_retention_and_stats_work_in_plaintext_mode(self, plaintext_db):
        db = plaintext_db
        for i in range(4):
            db.add_transcription(f"row {i}")
        db.flush()
        assert db.get_history_count() == 4
        assert db.get_today_stats()["count"] == 4
        assert db.delete(_first_row_id(db)) is True
        db.flush()
        assert db.get_history_count() == 3

    def test_cleartext_db_opened_with_keyring_later_backfills(self, tmp_path, monkeypatch):
        """A plaintext DB (written without a keyring) is encrypted once a
        keyring appears — without regenerating or losing anything."""
        from voice_typer.server.history_db import HistoryDB

        db_path = tmp_path / "late_keyring.db"

        # Session 1: no keyring → plaintext rows.
        _make_fake_keyring(monkeypatch, available=False)
        db = HistoryDB(db_path=db_path)
        db.add_transcription("written before the keyring existed")
        db.flush()
        assert db.encryption_status() == "disabled"
        db.close()

        # Session 2: keyring appears → backfill encrypts the old rows.
        _make_fake_keyring(monkeypatch, available=True)
        _text_crypto.reset_dek_cache()
        credential_store._reset_keyring_cache()
        db2 = HistoryDB(db_path=db_path)
        try:
            assert db2.encryption_status() == "active"
            _wait_for_backfill(db2, expected_encrypted=1)
            assert db2.get_recent(limit=10)[0]["text"] == "written before the keyring existed"
            assert db2.search("keyring")[0]["text"] == "written before the keyring existed"
        finally:
            db2.close()
