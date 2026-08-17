"""CR-8 regression tests: ``electron_notification`` event renamed to ``notification``.

The Python sidecar used to publish the toast/notification event under the
name ``electron_notification`` (a leftover from the Electron-only era). The
Tauri Rust host then renamed it to ``notification`` via a single ``match``
arm with no fallback. CR-8 fixes the naming inconsistency at the source:
Python now publishes under the platform-agnostic ``notification`` name
directly, and the Rust-side rename was removed (with a backward-compat
alias for rolling upgrades — see ``src-tauri/src/main.rs`` +
``docs/migration/tauri-sidecar-bridge.md``).

These tests pin the new contract:
- ``_handle_show_electron_notification`` (system_handlers.py) MUST publish
  with ``type == "notification"``.
- ``StartupSequence.run()`` crash-recovery branch (startup_sequence.py)
  MUST publish with ``type == "notification"`` when a crash summary exists.
- The legacy ``"electron_notification"`` string MUST NOT appear in the
  published event payloads of either path.

The tests are HEADLESS: they construct a minimal ``IPCServer`` via
``__new__`` (mirroring ``tests/test_bugfix_regressions.py::
TestElectronNotificationFieldValidation._make_server``) and patch
``event_bus.publish`` to capture the published event without needing a
real ``VoiceTyperApp`` or tray. ``StartupSequence`` is invoked with a
mock app + mocked ``crash_handler.report_pending_crash``.
"""

from __future__ import annotations

from threading import RLock
from unittest.mock import MagicMock, patch

import pytest

# ─── helpers ──────────────────────────────────────────────────────────────


def _make_ipc_server():
    """Build a minimal IPCServer with a mock app + service.

    Mirrors ``tests/test_bugfix_regressions.py::
    TestElectronNotificationFieldValidation._make_server`` — same
    surface (``app``, ``service``, ``_config_mutation_lock``) so the
    handler mixin can run its validation + publish path without a real
    VoiceTyperApp.
    """
    from voice_typer.server.ipc_server import IPCServer

    app = MagicMock()
    app._config_mutation_lock = RLock()
    server = IPCServer.__new__(IPCServer)
    server.app = app
    server.service = MagicMock()
    return server


# ─── system_handlers._handle_show_electron_notification ───────────────────


class TestShowNotificationEventName:
    """``_handle_show_electron_notification`` publishes ``notification``.

    NOTE: ``show_electron_notification`` was deliberately de-registered
    from ``_COMMAND_REGISTRY`` (it is not in the TS / Rust renderer
    allowlists) — the Python-side handler is retained for direct tests
    per the CHANGELOG convention ("The Python-side ``_handle_*`` methods
    are retained (tests still call them directly)"). These tests
    therefore invoke the handler directly rather than routing through
    ``IPCServer._dispatch``, which would return an unknown-command
    error.
    """

    def test_published_event_type_is_notification(self):
        """A well-formed payload must publish ``type == "notification"``.

        This is the core CR-8 assertion: the event name on the wire is
        the platform-agnostic ``notification``, NOT the legacy
        ``electron_notification``. The Tauri Rust host no longer renames
        the event (see ``src-tauri/src/main.rs``), so this is the
        canonical name that reaches the renderer on both the Electron
        and Tauri paths.
        """
        server = _make_ipc_server()
        captured: dict = {}
        resp: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
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
        # the legacy name must NOT appear in the published event.
        assert captured.get("type") != "electron_notification", "legacy 'electron_notification' name must not be used"

    def test_published_payload_carries_no_legacy_event_name(self):
        """The legacy ``electron_notification`` string must NOT appear
        anywhere in the published event payload (type or data).

        Belt-and-braces check: even if a future caller passes a
        ``title`` or ``message`` that contains the legacy string, the
        ``type`` field is still ``notification``. We assert both the
        ``type`` and that the data dict has the expected 4 fields
        (no extra ``legacy_event`` / ``original_event_name`` leakage).
        """
        server = _make_ipc_server()
        captured: dict = {}
        resp: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
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
        server = _make_ipc_server()
        captured: dict = {}
        resp: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification({}, resp)
        assert captured["type"] == "notification"
        assert captured["data"]["title"] == "Voice Typer"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False


# ─── startup_sequence.StartupSequence.run() crash branch ──────────────────


class TestStartupSequenceCrashNotificationEventName:
    """the crash-recovery startup branch publishes ``notification``."""

    def _make_app_with_crash_summary(self, crash_summary: str):
        """Build a mock VoiceTyperApp sufficient for ``StartupSequence.run``
        to reach the crash-notification publish branch.

        The branch is gated on ``crash_summary`` being truthy (returned
        by ``crash_handler.report_pending_crash``) AND the previous
        session having ended abnormally (``session_state.
        was_previous_session_abnormal`` — pinned in ``session_state.py``).
        We patch those so we don't depend on a real crash file or session
        marker existing on disk. We also stub ``app.tray.notify_safety``
        (best-effort tray toast) and ``app._shutting_down`` (so the
        sequence aborts right after the crash branch — we don't need to
        run the rest of startup).
        """
        app = MagicMock()
        app._shutting_down = True  # abort run() right after the crash branch
        app.tray = MagicMock()
        app.tray.notify_safety = MagicMock()
        return app

    def test_crash_branch_publishes_notification_event(self):
        """When ``report_pending_crash`` returns a summary AND the previous
        session ended abnormally (session marker survived),
        ``StartupSequence.run()`` must publish ``type == "notification"``
        with calm user-facing copy (no technical crash details).

        This pins the rename in the second call site
        (``startup_sequence.py``). The original code published
        ``electron_notification``; CR-8 renamed it to ``notification``.
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
        ):
            startup_sequence.StartupSequence(app).run()

        # The crash branch publishes exactly one event.
        assert len(captured) == 1, f"expected 1 event from crash branch, got {len(captured)}: {captured!r}"
        evt = captured[0]
        assert evt["type"] == "notification", (
            f"startup_sequence crash branch must publish type='notification' (got {evt['type']!r})"
        )
        assert evt["type"] != "electron_notification", "legacy 'electron_notification' name must not be used"
        assert evt["data"]["critical"] is True
        assert evt["data"]["duration_ms"] == 15000
        # CRASH-NOTIFY: the notification carries calm user-facing copy —
        # never the raw crash summary / technical details.
        message = evt["data"]["message"]
        assert "didn't close properly" in message
        assert "Settings" in message
        assert "heap corruption" not in message
        assert "python scripts" not in message
        # Clicking the toast opens Settings (Diagnostics) — the user's
        # clear next action, no terminal required.
        assert evt["data"].get("click_path") == "/settings"
        # The tray toast gets the same calm copy (title = app name only).
        app.tray.notify_safety.assert_called_once()
        tray_title, tray_body = app.tray.notify_safety.call_args.args
        assert "Crashed" not in tray_title
        assert tray_body == message

    def test_crash_branch_suppresses_when_previous_session_clean(self):
        """Crash files + a CLEAN previous shutdown (no session marker)
        must NOT publish a notification — teardown-noise ``python_crash``
        markers from a clean quit / backend restart are not crashes.
        This is the core false-positive fix.
        """
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
        ):
            startup_sequence.StartupSequence(app).run()

        assert captured == [], (
            "crash files from a cleanly-ended previous session must be "
            f"archived silently, not notified: {captured!r}"
        )
        app.tray.notify_safety.assert_not_called()

    def test_crash_branch_does_not_publish_when_no_crash(self):
        """Sanity: if ``report_pending_crash`` returns ``None`` (no prior
        crash), the startup sequence must NOT publish any notification
        event. Guards against accidentally always-publishing.
        """
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
        ):
            startup_sequence.StartupSequence(app).run()

        assert captured == [], f"no notification event should be published when crash_summary is None, got {captured!r}"


# ─── source-string guard (cheap refactor-resistance) ─────────────────────


class TestNoLegacyEventNameInSource:
    """Static-source guard: the literal ``"electron_notification"``
    string MUST NOT appear in the published-event-type position of
    ``system_handlers.py`` or ``startup_sequence.py``.

    This catches accidental reintroduction during a future refactor
    (e.g. someone copies the old name from a stale diff). It's a
    complement to the behavioral tests above — the behavioral tests
    pin the runtime contract; this pins the source-level intent.

    NOTE: the literal ``electron_notification`` MAY still appear in:
      - ``system_handlers.py`` docstrings/comments (referencing the
        legacy name for historical context),
      - ``ipc_server.py::_COMMAND_REGISTRY`` as the COMMAND name
        ``show_electron_notification`` (a different namespace — the
        command the renderer invokes, NOT the event the server emits),
      - ADR / docs / migration notes.
    We only forbid it as a ``"type"`` value in the publish call.
    """

    def test_system_handlers_does_not_publish_legacy_event_name(self):
        import inspect

        from voice_typer.server.handlers import system_handlers

        src = inspect.getsource(system_handlers)
        # The publish call site must use the new name.
        assert '"type": "notification"' in src, "system_handlers.py must publish with type='notification'"
        # The legacy name must NOT be used as the event type. Allow it
        # in comments/docstrings (prefixed by # or inside triple-quotes),
        # but forbid ``"type": "electron_notification"`` outright.
        assert '"type": "electron_notification"' not in src, (
            "system_handlers.py must not publish with the legacy 'electron_notification' event name"
        )

    def test_startup_sequence_does_not_publish_legacy_event_name(self):
        import inspect

        from voice_typer.server import startup_sequence

        src = inspect.getsource(startup_sequence)
        assert '"type": "notification"' in src, "startup_sequence.py must publish with type='notification'"
        assert '"type": "electron_notification"' not in src, (
            "startup_sequence.py must not publish with the legacy 'electron_notification' event name"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
