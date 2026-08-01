"""TY-18: ``mic_level`` push event publishing tests.

Verifies that ``voice_typer.server.level_monitor`` publishes
``{type: "mic_level", data: {level, peak, active}}`` events via
``event_bus.publish`` (so Agent C's ``useMicrophoneTest`` can subscribe
to a push event instead of polling the backend at 10 Hz).

Specifically:

1. ``mic_level`` events are published when monitoring is ACTIVE (the
   level worker thread enqueues them; the mic_level worker thread
   drains + publishes).
2. ``mic_level`` events are NOT published when monitoring is INACTIVE
   (the early-return in ``_process_level_chunk`` short-circuits before
   the ``_push_mic_level`` call site).
3. Coalescing to ≤30 Hz works — calling ``_push_mic_level`` 100 times
   in <33ms publishes AT MOST a handful of events (not 100).
4. The published payload shape matches the spec
   (``{type, data: {level, peak, active}}``).
5. ``active`` field reflects the current ``_monitor_active`` value.

All ``sounddevice`` calls are mocked so the tests run on any platform.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Test fixtures
# ═══════════════════════════════════════════════════════════════════════════


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
    # reset disconnect-detection state.
    lm._consecutive_zero_chunks = 0
    lm._device_lost_emitted = False
    # Stop any worker threads from a previous test.
    lm._stop_level_worker()
    lm._stop_mic_level_worker()
    # clear the mic_level queue + reset throttle timestamp so a
    # previous test's last-push time doesn't suppress the first push
    # in this test.
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


def _patch_event_bus_publish(monkeypatch):
    """Patch ``event_bus.publish`` to capture emitted events.

    Returns a list (thread-safe via a lock) that the patched publish
    appends to.
    """
    captured: list[dict] = []
    lock = threading.Lock()

    def _fake_publish(event: dict) -> bool:
        with lock:
            captured.append(dict(event))
        return True

    import voice_typer.server.event_bus as eb

    monkeypatch.setattr(eb, "publish", _fake_publish)
    return captured


def _wait_for_event_count(captured, predicate, expected, timeout=2.0):
    """Poll ``captured`` until ``predicate`` matches ``expected`` count
    or ``timeout`` elapses."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        count = sum(1 for e in captured if predicate(e))
        if count >= expected:
            return True
        time.sleep(0.005)
    return False


# ═══════════════════════════════════════════════════════════════════════════
# mic_level events published when monitoring is active
# ═══════════════════════════════════════════════════════════════════════════


class TestMicLevelPublishedWhenActive:
    """TY-18: ``mic_level`` events are published when monitoring is ACTIVE."""

    def test_single_chunk_emits_mic_level(self, monkeypatch):
        """Processing one chunk emits one ``mic_level`` event."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)

        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        holder["callback"](chunk, 512, None, None)

        ok = _wait_for_event_count(
            captured,
            lambda e: e.get("type") == "mic_level",
            expected=1,
            timeout=2.0,
        )
        assert ok, f"TY-18: a single chunk must produce a mic_level event; captured={captured}"

        mic_level_events = [e for e in captured if e.get("type") == "mic_level"]
        evt = mic_level_events[0]
        # Payload shape: {type, data: {level, peak, active}}.
        assert "data" in evt, f"TY-18: mic_level event must have a 'data' field; got {evt}"
        data = evt["data"]
        assert "level" in data, f"TY-18: data.level missing; got {data}"
        assert "peak" in data, f"TY-18: data.peak missing; got {data}"
        assert "active" in data, f"TY-18: data.active missing; got {data}"
        # active must reflect _monitor_active (True when monitoring).
        assert data["active"] is True, (
            f"TY-18: data.active must be True when monitoring is active; got {data['active']}"
        )
        # level/peak are floats.
        assert isinstance(data["level"], float), f"TY-18: data.level must be a float; got {type(data['level'])}"
        assert isinstance(data["peak"], float), f"TY-18: data.peak must be a float; got {type(data['peak'])}"

        lm.stop_monitoring()

    def test_payload_values_match_chunk(self, monkeypatch):
        """The published ``level`` (rms) and ``peak`` values match the
        chunk's RMS and peak (computed via the TY-17 AUDIO-NP path)."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm._level_processor = None  # hit the no-processor branch
        lm.start_monitoring(mic_id=None)

        # Chunk with known RMS=0.25, peak=0.25.
        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        holder["callback"](chunk, 512, None, None)

        ok = _wait_for_event_count(
            captured,
            lambda e: e.get("type") == "mic_level",
            expected=1,
            timeout=2.0,
        )
        assert ok

        evt = next(e for e in captured if e.get("type") == "mic_level")
        flat = chunk.ravel()
        expected_rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
        expected_peak = max(float(flat.max()), -float(flat.min()))
        assert abs(evt["data"]["level"] - expected_rms) < 1e-6, (
            f"TY-18: data.level {evt['data']['level']} != expected rms {expected_rms}"
        )
        assert abs(evt["data"]["peak"] - expected_peak) < 1e-6, (
            f"TY-18: data.peak {evt['data']['peak']} != expected peak {expected_peak}"
        )

        lm.stop_monitoring()


# ═══════════════════════════════════════════════════════════════════════════
# mic_level NOT published when monitoring is inactive
# ═══════════════════════════════════════════════════════════════════════════


class TestMicLevelNotPublishedWhenInactive:
    """TY-18: ``mic_level`` events are NOT published when monitoring is
    inactive (the early-return in ``_process_level_chunk`` short-circuits
    before the ``_push_mic_level`` call site)."""

    def test_no_mic_level_when_monitor_inactive(self, monkeypatch):
        """If ``_monitor_active`` is False, processing a chunk does NOT
        emit a ``mic_level`` event."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        # DO NOT call start_monitoring — _monitor_active stays False.

        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        # Call _process_level_chunk directly (bypass the ring buffer
        # so we don't need the level worker running).
        lm._process_level_chunk(chunk, None)

        # Allow any deferred publishes to land.
        time.sleep(0.1)
        mic_level_events = [e for e in captured if e.get("type") == "mic_level"]
        assert len(mic_level_events) == 0, (
            f"TY-18: mic_level must NOT be published when monitoring is inactive; got {mic_level_events}"
        )

    def test_no_mic_level_after_stop_monitoring(self, monkeypatch):
        """After ``stop_monitoring``, processing a chunk does NOT emit a
        ``mic_level`` event."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        captured = _patch_event_bus_publish(monkeypatch)
        lm.start_monitoring(mic_id=None)
        # Stop monitoring (sets _monitor_active=False).
        lm.stop_monitoring()

        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        lm._process_level_chunk(chunk, None)
        time.sleep(0.1)

        mic_level_events = [e for e in captured if e.get("type") == "mic_level"]
        assert len(mic_level_events) == 0, (
            f"TY-18: mic_level must NOT be published after stop_monitoring; got {mic_level_events}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# coalescing to ≤30 Hz
# ═══════════════════════════════════════════════════════════════════════════


class TestCoalescing30Hz:
    """TY-18: ``_push_mic_level`` coalesces to ≤30 Hz via a monotonic-clock
    gate. Calling it 100 times in <33ms publishes AT MOST a few events."""

    def test_push_mic_level_coalesces_rapid_calls(self, monkeypatch):
        """Calling ``_push_mic_level`` 100 times back-to-back (no time
        gap) results in AT MOST 1 queued event (the first call sets the
        last-push timestamp; subsequent calls within the 33ms window are
        suppressed)."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        _patch_event_bus_publish(monkeypatch)
        lm._mic_level_last_push_ts = 0.0  # ensure first call passes the gate

        # Call _push_mic_level 100 times in a tight loop. Total elapsed
        # time is well under the 33ms coalesce window.
        for _ in range(100):
            lm._push_mic_level(0.5, 0.7, True)

        # At most 1 event should be in the queue (the first call passes
        # the gate; subsequent 99 are suppressed within the 33ms window).
        # (Could be 0 if the loop somehow took >33ms — unlikely but
        # allowed; the test asserts the upper bound.)
        queued = len(lm._mic_level_queue)
        assert queued <= 1, (
            f"TY-18: 100 back-to-back _push_mic_level calls must coalesce "
            f"to ≤1 queued event (30 Hz gate); got qsize={queued}"
        )

    def test_push_mic_level_emits_after_coalesce_window(self, monkeypatch):
        """After sleeping > 33ms, a new ``_push_mic_level`` call passes
        the gate and enqueues an event."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        _patch_event_bus_publish(monkeypatch)
        lm._mic_level_last_push_ts = 0.0

        # First call passes the gate.
        lm._push_mic_level(0.5, 0.7, True)
        first_qsize = len(lm._mic_level_queue)
        assert first_qsize == 1, f"TY-18: first call must enqueue; got qsize={first_qsize}"

        # Immediate second call is suppressed.
        lm._push_mic_level(0.5, 0.7, True)
        assert len(lm._mic_level_queue) == 1, "TY-18: second call within coalesce window must be suppressed"

        # Sleep past the 30 Hz window (33.3ms) + margin.
        time.sleep(lm._MIC_LEVEL_COALESCE_SEC + 0.005)

        # Third call passes the gate again.
        lm._push_mic_level(0.5, 0.7, True)
        assert len(lm._mic_level_queue) == 2, (
            f"TY-18: call after coalesce window must enqueue; got qsize={len(lm._mic_level_queue)}"
        )

    def test_coalesced_rate_under_load(self, monkeypatch):
        """Under sustained chunk-rate load (~94 Hz at 48 kHz / 512), the
        ``mic_level`` publish rate stays ≤30 Hz.

        We can't easily simulate a 48 kHz device without real audio, so
        we call ``_push_mic_level`` in a tight loop for 200ms and verify
        the queue growth is bounded by ~30 Hz * 0.2s = ~6 events (with
        some margin for timing jitter).
        """
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        _patch_event_bus_publish(monkeypatch)
        lm._mic_level_last_push_ts = 0.0

        start = time.monotonic()
        elapsed = 0.0
        call_count = 0
        while elapsed < 0.2:  # 200ms
            lm._push_mic_level(0.5, 0.7, True)
            call_count += 1
            elapsed = time.monotonic() - start

        # Queue growth is bounded by the 30 Hz gate. Over 200ms, at most
        # ~7 events should pass (30 Hz * 0.2s = 6, +1 for the initial).
        # Allow generous margin for timing jitter (we care about the
        # upper bound, not exact count).
        queued = len(lm._mic_level_queue)
        upper_bound = int(0.2 / lm._MIC_LEVEL_COALESCE_SEC) + 2  # +2 margin
        assert queued <= upper_bound, (
            f"TY-18: queue growth ({queued}) over 200ms must be ≤ {upper_bound} "
            f"events (30 Hz coalesce gate); call_count={call_count}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# worker thread lifecycle
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkerLifecycle:
    """TY-18: the mic_level push worker thread is started by
    ``start_monitoring`` and stopped by ``stop_monitoring`` (idempotent)."""

    def test_worker_started_on_start_monitoring(self, monkeypatch):
        """``start_monitoring`` starts the mic_level push worker thread."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        assert lm._mic_level_worker_thread is None, "fixture: worker not running"

        lm.start_monitoring(mic_id=None)
        assert lm._mic_level_worker_thread is not None, "TY-18: start_monitoring must start the mic_level push worker"
        assert lm._mic_level_worker_thread.is_alive(), (
            "TY-18: mic_level push worker must be alive after start_monitoring"
        )

        lm.stop_monitoring()

    def test_worker_stopped_on_stop_monitoring(self, monkeypatch):
        """``stop_monitoring`` stops + joins the mic_level push worker."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)
        thread_ref = lm._mic_level_worker_thread
        lm.stop_monitoring()

        assert lm._mic_level_worker_thread is None, "TY-18: stop_monitoring must clear _mic_level_worker_thread"
        # The thread should have exited (joined with timeout=1.0).
        if thread_ref is not None:
            assert not thread_ref.is_alive(), "TY-18: mic_level push worker must be stopped after stop_monitoring"

    def test_worker_idempotent_restart(self, monkeypatch):
        """Calling ``_ensure_mic_level_worker_running`` twice doesn't
        spawn a second worker (idempotent)."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._ensure_mic_level_worker_running()
        first_thread = lm._mic_level_worker_thread
        assert first_thread is not None

        lm._ensure_mic_level_worker_running()
        assert lm._mic_level_worker_thread is first_thread, (
            "TY-18: _ensure_mic_level_worker_running must be idempotent — "
            "calling it twice must NOT spawn a second worker thread"
        )

        lm._stop_mic_level_worker()
