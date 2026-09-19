"""Backward-compat helper aliases over the canonical ``asr_utils`` helpers."""

from __future__ import annotations

from voice_typer.server.asr_utils import (
    NON_LATIN_RATIO_LIMIT,
    compute_overlap_skip as _compute_overlap_skip_impl,  # noqa: F401, re-exported alias (see note below)
    is_cuda_error as _is_cuda_error_impl,  # noqa: F401, re-exported alias (see note below)
    is_latin_char as _is_latin_char,  # noqa: F401, re-exported alias (see note below)
    is_likely_english as _is_likely_english_impl,
    merge_chunks as _merge_chunks_impl,  # noqa: F401, re-exported alias (see note below)
)

# resolved from the canonical module. The ``noqa: F401`` re-export
_ASR_UTILS_HELPERS_AVAILABLE = True

# PLAN_ONNX_INTEGRATION.md §5.3 / §5.4 moved ``_is_latin_char``,

# The resolved :func:`asr_utils.is_likely_english` classifies characters

# U+0000–U+001F plus DEL (U+007F), the only ASCII code points whose
_ASCII_NON_LATIN_TRANSLATION: dict[int, None] = {cp: None for cp in range(0x20)}
_ASCII_NON_LATIN_TRANSLATION[0x7F] = None

# The threshold the canonical implementation compares against
_LIKELY_ENGLISH_RATIO_LIMIT = NON_LATIN_RATIO_LIMIT


def _is_likely_english(text: str) -> bool:
    """Return False if *text* contains too many non-Latin-script characters."""
    if text and text.isascii():
        non_latin = len(text) - len(text.translate(_ASCII_NON_LATIN_TRANSLATION))
        if non_latin / len(text) <= _LIKELY_ENGLISH_RATIO_LIMIT:
            return True
    return _is_likely_english_impl(text)
