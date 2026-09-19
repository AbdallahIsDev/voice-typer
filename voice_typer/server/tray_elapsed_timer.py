"""Elapsed-time ticker for tray/recording UI."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class ElapsedTimer:
    """Elapsed-recording tooltip timer helper."""

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
        self._worker: threading.Thread | None = None
        # The stop event signals the worker to exit its loop. Set
        self._stop_event = threading.Event()
        # Generation counter: each ``start()`` increments it. The
        self._generation: int = 0

    @staticmethod
    def format_elapsed(seconds: float) -> str:
        """Format ``seconds`` as ``mm:ss`` (under 1h) or ``h:mm:ss`` (1h+)."""
        total = max(0, int(seconds))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def start(self) -> None:
        """Start (or restart) the 1-second elapsed-recording tooltip worker."""
        # Cancel + join the prior worker (if any) so we don't leak
        self.cancel()
        # Increment the generation so any in-flight worker from a
        self._generation += 1
        my_gen = self._generation
        # Fresh stop event for the new worker. ``cancel()`` above
        self._stop_event = threading.Event()

        def _worker_loop() -> None:
            """Single-worker loop. Exits on:"""
            while True:
                # ``wait(1.0)`` returns True iff the event was set
                stopped = self._stop_event.wait(timeout=1.0)
                if stopped:
                    return
                if not self._is_active():
                    return
                # Generation guard: a newer start() bumped the
                if self._generation != my_gen:
                    return
                try:
                    self._tick_callback()
                except Exception:
                    # A failing tick callback must NOT kill the
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
        """timeout is defensive against a worker stuck in a slow"""
        # Increment generation so an in-flight worker from the prior
        self._generation += 1
        # Signal the worker to exit at the top of its next loop
        self._stop_event.set()
        w = self._worker
        self._worker = None
        self._set_timer_ref(None)
        if w is not None:
            # defensive against a worker stuck in a slow
            try:
                w.join(timeout=1.5)
            except Exception:
                log.debug("[TRAY] elapsed-timer worker join failed", exc_info=True)
