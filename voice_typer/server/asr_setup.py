"""ASR auto-setup: GPU detection, dependency check, weight download.

This module provides utilities for automatically setting up the ASR
environment, including GPU detection, dependency checking, and model
weight downloading.

ARCH-001: ``pip_install`` and ``download_weights`` were removed from
this module.  The verbatim bodies were previously retained in
``archive/asr_setup_dead_code.py`` for reference; that archive file
has been deleted as part of dead-code cleanup since zero production
call sites referenced it.  The historical implementation can be
recovered from git history if needed for the future UX-005 on-demand
dependency install feature.

NEW-PAUSE-001: pause/resume flag for in-progress model downloads.
``set_download_paused(True)`` causes the polling loop in
:meth:`voice_typer.server.service.VoiceTyperService.download_model`
to freeze its progress reporting (and effectively stop user-visible
progress) until ``set_download_paused(False)`` is called.  The flag
is checked "between chunks" — i.e. once per 1-second poll iteration
in the service's polling loop.  The flag is module-level so the IPC
handler can set it from any thread.

Lifecycle:
  - :func:`reset_download_pause_state` — call at start of download
    (creates a fresh ``threading.Event``).
  - :func:`set_download_paused` — set/clear the pause flag.
  - :func:`is_download_paused` — check the flag (called by polling loop).
  - :func:`wait_while_paused` — block while paused (called by polling loop).
  - :func:`clear_download_pause_state` — call at end of download
    (sets the Event back to ``None`` so subsequent pause calls return
    ``False``).
"""

import logging
import os
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


# ── NEW-PAUSE-001: pause/resume flag ────────────────────────────────
#
# A single module-level ``threading.Event`` controls the pause state
# for ALL in-progress downloads.  We support only one concurrent
# download at a time (the existing ``_download_cancel_event`` in
# VoiceTyperService has the same constraint), so a single flag is
# sufficient.
#
# Semantics:
# - ``_download_pause_event`` is created lazily by
#   ``reset_download_pause_state()`` at the start of a download.
# - ``set_download_paused(True)``  -> ``_download_pause_event.set()``
# - ``set_download_paused(False)`` -> ``_download_pause_event.clear()``
# - ``is_download_paused()``       -> ``_download_pause_event.is_set()``
# - When no download is in progress, ``_download_pause_event`` is
#   ``None`` and ``is_download_paused()`` returns ``False``.
_download_pause_event: threading.Event | None = None
_download_pause_lock = threading.Lock()


def reset_download_pause_state() -> None:
    """Initialize the pause flag at the start of a download.

    Called by :meth:`VoiceTyperService.download_model` when a new
    download begins (so a stale ``paused=True`` from a previous
    download doesn't carry over).  Creates a fresh ``threading.Event``
    in the cleared (not-paused) state.  Safe to call from any thread.
    """
    global _download_pause_event
    with _download_pause_lock:
        _download_pause_event = threading.Event()
        # Starts cleared (not paused).


def clear_download_pause_state() -> None:
    """Clear the pause flag at the end of a download.

    Sets ``_download_pause_event`` back to ``None`` so subsequent
    calls to :func:`set_download_paused` return ``False`` (no active
    download to pause).  Called from every cleanup path in
    :meth:`VoiceTyperService.download_model` (success, failure, cancel).
    """
    global _download_pause_event
    with _download_pause_lock:
        _download_pause_event = None


def set_download_paused(paused: bool) -> bool:
    """Set or clear the pause flag.

    Returns ``True`` if the flag was successfully updated, ``False``
    if no download is currently in progress (in which case there's
    nothing to pause).  The renderer treats ``False`` as "no-op" —
    e.g. pressing Pause when nothing is downloading just dismisses
    the button.
    """
    global _download_pause_event
    with _download_pause_lock:
        if _download_pause_event is None:
            log.debug("[PAUSE] set_download_paused(%s) called with no active download", paused)
            return False
        if paused:
            _download_pause_event.set()
            log.info("[PAUSE] Model download pause requested")
        else:
            _download_pause_event.clear()
            log.info("[PAUSE] Model download resume requested")
    return True


def is_download_paused() -> bool:
    """Return ``True`` if the current download is paused.

    Returns ``False`` when no download is in progress (so callers
    can use this as a simple ``if is_download_paused(): ...`` guard
    without checking for ``None`` first).
    """
    with _download_pause_lock:
        if _download_pause_event is None:
            return False
        return _download_pause_event.is_set()


def wait_while_paused(timeout_s: float = 1.0) -> bool:
    """Block while the download is paused.

    Used by the service polling loop between progress updates.  Returns
    ``True`` if the pause flag was cleared within ``timeout_s`` seconds,
    ``False`` if it's still paused after the timeout (in which case the
    caller should loop and call again, or check cancellation).

    Safe to call when no download is in progress — returns immediately.
    """
    with _download_pause_lock:
        ev = _download_pause_event
    if ev is None:
        return True
    # If not paused, return immediately.
    if not ev.is_set():
        return True
    # Wait for the pause to be cleared (or timeout).
    return ev.wait(timeout=timeout_s)


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

# PROD-005: the local ``_check_disk_space`` and ``_ESTIMATED_MODEL_SIZES``
# duplicate was REMOVED. The canonical disk-space check lives in
# ``transcription.py::_check_disk_space_for_download`` (raises RuntimeError
# on insufficient space). ``asr_setup.py`` delegates to it (see
# ``download_parakeet_weights`` below). If the canonical import fails, we
# log the error and proceed — the model download will fail naturally if
# there's truly no space, which is a safer failure mode than running a
# second, divergent size table that could drift out of sync.


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
    progress_callback: Callable[[str], None] | None = None,
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
    parakeet_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

    msg = "Checking Parakeet model cache..."
    log.info("[ASR_SETUP] %s", msg)
    if progress_callback:
        progress_callback(msg)

    try:
        local_dir = snapshot_download(
            repo_id=repo_id,
            revision=parakeet_revision,
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
        # PROD-005: If the canonical check can't be imported, log and
        # proceed. The model download itself will fail naturally if
        # there's truly no space — a safer failure mode than running a
        # divergent local size table. Pre-fix this fell back to a local
        # ``_check_disk_space`` duplicate that had different size
        # thresholds and could drift out of sync with the canonical
        # version.
        log.debug("[ASR_SETUP] canonical disk space check unavailable, proceeding: %s", e)

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
            revision=parakeet_revision,
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
