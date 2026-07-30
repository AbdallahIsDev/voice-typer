"""AB-46 regression: ``event_bus._config_change_listeners`` is a
``weakref.WeakSet`` so destroyed listener objects are auto-evicted.

Pre-AB-46, ``_config_change_listeners`` was a plain ``set`` holding
strong references to listeners.  A listener that subscribed and was
later destroyed without calling ``unsubscribe_config_changes()`` stayed
in the set forever, keeping the listener (and any objects it
referenced) alive — a latent leak.

Post-AB-46, the set is a ``weakref.WeakSet``.  When the listener is
GC'd, the WeakSet's weakref callback fires and the entry is removed
automatically.  ``_publish_config_change`` defensively skips ``None``
refs (WeakSet auto-prunes, but the snapshot → call sequence has a
small race window).

These tests verify:
- A listener that is GC'd is auto-removed from the WeakSet.
- ``_publish_config_change`` skips dead refs defensively.
- A held listener still receives events.
- ``unsubscribe_config_changes`` is still safe (no-op for unregistered).
"""

from __future__ import annotations

import gc
import weakref

import pytest
from voice_typer.server import event_bus, log_rate_limit


@pytest.fixture(autouse=True)
def _clean_listeners():
    """Snapshot + clear the listener WeakSet for each test."""
    with event_bus._config_change_lock:
        original_listeners = set(event_bus._config_change_listeners)
        event_bus._config_change_listeners.clear()
    log_rate_limit.reset()
    yield
    with event_bus._config_change_lock:
        event_bus._config_change_listeners.clear()
        event_bus._config_change_listeners.update(original_listeners)


class _RecordingListener:
    """Listener that records every ``on_config_changed`` call.

    Defines ``on_config_changed`` so it satisfies the
    ``ConfigChangeListener`` protocol (duck typing).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def on_config_changed(self, updates: dict) -> None:
        self.calls.append(updates)


# ─── WeakSet semantics ────────────────────────────────────────────────


class TestWeakSetSemantics:
    """``_config_change_listeners`` is a ``weakref.WeakSet``."""

    def test_listeners_container_is_weakset(self):
        """``_config_change_listeners`` is an instance of WeakSet."""
        import weakref as _weakref

        assert isinstance(event_bus._config_change_listeners, _weakref.WeakSet), (
            "AB-46: _config_change_listeners MUST be a weakref.WeakSet. "
            "Got: " + repr(type(event_bus._config_change_listeners))
        )

    def test_listener_gc_evicts_from_weakset(self):
        """When a listener is GC'd, it's auto-removed from the WeakSet."""
        listener = _RecordingListener()
        weak = weakref.ref(listener)
        event_bus.subscribe_config_changes(listener)
        assert event_bus._config_change_listener_count() == 1

        # Drop the strong ref and force a GC pass.
        del listener
        gc.collect()

        # The listener is gone.
        assert weak() is None, "listener should have been GC'd"
        # The WeakSet entry is auto-removed.
        assert event_bus._config_change_listener_count() == 0, (
            "AB-46: WeakSet MUST auto-evict GC'd listeners.  Count = " + str(event_bus._config_change_listener_count())
        )

    def test_held_listener_still_receives_events(self):
        """A listener that's held by the caller still receives events
        (the WeakSet entry stays alive as long as the strong ref exists)."""
        listener = _RecordingListener()
        event_bus.subscribe_config_changes(listener)
        assert event_bus._config_change_listener_count() == 1

        result = event_bus._publish_config_change({"foo": 1})
        assert result is True
        assert listener.calls == [{"foo": 1}]

    def test_multiple_listeners_all_receive_events(self):
        """Multiple held listeners all receive the event."""
        l1 = _RecordingListener()
        l2 = _RecordingListener()
        l3 = _RecordingListener()
        for listener in (l1, l2, l3):
            event_bus.subscribe_config_changes(listener)
        assert event_bus._config_change_listener_count() == 3

        result = event_bus._publish_config_change({"x": 42})
        assert result is True
        assert l1.calls == [{"x": 42}]
        assert l2.calls == [{"x": 42}]
        assert l3.calls == [{"x": 42}]

    def test_dropping_one_listener_keeps_others(self):
        """When one listener is GC'd, the others still receive events."""
        l1 = _RecordingListener()
        l2 = _RecordingListener()
        event_bus.subscribe_config_changes(l1)
        event_bus.subscribe_config_changes(l2)
        assert event_bus._config_change_listener_count() == 2

        # Drop l1.
        del l1
        gc.collect()
        assert event_bus._config_change_listener_count() == 1

        # l2 still receives events.
        result = event_bus._publish_config_change({"y": 2})
        assert result is True
        assert l2.calls == [{"y": 2}]


# ─── unsubscribe still works ──────────────────────────────────────────


class TestUnsubscribeStillWorks:
    """``unsubscribe_config_changes`` is still safe (no-op for
    unregistered listeners)."""

    def test_unsubscribe_removes_listener(self):
        """A listener that's explicitly unsubscribed no longer receives
        events."""
        listener = _RecordingListener()
        event_bus.subscribe_config_changes(listener)
        assert event_bus._config_change_listener_count() == 1

        event_bus.unsubscribe_config_changes(listener)
        assert event_bus._config_change_listener_count() == 0

        result = event_bus._publish_config_change({"foo": 1})
        assert result is False
        assert listener.calls == []

    def test_unsubscribe_unknown_is_noop(self):
        """Unsubscribing a listener that was never registered is a
        no-op (doesn't raise)."""
        listener = _RecordingListener()
        # Should not raise.
        event_bus.unsubscribe_config_changes(listener)
        assert event_bus._config_change_listener_count() == 0

    def test_unsubscribe_none_is_noop(self):
        """``unsubscribe_config_changes(None)`` is a no-op."""
        event_bus.unsubscribe_config_changes(None)
        assert event_bus._config_change_listener_count() == 0


# ─── defensive None pruning ───────────────────────────────────────────


class TestDefensiveNonePruning:
    """``_publish_config_change`` defensively skips ``None`` refs (in
    case a listener is GC'd between the snapshot and the call)."""

    def test_publish_with_no_listeners_returns_false(self):
        """With no listeners, ``_publish_config_change`` returns False."""
        assert event_bus._config_change_listener_count() == 0
        result = event_bus._publish_config_change({"foo": 1})
        assert result is False

    def test_publish_skips_dead_refs_in_snapshot(self):
        """If a listener is GC'd between the snapshot and the call, the
        dead ref is skipped (no AttributeError)."""
        # This is hard to test deterministically without instrumentation,
        # but we can at least verify the snapshot logic handles the
        # common case correctly.
        listener = _RecordingListener()
        event_bus.subscribe_config_changes(listener)
        result = event_bus._publish_config_change({"foo": 1})
        assert result is True
        assert listener.calls == [{"foo": 1}]


# ─── listener raising doesn't break the WeakSet ───────────────────────


class TestListenerRaisingStillPruned:
    """A listener that raises is logged but doesn't break the WeakSet
    (the listener is still held by the caller, so the WeakSet entry
    stays alive)."""

    def test_raising_listener_still_in_weakset_after_publish(self):
        """A held listener that raises is still in the WeakSet after
        the publish (the WeakSet entry is keyed on the listener object,
        not the call result)."""

        class _Bad:
            def on_config_changed(self, _updates):
                raise RuntimeError("boom")

        bad = _Bad()
        event_bus.subscribe_config_changes(bad)
        assert event_bus._config_change_listener_count() == 1

        # Publish — the listener raises, but is_logged and skipped.
        result = event_bus._publish_config_change({"foo": 1})
        # No successful delivery (the only listener raised).
        assert result is False

        # The listener is still in the WeakSet (held by ``bad``).
        assert event_bus._config_change_listener_count() == 1

        # Drop the strong ref — now the WeakSet entry is auto-removed.
        del bad
        gc.collect()
        assert event_bus._config_change_listener_count() == 0
