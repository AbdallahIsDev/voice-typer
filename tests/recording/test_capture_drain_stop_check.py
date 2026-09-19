"""``AudioCallbackDispatcher.audio_worker_loop`` drain-loop stop-event"""

from __future__ import annotations

import collections
import inspect
import threading
import time
from typing import Any

import numpy as np
import pytest
from voice_typer.server.recording import capture as capture_mod
from voice_typer.server.recording.capture import AudioCallbackDispatcher

# Mirror the production module-level constant. The source-inspection
DRAIN_STOP_CHECK_INTERVAL = capture_mod._DRAIN_STOP_CHECK_INTERVAL


class _SlowFakeRecorder:
    """Mock recorder that simulates a backlogged drain."""

    def __init__(
        self,
        *,
        ring_maxlen: int = 64,
        chunk_process_seconds: float = 0.02,
    ) -> None:
        self._ring_buffer: collections.deque = collections.deque(maxlen=ring_maxlen)
        self._worker_stop_event = threading.Event()
        self._worker_wake_event = threading.Event()
        self._process_audio_chunk_calls: list[tuple] = []
        self._process_audio_chunk_lock = threading.Lock()
        self._chunk_process_seconds = chunk_process_seconds

    def _process_audio_chunk(self, *args: Any) -> None:
        # Simulate RNNoise / VAD inference latency. This is what makes
        with self._process_audio_chunk_lock:
            self._process_audio_chunk_calls.append(args)
        if self._chunk_process_seconds > 0:
            time.sleep(self._chunk_process_seconds)


def _make_chunk(seq: int) -> tuple:
    """Build a 5-tuple chunk payload matching the ring-buffer shape."""
    return (np.zeros(4, dtype=np.float32), 4, f"t{seq}", f"s{seq}", float(seq))


class TestAudioWorkerLoopDrainStopCheck:
    """AB-2: drain loop must check the stop event between iterations."""

    def test_drain_bails_out_when_stop_set_mid_drain(self):
        """Backlogged drain (20 slow chunks) + stop signal set after"""
        chunk_process_seconds = 0.02
        fake = _SlowFakeRecorder(chunk_process_seconds=chunk_process_seconds)
        for i in range(20):
            fake._ring_buffer.append(_make_chunk(i))
        dispatcher = AudioCallbackDispatcher(fake)

        # Start the worker. It will wake (no wake needed, it waits
        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake,),
            name="test-ab2-worker",
            daemon=True,
        )
        t.start()

        time.sleep(chunk_process_seconds * 5)
        stop_set_at_calls = len(fake._process_audio_chunk_calls)

        # Signal stop. With the fix, the worker should bail out within
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()  # wake the wait() in case it's between iterations

        # Wait for the worker to exit. With the fix, this should take
        t.join(timeout=2.0)
        assert not t.is_alive(), (
            "audio_worker_loop did not exit within 2.0s of stop signal, "
            "AB-2 regression: drain loop is not checking the stop event"
        )

        total_processed = len(fake._process_audio_chunk_calls)
        post_stop_processed = total_processed - stop_set_at_calls

        tolerance = 1
        assert post_stop_processed <= DRAIN_STOP_CHECK_INTERVAL + tolerance, (
            f"AB-2 regression: worker processed {post_stop_processed} chunks "
            f"after stop signal was set (expected ≤ "
            f"{DRAIN_STOP_CHECK_INTERVAL + tolerance}). The drain loop is "
            f"not checking the stop event between iterations."
        )

        # Sanity: the worker should NOT have processed the full backlog
        assert total_processed < 20, (
            f"AB-2 regression: worker processed all {total_processed} chunks "
            f"instead of bailing out early, the drain loop never noticed "
            f"the stop signal."
        )

    def test_drain_completes_when_stop_not_set(self):
        """drain, the drain loop must still drain the ring buffer fully"""
        # Fast processing so the test runs quickly.
        fake = _SlowFakeRecorder(chunk_process_seconds=0.0)
        for i in range(10):
            fake._ring_buffer.append(_make_chunk(i))
        dispatcher = AudioCallbackDispatcher(fake)

        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake,),
            name="test-ab2-negative-worker",
            daemon=True,
        )
        t.start()
        fake._worker_wake_event.set()
        # Give the worker time to drain all 10 chunks.
        time.sleep(0.05)
        # Now stop cleanly.
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        # All 10 chunks processed (the stop signal didn't bail out
        assert len(fake._process_audio_chunk_calls) == 10, (
            "AB-2 negative contract violated: drain loop bailed out early even though stop was NOT set during the drain"
        )
        assert len(fake._ring_buffer) == 0

    def test_module_level_drain_interval_constant_is_four(self):
        """Source-inspection guard: the production"""
        assert hasattr(capture_mod, "_DRAIN_STOP_CHECK_INTERVAL"), (
            "AB-2: capture module must define the module-level ``_DRAIN_STOP_CHECK_INTERVAL`` constant."
        )
        assert capture_mod._DRAIN_STOP_CHECK_INTERVAL == 4, (
            f"AB-2: _DRAIN_STOP_CHECK_INTERVAL must be 4, got "
            f"{capture_mod._DRAIN_STOP_CHECK_INTERVAL}. If you changed "
            f"it, update this test and the DRAIN_STOP_CHECK_INTERVAL "
            f"mirror at the top of this file."
        )

    def test_drain_loop_source_contains_stop_event_check(self):
        """Source-inspection guard: the drain loop body must contain"""
        src = inspect.getsource(AudioCallbackDispatcher.audio_worker_loop)
        # Strip the outer-docstring portion (the method's docstring
        drain_marker = "Drain all available chunks"
        drain_idx = src.find(drain_marker)
        assert drain_idx != -1, "could not locate drain loop in source"
        drain_body = src[drain_idx:]
        # The drain body must include a stop-event check (the  fix).
        assert "_stop.is_set()" in drain_body, (
            "AB-2: the drain loop body must contain a "
            "``_stop.is_set()`` check between chunk "
            "iterations. WM-8 renamed the dynamic "
            "``_worker_stop_event`` to the effective ``_stop`` "
            "local, the check must not be removed."
        )
        # The drain body must also yield the GIL with time.sleep(0).
        assert "time.sleep(0)" in drain_body, (
            "AB-2: the drain loop body must yield the GIL with ``time.sleep(0)`` every N chunks to reduce CPU burn."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--timeout=30"]))
