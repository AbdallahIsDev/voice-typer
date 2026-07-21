"""CR-25: split from tests/test_app.py.

All heavy dependencies are mocked via the project-wide ``mock_heavy_imports``
autouse fixture (in ``tests/conftest.py``) — CR-60 hoisted the
``force_pynput_hotkey_backend`` patch from the old local fixture into
that project-wide fixture, so test modules no longer need a local
override.
"""

import time
from unittest.mock import MagicMock

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
