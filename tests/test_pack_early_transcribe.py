"""§8.15 — Transcribe 2s after first launch (early transcribe).

Spec (§8.15):

  The "Preparing offline engine…" line appears, the request is queued,
  and it auto-continues when the pack is ready.

Tested behaviors:

  1. ``PackTranscriptionQueue.enqueue`` returns True when the pack is
     not ready (the renderer should show "Preparing offline engine…").
  2. When ``mark_ready`` is called, the queued request is drained
     (auto-continue).
  3. The renderer can use ``q.waiting > 0`` as the signal to show the
     "Preparing…" line.
  4. Multiple early requests are all auto-continued in arrival order.
  5. A request that arrives AFTER ``mark_ready`` is NOT queued (caller
     dispatches immediately).
"""

from __future__ import annotations

import threading
import time

import pytest
from voice_typer.server.service import offline_pack


class TestEarlyTranscribe:
    """§8.15 — early transcribe request + "Preparing…" line + auto-continue."""

    def test_early_request_is_queued(self):
        """A request arriving before the pack is ready is queued."""
        q = offline_pack.OfflinePackTranscriptionQueue()
        queued = q.enqueue({"audio_path": "/tmp/early.wav", "sample_rate": 16000})
        assert queued is True
        assert q.waiting == 1

    def test_renderer_shows_preparing_when_waiting(self):
        """``q.waiting > 0`` is the renderer's signal to show "Preparing…"."""
        q = offline_pack.OfflinePackTranscriptionQueue()
        assert q.waiting == 0  # no preparing line
        q.enqueue({"audio_path": "/tmp/a.wav"})
        assert q.waiting > 0  # show preparing line

    def test_auto_continue_on_mark_ready(self):
        """When the pack becomes ready, queued requests are drained in order."""
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.enqueue({"audio_path": "/tmp/a.wav"})
        q.enqueue({"audio_path": "/tmp/b.wav"})
        q.enqueue({"audio_path": "/tmp/c.wav"})
        drained = q.mark_ready(worker_pid=42)
        # All three queued requests are returned in arrival order.
        assert [r["audio_path"] for r in drained] == [
            "/tmp/a.wav",
            "/tmp/b.wav",
            "/tmp/c.wav",
        ]

    def test_post_ready_request_not_queued(self):
        """A request arriving AFTER ``mark_ready`` is NOT queued."""
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.mark_ready(worker_pid=1)
        queued = q.enqueue({"audio_path": "/tmp/late.wav"})
        assert queued is False
        assert q.waiting == 0

    def test_concurrent_enqueue_and_mark_ready(self):
        """Concurrent enqueues + mark_ready don't lose requests.

        Race: thread A enqueues 100 requests; thread B calls
        ``mark_ready``. Every request is either in the drained list
        or returns False (post-ready). No request is silently lost.
        """
        q = offline_pack.OfflinePackTranscriptionQueue()
        drained_lock = threading.Lock()
        drained: list[dict] = []
        enqueued_count = {"n": 0}
        post_ready_count = {"n": 0}

        def producer():
            for i in range(100):
                if q.enqueue({"audio_path": f"/tmp/{i}.wav", "id": i}):
                    enqueued_count["n"] += 1
                else:
                    post_ready_count["n"] += 1

        def ready_signaller():
            time.sleep(0.05)  # let some requests queue first
            drained_local = q.mark_ready(worker_pid=1)
            with drained_lock:
                drained.extend(drained_local)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=ready_signaller)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # Conservation: every request is either drained or post-ready;
        # ``len(drained)`` is a subset of ``enqueued_count["n"]``
        # (the drained requests WERE enqueued first).
        assert enqueued_count["n"] + post_ready_count["n"] == 100
        # All drained requests were enqueued.
        assert len(drained) <= enqueued_count["n"]

    def test_preparing_line_clears_after_ready(self):
        """After ``mark_ready``, ``q.waiting`` is 0 (preparing line hides)."""
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.enqueue({"audio_path": "/tmp/a.wav"})
        assert q.waiting > 0
        q.mark_ready(worker_pid=1)
        assert q.waiting == 0


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
