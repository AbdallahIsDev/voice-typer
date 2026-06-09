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


class TestPipInstall:
    def test_pip_install_empty_packages(self):
        from voice_typer.server.asr_setup import pip_install
        assert pip_install([]) is True

    @patch("voice_typer.server.asr_setup.subprocess.run")
    def test_pip_install_success(self, mock_run):
        from voice_typer.server.asr_setup import pip_install
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = pip_install(["some-package"])

        assert result is True
        mock_run.assert_called_once()

    @patch("voice_typer.server.asr_setup.subprocess.run")
    def test_pip_install_failure(self, mock_run):
        from voice_typer.server.asr_setup import pip_install
        mock_run.return_value = MagicMock(returncode=1, stderr="error")

        result = pip_install(["bad-package"])

        assert result is False

    @patch("voice_typer.server.asr_setup.subprocess.run")
    def test_pip_install_calls_progress_callback(self, mock_run):
        from voice_typer.server.asr_setup import pip_install
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        callback = MagicMock()

        pip_install(["pkg"], progress_callback=callback)

        assert callback.called


class TestDownloadWeights:
    def test_download_weights_already_cached(self):
        from voice_typer.server.asr_setup import download_weights

        mock_hf = MagicMock()
        mock_hf.snapshot_download.return_value = "/cache/path"

        callback = MagicMock()
        with patch.dict(sys.modules, {"huggingface_hub": mock_hf}):
            result = download_weights("small.en", progress_callback=callback)

        assert result is True

    def test_download_weights_not_cached_then_downloads(self):
        from voice_typer.server.asr_setup import download_weights

        mock_hf = MagicMock()
        # First call (cache check) fails, second (download) succeeds
        mock_hf.snapshot_download.side_effect = [
            Exception("not cached"),
            "/cache/path",
        ]

        with patch.dict(sys.modules, {"huggingface_hub": mock_hf}):
            result = download_weights("small.en")

        assert result is True

    def test_download_weights_no_huggingface_hub(self):
        from voice_typer.server.asr_setup import download_weights

        with patch.dict(sys.modules, {"huggingface_hub": None}):
            result = download_weights("small.en")

        assert result is False

    def test_download_weights_download_fails(self):
        from voice_typer.server.asr_setup import download_weights

        mock_hf = MagicMock()
        mock_hf.snapshot_download.side_effect = [
            Exception("not cached"),
            Exception("download failed"),
        ]

        with patch.dict(sys.modules, {"huggingface_hub": mock_hf}):
            result = download_weights("small.en")

        assert result is False
