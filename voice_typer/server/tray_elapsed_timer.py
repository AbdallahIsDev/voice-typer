"""Elapsed-recording tooltip timer extracted from ``tray.py``.

A single daemon worker thread per recording session
that loops on ``Event.wait(1.0)`` and invokes ``tick_callback`` on each
tick — replacing the prior self-rescheduling ``threading.Timer`` chain
(one Timer per second of recording, ~1 800 Timer allocations over a
30-minute dictation). The worker exits cleanly on cancel() or when
``is_active()`` returns False, and a rapid ``start()`` cancels and
joins the prior worker (no thread leak on RECORDING→RECORDING
transitions).

The owner (``TrayIcon``) keeps ``_elapsed_timer`` in sync via the
``set_timer_ref`` callback. Existing tests that read
``tray._elapsed_timer is None`` continue to work — the callback is
invoked with the worker thread on start and with ``None`` on cancel,
mirroring the prior ``threading.Timer`` reference contract (the value
is now a ``threading.Thread`` instead of a ``threading.Timer``, but
the ``is None`` / ``is not None`` checks are identical).

``tray.py`` re-exports ``ElapsedTimer`` and keeps the original
``TrayIcon._format_elapsed`` / ``_start_elapsed_timer`` /
``_cancel_elapsed_timer`` methods as thin delegators for backward
compatibility with tests that monkeypatch them directly
(``tests/test_er_fix_h.py``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class ElapsedTimer:
    """Elapsed-recording tooltip timer helper.

    Manages a single daemon worker thread (``tray-elapsed-timer``) that
    loops on ``Event.wait(1.0)`` and invokes ``tick_callback`` on each
    tick so the owner can refresh its tooltip with the current ``mm:ss``
    elapsed time. The worker exits cleanly when ``is_active()`` returns
    False OR when ``cancel()`` sets the stop event.

    The owner's ``_elapsed_timer`` attribute is synced via the
    ``set_timer_ref`` callback — called with the worker thread on
    ``start()`` and with ``None`` on ``cancel()`` — so existing tests
    that check ``tray._elapsed_timer is None`` continue to work.

    Args:
        tick_callback: Called on each 1s tick (e.g. to refresh the
            tray tooltip via ``_apply_state`` + ``_publish_tray_state``).
        is_active: Returns True if the worker should keep ticking
            (e.g. ``lambda: self._state == AppState.RECORDING``).
        set_timer_ref: Called with the new worker thread (or ``None``)
            on every start/cancel so the owner's ``_elapsed_timer``
            attribute stays in sync with the helper's internal
            reference.
    """

    def __init__(
        self,
        tick_callback: Callable[[], None],
        is_active: Callable[[], bool],
        set_timer_ref: Callable[[threading.Thread | None], None],
    ) -> None:
        self._tick_callback = tick_callback
        self._is_active = is_active
        self._set_timer_ref = set_timer_ref
        # The helper owns the canonical worker reference; the owner's
        # ``_elapsed_timer`` attribute is kept in sync via
        # ``_set_timer_ref`` so tests that read ``tray._elapsed_timer``
        # see the same object.
        self._worker: threading.Thread | None = None
        # The stop event signals the worker to exit its loop. Set
        # by ``cancel()`` (or implicitly by ``start()`` cancelling
        # the prior worker). Cleared at the top of each ``start()``
        # so a fresh worker starts with the event unset.
        self._stop_event = threading.Event()
        # Generation counter: each ``start()`` increments it. The
        # worker's loop captures the value at entry and checks it
        # on every tick — if a new ``start()`` has bumped the
        # counter, the old worker exits. Belt-and-suspenders
        # alongside the stop event (the event catches explicit
        # ``cancel()``; the generation catches a rapid
        # start()-without-cancel race where the stop event
        # was set+cleared before the old worker noticed).
        self._generation: int = 0

    @staticmethod
    def format_elapsed(seconds: float) -> str:
        """Format ``seconds`` as ``mm:ss`` (under 1h) or ``h:mm:ss`` (1h+).

        Negative inputs are clamped to 0. Used to append the elapsed
        recording time to the tray tooltip.
        """
        total = max(0, int(seconds))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def start(self) -> None:
        """Start (or restart) the 1-second elapsed-recording tooltip worker.

        Cancels and joins any prior worker first so rapid RECORDING →
        RECORDING transitions (e.g. from a stop/restart race) don't
        leak overlapping worker threads.

        Single-worker design: each ``start()`` creates EXACTLY
        ONE worker thread (not a chain of ``threading.Timer``
        objects). The worker loops on ``Event.wait(1.0)`` and exits
        when ``is_active()`` returns False, ``cancel()`` sets the
        stop event, OR a newer ``start()`` increments the
        generation counter.
        """
        # Cancel + join the prior worker (if any) so we don't leak
        # threads on rapid RECORDING → RECORDING transitions. The
        # prior worker's stop event is set so it exits the loop on
        # its next ``Event.wait(1.0)`` return; ``join()`` blocks
        # until the worker has actually exited (typically <1s).
        self.cancel()
        # Increment the generation so any in-flight worker from a
        # prior start() knows it has been superseded and exits the
        # loop on its next iteration. The new worker captures the
        # new generation value in ``my_gen`` below and checks
        # ``self._generation == my_gen`` on every tick.
        self._generation += 1
        my_gen = self._generation
        # Fresh stop event for the new worker. ``cancel()`` above
        # set the prior event but the prior worker is now joined
        # (or was never started) — a new event is cheap and avoids
        # any "stale-set-event" surprise if a future refactor makes
        # ``cancel()`` non-blocking.
        self._stop_event = threading.Event()

        def _worker_loop() -> None:
            """Single-worker loop. Exits on:
            1. ``is_active()`` returning False (the owner has
               transitioned away from RECORDING).
            2. ``_stop_event`` being set (``cancel()`` was called).
            3. Generation mismatch (a newer ``start()`` superseded
               this worker).
            """
            while True:
                # ``wait(1.0)`` returns True iff the event was set
                # before the timeout (i.e. cancel() was called). It
                # returns False on timeout (i.e. 1s elapsed, tick
                # the owner). Either way we re-check is_active()
                # and the generation BEFORE invoking the callback
                # so a stop/restart that happened during the
                # wait is observed on the next tick (not half a
                # tick late).
                stopped = self._stop_event.wait(timeout=1.0)
                if stopped:
                    return
                if not self._is_active():
                    return
                # Generation guard: a newer start() bumped the
                # counter — this worker is stale, exit without
                # invoking the callback.
                if self._generation != my_gen:
                    return
                try:
                    self._tick_callback()
                except Exception:
                    # A failing tick callback must NOT kill the
                    # worker (otherwise all subsequent ticks are
                    # lost until the next start()). Log at debug
                    # and continue — the next iteration will try
                    # again.
                    log.debug(
                        "[TRAY] elapsed-timer tick failed to refresh tooltip",
                        exc_info=True,
                    )

        t = threading.Thread(
            target=_worker_loop,
            name="tray-elapsed-timer",
            daemon=True,
        )
        self._worker = t
        self._set_timer_ref(t)
        t.start()

    def cancel(self) -> None:
        """Cancel the elapsed-recording worker if running.

        Idempotent — safe to call when no worker exists (e.g. before
        the first RECORDING transition). Sets the stop event so the
        worker exits its loop on the next ``Event.wait(1.0)`` return,
        then joins the worker (with a 1.5s timeout — the worker
        should exit within at most one 1s tick + a few µs; the
        timeout is defensive against a worker stuck in a slow
        ``tick_callback``).

        Also increments the generation counter so any in-flight
        worker that hasn't yet noticed the stop event exits
        immediately on its next ``is_active()`` / generation check.
        """
        # Increment generation so an in-flight worker from the prior
        # start() exits on its next check (BEFORE the stop event
        # takes effect, in case the worker is currently inside
        # ``tick_callback`` rather than ``Event.wait``).
        self._generation += 1
        # Signal the worker to exit at the top of its next loop
        # iteration (within 1s + however long the current
        # ``tick_callback`` takes, since ``Event.wait`` is the only
        # blocking call).
        self._stop_event.set()
        w = self._worker
        self._worker = None
        self._set_timer_ref(None)
        if w is not None:
            # ``join(1.5)`` — the worker should exit within at most
            # one 1s tick + a few µs. The 1.5s upper bound is
            # defensive against a worker stuck in a slow
            # ``tick_callback`` (which we can't interrupt from
            # another thread without a Cancel-like primitive). We
            # deliberately do NOT raise on join timeout — the
            # worker's daemon=True ensures it won't block process
            # exit, and the next start() cancels it again anyway.
            try:
                w.join(timeout=1.5)
            except Exception:
                log.debug("[TRAY] elapsed-timer worker join failed", exc_info=True)
