"""Tests for :meth:`AudioPipeline.detect_device_disconnect`."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording.audio_pipeline import AudioPipeline


def _make_disconnect_recorder_stub(
    *,
    chunk_count: int = 20,
    recording_active: bool = True,
) -> MagicMock:
    """Build a MagicMock ``Recorder`` stub with the disconnect state."""
    recorder = MagicMock(name="RecorderStub")
    # STATE-OWNERSHIP: the chunk counter is owned by the real
    recorder._audio_pipeline = AudioPipeline(recorder)
    recorder._audio_pipeline._chunk_count = chunk_count
    recorder._devices._device_disconnected = False
    recorder._disconnect_handler._single_flight_running = False
    recorder._recording_event = threading.Event()
    if recording_active:
        recorder._recording_event.set()
    recorder._stop_generation = 0
    # MagicMock so the test can assert call count + kwargs without
    recorder._spawn_device_thread = MagicMock(return_value=True)
    # ``_handle_device_disconnect`` is referenced by the spawn kwargs
    recorder._handle_device_disconnect = MagicMock(name="_handle_device_disconnect")
    return recorder


def _zero_chunk(n: int = 512) -> np.ndarray:
    """PortAudio input callback delivering no signal after a USB/BT"""
    return np.zeros((n, 1), dtype=np.float32)


class TestZeroFilledIndataTriggersDisconnect:
    """After the warmup window (``_chunk_count > 10``), zero-filled"""

    def test_zero_filled_indata_after_warmup_triggers_disconnect(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = recorder._audio_pipeline

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        # The detector returns True so the caller (process_audio_chunk)
        assert ret is True
        assert recorder._devices._device_disconnected is True
        assert recorder._disconnect_handler._single_flight_running is False
        # The handler was scheduled exactly once via
        assert recorder._spawn_device_thread.call_count == 1
        _args, kwargs = recorder._spawn_device_thread.call_args
        assert kwargs["name"] == "device-disconnect-handler"
        assert kwargs["target"] is recorder._handle_device_disconnect
        assert kwargs["single_flight"] is True
        assert kwargs["kwargs"] == {"_captured_generation": 0}


class TestDeliberateStopDoesNotTriggerDisconnect:
    """When ``stop()`` clears ``_recording_event`` and PortAudio drains"""

    def test_deliberate_stop_does_not_trigger_disconnect(self) -> None:
        # Simulate stop() having cleared the event before the drain.
        recorder = _make_disconnect_recorder_stub(
            chunk_count=20,
            recording_active=False,
        )
        pipeline = recorder._audio_pipeline

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        # The detector still returns True (the chunk is zero-filled —
        assert ret is True
        # ...the disconnect flag was NOT set (this is a deliberate
        assert recorder._devices._device_disconnected is False
        # ...the handler was NOT scheduled (no race with the stop()).
        assert recorder._spawn_device_thread.call_count == 0


class TestDisconnectHandlerSpawnedOnce:
    """Pre-fix, every subsequent zero-filled chunk after the warmup"""

    def test_disconnect_handler_spawned_once_not_89x(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = recorder._audio_pipeline

        # Simulate a sustained disconnect: 100 zero-filled callbacks
        for _ in range(100):
            pipeline.detect_device_disconnect(_zero_chunk())

        # The flag stays set for the entire window (the handler
        assert recorder._devices._device_disconnected is True
        # CRITICAL: the handler is spawned exactly ONCE, not 89
        assert recorder._spawn_device_thread.call_count == 1

    def test_first_zero_after_warmup_window_only_triggers(self) -> None:
        """Boundary: the very first zero-filled chunk past the warmup"""
        recorder = _make_disconnect_recorder_stub(chunk_count=11)
        pipeline = recorder._audio_pipeline

        # First zero past warmup, triggers.
        pipeline.detect_device_disconnect(_zero_chunk())
        assert recorder._devices._device_disconnected is True
        assert recorder._spawn_device_thread.call_count == 1

        # 50 more zeros, re-entrancy guard suppresses spawn.
        for _ in range(50):
            pipeline.detect_device_disconnect(_zero_chunk())

        assert recorder._devices._device_disconnected is True
        assert recorder._spawn_device_thread.call_count == 1


class TestWarmupWindowGuardsFalsePositive:
    """
    The first ~10 chunks after ``start()`` may legitimately be
    The detector must NOT fire during this warmup window.
    """

    def test_zero_chunk_during_warmup_does_not_trigger(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=5)
        pipeline = recorder._audio_pipeline

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        assert ret is False
        assert recorder._devices._device_disconnected is False
        assert recorder._spawn_device_thread.call_count == 0

    def test_boundary_chunk_count_equal_10_does_not_trigger(self) -> None:
        """The gate is ``> 10`` (strict), so chunk_count == 10 is"""
        recorder = _make_disconnect_recorder_stub(chunk_count=10)
        pipeline = recorder._audio_pipeline

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        assert ret is False
        assert recorder._devices._device_disconnected is False

    def test_boundary_chunk_count_equal_11_triggers(self) -> None:
        """chunk_count == 11 is the first value past the strict"""
        recorder = _make_disconnect_recorder_stub(chunk_count=11)
        pipeline = recorder._audio_pipeline

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        assert ret is True
        assert recorder._devices._device_disconnected is True
        assert recorder._spawn_device_thread.call_count == 1


class TestNonZeroIndataNeverTriggers:
    """Non-zero audio (the normal case) must never set the disconnect"""

    def test_non_zero_indata_returns_false(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = recorder._audio_pipeline

        # A 0.5-amplitude 440 Hz sine, normal audio.
        t = np.linspace(0, 512 / 16000, 512, endpoint=False)
        indata = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)
        ret = pipeline.detect_device_disconnect(indata)

        assert ret is False
        assert recorder._devices._device_disconnected is False
        assert recorder._spawn_device_thread.call_count == 0

    def test_partially_zero_indata_does_not_trigger(self) -> None:
        """A chunk that has ANY non-zero element is NOT a disconnect"""
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = recorder._audio_pipeline

        # Mostly zero, with a single non-zero sample, still
        indata = _zero_chunk()
        indata[100, 0] = 0.001
        ret = pipeline.detect_device_disconnect(indata)

        assert ret is False
        assert recorder._devices._device_disconnected is False
        assert recorder._spawn_device_thread.call_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov", "--timeout=30"])
