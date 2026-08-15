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

Phase 1c (PLAN_ONNX_INTEGRATION.md §5): this module is now also the
canonical home for the shared CUDA/OOM error classifiers
(:func:`is_cuda_error`, :func:`is_oom_error`) and the Parakeet
language-filter / chunk-merge helpers (:func:`is_likely_english`,
:func:`is_latin_char`, :func:`merge_chunks`, :func:`compute_overlap_skip`).
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
    # Parakeet TDT 0.6b v3 — ONNX fp16 export (visuall repo, 2026-08-15)
    # is ~1.28 GB uncompressed (the engine is ONNX-only post-migration;
    # the old torch/safetensors 2.5 GB estimate is obsolete). Pre-fix the
    # ``"parakeet"`` key was missing and ``_MODEL_SIZE_MB.get("parakeet", 500)``
    # fell through to the 500 MB default, so the disk-space pre-check
    # required only ~1000 MB (500 + 500 margin) and false-passed with
    # ~1 GB free — causing the download to fail partway with a less-clear
    # ``download_retry_exhausted`` reason instead of a clear
    # ``disk_space_insufficient`` reason. Value matches
    # ``model_registry.ModelMetadata.download_size_mb`` for "parakeet"
    # so the pre-check and the UI's download-size display agree.
    "parakeet": 1275,
}
# Extra margin for temporary files, metadata, tokenizer, etc.
_DISK_SPACE_MARGIN_MB = 500


def release_gpu_memory() -> None:
    """No-op for ONNX Runtime — kept for API compatibility.

    Historically this helper called ``torch.cuda.empty_cache()`` to
    release PyTorch's CUDA caching-allocator blocks after an engine
    ``unload()`` (NEW-MEM-001). After the ONNX Runtime migration
    (PLAN_ONNX_INTEGRATION.md §5.2), torch is no longer a project
    dependency and ONNX Runtime has **no** ``empty_cache()`` API —
    the CUDA arena is freed automatically when the
    ``ort.InferenceSession`` is destroyed (i.e. when the engine drops
    its session reference and ``gc.collect()`` runs).

    The function is preserved as a no-op so existing callers in
    ``TranscriptionEngine.unload()``, ``ParakeetEngine.unload()``,
    ``QwenEngine.unload()``, and the deferred-GC path
    (``TranscriptionEngine._run_deferred_gc``) continue to compile and
    call it without modification. Tests that ``patch(...)`` the
    function still see the call — the patched mock replaces the no-op.

    After total torch removal (Phase 1d), this function can be deleted
    and callers updated to drop the call entirely.
    """
    # Intentionally a no-op. ORT's CUDA arena is released on session
    # destroy; the caller's ``del self._session; gc.collect()`` is the
    # equivalent of ``del model; gc.collect(); torch.cuda.empty_cache()``.
    log.debug(
        "[GPU] release_gpu_memory() is a no-op for ONNX Runtime "
        "(ORT frees the CUDA arena on session destroy)"
    )


# ─── CUDA / OOM error classifiers (PLAN_ONNX_INTEGRATION.md §5.1) ───────


def is_cuda_error(exc: Exception) -> bool:
    """Return ``True`` if *exc* looks like a GPU/CUDA runtime failure.

    A 4-layer classifier preserved from the original
    ``TranscriptionEngine._is_gpu_runtime_error`` body (pre-torch-removal).
    The plan (PLAN_ONNX_INTEGRATION.md §5.1) explicitly forbids collapsing
    this to a 4-keyword frozenset — the layered structure is what
    distinguishes a true CUDA OOM from a CPU RAM exhaustion, a ROCm
    driver mismatch, or a Windows DLL-load failure.

    Layers (in evaluation order — first match wins):

    1. **ORT CUDA exceptions** (replaces the old
       ``isinstance(exc, torch.cuda.OutOfMemoryError)`` check that died
       with torch). ``onnxruntime.RuntimeException`` whose message
       contains ``"cuda"`` or ``"gpu"`` is a CUDA-side failure.
    2. **RuntimeError + attribute check.** Some libraries
       (``ctranslate2``, newer ``torch`` if installed) attach a
       structured ``.cuda_error`` attribute to a generic
       ``RuntimeError`` rather than raising a typed subclass.
    3. **Keyword match on the exception message** — 3 keywords
       (``"cuda"``, ``"cublas"``, ``"cudnn"``). OOM is handled
       separately by :func:`is_oom_error` so a CPU RAM exhaustion
       (``"out of memory"``) does not false-positive as a CUDA error.
    4. **DLL-load failures** (Windows) — 4 keywords
       (``"dll"``, ``"not found"``, ``"cannot be loaded"``,
       ``"load library"``). Critical for detecting missing CUDA
       Toolkit / cuDNN DLLs on Windows where ``onnxruntime-gpu`` is
       installed but the system CUDA Toolkit is not.

    Parameters
    ----------
    exc : Exception
        The exception to classify. Accepts any ``BaseException`` but
        the type hint is ``Exception`` for call-site ergonomics.

    Returns
    -------
    bool
        ``True`` if *exc* matches any of the 4 CUDA/GPU layers.
        ``False`` otherwise.
    """
    # Layer 1: ORT CUDA exceptions (replaces torch.cuda.OutOfMemoryError).
    try:
        import onnxruntime as ort

        # ``onnxruntime.RuntimeException`` no longer exists on the public
        # API (1.28+ only re-exports ``import_capi_exception``) — the
        # pybind11 exception classes live under
        # ``onnxruntime.capi.onnxruntime_pybind11_state``. Accept either
        # location (type-guarded so a mock that auto-magics the public
        # attribute falls through to the real path).
        _ort_exc = getattr(ort, "RuntimeException", None)
        if not isinstance(_ort_exc, type):
            _ort_exc = ort.capi.onnxruntime_pybind11_state.RuntimeException
        if isinstance(exc, _ort_exc):
            msg = str(exc).lower()
            if "cuda" in msg or "gpu" in msg:
                return True
    except (ImportError, AttributeError, TypeError):
        # ORT missing, its internal exception module unavailable on this
        # build, or a mock exposing no real exception class — fall
        # through to the attribute/keyword layers below.
        pass

    # Layer 2: RuntimeError + attribute check.
    if isinstance(exc, RuntimeError) and (
        getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False)
    ):
        return True

    # Layer 3: keyword match on the message (3 keywords — no "out of memory").
    err_str = str(exc).lower()
    if any(kw in err_str for kw in ("cuda", "cublas", "cudnn")):
        return True

    # Layer 4: DLL-load failures (Windows).
    return any(kw in err_str for kw in ("dll", "not found", "cannot be loaded", "load library"))


def is_oom_error(exc: Exception) -> bool:
    """Return ``True`` if *exc* is an out-of-memory error.

    Separate from :func:`is_cuda_error` (PLAN_ONNX_INTEGRATION.md §5.1)
    because ``"out of memory"`` alone is too broad — it matches CPU RAM
    exhaustion (e.g. ``MemoryError`` from a huge numpy allocation) which
    is NOT a CUDA error and should NOT trigger the GPU→CPU fallback
    path. The Parakeet engine's separate OOM check
    (``parakeet_engine.py:955``) relies on this distinction.

    Matches:
      - ``"out of memory"`` (case-insensitive substring)
      - ``"oom"`` (case-insensitive substring)

    Parameters
    ----------
    exc : Exception
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if the exception message contains an OOM marker.
    """
    err_str = str(exc).lower()
    return "out of memory" in err_str or "oom" in err_str


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


# ─── Language filter + chunk merge (moved from parakeet_engine.py) ──────
#
# PLAN_ONNX_INTEGRATION.md §5.3 (``is_likely_english`` / ``is_latin_char``)
# and §5.4 (``merge_chunks`` / ``compute_overlap_skip``). Moved here so the
# rewritten ONNX Parakeet engine and any future ONNX variant can import
# them directly from the shared ASR utilities module. The originals at
# ``parakeet_engine.py:47-78`` (language filter) and
# ``parakeet_engine.py:1023-1133`` (chunk merge) are kept as thin
# delegators for backward compatibility with tests that import them from
# ``parakeet_engine`` — see ``tests/test_parakeet_engine.py``.

# Maximum allowed ratio of non-Latin-script characters before we reject
# a transcription segment as a language-hallucination.
# The Parakeet model is English-only; output with >30% non-Latin characters
# is almost certainly a decoding error, not valid speech.
NON_LATIN_RATIO_LIMIT = 0.30


def is_latin_char(ch: str) -> bool:
    """Return ``True`` if *ch* belongs to the Latin script (or is whitespace/digit/punct).

    Moved verbatim from ``parakeet_engine._is_latin_char`` (PLAN_ONNX_INTEGRATION.md §5.3)
    so the rewritten ONNX Parakeet engine can import it from this shared
    module. The leading underscore was dropped because the function is
    now part of the public ASR utility surface (the old private name
    remains as a backward-compat alias in ``parakeet_engine``).

    The check is Unicode-category based: punctuation (``P*``), separators
    (``Z*``), symbols (``S*``), and digits are all treated as "Latin" so
    that legitimate English transcriptions containing punctuation, digits,
    or whitespace are not false-positive rejected by :func:`is_likely_english`.

    Parameters
    ----------
    ch : str
        A single character. If empty, returns ``False``.

    Returns
    -------
    bool
        ``True`` if *ch* is in the Latin script or a non-letter category
        (punctuation, separator, symbol, digit).
    """
    import unicodedata

    cat = unicodedata.category(ch)
    if cat.startswith("P") or cat.startswith("Z") or cat.startswith("S"):
        return True
    if ch.isdigit():
        return True
    script = unicodedata.name(ch, "").split(" ")[0] if ch else ""
    return script == "LATIN"


def is_likely_english(text: str) -> bool:
    """Return ``False`` if *text* contains too many non-Latin-script characters.

    Moved verbatim from ``parakeet_engine._is_likely_english``
    (PLAN_ONNX_INTEGRATION.md §5.3). The Parakeet model is English-only
    but sometimes hallucinates text in unrelated scripts (CJK, Arabic,
    Devanagari, etc.). This filter rejects those segments rather than
    pasting garbled text into the user's field.

    The hallucination is logged via :func:`log_hallucination_rejection`
    (PII-safe) when the ratio exceeds :data:`NON_LATIN_RATIO_LIMIT`.

    Parameters
    ----------
    text : str
        The transcription text to filter. Empty / whitespace-only text
        is treated as "likely English" (returns ``True``) so the caller's
        ``if not is_likely_english(text): return ""`` branch does not
        false-positive on silence.

    Returns
    -------
    bool
        ``True`` if the non-Latin ratio is at or below
        :data:`NON_LATIN_RATIO_LIMIT`. ``False`` if it exceeds the limit.
    """
    if not text or not text.strip():
        return True
    non_latin = sum(1 for ch in text if not is_latin_char(ch))
    ratio = non_latin / len(text)
    if ratio > NON_LATIN_RATIO_LIMIT:
        # Lazy import to avoid a circular dependency at module load time
        # (hallucination.py imports from asr_utils's neighbors).
        from voice_typer.server.hallucination import log_hallucination_rejection

        # Use PII-safe logging helper for hallucination text.
        log_hallucination_rejection(
            "[PARAKEET]",
            text,
            reason=f"non-English output ({ratio * 100:.0f}% non-Latin chars)",
            log_transcriptions=False,
        )
        return False
    return True


# Maximum number of leading words of the new chunk that the merge
# algorithm will skip when a true overlap duplicate is detected. Caps the
# skip so a long spurious match does not drop legitimate words.
# Original: ``parakeet_engine._MAX_BOUNDARY_SKIP_WORDS``.
MAX_BOUNDARY_SKIP_WORDS = 2
# Number of trailing words of the previous chunk to compare against the
# leading words of the new chunk when detecting true overlap duplicates.
# Original: ``parakeet_engine._OVERLAP_DEDUP_WINDOW``.
OVERLAP_DEDUP_WINDOW = 3


def compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
    """Return how many leading words of *new_words* to skip.

    Moved verbatim from ``parakeet_engine.ParakeetEngine._compute_overlap_skip``
    (PLAN_ONNX_INTEGRATION.md §5.4). The function is a ``@staticmethod``
    in the original; here it is a module-level function with the same
    signature and body.

    We detect a true overlap duplicate by searching (case-insensitively,
    ignoring punctuation) for the leading run of ``new_words`` as a
    *contiguous subsequence* within the trailing window of
    ``prev_words``. We pick the longest match that fits within
    :data:`OVERLAP_DEDUP_WINDOW` words on the new side, is at most
    :data:`MAX_BOUNDARY_SKIP_WORDS` long, and ends within the trailing
    ``OVERLAP_DEDUP_WINDOW + MAX_BOUNDARY_SKIP_WORDS`` words of the
    previous chunk. If no match is found, return 0 (do not drop
    legitimate words).

    Parameters
    ----------
    prev_words : list[str]
        The accumulated word list from the previous chunk(s). ``[]`` is
        valid (returns 0 — first chunk has no overlap).
    new_words : list[str]
        The word list of the new chunk to merge. ``[]`` is valid
        (returns 0).

    Returns
    -------
    int
        The number of leading words of *new_words* to skip before
        appending to *prev_words*. Always ``<= MAX_BOUNDARY_SKIP_WORDS``
        and ``<= len(new_words)``.
    """
    if not prev_words or not new_words:
        return 0

    def _norm(w: str) -> str:
        return w.strip(".,;:!?\"'()[]{}").lower()

    prev_window_size = OVERLAP_DEDUP_WINDOW + MAX_BOUNDARY_SKIP_WORDS
    prev_tail = [_norm(w) for w in prev_words[-prev_window_size:]]
    max_check = min(
        MAX_BOUNDARY_SKIP_WORDS,
        len(new_words),
    )
    new_head = [_norm(w) for w in new_words[:max_check]]

    best = 0
    for length in range(max_check, 0, -1):
        candidate = new_head[:length]
        for start in range(len(prev_tail) - length + 1):
            end_idx = start + length
            last_word_idx = len(prev_tail) - end_idx
            if last_word_idx >= OVERLAP_DEDUP_WINDOW:
                continue
            if prev_tail[start : start + length] == candidate:
                best = length
                break
        if best > 0:
            break

    if best > 0:
        return best

    return 0


def merge_chunks(texts: list[str]) -> str:
    """Concatenate chunk transcriptions, skipping overlap text.

    Moved verbatim from ``parakeet_engine.ParakeetEngine._merge_chunks``
    (PLAN_ONNX_INTEGRATION.md §5.4). The function is an instance method
    in the original (but ``self`` is unused in the body); here it is a
    module-level function with the same body.

    Chunks have ``_CHUNK_OVERLAP_SECONDS`` of overlapping audio at each
    boundary. When the model re-transcribes the overlap region in the
    new chunk, those leading words duplicate the previous chunk's tail
    and must be skipped.

    Parameters
    ----------
    texts : list[str]
        The per-chunk transcription texts, in chunk order. ``[]``
        returns ``""``. A single-element list returns ``texts[0]``.

    Returns
    -------
    str
        The merged transcription with overlap duplicates skipped.
        Whitespace is normalized to single spaces and stripped at the
        ends.
    """
    if len(texts) <= 1:
        return texts[0] if texts else ""

    result_words: list[str] = texts[0].split()
    for text in texts[1:]:
        words = text.split()
        if not words:
            continue

        skip = compute_overlap_skip(result_words, words)
        tail = words[skip:] if skip > 0 else words
        if tail:
            result_words.extend(tail)
    return " ".join(result_words).strip()


__all__ = [
    "MAX_BOUNDARY_SKIP_WORDS",
    "NON_LATIN_RATIO_LIMIT",
    "OVERLAP_DEDUP_WINDOW",
    "_check_disk_space_for_download",
    "_download_with_retry",
    "_require_huggingface_consent",
    "cleanup_hf_cache_dir",
    "compute_overlap_skip",
    "is_cuda_error",
    "is_latin_char",
    "is_likely_english",
    "is_oom_error",
    "merge_chunks",
    "release_gpu_memory",
    "split_audio",
]
