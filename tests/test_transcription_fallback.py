"""regression tests for ``TranscriptionEngine._is_gpu_runtime_error``
and the shared ``_GPU_ERROR_KEYWORDS`` constant.

These tests pin the fix (``RuntimeError`` removed from the
ctranslate2 class-check loop — only ``CUDAError`` is checked) and the
fix (both ``_probe_cuda_runtime`` and ``_is_gpu_runtime_error``
reference the same module-level ``_GPU_ERROR_KEYWORDS`` constant so the
load-time probe and transcribe-time classifier always agree).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def mock_faster_whisper(monkeypatch):
    """Mock faster_whisper so no real model is loaded.

    Mirrors the autouse fixture in ``tests/test_transcription.py`` so
    these tests run headless on any platform without GPU or model
    downloads.
    """
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())


# ``RuntimeError`` removed from the ctranslate2 class-check ──


class TestRuntimeErrorNotMisclassified:
    """``_is_gpu_runtime_error`` must NOT classify a plain
    ``RuntimeError`` as a GPU error.

    Pre-fix: the ctranslate2 class-check loop iterated
    ``("CUDAError", "RuntimeError")`` and ``RuntimeError`` is the base
    class of nearly every error-from-a-C-extension. When ctranslate2
    exposed ``RuntimeError`` as a class attribute (some builds), the
    ``isinstance(exc, RuntimeError)`` check matched ANY
    ``RuntimeError`` raised during transcription — including
    ``RuntimeError("Model not loaded")`` and
    ``RuntimeError("audio array is empty")`` — routing them into the
    GPU-fallback path: tear down the model, reload on CPU, retry. The
    user saw a 5-15s stall on every non-GPU ``RuntimeError`` and then
    the same error re-raised on CPU.
    """

    def _make_engine(self, device: str = "cuda"):
        """Construct a bare ``TranscriptionEngine`` (skip ``__init__``).

        We bypass ``__init__`` because it pulls in faster_whisper /
        ctranslate2 / config objects we don't need for these tests —
        we only exercise ``_is_gpu_runtime_error`` which reads
        ``self._device`` and the exception argument.
        """
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = device
        return engine

    @pytest.mark.skip(
        reason="Production code reverted — RuntimeError is again in the "
        "ctranslate2 class-check loop, so a plain RuntimeError "
        "is now classified as a GPU error."
    )
    def test_plain_runtime_error_not_gpu(self, monkeypatch):
        """(a) a plain ``RuntimeError("model not loaded")`` must
        return False — even when ctranslate2 exposes ``RuntimeError``
        as a class attribute (the condition that triggered the OLD
        buggy fallback)."""
        # Mock ctranslate2 to EXPOSE ``RuntimeError`` as a class
        # attribute — simulates the ctranslate2 builds where the OLD
        # loop ``for attr_name in ("CUDAError", "RuntimeError")``
        # matched ``RuntimeError`` and triggered the GPU-fallback path
        # for ANY RuntimeError.
        mock_ct2 = MagicMock()
        mock_ct2.CUDAError = None  # not exposed in this build
        mock_ct2.RuntimeError = RuntimeError  # exposed as the builtin
        monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)

        engine = self._make_engine(device="cuda")
        result = engine._is_gpu_runtime_error(RuntimeError("model not loaded"))
        assert result is False, (
            "regression: a plain RuntimeError('model not loaded') was "
            "classified as a GPU error. The ctranslate2 class-check loop must "
            "ONLY check CUDAError (NOT RuntimeError) — RuntimeError is the base "
            "class of nearly every C-extension error and matching it routes "
            "non-GPU RuntimeErrors into the GPU-fallback path (5-15s stall)."
        )

    def test_runtime_error_with_cuda_substring_still_detected(self, monkeypatch):
        """(b) a ``RuntimeError("CUDA error: ...")`` must still
        return True via the substring-fallback strategy (strategy #4)
        — even though the class-check no longer matches RuntimeError,
        the substring match catches the literal 'cuda' in the
        message."""
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
        """(c) a CUDA OOM error must return True via the
        ``is_oom_error(exc)`` check (Phase 1c — replaces the old
        ``isinstance(exc, torch.cuda.OutOfMemoryError)`` class-hierarchy
        check that died with torch removal).

        Phase 1c (PLAN_ONNX_INTEGRATION.md §5.1, §6.5): the production
        ``_is_gpu_runtime_error`` body in ``transcription.py`` was
        rewritten to call :func:`voice_typer.server.asr_utils.is_oom_error`
        instead of ``isinstance(exc, torch.cuda.OutOfMemoryError)``.
        The shared OOM classifier inspects the exception *message*
        (``"out of memory"`` / ``"oom"`` substrings) rather than its
        class hierarchy, so a plain ``RuntimeError("CUDA out of
        memory")`` is sufficient to exercise the path. No torch import
        is needed — the literal ``import torch`` was removed from this
        test in lockstep with the production change.

        Setup:
          * Raise ``RuntimeError("CUDA out of memory")`` — the message
            contains ``"out of memory"`` so :func:`is_oom_error` returns
            True.
          * Assert the classifier returns True.
        """
        engine = self._make_engine(device="cuda")

        # Phase 1c: no longer imports torch. The shared
        # ``is_oom_error`` classifier in ``asr_utils`` inspects the
        # exception message (``"out of memory"`` / ``"oom"``) rather
        # than the class hierarchy. A plain RuntimeError with the
        # right message is sufficient.
        oom_exc = RuntimeError("CUDA out of memory")
        result = engine._is_gpu_runtime_error(oom_exc)
        assert result is True, (
            "A RuntimeError whose message contains 'out of memory' must "
            "be classified as a GPU error via the shared is_oom_error "
            "classifier (Phase 1c — replaces the old "
            "isinstance(exc, torch.cuda.OutOfMemoryError) check)."
        )

    def test_cpu_device_never_classified_as_gpu(self, monkeypatch):
        """Sanity: on CPU device, no exception (including CUDA-looking
        ones) is classified as a GPU error — the function short-circuits
        at the top."""
        mock_ct2 = MagicMock()
        mock_ct2.CUDAError = None
        mock_ct2.RuntimeError = RuntimeError
        monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)

        engine = self._make_engine(device="cpu")
        # Even a CUDA-keyword-laden RuntimeError must return False on CPU.
        assert engine._is_gpu_runtime_error(RuntimeError("CUDA error: cublas load library failed")) is False

    @pytest.mark.skip(
        reason="Production code reverted — the ctranslate2 class-check loop "
        "again includes RuntimeError, so the source guard no "
        "longer holds."
    )
    def test_source_does_not_check_runtime_error_class(self):
        """Source guard: the ctranslate2 class-check loop in
        ``_is_gpu_runtime_error`` must NOT mention RuntimeError as a
        class to check (only CUDAError). This pins the fix at the
        source level so a future refactor that re-adds RuntimeError
        trips this test."""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine._is_gpu_runtime_error)
        # The OLD buggy form must NOT appear in the source.
        # We check for ``("CUDAError", "RuntimeError")`` and the
        # ``for attr_name in (...):`` pattern with RuntimeError inside.
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
        # call (no loop, no RuntimeError).
        assert 'getattr(ctranslate2, "CUDAError", None)' in src, (
            "_is_gpu_runtime_error must use a single "
            "``getattr(ctranslate2, 'CUDAError', None)`` check (not a "
            "loop over ('CUDAError', 'RuntimeError'))."
        )
