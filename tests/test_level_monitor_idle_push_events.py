"""Idle-timeout push-event awareness + stop/reset hygiene tests."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _reset_level_monitor_state():
    """Reset all module-level state in level_monitor between tests."""
    import voice_typer.server.level_monitor as lm

    lm._test_mode = False
    lm._test_chunks.clear()
    lm._test_raw_chunks.clear()
    lm._test_filtered_chunks.clear()
    lm._test_start_time = 0.0
    lm._test_duration = 10.0
    lm._monitor_sample_rate = 16000
    lm._monitor_active = False
    lm._monitor_stream = None
    lm._monitor_level = 0.0
    lm._monitor_peak = 0.0
    lm._monitor_mic_id = None
    lm._level_processor = None
    lm._dropped_level_chunks = 0
    lm._last_drop_log_time = 0.0
    lm._level_ring_buffer.clear()
    # Reset disconnect-detection state.
    lm._consecutive_zero_chunks = 0
    lm._device_lost_emitted = False
    # Stop any worker threads from a previous test.
    lm._stop_level_worker()
    lm._stop_mic_level_worker()
    while lm._mic_level_queue:
        try:
            lm._mic_level_queue.popleft()
        except Exception:
            break
    lm._mic_level_last_push_ts = 0.0
    # Reset the idle-timeout poll timestamp so a previous test's
    lm._last_get_level_poll_ts = 0.0
    # Reset quality metrics.
    lm._test_peak_history.clear()
    lm._test_rms_history.clear()
    lm._test_clip_count = 0
    lm._test_silence_blocks = 0


@pytest.fixture(autouse=True)
def _reset_level_monitor():
    _reset_level_monitor_state()
    yield
    _reset_level_monitor_state()


def _wire_stream_with_callback_capture(monkeypatch):
    """Wire a mock ``sd.InputStream`` capturing the audio callback."""
    import sounddevice as sd

    holder = {"callback": None}

    class _Stream:
        def __init__(self, *args, **kwargs):
            holder["callback"] = kwargs.get("callback")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.InputStream = _Stream  # type: ignore[assignment]
    sd.query_devices.return_value = {
        "name": "Mock Mic",
        "default_samplerate": 16000,
        "max_input_channels": 1,
        "hostapi": 0,
    }
    return holder


class TestIdleTimeoutPushEventAwareness:
    """``_idle_timeout_auto_stop`` considers both the ``get_level`` poll"""

    def test_does_not_fire_when_push_event_is_recent(self):
        """When ``_mic_level_last_push_ts`` is within the idle window"""
        import voice_typer.server.level_monitor as lm

        # Simulate: monitoring is active, no get_level poll has ever
        lm._monitor_active = True
        lm._monitor_stream = None  # no real stream needed for the no-op path
        lm._last_get_level_poll_ts = 0.0
        lm._mic_level_last_push_ts = time.monotonic()

        result = lm._idle_timeout_auto_stop()

        assert result is False, (
            "idle-timeout must NOT fire when _mic_level_last_push_ts is recent "
            "(frontend actively listening via push events)"
        )
        assert lm._monitor_active is True, "stream must stay alive when push events are recent"

    def test_fires_when_both_timestamps_are_old(self, monkeypatch):
        """``_mic_level_last_push_ts`` are older than the idle window,"""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)

        # Mock stream so stream.stop() / stream.close() are no-ops.
        stream = MagicMock()
        stream.stop = MagicMock()
        stream.close = MagicMock()

        # Simulate: monitoring is active, both timestamps are 120s ago
        old_ts = time.monotonic() - 120.0
        lm._monitor_active = True
        lm._monitor_stream = stream
        lm._last_get_level_poll_ts = old_ts
        lm._mic_level_last_push_ts = old_ts

        result = lm._idle_timeout_auto_stop()

        assert result is True, "idle-timeout MUST fire when both timestamps are older than the idle window"
        assert lm._monitor_active is False, "stream must be marked inactive after idle-timeout auto-stop"
        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        # Timestamps reset so the next start_monitoring is clean.
        assert lm._last_get_level_poll_ts == 0.0
        assert lm._mic_level_last_push_ts == 0.0

    def test_does_not_fire_when_poll_is_recent_even_if_push_is_stale(self):
        """The MORE RECENT of the two timestamps governs. If the poll"""
        import voice_typer.server.level_monitor as lm

        lm._monitor_active = True
        lm._monitor_stream = None
        lm._last_get_level_poll_ts = time.monotonic()  # recent poll
        lm._mic_level_last_push_ts = time.monotonic() - 120.0  # stale push

        result = lm._idle_timeout_auto_stop()

        assert result is False, "idle-timeout must NOT fire when EITHER timestamp is recent"
        assert lm._monitor_active is True


class TestStopMonitoringResetsProcessor:
    """``stop_monitoring`` calls ``_level_processor.reset()`` when the"""

    def test_reset_called_when_processor_set(self, monkeypatch):
        """When ``_level_processor`` is not None, ``stop_monitoring``"""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # Inject a mock processor with a reset() method.
        processor = MagicMock()
        processor.reset = MagicMock()
        lm._level_processor = processor

        result = lm.stop_monitoring()

        assert result["success"] is True
        assert processor.reset.called, (
            "stop_monitoring must call _level_processor.reset() when _level_processor is not None"
        )

    def test_no_crash_when_processor_is_none(self, monkeypatch):
        """When ``_level_processor`` is None, ``stop_monitoring`` must"""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)
        # Ensure no processor is set (the fixture already clears it,
        lm._level_processor = None

        result = lm.stop_monitoring()

        assert result["success"] is True
        assert lm._monitor_active is False


class TestRingBufferClearedOnWorkerLifecycle:
    """``_stop_level_worker`` and ``_ensure_level_worker_running`` both"""

    def test_stop_level_worker_clears_ring_buffer(self, monkeypatch):
        """``_stop_level_worker`` clears ``_level_ring_buffer`` after"""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._ensure_level_worker_running()

        lm._level_ring_buffer.append(
            (np.zeros((512, 1), dtype=np.float32), None),
        )
        lm._level_ring_buffer.append(
            (np.zeros((512, 1), dtype=np.float32), None),
        )
        assert len(lm._level_ring_buffer) >= 1, "fixture: buffer populated"

        lm._stop_level_worker()

        assert len(lm._level_ring_buffer) == 0, "_stop_level_worker must clear _level_ring_buffer"

    def test_ensure_level_worker_running_clears_on_fresh_start(self, monkeypatch):
        """``_ensure_level_worker_running`` clears ``_level_ring_buffer``"""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._level_ring_buffer.append(
            (np.zeros((512, 1), dtype=np.float32), None),
        )
        lm._level_ring_buffer.append(
            (np.zeros((512, 1), dtype=np.float32), None),
        )
        assert len(lm._level_ring_buffer) == 2, "fixture: buffer pre-populated"

        # Start a fresh worker, _ensure_level_worker_running must
        lm._ensure_level_worker_running()

        assert len(lm._level_ring_buffer) == 0, (
            "_ensure_level_worker_running must clear _level_ring_buffer on fresh worker start"
        )

        # Cleanup: stop the worker we just started.
        lm._stop_level_worker()
