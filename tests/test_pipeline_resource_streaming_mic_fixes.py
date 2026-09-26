"""Regression tests for three user-log findings."""

from __future__ import annotations

import collections
import contextlib
import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
from voice_typer.server.dictation_pipeline import DictationPipeline


def _new_pipeline(app) -> DictationPipeline:
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._duration = 1.0
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    return pipeline


class _PipelineTestApp:
    """Minimal app stub mirroring the empty-transcription test fixture."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.config = MagicMock()
        self.config.bubble_behavior = "show_on_record"
        self._waveform_bubble = MagicMock()
        self._schedule_timer = MagicMock()
        self.models = MagicMock()
        self.recording = MagicMock()
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

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


class TestSharedResourceThrottle:
    """The 60s probe throttle must survive across pipeline instances."""

    def test_second_pipeline_within_interval_skips_probe(self, monkeypatch):
        calls = []

        def _fake_check(*, logger=None):
            calls.append(1)

        monkeypatch.setattr(
            "voice_typer.server.resource_probe.check_resources",
            _fake_check,
        )
        app = SimpleNamespace()

        first = _new_pipeline(app)
        first._check_resources_throttled()
        assert len(calls) == 1
        assert first._last_resources_check_ts > 0.0

        second = _new_pipeline(app)
        second._check_resources_throttled()
        assert len(calls) == 1, "second cycle within 60s must skip the probe"

    def test_stale_shared_timestamp_runs_probe_again(self, monkeypatch):
        calls = []

        def _fake_check(*, logger=None):
            calls.append(1)

        monkeypatch.setattr(
            "voice_typer.server.resource_probe.check_resources",
            _fake_check,
        )
        import time

        app = SimpleNamespace()
        app._shared_resources_check_ts = time.monotonic() - 120.0

        pipeline = _new_pipeline(app)
        pipeline._check_resources_throttled()
        assert len(calls) == 1, "stale (>60s) shared timestamp must run the probe"


class TestStreamingEmptyBatchRetry:
    """Streaming-empty on loud audio must retry the batch backend."""

    def _streaming_app(self, finalize_text: str, batch_text: str, *, zero_input: bool = True):
        app = _PipelineTestApp()
        session = MagicMock()

        def _finalize(audio):
            if zero_input and isinstance(audio, np.ndarray) and audio.size:
                audio.fill(0)
            return finalize_text

        session.finalize.side_effect = _finalize
        app.recording.pop_streaming_session.return_value = session

        active = MagicMock()
        active.is_loaded = True
        active.device_info = "mock-device"
        active.last_quality_summary = None
        seen = {}

        def _batch(audio, audio_stats=None, local_engine=None):
            seen["audio"] = np.array(audio, copy=True) if isinstance(audio, np.ndarray) else audio
            seen["audio_stats"] = audio_stats
            return batch_text

        active.transcribe_with_fallback.side_effect = _batch
        app.models.active_transcriber.return_value = active

        @contextlib.contextmanager
        def _busy(name=None):
            yield name

        app.models.registry.busy_context.side_effect = _busy
        app.models.registry.active_name = "whisper"
        return app, active, seen

    def test_high_energy_streaming_empty_retries_batch(self):
        audio = (np.ones(16000 * 8, dtype=np.float32) * 0.05).copy()
        app, active, seen = self._streaming_app("", "hello world")
        pipeline = _new_pipeline(app)
        pipeline._audio = audio
        pipeline._audio_stats = (0.0339, 0.1470, 2.6)
        pipeline._duration = 8.3
        pipeline._recorded_rms = 0.0339

        result = pipeline._transcribe()

        assert result == "hello world"
        assert active.transcribe_with_fallback.call_count == 1
        retry_audio = seen["audio"]
        assert isinstance(retry_audio, np.ndarray)
        assert float(np.sqrt(np.mean(np.square(retry_audio)))) > 0.01, (
            "batch retry must use intact samples, not the zeroed finalize buffer"
        )
        assert seen["audio_stats"] == (0.0339, 0.1470, 2.6)

    def test_low_energy_streaming_empty_does_not_retry(self):
        audio = (np.ones(16000, dtype=np.float32) * 0.0005).copy()
        app, active, _ = self._streaming_app("", "")
        pipeline = _new_pipeline(app)
        pipeline._audio = audio
        pipeline._audio_stats = (0.0005, 0.0010, 99.0)
        pipeline._duration = 2.0
        pipeline._recorded_rms = 0.0005

        result = pipeline._transcribe()

        assert result == ""
        active.transcribe_with_fallback.assert_not_called()

    def test_streaming_success_does_not_retry(self):
        audio = (np.ones(16000 * 8, dtype=np.float32) * 0.05).copy()
        app, active, _ = self._streaming_app("streaming text", "batch text")
        pipeline = _new_pipeline(app)
        pipeline._audio = audio
        pipeline._audio_stats = (0.0339, 0.1470, 2.6)
        pipeline._duration = 8.3
        pipeline._recorded_rms = 0.0339

        assert pipeline._transcribe() == "streaming text"
        active.transcribe_with_fallback.assert_not_called()

    def test_empty_warning_survives_secret_redaction(self, caplog):
        """The empty-result remediation hint must not contain a 20+ char"""
        import re

        from voice_typer.server._secrets import redact_secret

        app = _PipelineTestApp()
        app.recording.pop_streaming_session.return_value = None
        active = MagicMock()
        active.transcribe_with_fallback.return_value = ""
        active.device_info = "mock-device"
        app.models.active_transcriber.return_value = active
        pipeline = _new_pipeline(app)
        pipeline._duration = 8.3
        pipeline._recorded_rms = 0.0339
        pipeline._audio_stats = (0.0339, 0.1470, 2.6)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.dictation_pipeline"):
            pipeline._transcribe()

        warnings = [r for r in caplog.records if "Empty transcription result" in r.getMessage()]
        assert warnings
        msg = warnings[0].getMessage()
        assert "check empty transcription handler guidance" in msg
        assert redact_secret(msg) == msg, f"warning must survive redaction, got: {redact_secret(msg)!r}"
        long_runs = re.findall(r"[A-Za-z0-9_\-]{20,}", msg)
        assert not long_runs, f"warning contains redactable runs: {long_runs}"


def _build_mock_recorder(*, device, open_success=True):
    recorder = MagicMock(name="recorder")
    recorder.config = MagicMock(name="config")
    recorder.config.sample_rate = 16000
    recorder.config.microphone = None
    recorder.config.save.return_value = True
    recorder._session_state.cache_session_config.return_value = 30
    recorder._devices._resolve_device.return_value = device
    recorder._devices._same_physical_microphone_candidates.return_value = [device]
    recorder._stream_lifecycle.build_audio_callback.return_value = object()
    if open_success:
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")
    else:
        recorder._stream_lifecycle._stream = None
    recorder._recording_event = threading.Event()
    recorder._preroll_active = False
    recorder._preroll_seconds = 0.0
    recorder._preroll_buffer = collections.deque(maxlen=0)
    recorder._audio_pipeline._buffer_sr = None
    recorder._effective_sr = 16000
    return recorder


class TestMicrophoneFallbackWording:
    """System Default resolution is not a failure; genuine fallbacks name it."""

    def test_system_default_resolution_logs_debug_not_failure(self, caplog):
        from voice_typer.server.recording.recording_lifecycle import start_recording

        recorder = _build_mock_recorder(device=None)
        recorder._devices._resolve_device.return_value = None
        recorder._devices._same_physical_microphone_candidates.return_value = [27]
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (27, 16000, None)
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            start_recording(recorder)

        assert not any(
            "saved selection unchanged" in rec.message and rec.levelno == logging.INFO for rec in caplog.records
        ), "normal System Default resolution must not log a failure INFO"
        assert any("System Default resolved to device [27]" in rec.getMessage() for rec in caplog.records), (
            "normal resolution should note the concrete device at DEBUG"
        )

    def test_system_default_genuine_fallback_names_default(self, caplog):
        from voice_typer.server.recording.recording_lifecycle import start_recording

        recorder = _build_mock_recorder(device=None, open_success=False)
        recorder._devices._resolve_device.return_value = None
        recorder._devices._same_physical_microphone_candidates.return_value = [None]
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (
            None,
            16000,
            RuntimeError("default failed"),
        )

        def _fallback_side_effect(rec, candidates, callback, eff_sr, last_err):
            recorder._stream_lifecycle._stream = MagicMock(name="fallback-stream")
            return (27, 16000, True, None)

        recorder._stream_lifecycle.open_stream_fallback.side_effect = _fallback_side_effect

        with caplog.at_level(logging.INFO, logger="voice_typer.server.recording"):
            start_recording(recorder)

        infos = [rec for rec in caplog.records if "saved selection unchanged" in rec.message]
        assert infos, "genuine fallback must keep the saved-selection INFO"
        assert "System Default" in infos[0].getMessage()
        assert "[None]" not in infos[0].getMessage()

    def test_concrete_fallback_keeps_saved_selection_notice(self, caplog):
        from voice_typer.server.recording.recording_lifecycle import start_recording

        recorder = _build_mock_recorder(device=5)
        recorder._devices._resolve_device.return_value = 5
        recorder._devices._same_physical_microphone_candidates.return_value = [5]
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (7, 16000, None)
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")

        with caplog.at_level(logging.INFO, logger="voice_typer.server.recording"):
            start_recording(recorder)

        assert any("saved selection unchanged" in rec.message for rec in caplog.records)
