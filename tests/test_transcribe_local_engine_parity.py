"""``local_engine`` kwarg parity across ASR backends."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession


def _audio() -> np.ndarray:
    return np.zeros(1600, dtype=np.float32)


class TestLocalEngineKwargSignatures:
    @pytest.mark.parametrize(
        "import_path,class_name",
        [
            ("voice_typer.server.transcription", "TranscriptionEngine"),
            ("voice_typer.server.parakeet_engine", "ParakeetEngine"),
            ("voice_typer.server.qwen_engine", "QwenEngine"),
            ("voice_typer.server.cloud_engines", "CloudEngine"),
        ],
    )
    def test_transcribe_with_fallback_accepts_local_engine(self, import_path: str, class_name: str) -> None:
        import importlib

        cls = getattr(importlib.import_module(import_path), class_name)
        sig = inspect.signature(cls.transcribe_with_fallback)
        assert "local_engine" in sig.parameters, (
            f"{class_name}.transcribe_with_fallback must accept local_engine "
            "so shared call sites can pass it unconditionally."
        )
        assert sig.parameters["local_engine"].default is None, (
            f"local_engine on {class_name}.transcribe_with_fallback must default to None for backwards compatibility."
        )


class TestLocalEnginesIgnoreLocalEngine:
    def test_whisper_unloaded_raises_runtime_error_not_type_error(self) -> None:
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine()
        with pytest.raises(RuntimeError, match="Model not loaded"):
            engine.transcribe_with_fallback(_audio(), local_engine=MagicMock())

    def test_parakeet_unloaded_raises_backend_error_not_type_error(self) -> None:
        from voice_typer.server.parakeet_engine import ParakeetEngine
        from voice_typer.server.parakeet_engine._shims import TranscriptionBackendError

        engine = ParakeetEngine()
        with pytest.raises(TranscriptionBackendError):
            engine.transcribe_with_fallback(_audio(), local_engine=MagicMock())

    def test_qwen_unloaded_raises_runtime_error_not_type_error(self) -> None:
        from voice_typer.server.qwen_engine import QwenEngine

        engine = QwenEngine(model_path="nonexistent-export-dir")
        with pytest.raises(RuntimeError, match="not loaded"):
            engine.transcribe_with_fallback(_audio(), local_engine=MagicMock())


class TestStreamingFinalizeWithLocalEngine:
    def test_finalize_forwards_local_engine_to_whisper_backend(self) -> None:
        """Reproduces the reported finalize crash chain."""
        from voice_typer.server.transcription import TranscriptionEngine

        session = StreamingTranscriptionSession(
            recorder=MagicMock(),
            transcriber=TranscriptionEngine(),
            config=StreamingConfig(),
            sample_rate=16000,
            local_engine=MagicMock(),
        )
        with pytest.raises(RuntimeError, match="Model not loaded"):
            session._finalize_impl_inner(
                _audio(),
                snapshot_committed_text="",
                snapshot_last_committed_time=0.0,
            )
