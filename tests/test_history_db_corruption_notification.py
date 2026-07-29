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
