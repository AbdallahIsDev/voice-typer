"""Shared ASR utilities: GPU memory release, download retry, disk-space check, HF cache cleanup.

EC-FIX-8: extracted from ``transcription.py`` to eliminate the DRY
violations catalogued in EC-17 finding #3 (``release_gpu_memory`` /
``_download_with_retry`` lived in ``transcription.py`` but were
imported by ``parakeet_engine`` and ``asr_setup`` — wrong module) and
finding #2 (``_cleanup_failed_cache`` was duplicated 3x across
``transcription.py``, ``asr_setup.py``, ``parakeet_engine.py``).

This module is the CANONICAL home for these helpers.  Existing
production callers (``transcription``, ``parakeet_engine``,
``asr_setup``, ``service``) now import from here.  The
``transcription.py`` module also re-exports these names
(``# noqa: F401``) for backward compatibility with tests that import
them from ``transcription``.

Design notes
------------
- Pure helpers (no module-level side effects, no global state) so any
  ASR engine can import them without coupling to the
  ``TranscriptionEngine`` class.
- All HuggingFace-related helpers lazily import
  ``voice_typer.server.config._config_dir`` inside the function body
  to avoid an import cycle (``config.py`` imports
  ``voice_typer.server._paths`` which imports other server modules).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


# PROD-004: Approximate model sizes (MB) for disk-space pre-check.
# These are the uncompressed sizes of the faster-whisper models.
_MODEL_SIZE_MB = {
    "tiny.en": 75,
    "tiny": 75,
    "base.en": 150,
    "base": 150,
    "small.en": 500,
    "small": 500,
    "medium.en": 1500,
    "medium": 1500,
    "large-v1": 3000,
    "large-v2": 3000,
    "large-v3": 3000,
    "large": 3000,
    # NEW-MODEL-001: added turbo + distilled variants.
    # ``large-v3-turbo`` (a.k.a. "turbo") is the fast multilingual model
    # released by OpenAI in 2024 — near-large-v3 accuracy at ~8x speed.
    # ``distil-large-v3`` and ``distil-medium.en`` are distilled variants
    # from the Distil-Whisper project: smaller, faster, slightly lower
    # accuracy.  See ``voice_typer/server/model_registry.py`` for full
    # metadata (VRAM, supported languages, repo IDs, speed ratings).
    "large-v3-turbo": 809,
    "turbo": 809,  # alias for large-v3-turbo
    "distil-large-v3": 1500,
    "distil-medium.en": 780,
}
# Extra margin for temporary files, metadata, tokenizer, etc.
_DISK_SPACE_MARGIN_MB = 500


def release_gpu_memory() -> None:
    """Release GPU memory held by PyTorch's caching allocator.

    NEW-MEM-001: ``del model; gc.collect()`` releases the Python
    references to the model but PyTorch's CUDA caching allocator
    retains the freed blocks for reuse by the same process.  After a
    backend switch (e.g. Whisper → Parakeet → Whisper), the cached
    blocks from the previous model are never reused (different model
    architecture), so they accumulate.  On RTX 3060/4060 (8–12 GB
    VRAM), 2 backend switches can OOM.

    This helper calls ``torch.cuda.empty_cache()`` to release the
    cached blocks back to the OS, making VRAM available for the next
    backend.  Safe to call when:

    - torch is not installed (no-op, debug-logged)
    - CUDA is not initialized (no-op, returns silently)
    - the current device is CPU (no-op)

    Designed to be called from every ASR engine's ``unload()`` and
    from every GPU→CPU fallback path in ``TranscriptionEngine``.
    """
    try:
        import torch
    except ImportError:
        # torch not installed — nothing to release.
        return
    try:
        if not torch.cuda.is_available():
            return
        # Synchronize before empty_cache so pending async kernels
        # finish and release their allocations.
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        log.debug("[GPU] torch.cuda.empty_cache() called after model unload")
    except Exception as exc:
        # CUDA not initialized, or some other runtime issue — log
        # at debug so we don't spam the log on every unload.
        log.debug("[GPU] torch.cuda.empty_cache() failed: %s", exc)


def _download_with_retry(
    download_fn,
    *,
    max_attempts: int = 3,
    delays: tuple[float, ...] = (5.0, 15.0, 45.0),
    **kwargs,
) -> str:
    """PROD-004: Wrap snapshot_download() with exponential backoff retry.

    Downloads can fail due to transient network issues, HuggingFace
    rate limits, or CDN timeouts.  Retrying with increasing delays
    gives the network time to recover and avoids failing the entire
    model load on a single transient error.

    Parameters
    ----------
    download_fn : callable
        The ``snapshot_download`` function (or a wrapper).
    max_attempts : int
        Maximum number of download attempts.
    delays : tuple[float, ...]
        Delay in seconds before each retry.  The first attempt has no
        delay; ``delays[i]`` is the delay before attempt ``i+1``.
    **kwargs
        Forwarded to ``download_fn``.

    Returns
    -------
    str
        The path to the downloaded model directory.

    Raises
    ------
    Exception
        The last exception if all attempts fail.
    """
    import time as _time

    last_exc: BaseException = RuntimeError("no transcription attempts made")
    for attempt in range(max_attempts):
        try:
            return download_fn(**kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                delay = delays[attempt] if attempt < len(delays) else delays[-1]
                log.warning(
                    "[PROD-004] Download attempt %d/%d failed: %s. Retrying in %.0fs...",
                    attempt + 1,
                    max_attempts,
                    exc,
                    delay,
                )
                _time.sleep(delay)
            else:
                # CR-41: log.exception preserves the traceback; keep max_attempts arg, drop exc.
                log.exception(
                    "[PROD-004] All %d download attempts failed.",
                    max_attempts,
                )
    raise last_exc


def cleanup_hf_cache_dir(repo_id: str, log_prefix: str = "") -> None:
    """G4-CR-06 / cache cleanup: best-effort delete a tampered HF cache dir.

    EC-FIX-8: canonical version, extracted from
    ``transcription.py::_cleanup_failed_whisper_cache``.  The local
    cleanup helpers in ``asr_setup._cleanup_failed_cache`` and
    ``parakeet_engine._cleanup_hf_cache_dir`` now delegate to this
    function (single source of truth — previously the same logic was
    duplicated 3x across the three modules).

    Called from each ASR engine's pre-download / verify path when
    ``verify_model_integrity()`` returns False (either on the cache-hit
    path or after a fresh download).  Removes the
    ``models--<org>--<repo>`` directory under
    ``<config_dir>/huggingface/hub/`` so the next call doesn't
    re-discover the tampered snapshot.

    Best-effort: logs but does not raise if the cleanup itself fails
    (e.g. file is locked on Windows, permission denied on POSIX).  The
    integrity hard-fail (``raise RuntimeError`` / fall-through to
    re-download) is the security gate; this cleanup is just hygiene.

    Parameters
    ----------
    repo_id : str
        HuggingFace repository identifier (e.g.
        ``"Systran/faster-whisper-small.en"`` or
        ``"nvidia/parakeet-tdt-0.6b-v3"``).
    log_prefix : str
        Prefix tag for log messages so each calling module's logs are
        identifiable (e.g. ``"[MODEL]"``, ``"[PARAKEET]"``,
        ``"[ASR_SETUP]"``).  Defaults to ``""`` (no prefix).  A
        trailing space is added automatically when the prefix is
        non-empty.
    """
    import shutil

    try:
        from voice_typer.server.config import _config_dir

        cache_root = _config_dir() / "huggingface" / "hub"
    except Exception as exc:
        log.debug(
            "%s could not resolve config dir for cache cleanup: %s",
            log_prefix,
            exc,
        )
        return

    model_dir = cache_root / f"models--{repo_id.replace('/', '--')}"
    if not model_dir.exists():
        return
    # Compose a tag like "[PARAKEET] " or "" (no leading space when empty).
    tag = f"{log_prefix} " if log_prefix else ""
    try:
        shutil.rmtree(model_dir)
        log.warning(
            "%sRemoved tampered HF cache directory %s after integrity check failure.",
            tag,
            model_dir,
        )
    except OSError as exc:
        log.warning(
            "%sCould not remove tampered HF cache directory %s: %s. Manual cleanup recommended.",
            tag,
            model_dir,
            exc,
        )


def _check_disk_space_for_download(repo_id: str, model_size: str) -> None:
    """PROD-005: Check available disk space before model download.

    Compares available space in the HuggingFace cache directory
    against the estimated model size with a 500 MB margin.
    Raises ``RuntimeError`` with a user-friendly message if
    insufficient space is detected.
    """
    import shutil

    try:
        # Determine the cache directory
        from huggingface_hub import constants

        cache_dir = constants.HF_HUB_CACHE
    except (ImportError, AttributeError):
        try:
            cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
        except Exception:
            return  # Can't determine cache dir, skip check

    try:
        usage = shutil.disk_usage(cache_dir)
        available_mb = usage.free // (1024 * 1024)
        estimated_mb = _MODEL_SIZE_MB.get(model_size, 500) + _DISK_SPACE_MARGIN_MB

        if available_mb < estimated_mb:
            raise RuntimeError(
                f"Insufficient disk space to download model '{model_size}'. "
                f"Available: {available_mb} MB, "
                f"Required (estimated): {estimated_mb} MB "
                f"(model ~{_MODEL_SIZE_MB.get(model_size, 500)} MB + "
                f"{_DISK_SPACE_MARGIN_MB} MB margin). "
                f"Free up disk space and try again."
            )
        log.debug(
            "[PROD-005] Disk space check passed: %d MB available, ~%d MB needed for '%s'",
            available_mb,
            estimated_mb,
            model_size,
        )
    except RuntimeError:
        raise
    except Exception as exc:
        # If we can't check disk space, don't block the download —
        # the download itself will fail with a clear error if space
        # runs out during the transfer.
        log.debug("[PROD-005] Disk space check skipped: %s", exc)
