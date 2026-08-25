"""``_timed_stage`` context manager contract tests.

Split from the former catch-all module
``tests/test_dictation_pipeline_review_fixes.py``. ``_timed_stage``
replaces the 10
duplicated ``_stage_t0 = time.perf_counter()`` /
``_<name>_ms = (...) * 1000`` blocks in ``DictationPipeline.run`` with
a single DRY primitive (implementation:
``voice_typer/server/dictation_pipeline/_stage_timer.py``).

These tests pin the contract: writes to the supplied dict, records a
positive duration, preserves exception propagation (with timing still
recorded up to the raise), and supports nested use across multiple
stages.
"""

from __future__ import annotations

import pytest


class TestTimedStageContextManager:
    """``_timed_stage`` replaces the 10 duplicated
    ``_stage_t0 = time.perf_counter()`` / ``_<name>_ms = (...) * 1000``
    blocks in ``DictationPipeline.run`` with a single DRY primitive.

    These tests pin the contract: writes to the supplied dict,
    records a positive duration, preserves exception propagation
    (with timing still recorded up to the raise), and supports
    nested use across multiple stages.
    """

    def test_records_positive_duration_in_dict(self) -> None:
        import time

        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        with _timed_stage(timings, "transcribe"):
            time.sleep(0.005)
        assert "transcribe" in timings
        # 5 ms sleep — allow generous lower bound (CPU contention)
        # and an upper bound that catches "forgot to subtract t0" bugs.
        assert 1.0 < timings["transcribe"] < 1000.0

    def test_exception_propagates_and_timing_still_recorded(self) -> None:
        # the ``finally`` clause runs before the exception
        # propagates so a ``[PIPE-PERF]`` log emitted from the
        # ``except`` block in ``run()`` has a best-effort timing for
        # the stage that failed.
        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        # Combine into a single ``with`` so ruff SIM117 doesn't flag the
        # nested-context pattern (the test still asserts both that the
        # exception propagates AND that timing is recorded).
        with pytest.raises(RuntimeError, match="boom"), _timed_stage(timings, "store"):
            raise RuntimeError("boom")
        assert "store" in timings
        assert timings["store"] >= 0.0

    def test_multiple_stages_each_recorded(self) -> None:
        # Mirrors the actual usage in ``DictationPipeline.run``: a
        # single dict is reused across consecutive ``with`` blocks,
        # one entry per stage name.
        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        with _timed_stage(timings, "clean"):
            pass
        with _timed_stage(timings, "vocab"):
            pass
        with _timed_stage(timings, "templates"):
            pass
        assert set(timings.keys()) == {"clean", "vocab", "templates"}
        # All three recorded with non-negative durations.
        assert all(v >= 0.0 for v in timings.values())
