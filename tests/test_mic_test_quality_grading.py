"""Regression tests for the mic-test quality-verdict fixes (backend only)."""

from __future__ import annotations

import inspect

import numpy as np
import pytest

SAMPLE_RATE = 16000


@pytest.fixture(autouse=True)
def _isolated_state_and_recordings(tmp_path, monkeypatch):
    """Reset level-monitor state and scope the WAV transport per test."""
    import voice_typer.server.level_monitor as lm
    from voice_typer.server.level_monitor import test_recording as _tr

    lm._reset_state_for_tests()
    recordings = tmp_path / "mic-test-recordings"

    def _isolated_dir():
        recordings.mkdir(parents=True, exist_ok=True)
        return recordings

    monkeypatch.setattr(_tr, "_test_recordings_dir", _isolated_dir)
    yield
    lm._reset_state_for_tests()


def _sine_rms(amplitude: float) -> float:
    """RMS of a full-cycle sine with the given peak amplitude."""
    return amplitude / np.sqrt(2.0)


def _drive_stop(raw_audio: np.ndarray, rms_hist: list[float], silence_blocks: int) -> dict:
    """Populate worker-style state, then run ``stop_test_recording``."""
    from voice_typer.server.level_monitor import test_recording as _tr
    from voice_typer.server.level_monitor._state import _state

    _state._test_mode = True
    _state._monitor_sample_rate = SAMPLE_RATE
    _state._test_raw_chunks.append(np.asarray(raw_audio, dtype=np.float32))
    for value in rms_hist:
        _state._test_rms_history.append(float(value))
    _state._test_silence_blocks = int(silence_blocks)
    return _tr.stop_test_recording()


def _speech_with_pauses() -> tuple[np.ndarray, list[float]]:
    """Loud clean speech shape: 220 Hz tone at 0.06 peak for the first"""
    n = SAMPLE_RATE
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    tone = 0.06 * np.sin(2 * np.pi * 220 * t)
    raw = np.concatenate([tone[: n // 2], np.zeros(n - n // 2)]).astype(np.float32)
    speech_rms = _sine_rms(0.06)
    hist = [speech_rms] * 16 + [0.0] * 15
    return raw, hist


class TestNoiseFloorGrading:
    def test_loud_clean_speech_with_pauses_is_not_high_noise(self):
        """Overall RMS ≈ 0.03 but the quiet half reveals a silent room:"""
        raw, hist = _speech_with_pauses()
        result = _drive_stop(raw, hist, silence_blocks=15)

        assert result["success"] is True
        quality = result["quality"]
        assert quality["noise_level"] == "low"
        assert "High background noise" not in quality["detected_issues"]
        assert quality["has_voice"] is True
        assert quality["volume_level"] == "good"

    def test_gapless_loud_tone_never_reports_high_noise(self):
        """A gapless 0.03-RMS tone has no quiet floor to measure, but"""
        t = np.arange(SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE
        raw = (0.0424 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        result = _drive_stop(raw, [0.03] * 31, silence_blocks=0)

        assert result["quality"]["noise_level"] == "moderate"
        assert "High background noise" not in result["quality"]["detected_issues"]

    def test_sustained_voice_caps_noise_at_moderate(self):
        """Continuous loud voiced audio (floor ≥ high band) must not"""
        t = np.arange(SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE
        raw = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        result = _drive_stop(raw, [_sine_rms(0.3)] * 31, silence_blocks=0)

        quality = result["quality"]
        assert quality["has_voice"] is True
        assert quality["noise_level"] == "moderate"

    def test_short_loud_test_without_voice_share_still_high(self):
        """short to establish a voice share (min 3 non-silent blocks), so"""
        raw = np.full(1024, 0.08, dtype=np.float32)
        result = _drive_stop(raw, [0.08, 0.08], silence_blocks=0)

        quality = result["quality"]
        assert quality["has_voice"] is False
        assert quality["noise_level"] == "high"
        assert "High background noise" in quality["detected_issues"]

    def test_quiet_room_stays_low_noise(self):
        """Digital silence: low noise floor, very-low volume, no voice."""
        raw = np.zeros(SAMPLE_RATE, dtype=np.float32)
        result = _drive_stop(raw, [0.0] * 31, silence_blocks=31)

        quality = result["quality"]
        assert quality["noise_level"] == "low"
        assert quality["volume_level"] == "very_low"
        assert quality["has_voice"] is False


class TestVolumeBandAgreement:
    def test_shared_boundaries(self):
        """Both audio paths read the same boundary constants."""
        from voice_typer.server._audio_constants import AUDIO_LOW_VOLUME_RMS, AUDIO_SILENCE_RMS
        from voice_typer.server.audio_quality import AudioQualityAnalyzer
        from voice_typer.server.level_monitor import test_recording as _tr

        assert pytest.approx(0.005) == AUDIO_LOW_VOLUME_RMS
        assert pytest.approx(0.0005) == AUDIO_SILENCE_RMS
        assert AudioQualityAnalyzer.LOW_VOLUME_THRESHOLD == AUDIO_LOW_VOLUME_RMS
        assert _tr.MIC_TEST_GOOD_VOLUME_RMS == AUDIO_LOW_VOLUME_RMS
        assert _tr.MIC_TEST_VERY_LOW_VOLUME_RMS == AUDIO_SILENCE_RMS

    def test_quiet_but_audible_rms_is_low_on_both_paths(self):
        """RMS 0.003: dictation flags low volume AND the mic test"""
        from voice_typer.server.audio_quality import AudioQualityAnalyzer

        amplitude = 0.003 * np.sqrt(2.0)
        t = np.arange(SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE
        raw = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

        report = AudioQualityAnalyzer().analyze_full_audio(raw)
        assert report.low_volume_detected is True

        result = _drive_stop(raw, [0.003] * 31, silence_blocks=0)
        assert result["quality"]["volume_level"] == "low"

    def test_healthy_rms_is_good_and_unflagged_on_both_paths(self):
        """RMS 0.01: dictation raises no low-volume flag and the mic"""
        from voice_typer.server.audio_quality import AudioQualityAnalyzer

        amplitude = 0.01 * np.sqrt(2.0)
        t = np.arange(SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE
        raw = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

        report = AudioQualityAnalyzer().analyze_full_audio(raw)
        assert report.low_volume_detected is False

        result = _drive_stop(raw, [0.01] * 31, silence_blocks=0)
        assert result["quality"]["volume_level"] == "good"

    def test_worker_silence_gate_matches_very_low_boundary(self):
        """A 0.0003-RMS block counts as silence, a 0.001-RMS block does"""
        from voice_typer.server._audio_constants import AUDIO_SILENCE_RMS
        from voice_typer.server.level_monitor import worker as _worker
        from voice_typer.server.level_monitor._state import _state

        _state._monitor_active = True
        _state._test_mode = True
        _state._monitor_sample_rate = SAMPLE_RATE
        _state._level_processor = None

        _worker._process_level_chunk(np.full(512, 0.0003, dtype=np.float32), None)
        assert _state._test_silence_blocks == 1
        _worker._process_level_chunk(np.full(512, 0.001, dtype=np.float32), None)
        assert _state._test_silence_blocks == 1
        assert pytest.approx(0.0005) == AUDIO_SILENCE_RMS


class TestVoiceGating:
    def test_single_click_in_silence_is_not_voice(self):
        """One loud transient (peak 0.8) in an otherwise silent test:"""
        raw = np.zeros(SAMPLE_RATE, dtype=np.float32)
        raw[SAMPLE_RATE // 2] = 0.8
        hist = [0.8 / np.sqrt(512.0)] + [0.0] * 30
        result = _drive_stop(raw, hist, silence_blocks=30)

        quality = result["quality"]
        assert quality["silence_ratio"] == pytest.approx(30 / 31, abs=1e-4)
        assert quality["has_voice"] is False
        assert "No voice detected, try speaking during the test" in quality["detected_issues"]

    def test_sustained_speech_counts_as_voice(self):
        raw, hist = _speech_with_pauses()
        result = _drive_stop(raw, hist, silence_blocks=15)

        assert result["quality"]["has_voice"] is True


class TestVolumeScorePenalty:
    def test_inaudible_input_charged_once(self):
        """Very-low input (single 0.06 impulse, no block history) takes"""
        raw = np.zeros(SAMPLE_RATE, dtype=np.float32)
        raw[0] = 0.06
        result = _drive_stop(raw, [], silence_blocks=0)

        quality = result["quality"]
        assert quality["volume_level"] == "very_low"
        assert quality["has_voice"] is True
        assert quality["estimated_transcription_quality"] == 60


class TestCancelGuard:
    def test_raw_only_state_clears_and_reports_inactive(self):
        """A raw-only leftover must be cleared even though no test is"""
        from unittest.mock import patch

        from voice_typer.server.level_monitor import test_recording as _tr
        from voice_typer.server.level_monitor._state import _state

        _state._test_raw_chunks.append(np.zeros(512, dtype=np.float32))
        with patch.object(_tr, "_secure_clear_test_chunks") as spy:
            result = _tr._cancel_test_locked()

        assert result is False
        assert len(_state._test_raw_chunks) == 0
        assert spy.called


class TestStagingPurge:
    def test_start_purges_crash_leftover_tmp_files(self, monkeypatch):
        """A kill between the staging write and the atomic replace"""
        from voice_typer.server.level_monitor import test_recording as _tr
        from voice_typer.server.level_monitor._state import _state

        recordings = _tr._test_recordings_dir()
        leftover = recordings / "test-raw-20240101-000000-deadbeef.wav.tmp"
        leftover.write_bytes(b"partial biometric audio")
        stale_wav = recordings / "test-filtered-old.wav"
        stale_wav.write_bytes(b"stale")

        monkeypatch.setattr(
            "voice_typer.server.level_monitor.monitoring.start_monitoring",
            lambda mic_id=None: {"success": True},
        )
        _state._monitor_sample_rate = SAMPLE_RATE
        try:
            assert _tr.start_test_recording(duration=1.0)["success"] is True
            assert not leftover.exists()
            assert not stale_wav.exists()
        finally:
            _tr.cancel_test_recording()


class TestPersistFailureEnvelope:
    def test_write_failure_returns_quality_without_raising(self, monkeypatch):
        """Disk-full after the chunk state was cleared: stop reports"""
        from voice_typer.server.level_monitor import test_recording as _tr

        def _boom(_buf, _kind):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(_tr, "_write_test_wav", _boom)
        raw, hist = _speech_with_pauses()
        result = _drive_stop(raw, hist, silence_blocks=15)

        assert result["success"] is False
        assert result["audio_file"] is None
        assert result["raw_audio_file"] is None
        assert result["quality"]["volume_level"] == "good"
        assert result["quality"]["has_voice"] is True
        assert "persist" in result["message"].lower()


class TestTranscriptionExceptionMarker:
    def test_setup_failure_marks_transcription_unavailable(self, monkeypatch, tmp_path):
        """An unreadable persisted WAV (outer except path) must set the"""
        from types import SimpleNamespace
        from typing import Any, cast

        from voice_typer.server import level_monitor
        from voice_typer.server.service.microphone_test import MicrophoneTestMixin

        bad = tmp_path / "test-filtered.wav"
        bad.write_bytes(b"not a wav file at all" * 100)
        monkeypatch.setattr(
            level_monitor,
            "stop_test_recording",
            lambda: {
                "success": True,
                "audio_file": {"path": str(bad), "bytes": bad.stat().st_size},
            },
        )
        mixin = MicrophoneTestMixin()
        mixin._app = cast(Any, SimpleNamespace(models=None))

        result = mixin.microphone_test_stop()

        assert result["success"] is True
        assert result["transcription_unavailable"] is True
        assert result["transcription_reason"] == "transcription_failed"
        assert "transcription" not in result

    def test_engine_failure_marks_transcription_unavailable(self, monkeypatch, tmp_path):
        """A throwing engine (inner except path) must set the same honest"""
        import wave
        from types import SimpleNamespace
        from typing import Any, cast

        import numpy as np
        from voice_typer.server import level_monitor
        from voice_typer.server.service.microphone_test import MicrophoneTestMixin

        good = tmp_path / "test-filtered.wav"
        with wave.open(str(good), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes((np.zeros(1600, dtype=np.float32) * 32767).astype(np.int16).tobytes())
        monkeypatch.setattr(
            level_monitor,
            "stop_test_recording",
            lambda: {
                "success": True,
                "audio_file": {"path": str(good), "bytes": good.stat().st_size},
            },
        )

        class _BoomEngine:
            is_loaded = True

            def transcribe(self, _audio):
                raise RuntimeError("engine exploded")

        mixin = MicrophoneTestMixin()
        mixin._app = cast(
            Any,
            SimpleNamespace(models=SimpleNamespace(active_transcriber=lambda: _BoomEngine())),
        )

        result = mixin.microphone_test_stop()

        assert result["success"] is True
        assert result["transcription_unavailable"] is True
        assert result["transcription_reason"] == "transcription_failed"
        assert "transcription" not in result


class TestWordingAlignment:
    def test_handler_docstring_caps_test_at_30s(self):
        """The consent docstring must not promise a 60 s exfiltration"""
        import voice_typer.server.handlers.microphone_test_handlers as _handlers

        source = inspect.getsource(_handlers)
        assert "up to 60s" not in source
        assert "up to 30s" in source

    def test_auto_stop_comment_matches_sized_deques(self):
        """The auto-stop comment must not claim a fixed 3 s / 16 kHz"""
        from voice_typer.server.level_monitor import test_recording as _tr

        source = inspect.getsource(_tr._do_auto_stop_test)
        assert "3s of audio at 16kHz" not in source
        assert "3s of" not in source
