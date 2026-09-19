"""Tests for the empty-transcription / silent-suppression fix."""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline


class _TestApp:
    """Minimal app stub for unit-testing DictationPipeline methods."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        self.config.bubble_behavior = "show_on_record"
        self._waveform_bubble = MagicMock()
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self.models = MagicMock()
        self.recording = MagicMock()
        # Recorder mock for the finally block in run()
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

    # Auto-mock unknown attributes (like MagicMock) but DO NOT
    def __getattr__(self, name: str) -> MagicMock:
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
        }:
            raise AttributeError(name)
        mock = MagicMock()
        object.__setattr__(self, name, mock)
        return mock


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    return pipeline


class TestHandleEmptyTranscriptionRefinedSuppression:
    """The grace-period suppression must consider recorded_rms."""

    def test_short_recording_with_near_silence_suppresses_notification(self):
        """Original UX-SILENCE-GRACE case: brief hotkey tap, no real audio."""
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 2.0  # < 15s grace
        pipeline._recorded_rms = 0.001  # < 0.005 threshold (near silence)

        pipeline._handle_empty_transcription()

        # No popup notification should fire.
        app.tray.notify.assert_not_called()
        # Tray status should reflect "no speech".
        statuses = [c.args[1] for c in app.tray.set_state.call_args_list]
        assert "No speech detected" in statuses, "Short near-silent recording should set tray to 'No speech detected'"

    def test_short_recording_with_real_audio_shows_empty_status(self):
        """HP-7 case: short recording, real audio, engine returned empty."""
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 5.0  # < 15s grace
        pipeline._recorded_rms = 0.15  # >= 0.005 threshold (real audio)

        pipeline._handle_empty_transcription()

        # No popup notification, short clip is too ambiguous.
        app.tray.notify.assert_not_called()
        # But tray status must reflect the empty-transcription failure.
        statuses = [c.args[1] for c in app.tray.set_state.call_args_list]
        assert "Transcription returned empty" in statuses, (
            "Short recording with real audio should set tray to "
            "'Transcription returned empty' (HP-7 fix), got: " + str(statuses)
        )

    def test_long_recording_with_near_silence_notifies_check_microphone(self):
        """Long recording with no audio → notify user to check microphone."""
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 20.0  # >= 15s grace
        pipeline._recorded_rms = 0.001  # < 0.005 threshold

        pipeline._handle_empty_transcription()

        # Popup notification should fire (microphone may not be capturing).
        app.tray.notify.assert_called_once()
        notification_text = app.tray.notify.call_args.args[1]
        assert "microphone" in notification_text.lower(), (
            "Long near-silent recording should notify user about microphone"
        )

    def test_long_recording_with_real_audio_notifies_empty_transcription(self):
        """Long recording with real audio but empty transcription → notify."""
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 20.0  # >= 15s grace
        pipeline._recorded_rms = 0.15  # >= 0.005 threshold (real audio)

        pipeline._handle_empty_transcription()

        # Popup notification should fire.
        app.tray.notify.assert_called_once()
        notification_text = app.tray.notify.call_args.args[1]
        assert "no transcription was produced" in notification_text.lower(), (
            "Long recording with real audio should notify user that "
            "transcription returned empty, got: " + notification_text
        )
        # Tray status must reflect the empty-transcription failure.
        statuses = [c.args[1] for c in app.tray.set_state.call_args_list]
        assert "Transcription returned empty" in statuses


class TestTranscribeEmptyResultDiagnostic:
    """``_transcribe`` must log a consolidated warning when the engine"""

    def test_empty_batch_result_logs_warning(self, caplog):
        """When ``transcribe_with_fallback`` returns \"\" on the batch"""
        app = _TestApp()
        # No streaming session → forces the batch path.
        app.recording.pop_streaming_session.return_value = None

        active = MagicMock()
        active.transcribe_with_fallback.return_value = ""  # empty!
        active.device_info = "mock-device"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._duration = 5.0
        pipeline._recorded_rms = 0.15
        pipeline._audio_stats = (0.15, 0.5, 25.0)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._transcribe()

        assert result == "", "Empty result should propagate unchanged"
        # The diagnostic warning must be logged.
        empty_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Empty transcription result" in r.getMessage()
        ]
        assert empty_warnings, "Empty transcription must emit a warning log with diagnostic context"
        msg = empty_warnings[0].getMessage()
        # Must include the key signals.
        assert "duration=5.00" in msg, f"duration missing from: {msg}"
        assert "recorded_rms=0.1500" in msg, f"recorded_rms missing from: {msg}"
        assert "backend=" in msg, f"backend missing from: {msg}"
        assert "path=batch" in msg, f"path missing from: {msg}"

    def test_nonempty_result_does_not_log_empty_warning(self, caplog):
        """When transcription succeeds, no empty-result warning fires."""
        app = _TestApp()
        app.recording.pop_streaming_session.return_value = None

        active = MagicMock()
        active.transcribe_with_fallback.return_value = "hello world"
        active.device_info = "mock-device"
        app.models.active_transcriber.return_value = active

        pipeline = _new_pipeline(app)
        pipeline._duration = 5.0
        pipeline._recorded_rms = 0.15
        pipeline._audio_stats = (0.15, 0.5, 25.0)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            result = pipeline._transcribe()

        assert result == "hello world"
        empty_warnings = [r for r in caplog.records if "Empty transcription result" in r.getMessage()]
        assert not empty_warnings, "Non-empty transcription must NOT emit the empty-result warning"


class TestAsrRegistryUnloadedBackendDiagnostic:
    """``AsrBackendRegistry.get_active`` must log a warning when it"""

    def test_unloaded_backend_warning_logged(self, caplog):
        from voice_typer.server.asr_registry import AsrBackendRegistry

        class _Config:
            asr_backend = "whisper"

        registry = AsrBackendRegistry(_Config())
        # Register a backend with is_loaded=False so get_active() falls
        unloaded_backend = MagicMock()
        unloaded_backend.is_loaded = False
        registry.register("whisper", unloaded_backend)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.asr_registry"):
            result = registry.get_active()

        assert result is None, "Fail-loud: last-resort unloaded returns None, never serves unloaded"
        unload_warnings = [r for r in caplog.records if "no loaded backend available" in r.getMessage()]
        assert unload_warnings, "get_active() must log a warning when no loaded backend is available"

    def test_loaded_backend_no_warning(self, caplog):
        """When the active backend IS loaded, no warning fires."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        class _Config:
            asr_backend = "whisper"

        registry = AsrBackendRegistry(_Config())
        loaded_backend = MagicMock()
        loaded_backend.is_loaded = True
        registry.register("whisper", loaded_backend)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.asr_registry"):
            result = registry.get_active()

        assert result is loaded_backend
        unload_warnings = [r for r in caplog.records if "no loaded backend available" in r.getMessage()]
        assert not unload_warnings, "Loaded backend must NOT trigger the no-loaded-backend warning"


class TestCancelledCycleEmptyHandling:
    """An ESC-cancelled cycle that aborts before the first"""

    def _cancelled_app(self, cancelled: bool = True) -> _TestApp:
        app = _TestApp()
        # Simulate recording_lifecycle ESC path:
        if cancelled:
            app.recording._cancelled_cycle_ids = {"test-cycle"}
        else:
            app.recording._cancelled_cycle_ids = {"other-cycle"}
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        return app

    def test_cancelled_cycle_is_quiet(self):
        """Long recording with real audio (the notify branch) + cancelled"""
        app = self._cancelled_app(cancelled=True)
        pipeline = _new_pipeline(app)
        pipeline._duration = 20.0  # past the 15s grace, would notify
        pipeline._recorded_rms = 0.01  # real audio, would notify

        pipeline._handle_empty_transcription()

        app.tray.set_state.assert_not_called()
        app.tray.notify.assert_not_called()
        app._schedule_timer.assert_not_called()

    def test_active_cycle_keeps_existing_behavior(self):
        """A non-cancelled cycle with the same empty result keeps the"""
        app = self._cancelled_app(cancelled=False)
        pipeline = _new_pipeline(app)
        pipeline._duration = 20.0
        pipeline._recorded_rms = 0.01

        pipeline._handle_empty_transcription()

        app.tray.notify.assert_called_once()
