"""Tests for :meth:`AudioPipeline.handle_xrun_status`.

The XRUN detector inspects the PortAudio ``status`` flag passed to
the audio callback and:

1. **Narrows** to ``status.input_overflow`` (the real input-XRUN
   flag) instead of ``if status:`` — pre-fix the latter over-counted
   by 1 on every ``start()`` because ``priming_output`` fires on the
   first callback after a stream start.
2. **Increments** ``_xruns`` and appends a timestamp to
   ``_xrun_timestamps`` for the rolling-window alert.
3. **Throttles** the warning log: emits at most every Nth xrun when
   a rolling-window threshold (5 xruns in 10 s) is exceeded, plus
   once on the first xrun. Without throttling, a sustained XRUN
   storm would flood the log at 16 Hz.
4. **Fires** the ``on_xrun_threshold`` callback every ``_xrun_threshold``
   xruns (every N, not just at exactly N — pre-fix ``==`` fired once).
5. **Drops** the partial chunk on XRUN (returns True so the caller
   skips the buffer append — appending the stale PortAudio buffer
   would corrupt the transcriber's input with a discontinuity).

The tests exercise :class:`AudioPipeline` directly with a
``MagicMock`` recorder stub that supplies the XRUN state
(``_xruns``, ``_xrun_timestamps``, ``_xrun_threshold``,
``on_xrun_threshold``). No real PortAudio / sounddevice is touched.
"""

from __future__ import annotations

import collections
import logging
from unittest.mock import MagicMock

import pytest
from voice_typer.server.recording.audio_pipeline import (
    _XRUN_ALERT_PERIOD,
    _XRUN_ALERT_THRESHOLD,
    AudioPipeline,
)

log = logging.getLogger("voice_typer.server.recording")


def _make_xrun_recorder_stub(
    *,
    xrun_threshold: int = 10,
) -> MagicMock:
    """Build a MagicMock ``Recorder`` with the XRUN-tracking state.

    The stub exposes exactly the attributes that
    ``AudioPipeline.handle_xrun_status`` reads / writes:

    - ``_xruns`` — running XRUN counter, incremented on every
      input-overflow.
    - ``_xrun_timestamps`` — real ``collections.deque`` so the
      rolling-window ``append`` + iteration in the production code
      works with real semantics (a MagicMock auto-mock would
      silently return a MagicMock for ``.append`` and break the
      windowed count iteration).
    - ``_xrun_threshold`` — every Nth xrun fires the tray callback.
    - ``on_xrun_threshold`` — MagicMock so the test can assert call
      count + the count value passed.
    """
    recorder = MagicMock(name="RecorderStub")
    recorder._xruns = 0
    recorder._xrun_timestamps = collections.deque()
    recorder._xrun_threshold = xrun_threshold
    recorder.on_xrun_threshold = MagicMock(name="on_xrun_threshold")
    return recorder


class _StatusFlags:
    """Minimal stand-in for ``sounddevice.CallbackFlags``.

    The production code reads ``status.input_overflow`` (bool) when
    the status object has the attribute. ``sounddevice.CallbackFlags``
    is a subclass of ``int`` so it also satisfies the
    ``isinstance(status, int)`` fallback path — this stub doesn't
    subclass int, so it exercises ONLY the attribute path. A separate
    test uses the raw-int path (status=2) to cover the int fallback.
    """

    def __init__(self, *, input_overflow: bool = False) -> None:
        self.input_overflow = input_overflow
        # Make ``if status:`` truthy whenever input_overflow is set,
        # mirroring how the real CallbackFlags behaves (bit-set int).
        self._truthy = input_overflow

    def __bool__(self) -> bool:
        return self._truthy


# ── 1. input_overflow increments the XRUN counter ───────────────────


class TestInputOverflowIncrementsXrunCounter:
    """A PortAudio status with ``input_overflow=True`` must increment
    ``_xruns`` and return True (so the caller drops the stale chunk)."""

    def test_input_overflow_increments_xrun_counter(self) -> None:
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        ret = pipeline.handle_xrun_status(_StatusFlags(input_overflow=True))

        # Return True → caller drops the partial chunk (avoids
        # corrupting the transcriber's input with a discontinuity).
        assert ret is True
        assert recorder._xruns == 1
        # A timestamp was appended for the rolling-window alert.
        assert len(recorder._xrun_timestamps) == 1

    def test_raw_int_status_with_bit_1_increments_xrun(self) -> None:
        """R18-F13: tests pass ``status=2`` (``paInputOverflow == 2``
        in PortAudio's flag enum) to simulate a CallbackFlags object
        without constructing one. The int-fallback path
        (``status & 2``) must detect the overflow."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        ret = pipeline.handle_xrun_status(2)

        assert ret is True
        assert recorder._xruns == 1
        assert len(recorder._xrun_timestamps) == 1

    def test_status_with_other_flags_only_does_not_increment(self) -> None:
        """``status.input_overflow=False`` (e.g. only
        ``priming_output`` set, which fires on the first callback
        after every start) must NOT be treated as an XRUN. Pre-fix,
        ``if status:`` over-counted by 1 on every ``start()``."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        # Truthy status (e.g. priming_output) but input_overflow=False.
        status = _StatusFlags(input_overflow=False)
        status._truthy = True  # make ``if status:`` fire
        ret = pipeline.handle_xrun_status(status)

        assert ret is False
        assert recorder._xruns == 0
        assert len(recorder._xrun_timestamps) == 0

    def test_raw_int_status_without_bit_1_does_not_increment(self) -> None:
        """A raw int with only bit 0 (``paNoError`` == 0 is falsy;
        bit 2 / 4 / etc. are other PortAudio flags) must NOT be
        treated as input overflow."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        # bit 0 set (value 1) — NOT paInputOverflow (which is bit 1, value 2)
        ret = pipeline.handle_xrun_status(1)

        assert ret is False
        assert recorder._xruns == 0

    def test_falsy_status_does_not_increment(self) -> None:
        """``status=0`` (no flags set — clean callback) must be a
        no-op."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        ret = pipeline.handle_xrun_status(0)

        assert ret is False
        assert recorder._xruns == 0
        assert len(recorder._xrun_timestamps) == 0

    def test_xrun_appends_monotonic_timestamp(self) -> None:
        """Each XRUN must append a ``time.monotonic()`` reading to
        ``_xrun_timestamps`` so the rolling-window alert can count
        recent XRUNs."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        pipeline.handle_xrun_status(2)
        pipeline.handle_xrun_status(2)
        pipeline.handle_xrun_status(2)

        assert recorder._xruns == 3
        assert len(recorder._xrun_timestamps) == 3
        # Timestamps are monotonic non-decreasing.
        ts = list(recorder._xrun_timestamps)
        assert ts[0] <= ts[1] <= ts[2]


# ── 2. rolling-window log throttling ────────────────────────────────


class TestXrunRollingWindowLogThrottling:
    """The warning log must be throttled by a rolling window: emit on
    the 1st XRUN, then ONLY when 5+ XRUNs land inside a 10-second
    window. Without throttling, a sustained XRUN storm (e.g. CPU
    spike / GC pause) would flood the log at 16 Hz."""

    def test_xrun_rolling_window_log_throttling(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Feed 4 XRUNs in a short window (< 10 s). The log must fire
        EXACTLY ONCE (on the 1st XRUN via the ``_xruns == 1`` clause)
        — XRUNs 2-4 are below the rolling-window threshold of 5 and
        must NOT log.

        A 5th XRUN in the same window crosses the threshold and the
        log fires again, proving the throttle releases at the
        threshold rather than permanently suppressing."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            # 4 XRUNs in the same window — only the 1st should log.
            for _ in range(4):
                pipeline.handle_xrun_status(2)

        # XRUNs 2-4 throttled — log fired only on the 1st.
        first_batch_logs = [
            r for r in caplog.records
            if "PortAudio status flag" in r.getMessage()
        ]
        assert len(first_batch_logs) == 1, (
            f"Expected exactly 1 log on the first 4 XRUNs (only the 1st via "
            f"the `_xruns == 1` clause); got {len(first_batch_logs)}."
        )
        # Snapshot the record count before the 5th XRUN so the
        # second-batch assertion can use a delta (caplog.records
        # accumulates across ``caplog.at_level`` context managers).
        records_before_5th = len(caplog.records)

        # 5th XRUN crosses the rolling-window threshold (recent_count
        # == 5 >= _XRUN_ALERT_THRESHOLD) — log fires again.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            pipeline.handle_xrun_status(2)

        second_batch_logs = [
            r
            for r in caplog.records[records_before_5th:]
            if "PortAudio status flag" in r.getMessage()
        ]
        assert len(second_batch_logs) == 1, (
            "Expected the 5th XRUN (rolling-window threshold crossed) to "
            f"log once; got {len(second_batch_logs)}."
        )

        # Sanity: counter tracked every XRUN regardless of logging.
        assert recorder._xruns == 5

    def test_below_threshold_xruns_do_not_log(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """XRUNs 2, 3, 4 (below the rolling-window threshold of 5 and
        ``_xruns != 1``) must NOT emit a log record. Only the 1st
        XRUN (via the ``_xruns == 1`` clause) logs."""
        recorder = _make_xrun_recorder_stub()
        pipeline = AudioPipeline(recorder)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            # 1st — logs (via _xruns == 1 clause)
            pipeline.handle_xrun_status(2)
            # 2nd-4th — throttled (recent_count < 5, _xruns != 1)
            pipeline.handle_xrun_status(2)
            pipeline.handle_xrun_status(2)
            pipeline.handle_xrun_status(2)

        portaudio_logs = [
            r for r in caplog.records
            if "PortAudio status flag" in r.getMessage()
        ]
        assert len(portaudio_logs) == 1, (
            f"Expected exactly 1 log (only on 1st XRUN); got {len(portaudio_logs)}."
        )
        # Sanity: counter tracked every XRUN regardless of logging.
        assert recorder._xruns == 4

    def test_threshold_constants_are_documented(self) -> None:
        """Pin the rolling-window constants so a future refactor
        can't silently change them and break the throttle math
        (5 XRUNs in 10 s)."""
        assert _XRUN_ALERT_THRESHOLD == 5
        assert _XRUN_ALERT_PERIOD == 10.0


# ── 3. on_xrun_threshold callback fires every N xruns ──────────────


class TestOnXrunThresholdCallbackFiresEveryN:
    """The tray-notification callback must fire every Nth XRUN
    (``_xruns % threshold == 0``), not just once at exactly N.
    Pre-fix, ``==`` fired EXACTLY ONCE per session (when _xruns
    incremented from N-1 to N) and never again."""

    def test_callback_fires_every_n_xruns(self) -> None:
        """With threshold=5, the callback fires on the 5th, 10th, 15th
        XRUN — three times for 15 XRUNs total."""
        recorder = _make_xrun_recorder_stub(xrun_threshold=5)
        pipeline = AudioPipeline(recorder)

        for _ in range(15):
            pipeline.handle_xrun_status(2)

        # 15 XRUNs / threshold of 5 = 3 callbacks (on xrun 5, 10, 15).
        assert recorder.on_xrun_threshold.call_count == 3
        # Each callback receives the current _xruns value.
        call_args = [c.args[0] for c in recorder.on_xrun_threshold.call_args_list]
        assert call_args == [5, 10, 15]

    def test_callback_does_not_fire_below_threshold(self) -> None:
        """With threshold=10, 4 XRUNs must not fire the callback."""
        recorder = _make_xrun_recorder_stub(xrun_threshold=10)
        pipeline = AudioPipeline(recorder)

        for _ in range(4):
            pipeline.handle_xrun_status(2)

        assert recorder.on_xrun_threshold.call_count == 0

    def test_callback_exceptions_are_suppressed(self) -> None:
        """If the user-supplied callback raises, the XRUN handler
        must swallow the exception (``contextlib.suppress(Exception)``)
        so a buggy callback doesn't kill the audio worker thread."""
        recorder = _make_xrun_recorder_stub(xrun_threshold=1)
        recorder.on_xrun_threshold.side_effect = RuntimeError("buggy callback")
        pipeline = AudioPipeline(recorder)

        # Must NOT raise — the suppress swallows the RuntimeError.
        ret = pipeline.handle_xrun_status(2)

        assert ret is True
        assert recorder._xruns == 1
        # The callback was invoked (and the exception suppressed).
        assert recorder.on_xrun_threshold.call_count == 1

    def test_no_callback_does_not_crash(self) -> None:
        """When ``on_xrun_threshold`` is ``None`` (no tray subscriber),
        the threshold check must short-circuit without raising."""
        recorder = _make_xrun_recorder_stub(xrun_threshold=1)
        recorder.on_xrun_threshold = None
        pipeline = AudioPipeline(recorder)

        ret = pipeline.handle_xrun_status(2)

        assert ret is True
        assert recorder._xruns == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov", "--timeout=30"])
