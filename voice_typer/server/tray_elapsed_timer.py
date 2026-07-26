"""Elapsed-recording tooltip timer extracted from ``tray.py``.

UX-11 (FIX-10): a daemon ``threading.Timer`` that ticks every 1 second
and invokes a ``tick_callback`` so the owner (``TrayIcon``) can refresh
its tooltip with the current ``mm:ss`` (or ``h:mm:ss``) elapsed
recording time. The timer reschedules itself on each tick as long as
the owner's ``is_active()`` callback returns True.

Previously this lived inline as three methods on ``TrayIcon``
(``_format_elapsed``, ``_start_elapsed_timer``, ``_cancel_elapsed_timer``
— ~70 LOC). Extracted here as a small ``ElapsedTimer`` helper class so
``tray.py`` shrinks toward the project's Rule-19/20 entry-file size
target and so the timer logic is unit-testable without instantiating a
``TrayIcon``.

``tray.py`` re-exports ``ElapsedTimer`` and keeps the original
``TrayIcon._format_elapsed`` / ``_start_elapsed_timer`` /
``_cancel_elapsed_timer`` methods as thin delegators for backward
compatibility with tests that monkeypatch them directly
(``tests/test_er_fix_h.py``) and with tests that assert on
``tray._elapsed_timer is None`` (the raw ``threading.Timer`` reference
is kept in sync via the ``set_timer_ref`` callback).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class ElapsedTimer:
    """UX-11 (FIX-10): elapsed-recording tooltip timer helper.

    Manages a daemon ``threading.Timer`` that ticks every 1 second and
    invokes ``tick_callback`` so the owner can refresh its tooltip with
    the current ``mm:ss`` elapsed time. The timer reschedules itself on
    each tick as long as ``is_active()`` returns True.

    The owner's ``_elapsed_timer`` attribute (the raw
    ``threading.Timer`` or ``None``) is synced via the
    ``set_timer_ref`` callback so existing tests that check
    ``tray._elapsed_timer is None`` continue to work.

    Args:
        tick_callback: Called on each 1s tick (e.g. to refresh the
            tray tooltip via ``_apply_state`` + ``_publish_tray_state``).
        is_active: Returns True if the timer should continue
            rescheduling (e.g. ``lambda: self._state == AppState.RECORDING``).
        set_timer_ref: Called with the new ``threading.Timer`` (or
            ``None``) on every start/cancel/reschedule so the owner's
            ``_elapsed_timer`` attribute stays in sync with the
            helper's internal reference.
    """

    def __init__(
        self,
        tick_callback: Callable[[], None],
        is_active: Callable[[], bool],
        set_timer_ref: Callable[[threading.Timer | None], None],
    ) -> None:
        self._tick_callback = tick_callback
        self._is_active = is_active
        self._set_timer_ref = set_timer_ref
        # The helper owns the canonical Timer reference; the owner's
        # ``_elapsed_timer`` attribute is kept in sync via
        # ``_set_timer_ref`` so tests that read ``tray._elapsed_timer``
        # see the same object.
        self._timer: threading.Timer | None = None

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
        """Start (or restart) the 1-second elapsed-recording tooltip timer.

        Cancels any prior timer first so rapid RECORDING → RECORDING
        transitions (e.g. from a stop/restart race) don't leak
        overlapping timers.
        """
        self.cancel()

        def _tick() -> None:
            # Re-check state inside the tick: a stop() may have fired
            # between the timer being scheduled and now. If we're no
            # longer recording, just exit without rescheduling.
            if not self._is_active():
                return
            try:
                self._tick_callback()
            except Exception:
                log.debug(
                    "[TRAY] elapsed-timer tick failed to refresh tooltip",
                    exc_info=True,
                )
            # Reschedule only if still active. The check happens AFTER
            # the tick callback so a state change during the tick is
            # caught.
            if self._is_active():
                t = threading.Timer(1.0, _tick)
                t.daemon = True
                self._timer = t
                self._set_timer_ref(t)
                t.start()

        t = threading.Timer(1.0, _tick)
        t.daemon = True
        self._timer = t
        self._set_timer_ref(t)
        t.start()

    def cancel(self) -> None:
        """Cancel the elapsed-recording timer if running.

        Idempotent — safe to call when no timer exists (e.g. before the
        first RECORDING transition). Clears the internal Timer reference
        and the owner's ``_elapsed_timer`` attribute (via
        ``set_timer_ref``) to ``None`` so ``set_state`` assertions on
        ``_elapsed_timer is None`` work.
        """
        t = self._timer
        self._timer = None
        self._set_timer_ref(None)
        if t is not None:
            try:
                t.cancel()
            except Exception:
                log.debug("[TRAY] elapsed-timer cancel failed", exc_info=True)
