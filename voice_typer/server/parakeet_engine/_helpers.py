"""Backward-compat helper aliases over the canonical ``asr_utils`` helpers.

The canonical implementations of the language filter and chunk-merge
helpers live in :mod:`voice_typer.server.asr_utils` (they moved there
during the ONNX migration). This module resolves them directly and keeps
the private alias names that ``parakeet_engine``'s sibling leaves
(``_transcribe.py``, the package facade ``__init__.py``) and the test
suite import, so no consuming site had to change when the historical
local fallback copies were deleted.
"""

from __future__ import annotations

from voice_typer.server.asr_utils import (
    NON_LATIN_RATIO_LIMIT,
    compute_overlap_skip as _compute_overlap_skip_impl,  # noqa: F401 — re-exported alias (see note below)
    is_cuda_error as _is_cuda_error_impl,  # noqa: F401 — re-exported alias (see note below)
    is_latin_char as _is_latin_char,  # noqa: F401 — re-exported alias (see note below)
    is_likely_english as _is_likely_english_impl,
    merge_chunks as _merge_chunks_impl,  # noqa: F401 — re-exported alias (see note below)
)

# The asr_utils import above is unconditional, so the helpers are always
# resolved from the canonical module. The ``noqa: F401`` re-export
# aliases are unused WITHIN this module on purpose: sibling leaves
# (``parakeet_engine/_transcribe.py``, the package facade
# ``parakeet_engine/__init__.py``) and the test suite import them from
# here — deleting them would break those import sites. The flag below is
# kept as a module constant because the package facade re-exports it; it
# is ``True`` by construction now that the parallel-refactor fallback
# machinery is gone.
_ASR_UTILS_HELPERS_AVAILABLE = True

# ─── Backward-compat aliases for moved helpers ─────────────────────────
#
# PLAN_ONNX_INTEGRATION.md §5.3 / §5.4 moved ``_is_latin_char``,
# ``_is_likely_english``, ``_merge_chunks`` and ``_compute_overlap_skip``
# to :mod:`voice_typer.server.asr_utils`. The canonical implementations
# live there now; the leading-underscore names above are kept as
# import-aliases so existing import sites
# (``tests/test_parakeet_engine.py``, ``tests/regressions/test_parakeet_merge.py``,
# ``tests/test_word_drop_regression.py``, ``parakeet_engine/_transcribe.py``)
# keep working without a parallel test rewrite. New code should import
# from ``asr_utils``.

# ─── ASCII fast path for the language filter ───────────────────────────
#
# The resolved :func:`asr_utils.is_likely_english` classifies characters
# one at a time (``sum(1 for ch in text if not is_latin_char(ch))``), and
# each classification costs two ``unicodedata`` calls. Transcription
# output is overwhelmingly pure ASCII, where the answer is computable in
# bulk: every ASCII code point satisfies ``is_latin_char`` (punctuation
# P*, separators Zs, symbols S*, digits via ``str.isdigit``, and letters
# whose ``unicodedata.name`` starts with "LATIN") EXCEPT the 33 Cc
# control characters (U+0000–U+001F, U+007F). So for ASCII text the
# non-Latin count is just the number of control characters, which
# ``str.translate`` counts at C speed. The threshold comparison below
# is the exact complement of the canonical ``ratio > LIMIT`` check (same
# operands → same float division → ``<=`` ≡ ``not >``), so the fast path
# returns ``True`` ONLY where the canonical implementation provably
# would; every other input (non-ASCII, empty, or ASCII above the limit)
# delegates to the canonical implementation, preserving its
# whitespace short-circuit and its hallucination-rejection logging
# verbatim.

# U+0000–U+001F plus DEL (U+007F) — the only ASCII code points whose
# ``unicodedata`` classification is not Latin/punct/symbol/separator.
_ASCII_NON_LATIN_TRANSLATION: dict[int, None] = {cp: None for cp in range(0x20)}
_ASCII_NON_LATIN_TRANSLATION[0x7F] = None

# The threshold the canonical implementation compares against
# (``asr_utils.NON_LATIN_RATIO_LIMIT`` — imported above so the fast path
# always reads the same object the canonical check uses, even if the
# constant's value ever changes).
_LIKELY_ENGLISH_RATIO_LIMIT = NON_LATIN_RATIO_LIMIT


def _is_likely_english(text: str) -> bool:
    """Return False if *text* contains too many non-Latin-script characters.

    ASCII fast path in front of the canonical
    :func:`asr_utils.is_likely_english` (see the ASCII fast-path block
    above): pure-ASCII text below the non-Latin ratio limit returns
    ``True`` without the per-character ``unicodedata`` loop; everything
    else — including every REJECT decision — goes through the canonical
    implementation unchanged so its threshold boundary and PII-safe
    rejection logging are preserved exactly.
    """
    if text and text.isascii():
        non_latin = len(text) - len(text.translate(_ASCII_NON_LATIN_TRANSLATION))
        if non_latin / len(text) <= _LIKELY_ENGLISH_RATIO_LIMIT:
            return True
    return _is_likely_english_impl(text)
