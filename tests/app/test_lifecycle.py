"""split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

import contextlib
import inspect
import logging
import sys
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _wait_for_busy_clear(app, timeout=10.0):
    """Poll until app._busy_event is set (not busy).

    Replaces bare time.sleep() calls that cause flaky failures under load.

    TEST-033 (fix): poll interval reduced from 50ms to 5ms to speed up
    the test suite. With ~100 call sites, this saves ~4.5s of cumulative
    sleep time across a full run.

    The generous default deadline absorbs slow shared CI runners: a full
    stop → transcription-thread → busy-reset cycle spawns real threads,
    and on a loaded macos-14 xdist worker it can exceed 2s wall-clock.
    The wait is still event-driven — it returns the moment the event is
    set, so the larger ceiling costs nothing on fast machines.
    """
    deadline = time.monotonic() + timeout
    while not app._busy_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not app._busy_event.is_set():
        raise TimeoutError(f"_busy_event still not set after {timeout}s")


class TestAppStateTransitions:
    def test_initial_state_is_idle(self, app):
        # (fix): removed redundant `assert not not` — the
        # following assert is a strict superset.
        assert app._busy_event.is_set()  # event is SET when not busy
        assert app.recorder.recording is False

    def test_start_dictation_sets_recording(self, app):
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.recorder.start = MagicMock()
        app.tray = MagicMock()
        app.models.transcriber = MagicMock()
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

        app.models.transcriber = MagicMock()

        app.recorder = MagicMock()
        app.recorder.recording = True
        # Return 0.1s of audio (less than 0.5s threshold)
        app.recorder.stop = MagicMock(return_value=np.zeros(int(0.1 * 16000), dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)
        assert app._busy_event.is_set()
        # the short-audio branch must NOT call into the transcription
        # engine at all (duration < 0.5s short-circuits before the
        # transcription thread is started in RecordingController.stop).
        app.models.transcriber.transcribe_with_fallback.assert_not_called()

    def test_transcribe_success_copies_to_clipboard(self, app, monkeypatch):
        app.clipboard = MagicMock()
        app.clipboard.copy = MagicMock(return_value=True)
        app.clipboard.paste = MagicMock(return_value=False)

        app.models.transcriber = MagicMock()
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
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="can we test this this now")
        app.models.transcriber.device_info = "cuda/float16/small.en"

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        # Forced terminal punctuation was removed from the pipeline.
        # auto_punctuation now defaults to True, so the
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
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="")

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        app.clipboard.copy.assert_not_called()

    def test_transcribe_failure_shows_error(self, app):
        from voice_typer.server.tray_types import AppState

        app.models.transcriber = MagicMock()
        app.models.transcriber.transcribe_with_fallback = MagicMock(side_effect=Exception("model crash"))

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        # mock the tray so the ERROR-state transition is observable.
        # The dictation pipeline's except handler calls
        # ``tray.set_state(AppState.ERROR, "Transcription failed")``.
        app.tray = MagicMock()

        app._stop_dictation()

        _wait_for_busy_clear(app)

        # Should not crash; error state should be set
        assert app._busy_event.is_set()
        # verify the ERROR tray state was actually entered (the
        # dictation pipeline's except handler must call
        # tray.set_state(AppState.ERROR, ...) — previously this test
        # never checked that the ERROR state was reached). The tooltip
        # must carry the mapped reason (generic exceptions fall back to
        # "Transcription failed (…)…"), not the bare state label.
        error_calls = [c.args for c in app.tray.set_state.call_args_list if c.args[0] == AppState.ERROR]
        assert error_calls, "dictation failure must enter the ERROR tray state"
        assert any("Transcription failed" in str(args[1]) for args in error_calls), (
            "ERROR tooltip must carry the transcription-failure reason. "
            f"Got set_state calls: {app.tray.set_state.call_args_list}"
        )

    def test_transcribe_cuda_fallback_clears_busy(self, app):
        """When GPU transcription fails with CUDA error, fallback to CPU succeeds
        and _busy is still cleared.

        WR-2: previously this test used ``return_value="fallback worked"``
        which always succeeded on the first call — the CUDA-fallback path
        (catch RuntimeError, retry on CPU) was never exercised. Now we use
        ``side_effect=[RuntimeError("CUDA error"), "fallback worked"]``
        so the first call simulates a CUDA failure and the second call
        succeeds, mirroring the production GPU→CPU retry.

        NOTE: the GPU→CPU retry logic lives inside
        ``TranscriptionEngine._transcribe_with_fallback_unlocked``
        (transcription.py:941).  The test fixture replaces the entire
        transcriber with a MagicMock, so the internal fallback is
        bypassed.  This test verifies that when the mocked
        ``transcribe_with_fallback`` returns successfully after a
        simulated first-call CUDA failure, busy is cleared.  The actual
        CUDA-fallback path is exercised by
        ``TestTranscribeWithFallback`` in test_transcription.py.
        """
        app.models.transcriber = MagicMock()
        # first call raises CUDA error, second call succeeds —
        # exercises the fallback path (production catches the exception
        # and retries; here the mock just returns the second value).
        app.models.transcriber.transcribe_with_fallback = MagicMock(
            side_effect=[RuntimeError("CUDA error"), "fallback worked"]
        )
        app.models.transcriber.device_info = "cpu (int8)"

        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))

        app._stop_dictation()

        _wait_for_busy_clear(app)

        assert app._busy_event.is_set()
        # The mock must have been called (at least once) by _transcribe().
        # With side_effect=[RuntimeError, success], production's
        # _transcribe would catch the RuntimeError and retry — the mock
        # is called twice in that path.
        assert app.models.transcriber.transcribe_with_fallback.called, (
            "WR-2: transcribe_with_fallback must be invoked by _transcribe"
        )

    def test_force_recover_resets_busy(self, app):
        """_force_recover_from_stuck_transcription clears _busy and resets tray."""
        app._busy_event.clear()  # set busy
        app.tray = MagicMock()

        # Phase 1: was ``app._force_recover_from_stuck_transcription()``
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
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="test")
        app.models.transcriber.device_info = "cpu (int8)"
        app.recorder = MagicMock()
        app.recorder.recording = True
        app.recorder.stop = MagicMock(return_value=np.ones(16000, dtype=np.float32))
        app.recorder.last_rms = 0.5

        app._stop_dictation()

        # Must set transcribing state instead of hiding
        app._waveform_bubble.set_state.assert_called_once_with("transcribing")
        # Must NOT hide the bubble synchronously during stop (it stays visible
        # during transcription). The pipeline's async completion will call hide()
        # once transcription finishes — that is tested separately.
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

        # Phase 1: was ``app._force_recover_from_stuck_transcription()``
        # (test-seam delegate removed); call the controller method directly.
        app.recording._force_recover_from_stuck_transcription()

        # No state change should have been made
        app.tray.set_state.assert_not_called()

    def test_force_recover_does_not_clear_busy_while_thread_is_alive(self, app):
        """Watchdog must not allow a second transcription while the old one runs."""
        app._busy_event.clear()  # busy
        app.tray = MagicMock()
        # write to RecordingController directly (was a
        # @property delegate on VoiceTyperApp).
        app.recording._transcription_thread = MagicMock()
        app.recording._transcription_thread.is_alive.return_value = True

        # Phase 1: was ``app._force_recover_from_stuck_transcription()``
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
        app.models.transcriber.transcribe_with_fallback.assert_called_once()

    def test_stop_dictation_emits_recording_stopped_event(self, app, monkeypatch):
        """SOUND-FIX-005: ``app._stop_dictation`` must emit the
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
        # dictation-pipeline refactor: ``RecordingController.stop``
        # now drives a real ``DictationPipeline`` that calls
        # ``transcriber.transcribe_with_fallback`` and pipes the result
        # through ``clean_transcribed_text`` / vocabulary / auto-punct.
        # The conftest ``app`` fixture leaves ``transcriber`` as a bare
        # ``MagicMock()`` whose ``transcribe_with_fallback`` returns a
        # child MagicMock — the cleanup chain then raises
        # ``TypeError: expected string or bytes-like object, got
        # 'MagicMock'`` inside the transcription thread. Stub the
        # transcriber to return a real string so the pipeline completes
        # cleanly and the ``recording_stopped`` event can be observed.
        app.models.transcriber = MagicMock()
        app.models.transcriber.transcribe_with_fallback = MagicMock(return_value="hello world")
        app.models.transcriber.is_loaded = True
        app.models.transcriber.device_info = "cpu (int8)"

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


class TestAppStartupIntegration:
    """Integration-ish: let tray.start() + _do_startup run for real."""

    def test_startup_reaches_do_startup_without_crash(self, tmp_config_dir, monkeypatch):
        """Verify _do_startup runs without crashing (integration)."""
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        # Make transcriber.load() a no-op (don't actually load a model)
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", MagicMock())

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        # Run _do_startup directly (normally called in a thread by tray.start)
        # Phase 1: was ``app._sync_prewarm_task = MagicMock()`` (test-seam
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
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from tests.test_tray import _FakeIcon, _FakeMenu, _FakeMenuItem

        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", MagicMock())

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


class TestStartupResilience:
    """Test that startup continues even when model loading fails."""

    def test_startup_registers_hotkey_before_model_load(self, app, monkeypatch):
        """Hotkey should be registered even if model loading fails."""
        call_order = []

        def track_register_hotkey():
            call_order.append("hotkey")

        def track_model_load(*args, **kwargs):
            call_order.append("model")

        # Phase 2: production callers now invoke ``app.hotkeys.register()``
        # directly (the ``app._register_hotkey`` facade is no longer in the
        # hot path). Monkeypatch the real call site.
        app.hotkeys.register = track_register_hotkey
        # model load now goes through _sync_asr_registry +
        # _asr_registry.load_with_fallback. Mock the registry so we
        # can track when model loading happens.
        # assign to ModelManager._registry directly (was
        # a @property delegate on VoiceTyperApp).
        app.models._registry = MagicMock()
        app.models.registry.load_with_fallback = track_model_load
        # The model-not-downloaded precheck would refuse the load before
        # it reaches load_with_fallback (no real model on disk in tests),
        # breaking the hotkey-before-model ordering assertion. Stub it
        # so the load path runs.
        app.models._model_downloaded_precheck = lambda: True
        # Phase 1: was ``app._sync_autostart = MagicMock()`` etc.
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
        # Phase 1: was ``app._sync_autostart = MagicMock()`` etc.
        # (test-seam delegates removed); patch the standalone functions
        # directly so production callers see the no-op.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_prewarm_task", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        # Phase 2: production callers invoke ``app.hotkeys.register()``
        # directly — monkeypatch the real call site (not the delegate).
        app.hotkeys.register = MagicMock()
        # _try_load_model runs inside the background loader thread; make it
        # raise to simulate a model-load failure.  The loader catches it.
        # Phase 2: was ``app._try_load_model = MagicMock(...)``
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
        """If model retry fails, should discard recording (AB-9)."""
        app.models.transcriber = MagicMock()
        app.models.transcriber.is_loaded = False
        app.models.transcriber.load = MagicMock(side_effect=RuntimeError("still OOM"))
        app.tray = MagicMock()
        app.recorder = MagicMock()
        app.recorder.recording = False
        # The real Recorder flips ``recording`` to True inside
        # ``start()``; the MagicMock does not, so simulate it — the
        # start path re-checks ``app.recorder.recording`` after the
        # model load and aborts post-load steps (including the
        # model-fail discard) when it is False.
        app.recorder.start = MagicMock(side_effect=lambda *a, **kw: setattr(app.recorder, "recording", True))

        app._start_dictation()

        # recorder.start() is called first (to buffer audio), then
        # model loading is attempted. When model still fails, the recorder
        # is discarded to avoid leaking the mic stream.
        app.recorder.start.assert_called_once()
        app.recorder.discard.assert_called_once()
        assert app.recorder.recording is False


# ─── Startup integration: construction → tray → hotkey → F2 ────────────


class TestStartupNoCrash:
    """Verify the full startup → hotkey → F2 path works correctly.

    These tests exercise the actual startup flow with mocked hardware
    dependencies but real code paths (not just isolated unit tests).
    """

    def test_app_construction_no_crash(self, tmp_config_dir, monkeypatch):
        """VoiceTyperApp() should construct without crashing."""
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", MagicMock())

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
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
        monkeypatch.setattr("voice_typer.server.transcription.TranscriptionEngine", MagicMock())

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


class TestAppInitManagerFailureWarning:
    """APP-8: ``__init__`` previously swallowed
    ``TemplateManager``/``VocabularyManager`` init failures at
    ``log.debug`` level, making them effectively invisible in
    production logs (default level is INFO). The fix bumps to
    ``log.warning`` with ``exc_info=True``.

    AB-30: construction is now LAZY — moved out of ``__init__`` into
    the dictation-pipeline steps (``text_steps._apply_templates`` /
    ``_apply_vocabulary``), which construct on first access and log
    ``[PIPELINE] ... failed`` at WARNING with ``exc_info=True`` when
    construction raises (DJ-2 kept the app-level ``_template_manager``
    / ``_vocabulary_manager`` properties as plain backings)."""

    # ─── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_pipeline(app):
        """Build a DictationPipeline shell wired to ``app`` so the
        text-step mixin methods can be exercised directly."""
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        pipeline._app = app
        pipeline._templates_applied = False
        return pipeline

    @staticmethod
    def _make_app(monkeypatch, tmp_config_dir):
        """Minimal app with the lazy manager backings + tray + config
        the pipeline steps read."""
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        app.tray = MagicMock()
        app.config.templates_enabled = True
        app.config.vocabulary_enabled = True
        return app

    def test_template_manager_failure_logged_at_warning(self, monkeypatch, caplog, tmp_path):
        """When TemplateManager construction raises inside
        ``_apply_templates``, the exception must be logged at WARNING
        level (not debug) and the app backing must stay None so the
        next cycle retries construction."""
        import logging

        import voice_typer.server.templates as templates_mod

        app = self._make_app(monkeypatch, tmp_path)

        original_tm = templates_mod.TemplateManager

        class _FailingTemplateManager(original_tm):
            def __init__(self, *a, **kw):
                raise RuntimeError("template init exploded")

        monkeypatch.setattr(templates_mod, "TemplateManager", _FailingTemplateManager)

        pipeline = self._make_pipeline(app)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._apply_templates("hello world")

        try:
            template_records = [
                r
                for r in caplog.records
                if "Template matching failed" in r.message or "Template matching failed" in str(r.getMessage())
            ]
            assert template_records, "APP-8: template lazy-init failure must be logged at WARNING"
            rec = template_records[0]
            assert rec.levelno == logging.WARNING, (
                f"APP-8: template init failure must be logged at WARNING level "
                f"(got {rec.levelname}); previously it was swallowed at debug "
                f"level, making failures invisible."
            )
            assert rec.exc_info is not None, (
                "APP-8: template init failure log must include exc_info=True "
                "so the stack trace is captured for diagnosis"
            )
            assert result == "hello world", (
                "The pipeline must return the original text when template "
                "matching fails (dictation completes un-transformed)."
            )
            assert app._template_manager is None, (
                "APP-8: on failure, _template_manager backing must stay None so the next cycle retries construction."
            )
        finally:
            templates_mod.TemplateManager = original_tm

    def test_vocabulary_manager_failure_logged_at_warning(self, monkeypatch, caplog, tmp_path):
        """When VocabularyManager construction raises inside
        ``_apply_vocabulary``, the exception must be logged at WARNING
        level (not debug) and the app backing must stay None so the
        next cycle retries construction."""
        import logging

        import voice_typer.server.vocabulary as vocab_mod

        app = self._make_app(monkeypatch, tmp_path)

        original_vm = vocab_mod.VocabularyManager

        class _FailingVocabularyManager(original_vm):
            def __init__(self, *a, **kw):
                raise RuntimeError("vocabulary init exploded")

        monkeypatch.setattr(vocab_mod, "VocabularyManager", _FailingVocabularyManager)

        pipeline = self._make_pipeline(app)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._apply_vocabulary("hello world")

        try:
            vocab_records = [
                r
                for r in caplog.records
                if "Vocabulary correction failed" in r.message or "Vocabulary correction failed" in str(r.getMessage())
            ]
            assert vocab_records, "APP-8: vocabulary lazy-init failure must be logged at WARNING"
            rec = vocab_records[0]
            assert rec.levelno == logging.WARNING, (
                f"APP-8: vocabulary init failure must be logged at WARNING level (got {rec.levelname})"
            )
            assert rec.exc_info is not None, (
                "APP-8: vocabulary init failure log must include exc_info=True so the stack trace is captured"
            )
            assert result == "hello world", (
                "The pipeline must return the original text when vocabulary "
                "correction fails (dictation completes un-transformed)."
            )
            assert app._vocabulary_manager is None, (
                "APP-8: on failure, _vocabulary_manager backing must stay None so the next cycle retries construction."
            )
        finally:
            vocab_mod.VocabularyManager = original_vm


class TestAppExcepthookInstallGuard:
    """``_crash_handler.install_python_excepthook()`` was called
    unconditionally in ``__init__``. If the install path raised (e.g.
    a missing Win32 API on an unsupported build), VoiceTyperApp
    construction would fail entirely. The fix wraps the call in
    try/except so the excepthook is a best-effort diagnostics aid."""

    def test_excepthook_install_failure_does_not_break_init(self, monkeypatch, tmp_config_dir):
        """If install_python_excepthook raises, VoiceTyperApp must
        still construct successfully (the excepthook is best-effort)."""
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server import crash_handler

        def _boom():
            raise RuntimeError("excepthook install exploded")

        monkeypatch.setattr(crash_handler, "install_python_excepthook", _boom)

        from voice_typer.server.app import VoiceTyperApp

        app_instance = VoiceTyperApp()
        assert app_instance is not None
        assert app_instance._shutting_down is False

    def test_excepthook_install_failure_logged_at_debug(self, monkeypatch, caplog, tmp_config_dir):
        """The excepthook-install failure must be logged at debug level
        with exc_info=True so it's diagnosable but doesn't spam the
        default-INFO production log."""
        import logging

        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        from voice_typer.server import crash_handler

        def _boom():
            raise RuntimeError("excepthook install exploded")

        monkeypatch.setattr(crash_handler, "install_python_excepthook", _boom)

        from voice_typer.server.app import VoiceTyperApp

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            VoiceTyperApp()

        hook_records = [r for r in caplog.records if "excepthook install failed" in r.message]
        assert hook_records, "excepthook install failure must be logged at debug level so the failure is diagnosable"
        rec = hook_records[0]
        assert rec.levelno == logging.DEBUG, (
            f"excepthook install failure must be logged at DEBUG "
            f"level (got {rec.levelname}); the excepthook is best-effort "
            f"and shouldn't spam the production INFO log."
        )
        assert rec.exc_info is not None, "excepthook install failure log must include exc_info=True"

    def test_excepthook_install_source_has_try_except(self):
        """Source-level invariant: the threading/crash-init builder must
        wrap the ``install_python_excepthook()`` call in a try/except
        block. (The call lives in ``_init_threading_and_crash`` since
        the ``__init__`` decomposition — the builder is the new home of
        the former inline ``__init__`` body.)"""
        import inspect

        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp._init_threading_and_crash)
        call_idx = src.find("_crash_handler.install_python_excepthook()")
        assert call_idx != -1, "init must call _crash_handler.install_python_excepthook()"
        before = src[:call_idx].rstrip()
        after = src[call_idx:]
        try_idx = before.rfind("try:")
        assert try_idx != -1, (
            "the excepthook install must be wrapped in a try/except block so a failure doesn't abort construction"
        )
        line_end = after.find("\n")
        rest = after[line_end + 1 :]
        except_idx = rest.find("except")
        assert except_idx != -1 and except_idx < 200, (
            "install_python_excepthook() call must be followed by an except clause within a few lines"
        )


# ==============================================================================
# Merged from tests/test_app_lifecycle_fixes.py —
#   targeted VoiceTyperApp regression pins (config.save raise inside restart_app, Config.load raise in __init__,
#   Event-based re-entry guards, main() wrapping ipc_main)
# ==============================================================================
# DE-2I (Group 4): targeted regression tests for four fixes in
# ``voice_typer/server/app.py``.
#
# Each test class exercises one finding:
#
# * ``DE-47`` — ``restart_app`` must not abort the restart sequence if
# ``self.config.save()`` raises an unexpected exception (e.g.
# ``RecursionError`` from ``asdict`` on a cyclic dataclass, or
# ``MemoryError`` during a huge credential_store migration).
# * ``VoiceTyperApp.__init__`` must not crash the entire
# backend if ``Config.load()`` propagates an unexpected exception
# (``KeyError`` / ``AttributeError`` / ``MemoryError`` — the
# deliberate "do not silently swallow" propagation in
# ``Config.load``).  We catch, log at ERROR with ``exc_info=True``,
# fall back to ``Config()`` defaults, and surface a tray
# notification once ``self.tray`` is built.
# * ``DE-49`` — the re-entry guards in ``quit_app`` and
# ``restart_app`` must check ``self._shutting_down_event.is_set()``
# (the ``threading.Event`` version, which provides cross-thread
# memory-ordering) instead of the plain ``self._shutting_down``
# boolean.
# * ``DE-50`` — ``app.main()`` must wrap the ``ipc_main()`` call in a
# top-level ``try/except Exception`` so a crash logs at ERROR with
# the full traceback and exits with code 1 (rather than propagating
# to the console-script wrapper with no structured log entry).
#
# All heavy dependencies are mocked via the project-wide
# ``mock_heavy_imports`` autouse fixture (in ``tests/conftest.py``).
#


def _stub_restart_environment(app, monkeypatch):
    """Stub out restart_app side effects so it runs in tests."""
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    # Patch global stdlib modules instead of ``voice_typer.server.app.*``
    # because monkeypatch.setattr with a dotted string resolves via
    # importlib.import_module(), which cannot import stdlib modules
    # (time, sys, os) as submodules of voice_typer.server.app when
    # those modules are not imported at app.py's module level.
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(
        "sys.exit",
        lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
    )
    monkeypatch.setattr("os._exit", lambda code: None)
    app.hotkeys._hotkey_backend = MagicMock()
    app.hotkeys._esc_backend = MagicMock()
    app.hotkeys._repaste_backend = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app.tray = MagicMock()
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recording._transcription_thread = None
    app.recording.get_streaming_session = MagicMock(return_value=None)
    app.recording.set_streaming_session = MagicMock()


# config.save() raising in restart_app ────────────────────────


class TestConfigSaveRaisesInRestartApp:
    """DE-47: ``self.config.save()`` in ``restart_app`` is wrapped in a
    ``try/except Exception`` so an unexpected raise cannot strand the
    user in a half-dead process (no relaunch event published, no
    exit).  The restart must continue regardless."""

    def test_restart_app_continues_when_config_save_raises(self, app, monkeypatch):
        """If ``config.save()`` raises (e.g. ``RecursionError`` from a
        cyclic dataclass, ``MemoryError`` during credential_store
        migration), ``restart_app`` must still push the
        ``relaunch_app`` event and proceed with the restart."""
        _stub_restart_environment(app, monkeypatch)

        publish_calls = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: publish_calls.append(msg),
        )
        # Force config.save() to raise an unexpected exception.
        monkeypatch.setattr(
            app.config,
            "save",
            lambda: (_ for _ in ()).throw(RecursionError("cyclic dataclass")),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # The relaunch_app event MUST still be pushed despite the save
        # failure — otherwise the user's "Restart" tray click is a
        # silent no-op.
        assert any(msg.get("type") == "relaunch_app" for msg in publish_calls), (
            "DE-47: restart_app must still publish the relaunch_app event "
            "even when config.save() raises; got pushes: " + repr(publish_calls)
        )

    def test_restart_app_logs_warning_when_config_save_raises(self, app, monkeypatch, caplog):
        """The exception must be logged at WARNING with ``exc_info=True``
        so the stack trace lands in the user's log file for triage."""
        _stub_restart_environment(app, monkeypatch)

        monkeypatch.setattr(
            app.config,
            "save",
            lambda: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"), contextlib.suppress(SystemExit):
            app.restart_app()

        save_warning_records = [r for r in caplog.records if "config.save() raised" in r.message]
        assert save_warning_records, (
            "DE-47: restart_app must log a WARNING containing 'config.save() raised' when config.save() raises"
        )
        # exc_info=True must be set so the traceback is captured.
        assert save_warning_records[0].exc_info is not None, (
            "DE-47: the config.save() warning must include exc_info=True so the traceback lands in the log"
        )
        assert isinstance(save_warning_records[0].exc_info[1], RuntimeError), (
            "DE-47: the logged exception must be the RuntimeError from config.save()"
        )

    def test_restart_app_still_logs_failure_when_save_returns_false(self, app, monkeypatch, caplog):
        """DE-47 must NOT regress the existing ``save() returns False``
        path (the documented ``OSError``/``PermissionError``/``TimeoutError``
        contract).  The original ``if not self.config.save():`` warning
        must still fire when save returns False."""
        _stub_restart_environment(app, monkeypatch)

        monkeypatch.setattr(app.config, "save", lambda: False)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"), contextlib.suppress(SystemExit):
            app.restart_app()

        false_warning_records = [r for r in caplog.records if "config.save() before push failed" in r.message]
        assert false_warning_records, (
            "DE-47: the existing 'config.save() before push failed' WARNING "
            "must still fire when save() returns False (preserved contract)"
        )

    def test_source_has_try_except_around_config_save(self):
        """Source-level invariant: the ``self.config.save()`` call in
        ``restart_app`` must be wrapped in ``try:/except Exception:``."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        # Find the save call.
        save_idx = src.find("self.config.save()")
        assert save_idx != -1, "restart_app must call self.config.save()"
        # The try: keyword must appear before the save call.
        try_idx = src.rfind("try:", 0, save_idx)
        assert try_idx != -1, (
            "DE-47: self.config.save() in restart_app must be wrapped in a "
            "try: block (no 'try:' found before the save call)"
        )
        # The except Exception: clause must appear after the save call.
        except_idx = src.find("except Exception:", save_idx)
        assert except_idx != -1, (
            "DE-47: self.config.save() in restart_app must be followed by "
            "an 'except Exception:' clause that logs the failure"
        )
        # The except body must log at WARNING with 'config.save() raised'.
        except_block = src[except_idx:]
        assert "config.save() raised" in except_block, "DE-47: the except block must log 'config.save() raised'"
        assert "exc_info=True" in except_block, "DE-47: the except block must pass exc_info=True to log.warning"


# Config.load() raising in __init__ ───────────────────────────


class TestConfigLoadRaisesInInit:
    """``VoiceTyperApp.__init__`` catches any ``Exception`` from
    ``Config.load()``, logs at ERROR with ``exc_info=True``, falls back
    to ``Config()`` defaults, and surfaces a tray notification."""

    def test_init_falls_back_to_defaults_when_config_load_raises(self, tmp_config_dir, monkeypatch):
        """When ``Config.load()`` raises (e.g. ``KeyError`` from a
        ``data[...]`` access without a default — the deliberate
        propagation in Config.load), ``__init__`` must catch it and
        construct with ``Config()`` defaults so the rest of init can
        proceed."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config
        from voice_typer.server.server_platform import autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
        monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        # Force Config.load to raise an unexpected exception.
        def _boom():
            raise KeyError("simulated bug in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        instance = app_module.VoiceTyperApp()

        # Config must be a default Config instance, NOT None.
        assert isinstance(instance.config, Config), (
            "__init__ must fall back to Config() defaults when Config.load() raises; got: " + repr(instance.config)
        )
        # The flag must be set so the deferred tray notification fires.
        assert instance._config_load_failed is True, (
            "__init__ must set _config_load_failed=True when Config.load() raises so the tray notification is deferred"
        )
        # Cleanup the instance to avoid resource leaks.
        with contextlib.suppress(Exception):
            instance._do_cleanup()

    def test_init_logs_error_with_exc_info_when_config_load_raises(self, tmp_config_dir, monkeypatch, caplog):
        """The exception must be logged at ERROR with ``exc_info=True``."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config
        from voice_typer.server.server_platform import autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
        monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        def _boom():
            raise AttributeError("simulated None deref in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.app"):
            instance = app_module.VoiceTyperApp()

        try:
            error_records = [r for r in caplog.records if "Config.load() raised" in r.message]
            assert error_records, (
                "__init__ must log an ERROR containing 'Config.load() raised' when Config.load() raises"
            )
            assert error_records[0].levelno == logging.ERROR, (
                "the Config.load failure must be logged at ERROR level "
                "(not WARNING/DEBUG) so it's visible in the default-INFO "
                "production log"
            )
            assert error_records[0].exc_info is not None, (
                "the Config.load failure log must include exc_info=True so the traceback lands in the log for triage"
            )
            assert isinstance(error_records[0].exc_info[1], AttributeError), (
                "the logged exception must be the AttributeError from Config.load"
            )
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_init_surfaces_tray_notification_when_config_load_raises(self, tmp_config_dir, monkeypatch):
        """After ``self.tray`` is built, ``__init__`` must call
        ``tray.notify`` with a user-facing message about the config
        load failure."""
        from voice_typer.server import app as app_module, i18n
        from voice_typer.server.config import Config
        from voice_typer.server.server_platform import autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
        monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        def _boom():
            raise MemoryError("simulated OOM in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        # Spy on TrayIcon to capture the notify call.
        notify_calls = []
        original_init = app_module.TrayIcon.__init__

        def _spy_init(self, *a, **kw):
            original_init(self, *a, **kw)
            original_notify = self.notify

            def _spy_notify(title, message, *a2, **kw2):
                notify_calls.append((title, message))
                return original_notify(title, message, *a2, **kw2)

            self.notify = _spy_notify

        monkeypatch.setattr(app_module.TrayIcon, "__init__", _spy_init)

        instance = app_module.VoiceTyperApp()

        try:
            # The tray notification must have been called.
            assert notify_calls, (
                "__init__ must call self.tray.notify when Config.load() raises (after the tray is built)"
            )
            # The message must mention the config load failure.
            # Resolve the i18n keys at the test's active locale (default
            # ``en``) so the assertion stays valid regardless of which
            # string the locale registry maps ``error.config_load_failed.*``
            # to — the contract under test is that __init__ routed the
            # notification through ``i18n.t(...)`` for these keys.
            titles_msgs = " ".join(f"{t} {m}" for t, m in notify_calls)
            expected_title = i18n.t("error.config_load_failed.title")
            expected_body = i18n.t("error.config_load_failed.body")
            assert expected_title in titles_msgs or expected_body in titles_msgs, (
                "the tray notification must mention the config-load "
                "failure (expected i18n-resolved title or body for the "
                "active locale); got: " + repr(notify_calls)
            )
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_init_does_not_notify_when_config_load_succeeds(self, tmp_config_dir, monkeypatch):
        """Sanity: when ``Config.load()`` succeeds, ``__init__`` must
        NOT call ``tray.notify`` for a config-load failure (the flag
        must be False and the notification branch skipped)."""
        from voice_typer.server import app as app_module
        from voice_typer.server.server_platform import autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
        monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        notify_calls = []
        original_init = app_module.TrayIcon.__init__

        def _spy_init(self, *a, **kw):
            original_init(self, *a, **kw)
            original_notify = self.notify

            def _spy_notify(title, message, *a2, **kw2):
                notify_calls.append((title, message))
                return original_notify(title, message, *a2, **kw2)

            self.notify = _spy_notify

        monkeypatch.setattr(app_module.TrayIcon, "__init__", _spy_init)

        instance = app_module.VoiceTyperApp()

        try:
            assert instance._config_load_failed is False, (
                "_config_load_failed must be False when Config.load() succeeds"
            )
            # No config-load-failure notification. The notification now
            # routes through ``i18n.t("error.config_load_failed.*")`` —
            # resolve the locale strings and assert neither was emitted.
            from voice_typer.server import i18n

            config_fail_title = i18n.t("error.config_load_failed.title")
            config_fail_body = i18n.t("error.config_load_failed.body")
            config_fail_notifies = [
                (t, m)
                for t, m in notify_calls
                if config_fail_title in (t or "")
                or config_fail_title in (m or "")
                or config_fail_body in (t or "")
                or config_fail_body in (m or "")
            ]
            assert config_fail_notifies == [], (
                "__init__ must NOT call tray.notify with a config-load "
                "failure message when Config.load() succeeds; got: " + repr(config_fail_notifies)
            )
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_init_tray_notify_failure_is_swallowed(self, tmp_config_dir, monkeypatch):
        """If ``tray.notify`` itself raises (e.g. tray backend not
        fully initialized), ``__init__`` must NOT re-raise — the
        user already has the ERROR log line + traceback for triage."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config
        from voice_typer.server.server_platform import autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
        monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

        def _boom():
            raise RuntimeError("simulated bug in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        # Make TrayIcon.notify raise.
        original_init = app_module.TrayIcon.__init__

        def _spy_init(self, *a, **kw):
            original_init(self, *a, **kw)
            self.notify = lambda *a, **kw: (_ for _ in ()).throw(OSError("tray backend not initialized"))

        monkeypatch.setattr(app_module.TrayIcon, "__init__", _spy_init)

        # Must not raise.
        instance = app_module.VoiceTyperApp()

        try:
            assert instance._config_load_failed is True
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_source_has_try_except_around_config_load(self):
        """Source-level invariant: ``Config.load()`` in the config-init
        builder must be wrapped in ``try:/except Exception:`` with an
        ``ERROR``-level log and a ``Config()`` fallback. (The call lives
        in ``_init_config`` since the ``__init__`` decomposition — the
        builder is the new home of the former inline ``__init__`` body.)"""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp._init_config)
        # Search for the actual call (``self.config = Config.load()``),
        # not the comment-text occurrences of ``Config.load()``.
        load_idx = src.find("self.config = Config.load()")
        assert load_idx != -1, "_init_config must assign self.config = Config.load()"
        try_idx = src.rfind("try:", 0, load_idx)
        assert try_idx != -1, "'self.config = Config.load()' in _init_config must be wrapped in a try: block"
        except_idx = src.find("except Exception:", load_idx)
        assert except_idx != -1, (
            "'self.config = Config.load()' in _init_config must be followed by an 'except Exception:' clause"
        )
        except_block = src[except_idx:]
        # Must log at ERROR.
        assert "log.error" in except_block, "the except block must use log.error (not log.warning or log.debug)"
        # Must include exc_info=True.
        assert "exc_info=True" in except_block, "the except block must pass exc_info=True so the traceback is logged"
        # Must fall back to Config() defaults.
        assert "Config()" in except_block, "the except block must fall back to Config() defaults"


# re-entry guard uses _shutting_down_event.is_set() ────────────


class TestReentryGuardUsesEventIsSet:
    """DE-49: the re-entry guards in ``quit_app`` and ``restart_app``
    check ``self._shutting_down_event.is_set()`` instead of the plain
    ``self._shutting_down`` boolean, for cross-thread memory-ordering."""

    def test_quit_app_guard_uses_event_is_set(self):
        """Source-level invariant: ``quit_app`` re-entry guard must
        use ``self._shutting_down_event.is_set():``, not the plain
        boolean form."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.quit_app)
        assert "if self._shutting_down_event.is_set():" in src, (
            "DE-49: quit_app must use 'if self._shutting_down_event.is_set():' as its re-entry guard"
        )

    def test_restart_app_guard_uses_event_is_set(self):
        """Source-level invariant: ``restart_app`` re-entry guard must
        use ``self._shutting_down_event.is_set():``, not the plain
        boolean form."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        assert "if self._shutting_down_event.is_set():" in src, (
            "DE-49: restart_app must use 'if self._shutting_down_event.is_set():' as its re-entry guard"
        )

    def test_quit_app_does_not_use_plain_boolean_guard(self):
        """The plain ``if self._shutting_down:`` form must NOT appear
        as a guard in ``quit_app`` (it's the buggy form that lacks
        cross-thread memory ordering)."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.quit_app)
        # The plain form (without _event.is_set) must not appear as
        # an executable guard.  We strip out comments/docstrings
        # loosely by checking that the only occurrence is inside the
        # docstring (which mentions the historical form).
        # Easiest invariant: search for the literal guard pattern
        # followed by a colon AND a newline.
        assert "if self._shutting_down:\n" not in src, (
            "DE-49: quit_app must NOT use the plain 'if self._shutting_down:' "
            "guard (use 'if self._shutting_down_event.is_set():' instead for "
            "cross-thread memory ordering)"
        )

    def test_restart_app_does_not_use_plain_boolean_guard(self):
        """The plain ``if self._shutting_down:`` form must NOT appear
        as a guard in ``restart_app``."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        assert "if self._shutting_down:\n" not in src, (
            "DE-49: restart_app must NOT use the plain "
            "'if self._shutting_down:' guard (use "
            "'if self._shutting_down_event.is_set():' instead)"
        )

    def test_quit_app_skips_quit_when_event_set(self, app, monkeypatch):
        """Behavioral: when ``_shutting_down_event`` is set (and the
        boolean is also True — quit() sets both), ``quit_app`` must
        skip the duplicate ``self.quit()`` call.  The push must still
        happen (APP-10 invariant)."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        monkeypatch.setattr("os._exit", lambda code: None)
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))

        # set the Event, not (only) the boolean.
        app._shutting_down_event.set()
        # Also set the boolean to mirror what quit() does in production
        # — both are set together so a test that sets only the Event
        # is sufficient, but mirroring production is cleaner.
        app._shutting_down = True

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [], "DE-49: quit_app must skip self.quit() when _shutting_down_event.is_set() is True"

    def test_quit_app_calls_quit_when_event_not_set(self, app, monkeypatch):
        """Behavioral: when ``_shutting_down_event`` is NOT set,
        ``quit_app`` must call ``self.quit()`` (the normal path)."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
        monkeypatch.setattr("os._exit", lambda code: None)

        # Sanity: event must be clear by default.
        assert not app._shutting_down_event.is_set()
        # Edge case: the boolean may be True from a prior test in this
        # session (some tests set it directly).  Clear it to mirror a
        # fresh app.
        app._shutting_down = False

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [True], "DE-49: quit_app must call self.quit() when _shutting_down_event is not set"

    def test_restart_app_skips_when_event_set(self, app, monkeypatch):
        """Behavioral: when ``_shutting_down_event`` is set,
        ``restart_app`` must short-circuit (no push, no save, no
        cleanup)."""
        _stub_restart_environment(app, monkeypatch)

        publish_calls = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: publish_calls.append(msg),
        )
        save_calls = []
        monkeypatch.setattr(app.config, "save", lambda: save_calls.append(True) or True)
        cleanup_calls = []
        original_do_cleanup = app._do_cleanup
        monkeypatch.setattr(app, "_do_cleanup", lambda: cleanup_calls.append(True) or original_do_cleanup())

        # set the Event (not just the boolean).
        app._shutting_down_event.set()
        app._shutting_down = True

        app.restart_app()

        assert publish_calls == [], "DE-49: restart_app must NOT push events when _shutting_down_event.is_set() is True"
        assert save_calls == [], (
            "DE-49: restart_app must NOT call config.save() when _shutting_down_event.is_set() is True"
        )
        assert cleanup_calls == [], (
            "DE-49: restart_app must NOT call _do_cleanup() when _shutting_down_event.is_set() is True"
        )

    def test_quit_app_guard_does_not_fire_on_boolean_only(self, app, monkeypatch):
        """DE-49 regression guard: setting ONLY the plain boolean
        ``_shutting_down = True`` (without setting the Event) must
        NOT short-circuit ``quit_app``'s guard — because the guard
        now reads the Event, not the boolean.

        This test pins the new behavior: a refactor that sets only
        the boolean (e.g. a future contributor copying the pre-DE-49
        pattern) will not accidentally trigger the re-entry guard.
        The production code in ``quit()`` / ``restart_app()`` sets
        BOTH the boolean and the Event, so production is unaffected;
        this test guards against the boolean-only anti-pattern in
        test scaffolding and any future code that touches the flag
        directly.
        """
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
        monkeypatch.setattr("os._exit", lambda code: None)

        # Set ONLY the boolean — NOT the Event.  Pre- this would
        # have short-circuited the guard; post- it must NOT.
        app._shutting_down = True
        app._shutting_down_event.clear()

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [True], (
            "DE-49: setting only the boolean _shutting_down=True (without "
            "setting the Event) must NOT short-circuit quit_app's guard. "
            "The guard reads _shutting_down_event.is_set(), which is False "
            "here — so self.quit() must still be called."
        )


# main() wraps ipc_main() in try/except ───────────────────────


class TestMainWrapsIpcMain:
    """DE-50: ``app.main()`` wraps ``ipc_main()`` in a top-level
    ``try/except Exception`` so a backend crash logs at ERROR with
    the full traceback and exits with code 1."""

    def test_main_logs_and_exits_when_ipc_main_raises(self, monkeypatch, caplog):
        """When ``ipc_main()`` raises an unexpected exception,
        ``app.main()`` must log it at ERROR with the full traceback
        and call ``sys.exit(1)``."""
        from voice_typer.server import app as app_module

        # Make faulthandler.enable() a no-op so the test is hermetic.
        monkeypatch.setitem(sys.modules, "faulthandler", MagicMock(enable=lambda: None))

        # Make ipc_main raise an unexpected exception.
        def _boom():
            raise RuntimeError("simulated backend crash")

        # Patch the ipc_server.main symbol BEFORE app.main imports it.
        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", _boom)

        exit_calls = []
        monkeypatch.setattr(app_module.sys, "exit", lambda code=0: exit_calls.append(code))

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.app"):
            app_module.main()

        # Must have called sys.exit(1).
        assert exit_calls == [1], "DE-50: app.main must call sys.exit(1) when ipc_main raises; got exit calls: " + repr(
            exit_calls
        )
        # Must have logged at ERROR with the FATAL prefix.
        fatal_records = [r for r in caplog.records if "[FATAL] backend crashed" in r.message]
        assert fatal_records, (
            "DE-50: app.main must log an ERROR containing '[FATAL] backend crashed' when ipc_main raises"
        )
        # log.exception captures exc_info automatically.
        assert fatal_records[0].exc_info is not None, (
            "DE-50: the FATAL log must include exc_info (log.exception captures the traceback automatically)"
        )
        assert isinstance(fatal_records[0].exc_info[1], RuntimeError), (
            "DE-50: the logged exception must be the RuntimeError from ipc_main"
        )

    def test_main_does_not_swallow_system_exit(self, monkeypatch):
        """``SystemExit`` (raised by ``sys.exit(0)`` inside ``quit()``
        / ``restart_app()``) must propagate unchanged — it's the
        normal shutdown signal and must NOT be caught by the
        ``except Exception:`` (since ``SystemExit`` inherits from
        ``BaseException``, not ``Exception``)."""
        from voice_typer.server import app as app_module

        # Make faulthandler.enable() a no-op so the test is hermetic.
        monkeypatch.setitem(sys.modules, "faulthandler", MagicMock(enable=lambda: None))

        # Make ipc_main raise SystemExit(0) (the intentional exit).
        def _raise_system_exit():
            raise SystemExit(0)

        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", _raise_system_exit)

        # sys.exit must NOT be called by the except branch (SystemExit
        # is not an Exception subclass, so it propagates).
        exit_calls = []
        monkeypatch.setattr(app_module.sys, "exit", lambda code=0: exit_calls.append(code))

        # SystemExit must propagate out of main().
        with pytest.raises(SystemExit) as exc_info:
            app_module.main()

        assert exc_info.value.code == 0, (
            "DE-50: SystemExit(0) from ipc_main must propagate with code 0 (not be caught and re-exited as 1)"
        )
        assert exit_calls == [], (
            "DE-50: app.main must NOT call sys.exit when ipc_main raises "
            "SystemExit (the normal shutdown path); got exit calls: " + repr(exit_calls)
        )

    # faulthandler fallback path — ``main()`` enables
    # ``faulthandler`` for crash thread-dumps and logs a WARNING (with
    # exc_info) when ``faulthandler.enable()`` raises or the module is
    # unavailable, WITHOUT aborting the backend startup.

    def test_main_logs_warning_when_faulthandler_enable_raises(self, monkeypatch, caplog):
        """When ``faulthandler.enable()`` raises (stripped minimal build
        / platform without SIGSEGV support), ``app.main()`` must log a
        WARNING with exc_info and STILL run ``ipc_main()`` (the crash
        dump capability is degraded, not fatal)."""
        from voice_typer.server import app as app_module

        # faulthandler.enable() raises — simulates a stripped interpreter.
        class _BoomFaulthandler:
            @staticmethod
            def enable():
                raise RuntimeError("enable() unsupported on this build")

        monkeypatch.setitem(sys.modules, "faulthandler", _BoomFaulthandler())

        called = []
        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", lambda: called.append(True))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            app_module.main()

        assert called, "main() must still invoke ipc_main() when faulthandler.enable() fails"
        warning_records = [r for r in caplog.records if "faulthandler not available" in r.message]
        assert warning_records, (
            "main() must log a WARNING containing 'faulthandler not available' when faulthandler.enable() raises"
        )
        assert warning_records[0].levelno == logging.WARNING, (
            "the faulthandler fallback must be logged at WARNING (not DEBUG)"
        )
        assert warning_records[0].exc_info is not None, (
            "the faulthandler warning must include exc_info (log.warning(..., exc_info=True))"
        )

    def test_main_logs_warning_when_faulthandler_import_fails(self, monkeypatch, caplog):
        """When ``import faulthandler`` itself fails (module stripped from
        a minimal build), ``app.main()`` must still degrade gracefully:
        log the WARNING and continue to ``ipc_main()``."""
        from voice_typer.server import app as app_module

        # sys.modules["faulthandler"] = None makes `import faulthandler`
        # raise ImportError ("import of faulthandler halted; None in
        # sys.modules") — exactly the degraded-build scenario.
        monkeypatch.setitem(sys.modules, "faulthandler", None)

        called = []
        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", lambda: called.append(True))

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            app_module.main()

        assert called, "main() must still invoke ipc_main() when faulthandler import fails"
        assert any("faulthandler not available" in r.message for r in caplog.records), (
            "main() must log 'faulthandler not available' when the import fails"
        )

    def test_main_enables_faulthandler_before_ipc_main(self, monkeypatch):
        """Happy path: ``faulthandler.enable()`` is invoked BEFORE
        ``ipc_main()`` so crash thread-dumps are armed for the whole
        backend lifetime."""
        from voice_typer.server import app as app_module

        enabled = []

        class _FakeFaulthandler:
            @staticmethod
            def enable():
                enabled.append(True)

        monkeypatch.setitem(sys.modules, "faulthandler", _FakeFaulthandler())

        called = []
        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", lambda: called.append(True))

        app_module.main()

        assert enabled == [True], "main() must call faulthandler.enable() on the happy path"
        assert called == [True], "main() must call ipc_main() after enabling faulthandler"

    def test_source_has_try_except_around_ipc_main(self):
        """Source-level invariant: the ``ipc_main()`` call in
        ``app.main()`` must be wrapped in ``try:/except Exception:``
        with ``log.exception('[FATAL] backend crashed')`` and
        ``sys.exit(1)``."""
        from voice_typer.server.app import main

        src = inspect.getsource(main)
        ipc_idx = src.find("ipc_main()")
        assert ipc_idx != -1, "main() must call ipc_main()"
        try_idx = src.rfind("try:", 0, ipc_idx)
        assert try_idx != -1, "DE-50: ipc_main() in main() must be wrapped in a try: block"
        except_idx = src.find("except Exception:", ipc_idx)
        assert except_idx != -1, "DE-50: ipc_main() in main() must be followed by an 'except Exception:' clause"
        except_block = src[except_idx:]
        assert "log.exception" in except_block, (
            "DE-50: the except block must use log.exception (which captures exc_info automatically)"
        )
        assert "[FATAL] backend crashed" in except_block, "DE-50: the except block must log '[FATAL] backend crashed'"
        assert "sys.exit(1)" in except_block, (
            "DE-50: the except block must call sys.exit(1) so the host sees a deterministic non-zero status"
        )
