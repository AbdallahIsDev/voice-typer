"""Tests for :meth:`AudioPipeline.detect_device_disconnect`.

The disconnect detector guards HOTKEY-CRASH: when a USB/BT mic is
unplugged mid-recording, PortAudio delivers zero-filled input frames
because the stream is still "open" but the device is gone. The
detector must:

1. Fire **once** (and only once) when zero-filled input appears after
   the warmup window (``_chunk_count > 10``). Pre-fix, the re-entrancy
   guard was missing and every subsequent zero chunk spawned a fresh
   ``device-disconnect-handler`` thread — ~89 threads for 100 zero
   chunks.
2. NOT fire during a deliberate ``stop()`` drain. ``stop()`` clears
   ``_recording_event`` before the stream is torn down; PortAudio may
   deliver zero-filled frames during that drain. Those MUST NOT be
   treated as a disconnect (the handler would race with the stop to
   close the stream).

The tests exercise :class:`AudioPipeline` directly with a
``MagicMock`` recorder stub that supplies the disconnect-relevant
state (``_chunk_count``, ``_device_disconnected``,
``_disconnect_handler_running``, ``_recording_event``,
``_stop_generation``) and a ``MagicMock`` for
``_spawn_device_thread`` so no real thread is launched. No real
PortAudio / sounddevice is touched.
"""

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
    """Build a MagicMock ``Recorder`` stub with the disconnect state.

    The stub exposes exactly the attributes that
    ``AudioPipeline.detect_device_disconnect`` reads / writes:

    - ``_chunk_count`` — buffer-chunk count, gates the warmup window
      (the detector only fires when ``> 10``).
    - ``_device_disconnected`` — re-entrancy flag, set to True on first
      detection and cleared by the disconnect handler on successful
      restart.
    - ``_disconnect_handler_running`` — single-flight guard for the
      handler thread.
    - ``_recording_event`` — real ``threading.Event``; the detector
      double-checks it's still set before treating zeros as a real
      disconnect (a deliberate ``stop()`` clears the event).
    - ``_stop_generation`` — captured into the handler kwargs so the
      handler can bail if a stop/start cycle happened in between.
    - ``_spawn_device_thread`` — MagicMock so we can assert it was
      called exactly once and inspect the kwargs.

    ``recording_active=False`` clears the event so tests can simulate
    the deliberate-stop drain path (zeros during stop() teardown).
    """
    recorder = MagicMock(name="RecorderStub")
    recorder._chunk_count = chunk_count
    recorder._devices._device_disconnected = False
    recorder._disconnect_handler_running = False
    recorder._recording_event = threading.Event()
    if recording_active:
        recorder._recording_event.set()
    recorder._stop_generation = 0
    # MagicMock so the test can assert call count + kwargs without
    # spawning a real device-disconnect-handler thread.
    recorder._spawn_device_thread = MagicMock(return_value=True)
    # ``_handle_device_disconnect`` is referenced by the spawn kwargs
    # — it must be a real callable target (MagicMock is fine since
    # the spawn mock swallows the call without invoking it).
    recorder._handle_device_disconnect = MagicMock(name="_handle_device_disconnect")
    return recorder


def _zero_chunk(n: int = 512) -> np.ndarray:
    """Return a zero-filled ``(n, 1)`` float32 array (simulates a
    PortAudio input callback delivering no signal after a USB/BT
    disconnect)."""
    return np.zeros((n, 1), dtype=np.float32)


# ── 1. zero-filled indata after warmup triggers disconnect ──────────


class TestZeroFilledIndataTriggersDisconnect:
    """After the warmup window (``_chunk_count > 10``), zero-filled
    input must set the disconnect flag and spawn the handler thread
    exactly once."""

    def test_zero_filled_indata_after_warmup_triggers_disconnect(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = AudioPipeline(recorder)

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        # The detector returns True so the caller (process_audio_chunk)
        # skips the rest of the pipeline for this chunk.
        assert ret is True
        # The disconnect flag was set — the re-entrancy guard for
        # subsequent chunks relies on this.
        assert recorder._devices._device_disconnected is True
        # The single-flight guard was cleared before the spawn so a
        # fresh handler can run even if a prior one hasn't fully
        # exited yet (mirrors the production code comment).
        assert recorder._disconnect_handler_running is False
        # The handler was scheduled exactly once via
        # ``_spawn_device_thread`` (registered with the thread
        # registry + single-flight guarded).
        assert recorder._spawn_device_thread.call_count == 1
        # The spawn kwargs carry the captured stop_generation so the
        # handler can bail if a stop/start cycle happened in between.
        _args, kwargs = recorder._spawn_device_thread.call_args
        assert kwargs["name"] == "device-disconnect-handler"
        assert kwargs["target"] is recorder._handle_device_disconnect
        assert kwargs["single_flight"] is True
        assert kwargs["kwargs"] == {"_captured_generation": 0}


# ── 2. deliberate stop does NOT trigger disconnect ──────────────────


class TestDeliberateStopDoesNotTriggerDisconnect:
    """When ``stop()`` clears ``_recording_event`` and PortAudio drains
    zero-filled frames during teardown, the detector MUST NOT treat
    those as a disconnect (would race with the deliberate stop to
    close the stream)."""

    def test_deliberate_stop_does_not_trigger_disconnect(self) -> None:
        # Simulate stop() having cleared the event before the drain.
        recorder = _make_disconnect_recorder_stub(
            chunk_count=20,
            recording_active=False,
        )
        pipeline = AudioPipeline(recorder)

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        # The detector still returns True (the chunk is zero-filled —
        # the caller skips the rest of the pipeline for this drain
        # chunk either way), but...
        assert ret is True
        # ...the disconnect flag was NOT set (this is a deliberate
        # stop drain, not a real disconnect).
        assert recorder._devices._device_disconnected is False
        # ...the handler was NOT scheduled (no race with the stop()).
        assert recorder._spawn_device_thread.call_count == 0


# ── 3. disconnect handler spawned exactly once (not 89x) ────────────


class TestDisconnectHandlerSpawnedOnce:
    """Pre-fix, every subsequent zero-filled chunk after the warmup
    window re-entered the spawn block and launched a fresh
    ``device-disconnect-handler`` thread — ~89 threads for 100 zero
    chunks. The re-entrancy guard (``_device_disconnected`` flag)
    must ensure the handler is spawned EXACTLY ONCE for a sustained
    zero-input window."""

    def test_disconnect_handler_spawned_once_not_89x(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = AudioPipeline(recorder)

        # Simulate a sustained disconnect: 100 zero-filled callbacks
        # in a row (≈6 seconds at 16 Hz — the device is gone, the
        # callback keeps delivering zeros).
        for _ in range(100):
            pipeline.detect_device_disconnect(_zero_chunk())

        # The flag stays set for the entire window (the handler
        # clears it on successful restart; here no restart happens so
        # it stays True).
        assert recorder._devices._device_disconnected is True
        # CRITICAL: the handler is spawned exactly ONCE — not 89
        # times. The re-entrancy guard short-circuits subsequent
        # chunks at the ``if recorder._devices._device_disconnected: return
        # True`` line BEFORE the spawn block.
        assert recorder._spawn_device_thread.call_count == 1

    def test_first_zero_after_warmup_window_only_triggers(self) -> None:
        """Boundary: the very first zero-filled chunk past the warmup
        window triggers; the next 50 zeros do NOT re-spawn."""
        recorder = _make_disconnect_recorder_stub(chunk_count=11)
        pipeline = AudioPipeline(recorder)

        # First zero past warmup — triggers.
        pipeline.detect_device_disconnect(_zero_chunk())
        assert recorder._devices._device_disconnected is True
        assert recorder._spawn_device_thread.call_count == 1

        # 50 more zeros — re-entrancy guard suppresses spawn.
        for _ in range(50):
            pipeline.detect_device_disconnect(_zero_chunk())

        assert recorder._devices._device_disconnected is True
        assert recorder._spawn_device_thread.call_count == 1


# ── 4. warmup window: zero chunks during warmup are ignored ────────


class TestWarmupWindowGuardsFalsePositive:
    """The first ~10 chunks after ``start()`` may legitimately be
    zero-filled (PortAudio priming the input buffer / device spin-up).
    The detector must NOT fire during this warmup window."""

    def test_zero_chunk_during_warmup_does_not_trigger(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=5)
        pipeline = AudioPipeline(recorder)

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        # Returns False — caller proceeds with normal pipeline (the
        # zero chunk is appended to the buffer as if it were audio).
        assert ret is False
        assert recorder._devices._device_disconnected is False
        assert recorder._spawn_device_thread.call_count == 0

    def test_boundary_chunk_count_equal_10_does_not_trigger(self) -> None:
        """The gate is ``> 10`` (strict), so chunk_count == 10 is
        still warmup."""
        recorder = _make_disconnect_recorder_stub(chunk_count=10)
        pipeline = AudioPipeline(recorder)

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        assert ret is False
        assert recorder._devices._device_disconnected is False

    def test_boundary_chunk_count_equal_11_triggers(self) -> None:
        """chunk_count == 11 is the first value past the strict
        ``> 10`` gate."""
        recorder = _make_disconnect_recorder_stub(chunk_count=11)
        pipeline = AudioPipeline(recorder)

        ret = pipeline.detect_device_disconnect(_zero_chunk())

        assert ret is True
        assert recorder._devices._device_disconnected is True
        assert recorder._spawn_device_thread.call_count == 1


# ── 5. non-zero indata never triggers ───────────────────────────────


class TestNonZeroIndataNeverTriggers:
    """Non-zero audio (the normal case) must never set the disconnect
    flag or spawn the handler."""

    def test_non_zero_indata_returns_false(self) -> None:
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = AudioPipeline(recorder)

        # A 0.5-amplitude 440 Hz sine — normal audio.
        t = np.linspace(0, 512 / 16000, 512, endpoint=False)
        indata = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)
        ret = pipeline.detect_device_disconnect(indata)

        assert ret is False
        assert recorder._devices._device_disconnected is False
        assert recorder._spawn_device_thread.call_count == 0

    def test_partially_zero_indata_does_not_trigger(self) -> None:
        """A chunk that has ANY non-zero element is NOT a disconnect
        (np.any short-circuits at the first non-zero sample)."""
        recorder = _make_disconnect_recorder_stub(chunk_count=20)
        pipeline = AudioPipeline(recorder)

        # Mostly zero, with a single non-zero sample — still
        # legitimate audio (a transient or a low-amplitude tail).
        indata = _zero_chunk()
        indata[100, 0] = 0.001
        ret = pipeline.detect_device_disconnect(indata)

        assert ret is False
        assert recorder._devices._device_disconnected is False
        assert recorder._spawn_device_thread.call_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov", "--timeout=30"])
