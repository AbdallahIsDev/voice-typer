"""CR-069: split from tests/test_feature_hardening_regressions.py (L651-826).

Source marker: ``tests/test_new_mem_001_gpu_release.py``.

Regression tests for NEW-MEM-001: GPU memory not released on backend switch.

Originally, ``del self._model; gc.collect()`` released Python references
but PyTorch's CUDA caching allocator retained the freed blocks for reuse.
After 2 backend switches (Whisper → Parakeet → Whisper) on RTX 3060/4060
(8–12 GB VRAM), the accumulated cached blocks caused GPU OOMs. The fix
added a shared ``release_gpu_memory()`` helper that called
``torch.cuda.empty_cache()`` after every model unload / fallback path.

After the ONNX Runtime migration (PLAN_ONNX_INTEGRATION.md §5.2), torch
is no longer a project dependency. ONNX Runtime has **no**
``empty_cache()`` API — the CUDA arena is freed automatically when the
``ort.InferenceSession`` is destroyed (i.e. when the engine drops its
session reference and ``gc.collect()`` runs). The helper is therefore a
no-op for ORT, kept for API compatibility with existing callers in
``TranscriptionEngine.unload()``, ``ParakeetEngine.unload()``, and
``QwenEngine.unload()``.

These tests pin the post-ONNX no-op contract: ``release_gpu_memory()``
must NOT invoke any ``torch.cuda.*`` method (because ORT has no
equivalent — the helper is a no-op regardless of torch / CUDA state).
"""

# === Source: tests/test_new_mem_001_gpu_release.py ===

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.asr_utils import release_gpu_memory


class TestReleaseGpuMemoryHelper:
    """The shared helper is a no-op for ONNX Runtime — kept for API
    compatibility with the existing ``unload()`` call sites."""

    def test_no_torch_installed_is_noop(self, monkeypatch):
        """When torch is not installed, the helper must silently no-op."""
        # Simulate torch not being importable.
        monkeypatch.setitem(sys.modules, "torch", None)
        # Must not raise.
        release_gpu_memory()

    def test_does_not_invoke_torch_cuda_api_when_cuda_available(self, monkeypatch):
        """The post-ONNX helper is a no-op regardless of torch / CUDA
        state. Even when a fake ``torch.cuda.is_available()`` would
        return True, the helper MUST NOT call ``is_available`` /
        ``synchronize`` / ``empty_cache`` — ONNX Runtime has no
        ``empty_cache()`` API, and the helper exists only for backward
        compatibility with the existing ``unload()`` call sites (see
        PLAN_ONNX_INTEGRATION.md §5.2).

        This test pins the no-op contract: a revert to the old
        torch-based ``empty_cache()`` implementation would fail it.
        """
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.synchronize = MagicMock()
        fake_torch.cuda.empty_cache = MagicMock()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        release_gpu_memory()

        # The post-ONNX no-op must NOT touch the torch.cuda API at all.
        fake_torch.cuda.is_available.assert_not_called()
        fake_torch.cuda.synchronize.assert_not_called()
        fake_torch.cuda.empty_cache.assert_not_called()

    def test_swallows_runtime_errors(self, monkeypatch):
        """If torch.cuda.synchronize() raises (e.g. CUDA not initialized),
        the helper must not propagate the exception.

        Post-ONNX this is trivially true (the no-op never calls
        ``synchronize``), but the assertion is preserved so a future
        revert to the torch-based implementation would still be
        regression-proof.
        """
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = True
        fake_torch.cuda.synchronize.side_effect = RuntimeError("cuda not initialized")
        fake_torch.cuda.empty_cache = MagicMock()
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        # Must not raise.
        release_gpu_memory()

        # empty_cache was NOT called (no-op path; pre-ONNX this verified
        # the synchronize-raised branch).
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
        """The GPU→CPU fallback paths in TranscriptionEngine must release
        GPU memory before reloading on CPU.

        NEW-MEM-001 refactor: the batch and streaming fallback methods
        (``_transcribe_with_fallback_unlocked`` /
        ``_transcribe_words_with_fallback_unlocked``) now delegate to the
        shared ``_with_gpu_fallback`` helper, which sets
        ``_pending_gc_collect = True``; the actual
        ``release_gpu_memory()`` runs in ``_run_deferred_gc`` AFTER the
        model reference is dropped (RACE-023 — calling it earlier was a
        no-op because the ctranslate2 model still held the CUDA context).
        """
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        # The batch and streaming GPU→CPU fallbacks both route through
        # the unified helper (no inline release_gpu_memory call anymore).
        src1 = inspect.getsource(TranscriptionEngine._transcribe_with_fallback_unlocked)
        assert "_with_gpu_fallback(" in src1, (
            "_transcribe_with_fallback_unlocked must delegate to _with_gpu_fallback (NEW-MEM-001)"
        )
        src2 = inspect.getsource(TranscriptionEngine._transcribe_words_with_fallback_unlocked)
        assert "_with_gpu_fallback(" in src2, (
            "_transcribe_words_with_fallback_unlocked must delegate to _with_gpu_fallback (NEW-MEM-001)"
        )
        # The unified fallback helper arms the deferred release.
        src_fb = inspect.getsource(TranscriptionEngine._with_gpu_fallback)
        assert "_pending_gc_collect = True" in src_fb, (
            "_with_gpu_fallback must set _pending_gc_collect so the deferred "
            "gc path releases GPU memory (NEW-MEM-001 / RACE-023)"
        )
        # The deferred gc actually performs release_gpu_memory().
        src_gc = inspect.getsource(TranscriptionEngine._run_deferred_gc)
        assert "release_gpu_memory()" in src_gc, (
            "_run_deferred_gc must call release_gpu_memory() after the model is dropped (NEW-MEM-001 / RACE-023)"
        )
        # The CUDA-probe early fallback path arms the RACE-023 deferred
        # release (HU-25): it must set ``_pending_gc_collect`` so the
        # next caller outside the lock runs release_gpu_memory() — the
        # direct in-lock call was removed as a no-op for VRAM release.
        src3 = inspect.getsource(TranscriptionEngine._probe_cuda_runtime)
        assert "_pending_gc_collect = True" in src3, (
            "_probe_cuda_runtime fallback path must arm the deferred GPU "
            "release (_pending_gc_collect = True) so release_gpu_memory() "
            "runs outside the lock (HU-25 / RACE-023 / NEW-MEM-001)"
        )


class TestReleaseGpuMemoryFunctional:
    """Functional test: actually invoke unload() and verify the helper
    is called."""

    def test_parakeet_unload_invokes_release(self):
        """End-to-end: ParakeetEngine.unload() must trigger
        release_gpu_memory().

        WR-4: previously this test patched
        `voice_typer.server.transcription.release_gpu_memory`, but
        `parakeet_engine.unload()` does a LOCAL import
        (`from voice_typer.server.asr_utils import release_gpu_memory`
        at parakeet_engine.py:1027), so the patch on `transcription.X`
        never intercepted the call and the mock was never invoked.
        Patching `voice_typer.server.asr_utils.release_gpu_memory` (the
        canonical source) intercepts the local import correctly.
        """
        from voice_typer.server.parakeet_engine import ParakeetEngine

        # Build a ParakeetEngine without loading the model.
        eng = ParakeetEngine.__new__(ParakeetEngine)
        eng._lock = threading.Lock()
        # unload() acquires ``_inference_cond`` (a Condition
        # wrapping ``_lock``) and waits on ``_active_inference == 0``
        # before nulling ``self._model``. The constructor sets these
        # up (parakeet_engine.py:205-212), but ``__new__`` bypasses
        # ``__init__`` — set them manually so ``unload()`` doesn't
        # AttributeError on the missing Condition.
        eng._inference_cond = threading.Condition(eng._lock)
        eng._active_inference = 0
        eng._model = None
        eng._processor = None

        # Mock the helper at its canonical source so the local import
        # inside `unload()` resolves to the mock.
        with patch("voice_typer.server.asr_utils.release_gpu_memory") as mock_release:
            eng.unload()
            mock_release.assert_called_once()

    def test_qwen_unload_invokes_release(self):
        """End-to-end: QwenEngine.unload() must trigger
        release_gpu_memory().

        Note: qwen_engine.py:661 does `from voice_typer.server.transcription
        import release_gpu_memory` at module load (not a local import inside
        `unload()`), so patching `voice_typer.server.transcription.
        release_gpu_memory` correctly intercepts the call here. The asymmetry
        with parakeet_engine (which uses a local import from `asr_utils`) is
        documented at parakeet_engine.py:1022-1027.
        """
        from voice_typer.server.qwen_engine import QwenEngine

        eng = QwenEngine.__new__(QwenEngine)
        eng._lock = threading.Lock()
        eng._model = None

        with patch("voice_typer.server.transcription.release_gpu_memory") as mock_release:
            eng.unload()
            mock_release.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
