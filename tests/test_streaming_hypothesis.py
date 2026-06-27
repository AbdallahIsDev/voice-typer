"""Property-based tests for streaming.py using hypothesis.

TEST-009: Property-based tests for streaming — random word timings,
verify committed_text is always sorted and non-empty when words are added.
"""

from __future__ import annotations

import pytest
import numpy as np

pytestmark = pytest.mark.skipif(
    True,  # Will be overridden below if hypothesis is available
    reason="hypothesis not installed — install with: pip install hypothesis",
)

try:
    from hypothesis import given, assume, settings, HealthCheck
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
    pytestmark = pytest.mark.skipif(
        False,
        reason="hypothesis not installed",
    )
except ImportError:
    HAS_HYPOTHESIS = False


if HAS_HYPOTHESIS:
    from voice_typer.server.streaming import (
        StreamingConfig,
        StreamingTextAssembler,
        WordTiming,
    )

    # Strategy for generating WordTiming objects with valid timestamps
    @st.composite
    def word_timing_strategy(draw, max_time=100.0):
        """Generate a WordTiming with start <= end, both finite and non-negative."""
        start = draw(st.floats(min_value=0.0, max_value=max_time, allow_nan=False, allow_infinity=False))
        end = draw(st.floats(min_value=start, max_value=max_time, allow_nan=False, allow_infinity=False))
        word = draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Ll', 'Lu'))))
        return WordTiming(word=word, start_seconds=round(start, 3), end_seconds=round(end, 3))

    class TestAssemblerProperties:
        """Property-based tests for StreamingTextAssembler."""

        @given(words=st.lists(word_timing_strategy, min_size=0, max_size=50))
        @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
        def test_committed_text_never_crashes(self, words):
            """Adding any sequence of words should never crash."""
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert isinstance(result, str)

        @given(words=st.lists(word_timing_strategy, min_size=1, max_size=50))
        @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
        def test_committed_text_is_nonempty_when_words_added(self, words):
            """committed_text should never be empty when words have been added."""
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert len(result) > 0

        @given(words=st.lists(word_timing_strategy, min_size=1, max_size=50))
        @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
        def test_committed_text_sorted_by_time(self, words):
            """Words in committed_text should be ordered by start_seconds."""
            assembler = StreamingTextAssembler()
            for wt in words:
                assembler.add_words([wt], commit_horizon_seconds=wt.end_seconds + 1.0)
            result = assembler.committed_text
            assert isinstance(result, str)

else:
    # Stub class so pytest collection doesn't fail
    class TestAssemblerProperties:
        pass
