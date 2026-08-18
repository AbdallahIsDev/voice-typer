"""DJ-37: ``ElapsedTimer`` uses a single worker thread per recording
session (was a self-rescheduling ``threading.Timer`` chain — one Timer
per second of recording).

Context: pre-DJ-37, ``ElapsedTimer.start()`` scheduled a
``threading.Timer(1.0, _tick)`` whose callback re-scheduled a fresh
``Timer`` on each tick. Over a 30-minute dictation that's ~1 800 Timer
objects created + cancelled, each going through
``threading.Thread.__init__`` + ``_start_new_thread`` + the C-level
timer heap. DJ-37 replaces the chain with ONE daemon worker thread
that loops on ``Event.wait(1.0)`` for the entire session — one futex
syscall per tick, no per-tick thread allocation.

These tests pin the contract:

1. A single recording session uses exactly ONE worker thread (the
   ``set_timer_ref`` callback is called once with the worker + once
   with None on cancel — NOT once per tick).
2. The worker's name is ``tray-elapsed-timer`` (so it's identifiable
   in thread dumps / py-spy).
3. ``cancel()`` joins the worker so it's no longer alive afterwards.
4. ``start()`` called twice (rapid RECORDING → RECORDING transition)
   cancels the prior worker — no leak.
5. No ``threading.Timer`` is constructed during the session (the
   implementation must not regress to the self-rescheduling chain).
"""

from __future__ import annotations

import threading
import time

from voice_typer.server.tray_elapsed_timer import ElapsedTimer


class TestElapsedTimerSingleThread:
    """DJ-37: only one worker thread is spawned per recording session."""

    def test_single_worker_thread_per_session(self):
        """A single recording session uses ONE worker thread, not one
        Timer per tick (the pre-DJ-37 behavior)."""
        ticks: list[float] = []
        active = threading.Event()
        active.set()  # is_active() returns True throughout

        refs: list[threading.Thread | None] = []

        timer = ElapsedTimer(
            tick_callback=lambda: ticks.append(time.time()),
            is_active=active.is_set,
            set_timer_ref=refs.append,
        )
        timer.start()
        try:
            # Wait for at least 3 ticks (3 seconds).
            deadline = time.monotonic() + 5.0
            while len(ticks) < 3 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(ticks) >= 3, (
                f"Expected at least 3 ticks in 5s, got {len(ticks)} — worker thread may not be ticking"
            )

            # During the session, set_timer_ref is called exactly ONCE
            # with a non-None value (the worker). Pre- it was
            # called once per tick (with a fresh Timer each time).
            non_none_refs = [r for r in refs if r is not None]
            assert len(non_none_refs) == 1, (
                f"Expected exactly 1 set_timer_ref(worker) call during the "
                f"session, got {len(non_none_refs)}: {non_none_refs}"
            )
            worker = non_none_refs[0]
            assert isinstance(worker, threading.Thread)
            assert worker.name == "tray-elapsed-timer", (
                f"Worker should be named 'tray-elapsed-timer' for debuggability, got {worker.name!r}"
            )
        finally:
            timer.cancel()

        # After cancel, set_timer_ref is called with None (so the
        # owner's _elapsed_timer attribute is cleared).
        assert refs[-1] is None, f"Expected final set_timer_ref(None) call after cancel, got {refs[-1]!r}"

    def test_cancel_joins_worker(self):
        """``cancel()`` joins the worker thread so it's no longer alive
        afterwards — the next ``start()`` can't race with a dying worker."""
        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        worker = timer._worker
        assert worker is not None, "Worker should be set after start()"
        assert worker.is_alive(), "Worker should be alive after start()"

        timer.cancel()

        assert not worker.is_alive(), "Worker should be joined (not alive) after cancel()"
        assert timer._worker is None, "Internal _worker reference should be cleared after cancel()"

    def test_restart_cancels_prior_worker(self):
        """``start()`` called twice cancels the prior worker — no leak
        (rapid RECORDING → RECORDING transitions don't accumulate threads)."""
        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        first_worker = timer._worker
        assert first_worker is not None and first_worker.is_alive()

        timer.start()  # should cancel the first worker internally
        second_worker = timer._worker
        assert second_worker is not None
        assert second_worker is not first_worker, "Restart should create a NEW worker thread, not reuse the prior one"
        # The first worker should be dead (cancel()-joined by start()).
        assert not first_worker.is_alive(), "Prior worker should be joined (dead) after restart — no leak"

        timer.cancel()
        assert not second_worker.is_alive()

    def test_no_threading_timer_constructed(self, monkeypatch):
        """No ``threading.Timer`` instances are created during the
        session — the implementation must not regress to the
        self-rescheduling Timer chain (DJ-37)."""
        # Wrap threading.Timer to track construction.
        real_timer = threading.Timer
        timer_constructions: list[tuple] = []

        class _TrackingTimer(real_timer):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                timer_constructions.append(args)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(threading, "Timer", _TrackingTimer)

        active = threading.Event()
        active.set()
        ticks: list[int] = []
        timer = ElapsedTimer(
            tick_callback=lambda: ticks.append(1),
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        try:
            deadline = time.monotonic() + 3.5
            while len(ticks) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(ticks) >= 2, f"Expected ≥2 ticks, got {len(ticks)}"
        finally:
            timer.cancel()

        assert timer_constructions == [], (
            f"Expected ZERO threading.Timer constructions during the session "
            f"(DJ-37 single-worker design), got {timer_constructions}"
        )

    def test_is_active_false_exits_worker(self):
        """When ``is_active()`` returns False mid-session (e.g. state
        changed away from RECORDING), the worker exits cleanly — no
        need for an explicit ``cancel()``."""
        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        worker = timer._worker
        assert worker is not None and worker.is_alive()

        # Flip is_active to False — the worker's next-tick check exits.
        active.clear()
        # Wait up to 2s for the worker to notice + exit on its own.
        deadline = time.monotonic() + 2.0
        while worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not worker.is_alive(), "Worker should exit on its own when is_active() returns False"
        # cancel() is still idempotent + safe.
        timer.cancel()
