"""Idle-timeout push-event awareness + stop/reset hygiene tests.

Three behaviours are covered:

1. **Idle-timeout push-event awareness** — ``_idle_timeout_auto_stop``
   considers BOTH ``_last_get_level_poll_ts`` (updated by ``get_level``)
   AND ``_mic_level_last_push_ts`` (updated by ``_push_mic_level``). After
   the push-event migration, the Microphone page and the always-visible
   bubble consume ``mic_level`` push events and may only call
   ``get_level`` once on mount — so checking only the poll timestamp
   would falsely trip the idle timeout while the frontend is actively
   listening via push events.

2. **stop_monitoring resets the audio processor** —
   ``stop_monitoring`` calls ``_level_processor.reset()`` (best-effort,
   wrapped in ``contextlib.suppress(Exception)``) so the IIR ``zi``
   arrays + RNNoise ``_carry`` don't retain audio-derived residuals
   from this monitoring session and bleed into the next one. Mirrors
   the XZ-PRIV-01 pattern in ``recording/session_state.py`` for the
   dictation ``AudioProcessor``. The model itself stays loaded; only
   the per-session filter state is zeroed.

3. **Ring buffer cleared on worker stop / fresh start** —
   ``_stop_level_worker`` clears ``_level_ring_buffer`` after joining
   the worker thread, and ``_ensure_level_worker_running`` clears it
   before spawning a fresh worker. Mirrors the pattern in
   ``voice_typer/server/recording/capture.py``.

All ``sounddevice`` calls are mocked so the tests run on any platform
(no real audio hardware required).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

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
    # Reset disconnect-detection state.
    lm._consecutive_zero_chunks = 0
    lm._device_lost_emitted = False
    # Stop any worker threads from a previous test.
    lm._stop_level_worker()
    lm._stop_mic_level_worker()
    # Clear the mic_level queue + reset throttle timestamp so a
    # previous test's last-push time doesn't suppress the first push
    # in this test.
    while lm._mic_level_queue:
        try:
            lm._mic_level_queue.popleft()
        except Exception:
            break
    lm._mic_level_last_push_ts = 0.0
    # Reset the idle-timeout poll timestamp so a previous test's
    # value doesn't influence this test's idle-timeout check.
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
    """Wire a mock ``sd.InputStream`` capturing the audio callback.

    Returns a holder dict with the captured ``callback`` (for tests
    that need to invoke the callback directly).
    """
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


# ═══════════════════════════════════════════════════════════════════════════
# Idle-timeout push-event awareness
# ═══════════════════════════════════════════════════════════════════════════


class TestIdleTimeoutPushEventAwareness:
    """``_idle_timeout_auto_stop`` considers both the ``get_level`` poll
    timestamp AND the ``mic_level`` push-event timestamp — the more
    recent of the two governs the idle check.
    """

    def test_does_not_fire_when_push_event_is_recent(self):
        """When ``_mic_level_last_push_ts`` is within the idle window
        (even if ``_last_get_level_poll_ts`` is stale / never set),
        the idle timeout must NOT fire — the frontend is actively
        listening via push events."""
        import voice_typer.server.level_monitor as lm

        # Simulate: monitoring is active, no get_level poll has ever
        # been recorded, but a mic_level push event was published
        # just now (the frontend is consuming push events).
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
        """When BOTH ``_last_get_level_poll_ts`` AND
        ``_mic_level_last_push_ts`` are older than the idle window,
        the idle timeout MUST fire — the frontend has truly abandoned
        the stream."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)

        # Mock stream so stream.stop() / stream.close() are no-ops.
        stream = MagicMock()
        stream.stop = MagicMock()
        stream.close = MagicMock()

        # Simulate: monitoring is active, both timestamps are 120s ago
        # (well past the 60s idle window).
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
        """The MORE RECENT of the two timestamps governs. If the poll
        is recent but the push is stale (or vice versa), the stream
        stays alive."""
        import voice_typer.server.level_monitor as lm

        lm._monitor_active = True
        lm._monitor_stream = None
        lm._last_get_level_poll_ts = time.monotonic()  # recent poll
        lm._mic_level_last_push_ts = time.monotonic() - 120.0  # stale push

        result = lm._idle_timeout_auto_stop()

        assert result is False, "idle-timeout must NOT fire when EITHER timestamp is recent"
        assert lm._monitor_active is True


# ═══════════════════════════════════════════════════════════════════════════
# stop_monitoring resets the audio processor
# ═══════════════════════════════════════════════════════════════════════════


class TestStopMonitoringResetsProcessor:
    """``stop_monitoring`` calls ``_level_processor.reset()`` when the
    processor is set, and does NOT crash when it is None."""

    def test_reset_called_when_processor_set(self, monkeypatch):
        """When ``_level_processor`` is not None, ``stop_monitoring``
        calls ``reset()`` on it (best-effort)."""
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
        """When ``_level_processor`` is None, ``stop_monitoring`` must
        not crash (the reset call is guarded)."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)
        # Ensure no processor is set (the fixture already clears it,
        # but be explicit).
        lm._level_processor = None

        result = lm.stop_monitoring()

        assert result["success"] is True
        assert lm._monitor_active is False


# ═══════════════════════════════════════════════════════════════════════════
# Ring buffer cleared on worker stop / fresh start
# ═══════════════════════════════════════════════════════════════════════════


class TestRingBufferClearedOnWorkerLifecycle:
    """``_stop_level_worker`` and ``_ensure_level_worker_running`` both
    clear ``_level_ring_buffer`` so stale chunks from a previous
    session don't bleed into the next one. Mirrors the pattern in
    ``voice_typer/server/recording/capture.py``."""

    def test_stop_level_worker_clears_ring_buffer(self, monkeypatch):
        """``_stop_level_worker`` clears ``_level_ring_buffer`` after
        joining the worker thread."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        # Start a worker thread so _stop_level_worker exercises the
        # join-then-clear path (the early-return path when thread is
        # None doesn't run the clear).
        lm._ensure_level_worker_running()

        # Populate the ring buffer with a couple of chunks. The worker
        # may or may not drain these before stop completes — the
        # contract under test is that AFTER _stop_level_worker returns,
        # the buffer is empty (cleared by stop, or drained by the
        # worker's exit iteration — either way, empty).
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
        """``_ensure_level_worker_running`` clears ``_level_ring_buffer``
        when starting a fresh worker (i.e. when no live worker is
        already running)."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        # Pre-populate the ring buffer with stale chunks from a
        # hypothetical previous session. No worker is running (the
        # fixture stopped any leftover worker).
        lm._level_ring_buffer.append(
            (np.zeros((512, 1), dtype=np.float32), None),
        )
        lm._level_ring_buffer.append(
            (np.zeros((512, 1), dtype=np.float32), None),
        )
        assert len(lm._level_ring_buffer) == 2, "fixture: buffer pre-populated"

        # Start a fresh worker — _ensure_level_worker_running must
        # clear the buffer BEFORE spawning the thread.
        lm._ensure_level_worker_running()

        assert len(lm._level_ring_buffer) == 0, (
            "_ensure_level_worker_running must clear _level_ring_buffer on fresh worker start"
        )

        # Cleanup: stop the worker we just started.
        lm._stop_level_worker()
