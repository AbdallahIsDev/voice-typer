"""corrupt DB file."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from tests.fixtures.history_test_helpers import history_plaintext_mode  # noqa: F401


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _force_corruption(db) -> None:
    """recovery path triggers."""
    # Close the writer's connection so we can rename the file.
    pass


class TestCorruptionNotification:
    """FR-29: corruption recovery publishes a ``history_corrupted`` event"""

    def test_publishes_history_corrupted_event(self, db, monkeypatch):
        """When corruption is detected, a ``history_corrupted`` event"""
        published_events: list[dict] = []
        from voice_typer.server import event_bus

        original_publish = event_bus.publish

        def spy_publish(event):
            published_events.append(event)
            return original_publish(event)

        monkeypatch.setattr(event_bus, "publish", spy_publish)

        # Make quick_check return a non-ok result.
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fake_conn.close = MagicMock()

        # Patch _open_write_conn so the recovery path doesn't open a real
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        # Act.
        db._maybe_recover_from_corruption(fake_conn)

        matching = [e for e in published_events if e.get("type") == "history_corrupted"]
        assert matching, (
            "FR-29 violation: _maybe_recover_from_corruption did not publish a "
            f"history_corrupted event. Published: {published_events}"
        )
        event = matching[0]
        assert "path" in event.get("data", {}), "event.data must include 'path'"
        assert "corrupt-" in event["data"]["path"], "event.data.path must name the corrupt backup file"

    def test_calls_tray_notify_when_app_ref_wired(self, db, monkeypatch):
        """When ``self._app.tray.notify`` is available, the recovery"""
        tray = MagicMock()
        app = MagicMock()
        app.tray = tray
        db._app = app  # type: ignore[attr-defined]

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        db._maybe_recover_from_corruption(fake_conn)

        tray.notify.assert_called_once()
        # First positional arg is the app name; second is the message.
        args, _kwargs = tray.notify.call_args
        assert len(args) >= 2
        message = args[1]
        assert "corrupted" in message.lower() or "backed up" in message.lower(), (
            f"tray.notify message must mention corruption/backup; got: {message!r}"
        )

    def test_does_not_crash_when_no_app_ref(self, db, monkeypatch):
        """When ``self._app`` is not set (early init), the recovery path"""
        # Ensure no _app attribute.
        assert not hasattr(db, "_app") or db._app is None

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        # Must not raise.
        result = db._maybe_recover_from_corruption(fake_conn)
        # Returns the fresh connection.
        assert result is fresh_conn

    def test_logs_warning_with_user_facing_message(self, db, caplog, monkeypatch):
        """A WARNING-level log with a user-facing message must be"""
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db_internals.corruption_recovery"):
            db._maybe_recover_from_corruption(fake_conn)

        # Look for a WARNING log that mentions "corrupted" and "backed up".
        matching = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "corrupted" in r.getMessage().lower()
            and "backed up" in r.getMessage().lower()
        ]
        assert matching, "FR-29 violation: expected a WARNING log with 'corrupted' and 'backed up'. Got: " + repr(
            [r.getMessage() for r in caplog.records]
        )

    def test_event_bus_failure_does_not_crash_recovery(self, db, monkeypatch):
        """If ``event_bus.publish`` raises, the recovery must still"""
        from voice_typer.server import event_bus

        def boom_publish(_event):
            raise RuntimeError("event bus offline")

        monkeypatch.setattr(event_bus, "publish", boom_publish)

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        # Must not raise.
        result = db._maybe_recover_from_corruption(fake_conn)
        assert result is fresh_conn

    def test_event_includes_recovered_count_field(self, db, monkeypatch):
        """the ``history_corrupted`` event payload must"""
        published_events: list[dict] = []
        from voice_typer.server import event_bus

        original_publish = event_bus.publish

        def spy_publish(event):
            published_events.append(event)
            return original_publish(event)

        monkeypatch.setattr(event_bus, "publish", spy_publish)

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)
        # Force iterdump recovery to return empty (no real corrupt file).
        monkeypatch.setattr(db, "_try_iterdump_recovery", lambda path: [])

        db._maybe_recover_from_corruption(fake_conn)

        matching = [e for e in published_events if e.get("type") == "history_corrupted"]
        assert matching, "history_corrupted event not published"
        event = matching[0]
        assert "recovered_count" in event["data"], f"event.data must include 'recovered_count'; got {event['data']}"
        assert event["data"]["recovered_count"] == 0, (
            f"with empty iterdump result, recovered_count must be 0; got {event['data']['recovered_count']}"
        )
        assert "db_path" in event["data"], f"event.data must include 'db_path'; got {event['data']}"

    def test_severe_corruption_falls_back_to_rename_and_fresh_db(self, db, monkeypatch):
        """when ``iterdump()`` fails (severe corruption),"""
        published_events: list[dict] = []
        from voice_typer.server import event_bus

        original_publish = event_bus.publish

        def spy_publish(event):
            published_events.append(event)
            return original_publish(event)

        monkeypatch.setattr(event_bus, "publish", spy_publish)

        # Simulate severe corruption: iterdump returns nothing.
        iterdump_called = {"count": 0}

        def fake_iterdump(_path):
            iterdump_called["count"] += 1
            return []

        monkeypatch.setattr(db, "_try_iterdump_recovery", fake_iterdump)

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        # Must not raise, even with no data recovered.
        result = db._maybe_recover_from_corruption(fake_conn)

        assert iterdump_called["count"] == 1, (
            f"expected _try_iterdump_recovery to be called once; got {iterdump_called['count']}"
        )
        # Fresh DB is still returned.
        assert result is fresh_conn
        # Event published with recovered_count=0.
        matching = [e for e in published_events if e.get("type") == "history_corrupted"]
        assert matching
        assert matching[0]["data"]["recovered_count"] == 0


class TestIterdumpDataRecovery:
    """end-to-end tests for the ``iterdump()`` data-recovery"""

    def test_try_iterdump_recovery_extracts_inserts_from_real_db(self, tmp_path):
        """``_try_iterdump_recovery`` opens a real (structurally-valid)"""
        from voice_typer.server.history_db import HistoryDB

        # Phase 1: create a real DB with two rows.
        db_path = tmp_path / "test_history.db"
        db1 = HistoryDB(db_path=db_path)
        db1.add_transcription("hello world", duration=1.0, model="tiny", device="cpu")
        db1.add_transcription("second row", duration=2.0, model="tiny", device="cpu")
        db1.flush()
        db1.checkpoint()
        db1.close()

        # Simulate the post-rename state: corrupt_main is the renamed
        corrupt_main = tmp_path / "test_history.db.corrupt-1700000000"
        db_path.rename(corrupt_main)

        fresh_db = HistoryDB(db_path=tmp_path / "fresh.db")
        try:
            inserts = fresh_db._try_iterdump_recovery(corrupt_main)

            # At least 2 INSERT statements (one per row).
            assert len(inserts) >= 2, f"expected >=2 INSERTs, got {len(inserts)}: {inserts}"

            # All statements must be INSERT INTO transcriptions (no
            for stmt in inserts:
                upper = stmt.lstrip().upper()
                assert upper.startswith("INSERT INTO"), f"expected INSERT statement, got: {stmt!r}"
                lower = stmt.lower()
                assert "schema_meta" not in lower, f"schema_meta INSERT must be filtered out: {stmt!r}"
                assert "transcriptions_fts" not in lower, f"transcriptions_fts INSERT must be filtered out: {stmt!r}"
                assert "sqlite_sequence" not in lower, f"sqlite_sequence INSERT must be filtered out: {stmt!r}"

            # The row data is present in the recovered statements.
            combined = "\n".join(inserts)
            assert "hello world" in combined, f"expected 'hello world' in recovered INSERTs; got: {combined!r}"
            assert "second row" in combined, f"expected 'second row' in recovered INSERTs; got: {combined!r}"
        finally:
            fresh_db.close()

    def test_try_iterdump_recovery_returns_empty_on_severe_corruption(self, tmp_path):
        """When the corrupt file is not a valid SQLite database (e.g."""
        from voice_typer.server.history_db import HistoryDB

        corrupt_main = tmp_path / "garbage.db.corrupt-1"
        corrupt_main.write_bytes(b"this is not a sqlite database, just garbage bytes")

        fresh_db = HistoryDB(db_path=tmp_path / "fresh.db")
        try:
            inserts = fresh_db._try_iterdump_recovery(corrupt_main)
            assert inserts == [], f"expected empty list for severe corruption, got: {inserts}"
        finally:
            fresh_db.close()

    def test_try_iterdump_recovery_returns_empty_for_missing_file(self, tmp_path):
        """When the corrupt file doesn't exist (e.g. the rename"""
        from voice_typer.server.history_db import HistoryDB

        missing_path = tmp_path / "does_not_exist.db.corrupt-1"

        fresh_db = HistoryDB(db_path=tmp_path / "fresh.db")
        try:
            inserts = fresh_db._try_iterdump_recovery(missing_path)
            assert inserts == []
        finally:
            fresh_db.close()

    def test_end_to_end_iterdump_recovery_applies_rows_to_fresh_db(self, tmp_path, monkeypatch):
        """when ``_maybe_recover_from_corruption`` runs"""
        from voice_typer.server import event_bus
        from voice_typer.server.history_db import HistoryDB

        # Phase 1: create a real DB with rows.
        db_path = tmp_path / "test_history.db"
        db1 = HistoryDB(db_path=db_path)
        db1.add_transcription("hello world", duration=1.0, model="tiny", device="cpu")
        db1.add_transcription("second row", duration=2.0, model="tiny", device="cpu")
        db1.flush()
        db1.checkpoint()
        db1.close()
        assert db_path.exists()
        assert db_path.stat().st_size > 0

        db2 = HistoryDB(db_path=db_path)

        published_events: list[dict] = []
        original_publish = event_bus.publish

        def spy_publish(event):
            published_events.append(event)
            return original_publish(event)

        monkeypatch.setattr(event_bus, "publish", spy_publish)

        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fake_conn.close = MagicMock()

        # Don't patch _open_write_conn, let it open a real fresh DB
        db2.close()
        try:
            result = db2._maybe_recover_from_corruption(fake_conn)

            # A real fresh sqlite3.Connection is returned.
            assert result is not None, "expected a fresh connection"
            assert result is not fake_conn, "expected a real fresh connection, not the fake corrupt conn"

            matching = [e for e in published_events if e.get("type") == "history_corrupted"]
            assert matching, f"history_corrupted event not published; published: {published_events}"
            event = matching[0]
            assert event["data"]["recovered_count"] >= 2, (
                f"expected recovered_count >= 2; got {event['data']['recovered_count']}"
            )
            assert "corrupt-" in event["data"]["path"], (
                f"event.data.path must name the corrupt backup file; got {event['data']['path']!r}"
            )

            # The fresh DB has the recovered rows.
            rows = result.execute("SELECT text FROM transcriptions ORDER BY id").fetchall()
            texts = [r[0] for r in rows]
            assert "hello world" in texts, f"expected 'hello world' in recovered rows; got: {texts}"
            assert "second row" in texts, f"expected 'second row' in recovered rows; got: {texts}"
        finally:
            # Close the returned fresh connection (it's not owned by
            with contextlib_suppress_sqlite_error():
                result.close()
            db2.close()

    def test_iterdump_recovery_skips_schema_meta_and_fts_rows(self, tmp_path):
        """the iterdump filter must NOT include"""
        import sqlite3 as _sqlite3

        from voice_typer.server.history_db import (
            _INSERT_TRANSCRIPTIONS_RE,
        )

        assert _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO \"transcriptions\" VALUES(1, 'text');"), (
            "must match quoted table name"
        )
        assert _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO transcriptions VALUES(1, 'text');"), (
            "must match unquoted table name"
        )
        assert _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO   transcriptions VALUES(1, 'text');"), (
            "must match extra whitespace"
        )
        # Must NOT match schema_meta, sqlite_sequence, FTS5 shadow
        assert not _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO \"schema_meta\" VALUES('version','3');"), (
            "must NOT match schema_meta"
        )
        assert not _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO \"sqlite_sequence\" VALUES('transcriptions',5);"), (
            "must NOT match sqlite_sequence"
        )
        assert not _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO \"transcriptions_fts\" VALUES(1, 'text');"), (
            "must NOT match transcriptions_fts (the FTS5 virtual table)"
        )
        assert not _INSERT_TRANSCRIPTIONS_RE.match("INSERT INTO \"transcriptions_fts_data\" VALUES(1, X'00');"), (
            "must NOT match transcriptions_fts shadow tables"
        )
        assert not _INSERT_TRANSCRIPTIONS_RE.match("CREATE TABLE transcriptions (id INTEGER PRIMARY KEY);"), (
            "must NOT match CREATE statements"
        )
        # Sanity check that sqlite3 is importable in this scope (used
        assert _sqlite3 is not None


def contextlib_suppress_sqlite_error():
    import contextlib
    import sqlite3

    return contextlib.suppress(sqlite3.Error)
