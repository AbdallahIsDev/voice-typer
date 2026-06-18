"""Archived dead code from voice_typer/server/asr_setup.py.

ARCH-001: ``pip_install`` and ``download_weights`` were defined in
``asr_setup.py`` but never called from any production code path.  They
were kept here for reference in case the on-demand-dependency install
feature (UX-005) is implemented in the future.

**Do NOT import this module in production code.**  It exists only as
documentation of what the dead functions looked like before removal.
If you need this functionality, copy the relevant function back into
``asr_setup.py`` and add a production call site + tests.
"""

# The bodies below are verbatim copies of the removed functions.
# They are NOT executed and NOT imported.

import logging
import shutil
import subprocess
import sys
from typing import Callable, Optional

log = logging.getLogger(__name__)


def _archived_pip_install(
    packages: list[str],
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Install packages via pip. (ARCHIVED — was dead code in production.)

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


def _archived_download_weights(
    model_size: str = 'small.en',
    progress_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """Download model weights via huggingface_hub. (ARCHIVED — was dead code.)

    Args:
        model_size: The model size to download (e.g., 'small.en').
        progress_callback: Optional callable(message: str) for progress updates.

    Returns:
        True if download succeeded or weights already cached, False otherwise.
    """
    # NOTE: ``ensure_hf_env`` is still live in asr_setup.py; this
    # archived copy assumes it's available at import time.  When
    # reviving, import it explicitly: ``from voice_typer.server.asr_setup import ensure_hf_env``
    from voice_typer.server.asr_setup import ensure_hf_env, download_parakeet_weights
    ensure_hf_env()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log.error("[ASR_SETUP] huggingface_hub not available for weight download")
        if progress_callback:
            progress_callback("huggingface_hub not installed, cannot download weights")
        return False

    if model_size == "parakeet":
        return download_parakeet_weights(progress_callback)

    repo_id = f"Systran/faster-whisper-{model_size}"

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
