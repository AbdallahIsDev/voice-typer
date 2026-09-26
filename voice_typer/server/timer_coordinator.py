"""Timer coordination for elapsed UI."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported only under TYPE_CHECKING to avoid a circular import at
    pass

log = logging.getLogger(__name__)


class _ZeroDelayThread(threading.Thread):
    """A daemon ``Thread`` that quacks like a ``Timer`` for ``delay == 0``.

    ``threading.Timer(0, func)`` still allocates the internal
    ``threading.Event`` (used to signal "timer finished"), the
    cancel-bookkeeping machinery, and the ``Timer`` sub-object itself —
    all wasted when the callback runs immediately. A bare ``Thread`` is
    cheaper and the generation guard inside ``guarded_func`` (RACE-013)
    is preserved unchanged.

    Provides a no-op ``cancel()`` so callers (and
    ``_cancel_pending_timers`` if a future change re-adds zero-delay
    timers to the pending list) can polymorphically call ``.cancel()``
    without ``AttributeError``. A started thread can't actually be
    cancelled, the no-op mirrors what ``Timer.cancel()`` would return
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

    Extracted from ``LausuApp``. The app passes
        itself (``app``) as a back-reference so ``TimerCoordinator`` can be
        extended later to call back into the app if needed (currently the
        two methods are self-contained and don't use ``self._app``, but
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
        current one and skips the user callback if they differ, i.e. the
        timer was scheduled before the most recent cancel and is now stale.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        # _pending_timers is appended to from the tray thread,
        self._pending_timers: list[threading.Timer] = []
        self._pending_timers_lock = threading.Lock()
        self._timer_generation: int = 0

    def _schedule_timer(self, delay: float, func) -> threading.Thread:
        """Create, track, and start a timer. Replaces fire-and-forget timers.

         Fast path: for ``delay <= 0`` (6 callers in
         ``recording_controller`` / ``model_manager`` pass ``0``),
         short-circuit to a bare daemon ``_ZeroDelayThread`` instead of
         ``threading.Timer(0, ...)``. ``Timer(0)`` still allocates the
         internal ``threading.Event`` (signals "timer finished"), the
         cancel-bookkeeping machinery, and the ``Timer`` sub-object —
         all wasted when the callback runs immediately. A bare
         ``Thread`` is cheaper. The ``guarded_func`` / generation-check
         logic (RACE-013) is preserved unchanged, the generation
         capture, the unlocked check, the locked re-check, the
         ``_shutting_down_event`` consultation, and the eviction from
         ``_pending_timers`` all run identically. We do NOT append the
         zero-delay thread to ``_pending_timers`` because a started
         thread cannot be cancelled, ``guarded_func``'s
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
         our read and our ``append``: a stale timer would capture the
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
        , a long-running app that schedules ~5 timers per dictation
         cycle accumulated ~4,000 stale ``threading.Timer`` shells in
         ``_pending_timers``, which ``_cancel_pending_timers`` would
         then iterate (calling ``timer.cancel()`` on already-fired
         timers, a no-op but still O(N) work) on every shutdown.
        """
        with self._pending_timers_lock:
            gen = self._timer_generation

            def guarded_func():
                # the generation check is a check-then-act TOCTOU.
                if gen != self._timer_generation:
                    return  # stale: scheduled before a cancel
                app = self._app
                shutting_down_event = getattr(app, "_shutting_down_event", None) if app is not None else None
                if shutting_down_event is not None and shutting_down_event.is_set():
                    log.debug("[TIMER] suppressed scheduled callback: app._shutting_down_event is set")
                    return
                # Re-check the generation under the lock so a concurrent
                with self._pending_timers_lock:
                    if gen != self._timer_generation:
                        return
                    # evict this timer from ``_pending_timers``
                    if isinstance(timer, threading.Timer) and timer in self._pending_timers:
                        self._pending_timers.remove(timer)
                func()

            # guard inside ``guarded_func`` (RACE-013) still suppresses
            if delay <= 0:
                timer = _ZeroDelayThread(target=guarded_func, daemon=True)
            else:
                timer = threading.Timer(delay, guarded_func)
                # RACE-016: daemon=True is acceptable because timer callbacks
                timer.daemon = True
                self._pending_timers.append(timer)
        timer.start()
        return timer

    def _cancel_pending_timers(self):
        """Cancel and clear all pending scheduled timers."""
        with self._pending_timers_lock:
            timers = list(self._pending_timers)
            self._pending_timers.clear()
            self._timer_generation += 1
        for timer in timers:
            try:
                timer.cancel()
            except Exception:
                log.exception("[APP] Failed to cancel scheduled timer")
