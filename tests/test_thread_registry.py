"""Tests for the central ThreadRegistry (Task 10).

These tests verify the ThreadRegistry class itself in isolation — they
do NOT depend on VoiceTyperApp or any of the spawn sites. The registry
is a pure coordination layer; the integration with each spawn site is
covered by the existing per-module tests (test_app.py, test_recording.py,
test_streaming.py, test_crash_recovery.py).

Test coverage:
- Registration / unregistration (basic CRUD)
- list_active() / list_all() snapshot semantics
- shutdown_all() joins all registered threads
- Per-thread join_timeout is respected
- A thread that doesn't exit within timeout is logged but doesn't block
- Idempotency: shutdown_all() can be called multiple times safely
- Defensive registration: re-registering an existing name logs + updates
- Threads with stop_event=None are joined but not signaled
- Cross-thread safety: concurrent register/unregister/shutdown_all
"""

from __future__ import annotations

import logging
import threading
import time

from voice_typer.server.thread_registry import (
    ThreadRegistry,
    ThreadRegistryEntry,
)

# ─── Helpers ─────────────────────────────────────────────────────────────


def _make_worker(
    stop_event: threading.Event | None,
    *,
    on_exit: threading.Event | None = None,
    sleep_before_exit: float = 0.0,
    never_exit: bool = False,
) -> threading.Thread:
    """Create and start a worker thread for tests.

    Parameters
    ----------
    stop_event : threading.Event | None
        If not None, the worker checks ``stop_event.is_set()`` each
        iteration and exits when set. If None, the worker loops forever
        (unless ``never_exit=False`` and ``sleep_before_exit`` is set).
    on_exit : threading.Event | None
        If provided, set when the worker exits. Used by tests to verify
        the worker actually exited (vs. the join timing out).
    sleep_before_exit : float
        Seconds to sleep after stop_event is set before exiting. Used to
        test the join timeout behavior.
    never_exit : bool
        If True, the worker never exits even when stop_event is set.
        Used to test the "thread didn't exit within timeout" path.
    """

    def _run() -> None:
        try:
            while True:
                if stop_event is not None and stop_event.is_set() and not never_exit:
                    if sleep_before_exit > 0:
                        time.sleep(sleep_before_exit)
                    return
                time.sleep(0.01)
        finally:
            if on_exit is not None:
                on_exit.set()

    t = threading.Thread(target=_run, name="test-worker", daemon=True)
    t.start()
    return t


# ─── Registration / unregistration ───────────────────────────────────────


class TestRegistration:
    def test_register_adds_entry(self):
        """register() adds an entry that list_all() can see."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        try:
            reg.register("worker-1", t, stop, join_timeout=1.0)
            assert "worker-1" in reg.list_all()
            assert reg.list_active() == ["worker-1"]
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_register_same_name_same_thread_is_silent(self, caplog):
        """Re-registering the same name with the same thread object is
        silent (used to update stop_event / join_timeout)."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
                reg.register("worker-1", t, stop, join_timeout=1.0)
                reg.register("worker-1", t, stop, join_timeout=2.0)
            # No warning should be emitted — same thread object.
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "Re-registering" in r.message]
            assert warnings == [], f"Unexpected re-register warning: {warnings}"
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_register_same_name_different_thread_logs_warning(self, caplog):
        """Re-registering an existing name with a DIFFERENT thread
        object logs a warning (defensive — caller should have stopped
        the old thread)."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        stop2 = threading.Event()
        t1 = _make_worker(stop1)
        t2 = _make_worker(stop2)
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
                reg.register("worker-1", t1, stop1, join_timeout=1.0)
                reg.register("worker-1", t2, stop2, join_timeout=1.0)
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "Re-registering" in r.message]
            assert len(warnings) == 1, f"Expected 1 warning, got {len(warnings)}"
            assert "worker-1" in warnings[0].message
        finally:
            stop1.set()
            stop2.set()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)

    def test_unregister_removes_entry(self):
        """unregister() removes the entry from list_all()."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        try:
            reg.register("worker-1", t, stop, join_timeout=1.0)
            assert "worker-1" in reg.list_all()
            reg.unregister("worker-1")
            assert "worker-1" not in reg.list_all()
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_unregister_unknown_name_is_noop(self):
        """unregister() on a name that's not registered is a no-op."""
        reg = ThreadRegistry()
        # Should not raise.
        reg.unregister("nonexistent")
        assert reg.list_all() == []

    def test_register_preserves_insertion_order(self):
        """list_all() returns names in insertion order (dict order)."""
        reg = ThreadRegistry()
        stops = [threading.Event() for _ in range(3)]
        threads = [_make_worker(s) for s in stops]
        try:
            reg.register("alpha", threads[0], stops[0], 1.0)
            reg.register("beta", threads[1], stops[1], 1.0)
            reg.register("gamma", threads[2], stops[2], 1.0)
            assert reg.list_all() == ["alpha", "beta", "gamma"]
        finally:
            for s in stops:
                s.set()
            for t in threads:
                t.join(timeout=2.0)


# ─── list_active / list_all ──────────────────────────────────────────────


class TestListActive:
    def test_list_active_excludes_dead_threads(self):
        """list_active() returns only names whose threads are still alive."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        reg.register("alive-worker", t, stop, join_timeout=1.0)
        try:
            assert reg.list_active() == ["alive-worker"]
        finally:
            stop.set()
        t.join(timeout=2.0)
        # After the thread exits, list_active() should be empty even
        # though the entry is still in the registry.
        assert reg.list_active() == []
        assert reg.list_all() == ["alive-worker"]

    def test_list_active_is_snapshot(self):
        """list_active() returns a snapshot — mutations after the call
        don't affect the returned list."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        try:
            reg.register("worker-1", t, stop, join_timeout=1.0)
            snapshot = reg.list_active()
            reg.register("worker-2", t, stop, join_timeout=1.0)
            # The snapshot should still be the old list.
            assert snapshot == ["worker-1"]
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_list_all_empty_registry(self):
        """list_all() on an empty registry returns []."""
        reg = ThreadRegistry()
        assert reg.list_all() == []
        assert reg.list_active() == []


# ─── shutdown_all ─────────────────────────────────────────────────────────


class TestShutdownAll:
    def test_shutdown_all_signals_and_joins(self):
        """shutdown_all() sets stop_events and joins all threads."""
        reg = ThreadRegistry()
        exits = [threading.Event() for _ in range(3)]
        stops = [threading.Event() for _ in range(3)]
        threads = [_make_worker(stops[i], on_exit=exits[i]) for i in range(3)]
        for i, t in enumerate(threads):
            reg.register(f"worker-{i}", t, stops[i], join_timeout=2.0)

        reg.shutdown_all()

        # All threads should have exited.
        for i, t in enumerate(threads):
            assert not t.is_alive(), f"worker-{i} still alive after shutdown_all"
            assert exits[i].is_set(), f"worker-{i} did not set its exit event"

    def test_shutdown_all_idempotent(self):
        """shutdown_all() can be called multiple times safely."""
        reg = ThreadRegistry()
        stop = threading.Event()
        exit_event = threading.Event()
        t = _make_worker(stop, on_exit=exit_event)
        reg.register("worker-1", t, stop, join_timeout=2.0)

        # First call signals and joins.
        reg.shutdown_all()
        assert not t.is_alive()

        # Second call is a no-op (thread is already dead; join returns
        # immediately).
        reg.shutdown_all()
        reg.shutdown_all()  # Third call also safe.
        assert not t.is_alive()

    def test_shutdown_all_empty_registry_is_noop(self):
        """shutdown_all() on an empty registry returns without error."""
        reg = ThreadRegistry()
        reg.shutdown_all()  # Should not raise.

    def test_shutdown_all_respects_per_thread_timeout(self):
        """A thread that sleeps past its join_timeout is logged but
        doesn't block shutdown."""
        reg = ThreadRegistry()
        stop = threading.Event()
        exit_event = threading.Event()
        # Worker sleeps 0.5s after stop is set before exiting. The
        # join_timeout is 0.1s, so the join will time out.
        t = _make_worker(stop, on_exit=exit_event, sleep_before_exit=0.5)
        reg.register("slow-worker", t, stop, join_timeout=0.1)

        start = time.monotonic()
        reg.shutdown_all()
        elapsed = time.monotonic() - start

        # shutdown_all should have returned in ~0.1s (the join timeout),
        # NOT 0.5s (the worker's sleep). Allow some scheduling slack.
        assert elapsed < 0.4, f"shutdown_all took {elapsed:.2f}s, expected ~0.1s (the join timeout)"
        # The thread is still alive (it's sleeping). It will exit after
        # 0.5s. Wait for it so we don't leak a thread.
        t.join(timeout=2.0)
        assert exit_event.is_set()

    def test_shutdown_all_logs_warning_for_unresponsive_thread(self, caplog):
        """A thread with a stop_event that doesn't exit within timeout
        is logged at WARNING level (potential deadlock / stuck I/O)."""
        reg = ThreadRegistry()
        stop = threading.Event()
        exit_event = threading.Event()
        # never_exit=True: worker ignores the stop event.
        t = _make_worker(stop, on_exit=exit_event, never_exit=True)
        reg.register("stuck-worker", t, stop, join_timeout=0.1)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
            reg.shutdown_all()

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "stuck-worker" in r.message and "did not exit" in r.message
        ]
        assert len(warnings) == 1, (
            f"Expected 1 warning for stuck-worker, got {len(warnings)}: {[r.message for r in caplog.records]}"
        )
        # The thread is still alive (never_exit=True). It's a daemon, so
        # it won't block process exit, but we need to clean it up for the
        # test. Patch never_exit by setting a flag the worker doesn't
        # check — the only way to stop it is to set stop_event AND wait
        # for the worker's time.sleep(0.01) loop. Since never_exit=True
        # makes the worker ignore stop_event, we just let it die as a
        # daemon when the test process exits. (Joining would hang.)
        # Verify it's still alive (confirms the warning is genuine).
        assert t.is_alive(), "stuck-worker should still be alive (never_exit=True)"

    def test_shutdown_all_logs_debug_for_no_stop_event_thread(self, caplog):
        """A thread with stop_event=None that doesn't exit within
        timeout is logged at DEBUG level (no signal was sent, so the
        timeout is expected — the existing per-site cleanup handles it)."""
        reg = ThreadRegistry()
        exit_event = threading.Event()
        # Worker with no stop_event — it loops forever.
        t = _make_worker(None, on_exit=exit_event)
        reg.register("no-stop-event-worker", t, stop_event=None, join_timeout=0.1)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.thread_registry"):
            reg.shutdown_all()

        # Should NOT have a WARNING (no stop_event was provided).
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "no-stop-event-worker" in r.message]
        assert warnings == [], f"Expected no warnings for no-stop-event-worker, got: {warnings}"
        # Should have a DEBUG message about the timeout being expected.
        debugs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "no-stop-event-worker" in r.message and "no stop_event" in r.message
        ]
        assert len(debugs) == 1, (
            f"Expected 1 debug message for no-stop-event-worker, got: "
            f"{[r.message for r in caplog.records if r.levelno == logging.DEBUG]}"
        )
        # Clean up: the worker is still alive (no stop_event). It's a
        # daemon, so it won't block process exit. We can't join it.
        # Verify it's still alive (confirms the debug message is genuine).
        assert t.is_alive(), "no-stop-event-worker should still be alive"

    def test_shutdown_all_skips_join_for_dead_threads(self, caplog):
        """shutdown_all() doesn't block on already-dead threads.

        PERF-23: the new bounded-join loop checks ``is_alive()`` before
        each slice join, so a dead thread is never joined. The log
        message is "exited cleanly after join" (the new common path
        for both already-dead and successfully-joined threads) instead
        of the old "already exited (no join needed)".
        """
        reg = ThreadRegistry()
        stop = threading.Event()
        exit_event = threading.Event()
        t = _make_worker(stop, on_exit=exit_event)
        reg.register("dead-worker", t, stop, join_timeout=5.0)

        # Stop the thread BEFORE shutdown_all.
        stop.set()
        t.join(timeout=2.0)
        assert not t.is_alive()

        # shutdown_all() should return immediately (no 5s join wait).
        start = time.monotonic()
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.thread_registry"):
            reg.shutdown_all()
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"shutdown_all took {elapsed:.2f}s on a dead thread, expected ~0s"
        # Should log that the thread exited cleanly (the new common
        # path covers both already-dead and successfully-joined).
        debugs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "dead-worker" in r.message and "exited cleanly" in r.message
        ]
        assert len(debugs) == 1, (
            f"Expected 'exited cleanly' debug for dead-worker, got: {[r.message for r in caplog.records]}"
        )

    def test_shutdown_all_continues_after_one_thread_times_out(self):
        """If thread A times out, shutdown_all() still signals and
        joins thread B (shutdown is never blocked by a single stuck thread)."""
        reg = ThreadRegistry()
        stop_a = threading.Event()
        stop_b = threading.Event()
        exit_b = threading.Event()

        # Thread A: never exits (never_exit=True). Short timeout.
        t_a = _make_worker(stop_a, never_exit=True)
        # Thread B: exits when stop is set.
        t_b = _make_worker(stop_b, on_exit=exit_b)

        reg.register("stuck", t_a, stop_a, join_timeout=0.1)
        reg.register("responsive", t_b, stop_b, join_timeout=2.0)

        start = time.monotonic()
        reg.shutdown_all()
        elapsed = time.monotonic() - start

        # shutdown_all should have returned in ~0.1s (A's timeout) + ~0s
        # (B exits immediately). Allow some scheduling slack.
        assert elapsed < 1.0, f"shutdown_all took {elapsed:.2f}s, expected ~0.1s"
        # B should have exited; A should still be alive.
        assert not t_b.is_alive(), "responsive worker should have exited"
        assert exit_b.is_set(), "responsive worker should have set exit event"
        assert t_a.is_alive(), "stuck worker should still be alive"


# ─── Thread safety ───────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_register_and_shutdown(self):
        """Concurrent register() and shutdown_all() calls don't corrupt
        the registry's internal state.

        This is a smoke test — threading bugs often manifest as flaky
        failures under load, so we run the concurrent ops a few times
        to increase the chance of catching a regression.
        """
        for _ in range(5):
            reg = ThreadRegistry()
            errors: list[Exception] = []
            self._run_concurrent_ops(reg, errors)
            assert errors == [], f"Concurrent ops raised: {errors}"
            # Final shutdown to clean up any remaining threads.
            reg.shutdown_all()

    @staticmethod
    def _run_concurrent_ops(reg: ThreadRegistry, errors: list[Exception]) -> None:
        """Run concurrent register/unregister/shutdown ops on ``reg``.

        Split out from ``test_concurrent_register_and_shutdown`` so the
        closure functions bind ``reg`` and ``errors`` as parameters
        (avoids ruff B023 false-positive about loop-variable binding).
        """

        def register_loop(r: ThreadRegistry, errs: list[Exception]) -> None:
            try:
                for i in range(20):
                    stop = threading.Event()
                    t = _make_worker(stop)
                    r.register(f"worker-{i}", t, stop, join_timeout=0.05)
                    # Immediately unregister some so the registry
                    # churns.
                    if i % 2 == 0:
                        r.unregister(f"worker-{i}")
                        stop.set()
                        t.join(timeout=0.5)
            except Exception as e:
                errs.append(e)

        def shutdown_loop(r: ThreadRegistry, errs: list[Exception]) -> None:
            try:
                for _ in range(10):
                    r.shutdown_all()
            except Exception as e:
                errs.append(e)

        threads = [
            threading.Thread(target=register_loop, args=(reg, errors)),
            threading.Thread(target=register_loop, args=(reg, errors)),
            threading.Thread(target=shutdown_loop, args=(reg, errors)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)


# ─── ThreadRegistryEntry dataclass ───────────────────────────────────────


class TestThreadRegistryEntry:
    def test_entry_is_frozen_like(self):
        """ThreadRegistryEntry is a regular dataclass — fields are
        accessible by name. We don't freeze it because shutdown_all()
        doesn't mutate entries, but tests can construct one directly."""
        stop = threading.Event()
        t = threading.Thread(target=lambda: None, daemon=True)
        entry = ThreadRegistryEntry(
            name="test",
            thread=t,
            stop_event=stop,
            join_timeout=1.5,
        )
        assert entry.name == "test"
        assert entry.thread is t
        assert entry.stop_event is stop
        assert entry.join_timeout == 1.5

    def test_entry_accepts_none_stop_event(self):
        """ThreadRegistryEntry accepts stop_event=None for threads that
        don't support graceful shutdown (e.g. one-shot preloaders)."""
        t = threading.Thread(target=lambda: None, daemon=True)
        entry = ThreadRegistryEntry(
            name="preloader",
            thread=t,
            stop_event=None,
            join_timeout=2.0,
        )
        assert entry.stop_event is None
