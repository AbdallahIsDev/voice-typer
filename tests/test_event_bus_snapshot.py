"""Tests for the resolver-snapshot optimization in ``event_bus.publish``.

The snapshot is a tuple of zero-argument "resolvers"
(``weakref.WeakMethod`` / ``_StrongResolver`` / ``_CWeakResolver``)
maintained atomically by ``_SubscriberSet._rebuild_snapshot()`` on
every subscribe / unsubscribe / clear / update under ``_lock``.
``publish()`` reads the tuple WITHOUT acquiring the lock (tuple read
is GIL-atomic) and iterates it directly — eliminating the per-publish
lock acquisition AND the per-publish ``list(_subscribers)``
allocation on the 60Hz ``bubble_level`` hot path.

The snapshot holds NO strong references to bound-method subscribers
(only to plain functions, which are module-level and never GC'd).
This preserves the PVT-031 weak-ref leak-prevention semantics: a
bound-method subscriber whose owner is GC'd is auto-evicted from
``_weak_py`` / ``_weak_c`` via the WeakMethod / weakref callback, and
its resolver returns ``None`` on the next ``publish()`` iteration
(silently skipped).

These tests pin:
  1. The snapshot is a ``tuple`` (immutable, GIL-atomic read).
  2. The snapshot is rebuilt on every subscribe / unsubscribe / clear.
  3. ``publish()`` reads the snapshot WITHOUT acquiring ``_lock``
     (verified by monkey-patching ``_lock`` to raise on acquire and
     confirming ``publish()`` still succeeds).
  4. Dead weak-ref subscribers (GC'd between snapshot and delivery)
     are silently skipped — no ``ReferenceError``, no log spam.
  5. The snapshot holds no strong references to a bound-method
     subscriber's ``__self__`` (PVT-031 leak-prevention preserved).
"""

from __future__ import annotations

import gc
import threading
import weakref

import pytest
from voice_typer.server import event_bus, log_rate_limit


@pytest.fixture(autouse=True)
def _clean_subscribers():
    """Snapshot + clear the event_bus subscriber set for each test.

    Mirrors the fixture in ``tests/test_event_bus.py`` so the
    process-global ``_subscribers`` singleton doesn't leak state
    across tests.
    """
    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    log_rate_limit.reset()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


# ── Snapshot type + initial state ──────────────────────────────────────


class TestSnapshotType:
    def test_snapshot_is_a_tuple(self):
        """The snapshot MUST be a tuple (immutable, GIL-atomic read).

        A list would not be safe to read without the lock — a
        concurrent subscribe could mutate it mid-iteration. A tuple is
        immutable; replacing the module-level reference is GIL-atomic.
        """
        assert isinstance(event_bus._subscribers._snapshot, tuple)

    def test_snapshot_is_empty_when_no_subscribers(self):
        """With no subscribers, the snapshot is the empty tuple."""
        assert event_bus._subscribers._snapshot == ()

    def test_snapshot_is_not_a_list(self):
        """Guard against a future refactor accidentally switching to a
        list (which would break the lock-free read invariant)."""
        assert not isinstance(event_bus._subscribers._snapshot, list)


# ── Snapshot rebuild on mutation ───────────────────────────────────────


class TestSnapshotRebuild:
    def test_subscribe_rebuilds_snapshot(self):
        """subscribe() must rebuild the snapshot so publish() sees the
        new subscriber without acquiring the lock."""
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
        """unsubscribe() must rebuild the snapshot so publish() no
        longer delivers to the removed callback."""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 1
        event_bus.unsubscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 0
        event_bus.publish({"type": "test"})
        assert received == []

    def test_clear_resets_snapshot_to_empty_tuple(self):
        """clear() must set the snapshot to () directly (not just
        rebuild — the buckets are empty so rebuild would also produce
        (), but setting () explicitly is faster and clearer)."""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 1
        event_bus._subscribers.clear()
        assert event_bus._subscribers._snapshot == ()

    def test_duplicate_subscribe_does_not_grow_snapshot(self):
        """Subscribing the same callable twice should still produce a
        snapshot of length 1 (set semantics, dedup)."""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        event_bus.subscribe(received.append)
        assert len(event_bus._subscribers._snapshot) == 1


# ── Lock-free publish ──────────────────────────────────────────────────


class TestLockFreePublish:
    def test_publish_does_not_acquire_the_subscriber_lock(self):
        """publish() must read the snapshot WITHOUT acquiring ``_lock``.

        This is the core optimization: the 60Hz ``bubble_level`` hot
        path no longer contends on ``_lock`` with subscribe /
        unsubscribe. Verified by replacing ``_lock.acquire`` with a
        function that raises — if publish() acquires the lock, the
        test fails.
        """
        received: list[dict] = []
        event_bus.subscribe(received.append)

        original_lock = event_bus._lock
        sentinel = RuntimeError("publish() must not acquire _lock")

        class _LockThatRaises:
            """A fake lock whose acquire() raises. release() is a
            no-op so the `with` block doesn't blow up on exit if
            some code path does acquire (it won't get past acquire)."""

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
            # _LockThatRaises.acquire() raises RuntimeError.
            result = event_bus.publish({"type": "test"})
            assert result is True
            assert received == [{"type": "test"}]
        finally:
            event_bus._lock = original_lock

    def test_publish_with_no_subscribers_does_not_acquire_lock(self):
        """The empty-snapshot early return must also not acquire the
        lock (the `if not snapshot: return False` check happens before
        any would-be lock acquisition)."""
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


# ── Weak-ref subscriber GC handling ────────────────────────────────────


class _Subscriber:
    """A simple subscriber object with a bound-method callback.

    Used to verify the snapshot holds NO strong ref to ``__self__``
    (the PVT-031 leak-prevention invariant).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on_event(self, event: dict) -> None:
        self.calls.append(event)


class TestWeakRefSnapshot:
    def test_snapshot_holds_no_strong_ref_to_bound_method_owner(self):
        """The snapshot must NOT hold a strong reference to a bound-
        method subscriber's ``__self__``. If it did, the owner would
        never be GC'd (the PVT-031 leak that the WeakMethod storage
        was designed to prevent).

        Verified by subscribing a bound method, dropping the caller's
        strong ref, forcing GC, and checking the owner is collected.
        """
        sub = _Subscriber()
        weak_owner = weakref.ref(sub)
        event_bus.subscribe(sub.on_event)
        assert len(event_bus._subscribers._snapshot) == 1

        # Drop the caller's strong ref and force GC.
        del sub
        gc.collect()

        # The owner MUST be collected — the snapshot holds only a
        # WeakMethod (weak ref), not a strong ref to the bound method.
        assert weak_owner() is None, (
            "snapshot must not hold a strong ref to the bound-method owner (PVT-031 leak-prevention invariant)"
        )

    def test_dead_resolver_is_silently_skipped(self):
        """If a subscriber is GC'd between snapshot creation and
        publish() delivery, the dead resolver returns None and is
        silently skipped — no ReferenceError, no log spam."""
        sub = _Subscriber()
        event_bus.subscribe(sub.on_event)
        assert len(event_bus._subscribers._snapshot) == 1

        # Drop the caller's strong ref and force GC. The WeakMethod
        # in the snapshot is now dead (returns None).
        del sub
        gc.collect()

        # publish() must not raise. The dead resolver is skipped.
        # The _weak_py dict may or may not have been pruned yet
        # (depends on whether the WeakMethod callback fired during
        # gc.collect()), but either way publish() must succeed.
        result = event_bus.publish({"type": "test"})
        # No live subscribers → False (the dead one doesn't count).
        assert result is False

    def test_live_bound_method_still_receives_events(self):
        """A held bound-method subscriber still receives events via
        the resolver snapshot (the WeakMethod resolves to the live
        method)."""
        sub = _Subscriber()
        event_bus.subscribe(sub.on_event)
        event_bus.publish({"type": "test"})
        assert sub.calls == [{"type": "test"}]


# ── Snapshot + deferred delivery ──────────────────────────────────────


class TestSnapshotDeferred:
    def test_async_dispatch_uses_snapshot(self):
        """``publish(async_dispatch=True)`` must pass the snapshot
        (tuple of resolvers) to the deferred executor, not a fresh
        list. Verified by checking the subscriber is still delivered
        the event asynchronously."""
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
