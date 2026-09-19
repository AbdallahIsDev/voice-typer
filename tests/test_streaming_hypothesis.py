"""Property-based tests for streaming.py using hypothesis."""

from __future__ import annotations

import pytest

try:
    from hypothesis import HealthCheck, given, settings, strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

pytestmark = pytest.mark.skipif(
    not HAS_HYPOTHESIS,
    reason="hypothesis not installed, install with: pip install hypothesis",
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
            """Words in committed_text should be ordered by start_seconds."""
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert isinstance(result, str)

            emitted_words = result.split()
            sorted_input = sorted(words, key=lambda w: (w.start_seconds, w.end_seconds))
            # Extract expected word texts (strip to match assembler's strip())
            expected_words = [w.word.strip() for w in sorted_input if w.word.strip()]
            # The assembler may deduplicate, so the emitted list may be
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
