"""AB-2 (High) — regression tests for the
``AudioCallbackDispatcher.audio_worker_loop`` drain-loop stop-event
check.

Background
----------
``capture.py``'s ``audio_worker_loop`` has an inner drain loop that
pops + ``_process_audio_chunk`` for every queued chunk with NO
stop-event check inside the loop. When the worker falls behind
(RNNoise ~50ms/chunk vs 32ms arrival at 16 Hz), the 64-slot ring
buffer fills (~1s of headroom). The drain loop then burns 64 × 50ms =
3.2s of solid CPU before checking the stop event — orphaning the
daemon worker after ``stop()`` returns (the join timeout is 2.0s) and
delaying ``stop()`` latency by the same amount.

Fix
---
The drain loop now checks ``recorder._worker_stop_event.is_set()``
every ``_DRAIN_STOP_CHECK_INTERVAL`` (=4) chunks. On stop signal, it
breaks out of the drain immediately via ``return`` (sacrificing
in-flight audio — acceptable since ``drain=True`` is best-effort).

These tests
-----------
Use a mock recorder whose ``_process_audio_chunk`` sleeps for a
simulated per-chunk processing time (mimicking RNNoise latency) so the
AB-2 race is reproducible: a backlogged drain (many chunks queued, slow
processing) and a concurrent ``stop()`` call. Without the fix, the
worker would process every chunk before exiting; with the fix, the
worker exits within ≤ ``_DRAIN_STOP_CHECK_INTERVAL`` chunks of the stop
signal.
"""

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
# test ``test_module_level_drain_interval_constant_is_four`` guarantees
# this mirror stays in sync with ``capture._DRAIN_STOP_CHECK_INTERVAL``.
DRAIN_STOP_CHECK_INTERVAL = capture_mod._DRAIN_STOP_CHECK_INTERVAL


class _SlowFakeRecorder:
    """Mock recorder that simulates a backlogged drain.

    ``_process_audio_chunk`` sleeps ``chunk_process_seconds`` per call
    so a 20-chunk backlog takes 20 × ``chunk_process_seconds`` to drain
    — long enough to reliably observe whether the worker checks the
    stop event mid-drain.
    """

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
        # the drain loop "fall behind" in production: each chunk takes
        # longer to process than the 32ms inter-arrival gap.
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
        """Backlogged drain (20 slow chunks) + stop signal set after
        ~5 chunks → the worker must exit within ~5 more chunks, NOT
        process the full backlog of 20.

        Without the AB-2 fix the worker would burn all 20 chunks × 20ms
        = 400ms of CPU before noticing the stop signal. With the fix,
        the check fires every 4 chunks so the worker bails out within
        ≤ ``_DRAIN_STOP_CHECK_INTERVAL`` chunks of the stop signal.
        """
        # 20 chunks × 20ms/chunk = 400ms of CPU per full drain. With
        # the AB-2 fix, the worker bails out within ≤ 4 chunks of the
        # stop signal = ≤ 80ms post-stop CPU.
        chunk_process_seconds = 0.02
        fake = _SlowFakeRecorder(chunk_process_seconds=chunk_process_seconds)
        for i in range(20):
            fake._ring_buffer.append(_make_chunk(i))
        dispatcher = AudioCallbackDispatcher(fake)

        # Start the worker. It will wake (no wake needed — it waits
        # with a 50ms timeout), find 20 chunks, and begin draining.
        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake,),
            name="test-ab2-worker",
            daemon=True,
        )
        t.start()

        # Let the worker drain ~5 chunks (5 × 20ms = 100ms) so the
        # stop signal lands mid-drain, not before the drain starts.
        time.sleep(chunk_process_seconds * 5)
        stop_set_at_calls = len(fake._process_audio_chunk_calls)

        # Signal stop. With the fix, the worker should bail out within
        # ≤ 4 chunks of this point (the next _DRAIN_STOP_CHECK_INTERVAL
        # boundary).
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()  # wake the wait() in case it's between iterations

        # Wait for the worker to exit. With the fix, this should take
        # ≤ 4 × 20ms = 80ms. Without the fix, this would take
        # 15 × 20ms = 300ms (the remaining backlog after the 5 chunks
        # we let process before signaling stop).
        t.join(timeout=2.0)
        assert not t.is_alive(), (
            "audio_worker_loop did not exit within 2.0s of stop signal — "
            "AB-2 regression: drain loop is not checking the stop event"
        )

        total_processed = len(fake._process_audio_chunk_calls)
        post_stop_processed = total_processed - stop_set_at_calls

        # AB-2 contract: the worker must process at most
        # ``_DRAIN_STOP_CHECK_INTERVAL`` additional chunks after the
        # stop signal is set (the check happens every N chunks, so the
        # worst case is N chunks processed before the next check fires).
        # Allow a small over-shoot tolerance for the in-flight chunk
        # that was already being processed when stop was set.
        tolerance = 1
        assert post_stop_processed <= DRAIN_STOP_CHECK_INTERVAL + tolerance, (
            f"AB-2 regression: worker processed {post_stop_processed} chunks "
            f"after stop signal was set (expected ≤ "
            f"{DRAIN_STOP_CHECK_INTERVAL + tolerance}). The drain loop is "
            f"not checking the stop event between iterations."
        )

        # Sanity: the worker should NOT have processed the full backlog
        # of 20 chunks — that would mean the stop check didn't fire.
        assert total_processed < 20, (
            f"AB-2 regression: worker processed all {total_processed} chunks "
            f"instead of bailing out early — the drain loop never noticed "
            f"the stop signal."
        )

    def test_drain_completes_when_stop_not_set(self):
        """AB-2 negative contract: when stop is NOT set during the
        drain, the drain loop must still drain the ring buffer fully
        (the early-exit only triggers on stop signal)."""
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
        # early because the drain had already completed before stop
        # was set; the early-exit only fires if stop is set DURING
        # the drain).
        assert len(fake._process_audio_chunk_calls) == 10, (
            "AB-2 negative contract violated: drain loop bailed out early even though stop was NOT set during the drain"
        )
        assert len(fake._ring_buffer) == 0

    def test_module_level_drain_interval_constant_is_four(self):
        """Source-inspection guard: the production
        ``_DRAIN_STOP_CHECK_INTERVAL`` module-level constant must equal
        4 (so the ``DRAIN_STOP_CHECK_INTERVAL`` mirror in this test file
        stays accurate). If a future change bumps the interval, update
        this test (and the mirror constant) deliberately."""
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
        """Source-inspection guard: the drain loop body must contain
        an inline ``_worker_stop_event.is_set()`` check (AB-2 fix).
        Guards against accidental removal of the check during future
        refactors."""
        src = inspect.getsource(AudioCallbackDispatcher.audio_worker_loop)
        # Strip the outer-docstring portion (the method's docstring
        # itself mentions ``_worker_stop_event`` so we need to scope
        # the check to the drain-loop region).
        # Find the drain loop body — it starts after the comment
        # "Drain all available chunks".
        drain_marker = "Drain all available chunks"
        drain_idx = src.find(drain_marker)
        assert drain_idx != -1, "could not locate drain loop in source"
        drain_body = src[drain_idx:]
        # The drain body must include a stop-event check (the AB-2 fix).
        assert "_worker_stop_event.is_set()" in drain_body, (
            "AB-2: the drain loop body must contain a "
            "``_worker_stop_event.is_set()`` check between chunk "
            "iterations. The fix appears to have been removed."
        )
        # The drain body must also yield the GIL with time.sleep(0).
        assert "time.sleep(0)" in drain_body, (
            "AB-2: the drain loop body must yield the GIL with ``time.sleep(0)`` every N chunks to reduce CPU burn."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--timeout=30"]))
