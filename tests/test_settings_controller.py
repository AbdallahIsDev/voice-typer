"""RW-9 regression tests for the ``SettingsController`` extraction.

The four settings-side-effect methods (``_toggle_autostart``,
``_set_autostart``, ``_set_notifications``, ``_select_microphone``)
were extracted from ``VoiceTyperApp`` to
``voice_typer/server/settings_controller.py``. ``VoiceTyperApp`` keeps
thin delegate methods so tray menu callbacks (and tests calling
``app._select_microphone`` directly) keep working unchanged.

These tests pin the contract of the extraction:

1. ``SettingsController`` is wired into ``VoiceTyperApp.__init__`` as
   ``self.settings``.
2. Each delegate method on ``VoiceTyperApp`` calls the corresponding
   ``SettingsController`` method.
3. ``SettingsController.set_autostart`` reads
   ``enable_autostart`` / ``disable_autostart`` dynamically from
   ``voice_typer.server.app`` (so the existing monkeypatch pattern in
   ``tests/test_app.py:app`` fixture keeps working).
4. ``SettingsController.select_microphone`` recreates ``app.recorder``
   when no recording is in progress, but defers when recording is
   active (preserves pre-extraction behaviour).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_for_settings(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies for settings tests.

    Mirrors the ``app`` fixture in ``tests/test_app.py`` but kept local
    so this file is self-contained.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    instance.models._sync_registry_from_fields()
    return instance


class TestSettingsControllerWiring:
    """Verify VoiceTyperApp.__init__ wires up SettingsController."""

    def test_app_has_settings_attribute(self, app_for_settings):
        """``self.settings`` must be a ``SettingsController`` instance."""
        from voice_typer.server.settings_controller import SettingsController

        assert hasattr(app_for_settings, "settings"), (
            "VoiceTyperApp.__init__ must construct self.settings (SettingsController)"
        )
        assert isinstance(app_for_settings.settings, SettingsController), (
            "self.settings must be a SettingsController instance"
        )

    def test_settings_controller_back_references_app(self, app_for_settings):
        """SettingsController must hold a back-reference to the app.

        RW-9 Phase 6 contract: the controller reads/writes app state via
        ``self._app.config`` / ``self._app.tray`` / ``self._app.recorder``
        — same attribute surface the original ``VoiceTyperApp`` methods
        used via ``self``.
        """
        assert app_for_settings.settings._app is app_for_settings, (
            "SettingsController._app must be the VoiceTyperApp instance that "
            "constructed it (back-reference for state access)"
        )


class TestSettingsControllerDelegates:
    """Each VoiceTyperApp delegate method must call the corresponding
    SettingsController method — no inline logic should remain on the app."""

    def test_toggle_autostart_delegates(self, app_for_settings, monkeypatch):
        called = []
        monkeypatch.setattr(app_for_settings.settings, "toggle_autostart", lambda: called.append(True))
        app_for_settings._toggle_autostart()
        assert called == [True], "_toggle_autostart must delegate to SettingsController.toggle_autostart"

    def test_set_autostart_delegates(self, app_for_settings, monkeypatch):
        captured = []
        monkeypatch.setattr(
            app_for_settings.settings,
            "set_autostart",
            lambda enabled: captured.append(enabled),
        )
        app_for_settings._set_autostart(True)
        app_for_settings._set_autostart(False)
        assert captured == [True, False], "_set_autostart must delegate to SettingsController.set_autostart"

    def test_set_notifications_delegates(self, app_for_settings, monkeypatch):
        captured = []
        monkeypatch.setattr(
            app_for_settings.settings,
            "set_notifications",
            lambda enabled: captured.append(enabled),
        )
        app_for_settings._set_notifications(True)
        app_for_settings._set_notifications(False)
        assert captured == [True, False], "_set_notifications must delegate to SettingsController.set_notifications"

    def test_select_microphone_delegates(self, app_for_settings, monkeypatch):
        captured = []
        monkeypatch.setattr(
            app_for_settings.settings,
            "select_microphone",
            lambda mic_name: captured.append(mic_name),
        )
        app_for_settings._select_microphone("mic-1")
        app_for_settings._select_microphone(None)
        assert captured == ["mic-1", None], "_select_microphone must delegate to SettingsController.select_microphone"


class TestSettingsControllerSetAutostart:
    """``SettingsController.set_autostart`` must read platform helpers
    dynamically from ``voice_typer.server.app`` so the existing
    monkeypatch pattern in tests/test_app.py:app fixture keeps working."""

    def test_set_autostart_true_enables_and_saves(self, app_for_settings, monkeypatch):
        enable_called = []
        disable_called = []

        # Re-monkeypatch the platform helpers (the app fixture already
        # patches them; here we use recorders to verify the controller
        # actually calls them).
        monkeypatch.setattr(
            "voice_typer.server.app.enable_autostart",
            lambda: enable_called.append(True),
        )
        monkeypatch.setattr(
            "voice_typer.server.app.disable_autostart",
            lambda: disable_called.append(True),
        )
        # Replace tray with a MagicMock so we can assert call counts.
        app_for_settings.tray = MagicMock()

        app_for_settings.settings.set_autostart(True)

        assert enable_called == [True], "set_autostart(True) must call enable_autostart()"
        assert disable_called == [], "set_autostart(True) must NOT call disable_autostart()"
        assert app_for_settings.config.autostart is True
        app_for_settings.tray.set_autostart_enabled.assert_called_once_with(True)

    def test_set_autostart_false_disables_and_saves(self, app_for_settings, monkeypatch):
        enable_called = []
        disable_called = []

        monkeypatch.setattr(
            "voice_typer.server.app.enable_autostart",
            lambda: enable_called.append(True),
        )
        monkeypatch.setattr(
            "voice_typer.server.app.disable_autostart",
            lambda: disable_called.append(True),
        )
        app_for_settings.tray = MagicMock()

        app_for_settings.settings.set_autostart(False)

        assert disable_called == [True], "set_autostart(False) must call disable_autostart()"
        assert enable_called == [], "set_autostart(False) must NOT call enable_autostart()"
        assert app_for_settings.config.autostart is False
        app_for_settings.tray.set_autostart_enabled.assert_called_once_with(False)

    def test_set_autostart_handles_exception_via_tray_notify(self, app_for_settings, monkeypatch):
        """If enable_autostart raises, set_autostart must NOT re-raise —
        it must log + notify the user via the tray."""

        def _boom():
            raise RuntimeError("permission denied")

        monkeypatch.setattr("voice_typer.server.app.enable_autostart", _boom)
        app_for_settings.tray = MagicMock()

        # Must not raise
        app_for_settings.settings.set_autostart(True)

        # The user must be notified
        app_for_settings.tray.notify.assert_called_once()
        args = app_for_settings.tray.notify.call_args
        assert "autostart" in str(args).lower(), (
            "Failure notification must mention autostart so the user knows what failed"
        )


class TestSettingsControllerSetNotifications:
    """``SettingsController.set_notifications`` updates config + tray."""

    def test_set_notifications_persists_and_updates_tray(self, app_for_settings):
        app_for_settings.tray = MagicMock()

        app_for_settings.settings.set_notifications(True)

        assert app_for_settings.config.show_notifications is True
        app_for_settings.tray.set_notifications_enabled.assert_called_once_with(True)

    def test_set_notifications_false_propagates(self, app_for_settings):
        app_for_settings.config.show_notifications = True
        app_for_settings.tray = MagicMock()

        app_for_settings.settings.set_notifications(False)

        assert app_for_settings.config.show_notifications is False
        app_for_settings.tray.set_notifications_enabled.assert_called_once_with(False)


class TestSettingsControllerSelectMicrophone:
    """``SettingsController.select_microphone`` updates config + recorder.

    Behaviour:
      - If a recording is in progress: do NOT recreate the recorder
        (would truncate the in-flight audio). Notify the user the
        change applies on the next recording.
      - If no recording: recreate the Recorder with the new mic config.
    """

    def test_select_microphone_updates_config(self, app_for_settings):
        app_for_settings.recorder = MagicMock()
        app_for_settings.recorder.recording = False

        app_for_settings.settings.select_microphone("mic-3")

        assert app_for_settings.config.microphone == "mic-3"

    def test_select_microphone_none_resets_to_default(self, app_for_settings):
        app_for_settings.config.microphone = "old-mic"
        app_for_settings.recorder = MagicMock()
        app_for_settings.recorder.recording = False

        app_for_settings.settings.select_microphone(None)

        assert app_for_settings.config.microphone is None

    def test_select_microphone_recreates_recorder_when_not_recording(self, app_for_settings, monkeypatch):
        app_for_settings.recorder = MagicMock()
        app_for_settings.recorder.recording = False
        old_recorder = app_for_settings.recorder

        # Stub Recorder so we can capture the constructor call without
        # actually constructing a real one (which needs sounddevice).
        captured_kwargs = []

        from voice_typer.server import settings_controller as sc_mod

        class _FakeRecorder:
            def __init__(self, config, audio_processor=None):
                captured_kwargs.append(
                    {
                        "config": config,
                        "audio_processor": audio_processor,
                    }
                )

        monkeypatch.setattr(sc_mod, "Recorder", _FakeRecorder)

        app_for_settings.settings.select_microphone("mic-1")

        assert len(captured_kwargs) == 1, "Recorder must be recreated exactly once"
        assert captured_kwargs[0]["config"] is app_for_settings.config
        assert captured_kwargs[0]["audio_processor"] is app_for_settings._audio_processor
        assert app_for_settings.recorder is not old_recorder, (
            "app.recorder must be replaced with the new Recorder instance"
        )

    def test_select_microphone_defers_recorder_recreation_when_recording(self, app_for_settings, monkeypatch):
        """If recording is in progress, do NOT recreate the recorder."""
        app_for_settings.recorder = MagicMock()
        app_for_settings.recorder.recording = True
        original_recorder = app_for_settings.recorder

        # If the controller tries to recreate, this would be called.
        from voice_typer.server import settings_controller as sc_mod

        recorder_constructor_called = []

        class _FakeRecorder:
            def __init__(self, *args, **kwargs):
                recorder_constructor_called.append(True)

        monkeypatch.setattr(sc_mod, "Recorder", _FakeRecorder)

        app_for_settings.settings.select_microphone("mic-2")

        assert recorder_constructor_called == [], (
            "select_microphone must NOT recreate Recorder when recording is active — "
            "the in-flight audio would be truncated."
        )
        assert app_for_settings.recorder is original_recorder, "app.recorder must be unchanged when recording is active"
        assert app_for_settings.config.microphone == "mic-2", (
            "config.microphone must still be updated (takes effect next recording)"
        )


class TestSettingsControllerToggleAutostart:
    """``SettingsController.toggle_autostart`` reads
    ``is_autostart_enabled()`` dynamically from ``voice_typer.server.app``
    and delegates to ``set_autostart``."""

    def test_toggle_when_disabled_enables(self, app_for_settings, monkeypatch):
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        captured = []
        monkeypatch.setattr(
            app_for_settings.settings,
            "set_autostart",
            lambda enabled: captured.append(enabled),
        )

        app_for_settings.settings.toggle_autostart()

        assert captured == [True], "toggle_autostart when disabled must call set_autostart(True)"

    def test_toggle_when_enabled_disables(self, app_for_settings, monkeypatch):
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: True)
        captured = []
        monkeypatch.setattr(
            app_for_settings.settings,
            "set_autostart",
            lambda enabled: captured.append(enabled),
        )

        app_for_settings.settings.toggle_autostart()

        assert captured == [False], "toggle_autostart when enabled must call set_autostart(False)"
