"""AB-45 regression: ``ThreadRegistry`` reaps dead entries on every
``register()`` call (and exposes a public ``reap_dead()`` method).

Pre-AB-45, ``ThreadRegistry._entries`` held strong references to dead
``Thread`` objects indefinitely.  A dead ``Thread`` holds strong refs
to its target callable + args + kwargs (the closure), which can
transitively keep large objects alive (audio buffers, model state, IPC
sockets).  Under repeated start/stop of threads that don't explicitly
call ``unregister()``, entries accumulated.

Post-AB-45:
- ``reap_dead()`` iterates ``_entries`` and removes entries whose
  ``entry.thread.is_alive()`` is ``False``.
- ``register()`` calls ``reap_dead()`` at the start (cheap O(n) over
  the small dict).
- Re-registering a name whose existing entry has a dead thread is
  SILENT (no warning) — the dead entry was reaped, so the
  existing-entry check doesn't fire.
"""

from __future__ import annotations

import logging
import threading
import time

from voice_typer.server.thread_registry import (
    ThreadRegistry,
    ThreadRegistryEntry,
)


def _make_short_lived_thread(exit_gate: threading.Event) -> threading.Thread:
    """Return a started daemon thread that stays alive until ``exit_gate``
    is set (bounded by a 2s cap), so tests control the exact moment of
    thread death instead of racing a fixed-lifetime sleep."""

    def _run() -> None:
        exit_gate.wait(timeout=2.0)

    t = threading.Thread(target=_run, name="short-lived", daemon=True)
    t.start()
    return t


def _wait_for_exit(t: threading.Thread, timeout: float = 2.0) -> None:
    """Wait for thread ``t`` to exit (no signaling — just join)."""
    t.join(timeout=timeout)
    assert not t.is_alive(), f"thread {t.name!r} did not exit within {timeout}s"


# ─── reap_dead ─────────────────────────────────────────────────────────


class TestReapDead:
    """``reap_dead()`` removes entries whose thread has exited."""

    def test_reap_dead_removes_dead_entries(self):
        """Dead entries are removed; live entries are kept."""
        reg = ThreadRegistry()
        stop = threading.Event()
        live_gate = threading.Event()
        live_t = _make_short_lived_thread(live_gate)

        # Make a thread that stays alive until we signal it.
        def _live_run():
            stop.wait()

        alive_t = threading.Thread(target=_live_run, name="alive", daemon=True)
        alive_t.start()

        try:
            reg.register("short-lived", live_t, stop_event=None, join_timeout=1.0)
            reg.register("alive", alive_t, stop_event=None, join_timeout=1.0)
            # Both entries are present while both threads are alive
            # (reap_dead hasn't been called).
            assert set(reg.list_all()) == {"short-lived", "alive"}
            # Now let the short-lived thread exit and confirm its death.
            live_gate.set()
            _wait_for_exit(live_t)
            # list_active excludes the dead thread.
            assert reg.list_active() == ["alive"]

            removed = reg.reap_dead()
            assert removed == 1
            assert reg.list_all() == ["alive"]
        finally:
            stop.set()
            alive_t.join(timeout=2.0)

    def test_reap_dead_no_op_on_empty_registry(self):
        """``reap_dead()`` on an empty registry returns 0."""
        reg = ThreadRegistry()
        assert reg.reap_dead() == 0
        assert reg.list_all() == []

    def test_reap_dead_no_op_when_all_alive(self):
        """``reap_dead()`` returns 0 when all entries have live threads."""
        reg = ThreadRegistry()
        stop = threading.Event()

        def _run():
            stop.wait()

        t = threading.Thread(target=_run, name="alive", daemon=True)
        t.start()
        try:
            reg.register("alive", t, stop_event=None, join_timeout=1.0)
            removed = reg.reap_dead()
            assert removed == 0
            assert reg.list_all() == ["alive"]
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_reap_dead_returns_count(self):
        """``reap_dead()`` returns the number of entries removed."""
        reg = ThreadRegistry()
        gates = [threading.Event() for _ in range(3)]
        threads = [_make_short_lived_thread(gate) for gate in gates]
        for i, t in enumerate(threads):
            reg.register(f"short-{i}", t, stop_event=None, join_timeout=1.0)
        for gate in gates:
            gate.set()
        for t in threads:
            _wait_for_exit(t)
        removed = reg.reap_dead()
        assert removed == 3
        assert reg.list_all() == []


# ─── register() calls reap_dead() ─────────────────────────────────────


class TestRegisterCallsReapDead:
    """``register()`` calls ``reap_dead()`` at the start so dead entries
    don't accumulate under repeated start/stop."""

    def test_register_reaps_dead_entries(self):
        """A subsequent ``register()`` call reaps dead entries from
        prior registrations."""
        reg = ThreadRegistry()
        t1_gate = threading.Event()
        t1 = _make_short_lived_thread(t1_gate)
        reg.register("worker", t1, stop_event=None, join_timeout=1.0)
        # The entry is present while its thread is alive.
        assert reg.list_all() == ["worker"]
        # Let the thread exit and confirm the dead entry is still in the
        # registry (reap only happens on the next register).
        t1_gate.set()
        _wait_for_exit(t1)
        assert reg.list_active() == []

        # Register a new thread — this should reap the dead entry.
        stop = threading.Event()

        def _run():
            stop.wait()

        t2 = threading.Thread(target=_run, name="worker-v2", daemon=True)
        t2.start()
        try:
            reg.register("worker-v2", t2, stop_event=None, join_timeout=1.0)
            # The dead "worker" entry was reaped; only "worker-v2" remains.
            assert reg.list_all() == ["worker-v2"]
        finally:
            stop.set()
            t2.join(timeout=2.0)

    def test_register_same_name_dead_thread_silent(self, caplog):
        """Re-registering a name whose existing thread is dead is SILENT
        (no warning) — the dead entry was reaped, so the
        existing-entry check doesn't fire."""
        reg = ThreadRegistry()
        t1_gate = threading.Event()
        t1 = _make_short_lived_thread(t1_gate)
        reg.register("worker", t1, stop_event=None, join_timeout=1.0)
        # Let the thread exit so the re-registration below sees a dead entry.
        t1_gate.set()
        _wait_for_exit(t1)
        assert not t1.is_alive()

        # Re-register "worker" with a NEW thread.  The old thread is
        # dead, so the reap at the start of register() removes the
        # dead entry; the existing-entry check should NOT fire a
        # warning.
        stop = threading.Event()

        def _run():
            stop.wait()

        t2 = threading.Thread(target=_run, name="worker-v2", daemon=True)
        t2.start()
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
                reg.register("worker", t2, stop_event=stop, join_timeout=1.0)
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "Re-registering" in r.message]
            assert warnings == [], (
                "AB-45: re-registering a name whose existing thread is "
                "dead MUST be silent (no warning).  Got: " + repr([r.message for r in warnings])
            )
        finally:
            stop.set()
            t2.join(timeout=2.0)

    def test_register_same_name_live_thread_warns(self, caplog):
        """Re-registering a name whose existing thread is STILL ALIVE
        with a different thread object logs a warning (potential leak
        — caller should have stopped the old thread)."""
        reg = ThreadRegistry()
        stop1 = threading.Event()
        stop2 = threading.Event()

        def _run(stop):
            stop.wait()

        t1 = threading.Thread(target=_run, args=(stop1,), name="worker-1", daemon=True)
        t2 = threading.Thread(target=_run, args=(stop2,), name="worker-2", daemon=True)
        t1.start()
        t2.start()
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.thread_registry"):
                reg.register("worker", t1, stop_event=stop1, join_timeout=1.0)
                reg.register("worker", t2, stop_event=stop2, join_timeout=1.0)
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "Re-registering" in r.message]
            assert len(warnings) == 1, (
                "AB-45: re-registering a name with a DIFFERENT, STILL-ALIVE "
                "thread MUST log a warning.  Got: " + repr([r.message for r in warnings])
            )
        finally:
            stop1.set()
            stop2.set()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)


# ─── thread safety ────────────────────────────────────────────────────


class TestReapDeadThreadSafety:
    """``reap_dead()`` is safe to call concurrently with ``register()``."""

    def test_concurrent_reap_and_register(self):
        """Concurrent ``reap_dead()`` and ``register()`` don't corrupt
        the registry's internal state."""
        reg = ThreadRegistry()
        errors: list[Exception] = []

        def register_loop():
            try:
                for i in range(20):
                    stop = threading.Event()
                    exit_gate = threading.Event()
                    t = _make_short_lived_thread(exit_gate)
                    reg.register(f"worker-{i}", t, stop_event=stop, join_timeout=0.05)
                    # Release the gate so the thread dies on its own
                    # (the registry never stops it) — reap_dead() then
                    # has dead entries to reap concurrently.
                    exit_gate.set()
            except Exception as e:
                errors.append(e)

        def reap_loop():
            try:
                for _ in range(20):
                    reg.reap_dead()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register_loop),
            threading.Thread(target=reap_loop),
            threading.Thread(target=reap_loop),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert errors == [], f"Concurrent reap/register raised: {errors}"
        # Final reap to clean up.
        reg.reap_dead()
        # All remaining entries should have live threads (or be empty).
        for _name in reg.list_all():
            entries: list[ThreadRegistryEntry] = []
            with reg._lock:
                entries = list(reg._entries.values())
            for entry in entries:
                # Either alive (still running) or reaped.  No torn state.
                assert entry.thread is not None
