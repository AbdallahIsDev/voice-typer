"""Tests for :class:`voice_typer.server._busyness.BusynessCoordinator`.

AC-66: extract BusynessCoordinator that owns the legacy
``_busy_event`` + ``_lock`` primitives and exposes intent-revealing
methods (``is_busy`` / ``set_busy`` / ``set_idle`` / ``wait_idle``).

These tests verify the coordinator's API surface + the NON-inverted
semantic (``is_busy()`` returns True while busy — vs the legacy
``_busy_event.is_set()`` which returned True when NOT busy).
"""

from __future__ import annotations

import threading
import time

from voice_typer.server._busyness import BusynessCoordinator


class TestBusynessCoordinatorInitialState:
    """A freshly-constructed coordinator is IDLE (matches legacy ``_busy_event.set()``)."""

    def test_fresh_coordinator_is_idle(self) -> None:
        bc = BusynessCoordinator()
        assert bc.is_busy() is False, "freshly-constructed coordinator must be IDLE (not busy)"

    def test_fresh_coordinator_wait_idle_returns_immediately(self) -> None:
        bc = BusynessCoordinator()
        # wait_idle should return True immediately when already idle.
        start = time.perf_counter()
        result = bc.wait_idle(timeout=0.1)
        elapsed = time.perf_counter() - start
        assert result is True
        assert elapsed < 0.05, f"wait_idle took {elapsed:.3f}s, expected immediate return"


class TestBusynessCoordinatorSetBusySetIdle:
    """``set_busy`` / ``set_idle`` round-trip preserves the natural-reading semantic."""

    def test_set_busy_makes_is_busy_true(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()
        assert bc.is_busy() is True

    def test_set_idle_makes_is_busy_false(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()
        bc.set_idle()
        assert bc.is_busy() is False

    def test_set_busy_is_idempotent(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()
        bc.set_busy()  # second call must be a no-op
        assert bc.is_busy() is True
        bc.set_idle()
        assert bc.is_busy() is False

    def test_set_idle_is_idempotent(self) -> None:
        bc = BusynessCoordinator()
        bc.set_idle()  # already idle
        bc.set_idle()
        assert bc.is_busy() is False


class TestBusynessCoordinatorWaitIdle:
    """``wait_idle`` blocks while busy and returns when set_idle fires."""

    def test_wait_idle_blocks_until_set_idle(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()

        def _background_setter() -> None:
            time.sleep(0.05)
            bc.set_idle()

        threading.Thread(target=_background_setter, daemon=True).start()
        result = bc.wait_idle(timeout=1.0)
        assert result is True, "wait_idle should return True once set_idle fires"

    def test_wait_idle_times_out_when_stays_busy(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()
        result = bc.wait_idle(timeout=0.05)
        assert result is False, "wait_idle should time out (return False) when coordinator stays busy"

    def test_wait_idle_no_timeout_returns_when_set_idle(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()

        def _background_setter() -> None:
            time.sleep(0.02)
            bc.set_idle()

        threading.Thread(target=_background_setter, daemon=True).start()
        # No timeout — would hang forever if the background thread didn't fire.
        result = bc.wait_idle()
        assert result is True


class TestBusynessCoordinatorLock:
    """The companion ``threading.Lock`` is exposed via the ``lock`` property."""

    def test_lock_is_a_threading_lock(self) -> None:

        bc = BusynessCoordinator()
        lk = bc.lock
        # duck-typed: must support acquire/release + context-manager protocol.
        assert hasattr(lk, "acquire")
        assert hasattr(lk, "release")
        assert hasattr(lk, "__enter__")
        assert hasattr(lk, "__exit__")
        # ``threading.Lock`` factory returns _thread.lock (not Lock class),
        # so we duck-type instead of isinstance.
        assert callable(lk.acquire)

    def test_lock_can_be_acquired_and_released(self) -> None:
        bc = BusynessCoordinator()
        lk = bc.lock
        assert lk.acquire(blocking=False) is True
        # second acquire (non-blocking) should fail (lock already held).
        assert lk.acquire(blocking=False) is False
        lk.release()
        # After release, should be acquirable again.
        assert lk.acquire(blocking=False) is True
        lk.release()

    def test_lock_can_be_used_as_context_manager(self) -> None:
        bc = BusynessCoordinator()
        lk = bc.lock
        with lk:
            # Inside the context: second non-blocking acquire should fail.
            assert lk.acquire(blocking=False) is False
        # Outside: should be acquirable again.
        assert lk.acquire(blocking=False) is True
        lk.release()

    def test_lock_is_stable_across_accesses(self) -> None:
        """``bc.lock`` returns the SAME lock primitive on every access."""
        bc = BusynessCoordinator()
        l1 = bc.lock
        l2 = bc.lock
        assert l1 is l2, "lock property must return the same primitive on every access"


class TestBusynessCoordinatorEventBackcompat:
    """The underlying ``threading.Event`` is exposed via the ``event`` property (back-compat)."""

    def test_event_is_a_threading_event(self) -> None:
        bc = BusynessCoordinator()
        ev = bc.event
        assert hasattr(ev, "is_set")
        assert hasattr(ev, "set")
        assert hasattr(ev, "clear")
        assert hasattr(ev, "wait")

    def test_event_set_when_idle(self) -> None:
        bc = BusynessCoordinator()
        # IDLE → event is set (legacy "ready signal" semantic).
        assert bc.event.is_set() is True
        assert bc.is_busy() is False

    def test_event_clear_when_busy(self) -> None:
        bc = BusynessCoordinator()
        bc.set_busy()
        # BUSY → event is cleared.
        assert bc.event.is_set() is False
        assert bc.is_busy() is True

    def test_event_is_stable_across_accesses(self) -> None:
        bc = BusynessCoordinator()
        ev1 = bc.event
        ev2 = bc.event
        assert ev1 is ev2

    def test_legacy_event_set_clear_route_through_coordinator(self) -> None:
        """Legacy ``_busy_event.set()`` / ``.clear()`` calls must update ``is_busy()``."""
        bc = BusynessCoordinator()
        # Simulate legacy ``_busy_event.clear()`` (which meant "busy = True").
        bc.event.clear()
        assert bc.is_busy() is True
        # Simulate legacy ``_busy_event.set()`` (which meant "busy = False").
        bc.event.set()
        assert bc.is_busy() is False
