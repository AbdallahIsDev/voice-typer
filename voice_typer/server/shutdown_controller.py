"""RW-9 god-class decomposition: ShutdownController — extracted from VoiceTyperApp.

Owns the entire shutdown / cleanup lifecycle of ``VoiceTyperApp``:

    - ``_do_cleanup`` — the shared, idempotent cleanup body invoked by
      ``quit()``, ``restart_app()``, and ``_atexit_cleanup()``. 30+
      try/except blocks release every subsystem (recorder, hotkeys,
      history DB, crash recovery, bubble level worker, Win32 mutex,
      Electron subprocess, devnull FDs, etc.).
    - ``quit`` — sets ``_shutting_down``, calls
      ``thread_registry.shutdown_all()``, delegates to ``_do_cleanup``,
      then ``sys.exit(0)`` (only when called from the main thread).
    - ``_atexit_log`` / ``_atexit_cleanup`` — atexit safety net that
      runs ``_do_cleanup`` if the process is killed externally without
      ``quit()`` / ``restart_app()`` having run.
    - ``_install_signal_handlers`` — POSIX SIGINT/SIGTERM handlers that
      trigger ``quit()`` on a separate thread.
    - ``_install_win32_console_handler`` / ``_win32_console_handler`` —
      Windows console control handler that keeps the tray app alive
      when the console window closes, and triggers ``quit()`` on
      Ctrl+C / logoff / shutdown.

Previously all of this lived on ``VoiceTyperApp`` as ~480 LOC across 7
methods. The behaviour is preserved verbatim — only the class boundary
moved. ``VoiceTyperApp`` keeps thin delegate methods (``app.quit()``,
``app._do_cleanup()``, ``app._atexit_cleanup()``, etc.) for back-compat
with callers (``app.start()`` registers the atexit handlers, tray menu
callbacks invoke ``quit_app`` which calls ``quit``, tests call
``app._do_cleanup()`` directly) and — crucially — so test spies that
``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercept the
cleanup call from ``quit`` / ``restart_app`` / ``_atexit_cleanup``.

A note on monkeypatching (mirrors the convention in
``settings_controller.py`` and ``startup_tasks.py``): tests like the
``app`` fixture in ``tests/test_app.py`` and the regression tests in
``tests/test_app_cleanup.py`` patch
``voice_typer.server.app._clear_backend_pid_file`` (and
``voice_typer.server.app.is_windows`` in other suites) at call time.
To keep those patches effective, the helpers are looked up DYNAMICALLY
from the ``voice_typer.server.app`` module inside each method rather
than being captured at import time.

A note on the delegate indirection for ``_do_cleanup``: ``quit`` and
``_atexit_cleanup`` deliberately call ``self._app._do_cleanup()``
(the delegate on ``VoiceTyperApp``) rather than ``self._do_cleanup()``
(the body on ``ShutdownController`` itself). This is so test spies
that ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercept
the call — see ``tests/test_app_cleanup.py::TestQuitAppUsesSharedCleanup::
test_quit_calls_do_cleanup`` and
``TestAtexitCleanupSafetyNet::test_atexit_cleanup_never_raises``.
``restart_app`` (which stays on ``VoiceTyperApp``) also calls
``self._do_cleanup()`` (the delegate) for the same reason.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    # ``shutdown_controller`` indirectly via the ``ShutdownController(self)``
    # call inside ``VoiceTyperApp.__init__``).  At runtime, ``app`` is
    # whatever object was passed to ``__init__`` (always a
    # ``VoiceTyperApp`` in production, but tests pass mocks that satisfy
    # the same duck-typed surface).
    from voice_typer.server.app import VoiceTyperApp

from voice_typer.server._timeout_utils import (
    SHUTDOWN_WATCHDOG_TIMEOUT_S,
    TIMEOUT,
    _run_parallel_with_timeout,
    _run_with_timeout,
)
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


# DR-28: the general-purpose thread-join timeout utilities
# (``_run_with_timeout``, ``_run_parallel_with_timeout``, the
# ``TIMEOUT`` sentinel, and ``SHUTDOWN_WATCHDOG_TIMEOUT_S``) now live in
# :mod:`voice_typer.server._timeout_utils`. They're re-exported here
# (via the ``from ... import`` above) so existing callers — in
# particular tests like ``tests/test_shutdown_controller_de.py`` that
# do ``from voice_typer.server.shutdown_controller import _run_with_timeout,
# _TIMEOUT, SHUTDOWN_WATCHDOG_TIMEOUT_S`` — continue to work unchanged.


class ShutdownController:
    """Owns the shutdown / cleanup lifecycle of ``VoiceTyperApp``.

    RW-9 Phase 7: extracted from ``VoiceTyperApp``. The app passes itself
    (``app``) so ``ShutdownController`` can:

    - Read/write ``app._shutting_down`` / ``app._shutting_down_event``
      (quit's idempotency guard).
    - Read/write ``app._cleanup_done`` (the hard idempotency flag inside
      ``_do_cleanup`` — once True, subsequent calls are no-ops).
    - Call ``app._thread_registry.shutdown_all()`` (centralized
      signal-and-join of all registered daemon threads).
    - Call ``app._cancel_pending_timers()`` (TimerCoordinator concern,
      kept on ``VoiceTyperApp``).
    - Call ``app._restore_volume(fade_ms=0)`` (VolumeController concern,
      kept on ``VoiceTyperApp``).
    - Shut down every subsystem: ``app.recording`` (watchdog thread +
      streaming session cancel), ``app.recorder`` (PortAudio stream +
      mic watcher), ``app.hotkeys`` (all three backends),
      ``app._crash_recovery`` (flush + shutdown),
      ``app.history_db`` (flush + close), ``app._bubble_level_worker_*``
      (daemon thread + queue sentinel), ``app.tray`` (pystray loop
      break), ``app._electron_pid`` (subprocess terminate),
      ``app._mutex_handle`` (Win32 named mutex CloseHandle).
    - Read platform helpers (``is_windows``) dynamically from
      ``voice_typer.server.app`` so tests that monkeypatch
      ``voice_typer.server.app.is_windows`` still take effect.
    - Read ``_clear_backend_pid_file`` / ``_close_devnull_files`` /
      ``_register_devnull_file`` dynamically from
      ``voice_typer.server.app`` so tests that monkeypatch those
      re-exports still take effect.
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app
        # MED-PPP / XCUT-4: POSIX signal handlers must be
        # async-signal-safe — i.e. they may only call a small set of
        # reentrant functions (``write``, ``_exit``, ``sigaction``-style
        # flag setters, ``sem_post``, etc.). The previous handler did
        # ``log.info(...)`` (acquires the logging lock),
        # ``signal.Signals(signum).name`` (does a dict lookup; mostly
        # safe but unnecessary), and ``threading.Thread(...).start()``
        # (acquires the import lock + allocates). If a signal arrived
        # while the main thread held the logging lock, the handler
        # deadlocked.
        #
        # The fix: the signal handler now ONLY calls
        # ``Event.set()`` (async-signal-safe in CPython — it's a thin
        # wrapper around ``PyThread_acquire_lock`` with a non-blocking
        # flag and never blocks). A watcher thread (started lazily in
        # ``_install_signal_handlers``) polls the event and performs
        # the unsafe work (logging + ``threading.Thread(target=quit)``)
        # outside the signal context.
        self._shutdown_signal_event: threading.Event = threading.Event()
        self._shutdown_signum: int | None = None
        self._signal_watcher_started = False
        # CR-51: dedicated lock for the check-then-set-then-shutdown_all
        # sequence in ``quit()``. Multiple shutdown triggers can fire
        # concurrently (POSIX signal-watcher, Win32 console handler,
        # IPC ``quit_app`` handler, atexit safety net). Without this
        # lock, two threads can both read ``app._shutting_down == False``,
        # both set it to True, and both proceed into
        # ``thread_registry.shutdown_all()`` — a duplicate pass that
        # races per-thread ``join_timeout`` accounting.
        #
        # This is a SEPARATE lock from the three app-level locks
        # (``_lock``, ``_config_mutation_lock``, ``_pending_timers_lock``)
        # governed by docs/architecture/lock-order-contract.md. It is
        # NEVER nested with any of those — ``quit()`` does not acquire
        # any of them. The lock is released BEFORE ``_do_cleanup()``
        # (which has its own ``_cleanup_done`` guard) so a second quit()
        # arriving during cleanup short-circuits at the
        # ``_shutting_down`` check rather than blocking on _quit_lock.
        self._quit_lock: threading.Lock = threading.Lock()

        # DE-53: dedicated lock for the ``_electron_pid`` read-terminate-clear
        # sequence inside ``_teardown_electron``. Two concurrent ``quit()``
        # callers (IPC + signal-watcher) could both read the same PID, both
        # call ``terminate_electron(pid)`` (racing with PID recycling on
        # Windows), and both clear the attribute — potentially clobbering a
        # NEW PID installed by a concurrent ``restart_app()``. This lock
        # serializes the read-terminate-clear critical section; the second
        # caller observes ``_electron_pid is None`` (cleared by the first)
        # and skips. Defense-in-depth: ``_do_cleanup``'s ``_cleanup_done``
        # guard already prevents double-entry, but this lock protects the
        # electron path specifically even if a future caller bypasses
        # ``_quit_lock``.
        self._electron_pid_lock: threading.Lock = threading.Lock()

        # DE-54 / GT-70: shared state between ``_teardown_recorder`` and
        # ``_teardown_sounddevice``. When ``recorder.stop()`` (or
        # ``discard()``) times out, the leaked worker thread is still
        # accessing the PortAudio stream; a subsequent ``sd.stop()`` call
        # can deadlock on PortAudio backends (notably WASAPI) where the
        # stream lock is held. ``_teardown_recorder`` sets
        # ``_recorder_force_closed = True`` and signals
        # ``_recorder_teardown_done``; ``_teardown_sounddevice`` waits for
        # the event then checks the flag, skipping ``sd.stop()`` when set.
        # Both helpers run in the XV-7 parallel batch, so the Event is the
        # synchronization primitive that gives ``_teardown_sounddevice`` a
        # happens-before guarantee on the flag read.
        self._recorder_teardown_done: threading.Event = threading.Event()
        self._recorder_force_closed: bool = False

    # ─── Shared cleanup body ───────────────────────────────────────────

    def _do_cleanup(self) -> None:
        """RW-3: shared cleanup body used by ``quit()``, ``restart_app()``,
        and ``_atexit_cleanup()``.

        Performs ALL the cleanup that ``quit()`` previously did inline,
        EXCEPT the final ``sys.exit(0)``.  Every operation is guarded by
        a None-check or try-except so the method is IDEMPOTENT — calling
        it twice (e.g. once from ``quit()`` and once from the atexit
        safety net) is a no-op on the second call.

        The caller is responsible for setting ``self._shutting_down = True``
        and ``self._shutting_down_event.set()`` BEFORE calling this
        method so the atexit safety net doesn't double-cleanup. The
        ``_cleanup_done`` flag below is the hard guarantee: once set,
        every subsequent call returns immediately.

        Prior to RW-3, ``restart_app()`` did only a PARTIAL cleanup
        (cancel timers, stop hotkey backends, stop tray) and skipped:
          - ``history_db.flush()`` — pending transcription history
            writes were silently lost
          - ``_crash_recovery.flush()`` / ``shutdown()`` — pending
            recovery writes were lost
          - ``recorder.shutdown_mic_watcher()`` — mic watcher daemon
            thread leaked
          - ``recorder.stop()`` / ``discard()`` — PortAudio stream
            not closed
          - ``_bubble_level_worker`` stop — daemon thread leaked
          - ``_clear_backend_pid_file()`` — stale PID file remained
          - Win32 mutex handle close

        The ``_atexit_cleanup`` safety net's ``_shutting_down`` guard
        meant it was completely DISABLED when ``restart_app()`` set
        ``_shutting_down = True``, so the safety net couldn't pick up
        the slack. Extracting the shared body here fixes both bugs.

        G4-H-30: ``_do_cleanup`` ALSO drains / cancels the WS dispatch
        pool BEFORE tearing down the recorder / history DB / crash
        recovery writer. The pool is stored on the IPC server instance
        as ``_ws_dispatch_pool`` (created lazily by
        ``sidecar_ws._make_dispatch``). ``pool.shutdown(wait=False,
        cancel_futures=True)`` immediately cancels queued (not-yet-
        started) dispatch tasks and signals in-flight tasks to exit
        (they observe the cancel via their own cooperative-shutdown
        checks). Without this drain, a long-running handler (e.g.
        ``download_model``) races teardown and can half-flush the
        history DB or leak a partial crash-recovery snapshot.
        """
        app = self._app
        # PVT-G5-026: guard the check-then-set on ``_cleanup_done`` with
        # ``_quit_lock``. Previously the check-then-set was not atomic —
        # two callers (signal-watcher thread + atexit) could both read
        # False, both set True, and both execute the cleanup body
        # concurrently. ``_quit_lock`` is released by ``quit()`` BEFORE
        # delegating to ``_do_cleanup()`` (see the comment in
        # ``quit()``), so acquiring it here does NOT deadlock with
        # ``quit()``. ``_atexit_cleanup()`` does not hold the lock when
        # it calls ``_do_cleanup()`` either. The lock is non-reentrant
        # by design; if a future caller invokes ``_do_cleanup()`` while
        # already holding ``_quit_lock``, that would deadlock — but
        # the only two callers (``quit()`` and ``_atexit_cleanup()``)
        # both release the lock first.
        with self._quit_lock:
            if getattr(app, "_cleanup_done", False):
                return
            app._cleanup_done = True

        # XV-7 / DE-54: reset the shared state between ``_teardown_recorder``
        # and ``_teardown_sounddevice`` for THIS cleanup pass. Both helpers
        # run in the parallel batch below; ``_teardown_sounddevice`` waits
        # on ``_recorder_teardown_done`` before reading
        # ``_recorder_force_closed`` so the flag has a happens-before
        # guarantee even under concurrent scheduling.
        self._recorder_teardown_done.clear()
        self._recorder_force_closed = False

        # ── Early bookend (sequential) ────────────────────────────────
        # PVT-G5-004 (partial): stop the IPC server EARLY so inbound
        # requests can't resurrect torn-down subsystems. FA2 is adding
        # a ``_shutting_down`` guard inside ``IPCServer._dispatch()``
        # to reject handlers mid-shutdown; this stop() call closes the
        # listening socket + worker pool so no NEW connections are
        # accepted either. Best-effort — failures here don't prevent
        # the rest of cleanup from running.
        try:
            ipc_server = getattr(app, "_ipc_server", None)
            if ipc_server is not None:
                _run_with_timeout(
                    "ipc_server.stop",
                    ipc_server.stop,
                    timeout=5.0,
                )
        except Exception:
            log.debug("[CLEANUP] ipc_server.stop() failed", exc_info=True)

        # G4-H-30: drain / cancel in-flight WS dispatch requests BEFORE
        # any subsystem teardown. ``_shutting_down`` is already True
        # (set by ``quit()`` before calling this method), so the
        # ``sidecar_ws._make_dispatch`` ``dispatch`` coroutine is
        # already rejecting NEW requests with
        # ``{"code": "server.shutting_down"}``. This call cancels the
        # in-flight requests that were accepted BEFORE the flag flipped
        # — they're the ones that race teardown. ``cancel_futures=True``
        # cancels queued-but-not-started tasks immediately; in-flight
        # tasks get a ``concurrent.futures.CancelledError`` on the next
        # ``await`` checkpoint.
        try:
            ipc_server = getattr(app, "_ipc_server", None)
            ws_pool = getattr(ipc_server, "_ws_dispatch_pool", None)
            if ws_pool is not None and hasattr(ws_pool, "shutdown"):
                ws_pool.shutdown(wait=False, cancel_futures=True)
                log.debug("[SHUTDOWN] WS dispatch pool shut down (cancel_futures=True)")
                # ``shutdown(wait=False, cancel_futures=True)`` only cancels
                # QUEUED (not-yet-started) dispatch tasks; RUNNING handlers
                # continue. Without a bounded join, teardown of the
                # recorder / history_db / crash_recovery subsystems (below)
                # races any in-flight WS handler that touches them, risking
                # a half-flushed history DB or a partial crash-recovery
                # snapshot. Spawn a daemon-thread ``shutdown(wait=True)`` and
                # join the spawner with a 5s hard deadline — generous for
                # any single IPC handler, short enough to bound teardown.
                # If the drain doesn't complete in 5s, log + proceed (the
                # alternative — blocking _do_cleanup indefinitely — would
                # hang the entire shutdown path on one stuck handler).
                #
                # Note: since Python 3.9, ``ThreadPoolExecutor`` worker
                # threads are non-daemon, so a handler stuck >5s continues
                # running on its worker and may delay process exit via the
                # ``concurrent.futures.thread`` atexit join. The 5s bound
                # only unblocks ``_do_cleanup`` — it does NOT bound the
                # atexit join.
                join_thread = threading.Thread(target=ws_pool.shutdown, kwargs={"wait": True}, daemon=True)
                join_thread.start()
                join_thread.join(timeout=5.0)
                if join_thread.is_alive():
                    log.warning("[SHUTDOWN] ws_dispatch_pool did not drain in 5s — proceeding anyway")

                # DJ-9: explicit ``threading.Event`` coordination between
                # the WS dispatch path and ``_do_cleanup``. The pool's
                # ``shutdown(wait=True)`` only guarantees that the
                # ``ThreadPoolExecutor`` has drained its worker queue — it
                # does NOT guarantee that the per-dispatch coroutine body
                # has finished its DB write (the Future resolves on
                # ``server._dispatch`` return, but the WS ``dispatch``
                # coroutine may still be in its ``await loop.run_in_executor``
                # unwind / result-serialisation tail when the pool reports
                # drained). That tail can race ``_teardown_history_db`` /
                # ``_teardown_crash_recovery`` below, silently losing the
                # user's final transcription_final DB write.
                #
                # ``sidecar_ws._make_dispatch`` clears ``_ws_drained_event``
                # on entry to each dispatch and sets it when the in-flight
                # count drops to zero (after the dispatch body fully
                # returns — including the post-Future unwind). We wait on
                # that Event here, bounded by 2s, BEFORE allowing the
                # parallel teardown batch to proceed. The 2s budget is
                # separate from the 5s pool-shutdown join above; the total
                # worst-case WS-drain budget is 7s, well within the per-
                # subsystem 10s parallel-batch deadline.
                #
                # If the Event wait times out, we log and proceed (the
                # in-flight handler is on its own — at best it finishes
                # after the DB is closed and silently fails, at worst the
                # OS force-kill reaps it; either way _do_cleanup must not
                # block indefinitely on a single stuck handler).
                ws_drained_event = getattr(ipc_server, "_ws_drained_event", None)
                if ws_drained_event is not None:
                    drained = ws_drained_event.wait(timeout=2.0)
                    if not drained:
                        in_flight = getattr(ipc_server, "_ws_inflight_count", 0)
                        log.warning(
                            "[SHUTDOWN] DJ-9: WS dispatch drain Event did not "
                            "fire in 2s — %s in-flight handler(s) may race DB "
                            "teardown; proceeding with cleanup (the in-flight "
                            "write may silently fail)",
                            in_flight,
                        )
        except Exception:
            log.debug("[SHUTDOWN] WS dispatch pool shutdown failed", exc_info=True)

        # ── Parallel batch (XV-7): 14 independent teardown helpers ───
        # Each helper is isolated — a failure in one does NOT propagate
        # (``_run_parallel_with_timeout`` captures per-call exceptions).
        # Shared 10s deadline: each helper is wrapped in
        # ``_run_with_timeout(..., timeout=10.0)`` by
        # ``_run_parallel_with_timeout``; if a helper exceeds 10s, the
        # worker thread is leaked as a daemon and the orchestrator moves
        # on. The bookends (early WS drain above + late ``tray.stop``
        # below) remain sequential.
        #
        # Ordering within the batch (matters for DE-10 flush-before-
        # teardown ordering tests): ``_teardown_crash_recovery`` and
        # ``_teardown_history_db`` are placed first AND call ``flush()``
        # directly (no inner ``_run_with_timeout`` for the flush call),
        # so their flush side-effects fire immediately when their worker
        # threads start. The other helpers either wrap their main call
        # in ``_run_with_timeout`` (adds thread-creation latency) or are
        # placed later in the list (max_workers=8 → positions 8-13
        # start only after 6 of the first 8 finish). This gives the
        # flushes a deterministic head start over hotkeys / level_monitor
        # / event_bus teardown, satisfying DE-10's "flushes run BEFORE
        # the hotkey / level_monitor / event_bus teardown" guarantee
        # without sacrificing concurrency.
        parallel_items: list[tuple[str, object, float]] = [
            ("teardown_asr_models", self._teardown_asr_models, 10.0),
            ("teardown_crash_recovery", self._teardown_crash_recovery, 10.0),
            ("teardown_history_db", self._teardown_history_db, 10.0),
            ("teardown_timers_and_recording", self._teardown_timers_and_recording, 10.0),
            ("teardown_recorder", self._teardown_recorder, 10.0),
            ("teardown_restore_volume", self._teardown_restore_volume, 10.0),
            ("teardown_waveform_wiring", self._teardown_waveform_wiring, 10.0),
            ("teardown_sounddevice", self._teardown_sounddevice, 10.0),
            ("teardown_pid_file", self._teardown_pid_file, 10.0),
            ("teardown_mutex_handle", self._teardown_mutex_handle, 10.0),
            ("teardown_devnull_files", self._teardown_devnull_files, 10.0),
            ("teardown_level_monitor", self._teardown_level_monitor, 10.0),
            ("teardown_hotkeys", self._teardown_hotkeys, 10.0),
            ("teardown_electron", self._teardown_electron, 10.0),
            ("teardown_event_bus", self._teardown_event_bus, 10.0),
        ]
        for _desc, _result in _run_parallel_with_timeout(parallel_items):
            if isinstance(_result, BaseException):
                log.debug("[SHUTDOWN] %s raised: %r", _desc, _result)
            elif _result is TIMEOUT:
                log.debug("[SHUTDOWN] %s timed out", _desc)

        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # ── Late bookend (sequential) ────────────────────────────────
        # PVT-G5-003: ``tray.stop()`` MUST be the LAST step in
        # ``_do_cleanup()``. Previously it was step 13 of 19, which
        # broke the pystray loop on the main thread (blocked in
        # ``tray.run()`` via ``ipc_server.main()``) before the
        # remaining cleanups could finish. Moving ``tray.stop()`` to
        # the end ensures the main thread stays alive (blocked in
        # ``tray.run()``) until every other cleanup has completed.
        # Idempotent — wrapped in try-except so a second call after
        # the tray is already stopped doesn't propagate.
        # PVT-G5-057: 5s timeout.
        #
        # GT-43 / XV-10: if ``tray.stop()`` times out AND we're on a
        # non-main thread, call ``os._exit(0)`` immediately. The main
        # thread is parked in pystray's ``tray.run()`` event loop and
        # relies on ``tray.stop()`` breaking that loop to return. If
        # ``tray.stop()`` hangs, the main thread never returns and the
        # process is unkillable via the normal path — ``sys.exit(0)`` in
        # ``quit()`` only raises ``SystemExit`` in THIS worker thread.
        # ``os._exit(0)`` bypasses Python's orderly shutdown but is safe
        # here because every other subsystem has already been torn down
        # by the cleanup steps above. On the main thread, we just log
        # and continue — ``quit()``'s ``sys.exit(0)`` will handle exit.
        #
        # DE-11: when ``tray.stop()`` RAISES (not times out), the
        # failure is logged at ERROR (was DEBUG pre-fix) so operators
        # can see why the main thread stayed parked in ``tray.run()``.
        try:
            _tray_stop_result = _run_with_timeout(
                "tray.stop",
                app.tray.stop,
                timeout=5.0,
            )
            if _tray_stop_result is TIMEOUT and (threading.current_thread() is not threading.main_thread()):
                log.warning(
                    "[SHUTDOWN] GT-43: tray.stop() timed out on non-main thread "
                    "— calling os._exit(0) to unblock the main thread parked in "
                    "tray.run() (all subsystem cleanup already completed)"
                )
                os._exit(0)
        except Exception:
            log.error("[CLEANUP] tray.stop() failed", exc_info=True)

    def _do_fast_cleanup(self) -> None:
        """XZ-R17-06: critical-only cleanup for Windows logoff/shutdown.

        Windows CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT give the process
        ~5 seconds before the OS forcibly terminates it. The full
        :meth:`_do_cleanup` body has a cumulative worst-case of ~85s.
        This fast path runs ONLY critical-resource cleanup with 1s
        timeouts each, targeting <3s total.

        Critical path: crash_recovery.flush, history_db.flush,
        recorder.stop, _clear_backend_pid_file, mutex CloseHandle/release.
        Non-critical steps (tray.stop, Electron terminate, hotkey stop,
        level_monitor, waveform worker, event_bus, devnull) are SKIPPED.

        Idempotent with :meth:`_do_cleanup` via the shared ``_cleanup_done``
        guard. DR-28: the actual ctrl_logoff/shutdown routing lives in
        :func:`voice_typer.server.signal_handlers.win32_console_handler`;
        the cross-file change to route logoff/shutdown to this method
        instead of ``controller.quit()`` is tracked under XZ-R17-06.

        UE-1: this method ends with ``os._exit(0)`` — bypassing atexit
        handlers is correct here because (a) the OS is force-killing us
        within ~5s, so orderly atexit cleanup would race the OS deadline
        and lose, and (b) the critical cleanup above has already run (or
        a prior call already ran it via the ``_cleanup_done`` guard). The
        ``os._exit(0)`` MUST fire even when ``_cleanup_done`` was already
        True on entry — the Win32 console-control callback must NOT
        return ``True`` to the OS without exiting, otherwise the OS will
        re-evaluate us with a CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT
        escalation. Tests that invoke this method directly MUST monkey-
        patch ``os._exit`` (see ``tests/test_shutdown_xz_r17_fixes.py``'s
        autouse ``_stub_os_exit`` fixture).
        """
        app = self._app
        with self._quit_lock:
            already_done = bool(getattr(app, "_cleanup_done", False))
            if not already_done:
                app._cleanup_done = True

        if not already_done:
            log.warning(
                "[SHUTDOWN] XZ-R17-06: fast cleanup path (Windows logoff/shutdown "
                "— ~5s OS deadline); running critical-only teardown with 1s timeouts"
            )

            # 1. crash_recovery.flush()
            try:
                if app._crash_recovery is not None:
                    app._crash_recovery.flush(timeout=1.0)
            except Exception:
                log.debug("[SHUTDOWN] fast-path crash_recovery.flush failed", exc_info=True)

            # 2. history_db.flush()
            try:
                if app.history_db is not None:
                    _run_with_timeout(
                        "history_db.flush (fast-path)",
                        app.history_db.flush,
                        timeout=1.0,
                    )
            except Exception:
                log.debug("[SHUTDOWN] fast-path history_db.flush failed", exc_info=True)

            # 3. recorder.stop() — release the PortAudio stream.
            try:
                if app.recorder is not None and app.recorder.recording:
                    _stop_result = _run_with_timeout(
                        "recorder.stop (fast-path)",
                        app.recorder.stop,
                        timeout=1.0,
                    )
                    if _stop_result is TIMEOUT:
                        with contextlib.suppress(Exception):
                            app.recorder._force_closed = True
                        log.warning("[SHUTDOWN] XZ-R17-06: recorder.stop() timed out in fast-path")
            except Exception:
                log.debug("[SHUTDOWN] fast-path recorder.stop failed", exc_info=True)

            # 4. _clear_backend_pid_file()
            try:
                from voice_typer.server import app as _app_module

                _app_module._clear_backend_pid_file()
            except Exception:
                log.debug("[SHUTDOWN] fast-path _clear_backend_pid_file failed", exc_info=True)

            # 5. Win32 mutex CloseHandle / POSIX flock release.
            try:
                if hasattr(app, "_mutex_handle") and app._mutex_handle:
                    if is_windows():
                        import ctypes

                        ctypes.windll.kernel32.CloseHandle(app._mutex_handle)
                    else:
                        app._mutex_handle.release()
                    app._mutex_handle = None
            except Exception:
                log.debug("[SHUTDOWN] fast-path mutex release failed", exc_info=True)

            log.warning("[SHUTDOWN] XZ-R17-06: fast cleanup path complete")

        # UE-1: bypass atexit — the OS is killing us (Windows logoff/shutdown
        # gives ~5s). Orderly atexit cleanup would race the OS force-kill and
        # lose. Safe because we've already run critical cleanup above (or a
        # prior call did, via the ``_cleanup_done`` idempotency guard). The
        # ``os._exit(0)`` MUST fire even on a no-op second invocation so the
        # Win32 callback does not return ``True`` to the OS without exiting.
        # ``os._exit`` is async-signal-safe per POSIX, which is the correct
        # primitive for a console-control callback context.
        os._exit(0)

    # ─── XV-7 parallel teardown helpers ──────────────────────────────
    # ─── XV-7 parallel teardown helpers ──────────────────────────────
    #
    # Each helper takes ``self`` only (no args), accesses ``self._app``
    # for subsystem references, and logs its own outcome at DEBUG with
    # ``exc_info=True``. Failures do NOT propagate —
    # ``_run_parallel_with_timeout`` captures per-call exceptions so
    # one slow/failing helper does not mask its peers.

    def _teardown_timers_and_recording(self) -> None:
        """XV-7: cancel pending timers + drain in-flight timer threads,
        stop the recording watchdog, and atomically pop the streaming
        session (DE-7).

        Groups three concerns that all touch the RecordingController /
        TimerCoordinator surface and were previously sequential blocks
        at the top of ``_do_cleanup``.
        """
        app = self._app
        # Cancel all pending timers.
        # GT-72: ``_cancel_pending_timers`` (on TimerCoordinator) bumps
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

        # PROD-003: Stop the persistent watchdog thread.
        try:
            if hasattr(app, "recording") and app.recording is not None:
                app.recording._stop_watchdog_thread()
        except Exception:
            log.debug("[CLEANUP] _stop_watchdog_thread failed", exc_info=True)

        # DE-7: atomically pop the streaming session instead of the
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

    def _teardown_recorder(self) -> None:
        """XV-7: stop the PortAudio stream (recorder.stop / discard) and
        the mic watcher; join the transcription thread.

        GT-70: if ``recorder.stop()`` (or ``discard()``) times out, the
        leaked worker thread is still accessing the PortAudio stream.
        We set a local ``recorder_force_closed`` flag, mirror it onto
        ``app.recorder._force_closed`` so the recorder itself can
        short-circuit any later access, and SKIP the downstream
        ``shutdown_mic_watcher`` call. We also signal
        ``self._recorder_teardown_done`` and set
        ``self._recorder_force_closed`` so ``_teardown_sounddevice``
        (running concurrently in the parallel batch) can SKIP
        ``sd.stop()`` to avoid a double-stop deadlock (DE-54).
        """
        app = self._app
        recorder_force_closed = False
        try:
            if app.recorder is not None and app.recorder.recording:
                try:
                    _stop_result = _run_with_timeout(
                        "recorder.stop",
                        app.recorder.stop,
                        timeout=5.0,
                    )
                    if _stop_result is TIMEOUT:
                        recorder_force_closed = True
                        # The ``_force_closed`` field is declared on
                        # ``Recorder.__init__`` (always present on any real
                        # ``Recorder`` instance), so the write is safe without
                        # ``contextlib.suppress`` — the suppress wrapper would
                        # only mask a real bug.
                        app.recorder._force_closed = True
                        log.warning(
                            "[SHUTDOWN] GT-70: recorder.stop() timed out — "
                            "marking recorder as force-closed; downstream "
                            "recorder.shutdown_mic_watcher will be skipped"
                        )
                except Exception as e:
                    log.warning("[SHUTDOWN] recorder.stop() failed: %s, trying discard()", e)
                    try:
                        _discard_result = _run_with_timeout(
                            "recorder.discard",
                            app.recorder.discard,
                            timeout=5.0,
                        )
                        if _discard_result is TIMEOUT:
                            recorder_force_closed = True
                            # See note above: ``_force_closed`` is always
                            # present on a real ``Recorder`` instance.
                            app.recorder._force_closed = True
                            log.warning(
                                "[SHUTDOWN] GT-70: recorder.discard() timed out — "
                                "marking recorder as force-closed; downstream "
                                "recorder.shutdown_mic_watcher will be skipped"
                            )
                    except Exception as e2:
                        log.warning("[SHUTDOWN] recorder.discard() also failed: %s", e2)
        except Exception:
            log.debug("[CLEANUP] recorder stop/discard failed", exc_info=True)

        # PERF-MIC-001: stop the OS-event device watcher. GT-70: SKIP
        # this step if ``recorder.stop`` / ``recorder.discard`` timed
        # out above — the leaked worker thread is still accessing the
        # PortAudio stream, and concurrent ``shutdown_mic_watcher``
        # calls can segfault or leave the audio device inconsistent.
        try:
            if app.recorder is not None and not recorder_force_closed:
                _run_with_timeout(
                    "recorder.shutdown_mic_watcher",
                    app.recorder.shutdown_mic_watcher,
                    timeout=5.0,
                )
            elif recorder_force_closed:
                log.warning(
                    "[SHUTDOWN] GT-70: skipping recorder.shutdown_mic_watcher "
                    "because recorder.stop()/discard() timed out (leaked worker "
                    "may still be accessing the PortAudio stream)"
                )
        except Exception as e:
            log.debug("[SHUTDOWN] mic watcher shutdown failed: %s", e)

        # Wait for any running transcription thread to finish (short timeout).
        # ARCH-REFAC-003: read directly from RecordingController (was a
        # @property delegate previously).
        try:
            if hasattr(app, "recording") and app.recording is not None:
                t = app.recording._transcription_thread
                if t is not None and t.is_alive():
                    log.info("[SHUTDOWN] Waiting for transcription thread to finish...")
                    t.join(timeout=3.0)
                    if t.is_alive():
                        log.warning("[SHUTDOWN] Transcription thread did not finish in time, continuing shutdown")
        except Exception:
            log.debug("[CLEANUP] transcription thread join failed", exc_info=True)

        # DE-54 / GT-70: publish the force-closed flag for
        # ``_teardown_sounddevice`` (running concurrently in the parallel
        # batch) and signal that recorder teardown is done. The Event
        # gives the sounddevice helper a happens-before guarantee on the
        # flag read even though both helpers run in the same
        # ThreadPoolExecutor wave.
        self._recorder_force_closed = recorder_force_closed
        self._recorder_teardown_done.set()

    def _teardown_level_monitor(self) -> None:
        """XV-7: stop the level_monitor module's PortAudio InputStream +
        worker thread.

        MED-NNN / XCUT-2: the level_monitor module owns its own
        PortAudio InputStream + worker thread as module-level globals
        that are NOT registered with ``app._thread_registry``. Without
        this call the stream + worker leak across restart_app().
        Best-effort — stop_monitoring() is itself idempotent.
        """
        try:
            from voice_typer.server import level_monitor

            _run_with_timeout(
                "level_monitor.stop_monitoring",
                level_monitor.stop_monitoring,
                timeout=5.0,
            )
        except Exception:
            log.warning(
                "[SHUTDOWN] level_monitor.stop_monitoring failed",
                exc_info=True,
            )

    def _teardown_restore_volume(self) -> None:
        """XV-7: restore OS volume if it was ducked when the app quit.

        Without this, a quit-during-recording leaves volume stuck low.
        Uses ``fade_ms=0`` for instant restore — the app is exiting.
        """
        app = self._app
        try:
            _run_with_timeout(
                "restore_volume",
                lambda: app._restore_volume(fade_ms=0),
                timeout=5.0,
            )
        except Exception:
            log.debug("[CLEANUP] volume restore failed", exc_info=True)

    def _teardown_hotkeys(self) -> None:
        """XV-7: stop all three hotkey backends (dictation / ESC / repaste)
        in a nested parallel batch.

        The three backends touch disjoint OS resources (RegisterHotKey
        handles on Windows, evdev/X11 sockets on Linux, CGEventTap on
        macOS) and are safe to stop in parallel. Sequential stop() took
        up to 15s (3x5s) worst case; parallel stop() finishes in ≤5s.
        """
        app = self._app
        try:
            _hk_info = (
                f"dictation={app.hotkeys._hotkey_backend.hotkey_str if app.hotkeys._hotkey_backend else 'none'}, "
                f"esc={app.hotkeys._esc_backend.hotkey_str if app.hotkeys._esc_backend else 'none'}, "
                f"repaste={app.hotkeys._repaste_backend.hotkey_str if app.hotkeys._repaste_backend else 'none'}"
            )
            log.info("[HOTKEY] Stopping hotkey listeners (%s)", _hk_info)

            # XV-7: the three hotkey backends touch disjoint OS resources
            # and are safe to stop in parallel.
            parallel_stops: list[tuple[str, object, float]] = []
            if app.hotkeys._hotkey_backend:
                parallel_stops.append(("hotkey_backend.stop", app.hotkeys._hotkey_backend.stop, 5.0))
            # RELIABILITY-003: also stop ESC cancel and repaste hotkey
            # backends so their RegisterHotKey / GlobalHotKeys registrations
            # are released before the next instance tries to claim them.
            if app.hotkeys._esc_backend:
                parallel_stops.append(("esc_backend.stop", app.hotkeys._esc_backend.stop, 5.0))
            if app.hotkeys._repaste_backend:
                parallel_stops.append(("repaste_backend.stop", app.hotkeys._repaste_backend.stop, 5.0))
            for _desc, _result in _run_parallel_with_timeout(parallel_stops):
                if isinstance(_result, BaseException):
                    log.warning("[SHUTDOWN] %s failed: %s", _desc, _result)
                elif _result is TIMEOUT:
                    log.warning("[SHUTDOWN] %s timed out", _desc)

            # XZ-R17-11: null the hotkey backend refs after stop() so a
            # subsequent _do_cleanup pass does NOT re-enter stop() on an
            # already-torn-down backend. stop_all() on HotkeyDispatcher
            # nulls these refs, but the shutdown path calls individual
            # backends in parallel (XV-7); mirror the nulling here.
            for _attr in ("_hotkey_backend", "_esc_backend", "_repaste_backend"):
                with contextlib.suppress(Exception):
                    setattr(app.hotkeys, _attr, None)

            log.info("[HOTKEY] All hotkey listeners stopped")
        except Exception:
            log.debug("[CLEANUP] hotkey backend stop failed", exc_info=True)

    def _teardown_crash_recovery(self) -> None:
        """XV-7: flush pending crash-recovery writes + shutdown the writer.

        RELIABILITY-005: flush before the process exits so the latest
        state is persisted. Short timeout — if the disk is genuinely
        slow we'd rather exit and lose the in-flight snapshot than hang
        the shutdown.
        """
        app = self._app
        try:
            if app._crash_recovery is not None:
                app._crash_recovery.flush(timeout=2.0)
                _run_with_timeout(
                    "crash_recovery.shutdown",
                    app._crash_recovery.shutdown,
                    timeout=5.0,
                )
        except Exception as e:
            log.warning("[SHUTDOWN] crash recovery flush failed: %s", e)

    def _teardown_history_db(self) -> None:
        """XV-7: flush pending fire-and-forget history DB writes + close
        the DB (joins the writer thread).

        CRASH-SAFE-GAP-A: ``add_transcription()`` is fire-and-forget
        (enqueues the INSERT and returns immediately). If quit() exits
        without draining the queue, the writer thread (a daemon) is
        killed by the OS and any unprocessed INSERTs are silently lost.
        Flushing here ensures the writer drains its queue and commits
        all pending writes before the process terminates.
        """
        app = self._app
        try:
            if app.history_db is not None:
                _run_with_timeout(
                    "history_db.flush",
                    app.history_db.flush,
                    timeout=10.0,
                )
                _run_with_timeout(
                    "history_db.close",
                    app.history_db.close,
                    timeout=5.0,
                )
        except Exception as e:
            log.warning("[SHUTDOWN] history DB flush/close failed: %s", e)

    def _teardown_waveform_wiring(self) -> None:
        """XV-7: stop the bubble level / waveform worker so it doesn't
        try to push to a torn-down IPC server during shutdown.

        PERF-NEW-001: the worker / queue / stop_event live on
        WaveformBubbleWiring; delegate to its stop() helper.
        """
        app = self._app
        try:
            _run_with_timeout(
                "waveform_wiring.stop",
                app.waveform_wiring.stop,
                timeout=5.0,
            )
        except Exception as e:
            log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)

    def _teardown_sounddevice(self) -> None:
        """XV-7 / DE-54: safety-net ``sd.stop()`` — skipped when
        ``recorder.stop()`` (or ``discard()``) timed out.

        PROD-003: if recorder.stop() above failed or an audio callback
        leaked a stream, this ensures sounddevice doesn't hold the
        microphone. DE-54: SKIP this call when the recorder teardown
        timed out — the leaked recorder.stop() worker thread is still
        holding the PortAudio stream lock, and calling ``sd.stop()``
        while that lock is held deadlocks the cleanup thread on
        PortAudio backends (notably WASAPI).

        This helper waits for ``_teardown_recorder`` to finish (via
        ``_recorder_teardown_done``) before reading the
        ``_recorder_force_closed`` flag, giving a happens-before
        guarantee even though both helpers run concurrently in the
        parallel batch.

        UE-2: ``sd.stop()`` is the non-blocking signal that asks every
        active PortAudio stream to stop; ``sd.wait()`` is the bounded
        drain that blocks until each stream has actually closed. Both
        are wrapped via :func:`_run_with_timeout` so the cleanup thread
        is never blocked indefinitely. The ``_run_with_timeout`` return
        value is checked against :data:`TIMEOUT` — if either call times
        out (the ``wait()`` case is the dangerous one because
        PortAudio's stream-close handshake can deadlock on backends
        like WASAPI where the audio callback holds the stream lock),
        we log at ERROR and force-abort every active stream via
        :meth:`_abort_sounddevice_streams` (which calls
        ``stream.abort()`` on each — ``abort()`` is documented to
        "terminate the stream immediately", bypassing the orderly
        stop handshake and releasing the PortAudio resources the
        deadlock was holding).
        """
        # Wait for recorder teardown to complete (it sets
        # _recorder_force_closed). Bound the wait at 9.5s so the outer
        # _run_with_timeout(10.0) wrapper still has 0.5s slack to log
        # and return if the recorder helper genuinely finishes near the
        # shared deadline.
        self._recorder_teardown_done.wait(timeout=9.5)
        if self._recorder_force_closed:
            log.warning(
                "[SHUTDOWN] DE-54: skipping sd.stop() because "
                "recorder.stop()/discard() timed out (leaked worker may "
                "still be accessing the PortAudio stream)"
            )
            return
        try:
            import sounddevice as sd

            # UE-2: ``sd.stop()`` is the non-blocking signal; wrap it
            # so a wedged PortAudio backend (e.g. WASAPI stream lock
            # held by a leaked callback) cannot block the cleanup
            # thread indefinitely. If the call times out, force-abort
            # every active stream — ``abort()`` bypasses the orderly
            # stop handshake and breaks the deadlock.
            _stop_result = _run_with_timeout(
                "sounddevice.stop",
                sd.stop,
                timeout=3.0,
            )
            if _stop_result is TIMEOUT:
                log.error(
                    "[SHUTDOWN] UE-2: sd.stop() did not return within 3s — "
                    "PortAudio may be deadlocked (stream lock held by a "
                    "leaked callback on backends like WASAPI); force-"
                    "aborting active streams to release resources"
                )
                self._abort_sounddevice_streams(sd)
                return

            # UE-2: ``sd.wait()`` blocks until every active stream has
            # actually drained. PortAudio's stream-close handshake can
            # deadlock on backends where the audio callback holds the
            # stream lock; without a bounded wait, this would block
            # shutdown indefinitely. Wrap it; on timeout, log at ERROR
            # and force-abort the streams (the wait() return value is
            # checked explicitly against TIMEOUT).
            _wait_result = _run_with_timeout(
                "sounddevice.wait",
                sd.wait,
                timeout=2.0,
            )
            if _wait_result is TIMEOUT:
                log.error(
                    "[SHUTDOWN] UE-2: sd.wait() did not return within 2s — "
                    "PortAudio stream(s) did not drain (potential deadlock "
                    "on backends like WASAPI); force-aborting active "
                    "streams to release the audio device"
                )
                self._abort_sounddevice_streams(sd)
        except Exception:
            log.debug("[CLEANUP] sd.stop()/wait() failed", exc_info=True)

    def _abort_sounddevice_streams(self, sd_module) -> None:
        """UE-2: force-abort every active sounddevice stream.

        ``sounddevice._streams`` is the module-level registry of active
        ``sd.Stream`` / ``sd.InputStream`` / ``sd.OutputStream`` instances
        that ``sd.stop()`` and ``sd.wait()`` operate on. When the
        orderly drain times out (a PortAudio deadlock — the audio
        callback is holding the stream lock and the close handshake
        cannot complete), iterate a snapshot of the registry and call
        ``stream.abort()`` on each.

        ``Stream.abort()`` is documented as "Terminate the stream
        immediately" — it sets the stream's ``_CallbackFlags`` and
        invokes ``Pa_AbortStream`` under the hood, which closes the
        stream without waiting for in-flight audio callbacks to drain.
        This breaks the deadlock by releasing the PortAudio resources
        the leaked callback was holding, so the audio device is
        available for the next process launch (without this, the next
        launch fails with "Device unavailable" because the OS still
        sees the stream as in-use).

        Best-effort: per-stream failures are suppressed
        (``contextlib.suppress(Exception)``) so one bad stream does
        not prevent the abort of the others. The ``_streams`` list is
        snapshotted before iteration to avoid mutation-during-iteration
        if ``abort()`` removes the stream from the registry.
        """
        try:
            streams = [s for s in getattr(sd_module, "_streams", []) if s is not None]
            for stream in streams:
                with contextlib.suppress(Exception):
                    stream.abort()
        except Exception:
            log.debug(
                "[SHUTDOWN] UE-2: _abort_sounddevice_streams fallback failed",
                exc_info=True,
            )

    def _teardown_electron(self) -> None:
        """XV-7 / DE-53: terminate the Electron subprocess.

        P1-1.3: prefer the dedicated ``electron_launcher.terminate_electron``
        helper (which kills the entire process tree on Windows and uses
        SIGTERM → SIGKILL on POSIX) when we have a tracked PID. Fall
        back to the legacy ``tray_window`` path for PID discovery.

        DE-53: the read-terminate-clear sequence is guarded by
        ``self._electron_pid_lock`` so concurrent ``quit()`` callers
        don't double-terminate or clobber a freshly-installed PID.

        XV-8: both branches are wrapped in ``_run_with_timeout(5.0)``.
        The legacy ``tray_window`` path now does SIGTERM → 2s wait →
        SIGKILL on POSIX (was SIGTERM-only with a 5s timeout that
        ``os.kill`` never actually blocks on).
        """
        app = self._app
        try:
            from voice_typer.server import electron_launcher

            # DE-53: hold the lock only across the read-terminate-clear
            # critical section so a concurrent caller observes the
            # cleared PID and skips. The lock is non-reentrant; the
            # terminate_electron call inside the critical section does
            # not re-acquire it.
            with self._electron_pid_lock:
                launched_pid = getattr(app, "_electron_pid", None)
                if launched_pid:
                    log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", launched_pid)
                    _term_result = _run_with_timeout(
                        "electron_launcher.terminate_electron",
                        lambda: electron_launcher.terminate_electron(launched_pid),
                        timeout=5.0,
                    )
                    if _term_result is TIMEOUT and sys.platform != "win32":
                        import signal as _sig_kill

                        with contextlib.suppress(OSError, ProcessLookupError):
                            os.kill(launched_pid, _sig_kill.SIGKILL)
                    app._electron_pid = None
                else:
                    from voice_typer.server.tray_window import get_electron_pid

                    electron_pid = get_electron_pid()
                    if electron_pid is not None:
                        import signal as _sig

                        log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", electron_pid)
                        # XV-8: SIGTERM → 2s waitpid poll → SIGKILL on POSIX.
                        # ``os.kill(SIGTERM)`` returns immediately (it just
                        # queues the signal); the 2s waitpid poll gives the
                        # child a grace period to exit cleanly before we
                        # escalate to SIGKILL.
                        with contextlib.suppress(OSError, ProcessLookupError):
                            os.kill(electron_pid, _sig.SIGTERM)
                        deadline = time.monotonic() + 2.0
                        reaped = False
                        while time.monotonic() < deadline:
                            try:
                                pid_done, _status = os.waitpid(electron_pid, os.WNOHANG)
                                if pid_done != 0:
                                    reaped = True
                                    break
                            except OSError:
                                # Child already reaped or not a child of
                                # this process — stop polling.
                                reaped = True
                                break
                            time.sleep(0.1)
                        if not reaped and sys.platform != "win32":
                            with contextlib.suppress(OSError, ProcessLookupError):
                                os.kill(electron_pid, _sig.SIGKILL)
        except Exception:
            log.debug("[SHUTDOWN] Electron subprocess termination failed", exc_info=True)

    def _teardown_pid_file(self) -> None:
        """XV-7: clear the backend PID file so a subsequent launch isn't
        falsely blocked by the single-instance check.

        Looks up ``_clear_backend_pid_file`` dynamically from the app
        module so tests that monkeypatch
        ``voice_typer.server.app._clear_backend_pid_file`` still take
        effect (mirrors the SettingsController convention).
        """
        try:
            from voice_typer.server import app as _app_module

            _app_module._clear_backend_pid_file()
        except Exception:
            log.debug("[SHUTDOWN] could not clear backend PID file", exc_info=True)

    def _teardown_mutex_handle(self) -> None:
        """XV-7: release the single-instance mutex handle.

        PLAT-HLEAK: on Windows, ``CloseHandle`` releases the named mutex
        so a subsequent launch can claim it. On POSIX, the
        ``_mutex_handle`` is a ``_PosixSingleInstanceHandle`` wrapping
        the lockfile fd — its ``release()`` closes the fd (releasing the
        ``fcntl.flock``) and unlinks the ``backend.lock``. Without this
        branch, the Windows-only ``ctypes.windll.kernel32.CloseHandle``
        call would raise ``AttributeError`` on POSIX
        (``ctypes.windll`` is Windows-only), which was swallowed by the
        try/except, leaving the lockfile fd dangling until process exit
        and racing a fast re-launch. ``contextlib.suppress(Exception)``
        mirrors the Windows branch's best-effort contract: cleanup must
        never propagate failures.
        """
        app = self._app
        try:
            if hasattr(app, "_mutex_handle") and app._mutex_handle:
                if is_windows():
                    import ctypes

                    ctypes.windll.kernel32.CloseHandle(app._mutex_handle)
                else:
                    # POSIX: release the flock-based single-instance
                    # handle (closes the fd + unlinks the lockfile).
                    app._mutex_handle.release()
                app._mutex_handle = None
        except Exception:
            log.debug("[CLEANUP] mutex handle release failed", exc_info=True)

    def _teardown_devnull_files(self) -> None:
        """XV-7: close devnull streams opened during logging setup.

        Looks up ``_close_devnull_files`` dynamically from the app
        module so tests that monkeypatch
        ``voice_typer.server.app._close_devnull_files`` still take
        effect.
        """
        try:
            from voice_typer.server import app as _app_module

            _app_module._close_devnull_files()
        except Exception:
            log.debug("[CLEANUP] close devnull files failed", exc_info=True)

    def _teardown_asr_models(self) -> None:
        """DJ-7: unload active ASR backend + release CUDA caching allocator
        blocks so torch's VRAM is returned to the OS before process exit.

        Pre-fix, ``shutdown_controller._do_cleanup`` ran 14 parallel
        teardown helpers — NONE of them touched ``app.models`` /
        ``app.models.registry``. ``asr_registry.unload()`` was only
        invoked on (a) backend load failure and (b) ``app._change_model()``.
        On a normal quit / restart_app / atexit, the active Parakeet /
        Whisper backend's ``unload()`` was never called. Combined with
        DJ-6 (host force-kills after 2-6s), the Python process was
        SIGKILLed before Python's GC could drop the model references —
        meaning torch's ``empty_cache()`` / ``cuda.synchronize()`` /
        context destructor never ran. On GPU systems this leaked CUDA
        memory across rapid restart cycles; on CPU-only Whisper ~1-3GB
        RSS stayed resident longer than necessary.

        This helper is placed FIRST in the parallel batch so the
        (potentially slow) CUDA context teardown starts as early as
        possible. ``registry.unload()`` is idempotent and already wraps
        every per-backend ``backend.unload()`` in try/except, so a
        single failing backend doesn't abort the others.
        ``release_gpu_memory()`` guards on ``torch.cuda.is_available()``
        and wraps both ``synchronize()`` and ``empty_cache()`` in
        try/except, so it is a no-op on CPU-only hosts.
        """
        try:
            registry = getattr(self._app.models, "registry", None)
            if registry is not None and hasattr(registry, "unload"):
                registry.unload()
        except Exception:
            log.debug("[CLEANUP] asr_registry.unload() failed", exc_info=True)
        try:
            from voice_typer.server.asr_utils import release_gpu_memory

            release_gpu_memory()
        except Exception:
            log.debug(
                "[CLEANUP] release_gpu_memory() failed (non-fatal)",
                exc_info=True,
            )

    def _teardown_event_bus(self) -> None:
        """XV-7: shut down the event_bus deferred-publish executor.

        M-22: this is the LAST module-level cleanup because earlier
        steps (bubble worker stop, recorder stop, hotkey stop) can each
        publish events via ``event_bus.publish``, and an RT-thread
        publish defers to this executor. Shutting it down here ensures
        no deferred ``_deliver`` tasks outlive the subsystems they
        deliver TO.

        TY-15: ``event_bus.shutdown`` now calls
        ``executor.shutdown(wait=True, cancel_futures=True)`` so the
        5s ``_run_with_timeout`` wrapper ACTUALLY bounds the wait
        (previously ``wait=False`` returned immediately and the
        non-daemon worker thread lingered past the 5s "timeout").
        Idempotent — safe under the ``_do_cleanup`` double-call guard.
        """
        try:
            from voice_typer.server import event_bus as _event_bus

            _run_with_timeout(
                "event_bus.shutdown",
                _event_bus.shutdown,
                timeout=5.0,
            )
        except Exception:
            log.debug("[CLEANUP] event_bus.shutdown failed", exc_info=True)

    # ─── Quit ──────────────────────────────────────────────────────────

    def quit(self):
        """Shut down the application cleanly.

        PROD-003: ensures all threads, PortAudio streams, and
        subprocesses are properly stopped with timeouts. Previously
        thread joins had no timeout and PortAudio streams could be
        left open if quit() raced with the audio callback.

        RW-3: the cleanup body has been extracted into
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

        CR-51: the check-then-set on ``_shutting_down`` is now
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
        app = self._app
        # CR-51: hold _quit_lock around the check-then-set-then-
        # shutdown_all sequence. A second concurrent caller that
        # arrives while we hold the lock will see
        # ``app._shutting_down == True`` once we release it (because
        # we set it inside the critical section) and short-circuit.
        # CR-51 / PVT-024: hold _quit_lock ONLY around the check-then-set
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
        with self._quit_lock:
            if app._shutting_down:
                log.debug("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
                return

            is_main = threading.current_thread() is threading.main_thread()
            log.info("[SHUTDOWN] Shutting down")
            app._shutting_down = True
            # RACE-020: also set the Event version so executor tasks can check it
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
        # (see PVT-024 note above) so a concurrent quit() doesn't
        # block on the per-thread joins.
        try:
            app._thread_registry.shutdown_all()
        except Exception:
            log.debug(
                "[SHUTDOWN] thread_registry.shutdown_all() failed",
                exc_info=True,
            )

        # RW-3: delegate to the shared, idempotent cleanup body. The
        # _cleanup_done flag inside _do_cleanup() guarantees that a
        # later _atexit_cleanup() call (or a duplicate quit()) is a
        # no-op rather than double-flushing / double-stopping.
        #
        # NOTE: we call ``app._do_cleanup()`` (the delegate on
        # VoiceTyperApp) rather than ``self._do_cleanup()`` (the body
        # on this controller) so test spies that
        # ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still
        # intercept the call — see
        # tests/test_app_cleanup.py::test_quit_calls_do_cleanup.
        app._do_cleanup()

        # GT-43: After ``_do_cleanup()`` completes on a non-main thread,
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
            self._arm_shutdown_watchdog(SHUTDOWN_WATCHDOG_TIMEOUT_S)

        if is_main:
            sys.exit(0)

    def _arm_shutdown_watchdog(self, timeout_s: float) -> None:
        """GT-43 / DE-11: arm a daemon-thread watchdog that calls
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

        DE-11: the watchdog uses a single ``time.sleep(timeout_s)`` call
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
            os._exit(0)

        t = threading.Thread(
            target=_watchdog,
            name="shutdown-watchdog",
            daemon=True,
        )
        t.start()

    # ─── atexit safety net (DR-28: body → voice_typer.server.atexit_safety) ──

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called.

        DR-28: body lives in :func:`voice_typer.server.atexit_safety.atexit_log`.
        This delegate preserves the instance-method API used by
        ``atexit.register(self._atexit_log)`` in ``VoiceTyperApp.start()``.
        """
        from voice_typer.server.atexit_safety import atexit_log

        atexit_log(self)

    def _atexit_cleanup(self) -> None:
        """RACE-016: atexit handler for critical cleanup paths.

        Idempotent — short-circuits on ``_shutting_down`` and never
        raises (CR-21). See :func:`voice_typer.server.atexit_safety.atexit_cleanup`
        for the full behavior contract (DR-28 extraction).

        DR-28: body lives in :mod:`voice_typer.server.atexit_safety`.
        This delegate preserves the instance-method API used by tests
        (``controller._atexit_cleanup()``) and the ``VoiceTyperApp``
        wiring (``atexit.register(self._atexit_cleanup)``).
        """
        from voice_typer.server.atexit_safety import atexit_cleanup

        atexit_cleanup(self)

    # ─── Signal handlers (DR-28: body → voice_typer.server.signal_handlers) ──

    def _install_signal_handlers(self):
        """Install SIGINT/SIGTERM/SIGHUP handlers for graceful shutdown.

        DR-28: body lives in
        :func:`voice_typer.server.signal_handlers.install_signal_handlers`.
        This delegate preserves the instance-method API used by tests
        (``controller._install_signal_handlers()``) and the
        ``VoiceTyperApp`` wiring (``app.start()`` calls
        ``self._install_signal_handlers()``).
        """
        from voice_typer.server.signal_handlers import install_signal_handlers

        install_signal_handlers(self)

    def _signal_watcher_loop(self) -> None:
        """Watcher thread for the POSIX signal handlers.

        DR-28: body lives in
        :func:`voice_typer.server.signal_handlers.signal_watcher_loop`.
        This delegate is kept so the test fixture that calls
        ``controller._signal_watcher_loop()`` directly continues to work,
        and so legacy code that captured ``target=self._signal_watcher_loop``
        before the DR-28 split keeps functioning. New code should call
        ``signal_handlers.signal_watcher_loop(controller)`` directly.
        """
        from voice_typer.server.signal_handlers import signal_watcher_loop

        signal_watcher_loop(self)

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure.

        DR-28: body lives in
        :func:`voice_typer.server.signal_handlers.install_win32_console_handler`.
        This delegate preserves the instance-method API used by tests
        (``controller._install_win32_console_handler()``) and the
        ``VoiceTyperApp`` wiring (``app.start()`` calls
        ``self._install_win32_console_handler()``).
        """
        from voice_typer.server.signal_handlers import (
            install_win32_console_handler,
        )

        install_win32_console_handler(self)

    def _win32_console_handler(self, ctrl_type):
        """Callback for Windows console control events.

        DR-28: body lives in
        :func:`voice_typer.server.signal_handlers.win32_console_handler`.
        This delegate preserves the instance-method API used by tests
        (``controller._win32_console_handler(ctrl_type)`` — see
        ``tests/test_shutdown_controller.py::TestWin32ConsoleHandlerRouting``)
        and the ctypes callback wiring (``handler_routine(self._win32_console_handler)``
        inside :func:`signal_handlers.install_win32_console_handler`).
        """
        from voice_typer.server.signal_handlers import win32_console_handler

        return win32_console_handler(self, ctrl_type)
