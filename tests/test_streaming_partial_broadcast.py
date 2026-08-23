"""Tests for the streaming partial-transcription broadcaster.

Covers the coalescing contract (latest-value-wins, ≤4 Hz throttle,
unchanged/empty suppression, forced flush) AND the bubble-channel
mirror: every eligible publish must also emit a ``bubble_set_state``
payload carrying the live ``transcript`` while the session is active,
and must NOT emit one after ``stop()`` or on a forced finalize flush
(a late ``state:"recording"`` would flip the pill out of its
transcribing/idle state).
"""

from __future__ import annotations

import threading

import pytest
from voice_typer.server.streaming import (
    PartialTranscriptionBroadcaster,
)


class FakeClock:
    """Deterministic monotonic clock driven by the test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def published(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture every event_bus.publish payload."""
    events: list[dict] = []
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda evt: events.append(evt),
    )
    return events


def _partials(events: list[dict]) -> list[str]:
    return [e["data"]["text"] for e in events if e["type"] == "transcription_partial"]


def _bubble_mirrors(events: list[dict]) -> list[dict]:
    return [e["data"] for e in events if e["type"] == "bubble_set_state"]


def test_first_push_publishes_immediately_and_mirrors_to_bubble(published):
    clock = FakeClock()
    b = PartialTranscriptionBroadcaster(cycle_id="c1", clock=clock)
    b.push("hello")
    b._publish_eligible()

    assert _partials(published) == ["hello"]
    mirrors = _bubble_mirrors(published)
    assert mirrors == [{"state": "recording", "transcript": "hello"}]
    partial_payload = next(e for e in published if e["type"] == "transcription_partial")["data"]
    assert partial_payload["cycle_id"] == "c1"


def test_throttle_defers_then_publishes_latest(published):
    clock = FakeClock()
    b = PartialTranscriptionBroadcaster(cycle_id="c1", min_interval_seconds=0.25, clock=clock)
    b.push("one")
    b._publish_eligible()
    # Within the window: latest-value-wins slot holds the newest text.
    clock.now += 0.1
    b.push("two")
    b.push("three")
    b._publish_eligible()
    assert _partials(published) == ["one"]
    # After the interval elapses the newest pending value lands.
    clock.now += 0.3
    b._publish_eligible()
    assert _partials(published) == ["one", "three"]
    assert [m["transcript"] for m in _bubble_mirrors(published)] == ["one", "three"]


def test_unchanged_text_is_suppressed(published):
    clock = FakeClock()
    b = PartialTranscriptionBroadcaster(cycle_id="c1", clock=clock)
    b.push("same")
    b._publish_eligible()
    clock.now += 1.0
    b.push("same")
    b._publish_eligible()
    assert len(_partials(published)) == 1


def test_empty_text_is_suppressed(published):
    clock = FakeClock()
    b = PartialTranscriptionBroadcaster(cycle_id="c1", clock=clock)
    b.push("   ")
    b._publish_eligible()
    assert published == []


def test_flush_bypasses_throttle_but_skips_bubble_mirror(published):
    """finalize()'s synchronous flush lands the last partial for
    main-window consumers, but must NOT re-assert ``recording`` on the
    bubble channel — the lifecycle has already moved the pill to
    transcribing by then.
    """
    clock = FakeClock()
    b = PartialTranscriptionBroadcaster(cycle_id="c1", min_interval_seconds=0.25, clock=clock)
    b.push("final words")
    b.flush()
    assert _partials(published) == ["final words"]
    assert _bubble_mirrors(published) == []


def test_no_bubble_mirror_after_stop(published):
    clock = FakeClock()
    b = PartialTranscriptionBroadcaster(cycle_id="c1", clock=clock)
    b.stop()
    b.push("after stop")  # push is a no-op post-stop
    b._publish_eligible()
    b.flush()
    assert published == []


def test_worker_thread_drains_pending_slot():
    """End-to-end worker path: push wakes the lazy worker which drains
    the slot without test intervention (real threads, real clock)."""
    received: list[str] = []
    seen = threading.Event()

    def capture(evt: dict) -> None:
        if evt["type"] == "transcription_partial":
            received.append(evt["data"]["text"])
            seen.set()

    import voice_typer.server.event_bus as eb

    original = eb.publish
    eb.publish = capture
    try:
        b = PartialTranscriptionBroadcaster(cycle_id="c2")
        b.push("worker drained me")
        assert seen.wait(timeout=5.0), "worker never published the partial"
        assert received[-1] == "worker drained me"
        b.stop()
    finally:
        eb.publish = original
