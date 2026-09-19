"""JB-46 tests: level worker thread exits after idle-timeout auto-stop."""

from __future__ import annotations

import time

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
    # Stop any worker thread from a previous test.
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


def _wire_stream(monkeypatch):
    """Wire a mock ``sd.InputStream`` so ``start_monitoring`` succeeds."""
    import sounddevice as sd

    class _Stream:
        def __init__(self, *args, **kwargs):
            pass

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


def _wait_for_worker_exit(thread, timeout_sec: float = 3.0) -> bool:
    """Poll ``thread.is_alive()`` until False or timeout."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False  # exited, good
        time.sleep(0.02)
    return thread.is_alive()  # True if still alive (bad), False if exited


class TestWorkerExitsAfterIdleTimeout:
    """
    JB-46: the level worker thread exits after idle-timeout auto-stop.
    Pre-fix: the worker kept spinning at 4 Hz (250 ms backstop ``wait()``
    """

    def test_worker_thread_exits_after_idle_auto_stop(self, monkeypatch):
        """After ``_idle_timeout_auto_stop`` fires, the worker thread"""
        import voice_typer.server.level_monitor as lm

        _wire_stream(monkeypatch)

        # Start monitoring, this spawns the worker thread.
        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        worker_thread = lm._level_worker_thread
        assert worker_thread is not None, "worker thread should be spawned"
        assert worker_thread.is_alive(), "worker thread should be alive"

        # Simulate idle: set BOTH activity timestamps to be well past
        old_ts = time.monotonic() - 120.0
        lm._last_get_level_poll_ts = old_ts
        lm._mic_level_last_push_ts = old_ts

        # Wait for the worker's next iteration (backstop timeout is
        still_alive = _wait_for_worker_exit(worker_thread, timeout_sec=3.0)

        assert not still_alive, (
            "JB-46: worker thread should EXIT after _idle_timeout_auto_stop "
            "fires; pre-fix the worker kept spinning at 4 Hz indefinitely "
            "(battery drain). Post-fix the worker returns from its loop."
        )
        assert lm._level_worker_thread is None, (
            "JB-46: _level_worker_thread slot should be cleared (set to None "
            "by the worker before returning) so a concurrent "
            "start_monitoring call's _ensure_level_worker_running sees "
            "'no worker' and spawns a fresh one (race-safe restart)."
        )
        assert lm._monitor_active is False, "idle-timeout should have flipped _monitor_active to False"

    def test_worker_does_not_consume_cpu_after_idle_auto_stop(self, monkeypatch):
        """After idle-timeout, the worker thread is NOT alive, a dead"""
        import voice_typer.server.level_monitor as lm

        _wire_stream(monkeypatch)

        lm.start_monitoring(mic_id=None)
        worker_thread = lm._level_worker_thread
        assert worker_thread is not None and worker_thread.is_alive()

        # Trigger idle-timeout.
        old_ts = time.monotonic() - 120.0
        lm._last_get_level_poll_ts = old_ts
        lm._mic_level_last_push_ts = old_ts

        # Wait for the worker to exit.
        still_alive = _wait_for_worker_exit(worker_thread, timeout_sec=3.0)

        assert not still_alive, (
            "JB-46: worker thread must exit after idle-timeout, a dead thread consumes zero CPU (no 4 Hz wakeups)."
        )


class TestStartMonitoringSpawnsFreshWorkerAfterIdle:
    """
    JB-46: after the idle-timeout exits the worker, the next
    This is the user-visible contract: the level bar must work after
    """

    def test_start_monitoring_spawns_new_worker_after_idle(self, monkeypatch):
        """After idle-timeout exits the worker, ``start_monitoring``"""
        import voice_typer.server.level_monitor as lm

        _wire_stream(monkeypatch)

        # First start: spawns worker #1.
        result1 = lm.start_monitoring(mic_id=None)
        assert result1["success"] is True
        worker1 = lm._level_worker_thread
        assert worker1 is not None and worker1.is_alive()

        # Trigger idle-timeout → worker #1 exits.
        old_ts = time.monotonic() - 120.0
        lm._last_get_level_poll_ts = old_ts
        lm._mic_level_last_push_ts = old_ts

        still_alive = _wait_for_worker_exit(worker1, timeout_sec=3.0)
        assert not still_alive, "worker #1 should exit after idle-timeout"

        result2 = lm.start_monitoring(mic_id=None)
        assert result2["success"] is True, (
            "JB-46: start_monitoring must succeed after idle-timeout, "
            "the level bar must work when the app comes back from idle"
        )
        worker2 = lm._level_worker_thread
        assert worker2 is not None, "JB-46: a fresh worker thread must be spawned after idle-timeout"
        assert worker2 is not worker1, (
            "JB-46: the new worker must be a FRESH thread, not the dead one from the previous session"
        )
        assert worker2.is_alive(), (
            "JB-46: the fresh worker thread must be alive and ready to process chunks from the new stream"
        )

    def test_fresh_worker_processes_chunks_after_idle_restart(self, monkeypatch):
        """The fresh worker spawned after idle-timeout actually"""
        import numpy as np
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # First start + idle-timeout cycle.
        lm.start_monitoring(mic_id=None)
        worker1 = lm._level_worker_thread
        old_ts = time.monotonic() - 120.0
        lm._last_get_level_poll_ts = old_ts
        lm._mic_level_last_push_ts = old_ts
        still_alive = _wait_for_worker_exit(worker1, timeout_sec=3.0)
        assert not still_alive, "worker #1 should exit after idle-timeout"

        # Second start: spawn fresh worker.
        lm.start_monitoring(mic_id=None)
        worker2 = lm._level_worker_thread
        assert worker2 is not None and worker2.is_alive()
        assert worker2 is not worker1

        # Push a chunk via the captured callback and verify the worker
        chunk = np.ones((512, 1), dtype=np.float32) * 0.5
        holder["callback"](chunk, 512, None, None)

        # Wait for the worker to drain + process.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if lm._monitor_level > 0.0:
                break
            time.sleep(0.02)

        assert lm._monitor_level > 0.0, (
            "JB-46: the fresh worker spawned after idle-timeout must process "
            "audio chunks (the level bar must update). Pre-fix the worker "
            "stayed alive but did no useful work after idle; post-fix the "
            "exited worker is replaced by a fresh one that processes chunks."
        )
