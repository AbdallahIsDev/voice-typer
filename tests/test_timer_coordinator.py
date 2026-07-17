"""RW-9 regression tests for the ``TimerCoordinator`` extraction.

The two timer-lifecycle methods (``_schedule_timer`` and
``_cancel_pending_timers``) were extracted from ``VoiceTyperApp`` to
``voice_typer/server/timer_coordinator.py``. ``VoiceTyperApp`` keeps
thin delegate methods so existing callers (and tests that monkeypatch
``app._schedule_timer`` / ``app._cancel_pending_timers``) keep working
unchanged.

These tests pin the contract of the extraction:

1. ``TimerCoordinator`` exposes the two methods and the three state
   attributes (``_pending_timers``, ``_pending_timers_lock``,
   ``_timer_generation``).
2. ``_schedule_timer`` appends to ``_pending_timers`` and starts the
   timer.
3. ``_schedule_timer`` callbacks actually fire after the delay.
4. ``_cancel_pending_timers`` clears the list, increments the
   generation, and cancels the underlying timers (so their callbacks
   do not fire).
5. The generation guard prevents stale callbacks (scheduled before a
   cancel) from firing after the cancel.
6. The lock makes concurrent schedule/cancel safe from multiple
   threads (ARCH-022 / NEW-CONC-002).

The tests use real ``threading.Timer`` instances with very short
delays (1–10 ms) so the full suite runs in well under a second.
"""

from __future__ import annotations

import threading
import time

import pytest
from voice_typer.server.timer_coordinator import TimerCoordinator


@pytest.fixture
def coordinator() -> TimerCoordinator:
    """Return a fresh ``TimerCoordinator`` with a dummy back-reference.

    The two methods under test don't touch ``self._app``, so a
    ``MagicMock`` (or ``None``) suffices. We pass ``None`` to keep the
    fixture dependency-free and avoid any chance of accidentally
    exercising real app state.
    """
    return TimerCoordinator(app=None)


# ─── Construction / API surface ────────────────────────────────────────


class TestTimerCoordinatorConstruction:
    """Verify ``TimerCoordinator.__init__`` sets up the migrated state."""

    def test_methods_exist_and_are_callable(self, coordinator):
        """The two extracted methods must exist and be callable."""
        assert callable(coordinator._schedule_timer), "TimerCoordinator._schedule_timer must be callable"
        assert callable(coordinator._cancel_pending_timers), "TimerCoordinator._cancel_pending_timers must be callable"

    def test_pending_timers_initialized_empty(self, coordinator):
        """``_pending_timers`` starts as an empty list."""
        assert coordinator._pending_timers == [], "TimerCoordinator._pending_timers must start as an empty list"

    def test_pending_timers_lock_is_threading_lock(self, coordinator):
        """``_pending_timers_lock`` must be a ``threading.Lock`` (ARCH-022).

        ``RLock`` would also work but the contract pins ``Lock`` so
        that re-entrant acquisition (which would mask lock-ordering
        bugs) is impossible.
        """
        assert isinstance(coordinator._pending_timers_lock, type(threading.Lock())), (
            "TimerCoordinator._pending_timers_lock must be a threading.Lock instance"
        )

    def test_timer_generation_starts_at_zero(self, coordinator):
        """``_timer_generation`` must start at 0 (incremented on each cancel)."""
        assert coordinator._timer_generation == 0, "TimerCoordinator._timer_generation must start at 0"

    def test_back_reference_app_is_stored(self):
        """``TimerCoordinator`` must hold a back-reference to the app."""
        sentinel = object()
        tc = TimerCoordinator(app=sentinel)
        assert tc._app is sentinel, "TimerCoordinator._app must be the object passed to __init__"


# ─── _schedule_timer ───────────────────────────────────────────────────


class TestScheduleTimer:
    """Pin ``_schedule_timer``'s side effects and firing behaviour."""

    def test_schedule_appends_to_pending_timers(self, coordinator):
        """Each call must append exactly one timer to ``_pending_timers``."""
        coordinator._schedule_timer(10.0, lambda: None)  # long delay — won't fire
        assert len(coordinator._pending_timers) == 1, "_schedule_timer must append the new timer to _pending_timers"
        coordinator._schedule_timer(10.0, lambda: None)
        assert len(coordinator._pending_timers) == 2
        # cleanup so the long-delay timers don't leak into the next test
        coordinator._cancel_pending_timers()

    def test_schedule_returns_started_threading_timer(self, coordinator):
        """The returned object must be a started ``threading.Timer``."""
        timer = coordinator._schedule_timer(10.0, lambda: None)
        try:
            assert isinstance(timer, threading.Timer), "_schedule_timer must return a threading.Timer instance"
            assert timer.daemon is True, (
                "RACE-016: scheduled timers must be daemon=True so they don't block process exit"
            )
            # The timer must be started (its internal thread is alive
            # while it's waiting for the delay to elapse).
            assert timer.is_alive(), "_schedule_timer must call timer.start() before returning"
        finally:
            timer.cancel()

    def test_schedule_callback_fires_after_delay(self, coordinator):
        """The scheduled callback must actually fire once the delay elapses."""
        fired = threading.Event()
        coordinator._schedule_timer(0.01, fired.set)
        assert fired.wait(timeout=1.0), "_schedule_timer callback must fire within 1s of a 10ms delay"

    def test_schedule_does_not_increment_generation(self, coordinator):
        """Scheduling must NOT bump the generation counter.

        Only ``_cancel_pending_timers`` bumps the generation — this is
        what makes the stale-callback guard work.
        """
        gen_before = coordinator._timer_generation
        timer = coordinator._schedule_timer(10.0, lambda: None)
        try:
            assert coordinator._timer_generation == gen_before, "_schedule_timer must not increment _timer_generation"
        finally:
            timer.cancel()


# ─── _cancel_pending_timers ────────────────────────────────────────────


class TestCancelPendingTimers:
    """Pin ``_cancel_pending_timers``'s clear / cancel / bump behaviour."""

    def test_cancel_clears_pending_timers_list(self, coordinator):
        """After cancel, ``_pending_timers`` must be empty."""
        coordinator._schedule_timer(10.0, lambda: None)
        coordinator._schedule_timer(10.0, lambda: None)
        assert len(coordinator._pending_timers) == 2
        coordinator._cancel_pending_timers()
        assert coordinator._pending_timers == [], "_cancel_pending_timers must clear _pending_timers"

    def test_cancel_increments_generation(self, coordinator):
        """Each cancel must bump ``_timer_generation`` by exactly 1."""
        assert coordinator._timer_generation == 0
        coordinator._cancel_pending_timers()
        assert coordinator._timer_generation == 1, "_cancel_pending_timers must increment _timer_generation"
        coordinator._cancel_pending_timers()
        assert coordinator._timer_generation == 2

    def test_cancel_prevents_pending_callbacks_from_firing(self, coordinator):
        """Cancelled timers must NOT invoke their callbacks."""
        fired = threading.Event()
        coordinator._schedule_timer(0.05, fired.set)
        # Cancel before the 50ms delay elapses.
        coordinator._cancel_pending_timers()
        # Give the timer plenty of time to "would have fired" if cancel
        # hadn't worked.
        time.sleep(0.15)
        assert not fired.is_set(), "_cancel_pending_timers must cancel the underlying Timer so the callback never fires"

    def test_cancel_is_safe_when_no_timers_pending(self, coordinator):
        """``_cancel_pending_timers`` on an empty list must not raise."""
        # Should be a no-op.
        coordinator._cancel_pending_timers()
        coordinator._cancel_pending_timers()
        assert coordinator._pending_timers == []


# ─── Generation guard (stale-callback prevention) ──────────────────────


class TestGenerationGuard:
    """Pin the generation guard: stale callbacks must be suppressed."""

    def test_stale_callback_does_not_fire_after_cancel(self, coordinator):
        """A timer scheduled BEFORE a cancel must not fire AFTER it.

        Scenario:
          - Schedule a timer with delay D1.
          - Cancel (bumps generation from 0 -> 1).
          - Wait > D1.

        The captured generation (0) no longer matches the current
        generation (1), so the guarded wrapper must skip the user
        callback.
        """
        fired = threading.Event()
        # Schedule with a delay long enough that the cancel will land
        # before the timer fires.
        coordinator._schedule_timer(0.05, fired.set)
        # Cancel immediately (well before 50ms).
        coordinator._cancel_pending_timers()
        # Wait long enough that, without the generation guard, the
        # callback would have fired.
        time.sleep(0.15)
        assert not fired.is_set(), "Generation guard failed: stale callback fired after cancel"

    def test_new_callback_after_cancel_does_fire(self, coordinator):
        """A timer scheduled AFTER a cancel must fire normally.

        Confirms the guard doesn't over-suppress: a fresh schedule
        captures the new generation and fires as expected.
        """
        fired = threading.Event()
        coordinator._cancel_pending_timers()  # bumps gen 0 -> 1
        coordinator._schedule_timer(0.01, fired.set)  # captures gen 1
        assert fired.wait(timeout=1.0), (
            "Fresh callbacks scheduled after a cancel must fire (generation guard must not over-suppress)"
        )

    def test_multiple_cancels_invalidate_all_prior_schedules(self, coordinator):
        """All timers scheduled before ANY cancel are invalidated."""
        fired_a = threading.Event()
        fired_b = threading.Event()
        coordinator._schedule_timer(0.05, fired_a.set)  # captures gen 0
        coordinator._cancel_pending_timers()  # gen 0 -> 1
        coordinator._schedule_timer(0.05, fired_b.set)  # captures gen 1
        coordinator._cancel_pending_timers()  # gen 1 -> 2
        time.sleep(0.15)
        assert not fired_a.is_set(), "Timer A (gen 0) must be suppressed"
        assert not fired_b.is_set(), "Timer B (gen 1) must be suppressed"


# ─── Thread safety (ARCH-022 / NEW-CONC-002) ───────────────────────────


class TestThreadSafety:
    """Concurrent schedule/cancel from multiple threads must be safe."""

    def test_concurrent_schedule_and_cancel_no_errors(self, coordinator):
        """Two threads — one scheduling, one cancelling — must not raise.

        Reproduces the ARCH-022 race condition that motivated
        ``_pending_timers_lock``: without the lock, the canceller's
        ``for timer in self._pending_timers`` iteration races with the
        scheduler's ``self._pending_timers.append(timer)`` and raises
        ``RuntimeError: list changed size during iteration``.
        """
        errors: list[Exception] = []
        stop = threading.Event()

        def scheduler():
            try:
                while not stop.is_set():
                    coordinator._schedule_timer(0.001, lambda: None)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def canceller():
            try:
                while not stop.is_set():
                    coordinator._cancel_pending_timers()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=scheduler, name="scheduler")
        t2 = threading.Thread(target=canceller, name="canceller")
        t1.start()
        t2.start()
        time.sleep(0.1)
        stop.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        assert not t1.is_alive(), "scheduler thread deadlocked"
        assert not t2.is_alive(), "canceller thread deadlocked"
        assert errors == [], f"concurrent schedule/cancel raised: {errors}"
        # Final cleanup so no daemon timers leak into the next test.
        coordinator._cancel_pending_timers()

    def test_many_threads_concurrent_schedule_no_errors(self, coordinator):
        """4 scheduler threads + 1 canceller must be race-free.

        A harder stress: ARCH-022 notes the list is appended to from
        the tray thread, the transcription thread, AND the timer thread
        itself — so the lock must handle N>2 concurrent appenders.
        """
        errors: list[Exception] = []
        stop = threading.Event()

        def make_scheduler(idx: int):
            def _run():
                try:
                    while not stop.is_set():
                        coordinator._schedule_timer(0.001, lambda: None)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            return _run

        def canceller():
            try:
                while not stop.is_set():
                    coordinator._cancel_pending_timers()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=make_scheduler(i), name=f"sched-{i}") for i in range(4)]
        threads.append(threading.Thread(target=canceller, name="canceller"))
        for t in threads:
            t.start()
        time.sleep(0.1)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
            assert not t.is_alive(), f"thread {t.name!r} deadlocked"
        assert errors == [], f"multi-thread schedule/cancel raised: {errors}"
        coordinator._cancel_pending_timers()

    def test_lock_held_during_snapshot_clear_increment(self, coordinator):
        """The lock must be held across snapshot+clear+generation-increment.

        The documented contract is that ``_cancel_pending_timers`` does
        its snapshot / list-clear / generation-increment *inside* the
        lock and the per-timer ``timer.cancel()`` calls *outside* the
        lock (so a slow ``timer.cancel()`` doesn't block other threads
        from scheduling). We pin this by replacing ``_pending_timers``
        with a list subclass whose ``clear()`` blocks until released —
        if the lock isn't held during ``clear()``, a concurrent
        observer would be able to acquire it mid-clear (which would
        race).

        Concretely: while the patched ``clear()`` is blocked, an
        observer thread tries to acquire ``_pending_timers_lock`` and
        must NOT succeed until ``clear()`` returns.
        """
        clear_started = threading.Event()
        clear_release = threading.Event()

        class SlowClearList(list):
            """``list`` subclass whose ``clear()`` blocks on an event."""

            def clear(self):  # type: ignore[override]
                clear_started.set()
                clear_release.wait(timeout=2.0)
                super().clear()

        # Swap in the slow-clear list with one pre-existing timer entry
        # so the snapshot step has something to iterate.
        coordinator._pending_timers = SlowClearList()
        coordinator._pending_timers.append(threading.Timer(10.0, lambda: None))

        got_lock_during_clear = threading.Event()

        def observer():
            # Wait until the cancel has entered the slow clear().
            assert clear_started.wait(timeout=1.0), "cancel did not reach the slow clear() step within 1s"
            # Try to acquire the lock while clear() is still blocked.
            # If the lock is held (correct), this blocks until we
            # release clear_release. If the lock is NOT held (regression),
            # this succeeds immediately.
            with coordinator._pending_timers_lock:
                got_lock_during_clear.set()

        obs = threading.Thread(target=observer, name="lock-observer")
        obs.start()

        cancel_thread = threading.Thread(target=coordinator._cancel_pending_timers, name="cancel")
        cancel_thread.start()

        # Give the cancel thread time to acquire the lock and reach the
        # slow clear() (which sets clear_started). Give the observer
        # time to attempt the lock acquisition.
        time.sleep(0.05)
        assert clear_started.is_set(), "cancel did not enter the slow clear() — fixture setup wrong"
        assert not got_lock_during_clear.is_set(), (
            "_cancel_pending_timers must hold _pending_timers_lock during the "
            "snapshot+clear+generation-increment critical section (an observer "
            "acquired the lock while clear() was still blocked)"
        )

        # Release the slow clear() so cancel can finish.
        clear_release.set()
        cancel_thread.join(timeout=2.0)
        obs.join(timeout=2.0)
        assert not cancel_thread.is_alive(), "cancel thread deadlocked"
        assert not obs.is_alive(), "observer thread deadlocked"
        assert got_lock_during_clear.is_set(), (
            "After cancel releases the lock, the observer must be able to acquire it (confirms cancel actually held it)"
        )
