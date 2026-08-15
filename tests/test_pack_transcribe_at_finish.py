"""§8.14 — Race: transcribe-at-finish.

Spec (§8.14):

  "Ready" is a single definition: downloaded + verified + worker
  started + prewarmed. All features check that state. Clicking early
  queues the request and it auto-continues when ready.

Tested behaviors:

  1. ``PackTranscriptionQueue`` starts in NOT-ready state.
  2. ``enqueue`` returns True (queued) when not ready; False when ready.
  3. ``mark_ready`` drains the queue and returns the drained requests
     (caller dispatches them to the worker).
  4. ``mark_ready`` publishes ``pack_ready`` with the worker PID.
  5. After ``mark_ready``, ``enqueue`` returns False (immediate dispatch).
  6. ``mark_not_ready`` reverses the state (worker crashed).
  7. ``mark_not_ready`` publishes ``worker_unloaded``.
"""

from __future__ import annotations

import pytest
from voice_typer.server.service import offline_pack


class TestPackTranscriptionQueue:
    """§8.14 — transcribe-at-finish queue + auto-continue."""

    def test_starts_not_ready(self):
        q = offline_pack.OfflinePackTranscriptionQueue()
        assert q.ready is False
        assert q.waiting == 0

    def test_enqueue_when_not_ready_queues(self):
        q = offline_pack.OfflinePackTranscriptionQueue()
        queued = q.enqueue({"audio_path": "/tmp/a.wav"})
        assert queued is True
        assert q.waiting == 1

    def test_enqueue_when_ready_returns_false(self):
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.mark_ready(worker_pid=12345)
        queued = q.enqueue({"audio_path": "/tmp/a.wav"})
        assert queued is False  # caller dispatches immediately
        assert q.waiting == 0

    def test_mark_ready_drains_queue(self):
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.enqueue({"audio_path": "/tmp/a.wav"})
        q.enqueue({"audio_path": "/tmp/b.wav"})
        q.enqueue({"audio_path": "/tmp/c.wav"})
        drained = q.mark_ready(worker_pid=12345)
        assert len(drained) == 3
        assert drained[0]["audio_path"] == "/tmp/a.wav"
        assert drained[2]["audio_path"] == "/tmp/c.wav"
        assert q.waiting == 0
        assert q.ready is True

    def test_mark_ready_publishes_pack_ready_event(self):
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        q = offline_pack.OfflinePackTranscriptionQueue(event_bus=FakeBus())
        q.mark_ready(worker_pid=99999)
        ready_events = [e for e in events if e["type"] == "offline_pack_ready"]
        assert ready_events
        assert ready_events[0]["data"]["worker_pid"] == 99999

    def test_mark_not_ready_reverses_state(self):
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.mark_ready(worker_pid=1)
        assert q.ready is True
        q.mark_not_ready(reason="worker_crashed")
        assert q.ready is False
        # After crash, new enqueues are queued again.
        assert q.enqueue({"audio_path": "/tmp/x.wav"}) is True

    def test_mark_not_ready_publishes_worker_unloaded(self):
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        q = offline_pack.OfflinePackTranscriptionQueue(event_bus=FakeBus())
        q.mark_ready(worker_pid=1)
        q.mark_not_ready(reason="worker_crashed")
        unloaded = [e for e in events if e["type"] == "worker_unloaded"]
        assert unloaded
        assert unloaded[0]["data"]["reason"] == "worker_crashed"

    def test_clear_drops_pending_requests(self):
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.enqueue({"audio_path": "/tmp/a.wav"})
        q.enqueue({"audio_path": "/tmp/b.wav"})
        q.clear()
        assert q.waiting == 0

    def test_multiple_mark_ready_calls_idempotent(self):
        """Calling ``mark_ready`` twice drains only newly-queued requests."""
        q = offline_pack.OfflinePackTranscriptionQueue()
        q.enqueue({"audio_path": "/tmp/a.wav"})
        first = q.mark_ready(worker_pid=1)
        assert len(first) == 1
        second = q.mark_ready(worker_pid=1)
        assert second == []  # nothing queued between the two calls


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
