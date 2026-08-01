"""XV-65: QwenEngine CUDA device resolution tests.

Verifies that Qwen3-ASR is actually moved to the GPU when CUDA is
available.  Previously ``QwenEngine.load()`` called
``qwen_asr.Qwen3ASRModel.from_pretrained(self.model_path)`` with no
``device=`` kwarg and no ``.to(self.device)`` call, so the model ran
entirely on CPU regardless of GPU config (5-10× slower inference).
``__init__`` stored ``self.device`` but ``load()`` never used it, and
``transcribe_with_fallback``'s ``if self.device == "cuda"`` branch was
unreachable when the config used ``device="auto"``.

These tests mock ``qwen_asr.Qwen3ASRModel`` and
``torch.cuda.is_available`` — no real GPU or model weights required.
"""

import json as _json
from unittest.mock import MagicMock, patch

import pytest


def _make_engine(model_path: str = "/fake/qwen/model", **kwargs):
    from voice_typer.server.qwen_engine import QwenEngine

    return QwenEngine(model_path=model_path, **kwargs)


def _make_model_dir(tmp_path) -> str:
    """Create a minimal Qwen model dir with a valid config.json.

    Mirrors the setup in ``tests/test_qwen_engine.py::test_load_success``
    so ``load()``'s SEC-audit-007 directory validation + config.json
    read pass through to the ``qwen_asr`` import block.
    """
    model_dir = tmp_path / "qwen_model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(_json.dumps({"arch": "qwen3"}))
    return str(model_dir)


def _make_mock_torch(cuda_available: bool) -> MagicMock:
    """Build a fake ``torch`` module with a sentinel ``float16``.

    Using a string sentinel (rather than the real ``torch.float16``
    dtype) makes ``mock_model.to.assert_any_call(...)`` assertions
    deterministic and independent of the host's torch installation.
    """
    mock_torch = MagicMock(name="torch")
    mock_torch.cuda.is_available.return_value = cuda_available
    mock_torch.float16 = "FLOAT16_SENTINEL"
    return mock_torch


class TestQwenResolveDevice:
    """XV-65: ``_resolve_device`` mirrors ``TranscriptionEngine._resolve_device``."""

    def test_auto_resolves_to_cuda_when_available(self):
        engine = _make_engine(device="auto")
        mock_torch = _make_mock_torch(cuda_available=True)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert engine._resolve_device() == "cuda"

    def test_auto_resolves_to_cpu_when_unavailable(self):
        engine = _make_engine(device="auto")
        mock_torch = _make_mock_torch(cuda_available=False)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert engine._resolve_device() == "cpu"

    def test_auto_falls_back_to_cpu_when_torch_missing(self):
        engine = _make_engine(device="auto")
        # Simulate torch not installed: ``sys.modules["torch"] = None``
        # makes ``import torch`` raise ``ImportError``.
        with patch.dict("sys.modules", {"torch": None}):
            assert engine._resolve_device() == "cpu"

    def test_cuda_returned_as_is_without_torch_probe(self):
        """Explicit ``"cuda"`` must NOT call ``torch.cuda.is_available``."""
        engine = _make_engine(device="cuda")
        mock_torch = _make_mock_torch(cuda_available=True)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert engine._resolve_device() == "cuda"
        # No CUDA probe — explicit device wins.
        mock_torch.cuda.is_available.assert_not_called()

    def test_cpu_returned_as_is(self):
        engine = _make_engine(device="cpu")
        assert engine._resolve_device() == "cpu"

    def test_does_not_mutate_self_device(self):
        """``_resolve_device`` must NOT update ``self.device`` — only ``load()`` does.

        This is the XV-65 design contract: a failed ``.to("cuda")``
        must not leave ``self.device`` stuck at ``"cuda"``.
        """
        engine = _make_engine(device="auto")
        mock_torch = _make_mock_torch(cuda_available=True)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            engine._resolve_device()
        assert engine.device == "auto"  # unchanged


class TestQwenLoadMovesModelToCuda:
    """XV-65: ``load()`` moves the model to CUDA when available."""

    def test_load_with_auto_moves_model_to_cuda(self, tmp_path):
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True
        # Model moved to CUDA after from_pretrained().
        mock_model.to.assert_any_call("cuda")
        # float16 conversion attempted ( fix step 2).
        mock_model.to.assert_any_call("FLOAT16_SENTINEL")
        # self.device updated from "auto" to "cuda" so
        # transcribe_with_fallback's CUDA-error branch is reachable.
        assert engine.device == "cuda"

    def test_load_with_cuda_moves_model_to_cuda(self, tmp_path):
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cuda")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True
        mock_model.to.assert_any_call("cuda")
        mock_model.to.assert_any_call("FLOAT16_SENTINEL")
        assert engine.device == "cuda"

    def test_load_with_auto_falls_back_to_cpu_when_no_cuda(self, tmp_path):
        """When ``auto`` resolves to CPU, no ``.to("cuda")`` call is made."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=False)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True
        # No device move attempted — model stays where from_pretrained put it.
        mock_model.to.assert_not_called()
        assert engine.device == "cpu"

    def test_load_with_cpu_does_not_move_to_cuda(self, tmp_path):
        """Explicit CPU config must not trigger CUDA placement even if a GPU exists."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cpu")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True
        mock_model.to.assert_not_called()
        assert engine.device == "cpu"

    def test_float16_failure_does_not_abort_load(self, tmp_path):
        """XV-65: float16 conversion is best-effort; a wrapper that rejects it still loads."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="cuda")

        mock_model = MagicMock(name="qwen_model")
        # First .to("cuda") succeeds; second .to(torch.float16) raises.
        mock_model.to.side_effect = [None, RuntimeError("half not supported")]

        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is True, "float16 failure must not abort load()"
        assert engine.device == "cuda"
        # Both calls attempted; the float16 exception was swallowed.
        assert mock_model.to.call_count == 2

    def test_load_failure_does_not_set_device_to_cuda(self, tmp_path):
        """XV-65: if ``.to("cuda")`` raises, ``self.device`` must NOT become "cuda".

        With ``device="auto"``, the resolved device is "cuda" but the
        model move fails (e.g. driver mismatch, OOM at load). The
        ``self.device = effective_device`` assignment is AFTER the
        ``.to("cuda")`` call, so a raised exception propagates to the
        outer try/except and ``self.device`` stays at "auto". This is
        the XV-65 contract: ``self.device`` is only updated to "cuda"
        AFTER a successful ``.to("cuda")`` — otherwise
        ``transcribe_with_fallback``'s ``if self.device == "cuda"``
        branch would think the model was on GPU when it had never been
        moved, retrying CUDA forever instead of erroring out cleanly.
        """
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        mock_model = MagicMock(name="qwen_model")
        # ``.to("cuda")`` raises — simulates a real CUDA init failure
        # (driver mismatch, OOM at load, etc.).
        mock_model.to.side_effect = RuntimeError("CUDA driver not initialized")

        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            result = engine.load()

        assert result is False
        # ``self.device`` is NOT "cuda" — the failed move prevented the
        # update. It stays at the constructor value "auto".
        assert engine.device == "auto"
        assert engine.is_loaded is False


class TestQwenDeviceInfoUsesResolvedDevice:
    """XV-65: ``device_info`` reflects the resolved device, not the raw config."""

    def test_device_info_auto_with_cuda(self):
        engine = _make_engine(device="auto")
        mock_torch = _make_mock_torch(cuda_available=True)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert engine.device_info == "qwen/cuda"

    def test_device_info_auto_without_cuda(self):
        engine = _make_engine(device="auto")
        mock_torch = _make_mock_torch(cuda_available=False)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert engine.device_info == "qwen/cpu"

    def test_device_info_explicit_cuda(self):
        engine = _make_engine(device="cuda")
        # Explicit device — no torch probe needed.
        assert engine.device_info == "qwen/cuda"

    def test_device_info_explicit_cpu(self):
        engine = _make_engine(device="cpu")
        assert engine.device_info == "qwen/cpu"

    def test_device_info_after_load_uses_resolved_value(self, tmp_path):
        """After ``load()``, ``self.device`` is concrete and ``device_info`` returns it."""
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        mock_model = MagicMock(name="qwen_model")
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            engine.load()

        # After load, self.device is "cuda" (concrete) — device_info
        # returns it without re-probing.
        assert engine.device_info == "qwen/cuda"


class TestTranscribeWithFallbackCudaBranchReachable:
    """XV-65 regression: ``transcribe_with_fallback``'s CUDA-error branch must be reachable.

    Before the fix, ``device="auto"`` made ``self.device`` stay as the
    literal ``"auto"`` after ``load()``, so ``if self.device == "cuda"``
    was always False and CUDA errors were never retried on CPU.
    """

    def test_cuda_error_triggers_cpu_retry_after_auto_load(self, tmp_path):
        model_dir = _make_model_dir(tmp_path)
        engine = _make_engine(model_path=model_dir, device="auto")

        # First transcribe call raises a CUDA error; the second (CPU
        # retry) returns a proper ASRTranscription-shaped result so the
        # non-chunked path's ``result[0].text`` extraction succeeds.
        mock_result = MagicMock(name="asr_result")
        mock_result.text = "recovered on cpu"

        mock_model = MagicMock(name="qwen_model")
        mock_model.transcribe.side_effect = [
            RuntimeError("CUDA out of memory"),
            [mock_result],
        ]
        mock_qwen_module = MagicMock(name="qwen_asr")
        mock_qwen_module.Qwen3ASRModel.from_pretrained.return_value = mock_model
        mock_torch = _make_mock_torch(cuda_available=True)

        with (
            patch.dict(
                "sys.modules",
                {"qwen_asr": mock_qwen_module, "torch": mock_torch},
            ),
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=True,
            ),
        ):
            engine.load()

        # After load, self.device must be "cuda" (not "auto") so the
        # fallback branch is reachable.
        assert engine.device == "cuda"

        import numpy as np

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe_with_fallback(audio)
        assert result == "recovered on cpu"
        # Model was moved to CPU during fallback.
        mock_model.to.assert_any_call("cpu")
        # self.device flipped to "cpu" by the fallback path.
        assert engine.device == "cpu"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
