"""Property-based tests for streaming.py using hypothesis.

TEST-009: Property-based tests for streaming — random word timings,
verify committed_text is always sorted and non-empty when words are added.
"""

from __future__ import annotations

import pytest

# WR-11: single-assignment pytestmark. The previous code first set
# ``pytestmark = pytest.mark.skipif(True, ...)`` then reassigned it to
# ``pytest.mark.skipif(False, ...)`` inside the ``try`` block if
# hypothesis imported cleanly. The reassignment worked but was
# confusing — the two-stage pattern read as "always skip first, then
# maybe un-skip". The single-assignment form below is equivalent and
# clearer: detect hypothesis up front, then set the skipif mark based
# on the result.
try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

pytestmark = pytest.mark.skipif(
    not HAS_HYPOTHESIS,
    reason="hypothesis not installed — install with: pip install hypothesis",
)


if HAS_HYPOTHESIS:
    from voice_typer.server.streaming import (
        StreamingTextAssembler,
        WordTiming,
    )

    # Strategy for generating WordTiming objects with valid timestamps
    @st.composite
    def word_timing_strategy(draw, max_time=100.0):
        """Generate a WordTiming with start <= end, both finite and non-negative."""
        start = draw(st.floats(min_value=0.0, max_value=max_time, allow_nan=False, allow_infinity=False))
        end = draw(st.floats(min_value=start, max_value=max_time, allow_nan=False, allow_infinity=False))
        word = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll", "Lu"))))
        return WordTiming(word=word, start_seconds=round(start, 3), end_seconds=round(end, 3))

    class TestAssemblerProperties:
        """Property-based tests for StreamingTextAssembler."""

        @given(words=st.lists(word_timing_strategy(), min_size=0, max_size=50))
        @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
        def test_committed_text_never_crashes(self, words):
            """Adding any sequence of words should never crash."""
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert isinstance(result, str)

        @given(words=st.lists(word_timing_strategy(), min_size=1, max_size=50))
        @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
        def test_committed_text_is_nonempty_when_words_added(self, words):
            """committed_text should never be empty when words have been added."""
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert len(result) > 0

        @given(words=st.lists(word_timing_strategy(), min_size=1, max_size=50))
        @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
        def test_committed_text_sorted_by_time(self, words):
            """Words in committed_text should be ordered by start_seconds.

            TEST-009: Pre-fix this test only asserted ``isinstance(result, str)``
            — the docstring promised sort-order verification but the body
            delivered a type check. Now we verify the actual chronological
            order by comparing the emitted word sequence against the input
            sorted by ``start_seconds``.
            """
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert isinstance(result, str)

            # TEST-009: verify sort order. The committed_text joins words
            # with spaces; we split to get the word sequence and compare
            # against the input sorted by the same key the assembler uses:
            # (start_seconds, end_seconds).
            emitted_words = result.split()
            # Build expected order: sort input words by the same key the
            # assembler uses — (start_seconds, end_seconds) — then extract
            # the .word field. This handles the case where two words share
            # the same start_seconds (the assembler breaks ties by
            # end_seconds).
            sorted_input = sorted(words, key=lambda w: (w.start_seconds, w.end_seconds))
            # Extract expected word texts (strip to match assembler's strip())
            expected_words = [w.word.strip() for w in sorted_input if w.word.strip()]
            # The assembler may deduplicate, so the emitted list may be
            # shorter. Verify that every emitted word appears in the
            # expected list AND in the same relative order.
            expected_idx = 0
            for emitted in emitted_words:
                # Find this word in the remaining expected list
                found = False
                while expected_idx < len(expected_words):
                    if expected_words[expected_idx] == emitted:
                        found = True
                        expected_idx += 1
                        break
                    expected_idx += 1
                assert found, (
                    f"TEST-009: emitted word {emitted!r} is out of order or "
                    f"not found in expected sequence. Emitted: {emitted_words}, "
                    f"Expected: {expected_words}"
                )

else:
    # Stub class so pytest collection doesn't fail
    class TestAssemblerProperties:
        pass
