"""CR-069: split from tests/test_feature_hardening_regressions.py (L651-826).

Source marker: ``tests/test_new_mem_001_gpu_release.py``.

Regression tests for NEW-MEM-001: GPU memory not released on backend switch.

Previously, ``del self._model; gc.collect()`` released Python references
but PyTorch's CUDA caching allocator retained the freed blocks for
reuse.  After 2 backend switches (Whisper → Parakeet → Whisper) on
RTX 3060/4060 (8–12 GB VRAM), the accumulated cached blocks caused
GPU OOMs.

The fix adds a shared ``release_gpu_memory()`` helper that calls
``torch.cuda.empty_cache()`` after every model unload / fallback path.

Class/method names, assertion logic, and imports below are preserved
verbatim from the original monolith — only file location has changed.
"""

# === Source: tests/test_new_mem_001_gpu_release.py ===

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.asr_utils import release_gpu_memory


class TestReleaseGpuMemoryHelper:
    """The shared helper must be safe in every environment."""

    def test_no_torch_installed_is_noop(self, monkeypatch):
        """When torch is not installed, the helper must silently no-op."""
        # Simulate torch not being importable.
        monkeypatch.setitem(sys.modules, "torch", None)
        # Must not raise.
        release_gpu_memory()

    def test_cuda_not_available_is_noop(self, monkeypatch):
        """When CUDA is not available, the helper must no-op."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.cuda.synchronize = MagicMock()
        fake_torch.cuda.empty_cache = MagicMock()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        release_gpu_memory()

        # is_available was called; synchronize/empty_cache were NOT.
        fake_torch.cuda.is_available.assert_called_once()
        fake_torch.cuda.synchronize.assert_not_called()
        fake_torch.cuda.empty_cache.assert_not_called()

    def test_calls_empty_cache_when_cuda_available(self, monkeypatch):
        """When CUDA is available, the helper must call empty_cache()."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.synchronize = MagicMock()
        fake_torch.cuda.empty_cache = MagicMock()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        release_gpu_memory()

        # is_available, synchronize, and empty_cache were all called.
        fake_torch.cuda.is_available.assert_called_once()
        fake_torch.cuda.synchronize.assert_called_once()
        fake_torch.cuda.empty_cache.assert_called_once()

    def test_swallows_runtime_errors(self, monkeypatch):
        """If torch.cuda.synchronize() raises (e.g. CUDA not initialized),
        the helper must not propagate the exception."""
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.synchronize.side_effect = RuntimeError("cuda not initialized")
        fake_torch.cuda.empty_cache = MagicMock()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        # Must not raise.
        release_gpu_memory()

        # empty_cache was NOT called because synchronize raised first.
        fake_torch.cuda.empty_cache.assert_not_called()


class TestEnginesCallReleaseGpuMemory:
    """Each ASR engine's unload() must call release_gpu_memory()."""

    def test_transcription_engine_unload_calls_release(self):
        """TranscriptionEngine.unload() must invoke release_gpu_memory()."""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        source = inspect.getsource(TranscriptionEngine.unload)
        assert "release_gpu_memory()" in source, (
            "TranscriptionEngine.unload() must call release_gpu_memory() "
            "to release PyTorch's CUDA cached blocks (NEW-MEM-001)"
        )

    def test_parakeet_engine_unload_calls_release(self):
        """ParakeetEngine.unload() must invoke release_gpu_memory()."""
        import inspect

        from voice_typer.server.parakeet_engine import ParakeetEngine

        source = inspect.getsource(ParakeetEngine.unload)
        assert "release_gpu_memory()" in source, (
            "ParakeetEngine.unload() must call release_gpu_memory() "
            "to release PyTorch's CUDA cached blocks (NEW-MEM-001)"
        )

    def test_qwen_engine_unload_calls_release(self):
        """QwenEngine.unload() must invoke release_gpu_memory()."""
        import inspect

        from voice_typer.server.qwen_engine import QwenEngine

        source = inspect.getsource(QwenEngine.unload)
        assert "release_gpu_memory()" in source, (
            "QwenEngine.unload() must call release_gpu_memory() to release PyTorch's CUDA cached blocks (NEW-MEM-001)"
        )

    def test_gpu_fallback_paths_call_release(self):
        """The GPU→CPU fallback paths in TranscriptionEngine must call
        release_gpu_memory() before reloading on CPU.
        """
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        # The GPU→CPU fallback for plain transcription lives in
        # _transcribe_with_fallback_unlocked.
        src1 = inspect.getsource(TranscriptionEngine._transcribe_with_fallback_unlocked)
        assert "release_gpu_memory()" in src1, (
            "_transcribe_with_fallback_unlocked GPU fallback path must call release_gpu_memory() (NEW-MEM-001)"
        )
        # The GPU→CPU fallback for timestamped transcription lives in
        # _transcribe_words_with_fallback_unlocked.
        src2 = inspect.getsource(TranscriptionEngine._transcribe_words_with_fallback_unlocked)
        assert "release_gpu_memory()" in src2, (
            "_transcribe_words_with_fallback_unlocked GPU fallback path must call release_gpu_memory() (NEW-MEM-001)"
        )
        # The CUDA-probe early fallback path also calls it.
        src3 = inspect.getsource(TranscriptionEngine._probe_cuda_runtime)
        assert "release_gpu_memory()" in src3, (
            "_probe_cuda_runtime fallback path must call release_gpu_memory() (NEW-MEM-001)"
        )


class TestReleaseGpuMemoryFunctional:
    """Functional test: actually invoke unload() and verify the helper
    is called."""

    def test_parakeet_unload_invokes_release(self, monkeypatch):
        """End-to-end: ParakeetEngine.unload() must trigger
        release_gpu_memory()."""
        from voice_typer.server.parakeet_engine import ParakeetEngine

        # Build a ParakeetEngine without loading the model.
        eng = ParakeetEngine.__new__(ParakeetEngine)
        eng._lock = threading.Lock()
        eng._model = None
        eng._processor = None

        # Mock the helper to track calls.
        with (
            patch("voice_typer.server.parakeet_engine.release_gpu_memory")
            if False
            else patch("voice_typer.server.transcription.release_gpu_memory") as mock_release
        ):
            eng.unload()
            mock_release.assert_called_once()

    def test_qwen_unload_invokes_release(self):
        """End-to-end: QwenEngine.unload() must trigger
        release_gpu_memory()."""
        from voice_typer.server.qwen_engine import QwenEngine

        eng = QwenEngine.__new__(QwenEngine)
        eng._lock = threading.Lock()
        eng._model = None

        with patch("voice_typer.server.transcription.release_gpu_memory") as mock_release:
            eng.unload()
            mock_release.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
