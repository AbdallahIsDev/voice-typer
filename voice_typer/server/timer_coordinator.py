"""RW-9 god-class decomposition: TimerCoordinator — extracted from VoiceTyperApp.

Owns the lifecycle of fire-and-forget ``threading.Timer`` instances
scheduled by the application:

    - ``_schedule_timer`` — create, track, and start a timer. A
      *generation guard* prevents stale callbacks (scheduled before a
      cancel) from firing after ``_cancel_pending_timers`` has bumped
      the generation counter.
    - ``_cancel_pending_timers`` — cancel and clear all pending timers.
      The pending list is guarded by ``_pending_timers_lock`` (ARCH-022)
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


class TimerCoordinator:
    """Owns creation/tracking/cancellation of scheduled timers.

    RW-9 Phase 6: extracted from ``VoiceTyperApp``. The app passes
    itself (``app``) as a back-reference so ``TimerCoordinator`` can be
    extended later to call back into the app if needed (currently the
    two methods are self-contained and don't use ``self._app`` — but
    the back-reference is kept for parity with ``SettingsController``
    and to support future wiring such as ``app._shutting_down_event``
    gating).

    Threading contract (ARCH-022):

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
        # ARCH-022: _pending_timers is appended to from the tray thread,
        # the transcription thread, and the timer thread itself; the
        # `for timer in self._pending_timers` iteration in
        # _cancel_pending_timers can race with concurrent appends and
        # raise RuntimeError("list changed size during iteration").
        # Guard the list with a dedicated lock.
        self._pending_timers: list[threading.Timer] = []
        self._pending_timers_lock = threading.Lock()
        self._timer_generation: int = 0

    # ── Scheduling / Tracking ──────────────────────────────────────────

    def _schedule_timer(self, delay: float, func) -> threading.Timer:
        """Create, track, and start a timer. Replaces fire-and-forget timers.

        PERF-TMR: Each call creates a fresh threading.Timer. A timer pool
        was considered but rejected because:
          - Only ~3-5 timers are created per dictation cycle
          - threading.Timer creation cost (~0.05 ms) is negligible vs.
            transcription latency (~1-5 seconds)
          - A timer pool would add complexity (reuse tracking, stale timer
            cleanup, thread-safety) for no measurable user-visible gain
          - The generation-guard pattern already prevents stale callbacks

        PERF-26: ``gen = self._timer_generation`` is now captured INSIDE
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
        """
        with self._pending_timers_lock:
            gen = self._timer_generation

            def guarded_func():
                # GT-72: the generation check is a check-then-act TOCTOU.
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
                func()

            timer = threading.Timer(delay, guarded_func)
            # RACE-016: daemon=True is acceptable because timer callbacks
            # are fire-and-forget UI updates; missing one on shutdown is harmless.
            timer.daemon = True
            self._pending_timers.append(timer)
        timer.start()
        return timer

    def _cancel_pending_timers(self):
        """Cancel and clear all pending scheduled timers.

        ARCH-022: take the lock so concurrent appends from the tray /
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
