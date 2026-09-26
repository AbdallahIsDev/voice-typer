"""Tests for the DE-2K fix group (recording_controller.py)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.recording_controller import RecordingController


def _make_controller() -> RecordingController:
    """Build a RecordingController with a fully-mocked app."""
    from voice_typer.server.recording_controller import RecordingController

    app = MagicMock()
    # ``threading.Event`` for the busy flag, MagicMock's auto-event
    app.config.hotkey = "<f2>"
    app._busy_event = threading.Event()
    app._busy_event.set()  # not busy = set (per the project's convention)
    # Production resets via the coordinator (``_busyness.set_idle()``) -
    app._busyness.set_idle.side_effect = lambda: app._busy_event.set()
    app._busyness.set_busy.side_effect = lambda: app._busy_event.clear()
    app._cycle_counter = 0
    app._cycle_id = "#0"
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recorder.last_rms = 0.0
    # ``_stop_impl`` reads ``_dropped_ring_chunks`` after
    app.recorder._dropped_ring_chunks = 0
    app.config = MagicMock()
    app.config.voice_biometric_consent = True
    app.config.sample_rate = 16000
    app.config.streaming_transcription = False
    app.config.bubble_behavior = "auto_hide"
    app.config.show_notifications = True
    app.models = MagicMock()
    app.models._model_load_thread = None
    app.models._pending_dictation = False
    active = MagicMock()
    active.is_loaded = True
    app.models.active_transcriber.return_value = active
    app.models.apply_pending_model_change.return_value = None
    app.models.ensure_active_engine_loaded.return_value = None
    app.models.fallback_to_whisper.return_value = None
    app.tray = MagicMock()
    app._waveform_bubble = MagicMock()
    app._audio_quality = MagicMock()
    app._thread_registry = None
    app._schedule_timer = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app._restore_volume = MagicMock()
    app._duck_volume = MagicMock()
    app._finalize_audio_quality_report = MagicMock()
    app._stop_dictation = MagicMock(side_effect=lambda: app.recording_control.stop())
    app._start_dictation = MagicMock(side_effect=lambda: app.recording_control.start())

    ctrl = RecordingController(app)
    app.recording_control = ctrl
    return ctrl, app


class TestMicLeakOnPartialStartFailure:
    """DE-8: ``start()`` except block must ``recorder.discard()`` if"""

    def test_discard_called_when_streaming_session_start_raises(self):
        ctrl, app = _make_controller()

        # Simulate recorder.start() succeeding (stream opened,
        def fake_recorder_start():
            app.recorder.recording = True  # PortAudio stream now open

        app.recorder.start.side_effect = fake_recorder_start
        ctrl._start_streaming_session_if_enabled = MagicMock(
            side_effect=RuntimeError("simulated streaming-session start failure")
        )
        app.recorder.discard = MagicMock()

        ctrl.start()

        assert app.recorder.discard.called, (
            "DE-8: recorder.discard() must be called in start() except block "
            "to release the PortAudio input stream when a partial-start "
            "failure occurs after recorder.start() succeeded."
        )
        assert app.recorder.recording is False, (
            "DE-8: app.recorder.recording must be reset to False after a "
            "partial-start failure so the next start() call doesn't no-op."
        )

    def test_discard_failure_does_not_propagate(self):
        """If ``recorder.discard()`` itself raises, the except block"""
        ctrl, app = _make_controller()

        def fake_recorder_start():
            app.recorder.recording = True

        app.recorder.start.side_effect = fake_recorder_start
        ctrl._start_streaming_session_if_enabled = MagicMock(side_effect=RuntimeError("simulated failure"))
        app.recorder.discard = MagicMock(side_effect=OSError("PortAudio stream close failed"))

        # Must NOT raise.
        ctrl.start()

        # Cleanup still ran.
        assert app.tray.set_state.called
        assert app.tray.notify.called


class TestConsentCheckFailsClosed:
    """DE-9: if the consent-check ``getattr`` raises, recording must NOT"""

    def test_corrupted_config_does_not_start_recording(self):
        ctrl, app = _make_controller()

        # Make getattr(app.config, "voice_biometric_consent", False) raise.
        class CorruptedConfig:
            def __getattr__(self, name):
                raise RuntimeError("simulated corrupted config read")

        app.config = CorruptedConfig()
        app.recorder.start = MagicMock()

        ctrl.start()

        assert not app.recorder.start.called, (
            "DE-9: recorder.start() must NOT be called when the consent check "
            "raises, fail CLOSED to enforce the GDPR Art. 9 consent gate."
        )
        tray_states = [call.args[0] for call in app.tray.set_state.call_args_list]
        from voice_typer.server.tray_types import AppState

        assert AppState.ERROR in tray_states, (
            f"DE-9: tray state must include ERROR on consent-check failure (got {tray_states})"
        )

    def test_consent_false_does_not_start_recording(self):
        """Pre-existing behavior (consent=False refuses to start) must"""
        ctrl, app = _make_controller()
        app.config.voice_biometric_consent = False
        app.recorder.start = MagicMock()

        ctrl.start()

        assert not app.recorder.start.called


class TestStopAndCancelSerialized:
    """DE-12: ``stop()`` and ``cancel()`` acquire ``_toggle_lock`` so"""

    def test_toggle_lock_is_rlock(self):
        """``_toggle_lock`` must be an RLock so the call path"""
        ctrl, _ = _make_controller()
        assert isinstance(ctrl._toggle_lock, type(threading.RLock())), (
            "DE-12: _toggle_lock must be an RLock (reentrant) so stop()/cancel() "
            "called from inside toggle()'s already-held lock doesn't deadlock."
        )

    def test_concurrent_stop_calls_invoke_recorder_stop_once(self):
        """Two concurrent ``stop()`` calls must serialize —"""
        ctrl, app = _make_controller()
        app.recorder.recording = True

        # Make recorder.stop() block briefly so the second caller
        def slow_recorder_stop():
            time.sleep(0.05)
            app.recorder.recording = False
            # Return ~1s of audio so transcription path proceeds.
            return b"\x00" * 16000

        app.recorder.stop.side_effect = slow_recorder_stop

        # Patch DictationPipeline import inside _stop_impl so we don't
        with patch("voice_typer.server.dictation_pipeline.DictationPipeline") as pipeline_mock:
            pipeline = MagicMock()
            pipeline_mock.return_value = pipeline

            threads = [threading.Thread(target=ctrl.stop, name=f"stop-{i}") for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=2.0)

        assert app.recorder.stop.call_count == 1, (
            f"DE-12: recorder.stop() must be called exactly once under concurrent "
            f"stop() calls (got {app.recorder.stop.call_count})"
        )

    def test_stop_call_from_toggle_does_not_deadlock(self):
        """The re-entrant call path ``toggle() → _stop_dictation() →"""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        app._busy_event.set()  # not busy

        def slow_recorder_stop():
            time.sleep(0.02)
            app.recorder.recording = False
            return b"\x00" * 16000

        app.recorder.stop.side_effect = slow_recorder_stop

        with patch("voice_typer.server.dictation_pipeline.DictationPipeline"):
            done = threading.Event()

            def run_toggle():
                ctrl.toggle()
                done.set()

            t = threading.Thread(target=run_toggle, name="toggle-thread")
            t.start()
            assert done.wait(timeout=2.0), "DE-12: toggle() → stop() call path deadlocked (RLock not reentrant?)"
            t.join(timeout=1.0)


class TestForceRecoveryGcCollect:
    """DE-13: ``_force_recover_from_stuck_transcription`` calls"""

    def test_gc_collect_called_after_force_recovery(self):
        ctrl, app = _make_controller()
        app._busy_event.clear()  # busy = True (force-recovery is needed)
        ctrl._transcription_thread = MagicMock()
        ctrl._transcription_thread.is_alive.return_value = True
        ctrl._watchdog_firings = ctrl._watchdog_max_firings

        with patch("voice_typer.server.transcription_watchdog.gc.collect") as gc_collect:
            ctrl._force_recover_from_stuck_transcription(force=True)

        assert gc_collect.called, (
            "DE-13: gc.collect() must be called after force-recovery to release "
            "orphaned audio-buffer cycles held by the cancelled streaming session."
        )

    def test_force_recovery_still_resets_busy_and_tray(self):
        """DE-13's gc.collect() addition must not break the existing"""
        ctrl, app = _make_controller()
        app._busy_event.clear()  # busy
        ctrl._transcription_thread = None  # no live thread → force-recover path

        ctrl._force_recover_from_stuck_transcription(force=True)

        assert app._busy_event.is_set(), "busy flag must be cleared (set = not busy)"
        tray_states = [call.args[0] for call in app.tray.set_state.call_args_list]
        from voice_typer.server.tray_types import AppState

        assert AppState.IDLE in tray_states

    def test_force_recovery_clears_current_audio_slot(self):
        """DE-13 (privacy): ``_force_recover_from_stuck_transcription`` must"""
        ctrl, app = _make_controller()
        app._busy_event.clear()  # busy = True (force-recovery is needed)
        ctrl._transcription_thread = None  # no live thread → force-recover path

        # Simulate audio bytes captured by stop() before the watchdog fired.
        captured_audio = b"\x00\x01\x02\x03" * 4096
        ctrl._current_audio = captured_audio

        ctrl._force_recover_from_stuck_transcription(force=True)

        assert ctrl._current_audio is None, (
            "DE-13: _force_recover_from_stuck_transcription must clear "
            "self._current_audio so the raw voice bytes can be garbage-"
            "collected after a user-initiated cancel."
        )


class TestGenericErrorMessage:
    """DE-51: ``start()`` except block publishes a generic user-facing"""

    def test_tray_notify_does_not_contain_exception_text(self):
        ctrl, app = _make_controller()
        sensitive_path = "/home/user/.lausu/models/secret-model.bin"
        app.recorder.start.side_effect = RuntimeError(
            f"PortAudio error opening stream on device 'Built-in Microphone' loading model from {sensitive_path}"
        )

        with patch("voice_typer.server.event_bus.publish") as publish:
            ctrl.start()

        notify_call = app.tray.notify.call_args
        assert notify_call is not None, "tray.notify must be called on start() failure"
        notify_msg = notify_call.args[1]
        assert sensitive_path not in notify_msg, (
            f"DE-51: tray.notify message must NOT contain the raw exception text "
            f"(which can leak absolute paths, device names, hostnames). Got: {notify_msg!r}"
        )
        assert "Could not start recording" in notify_msg

        publish.assert_called()
        publish_call = publish.call_args
        payload = publish_call.args[0]
        assert payload["type"] == "error"
        msg = payload["data"]["message"]
        assert sensitive_path not in msg, (
            f"DE-51: event_bus.publish error message must NOT contain the raw exception text. Got: {msg!r}"
        )
        assert "Could not start recording" in msg


# (supersedes the  partial deferral): ``_stop_impl`` now


class TestStopDoesNotPreCancelStreamingSession:
    """Regression guard for the streaming-finalize fast-path fix."""

    def test_stop_does_not_cancel_session_on_normal_path(self):
        ctrl, app = _make_controller()
        app.recorder.recording = True
        # Return ~1s of audio so the early-return path (which calls
        app.recorder.stop.return_value = b"\x00" * 16000

        fake_session = MagicMock()
        fake_session._cancel_event = threading.Event()
        ctrl.set_streaming_session(fake_session)

        with patch("voice_typer.server.dictation_pipeline.DictationPipeline"):
            ctrl.stop()

        # pop + cancel. _stop_impl must NOT pre-cancel.
        assert not fake_session.cancel.called, (
            "_stop_impl must NOT pre-cancel the streaming session; "
            "DictationPipeline.run()'s finally block owns the cancel."
        )
        assert not fake_session._cancel_event.is_set(), "_stop_impl must NOT poke the private _cancel_event either."
        # The session must still be in the slot, the pipeline will pop it.
        assert ctrl.get_streaming_session() is fake_session, (
            "_stop_impl must NOT pre-pop the streaming session; DictationPipeline.run()'s finally block owns the pop."
        )


class TestWatchdogThreadReuseRace:
    """DE-55: ``_start_watchdog_thread()`` ``join(timeout=0.1)``s the"""

    def test_dying_thread_is_joined_and_new_thread_created(self):
        """If the previous watchdog thread is alive at the is_alive()"""
        ctrl, _ = _make_controller()

        # Mock the previous thread: is_alive() returns True before
        prev_thread = MagicMock()
        is_alive_returns = [True, False]

        def is_alive_side_effect():
            return is_alive_returns.pop(0) if is_alive_returns else False

        prev_thread.is_alive.side_effect = is_alive_side_effect
        prev_thread.join = MagicMock()  # join is a no-op; is_alive flips via side_effect

        ctrl._watchdog_thread = prev_thread

        ctrl._start_watchdog_thread()

        prev_thread.join.assert_called_once_with(timeout=0.1)
        assert ctrl._watchdog_thread is not prev_thread, (
            "DE-55: _start_watchdog_thread must NOT reuse a dying thread, a "
            "new thread must be created after the join completes."
        )
        # The new thread must be alive (running the watchdog loop).
        assert ctrl._watchdog_thread is not None
        assert ctrl._watchdog_thread.is_alive(), (
            "DE-55: the new watchdog thread must be alive (running _watchdog_loop)."
        )
        # Cleanup: stop the new thread. Capture the reference before
        new_thread = ctrl._watchdog_thread
        ctrl._stop_watchdog_thread()
        if new_thread is not None and new_thread.is_alive():
            new_thread.join(timeout=1.0)

    def test_actively_running_thread_is_reused(self):
        """If the previous watchdog thread is actively running (join"""
        ctrl, _ = _make_controller()

        # Start a real watchdog thread via _start_watchdog_thread.
        ctrl._start_watchdog_thread()
        first_thread = ctrl._watchdog_thread
        assert first_thread is not None
        assert first_thread.is_alive()

        # Call _start_watchdog_thread again, should reuse the running thread.
        ctrl._start_watchdog_thread()
        assert ctrl._watchdog_thread is first_thread, (
            "DE-55: an actively-running watchdog thread must be reused (the join times out and we return early)."
        )

        # Cleanup.
        ctrl._stop_watchdog_thread()
        first_thread.join(timeout=1.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
