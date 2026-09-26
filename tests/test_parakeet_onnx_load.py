"""Tests for ``ParakeetEngine.load()`` against the ONNX Runtime backend."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# NOTE: no module-level ``pytest.importorskip("onnx_asr")``, these
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
    mock.get_available_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    mock.RunOptions = MagicMock(name="mock_RunOptions")
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


def _make_engine(device: str = "cuda", language: str = "en"):
    """Build a ParakeetEngine WITHOUT triggering the lazy onnx_asr import."""
    return ParakeetEngine(device=device, language=language)


def _make_engine_with_mocks(device: str = "cuda", language: str = "en"):
    """Build a ParakeetEngine and force mocked onnx_asr/onnxruntime imports."""
    mock_onnx_asr = _mock_onnx_asr_module()
    mock_onnxruntime = _mock_onnxruntime_module()
    with patch.dict(
        sys.modules,
        {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
    ):
        engine = ParakeetEngine(device=device, language=language)
        # Force the lazy import to fire now (inside the patch context).
        ParakeetEngine._ensure_imports()
    return engine, mock_onnx_asr, mock_onnxruntime


class TestParakeetOnnxIsAvailable:
    """``ParakeetEngine.is_available()`` probes the onnx_asr import surface."""

    def test_is_available_returns_true_when_onnx_asr_installed(self):
        """When onnx_asr + onnxruntime are importable, ``is_available()``"""
        mock_onnx_asr = _mock_onnx_asr_module()
        mock_onnxruntime = _mock_onnxruntime_module()
        with patch.dict(
            sys.modules,
            {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
        ):
            assert ParakeetEngine.is_available() is True

    def test_is_available_returns_false_when_onnx_asr_missing(self):
        """When onnx_asr is not importable, ``is_available()`` returns"""
        # Evict onnx_asr from sys.modules so the import fails. Use
        with patch.dict(sys.modules, {"onnx_asr": None}):
            assert ParakeetEngine.is_available() is False


class TestParakeetOnnxEnsureImports:
    """``_ensure_imports()`` lazily imports onnx_asr + onnxruntime."""

    def test_returns_false_when_packages_missing(self):
        """Missing onnx_asr / onnxruntime → ``_ensure_imports()`` returns"""
        with patch.dict(sys.modules, {"onnx_asr": None, "onnxruntime": None}):
            ParakeetEngine._imports_loaded = False
            ParakeetEngine._onnx_asr = None
            ParakeetEngine._ort = None
            result = ParakeetEngine._ensure_imports()
        assert result is False
        assert ParakeetEngine._imports_loaded is False

    def test_returns_true_and_stashes_modules_when_present(self):
        """Successful import → ``True``, modules stashed on class attrs."""
        mock_onnx_asr = _mock_onnx_asr_module()
        mock_onnxruntime = _mock_onnxruntime_module()
        with patch.dict(
            sys.modules,
            {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
        ):
            ParakeetEngine._imports_loaded = False
            result = ParakeetEngine._ensure_imports()
        assert result is True
        assert ParakeetEngine._imports_loaded is True
        assert ParakeetEngine._onnx_asr is mock_onnx_asr
        assert ParakeetEngine._ort is mock_onnxruntime

    def test_idempotent_on_re_entry(self):
        """Calling ``_ensure_imports()`` twice is a fast flag-check on"""
        mock_onnx_asr = _mock_onnx_asr_module()
        mock_onnxruntime = _mock_onnxruntime_module()
        with patch.dict(
            sys.modules,
            {"onnx_asr": mock_onnx_asr, "onnxruntime": mock_onnxruntime},
        ):
            ParakeetEngine._ensure_imports()
            # Replace the mocks to prove the second call doesn't re-import.
            new_mock = _mock_onnx_asr_module()
            with patch.dict(sys.modules, {"onnx_asr": new_mock}):
                ParakeetEngine._ensure_imports()
            # The class attr should still be the FIRST mock.
            assert ParakeetEngine._onnx_asr is mock_onnx_asr


class TestParakeetOnnxLoad:
    """``load()`` invokes ``onnx_asr.load_model`` with the right args."""

    def test_load_failure_raises_when_imports_missing(self):
        """If onnx_asr / onnxruntime aren't installed, load() returns False."""
        engine = _make_engine(device="cpu")
        with patch.dict(sys.modules, {"onnx_asr": None, "onnxruntime": None}):
            ParakeetEngine._imports_loaded = False
            result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_success_with_cached_model(self):
        """load() succeeds when imports + cache + Model() all work."""
        engine, mock_onnx_asr, _ = _make_engine_with_mocks(device="cpu")
        with patch.object(type(engine), "_is_cached", return_value=True):
            result = engine.load()
        assert result is True
        assert engine.is_loaded is True
        mock_onnx_asr.load_model.assert_called_once()
        call_args, call_kwargs = mock_onnx_asr.load_model.call_args
        # First positional arg is the onnx-asr TYPE name
        assert call_args[0] == "nemo-conformer-tdt"
        assert call_kwargs.get("quantization") == "fp16"
        assert call_kwargs.get("providers") == ["CPUExecutionProvider"]

    def test_load_cuda_uses_cuda_provider_when_available(self):
        """device='cuda' + CUDAExecutionProvider available → providers"""
        engine, mock_onnx_asr, mock_ort = _make_engine_with_mocks(device="cuda")
        mock_ort.get_available_providers.return_value = [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ]
        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            result = engine.load()
        assert result is True
        call_args, call_kwargs = mock_onnx_asr.load_model.call_args
        assert call_kwargs["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def test_load_cuda_falls_back_to_cpu_when_ep_unavailable(self):
        """device='cuda' but CUDAExecutionProvider NOT in available_providers"""
        engine, mock_onnx_asr, mock_ort = _make_engine_with_mocks(device="cuda")
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        with (
            patch.object(type(engine), "_is_cached", return_value=True),
            patch.object(type(engine), "_should_force_cpu", return_value=False),
        ):
            result = engine.load()
        assert result is True
        call_args, call_kwargs = mock_onnx_asr.load_model.call_args
        assert call_kwargs["providers"] == ["CPUExecutionProvider"]

    def test_load_returns_true_when_already_loaded(self):
        """Idempotent load: if model is already set, return True immediately."""
        engine, mock_onnx_asr, _ = _make_engine_with_mocks(device="cpu")
        engine._model = MagicMock()  # simulate already-loaded
        # load_model(...) must NOT be re-called.
        result = engine.load()
        assert result is True
        mock_onnx_asr.load_model.assert_not_called()

    def test_load_raises_when_model_not_cached(self):
        """If the model is not in the HF cache, load() raises"""
        from voice_typer.server.asr_errors import ModelNotDownloadedError

        engine, _, _ = _make_engine_with_mocks(device="cpu")
        with (
            patch.object(type(engine), "_is_cached", return_value=False),
            pytest.raises(ModelNotDownloadedError),
        ):
            engine.load()
        assert engine.is_loaded is False

    def test_load_failure_when_model_constructor_raises(self):
        """An exception from onnx_asr.load_model(...) returns False (not re-raise)."""
        engine, mock_onnx_asr, _ = _make_engine_with_mocks(device="cpu")
        mock_onnx_asr.load_model.side_effect = RuntimeError("disk read failed")
        with patch.object(type(engine), "_is_cached", return_value=True):
            result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_invokes_progress_callback(self):
        """When supplied, the progress callback is called with status messages."""
        engine, _, _ = _make_engine_with_mocks(device="cpu")
        with patch.object(type(engine), "_is_cached", return_value=True):
            messages: list[str] = []
            engine.load(progress_callback=messages.append)
        assert any("Parakeet" in m for m in messages)
        assert any("ready" in m.lower() for m in messages)

    def test_load_resets_cpu_fallback_notification_flag(self):
        """A fresh ``load()`` resets ``_cpu_fallback_notified`` so a"""
        engine, _, _ = _make_engine_with_mocks(device="cpu")
        engine._cpu_fallback_notified = True  # simulate prior fallback
        with patch.object(type(engine), "_is_cached", return_value=True):
            engine.load()
        assert engine._cpu_fallback_notified is False


class TestParakeetOnnxInit:
    """``ParakeetEngine.__init__`` stores config without importing deps."""

    def test_init_defaults(self):
        engine = _make_engine()
        assert engine.device == "cuda"
        assert engine.language == "en"
        assert engine._model is None
        assert engine.is_loaded is False

    def test_init_cpu_device(self):
        engine = _make_engine(device="cpu")
        assert engine.device == "cpu"

    def test_init_non_english_language(self):
        engine = _make_engine(language="fr")
        assert engine.language == "fr"

    def test_init_default_batch_size_is_two(self):
        """``_INFERENCE_BATCH_SIZE`` default is 2 (kept for backward"""
        engine = _make_engine()
        assert engine._INFERENCE_BATCH_SIZE == 2

    def test_device_info_reflects_device(self):
        engine = _make_engine(device="cpu")
        assert engine.device_info == "parakeet/cpu"

    def test_loaded_via_includes_model_id(self):
        engine = _make_engine(device="cuda")
        assert "parakeet/cuda/" in engine.loaded_via
        assert "grikdotnet/parakeet-tdt-0.6b-fp16" in engine.loaded_via


class TestParakeetOnnxSelectProviders:
    """``_select_providers(device)`` maps device → ORT providers list."""

    def test_cpu_device_returns_cpu_only(self):
        engine, _, _ = _make_engine_with_mocks(device="cpu")
        assert engine._select_providers("cpu") == ["CPUExecutionProvider"]

    def test_cuda_device_with_cuda_ep_available(self):
        engine, _, mock_ort = _make_engine_with_mocks(device="cuda")
        mock_ort.get_available_providers.return_value = [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ]
        assert engine._select_providers("cuda") == [
            "CUDAExecutionProvider",
            "CPUExecutionProvider",
        ]

    def test_cuda_device_without_cuda_ep_falls_back_to_cpu(self):
        engine, _, mock_ort = _make_engine_with_mocks(device="cuda")
        mock_ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        assert engine._select_providers("cuda") == ["CPUExecutionProvider"]

    def test_cuda_device_when_get_available_providers_raises(self):
        """Defensive: if ``get_available_providers()`` raises (shouldn't"""
        engine, _, mock_ort = _make_engine_with_mocks(device="cuda")
        mock_ort.get_available_providers.side_effect = RuntimeError("probe failed")
        assert engine._select_providers("cuda") == ["CPUExecutionProvider"]
