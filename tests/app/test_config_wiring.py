"""split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

import contextlib
import json
import sys
import time
from unittest.mock import MagicMock

import numpy as np


def _wait_for_busy_clear(app, timeout=2.0):
    """Poll until app._busy_event is set (not busy).

    Replaces bare time.sleep() calls that cause flaky failures under load.

    TEST-033 (fix): poll interval reduced from 50ms to 5ms to speed up
    the test suite. With ~100 call sites, this saves ~4.5s of cumulative
    sleep time across a full run.
    """
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
        # construction is now centralized in AsrBackendRegistry.create()
        # which imports TranscriptionEngine dynamically from voice_typer.server.transcription.
        # Monkeypatch the SOURCE module (not app.TranscriptionEngine) so the
        # registry's dynamic import picks up the mock.
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", transcriber_cls)

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # TranscriptionEngine is now created in _do_startup (background), not __init__
        # Phase 1: the app-level test-seam delegates have been removed;
        # patch the controllers / module-level functions directly.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        app.hotkeys.register = MagicMock()
        app.models.try_load = MagicMock()
        # The model-not-downloaded precheck would refuse the load before
        # TranscriptionEngine is constructed (no real model on disk in
        # tests), leaving transcriber_cls never called. Stub it so the
        # engine construction path runs.
        app.models._model_downloaded_precheck = lambda: True
        app._do_startup()
        # Model load now runs in a daemon thread — wait for it so the
        # assertions below don't race with the background worker.
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
        # Phase 1: was ``app._sync_autostart()`` (test-seam delegate
        # removed); call the standalone function directly.
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
        # Phase 1: was ``app._sync_autostart()`` (test-seam delegate
        # removed); call the standalone function directly.
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

        # (fix): use the monkeypatch fixture instead of
        # pytest.MonkeyPatch() so the patch is auto-reverted after the
        # test. Previously the manual instantiation bypassed pytest's
        # lifecycle and could leak patches on test failure. The patch
        # targets text_cleanup's namespace — that's where the dictation
        # pipeline resolves clean_transcribed_text at call time, so the
        # spy fires if the disabled path ever regresses.
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
        """StartupSequence.run should call configure_corrections with config_dir.

        Phase 5: the call moved from ``VoiceTyperApp._do_startup`` to
        ``StartupSequence.run`` (in ``startup_sequence.py``). The monkeypatch
        target must follow the call site — patch the name in
        ``startup_sequence``'s namespace, not ``app``'s.
        """
        called_with = {}

        def spy(config_dir=None, corrections_path=None):
            called_with["config_dir"] = config_dir

        # Patch the name in startup_sequence's namespace ( Phase 5 moved
        # the call there).
        monkeypatch.setattr("voice_typer.server.startup_sequence.configure_corrections", spy)
        app._settings_window = None
        # Phase 1: was ``app._sync_prewarm_task = MagicMock()``
        # (test-seam delegate removed); patch the standalone function.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        # Prevent the background model loader from doing real work — we
        # only care that configure_corrections ran synchronously in Step 0.
        app.models.load_background = MagicMock()
        app._do_startup()
        assert called_with.get("config_dir") == app.config.config_dir, (
            f"configure_corrections should receive config_dir={app.config.config_dir}, "
            f"got {called_with.get('config_dir')}"
        )


class TestSettingsWindowIntegration:
    # ARCH-DEAD-SETTINGS: the show_settings / SettingsWindow tests were
    # removed when voice_typer.server.settings was deleted. The tkinter
    # settings UI is fully replaced by the Electron frontend; no
    # production code path constructs a SettingsWindow or calls
    # show_settings / open_settings.

    def test_restart_hotkey_stops_existing_backend_and_registers_new_one(self, app, monkeypatch):
        old_backend = MagicMock()
        app.hotkeys._hotkey_backend = old_backend
        # #2 _register_hotkey now delegates to HotkeyDispatcher.register().
        # Monkeypatch the factory the dispatcher uses to build a new
        # main backend, so we can assert the swap happened WITHOUT
        # going through ``register()`` (which restart() inlines since
        # AB-34: restart swaps only the main hotkey, skipping the aux
        # backends register_esc / register_repaste to avoid the
        # subprocess-spawn + Win32-hook-reinstall churn).
        new_backend = MagicMock()
        new_backend.is_alive.return_value = True
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            MagicMock(return_value=new_backend),
        )
        app.hotkeys.register = MagicMock()

        # Phase 2: was ``app._restart_hotkey("<f3>")`` (test-seam
        # delegate removed); call the dispatcher method directly.
        app.hotkeys.restart("<f3>")

        assert app.config.hotkey == "<f3>"
        old_backend.stop.assert_called_once()
        # restart() inlines main-backend creation; the factory was
        # called with the new spec (and the role kwarg that the
        # role-aware factory accepts).
        import voice_typer.server.hotkey_dispatcher as _hd

        _hd.create_hotkey_backend.assert_called_with("<f3>", role="dictation")
        assert app.hotkeys._hotkey_backend is new_backend

    def test_restart_app_does_not_spawn_subprocess(self, app, monkeypatch):
        """fix-restart-tcp: restart_app() must NOT spawn a replacement
        subprocess.  Electron's exit handler is the sole spawner; if
        Python also spawns one, the two new processes race for port
        9876 (one binds, one crashes with EADDRINUSE), TCP bounces
        between them, and the renderer sees cascading "Error: Timeout"
        plus a false "downloading model" screen.

        This test replaces the old test_restart_app_forwards_port_argument
        and test_restart_app_without_port_uses_stdin_mode, which both
        asserted on the subprocess args that are no longer produced.
        """
        import subprocess as _sp

        # Pre-warm the keyring probe cache so ``app.config.save()``
        # (called inside ``restart_app``) doesn't trigger subprocess
        # spawns via ``ctypes.util.find_library``. The keyring library
        # loads ALL backend plugins (including the macOS plugin, which
        # is unconditionally loaded even on Linux) the first time
        # ``keyring.get_keyring()`` is called; the macOS plugin does
        # ``ctypes.CDLL(find_library('Security'))`` which on Linux
        # spawns ``/sbin/ldconfig -p``, ``gcc -Wl,-t``, and ``ld -t``
        # as library-probing side effects.
        #
        # In production these subprocess calls happen exactly ONCE
        # (the first ``config.save()`` during ``apply_config`` at
        # startup, well before any restart); the cache is then
        # populated for the process lifetime and ``restart_app``'s
        # ``config.save()`` hits the cache without spawning. The
        # ``app`` fixture here doesn't trigger that path, so we
        # pre-warm the cache BEFORE mocking ``subprocess.Popen`` so
        # the probe's library-probing subprocess calls aren't captured
        # by the assertion below. These are benign OS library probes,
        # NOT replacement backend/Electron spawns.
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
        # in the test environment (no IPC server wired up).
        # B-1: production code now calls event_bus.publish directly.
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: None,
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # fix-restart-tcp: the port-race risk is a REPLACEMENT backend /
        # Electron spawn (two processes fighting over port 9876).
        # restart_app() itself must not spawn one. Benign OS utilities
        # that run as a side effect of config-dir setup / resource
        # probing (``icacls`` ACL enforcement on the fresh temp config
        # dir, ``lscpu`` CPU inventory) are NOT replacement spawns and
        # are filtered out.
        replacement_spawns = [
            (args, kwargs)
            for args, kwargs in popen_calls
            if not (args and args[0] and args[0][0] in ("icacls", "lscpu"))
        ]
        assert replacement_spawns == [], (
            "restart_app must NOT spawn a replacement backend/Electron "
            f"subprocess (port-race); got: {replacement_spawns}"
        )

    def test_restart_app_pushes_restart_ack_event(self, app, monkeypatch):
        """fix-restart-tcp: restart_app() must push a ``relaunch_electron``
        event over the TCP channel BEFORE exiting.  Electron listens
        for this event to call app.relaunch() + app.exit(0), which
        spawns a fresh Electron process (and in turn a fresh Python
        backend).  This replaces the old ``restart_ack`` design which
        tried to keep Electron alive while swapping only the Python
        backend — that design had multiple race conditions (TCP close
        racing with restart_ack delivery, tcpSocket set before connect
        causing auth failures, _restarting flag cleared too early)
        that produced cascading "Error: Timeout" and "Python socket
        closed" errors.  The full-relaunch approach eliminates all of
        them: the entire OS process is replaced."""
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
        # construction is now centralized in AsrBackendRegistry.create()
        # which imports TranscriptionEngine dynamically from voice_typer.server.transcription.
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", transcriber_cls)

        app.config.device = "cpu"
        # Phase 2: was ``app._change_model("medium.en")`` (test-seam
        # delegate removed); call the ModelManager method directly.
        # change_model is now non-blocking (returns immediately with
        # "loading" status); tests that need to inspect the synchronous result
        # must call the blocking variant.
        app.models._change_model_blocking("medium.en")

        assert app.config.model_size == "medium.en"
        assert app.models._model_load_attempted is False
        _, kwargs = transcriber_cls.call_args
        assert kwargs["model_size"] == "medium.en"
        assert kwargs["device"] == "cpu"


# ── RELIABILITY-001: quit_app / restart_app must use clean shutdown ──────
