"""Phase 2d degradation matrix (§8.10) — mic-test auto-transcription.

``MicrophoneTestMixin.microphone_test_stop`` attempts a best-effort
auto-transcription of the test recording so the UI can show
"You said: ...". When no engine is loaded (offline pack missing → no
offline engine; or engine still warming up), the transcription is
silently skipped — the degradation matrix requires the server to say
WHY instead. These tests pin the ``transcription_unavailable`` +
``transcription_reason`` markers.
"""

from __future__ import annotations

import io
import struct
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock

from voice_typer.server.service.microphone_test import MicrophoneTestMixin


def _tiny_wav_result(tmp_path, duration_s: float = 0.05, sample_rate: int = 16000) -> dict:
    """Persist a tiny valid mono WAV under tmp_path and return a
    file-reference stop result — mirrors the new disk transport contract."""
    import os

    n = int(sample_rate * duration_s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    wav_file = tmp_path / "test-filtered.wav"
    wav_file.write_bytes(buf.getvalue())
    if os.name != "nt":
        os.chmod(wav_file, 0o600)
    return {
        "success": True,
        "audio_file": {"path": str(wav_file), "bytes": wav_file.stat().st_size},
    }


def _mixin(app) -> MicrophoneTestMixin:
    mixin = MicrophoneTestMixin()
    mixin._app = app  # type: ignore[attr-defined] — ServiceMixinBase._app
    return mixin


class TestMicTestDegradation:
    def test_no_engine_loaded_marks_transcription_unavailable(self, monkeypatch, tmp_path):
        """Engine absent → marker set so the UI can explain the gap."""
        from voice_typer.server import level_monitor

        monkeypatch.setattr(
            level_monitor,
            "stop_test_recording",
            lambda: _tiny_wav_result(tmp_path),
        )
        app = SimpleNamespace(models=SimpleNamespace(active_transcriber=lambda: None))
        result = _mixin(app).microphone_test_stop()
        assert result["success"] is True
        assert result["transcription_unavailable"] is True
        assert result["transcription_reason"] == "no_engine_loaded"
        assert "transcription" not in result

    def test_unloaded_engine_marks_transcription_unavailable(self, monkeypatch, tmp_path):
        """Engine exists but not loaded → same marker (still warming / no pack)."""
        from voice_typer.server import level_monitor

        monkeypatch.setattr(
            level_monitor,
            "stop_test_recording",
            lambda: _tiny_wav_result(tmp_path),
        )
        engine = MagicMock()
        engine.is_loaded = False
        app = SimpleNamespace(models=SimpleNamespace(active_transcriber=lambda: engine))
        result = _mixin(app).microphone_test_stop()
        assert result["transcription_unavailable"] is True
        assert result["transcription_reason"] == "no_engine_loaded"

    def test_loaded_engine_transcribes_without_marker(self, monkeypatch, tmp_path):
        """Engine loaded → transcription present, no degradation marker."""
        from voice_typer.server import level_monitor

        monkeypatch.setattr(
            level_monitor,
            "stop_test_recording",
            lambda: _tiny_wav_result(tmp_path),
        )
        engine = MagicMock()
        engine.is_loaded = True
        engine.transcribe.return_value = "hello world"
        app = SimpleNamespace(models=SimpleNamespace(active_transcriber=lambda: engine))
        result = _mixin(app).microphone_test_stop()
        assert result["transcription"] == "hello world"
        assert "transcription_unavailable" not in result

    def test_no_recording_audio_skips_marker(self, monkeypatch):
        """stop_test_recording failure → no marker (nothing to transcribe)."""
        from voice_typer.server import level_monitor

        monkeypatch.setattr(level_monitor, "stop_test_recording", lambda: {"success": False})
        app = SimpleNamespace(models=SimpleNamespace(active_transcriber=lambda: None))
        result = _mixin(app).microphone_test_stop()
        assert result["success"] is False
        assert "transcription_unavailable" not in result
