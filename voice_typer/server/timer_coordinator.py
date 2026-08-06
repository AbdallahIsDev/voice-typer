"""god-class decomposition: TimerCoordinator — extracted from VoiceTyperApp.

Owns the lifecycle of fire-and-forget ``threading.Timer`` instances
scheduled by the application:

    - ``_schedule_timer`` — create, track, and start a timer. A
      *generation guard* prevents stale callbacks (scheduled before a
      cancel) from firing after ``_cancel_pending_timers`` has bumped
      the generation counter.
    - ``_cancel_pending_timers`` — cancel and clear all pending timers.
The pending list is guarded by ``_pending_timers_lock`` ()
      so concurrent appends from the tray / transcription / timer
      threads can't race with the snapshot-and-clear iteration.

The actual logic lived on ``VoiceTyperApp`` as two private methods of
the same name. The behaviour is preserved verbatim — only the class
boundary moved. ``VoiceTyperApp`` keeps thin delegate methods so all
existing callers (and tests that monkeypatch
``app._schedule_timer`` / ``app._cancel_pending_timers``) keep working
unchanged.

State migrated from ``VoiceTyperApp.__init__``:

    - ``self._pending_timers: list[threading.Timer]``
    - ``self._pending_timers_lock = threading.Lock()``
    - ``self._timer_generation: int = 0``

These now live on ``TimerCoordinator.__init__``. The primary agent
will remove the corresponding lines from ``VoiceTyperApp.__init__``
when wiring the delegate.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported only under TYPE_CHECKING to avoid a circular import at
    # runtime — ``voice_typer.server.app`` imports
    # ``voice_typer.server.timer_coordinator`` (this module).
    pass

log = logging.getLogger(__name__)


class _ZeroDelayThread(threading.Thread):
    """A daemon ``Thread`` that quacks like a ``Timer`` for ``delay == 0``.

    XV-134: ``threading.Timer(0, func)`` still allocates the internal
    ``threading.Event`` (used to signal "timer finished"), the
    cancel-bookkeeping machinery, and the ``Timer`` sub-object itself —
    all wasted when the callback runs immediately. A bare ``Thread`` is
    cheaper and the generation guard inside ``guarded_func`` (RACE-013)
    is preserved unchanged.

    Provides a no-op ``cancel()`` so callers (and
    ``_cancel_pending_timers`` if a future change re-adds zero-delay
    timers to the pending list) can polymorphically call ``.cancel()``
    without ``AttributeError``. A started thread can't actually be
    cancelled — the no-op mirrors what ``Timer.cancel()`` would return
    for an already-fired timer (``False``), without the bookkeeping.
    """

    def cancel(self) -> None:
        """No-op: a started thread cannot be cancelled.

        The generation guard inside ``guarded_func`` is the real
        stale-callback suppression mechanism; ``cancel()`` here is
        purely defensive so callers that polymorphically invoke
        ``.cancel()`` on the returned object don't raise.
        """
        return None


class TimerCoordinator:
    """Owns creation/tracking/cancellation of scheduled timers.

    Phase 6: extracted from ``VoiceTyperApp``. The app passes
        itself (``app``) as a back-reference so ``TimerCoordinator`` can be
        extended later to call back into the app if needed (currently the
        two methods are self-contained and don't use ``self._app`` — but
        the back-reference is kept for parity with ``SettingsController``
        and to support future wiring such as ``app._shutting_down_event``
        gating).

    Threading contract ():

        ``_pending_timers`` is appended to from the tray thread, the
        transcription thread, and the timer thread itself; the
        ``for timer in self._pending_timers`` iteration in
        ``_cancel_pending_timers`` can race with concurrent appends and
        raise ``RuntimeError("list changed size during iteration")``.
        The list is therefore guarded by ``_pending_timers_lock``.

        Generation guard (stale-callback prevention):

        Every scheduled timer captures the *current* value of
        ``_timer_generation`` at scheduling time. ``_cancel_pending_timers``
        *increments* the counter (under the lock). When a timer fires, the
        guarded callback compares its captured generation against the
        current one and skips the user callback if they differ — i.e. the
        timer was scheduled before the most recent cancel and is now stale.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        # _pending_timers is appended to from the tray thread,
        # the transcription thread, and the timer thread itself; the
        # `for timer in self._pending_timers` iteration in
        # _cancel_pending_timers can race with concurrent appends and
        # raise RuntimeError("list changed size during iteration").
        # Guard the list with a dedicated lock.
        self._pending_timers: list[threading.Timer] = []
        self._pending_timers_lock = threading.Lock()
        self._timer_generation: int = 0

    # ── Scheduling / Tracking ──────────────────────────────────────────

    def _schedule_timer(self, delay: float, func) -> threading.Thread:
        """Create, track, and start a timer. Replaces fire-and-forget timers.

        XV-134 fast path: for ``delay <= 0`` (6 callers in
        ``recording_controller`` / ``model_manager`` pass ``0``),
        short-circuit to a bare daemon ``_ZeroDelayThread`` instead of
        ``threading.Timer(0, ...)``. ``Timer(0)`` still allocates the
        internal ``threading.Event`` (signals "timer finished"), the
        cancel-bookkeeping machinery, and the ``Timer`` sub-object —
        all wasted when the callback runs immediately. A bare
        ``Thread`` is cheaper. The ``guarded_func`` / generation-check
        logic (RACE-013) is preserved unchanged — the generation
        capture, the unlocked check, the locked re-check, the
        ``_shutting_down_event`` consultation, and the eviction from
        ``_pending_timers`` all run identically. We do NOT append the
        zero-delay thread to ``_pending_timers`` because a started
        thread cannot be cancelled — ``guarded_func``'s
        ``if timer in self._pending_timers: remove(timer)`` becomes a
        no-op (the thread was never in the list), so the list doesn't
        accumulate stale shells (the original PERF-TMR concern).

        PERF-TMR: Each call creates a fresh threading.Timer. A timer pool
        was considered but rejected because:
          - Only ~3-5 timers are created per dictation cycle
          - threading.Timer creation cost (~0.05 ms) is negligible vs.
            transcription latency (~1-5 seconds)
          - A timer pool would add complexity (reuse tracking, stale timer
            cleanup, thread-safety) for no measurable user-visible gain
          - The generation-guard pattern already prevents stale callbacks

        ``gen = self._timer_generation`` is now captured INSIDE
        the ``_pending_timers_lock`` critical section. Previously the
        read happened outside the lock, so a concurrent
        ``_cancel_pending_timers`` could bump the generation between
        our read and our ``append`` — a stale timer would capture the
        OLD generation, then fire after the cancel and incorrectly
        run ``func`` (because ``gen == self._timer_generation`` would
        still be True at fire time if no further cancel happened).
        Reading under the lock pairs the capture with the append so
        the timer either:
          (a) is in the pending list with the current generation
              (will be cancelled by a subsequent cancel), OR
          (b) is in the pending list with the current generation and
              no cancel happens before it fires (legitimate run).
        The previous race let a timer escape cancellation entirely.

        when a timer fires (the guarded callback runs),
        ``guarded_func`` removes it from ``_pending_timers`` under
        the lock. Previously fired timers stayed in the list forever
        — a long-running app that schedules ~5 timers per dictation
        cycle accumulated ~4,000 stale ``threading.Timer`` shells in
        ``_pending_timers``, which ``_cancel_pending_timers`` would
        then iterate (calling ``timer.cancel()`` on already-fired
        timers — a no-op but still O(N) work) on every shutdown.
        """
        with self._pending_timers_lock:
            gen = self._timer_generation

            def guarded_func():
                # the generation check is a check-then-act TOCTOU.
                # ``threading.Timer.cancel()`` only prevents a timer that
                # hasn't fired yet. If this ``guarded_func`` has already
                # been invoked by the Timer thread (and passed the
                # unlocked gen check below) when
                # ``_cancel_pending_timers`` bumps the generation, the
                # running callback would still proceed to call
                # ``func()`` — which touches app state (tray, recorder,
                # IPC server) that ``_do_cleanup`` is concurrently
                # tearing down. We close the window with a second
                # generation check performed UNDER the lock (pairs with
                # the bump in ``_cancel_pending_timers``), and ALSO
                # consult ``app._shutting_down_event`` so a callback
                # that races against the very start of shutdown (before
                # ``_cancel_pending_timers`` has run but after the
                # shutdown event has been set) is still suppressed.
                if gen != self._timer_generation:
                    return  # stale: scheduled before a cancel
                app = self._app
                shutting_down_event = getattr(app, "_shutting_down_event", None) if app is not None else None
                if shutting_down_event is not None and shutting_down_event.is_set():
                    log.debug("[TIMER] suppressed scheduled callback: app._shutting_down_event is set")
                    return
                # Re-check the generation under the lock so a concurrent
                # ``_cancel_pending_timers`` cannot bump-and-clear
                # between the unlocked check above and the ``func()``
                # call below. The lock is released immediately (we do
                # NOT hold it during ``func()``) so slow callbacks don't
                # block other threads from scheduling.
                with self._pending_timers_lock:
                    if gen != self._timer_generation:
                        return
                    # evict this timer from ``_pending_timers``
                    # BEFORE invoking ``func()`` so the list doesn't
                    # accumulate fired-timer shells. Lock is held only
                    # for the mutation, released before ``func()`` so a
                    # slow callback doesn't block other threads. ``timer``
                    # is captured via closure on the enclosing
                    # ``_schedule_timer`` call (one ``guarded_func`` per
                    # timer). For the XV-134 zero-delay fast path,
                    # ``timer`` is NOT in ``_pending_timers`` so this
                    # ``in`` check is False — no-op, no harm.
                    if isinstance(timer, threading.Timer) and timer in self._pending_timers:
                        self._pending_timers.remove(timer)
                func()

            # XV-134: zero/near-zero delay → bare daemon Thread instead
            # of ``Timer(0, ...)``. ``Timer(0)`` still pays for the
            # internal ``threading.Event`` and cancel-bookkeeping — all
            # wasted when the callback runs immediately. We do NOT
            # append to ``_pending_timers``: a started thread can't be
            # cancelled, so tracking it there would only accumulate
            # stale shells (the exact PERF-TMR pathology the eviction
            # in ``guarded_func`` was added to prevent). The generation
            # guard inside ``guarded_func`` (RACE-013) still suppresses
            # stale callbacks if a cancel lands while the callback is
            # mid-flight.
            if delay <= 0:
                timer = _ZeroDelayThread(target=guarded_func, daemon=True)
            else:
                timer = threading.Timer(delay, guarded_func)
                # RACE-016: daemon=True is acceptable because timer callbacks
                # are fire-and-forget UI updates; missing one on shutdown is harmless.
                timer.daemon = True
                self._pending_timers.append(timer)
        timer.start()
        return timer

    def _cancel_pending_timers(self):
        """Cancel and clear all pending scheduled timers.

        Take the lock so concurrent appends from the tray
        transcription / timer threads can't race with our iteration.
        The actual ``timer.cancel()`` calls happen outside the lock to
        avoid holding it longer than necessary.
        """
        with self._pending_timers_lock:
            timers = list(self._pending_timers)
            self._pending_timers.clear()
            self._timer_generation += 1
        for timer in timers:
            try:
                timer.cancel()
            except Exception:
                log.exception("[APP] Failed to cancel scheduled timer")
