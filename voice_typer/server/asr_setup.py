"""ASR auto-setup: GPU detection, dependency check, weight download.

This module provides utilities for automatically setting up the ASR
environment, including GPU detection, dependency checking, and model
weight downloading.

ARCH-001: ``pip_install`` and ``download_weights`` were removed from
this module.  See ``archive/asr_setup_dead_code.py`` for the verbatim
bodies in case they're needed for the future UX-005 on-demand
dependency install feature.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional, Callable

log = logging.getLogger(__name__)

_CONFIG_DIR: Path | None = None


def _config_dir() -> Path:
    global _CONFIG_DIR
    if _CONFIG_DIR is None:
        from voice_typer.server.config import _config_dir as _cfg
        _CONFIG_DIR = _cfg()
    return _CONFIG_DIR


def ensure_hf_env():
    """Ensure HF_HOME points to ~/.voice-typer/huggingface/."""
    hf_home = str(_config_dir() / "huggingface")
    if os.environ.get("HF_HOME") != hf_home:
        os.environ["HF_HOME"] = hf_home
    # Disable symlink warnings on Windows (Developer Mode not required)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # Disable xet transfer protocol — can be extremely slow on some connections
    os.environ.setdefault("HF_HUB_DISABLE_XET", "true")
    # Suppress "unauthenticated requests" nag
    os.environ.setdefault("HF_HUB_DISABLE_UNVERIFIED_ACCESS_WARNING", "1")


def get_voice_typer_python() -> str:
    """Return path to the voice-typer's own Python interpreter.

    Prefers the venv Python at ``~/.voice-typer/venv/Scripts/python.exe``.
    Falls back to ``sys.executable``.
    """
    venv_python = _config_dir() / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python.resolve())
    return sys.executable


def download_parakeet_weights(
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download Parakeet TDT v3 model weights via huggingface_hub.

    Args:
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        True if download succeeded or weights already cached, False otherwise.
    """
    ensure_hf_env()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log.error("[ASR_SETUP] huggingface_hub not available for Parakeet download")
        if progress_callback:
            progress_callback("huggingface_hub not installed, cannot download weights")
        return False

    repo_id = "nvidia/parakeet-tdt-0.6b-v3"

    msg = "Checking Parakeet model cache..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        snapshot_download(repo_id=repo_id, local_files_only=True)
        msg = "Parakeet model already cached"
        log.info("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return True
    except Exception:
        pass

    msg = "Downloading Parakeet TDT v3 model..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        snapshot_download(repo_id=repo_id, resume_download=True)
        msg = "Parakeet model download complete"
        log.info("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return True
    except Exception as e:
        log.error("[ASR_SETUP] Parakeet weight download failed: %s", e)
        if progress_callback:
            progress_callback(f"Download failed: {e}")
        return False
