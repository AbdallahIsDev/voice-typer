"""regression tests for ``TranscriptionEngine._is_gpu_runtime_error``."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_heavy_imports_for_classifier(monkeypatch):
    """Mock ``faster_whisper`` and ``ctranslate2`` so the engine can be"""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock(name="mock_ctranslate2")
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


@pytest.fixture()
def cuda_engine():
    """classifier's ``if self._device == \"cpu\": return False`` early-exit"""
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    engine._device = "cuda"
    engine._compute_type = "float16"
    return engine


class TestIsGpuRuntimeErrorClassifier:
    """``_is_gpu_runtime_error`` class-hierarchy / MRO / attribute"""

    def test_ctranslate2_cuda_error_class_match(self, cuda_engine, monkeypatch):
        """#2 (check #2): a real ``ctranslate2.CUDAError`` subclass"""
        import ctranslate2

        class FakeCUDAError(RuntimeError):
            """Real class, mirrors ctranslate2.CUDAError's hierarchy."""

        monkeypatch.setattr(ctranslate2, "CUDAError", FakeCUDAError, raising=False)

        exc = FakeCUDAError("ctranslate2 CUDA kernel failed")
        assert cuda_engine._is_gpu_runtime_error(exc) is True, (
            "an exception that IS a subclass of ctranslate2.CUDAError "
            "must be classified as a GPU runtime error (check #2)."
        )

    def test_mro_class_name_match(self, cuda_engine):
        """#3 (check #3): an exception whose class name contains"""

        class CudaRuntimeError(RuntimeError):
            pass

        assert "cuda" in CudaRuntimeError.__name__.lower(), (
            "test class name must contain 'cuda' so the MRO check fires."
        )

        exc = CudaRuntimeError("wrapped cuda error from a third-party lib")
        assert cuda_engine._is_gpu_runtime_error(exc) is True, (
            "an exception whose class name (in the MRO) contains 'cuda' "
            "must be classified as a GPU runtime error (check #3)."
        )

    def test_attribute_check_cuda_error_flag(self, cuda_engine):
        """#4 (check #4): an exception carrying a ``.cuda_error``"""
        exc = RuntimeError("oom")
        exc.cuda_error = "oom"  # type: ignore[attr-defined]

        # Defensive: confirm the exception would NOT match the substring
        assert "cuda" not in str(exc).lower()
        assert "cublas" not in str(exc).lower()
        assert "cudnn" not in str(exc).lower()
        assert "gpu" not in str(exc).lower()

        assert cuda_engine._is_gpu_runtime_error(exc) is True, (
            "an exception with .cuda_error set must be classified as a GPU runtime error (check #4)."
        )
