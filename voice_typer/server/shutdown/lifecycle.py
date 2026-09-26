"""Shutdown lifecycle sequencing."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


def quit(controller: ShutdownController) -> None:  # noqa: A001, mirrors the method name
    """Shut down the application cleanly."""
    # Lazy import so tests that patch
    from voice_typer.server.shutdown_controller import (
        SHUTDOWN_WATCHDOG_TIMEOUT_S,
    )

    app = controller._app
    # hold _quit_lock around the check-then-set-then-
    with controller._quit_lock:
        if app._shutting_down:
            log.debug("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        # DEBUG: the caller's "[QUIT] Quitting <app>" line already
        log.debug("[SHUTDOWN] Shutting down")
        app._shutting_down = True
        # also set the Event version so executor tasks can check it

    # NOTIFY-HOST: publish ``quit_app`` so the predecessor frontend
    if not getattr(app, "_quit_app_published", False):
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "quit_app"})
        except Exception:
            log.debug("[SHUTDOWN] quit_app event publish failed", exc_info=True)

    # THREAD-REGISTRY: signal all registered threads to stop and
    try:
        app._thread_registry.shutdown_all()
    except Exception:
        log.debug(
            "[SHUTDOWN] thread_registry.shutdown_all() failed",
            exc_info=True,
        )

    # delegate to the shared, idempotent cleanup body. The
    app._do_cleanup()

    # After ``_do_cleanup()`` completes on a non-main thread,
    if not is_main:
        controller._arm_shutdown_watchdog(SHUTDOWN_WATCHDOG_TIMEOUT_S)

    if is_main:
        sys.exit(0)


def arm_shutdown_watchdog(
    controller: ShutdownController,
    timeout_s: float,
) -> None:
    """Used by ``quit()`` (and ``restart_app()`` on the ``LausuApp``"""

    cancel_event = threading.Event()

    def _watchdog() -> None:
        # Grace-period wait. ``time.sleep`` (not ``cancel_event.wait``)
        _grace_deadline = time.monotonic() + timeout_s
        time.sleep(timeout_s)
        # If the sleep was patched to a no-op (test suites patch the
        _grace_remaining = _grace_deadline - time.monotonic()
        if _grace_remaining > 0 and cancel_event.wait(_grace_remaining):
            return
        # If the test-suite drain disarmed us while we slept, return
        if cancel_event.is_set():
            return
        log.warning(
            "[SHUTDOWN] watchdog: process still alive %.1fs after "
            "_do_cleanup completed, calling os._exit(0) to unblock the "
            "main thread (parked in tray.run())",
            timeout_s,
        )
        # Best-effort drain of leaked daemon worker threads before
        try:
            from voice_typer.server.shutdown_controller import (
                join_leaked_workers,
            )

            join_leaked_workers(total_budget=1.0)
        except Exception:
            log.debug(
                "[SHUTDOWN] join_leaked_workers raised, proceeding to os._exit(0)",
                exc_info=True,
            )
        # Best-effort clear of the backend PID file BEFORE
        try:
            # Resolve through the owning module object at call time so
            from voice_typer.server import backend_pid as _backend_pid_module

            _backend_pid_module._clear_backend_pid_file()
        except Exception:
            log.warning(
                "[SHUTDOWN] could not clear backend PID file before os._exit",
                exc_info=True,
            )
        os._exit(0)

    t = threading.Thread(
        target=_watchdog,
        name="shutdown-watchdog",
        daemon=True,
    )
    # Cancellation handle for the test-suite drain (see
    _WATCHDOG_CANCEL_EVENTS[t] = cancel_event
    _LIVE_SHUTDOWN_WATCHDOG_THREADS.add(t)
    t.start()


def _drain_shutdown_watchdogs() -> None:
    """Cancel and join any live shutdown-watchdog threads."""
    for t in list(_LIVE_SHUTDOWN_WATCHDOG_THREADS):
        cancel = _WATCHDOG_CANCEL_EVENTS.get(t)
        if cancel is not None:
            cancel.set()
        # Best-effort join; a leaked watchdog may still be in its
        t.join(timeout=0.5)
        # Drop from the registries so a later drain doesn't re-join and
        _LIVE_SHUTDOWN_WATCHDOG_THREADS.discard(t)
        _WATCHDOG_CANCEL_EVENTS.pop(t, None)


# Module-level registry of live shutdown-watchdog threads so the test
_LIVE_SHUTDOWN_WATCHDOG_THREADS: weakref.WeakSet = weakref.WeakSet()
# Parallel WeakKeyDictionary mapping each live watchdog thread to its
_WATCHDOG_CANCEL_EVENTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


__all__ = [
    "_drain_shutdown_watchdogs",
    "_LIVE_SHUTDOWN_WATCHDOG_THREADS",
    "arm_shutdown_watchdog",
    "quit",
]
