"""ASR utility helpers."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)


# Approximate model sizes (MB) for disk-space pre-check.
_MODEL_SIZE_MB = {
    "tiny": 75,
    # ``large-v3``: highest-accuracy multilingual Whisper (~3 GB).
    "large-v3": 3000,
    # ``large-v3-turbo`` is the fast multilingual model released by
    "large-v3-turbo": 809,
    # Parakeet TDT 0.6b v3 ONNX fp16 export is ~1.28 GB uncompressed
    "parakeet": 1275,
}
# Extra margin for temporary files, metadata, tokenizer, etc.
_DISK_SPACE_MARGIN_MB = 500


def release_gpu_memory() -> None:
    """No-op for ONNX Runtime, kept for API compatibility."""
    # Intentionally a no-op. ORT's CUDA arena is released on session
    log.debug("[GPU] release_gpu_memory() is a no-op for ONNX Runtime (ORT frees the CUDA arena on session destroy)")


def is_cuda_error(exc: Exception) -> bool:
    """Return ``True`` if *exc* looks like a GPU/CUDA runtime failure."""
    # Layer 1: ORT CUDA exceptions (replaces the typed CUDA OOM check).
    try:
        import onnxruntime as ort

        # ``onnxruntime.RuntimeException`` no longer exists on the public
        _ort_exc = getattr(ort, "RuntimeException", None)
        if not isinstance(_ort_exc, type):
            _ort_exc = ort.capi.onnxruntime_pybind11_state.RuntimeException
        if isinstance(exc, _ort_exc):
            msg = str(exc).lower()
            if "cuda" in msg or "gpu" in msg:
                return True
    except (ImportError, AttributeError, TypeError):
        # ORT missing, its internal exception module unavailable on this
        pass

    # Layer 2: RuntimeError + attribute check.
    if isinstance(exc, RuntimeError) and (getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False)):
        return True

    # Layer 3: keyword match on the message (3 keywords, no "out of memory").
    err_str = str(exc).lower()
    if any(kw in err_str for kw in ("cuda", "cublas", "cudnn")):
        return True

    # Layer 4: DLL-load failures (Windows).
    return any(kw in err_str for kw in ("dll", "not found", "cannot be loaded", "load library"))


def is_oom_error(exc: Exception) -> bool:
    """Return ``True`` if *exc* is an out-of-memory error."""
    err_str = str(exc).lower()
    return "out of memory" in err_str or "oom" in err_str


def _download_with_retry(
    download_fn,
    *,
    max_attempts: int = 3,
    delays: tuple[float, ...] = (5.0, 15.0, 45.0),
    **kwargs,
) -> str:
    """Wrap snapshot_download() with exponential backoff retry."""
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
    """cache cleanup: best-effort delete a tampered HF cache dir."""
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
    """Check available disk space before model download."""
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
            raise _disk_space_error(
                cache_dir,
                available_mb,
                estimated_mb,
                f"model '{model_size}'",
                detail=(f"model ~{_MODEL_SIZE_MB.get(model_size, 500)} MB + {_DISK_SPACE_MARGIN_MB} MB margin"),
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
        log.debug("[DISK] Disk space check skipped: %s", exc)


def _disk_space_error(
    directory: str,
    available_mb: int,
    required_mb: int,
    what: str,
    *,
    detail: str = "",
) -> RuntimeError:
    """Build the shared "insufficient disk space" error message."""
    detail_part = f" ({detail})" if detail else ""
    return RuntimeError(
        f"Insufficient disk space to download {what}. "
        f"Available: {available_mb} MB, Required: {required_mb} MB"
        f"{detail_part}. "
        f"Free up disk space and try again."
    )


def _require_huggingface_consent(
    config,
    model_identifier: str,
    *,
    log_prefix: str = "[MODEL]",
    progress_message: str | None = None,
    progress_callback=None,
) -> None:
    """Raise :class:`HuggingFaceConsentRequiredError` if HuggingFace consent is not given."""
    cfg = config
    consent = False if cfg is None else bool(getattr(cfg, "huggingface_consent", False))
    if consent:
        return
    log.warning(
        "%s HuggingFace consent not given, refusing to download %s. The renderer should show a consent dialog.",
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
    from voice_typer.server.asr_errors import HuggingFaceConsentRequiredError

    raise HuggingFaceConsentRequiredError(f"HuggingFace consent not given, refusing to download {model_identifier}.")


def split_audio(
    audio: np.ndarray,
    chunk_duration: float,
    overlap_duration: float,
    sample_rate: int = WHISPER_SAMPLE_RATE,
) -> list[np.ndarray]:
    """Split a 1-D audio array into overlapping chunks."""
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


# PLAN_ONNX_INTEGRATION.md §5.3 (``is_likely_english`` / ``is_latin_char``)

# Maximum allowed ratio of non-Latin-script characters before we reject
NON_LATIN_RATIO_LIMIT = 0.30


def is_latin_char(ch: str) -> bool:
    """Return ``True`` if *ch* belongs to the Latin script (or is whitespace/digit/punct)."""
    import unicodedata

    cat = unicodedata.category(ch)
    if cat.startswith("P") or cat.startswith("Z") or cat.startswith("S"):
        return True
    if ch.isdigit():
        return True
    script = unicodedata.name(ch, "").split(" ")[0] if ch else ""
    return script == "LATIN"


def is_likely_english(text: str) -> bool:
    """Return ``False`` if *text* contains too many non-Latin-script characters."""
    if not text or not text.strip():
        return True
    non_latin = sum(1 for ch in text if not is_latin_char(ch))
    ratio = non_latin / len(text)
    if ratio > NON_LATIN_RATIO_LIMIT:
        # Lazy import to avoid a circular dependency at module load time
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
MAX_BOUNDARY_SKIP_WORDS = 2
# Number of trailing words of the previous chunk to compare against the
OVERLAP_DEDUP_WINDOW = 3


def compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
    """Return how many leading words of *new_words* to skip."""
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
    """Concatenate chunk transcriptions, skipping overlap text."""
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
    "_disk_space_error",
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
