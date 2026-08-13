"""Tests for the shared CUDA/OOM error classifiers in :mod:`voice_typer.server.asr_utils`.

Phase 1c (PLAN_ONNX_INTEGRATION.md §5.1): the 5-layer CUDA error
classifier and the separate OOM classifier were extracted from
``TranscriptionEngine._is_gpu_runtime_error`` and
``parakeet_engine.py:955`` into the shared ``asr_utils`` module so
both the Whisper (transcription.py) and Parakeet (parakeet_engine.py)
GPU→CPU fallback paths use the same logic.

These tests pin the contract:

- ``is_cuda_error`` is a 4-layer classifier (ORT exception → RuntimeError
  attribute → keyword match → DLL-load keyword match) that does NOT
  match ``"out of memory"`` alone (CPU RAM exhaustion is NOT a CUDA
  error).
- ``is_oom_error`` is a separate message-substring check
  (``"out of memory"`` / ``"oom"``) used by the OOM-specific branch of
  the GPU→CPU fallback.
- Both classifiers are conservative (prefer false-negatives over
  false-positives) because a false-positive routes a non-GPU error
  into the GPU→CPU fallback path (5-15s stall on session recreation).

The tests do NOT import torch — the classifiers are torch-free by
design (Phase 1c). A fake ORT exception class is used to exercise
layer 1 without requiring ``onnxruntime`` to be installed.
"""

from __future__ import annotations

from voice_typer.server.asr_utils import is_cuda_error, is_oom_error

# ─── is_cuda_error ───────────────────────────────────────────────────────


class TestIsCudaError:
    """``is_cuda_error`` 4-layer classifier contract."""

    # ── Layer 1: ORT RuntimeException ───────────────────────────────

    def test_ort_runtime_exception_with_cuda_message_returns_true(self, monkeypatch):
        """An ``onnxruntime.RuntimeException`` whose message contains
        ``"cuda"`` must match via layer 1."""
        import sys
        from unittest.mock import MagicMock

        # Build a fake onnxruntime module with a real RuntimeException
        # class so ``isinstance(exc, ort.RuntimeException)`` works.
        class _FakeOrtRuntimeError(RuntimeError):
            pass

        mock_ort = MagicMock(name="mock_onnxruntime")
        # onnxruntime 1.28+ does not export the exception from the
        # public namespace — it lives under the pybind11 state module.
        mock_ort.capi.onnxruntime_pybind11_state.RuntimeException = _FakeOrtRuntimeError
        # Also stub get_device so import-side-effect doesn't fail.
        mock_ort.get_device = lambda: "cpu"
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)

        exc = _FakeOrtRuntimeError("CUDA kernel failed: out of memory")
        assert is_cuda_error(exc) is True

    def test_ort_runtime_exception_with_gpu_message_returns_true(self, monkeypatch):
        """An ``onnxruntime.RuntimeException`` whose message contains
        ``"gpu"`` must match via layer 1."""
        import sys
        from unittest.mock import MagicMock

        class _FakeOrtRuntimeError(RuntimeError):
            pass

        mock_ort = MagicMock(name="mock_onnxruntime")
        # onnxruntime 1.28+ does not export the exception from the
        # public namespace — it lives under the pybind11 state module.
        mock_ort.capi.onnxruntime_pybind11_state.RuntimeException = _FakeOrtRuntimeError
        mock_ort.get_device = lambda: "cpu"
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)

        exc = _FakeOrtRuntimeError("GPU device not available")
        assert is_cuda_error(exc) is True

    def test_ort_runtime_exception_without_cuda_keyword_returns_false(self, monkeypatch):
        """An ``onnxruntime.RuntimeException`` whose message contains
        neither ``"cuda"`` nor ``"gpu"`` must NOT match via layer 1
        (but may still match via later layers if the message has
        ``"cublas"`` etc.)."""
        import sys
        from unittest.mock import MagicMock

        class _FakeOrtRuntimeError(RuntimeError):
            pass

        mock_ort = MagicMock(name="mock_onnxruntime")
        # onnxruntime 1.28+ does not export the exception from the
        # public namespace — it lives under the pybind11 state module.
        mock_ort.capi.onnxruntime_pybind11_state.RuntimeException = _FakeOrtRuntimeError
        mock_ort.get_device = lambda: "cpu"
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)

        # Plain message — no cuda/gpu/cublas/cudnn/dll keywords.
        exc = _FakeOrtRuntimeError("input shape mismatch")
        assert is_cuda_error(exc) is False

    def test_ort_not_installed_falls_through_to_other_layers(self, monkeypatch):
        """When ``onnxruntime`` is not installed, layer 1 is skipped
        and the classifier falls through to the keyword layers."""
        import sys

        # Ensure onnxruntime is not in sys.modules.
        monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)

        # A RuntimeError with "cuda" in the message still matches via layer 3.
        assert is_cuda_error(RuntimeError("cuda error")) is True

    # ── Layer 2: RuntimeError + attribute check ─────────────────────

    def test_runtime_error_with_cuda_error_attr_returns_true(self):
        """A ``RuntimeError`` carrying a truthy ``.cuda_error``
        attribute must match via layer 2."""
        exc = RuntimeError("oom")
        exc.cuda_error = "oom"  # type: ignore[attr-defined]
        assert is_cuda_error(exc) is True

    def test_runtime_error_with_is_cuda_error_flag_returns_true(self):
        """A ``RuntimeError`` carrying a truthy ``.is_cuda_error``
        attribute must match via layer 2."""
        exc = RuntimeError("oom")
        exc.is_cuda_error = True  # type: ignore[attr-defined]
        assert is_cuda_error(exc) is True

    def test_non_runtime_error_with_cuda_error_attr_returns_false(self):
        """The attribute check (layer 2) only fires for
        ``RuntimeError`` subclasses — a ``ValueError`` with
        ``.cuda_error`` set must NOT match via layer 2 (it may still
        match via the keyword layers if the message has the right
        substrings)."""
        exc = ValueError("oom")
        exc.cuda_error = "oom"  # type: ignore[attr-defined]
        # ValueError is NOT a RuntimeError, so layer 2 does not fire.
        # The message "oom" does not contain cuda/cublas/cudnn/dll keywords.
        assert is_cuda_error(exc) is False

    # ── Layer 3: keyword match (cuda / cublas / cudnn) ──────────────

    def test_cuda_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("CUDA error: device not found")) is True

    def test_cublas_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cublas64_12.dll load failed")) is True

    def test_cudnn_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cudnn version mismatch")) is True

    def test_out_of_memory_alone_does_not_match(self):
        """``"out of memory"`` alone must NOT match ``is_cuda_error``
        — it matches CPU RAM exhaustion too. OOM is handled separately
        by :func:`is_oom_error`."""
        assert is_cuda_error(RuntimeError("out of memory")) is False

    def test_oom_alone_does_not_match(self):
        """``"oom"`` alone must NOT match ``is_cuda_error``."""
        assert is_cuda_error(RuntimeError("oom")) is False

    # ── Layer 4: DLL-load keyword match (Windows) ───────────────────

    def test_dll_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("DLL load failed")) is True

    def test_not_found_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cublas not found")) is True

    def test_cannot_be_loaded_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cuda library cannot be loaded")) is True

    def test_load_library_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("load library failed for cudnn")) is True

    # ── Negative cases ──────────────────────────────────────────────

    def test_plain_runtime_error_returns_false(self):
        """A plain ``RuntimeError("model not loaded")`` must return
        False — it's not a CUDA error and routing it into the GPU→CPU
        fallback path would mask the real cause."""
        assert is_cuda_error(RuntimeError("model not loaded")) is False

    def test_value_error_returns_false(self):
        """A ``ValueError`` is never a CUDA error."""
        assert is_cuda_error(ValueError("invalid argument")) is False

    def test_key_error_returns_false(self):
        """A ``KeyError`` is never a CUDA error."""
        assert is_cuda_error(KeyError("missing key")) is False

    def test_attribute_error_returns_false(self):
        """An ``AttributeError`` is never a CUDA error."""
        assert is_cuda_error(AttributeError("no attribute")) is False


# ─── is_oom_error ───────────────────────────────────────────────────────


class TestIsOomError:
    """``is_oom_error`` message-substring classifier contract."""

    def test_out_of_memory_substring_returns_true(self):
        assert is_oom_error(RuntimeError("CUDA out of memory")) is True

    def test_out_of_memory_case_insensitive_returns_true(self):
        assert is_oom_error(RuntimeError("OUT OF MEMORY")) is True

    def test_oom_substring_returns_true(self):
        assert is_oom_error(RuntimeError("OOM during inference")) is True

    def test_oom_case_insensitive_returns_true(self):
        assert is_oom_error(RuntimeError("oom")) is True

    def test_cuda_oom_returns_true(self):
        assert is_oom_error(RuntimeError("CUDA OOM")) is True

    def test_plain_runtime_error_returns_false(self):
        """A plain ``RuntimeError("model not loaded")`` is NOT an OOM error."""
        assert is_oom_error(RuntimeError("model not loaded")) is False

    def test_cublas_error_returns_false(self):
        """A cuBLAS load failure is NOT an OOM error — it's a DLL-load
        failure (handled by :func:`is_cuda_error` layer 4)."""
        assert is_oom_error(RuntimeError("cublas load library failed")) is False

    def test_value_error_returns_false(self):
        assert is_oom_error(ValueError("invalid argument")) is False


# ─── is_cuda_error + is_oom_error combination ───────────────────────────


class TestCudaOomCombination:
    """The two classifiers are separate by design (PLAN_ONNX_INTEGRATION.md §5.1).

    ``"out of memory"`` alone matches ``is_oom_error`` but NOT
    ``is_cuda_error`` (CPU RAM exhaustion is not a CUDA error).
    ``"cuda out of memory"`` matches BOTH — a true CUDA OOM.
    ``"cublas load failed"`` matches ``is_cuda_error`` but NOT
    ``is_oom_error`` (DLL load failure, not OOM).
    """

    def test_cpu_ram_exhaustion_is_oom_but_not_cuda(self):
        """``"out of memory"`` alone → OOM=True, CUDA=False."""
        exc = RuntimeError("out of memory")
        assert is_oom_error(exc) is True
        assert is_cuda_error(exc) is False

    def test_cuda_oom_is_both(self):
        """``"cuda out of memory"`` → OOM=True, CUDA=True."""
        exc = RuntimeError("cuda out of memory")
        assert is_oom_error(exc) is True
        assert is_cuda_error(exc) is True

    def test_cublas_load_failure_is_cuda_but_not_oom(self):
        """``"cublas load library failed"`` → OOM=False, CUDA=True."""
        exc = RuntimeError("cublas load library failed")
        assert is_oom_error(exc) is False
        assert is_cuda_error(exc) is True

    def test_plain_runtime_error_is_neither(self):
        """``"model not loaded"`` → OOM=False, CUDA=False."""
        exc = RuntimeError("model not loaded")
        assert is_oom_error(exc) is False
        assert is_cuda_error(exc) is False
