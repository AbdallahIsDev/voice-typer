"""regression tests: the predecessor-era event name renamed to ``notification``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.ipc_test_helpers import make_bare_ipc_server


class TestShowNotificationEventName:
    """``_handle_show_notification`` publishes ``notification``."""

    def test_published_event_type_is_notification(self):
        """A well-formed payload must publish ``type == \"notification\"``."""
        server = make_bare_ipc_server()
        captured: dict = {}
        resp: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {
                    "title": "Hello",
                    "message": "World",
                    "duration_ms": 5000,
                    "critical": True,
                },
                resp,
            )
        assert resp["type"] == "ack", f"handler should ack a well-formed payload, got {resp!r}"
        assert captured.get("type") == "notification", (
            f"event_bus.publish must be called with type='notification' (got {captured.get('type')!r})"
        )

    def test_published_payload_carries_canonical_event_name(self):
        """The published event payload uses the canonical name."""
        server = make_bare_ipc_server()
        captured: dict = {}
        resp: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {
                    "title": "Title",
                    "message": "Body",
                    "duration_ms": 1000,
                    "critical": False,
                },
                resp,
            )
        assert captured["type"] == "notification"
        assert set(captured["data"].keys()) == {
            "title",
            "message",
            "duration_ms",
            "critical",
        }, f"unexpected data keys: {set(captured['data'].keys())!r}"
        assert captured["data"] == {
            "title": "Title",
            "message": "Body",
            "duration_ms": 1000,
            "critical": False,
        }

    def test_default_payload_uses_notification_event_name(self):
        """Empty ``data: {}`` must still publish under ``notification``."""
        server = make_bare_ipc_server()
        captured: dict = {}
        resp: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification({}, resp)
        assert captured["type"] == "notification"
        assert captured["data"]["title"] == "Voice Typer"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False


class TestStartupSequenceCrashNotificationEventName:
    """the crash-recovery startup branch publishes ``notification``."""

    def _make_app_with_crash_summary(self, crash_summary: str):
        """Build a mock VoiceTyperApp sufficient for ``StartupSequence.run``"""
        app = MagicMock()
        app._shutting_down = True  # abort run() right after the crash branch
        mock_event = MagicMock()
        mock_event.is_set.return_value = True
        app._shutting_down_event = mock_event
        app.tray = MagicMock()
        app.tray.notify_safety = MagicMock()
        # Minimal config stub so later phases that read config don't
        app.config = MagicMock()
        app.config.config_dir = MagicMock(return_value=MagicMock())
        return app

    def test_crash_branch_publishes_notification_event(self):
        """
        When ``report_pending_crash`` returns a summary AND the previous
        This pins the rename in the second call site
        """
        from voice_typer.server import startup_sequence

        app = self._make_app_with_crash_summary("heap corruption at 0x...")
        captured: list[dict] = []
        with (
            patch(
                "voice_typer.server.event_bus.publish",
                lambda msg: captured.append(dict(msg)),
            ),
            patch(
                "voice_typer.server.crash_handler.report_pending_crash",
                return_value="heap corruption at 0xdeadbeef",
            ),
            patch(
                "voice_typer.server.session_state.was_previous_session_abnormal",
                return_value=True,
            ),
            patch("voice_typer.server.config._config_dir", return_value=MagicMock()),
        ):
            # Call only the crash-diagnostics phase directly to avoid
            seq = startup_sequence.StartupSequence(app)
            seq._phase_2_crash_diagnostics()

        # The crash branch publishes exactly one event.
        assert len(captured) == 1, f"expected 1 event from crash branch, got {len(captured)}: {captured!r}"
        evt = captured[0]
        assert evt["type"] == "notification", (
            f"startup_sequence crash branch must publish type='notification' (got {evt['type']!r})"
        )
        assert evt["data"]["critical"] is True
        assert evt["data"]["duration_ms"] == 15000
        # CRASH-NOTIFY: the notification carries calm user-facing copy —
        message = evt["data"]["message"]
        assert "didn't close properly" in message
        assert "Settings" in message
        assert "heap corruption" not in message
        assert "python scripts" not in message
        # Clicking the toast opens Settings (Diagnostics), the user's
        assert evt["data"].get("click_path") == "/settings"
        # The tray toast gets the same calm copy (title = app name only).
        app.tray.notify_safety.assert_called_once()
        tray_title, tray_body = app.tray.notify_safety.call_args.args
        assert "Crashed" not in tray_title
        assert tray_body == message

    def test_crash_branch_suppresses_when_previous_session_clean(self):
        """Crash files + a CLEAN previous shutdown (no session marker)"""
        from voice_typer.server import startup_sequence

        app = self._make_app_with_crash_summary("should-not-reach")
        captured: list[dict] = []
        with (
            patch(
                "voice_typer.server.event_bus.publish",
                lambda msg: captured.append(dict(msg)),
            ),
            patch(
                "voice_typer.server.crash_handler.report_pending_crash",
                return_value="heap corruption at 0xdeadbeef",
            ),
            patch(
                "voice_typer.server.session_state.was_previous_session_abnormal",
                return_value=False,
            ),
            patch("voice_typer.server.config._config_dir", return_value=MagicMock()),
        ):
            seq = startup_sequence.StartupSequence(app)
            seq._phase_2_crash_diagnostics()

        assert captured == [], (
            f"crash files from a cleanly-ended previous session must be archived silently, not notified: {captured!r}"
        )
        app.tray.notify_safety.assert_not_called()

    def test_crash_branch_does_not_publish_when_no_crash(self):
        """Sanity: if ``report_pending_crash`` returns ``None`` (no prior"""
        from voice_typer.server import startup_sequence

        app = self._make_app_with_crash_summary("should-not-reach")
        captured: list[dict] = []
        with (
            patch(
                "voice_typer.server.event_bus.publish",
                lambda msg: captured.append(dict(msg)),
            ),
            patch(
                "voice_typer.server.crash_handler.report_pending_crash",
                return_value=None,
            ),
            patch("voice_typer.server.config._config_dir", return_value=MagicMock()),
        ):
            seq = startup_sequence.StartupSequence(app)
            seq._phase_2_crash_diagnostics()

        assert captured == [], f"no notification event should be published when crash_summary is None, got {captured!r}"


class TestNoLegacyEventNameInSource:
    """Static-source guard: the literal ``\"the legacy notification event name\"``"""

    def test_system_handlers_publishes_canonical_event_name(self):
        import inspect

        from voice_typer.server.handlers import system_handlers

        src = inspect.getsource(system_handlers)
        assert '"type": "notification"' in src, "system_handlers.py must publish with type='notification'"

    def test_startup_sequence_publishes_canonical_event_name(self):
        from pathlib import Path

        from voice_typer.server import startup_sequence

        pkg_dir = Path(startup_sequence.__file__).parent
        src = "".join(p.read_text(encoding="utf-8") for p in sorted(pkg_dir.glob("*.py")))
        assert '"type": "notification"' in src, "startup_sequence must publish with type='notification'"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
