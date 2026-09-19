"""Tests for the resolver-snapshot optimization in ``event_bus.publish``."""

from __future__ import annotations

import gc
import threading
import weakref

import pytest
from voice_typer.server import event_bus, log_rate_limit


@pytest.fixture(autouse=True)
def _clean_subscribers():
    """Snapshot + clear the event_bus subscriber set for each test."""
    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    log_rate_limit.reset()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


class TestSnapshotType:
    def test_snapshot_is_a_tuple(self):
        """The snapshot MUST be a tuple (immutable, GIL-atomic read)."""
        assert isinstance(event_bus._subscribers._snapshot, tuple)

    def test_snapshot_is_empty_when_no_subscribers(self):
        """With no subscribers, the snapshot is the empty tuple."""
        assert event_bus._subscribers._snapshot == ()

    def test_snapshot_is_not_a_list(self):
        """Guard against a future refactor accidentally switching to a"""
        assert not isinstance(event_bus._subscribers._snapshot, list)


class TestSnapshotRebuild:
    def test_subscribe_rebuilds_snapshot(self):
        """subscribe() must rebuild the snapshot so publish() sees the"""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        snapshot = event_bus._subscribers._snapshot
        assert len(snapshot) == 1
        # The resolver should resolve to the subscribed callback.
        resolved = snapshot[0]()
        assert resolved is not None
        # Publish should deliver the event.
        event_bus.publish({"type": "test"})
        assert received == [{"type": "test"}]

    def test_unsubscribe_rebuilds_snapshot(self):
        """unsubscribe() must rebuild the snapshot so publish() no"""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 1
        event_bus.unsubscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 0
        event_bus.publish({"type": "test"})
        assert received == []

    def test_clear_resets_snapshot_to_empty_tuple(self):
        """clear() must set the snapshot to () directly (not just"""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 1
        event_bus._subscribers.clear()
        assert event_bus._subscribers._snapshot == ()

    def test_duplicate_subscribe_does_not_grow_snapshot(self):
        """Subscribing the same callable twice should still produce a"""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        event_bus.subscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 1


class TestLockFreePublish:
    def test_publish_does_not_acquire_the_subscriber_lock(self):
        """publish() must read the snapshot WITHOUT acquiring ``_lock``."""
        received: list[dict] = []
        event_bus.subscribe(received.append)

        original_lock = event_bus._lock
        sentinel = RuntimeError("publish() must not acquire _lock")

        class _LockThatRaises:
            """no-op so the `with` block doesn't blow up on exit if"""

            def acquire(self, *args, **kwargs):
                raise sentinel

            def release(self):
                pass

            def __enter__(self):
                return self.acquire()

            def __exit__(self, *args):
                self.release()
                return False

        event_bus._lock = _LockThatRaises()
        try:
            # publish() must NOT acquire _lock. If it does, the
            result = event_bus.publish({"type": "test"})
            assert result is True
            assert received == [{"type": "test"}]
        finally:
            event_bus._lock = original_lock

    def test_publish_with_no_subscribers_does_not_acquire_lock(self):
        """lock (the `if not snapshot: return False` check happens before"""
        original_lock = event_bus._lock
        sentinel = RuntimeError("publish() must not acquire _lock")

        class _LockThatRaises:
            def acquire(self, *args, **kwargs):
                raise sentinel

            def release(self):
                pass

            def __enter__(self):
                return self.acquire()

            def __exit__(self, *args):
                self.release()
                return False

        event_bus._lock = _LockThatRaises()
        try:
            result = event_bus.publish({"type": "test"})
            assert result is False
        finally:
            event_bus._lock = original_lock


class _Subscriber:
    """A simple subscriber object with a bound-method callback."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on_event(self, event: dict) -> None:
        self.calls.append(event)


class TestWeakRefSnapshot:
    def test_snapshot_holds_no_strong_ref_to_bound_method_owner(self):
        """The snapshot must NOT hold a strong reference to a bound-"""
        sub = _Subscriber()
        weak_owner = weakref.ref(sub)
        event_bus.subscribe(sub.on_event)
        assert len(event_bus._subscribers._snapshot) == 1

        # Drop the caller's strong ref and force GC.
        del sub
        gc.collect()

        assert weak_owner() is None, (
            "snapshot must not hold a strong ref to the bound-method owner (PVT-031 leak-prevention invariant)"
        )

    def test_dead_resolver_is_silently_skipped(self):
        """publish() delivery, the dead resolver returns None and is"""
        sub = _Subscriber()
        event_bus.subscribe(sub.on_event)
        assert len(event_bus._subscribers._snapshot) == 1

        # Drop the caller's strong ref and force GC. The WeakMethod
        del sub
        gc.collect()

        result = event_bus.publish({"type": "test"})
        # No live subscribers → False (the dead one doesn't count).
        assert result is False

    def test_live_bound_method_still_receives_events(self):
        """A held bound-method subscriber still receives events via"""
        sub = _Subscriber()
        event_bus.subscribe(sub.on_event)
        event_bus.publish({"type": "test"})
        assert sub.calls == [{"type": "test"}]


class TestSnapshotDeferred:
    def test_async_dispatch_uses_snapshot(self):
        """``publish(async_dispatch=True)`` must pass the snapshot"""
        import time

        received: list[dict] = []
        received_thread: list[str] = []

        def _record(m: dict) -> None:
            received.append(m)
            received_thread.append(threading.current_thread().name)

        cb = _record
        event_bus.subscribe(cb)
        try:
            publisher_thread = threading.current_thread().name
            result = event_bus.publish({"type": "test"}, async_dispatch=True)
            assert result is True
            deadline = time.monotonic() + 2.0
            while not received and time.monotonic() < deadline:
                time.sleep(0.01)
            assert received == [{"type": "test"}]
            assert received_thread[0] != publisher_thread
            assert "event-bus-publisher" in received_thread[0]
        finally:
            event_bus.unsubscribe(cb)
