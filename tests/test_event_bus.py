"""Tests for ``voice_typer.server.event_bus``.

B-1: extracted from ``ipc_server._push_event_now`` to break the tight
coupling between 12+ domain modules and the IPC transport layer.

These tests cover the public API (``publish`` / ``subscribe`` /
``unsubscribe``) plus the regression properties preserved from the
previous ``_push_event_now`` semantics:

- publish with no subscribers is a no-op (returns False).
- subscribe + publish → callback called with the event.
- multiple subscribers → all called.
- unsubscribe → callback no longer called.
- thread safety: concurrent publish from multiple threads → no
  corruption, all events delivered.
- callback that raises → does NOT block other subscribers (log and
  continue).
- subscribe(None) / unsubscribe(None) are safe no-ops.
- duplicate subscribe is deduplicated (set semantics).
- unsubscribe with an unknown callable is a safe no-op.
- subscriber snapshot is taken under the lock so a subscriber that
  unsubscribes itself (or another) during publish does not raise
  ``RuntimeError: Set changed size during iteration``.
- re-entrant publish (a subscriber that itself calls publish) does
  not deadlock (RLock).

GT-3 additions (TestSubscriberExceptionLogLevel /
TestConfigChangeListenerExceptionLogLevel) pin the WARNING-on-first /
 DEBUG-on-repeat rate-limit policy for subscriber exceptions.

GT-C1-7 additions (TestShutdownConsolidation) pin the deletion of the
duplicate ``shutdown_executor()``.

GT-53 additions (TestCanonicalCatalogue) pin the catalogue completeness
(4 newly-listed events: tray_menu, tray_state, consent_required,
parakeet_cpu_fallback).
"""

from __future__ import annotations

import logging
import threading
import time

import pytest
from voice_typer.server import event_bus, log_rate_limit

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_subscribers():
    """Snapshot and clear the event_bus subscriber set for each test.

    Without this, subscribers registered by one test would leak into
    the next (the event_bus is a process-global singleton).  We
    snapshot, clear, yield, then restore so concurrent test runs in
    the same process don't see each other's state.

    Also resets the ``log_rate_limit`` counters so each test starts
    with a clean rate-limit slate — otherwise a subscriber that
    raises in test N would be on occurrence 2+ by test N+1 and the
    GT-3 WARNING-on-first-occurrence assertion would fail.
    """
    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    with event_bus._config_change_lock:
        original_listeners = set(event_bus._config_change_listeners)
        event_bus._config_change_listeners.clear()
    log_rate_limit.reset()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)
    with event_bus._config_change_lock:
        event_bus._config_change_listeners.clear()
        event_bus._config_change_listeners.update(original_listeners)


# ── publish with no subscribers ────────────────────────────────────────


class TestPublishNoSubscribers:
    def test_publish_returns_false_with_no_subscribers(self):
        """publish() returns False when no subscribers are registered."""
        result = event_bus.publish({"type": "test"})
        assert result is False

    def test_publish_does_not_raise_with_no_subscribers(self):
        """publish() is a no-op (no exception) when there are no subscribers."""
        # Just verify it doesn't raise.
        event_bus.publish({"type": "test"})
        event_bus.publish({"type": "another", "data": {"foo": "bar"}})


# ── subscribe + publish ────────────────────────────────────────────────


class TestSubscribePublish:
    def test_subscriber_receives_published_event(self):
        """A registered subscriber is called with the published event."""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        event_bus.publish({"type": "test", "data": {"x": 1}})
        assert received == [{"type": "test", "data": {"x": 1}}]

    def test_publish_returns_true_when_subscriber_accepts(self):
        """publish() returns True when at least one subscriber accepts."""
        event_bus.subscribe(lambda _msg: None)
        result = event_bus.publish({"type": "test"})
        assert result is True

    def test_multiple_publishes_each_delivered(self):
        """Each publish() delivers exactly one event to each subscriber."""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        for i in range(5):
            event_bus.publish({"type": "test", "i": i})
        assert received == [
            {"type": "test", "i": 0},
            {"type": "test", "i": 1},
            {"type": "test", "i": 2},
            {"type": "test", "i": 3},
            {"type": "test", "i": 4},
        ]


# ── multiple subscribers ───────────────────────────────────────────────


class TestMultipleSubscribers:
    def test_all_subscribers_called_in_order(self):
        """All subscribers are called with the same event."""
        received_a: list[dict] = []
        received_b: list[dict] = []
        event_bus.subscribe(received_a.append)
        event_bus.subscribe(received_b.append)
        event_bus.publish({"type": "broadcast"})
        assert received_a == [{"type": "broadcast"}]
        assert received_b == [{"type": "broadcast"}]

    def test_three_subscribers_all_called(self):
        received_a: list[dict] = []
        received_b: list[dict] = []
        received_c: list[dict] = []
        event_bus.subscribe(received_a.append)
        event_bus.subscribe(received_b.append)
        event_bus.subscribe(received_c.append)
        event_bus.publish({"type": "ping"})
        assert len(received_a) == 1
        assert len(received_b) == 1
        assert len(received_c) == 1


# ── unsubscribe ────────────────────────────────────────────────────────


class TestUnsubscribe:
    def test_unsubscribed_callback_no_longer_called(self):
        """After unsubscribe, the callback is not invoked on publish."""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)
        event_bus.publish({"type": "first"})
        assert received == [{"type": "first"}]

        event_bus.unsubscribe(cb)
        event_bus.publish({"type": "second"})
        # Only the first event was received.
        assert received == [{"type": "first"}]

    def test_unsubscribe_unknown_callable_is_noop(self):
        """Unregistering a callable that was never registered is safe."""
        # Should not raise.
        event_bus.unsubscribe(lambda _msg: None)

    def test_other_subscribers_unaffected_by_unsubscribe(self):
        """Unsubscribing one callback does not affect others."""
        received_a: list[dict] = []
        received_b: list[dict] = []
        event_bus.subscribe(received_a.append)
        event_bus.subscribe(received_b.append)
        event_bus.unsubscribe(received_a.append)
        event_bus.publish({"type": "after"})
        assert received_a == []
        assert received_b == [{"type": "after"}]


# ── thread safety ──────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_publish_no_corruption(self):
        """Concurrent publish() calls from multiple threads must all
        deliver their events without raising or losing any."""
        received: list[dict] = []
        lock = threading.Lock()

        def listener(msg: dict) -> None:
            with lock:
                received.append(msg)

        event_bus.subscribe(listener)

        n_threads = 8
        n_per_thread = 50

        def worker(thread_id: int) -> None:
            for i in range(n_per_thread):
                event_bus.publish({"type": "test", "t": thread_id, "i": i})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 8 * 50 = 400 events should have been delivered.
        assert len(received) == n_threads * n_per_thread

    def test_concurrent_subscribe_unsubscribe(self):
        """Concurrent subscribe/unsubscribe must not corrupt the set."""
        # We're verifying no exception is raised; the exact state of
        # the set afterward depends on thread scheduling.
        n_threads = 4
        n_iter = 100
        barrier = threading.Barrier(n_threads)

        def cb(_msg: dict) -> None:
            pass

        def worker() -> None:
            barrier.wait()
            for _ in range(n_iter):
                event_bus.subscribe(cb)
                event_bus.unsubscribe(cb)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # After all threads finish, the callable should not be registered
        # (every subscribe was matched by an unsubscribe).
        assert cb not in event_bus._subscribers

    def test_concurrent_publish_and_subscribe(self):
        """A thread publishing while another subscribes must not raise."""
        received: list[dict] = []
        lock = threading.Lock()
        stop = threading.Event()

        def listener(msg: dict) -> None:
            with lock:
                received.append(msg)

        def publisher() -> None:
            while not stop.is_set():
                event_bus.publish({"type": "test"})

        def subscriber() -> None:
            for _ in range(50):
                event_bus.subscribe(listener)
                event_bus.unsubscribe(listener)

        pub_thread = threading.Thread(target=publisher)
        sub_thread = threading.Thread(target=subscriber)
        pub_thread.start()
        sub_thread.start()
        sub_thread.join()
        stop.set()
        pub_thread.join(timeout=2.0)

        # The test passes if no exception was raised during the run.


# ── subscriber exception isolation ─────────────────────────────────────


class TestSubscriberExceptionIsolation:
    def test_subscriber_that_raises_does_not_block_others(self):
        """A subscriber that raises must not prevent other subscribers
        from receiving the event."""
        received_good: list[dict] = []

        def bad_subscriber(_msg: dict) -> None:
            raise RuntimeError("subscriber exploded")

        def good_subscriber(msg: dict) -> None:
            received_good.append(msg)

        event_bus.subscribe(bad_subscriber)
        event_bus.subscribe(good_subscriber)
        # Should not raise.
        event_bus.publish({"type": "test"})
        assert received_good == [{"type": "test"}]

    def test_publish_returns_false_when_all_subscribers_raise(self):
        """If every subscriber raises, publish() returns False
        (no successful delivery)."""

        def always_raises(_msg: dict) -> None:
            raise RuntimeError("always fails")

        event_bus.subscribe(always_raises)
        result = event_bus.publish({"type": "test"})
        assert result is False

    def test_publish_returns_true_when_at_least_one_succeeds(self):
        """If at least one subscriber accepts (doesn't raise), publish
        returns True even if another subscriber raised."""

        def bad(_msg: dict) -> None:
            raise RuntimeError("bad")

        def good(_msg: dict) -> None:
            pass

        event_bus.subscribe(bad)
        event_bus.subscribe(good)
        result = event_bus.publish({"type": "test"})
        assert result is True

    def test_exception_in_first_subscriber_does_not_skip_second(self, caplog):
        """Order of registration does not affect delivery — even if
        the first subscriber raises, the second still receives."""
        first_called: list[bool] = []
        second_received: list[dict] = []

        def first(_msg: dict) -> None:
            first_called.append(True)
            raise RuntimeError("first fails")

        def second(msg: dict) -> None:
            second_received.append(msg)

        event_bus.subscribe(first)
        event_bus.subscribe(second)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            event_bus.publish({"type": "test"})
        assert first_called == [True]
        assert second_received == [{"type": "test"}]
        # the first exception from this subscriber must be logged
        # (no longer silently dropped at DEBUG). The exact level is
        # verified by ``TestSubscriberExceptionLogLevel`` below; here
        # we only assert the message is captured.
        assert any("subscriber raised" in r.getMessage() for r in caplog.records)


# subscriber exception log level ───────────────────────────────


class TestSubscriberExceptionLogLevel:
    """GT-3: subscriber exceptions must surface at WARNING (not DEBUG)
    on the FIRST occurrence per subscriber so production file handlers
    (INFO+) actually capture them.  Subsequent occurrences from the
    SAME subscriber rate-limit back to DEBUG so a persistently-broken
    subscriber doesn't spam the log.

    These tests pin the rate-limit contract: WARNING on occurrence #1,
    DEBUG on occurrence #2+.
    """

    def test_first_exception_logged_at_warning(self, caplog):
        """GT-3: the first exception from a subscriber is logged at
        WARNING level with the message body present."""
        log_rate_limit.reset()

        def bad(_msg: dict) -> None:
            raise RuntimeError("boom")

        event_bus.subscribe(bad)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            event_bus.publish({"type": "test"})
        # Find the record emitted by the subscriber-exception path.
        matching = [r for r in caplog.records if "subscriber raised" in r.getMessage()]
        assert len(matching) == 1, f"expected 1 record, got {matching}"
        assert matching[0].levelno == logging.WARNING, (
            f"GT-3: first occurrence must be WARNING, got {logging.getLevelName(matching[0].levelno)}"
        )

    def test_first_exception_includes_exc_info(self, caplog):
        """GT-3: the first-occurrence WARNING record must carry the
        exception traceback (``exc_info``) so operators can diagnose."""
        log_rate_limit.reset()

        def bad(_msg: dict) -> None:
            raise RuntimeError("traceback please")

        event_bus.subscribe(bad)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            event_bus.publish({"type": "test"})
        matching = [r for r in caplog.records if "subscriber raised" in r.getMessage()]
        assert matching, "no matching record captured"
        record = matching[0]
        assert record.levelno == logging.WARNING
        assert record.exc_info is not None, "GT-3: first-occurrence WARNING must include exc_info"
        # exc_info is (type, value, tb); the value should be our RuntimeError.
        assert record.exc_info[0] is RuntimeError
        assert "traceback please" in str(record.exc_info[1])

    def test_second_exception_logged_at_debug(self, caplog):
        """GT-3: the SECOND exception from the SAME subscriber
        rate-limits to DEBUG (no WARNING, no exc_info) so a
        persistently-broken subscriber can't spam the log."""
        log_rate_limit.reset()

        def bad(_msg: dict) -> None:
            raise RuntimeError("again")

        event_bus.subscribe(bad)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            event_bus.publish({"type": "first"})
            event_bus.publish({"type": "second"})
        matching = [r for r in caplog.records if "subscriber raised" in r.getMessage()]
        # Two records total: WARNING then DEBUG.
        assert len(matching) == 2, (
            f"expected 2 records (WARNING + DEBUG), got {len(matching)}: "
            f"{[(r.levelname, r.getMessage()) for r in matching]}"
        )
        assert matching[0].levelno == logging.WARNING
        assert matching[1].levelno == logging.DEBUG, (
            f"GT-3: second occurrence must be DEBUG, got {logging.getLevelName(matching[1].levelno)}"
        )
        # The DEBUG record carries no exc_info (rate-limit suppresses
        # the traceback on repeats — see log_rate_limit.log_rate_limited).
        assert matching[1].exc_info is None

    def test_distinct_subscribers_each_get_warning_on_first(self, caplog):
        """GT-3: rate-limit counters are PER-SUBSCRIBER — the first
        exception from subscriber B still logs at WARNING even if
        subscriber A has already raised."""
        log_rate_limit.reset()

        def a(_msg: dict) -> None:
            raise RuntimeError("a")

        def b(_msg: dict) -> None:
            raise RuntimeError("b")

        event_bus.subscribe(a)
        event_bus.subscribe(b)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            # Single publish: both raise for the first time.
            event_bus.publish({"type": "test"})
        matching = [r for r in caplog.records if "subscriber raised" in r.getMessage()]
        assert len(matching) == 2, f"expected 2 records (one per subscriber), got {len(matching)}"
        # Both should be WARNING (first occurrence for each subscriber).
        assert all(r.levelno == logging.WARNING for r in matching), (
            f"GT-3: each subscriber's FIRST exception must be WARNING; got levels {[r.levelname for r in matching]}"
        )

    def test_subsequent_occurrence_message_visible_at_debug(self, caplog):
        """GT-3: rate-limited (suppressed) occurrences are still
        emitted at DEBUG so they're visible when debug logging is
        enabled — they're just not promoted to WARNING."""
        log_rate_limit.reset()

        def bad(_msg: dict) -> None:
            raise RuntimeError("repeat")

        event_bus.subscribe(bad)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            for _ in range(5):
                event_bus.publish({"type": "test"})
        matching = [r for r in caplog.records if "subscriber raised" in r.getMessage()]
        # 1 WARNING + 4 DEBUG suppressed occurrences.
        assert len(matching) == 5
        assert matching[0].levelno == logging.WARNING
        assert all(r.levelno == logging.DEBUG for r in matching[1:])


class TestConfigChangeListenerExceptionLogLevel:
    """GT-3: the config-change-listener fan-out path mirrors the
    generic publish path — first occurrence WARNING, subsequent
    DEBUG."""

    def test_first_listener_exception_logged_at_warning(self, caplog):
        log_rate_limit.reset()

        class _BadListener:
            def on_config_changed(self, _updates: dict) -> None:
                raise RuntimeError("config boom")

        bad = _BadListener()
        event_bus.subscribe_config_changes(bad)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            result = event_bus._publish_config_change({"foo": 1})
        # No successful delivery (the only listener raised).
        assert result is False
        matching = [r for r in caplog.records if "config-change listener raised" in r.getMessage()]
        assert len(matching) == 1
        assert matching[0].levelno == logging.WARNING
        assert matching[0].exc_info is not None

    def test_second_listener_exception_logged_at_debug(self, caplog):
        log_rate_limit.reset()

        # SAME instance — shares the rate-limit counter.
        bad = _BadConfigListener()
        event_bus.subscribe_config_changes(bad)
        with caplog.at_level("DEBUG", logger="voice_typer.server.event_bus"):
            event_bus._publish_config_change({"foo": 1})
            event_bus._publish_config_change({"foo": 2})
        matching = [r for r in caplog.records if "config-change listener raised" in r.getMessage()]
        assert len(matching) == 2
        assert matching[0].levelno == logging.WARNING
        assert matching[1].levelno == logging.DEBUG


class _BadConfigListener:
    """Module-level listener class so ``__qualname__`` is stable
    (a nested class would also work; this just reads cleaner)."""

    def on_config_changed(self, _updates: dict) -> None:
        raise RuntimeError("config repeat")


# ── None handling ──────────────────────────────────────────────────────


class TestNoneHandling:
    def test_subscribe_none_is_noop(self):
        """subscribe(None) must not register anything."""
        event_bus.subscribe(None)
        assert len(event_bus._subscribers) == 0

    def test_unsubscribe_none_is_noop(self):
        """unsubscribe(None) must not raise or modify state."""
        event_bus.subscribe(lambda _msg: None)
        before = len(event_bus._subscribers)
        event_bus.unsubscribe(None)
        assert len(event_bus._subscribers) == before

    def test_subscribe_none_does_not_affect_existing(self):
        """subscribe(None) must not clear or affect existing subscribers."""
        received: list[dict] = []
        event_bus.subscribe(received.append)
        event_bus.subscribe(None)  # Should be a no-op.
        event_bus.publish({"type": "test"})
        assert received == [{"type": "test"}]


# ── duplicate subscribe ────────────────────────────────────────────────


class TestDuplicateSubscribe:
    def test_duplicate_subscribe_deduplicated(self):
        """Subscribing the same callable twice registers it once
        (set semantics)."""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)
        event_bus.subscribe(cb)
        assert len(event_bus._subscribers) == 1
        event_bus.publish({"type": "test"})
        # Only one call, not two.
        assert received == [{"type": "test"}]

    def test_unsubscribe_after_duplicate_subscribe(self):
        """One unsubscribe removes the callable regardless of how many
        times subscribe was called."""
        cb = lambda _msg: None  # noqa: E731
        event_bus.subscribe(cb)
        event_bus.subscribe(cb)
        event_bus.subscribe(cb)
        assert len(event_bus._subscribers) == 1
        event_bus.unsubscribe(cb)
        assert len(event_bus._subscribers) == 0


# ── subscriber mutation during publish ─────────────────────────────────


class TestSubscriberMutationDuringPublish:
    def test_subscriber_unsubscribing_itself_does_not_raise(self):
        """A subscriber that unsubscribes itself during publish must not
        trigger ``Set changed size during iteration``.

        The subscriber list is snapshotted under the lock before
        iteration, so mutations during iteration are safe.
        """
        call_count = [0]

        def self_unsubscribing(_msg: dict) -> None:
            call_count[0] += 1
            event_bus.unsubscribe(self_unsubscribing)

        event_bus.subscribe(self_unsubscribing)
        # Should not raise.
        event_bus.publish({"type": "test"})
        assert call_count[0] == 1
        # Second publish should be a no-op (subscriber was removed).
        event_bus.publish({"type": "test"})
        assert call_count[0] == 1

    def test_subscriber_unsubscribing_other_does_not_raise(self):
        """A subscriber that unsubscribes a different subscriber during
        publish must not raise."""
        other_received: list[dict] = []

        def other(msg: dict) -> None:
            other_received.append(msg)

        def first(_msg: dict) -> None:
            event_bus.unsubscribe(other)

        # Register both — order matters for the snapshot semantics.
        event_bus.subscribe(first)
        event_bus.subscribe(other)
        # Should not raise.
        event_bus.publish({"type": "test"})
        # `other` may or may not have been called depending on whether
        # it appeared before or after `first` in the snapshot; both are
        # valid. The test only verifies no exception.


# ── re-entrant publish ─────────────────────────────────────────────────


class TestReentrantPublish:
    def test_subscriber_that_publishes_does_not_deadlock(self):
        """A subscriber that calls publish() re-entrantly must not
        deadlock.  The RLock allows the same thread to re-acquire.

        The outer subscriber unsubscribes itself before re-publishing
        to avoid infinite recursion (which would be a bug in the
        subscriber, not the event bus).  The test verifies the RLock
        permits re-entrant acquisition — a plain Lock would deadlock.
        """
        outer_received: list[dict] = []
        inner_received: list[dict] = []

        def inner_listener(msg: dict) -> None:
            inner_received.append(msg)

        def outer_listener(_msg: dict) -> None:
            outer_received.append({"triggered": True})
            # Unsubscribe self BEFORE re-publishing so we don't recurse
            # infinitely.  The point of the test is that the re-entrant
            # publish call below acquires the RLock in the same thread
            # — a plain Lock would deadlock here.
            event_bus.unsubscribe(outer_listener)
            event_bus.publish({"type": "inner"})

        event_bus.subscribe(outer_listener)
        event_bus.subscribe(inner_listener)

        # Should complete (not hang) — RLock allows re-entrancy.
        event_bus.publish({"type": "outer"})

        # The outer listener was called exactly once (it unsubscribed
        # itself before re-publishing).
        assert outer_received == [{"triggered": True}]

        # The inner listener received BOTH events: the original
        # "outer" event (delivered as part of the snapshot iteration
        # in the outer publish) and the "inner" event (delivered by
        # the re-entrant publish call from inside outer_listener).
        # The order depends on set iteration order (which is not
        # guaranteed for ``set`` in Python); we assert set-equality
        # instead of list-equality.
        assert len(inner_received) == 2
        assert sorted(e["type"] for e in inner_received) == ["inner", "outer"]


# ── back-compat shim verification ──────────────────────────────────────


class TestBackwardCompatShim:
    """Behavior preserved after the ipc_server ↔ event_bus shim was removed.

    the ``_push_event_registry`` / ``_push_event_registry_lock``
    aliases and the ``_set_push_event`` / ``_clear_push_event`` shims were
    deleted from ``ipc_server.py``.  Domain code (and tests) now call
    ``event_bus.subscribe`` / ``event_bus.unsubscribe`` directly.  These
    tests verify the remaining public surface (``_push_event_now`` delegates
    to ``event_bus.publish``) and that subscribe/unsubscribe work the same
    way the old shims did — without reaching into ipc_server's removed
    internals.
    """

    def test_push_event_now_delegates_to_event_bus_publish(self):
        """ipc_server._push_event_now calls event_bus.publish."""
        from voice_typer.server import ipc_server

        received: list[dict] = []
        event_bus.subscribe(received.append)
        try:
            result = ipc_server._push_event_now({"type": "shim_test"})
            assert result is True
            assert received == [{"type": "shim_test"}]
        finally:
            event_bus.unsubscribe(received.append)

    def test_subscribe_unsubscribe_roundtrip(self):
        """Direct event_bus subscribe/unsubscribe (replaces the old
        _set_push_event / _clear_push_event shims)."""
        received: list[dict] = []

        event_bus.subscribe(received.append)
        event_bus.publish({"type": "shim_subscribe"})
        assert received == [{"type": "shim_subscribe"}]

        event_bus.unsubscribe(received.append)
        event_bus.publish({"type": "after_clear"})
        assert received == [{"type": "shim_subscribe"}]

    def test_unsubscribe_unknown_is_noop(self):
        """unsubscribe of a callable that was never subscribed must not
        raise — preserves the old _set_push_event(None) no-op semantics."""
        before = len(event_bus._subscribers)
        event_bus.unsubscribe(lambda _msg: None)
        assert len(event_bus._subscribers) == before


# ── type / signature sanity ────────────────────────────────────────────


class TestSignatureSanity:
    def test_publish_accepts_any_dict(self):
        """publish() accepts any dict regardless of keys."""
        event_bus.subscribe(lambda _msg: None)
        event_bus.publish({})
        event_bus.publish({"type": "x"})
        event_bus.publish({"type": "x", "data": {}})
        event_bus.publish({"type": "x", "data": {"nested": [1, 2, 3]}})

    def test_subscribe_accepts_callable_taking_dict(self):
        """subscribe() accepts any callable that takes a dict."""
        # Various callable shapes should all be accepted.
        event_bus.subscribe(lambda msg: None)

        def named(_msg: dict) -> None:
            pass

        event_bus.subscribe(named)

        class _CallableSubscriber:
            def __call__(self, _msg: dict) -> None:
                pass

        event_bus.subscribe(_CallableSubscriber())


# ── PERF-2: real-time thread guard ─────────────────────────────────────


class TestRTThreadGuard:
    """PERF-2: publish() must refuse to run on real-time audio threads.

    PortAudio (used by sounddevice) names its callback threads
    ``PortAudio…``.  Calling publish() from such a thread is forbidden
    because a slow subscriber would block the audio capture loop and
    glitch the recording.  The guard returns ``False`` and logs an
    error so callers fail loudly instead of silently degrading audio.

    These tests spawn worker threads with the relevant names and
    assert publish() short-circuits.  Two companion tests verify that
    publish() on a normal worker thread and on the main thread still
    works (the guard must not regress the common path).
    """

    def test_publish_returns_false_on_portaudio_thread(self):
        """Publishing from a thread named 'PortAudio-...' returns False."""
        result: dict = {}

        def rt_call() -> None:
            result["value"] = event_bus.publish({"type": "test"})

        t = threading.Thread(target=rt_call, name="PortAudio-Callback", daemon=True)
        t.start()
        t.join()
        assert result["value"] is False

    def test_publish_returns_false_on_audio_callback_thread(self):
        """Publishing from a thread named 'audio-callback-...' returns False."""
        result: dict = {}

        def rt_call() -> None:
            result["value"] = event_bus.publish({"type": "test"})

        t = threading.Thread(target=rt_call, name="audio-callback-worker", daemon=True)
        t.start()
        t.join()
        assert result["value"] is False

    def test_publish_defers_dispatch_off_rt_thread(self):
        """PERF-2: publishing from an RT (PortAudio) thread must NOT invoke
        the subscriber synchronously in the RT thread — fan-out is deferred
        to the single-worker executor so the audio callback returns in
        microseconds. The subscriber is still eventually delivered, but on
        the deferred executor thread, never blocking the RT loop."""
        received: list[dict] = []
        received_thread: list[str] = []
        event_bus.subscribe(lambda m: (received.append(m), received_thread.append(threading.current_thread().name)))

        rt_thread_name: list[str] = []

        def rt_call() -> None:
            rt_thread_name.append(threading.current_thread().name)
            event_bus.publish({"type": "rt_test"})

        t = threading.Thread(target=rt_call, name="PortAudio-stream", daemon=True)
        t.start()
        t.join()
        # The RT thread itself must not have run the subscriber.
        assert rt_thread_name == ["PortAudio-stream"]
        # Delivery is deferred: give the executor a moment, then assert it
        # happened off the RT thread.
        deadline = time.monotonic() + 2.0
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert received == [{"type": "rt_test"}], "subscriber must still be delivered"
        assert received_thread and "PortAudio-stream" not in received_thread, (
            "subscriber must NOT be invoked on the RT thread"
        )

    def test_publish_works_on_normal_thread(self):
        """Publishing from a normal worker thread still works."""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)

        result: dict = {}

        def normal_call() -> None:
            result["value"] = event_bus.publish({"type": "test_normal"})

        try:
            t = threading.Thread(target=normal_call, name="worker-1", daemon=True)
            t.start()
            t.join()
            assert result["value"] is True
            assert received == [{"type": "test_normal"}]
        finally:
            event_bus.unsubscribe(cb)

    def test_publish_works_on_main_thread(self):
        """Publishing from the main thread still works."""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)
        try:
            ok = event_bus.publish({"type": "test_main"})
            assert ok is True
            assert received == [{"type": "test_main"}]
        finally:
            event_bus.unsubscribe(cb)


# shutdown function consolidation ───────────────────────────


class TestShutdownConsolidation:
    """GT-C1-7: ``shutdown_executor()`` was a dead-code duplicate of
    ``shutdown()`` (only ``shutdown()`` is called from
    ``shutdown_controller._do_cleanup``). The duplicate is deleted;
    ``shutdown()`` remains the single public lifecycle hook.

    These tests pin the consolidation so a future "refactor" doesn't
    silently reintroduce the duplicate.
    """

    def test_shutdown_executor_is_deleted(self):
        """GT-C1-7: ``shutdown_executor`` must NOT be exported."""
        assert not hasattr(event_bus, "shutdown_executor"), (
            "GT-C1-7: shutdown_executor() was deleted as a duplicate of "
            "shutdown(); reintroducing it breaks DRY (Rule 24)"
        )

    def test_shutdown_function_exists(self):
        """GT-C1-7: ``shutdown()`` is the single canonical hook."""
        assert hasattr(event_bus, "shutdown")
        assert callable(event_bus.shutdown)

    def test_shutdown_is_idempotent(self):
        """GT-C1-7: ``shutdown()`` is idempotent — calling it twice
        (or with no executor ever created) must not raise."""
        # No executor was created in this test (no RT-thread publish).
        event_bus.shutdown()  # no-op
        event_bus.shutdown()  # still no-op
        # And after shutdown, a fresh publish still works (lazy
        # executor re-creation if an RT thread later publishes).
        received: list[dict] = []
        event_bus.subscribe(received.append)
        assert event_bus.publish({"type": "post_shutdown"}) is True
        assert received == [{"type": "post_shutdown"}]
        # Cleanup: shut down any executor the publish may have created
        # (it didn't, because we're on a non-RT thread, but be tidy).
        event_bus.shutdown()


# canonical catalogue completeness ────────────────────────────


class TestCanonicalCatalogue:
    """GT-53: the docstring catalogue must list every event actually
    emitted by the Python sidecar. The four events below were
    previously missing (tray_menu, tray_state, consent_required,
    parakeet_cpu_fallback)."""

    def test_catalogue_lists_tray_menu_event(self):
        assert "``tray_menu``" in event_bus.__doc__

    def test_catalogue_lists_tray_state_event(self):
        assert "``tray_state``" in event_bus.__doc__

    def test_catalogue_lists_consent_required_event(self):
        assert "``consent_required``" in event_bus.__doc__

    def test_catalogue_lists_parakeet_cpu_fallback_event(self):
        assert "``parakeet_cpu_fallback``" in event_bus.__doc__

    def test_catalogue_total_count_updated(self):
        """GT-53: the 'Total: N events' line must reflect the 4 newly
        catalogued events (24 → 28)."""
        assert "Total: 28 events" in event_bus.__doc__
        # The old stale count must NOT still be present.
        assert "Total: 24 events" not in event_bus.__doc__


# async dispatch option ───────────────────────────────────────


class TestAsyncDispatch:
    """ZR-20: ``publish(event, async_dispatch=True)`` defers fan-out to
    the single-worker executor so non-RT publisher threads are not
    blocked by slow subscribers (e.g. ``IPCServer.push`` →
    ``socket.sendall`` to a stalled Electron renderer).

    These tests pin:
    1. ``async_dispatch=True`` queues the event and returns immediately
       (subscriber is NOT invoked synchronously in the publisher's
       thread).
    2. The subscriber is eventually delivered the event on the
       deferred-executor thread.
    3. ``publish_sync(event)`` is an explicit-sync alias — subscribers
       ARE invoked synchronously before the call returns.
    4. The default ``publish(event)`` (no flag) preserves the existing
       synchronous semantics (back-compat for the 30+ call sites).
    """

    def test_async_dispatch_returns_true_and_defers(self):
        """``async_dispatch=True`` returns True immediately and the
        subscriber is delivered on the deferred-executor thread (NOT
        the publisher's thread).

        We don't assert that ``received == []`` immediately after
        ``publish`` returns — the single-worker executor may have
        already drained the queue by the time the assertion runs
        (especially on a fast machine). Instead we wait for delivery
        and then assert the subscriber ran on the executor thread
        (``event-bus-publisher-N``), not the publisher's thread
        (``MainThread`` in this test)."""
        received: list[dict] = []
        received_thread: list[str] = []
        cb = lambda m: (received.append(m), received_thread.append(threading.current_thread().name))  # noqa: E731
        event_bus.subscribe(cb)
        try:
            publisher_thread = threading.current_thread().name
            result = event_bus.publish({"type": "test"}, async_dispatch=True)
            # Returns True (queued for delivery).
            assert result is True
            # Wait for deferred delivery (executor may drain instantly
            # on a fast machine, so we cannot assert ``received == []``
            # at this point — race-free assertion is the thread name
            # check below).
            deadline = time.monotonic() + 2.0
            while not received and time.monotonic() < deadline:
                time.sleep(0.01)
            assert received == [{"type": "test"}], "subscriber must be delivered asynchronously"
            # The subscriber ran on the executor thread, NOT the publisher's.
            assert received_thread, "subscriber thread name must be recorded"
            assert received_thread[0] != publisher_thread, (
                f"subscriber must run on the deferred-executor thread, not the publisher's ({publisher_thread!r})"
            )
            assert "event-bus-publisher" in received_thread[0], (
                f"subscriber must run on the event-bus-publisher thread; got {received_thread[0]!r}"
            )
        finally:
            event_bus.unsubscribe(cb)

    def test_publish_sync_invokes_subscriber_synchronously(self):
        """``publish_sync`` is an explicit-sync alias — the subscriber
        is invoked before the call returns."""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)
        try:
            result = event_bus.publish_sync({"type": "test_sync"})
            assert result is True
            # Synchronous: subscriber was invoked before the call returned.
            assert received == [{"type": "test_sync"}]
        finally:
            event_bus.unsubscribe(cb)

    def test_default_publish_is_synchronous_backcompat(self):
        """The default ``publish(event)`` (no flag) preserves the
        existing synchronous semantics — subscriber is invoked before
        the call returns. This protects the 30+ existing call sites
        that rely on synchronous delivery."""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)
        try:
            result = event_bus.publish({"type": "test_default"})
            assert result is True
            # Synchronous: subscriber was invoked before the call returned.
            assert received == [{"type": "test_default"}]
        finally:
            event_bus.unsubscribe(cb)

    def test_async_dispatch_with_no_subscribers_returns_false(self):
        """``async_dispatch=True`` with no subscribers returns False
        (same as the sync path — no one to deliver to)."""
        # Make sure no subscribers are registered (the autouse
        # _reset_event_bus fixture in conftest handles this between
        # tests; we assert here defensively).
        result = event_bus.publish({"type": "test_no_subs"}, async_dispatch=True)
        assert result is False
