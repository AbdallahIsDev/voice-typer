"""ASR auto-setup: GPU detection, dependency check, pip install, weight download.

This module provides utilities for automatically setting up the ASR
environment, including GPU detection, dependency installation, and
model weight downloading.
"""

import logging
import platform
import shutil
import subprocess
import sys
from typing import Optional, Callable

log = logging.getLogger(__name__)


def detect_gpu() -> dict:
    """Detect GPU availability and capabilities.

    Returns a dict with keys:
        - 'available': bool
        - 'device_name': str or None
        - 'cuda_version': str or None
        - 'vram_mb': int or None
    """
    result = {
        'available': False,
        'device_name': None,
        'cuda_version': None,
        'vram_mb': None,
    }

    # Try ctranslate2 CUDA detection
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            result['available'] = True
            result['device_name'] = 'CUDA GPU'
            try:
                result['cuda_version'] = ctranslate2.get_cuda_version()
            except Exception:
                pass
            log.info("[ASR_SETUP] CUDA GPU detected via ctranslate2")
            return result
    except ImportError:
        pass
    except Exception as e:
        log.debug("[ASR_SETUP] ctranslate2 CUDA detection failed: %s", e)

    # Try PyTorch CUDA detection
    try:
        import torch
        if torch.cuda.is_available():
            result['available'] = True
            result['device_name'] = torch.cuda.get_device_name(0)
            result['vram_mb'] = torch.cuda.get_device_properties(0).total_mem // (1024 * 1024)
            log.info("[ASR_SETUP] CUDA GPU detected via PyTorch: %s", result['device_name'])
            return result
    except ImportError:
        pass
    except Exception as e:
        log.debug("[ASR_SETUP] PyTorch CUDA detection failed: %s", e)

    log.info("[ASR_SETUP] No GPU detected")
    return result


def check_dependencies() -> dict:
    """Check which ASR dependencies are installed.

    Returns a dict mapping package names to their installed version
    strings (or None if not installed).
    """
    deps = {
        'faster-whisper': None,
        'ctranslate2': None,
        'numpy': None,
        'scipy': None,
        'sounddevice': None,
    }

    for pkg in deps:
        try:
            mod = __import__(pkg.replace('-', '_'))
            deps[pkg] = getattr(mod, '__version__', 'installed')
        except (ImportError, ValueError):
            pass

    log.info("[ASR_SETUP] Dependency check: %s", deps)
    return deps


def pip_install(
    packages: list[str],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Install packages via pip.

    Args:
        packages: List of package specifiers (e.g., ['faster-whisper>=1.0']).
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        True if all packages installed successfully, False otherwise.
    """
    if not packages:
        return True

    pip_path = shutil.which('pip') or sys.executable
    cmd = [pip_path, '-m', 'pip', 'install', '--quiet'] + packages
    if pip_path == sys.executable:
        cmd = [sys.executable, '-m', 'pip', 'install', '--quiet'] + packages

    msg = f"Installing packages: {', '.join(packages)}"
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            log.error(
                "[ASR_SETUP] pip install failed (rc=%d): %s",
                result.returncode, result.stderr,
            )
            if progress_callback:
                progress_callback(f"Installation failed: {result.stderr[:200]}")
            return False

        msg = "Package installation complete"
        log.info("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return True
    except subprocess.TimeoutExpired:
        log.error("[ASR_SETUP] pip install timed out")
        if progress_callback:
            progress_callback("Installation timed out")
        return False
    except Exception as e:
        log.error("[ASR_SETUP] pip install error: %s", e)
        if progress_callback:
            progress_callback(f"Installation error: {e}")
        return False


def download_weights(
    model_size: str = 'small.en',
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download model weights via huggingface_hub.

    Args:
        model_size: The model size to download (e.g., 'small.en').
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        True if download succeeded or weights already cached, False otherwise.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log.error("[ASR_SETUP] huggingface_hub not available for weight download")
        if progress_callback:
            progress_callback("huggingface_hub not installed, cannot download weights")
        return False

    repo_id = f"Systran/faster-whisper-{model_size}"

    # Check if already cached
    msg = f"Checking model cache for '{model_size}'..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        snapshot_download(repo_id=repo_id, local_files_only=True)
        msg = f"Model '{model_size}' already cached"
        log.info("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return True
    except Exception:
        pass

    # Download
    msg = f"Downloading model '{model_size}'..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        snapshot_download(repo_id=repo_id, resume_download=True)
        msg = f"Model '{model_size}' download complete"
        log.info("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return True
    except Exception as e:
        log.error("[ASR_SETUP] Weight download failed: %s", e)
        if progress_callback:
            progress_callback(f"Download failed: {e}")
        return False
