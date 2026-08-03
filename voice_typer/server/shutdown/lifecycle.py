"""Lifecycle orchestration for shutdown: ``quit`` + shutdown watchdog
(extracted from :mod:`voice_typer.server.shutdown_controller`).

Houses the body of :meth:`ShutdownController.quit` and
:meth:`ShutdownController._arm_shutdown_watchdog` so the controller class
can shrink to a thin facade of delegators. The atexit safety net itself
(``atexit_log`` / ``atexit_cleanup``) lives in
:mod:`voice_typer.server.atexit_safety`; the controller keeps one-line
delegators for back-compat with ``atexit.register(self._atexit_log)``
wiring and tests that call ``controller._atexit_cleanup()`` directly.

Each function takes the owning :class:`ShutdownController` instance as its
``controller`` argument so it can read ``controller._app`` /
``controller._quit_lock`` and call back into ``controller._app._do_cleanup()``
(the delegate on :class:`VoiceTyperApp`) — preserving the existing test-spy
contract (``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercepts
the call from the quit path; see
``tests/test_app_cleanup.py::TestQuitAppUsesSharedCleanup::test_quit_calls_do_cleanup``).

A note on lazy imports (mirrors the convention in
:mod:`voice_typer.server.shutdown.plan`): ``quit`` and
``arm_shutdown_watchdog`` look up ``SHUTDOWN_WATCHDOG_TIMEOUT_S`` and
``join_leaked_workers`` DYNAMICALLY from
:mod:`voice_typer.server.shutdown_controller` (via a function-local
``from ... import``) rather than capturing them at module import time.
This is so tests that
``monkeypatch.setattr("voice_typer.server.shutdown_controller.join_leaked_workers",
fake_join)`` (see ``tests/test_shutdown_parallel_pool_drain.py::
TestWatchdogJoinLeakedWorkers`` and
``tests/test_shutdown_recording_fixes.py::TestIn17WatchdogJoinsLeakedWorkers``)
still intercept the call from the new location. The lazy import also breaks
the would-be circular import (``shutdown_controller`` imports
``shutdown.lifecycle`` at module load time via the delegator bodies).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


def quit(controller: ShutdownController) -> None:  # noqa: A001 — mirrors the method name
    """Shut down the application cleanly.

    ensures all threads, PortAudio streams, and
    subprocesses are properly stopped with timeouts. Previously
    thread joins had no timeout and PortAudio streams could be
    left open if quit() raced with the audio callback.

    the cleanup body has been extracted into
    ``_do_cleanup()`` so ``restart_app()`` and ``_atexit_cleanup()``
    share the SAME audited shutdown path. This eliminates the
    silent data-loss bug where ``restart_app()`` skipped
    ``history_db.flush()``, ``_crash_recovery.flush()``,
    ``recorder.shutdown_mic_watcher()``, ``recorder.stop()``,
    ``_bubble_level_worker`` stop, ``_clear_backend_pid_file()``,
    and the Win32 mutex handle close — losing pending DB writes
    and leaking PortAudio streams + the mutex on every restart.

    THREAD-REGISTRY: ``shutdown_all()`` runs BEFORE the existing
    ``_do_cleanup()`` sequence so the registry's centralized
    signal-and-join runs first. This closes the "leaked daemon"
    gap for the bubble-level-pusher (noted at app.py:1377) and
    gives every registered thread a chance to exit gracefully via
    its stop_event. The per-site shutdown methods in
    ``_do_cleanup()`` then run as a safety net — they're all
    idempotent (Event.set is a no-op if already set; join on a
    dead thread returns immediately), so the redundant calls are
    harmless. ``shutdown_all()`` is itself idempotent, so a
    subsequent call from ``_atexit_cleanup()`` is a no-op.

    the check-then-set on ``_shutting_down`` is now
    serialized by ``_quit_lock``. Multiple shutdown triggers can
    fire concurrently (POSIX signal-watcher thread, Win32 console
    handler thread, IPC ``quit_app`` handler thread, atexit safety
    net). Without the lock, two threads could both read False,
    both set True, and both proceed into ``shutdown_all()``. The
    lock is released BEFORE ``_do_cleanup()`` (which has its own
    ``_cleanup_done`` guard) so a second quit() arriving during
    cleanup short-circuits at the ``_shutting_down`` check rather
    than blocking on _quit_lock.
    """
    # Lazy import so tests that patch
    # ``voice_typer.server.shutdown_controller.SHUTDOWN_WATCHDOG_TIMEOUT_S``
    # (or read it via ``voice_typer.server.shutdown_controller.
    # SHUTDOWN_WATCHDOG_TIMEOUT_S`` — see
    # ``tests/test_shutdown_controller.py::TestShutdownWatchdog::
    # test_watchdog_armed_when_quit_runs_on_non_main_thread``) still
    # see the patched attribute. Mirrors the convention used by
    # :mod:`voice_typer.server.shutdown.plan` for ``_run_with_timeout``.
    from voice_typer.server.shutdown_controller import (
        SHUTDOWN_WATCHDOG_TIMEOUT_S,
    )

    app = controller._app
    # hold _quit_lock around the check-then-set-then-
    # shutdown_all sequence. A second concurrent caller that
    # arrives while we hold the lock will see
    # ``app._shutting_down == True`` once we release it (because
    # we set it inside the critical section) and short-circuit.
    # hold _quit_lock ONLY around the check-then-set
    # on ``_shutting_down``. ``shutdown_all()`` joins every registered
    # daemon thread with its per-thread timeout (N threads x M-second
    # timeouts); holding the lock across it blocked a concurrent
    # ``quit()`` from the POSIX signal-watcher / Win32 console handler
    # / IPC ``quit_app`` handler / atexit net for the entire join
    # window. The ``_shutting_down`` flag (set inside the lock) plus
    # ``shutdown_all()``'s own idempotency make it safe to release
    # the lock BEFORE joining -- a second caller arriving mid-join
    # short-circuits at the ``_shutting_down`` check above rather
    # than blocking on the lock.
    with controller._quit_lock:
        if app._shutting_down:
            log.debug("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        log.info("[SHUTDOWN] Shutting down")
        app._shutting_down = True
        # also set the Event version so executor tasks can check it
        app._shutting_down_event.set()
        # _quit_lock is released here (end of ``with`` block) BEFORE
        # ``shutdown_all()`` and ``_do_cleanup()`` run. Both have
        # their own idempotency guards (``_shutting_down`` /
        # ``_cleanup_done``), so a concurrent quit() that arrives
        # during the join / cleanup will short-circuit at the
        # ``_shutting_down`` check above (now True) rather than
        # block on _quit_lock.

    # THREAD-REGISTRY: signal all registered threads to stop and
    # join them with their per-thread timeouts. Runs BEFORE
    # _do_cleanup() so the registry's centralized shutdown is the
    # first pass; the per-site methods in _do_cleanup() then run
    # as a safety net. Best-effort -- failures here don't prevent
    # the rest of shutdown from running. Runs OUTSIDE _quit_lock
    # (see  note above) so a concurrent quit() doesn't
    # block on the per-thread joins.
    try:
        app._thread_registry.shutdown_all()
    except Exception:
        log.debug(
            "[SHUTDOWN] thread_registry.shutdown_all() failed",
            exc_info=True,
        )

    # delegate to the shared, idempotent cleanup body. The
    # _cleanup_done flag inside _do_cleanup() guarantees that a
    # later _atexit_cleanup() call (or a duplicate quit()) is
    # a no-op rather than double-flushing / double-stopping.
    #
    # NOTE: we call ``app._do_cleanup()`` (the delegate on
    # VoiceTyperApp) rather than ``controller._do_cleanup()`` (the
    # body on this controller) so test spies that
    # ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still
    # intercept the call — see
    # tests/test_app_cleanup.py::test_quit_calls_do_cleanup.
    app._do_cleanup()

    # After ``_do_cleanup()`` completes on a non-main thread,
    # arm a 10s watchdog daemon thread. ``sys.exit(0)`` below only
    # raises ``SystemExit`` in THIS worker thread — process exit
    # relies on the main thread returning from ``tray.run()`` (which
    # ``tray.stop()``, called inside ``_do_cleanup()``, was supposed
    # to break). If the main thread still hasn't returned after 10s
    # (pystray's event loop didn't actually break, or the OS window
    # manager is stuck), the watchdog calls ``os._exit(0)`` as a
    # last resort. ``os._exit(0)`` is safe here because every
    # subsystem has already been torn down by ``_do_cleanup()``.
    # The watchdog is a daemon thread, so it never blocks process
    # exit in the normal case (main thread returns, process exits,
    # daemon thread is killed).
    if not is_main:
        controller._arm_shutdown_watchdog(SHUTDOWN_WATCHDOG_TIMEOUT_S)

    if is_main:
        sys.exit(0)


def arm_shutdown_watchdog(
    controller: ShutdownController,
    timeout_s: float,
) -> None:
    """arm a daemon-thread watchdog that calls
    ``os._exit(0)`` after ``timeout_s`` seconds if the process is
    still alive.

    Used by ``quit()`` (and ``restart_app()`` on the ``VoiceTyperApp``
    side, which mirrors this pattern) when invoked from a non-main
    thread. ``sys.exit(0)`` only raises ``SystemExit`` in the worker
    thread — process exit relies on ``tray.stop()`` breaking the
    pystray loop on the main thread (parked in ``tray.run()``). If
    ``tray.stop()`` succeeded but the main thread still hasn't
    returned from ``tray.run()`` (e.g. pystray's Gtk/Cocoa backend
    didn't actually break the loop, or the OS window manager is
    stuck), the watchdog fires ``os._exit(0)`` as a last resort.

    ``os._exit(0)`` bypasses Python's orderly shutdown (no atexit,
    no daemon-thread joins, no stdio flush). This is safe because
    ``_do_cleanup()`` has already run every subsystem cleanup by
    the time the watchdog is armed.

    the watchdog uses a single ``time.sleep(timeout_s)`` call
    (was a 0.5s polling loop pre-fix). The polling loop's repeated
    ``time.sleep`` calls made it impossible to distinguish a 2s grace
    period from 4×0.5s ticks in tests; the single sleep makes the
    grace period observable. The watchdog is a daemon thread, so it
    never blocks process exit in the normal case (main thread
    returns, process exits, daemon thread is killed). Tests can
    shorten the timeout by patching ``SHUTDOWN_WATCHDOG_TIMEOUT_S``
    or by passing a smaller ``timeout_s`` directly.
    """

    def _watchdog() -> None:
        time.sleep(timeout_s)
        log.warning(
            "[SHUTDOWN] GT-43 watchdog: process still alive %.1fs after "
            "_do_cleanup completed — calling os._exit(0) to unblock the "
            "main thread (parked in tray.run())",
            timeout_s,
        )
        # Best-effort drain of leaked daemon worker threads before
        # ``os._exit(0)``. ``_do_cleanup`` runs several teardowns
        # inside ``_run_with_timeout`` / ``_run_parallel_with_timeout``
        # — when a teardown exceeds its per-helper 10s deadline, the
        # worker thread is leaked as a daemon and registered in
        # ``_timeout_utils._LEAKED_WORKERS``. ``os._exit(0)`` bypasses
        # interpreter shutdown, so those daemon threads are killed
        # mid-flight by the OS — usually benign (teardown is best
        # effort), but if a leaked thread holds a lock on
        # ``history_db._write_lock`` or the ctranslate2 model mutex,
        # the OS-level kill can leave the SQLite WAL half-written
        # or the CUDA context half-torn-down. ``join_leaked_workers``
        # blocks the watchdog for up to 2s total (per-worker 200ms
        # join, capped at 10 workers) so the daemons can finish
        # their critical sections. The 2s drain is well within the
        # watchdog's 30s budget.
        #
        # Lazy import so tests that patch
        # ``voice_typer.server.shutdown_controller.join_leaked_workers``
        # (see ``tests/test_shutdown_parallel_pool_drain.py::
        # TestWatchdogJoinLeakedWorkers`` and
        # ``tests/test_shutdown_recording_fixes.py::
        # TestIn17WatchdogJoinsLeakedWorkers``) still intercept the
        # call from this new location. Mirrors the lazy-import
        # convention in :mod:`voice_typer.server.shutdown.plan`.
        try:
            from voice_typer.server.shutdown_controller import (
                join_leaked_workers,
            )

            join_leaked_workers(timeout=0.5)
        except Exception:
            log.debug(
                "[SHUTDOWN] join_leaked_workers raised — proceeding to os._exit(0)",
                exc_info=True,
            )
        os._exit(0)

    t = threading.Thread(
        target=_watchdog,
        name="shutdown-watchdog",
        daemon=True,
    )
    t.start()


__all__ = [
    "arm_shutdown_watchdog",
    "quit",
]
