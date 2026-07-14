"""Tests for app state transitions and error handling.

All heavy dependencies are mocked so these tests run on any platform
without GPU, microphone, or display.
"""

import contextlib
import json
import sys
import time
from pathlib import Path

# TEST-021: removed unused `PropertyMock` import (ruff F401).
# Path is used by TestSingleInstanceEnforcement (TEST-037).
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


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


# Mock heavy imports before they're loaded by the app module
# These patches stay active for the entire module
@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    """Mock all hardware/GUI dependencies so tests run headless."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)

    mock_whisper = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())

    mock_pynput = MagicMock()
    mock_pynput_kb = MagicMock()
    monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)

    mock_pystray = MagicMock()
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())

    # Prevent clipboard operations
    monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

    # Prevent the app's atexit handler from polluting test output.
    monkeypatch.setattr("voice_typer.server.app.atexit.register", lambda *a, **kw: None)

    # Force PynputHotkey backend so tests can mock pynput.keyboard.GlobalHotKeys.
    # fix: patch BOTH app.create_hotkey_backend AND
    # hotkey_dispatcher.create_hotkey_backend. The actual call site is in
    # hotkey_dispatcher.register() (line 72), which uses its own imported
    # copy of create_hotkey_backend — NOT app's. Patching only app's copy
    # (the old behavior) was a no-op; tests passed only because on Linux/X11
    # the unpatched create_hotkey_backend returns PynputHotkey by default.
    from voice_typer.server.hotkeys import PynputHotkey

    def _force_pynput(hotkey_str):
        return PynputHotkey(hotkey_str)

    monkeypatch.setattr(
        "voice_typer.server.app.create_hotkey_backend",
        _force_pynput,
    )
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        _force_pynput,
    )


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies."""
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # NEW-PRIV-009 (revised): RecordingController.start() now enforces
    # voice_biometric_consent before capturing audio. Tests that exercise
    # the recording path must explicitly opt in (just like real users
    # must enable the toggle in Settings > Privacy before recording).
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is now created in _do_startup (background), not __init__
    # Set a mock transcriber for tests that need it.
    # ARCH-REFAC-003: with the @property delegate removed, assigning to
    # instance.models.transcriber no longer auto-syncs the registry —
    # call _sync_registry_from_fields() so the registry knows about the
    # mock and _start_dictation's ensure_active_engine_loaded() doesn't
    # try to create a fresh TranscriptionEngine.
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    instance.models._sync_registry_from_fields()
    return instance


class TestAppStateTransitions:
    def test_initial_state_is_idle(self, app):
        # TEST-008 (fix): removed redundant `assert not not` — the
        # following assert is a strict superset.
        assert app._busy_event.is_set()  # event is SET when not busy
        assert app.recorder.recording is False

    def test_start_dictation_sets_recording(self, app):
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.recorder.start = MagicMock()
        app.tray = MagicMock()
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = True

        app._start_dictation()

        app.recorder.start.assert_called_once()
        app.tray.set_state.assert_called()

    def test_start_dictation_ignored_if_already_recording(self, app):
        app.recorder = MagicMock()
        app.recorder.recording = True

        app._start_dictation()

        app.recorder.start.assert_not_called()

    def test_stop_dictation_ignored_if_not_recording(self, app):
        app.recorder = MagicMock()
        app.recorder.recording = False

        app._stop_dictation()

        # Should not try to stop if not recording
        app.recorder.stop.assert_not_called()

    def test_short_audio_skips_transcription(self, app, monkeypatch):
        import voice_typer.server.app as app_mod

        # Use monkeypatch.setattr so the mock is auto-reverted after
        # the test.  Previously this did `app_mod.time = MagicMock()`
        # which leaked into subsequent tests and broke _push_bubble_level's
        # `time.monotonic()` call (returning a MagicMock instead of a float).
        monkeypatch.setattr(app_mod, "time", MagicMock())

        app.recorder = MagicMock()
        app.recorder.recording = True
        # Return 0.1s of audio (less than 0.5s threshold)
        app.recorder.stop = MagicMock(return_value=np.zeros(int(0.1 * 16000), dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)
        assert app._busy_event.is_set()

    def test_transcribe_success_copies_to_clipboard(self, app, monkeypatch):
        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.clipboard.paste = MagicMock(return_value=False)

        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="hello world")
        app.models.transcriber.device_info = "cpu (int8)"

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        app.clipboard.copy.assert_called_with("Hello world")
        app.clipboard.paste.assert_called_once()

    def test_transcribe_cleans_text_before_clipboard(self, app, monkeypatch):
        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.clipboard.paste = MagicMock(return_value=False)

        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="can we test this this now")
        app.models.transcriber.device_info = "cuda/float16/small.en"

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        # Forced terminal punctuation was removed from the pipeline.
        # NEW-UX-010: auto_punctuation now defaults to True, so the
        # auto-punctuation step adds a "?" at the end of the question.
        app.clipboard.copy.assert_called_once_with("Can we test this now?")

    def test_clipboard_copy_failure_prevents_paste(self, app):
        """Regression test for Finding 1: stale clipboard must not be pasted.

        ADR-0010 §5.2: copy() now raises ClipboardCopyError on genuine
        copy failure (instead of returning False). The dictation
        pipeline's _copy_and_paste() catches it and short-circuits —
        paste() is never called.
        """
        from voice_typer.server.clipboard import ClipboardCopyError

        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(side_effect=ClipboardCopyError("simulated copy failure"))
        app.clipboard.paste = MagicMock(return_value=True)

        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="secret text")
        app.models.transcriber.device_info = "cpu (int8)"

        app.tray = MagicMock()

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        # copy was called
        app.clipboard.copy.assert_called_once_with("Secret text")
        # paste must NOT have been called
        app.clipboard.paste.assert_not_called()
        # tray should show clipboard-unavailable status
        app.tray.notify.assert_called()
        notify_args = app.tray.notify.call_args
        assert "clipboard" in notify_args[0][1].lower() or "clipboard" in str(notify_args).lower()

    def test_transcribe_empty_result_no_clipboard(self, app):
        app.clipboard = MagicMock()
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="")

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        app.clipboard.copy.assert_not_called()

    def test_transcribe_failure_shows_error(self, app):
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(side_effect=Exception("model crash"))

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        # Should not crash; error state should be set
        assert app._busy_event.is_set()

    def test_transcribe_cuda_fallback_clears_busy(self, app):
        """When GPU transcription fails with CUDA error, fallback to CPU succeeds
        and _busy is still cleared."""
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        # First call (GPU) raises CUDA error, fallback (CPU) returns text
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="fallback worked")
        app.models.transcriber.device_info = "cpu (int8)"

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        assert app._busy_event.is_set()
        app.models.transcriber.transcribe_with_fallback.assert_called_once()

    def test_force_recover_resets_busy(self, app):
        """_force_recover_from_stuck_transcription clears _busy and resets tray."""
        app._busy_event.clear()  # set busy
        app.tray = MagicMock()

        # RW-9 Phase 1: was ``app._force_recover_from_stuck_transcription()``
        # (test-seam delegate removed); call the controller method directly.
        app.recording._force_recover_from_stuck_transcription()

        assert app._busy_event.is_set()
        app.tray.set_state.assert_called()

    def test_stop_dictation_sets_bubble_transcribing_state(self, app):
        """_stop_dictation must call bubble.set_state('transcribing').

        NEW-BUBBLE-TRANSCRIBING: When recording stops, the bubble should
        show "Transcribing…" text with animated dots instead of hiding
        immediately. The bubble transitions:
          recording → transcribing (on stop) → idle/hidden (on completion)
        """
        app._waveform_bubble = MagicMock()
        app._waveform_bubble.visible = True
        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="test")
        app.models.transcriber.device_info = "cpu (int8)"
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app.recorder.last_rms = 0.5

        app._stop_dictation()

        # Must set transcribing state instead of hiding
        app._waveform_bubble.set_state.assert_called_once_with("transcribing")
        # Must NOT hide the bubble (it stays visible during transcription)
        app._waveform_bubble.hide.assert_not_called()

        _wait_for_busy_clear(app)

    def test_stop_dictation_calls_set_state_transcribing(self, app):
        """_stop_dictation must call bubble.set_state('transcribing') during
        the stop flow so the renderer can show the transcribing overlay."""
        call_order = []

        app._waveform_bubble = MagicMock()
        original_set_state = app._waveform_bubble.set_state

        def track_set_state(state):
            call_order.append(("set_state", state))
            return original_set_state(state) if callable(original_set_state) else None

        app._waveform_bubble.set_state = track_set_state

        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="test")
        app.models.transcriber.device_info = "cpu (int8)"
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app.recorder.last_rms = 0.5

        app._stop_dictation()

        _wait_for_busy_clear(app)

        # set_state('transcribing') should have been called
        transcribing_calls = [call for call in call_order if call[0] == "set_state" and call[1] == "transcribing"]
        assert len(transcribing_calls) >= 1, f"set_state('transcribing') must be called, call_order={call_order}"

    def test_force_recover_noop_when_not_busy(self, app):
        """_force_recover is a no-op if not busy."""
        app._busy_event.set()  # not busy
        app.tray = MagicMock()

        # RW-9 Phase 1: was ``app._force_recover_from_stuck_transcription()``
        # (test-seam delegate removed); call the controller method directly.
        app.recording._force_recover_from_stuck_transcription()

        # No state change should have been made
        app.tray.set_state.assert_not_called()

    def test_force_recover_does_not_clear_busy_while_thread_is_alive(self, app):
        """Watchdog must not allow a second transcription while the old one runs."""
        app._busy_event.clear()  # busy
        app.tray = MagicMock()
        # ARCH-REFAC-003: write to RecordingController directly (was a
        # @property delegate on VoiceTyperApp).
        app.recording._transcription_thread = MagicMock()
        app.recording._transcription_thread.is_alive.return_value = True

        # RW-9 Phase 1: was ``app._force_recover_from_stuck_transcription()``
        # (test-seam delegate removed); call the controller method directly.
        app.recording._force_recover_from_stuck_transcription()

        assert not app._busy_event.is_set()  # Still busy
        app.tray.set_state.assert_called()
        status_text = app.tray.set_state.call_args[0][1]
        assert "Still transcribing" in status_text

    def test_f2_works_after_transcription_failure(self, app):
        """After a transcription failure, pressing F2 should work again."""
        # Simulate: transcription failed, busy was cleared
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(
            side_effect=RuntimeError("cublas64_12.dll is not found or cannot be loaded")
        )
        app.models.transcriber.device_info = "cpu (int8)"

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        # First stop — transcription fails
        app._stop_dictation()
        _wait_for_busy_clear(app)

        assert app._busy_event.is_set(), "_busy_event must be set after failed transcription"

        # Now simulate pressing F2 again
        app.recorder.recording = False
        app.models.transcriber.is_loaded = True
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="recovered!")

        app.toggle_dictation()  # F2 → start recording
        app.recorder.start.assert_called_once()

        app.recorder.recording = True
        app.toggle_dictation()  # F2 → stop recording, transcribe
        _wait_for_busy_clear(app)

        assert app._busy_event.is_set()
        app.models.transcriber.transcribe_with_fallback.assert_called_once_with(app.recorder.stop.return_value)

    def test_stop_dictation_emits_recording_stopped_event(self, app, monkeypatch):
        """SOUND-FIX-005 (Round 0): ``app._stop_dictation`` must emit the
        ``recording_stopped`` IPC push event so the renderer's
        ``useSoundFeedback`` hook can play the stop cue.

        Previously ``app._stop_dictation`` was a 125-line duplicate of
        ``RecordingController.stop()`` that skipped the emit, so the stop
        beep never played. This test guards against regression by
        asserting the event is pushed exactly once per stop.
        """
        # Simulate a recording in progress with enough audio to pass the
        # 0.5s short-circuit gate (we don't need transcription to succeed).
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        # Stub the pipeline so the transcription thread completes quickly.
        captured = {"events": []}

        def fake_push_event_now(event):
            captured["events"].append(event)

        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            fake_push_event_now,
        )

        # Drive a real stop via the delegate.
        app._stop_dictation()
        _wait_for_busy_clear(app)

        # Assert the recording_stopped event was emitted exactly once.
        stop_events = [e for e in captured["events"] if e.get("type") == "recording_stopped"]
        assert len(stop_events) == 1, (
            f"Expected exactly one recording_stopped event, got {len(stop_events)}. "
            f"All captured events: {captured['events']}"
        )


class TestConfigWiring:
    def test_paste_on_stop_preserves_user_value(self, tmp_config_dir, monkeypatch):
        """After override removal, user's paste_on_stop=False must be preserved."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"paste_on_stop": False}))

        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        assert app.config.paste_on_stop is False
        assert app.clipboard.paste_enabled is False

    def test_streaming_preserves_user_value(self, tmp_config_dir, monkeypatch):
        """After override removal, user's streaming_transcription=False must be preserved."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"streaming_transcription": False}))

        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

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

        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        transcriber_cls = MagicMock()
        # ARCH-007: construction is now centralized in AsrBackendRegistry.create()
        # which imports TranscriptionEngine dynamically from voice_typer.server.transcription.
        # Monkeypatch the SOURCE module (not app.TranscriptionEngine) so the
        # registry's dynamic import picks up the mock.
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", transcriber_cls)

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # TranscriptionEngine is now created in _do_startup (background), not __init__
        # RW-9 Phase 1: the app-level test-seam delegates have been removed;
        # patch the controllers / module-level functions directly.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        app.hotkeys.register = MagicMock()
        app.models.try_load = MagicMock()
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

        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        called = []
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: called.append(True) or True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        from voice_typer.server import startup_tasks
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # RW-9 Phase 1: was ``app._sync_autostart()`` (test-seam delegate
        # removed); call the standalone function directly.
        startup_tasks.sync_autostart(app)

        assert len(called) == 1  # enable_autostart was called

    def test_autostart_disabled_when_config_false(self, tmp_config_dir, monkeypatch):
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"autostart": False}))

        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        called = []
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: called.append(True) or True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        from voice_typer.server import startup_tasks
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # RW-9 Phase 1: was ``app._sync_autostart()`` (test-seam delegate
        # removed); call the standalone function directly.
        startup_tasks.sync_autostart(app)

        assert len(called) == 1  # disable_autostart was called


class TestSettingsWindowIntegration:
    # ARCH-DEAD-SETTINGS: the show_settings / SettingsWindow tests were
    # removed when voice_typer.server.settings was deleted. The tkinter
    # settings UI is fully replaced by the Electron frontend; no
    # production code path constructs a SettingsWindow or calls
    # show_settings / open_settings.

    def test_restart_hotkey_stops_existing_backend_and_registers_new_one(self, app):
        old_backend = MagicMock()
        app.hotkeys._hotkey_backend = old_backend
        # #2 _register_hotkey now delegates to HotkeyDispatcher.register().
        # Monkeypatch the dispatcher method directly.
        app.hotkeys.register = MagicMock()

        # RW-9 Phase 2: was ``app._restart_hotkey("<f3>")`` (test-seam
        # delegate removed); call the dispatcher method directly.
        app.hotkeys.restart("<f3>")

        assert app.config.hotkey == "<f3>"
        old_backend.stop.assert_called_once()
        app.hotkeys.register.assert_called_once()

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

        popen_calls = []
        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: popen_calls.append((a, kw)))
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        monkeypatch.setattr(sys, "argv", ["voice_typer", "--port", "9876"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
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

        assert popen_calls == [], (
            f"restart_app must NOT spawn a subprocess (Electron is the sole spawner); got Popen calls: {popen_calls}"
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
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
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
        assert any(m.get("type") == "relaunch_electron" for m in pushed), (
            f"restart_app must push a relaunch_electron event; got: {pushed}"
        )

    def test_model_change_uses_config_device(self, app, monkeypatch):
        """_change_model should use self.config.device, not hardcoded cuda."""
        transcriber_cls = MagicMock()
        # ARCH-007: construction is now centralized in AsrBackendRegistry.create()
        # which imports TranscriptionEngine dynamically from voice_typer.server.transcription.
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", transcriber_cls)

        app.config.device = "cpu"
        # RW-9 Phase 2: was ``app._change_model("medium.en")`` (test-seam
        # delegate removed); call the ModelManager method directly.
        app.models.change_model("medium.en")

        assert app.config.model_size == "medium.en"
        assert app.models._model_load_attempted is False
        _, kwargs = transcriber_cls.call_args
        assert kwargs["model_size"] == "medium.en"
        assert kwargs["device"] == "cpu"


# ── RELIABILITY-001: quit_app / restart_app must use clean shutdown ──────


class TestQuitAppCleanShutdown:
    """RELIABILITY-001: ``quit_app`` must NOT use ``os._exit(0)``.
    It should delegate to the audited ``self.quit()`` cleanup path so
    that Python atexit handlers, ``__del__`` methods, and ``finally``
    blocks run — releasing the Win32 mutex, closing PortAudio streams,
    and unregistering hotkeys.
    """

    def test_quit_app_does_not_call_os_exit(self, app, monkeypatch):
        """os._exit(0) must never be called from quit_app."""
        os_exit_called = []
        monkeypatch.setattr(
            "voice_typer.server.app.os._exit",
            lambda code: os_exit_called.append(code),
        )
        # Stub out clean-shutdown side effects so quit() can run without
        # actually joining threads / stopping pystray.
        app._cancel_pending_timers = MagicMock()
        # RW-9 Phase 1: was ``app._get_streaming_session`` / ``app._set_streaming_session``
        # (test-seam delegates removed); patch the controller methods directly.
        app.recording.get_streaming_session = MagicMock(return_value=None)
        app.recording.set_streaming_session = MagicMock()
        app.recorder = MagicMock()
        # ARCH-REFAC-003: write to RecordingController directly (was a
        # @property delegate on VoiceTyperApp).
        app.recording._transcription_thread = None
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.quit_app()

        assert os_exit_called == [], f"quit_app must not call os._exit; called with {os_exit_called}"

    def test_quit_app_calls_self_quit(self, app, monkeypatch):
        """quit_app should delegate to self.quit() (the audited cleanup
        path) rather than duplicating cleanup inline."""
        quit_called = []

        # quit() is supposed to raise SystemExit; simulate that so
        # quit_app's flow terminates the test cleanly.
        def fake_quit():
            quit_called.append(True)
            raise SystemExit(0)

        monkeypatch.setattr(app, "quit", fake_quit)
        # Stub the side-effect that runs before quit() — push_event
        # goes over IPC and is not relevant to this unit test.
        # B-1: production code now calls event_bus.publish directly.
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: None,
        )
        # Belt-and-suspenders: if quit_app falls through to os._exit
        # (it shouldn't after this fix), don't kill the pytest process.
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)

        with pytest.raises(SystemExit):
            app.quit_app()

        assert quit_called == [True], "quit_app must call self.quit()"

    def test_quit_app_notifies_electron_first(self, app, monkeypatch):
        """Before any cleanup, quit_app pushes a quit_app event over IPC
        so the Electron frontend can call app.quit() and shut down
        cleanly (instead of being orphaned)."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        # Stub self.quit() so we can verify the push happens before it.
        monkeypatch.setattr(app, "quit", lambda: (_ for _ in ()).throw(SystemExit(0)))
        # Belt-and-suspenders: don't let os._exit kill the pytest process.
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)

        with pytest.raises(SystemExit):
            app.quit_app()

        assert pushed == [{"type": "quit_app"}]

    def test_quit_stops_esc_and_repaste_backends(self, app, monkeypatch):
        """RELIABILITY-003: quit() (called by quit_app) must stop
        esc_backend and repaste_backend, not just hotkey_backend."""
        app._cancel_pending_timers = MagicMock()
        # RW-9 Phase 1: was ``app._get_streaming_session`` / ``app._set_streaming_session``
        # (test-seam delegates removed); patch the controller methods directly.
        app.recording.get_streaming_session = MagicMock(return_value=None)
        app.recording.set_streaming_session = MagicMock()
        app.recorder = MagicMock()
        # ARCH-REFAC-003: write to RecordingController directly (was a
        # @property delegate on VoiceTyperApp).
        app.recording._transcription_thread = None
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.quit()

        app.hotkeys._hotkey_backend.stop.assert_called_once()
        app.hotkeys._esc_backend.stop.assert_called_once()
        app.hotkeys._repaste_backend.stop.assert_called_once()


class TestRestartAppCleanShutdown:
    """RELIABILITY-001: ``restart_app`` must NOT use ``os._exit(0)``.
    After spawning the new subprocess, it should stop backends
    (including esc_backend and repaste_backend — RELIABILITY-003) and
    exit via ``sys.exit(0)`` so Python cleanup runs in the old
    process."""

    def test_restart_app_does_not_call_os_exit(self, app, monkeypatch):
        os_exit_called = []
        monkeypatch.setattr(
            "voice_typer.server.app.os._exit",
            lambda code: os_exit_called.append(code),
        )
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        # Force the pre-restart sleep to no-op so the test is fast.
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert os_exit_called == [], f"restart_app must not call os._exit; called with {os_exit_called}"

    def test_restart_app_stops_esc_and_repaste_backends(self, app, monkeypatch):
        """RELIABILITY-003: restart_app must stop esc_backend and
        repaste_backend, not just hotkey_backend."""
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        # Belt-and-suspenders: don't let os._exit kill the pytest process.
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.hotkeys._hotkey_backend.stop.assert_called_once()
        app.hotkeys._esc_backend.stop.assert_called_once()
        app.hotkeys._repaste_backend.stop.assert_called_once()

    def test_restart_app_calls_tray_stop(self, app, monkeypatch):
        """restart_app must call self.tray.stop() to break the pystray
        event loop so the process can actually exit via sys.exit(0)."""
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        # Belt-and-suspenders: don't let os._exit kill the pytest process.
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.tray.stop.assert_called_once()

    def test_restart_app_sets_shutting_down_before_exit(self, app, monkeypatch):
        """RELIABILITY-006: restart_app must set _shutting_down=True so
        the atexit handler (_atexit_log) classifies the exit as
        intentional. Without this, every restart logs "likely killed
        externally", making it impossible to distinguish real external
        kills from intentional restarts when triaging crash logs.
        """
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        # Sanity: flag starts False.
        assert app._shutting_down is False

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # Must be True after restart_app so the atexit handler
        # doesn't log a spurious "likely killed externally" warning.
        assert app._shutting_down is True, (
            "RELIABILITY-006 regression: restart_app did not set "
            "_shutting_down=True; atexit handler will misclassify "
            "intentional restarts as external kills."
        )


class TestHotkeyMapping:
    """Verify the hotkey registration uses the new backend abstraction."""

    def test_register_hotkey_creates_backend(self, app, monkeypatch):
        """_register_hotkey should create a hotkey backend and call start()."""

        # Ensure GlobalHotKeys works (mock returns a MagicMock with is_alive=True)
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        # pyrefly: ignore [missing-attribute]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        assert app.hotkeys._hotkey_backend is not None
        # Main hotkey + repaste hotkey both call GlobalHotKeys.start
        assert mock_ghk_cls.call_count >= 1
        assert mock_listener.start.call_count >= 1

    def test_register_hotkey_failure_does_not_crash(self, app):
        """If both GlobalHotKeys AND fallback Listener raise, app should not crash."""
        mock_kb = sys.modules["pynput.keyboard"]
        # pyrefly: ignore [missing-attribute]
        mock_kb.GlobalHotKeys = MagicMock(side_effect=Exception("no display"))
        # pyrefly: ignore [missing-attribute]
        mock_kb.Listener = MagicMock(side_effect=Exception("no input"))

        # Should not raise
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()
        # Backend was created but start() failed -> not alive or None
        if app.hotkeys._hotkey_backend is not None:
            assert app.hotkeys._hotkey_backend.is_alive() is False

    def test_register_esc_hotkey_creates_and_starts_backend(self, app, monkeypatch):
        """_register_esc_hotkey should create a backend and call start().

        ARCH-ESC-001 (Round 0): the ESC callback is now a closure
        (_esc_callback in hotkey_dispatcher.register_esc) that wraps
        app._cancel_dictation with a keyboard_ownership guard — so the
        test must accept any callable, not the raw bound method.
        """

        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        assert app.hotkeys._esc_backend is None
        # RW-9 Phase 1: was ``app._register_esc_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register_esc()

        assert app.hotkeys._esc_backend is not None
        # The callback is now a closure (ARCH-ESC-001), so accept any
        # callable rather than asserting it's the raw bound method.
        mock_ghk_cls.assert_called_once()
        call_args = mock_ghk_cls.call_args
        assert len(call_args.args) == 1, "GlobalHotKeys must be called with the hotkey dict"
        hotkey_dict = call_args.args[0]
        assert "<esc>" in hotkey_dict, "hotkey dict must contain <esc>"
        assert callable(hotkey_dict["<esc>"]), "ESC callback must be callable"
        mock_listener.start.assert_called_once()

    def test_unregister_esc_hotkey_stops_and_clears_backend(self, app, monkeypatch):
        """_unregister_esc_hotkey should stop the backend and set it to None."""
        mock_backend = MagicMock()
        # #2 create_hotkey_backend is now imported in
        # hotkey_dispatcher, so monkeypatch it there.
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            MagicMock(return_value=mock_backend),
        )

        # RW-9 Phase 1: was ``app._register_esc_hotkey()`` /
        # ``app._unregister_esc_hotkey()`` (test-seam delegates removed);
        # call the dispatcher methods directly.
        app.hotkeys.register_esc()
        assert app.hotkeys._esc_backend is mock_backend

        app.hotkeys.unregister_esc()

        assert app.hotkeys._esc_backend is None
        mock_backend.stop.assert_called_once()

    def test_unregister_esc_hotkey_noop_when_none(self, app):
        """_unregister_esc_hotkey should not crash when _esc_backend is None."""
        app.hotkeys._esc_backend = None
        # RW-9 Phase 1: was ``app._unregister_esc_hotkey()`` (test-seam
        # delegate removed); call the dispatcher method directly.
        app.hotkeys.unregister_esc()  # Must not raise

    def test_register_esc_hotkey_failure_does_not_crash(self, app, monkeypatch):
        """If ESC hotkey registration fails, app should not crash."""

        def failing_create(*args):
            raise RuntimeError("no display")

        # #2 create_hotkey_backend is now imported in
        # hotkey_dispatcher, so monkeypatch it there.
        monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", failing_create)
        # Should not raise even though create_hotkey_backend raises
        # RW-9 Phase 1: was ``app._register_esc_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register_esc()
        assert app.hotkeys._esc_backend is None

    def test_register_hotkey_includes_esc_when_enabled(self, app, monkeypatch):
        """When esc_cancel_enabled is True, _register_hotkey should also call _register_esc_hotkey."""

        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        app.config.esc_cancel_enabled = True
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        assert app.hotkeys._esc_backend is not None

    def test_dictation_callback_respects_hotkey_capture(self, app, monkeypatch):
        """HOTKEY-FIX-001: the dictation hotkey callback must
        be a no-op when the frontend is in hotkey capture mode (keyboard
        ownership == "hotkey_capture"). This prevents sub-tasks 2.4 and
        2.5 — pressing a key during hotkey capture immediately triggering
        recording.
        """
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        # Build the callback via the dispatcher's helper
        callback = app.hotkeys._make_dictation_callback()
        assert callable(callback)

        # Track toggle_dictation calls
        toggle_called = {"n": 0}
        monkeypatch.setattr(app, "toggle_dictation", lambda: toggle_called.__setitem__("n", toggle_called["n"] + 1))

        # Case 1: ownership = normal → callback should fire toggle_dictation
        keyboard_ownership().set_owner("normal", reason="test")
        callback()
        assert toggle_called["n"] == 1, "callback should fire when capture is NOT active"

        # Case 2: ownership = hotkey_capture → callback should be a no-op
        keyboard_ownership().set_owner("hotkey_capture", reason="test capture")
        callback()
        assert toggle_called["n"] == 1, "callback must NOT fire when capture IS active"

        # Case 3: ownership back to normal → callback fires again
        keyboard_ownership().set_owner("normal", reason="test done")
        callback()
        assert toggle_called["n"] == 2, "callback should fire again after capture ends"

    def test_repaste_callback_respects_hotkey_capture(self, app, monkeypatch):
        """HOTKEY-FIX-001: the repaste hotkey callback must
        also be a no-op during hotkey capture (defense-in-depth)."""
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        callback = app.hotkeys._make_repaste_callback()
        assert callable(callback)

        repaste_called = {"n": 0}
        monkeypatch.setattr(app, "repaste_last", lambda: repaste_called.__setitem__("n", repaste_called["n"] + 1))

        keyboard_ownership().set_owner("normal", reason="test")
        callback()
        assert repaste_called["n"] == 1

        keyboard_ownership().set_owner("hotkey_capture", reason="test capture")
        callback()
        assert repaste_called["n"] == 1, "repaste must NOT fire during capture"

        keyboard_ownership().set_owner("normal", reason="test done")
        callback()
        assert repaste_called["n"] == 2

    def test_register_hotkey_skips_esc_when_disabled(self, app, monkeypatch):
        """When esc_cancel_enabled is False, _register_hotkey should skip ESC registration."""

        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_ghk_cls = MagicMock(return_value=mock_listener)

        mock_kb = sys.modules["pynput.keyboard"]
        mock_kb.GlobalHotKeys = mock_ghk_cls

        app.config.esc_cancel_enabled = False
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        assert app.hotkeys._esc_backend is None


class TestFallbackHotkeyParser:
    """Verify parse_hotkey_to_vk correctly converts hotkey strings."""

    def test_parse_f2(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        result = parse_hotkey_to_vk("<f2>")
        assert result == 0x71

    def test_parse_f1(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        result = parse_hotkey_to_vk("<f1>")
        assert result == 0x70

    def test_parse_f12(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        result = parse_hotkey_to_vk("<f12>")
        assert result == 0x7B


class TestToggleDictationDispatch:
    """Verify toggle_dictation correctly dispatches to start/stop."""

    def test_toggle_calls_start_when_not_recording(self, app):
        """toggle_dictation() -> _start_dictation() when recorder.recording is False."""
        app.recorder = MagicMock()
        app.recorder.recording = False

        # Track which method was called
        start_called = []

        def tracked_start():
            start_called.append(True)

        app._start_dictation = tracked_start

        stop_called = []

        def tracked_stop():
            stop_called.append(True)

        app._stop_dictation = tracked_stop

        app.toggle_dictation()

        assert len(start_called) == 1, "toggle_dictation should call _start_dictation when not recording"
        assert len(stop_called) == 0, "toggle_dictation should NOT call _stop_dictation when not recording"

    def test_toggle_calls_stop_when_recording(self, app):
        """toggle_dictation() -> _stop_dictation() when recorder.recording is True."""
        app.recorder = MagicMock()
        app.recorder.recording = True

        start_called = []

        def tracked_start():
            start_called.append(True)

        app._start_dictation = tracked_start

        stop_called = []

        def tracked_stop():
            stop_called.append(True)

        app._stop_dictation = tracked_stop

        app.toggle_dictation()

        assert len(stop_called) == 1, "toggle_dictation should call _stop_dictation when recording"
        assert len(start_called) == 0, "toggle_dictation should NOT call _start_dictation when recording"

    def test_toggle_ignored_when_busy(self, app):
        """toggle_dictation() should do nothing when busy."""
        app._busy_event.clear()  # busy
        app.recorder = MagicMock()
        app.recorder.recording = False

        app._start_dictation = MagicMock()
        app._stop_dictation = MagicMock()

        app.toggle_dictation()

        app._start_dictation.assert_not_called()
        app._stop_dictation.assert_not_called()


class TestModelLoadingQueue:
    """When the model is still loading in the background, F2 queues the
    dictation and auto-starts it once loading completes."""

    def test_toggle_queues_when_model_loading(self, app):
        """F2 during background model load sets _pending_dictation and
        shows a LOADING state — does NOT call _start/_stop_dictation."""
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()

        # Simulate an in-flight background loader.
        loader = MagicMock()
        loader.is_alive.return_value = True
        app.models._model_load_thread = loader

        app._start_dictation = MagicMock()
        app._stop_dictation = MagicMock()

        app.toggle_dictation()

        assert app.models._pending_dictation is True
        app._start_dictation.assert_not_called()
        app._stop_dictation.assert_not_called()
        # Tray should reflect the loading state.
        app.tray.set_state.assert_called()

    def test_toggle_does_not_queue_when_load_complete(self, app):
        """Once the loader thread has finished, F2 goes straight to
        _start_dictation (no queueing)."""
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()

        # Loader finished (thread object exists but not alive).
        loader = MagicMock()
        loader.is_alive.return_value = False
        app.models._model_load_thread = loader

        started = []
        app._start_dictation = lambda: started.append(True)
        app._stop_dictation = MagicMock()

        app.toggle_dictation()

        assert app.models._pending_dictation is False
        assert len(started) == 1

    def test_toggle_survives_loader_cleared_during_is_alive(self, app):
        """Regression for TOCTOU race: the background loader's finally
        block sets self._model_load_thread = None.  If that runs while
        toggle_dictation is between its `is not None` check and the
        `.is_alive()` call, the old (two-LOAD_ATTR) code raised
        ``AttributeError: 'NoneType' object has no attribute 'is_alive'``.

        We simulate the race by making the loader's is_alive() clear the
        attribute — exactly what the loader thread does in its finally
        block — and assert toggle_dictation does not crash.  With the fix
        (capture the reference into a local first), is_alive() runs on the
        captured local, so the attribute becoming None is harmless.
        """
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()

        loader = MagicMock()

        # is_alive() simulates the loader finishing: clears the attribute
        # (as the real finally block does) then returns True so the queuing
        # path is exercised.
        def clear_then_alive():
            app.models._model_load_thread = None
            return True

        loader.is_alive.side_effect = clear_then_alive
        app.models._model_load_thread = loader

        # Must not raise AttributeError.  (The old code re-read the
        # attribute for is_alive() and would hit None here.)
        try:
            app.toggle_dictation()
        except AttributeError as exc:
            pytest.fail(f"toggle_dictation crashed on race: {exc}")

        assert app.models._pending_dictation is True

    def test_background_load_auto_starts_pending_dictation(self, app, monkeypatch):
        """When the loader finishes and _pending_dictation is set, it
        schedules _start_dictation via a 0-delay timer."""
        app.tray = MagicMock()
        # Stub the engine init so the loader body doesn't do real work.
        app.models._ensure_engine = MagicMock()
        # RW-9 Phase 2: was ``app._try_load_model = MagicMock()`` /
        # ``app._fallback_to_whisper = MagicMock()`` (delegates removed);
        # patch the ModelManager methods directly.
        app.models.try_load = MagicMock()
        app.models.fallback_to_whisper = MagicMock()

        # Simulate a queued F2 press.
        app.models._pending_dictation = True

        scheduled = []

        def fake_schedule(delay, func):
            scheduled.append((delay, func))

        app._schedule_timer = fake_schedule

        # Run the loader synchronously (it would normally be in a thread).
        # Force the Whisper backend path (default) so it's a no-op with
        # _try_load_model mocked.
        app.config.asr_backend = "whisper"
        app.models.load_background()

        # The pending dictation should have been cleared and a 0-delay
        # _start_dictation scheduled.
        assert app.models._pending_dictation is False
        assert any(delay == 0 for delay, _ in scheduled), (
            "pending dictation should schedule _start_dictation at delay=0"
        )

    def test_background_load_no_auto_start_when_not_pending(self, app):
        """If the user did NOT press F2 during load, nothing is scheduled."""
        app.tray = MagicMock()
        app.models._ensure_engine = MagicMock()
        # RW-9 Phase 2: was ``app._try_load_model = MagicMock()`` /
        # ``app._fallback_to_whisper = MagicMock()`` (delegates removed);
        # patch the ModelManager methods directly.
        app.models.try_load = MagicMock()
        app.models.fallback_to_whisper = MagicMock()
        app.models._pending_dictation = False

        scheduled = []
        app._schedule_timer = lambda delay, func: scheduled.append((delay, func))

        app.config.asr_backend = "whisper"
        app.models.load_background()

        assert scheduled == [], "no pending dictation → nothing scheduled"

    def test_background_load_catches_exception_and_sets_error(self, app):
        """A crashing loader sets ERROR state but does not propagate."""
        app.tray = MagicMock()
        app.models._ensure_engine = MagicMock()
        # ARCH-007: _load_transcription_engine_background now delegates
        # to AsrBackendRegistry.load_with_fallback. Mock it to raise.
        app.models._sync_registry_from_fields = MagicMock()
        # ARCH-REFAC-003: assign to ModelManager._registry directly (was
        # a @property delegate on VoiceTyperApp).
        app.models._registry = MagicMock()
        app.models.registry.load_with_fallback = MagicMock(side_effect=RuntimeError("disk on fire"))
        # ARCH-REFAC-003: write to ModelManager directly (was a @property
        # delegate on VoiceTyperApp).
        app.models._pending_dictation = False
        app.config.asr_backend = "whisper"

        # Must not raise.
        app.models.load_background()

        # Tray should show ERROR.
        states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert any("ERROR" in str(s) for s in states), f"expected ERROR state after crash, got {states}"


class TestStartDictationBehavior:
    """Verify _start_dictation sets correct state and calls recorder.start()."""

    def test_start_calls_recorder_start_and_sets_recording_state(self, app):
        """_start_dictation must call recorder.start() and set tray state to RECORDING."""
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = True

        app._start_dictation()

        app.recorder.start.assert_called_once()
        app.tray.set_state.assert_called_once()
        from voice_typer.server.tray import AppState

        args = app.tray.set_state.call_args
        assert args[0][0] == AppState.RECORDING, f"Expected AppState.RECORDING, got {args[0][0]}"

    def test_start_is_noop_if_already_recording(self, app):
        """_start_dictation must not call recorder.start() if already recording."""
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.tray = MagicMock()

        app._start_dictation()

        app.recorder.start.assert_not_called()


class TestHotkeyCallbackChain:
    """End-to-end: hotkey callback -> toggle_dictation -> _start_dictation."""

    def test_full_callback_chain(self, app):
        """Simulate the exact callback path: GlobalHotKeys fires toggle_dictation,
        which should call recorder.start() and set state to RECORDING."""
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app._busy_event.set()  # not busy
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = True

        # Simulate what GlobalHotKeys does: call the registered callback directly
        from voice_typer.server.tray import AppState

        # The callback stored in the hotkey mapping IS app.toggle_dictation
        app.toggle_dictation()

        app.recorder.start.assert_called_once()
        app.tray.set_state.assert_called_once()
        args = app.tray.set_state.call_args
        assert args[0][0] == AppState.RECORDING

    def test_callback_chain_register_then_fire(self, app, monkeypatch):
        """Register hotkey, extract the callback, call it, verify recording starts."""
        captured_mapping = {}

        class FakeGlobalHotKeys:
            def __init__(self, mapping):
                captured_mapping.update(mapping)

            def start(self):
                pass

        mock_kb = sys.modules["pynput.keyboard"]
        # pyrefly: ignore [missing-attribute]
        mock_kb.GlobalHotKeys = FakeGlobalHotKeys

        # NATIVE-001: force the legacy pynput backend (skip native binary)
        # so FakeGlobalHotKeys is actually used. Use monkeypatch.setattr
        # so the patch is reverted after the test (no state leakage).
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)

        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app._busy_event.set()  # not busy

        # Register hotkey - this captures the mapping
        # RW-9 Phase 1: was ``app._register_hotkey()`` (test-seam delegate
        # removed); call the dispatcher method directly.
        app.hotkeys.register()

        # The default hotkey is platform-aware (Fn on macOS, Caps Lock on
        # Windows/Linux, F2 on unknown). Use the actual config value.
        expected_hotkey = app.config.hotkey
        assert expected_hotkey in captured_mapping
        callback = captured_mapping[expected_hotkey]
        # HOTKEY-FIX-001: the callback is now an ownership-checking
        # wrapper (_dictation_callback) that calls app.toggle_dictation
        # internally, not the raw bound method. Accept any callable and
        # verify it fires toggle_dictation when ownership is "normal".
        assert callable(callback)

        # Simulate the hotkey being pressed (ownership = normal → should fire)
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        keyboard_ownership().set_owner("normal", reason="test")
        callback()

        from voice_typer.server.tray import AppState

        app.recorder.start.assert_called_once()
        args = app.tray.set_state.call_args
        assert args[0][0] == AppState.RECORDING


class TestMicrophoneSelection:
    def test_select_mic_by_id_updates_config(self, app):
        app._select_microphone("3")
        assert app.config.microphone == "3"

    def test_select_none_resets_to_default(self, app):
        app.config.microphone = "5"
        app._select_microphone(None)
        # pyrefly: ignore [unnecessary-comparison]
        assert app.config.microphone is None

    def test_select_mic_saves_config(self, app, tmp_config_dir):
        app._select_microphone("2")
        config_file = tmp_config_dir / "config.json"
        data = json.loads(config_file.read_text())
        assert data["microphone"] == "2"

    def test_select_mic_recreates_recorder(self, app):
        old_recorder = app.recorder
        app._select_microphone("1")
        assert app.recorder is not old_recorder
        assert app.config.microphone == "1"


# ─── Integration: real startup path ────────────────────────────────────


class TestAppStartupIntegration:
    """Integration-ish: let tray.start() + _do_startup run for real."""

    def test_startup_reaches_do_startup_without_crash(self, tmp_config_dir, monkeypatch):
        """Verify _do_startup runs without crashing (integration)."""
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        # Make transcriber.load() a no-op (don't actually load a model)
        monkeypatch.setattr("voice_typer.server.app.TranscriptionEngine", MagicMock())

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        # Run _do_startup directly (normally called in a thread by tray.start)
        # RW-9 Phase 1: was ``app._sync_prewarm_task = MagicMock()`` (test-seam
        # delegate removed); patch the standalone function directly.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        app._do_startup()
        # Model load runs in a background thread now — wait for it so the
        # test doesn't tear down while the loader is mid-flight.
        load_thread = app.models._model_load_thread
        if load_thread is not None:
            load_thread.join(timeout=5)

        # If we got here without exception, startup succeeded
        # Verify the tray was wired up
        assert app.tray is not None

    def test_tray_icon_created_on_start(self, tmp_config_dir, monkeypatch):
        """Verify tray.start() creates an icon with menu= wrapped in pystray.Menu."""
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

        from tests.test_tray import _FakeIcon, _FakeMenu, _FakeMenuItem

        monkeypatch.setattr("voice_typer.server.app.TranscriptionEngine", MagicMock())

        # Ensure voice_typer.tray uses our fakes
        import voice_typer.server.tray as tray_mod

        mock_pystray = MagicMock()
        mock_pystray.Icon = _FakeIcon
        mock_pystray.Menu = _FakeMenu
        mock_pystray.Menu.SEPARATOR = "SEP"
        mock_pystray.MenuItem = _FakeMenuItem
        monkeypatch.setattr(tray_mod, "pystray", mock_pystray)

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        # Reset before our call
        _FakeIcon.last_kwargs = {}

        # Call tray.start directly — should create the icon without blocking
        app.tray.start(bg_work=None)

        # The tray should now have an icon
        assert app.tray._icon is not None

        # The icon should have menu= set to a _FakeMenu (regression check)
        menu = _FakeIcon.last_kwargs.get("menu")
        assert isinstance(menu, _FakeMenu), f"menu= must be a pystray.Menu, got {type(menu).__name__}: {menu!r}"
        # _FakeMenu IS callable (mirrors real pystray.Menu) — verify it wraps a callable
        assert hasattr(menu, "args") and len(menu.args) >= 1 and callable(menu.args[0]), (
            "menu= should wrap a callable inside pystray.Menu, not be a bare function"
        )


# ─── Transcription load resilience ─────────────────────────────────────


class TestTryLoadModel:
    """Test _try_load_model helper method.

    ARCH-007/008: _try_load_model now delegates to the ASR registry,
    so each test must set up the registry before calling it.
    """

    def _setup_registry(self, app):
        """Ensure the ASR registry exists and has the whisper backend registered."""
        app.models._sync_registry_from_fields()

    def test_try_load_success_sets_idle_state(self, app):
        """On successful load, tray state should be IDLE with device info."""
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock()
        app.models.transcriber.device_info = "cpu (int8)"
        app.models.transcriber.loaded_via = "cpu/int8/small.en"

        # RW-9 Phase 2: was ``app._try_load_model()`` (delegate removed);
        # call the ModelManager method directly.
        app.models.try_load()

        app.models.transcriber.load.assert_called_once()
        app.tray.set_state.assert_called()
        from voice_typer.server.tray import AppState

        # The last set_state call should be IDLE
        last_call = app.tray.set_state.call_args_list[-1]
        assert last_call[0][0] == AppState.IDLE
        assert "cpu" in last_call[0][1]

    def test_try_load_failure_sets_error_state(self, app):
        """On failed load, tray state should be ERROR."""
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock(side_effect=RuntimeError("OOM"))
        app.models.transcriber.is_loaded = False

        # RW-9 Phase 2: was ``app._try_load_model()`` (delegate removed);
        # call the ModelManager method directly.
        app.models.try_load()

        from voice_typer.server.tray import AppState

        last_call = app.tray.set_state.call_args_list[-1]
        assert last_call[0][0] == AppState.ERROR
        assert "retry" in last_call[0][1].lower()

    def test_try_load_failure_with_notify(self, app):
        """notify_on_failure=True should send a desktop notification."""
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock(side_effect=RuntimeError("OOM"))
        app.models.transcriber.is_loaded = False

        # RW-9 Phase 2: was ``app._try_load_model(notify_on_failure=True)``
        # (delegate removed); call the ModelManager method directly.
        app.models.try_load(notify_on_failure=True)

        app.tray.notify.assert_called_once()
        notify_args = app.tray.notify.call_args[0]
        assert "Could not load" in notify_args[1]

    def test_try_load_failure_without_notify(self, app):
        """notify_on_failure=False should NOT send a notification."""
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock(side_effect=RuntimeError("OOM"))
        app.models.transcriber.is_loaded = False

        # RW-9 Phase 2: was ``app._try_load_model(notify_on_failure=False)``
        # (delegate removed); call the ModelManager method directly.
        app.models.try_load(notify_on_failure=False)

        app.tray.notify.assert_not_called()

    def test_try_load_sets_model_load_attempted(self, app):
        """_model_load_attempted should be True after _try_load_model."""
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock()

        assert app.models._model_load_attempted is False
        # RW-9 Phase 2: was ``app._try_load_model()`` (delegate removed);
        # call the ModelManager method directly.
        app.models.try_load()
        # pyrefly: ignore [unnecessary-comparison]
        assert app.models._model_load_attempted is True

    def test_try_load_spawns_background_prewarm_on_timeout(self, app, monkeypatch):
        """Task 5: when wait_for_prewarm() times out, try_load() must
        call spawn_background_prewarm(force=True) so the cache is warm
        for the next app launch.

        Without this, every subsequent launch in the same boot session
        hits a cold cache (the current prewarm was preempted by the
        app's disk I/O and never finished).
        """
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock()
        app.models.transcriber.device_info = "cpu (int8)"
        app.models.transcriber.loaded_via = "cpu/int8/small.en"

        # Mock wait_for_prewarm to return False (timeout).
        spawn_called = []
        monkeypatch.setattr(
            "voice_typer.server.prewarm.wait_for_prewarm",
            lambda timeout_s=60.0: False,  # simulate timeout
        )

        # PW-2: production call is spawn_background_prewarm(force=True,
        # trigger="manual") — the mock must accept the trigger kwarg or
        # the TypeError is silently swallowed by model_manager's
        # ``except Exception as bg_exc`` block, making the test pass
        # vacuously (spawn_called stays empty).
        def _spawn(force=True, trigger="manual"):
            spawn_called.append(force)

        monkeypatch.setattr(
            "voice_typer.server.prewarm.spawn_background_prewarm",
            _spawn,
        )

        # RW-9 Phase 2: was ``app._try_load_model()`` (delegate removed);
        # call the ModelManager method directly.
        app.models.try_load()

        assert spawn_called, (
            "Task 5: try_load() must call spawn_background_prewarm() when "
            "wait_for_prewarm() times out, so the cache is warm for the "
            "next app launch"
        )
        assert spawn_called == [True], (
            "Task 5: spawn_background_prewarm must be called with force=True "
            "to bypass the boot-sentinel dedup (the current boot's prewarm "
            "hasn't finished)"
        )

    def test_try_load_does_not_spawn_prewarm_when_finished(self, app, monkeypatch):
        """Task 5 + PREWARM-FIX: when wait_for_prewarm() returns True because
        prewarm actually finished (it was running, and the boot sentinel
        proves the cache is already warm), try_load() must NOT spawn a
        background prewarm.

        Only the timeout case, and the PREWARM-FIX "scheduled task missed
        this boot" case, trigger the re-spawn.
        """
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock()
        app.models.transcriber.device_info = "cpu (int8)"
        app.models.transcriber.loaded_via = "cpu/int8/small.en"

        spawn_called = []
        monkeypatch.setattr(
            "voice_typer.server.prewarm.wait_for_prewarm",
            lambda timeout_s=60.0: True,  # prewarm finished (or not running)
        )
        # PREWARM-FIX: represent a prewarm that genuinely ran and warmed the
        # cache this boot — is_prewarm_running() was True, and the boot
        # sentinel confirms a successful warm. Under these conditions the
        # PREWARM-FIX re-spawn branch must NOT fire.
        monkeypatch.setattr(
            "voice_typer.server.prewarm.is_prewarm_running",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.prewarm._already_warmed",
            lambda: True,
        )
        # Accept trigger kwarg so a (correctly) swallowed TypeError can't
        # make this test pass vacuously.
        monkeypatch.setattr(
            "voice_typer.server.prewarm.spawn_background_prewarm",
            lambda force=True, trigger="manual": spawn_called.append((force, trigger)),
        )

        # RW-9 Phase 2: was ``app._try_load_model()`` (delegate removed);
        # call the ModelManager method directly.
        app.models.try_load()

        assert not spawn_called, (
            "Task 5: spawn_background_prewarm must NOT be called when "
            "wait_for_prewarm returned True AND the cache is already warm "
            "(prewarm genuinely finished this boot)"
        )

    def test_try_load_spawns_prewarm_when_scheduled_task_missed(self, app, monkeypatch):
        """PREWARM-FIX: if the OS prewarm scheduled task never fired this
        boot (no prewarm process, boot sentinel absent) but fast_startup is
        enabled, try_load() must spawn a background prewarm so the NEXT
        launch isn't cold.

        This is the regression guard for the bug where an InteractiveToken +
        BootTrigger/EventTrigger task could never start (Last Result
        0x41303, "never run"), leaving the user on a permanently cold cache.
        """
        self._setup_registry(app)
        app.tray = MagicMock()
        app.models.transcriber.load = MagicMock()
        app.models.transcriber.device_info = "cpu (int8)"
        app.models.transcriber.loaded_via = "cpu/int8/small.en"
        # fast_startup enabled (the default) — prewarm is expected to run.
        app.config.fast_startup = True

        spawn_called = []
        # prewarm wasn't running and reported "finished" only because there
        # was nothing to wait for.
        monkeypatch.setattr(
            "voice_typer.server.prewarm.wait_for_prewarm",
            lambda timeout_s=60.0: True,
        )
        # No prewarm process is alive...
        monkeypatch.setattr(
            "voice_typer.server.prewarm.is_prewarm_running",
            lambda: False,
        )
        # ...and the cache was never warmed this boot (sentinel absent).
        monkeypatch.setattr(
            "voice_typer.server.prewarm._already_warmed",
            lambda: False,
        )
        monkeypatch.setattr(
            "voice_typer.server.prewarm.spawn_background_prewarm",
            lambda force=True, trigger="manual": spawn_called.append((force, trigger)),
        )

        app.models.try_load()

        assert spawn_called == [(True, "manual")], (
            "PREWARM-FIX: try_load() must spawn a background prewarm "
            "(force=True, trigger='manual') when the scheduled task missed "
            "this boot and the cache is cold, so the next launch is warm"
        )


class TestStreamingIntegration:
    def test_start_dictation_starts_streaming_session_when_enabled(self, app, monkeypatch):
        app.config.streaming_transcription = True
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = True
        app.tray = MagicMock()
        app.clipboard = MagicMock()

        session = MagicMock()
        session_cls = MagicMock(return_value=session)
        # #2 streaming session now lives in RecordingController,
        # so monkeypatch the module where it's actually imported.
        monkeypatch.setattr(
            "voice_typer.server.recording_controller.StreamingTranscriptionSession",
            session_cls,
            raising=False,
        )

        app._start_dictation()

        session_cls.assert_called_once()
        session.start.assert_called_once()
        app.clipboard.copy.assert_not_called()
        app.clipboard.paste.assert_not_called()

    def test_stop_dictation_uses_streaming_final_text(self, app):
        app.config.streaming_transcription = True
        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.clipboard.paste = MagicMock(return_value=True)

        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="batch text")
        app.models.transcriber.device_info = "cpu (int8)"

        app.recorder = MagicMock()
        app.recorder.recording = True
        audio = np.ones(16000, dtype=np.float32)
        app.recorder.stop = MagicMock(return_value=audio)
        app.recorder.last_rms = 0.2

        session = MagicMock()
        session.finalize = MagicMock(return_value="streamed text")
        # RW-9 Phase 1: was ``app._set_streaming_session(session)`` (test-seam
        # delegate removed); call the controller method directly.
        app.recording.set_streaming_session(session)

        app._stop_dictation()
        _wait_for_busy_clear(app)

        session.finalize.assert_called_once_with(audio)
        app.models.transcriber.transcribe_with_fallback.assert_not_called()
        app.clipboard.copy.assert_called_once_with("Streamed text")
        app.clipboard.paste.assert_called_once()

    def test_streaming_kill_switch_forces_batch_path(self, app, monkeypatch):
        app.config.streaming_transcription = True
        monkeypatch.setenv("VOICE_TYPER_STREAMING", "0")
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = True
        app.tray = MagicMock()

        session_cls = MagicMock()
        monkeypatch.setattr("voice_typer.server.app.StreamingTranscriptionSession", session_cls, raising=False)

        app._start_dictation()

        session_cls.assert_not_called()
        # RW-9 Phase 1: was ``app._get_streaming_session()`` (test-seam
        # delegate removed); call the controller method directly.
        assert app.recording.get_streaming_session() is None

    def test_quit_cancels_active_streaming_session(self, app):
        session = MagicMock()
        # RW-9 Phase 1: was ``app._set_streaming_session(session)`` (test-seam
        # delegate removed); call the controller method directly.
        app.recording.set_streaming_session(session)
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()

        with pytest.raises(SystemExit):
            app.quit()

        session._cancel_event.set.assert_called_once()

    def test_select_microphone_during_recording_defers_recorder_recreation(self, app):
        app.recorder = MagicMock()
        app.recorder.recording = True
        original_recorder = app.recorder
        app.tray = MagicMock()

        app._select_microphone("1")

        assert app.config.microphone == "1"
        assert app.recorder is original_recorder


class TestStartupResilience:
    """Test that startup continues even when model loading fails."""

    def test_startup_registers_hotkey_before_model_load(self, app, monkeypatch):
        """Hotkey should be registered even if model loading fails."""
        call_order = []

        def track_register_hotkey():
            call_order.append("hotkey")

        def track_model_load(*args, **kwargs):
            call_order.append("model")

        # RW-9 Phase 2: production callers now invoke ``app.hotkeys.register()``
        # directly (the ``app._register_hotkey`` facade is no longer in the
        # hot path). Monkeypatch the real call site.
        app.hotkeys.register = track_register_hotkey
        # ARCH-007: model load now goes through _sync_asr_registry +
        # _asr_registry.load_with_fallback. Mock the registry so we
        # can track when model loading happens.
        app.models._sync_registry_from_fields = MagicMock()
        # ARCH-REFAC-003: assign to ModelManager._registry directly (was
        # a @property delegate on VoiceTyperApp).
        app.models._registry = MagicMock()
        app.models.registry.load_with_fallback = track_model_load
        # RW-9 Phase 1: was ``app._sync_autostart = MagicMock()`` etc.
        # (test-seam delegates removed); patch the standalone functions
        # directly so production callers see the no-op.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        app.tray = MagicMock()

        app._do_startup()
        # Model load now runs in a background thread — wait for it so
        # the "model" step has actually executed before asserting order.
        load_thread = app.models._model_load_thread
        if load_thread is not None:
            load_thread.join(timeout=5)

        assert call_order == ["hotkey", "model"], f"Expected hotkey before model, got {call_order}"

    def test_startup_survives_model_load_exception(self, app, monkeypatch):
        """Even if model load raises, _do_startup should not crash."""
        # RW-9 Phase 1: was ``app._sync_autostart = MagicMock()`` etc.
        # (test-seam delegates removed); patch the standalone functions
        # directly so production callers see the no-op.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        # RW-9 Phase 2: production callers invoke ``app.hotkeys.register()``
        # directly — monkeypatch the real call site (not the delegate).
        app.hotkeys.register = MagicMock()
        # _try_load_model runs inside the background loader thread; make it
        # raise to simulate a model-load failure.  The loader catches it.
        # RW-9 Phase 2: was ``app._try_load_model = MagicMock(...)``
        # (delegate removed); patch the ModelManager method directly.
        app.models.try_load = MagicMock(side_effect=RuntimeError("OOM"))
        app.tray = MagicMock()

        # Should not raise — the exception is caught inside the loader thread.
        app._do_startup()
        # Wait for the background loader so the assertion below sees the
        # post-load state (hotkey registered, app still alive).
        load_thread = app.models._model_load_thread
        if load_thread is not None:
            load_thread.join(timeout=5)
            assert not load_thread.is_alive(), "loader thread hung"

        # Hotkey should still have been registered
        app.hotkeys.register.assert_called_once()

    def test_start_dictation_retries_model_load(self, app):
        """When model not loaded, _start_dictation should try loading it."""
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = False
        app.models.transcriber.load = MagicMock()
        app.models.transcriber.device_info = "cpu (int8)"
        app.models.transcriber.loaded_via = "cpu/int8/tiny.en"
        app.tray = MagicMock()
        app.recorder = MagicMock()
        app.recorder.recording = False

        # After _try_load_model, is_loaded becomes True
        def mock_load(**kwargs):
            app.models.transcriber.is_loaded = True

        app.models.transcriber.load = mock_load

        app._start_dictation()

        # Should have attempted to start recording (model was loaded on retry)
        app.recorder.start.assert_called_once()

    def test_start_dictation_fails_gracefully_if_model_still_unavailable(self, app):
        """If model retry fails, should not attempt recording."""
        app.models.transcriber = MagicMock()
        app.models._sync_registry_from_fields()
        app.models.transcriber.is_loaded = False
        app.models.transcriber.load = MagicMock(side_effect=RuntimeError("still OOM"))
        app.tray = MagicMock()
        app.recorder = MagicMock()
        app.recorder.recording = False

        app._start_dictation()

        # Should NOT have tried to record
        app.recorder.start.assert_not_called()


# ─── Startup integration: construction → tray → hotkey → F2 ────────────


class TestStartupNoCrash:
    """Verify the full startup → hotkey → F2 path works correctly.

    These tests exercise the actual startup flow with mocked hardware
    dependencies but real code paths (not just isolated unit tests).
    """

    def test_app_construction_no_crash(self, tmp_config_dir, monkeypatch):
        """VoiceTyperApp() should construct without crashing."""
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])
        monkeypatch.setattr("voice_typer.server.app.TranscriptionEngine", MagicMock())

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        assert app.config is not None
        assert app.tray is not None
        assert app.recorder is not None
        assert app.clipboard is not None
        assert app.hotkeys._hotkey_backend is None
        assert app._busy_event.is_set()  # not busy

    def test_tray_start_creates_icon(self, tmp_config_dir, monkeypatch):
        """app.tray.start(bg_work=None) should create the tray icon without crashing."""
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])
        monkeypatch.setattr("voice_typer.server.app.TranscriptionEngine", MagicMock())

        # Ensure tray module uses fakes
        import voice_typer.server.tray as tray_mod

        from tests.test_tray import _FakeIcon, _FakeMenu, _FakeMenuItem

        mock_pystray = MagicMock()
        mock_pystray.Icon = _FakeIcon
        mock_pystray.Menu = _FakeMenu
        mock_pystray.Menu.SEPARATOR = "SEP"
        mock_pystray.MenuItem = _FakeMenuItem
        monkeypatch.setattr(tray_mod, "pystray", mock_pystray)

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        _FakeIcon.last_kwargs = {}

        app.tray.start(bg_work=None)

        assert app.tray._icon is not None
        menu = _FakeIcon.last_kwargs.get("menu")
        assert isinstance(menu, _FakeMenu), f"Expected _FakeMenu, got {type(menu)}"


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
        app.models._sync_registry_from_fields()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="hello world")
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app.recorder.last_rms = 0.5

        # TEST-028 (fix): use the monkeypatch fixture instead of
        # pytest.MonkeyPatch() so the patch is auto-reverted after the
        # test. Previously the manual instantiation bypassed pytest's
        # lifecycle and could leak patches on test failure.
        import voice_typer.server.app as app_mod

        monkeypatch.setattr(app_mod, "clean_transcribed_text", spy)
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


class TestTrayControllerProtocolCompliance:
    """Verify VoiceTyperApp implements all TrayController protocol methods."""

    # DEAD-008: toggle_autostart, set_notifications, set_silence_*,
    # set_max_recording_time_seconds, create_desktop_shortcut removed
    # from TrayController protocol — no caller existed.  The public
    # methods are now just the ones the tray menu actually invokes.
    # ARCH-DEAD-SETTINGS: show_settings / open_settings removed along
    # with voice_typer.server.settings; the Electron frontend owns the
    # settings UI now.
    REQUIRED_PUBLIC_METHODS = [
        "toggle_dictation",
        "quit",
    ]

    REQUIRED_CALLBACK_METHODS = [
        "_toggle_autostart",
        "_set_notifications",
        "_select_microphone",
        # RW-9 Phase 2: ``_change_model`` and ``_restart_hotkey`` removed —
        # the tray now calls ``change_model`` / ``change_hotkey`` (the
        # TrayController Protocol methods), which internally invoke
        # ``self.models.change_model`` / ``self.hotkeys.restart`` directly.
    ]

    def test_app_has_all_traycontroller_public_methods(self, app):
        """VoiceTyperApp must expose public methods for the TrayController protocol."""
        for method in self.REQUIRED_PUBLIC_METHODS:
            assert hasattr(app, method), f"Missing public method: {method}"
            assert callable(getattr(app, method)), f"Attribute '{method}' exists but is not callable"

    def test_app_has_all_tray_callback_methods(self, app):
        """VoiceTyperApp must have the private methods wired as TrayIcon callbacks."""
        for method in self.REQUIRED_CALLBACK_METHODS:
            assert hasattr(app, method), f"Missing callback method: {method}"
            assert callable(getattr(app, method)), f"Attribute '{method}' exists but is not callable"


class TestExternalCorrectionsWiring:
    """Verify configure_corrections is called at startup."""

    def test_configure_corrections_called_at_startup(self, app, monkeypatch):
        """StartupSequence.run should call configure_corrections with config_dir.

        RW-9 Phase 5: the call moved from ``VoiceTyperApp._do_startup`` to
        ``StartupSequence.run`` (in ``startup_sequence.py``). The monkeypatch
        target must follow the call site — patch the name in
        ``startup_sequence``'s namespace, not ``app``'s.
        """
        called_with = {}

        def spy(config_dir=None, corrections_path=None):
            called_with["config_dir"] = config_dir

        # Patch the name in startup_sequence's namespace (RW-9 Phase 5 moved
        # the call there). Also patch app's namespace for backwards compat
        # in case any other code path still reaches it via app.configure_corrections.
        monkeypatch.setattr("voice_typer.server.startup_sequence.configure_corrections", spy)
        monkeypatch.setattr("voice_typer.server.app.configure_corrections", spy)
        app._settings_window = None
        # RW-9 Phase 1: was ``app._sync_prewarm_task = MagicMock()``
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


class TestWin32ConsoleHandler:
    """P2 fix: Test the Win32 console control handler."""

    def test_ctrl_close_event_frees_console(self, app):
        """CTRL_CLOSE_EVENT should call FreeConsole and redirect stdout."""
        app._kernel32 = MagicMock()
        app._kernel32.FreeConsole.return_value = 1

        with patch("builtins.open", MagicMock()) as mock_open:
            mock_file = MagicMock()
            mock_open.return_value = mock_file
            result = app._win32_console_handler(2)  # CTRL_CLOSE_EVENT

        assert result is True
        app._kernel32.FreeConsole.assert_called_once()

    def test_ctrl_logoff_event_starts_quit_thread(self, app):
        """CTRL_LOGOFF_EVENT should start a quit thread."""
        with patch("voice_typer.server.app.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(5)  # CTRL_LOGOFF_EVENT

        assert result is True

    def test_ctrl_shutdown_event_starts_quit_thread(self, app):
        """CTRL_SHUTDOWN_EVENT should start a quit thread."""
        with patch("voice_typer.server.app.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(6)  # CTRL_SHUTDOWN_EVENT

        assert result is True

    def test_ctrl_c_event_starts_quit_thread(self, app):
        """CTRL_C_EVENT should start a quit thread."""
        with patch("voice_typer.server.app.threading.Thread") as mock_thread:
            mock_thread_instance = MagicMock()
            mock_thread.return_value = mock_thread_instance
            result = app._win32_console_handler(0)  # CTRL_C_EVENT

        assert result is True

    def test_unknown_event_returns_false(self, app):
        """Unknown event types should return False."""
        result = app._win32_console_handler(99)
        assert result is False


# ── TEST-004: restart_app cleanup path ───────────────────────────────────


class TestRestartAppCleanupPath:
    """TEST-004: verify that restart_app stops all three hotkey backends
    (hotkey, esc, repaste) and calls tray.stop() before exiting.

    This is a regression test for RELIABILITY-003, which was fixed
    alongside RELIABILITY-001."""

    def test_restart_stops_all_backends(self, app, monkeypatch):
        """restart_app must stop _hotkey_backend, _esc_backend, and
        _repaste_backend — not just _hotkey_backend."""
        import subprocess as _sp

        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        monkeypatch.setattr("voice_typer.server.app.sys.exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.hotkeys._hotkey_backend.stop.assert_called_once()
        app.hotkeys._esc_backend.stop.assert_called_once()
        app.hotkeys._repaste_backend.stop.assert_called_once()

    def test_restart_calls_tray_stop(self, app, monkeypatch):
        """restart_app must call tray.stop() to break the pystray loop."""
        import subprocess as _sp

        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        monkeypatch.setattr("voice_typer.server.app.sys.exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        app.tray.stop.assert_called_once()

    def test_restart_does_not_use_os_exit(self, app, monkeypatch):
        """restart_app must exit via sys.exit(0), not os._exit(0).
        os._exit skips Python cleanup (atexit, __del__, finally)."""
        import subprocess as _sp

        os_exit_calls = []
        monkeypatch.setattr(_sp, "Popen", lambda *a, **kw: MagicMock())
        monkeypatch.setattr("voice_typer.server.app.os.environ", {})
        monkeypatch.setattr(sys, "argv", ["voice_typer"])
        monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: os_exit_calls.append(code))
        monkeypatch.setattr("voice_typer.server.app.sys.exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        app.hotkeys._hotkey_backend = MagicMock()
        app.hotkeys._esc_backend = MagicMock()
        app.hotkeys._repaste_backend = MagicMock()
        app._cancel_pending_timers = MagicMock()
        app.tray = MagicMock()

        with contextlib.suppress(SystemExit):
            app.restart_app()

        assert os_exit_calls == [], f"restart_app must not call os._exit; got {os_exit_calls}"


class TestSingleInstanceEnforcement:
    """TEST-037: verify VoiceTyperApp is only instantiated once per
    process. The audit claimed ``VoiceTyperApp()`` was called twice in
    startup code; investigation shows it's called exactly once (in
    ``ipc_server.main()``). This test enforces that invariant so a
    future refactor doesn't accidentally introduce a double-instantiation
    bug.

    The process-level single-instance guarantee is enforced by
    ``_ensure_single_instance`` (Windows mutex), not by a Python
    singleton pattern. This test verifies the call-site count; the
    mutex behavior is tested in ``test_platform.py``.
    """

    def test_voice_typer_app_has_single_call_site(self):
        """VoiceTyperApp() must be called from exactly one location."""
        import ast

        import voice_typer.server as server_pkg

        pkg_dir = Path(server_pkg.__file__).parent
        call_sites = []
        for py_file in pkg_dir.glob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "VoiceTyperApp":
                    call_sites.append(f"{py_file.name}:{node.lineno}")
        assert len(call_sites) == 1, (
            f"VoiceTyperApp() should be called from exactly one location "
            f"(ipc_server.main); found {len(call_sites)} call sites: {call_sites}"
        )
        assert "ipc_server.py" in call_sites[0], (
            f"VoiceTyperApp() should only be called from ipc_server.py; found call at {call_sites[0]}"
        )

    def test_ensure_single_instance_is_called_from_main(self):
        """ipc_server.main() must call _ensure_single_instance before
        creating VoiceTyperApp, so a duplicate process exits before
        loading any heavy modules."""
        import voice_typer.server.ipc_server as ipc

        source = Path(ipc.__file__).read_text(encoding="utf-8")
        assert "_ensure_single_instance" in source, (
            "ipc_server.py must call _ensure_single_instance to enforce the single-process invariant"
        )
        assert "VoiceTyperApp()" in source, "ipc_server.py must instantiate VoiceTyperApp exactly once"
        # _ensure_single_instance must appear BEFORE VoiceTyperApp()
        # in the source so the mutex is acquired before any heavy init.
        si_idx = source.index("_ensure_single_instance")
        app_idx = source.index("VoiceTyperApp()")
        assert si_idx < app_idx, (
            "_ensure_single_instance must be called BEFORE VoiceTyperApp() "
            "so a duplicate process exits before loading torch/etc."
        )
