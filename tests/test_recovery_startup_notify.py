"""Startup recovery notification reaches the user in Tauri mode.

The recovery store's startup check returns unpasted transcriptions, and
the startup phase must surface them through the tray safety path (which
routes to the Rust-owned native toast under Tauri) plus a notification
event carrying a History deep link. A silent log-only branch leaves the
user unaware of recoverable text.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes


def _make_phase_app(fake_app, unpasted):
    fake_app.config = MagicMock()
    fake_app.config.crash_recovery_enabled = True
    fake_app.config.config_dir = MagicMock(return_value=MagicMock())
    fake_app._crash_recovery = MagicMock()
    fake_app._crash_recovery.check_on_startup.return_value = unpasted
    fake_app.tray = MagicMock()
    fake_app.tray.notify_safety = MagicMock()
    fake_app.history_db = MagicMock()
    fake_app.history_db.apply_retention.return_value = None
    fake_app._thread_registry = MagicMock()
    fake_app._shutting_down = False
    return fake_app


class TestRecoveryStartupNotify:
    def test_unpasted_entries_notify_through_tray_safety(self):
        _, fake_app, _ = make_ipc_server_with_fakes()
        entries = [{"text": "hello", "pasted": False}, {"text": "world", "pasted": False}]
        _make_phase_app(fake_app, entries)
        from voice_typer.server.startup_sequence import StartupSequence

        with (
            patch(
                "voice_typer.server.startup_sequence._phases_early.configure_corrections",
                return_value=None,
            ),
            patch("voice_typer.server.event_bus.publish") as publish,
        ):
            StartupSequence(fake_app)._phase_4_corrections_and_recovery()
        fake_app._crash_recovery.check_on_startup.assert_called_once()
        fake_app.tray.notify_safety.assert_called_once()
        title, body = fake_app.tray.notify_safety.call_args.args
        assert "2" in body
        assert "History" in body
        assert publish.called
        events = [c.args[0] for c in publish.call_args_list]
        notifications = [e for e in events if e.get("type") == "notification"]
        assert notifications, "recovery must publish a notification event"
        data = notifications[0]["data"]
        assert "2" in data["message"]
        assert data.get("click_path") == "/history"

    def test_no_unpasted_entries_stays_silent(self):
        _, fake_app, _ = make_ipc_server_with_fakes()
        _make_phase_app(fake_app, None)
        from voice_typer.server.startup_sequence import StartupSequence

        with (
            patch(
                "voice_typer.server.startup_sequence._phases_early.configure_corrections",
                return_value=None,
            ),
            patch("voice_typer.server.event_bus.publish") as publish,
        ):
            StartupSequence(fake_app)._phase_4_corrections_and_recovery()
        fake_app.tray.notify_safety.assert_not_called()
        events = [c.args[0] for c in publish.call_args_list]
        assert [e for e in events if e.get("type") == "notification"] == []

    def test_recovery_notice_uses_tauri_notification_event(self):
        _, fake_app, _ = make_ipc_server_with_fakes()
        _make_phase_app(fake_app, [{"text": "x", "pasted": False}])
        from voice_typer.server.startup_sequence import StartupSequence

        with (
            patch(
                "voice_typer.server.startup_sequence._phases_early.configure_corrections",
                return_value=None,
            ),
            patch("voice_typer.server.event_bus.publish") as publish,
            patch.dict("os.environ", {"TAURI_SIDECAR": "1"}),
        ):
            import os

            os.environ["TAURI_SIDECAR"] = "1"
            try:
                StartupSequence(fake_app)._phase_4_corrections_and_recovery()
            finally:
                del os.environ["TAURI_SIDECAR"]
        events = [c.args[0] for c in publish.call_args_list]
        notifications = [e for e in events if e.get("type") == "notification"]
        assert notifications
        assert notifications[0]["type"] != "electron_notification"
