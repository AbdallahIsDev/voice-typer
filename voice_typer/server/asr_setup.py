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
import shutil
import time
from pathlib import Path
from typing import Optional, Callable

log = logging.getLogger(__name__)

# SEC-audit-005: Allowlist of file patterns permitted in model downloads.
# Prevents supply-chain attacks where a compromised HF repo could include
# executables, scripts, or other unexpected files.
_HF_ALLOW_PATTERNS = [
    "*.safetensors", "*.bin", "config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json",
    "preprocessor_config.json", "feature_extractor_config.json",
    "generation_config.json", "model.safetensors.index.json", "*.model",
]

# NEW-DEAD-027: removed the module-level ``_CONFIG_DIR`` cache.
# It was a one-line indirection over ``config._config_dir()`` that
# provided no measurable performance benefit (Path construction is
# ~1 µs) and made the code harder to read.  Callers now use
# ``config._config_dir()`` directly.

# PROD-004: maximum number of download retries with exponential backoff.
_MAX_DOWNLOAD_RETRIES = 4

# PROD-005: estimated model sizes in bytes for disk-space pre-check.
_ESTIMATED_MODEL_SIZES = {
    "nvidia/parakeet-tdt-0.6b-v3": 2 * 1024 * 1024 * 1024,  # ~2 GB
    "Systran/faster-whisper-tiny.en": 75 * 1024 * 1024,  # ~75 MB
    "Systran/faster-whisper-small.en": 466 * 1024 * 1024,  # ~466 MB
    "Systran/faster-whisper-medium.en": 1500 * 1024 * 1024,  # ~1.5 GB
    "Systran/faster-whisper-large-v3": 3000 * 1024 * 1024,  # ~3 GB
}


def _check_disk_space(repo_id: str, cache_dir: Optional[Path] = None) -> bool:
    """PROD-005: Check available disk space before model download.

    Returns True if there is sufficient disk space for the model,
    False otherwise.  If the model size is unknown, assumes 2 GB.
    """
    try:
        estimated_size = _ESTIMATED_MODEL_SIZES.get(repo_id, 2 * 1024 * 1024 * 1024)
        if cache_dir is None:
            from voice_typer.server.config import _config_dir
            cache_dir = _config_dir() / "huggingface"
        target_dir = cache_dir if cache_dir.exists() else Path.home()
        usage = shutil.disk_usage(str(target_dir))
        available_gb = usage.free / (1024 * 1024 * 1024)
        required_gb = estimated_size / (1024 * 1024 * 1024)
        if usage.free < estimated_size:
            log.warning(
                "[ASR_SETUP] Insufficient disk space for %s: "
                "%.1f GB available, ~%.1f GB required",
                repo_id, available_gb, required_gb,
            )
            return False
        log.debug(
            "[ASR_SETUP] Disk space OK for %s: %.1f GB available, ~%.1f GB required",
            repo_id, available_gb, required_gb,
        )
        return True
    except Exception as exc:
        log.debug("[ASR_SETUP] Disk space check failed (proceeding anyway): %s", exc)
        return True  # proceed if we can't check


def ensure_hf_env():
    """Ensure HF_HOME points to ~/.voice-typer/huggingface/."""
    from voice_typer.server.config import _config_dir
    hf_home = str(_config_dir() / "huggingface")
    if os.environ.get("HF_HOME") != hf_home:
        os.environ["HF_HOME"] = hf_home
    # Disable symlink warnings on Windows (Developer Mode not required)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # Disable xet transfer protocol — can be extremely slow on some connections
    os.environ.setdefault("HF_HUB_DISABLE_XET", "true")
    # Suppress "unauthenticated requests" nag
    os.environ.setdefault("HF_HUB_DISABLE_UNVERIFIED_ACCESS_WARNING", "1")


def _verify_model_integrity(repo_id: str, local_dir: str) -> bool:
    """Verify downloaded model files have valid structure.

    PROD-006: Basic integrity check that the model directory
    contains expected files and they're not empty.

    SEC-audit-005: Delegates to the centralized
    ``security.verify_model_integrity()`` which also checks SHA-256
    hashes against the MODEL_HASHES manifest when available.
    """
    from voice_typer.server.security import verify_model_integrity
    return verify_model_integrity(local_dir, repo_id)


def download_parakeet_weights(
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download Parakeet TDT v3 model weights via huggingface_hub.

    PROD-004: wraps snapshot_download in retry loop with exponential
    backoff (1s, 2s, 4s, 8s, max 4 retries). Logs each retry attempt.

    PROD-005: checks disk space before attempting download.

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

    # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest
    from voice_typer.server.security import MODEL_HASHES
    PARAKEET_REVISION = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

    msg = "Checking Parakeet model cache..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        local_dir = snapshot_download(
            repo_id=repo_id,
            revision=PARAKEET_REVISION,
            allow_patterns=_HF_ALLOW_PATTERNS,
            local_files_only=True,
        )
        # PROD-006: Verify model integrity for cached weights
        if local_dir and _verify_model_integrity(repo_id, local_dir):
            msg = "Parakeet model already cached"
            log.info("[ASR_SETUP] %s", msg)
            if progress_callback:
                progress_callback(msg)
            return True
        else:
            log.warning("[ASR_SETUP] Cached model failed integrity check, re-downloading")
    except Exception:
        pass

    # PROD-005 (revised): Use the canonical disk space check from
    # transcription.py instead of the local _check_disk_space() duplicate.
    # The two implementations had different size tables and different
    # return semantics (bool vs raise RuntimeError), creating a
    # maintenance hazard. Now asr_setup delegates to the canonical version.
    # See FORENSIC_REVIEW_COMPLETE.md → PROD-005.
    try:
        from voice_typer.server.transcription import _check_disk_space_for_download
        _check_disk_space_for_download(repo_id, "parakeet")  # raises on insufficient space
    except RuntimeError as e:
        msg = str(e)
        log.error("[ASR_SETUP] %s", msg)
        if progress_callback:
            progress_callback(msg)
        return False
    except Exception as e:
        # If the canonical check can't be imported or fails unexpectedly,
        # fall back to the local _check_disk_space (which returns bool).
        log.debug("[ASR_SETUP] canonical disk space check unavailable, using local: %s", e)
        if not _check_disk_space(repo_id):
            msg = "Not enough disk space to download model"
            log.error("[ASR_SETUP] %s", msg)
            if progress_callback:
                progress_callback(msg)
            return False

    msg = "Downloading Parakeet TDT v3 model..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    # PROD-004 (revised): Use the canonical _download_with_retry from
    # transcription.py instead of the inline retry loop. The two
    # implementations had different delay tables ([5,15,45] vs 2**attempt)
    # and different API shapes (callable vs inline). Now asr_setup
    # delegates to the canonical version. See FORENSIC_REVIEW_COMPLETE.md
    # → PROD-004.
    try:
        from voice_typer.server.transcription import _download_with_retry
        local_dir = _download_with_retry(
            snapshot_download,
            max_attempts=_MAX_DOWNLOAD_RETRIES,
            delays=tuple(2 ** i for i in range(_MAX_DOWNLOAD_RETRIES)),  # keep exponential backoff
            repo_id=repo_id,
            revision=PARAKEET_REVISION,
            allow_patterns=_HF_ALLOW_PATTERNS,
            resume_download=True,
        )
    except Exception as e:
        log.error(
            "[ASR_SETUP] All %d download attempts failed. Last error: %s",
            _MAX_DOWNLOAD_RETRIES, e,
        )
        if progress_callback:
            progress_callback(f"Download failed after {_MAX_DOWNLOAD_RETRIES} attempts: {e}")
        return False

    # PROD-006: Verify model integrity after download
    if not _verify_model_integrity(repo_id, local_dir):
        log.error("[ASR_SETUP] Model integrity check failed after download")
        if progress_callback:
            progress_callback("Download completed but integrity check failed")
        return False
    msg = "Parakeet model download complete"
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)
    return True
