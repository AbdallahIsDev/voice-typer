"""Tests for ASR auto-setup utilities."""

import sys
import pytest
from unittest.mock import MagicMock, patch, call


class TestDetectGpu:
    def test_detect_gpu_no_cuda(self):
        """When no CUDA libraries are available, returns available=False."""
        from voice_typer.server.asr_setup import detect_gpu

        # Mock both ctranslate2 and torch as unavailable
        with patch.dict(sys.modules, {"ctranslate2": None, "torch": None}):
            result = detect_gpu()

        assert result["available"] is False
        assert result["device_name"] is None

    def test_detect_gpu_via_ctranslate2(self):
        """When ctranslate2 reports CUDA devices, GPU is available."""
        from voice_typer.server.asr_setup import detect_gpu

        mock_ct2 = MagicMock()
        mock_ct2.get_cuda_device_count.return_value = 1
        mock_ct2.get_cuda_version.return_value = "12.0"

        with patch.dict(sys.modules, {"ctranslate2": mock_ct2}):
            result = detect_gpu()

        assert result["available"] is True
        assert result["cuda_version"] == "12.0"

    def test_detect_gpu_via_torch_fallback(self):
        """When ctranslate2 fails but torch has CUDA, GPU is available."""
        from voice_typer.server.asr_setup import detect_gpu

        mock_ct2 = MagicMock()
        mock_ct2.get_cuda_device_count.return_value = 0

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.get_device_name.return_value = "GeForce RTX 4090"
        mock_props = MagicMock()
        mock_props.total_mem = 24 * 1024 * 1024 * 1024  # 24 GB
        mock_torch.cuda.get_device_properties.return_value = mock_props

        with patch.dict(sys.modules, {"ctranslate2": mock_ct2, "torch": mock_torch}):
            result = detect_gpu()

        assert result["available"] is True
        assert "RTX" in result["device_name"]
        assert result["vram_mb"] is not None


class TestCheckDependencies:
    def test_check_dependencies_returns_dict(self):
        from voice_typer.server.asr_setup import check_dependencies
        result = check_dependencies()
        assert isinstance(result, dict)
        assert "faster-whisper" in result
        assert "numpy" in result


# ARCH-001: ``TestPipInstall`` and ``TestDownloadWeights`` were removed
# because the corresponding functions (``pip_install``, ``download_weights``)
# were dead code that has been moved to ``archive/asr_setup_dead_code.py``.
# If the functions are revived, restore these tests too.
