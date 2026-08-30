"""Phase 4.5: focused unit tests for
``AudioPipeline.process_audio_chunk``.

These tests exercise the *orchestration body* of the former
``Recorder._process_audio_chunk`` in isolation, with the six named
helpers (``_detect_device_disconnect`` / ``_handle_xrun_status`` /
``_apply_filter_chain`` / ``_append_to_buffer_locked`` /
``_compute_rms_and_peak`` / ``_run_vad_state_machine``) and the
clipping helper (``_detect_and_emit_clipping``) stubbed out on a
mock ``Recorder`` so no real audio I/O, no real VAD model, and no
real PortAudio is touched.

The tests verify:

- Early-return paths (device-disconnect, XRUN) skip downstream
  helpers.
- The happy-path orchestration calls every helper in the correct
  order with the correct arguments.
- ``_last_rms`` is updated under the lock with the post-filter
  ``chunk_rms``.
- ``_recent_rms_values`` is appended with ``chunk_rms``.
- The RMS callback contract: fired with the 2-arg signature
  ``(chunk_rms, chunk_peak)`` when set; not fired when ``None``.
- NEW-CONC-004: callback-exception logging suppresses traceback
  formatting after the first occurrence, re-formats on every 100th.

These tests intentionally bypass the recorder-construction path
(which would otherwise require patching ``sounddevice``,
``scipy``, etc.) by instantiating ``AudioPipeline`` directly with a
``MagicMock`` recorder stub.
"""

from __future__ import annotations

import collections
import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording.audio_pipeline import AudioPipeline

# Canned return values used across the happy-path tests. Chosen to be
# distinct primitives so test assertions can pin the exact value that
# flows through the orchestration body.
_FILTERED = np.array([0.1, -0.2, 0.3], dtype=np.float32)
_CHUNK_COUNT = 7
_BUFFER_LEN = 9
_CHUNK_RMS = 0.5
_CHUNK_PEAK = 0.9
_CHUNK_DURATION = 0.032
_RECORDING_START = 100.0


def _make_pipeline_stub(
    *,
    detect_disconnect_returns: bool = False,
    handle_xrun_returns: bool = False,
    rms_callback: callable | None = None,
    recording_start: float = _RECORDING_START,
) -> AudioPipeline:
    """Build a stub ``Recorder`` + ``AudioPipeline`` with the 6 named
    helpers + clip helper mocked on the pipeline instance.

    The stub recorder exposes exactly the attributes that
    ``AudioPipeline.process_audio_chunk`` reads / writes. The 6 named
    helpers are shadowed on the pipeline instance with ``MagicMock``
    objects so the test can assert call counts and arguments (the
    orchestration body now invokes its own methods directly — the
    historical ``Recorder._<helper>`` delegators were removed). Real
    ``threading.Lock`` and ``deque`` are installed for ``_lock`` and
    ``_recent_rms_values`` so the orchestration body's
    ``with self._recorder._lock:`` and
    ``self._recorder._recent_rms_values.append(chunk_rms)`` lines work
    with real semantics (a MagicMock auto-mock for either would NOT
    support the with-statement / append contract the body relies on).
    """
    recorder = MagicMock(name="RecorderStub")
    pipeline = AudioPipeline(recorder)
    # Helper mocks (return values tested by orchestration tests). The
    # methods are shadowed on the pipeline INSTANCE with MagicMocks —
    # bound methods don't accept attribute assignment.
    pipeline.detect_device_disconnect = MagicMock(return_value=detect_disconnect_returns)
    pipeline.handle_xrun_status = MagicMock(return_value=handle_xrun_returns)
    pipeline.apply_filter_chain = MagicMock(return_value=_FILTERED)
    pipeline.append_to_buffer_locked = MagicMock(return_value=(_CHUNK_COUNT, _BUFFER_LEN))
    pipeline.compute_rms_and_peak = MagicMock(
        return_value=(
            _CHUNK_RMS,
            _CHUNK_PEAK,
            _CHUNK_DURATION,
        )
    )
    pipeline.run_vad_state_machine = MagicMock()
    pipeline.detect_and_emit_clipping = MagicMock()
    # ``run_vad_state_machine`` and ``detect_and_emit_clipping`` are
    # no-op MagicMocks by default — the tests assert call counts / args.
    # Real lock so ``with recorder._lock:`` is a real context manager.
    recorder._lock = threading.Lock()
    # Real deque so ``recorder._recent_rms_values.append(chunk_rms)`` works.
    recorder._recent_rms_values = collections.deque(maxlen=10)
    # Writable mutable state — these are assigned by the orchestration
    # body and inspected by the tests.
    recorder._last_rms = None
    recorder._rms_callback_error_count = 0
    # Callbacks + recording-start — read outside the lock.
    recorder.on_rms_level = rms_callback
    recorder.on_silence_warning = None
    recorder.on_silence_auto_stop = None
    recorder.on_max_duration_auto_stop = None
    recorder._recording_start_time = recording_start
    return pipeline


# ── Orchestration: early-return paths ────────────────────────────────


class TestProcessAudioChunkEarlyReturns:
    """The two early-return paths (disconnect, XRUN) must short-circuit
    all downstream helpers."""

    def test_detect_disconnect_true_skips_all_other_helpers(self) -> None:
        pipeline = _make_pipeline_stub(detect_disconnect_returns=True)
        indata = np.zeros((512, 1), dtype=np.float32)

        pipeline.process_audio_chunk(indata, 512, None, 0, 12345.0)

        pipeline.detect_device_disconnect.assert_called_once_with(indata)
        pipeline.handle_xrun_status.assert_not_called()
        pipeline.apply_filter_chain.assert_not_called()
        pipeline.append_to_buffer_locked.assert_not_called()
        pipeline.compute_rms_and_peak.assert_not_called()
        pipeline.detect_and_emit_clipping.assert_not_called()
        pipeline.run_vad_state_machine.assert_not_called()
        # No RMS callback fired, no error count incremented.
        assert pipeline._recorder._rms_callback_error_count == 0
        # No deque append.
        assert list(pipeline._recorder._recent_rms_values) == []
        # _last_rms untouched.
        assert pipeline._recorder._last_rms is None

    def test_handle_xrun_true_skips_filter_and_buffer_helpers(self) -> None:
        pipeline = _make_pipeline_stub(
            detect_disconnect_returns=False,
            handle_xrun_returns=True,
        )

        indata = np.zeros((512, 1), dtype=np.float32)

        pipeline.process_audio_chunk(indata, 512, None, 2, 12345.0)

        pipeline.detect_device_disconnect.assert_called_once_with(indata)
        pipeline.handle_xrun_status.assert_called_once_with(2)
        pipeline.apply_filter_chain.assert_not_called()
        pipeline.append_to_buffer_locked.assert_not_called()
        pipeline.compute_rms_and_peak.assert_not_called()
        pipeline.detect_and_emit_clipping.assert_not_called()
        pipeline.run_vad_state_machine.assert_not_called()
        assert list(pipeline._recorder._recent_rms_values) == []
        assert pipeline._recorder._last_rms is None


# ── Orchestration: happy path ─────────────────────────────────────────


class TestProcessAudioChunkHappyPath:
    """The happy path calls every helper in the correct order, with the
    correct arguments threaded through the orchestration body."""

    def test_all_helpers_called_with_correct_args(self) -> None:
        rms_calls: list[tuple[float, float]] = []

        def rms_cb(rms: float, peak: float) -> None:
            rms_calls.append((rms, peak))

        pipeline = _make_pipeline_stub(
            detect_disconnect_returns=False,
            handle_xrun_returns=False,
            rms_callback=rms_cb,
        )

        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)
        perf_ts = 12345.6

        pipeline.process_audio_chunk(indata, 3, None, 0, perf_ts)

        # The 6 named helpers + clipping helper are all called via
        # ``self._recorder.X`` (preserves patch compatibility for
        # ``monkeypatch.setattr(Recorder, "_detect_device_disconnect", fake)``).
        pipeline.detect_device_disconnect.assert_called_once_with(indata)
        pipeline.handle_xrun_status.assert_called_once_with(0)
        pipeline.apply_filter_chain.assert_called_once_with(indata)
        pipeline.append_to_buffer_locked.assert_called_once_with(_FILTERED)
        pipeline.compute_rms_and_peak.assert_called_once_with(_FILTERED)
        # ``_detect_and_emit_clipping`` receives the chunk_peak value
        # returned by ``_compute_rms_and_peak`` (NOT a fixed 0.99
        # threshold — the helper itself owns the threshold check).
        pipeline.detect_and_emit_clipping.assert_called_once_with(pipeline._recorder, _CHUNK_PEAK)

        # ``_run_vad_state_machine`` receives the threaded-through args
        # in the exact positional order the body uses.
        pipeline.run_vad_state_machine.assert_called_once()
        args, _kwargs = pipeline.run_vad_state_machine.call_args
        assert args[0] is _FILTERED
        assert args[1] == _CHUNK_RMS
        assert args[2] == _CHUNK_DURATION
        assert args[3] == perf_ts
        assert args[4] == _CHUNK_COUNT
        assert args[5] == _BUFFER_LEN
        assert args[6] == _RECORDING_START
        assert args[7] is None  # silence_warning_cb
        assert args[8] is None  # silence_auto_stop_cb
        assert args[9] is None  # max_duration_cb

        # ``_last_rms`` is updated under the lock with chunk_rms.
        assert pipeline._recorder._last_rms == _CHUNK_RMS
        # ``_recent_rms_values`` deque appended chunk_rms.
        assert list(pipeline._recorder._recent_rms_values) == [_CHUNK_RMS]
        # RMS callback fired with the 2-arg signature (chunk_rms, chunk_peak).
        assert rms_calls == [(_CHUNK_RMS, _CHUNK_PEAK)]

    def test_run_vad_state_machine_threads_callbacks_through(self) -> None:
        """The callback refs (``on_silence_warning`` etc.) read outside
        the lock are passed through verbatim to
        ``_run_vad_state_machine``. A torn read just means we miss one
        callback; this test pins the threading contract."""

        def silence_warning_cb() -> None:
            pass

        def silence_auto_stop_cb() -> None:
            pass

        def max_duration_cb() -> None:
            pass

        def rms_cb(rms: float, peak: float) -> None:
            pass

        pipeline = _make_pipeline_stub(rms_callback=rms_cb)
        pipeline._recorder.on_silence_warning = silence_warning_cb
        pipeline._recorder.on_silence_auto_stop = silence_auto_stop_cb
        pipeline._recorder.on_max_duration_auto_stop = max_duration_cb

        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        args, _kwargs = pipeline.run_vad_state_machine.call_args
        assert args[7] is silence_warning_cb
        assert args[8] is silence_auto_stop_cb
        assert args[9] is max_duration_cb


# ── RMS callback contract ────────────────────────────────────────────


class TestProcessAudioChunkRmsCallbackContract:
    """The RMS callback (``on_rms_level``) contract:

    - Fired with the 2-arg signature ``(chunk_rms, chunk_peak)`` when set.
    - Not fired when ``None``.
    - Exceptions suppressed (NEW-CONC-004): traceback formatted only
      on the 1st occurrence and every 100th subsequent occurrence.
    """

    def test_no_callback_fired_when_on_rms_level_is_none(self) -> None:
        pipeline = _make_pipeline_stub(rms_callback=None)
        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        # Must not raise even though rms_callback is None.
        pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        # All helpers still called.
        pipeline.apply_filter_chain.assert_called_once()
        pipeline.run_vad_state_machine.assert_called_once()
        # Error counter unchanged (no callback invocation, no raise).
        assert pipeline._recorder._rms_callback_error_count == 0

    def test_first_callback_exception_logs_with_exc_info(self, caplog) -> None:
        def bad_cb(rms: float, peak: float) -> None:
            raise RuntimeError("boom")

        pipeline = _make_pipeline_stub(rms_callback=bad_cb)
        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        # Counter incremented exactly once.
        assert pipeline._recorder._rms_callback_error_count == 1
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records, "Expected at least one DEBUG log record"
        last = debug_records[-1]
        assert "occurrence #1" in last.getMessage()
        # First-occurrence log includes the formatted traceback.
        assert last.exc_info is not None

    def test_second_callback_exception_suppresses_traceback(self, caplog) -> None:
        def bad_cb(rms: float, peak: float) -> None:
            raise RuntimeError("boom")

        pipeline = _make_pipeline_stub(rms_callback=bad_cb)
        # Pre-existing first occurrence — next raise is occurrence #2.
        pipeline._recorder._rms_callback_error_count = 1

        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        assert pipeline._recorder._rms_callback_error_count == 2
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records, "Expected at least one DEBUG log record"
        last = debug_records[-1]
        assert "occurrence #2" in last.getMessage()
        assert "traceback suppressed" in last.getMessage()
        # Occurrences 2-99 must NOT include exc_info.
        assert last.exc_info is None

    def test_100th_callback_exception_logs_with_exc_info(self, caplog) -> None:
        def bad_cb(rms: float, peak: float) -> None:
            raise RuntimeError("boom")

        pipeline = _make_pipeline_stub(rms_callback=bad_cb)
        # Pre-existing 99 occurrences — next raise is occurrence #100.
        pipeline._recorder._rms_callback_error_count = 99

        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        assert pipeline._recorder._rms_callback_error_count == 100
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_records, "Expected at least one DEBUG log record"
        last = debug_records[-1]
        assert "occurrence #100" in last.getMessage()
        # Every 100th occurrence re-formats the traceback.
        assert last.exc_info is not None

    def test_repeated_callback_exceptions_keep_incrementing_counter(self, caplog) -> None:
        """5 successive chunks with a raising callback increment the
        counter to 5 and emit exactly 2 exc_info-bearing records (1st
        and … well, just the 1st since 5 < 100)."""

        def bad_cb(rms: float, peak: float) -> None:
            raise RuntimeError("boom")

        pipeline = _make_pipeline_stub(rms_callback=bad_cb)
        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.recording"):
            for _ in range(5):
                pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        assert pipeline._recorder._rms_callback_error_count == 5
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        with_exc_info = [r for r in debug_records if r.exc_info is not None]
        # Only the 1st occurrence (in this <100-occurrence run) carries
        # exc_info — the others use the "traceback suppressed" branch.
        assert len(with_exc_info) == 1, (
            f"Expected exactly 1 exc_info-bearing record (the 1st occurrence); got {len(with_exc_info)}"
        )


# ── Shared-state mutations ───────────────────────────────────────────


class TestProcessAudioChunkSharedStateMutations:
    """Verifies that the orchestration body writes to the expected
    shared-state attributes on the recorder."""

    def test_last_rms_updated_under_lock_with_chunk_rms(self) -> None:
        pipeline = _make_pipeline_stub(rms_callback=None)
        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        # ``_last_rms`` is the post-filter RMS returned by
        # ``_compute_rms_and_peak`` (the stub returns _CHUNK_RMS).
        assert pipeline._recorder._last_rms == _CHUNK_RMS

    def test_recent_rms_values_appended_with_chunk_rms(self) -> None:
        pipeline = _make_pipeline_stub(rms_callback=None)
        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        # Push 3 chunks; the deque should accumulate one entry per chunk.
        for _ in range(3):
            pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        assert list(pipeline._recorder._recent_rms_values) == [_CHUNK_RMS, _CHUNK_RMS, _CHUNK_RMS]

    def test_recent_rms_values_respects_maxlen(self) -> None:
        pipeline = _make_pipeline_stub(rms_callback=None)
        # Override the deque with a tighter maxlen to exercise the
        # bounded-queue contract.
        pipeline._recorder._recent_rms_values = collections.deque(maxlen=2)

        indata = np.array([[0.1], [-0.2], [0.3]], dtype=np.float32)

        for _ in range(5):
            pipeline.process_audio_chunk(indata, 3, None, 0, 12345.0)

        # Bounded at 2 — the oldest entries dropped.
        assert list(pipeline._recorder._recent_rms_values) == [_CHUNK_RMS, _CHUNK_RMS]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
