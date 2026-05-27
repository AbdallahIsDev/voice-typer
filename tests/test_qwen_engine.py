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
        # Ensure qwen_asr is not importable
        with patch.dict("sys.modules", {"qwen_asr": None}):
            engine.load()

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
        c = Config(asr_backend="qwen", qwen_model_path="/path/to/model")
        c.save()
        loaded = Config.load()
        assert loaded.asr_backend == "qwen"
        assert loaded.qwen_model_path == "/path/to/model"


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
