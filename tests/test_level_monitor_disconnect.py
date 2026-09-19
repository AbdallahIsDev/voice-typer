"""TY-4: level_monitor disconnect detection tests."""

from __future__ import annotations

import threading
import time

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
    """Wire a mock ``sd.InputStream`` capturing BOTH the audio callback"""
    import sounddevice as sd

    holder = {"callback": None, "finished_callback": None}

    class _Stream:
        def __init__(self, *args, **kwargs):
            holder["callback"] = kwargs.get("callback")
            holder["finished_callback"] = kwargs.get("finished_callback")

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


def _patch_event_bus_publish(monkeypatch):
    """Patch ``event_bus.publish`` to capture emitted events."""
    captured: list[dict] = []
    lock = threading.Lock()

    def _fake_publish(event: dict) -> bool:
        with lock:
            captured.append(dict(event))
        return True

    import voice_typer.server.event_bus as eb

    monkeypatch.setattr(eb, "publish", _fake_publish)
    return captured


class TestFinishedCallbackWiring:
    """TY-4: ``sd.InputStream`` is constructed with a ``finished_callback``."""

    def test_input_stream_receives_finished_callback(self, monkeypatch):
        """The ``sd.InputStream`` constructor is called with a"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)

        assert holder["finished_callback"] is not None, (
            "TY-4: sd.InputStream must be constructed with a "
            "finished_callback kwarg so PortAudio can signal device loss"
        )
        assert callable(holder["finished_callback"]), "TY-4: finished_callback must be a callable"

        lm.stop_monitoring()

    def test_finished_callback_sets_monitor_active_false(self, monkeypatch):
        """Invoking the captured finished_callback sets"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)
        assert lm._monitor_active is True, "fixture: monitor should be active"

        # Simulate PortAudio firing the finished_callback (device lost).
        holder["finished_callback"]()

        assert lm._monitor_active is False, "TY-4: _level_stream_finished must set _monitor_active=False"

        lm.stop_monitoring()


class TestDeviceLostEventEmitted:
    """TY-4: a ``device_lost`` IPC event is published on disconnect."""

    def test_finished_callback_emits_device_lost(self, monkeypatch):
        """The finished_callback path publishes a ``device_lost`` event"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # Fire the finished_callback (simulates PortAudio device-lost).
        holder["finished_callback"]()

        device_lost_events = [e for e in captured if e.get("type") == "device_lost"]
        assert len(device_lost_events) == 1, (
            f"TY-4: finished_callback must publish exactly one device_lost "
            f"event; got {len(device_lost_events)} (all events: {captured})"
        )
        evt = device_lost_events[0]
        assert evt["data"]["source"] == "stream_finished", (
            f"TY-4: source must be 'stream_finished' for the callback path; got {evt['data']['source']!r}"
        )

        lm.stop_monitoring()

    def test_zero_chunk_detector_emits_device_lost(self, monkeypatch):
        """The worker's zero-fill detector publishes a ``device_lost``"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        n = lm._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD
        zero_chunk = np.zeros((512, 1), dtype=np.float32)
        for _ in range(n):
            holder["callback"](zero_chunk, 512, None, None)

        # Wait for the worker thread to drain + publish.
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if any(e.get("type") == "device_lost" for e in captured):
                break
            time.sleep(0.01)

        device_lost_events = [e for e in captured if e.get("type") == "device_lost"]
        assert len(device_lost_events) >= 1, (
            f"TY-4: zero-chunk detector must publish a device_lost event "
            f"after {n} consecutive zero-filled chunks; got {captured}"
        )
        evt = device_lost_events[0]
        assert evt["data"]["source"] == "zero_chunks", (
            f"TY-4: source must be 'zero_chunks' for the worker path; got {evt['data']['source']!r}"
        )

        lm.stop_monitoring()

    def test_zero_chunk_detector_resets_on_nonzero_chunk(self, monkeypatch):
        """A non-zero chunk in the middle of a zero-run resets the"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        n = lm._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD
        zero_chunk = np.zeros((512, 1), dtype=np.float32)
        nonzero_chunk = np.ones((512, 1), dtype=np.float32) * 0.1

        # Push (n-1) zero chunks, then a non-zero chunk, then (n-1) more
        for _ in range(n - 1):
            holder["callback"](zero_chunk, 512, None, None)
        holder["callback"](nonzero_chunk, 512, None, None)
        for _ in range(n - 1):
            holder["callback"](zero_chunk, 512, None, None)

        # Wait briefly to let the worker drain.
        time.sleep(0.3)

        device_lost_events = [e for e in captured if e.get("type") == "device_lost"]
        assert len(device_lost_events) == 0, (
            f"TY-4: a non-zero chunk must reset the zero-chunk counter; "
            f"got {len(device_lost_events)} unexpected device_lost events"
        )

        lm.stop_monitoring()


class TestIdempotency:
    """TY-4: ``device_lost`` is emitted ONCE per disconnect episode."""

    def test_finished_callback_idempotent(self, monkeypatch):
        """Calling the finished_callback twice publishes only one"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # Fire the finished_callback twice (PortAudio shouldn't do this,
        holder["finished_callback"]()
        holder["finished_callback"]()

        device_lost_events = [e for e in captured if e.get("type") == "device_lost"]
        assert len(device_lost_events) == 1, (
            f"TY-4: finished_callback must be idempotent, _device_lost_emitted "
            f"guards re-entry; got {len(device_lost_events)} events"
        )

        lm.stop_monitoring()

    def test_zero_chunk_then_finished_callback_emits_once(self, monkeypatch):
        """If the zero-chunk detector fires AND THEN the finished_callback"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # Trigger zero-chunk detector first.
        n = lm._LEVEL_ZERO_CHUNK_DISCONNECT_THRESHOLD
        zero_chunk = np.zeros((512, 1), dtype=np.float32)
        for _ in range(n):
            holder["callback"](zero_chunk, 512, None, None)

        # Wait for the zero-chunk emit.
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if any(e.get("type") == "device_lost" for e in captured):
                break
            time.sleep(0.01)

        # Now fire the finished_callback (PortAudio tears down the stream).
        holder["finished_callback"]()

        # Wait briefly for any trailing publish.
        time.sleep(0.2)

        device_lost_events = [e for e in captured if e.get("type") == "device_lost"]
        assert len(device_lost_events) == 1, (
            f"TY-4: zero-chunk + finished_callback must coalesce to ONE "
            f"device_lost event (via _device_lost_emitted); got "
            f"{len(device_lost_events)} events"
        )

        lm.stop_monitoring()

    def test_device_lost_flag_cleared_on_restart(self, monkeypatch):
        """``_device_lost_emitted`` flag is cleared so a fresh disconnect"""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # First disconnect.
        holder["finished_callback"]()
        first_count = sum(1 for e in captured if e.get("type") == "device_lost")
        assert first_count == 1, "fixture: first disconnect should emit once"

        # Stop + restart.
        lm.stop_monitoring()
        holder2 = _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)

        assert lm._device_lost_emitted is False, (
            "TY-4: start_monitoring must clear _device_lost_emitted so a "
            "fresh disconnect can emit a new device_lost event"
        )

        # Second disconnect should emit a new event.
        holder2["finished_callback"]()
        second_count = sum(1 for e in captured if e.get("type") == "device_lost")
        assert second_count == 2, (
            f"TY-4: after restart, a new disconnect must emit a new "
            f"device_lost event; expected 2 total, got {second_count}"
        )

        lm.stop_monitoring()


class TestConstructorFailure:
    """``sd.InputStream(...)`` itself can raise (no device, PortAudio"""

    def test_constructor_failure_returns_clean_failure(self, monkeypatch):
        """A raising constructor yields ``success=False`` with cleared state."""
        import sounddevice as sd
        import voice_typer.server.level_monitor as lm

        def _raise(*args, **kwargs):
            raise OSError("No input devices available")

        monkeypatch.setattr(sd, "InputStream", _raise)
        monkeypatch.setattr(
            sd,
            "query_devices",
            lambda *args, **kwargs: {"name": "Mock Mic", "default_samplerate": 16000},
        )

        result = lm.start_monitoring(mic_id=None)

        assert result["success"] is False, f"constructor failure must return success=False; got {result}"
        assert "No input devices available" in result["message"]
        assert lm._monitor_active is False
        assert lm._monitor_stream is None
        assert lm._monitor_mic_id is None
