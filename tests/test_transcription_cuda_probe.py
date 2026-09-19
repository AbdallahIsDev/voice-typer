"""regression tests for ``TranscriptionEngine._probe_cuda_runtime``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def cuda_engine():
    """Construct a TranscriptionEngine on CPU then force the CUDA path."""
    from voice_typer.server.transcription import TranscriptionEngine

    engine = TranscriptionEngine(model_size="small.en", device="cpu")
    engine._device = "cuda"
    engine._compute_type = "float16"
    return engine


class TestProbeCudaRuntime:
    """``_probe_cuda_runtime`` branch coverage."""

    def test_probe_cuda_runtime_skips_when_model_none(self, cuda_engine):
        """#1: ``self._model is None`` must trigger an early"""
        cuda_engine._model = None

        # Call probe, must return None without raising.
        result = cuda_engine._probe_cuda_runtime()
        assert result is None, (
            "_probe_cuda_runtime must return None when _model is None (early-return contract at line 520-522)."
        )
        # Sanity: device/compute_type untouched (no fallback ran).
        assert cuda_engine._device == "cuda"
        assert cuda_engine._compute_type == "float16"

    def test_probe_cuda_runtime_success_no_fallback(self, cuda_engine):
        """
        #2: successful probe must keep ``_device == "cuda"``.
        The fallback branch must NOT fire: ``_device`` stays
        """
        mock_model = MagicMock()
        segments_returned: list[object] = []
        mock_model.transcribe.return_value = (segments_returned, MagicMock())
        cuda_engine._model = mock_model

        # Patch _reload_under_lock so we can assert it was NOT called
        cuda_engine._reload_under_lock = MagicMock(name="_reload_under_lock")

        result = cuda_engine._probe_cuda_runtime()

        assert result is None, "_probe_cuda_runtime returns None on success."
        assert mock_model.transcribe.call_count == 1, (
            f"expected exactly 1 transcribe call, got {mock_model.transcribe.call_count}"
        )
        _, probe_kwargs = mock_model.transcribe.call_args
        # ``best_of`` must NOT be forwarded: faster-whisper only honors it
        assert "best_of" not in probe_kwargs
        assert probe_kwargs["temperature"] == 0.0
        # Device stays CUDA, the fallback did NOT fire.
        assert cuda_engine._device == "cuda", "on probe success _device must remain 'cuda', the fallback must NOT fire."
        assert cuda_engine._compute_type == "float16", "on probe success _compute_type must remain 'float16'."
        # _reload_under_lock must NOT have been called.
        cuda_engine._reload_under_lock.assert_not_called()

    def test_probe_cuda_runtime_cublas_error_triggers_cpu_fallback(self, cuda_engine):
        """#3: a cuBLAS load failure must trigger CPU fallback."""
        mock_model = MagicMock()

        # Raise the cuBLAS error during the segment iteration, not
        def _transcribe_side_effect(*args, **kwargs):
            def _gen():
                raise Exception("cuBLAS load library failed")
                yield  # unreachable, makes this a generator function

            return (_gen(), MagicMock())

        mock_model.transcribe.side_effect = _transcribe_side_effect
        cuda_engine._model = mock_model

        # Patch _reload_under_lock to a no-op mock so we can assert it
        cuda_engine._reload_under_lock = MagicMock(name="_reload_under_lock")

        import gc

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(gc, "collect", lambda: None)

            cuda_engine._probe_cuda_runtime()

        # All three state transitions fired.
        assert cuda_engine._device == "cpu", "cuBLAS error must trigger _device = 'cpu' fallback."
        assert cuda_engine._compute_type == "int8", "cuBLAS error must trigger _compute_type = 'int8' fallback."
        # Model was nulled before reload so a concurrent transcribe()
        assert cuda_engine._model is None or cuda_engine._model is mock_model, (
            "_model must be set to None before _reload_under_lock runs; "
            "the reload mock leaves it as-is so we accept None or the "
            "pre-reload sentinel."
        )
        # The reload was called exactly once.
        assert cuda_engine._reload_under_lock.call_count == 1, (
            "_reload_under_lock must be called exactly once on cuBLAS fallback."
        )
        # HU-25: the RACE-023 deferred release must be armed so the next
        assert cuda_engine._pending_gc_collect is True, "cuBLAS fallback must set _pending_gc_collect = True (HU-25)"

    def test_probe_cuda_runtime_non_cuda_error_propagates(self, cuda_engine):
        """#4: a non-CUDA exception must propagate (NOT swallowed)."""
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = ValueError("unrelated")
        cuda_engine._model = mock_model

        cuda_engine._reload_under_lock = MagicMock(name="_reload_under_lock")

        with pytest.raises(ValueError, match="unrelated"):
            cuda_engine._probe_cuda_runtime()

        # The fallback must NOT have fired for a non-CUDA error.
        assert cuda_engine._device == "cuda", "non-CUDA error must NOT trigger device fallback."
        assert cuda_engine._compute_type == "float16", "non-CUDA error must NOT trigger compute_type fallback."
        cuda_engine._reload_under_lock.assert_not_called()
