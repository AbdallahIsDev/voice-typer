"""Shared ASR utilities: GPU memory release, download retry, disk-space check, HF cache cleanup.

extracted from ``transcription.py`` to eliminate the DRY
violations catalogued in  finding #3 (``release_gpu_memory``
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
from typing import TYPE_CHECKING

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)


# Approximate model sizes (MB) for disk-space pre-check.
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
    # added turbo + distilled variants.
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
    # Parakeet TDT 0.6b v3 is ~2.5 GB uncompressed. Pre-fix the
    # ``"parakeet"`` key was missing and ``_MODEL_SIZE_MB.get("parakeet", 500)``
    # fell through to the 500 MB default, so the disk-space pre-check
    # required only ~1000 MB (500 + 500 margin) and false-passed with
    # ~1 GB free — causing the download to fail partway with a less-clear
    # ``download_retry_exhausted`` reason instead of a clear
    # ``disk_space_insufficient`` reason. Value matches
    # ``model_registry.ModelMetadata.download_size_mb`` for "parakeet"
    # so the pre-check and the UI's download-size display agree.
    "parakeet": 2500,
}
# Extra margin for temporary files, metadata, tokenizer, etc.
_DISK_SPACE_MARGIN_MB = 500


def release_gpu_memory() -> None:
    """Release GPU memory held by PyTorch's caching allocator.

    ``del model; gc.collect()`` releases the Python
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
    """Wrap snapshot_download() with exponential backoff retry.

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
                    "[DOWNLOAD] Download attempt %d/%d failed: %s. Retrying in %.0fs...",
                    attempt + 1,
                    max_attempts,
                    exc,
                    delay,
                )
                _time.sleep(delay)
            else:
                # log.exception preserves the traceback; keep max_attempts arg, drop exc.
                log.exception(
                    "[DOWNLOAD] All %d download attempts failed.",
                    max_attempts,
                )
    raise last_exc


def cleanup_hf_cache_dir(repo_id: str, log_prefix: str = "") -> None:
    """cache cleanup: best-effort delete a tampered HF cache dir.

    canonical version, extracted from
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
    """Check available disk space before model download.

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
            "[DISK] Disk space check passed: %d MB available, ~%d MB needed for '%s'",
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
        log.debug("[DISK] Disk space check skipped: %s", exc)


def _require_huggingface_consent(
    config,
    model_identifier: str,
    *,
    log_prefix: str = "[MODEL]",
    progress_message: str | None = None,
    progress_callback=None,
) -> None:
    """Raise :class:`ConsentRequiredError` if HuggingFace consent is not given.

    Single source of truth for the consent gate that previously drifted
    across three sites (``transcription._pre_download_model``,
    ``parakeet_engine.load``, ``service/model._require_huggingface_consent``).
    Each site had its own copy of the ``cfg = self.config; consent = False
    if cfg is None else getattr(cfg, 'huggingface_consent', False)`` block
    plus its own log-format string and progress-callback wording — making
    it easy for the consent gate to silently diverge (e.g. one site logs
    at WARNING, another at INFO; one surfaces a progress message, another
    doesn't). Centralizing the gate here ensures every download path
    applies the SAME GDPR Art. 6/13 safe-default (no consent → refuse to
    contact HuggingFace) and surfaces the SAME typed exception
    (``ConsentRequiredError``) so the IPC layer's ``isinstance``-check
    continues to map it to the consent-dialog command.

    Parameters
    ----------
    config : object or None
        The engine's config reference. ``None`` is treated as
        "consent not given" — safe default per GDPR Art. 6/13. This
        covers the degenerate / test-stub / benchmark paths where the
        engine is constructed without a Config.
    model_identifier : str
        Human-readable label for the model being downloaded
        (e.g. ``"small.en"`` for Whisper, ``"nvidia/parakeet-tdt-0.6b-v3"``
        for Parakeet). Used in the log warning, the progress message,
        AND the raised exception message so the user / operator can
        identify which download was blocked.
    log_prefix : str, optional
        Tag for the log message so each calling module's logs are
        identifiable (e.g. ``"[MODEL]"``, ``"[PARAKEET]"``). Defaults to
        ``"[MODEL]"``.
    progress_message : str, optional
        Custom progress-callback message. When ``None``, a default of
        ``"HuggingFace consent required before downloading <identifier>."``
        is used.
    progress_callback : callable, optional
        Optional ``progress_callback(str)`` to surface the consent
        requirement to the UI (e.g. the Models page progress bar).

    Raises
    ------
    ConsentRequiredError
        When ``config`` is ``None`` or
        ``config.huggingface_consent`` is not truthy.
    """
    cfg = config
    consent = False if cfg is None else bool(getattr(cfg, "huggingface_consent", False))
    if consent:
        return
    log.warning(
        "%s HuggingFace consent not given — refusing to download %s. The renderer should show a consent dialog.",
        log_prefix,
        model_identifier,
    )
    if progress_callback is not None:
        if progress_message is None:
            progress_message = f"HuggingFace consent required before downloading {model_identifier}."
        try:
            progress_callback(progress_message)
        except Exception:
            log.debug(
                "%s progress_callback raised while reporting consent requirement",
                log_prefix,
                exc_info=True,
            )
    from voice_typer.server.asr_errors import ConsentRequiredError

    raise ConsentRequiredError(f"HuggingFace consent not given — refusing to download {model_identifier}.")


# ─── Audio chunking ────────────────────────────────────────────────────────


def split_audio(
    audio: np.ndarray,
    chunk_duration: float,
    overlap_duration: float,
    sample_rate: int = WHISPER_SAMPLE_RATE,
) -> list[np.ndarray]:
    """Split a 1-D audio array into overlapping chunks.

    Single source of truth for the chunking loop previously duplicated
    verbatim across ``ParakeetEngine._split_audio`` (instance method) and
    ``QwenEngine._split_audio`` (``@staticmethod``). Both engine methods
    now delegate to this function; their original method signatures are
    preserved so existing call sites (``engine._split_audio(audio,
    chunk_sec, overlap_sec)``) and tests
    (``tests/test_parakeet_engine.py::TestSplitAudio``,
    ``tests/test_word_drop_regression.py::test_qwen_split_audio_covers_full_array``)
    keep passing unchanged.

    Parameters
    ----------
    audio : np.ndarray
        1-D audio samples (any dtype that supports slicing — the body
        only uses ``len()`` and ``audio[start:end]``).
    chunk_duration : float
        Target chunk length in seconds.
        ``chunk_len = int(chunk_duration * sample_rate)``.
    overlap_duration : float
        Overlap between successive chunks in seconds.
        ``overlap_len = int(overlap_duration * sample_rate)``.
        ``step = chunk_len - overlap_len``.
    sample_rate : int
        Sample rate in Hz. Defaults to :data:`WHISPER_SAMPLE_RATE`
        (16000) — the rate every ASR engine in this project resamples to
        before inference, so callers can usually omit it.

    Returns
    -------
    list[np.ndarray]
        Overlapping slices of ``audio``. Each slice is at most
        ``chunk_len`` samples long; the last slice is truncated to the
        remaining audio (may be shorter than ``chunk_len``). Returns a
        single chunk covering the whole array when
        ``len(audio) <= chunk_len``. Returns an empty list when
        ``len(audio) == 0``.

    Notes
    -----
    The loop terminates as soon as a chunk reaches the end of the audio
    (``end == len(audio)``), so the last chunk always contains the final
    sample of ``audio`` — no tail is silently dropped. This invariant is
    pinned by ``tests/test_word_drop_regression.py::test_qwen_split_audio_covers_full_array``.
    """
    chunk_len = int(chunk_duration * sample_rate)
    overlap_len = int(overlap_duration * sample_rate)
    step = chunk_len - overlap_len
    chunks: list[np.ndarray] = []
    start = 0
    while start < len(audio):
        end = min(start + chunk_len, len(audio))
        chunks.append(audio[start:end])
        if end == len(audio):
            break
        start += step
    return chunks
