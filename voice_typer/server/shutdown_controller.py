"""god-class decomposition: ShutdownController — extracted from VoiceTyperApp.

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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

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
    join_leaked_workers,
)
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


# the general-purpose thread-join timeout utilities
# (``_run_with_timeout``, ``_run_parallel_with_timeout``, the
# ``TIMEOUT`` sentinel, and ``SHUTDOWN_WATCHDOG_TIMEOUT_S``) now live in
# :mod:`voice_typer.server._timeout_utils`. They're re-exported here
# (via the ``from ... import`` above) so existing callers — in
# particular tests like ``tests/test_shutdown_controller_de.py`` that
# do ``from voice_typer.server.shutdown_controller import _run_with_timeout,
# _TIMEOUT, SHUTDOWN_WATCHDOG_TIMEOUT_S`` — continue to work unchanged.


# ─── ShutdownPlan: declarative teardown ordering contract ──────────
#
# ``_do_cleanup`` previously had an implicit ordering contract
# encoded only as the linear position of try/except blocks in a ~1000-
# line method body. Adding a new subsystem teardown at the wrong
# position could race (notably when the new teardown touches the same
# OS resource as an upstream call — e.g. PortAudio, the Win32 mutex,
# the Electron subprocess). The "shutdown barrier" pattern
# (skip a downstream call when its upstream dependency timed out) was
# applied inconsistently — only the ``recorder.stop`` →
# ``shutdown_mic_watcher`` pair had it inline.
#
# The dataclasses below make the ordering contract EXPLICIT and
# machine-checkable. ``_do_cleanup`` builds two ``ShutdownPlan``
# instances (sequenced + parallel) and hands them to ``_run_plan``,
# which:
#   1. runs each step via ``_run_with_timeout`` (sequenced) or
#      ``_run_parallel_with_timeout`` (parallel);
#   2. records which step names timed out; and
#   3. for each step, checks ``depends_on`` + ``skip_if_dep_timed_out``
#      — if the named dependency timed out and the step opts in to
#      skip-on-dep-timeout, the step is skipped (the barrier applied
#      uniformly).
#
# The barrier is currently expressed for the PortAudio resource pair
# (``teardown_sounddevice`` depends on ``teardown_recorder`` and skips
# when the recorder timed out). The same pattern can be extended to
# future resource pairs without modifying ``_run_plan``.


@dataclass(frozen=True)
class ShutdownStep:
    """One declarative teardown step.

    Parameters
    ----------
    name:
        Unique identifier within the owning :class:`ShutdownPlan`.
        Used as the ``depends_on`` target by other steps.
    func:
        The callable to invoke (typically a bound ``_teardown_*``
        method on :class:`ShutdownController`).
    timeout:
        Per-step hard timeout in seconds. When the step does not
        finish in time, ``_run_with_timeout`` returns :data:`TIMEOUT`
        and the worker thread is leaked as a daemon (registered for
        best-effort join via ``join_leaked_workers``).
    depends_on:
        Name of another step in the SAME plan or in a previously-run
        plan whose completion this step logically depends on. Used
        together with ``skip_if_dep_timed_out`` to express the
        barrier pattern. ``None`` (default) means no dependency.
    skip_if_dep_timed_out:
        When True, ``_run_plan`` skips this step if the named
        ``depends_on`` step timed out. This is the barrier: a
        downstream call that touches the same OS resource as an
        upstream call (e.g. ``sd.stop()`` after a leaked
        ``recorder.stop()``) MUST be skipped because the upstream
        worker is still accessing the resource and a concurrent
        downstream call can deadlock (notably on WASAPI PortAudio
        backends where the stream lock is held).
    """

    name: str
    func: Callable[[], object]
    timeout: float
    depends_on: str | None = None
    skip_if_dep_timed_out: bool = False


@dataclass(frozen=True)
class ShutdownPlan:
    """An ordered collection of :class:`ShutdownStep` instances.

    The ``phase`` field selects the execution strategy:

    * ``"sequenced"`` — steps run one at a time, each wrapped in
      ``_run_with_timeout``. A slow step does not block subsequent
      steps past its own timeout. Used for teardowns that MUST
      complete in order (e.g. ``recorder.stop`` must finish before
      ``history_db.flush`` so the transcription thread's final write
      is enqueued before the DB is closed).
    * ``"parallel"`` — steps run concurrently via
      ``_run_parallel_with_timeout`` (a bounded ``ThreadPoolExecutor``
      with max_workers=8). Used for teardowns that touch disjoint
      resources and can race safely.

    The ``_run_plan`` driver returns the set of step names that
    timed out (so a subsequent plan can apply ``skip_if_dep_timed_out``
    barriers against them).
    """

    phase: Literal["sequenced", "parallel"]
    steps: tuple[ShutdownStep, ...] = field(default_factory=tuple)


class ShutdownController:
    """Owns the shutdown / cleanup lifecycle of ``VoiceTyperApp``.

     Phase 7: extracted from ``VoiceTyperApp``. The app passes itself
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

    # AC-87: the ordered list of every ``_teardown_*`` phase method that
    # ``_do_cleanup`` invokes. The first FOUR entries are the sequenced
    # critical phase (timers/recording → recorder → history_db →
    # crash_recovery) whose flush-bearing helpers
    # (``_teardown_history_db`` / ``_teardown_crash_recovery``) MUST run
    # before the hotkey / level_monitor / event_bus teardowns begin
    # (flush-before-teardown guarantee — see
    # ``tests/regressions/electron_test.py::TestShutdownControllerPhasesContract::
    # test_flush_bearing_phases_run_first``). The remaining 11 entries
    # are the parallel batch. The list is inspectable at runtime so
    # tests (and operators) can pin the decomposition.
    _PARALLEL_TEARDOWN_PHASE_NAMES: tuple[str, ...] = (
        "_teardown_timers_and_recording",
        "_teardown_recorder",
        "_teardown_history_db",
        "_teardown_crash_recovery",
        "_teardown_asr_models",
        "_teardown_restore_volume",
        "_teardown_waveform_wiring",
        "_teardown_sounddevice",
        "_teardown_pid_file",
        "_teardown_mutex_handle",
        "_teardown_devnull_files",
        "_teardown_level_monitor",
        "_teardown_hotkeys",
        "_teardown_electron",
        "_teardown_event_bus",
    )

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
        # dedicated lock for the check-then-set-then-shutdown_all
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

        # dedicated lock for the ``_electron_pid`` read-terminate-clear
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

        # shared state between ``_teardown_recorder`` and
        # ``_teardown_sounddevice``. When ``recorder.stop()`` (or
        # ``discard()``) times out, the leaked worker thread is still
        # accessing the PortAudio stream; a subsequent ``sd.stop()`` call
        # can deadlock on PortAudio backends (notably WASAPI) where the
        # stream lock is held. ``_teardown_recorder`` sets
        # ``_recorder_force_closed = True`` and signals
        # ``_recorder_teardown_done``; ``_teardown_sounddevice`` waits for
        # the event then checks the flag, skipping ``sd.stop()`` when set.
        # Both helpers run in the  parallel batch, so the Event is the
        # synchronization primitive that gives ``_teardown_sounddevice`` a
        # happens-before guarantee on the flag read.
        self._recorder_teardown_done: threading.Event = threading.Event()
        self._recorder_force_closed: bool = False

    # ─── Shared cleanup body ───────────────────────────────────────────

    def _do_cleanup(self) -> None:
        """shared cleanup body used by ``quit()``, ``restart_app()``,
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

        Prior to , ``restart_app()`` did only a PARTIAL cleanup
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

        ``_do_cleanup`` ALSO drains / cancels the WS dispatch
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
        # guard the check-then-set on ``_cleanup_done`` with
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

        #  reset the shared state between ``_teardown_recorder``
        # and ``_teardown_sounddevice`` for THIS cleanup pass. Both helpers
        # run in the parallel batch below; ``_teardown_sounddevice`` waits
        # on ``_recorder_teardown_done`` before reading
        # ``_recorder_force_closed`` so the flag has a happens-before
        # guarantee even under concurrent scheduling.
        self._recorder_teardown_done.clear()
        self._recorder_force_closed = False

        # Overall deadline for the entire ``_do_cleanup`` body. The
        # cumulative worst-case pre-deadline was 77s (sequenced phase:
        # timers 10s + recorder 15s + history_db 15s + crash_recovery
        # 10s; parallel batch up to 10s; bookends). history_db +
        # crash_recovery stay in the sequenced phase (NOT a parallel
        # sub-batch) so the recorder's transcription thread is joined
        # before the DB flush, and the crash-recovery snapshot drains
        # after — see the sequenced-phase rationale below. The 20s
        # deadline is checked before each phase; when the remaining
        # budget drops below 5s, non-critical teardowns are SKIPPED and
        # only critical flushes (history_db, crash_recovery,
        # recorder.stop, mutex, PID file) + the late ``tray.stop``
        # bookend run. Skipped teardowns are logged at WARNING.
        _shutdown_deadline: float = time.monotonic() + 20.0
        _shutdown_skipped: list[str] = []

        def _shutdown_remaining() -> float:
            return max(0.0, _shutdown_deadline - time.monotonic())

        def _shutdown_deadline_near() -> bool:
            return _shutdown_remaining() < 5.0

        # ── Early bookend (sequential) ────────────────────────────────
        #  (partial): stop the IPC server EARLY so inbound
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

        # drain / cancel in-flight WS dispatch requests BEFORE
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

                # explicit ``threading.Event`` coordination between
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
                            "[SHUTDOWN] WS dispatch drain Event did not "
                            "fire in 2s — %s in-flight handler(s) may race DB "
                            "teardown; proceeding with cleanup (the in-flight "
                            "write may silently fail)",
                            in_flight,
                        )
        except Exception:
            log.debug("[SHUTDOWN] WS dispatch pool shutdown failed", exc_info=True)

        # ── Sequenced critical teardowns ────────────────────────────
        # The transcription thread (spawned by ``recorder.stop()``) runs
        # ASR inference and writes its result to ``history_db`` via
        # fire-and-forget ``add_transcription()``. The ASR model the
        # thread is mid-inference on must NOT be unloaded, and the DB
        # must NOT be closed, until the thread has finished. Running
        # ``_teardown_recorder``, ``_teardown_history_db``, and
        # ``_teardown_asr_models`` concurrently in a single parallel
        # wave races the thread's inference + DB write, risking:
        #   - a segfault or undefined torch state when the ASR model is
        #     unloaded under the transcription thread
        #   - silent drop of the user's last utterance when the DB is
        #     closed before the thread's ``add_transcription()`` fires
        #
        # The sequenced phase runs the dependent teardowns IN ORDER, each
        # wrapped in ``_run_with_timeout`` so a stuck helper doesn't
        # block the rest of cleanup:
        #   1. ``_teardown_timers_and_recording`` — cancel timers, pop
        #      the streaming session, signal cancel.
        #   2. ``_teardown_recorder`` — ``recorder.stop()`` + join the
        #      transcription thread (3s timeout). Sets
        #      ``_recorder_teardown_done`` so the downstream
        #      ``_teardown_sounddevice`` (in the parallel batch) gets a
        #      happens-before guarantee on ``_recorder_force_closed``.
        #   3. ``_teardown_history_db`` — ``flush()`` + ``close()`` to
        #      drain pending writes (including the one the transcription
        #      thread just enqueued).
        #   4. ``_teardown_crash_recovery`` — ``flush()`` + ``shutdown()``
        #      to drain pending crash-recovery snapshots.
        #
        # ``_teardown_asr_models`` stays in the parallel batch (below):
        # the sequenced phase completes BEFORE the parallel batch starts,
        # so the transcription thread is already joined by the time the
        # ASR model is unloaded. This preserves the parallel speedup for
        # CUDA teardown (which is independent of the DB close).
        #
        # The sequenced phase is now declared as a list of
        # 5-element tuples ``(name, func, timeout, depends_on,
        # skip_if_dep_timed_out)`` and executed by :meth:`_run_plan`
        # via the :class:`ShutdownPlan` dataclass. The driver returns
        # the set of step names that timed out, which is threaded into
        # the parallel plan below so the barrier
        # (``skip_if_dep_timed_out``) can skip downstream steps whose
        # upstream resource is still being accessed by a leaked worker.
        # The list-of-tuples form (rather than direct ``ShutdownStep``
        # construction) is kept so source-text contract tests
        # (``tests/test_shutdown_fast_path.py::TestSequentialHistoryAndCrashRecovery``
        # and ``tests/test_shutdown_asr_unload.py::TestTeardownAsrModelsContract``)
        # continue to find the sequenced / parallel symbols + the
        # ``("teardown_<name>",`` entry pattern.
        #
        # Overall-deadline skip: when the 20s deadline is near (< 5s
        # remaining) at the start of the sequenced phase,
        # ``teardown_timers_and_recording`` is SKIPPED (non-critical).
        # ``teardown_recorder``, ``teardown_history_db``, and
        # ``teardown_crash_recovery`` ALWAYS run — they contain critical
        # flushes.
        sequenced_items: list[tuple[str, object, float, str | None, bool]] = []
        if _shutdown_deadline_near():
            log.warning(
                "[SHUTDOWN] deadline near (%.1fs remaining) at sequenced "
                "phase entry — skipping teardown_timers_and_recording (non-critical)",
                _shutdown_remaining(),
            )
            _shutdown_skipped.append("teardown_timers_and_recording")
        else:
            sequenced_items.append(
                ("teardown_timers_and_recording", self._teardown_timers_and_recording, 10.0, None, False),
            )
        sequenced_items.append(
            ("teardown_recorder", self._teardown_recorder, 15.0, None, False),
        )
        sequenced_items.append(
            ("teardown_history_db", self._teardown_history_db, 15.0, None, False),
        )
        sequenced_items.append(
            ("teardown_crash_recovery", self._teardown_crash_recovery, 10.0, None, False),
        )
        sequenced_plan = ShutdownPlan(
            phase="sequenced",
            steps=tuple(ShutdownStep(*item) for item in sequenced_items),
        )
        _timed_out = self._run_plan(sequenced_plan, frozenset())

        # ── Parallel batch: 11 independent teardown helpers ─────────
        # Each helper is isolated — a failure in one does NOT propagate
        # (``_run_parallel_with_timeout`` captures per-call exceptions).
        # Shared 10s deadline: each helper is wrapped in
        # ``_run_with_timeout(..., timeout=10.0)`` by
        # ``_run_parallel_with_timeout``; if a helper exceeds 10s, the
        # worker thread is leaked as a daemon and the orchestrator moves
        # on. The bookends (early WS drain + sequenced critical phase
        # above + late ``tray.stop`` below) remain sequential.
        #
        # ``_teardown_asr_models`` is placed FIRST in the parallel batch
        # so the (potentially slow) CUDA context teardown starts as
        # early as possible. It runs AFTER the sequenced critical phase
        # (which joins the transcription thread), so the ASR model is
        # only unloaded once the thread's inference has completed — no
        # race between ``registry.unload()`` and mid-inference torch
        # state.
        #
        # Barrier: ``teardown_sounddevice`` declares
        # ``depends_on="teardown_recorder"`` + ``skip_if_dep_timed_out=
        # True``. When the recorder's PortAudio stream failed to close
        # in time, the leaked worker is still accessing the stream and
        # a concurrent ``sd.stop()`` can deadlock on WASAPI backends
        # (stream lock held). The ``_run_plan`` driver skips the step
        # when the dependency is in ``_timed_out``. The existing
        # ``_recorder_teardown_done`` Event inside ``teardown_sounddevice``
        # is kept as a defense-in-depth happens-before guard for the
        # ``_recorder_force_closed`` flag read (the dataclass barrier
        # short-circuits before the Event wait when the recorder timed
        # out; the Event still fires for the success path so the flag
        # read is ordered).
        #
        # Overall-deadline skip: when the 20s deadline is near (< 5s
        # remaining), skip NON-CRITICAL parallel helpers. The critical
        # set is ``{teardown_pid_file, teardown_mutex_handle}`` — they
        # release the single-instance PID file + mutex so the next
        # launch isn't blocked. Everything else is non-critical under a
        # tight deadline — the OS will reap those resources at process
        # exit.
        _shutdown_critical_parallel: frozenset[str] = frozenset({"teardown_pid_file", "teardown_mutex_handle"})
        all_parallel_items: list[tuple[str, object, float, str | None, bool]] = [
            ("teardown_asr_models", self._teardown_asr_models, 10.0, None, False),
            ("teardown_restore_volume", self._teardown_restore_volume, 10.0, None, False),
            ("teardown_waveform_wiring", self._teardown_waveform_wiring, 10.0, None, False),
            ("teardown_sounddevice", self._teardown_sounddevice, 10.0, "teardown_recorder", True),
            ("teardown_pid_file", self._teardown_pid_file, 10.0, None, False),
            ("teardown_mutex_handle", self._teardown_mutex_handle, 10.0, None, False),
            ("teardown_devnull_files", self._teardown_devnull_files, 10.0, None, False),
            ("teardown_level_monitor", self._teardown_level_monitor, 10.0, None, False),
            ("teardown_hotkeys", self._teardown_hotkeys, 10.0, None, False),
            ("teardown_electron", self._teardown_electron, 10.0, None, False),
            ("teardown_event_bus", self._teardown_event_bus, 10.0, None, False),
        ]
        parallel_items: list[tuple[str, object, float, str | None, bool]] = []
        for _desc, _func, _timeout, _dep, _skip in all_parallel_items:
            if _shutdown_deadline_near() and _desc not in _shutdown_critical_parallel:
                log.warning(
                    "[SHUTDOWN] deadline near (%.1fs remaining) — skipping non-critical %s",
                    _shutdown_remaining(),
                    _desc,
                )
                _shutdown_skipped.append(_desc)
                continue
            parallel_items.append((_desc, _func, _timeout, _dep, _skip))
        # Guard against empty parallel_items (defensive — critical set
        # ensures at least 2 items always run).
        if parallel_items:
            parallel_plan = ShutdownPlan(
                phase="parallel",
                steps=tuple(ShutdownStep(*item) for item in parallel_items),
            )
            self._run_plan(parallel_plan, _timed_out)

        # Overall-deadline summary: emit a single WARNING listing every
        # teardown that was skipped due to the 20s deadline.
        if _shutdown_skipped:
            log.warning(
                "[SHUTDOWN] skipped %d teardown(s) due to 20s deadline: %s",
                len(_shutdown_skipped),
                ", ".join(_shutdown_skipped),
            )

        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # ── Late bookend (sequential) ────────────────────────────────
        # ``tray.stop()`` MUST be the LAST step in
        # ``_do_cleanup()``. Previously it was step 13 of 19, which
        # broke the pystray loop on the main thread (blocked in
        # ``tray.run()`` via ``ipc_server.main()``) before the
        # remaining cleanups could finish. Moving ``tray.stop()`` to
        # the end ensures the main thread stays alive (blocked in
        # ``tray.run()``) until every other cleanup has completed.
        # Idempotent — wrapped in try-except so a second call after
        # the tray is already stopped doesn't propagate.
        # 5s timeout.
        #
        #  if ``tray.stop()`` times out AND we're on a
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
        # when ``tray.stop()`` RAISES (not times out), the
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
                    "[SHUTDOWN] tray.stop() timed out on non-main thread "
                    "— calling os._exit(0) to unblock the main thread parked in "
                    "tray.run() (all subsystem cleanup already completed)"
                )
                os._exit(0)
        except Exception:
            log.error("[CLEANUP] tray.stop() failed", exc_info=True)

    def _run_plan(
        self,
        plan: ShutdownPlan,
        prior_timed_out: frozenset[str],
    ) -> frozenset[str]:
        """Execute a :class:`ShutdownPlan` and return the set of step
        names that timed out.

        This driver replaces the inline ``for ... in items`` loops
        that previously lived in ``_do_cleanup``. Behaviour preserved:

        * Sequenced phase: each step is wrapped in ``_run_with_timeout``;
          per-step failures (BaseException) are logged at DEBUG and
          captured into the ``degraded`` list; TIMEOUT results are
          logged at WARNING. A summary WARNING fires after the loop if
          any step degraded.
        * Parallel phase: steps are handed to
          ``_run_parallel_with_timeout`` (bounded ThreadPoolExecutor,
          max_workers=8); per-step results are inspected for BaseException
          (logged at DEBUG) or TIMEOUT (logged at WARNING); a summary
          WARNING fires if any step degraded.

        Barrier: for each step, if ``depends_on`` is set and the
        named dependency is in ``prior_timed_out`` (the union of
        upstream-plan timed-out steps and any same-plan timed-out steps
        observed so far) and ``skip_if_dep_timed_out`` is True, the step
        is SKIPPED (not invoked). The skip is logged at WARNING so
        operators see the barrier fire. Without this barrier, a
        downstream call that touches the same OS resource as a leaked
        upstream worker (e.g. ``sd.stop()`` after a timed-out
        ``recorder.stop()``) can deadlock on backends like WASAPI where
        the stream lock is held.

        Parameters
        ----------
        plan:
            The :class:`ShutdownPlan` to execute.
        prior_timed_out:
            Step names that timed out in a previously-run plan (e.g. the
            sequenced plan's timed-out steps are passed in when running
            the parallel plan, so parallel steps with
            ``depends_on="teardown_recorder"`` can apply the
            barrier).

        Returns
        -------
        frozenset[str]
            The union of ``prior_timed_out`` and any step names in THIS
            plan that timed out. Pass this to the next ``_run_plan``
            call so cross-plan barriers work.
        """
        if not plan.steps:
            return prior_timed_out

        timed_out: set[str] = set(prior_timed_out)
        degraded: list[str] = []

        if plan.phase == "sequenced":
            for step in plan.steps:
                if step.depends_on is not None and step.skip_if_dep_timed_out and step.depends_on in timed_out:
                    log.warning(
                        "[SHUTDOWN] skipping %s because dependency %s "
                        "timed out (barrier — downstream call "
                        "touches the same OS resource as the leaked "
                        "upstream worker)",
                        step.name,
                        step.depends_on,
                    )
                    degraded.append(f"{step.name} (skipped: dep {step.depends_on} timed out)")
                    continue
                try:
                    result = _run_with_timeout(step.name, step.func, timeout=step.timeout)
                    if result is TIMEOUT:
                        log.warning(
                            "[SHUTDOWN] %s timed out — worker thread leaked as daemon",
                            step.name,
                        )
                        timed_out.add(step.name)
                        degraded.append(f"{step.name} (timeout)")
                except BaseException as exc:  # noqa: BLE001 — per-step isolation
                    log.debug("[SHUTDOWN] %s raised: %r", step.name, exc)
                    degraded.append(f"{step.name} (raised: {exc!r})")
        elif plan.phase == "parallel":
            # Barrier (pre-flight skip): for each step, if its
            # declared ``depends_on`` is in ``timed_out`` (which is the
            # union of ``prior_timed_out`` from earlier plans and any
            # same-plan timed-out steps observed so far — though the
            # latter is rare in the parallel phase because steps run
            # concurrently), and the step opted in to
            # ``skip_if_dep_timed_out``, SKIP the step (do not submit
            # it to the pool). This is the barrier applied
            # uniformly: a downstream call that touches the same OS
            # resource as a leaked upstream worker MUST be skipped
            # because the upstream worker is still accessing the
            # resource and a concurrent downstream call can deadlock
            # (notably on WASAPI PortAudio backends where the stream
            # lock is held).
            #
            # The pre-flight skip is the canonical barrier
            # location for cross-plan dependencies (e.g. the parallel
            # ``teardown_sounddevice`` step depending on the sequenced
            # ``teardown_recorder`` step). Per-step in-body barriers
            # (e.g. the ``_recorder_teardown_done`` Event inside
            # ``teardown_sounddevice``) remain as defense-in-depth for
            # the case where the dependency SUCCEEDED but the per-step
            # body still needs to coordinate with the upstream step's
            # published state (e.g. the ``_recorder_force_closed``
            # flag).
            items: list[tuple[str, object, float]] = []
            for step in plan.steps:
                if step.depends_on is not None and step.skip_if_dep_timed_out and step.depends_on in timed_out:
                    log.warning(
                        "[SHUTDOWN] skipping %s because dependency %s "
                        "timed out (barrier — downstream call "
                        "touches the same OS resource as the leaked "
                        "upstream worker)",
                        step.name,
                        step.depends_on,
                    )
                    degraded.append(f"{step.name} (skipped: dep {step.depends_on} timed out)")
                    continue
                items.append((step.name, step.func, step.timeout))
            results = _run_parallel_with_timeout(items)
            for desc, result in results:
                if isinstance(result, BaseException):
                    log.debug("[SHUTDOWN] %s raised: %r", desc, result)
                    degraded.append(f"{desc} (raised: {result!r})")
                elif result is TIMEOUT:
                    log.warning(
                        "[SHUTDOWN] %s timed out — worker thread leaked as daemon",
                        desc,
                    )
                    timed_out.add(desc)
                    degraded.append(f"{desc} (timeout)")
        else:  # pragma: no cover — defensive; Literal type guards this
            log.error("[SHUTDOWN] unknown plan phase: %r", plan.phase)

        if degraded:
            log.warning(
                "[SHUTDOWN] %d/%d %s teardown helpers degraded: %s",
                len(degraded),
                len(plan.steps),
                plan.phase,
                ", ".join(degraded),
            )

        return frozenset(timed_out)

    def _do_fast_cleanup(self) -> None:
        """critical-only cleanup for Windows logoff/shutdown.

        Windows CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT give the process
        ~5 seconds before the OS forcibly terminates it. The full
        :meth:`_do_cleanup` body has a cumulative worst-case of ~85s.
        This fast path runs ONLY critical-resource cleanup with 1s
        timeouts each, targeting <3s total.

        Critical path: crash_recovery.flush, history_db.flush,
        recorder.stop, _clear_backend_pid_file, mutex CloseHandle/release.
        Non-critical steps (tray.stop, Electron terminate, hotkey stop,
        level_monitor, waveform worker, event_bus, devnull) are SKIPPED.

        UNCONDITIONAL FLUSHES: the critical cleanup steps below run
        EVERY invocation — they are NOT gated by ``_cleanup_done``. The
        writes (``crash_recovery.flush``, ``history_db.flush``) are
        idempotent and bounded by per-step 1s timeouts; running them
        twice is safe. The previous ``if not already_done:`` gate
        created a false positive: if a normal ``quit()`` was in flight
        (had set ``_cleanup_done = True`` at the start of
        ``_do_cleanup``) when Windows logoff fired ``_do_fast_cleanup``,
        the fast path skipped its own critical flushes — losing pending
        history DB writes and crash-recovery snapshots. Both cleanup
        paths skipped the critical writes (the slow one was killed by
        ``os._exit(0)`` mid-flight; the fast one short-circuited). The
        fix: run the critical flushes unconditionally on every
        invocation, then ``os._exit(0)``.

        The ``_cleanup_done`` flag is STILL set (under ``_quit_lock``)
        so a subsequent ``_do_cleanup`` call short-circuits — but it no
        longer gates the fast-cleanup body. The actual
        ctrl_logoff/shutdown routing lives in
        :func:`voice_typer.server.signal_handlers.win32_console_handler`;
        the cross-file change to route logoff/shutdown to this method
        instead of ``controller.quit()`` is tracked under separate
        cover.

        This method ends with ``os._exit(0)`` — bypassing atexit
        handlers is correct here because (a) the OS is force-killing us
        within ~5s, so orderly atexit cleanup would race the OS deadline
        and lose, and (b) the critical cleanup above has already run
        (and is idempotent, so running it twice under a concurrent
        ``_do_cleanup`` is safe). The ``os._exit(0)`` MUST fire even
        when ``_cleanup_done`` was already True on entry — the Win32
        console-control callback must NOT return ``True`` to the OS
        without exiting, otherwise the OS will re-evaluate us with a
        CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT escalation. Tests that
        invoke this method directly MUST monkey-patch ``os._exit`` (see
        ``tests/test_shutdown_xz_r17_fixes.py``'s autouse
        ``_stub_os_exit`` fixture).
        """
        app = self._app
        # Set ``_cleanup_done`` so a concurrent / subsequent
        # ``_do_cleanup`` call short-circuits. The flag does NOT gate
        # the critical flushes below — they run unconditionally so a
        # quit-during-logoff doesn't lose the user's last write
        # (the writes are idempotent; running them twice is safe).
        with self._quit_lock:
            app._cleanup_done = True

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

        # 6. Restore system volume if it was ducked during recording +
        #    clear the duck crash-recovery marker.
        # The normal ``_do_cleanup`` path runs ``_teardown_restore_volume``
        # (which calls ``app._restore_volume(fade_ms=0)`` via
        # ``_run_with_timeout(timeout=5.0)``). The fast path was missing
        # this, so a quit-during-recording on Windows logoff/shutdown
        # left the system volume ducked at 25%. ``_restore_volume`` is
        # wrapped in ``_run_with_timeout`` (1s — fast-path budget) and
        # BOTH the restore and the crash-recovery ``clear()`` are wrapped
        # in ``contextlib.suppress(Exception)`` so fast-cleanup NEVER
        # raises (the OS is killing us within ~5s; raising would skip
        # the trailing ``os._exit(0)`` and let the Win32 callback return
        # True without exiting).
        with contextlib.suppress(Exception):
            _restore_result = _run_with_timeout(
                "restore_volume (fast-path)",
                lambda: app._restore_volume(fade_ms=0),
                timeout=1.0,
            )
            if _restore_result is TIMEOUT:
                log.warning("[SHUTDOWN] restore_volume timed out in fast-path — system volume may remain ducked")
        with contextlib.suppress(Exception):
            app._duck_crash_recovery.clear()

        log.warning("[SHUTDOWN] XZ-R17-06: fast cleanup path complete")

        # Bypass atexit — the OS is killing us (Windows logoff/shutdown
        # gives ~5s). Orderly atexit cleanup would race the OS force-kill
        # and lose. Safe because we've already run the critical flushes
        # above (idempotent — safe even if a concurrent ``_do_cleanup``
        # is also mid-flight). The ``os._exit(0)`` MUST fire on every
        # invocation so the Win32 callback does not return ``True`` to
        # the OS without exiting. ``os._exit`` is async-signal-safe per
        # POSIX, which is the correct primitive for a console-control
        # callback context.
        os._exit(0)

    # ───  parallel teardown helpers ──────────────────────────────
    # ───  parallel teardown helpers ──────────────────────────────
    #
    # Each helper is a thin delegate that calls the standalone
    # function in :mod:`voice_typer.server.shutdown.teardowns`
    # (extracted in Phase 4.5 / OI-36 so the controller class body
    # shrinks to orchestration only). The delegate indirection is
    # kept so:
    #
    #   * tests that ``monkeypatch.setattr(controller, "_teardown_X",
    #     spy)`` still intercept the call (see
    #     ``tests/test_shutdown_parallel.py``); and
    #   * the sequenced-phase list and parallel-batch list in
    #     ``_do_cleanup`` keep referencing ``self._teardown_X`` (the
    #     callable attribute must remain on the controller instance).
    #
    # The standalone functions all take ``controller`` as their first
    # positional argument so they can read ``controller._app`` and
    # (in two cases) the shared synchronization state
    # (``_recorder_teardown_done`` / ``_recorder_force_closed`` /
    # ``_electron_pid_lock``) initialized in ``__init__``.

    def _teardown_timers_and_recording(self) -> None:
        """cancel pending timers + drain in-flight timer threads,
        stop the recording watchdog, and atomically pop the streaming
        session.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.timers_and_recording.teardown_timers_and_recording`.
        """
        from voice_typer.server.shutdown.teardowns.timers_and_recording import (
            teardown_timers_and_recording,
        )

        teardown_timers_and_recording(self)

    def _teardown_recorder(self) -> None:
        """stop the PortAudio stream (recorder.stop / discard) and
        the mic watcher; join the transcription thread.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.recorder.teardown_recorder`.
        Publishes ``_recorder_force_closed`` / ``_recorder_teardown_done``
        on this controller for the sounddevice helper's happens-before
        guarantee.
        """
        from voice_typer.server.shutdown.teardowns.recorder import (
            teardown_recorder,
        )

        teardown_recorder(self)

    def _teardown_level_monitor(self) -> None:
        """stop the level_monitor module's PortAudio InputStream +
        worker thread.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.level_monitor.teardown_level_monitor`.
        """
        from voice_typer.server.shutdown.teardowns.level_monitor import (
            teardown_level_monitor,
        )

        teardown_level_monitor(self)

    def _teardown_restore_volume(self) -> None:
        """restore OS volume if it was ducked when the app quit.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.volume.teardown_restore_volume`.
        """
        from voice_typer.server.shutdown.teardowns.volume import (
            teardown_restore_volume,
        )

        teardown_restore_volume(self)

    def _teardown_hotkeys(self) -> None:
        """stop all three hotkey backends (dictation / ESC / repaste)
        in a nested parallel batch.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.hotkeys.teardown_hotkeys`.
        """
        from voice_typer.server.shutdown.teardowns.hotkeys import (
            teardown_hotkeys,
        )

        teardown_hotkeys(self)

    def _teardown_crash_recovery(self) -> None:
        """flush pending crash-recovery writes + shutdown the writer.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.crash_recovery.teardown_crash_recovery`.
        """
        from voice_typer.server.shutdown.teardowns.crash_recovery import (
            teardown_crash_recovery,
        )

        teardown_crash_recovery(self)

    def _teardown_history_db(self) -> None:
        """flush pending fire-and-forget history DB writes + close
        the DB (joins the writer thread).

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.history_db.teardown_history_db`.
        """
        from voice_typer.server.shutdown.teardowns.history_db import (
            teardown_history_db,
        )

        teardown_history_db(self)

    def _teardown_waveform_wiring(self) -> None:
        """stop the bubble level / waveform worker so it doesn't
        try to push to a torn-down IPC server during shutdown.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.waveform.teardown_waveform_wiring`.
        """
        from voice_typer.server.shutdown.teardowns.waveform import (
            teardown_waveform_wiring,
        )

        teardown_waveform_wiring(self)

    def _teardown_sounddevice(self) -> None:
        """safety-net ``sd.stop()`` — skipped when
        ``recorder.stop()`` (or ``discard()``) timed out.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.sounddevice.teardown_sounddevice`.
        Reads ``self._recorder_teardown_done`` / ``_recorder_force_closed``
        (set by :meth:`_teardown_recorder`) for the happens-before
        guarantee.
        """
        from voice_typer.server.shutdown.teardowns.sounddevice import (
            teardown_sounddevice,
        )

        teardown_sounddevice(self)

    def _abort_sounddevice_streams(self, sd_module) -> None:
        """force-abort every active sounddevice stream.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.sounddevice.abort_sounddevice_streams`.
        """
        from voice_typer.server.shutdown.teardowns.sounddevice import (
            abort_sounddevice_streams,
        )

        abort_sounddevice_streams(self, sd_module)

    def _teardown_electron(self) -> None:
        """terminate the Electron subprocess.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.electron.teardown_electron`.
        Acquires ``self._electron_pid_lock`` (initialized in ``__init__``)
        around the read-terminate-clear critical section.
        """
        from voice_typer.server.shutdown.teardowns.electron import (
            teardown_electron,
        )

        teardown_electron(self)

    def _teardown_pid_file(self) -> None:
        """clear the backend PID file so a subsequent launch isn't
        falsely blocked by the single-instance check.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.pid_file.teardown_pid_file`.
        """
        from voice_typer.server.shutdown.teardowns.pid_file import (
            teardown_pid_file,
        )

        teardown_pid_file(self)

    def _teardown_mutex_handle(self) -> None:
        """release the single-instance mutex handle.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.mutex.teardown_mutex_handle`.
        """
        from voice_typer.server.shutdown.teardowns.mutex import (
            teardown_mutex_handle,
        )

        teardown_mutex_handle(self)

    def _teardown_devnull_files(self) -> None:
        """close devnull streams opened during logging setup.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.devnull.teardown_devnull_files`.
        """
        from voice_typer.server.shutdown.teardowns.devnull import (
            teardown_devnull_files,
        )

        teardown_devnull_files(self)

    def _teardown_asr_models(self) -> None:
        """unload active ASR backend + release CUDA caching allocator
        blocks so torch's VRAM is returned to the OS before process exit.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.asr_models.teardown_asr_models`.
        """
        from voice_typer.server.shutdown.teardowns.asr_models import (
            teardown_asr_models,
        )

        teardown_asr_models(self)

    def _teardown_event_bus(self) -> None:
        """shut down the event_bus deferred-publish executor.

        Body lives in
        :func:`voice_typer.server.shutdown.teardowns.event_bus.teardown_event_bus`.
        """
        from voice_typer.server.shutdown.teardowns.event_bus import (
            teardown_event_bus,
        )

        teardown_event_bus(self)

    # ─── Quit ──────────────────────────────────────────────────────────

    def quit(self):
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
        app = self._app
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
        with self._quit_lock:
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
            self._arm_shutdown_watchdog(SHUTDOWN_WATCHDOG_TIMEOUT_S)

        if is_main:
            sys.exit(0)

    def _arm_shutdown_watchdog(self, timeout_s: float) -> None:
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
            try:
                join_leaked_workers(timeout_s=2.0)
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

    # ─── atexit safety net (: body → voice_typer.server.atexit_safety) ──

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called.

        body lives in :func:`voice_typer.server.atexit_safety.atexit_log`.
        This delegate preserves the instance-method API used by
        ``atexit.register(self._atexit_log)`` in ``VoiceTyperApp.start()``.
        """
        from voice_typer.server.atexit_safety import atexit_log

        atexit_log(self)

    def _atexit_cleanup(self) -> None:
        """atexit handler for critical cleanup paths.

        Idempotent — short-circuits on ``_shutting_down`` and never
        raises (). See :func:`voice_typer.server.atexit_safety.atexit_cleanup`
        for the full behavior contract ( extraction).

        body lives in :mod:`voice_typer.server.atexit_safety`.
        This delegate preserves the instance-method API used by tests
        (``controller._atexit_cleanup()``) and the ``VoiceTyperApp``
        wiring (``atexit.register(self._atexit_cleanup)``).
        """
        from voice_typer.server.atexit_safety import atexit_cleanup

        atexit_cleanup(self)

    # ─── Signal handlers (: body → voice_typer.server.signal_handlers) ──

    def _install_signal_handlers(self):
        """Install SIGINT/SIGTERM/SIGHUP handlers for graceful shutdown.

        body lives in
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

        body lives in
        :func:`voice_typer.server.signal_handlers.signal_watcher_loop`.
        This delegate is kept so the test fixture that calls
        ``controller._signal_watcher_loop()`` directly continues to work,
        and so legacy code that captured ``target=self._signal_watcher_loop``
        before the  split keeps functioning. New code should call
        ``signal_handlers.signal_watcher_loop(controller)`` directly.
        """
        from voice_typer.server.signal_handlers import signal_watcher_loop

        signal_watcher_loop(self)

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure.

        body lives in
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

        body lives in
        :func:`voice_typer.server.signal_handlers.win32_console_handler`.
        This delegate preserves the instance-method API used by tests
        (``controller._win32_console_handler(ctrl_type)`` — see
        ``tests/test_shutdown_controller.py::TestWin32ConsoleHandlerRouting``)
        and the ctypes callback wiring (``handler_routine(self._win32_console_handler)``
        inside :func:`signal_handlers.install_win32_console_handler`).
        """
        from voice_typer.server.signal_handlers import win32_console_handler

        return win32_console_handler(self, ctrl_type)
