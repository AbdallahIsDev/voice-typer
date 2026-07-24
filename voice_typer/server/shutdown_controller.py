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
import signal
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    # ``shutdown_controller`` indirectly via the ``ShutdownController(self)``
    # call inside ``VoiceTyperApp.__init__``).  At runtime, ``app`` is
    # whatever object was passed to ``__init__`` (always a
    # ``VoiceTyperApp`` in production, but tests pass mocks that satisfy
    # the same duck-typed surface).
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


# GT-43 / GT-70: sentinel returned by ``_run_with_timeout`` when the worker
# thread did not finish within the timeout. Distinct from ``None`` so callers
# can reliably detect a timeout and apply per-resource shutdown barriers
# (GT-70) or hard-kill fallbacks (GT-43 tray.stop → os._exit(0)).
class _TimeoutSentinel:
    """Singleton sentinel signaling that ``_run_with_timeout`` abandoned
    its worker thread. Use ``is TIMEOUT`` to compare."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — cosmetic
        return "<TIMEOUT>"


TIMEOUT = _TimeoutSentinel()
_TIMEOUT = TIMEOUT
_DE11_GRACE_PERIOD_SECONDS: float = 2.0


# GT-43: watchdog timeout for the non-main-thread ``quit()`` /
# ``restart_app()`` path. After ``_do_cleanup()`` completes, we arm a
# daemon-thread watchdog that calls ``os._exit(0)`` after this many
# seconds if the process is still alive (i.e. the main thread hasn't
# returned from ``tray.run()``). 10s matches the GT-43 spec; tests patch
# this to a shorter value to keep the suite fast.
SHUTDOWN_WATCHDOG_TIMEOUT_S: float = 10.0


def _run_with_timeout(description: str, func, timeout: float = 5.0):
    """PVT-G5-057: run *func* in a worker thread with a hard timeout.

    Returns whatever ``func()`` returned, or :data:`TIMEOUT` (a sentinel
    distinct from ``None``) if it did not finish within *timeout* seconds.
    Re-raises any exception raised by ``func()`` so the caller's existing
    try/except still applies. The worker thread is daemon-marked so it
    doesn't block interpreter exit if it really is stuck.

    GT-70: callers that share a resource across multiple
    ``_run_with_timeout`` calls (e.g. ``app.recorder`` for
    ``recorder.stop`` → ``recorder.shutdown_mic_watcher``) MUST check the
    return value against :data:`TIMEOUT` and skip the downstream call
    when the upstream one timed out — otherwise the leaked worker thread
    races the next call on the same resource (PortAudio is not safe for
    concurrent stream operations from multiple threads).
    """
    result_holder: dict = {}

    def _worker() -> None:
        try:
            result_holder["value"] = func()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            result_holder["error"] = exc

    t = threading.Thread(
        target=_worker,
        daemon=True,
        name=f"cleanup-{description}",
    )
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log.warning(
            "[SHUTDOWN] %s did not finish in %.1fs — continuing (worker thread leaked as daemon)",
            description,
            timeout,
        )
        return TIMEOUT
    if "error" in result_holder:
        raise result_holder["error"]
    return result_holder.get("value")


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
        # ``await`` checkpoint (handlers that don't await remain
        # uncancellable, but those are by definition short and finish
        # on their own).
        #
        # Defensive: ``app._ipc_server`` may be None (server never
        # started — e.g. the atexit safety net fires before
        # ``main()`` reaches ``build_ipc_server``) or a MagicMock (test
        # doubles). ``getattr(..., None)`` handles both: ``None`` has
        # no ``_ws_dispatch_pool`` attribute (returns None), and
        # ``MagicMock`` returns a child mock (which lacks the
        # ``shutdown`` method we need — guarded by the
        # ``hasattr(pool, "shutdown")`` check).
        try:
            ipc_server = getattr(app, "_ipc_server", None)
            ws_pool = getattr(ipc_server, "_ws_dispatch_pool", None)
            if ws_pool is not None and hasattr(ws_pool, "shutdown"):
                ws_pool.shutdown(wait=False, cancel_futures=True)
                log.debug("[SHUTDOWN] WS dispatch pool shut down (cancel_futures=True)")
        except Exception:
            log.debug("[SHUTDOWN] WS dispatch pool shutdown failed", exc_info=True)

        # Cancel all pending timers.
        # GT-72: ``_cancel_pending_timers`` (on TimerCoordinator) bumps
        # ``_timer_generation`` and calls ``Timer.cancel()`` on every
        # pending timer — but ``Timer.cancel()`` only prevents a timer
        # that hasn't fired yet. A timer whose ``guarded_func`` has
        # already been invoked by the Timer thread (passed the
        # ``gen == self._timer_generation`` check) but hasn't yet called
        # ``func()`` will STILL run ``func()`` after the generation bump,
        # racing the subsystem teardown below. The fix on the
        # TimerCoordinator side (GT-72 primary fix, NOT owned by this
        # agent) is to re-check the generation under a shutdown lock; the
        # fix HERE is to give those in-flight ``func()`` invocations a
        # short bounded window to complete before we start tearing down
        # the subsystems they touch (tray, recorder, IPC server).
        #
        # We capture the pending-timer list BEFORE calling
        # ``_cancel_pending_timers`` (which clears it), then ``.join()``
        # each captured timer thread with a short per-timer timeout so
        # the total drain is bounded. ``Timer.cancel()`` is a no-op for
        # already-fired timers, so joining them is safe and gives the
        # in-flight ``guarded_func`` body a chance to return.
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
            # 5s budget that the rest of cleanup tolerates per step.
            for timer in in_flight_timers:
                try:
                    timer.join(timeout=0.5)
                except Exception:
                    log.debug("[CLEANUP] in-flight timer join failed", exc_info=True)
        except Exception:
            log.debug("[CLEANUP] _cancel_pending_timers failed", exc_info=True)

        # PROD-003: Stop the persistent watchdog thread
        try:
            if hasattr(app, "recording") and app.recording is not None:
                app.recording._stop_watchdog_thread()
        except Exception:
            log.debug("[CLEANUP] _stop_watchdog_thread failed", exc_info=True)

        # Signal streaming session to cancel without blocking on join.
        # The old code called _cancel_streaming_session() → session.cancel()
        # → thread.join(timeout=10) which blocked quit for up to 10 seconds.
        # Instead, just signal the cancel event; the daemon thread will die
        # when the process exits.
        try:
            # RW-9 Phase 2: call RecordingController directly.
            session = app.recording.get_streaming_session()
            app.recording.set_streaming_session(None)
            if session is not None:
                session._cancel_event.set()
        except Exception:
            log.debug("[CLEANUP] streaming session cancel failed", exc_info=True)

        # PROD-003: Close PortAudio stream properly.
        # recorder.stop() fully closes the PortAudio stream (stop + close),
        # while discard() just clears the recording flag. Use stop() first
        # for a clean shutdown, then discard() as fallback if stop() fails.
        # PVT-G5-057: wrap in timeout — PortAudio's stop() can hang on
        # some backends (notably WASAPI on Windows when the device is
        # gone). 5s matches the daemon-thread join convention.
        #
        # GT-70: per-resource "shutdown barrier" for the recorder. If
        # ``recorder.stop`` (or ``recorder.discard``) times out, the
        # worker thread is LEAKED as a daemon and continues touching the
        # PortAudio stream in the background. A subsequent
        # ``recorder.shutdown_mic_watcher`` call would race the leaked
        # worker — PortAudio is not safe for concurrent stream
        # operations from multiple threads. We set a local
        # ``recorder_force_closed`` flag (and mirror it onto
        # ``app.recorder._force_closed`` so the recorder itself can
        # short-circuit any later access) and skip the downstream
        # ``shutdown_mic_watcher`` call when the flag is set.
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
                        with contextlib.suppress(Exception):
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
                            with contextlib.suppress(Exception):
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

        # PERF-MIC-001: stop the OS-event device watcher so its daemon
        # thread exits cleanly before the process tears down. Best-effort
        # — the thread is a daemon and would die on process exit anyway,
        # but explicit stop() avoids a 2s join race during GC.
        # PVT-G5-057: 5s timeout.
        # GT-70: SKIP this step if ``recorder.stop`` / ``recorder.discard``
        # timed out above — the leaked worker thread is still accessing
        # the PortAudio stream, and concurrent ``shutdown_mic_watcher``
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

        # MED-NNN / XCUT-2: the level_monitor module owns its own
        # PortAudio InputStream + worker thread as module-level globals
        # that are NOT registered with ``app._thread_registry`` (they
        # predate the registry and were never wired into it). Without
        # this call the stream + worker leak across restart_app() and
        # survive until the OS tears the process down. Best-effort —
        # stop_monitoring() is itself idempotent (it short-circuits
        # when ``_monitor_active`` is already False).
        # PVT-G5-057: 5s timeout.
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

        # Restore volume if we were ducked when the app quit.
        # Without this, a quit-during-recording leaves volume stuck low.
        # Use fade_ms=0 for instant restore — the app is exiting.
        # PVT-G5-057: 5s timeout — some platform volume backends block
        # on IPC to the OS audio server.
        try:
            _run_with_timeout(
                "restore_volume",
                lambda: app._restore_volume(fade_ms=0),
                timeout=5.0,
            )
        except Exception:
            log.debug("[CLEANUP] volume restore failed", exc_info=True)

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

        # ARCH-REFAC-003: access HotkeyDispatcher directly (was a
        # @property delegate previously).
        # PVT-G5-057: each backend stop() wrapped in 5s timeout —
        # pynput/Win32 RegisterHotKey teardown can block on OS calls.
        try:
            _hk_info = (
                f"dictation={app.hotkeys._hotkey_backend.hotkey_str if app.hotkeys._hotkey_backend else 'none'}, "
                f"esc={app.hotkeys._esc_backend.hotkey_str if app.hotkeys._esc_backend else 'none'}, "
                f"repaste={app.hotkeys._repaste_backend.hotkey_str if app.hotkeys._repaste_backend else 'none'}"
            )
            log.info("[HOTKEY] Stopping hotkey listeners (%s)", _hk_info)

            if app.hotkeys._hotkey_backend:
                _run_with_timeout(
                    "hotkey_backend.stop",
                    app.hotkeys._hotkey_backend.stop,
                    timeout=5.0,
                )

            # RELIABILITY-003: also stop ESC cancel and repaste hotkey
            # backends so their RegisterHotKey / GlobalHotKeys registrations
            # are released before the next instance tries to claim them.
            if app.hotkeys._esc_backend:
                try:
                    _run_with_timeout(
                        "esc_backend.stop",
                        app.hotkeys._esc_backend.stop,
                        timeout=5.0,
                    )
                except Exception as e:
                    log.warning("[SHUTDOWN] ESC backend stop failed: %s", e)
            if app.hotkeys._repaste_backend:
                try:
                    _run_with_timeout(
                        "repaste_backend.stop",
                        app.hotkeys._repaste_backend.stop,
                        timeout=5.0,
                    )
                except Exception as e:
                    log.warning("[SHUTDOWN] repaste backend stop failed: %s", e)

            log.info("[HOTKEY] All hotkey listeners stopped")
        except Exception:
            log.debug("[CLEANUP] hotkey backend stop failed", exc_info=True)

        # RELIABILITY-005: flush any pending crash-recovery writes
        # before the process exits, so the latest state is persisted.
        # Short timeout — if the disk is genuinely slow we'd rather
        # exit and lose the in-flight snapshot than hang the shutdown.
        # PVT-G5-057: ``flush(timeout=2.0)`` already takes a timeout
        # arg; wrap ``shutdown()`` in a 5s timeout helper for parity.
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

        # CRASH-SAFE-GAP-A: flush pending fire-and-forget history DB writes
        # before the process exits. add_transcription() is fire-and-forget
        # (enqueues the INSERT and returns immediately). If quit() exits
        # without draining the queue, the writer thread (a daemon) is killed
        # by the OS and any unprocessed INSERTs are silently lost. Flushing
        # here ensures the writer drains its queue and commits all pending
        # writes before the process terminates.
        # RELIABILITY-006-FIX-11: also close() the DB so the writer thread
        # is joined and SQLite connections are closed cleanly. flush()
        # already drained the queue, so the writer join in close() should
        # be fast.
        # PVT-G5-057: 10s for flush (may drain many pending INSERTs);
        # 5s for close (joins the writer thread).
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

        # PERF-NEW-001: stop the bubble level worker so it doesn't
        # try to push to a torn-down IPC server during shutdown.
        # RW-9 Phase 7: the worker / queue / stop_event now live on
        # WaveformBubbleWiring; delegate to its stop() helper.
        # PVT-G5-057: 5s timeout.
        try:
            _run_with_timeout(
                "waveform_wiring.stop",
                app.waveform_wiring.stop,
                timeout=5.0,
            )
        except Exception as e:
            log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)

        # PROD-003: Safety net — stop any remaining PortAudio streams.
        # If recorder.stop() above failed or an audio callback leaked
        # a stream, this ensures sounddevice doesn't hold the microphone.
        # PVT-G5-057: 5s timeout.
        try:
            import sounddevice as sd

            _run_with_timeout(
                "sounddevice.stop",
                sd.stop,
                timeout=5.0,
            )
        except Exception:
            log.debug("[CLEANUP] sd.stop() failed", exc_info=True)

        # PROD-003: Terminate the Electron subprocess if we spawned one.
        # The IPC "quit_app" push was sent earlier; this is a forced
        # termination as a safety net if the graceful signal didn't land.
        # P1-1.3: prefer the dedicated electron_launcher.terminate_electron
        # helper (which kills the entire process tree on Windows and uses
        # SIGTERM → SIGKILL on POSIX) when we have a tracked PID.  Fall
        # back to the legacy tray_window path for PID discovery so any
        # Electron launched via tray_window.open_electron_window() is also
        # cleaned up.
        try:
            from voice_typer.server import electron_launcher

            launched_pid = getattr(app, "_electron_pid", None)
            if launched_pid:
                log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", launched_pid)
                electron_launcher.terminate_electron(launched_pid)
                app._electron_pid = None
            else:
                from voice_typer.server.tray_window import get_electron_pid

                electron_pid = get_electron_pid()
                if electron_pid is not None:
                    import signal as _sig

                    log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", electron_pid)
                    with contextlib.suppress(OSError, ProcessLookupError):
                        os.kill(electron_pid, _sig.SIGTERM)
        except Exception:
            log.debug("[SHUTDOWN] Electron subprocess termination failed", exc_info=True)

        # P1-1.4: release the single-instance mutex and remove the PID
        # file so a subsequent launch isn't falsely blocked.
        # Look up _clear_backend_pid_file dynamically from the app module
        # so tests that monkeypatch voice_typer.server.app._clear_backend_pid_file
        # still take effect (mirrors the SettingsController convention).
        try:
            from voice_typer.server import app as _app_module

            _app_module._clear_backend_pid_file()
        except Exception:
            log.debug("[SHUTDOWN] could not clear backend PID file", exc_info=True)

        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # PLAT-HLEAK: Close the mutex handle on shutdown
        try:
            if hasattr(app, "_mutex_handle") and app._mutex_handle:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(app._mutex_handle)
                app._mutex_handle = None
        except Exception:
            log.debug("[CLEANUP] CloseHandle failed", exc_info=True)

        # Close devnull streams opened during logging setup.
        # Look up _close_devnull_files dynamically from the app module so
        # tests that monkeypatch voice_typer.server.app._close_devnull_files
        # still take effect.
        try:
            from voice_typer.server import app as _app_module

            _app_module._close_devnull_files()
        except Exception:
            log.debug("[CLEANUP] close devnull files failed", exc_info=True)

        # M-22: shut down the event_bus deferred-publish executor.
        # This is the LAST module-level cleanup because earlier steps
        # (bubble worker stop, recorder stop, hotkey stop) can each
        # publish events via event_bus.publish, and an RT-thread
        # publish defers to this executor. Shutting it down here
        # ensures no deferred _deliver tasks outlive the subsystems
        # they deliver TO (e.g. the IPC server's TCP push, which was
        # torn down with the socket close above). Idempotent — safe
        # under the _do_cleanup double-call guard. Already-queued
        # tasks finish on the worker thread (shutdown(wait=False)
        # doesn't cancel them), so in-flight events are not lost.
        # PVT-G5-057: 5s timeout — the executor's worker thread should
        # be idle by this point (subsystems that publish are torn down),
        # but a stuck subscriber could keep it busy.
        try:
            from voice_typer.server import event_bus as _event_bus

            _run_with_timeout(
                "event_bus.shutdown",
                _event_bus.shutdown,
                timeout=5.0,
            )
        except Exception:
            log.debug("[CLEANUP] event_bus.shutdown failed", exc_info=True)

        # PVT-G5-003: ``tray.stop()`` MUST be the LAST step in
        # ``_do_cleanup()``. Previously it was step 13 of 19, which
        # broke the pystray loop on the main thread (blocked in
        # ``tray.run()`` via ``ipc_server.main()``) before the
        # remaining cleanups (sd.stop, electron terminate, PID file
        # clear, CloseHandle, devnull close, event_bus.shutdown) could
        # finish. The daemon TCP worker thread running ``_do_cleanup()``
        # was killed mid-cleanup when the main thread returned. Moving
        # ``tray.stop()`` to the end ensures the main thread stays
        # alive (blocked in ``tray.run()``) until every other cleanup
        # has completed. Idempotent — wrapped in try-except so a second
        # call after the tray is already stopped doesn't propagate.
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
            log.debug("[CLEANUP] tray.stop() failed", exc_info=True)

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
        with self._quit_lock:
            if app._shutting_down:
                log.debug("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
                return

            is_main = threading.current_thread() is threading.main_thread()
            log.info("[SHUTDOWN] Shutting down")
            app._shutting_down = True
            # RACE-020: also set the Event version so executor tasks can check it
            app._shutting_down_event.set()

            # THREAD-REGISTRY: signal all registered threads to stop and
            # join them with their per-thread timeouts. Runs BEFORE
            # _do_cleanup() so the registry's centralized shutdown is the
            # first pass; the per-site methods in _do_cleanup() then run
            # as a safety net. Best-effort — failures here don't prevent
            # the rest of shutdown from running.
            try:
                app._thread_registry.shutdown_all()
            except Exception:
                log.debug(
                    "[SHUTDOWN] thread_registry.shutdown_all() failed",
                    exc_info=True,
                )
            # _quit_lock is released here (end of ``with`` block) BEFORE
            # _do_cleanup() runs. _do_cleanup() has its own
            # ``_cleanup_done`` idempotency guard, so a concurrent quit()
            # that arrives during cleanup will short-circuit at the
            # ``_shutting_down`` check above (now True) rather than
            # block on _quit_lock.

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
        """GT-43: arm a daemon-thread watchdog that calls ``os._exit(0)``
        after ``timeout_s`` seconds if the process is still alive.

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

        The watchdog daemon thread polls in 0.5s increments so it stays
        responsive to interpreter shutdown. Tests can shorten the
        timeout by patching ``SHUTDOWN_WATCHDOG_TIMEOUT_S`` or by
        passing a smaller ``timeout_s`` directly.
        """

        def _watchdog() -> None:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.5, remaining))
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

    # ─── atexit safety net ─────────────────────────────────────────────

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called."""
        app = self._app
        if not app._shutting_down_event.is_set():
            log.warning(
                "[ATEXIT] Process exiting without quit() -- "
                "likely killed externally (console close, task manager, etc.)"
            )

    def _atexit_cleanup(self) -> None:
        """RACE-016: atexit handler for critical cleanup paths.

        Daemon threads can be killed by the interpreter without running
        their finally blocks.  This method is a safety net that ensures
        critical cleanup (volume restore, hotkey release, crash recovery
        flush, history DB flush, recorder stop, PID file + mutex
        release) happens even if the daemon thread's finally block
        didn't run.  It is idempotent — calling it after ``quit()`` or
        ``restart_app()`` is a no-op because both set
        ``_shutting_down = True`` before delegating to ``_do_cleanup()``,
        and ``_do_cleanup()`` itself guards against double-execution
        via the ``_cleanup_done`` flag.

        RW-3: previously this method ran an ad-hoc subset of cleanup
        (volume restore + hotkey stop + crash recovery flush) that
        DIVERGED from ``quit()``'s path.  When the process was killed
        externally (no ``quit()`` / ``restart_app()``), the safety net
        skipped history DB flush, recorder stop, mic watcher shutdown,
        bubble level worker stop, PID file clear, and mutex handle
        close — leaking the same resources that the OLD
        ``restart_app()`` leaked.  It now delegates to
        ``_do_cleanup()`` so the safety net runs the SAME audited
        shutdown path as the regular flow.
        """
        app = self._app
        try:
            if app._shutting_down:
                # quit() or restart_app() already ran (or is running)
                # _do_cleanup(); the _cleanup_done flag inside
                # _do_cleanup() makes a second call a no-op, but we
                # short-circuit here too to avoid the spurious
                # "[ATEXIT] Running emergency cleanup" log line on
                # every intentional shutdown.
                return
            log.info("[ATEXIT] Running emergency cleanup")
            # NOTE: we call ``app._do_cleanup()`` (the delegate on
            # VoiceTyperApp) rather than ``self._do_cleanup()`` (the
            # body on this controller) so test spies that
            # ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still
            # intercept the call — see
            # tests/test_app_cleanup.py::test_atexit_cleanup_never_raises.
            app._do_cleanup()
        except Exception:
            # CR-21: previously this was a bare ``except Exception: pass``
            # which silently swallowed cleanup failures and left no trace
            # in the log — making post-mortem debugging of crash-loop
            # exits effectively impossible. We still never re-raise out
            # of an atexit handler (that would mask the original exit
            # cause and produce confusing tracebacks), but we now log
            # the exception with traceback so operators can see what
            # broke in the emergency cleanup path.
            log.exception("[ATEXIT] _do_cleanup() raised — emergency cleanup incomplete")

    # ─── Signal handlers ───────────────────────────────────────────────

    def _install_signal_handlers(self):
        """Install SIGINT/SIGTERM handlers for graceful shutdown.

        PROD-003: On POSIX there was no signal handler, so Ctrl+C
        would kill the process without running quit() cleanup
        (stop hotkeys, restore volume, release mutex). This method
        installs handlers that trigger quit() on a separate thread
        to avoid deadlock when the main thread is inside the signal
        handler.

        MED-PPP / XCUT-4: the handler body itself is now
        async-signal-safe — it only calls ``Event.set()`` (a thin
        wrapper around ``PyThread_acquire_lock(NOWAIT_LOCK)`` which
        is reentrant and never blocks). A long-lived watcher thread
        (started lazily here) wakes on the event and performs the
        unsafe work (logging + ``threading.Thread(target=quit)``)
        outside the signal context. This eliminates the deadlock
        risk if the signal fires while the main thread holds the
        logging lock.
        """

        # Start the watcher daemon once. ``_signal_watcher_started`` is
        # set BEFORE the thread is created so a signal arriving during
        # ``Thread.__init__`` (which acquires the import lock) won't
        # race us into starting a second watcher.
        if not self._signal_watcher_started:
            self._signal_watcher_started = True
            threading.Thread(
                target=self._signal_watcher_loop,
                name="shutdown-signal-watcher",
                daemon=True,
            ).start()

        def _signal_handler(signum, frame):
            # Async-signal-safe: only record the signum and set the
            # event. ``Event.set()`` is a thin wrapper around a
            # non-blocking lock acquire in CPython and is safe to
            # call from a signal handler. ``int`` assignment is
            # atomic under the GIL. Everything else (logging,
            # thread creation) is deferred to ``_signal_watcher_loop``
            # which runs in a normal thread context.
            self._shutdown_signum = signum
            self._shutdown_signal_event.set()

        # PVT-G5-014: also register SIGHUP on POSIX so terminal close
        # / SSH disconnect triggers graceful shutdown (default action
        # would terminate immediately without running atexit). Filtered
        # via ``hasattr`` because Windows doesn't define SIGHUP. The
        # ``contextlib.suppress`` already handles the case where the
        # signal can't be installed (e.g. not in the main thread).
        _posix_signals = [signal.SIGINT, signal.SIGTERM]
        _sighup = getattr(signal, "SIGHUP", None)
        if _sighup is not None:
            _posix_signals.append(_sighup)
        for sig in _posix_signals:
            with contextlib.suppress(OSError, ValueError):
                # SIGTERM not available on Windows; signal.signal can
                # raise if not in the main thread
                signal.signal(sig, _signal_handler)

    def _signal_watcher_loop(self) -> None:
        """Watcher thread for the POSIX signal handlers.

        MED-PPP / XCUT-4: polls ``_shutdown_signal_event`` (1s
        timeout) and, when set, performs the unsafe work that the
        signal handler itself must not do — logging the signal name
        and spawning the quit() worker thread. Runs as a daemon so
        it never blocks process exit; ``quit()`` is idempotent so a
        duplicate signal that re-triggers the watcher is harmless.
        """
        # Block indefinitely until the event is set. ``wait(timeout=1)``
        # (rather than ``wait()`` with no timeout) keeps the thread
        # responsive to interpreter shutdown on platforms where the
        # underlying lock isn't released automatically — a one-second
        # poll loop is cheap and matches the convention used by the
        # other daemon watchers in this codebase.
        while not self._shutdown_signal_event.wait(timeout=1.0):
            pass
        # Outside the signal context — safe to use logging and threading.
        signum = self._shutdown_signum
        try:
            sig_name = signal.Signals(signum).name if signum is not None else "UNKNOWN"
            log.info("[SIGNAL] %s received, shutting down gracefully", sig_name)
        except Exception:
            # Never let a logging failure here prevent shutdown —
            # the signal was delivered and we must still call quit().
            pass
        # RACE-016: daemon=True is acceptable because quit() is
        # idempotent and the atexit handler covers critical cleanup.
        try:
            threading.Thread(target=self.quit, daemon=True).start()
        except Exception:
            log.exception("[SIGNAL] failed to spawn quit() worker thread")

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure.

        ARCH-046: skip when running under ``pythonw.exe`` — there's no
        console attached, so SetConsoleCtrlHandler is a no-op that
        spews "no console" warnings in the log.
        """
        # Look up the platform helper from the app module at call time
        # so tests that monkeypatch voice_typer.server.app.is_windows
        # still take effect (mirrors the SettingsController convention).
        from voice_typer.server import app as _app_module

        if not _app_module.is_windows():
            return
        app = self._app
        # ARCH-046: detect pythonw.exe (no console) and skip install.
        exe_name = Path(sys.executable).name.lower()
        if exe_name == "pythonw.exe":
            log.debug("[WIN32] pythonw.exe detected — skipping console control handler")
            return

        try:
            import ctypes
            from ctypes import wintypes

            handler_routine = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            app._console_handler = handler_routine(self._win32_console_handler)
            app._kernel32 = ctypes.windll.kernel32
            kernel32 = app._kernel32
            kernel32.SetConsoleCtrlHandler.argtypes = [handler_routine, wintypes.BOOL]
            kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
            kernel32.FreeConsole.argtypes = []
            kernel32.FreeConsole.restype = wintypes.BOOL

            result = kernel32.SetConsoleCtrlHandler(app._console_handler, True)
            if result:
                log.info("[WIN32] Console control handler installed")
            else:
                log.warning("[WIN32] SetConsoleCtrlHandler failed")
        except Exception:
            log.exception("[WIN32] Failed to install console control handler")

    def _win32_console_handler(self, ctrl_type):
        """Callback for Windows console control events."""
        app = self._app
        ctrl_c_event = 0
        ctrl_break_event = 1
        ctrl_close_event = 2
        ctrl_logoff_event = 5
        ctrl_shutdown_event = 6

        if ctrl_type == ctrl_close_event:
            log.info("[WIN32] Console window closing -- keeping process alive (tray app survives)")
            try:
                app._kernel32.FreeConsole()
                # PERF-004: reuse the existing devnull object instead of
                # opening a new one on every ctrl_close_event (would hit
                # Windows' 10,000 handle cap after ~250 RDP logout cycles).
                if getattr(app, "_devnull", None) is None or app._devnull.closed:
                    app._devnull = open(os.devnull, "w")  # noqa: SIM115
                    # Look up _register_devnull_file dynamically from the
                    # app module so tests that monkeypatch
                    # voice_typer.server.app._register_devnull_file
                    # still take effect.
                    from voice_typer.server import app as _app_module

                    _app_module._register_devnull_file(app._devnull)
                sys.stdout = app._devnull
                sys.stderr = app._devnull
                log.info("[WIN32] Detached from console (FreeConsole)")
            except Exception:
                log.warning("[WIN32] FreeConsole() failed")
            return True

        if ctrl_type in (ctrl_logoff_event, ctrl_shutdown_event):
            log.info("[WIN32] System event %d received, shutting down", ctrl_type)
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        if ctrl_type in (ctrl_c_event, ctrl_break_event):
            log.info("[WIN32] Ctrl+C received, shutting down")
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        return False
