"""Teardown helper for the timer coordinator + recording watchdog.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_timers_and_recording`. The body is
unchanged; only the class boundary moved. See the module docstring of
:mod:`voice_typer.server.shutdown.teardowns` for the convention.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_timers_and_recording(controller) -> None:
    """cancel pending timers + drain in-flight timer threads,
    stop the recording watchdog, and atomically pop the streaming
    session.

    Groups three concerns that all touch the RecordingController /
    TimerCoordinator surface and were previously sequential blocks
    at the top of ``_do_cleanup``.
    """
    app = controller._app
    # Cancel all pending timers.
    # ``_cancel_pending_timers`` (on TimerCoordinator) bumps
    # ``_timer_generation`` and calls ``Timer.cancel()`` on every
    # pending timer — but ``Timer.cancel()`` only prevents a timer
    # that hasn't fired yet. A timer whose ``guarded_func`` has
    # already been invoked by the Timer thread (passed the
    # ``gen == self._timer_generation`` check) but hasn't yet called
    # ``func()`` will STILL run ``func()`` after the generation bump,
    # racing the subsystem teardown below. The fix HERE is to give
    # those in-flight ``func()`` invocations a short bounded window
    # to complete before we start tearing down the subsystems they
    # touch.
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
        # Per-timer timeout of 0.5s × N timers — for the typical
        # 3-5 pending timers, total drain is ≤2.5s, well within the
        # 10s shared deadline.
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
    # two-step ``get_streaming_session()`` + ``set_streaming_session(None)``
    # pair. The two-step had a TOCTOU race where a concurrent
    # ``_start_streaming_session_if_enabled`` could install a NEW
    # session that the subsequent ``set_streaming_session(None)``
    # would clobber. ``pop_streaming_session()`` is atomic under the
    # recording controller's lock. If a non-None session is popped,
    # set its ``_cancel_event`` so the daemon streaming transcription
    # thread observes the cancel signal.
    try:
        if hasattr(app, "recording") and app.recording is not None:
            session = app.recording.pop_streaming_session()
            if session is not None:
                session._cancel_event.set()
    except Exception:
        log.debug("[CLEANUP] streaming session cancel failed", exc_info=True)


__all__ = ["teardown_timers_and_recording"]
