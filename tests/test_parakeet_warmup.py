"""Warmup-concept regression guard for ``ParakeetEngine``."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.parakeet_engine import ParakeetEngine  # noqa: E402


def _mock_onnx_asr_module() -> MagicMock:
    """Build a MagicMock that quacks like the bits of ``onnx_asr`` we use."""
    mock = MagicMock(name="mock_onnx_asr")
    mock.__version__ = "0.12.0-test"
    mock.load_model.side_effect = lambda *args, **kwargs: MagicMock(name="mock_onnx_asr_model")
    return mock


def _mock_onnxruntime_module() -> MagicMock:
    """Build a MagicMock that quacks like the bits of ``onnxruntime`` we use."""
    mock = MagicMock(name="mock_onnxruntime")
    mock.__version__ = "1.20.0-test"
    mock.get_available_providers.return_value = ["CPUExecutionProvider"]
    return mock


@pytest.fixture(autouse=True)
def _reset_parakeet_engine_class_state(monkeypatch, tmp_path):
    """Reset ``ParakeetEngine`` class-level state between tests."""
    # load() verifies every snapshot under snapshot_search_dirs; point the
    # search at a tmp snapshot and treat it as verified, so tests never
    # touch the real HF cache (which may be absent/incomplete here).
    snap = tmp_path / "models--grikdotnet--parakeet-tdt-0.6b-fp16" / "snapshots" / "test123"
    snap.mkdir(parents=True)
    (snap / "model.onnx").write_bytes(b"fake-onnx")
    monkeypatch.setattr(
        "voice_typer.server.model_availability.snapshot_search_dirs",
        lambda config_dir, repo_id: [snap.parent.parent],
    )
    monkeypatch.setattr(
        "voice_typer.server.security.verify_model_integrity",
        lambda snapshot, repo_id: True,
    )
    saved = (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._onnx_asr,
        ParakeetEngine._ort,
    )
    ParakeetEngine._imports_loaded = False
    ParakeetEngine._onnx_asr = None
    ParakeetEngine._ort = None
    yield
    (
        ParakeetEngine._imports_loaded,
        ParakeetEngine._onnx_asr,
        ParakeetEngine._ort,
    ) = saved


def _make_engine_with_mocks(device: str = "cpu"):
    """Build a ParakeetEngine and force mocked onnx_asr/onnxruntime imports."""
    mock_onnx_asr = _mock_onnx_asr_module()
    mock_onnxruntime = _mock_onnxruntime_module()
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device=device, language="en")
        ParakeetEngine._ensure_imports()
    return engine, mock_onnx_asr, mock_onnxruntime


class TestParakeetOnnxNoWarmup:
    """The ONNX migration removed the warmup pass, assert its absence."""

    def test_engine_has_no_warm_up_model_attribute(self):
        """``ParakeetEngine`` must NOT have a ``_warm_up_model`` attribute."""
        engine = _make_engine_with_mocks(device="cpu")[0]
        assert not hasattr(engine, "_warm_up_model"), (
            "ParakeetEngine must NOT have a _warm_up_model method, the ONNX "
            "migration removed warmup (no torch generate() to prime, no "
            "separate processor + model split). Re-introducing it would be "
            "dead code on the ORT backend."
        )
        assert not hasattr(ParakeetEngine, "_warm_up_model"), (
            "ParakeetEngine class must NOT have a _warm_up_model method either."
        )

    def test_load_does_not_call_model_recognize(self):
        """``load()`` must NOT call ``model.recognize()``, the ONNX"""
        engine, mock_onnx_asr, _ = _make_engine_with_mocks(device="cpu")
        with patch.object(type(engine), "_is_cached", return_value=True):
            engine.load()

        mock_onnx_asr.load_model.assert_called_once()
        # The constructed model's recognize() must NOT have been called.
        mock_model = engine._model
        assert mock_model is not None, "load() must construct self._model"
        (
            mock_model.recognize.assert_not_called(),
            (
                "load() must NOT call model.recognize(), the ONNX backend has "
                "no warmup pass. The first recognize() happens on the first "
                "transcribe() (the user's first dictation)."
            ),
        )

    def test_engine_has_no_torch_class_attribute(self):
        """``ParakeetEngine`` must NOT have a ``_torch`` class attribute."""
        for attr in ("_torch", "_AutoModelForTDT", "_AutoProcessor", "_hf_home_set"):
            assert not hasattr(ParakeetEngine, attr), (
                f"ParakeetEngine must NOT have a {attr!r} class attribute, "
                f"the ONNX migration removed all torch/transformers references. "
                f"Re-introducing it would revert the ONNX migration."
            )
