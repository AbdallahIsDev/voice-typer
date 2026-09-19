"""``_timed_stage`` context manager contract tests."""

from __future__ import annotations

import pytest


class TestTimedStageContextManager:
    """
    ``_timed_stage`` replaces the 10 duplicated
    These tests pin the contract: writes to the supplied dict,
    """

    def test_records_positive_duration_in_dict(self) -> None:
        import time

        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        with _timed_stage(timings, "transcribe"):
            time.sleep(0.005)
        assert "transcribe" in timings
        # 5 ms sleep, allow generous lower bound (CPU contention)
        assert 1.0 < timings["transcribe"] < 1000.0

    def test_exception_propagates_and_timing_still_recorded(self) -> None:
        from voice_typer.server.dictation_pipeline import _timed_stage

        timings: dict[str, float] = {}
        with pytest.raises(RuntimeError, match="boom"), _timed_stage(timings, "store"):
            raise RuntimeError("boom")
        assert "store" in timings
        assert timings["store"] >= 0.0

    def test_multiple_stages_each_recorded(self) -> None:
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
