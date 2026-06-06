"""Tests for the optional Qwen3-ASR backend."""

import os
import pytest
import numpy as np
from unittest.mock import MagicMock, patch


class TestQwenEngineUnit:
    """Unit tests for QwenEngine — all model calls are mocked."""

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.qwen_engine import QwenEngine
        return QwenEngine(model_path=model_path, **kwargs)

    def test_init_defaults(self):
        engine = self._make_engine()
        assert engine.model_path == "/fake/qwen/model"
        assert engine.device == "cuda"
        assert engine.language == "en"
        assert engine.is_loaded is False

    def test_transcribe_raises_when_not_loaded(self):
        engine = self._make_engine()
        with pytest.raises(RuntimeError, match="not loaded"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_transcribe_empty_audio_returns_empty(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        result = engine.transcribe(np.array([], dtype=np.float32))
        assert result == ""

    def test_load_success(self):
        engine = self._make_engine()
        mock_model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "hello from qwen"
        mock_model.transcribe.return_value = [mock_transcription]

        mock_qwen_module = MagicMock()
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        with patch.dict("sys.modules", {"qwen_asr": mock_qwen_module}):
            engine.load()

        assert engine.is_loaded is True

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "hello from qwen"

    def test_load_failure_missing_package(self):
        engine = self._make_engine()
        # Ensure qwen_asr is not importable — load() returns False
        with patch.dict("sys.modules", {"qwen_asr": None}):
            result = engine.load()

        assert result is False
        assert engine.is_loaded is False

    def test_load_failure_missing_weights(self):
        engine = self._make_engine()
        mock_qwen_module = MagicMock()
        mock_qwen_module.Qwen3ASRModel.from_pretrained.side_effect = FileNotFoundError("weights not found")
        with patch.dict("sys.modules", {"qwen_asr": mock_qwen_module}):
            engine.load()

        assert engine.is_loaded is False

    def test_load_failure_cuda_error(self):
        engine = self._make_engine()
        mock_qwen_module = MagicMock()
        mock_qwen_module.Qwen3ASRModel.from_pretrained.side_effect = RuntimeError("CUDA out of memory")
        with patch.dict("sys.modules", {"qwen_asr": mock_qwen_module}):
            engine.load()

        assert engine.is_loaded is False

    def test_unload_clears_model(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        assert engine.is_loaded is True

        engine.unload()
        # pyrefly: ignore [unnecessary-comparison]
        assert engine.is_loaded is False

    def test_transcribe_strips_whitespace(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "  hello world  "
        engine._model.transcribe.return_value = [mock_transcription]

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "hello world"

    def test_transcribe_empty_result(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        engine._model.transcribe.return_value = []

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == ""


class TestQwenConfigKeys:
    """Verify new config keys exist and have correct defaults."""

    def test_asr_backend_default_is_whisper(self):
        from voice_typer.config import Config
        c = Config()
        assert c.asr_backend == "whisper"

    def test_qwen_model_path_default_is_none(self):
        from voice_typer.config import Config
        c = Config()
        assert c.qwen_model_path is None

    def test_asr_backend_persists(self, tmp_path, monkeypatch):
        from voice_typer.config import Config
        monkeypatch.setattr("voice_typer.config._config_dir", lambda: tmp_path)
        model_dir = tmp_path / "qwen_model"
        model_dir.mkdir()
        c = Config(asr_backend="qwen", qwen_model_path=str(model_dir))
        c.save()
        loaded = Config.load()
        assert loaded.asr_backend == "qwen"
        assert loaded.qwen_model_path == str(model_dir)


class TestQwenBackendSelection:
    """Verify the app uses the correct backend based on config."""

    def test_whisper_is_default_backend(self):
        from voice_typer.config import Config
        c = Config()
        assert c.asr_backend == "whisper"

    def test_qwen_backend_requires_model_path(self):
        from voice_typer.config import Config
        c = Config(asr_backend="qwen", qwen_model_path=None)
        # Without a model path, Qwen can't load
        assert c.qwen_model_path is None


@pytest.mark.skipif(
    not os.environ.get("VOICE_TYPER_TEST_QWEN"),
    reason="Qwen integration tests require VOICE_TYPER_TEST_QWEN=1 and real model weights",
)
class TestQwenIntegration:
    """Integration tests — only run when user has downloaded Qwen weights."""

    def test_real_qwen_transcribe(self):
        """Test with real Qwen model. Requires VOICE_TYPER_TEST_QWEN=1."""
        from voice_typer.qwen_engine import QwenEngine
        model_path = os.environ.get("VOICE_TYPER_QWEN_PATH", "Qwen3-ASR-0.6B")
        engine = QwenEngine(model_path=model_path)
        engine.load()
        assert engine.is_loaded
        # Generate 1s of silence
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert isinstance(result, str)


class TestP1WhisperSkipWhenQwenActive:
    """P1 fix: Skip Whisper load when Qwen is active, with lazy Whisper fallback."""

    def _make_app_with_qwen(self, monkeypatch, tmp_path, qwen_loaded=True):
        """Create a VoiceTyperApp with Qwen backend configured."""
        import sys
        from unittest.mock import MagicMock

        # Mock heavy imports BEFORE importing the app module
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
        monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

        monkeypatch.setattr("voice_typer.config._config_dir", lambda: tmp_path)
        monkeypatch.setattr("voice_typer.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.app.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.app.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.app.list_microphones", lambda: [])
        monkeypatch.setattr("voice_typer.app.atexit.register", lambda *a, **kw: None)
        from voice_typer.hotkeys import PynputHotkey
        monkeypatch.setattr(
            "voice_typer.app.create_hotkey_backend",
            lambda hotkey_str: PynputHotkey(hotkey_str),
        )

        # Write a config with Qwen backend
        import json
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "asr_backend": "qwen",
            "qwen_model_path": str(tmp_path / "qwen_model"),
        }))

        # Create the Qwen model dir so path validation passes
        (tmp_path / "qwen_model").mkdir(exist_ok=True)

        from voice_typer.app import VoiceTyperApp
        app = VoiceTyperApp()

        # Mock the Qwen engine
        app._qwen_engine = MagicMock()
        app._qwen_engine.is_loaded = qwen_loaded
        app._qwen_engine.load = MagicMock()

        return app

    def test_startup_skips_whisper_when_qwen_active(self, monkeypatch, tmp_path):
        """When Qwen backend is active and loaded, Whisper should NOT be loaded during startup."""
        app = self._make_app_with_qwen(monkeypatch, tmp_path, qwen_loaded=True)
        app._sync_autostart = MagicMock()
        app._load_microphones = MagicMock()
        app._register_hotkey = MagicMock()
        app.recorder.warm_up_resampler = MagicMock()

        # Track if Whisper load was attempted
        whisper_load_called = []
        original_try_load = app._try_load_model
        def track_try_load(*args, **kwargs):
            whisper_load_called.append(True)
            # Simulate successful load so it doesn't loop
            # pyrefly: ignore [read-only]
            app.transcriber.is_loaded = True
            # pyrefly: ignore [read-only]
            app.transcriber.device_info = "cpu (int8)"
            # pyrefly: ignore [read-only]
            app.transcriber.loaded_via = "cpu/int8/small.en"
            original_try_load(*args, **kwargs)

        app._try_load_model = track_try_load

        app._do_startup()

        # Qwen engine should have been loaded
        # pyrefly: ignore [missing-attribute]
        app._qwen_engine.load.assert_called_once()
        # Whisper should NOT have been loaded since Qwen succeeded
        assert len(whisper_load_called) == 0, "Whisper should not be loaded when Qwen is active and loaded"

    def test_startup_falls_back_to_whisper_when_qwen_fails(self, monkeypatch, tmp_path):
        """When Qwen backend fails to load, Whisper should be loaded as fallback."""
        app = self._make_app_with_qwen(monkeypatch, tmp_path, qwen_loaded=False)
        app._sync_autostart = MagicMock()
        app._load_microphones = MagicMock()
        app._register_hotkey = MagicMock()
        app.recorder.warm_up_resampler = MagicMock()

        # Mock TranscriptionEngine so _do_startup creates a mock
        mock_transcriber = MagicMock()
        mock_transcriber.is_loaded = False
        mock_transcriber.load = MagicMock()
        mock_transcriber.device_info = "cpu (int8)"
        mock_transcriber.loaded_via = "cpu/int8/small.en"
        monkeypatch.setattr(
            "voice_typer.app.TranscriptionEngine",
            MagicMock(return_value=mock_transcriber),
        )
        app.transcriber = mock_transcriber

        # Make Qwen load() fail (is_loaded stays False)
        # pyrefly: ignore [missing-attribute]
        app._qwen_engine.load = MagicMock()  # load does nothing, is_loaded stays False

        app._do_startup()

        # Qwen engine should have been attempted
        # pyrefly: ignore [missing-attribute]
        app._qwen_engine.load.assert_called_once()
        # Whisper should have been loaded as fallback
        app.transcriber.load.assert_called_once()

    def test_start_dictation_lazy_loads_whisper_when_qwen_unavailable(self, monkeypatch, tmp_path):
        """When Qwen is configured but not loaded, _start_dictation should lazy-load Whisper."""
        app = self._make_app_with_qwen(monkeypatch, tmp_path, qwen_loaded=False)
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app.transcriber = MagicMock()
        app.transcriber.is_loaded = False
        app.transcriber.load = MagicMock()
        app.transcriber.device_info = "cpu (int8)"
        app.transcriber.loaded_via = "cpu/int8/small.en"

        def mock_load(**kwargs):
            # pyrefly: ignore [read-only]
            app.transcriber.is_loaded = True
        app.transcriber.load = mock_load

        app._start_dictation()

        # Should have attempted to start recording (Whisper was lazy-loaded)
        app.recorder.start.assert_called_once()


class TestM23LoadReturnValues:
    """M23: QwenEngine load() silently eats errors."""

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.qwen_engine import QwenEngine
        return QwenEngine(model_path=model_path, **kwargs)

    def test_load_returns_true_on_success(self):
        engine = self._make_engine()
        mock_qwen_module = MagicMock()
        mock_model = MagicMock()
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        with patch.dict("sys.modules", {"qwen_asr": mock_qwen_module}):
            result = engine.load()
        assert result is True
        assert engine.is_loaded is True

    def test_load_returns_false_on_import_error(self):
        engine = self._make_engine()
        with patch.dict("sys.modules", {"qwen_asr": None}):
            result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_returns_false_on_runtime_error(self):
        engine = self._make_engine()
        mock_qwen_module = MagicMock()
        mock_qwen_module.Qwen3ASRModel.from_pretrained.side_effect = RuntimeError("fail")
        with patch.dict("sys.modules", {"qwen_asr": mock_qwen_module}):
            result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_returns_true_on_already_loaded(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        result = engine.load()
        assert result is True


class TestM13HallucinationDetection:
    """M13: QwenEngine no hallucination detection."""

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.qwen_engine import QwenEngine
        return QwenEngine(model_path=model_path, **kwargs)

    def test_rejects_hallucination_on_silence(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Thanks for watching"
        engine._model.transcribe.return_value = [mock_transcription]

        # Near-silence audio (RMS < 0.001)
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == ""

    def test_keeps_hallucination_phrase_with_audio(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Thanks for watching"
        engine._model.transcribe.return_value = [mock_transcription]

        # Audio with real speech (RMS > 0.001)
        audio = np.full(16000, 0.05, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "Thanks for watching"

    def test_keeps_non_hallucination_on_silence(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Hello world"
        engine._model.transcribe.return_value = [mock_transcription]

        # Near-silence audio but text is not a known hallucination
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "Hello world"
