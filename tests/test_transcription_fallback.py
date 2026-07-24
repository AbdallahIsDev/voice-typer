"""AC-2 / AC-3 regression tests for ``TranscriptionEngine._is_gpu_runtime_error``
and the shared ``_GPU_ERROR_KEYWORDS`` constant.

These tests pin the AC-2 fix (``RuntimeError`` removed from the
ctranslate2 class-check loop — only ``CUDAError`` is checked) and the
AC-3 fix (both ``_probe_cuda_runtime`` and ``_is_gpu_runtime_error``
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


# ── AC-2: ``RuntimeError`` removed from the ctranslate2 class-check ──


class TestAC2RuntimeErrorNotMisclassified:
    """AC-2: ``_is_gpu_runtime_error`` must NOT classify a plain
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

    def test_plain_runtime_error_not_gpu(self, monkeypatch):
        """(a) AC-2: a plain ``RuntimeError("model not loaded")`` must
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
            "AC-2 regression: a plain RuntimeError('model not loaded') was "
            "classified as a GPU error. The ctranslate2 class-check loop must "
            "ONLY check CUDAError (NOT RuntimeError) — RuntimeError is the base "
            "class of nearly every C-extension error and matching it routes "
            "non-GPU RuntimeErrors into the GPU-fallback path (5-15s stall)."
        )

    def test_runtime_error_with_cuda_substring_still_detected(self, monkeypatch):
        """(b) AC-2: a ``RuntimeError("CUDA error: ...")`` must still
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
            "AC-2: a RuntimeError with 'CUDA' in the message must still be "
            "classified as a GPU error via the substring fallback."
        )

    def test_torch_cuda_oom_classified_as_gpu(self, monkeypatch):
        """(c) AC-2: a ``torch.cuda.OutOfMemoryError`` instance must
        return True via the strategy #1 isinstance check (class
        hierarchy). The conftest autouse torch mock installs a real
        ``_FakeOutOfMemoryError`` class at ``torch.cuda.OutOfMemoryError``
        so the production ``isinstance(exc, torch.cuda.OutOfMemoryError)``
        check returns True for instances of that class."""
        engine = self._make_engine(device="cuda")

        # Use the (mocked) torch.cuda.OutOfMemoryError installed by
        # the autouse conftest fixture. We import torch lazily so the
        # autouse mock has already been installed before we resolve the
        # class.
        import torch

        oom_cls = torch.cuda.OutOfMemoryError
        # Sanity: the mock installs a real class (not a MagicMock) so
        # ``isinstance`` works. If this assertion fails, the conftest
        # torch mock has changed and this test needs updating.
        assert isinstance(oom_cls, type), (
            "Test setup: torch.cuda.OutOfMemoryError must be a real class "
            "(not a MagicMock) so isinstance works. Check the autouse mock "
            "in tests/conftest.py."
        )

        oom_exc = oom_cls("CUDA out of memory")
        result = engine._is_gpu_runtime_error(oom_exc)
        assert result is True, (
            "AC-2: torch.cuda.OutOfMemoryError must be classified as a GPU "
            "error via the strategy #1 isinstance check (class hierarchy)."
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
        assert engine._is_gpu_runtime_error(
            RuntimeError("CUDA error: cublas load library failed")
        ) is False

    def test_source_does_not_check_runtime_error_class(self):
        """Source guard: the ctranslate2 class-check loop in
        ``_is_gpu_runtime_error`` must NOT mention RuntimeError as a
        class to check (only CUDAError). This pins the AC-2 fix at the
        source level so a future refactor that re-adds RuntimeError
        trips this test."""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine._is_gpu_runtime_error)
        # The OLD buggy form must NOT appear in the source.
        # We check for ``("CUDAError", "RuntimeError")`` and the
        # ``for attr_name in (...):`` pattern with RuntimeError inside.
        assert "RuntimeError" not in src.replace(
            "# ``RuntimeError`` is the base class", ""
        ).replace(
            "# ``RuntimeError`` matched", ""
        ) or "for attr_name in" not in src, (
            "AC-2 regression: _is_gpu_runtime_error source still references "
            "RuntimeError as a class to check. The ctranslate2 loop must "
            "ONLY check CUDAError."
        )
        # The NEW form: a single ``getattr(ctranslate2, "CUDAError", None)``
        # call (no loop, no RuntimeError).
        assert 'getattr(ctranslate2, "CUDAError", None)' in src, (
            "AC-2: _is_gpu_runtime_error must use a single "
            "``getattr(ctranslate2, 'CUDAError', None)`` check (not a "
            "loop over ('CUDAError', 'RuntimeError'))."
        )


# ── AC-3: unified GPU error keyword list ────────────────────────────


class TestAC3UnifiedGpuErrorKeywords:
    """AC-3: both ``_probe_cuda_runtime`` (load-time probe) and
    ``_is_gpu_runtime_error`` (transcribe-time classifier) must
    reference the SAME module-level ``_GPU_ERROR_KEYWORDS`` constant.

    Pre-fix: the two sites maintained independent keyword lists that
    had drifted apart — the probe had ``"dll"``, ``"load library"``,
    ``"cannot be loaded"`` but no ``"gpu"``; the classifier had
    ``"gpu"`` but no ``"dll"`` / ``"load library"``. The same
    exception (e.g. ``"cuBLAS DLL not found"``) was classified
    differently at load time vs transcribe time.
    """

    def test_module_constant_exists(self):
        """The shared ``_GPU_ERROR_KEYWORDS`` constant must exist at
        module scope and be a tuple of lowercased strings."""
        from voice_typer.server.transcription import _GPU_ERROR_KEYWORDS

        assert isinstance(_GPU_ERROR_KEYWORDS, tuple)
        assert len(_GPU_ERROR_KEYWORDS) > 0
        for kw in _GPU_ERROR_KEYWORDS:
            assert isinstance(kw, str)
            # All keywords must be lowercase so the substring check
            # against ``error_str.lower()`` matches correctly.
            assert kw == kw.lower(), (
                f"AC-3: keyword {kw!r} must be lowercase (the substring "
                f"check uses ``error_str.lower()``)."
            )

    def test_union_of_legacy_keywords(self):
        """The unified list must be a SUPERSET (in matched-strings
        terms) of both legacy lists: probe had ``{cublas, cuda, cudnn,
        dll, not found, cannot be loaded, load library}`` and
        classifier had ``{cublas, cuda, cudnn, gpu, not found or
        cannot be loaded}``.

        Note: the legacy classifier keyword ``"not found or cannot
        be loaded"`` is a LONGER substring than the unified
        ``"not found"`` + ``"cannot be loaded"`` — every string
        that matched the long form also matches one of the short
        forms, so dropping the long form is a strict widening (no
        regression). We verify the legacy probe keywords are all
        present verbatim, plus the legacy classifier's other
        keywords (cublas/cuda/cudnn/gpu)."""
        from voice_typer.server.transcription import _GPU_ERROR_KEYWORDS

        legacy_probe = {
            "cublas", "cuda", "cudnn", "dll",
            "not found", "cannot be loaded", "load library",
        }
        # Legacy classifier keywords EXCLUDING the longer "not found or
        # cannot be loaded" (subsumed by "not found" in the unified list).
        legacy_classifier = {"cublas", "cuda", "cudnn", "gpu"}
        union = legacy_probe | legacy_classifier
        missing = union - set(_GPU_ERROR_KEYWORDS)
        assert not missing, (
            f"AC-3: _GPU_ERROR_KEYWORDS is missing keywords that were in "
            f"one of the legacy lists: {sorted(missing)}. The unified "
            f"list must be a superset of both legacy lists so no "
            f"previously-detected keyword regresses."
        )
        # Verify the longer legacy-classifier keyword "not found or
        # cannot be loaded" is subsumed by the unified list — any string
        # matching the long form must also match at least one unified
        # keyword. We pick a representative string.
        long_form = "not found or cannot be loaded"
        assert any(kw in long_form for kw in _GPU_ERROR_KEYWORDS), (
            "AC-3: the legacy classifier keyword 'not found or cannot be "
            "loaded' must still be detected by at least one keyword in the "
            "unified _GPU_ERROR_KEYWORDS list (e.g. 'not found' or 'cannot "
            "be loaded'). Otherwise strings that the OLD classifier caught "
            "would silently slip through the NEW unified classifier."
        )

    def test_classifier_uses_shared_constant(self):
        """Source guard: ``_is_gpu_runtime_error`` must reference
        ``_GPU_ERROR_KEYWORDS`` (not an inline list)."""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine._is_gpu_runtime_error)
        assert "_GPU_ERROR_KEYWORDS" in src, (
            "AC-3: _is_gpu_runtime_error must reference the shared "
            "_GPU_ERROR_KEYWORDS constant instead of an inline list."
        )
        # The OLD inline form must NOT appear.
        assert '"not found or cannot be loaded"' not in src, (
            "AC-3 regression: _is_gpu_runtime_error still uses the OLD "
            "inline keyword list with 'not found or cannot be loaded' — "
            "must reference _GPU_ERROR_KEYWORDS instead."
        )

    def test_probe_uses_shared_constant(self):
        """Source guard: ``_probe_cuda_runtime`` must reference
        ``_GPU_ERROR_KEYWORDS`` (not an inline list)."""
        import inspect

        from voice_typer.server.transcription import TranscriptionEngine

        src = inspect.getsource(TranscriptionEngine._probe_cuda_runtime)
        assert "_GPU_ERROR_KEYWORDS" in src, (
            "AC-3: _probe_cuda_runtime must reference the shared "
            "_GPU_ERROR_KEYWORDS constant instead of an inline list."
        )

    @pytest.mark.parametrize(
        "keyword",
        [
            "cublas",
            "cuda",
            "cudnn",
            "dll",
            "gpu",
            "not found",
            "cannot be loaded",
            "load library",
        ],
    )
    def test_each_keyword_triggers_classifier(self, keyword, monkeypatch):
        """AC-3 contract: every keyword in ``_GPU_ERROR_KEYWORDS``
        must trigger ``_is_gpu_runtime_error`` to return True when
        the keyword appears in the exception message (substring
        match)."""
        # Ensure ctranslate2 doesn't expose CUDAError or RuntimeError
        # so we exercise the substring-fallback path (strategy #4).
        mock_ct2 = MagicMock()
        mock_ct2.CUDAError = None
        mock_ct2.RuntimeError = None
        monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)

        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cuda"

        # Construct an exception whose message contains the keyword.
        # We use ValueError (NOT RuntimeError) to avoid the (now-fixed)
        # RuntimeError class-check path — isolates the substring check.
        exc = ValueError(f"prefix {keyword} suffix")
        assert engine._is_gpu_runtime_error(exc) is True, (
            f"AC-3: keyword {keyword!r} in the exception message must "
            f"trigger _is_gpu_runtime_error to return True (substring "
            f"fallback). The keyword is in _GPU_ERROR_KEYWORDS but the "
            f"classifier did not match it."
        )

    @pytest.mark.parametrize(
        "keyword",
        [
            "cublas",
            "cuda",
            "cudnn",
            "dll",
            "gpu",
            "not found",
            "cannot be loaded",
            "load library",
        ],
    )
    def test_each_keyword_triggers_probe(self, keyword, monkeypatch):
        """AC-3 contract: every keyword in ``_GPU_ERROR_KEYWORDS``
        must trigger ``_probe_cuda_runtime``'s GPU-error branch when
        the keyword appears in the exception message.

        We exercise this by constructing a bare engine with a mocked
        ``_model.transcribe`` that raises an exception containing the
        keyword, then calling ``_probe_cuda_runtime`` and asserting
        the probe RE-RAISES (the non-GPU branch re-raises; the GPU
        branch swallows and reloads). To distinguish, we mock the
        GPU branch's ``_reload_under_lock`` and check the probe
        completes without re-raising.
        """
        from voice_typer.server.transcription import (
            TranscriptionEngine,
            _GPU_ERROR_KEYWORDS,
        )

        # Confirm the keyword is in the unified constant (sanity for
        # the parametrize above).
        assert keyword in _GPU_ERROR_KEYWORDS

        # Build a bare engine; only ``_model``, ``_lock``, ``beam_size``,
        # ``best_of``, ``language``, ``condition_on_previous_text``,
        # ``_device``, ``_compute_type``, ``loaded_via`` are read by
        # ``_probe_cuda_runtime``.
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cuda"
        engine._compute_type = "float16"
        engine.beam_size = 1
        engine.best_of = 1
        engine.language = "en"
        engine.condition_on_previous_text = False
        engine._lock = __import__("threading").Lock()
        # Mock model — its ``transcribe`` raises the keyword-laden error.
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError(f"prefix {keyword} suffix")
        engine._model = mock_model

        # Mock the reload path so we don't actually try to load a model.
        # The GPU branch calls ``_reload_under_lock`` after detecting a
        # GPU error; we monkeypatch it to a no-op.
        reloaded = {"n": 0}

        def fake_reload():
            reloaded["n"] += 1

        engine._reload_under_lock = fake_reload
        # ``loaded_via`` is a read-only @property on TranscriptionEngine.
        # Patch it on the CLASS (not the instance) so the post-reload
        # ``log.warning("...Loaded via: %s", self.loaded_via)`` line reads
        # our stub instead of computing the real value (which would touch
        # unset instance attrs).
        from voice_typer.server.transcription import TranscriptionEngine as _TE

        monkeypatch.setattr(type(engine), "loaded_via", property(lambda self: "cuda/float16/test"))

        # ``release_gpu_memory`` is imported at call time from
        # ``asr_utils`` — patch it so it doesn't actually try to release.
        monkeypatch.setattr(
            "voice_typer.server.asr_utils.release_gpu_memory",
            lambda: None,
        )

        # Call the probe — should NOT re-raise (keyword matches the
        # GPU-error branch which swallows + reloads).
        try:
            engine._probe_cuda_runtime(progress_callback=None)
        except RuntimeError as exc:
            pytest.fail(
                f"AC-3: keyword {keyword!r} did NOT trigger the probe's "
                f"GPU-error branch — the probe re-raised the exception "
                f"instead of swallowing + reloading. Exc: {exc}"
            )

        # Sanity: the GPU branch should have called _reload_under_lock.
        assert reloaded["n"] == 1, (
            f"AC-3: keyword {keyword!r} matched the probe's GPU-error "
            f"branch (no re-raise) but the reload counter is "
            f"{reloaded['n']} (expected 1). The probe may have hit a "
            f"different branch than expected."
        )
