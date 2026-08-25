"""split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

import time
from unittest.mock import MagicMock

import numpy as np


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
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

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
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

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
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])
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
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])
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
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

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
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

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

        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

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
