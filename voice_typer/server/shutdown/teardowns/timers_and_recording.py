"""Teardown helper for the timer coordinator + recording watchdog."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_timers_and_recording(controller) -> None:
    """cancel pending timers + drain in-flight timer threads,"""
    app = controller._app
    # Cancel all pending timers.
    try:
        timers_coord = getattr(app, "timers", None)
        in_flight_timers: list = []
        if timers_coord is not None:
            pending_lock = getattr(timers_coord, "_pending_timers_lock", None)
            if pending_lock is not None:
                with pending_lock:
                    in_flight_timers = list(getattr(timers_coord, "_pending_timers", []))
        app._cancel_pending_timers()
        # Drain in-flight timer threads with a short total budget.
        for timer in in_flight_timers:
            try:
                timer.join(timeout=0.5)
            except Exception:
                log.debug("[CLEANUP] in-flight timer join failed", exc_info=True)
    except Exception:
        log.debug("[CLEANUP] _cancel_pending_timers failed", exc_info=True)

    # Stop the persistent watchdog thread.
    try:
        if hasattr(app, "recording") and app.recording is not None:
            app.recording._stop_watchdog_thread()
    except Exception:
        log.debug("[CLEANUP] _stop_watchdog_thread failed", exc_info=True)

    # atomically pop the streaming session instead of the
    try:
        if hasattr(app, "recording") and app.recording is not None:
            session = app.recording.pop_streaming_session()
            if session is not None:
                session._cancel_event.set()
    except Exception:
        log.debug("[CLEANUP] streaming session cancel failed", exc_info=True)


__all__ = ["teardown_timers_and_recording"]
