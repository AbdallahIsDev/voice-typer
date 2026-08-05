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
        # NOT 0.5s (the worker's sleep). Allow generous scheduling slack:
        # CI runners (GitHub Actions ubuntu-latest) exhibit significant
        # CPU jitter under concurrent load — a 0.4s wall-clock threshold
        # flaked ~1% of runs. Bumped to 1.5s (15x the join timeout) which
        # still catches the regression (a true hang would be >30s).
        assert elapsed < 1.5, f"shutdown_all took {elapsed:.2f}s, expected ~0.1s (the join timeout)"
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


# register() auto-prune ────────────────────────────────────


class TestRegisterAutoPrune:
    """UE-11-F3: ``register()`` auto-prunes dead entries at the start
    of every call."""

    def test_register_prunes_dead_entries(self):
        """A dead entry is removed when the next ``register()`` is called."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        t1 = _make_worker(stop1)
        try:
            reg.register("old", t1, stop1, join_timeout=1.0)
            assert reg.list_all() == ["old"]
            stop1.set()
            t1.join(timeout=2.0)
            assert not t1.is_alive()
            # list_all still shows "old" (no register call yet).
            assert reg.list_all() == ["old"]
            # Now register a new thread — auto-prune should remove "old".
            stop2 = threading.Event()
            t2 = _make_worker(stop2)
            try:
                reg.register("new", t2, stop2, join_timeout=1.0)
                assert reg.list_all() == ["new"], f"dead 'old' entry should have been pruned; got {reg.list_all()}"
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            stop1.set()
            t1.join(timeout=2.0)

    def test_register_keeps_alive_entries(self):
        """Alive entries are NOT pruned."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        stop2 = threading.Event()
        t1 = _make_worker(stop1)
        t2 = _make_worker(stop2)
        try:
            reg.register("a", t1, stop1, join_timeout=1.0)
            reg.register("b", t2, stop2, join_timeout=1.0)
            assert reg.list_all() == ["a", "b"]
        finally:
            stop1.set()
            stop2.set()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)

    def test_register_same_name_prunes_dead_old_silently(self, caplog):
        """Re-registering a name whose old thread is DEAD is silent."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        t1 = _make_worker(stop1)
        try:
            reg.register("w", t1, stop1, join_timeout=1.0)
            stop1.set()
            t1.join(timeout=2.0)
            assert not t1.is_alive()

            stop2 = threading.Event()
            t2 = _make_worker(stop2)
            try:
                with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
                    reg.register("w", t2, stop2, join_timeout=1.0)
                warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "Re-registering" in r.message]
                assert warnings == [], (
                    f"re-registering a dead name should be silent; got warnings: {[r.message for r in warnings]}"
                )
                assert reg.list_all() == ["w"]
                assert reg._entries["w"].thread is t2
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            stop1.set()
            t1.join(timeout=2.0)

    def test_register_prunes_multiple_dead_entries(self):
        """Multiple dead entries are all pruned in one ``register()`` call."""
        reg = ThreadRegistry()
        stops = [threading.Event() for _ in range(3)]
        threads = [_make_worker(s) for s in stops]
        try:
            for i, (s, t) in enumerate(zip(stops, threads, strict=False)):
                reg.register(f"w{i}", t, s, join_timeout=1.0)
            assert reg.list_all() == ["w0", "w1", "w2"]
            for s in stops:
                s.set()
            for t in threads:
                t.join(timeout=2.0)
            assert reg.list_all() == ["w0", "w1", "w2"]
            assert reg.list_active() == []
            stop_new = threading.Event()
            t_new = _make_worker(stop_new)
            try:
                reg.register("new", t_new, stop_new, join_timeout=1.0)
                assert reg.list_all() == ["new"], f"all dead entries should be pruned; got {reg.list_all()}"
            finally:
                stop_new.set()
                t_new.join(timeout=2.0)
        finally:
            for s in stops:
                s.set()
            for t in threads:
                t.join(timeout=2.0)


# shutdown_all() auto-prune ────────────────────────────────


class TestShutdownAllAutoPrune:
    """UE-11-F3: ``shutdown_all()`` prunes dead entries at the end of
    the call (after Phase 3 logging)."""

    def test_shutdown_all_prunes_dead_entries_at_end(self):
        """After ``shutdown_all()``, dead entries are removed from
        ``self._entries``."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        reg.register("w", t, stop, join_timeout=2.0)
        try:
            assert reg.list_all() == ["w"]
            reg.shutdown_all()
            assert not t.is_alive()
            assert reg.list_all() == [], f"dead entry should be pruned after shutdown_all; got {reg.list_all()}"
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_shutdown_all_keeps_alive_entries(self):
        """Alive entries (stuck threads) stay in the registry after
        ``shutdown_all()``."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop, never_exit=True)
        reg.register("stuck", t, stop, join_timeout=0.1)
        try:
            reg.shutdown_all()
            assert t.is_alive(), "stuck thread should still be alive"
            assert reg.list_all() == ["stuck"], f"alive entry should stay after shutdown_all; got {reg.list_all()}"
        finally:
            pass  # never_exit worker — let it die as daemon

    def test_shutdown_all_phase3_still_logs_exited_cleanly(self, caplog):
        """Phase 3 logging uses the original snapshot, so a dead entry
        still gets the 'exited cleanly' debug log."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        reg.register("w", t, stop, join_timeout=5.0)
        stop.set()
        t.join(timeout=2.0)
        assert not t.is_alive()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.thread_registry"):
            reg.shutdown_all()
        debugs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "w" in r.message and "exited cleanly" in r.message
        ]
        assert len(debugs) == 1, (
            f"expected 'exited cleanly' debug for dead entry; got: {[r.message for r in caplog.records]}"
        )

    def test_shutdown_all_prunes_dead_entries_each_slice(self):
        """UE-11-F3 sub-item 5: the join loop prunes dead entries at
        the start of each slice so we don't keep re-walking them."""
        reg = ThreadRegistry()
        for i in range(10):
            dead_t = threading.Thread(target=lambda: None, daemon=True)
            dead_t.start()
            dead_t.join(timeout=1.0)
            assert not dead_t.is_alive()
            reg.register(f"dead{i}", dead_t, stop_event=None, join_timeout=5.0)
        stop = threading.Event()
        alive_t = _make_worker(stop)
        reg.register("alive", alive_t, stop, join_timeout=2.0)
        try:
            start = time.monotonic()
            reg.shutdown_all()
            elapsed = time.monotonic() - start
            assert elapsed < 1.0, f"shutdown_all took {elapsed:.2f}s with 10 dead + 1 alive; expected <1.0s"
            assert not alive_t.is_alive()
            assert reg.list_all() == []
        finally:
            stop.set()
            alive_t.join(timeout=2.0)

    def test_shutdown_all_idempotent_after_prune(self):
        """After the first ``shutdown_all()`` prunes dead entries, the
        second call sees an empty registry and returns immediately."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        reg.register("w", t, stop, join_timeout=2.0)
        try:
            reg.shutdown_all()
            assert not t.is_alive()
            assert reg.list_all() == []
            start = time.monotonic()
            reg.shutdown_all()
            elapsed = time.monotonic() - start
            # Idempotent shutdown on an empty registry should return in
            # microseconds. Allow generous headroom: CI runners (GitHub
            # Actions ubuntu-latest) exhibit CPU jitter under -n auto load
            # — a 0.1s wall-clock threshold flaked occasionally. Bumped
            # to 0.5s (5x) which still catches the regression (a real
            # re-scan would take seconds, not sub-100ms).
            assert elapsed < 0.5, f"second shutdown_all on empty registry took {elapsed:.2f}s; expected <0.1s"
        finally:
            stop.set()
            t.join(timeout=2.0)


# register(join_previous_timeout=...) ─────────────────────


class TestJoinPreviousTimeout:
    """UE-11-F3: ``register()`` optionally signals + joins the previous
    thread before overwriting."""

    def test_join_previous_timeout_signals_and_joins_old(self):
        """``join_previous_timeout=1.0`` sets the old thread's
        stop_event and joins it before overwriting."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        t1 = _make_worker(stop1)
        try:
            reg.register("w", t1, stop1, join_timeout=1.0)
            assert t1.is_alive()

            stop2 = threading.Event()
            t2 = _make_worker(stop2)
            try:
                reg.register(
                    "w",
                    t2,
                    stop2,
                    join_timeout=1.0,
                    join_previous_timeout=2.0,
                )
                assert not t1.is_alive(), "join_previous_timeout should have signaled + joined t1"
                assert stop1.is_set(), "stop1 should have been set"
                assert reg._entries["w"].thread is t2
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            stop1.set()
            t1.join(timeout=2.0)

    def test_join_previous_timeout_zero_preserves_old_behavior(self):
        """``join_previous_timeout=0.0`` (default) does NOT join the
        old thread — preserves the prior behavior."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        t1 = _make_worker(stop1)
        try:
            reg.register("w", t1, stop1, join_timeout=1.0)
            assert t1.is_alive()

            stop2 = threading.Event()
            t2 = _make_worker(stop2)
            try:
                reg.register("w", t2, stop2, join_timeout=1.0)
                assert t1.is_alive(), "default join_previous_timeout=0 should NOT join t1"
                assert not stop1.is_set(), "default join_previous_timeout=0 should NOT set stop1"
                assert reg._entries["w"].thread is t2
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            stop1.set()
            t1.join(timeout=2.0)

    def test_join_previous_timeout_no_stop_event_joins_without_signal(self):
        """When the old entry has ``stop_event=None``, join_previous
        joins without signaling."""
        reg = ThreadRegistry()

        def _quick():
            time.sleep(0.05)

        t1 = threading.Thread(target=_quick, daemon=True, name="old")
        t1.start()
        try:
            reg.register("w", t1, stop_event=None, join_timeout=1.0)
            assert t1.is_alive()

            stop2 = threading.Event()
            t2 = _make_worker(stop2)
            try:
                reg.register(
                    "w",
                    t2,
                    stop2,
                    join_timeout=1.0,
                    join_previous_timeout=2.0,
                )
                assert not t1.is_alive(), "join_previous_timeout should have joined t1 even without stop_event"
                assert reg._entries["w"].thread is t2
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            t1.join(timeout=2.0)

    def test_join_previous_timeout_overwrites_on_timeout(self, caplog):
        """If the old thread doesn't exit within join_previous_timeout,
        the entry is overwritten anyway (best-effort) and a warning is
        logged."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        t1 = _make_worker(stop1, never_exit=True)
        try:
            reg.register("w", t1, stop1, join_timeout=1.0)
            assert t1.is_alive()

            stop2 = threading.Event()
            t2 = _make_worker(stop2)
            try:
                with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
                    reg.register(
                        "w",
                        t2,
                        stop2,
                        join_timeout=1.0,
                        join_previous_timeout=0.05,
                    )
                assert t1.is_alive(), "t1 should still be alive (never_exit)"
                assert reg._entries["w"].thread is t2, "t2 should be registered even though t1 didn't exit"
                timeout_warnings = [
                    r
                    for r in caplog.records
                    if r.levelno >= logging.WARNING and "did not exit within" in r.message and "'w'" in r.message
                ]
                assert len(timeout_warnings) >= 1, (
                    f"expected a warning about t1 not exiting; got: {[r.message for r in caplog.records]}"
                )
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            stop1.set()

    def test_join_previous_timeout_same_thread_is_noop(self):
        """``join_previous_timeout`` doesn't fire when re-registering
        the SAME thread object."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = _make_worker(stop)
        try:
            reg.register("w", t, stop, join_timeout=1.0)
            reg.register(
                "w",
                t,
                stop,
                join_timeout=2.0,
                join_previous_timeout=1.0,
            )
            assert t.is_alive(), "same-thread re-register should not join"
            assert reg._entries["w"].join_timeout == 2.0
        finally:
            stop.set()
            t.join(timeout=2.0)


# spawn_and_register ──────────────────────────────────────


class TestSpawnAndRegister:
    """UE-11-F3: ``spawn_and_register`` creates, starts, and registers
    a worker thread in one call."""

    def test_creates_starts_and_registers(self):
        """``spawn_and_register`` returns a started, registered thread."""
        reg = ThreadRegistry()
        stop = threading.Event()
        try:
            t = reg.spawn_and_register(
                "my-worker",
                _worker_target,
                args=(stop,),
                stop_event=stop,
                join_timeout=2.0,
            )
            assert isinstance(t, threading.Thread)
            assert t.name == "my-worker"
            assert t.is_alive(), "spawned thread should be running"
            assert "my-worker" in reg.list_all()
            assert reg.list_active() == ["my-worker"]
            assert reg._entries["my-worker"].thread is t
        finally:
            stop.set()
            reg.shutdown_all()

    def test_returns_the_registered_thread(self):
        """The returned thread is the same object as the one in the
        registry."""
        reg = ThreadRegistry()
        stop = threading.Event()
        try:
            t = reg.spawn_and_register(
                "w",
                _worker_target,
                args=(stop,),
                stop_event=stop,
                join_timeout=2.0,
            )
            assert reg._entries["w"].thread is t
        finally:
            stop.set()
            reg.shutdown_all()

    def test_daemon_by_default(self):
        """The spawned thread is a daemon by default."""
        reg = ThreadRegistry()
        stop = threading.Event()
        try:
            t = reg.spawn_and_register(
                "w",
                _worker_target,
                args=(stop,),
                stop_event=stop,
                join_timeout=2.0,
            )
            assert t.daemon is True
        finally:
            stop.set()
            reg.shutdown_all()

    def test_non_daemon_when_requested(self):
        """``daemon=False`` creates a non-daemon thread."""
        reg = ThreadRegistry()
        stop = threading.Event()
        try:
            t = reg.spawn_and_register(
                "w",
                _worker_target,
                args=(stop,),
                stop_event=stop,
                join_timeout=2.0,
                daemon=False,
            )
            assert t.daemon is False
        finally:
            stop.set()
            reg.shutdown_all()

    def test_passes_args_and_kwargs(self):
        """``args`` and ``kwargs`` are forwarded to the target."""
        reg = ThreadRegistry()
        stop = threading.Event()
        received: dict = {}
        received_lock = threading.Lock()

        def _target(stop_event, *, label):
            with received_lock:
                received["args"] = (stop_event,)
                received["kwargs"] = {"label": label}
            while not stop_event.is_set():
                time.sleep(0.01)

        try:
            reg.spawn_and_register(
                "w",
                _target,
                args=(stop,),
                kwargs={"label": "test"},
                stop_event=stop,
                join_timeout=2.0,
            )
            for _ in range(100):
                if received:
                    break
                time.sleep(0.01)
            assert received.get("kwargs") == {"label": "test"}, f"kwargs not forwarded; got {received}"
            assert received.get("args") == (stop,), f"args not forwarded; got {received}"
        finally:
            stop.set()
            reg.shutdown_all()

    def test_shutdown_all_joins_spawned_worker(self):
        """A worker spawned via ``spawn_and_register`` is joined by
        ``shutdown_all()``."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = reg.spawn_and_register(
            "w",
            _worker_target,
            args=(stop,),
            stop_event=stop,
            join_timeout=2.0,
        )
        reg.shutdown_all()
        assert not t.is_alive(), "shutdown_all should have joined the spawned worker"

    def test_spawn_and_register_join_previous(self):
        """``spawn_and_register`` forwards ``join_previous_timeout`` to
        ``register()``."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        t1 = reg.spawn_and_register(
            "w",
            _worker_target,
            args=(stop1,),
            stop_event=stop1,
            join_timeout=1.0,
        )
        try:
            assert t1.is_alive()
            stop2 = threading.Event()
            t2 = reg.spawn_and_register(
                "w",
                _worker_target,
                args=(stop2,),
                stop_event=stop2,
                join_timeout=1.0,
                join_previous_timeout=2.0,
            )
            try:
                assert not t1.is_alive(), "spawn_and_register(join_previous_timeout=...) should have joined t1"
                assert stop1.is_set()
                assert reg._entries["w"].thread is t2
            finally:
                stop2.set()
                t2.join(timeout=2.0)
        finally:
            stop1.set()
            t1.join(timeout=2.0)

    def test_spawn_and_register_no_stop_event(self):
        """``spawn_and_register`` accepts ``stop_event=None`` for
        fire-and-forget workers."""
        reg = ThreadRegistry()
        done = threading.Event()

        def _quick():
            time.sleep(0.05)
            done.set()

        try:
            t = reg.spawn_and_register(
                "quick",
                _quick,
                stop_event=None,
                join_timeout=2.0,
            )
            done.wait(timeout=2.0)
            t.join(timeout=2.0)
            assert not t.is_alive()
            reg.shutdown_all()
        finally:
            pass


# ─── Helpers for the new test classes ───────────────────────────────────


def _worker_target(stop_event: threading.Event) -> None:
    """A simple worker that loops until *stop_event* is set."""
    while not stop_event.is_set():
        time.sleep(0.01)
