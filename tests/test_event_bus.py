"""Tests for ``voice_typer.server.event_bus``."""

from __future__ import annotations

import logging
import threading
import time

import pytest
from voice_typer.server import event_bus, log_rate_limit


@pytest.fixture(autouse=True)
def _clean_subscribers():
    """Snapshot and clear the event_bus subscriber set for each test."""
    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    log_rate_limit.reset()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


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


class TestThreadSafety:
    def test_concurrent_publish_no_corruption(self):
        """Concurrent publish() calls from multiple threads must all"""
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


class TestSubscriberExceptionIsolation:
    def test_subscriber_that_raises_does_not_block_others(self):
        """A subscriber that raises must not prevent other subscribers"""
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
        """If every subscriber raises, publish() returns False"""

        def always_raises(_msg: dict) -> None:
            raise RuntimeError("always fails")

        event_bus.subscribe(always_raises)
        result = event_bus.publish({"type": "test"})
        assert result is False

    def test_publish_returns_true_when_at_least_one_succeeds(self):
        """If at least one subscriber accepts (doesn't raise), publish"""

        def bad(_msg: dict) -> None:
            raise RuntimeError("bad")

        def good(_msg: dict) -> None:
            pass

        event_bus.subscribe(bad)
        event_bus.subscribe(good)
        result = event_bus.publish({"type": "test"})
        assert result is True

    def test_exception_in_first_subscriber_does_not_skip_second(self, caplog):
        """Order of registration does not affect delivery, even if"""
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
        assert any("subscriber raised" in r.getMessage() for r in caplog.records)


class TestSubscriberExceptionLogLevel:
    """
    subscriber exceptions must surface at WARNING (not DEBUG)
    These tests pin the rate-limit contract: WARNING on occurrence #1,
    """

    def test_first_exception_logged_at_warning(self, caplog):
        """the first exception from a subscriber is logged at"""
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
        """the first-occurrence WARNING record must carry the"""
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
        assert record.exc_info[0] is RuntimeError
        assert "traceback please" in str(record.exc_info[1])

    def test_second_exception_logged_at_debug(self, caplog):
        """the SECOND exception from the SAME subscriber"""
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
        assert matching[1].exc_info is None

    def test_distinct_subscribers_each_get_warning_on_first(self, caplog):
        """rate-limit counters are PER-SUBSCRIBER, the first"""
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
        """rate-limited (suppressed) occurrences are still"""
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


class TestDuplicateSubscribe:
    def test_duplicate_subscribe_deduplicated(self):
        """Subscribing the same callable twice registers it once"""
        received: list[dict] = []
        cb = received.append
        event_bus.subscribe(cb)
        event_bus.subscribe(cb)
        assert len(event_bus._subscribers) == 1
        event_bus.publish({"type": "test"})
        # Only one call, not two.
        assert received == [{"type": "test"}]

    def test_unsubscribe_after_duplicate_subscribe(self):
        """One unsubscribe removes the callable regardless of how many"""
        cb = lambda _msg: None  # noqa: E731
        event_bus.subscribe(cb)
        event_bus.subscribe(cb)
        event_bus.subscribe(cb)
        assert len(event_bus._subscribers) == 1
        event_bus.unsubscribe(cb)
        assert len(event_bus._subscribers) == 0


class TestSubscriberMutationDuringPublish:
    def test_subscriber_unsubscribing_itself_does_not_raise(self):
        """A subscriber that unsubscribes itself during publish must not"""
        call_count = [0]

        def self_unsubscribing(_msg: dict) -> None:
            call_count[0] += 1
            event_bus.unsubscribe(self_unsubscribing)

        event_bus.subscribe(self_unsubscribing)
        # Should not raise.
        event_bus.publish({"type": "test"})
        assert call_count[0] == 1
        event_bus.publish({"type": "test"})
        assert call_count[0] == 1

    def test_subscriber_unsubscribing_other_does_not_raise(self):
        """A subscriber that unsubscribes a different subscriber during"""
        other_received: list[dict] = []

        def other(msg: dict) -> None:
            other_received.append(msg)

        def first(_msg: dict) -> None:
            event_bus.unsubscribe(other)

        # Register both, order matters for the snapshot semantics.
        event_bus.subscribe(first)
        event_bus.subscribe(other)
        # Should not raise.
        event_bus.publish({"type": "test"})
        # `other` may or may not have been called depending on whether


class TestReentrantPublish:
    def test_subscriber_that_publishes_does_not_deadlock(self):
        """A subscriber that calls publish() re-entrantly must not"""
        outer_received: list[dict] = []
        inner_received: list[dict] = []

        def inner_listener(msg: dict) -> None:
            inner_received.append(msg)

        def outer_listener(_msg: dict) -> None:
            outer_received.append({"triggered": True})
            # Unsubscribe self BEFORE re-publishing so we don't recurse
            event_bus.unsubscribe(outer_listener)
            event_bus.publish({"type": "inner"})

        event_bus.subscribe(outer_listener)
        event_bus.subscribe(inner_listener)

        # Should complete (not hang). RLock allows re-entrancy.
        event_bus.publish({"type": "outer"})

        # The outer listener was called exactly once (it unsubscribed
        assert outer_received == [{"triggered": True}]

        # The inner listener received BOTH events: the original
        assert len(inner_received) == 2
        assert sorted(e["type"] for e in inner_received) == ["inner", "outer"]


class TestBackwardCompatShim:
    """the ``_push_event_registry`` / ``_push_event_registry_lock``"""

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
        """Direct event_bus subscribe/unsubscribe (replaces the old"""
        received: list[dict] = []

        event_bus.subscribe(received.append)
        event_bus.publish({"type": "shim_subscribe"})
        assert received == [{"type": "shim_subscribe"}]

        event_bus.unsubscribe(received.append)
        event_bus.publish({"type": "after_clear"})
        assert received == [{"type": "shim_subscribe"}]

    def test_unsubscribe_unknown_is_noop(self):
        """unsubscribe of a callable that was never subscribed must not"""
        before = len(event_bus._subscribers)
        event_bus.unsubscribe(lambda _msg: None)
        assert len(event_bus._subscribers) == before


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


class TestRTThreadGuard:
    """PERF-2: publish() must refuse to run on real-time audio threads."""

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
        """PERF-2: publishing from an RT (PortAudio) thread must NOT invoke"""
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


class TestShutdownConsolidation:
    """
    ``shutdown_controller._do_cleanup``). The duplicate is deleted;
    These tests pin the consolidation so a future "refactor" doesn't
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
        """GT-C1-7: ``shutdown()`` is idempotent, calling it twice"""
        # No executor was created in this test (no RT-thread publish).
        event_bus.shutdown()  # no-op
        event_bus.shutdown()  # still no-op
        # And after shutdown, a fresh publish still works (lazy
        received: list[dict] = []
        event_bus.subscribe(received.append)
        assert event_bus.publish({"type": "post_shutdown"}) is True
        assert received == [{"type": "post_shutdown"}]
        # Cleanup: shut down any executor the publish may have created
        event_bus.shutdown()


class TestCanonicalCatalogue:
    """the docstring catalogue must list every event actually"""

    def test_catalogue_lists_tray_menu_event(self):
        assert "``tray_menu``" in event_bus.__doc__

    def test_catalogue_lists_tray_state_event(self):
        assert "``tray_state``" in event_bus.__doc__

    def test_catalogue_lists_consent_required_event(self):
        assert "``consent_required``" in event_bus.__doc__

    def test_catalogue_lists_parakeet_cpu_fallback_event(self):
        assert "``parakeet_cpu_fallback``" in event_bus.__doc__

    def test_catalogue_total_count_updated(self):
        """The 'Total: N events' docstring line must stay in lockstep"""
        total = len(event_bus.EVENT_TYPES)
        assert f"Total: {total} events" in event_bus.__doc__, (
            f"event_bus.__doc__ must say 'Total: {total} events', "
            "update the catalogue docstring when EVENT_TYPES changes."
        )


class TestAsyncDispatch:
    """the single-worker executor so non-RT publisher threads are not"""

    def test_async_dispatch_returns_true_and_defers(self):
        """subscriber is delivered on the deferred-executor thread (NOT"""
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

    def test_default_publish_is_synchronous_backcompat(self):
        """existing synchronous semantics, subscriber is invoked before"""
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
        """``async_dispatch=True`` with no subscribers returns False"""
        # Make sure no subscribers are registered (the autouse
        result = event_bus.publish({"type": "test_no_subs"}, async_dispatch=True)
        assert result is False
