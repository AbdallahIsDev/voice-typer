"""
Tests for the shared CUDA/OOM error classifiers in :mod:`voice_typer.server.asr_utils`.
The tests do NOT import torch, the classifiers are torch-free by
"""

from __future__ import annotations

from voice_typer.server.asr_utils import is_cuda_error, is_oom_error


class TestIsCudaError:
    """``is_cuda_error`` 4-layer classifier contract."""

    def test_ort_runtime_exception_with_cuda_message_returns_true(self, monkeypatch):
        """An ``onnxruntime.RuntimeException`` whose message contains"""
        import sys
        from unittest.mock import MagicMock

        # Build a fake onnxruntime module with a real RuntimeException
        class _FakeOrtRuntimeError(RuntimeError):
            pass

        mock_ort = MagicMock(name="mock_onnxruntime")
        mock_ort.capi.onnxruntime_pybind11_state.RuntimeException = _FakeOrtRuntimeError
        # Also stub get_device so import-side-effect doesn't fail.
        mock_ort.get_device = lambda: "cpu"
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)

        exc = _FakeOrtRuntimeError("CUDA kernel failed: out of memory")
        assert is_cuda_error(exc) is True

    def test_ort_runtime_exception_with_gpu_message_returns_true(self, monkeypatch):
        """An ``onnxruntime.RuntimeException`` whose message contains"""
        import sys
        from unittest.mock import MagicMock

        class _FakeOrtRuntimeError(RuntimeError):
            pass

        mock_ort = MagicMock(name="mock_onnxruntime")
        mock_ort.capi.onnxruntime_pybind11_state.RuntimeException = _FakeOrtRuntimeError
        mock_ort.get_device = lambda: "cpu"
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)

        exc = _FakeOrtRuntimeError("GPU device not available")
        assert is_cuda_error(exc) is True

    def test_ort_runtime_exception_without_cuda_keyword_returns_false(self, monkeypatch):
        """An ``onnxruntime.RuntimeException`` whose message contains"""
        import sys
        from unittest.mock import MagicMock

        class _FakeOrtRuntimeError(RuntimeError):
            pass

        mock_ort = MagicMock(name="mock_onnxruntime")
        mock_ort.capi.onnxruntime_pybind11_state.RuntimeException = _FakeOrtRuntimeError
        mock_ort.get_device = lambda: "cpu"
        monkeypatch.setitem(sys.modules, "onnxruntime", mock_ort)

        # Plain message, no cuda/gpu/cublas/cudnn/dll keywords.
        exc = _FakeOrtRuntimeError("input shape mismatch")
        assert is_cuda_error(exc) is False

    def test_ort_not_installed_falls_through_to_other_layers(self, monkeypatch):
        """When ``onnxruntime`` is not installed, layer 1 is skipped"""
        import sys

        # Ensure onnxruntime is not in sys.modules.
        monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)

        # A RuntimeError with "cuda" in the message still matches via layer 3.
        assert is_cuda_error(RuntimeError("cuda error")) is True

    def test_runtime_error_with_cuda_error_attr_returns_true(self):
        """A ``RuntimeError`` carrying a truthy ``.cuda_error``"""
        exc = RuntimeError("oom")
        exc.cuda_error = "oom"  # type: ignore[attr-defined]
        assert is_cuda_error(exc) is True

    def test_runtime_error_with_is_cuda_error_flag_returns_true(self):
        """A ``RuntimeError`` carrying a truthy ``.is_cuda_error``"""
        exc = RuntimeError("oom")
        exc.is_cuda_error = True  # type: ignore[attr-defined]
        assert is_cuda_error(exc) is True

    def test_non_runtime_error_with_cuda_error_attr_returns_false(self):
        """``.cuda_error`` set must NOT match via layer 2 (it may still"""
        exc = ValueError("oom")
        exc.cuda_error = "oom"  # type: ignore[attr-defined]
        # ValueError is NOT a RuntimeError, so layer 2 does not fire.
        assert is_cuda_error(exc) is False

    def test_cuda_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("CUDA error: device not found")) is True

    def test_cublas_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cublas64_12.dll load failed")) is True

    def test_cudnn_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cudnn version mismatch")) is True

    def test_out_of_memory_alone_does_not_match(self):
        """``\"out of memory\"`` alone must NOT match ``is_cuda_error``"""
        assert is_cuda_error(RuntimeError("out of memory")) is False

    def test_oom_alone_does_not_match(self):
        """``\"oom\"`` alone must NOT match ``is_cuda_error``."""
        assert is_cuda_error(RuntimeError("oom")) is False

    def test_dll_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("DLL load failed")) is True

    def test_not_found_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cublas not found")) is True

    def test_cannot_be_loaded_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("cuda library cannot be loaded")) is True

    def test_load_library_keyword_in_message_returns_true(self):
        assert is_cuda_error(RuntimeError("load library failed for cudnn")) is True

    def test_plain_runtime_error_returns_false(self):
        """A plain ``RuntimeError(\"model not loaded\")`` must return"""
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
        """A plain ``RuntimeError(\"model not loaded\")`` is NOT an OOM error."""
        assert is_oom_error(RuntimeError("model not loaded")) is False

    def test_cublas_error_returns_false(self):
        """A cuBLAS load failure is NOT an OOM error, it's a DLL-load"""
        assert is_oom_error(RuntimeError("cublas load library failed")) is False

    def test_value_error_returns_false(self):
        assert is_oom_error(ValueError("invalid argument")) is False


class TestCudaOomCombination:
    """The two classifiers are separate by design (PLAN_ONNX_INTEGRATION.md §5.1)."""

    def test_cpu_ram_exhaustion_is_oom_but_not_cuda(self):
        """``\"out of memory\"`` alone → OOM=True, CUDA=False."""
        exc = RuntimeError("out of memory")
        assert is_oom_error(exc) is True
        assert is_cuda_error(exc) is False

    def test_cuda_oom_is_both(self):
        """``\"cuda out of memory\"`` → OOM=True, CUDA=True."""
        exc = RuntimeError("cuda out of memory")
        assert is_oom_error(exc) is True
        assert is_cuda_error(exc) is True

    def test_cublas_load_failure_is_cuda_but_not_oom(self):
        """``\"cublas load library failed\"`` → OOM=False, CUDA=True."""
        exc = RuntimeError("cublas load library failed")
        assert is_oom_error(exc) is False
        assert is_cuda_error(exc) is True

    def test_plain_runtime_error_is_neither(self):
        """``\"model not loaded\"`` → OOM=False, CUDA=False."""
        exc = RuntimeError("model not loaded")
        assert is_oom_error(exc) is False
        assert is_cuda_error(exc) is False
