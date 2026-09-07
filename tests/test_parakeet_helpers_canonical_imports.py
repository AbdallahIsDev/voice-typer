"""Canonical-resolution tests for the parakeet engine helper aliases.

The historical defensive-fallback machinery in
``voice_typer.server.parakeet_engine._helpers`` (a try/except around the
``asr_utils`` import, six ``None`` placeholders, six ``_local_*``
re-implementations, and a dispatch block) was removed once the ONNX
parallel refactor completed: ``asr_utils`` lives in the same package, so
the fallback was unreachable dead code. These tests pin the post-cleanup
contract so it cannot silently regress:

1. every alias exported by ``_helpers`` resolves to the CANONICAL
   ``asr_utils`` implementation (same function object — no parallel
   copy can creep back in);
2. the merge-chunk / language-filter constants in
   ``parakeet_engine._constants`` are the same objects as their
   ``asr_utils`` counterparts (no re-declared literals that could
   drift);
3. the backward-compat names the package facade re-exports (used by
   ``_transcribe.py``, ``tests/test_parakeet_engine.py``,
   ``tests/regressions/test_parakeet_merge.py``) keep working.
"""

from __future__ import annotations

import voice_typer.server.asr_utils as asr_utils
import voice_typer.server.parakeet_engine as _package
import voice_typer.server.parakeet_engine._constants as _constants
import voice_typer.server.parakeet_engine._helpers as _helpers


class TestHelpersResolveCanonicalImplementations:
    """``_helpers`` must be pure aliases over ``asr_utils``."""

    def test_impl_aliases_are_the_asr_utils_functions(self) -> None:
        assert _helpers._is_cuda_error_impl is asr_utils.is_cuda_error
        assert _helpers._is_latin_char is asr_utils.is_latin_char
        assert _helpers._is_likely_english_impl is asr_utils.is_likely_english
        assert _helpers._merge_chunks_impl is asr_utils.merge_chunks
        assert _helpers._compute_overlap_skip_impl is asr_utils.compute_overlap_skip

    def test_no_local_fallback_copies_remain(self) -> None:
        """The ``_local_*`` re-implementations were deleted — none of
        the names may reappear (a reintroduced fallback would be
        unreachable dead code again, and a drift hazard)."""
        for name in (
            "_local_is_latin_char",
            "_local_is_likely_english",
            "_local_is_cuda_error",
            "_local_compute_overlap_skip",
            "_local_merge_chunks",
        ):
            assert not hasattr(_helpers, name), f"_helpers.{name} must not exist"

    def test_helpers_available_flag_is_true(self) -> None:
        """The package facade re-exports this flag; with the direct
        import it is ``True`` by construction."""
        assert _helpers._ASR_UTILS_HELPERS_AVAILABLE is True

    def test_ascii_fast_path_threshold_is_the_canonical_constant(self) -> None:
        assert _helpers._LIKELY_ENGLISH_RATIO_LIMIT is asr_utils.NON_LATIN_RATIO_LIMIT


class TestConstantsAreCanonicalObjects:
    """``_constants`` must not re-declare the ``asr_utils`` thresholds."""

    def test_constants_are_the_asr_utils_objects(self) -> None:
        assert _constants._NON_LATIN_RATIO_LIMIT is asr_utils.NON_LATIN_RATIO_LIMIT
        assert _constants._MAX_BOUNDARY_SKIP_WORDS is asr_utils.MAX_BOUNDARY_SKIP_WORDS
        assert _constants._OVERLAP_DEDUP_WINDOW is asr_utils.OVERLAP_DEDUP_WINDOW

    def test_values_unchanged(self) -> None:
        """The historical literal values the importers relied on."""
        assert _constants._NON_LATIN_RATIO_LIMIT == 0.30
        assert _constants._MAX_BOUNDARY_SKIP_WORDS == 2
        assert _constants._OVERLAP_DEDUP_WINDOW == 3


class TestPackageFacadeCompatNames:
    """The facade re-exports must keep resolving (importers unchanged)."""

    def test_facade_aliases_resolve(self) -> None:
        assert _package._is_latin_char is asr_utils.is_latin_char
        assert _package._is_likely_english is _helpers._is_likely_english
        assert _package._MAX_BOUNDARY_SKIP_WORDS is asr_utils.MAX_BOUNDARY_SKIP_WORDS
        assert _package._OVERLAP_DEDUP_WINDOW is asr_utils.OVERLAP_DEDUP_WINDOW
        assert _package._NON_LATIN_RATIO_LIMIT is asr_utils.NON_LATIN_RATIO_LIMIT
