"""Tests for the empty-transcription / silent-suppression fix.

The "finish dictation → nothing transcribed" symptom had two
co-operating causes:

1. ``DictationPipeline._handle_empty_transcription`` suppressed the
   user-facing notification for EVERY recording shorter than the 15s
   grace period — including ones with clear audio (high RMS) where the
   engine was the real culprit (returned ``""`` silently). The user
   saw no clipboard output, no error toast, no tray status beyond
   "No speech detected".

2. ``DictationPipeline._transcribe`` had no diagnostic log when the
   engine returned an empty string — the silent-failure path was
   invisible in the log file.

3. ``AsrBackendRegistry.get_active`` would return an unloaded backend
   as a last resort without warning, even though an unloaded backend
   can return ``""`` from ``transcribe_with_fallback`` without
   raising — making the empty-transcription path impossible to
   trace from the registry side.

The fix narrows the suppression to ONLY the case it was designed for
(short recording AND near-silence), surfaces a distinct tray status
for short recordings with real audio, surfaces a popup notification
for long recordings with real audio, adds a consolidated warning log
in ``_transcribe`` for every empty result, and adds a warning log in
``get_active`` when an unloaded backend is returned.

These tests verify each branch of the refined suppression logic and
the new diagnostic logs.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline


class _TestApp:
    """Minimal app stub for unit-testing DictationPipeline methods.

    A custom class (instead of ``MagicMock``) so the four notify-once
    flag attributes correctly default to ``False`` via
    ``getattr(..., False)`` — MagicMock would auto-create truthy
    children for any attribute access.
    """

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        # bubble_behavior is read in _handle_empty_transcription
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
    # auto-create the notify-once flag names — they must default to
    # False via getattr-with-default.
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
    """Build a fresh DictationPipeline tied to ``app``.

    Mirrors how ``RecordingController._stop_dictation`` constructs a
    new pipeline per transcription cycle.
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
    return pipeline


# ─── _handle_empty_transcription: refined suppression logic ─────────────


class TestHandleEmptyTranscriptionRefinedSuppression:
    """The grace-period suppression must consider recorded_rms.

    Pre-fix: any recording < 15s suppressed the notification entirely,
    even when the audio clearly had signal (high RMS). That hid the
    "engine returned empty" failure mode behind a "No speech detected"
    tray status.

    Post-fix: suppression only fires for short recordings with
    near-silence (rms < 0.005). Short recordings with real audio get a
    distinct "Transcription returned empty" tray status (no popup —
    too ambiguous). Long recordings with real audio get a popup
    notification.
    """

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
        """HP-7 case: short recording, real audio, engine returned empty.

        Pre-fix: this was silently suppressed — user saw "No speech
        detected" with no indication that audio was captured but the
        engine returned nothing. Post-fix: tray status reflects
        "Transcription returned empty" so the user knows something
        happened, but no popup (too ambiguous for a short clip).
        """
        app = _TestApp()
        pipeline = _new_pipeline(app)
        pipeline._duration = 5.0  # < 15s grace
        pipeline._recorded_rms = 0.15  # >= 0.005 threshold (real audio)

        pipeline._handle_empty_transcription()

        # No popup notification — short clip is too ambiguous.
        app.tray.notify.assert_not_called()
        # But tray status must reflect the empty-transcription failure.
        statuses = [c.args[1] for c in app.tray.set_state.call_args_list]
        assert "Transcription returned empty" in statuses, (
            "Short recording with real audio should set tray to "
            "'Transcription returned empty' (HP-7 fix) — got: " + str(statuses)
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
        """Long recording with real audio but empty transcription → notify.

        This is the unusual case: 15+ seconds of intelligible audio
        should produce SOMETHING. If it doesn't, the user should be
        notified so they know to retry or check the log file.
        """
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
            "transcription returned empty — got: " + notification_text
        )
        # Tray status must reflect the empty-transcription failure.
        statuses = [c.args[1] for c in app.tray.set_state.call_args_list]
        assert "Transcription returned empty" in statuses


# ─── _transcribe: empty-result diagnostic log ───────────────────────────


class TestTranscribeEmptyResultDiagnostic:
    """``_transcribe`` must log a consolidated warning when the engine
    returns an empty string. The warning includes every signal we have
    (duration, RMS, audio stats, backend type, streaming vs batch path)
    so the silent-failure path is traceable from the log file.
    """

    def test_empty_batch_result_logs_warning(self, caplog):
        """When ``transcribe_with_fallback`` returns "" on the batch
        path, a warning must be logged with the diagnostic context.
        """
        app = _TestApp()
        # No streaming session → forces the batch path.
        app.recording.get_streaming_session.return_value = None

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
        app.recording.get_streaming_session.return_value = None

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


# ─── asr_registry.get_active: unloaded-backend diagnostic ──────────────


class TestAsrRegistryUnloadedBackendDiagnostic:
    """``AsrBackendRegistry.get_active`` must log a warning when it
    returns an unloaded backend as a last resort. An unloaded backend
    can return ``""`` from ``transcribe_with_fallback`` silently —
    surfacing this state in the log makes the empty-transcription
    failure mode traceable from the registry side.
    """

    def test_unloaded_backend_warning_logged(self, caplog):
        from voice_typer.server.asr_registry import AsrBackendRegistry

        class _Config:
            asr_backend = "whisper"

        registry = AsrBackendRegistry(_Config())
        # Register a backend with is_loaded=False so get_active() falls
        # through to the last-resort loop.
        unloaded_backend = MagicMock()
        unloaded_backend.is_loaded = False
        registry.register("whisper", unloaded_backend)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.asr_registry"):
            result = registry.get_active()

        assert result is unloaded_backend, "Last-resort path must still return the unloaded backend"
        unload_warnings = [r for r in caplog.records if "returning unloaded backend" in r.getMessage()]
        assert unload_warnings, "get_active() must log a warning when returning an unloaded backend"

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
        unload_warnings = [r for r in caplog.records if "returning unloaded backend" in r.getMessage()]
        assert not unload_warnings, "Loaded backend must NOT trigger the unloaded-backend warning"
