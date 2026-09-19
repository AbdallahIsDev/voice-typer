"""All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``"""

import contextlib
import json
import sys
import time
from unittest.mock import MagicMock

import numpy as np


def _wait_for_busy_clear(app, timeout=2.0):
    """Poll until app._busy_event is set (not busy)."""
    deadline = time.monotonic() + timeout
    while not app._busy_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not app._busy_event.is_set():
        raise TimeoutError(f"_busy_event still not set after {timeout}s")


class TestConfigWiring:
    def test_paste_on_stop_preserves_user_value(self, tmp_config_dir, monkeypatch):
        """After override removal, user's paste_on_stop=False must be preserved."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"paste_on_stop": False}))

        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        assert app.config.paste_on_stop is False
        assert app.clipboard.paste_enabled is False

    def test_streaming_preserves_user_value(self, tmp_config_dir, monkeypatch):
        """After override removal, user's streaming_transcription=False must be preserved."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"streaming_transcription": False}))

        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        assert app.config.streaming_transcription is False

    def test_transcription_speed_settings_wired(self, tmp_config_dir, monkeypatch):
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "beam_size": 2,
                    "best_of": 2,
                    "condition_on_previous_text": True,
                }
            )
        )

        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        transcriber_cls = MagicMock()
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", transcriber_cls)

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # TranscriptionEngine is now created in _do_startup (background), not __init__
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        app.hotkeys.register = MagicMock()
        app.models.try_load = MagicMock()
        # The model-not-downloaded precheck would refuse the load before
        app.models._model_downloaded_precheck = lambda: True
        app._do_startup()
        load_thread = app.models._model_load_thread
        if load_thread is not None:
            load_thread.join(timeout=5)
            assert not load_thread.is_alive(), "model load thread hung"

        _, kwargs = transcriber_cls.call_args
        assert kwargs["beam_size"] == 2
        assert kwargs["best_of"] == 2
        assert kwargs["condition_on_previous_text"] is True

    def test_autostart_syncs_with_platform(self, tmp_config_dir, monkeypatch):
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"autostart": True}))

        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        called = []
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart.enable_autostart",
            lambda: called.append(True) or True,
        )
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server import startup_tasks
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        startup_tasks.sync_autostart(app)

        assert len(called) == 1  # enable_autostart was called

    def test_autostart_disabled_when_config_false(self, tmp_config_dir, monkeypatch):
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"autostart": False}))

        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        called = []
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart.disable_autostart",
            lambda: called.append(True) or True,
        )
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server import startup_tasks
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        startup_tasks.sync_autostart(app)

        assert len(called) == 1  # disable_autostart was called


class TestTextCleanupConfig:
    """Test that text cleanup can be disabled via config."""

    def test_cleanup_skipped_when_disabled(self, app, monkeypatch):
        """When config.text_cleanup_enabled=False, should not call cleanup."""
        from voice_typer.server import text_cleanup as tc_mod

        original = tc_mod.clean_transcribed_text
        called = False

        def spy(text, **kw):
            nonlocal called
            called = True
            return original(text)

        app.config.text_cleanup_enabled = False
        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.clipboard.paste = MagicMock(return_value=False)
        app.models.transcriber = MagicMock()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="hello world")
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app.recorder.last_rms = 0.5

        monkeypatch.setattr("voice_typer.server.text_cleanup.clean_transcribed_text", spy)
        app._stop_dictation()
        _wait_for_busy_clear(app)
        assert not called, "clean_transcribed_text should NOT be called when disabled"

    def test_cleanup_applied_when_enabled(self):
        from voice_typer.server.text_cleanup import clean_transcribed_text

        text = "this is a test of the cleanup"
        result = clean_transcribed_text(text)
        # Cleanup applies capitalization and other transforms
        assert result == "This is a test of the cleanup"

    def test_cleanup_applied_when_enabled_streaming(self):
        from voice_typer.server.text_cleanup import clean_transcribed_text

        text = "this is a test of the cleanup"
        result = clean_transcribed_text(text)
        # Cleanup applies capitalization and other transforms
        assert result == "This is a test of the cleanup"


class TestExternalCorrectionsWiring:
    """Verify configure_corrections is called at startup."""

    def test_configure_corrections_called_at_startup(self, app, monkeypatch):
        """StartupSequence.run should call configure_corrections with config_dir."""
        called_with = {}

        def spy(config_dir=None, corrections_path=None):
            called_with["config_dir"] = config_dir

        monkeypatch.setattr("voice_typer.server.startup_sequence._phases_early.configure_corrections", spy)
        app._settings_window = None
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        # Prevent the background model loader from doing real work, we
        app.models.load_background = MagicMock()
        app._do_startup()
        assert called_with.get("config_dir") == app.config.config_dir, (
            f"configure_corrections should receive config_dir={app.config.config_dir}, "
            f"got {called_with.get('config_dir')}"
        )


class TestSettingsWindowIntegration:
    # ARCH-DEAD-SETTINGS: the show_settings / SettingsWindow tests were

    def test_restart_hotkey_stops_existing_backend_and_registers_new_one(self, app, monkeypatch):
        old_backend = MagicMock()
        app.hotkeys._hotkey_backend = old_backend
        # #2 _register_hotkey now delegates to HotkeyDispatcher.register().
        new_backend = MagicMock()
        new_backend.is_alive.return_value = True
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            MagicMock(return_value=new_backend),
        )
        app.hotkeys.register = MagicMock()

        app.hotkeys.restart("<f3>")

        assert app.config.hotkey == "<f3>"
        old_backend.stop.assert_called_once()
        import voice_typer.server.hotkey_dispatcher as _hd

        _hd.create_hotkey_backend.assert_called_with("<f3>", role="dictation")
        assert app.hotkeys._hotkey_backend is new_backend

    def test_restart_app_does_not_spawn_subprocess(self, app, monkeypatch):
        """restart_app() must NOT spawn a replacement"""
        import subprocess as _sp

        # Pre-warm the keyring probe cache so ``app.config.save()``
        from voice_typer.server import credential_store

        with contextlib.suppress(Exception):
            credential_store.is_keyring_available()

        popen_calls = []
        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: popen_calls.append((a, kw)))
        monkeypatch.setattr("os._exit", lambda code: None)
        monkeypatch.setattr(sys, "argv", ["voice_typer", "--port", "9876"])
        monkeypatch.setattr("time.sleep", lambda s: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()
        # Stub _push_event_now so restart_app's TCP push doesn't blow up
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: None,
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        replacement_spawns = [
            (args, kwargs)
            for args, kwargs in popen_calls
            if not (args and args[0] and args[0][0] in ("icacls", "lscpu"))
        ]
        assert replacement_spawns == [], (
            f"restart_app must NOT spawn a replacement backend/host subprocess (port-race); got: {replacement_spawns}"
        )

    def test_restart_app_pushes_restart_ack_event(self, app, monkeypatch):
        """restart_app() must push a ``relaunch_app``"""
        monkeypatch.setattr("os._exit", lambda code: None)
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert pushed, "restart_app must push at least one event"
        assert any(m.get("type") == "relaunch_app" for m in pushed), (
            f"restart_app must push a relaunch_app event; got: {pushed}"
        )

    def test_model_change_uses_config_device(self, app, monkeypatch):
        """_change_model should use self.config.device, not hardcoded cuda."""
        transcriber_cls = MagicMock()
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", transcriber_cls)

        app.config.device = "cpu"
        app.models._change_model_blocking("medium.en")

        assert app.config.model_size == "medium.en"
        assert app.models._model_load_attempted is False
        _, kwargs = transcriber_cls.call_args
        assert kwargs["model_size"] == "medium.en"
        assert kwargs["device"] == "cpu"
