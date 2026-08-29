"""Local fallback helpers + backward-compat helper aliases.

The defensive ``asr_utils`` import block and the ``_local_*``
fallback implementations moved here verbatim; consuming leaves
import the resolved names from this module."""

from __future__ import annotations

import logging

from voice_typer.server.hallucination import log_hallucination_rejection

# Shared ASR helpers (PLAN_ONNX_INTEGRATION.md §5.1, §5.3, §5.4). These
# were moved to asr_utils as part of the ONNX migration; the engine
# imports them directly. ``asr_utils`` is owned by Sub-agent 3 — the
# signatures are documented in the plan; we code against them.
#
# DEFENSIVE IMPORT: in the parallel-refactor window, Sub-agent 3 may not
# have landed the asr_utils helpers yet. The try/except falls back to
# local implementations (mirroring the pre-migration bodies verbatim)
# so this module imports cleanly either way. Once Sub-agent 3 lands,
# the canonical asr_utils versions are used automatically. The local
# fallbacks are NOT a long-term contract — they exist solely to keep
# the ONNX-rewritten parakeet_engine importable during the refactor
# window. New code should import from asr_utils directly.
try:
    from voice_typer.server.asr_utils import (
        NON_LATIN_RATIO_LIMIT as _ASR_NON_LATIN_RATIO_LIMIT,
        compute_overlap_skip as _asr_compute_overlap_skip,
        is_cuda_error as _asr_is_cuda_error,
        is_latin_char as _asr_is_latin_char,
        is_likely_english as _asr_is_likely_english,
        merge_chunks as _asr_merge_chunks,
    )

    _ASR_UTILS_HELPERS_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive fallback during parallel refactor
    _ASR_UTILS_HELPERS_AVAILABLE = False
    _asr_is_cuda_error = None  # type: ignore[assignment]
    _asr_is_latin_char = None  # type: ignore[assignment]
    _asr_is_likely_english = None  # type: ignore[assignment]
    _asr_merge_chunks = None  # type: ignore[assignment]
    _asr_compute_overlap_skip = None  # type: ignore[assignment]
    _ASR_NON_LATIN_RATIO_LIMIT = None  # type: ignore[assignment]

from ._constants import (
    _MAX_BOUNDARY_SKIP_WORDS,
    _NON_LATIN_RATIO_LIMIT,
    _OVERLAP_DEDUP_WINDOW,
)

log = logging.getLogger(__name__)


# ─── Local fallback implementations (used only if asr_utils lacks the
#     helpers — see DEFENSIVE IMPORT note above). Mirror the pre-
#     migration bodies verbatim so behavior is identical. These are
#     NOT the canonical home — asr_utils is. ─────────────────────────


def _local_is_latin_char(ch: str) -> bool:
    """Return True if *ch* belongs to the Latin script (or is ws/digit/punct)."""
    import unicodedata

    cat = unicodedata.category(ch)
    if cat.startswith("P") or cat.startswith("Z") or cat.startswith("S"):
        return True
    if ch.isdigit():
        return True
    script = unicodedata.name(ch, "").split(" ")[0] if ch else ""
    return script == "LATIN"


def _local_is_likely_english(text: str) -> bool:
    """Return False if *text* contains too many non-Latin-script characters."""
    if not text or not text.strip():
        return True
    non_latin = sum(1 for ch in text if not _local_is_latin_char(ch))
    ratio = non_latin / len(text)
    if ratio > _NON_LATIN_RATIO_LIMIT:
        log_hallucination_rejection(
            "[PARAKEET]",
            text,
            reason=f"non-English output ({ratio * 100:.0f}% non-Latin chars)",
            log_transcriptions=False,
        )
        return False
    return True


def _local_is_cuda_error(exc: Exception) -> bool:
    """Conservative CUDA-error classifier (5-layer, mirrors asr_utils)."""
    err_str = str(exc).lower()
    # Layer 1: ORT RuntimeException with cuda/gpu in message.
    try:
        import onnxruntime as _ort  # type: ignore[import-untyped]

        if isinstance(exc, _ort.RuntimeException) and ("cuda" in err_str or "gpu" in err_str):
            return True
    except ImportError:
        pass
    # Layer 2: RuntimeError + attribute check.
    if isinstance(exc, RuntimeError) and (getattr(exc, "cuda_error", None) or getattr(exc, "is_cuda_error", False)):
        return True
    # Layer 3: keyword match (3 keywords — OOM handled separately).
    if any(kw in err_str for kw in ("cuda", "cublas", "cudnn")):
        return True
    # Layer 4: DLL-load failures (Windows).
    return any(kw in err_str for kw in ("dll", "not found", "cannot be loaded", "load library"))


def _local_compute_overlap_skip(prev_words: list[str], new_words: list[str]) -> int:
    """Return how many leading words of *new_words* to skip (overlap dedup)."""
    if not prev_words or not new_words:
        return 0

    def _norm(w: str) -> str:
        return w.strip(".,;:!?\"'()[]{}").lower()

    prev_window_size = _OVERLAP_DEDUP_WINDOW + _MAX_BOUNDARY_SKIP_WORDS
    prev_tail = [_norm(w) for w in prev_words[-prev_window_size:]]
    max_check = min(_MAX_BOUNDARY_SKIP_WORDS, len(new_words))
    new_head = [_norm(w) for w in new_words[:max_check]]

    best = 0
    for length in range(max_check, 0, -1):
        candidate = new_head[:length]
        for start in range(len(prev_tail) - length + 1):
            end_idx = start + length
            last_word_idx = len(prev_tail) - end_idx
            if last_word_idx >= _OVERLAP_DEDUP_WINDOW:
                continue
            if prev_tail[start : start + length] == candidate:
                best = length
                break
        if best > 0:
            break
    return best


def _local_merge_chunks(texts: list[str]) -> str:
    """Concatenate chunk transcriptions, skipping overlap text."""
    if len(texts) <= 1:
        return texts[0] if texts else ""
    result_words: list[str] = texts[0].split()
    for text in texts[1:]:
        words = text.split()
        if not words:
            continue
        skip = _local_compute_overlap_skip(result_words, words)
        tail = words[skip:] if skip > 0 else words
        if tail:
            result_words.extend(tail)
    return " ".join(result_words).strip()


# Resolve the effective helpers: prefer asr_utils, fall back to local.
_is_latin_char_impl = _asr_is_latin_char if _asr_is_latin_char is not None else _local_is_latin_char
_is_likely_english_impl = _asr_is_likely_english if _asr_is_likely_english is not None else _local_is_likely_english
_is_cuda_error_impl = _asr_is_cuda_error if _asr_is_cuda_error is not None else _local_is_cuda_error
_merge_chunks_impl = _asr_merge_chunks if _asr_merge_chunks is not None else _local_merge_chunks
_compute_overlap_skip_impl = (
    _asr_compute_overlap_skip if _asr_compute_overlap_skip is not None else _local_compute_overlap_skip
)


# ─── Backward-compat aliases for moved helpers ─────────────────────────
#
# PLAN_ONNX_INTEGRATION.md §5.3 / §5.4 moved ``_is_latin_char``,
# ``_is_likely_english``, ``_merge_chunks`` and ``_compute_overlap_skip``
# to :mod:`voice_typer.server.asr_utils`. The canonical implementations
# live there now; the leading-underscore names below are kept as
# backward-compat aliases so existing import sites
# (``tests/test_parakeet_engine.py``, ``tests/regressions/test_parakeet_merge.py``,
# ``tests/test_word_drop_regression.py``) keep working without a
# parallel test rewrite. New code should import from ``asr_utils``.

_is_latin_char = _is_latin_char_impl

# ─── ASCII fast path for the language filter ───────────────────────────
#
# The resolved :func:`is_likely_english` classifies characters one at a
# time (``sum(1 for ch in text if not is_latin_char(ch))``), and each
# classification costs two ``unicodedata`` calls. Transcription output
# is overwhelmingly pure ASCII, where the answer is computable in bulk:
# every ASCII code point satisfies ``is_latin_char`` (punctuation P*,
# separators Zs, symbols S*, digits via ``str.isdigit``, and letters
# whose ``unicodedata.name`` starts with "LATIN") EXCEPT the 33 Cc
# control characters (U+0000–U+001F, U+007F). So for ASCII text the
# non-Latin count is just the number of control characters, which
# ``str.translate`` counts at C speed. The threshold comparison below
# is the exact complement of the canonical ``ratio > LIMIT`` check (same
# operands → same float division → ``<=`` ≡ ``not >``), so the fast path
# returns ``True`` ONLY where the canonical implementation provably
# would; every other input (non-ASCII, empty, or ASCII above the limit)
# delegates to the unresolved canonical implementation, preserving its
# whitespace short-circuit and its hallucination-rejection logging
# verbatim.

# U+0000–U+001F plus DEL (U+007F) — the only ASCII code points whose
# ``unicodedata`` classification is not Latin/punct/symbol/separator.
_ASCII_NON_LATIN_TRANSLATION: dict[int, None] = {cp: None for cp in range(0x20)}
_ASCII_NON_LATIN_TRANSLATION[0x7F] = None

# The threshold the RESOLVED implementation compares against (they are
# the same value today; reading it from the same module keeps the fast
# path exact if the canonical constant ever moves).
_LIKELY_ENGLISH_RATIO_LIMIT = (
    _ASR_NON_LATIN_RATIO_LIMIT if _ASR_NON_LATIN_RATIO_LIMIT is not None else _NON_LATIN_RATIO_LIMIT
)


def _is_likely_english(text: str) -> bool:
    """Return False if *text* contains too many non-Latin-script characters.

    ASCII fast path in front of the resolved canonical implementation
    (see the ASCII fast-path block above): pure-ASCII text below the
    non-Latin ratio limit returns ``True`` without the per-character
    ``unicodedata`` loop; everything else — including every REJECT
    decision — goes through the canonical implementation unchanged so
    its threshold boundary and PII-safe rejection logging are preserved
    exactly.
    """
    if text and text.isascii():
        non_latin = len(text) - len(text.translate(_ASCII_NON_LATIN_TRANSLATION))
        if non_latin / len(text) <= _LIKELY_ENGLISH_RATIO_LIMIT:
            return True
    return _is_likely_english_impl(text)
