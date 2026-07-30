"""FR-29: regression tests for the user-facing notification emitted by
``history_db._maybe_recover_from_corruption`` after it renames a
corrupt DB file.

The previous implementation logged at WARNING with only the destination
filename — no event_bus publication, no tray notification. The user
only discovered the loss when they next opened the History page and saw
it empty.

The fix:
  - logs at WARNING with a clear user-facing message that names the
    backup file's location,
  - publishes a ``history_corrupted`` event via ``event_bus.publish``
    so the renderer can show a toast/notification, and
  - attempts a tray notification via ``self._app.tray.notify`` if an
    app reference is wired in (best-effort).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _force_corruption(db) -> None:
    """Make ``PRAGMA quick_check`` return a non-ok result so the
    recovery path triggers.

    We monkeypatch the connection's ``execute`` to return a fake
    corrupt-check result, then call ``_maybe_recover_from_corruption``
    directly.
    """
    # Close the writer's connection so we can rename the file.
    # We'll pass a mock connection to _maybe_recover_from_corruption.
    pass


class TestCorruptionNotification:
    """FR-29: corruption recovery publishes a ``history_corrupted`` event
    and (best-effort) calls ``tray.notify``."""

    def test_publishes_history_corrupted_event(self, db, monkeypatch):
        """When corruption is detected, a ``history_corrupted`` event
        must be published via ``event_bus.publish``."""
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
        # fresh DB (we just want to verify the event is published before
        # the reopen). The returned connection's quick_check is also
        # mocked so init_schema's recursive call returns "ok".
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        # Act.
        db._maybe_recover_from_corruption(fake_conn)

        # Assert: the history_corrupted event was published with the
        # corrupt-file path.
        matching = [e for e in published_events if e.get("type") == "history_corrupted"]
        assert matching, (
            "FR-29 violation: _maybe_recover_from_corruption did not publish a "
            f"history_corrupted event. Published: {published_events}"
        )
        event = matching[0]
        assert "path" in event.get("data", {}), "event.data must include 'path'"
        assert "corrupt-" in event["data"]["path"], "event.data.path must name the corrupt backup file"

    def test_calls_tray_notify_when_app_ref_wired(self, db, monkeypatch):
        """When ``self._app.tray.notify`` is available, the recovery
        path must call it with a user-facing message."""
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
        """When ``self._app`` is not set (early init), the recovery path
        must still succeed — only the event_bus publication fires."""
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
        """A WARNING-level log with a user-facing message must be
        emitted (so users grepping the log can find the backup file)."""
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fresh_conn = MagicMock()
        fresh_conn.execute.return_value.fetchall.return_value = [("ok",)]
        monkeypatch.setattr(db, "_open_write_conn", lambda: fresh_conn)
        monkeypatch.setattr(db, "_check_wal_mode", lambda conn: None)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db"):
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
        """If ``event_bus.publish`` raises, the recovery must still
        return a fresh connection (best-effort notification)."""
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
        """S5-CR-61: the ``history_corrupted`` event payload must
        include a ``recovered_count`` field (in addition to the
        FR-29 ``path`` field checked by the test above)."""
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
        """S5-CR-61: when ``iterdump()`` fails (severe corruption),
        the rename + fresh-DB fallback still runs and an event with
        ``recovered_count=0`` is published."""
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

        # iterdump recovery was attempted.
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
    """S5-CR-61: end-to-end tests for the ``iterdump()`` data-recovery
    path that extracts user-data INSERTs from a corrupt DB and replays
    them on the fresh DB."""

    def test_try_iterdump_recovery_extracts_inserts_from_real_db(self, tmp_path):
        """``_try_iterdump_recovery`` opens a real (structurally-valid)
        DB file read-only and returns the ``INSERT INTO transcriptions``
        statements, filtering out schema rows and FTS5 shadow-table
        rows."""
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
        # main DB file.
        corrupt_main = tmp_path / "test_history.db.corrupt-1700000000"
        db_path.rename(corrupt_main)

        # Use a fresh HistoryDB instance to call the method (the
        # method is on the instance, not the class, because it reads
        # ``self`` for logging context).
        fresh_db = HistoryDB(db_path=tmp_path / "fresh.db")
        try:
            inserts = fresh_db._try_iterdump_recovery(corrupt_main)

            # At least 2 INSERT statements (one per row).
            assert len(inserts) >= 2, f"expected >=2 INSERTs, got {len(inserts)}: {inserts}"

            # All statements must be INSERT INTO transcriptions (no
            # schema_meta, no transcriptions_fts, no sqlite_sequence).
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
        """When the corrupt file is not a valid SQLite database (e.g.
        garbage bytes), ``_try_iterdump_recovery`` returns an empty
        list rather than raising."""
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
        """When the corrupt file doesn't exist (e.g. the rename
        failed silently), ``_try_iterdump_recovery`` returns an empty
        list rather than raising."""
        from voice_typer.server.history_db import HistoryDB

        missing_path = tmp_path / "does_not_exist.db.corrupt-1"

        fresh_db = HistoryDB(db_path=tmp_path / "fresh.db")
        try:
            inserts = fresh_db._try_iterdump_recovery(missing_path)
            assert inserts == []
        finally:
            fresh_db.close()

    def test_end_to_end_iterdump_recovery_applies_rows_to_fresh_db(self, tmp_path, monkeypatch):
        """S5-CR-61: when ``_maybe_recover_from_corruption`` runs
        against a real corrupt-renamed DB file, the recovered INSERTs
        are applied to the fresh DB and ``recovered_count`` reflects
        the actual row count."""
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

        # Phase 2: open a new HistoryDB on the same path. The writer
        # thread opens the existing DB and runs init_schema (which
        # passes quick_check because the DB is structurally valid).
        # We then directly invoke _maybe_recover_from_corruption with
        # a fake conn whose quick_check returns a corrupt result —
        # this triggers the rename + iterdump + fresh-DB path.
        db2 = HistoryDB(db_path=db_path)

        published_events: list[dict] = []
        original_publish = event_bus.publish

        def spy_publish(event):
            published_events.append(event)
            return original_publish(event)

        monkeypatch.setattr(event_bus, "publish", spy_publish)

        # fake_conn simulates a connection whose quick_check fails.
        # The real DB file at db_path is intact, so iterdump can
        # extract the rows.
        fake_conn = MagicMock()
        fake_conn.execute.return_value.fetchall.return_value = [("integrity check failed",)]
        fake_conn.close = MagicMock()

        # Don't patch _open_write_conn — let it open a real fresh DB
        # after the rename moves the existing db_path aside.
        try:
            result = db2._maybe_recover_from_corruption(fake_conn)

            # A real fresh sqlite3.Connection is returned.
            assert result is not None, "expected a fresh connection"
            assert result is not fake_conn, "expected a real fresh connection, not the fake corrupt conn"

            # The history_corrupted event was published with
            # recovered_count >= 2.
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
            # db2's writer thread — the writer thread still has its
            # own connection to the OLD renamed file).
            with contextlib_suppress_sqlite_error():
                result.close()
            db2.close()

    def test_iterdump_recovery_skips_schema_meta_and_fts_rows(self, tmp_path):
        """S5-CR-61: the iterdump filter must NOT include
        ``schema_meta`` or ``transcriptions_fts`` rows (replaying
        schema_meta would PRIMARY KEY-conflict with the version row
        init_schema writes; FTS5 rows are auto-populated by the
        AFTER-INSERT trigger)."""
        import sqlite3 as _sqlite3

        from voice_typer.server.history_db import (
            _INSERT_TRANSCRIPTIONS_RE,
        )

        # Verify the regex against the canonical iterdump output
        # formats. (This is a unit test on the regex itself, but it
        # documents the contract that _try_iterdump_recovery relies
        # on.)
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
        # tables, or CREATE statements.
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
        # by the import above to ensure the test fails fast if the
        # module-level import path changes).
        assert _sqlite3 is not None


# Helper used by the end-to-end test above (kept at module scope so
# pytest doesn't try to collect it as a test — its name doesn't start
# with ``test_``).
def contextlib_suppress_sqlite_error():
    import contextlib
    import sqlite3

    return contextlib.suppress(sqlite3.Error)
