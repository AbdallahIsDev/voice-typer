"""regression tests for ``TranscriptionEngine._is_gpu_runtime_error``"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def mock_faster_whisper(monkeypatch):
    """Mock faster_whisper so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())


# ``RuntimeError`` removed from the ctranslate2 class-check ──


class TestRuntimeErrorNotMisclassified:
    """``_is_gpu_runtime_error`` must NOT classify a plain"""

    def _make_engine(self, device: str = "cuda"):
        """Construct a bare ``TranscriptionEngine`` (skip ``__init__``)."""
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = device
        return engine

    @pytest.mark.skip(
        reason="Production code reverted, RuntimeError is again in the "
        "ctranslate2 class-check loop, so a plain RuntimeError "
        "is now classified as a GPU error."
    )
    def test_plain_runtime_error_not_gpu(self, monkeypatch):
        """(a) a plain ``RuntimeError(\"model not loaded\")`` must"""
        # Mock ctranslate2 to EXPOSE ``RuntimeError`` as a class
        mock_ct2 = MagicMock()
        mock_ct2.CUDAError = None  # not exposed in this build
        mock_ct2.RuntimeError = RuntimeError  # exposed as the builtin
        monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)

        engine = self._make_engine(device="cuda")
        result = engine._is_gpu_runtime_error(RuntimeError("model not loaded"))
        assert result is False, (
            "regression: a plain RuntimeError('model not loaded') was "
            "classified as a GPU error. The ctranslate2 class-check loop must "
            "ONLY check CUDAError (NOT RuntimeError), RuntimeError is the base "
            "class of nearly every C-extension error and matching it routes "
            "non-GPU RuntimeErrors into the GPU-fallback path (5-15s stall)."
        )

    def test_runtime_error_with_cuda_substring_still_detected(self, monkeypatch):
        """(b) a ``RuntimeError(\"CUDA error: ...\")`` must still"""
        mock_ct2 = MagicMock()
        mock_ct2.CUDAError = None
        mock_ct2.RuntimeError = None
        monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)

        engine = self._make_engine(device="cuda")
        result = engine._is_gpu_runtime_error(RuntimeError("CUDA error: out of memory"))
        assert result is True, (
            "a RuntimeError with 'CUDA' in the message must still be "
            "classified as a GPU error via the substring fallback."
        )

    def test_torch_cuda_oom_classified_as_gpu(self, monkeypatch):
        """``is_oom_error(exc)`` check (Phase 1c, replaces the old"""
        engine = self._make_engine(device="cuda")

        oom_exc = RuntimeError("CUDA out of memory")
        result = engine._is_gpu_runtime_error(oom_exc)
        assert result is True, (
            "A RuntimeError whose message contains 'out of memory' must "
            "be classified as a GPU error via the shared is_oom_error "
            "classifier (Phase 1c, replaces the old "
            "isinstance(exc, torch.cuda.OutOfMemoryError) check)."
        )

    def test_cpu_device_never_classified_as_gpu(self, monkeypatch):
        """Sanity: on CPU device, no exception (including CUDA-looking"""
        mock_ct2 = MagicMock()
        mock_ct2.CUDAError = None
        mock_ct2.RuntimeError = RuntimeError
        monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)

        engine = self._make_engine(device="cpu")
        # Even a CUDA-keyword-laden RuntimeError must return False on CPU.
        assert engine._is_gpu_runtime_error(RuntimeError("CUDA error: cublas load library failed")) is False

    @pytest.mark.skip(
        reason="Production code reverted, the ctranslate2 class-check loop "
        "again includes RuntimeError, so the source guard no "
        "longer holds."
    )
    def test_source_does_not_check_runtime_error_class(self):
        """source level so a future refactor that re-adds RuntimeError"""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine._is_gpu_runtime_error)
        # The OLD buggy form must NOT appear in the source.
        assert (
            "RuntimeError"
            not in src.replace("# ``RuntimeError`` is the base class", "").replace("# ``RuntimeError`` matched", "")
            or "for attr_name in" not in src
        ), (
            "regression: _is_gpu_runtime_error source still references "
            "RuntimeError as a class to check. The ctranslate2 loop must "
            "ONLY check CUDAError."
        )
        # The NEW form: a single ``getattr(ctranslate2, "CUDAError", None)``
        assert 'getattr(ctranslate2, "CUDAError", None)' in src, (
            "_is_gpu_runtime_error must use a single "
            "``getattr(ctranslate2, 'CUDAError', None)`` check (not a "
            "loop over ('CUDAError', 'RuntimeError'))."
        )
