"""Tests for :meth:`AudioPipeline.handle_xrun_status`."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from voice_typer.server.recording.audio_pipeline import (
    _XRUN_ALERT_PERIOD,
    _XRUN_ALERT_THRESHOLD,
    AudioPipeline,
)

log = logging.getLogger("voice_typer.server.recording")


def _make_xrun_pipeline(
    *,
    xrun_threshold: int = 10,
) -> tuple[MagicMock, AudioPipeline]:
    """Build a MagicMock ``Recorder`` + the owning ``AudioPipeline``."""
    recorder = MagicMock(name="RecorderStub")
    recorder.on_xrun_threshold = MagicMock(name="on_xrun_threshold")
    pipeline = AudioPipeline(recorder)
    pipeline._xrun_threshold = xrun_threshold
    return recorder, pipeline


class _StatusFlags:
    """Minimal stand-in for ``sounddevice.CallbackFlags``."""

    def __init__(self, *, input_overflow: bool = False) -> None:
        self.input_overflow = input_overflow
        # Make ``if status:`` truthy whenever input_overflow is set,
        self._truthy = input_overflow

    def __bool__(self) -> bool:
        return self._truthy


class TestInputOverflowIncrementsXrunCounter:
    """A PortAudio status with ``input_overflow=True`` must increment"""

    def test_input_overflow_increments_xrun_counter(self) -> None:
        recorder, pipeline = _make_xrun_pipeline()

        ret = pipeline.handle_xrun_status(_StatusFlags(input_overflow=True))

        # Return True → caller drops the partial chunk (avoids
        assert ret is True
        assert pipeline._xruns == 1
        # A timestamp was appended for the rolling-window alert.
        assert len(pipeline._xrun_timestamps) == 1

    def test_raw_int_status_with_bit_1_increments_xrun(self) -> None:
        """R18-F13: tests pass ``status=2`` (``paInputOverflow == 2``"""
        recorder, pipeline = _make_xrun_pipeline()

        ret = pipeline.handle_xrun_status(2)

        assert ret is True
        assert pipeline._xruns == 1
        assert len(pipeline._xrun_timestamps) == 1

    def test_status_with_other_flags_only_does_not_increment(self) -> None:
        """``status.input_overflow=False`` (e.g. only"""
        recorder, pipeline = _make_xrun_pipeline()

        # Truthy status (e.g. priming_output) but input_overflow=False.
        status = _StatusFlags(input_overflow=False)
        status._truthy = True  # make ``if status:`` fire
        ret = pipeline.handle_xrun_status(status)

        assert ret is False
        assert pipeline._xruns == 0
        assert len(pipeline._xrun_timestamps) == 0

    def test_raw_int_status_without_bit_1_does_not_increment(self) -> None:
        """A raw int with only bit 0 (``paNoError`` == 0 is falsy;"""
        recorder, pipeline = _make_xrun_pipeline()

        ret = pipeline.handle_xrun_status(1)

        assert ret is False
        assert pipeline._xruns == 0

    def test_falsy_status_does_not_increment(self) -> None:
        """``status=0`` (no flags set, clean callback) must be a"""
        recorder, pipeline = _make_xrun_pipeline()

        ret = pipeline.handle_xrun_status(0)

        assert ret is False
        assert pipeline._xruns == 0
        assert len(pipeline._xrun_timestamps) == 0

    def test_xrun_appends_monotonic_timestamp(self) -> None:
        """``_xrun_timestamps`` so the rolling-window alert can count"""
        recorder, pipeline = _make_xrun_pipeline()

        pipeline.handle_xrun_status(2)
        pipeline.handle_xrun_status(2)
        pipeline.handle_xrun_status(2)

        assert pipeline._xruns == 3
        assert len(pipeline._xrun_timestamps) == 3
        # Timestamps are monotonic non-decreasing.
        ts = list(pipeline._xrun_timestamps)
        assert ts[0] <= ts[1] <= ts[2]


class TestXrunRollingWindowLogThrottling:
    """the 1st XRUN, then ONLY when 5+ XRUNs land inside a 10-second"""

    def test_xrun_rolling_window_log_throttling(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Feed 4 XRUNs in a short window (< 10 s). The log must fire"""
        recorder, pipeline = _make_xrun_pipeline()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            # 4 XRUNs in the same window, only the 1st should log.
            for _ in range(4):
                pipeline.handle_xrun_status(2)

        # XRUNs 2-4 throttled, log fired only on the 1st.
        first_batch_logs = [r for r in caplog.records if "PortAudio status flag" in r.getMessage()]
        assert len(first_batch_logs) == 1, (
            f"Expected exactly 1 log on the first 4 XRUNs (only the 1st via "
            f"the `_xruns == 1` clause); got {len(first_batch_logs)}."
        )
        records_before_5th = len(caplog.records)

        # 5th XRUN crosses the rolling-window threshold (recent_count
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            pipeline.handle_xrun_status(2)

        second_batch_logs = [
            r for r in caplog.records[records_before_5th:] if "PortAudio status flag" in r.getMessage()
        ]
        assert len(second_batch_logs) == 1, (
            f"Expected the 5th XRUN (rolling-window threshold crossed) to log once; got {len(second_batch_logs)}."
        )

        # Sanity: counter tracked every XRUN regardless of logging.
        assert pipeline._xruns == 5

    def test_below_threshold_xruns_do_not_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``_xruns != 1``) must NOT emit a log record. Only the 1st"""
        recorder, pipeline = _make_xrun_pipeline()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            # 1st, logs (via _xruns == 1 clause)
            pipeline.handle_xrun_status(2)
            # 2nd-4th, throttled (recent_count < 5, _xruns != 1)
            pipeline.handle_xrun_status(2)
            pipeline.handle_xrun_status(2)
            pipeline.handle_xrun_status(2)

        portaudio_logs = [r for r in caplog.records if "PortAudio status flag" in r.getMessage()]
        assert len(portaudio_logs) == 1, f"Expected exactly 1 log (only on 1st XRUN); got {len(portaudio_logs)}."
        # Sanity: counter tracked every XRUN regardless of logging.
        assert pipeline._xruns == 4

    def test_threshold_constants_are_documented(self) -> None:
        """Pin the rolling-window constants so a future refactor"""
        assert _XRUN_ALERT_THRESHOLD == 5
        assert _XRUN_ALERT_PERIOD == 10.0


class TestOnXrunThresholdCallbackFiresEveryN:
    """The tray-notification callback must fire every Nth XRUN"""

    def test_callback_fires_every_n_xruns(self) -> None:
        """With threshold=5, the callback fires on the 5th, 10th, 15th"""
        recorder, pipeline = _make_xrun_pipeline(xrun_threshold=5)

        for _ in range(15):
            pipeline.handle_xrun_status(2)

        # 15 XRUNs / threshold of 5 = 3 callbacks (on xrun 5, 10, 15).
        assert recorder.on_xrun_threshold.call_count == 3
        # Each callback receives the current _xruns value.
        call_args = [c.args[0] for c in recorder.on_xrun_threshold.call_args_list]
        assert call_args == [5, 10, 15]

    def test_callback_does_not_fire_below_threshold(self) -> None:
        """With threshold=10, 4 XRUNs must not fire the callback."""
        recorder, pipeline = _make_xrun_pipeline(xrun_threshold=10)

        for _ in range(4):
            pipeline.handle_xrun_status(2)

        assert recorder.on_xrun_threshold.call_count == 0

    def test_callback_exceptions_are_suppressed(self) -> None:
        """If the user-supplied callback raises, the XRUN handler"""
        recorder, pipeline = _make_xrun_pipeline(xrun_threshold=1)
        recorder.on_xrun_threshold.side_effect = RuntimeError("buggy callback")

        # Must NOT raise, the suppress swallows the RuntimeError.
        ret = pipeline.handle_xrun_status(2)

        assert ret is True
        assert pipeline._xruns == 1
        # The callback was invoked (and the exception suppressed).
        assert recorder.on_xrun_threshold.call_count == 1

    def test_no_callback_does_not_crash(self) -> None:
        """When ``on_xrun_threshold`` is ``None`` (no tray subscriber),"""
        recorder, pipeline = _make_xrun_pipeline(xrun_threshold=1)
        recorder.on_xrun_threshold = None

        ret = pipeline.handle_xrun_status(2)

        assert ret is True
        assert pipeline._xruns == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov", "--timeout=30"])
