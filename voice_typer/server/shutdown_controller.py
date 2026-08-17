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
import sys  # noqa: F401  # re-exported / monkeypatch target for tests
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

# Single source of truth for the declarative shutdown plan + driver lives
# in :mod:`voice_typer.server.shutdown.plan` (extracted out of this module
# to keep the controller wiring-focused). Re-imported here so existing
# callers — tests do
# ``from voice_typer.server.shutdown_controller import ShutdownPlan,
# ShutdownStep`` — keep resolving, and so the dataclass constructors used
# in ``_do_cleanup`` below remain in scope without duplication.
from voice_typer.server._timeout_utils import (  # noqa: F401  # SHUTDOWN_WATCHDOG_TIMEOUT_S + join_leaked_workers re-exported for tests
    SHUTDOWN_WATCHDOG_TIMEOUT_S,
    TIMEOUT,
    _run_parallel_with_timeout,
    _run_with_timeout,
    join_leaked_workers,
)
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.shutdown.plan import (  # noqa: F401  # re-exported for tests + call sites
    ShutdownPlan,
    ShutdownStep,
    run_plan,
)

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
# The dataclasses in :mod:`voice_typer.server.shutdown.plan`
# (``ShutdownStep`` / ``ShutdownPlan``) make the ordering contract
# EXPLICIT and machine-checkable, and the :func:`run_plan` driver
# encapsulates the per-step timeout + barrier logic.
# ``_do_cleanup`` builds two ``ShutdownPlan`` instances (sequenced +
# parallel) and hands them to ``_run_plan`` (a thin delegate to
# :func:`run_plan`), which:
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
# future resource pairs without modifying ``run_plan``.


# ─── Shutdown-deadline budget helpers (module-level) ────────────────
#
# Extracted from the closures that previously lived inside
# ``_do_cleanup`` so the extracted plan-building helpers
# (``_build_sequenced_plan`` / ``_build_parallel_plan``) and
# ``_run_plan`` (inter-step deadline check) can share the same
# deadline-budget logic without re-defining the closures or passing
# them as parameters. Reading *deadline* as a parameter (instead of
# capturing a local) keeps the helpers pure functions of the deadline.


def _shutdown_remaining(deadline: float) -> float:
    """Return the remaining seconds until *deadline* (clamped to 0).

    The clamp to 0 ensures downstream ``< 5.0`` (near) and
    ``min(step_timeout, remaining)`` (cap) computations never go
    negative when the deadline has already passed.
    """
    return max(0.0, deadline - time.monotonic())


def _shutdown_deadline_near(deadline: float) -> bool:
    """True when less than 5s remain before *deadline*.

    The 5s threshold gates the skip of non-critical teardowns so the
    remaining budget can be spent on critical flushes (history_db,
    crash_recovery, recorder.stop, mutex, PID file) + the late
    ``tray.stop`` bookend.
    """
    return _shutdown_remaining(deadline) < 5.0


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
    - Read ``_clear_backend_pid_file`` dynamically from
      ``voice_typer.server.app`` so tests that monkeypatch that
      re-export still take effect. (``register_devnull_file`` /
      ``close_devnull_files`` live on ``voice_typer.server.log`` and
      are called directly from ``signal_handlers`` / the devnull
      teardown — not via an app re-export.)
    """

    # the ordered list of every ``_teardown_*`` phase method that
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
        # Counter for the number of POSIX signals received. Incremented
        # in the async-signal-safe handler (atomic ``int`` increment
        # under the GIL) and read by ``signal_watcher_loop`` to
        # escalate on the SECOND signal (force-exit via ``os._exit(1)``).
        # Initial value 0; the first signal increments to 1 (graceful
        # path), the second to 2 (escalation path).
        # ``getattr(controller, "_signal_count", 0)`` in the watcher
        # handles the case where a test bypassed ``__init__`` (e.g.
        # ``SimpleNamespace``).
        self._signal_count: int = 0
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

        # Published by ``_do_cleanup`` so ``_run_plan`` can apply
        # inter-step deadline checks between sequenced
        # teardowns without changing its call-site signature (the exact
        # text ``_timed_out = self._run_plan(sequenced_plan,
        # frozenset())`` is pinned by
        # ``tests/test_shutdown_recording_fixes.py``). ``None`` outside
        # an active ``_do_cleanup`` call — direct ``_run_plan``
        # invocations from tests (which use a fresh controller or
        # ``__new__``) skip the inter-step check.
        self._shutdown_deadline: float | None = None
        # Published by ``_do_cleanup`` alongside ``_shutdown_deadline``
        # so the extracted ``run_plan`` driver (in ``shutdown/plan.py``)
        # can append inter-step deadline-skip entries to the shared
        # list. ``None`` outside an active ``_do_cleanup`` call.
        self._shutdown_skipped: list[str] | None = None

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

        # Session-liveness marker: the session ends HERE. The marker is
        # cleared by the FIRST sequenced teardown
        # (``teardown_session_marker``) so a kill mid-teardown (watchdog
        # ``os._exit(0)``, SIGKILL fallback, Windows logoff force-kill)
        # still counts as a clean shutdown — the user initiated it, so
        # the next launch must not report a crash.

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
        # after — see the sequenced-phase rationale in
        # ``_build_sequenced_plan``. The 20s deadline is checked before
        # each phase and between each sequenced step; when the
        # remaining budget drops below 5s, non-critical teardowns are
        # SKIPPED and only critical flushes (history_db, crash_recovery,
        # recorder.stop, mutex, PID file) + the late ``tray.stop``
        # bookend run. Skipped teardowns are logged at WARNING.
        _shutdown_deadline: float = time.monotonic() + 20.0
        _shutdown_skipped: list[str] = []
        # Publish the deadline on the instance so ``_run_plan`` can
        # apply inter-step deadline checks between sequenced
        # teardowns without changing its call-site signature (the exact
        # text ``_timed_out = self._run_plan(sequenced_plan,
        # frozenset())`` is pinned by static contract tests in
        # ``tests/test_shutdown_recording_fixes.py``). ``_do_cleanup``
        # is idempotent (gated by ``_cleanup_done``) so the body runs
        # at most once per controller; direct ``_run_plan`` invocations
        # from tests use a fresh controller where the attribute is
        # ``None`` (initialised in ``__init__``).
        self._shutdown_deadline = _shutdown_deadline
        # Publish the skipped-list on the instance so the extracted
        # ``run_plan`` driver (in ``shutdown/plan.py``) can append
        # inter-step deadline-skip entries to the SAME list that
        # ``_build_sequenced_plan`` / ``_build_parallel_plan`` append
        # to. The single shared list is then summarised in the
        # ``if _shutdown_skipped:`` WARNING block below. ``None``
        # outside an active ``_do_cleanup`` call (mirrors
        # ``_shutdown_deadline``).
        self._shutdown_skipped = _shutdown_skipped

        # ── Early bookend (parallel ipc_server.stop + WS drain) ─────────
        #  (partial) + SU-23: stop the IPC server EARLY so inbound
        # requests can't resurrect torn-down subsystems, and drain / cancel
        # in-flight WS dispatch requests BEFORE any subsystem teardown —
        # CONCURRENTLY, in a single ``_run_parallel_with_timeout`` batch
        # (SU-23). They touch disjoint pools (the TCP worker pool and the
        # WS dispatch pool), so parallelisation is safe. Body extracted to
        # ``_drain_ws_dispatch_pool`` — preserves the exact
        # WS-pool drain logic including the ``if join_thread.is_alive():``
        # timeout branch.
        self._drain_ws_dispatch_pool(app)

        # ── Sequenced critical teardowns ────────────────────────────
        # The transcription thread (spawned by ``recorder.stop()``) runs
        # ASR inference and writes its result to ``history_db`` via
        # fire-and-forget ``add_transcription()``. The ASR model the
        # thread is mid-inference on must NOT be unloaded, and the DB
        # must NOT be closed, until the thread has finished. The
        # sequenced phase runs the dependent teardowns IN ORDER (timers
        # → recorder → history_db → crash_recovery), each wrapped in
        # ``_run_with_timeout`` so a stuck helper doesn't block the
        # rest of cleanup. Plan construction (including the deadline-near
        # skip of ``teardown_timers_and_recording``) lives in
        # ``_build_sequenced_plan``.
        sequenced_plan = self._build_sequenced_plan(_shutdown_deadline, _shutdown_skipped)
        _timed_out = self._run_plan(sequenced_plan, frozenset())

        # ── Parallel batch: 11 independent teardown helpers ─────────
        # Each helper is isolated — a failure in one does NOT propagate
        # (``_run_parallel_with_timeout`` captures per-call exceptions).
        # ``_teardown_asr_models`` is placed FIRST so the (potentially
        # slow) CUDA context teardown starts as early as possible. It
        # runs AFTER the sequenced critical phase (which joins the
        # transcription thread), so the ASR model is only unloaded once
        # the thread's inference has completed. Plan construction
        # (including the deadline-near skip of non-critical helpers +
        # the ``teardown_sounddevice`` barrier on ``teardown_recorder``)
        # lives in ``_build_parallel_plan``.
        parallel_plan = self._build_parallel_plan(_shutdown_deadline, _timed_out, _shutdown_skipped)
        if parallel_plan is not None:
            self._run_plan(parallel_plan, _timed_out)

        # Overall-deadline summary: emit a single WARNING listing every
        # teardown that was skipped due to the 20s deadline.
        if _shutdown_skipped:
            log.warning(
                "[SHUTDOWN] skipped %d teardown(s) due to 20s deadline: %s",
                len(_shutdown_skipped),
                ", ".join(_shutdown_skipped),
            )

        if _shutdown_skipped:
            log.info(
                "[SHUTDOWN] Shutdown complete, exiting with %d teardown(s) skipped",
                len(_shutdown_skipped),
            )
        else:
            log.info("[SHUTDOWN] Shutdown complete, exiting successfully")

        # ── Late bookend (sequential) ────────────────────────────────
        # ``tray.stop()`` MUST be the LAST step in ``_do_cleanup()``.
        # Body extracted to ``_late_bookend_tray_stop`` —
        # preserves the timeout branch + the non-main-thread
        # ``os._exit(0)`` fallback.
        self._late_bookend_tray_stop(app)

    # ─── Early bookend helper — ──────────────────────

    def _drain_ws_dispatch_pool(self, app) -> None:
        """Early bookend: stop the IPC server + drain the WS dispatch pool.

        Extracted from ``_do_cleanup``. Stops the IPC server
        EARLY so inbound requests can't resurrect torn-down subsystems,
        and drains / cancels in-flight WS dispatch requests BEFORE any
        subsystem teardown — concurrently, in a single
        ``_run_parallel_with_timeout`` batch. They touch disjoint pools
        (the TCP worker pool and the WS dispatch pool), so
        parallelisation is safe. ``_shutting_down`` is already True (set
        by ``quit()`` before calling ``_do_cleanup``), so the
        ``sidecar_ws._make_dispatch`` ``dispatch`` coroutine is already
        rejecting NEW requests. Best-effort — failures here don't
        prevent the rest of cleanup from running.

        Preserves the ``if join_thread.is_alive():`` drain-timeout
        branch (pinned by
        ``tests/test_shutdown_fast_path.py::TestOsExitOnStuckWsDrain::
        test_ws_drain_timeout_branch_exists``).
        """
        try:
            ipc_server = getattr(app, "_ipc_server", None)
            ws_pool = getattr(ipc_server, "_ws_dispatch_pool", None) if ipc_server is not None else None

            early_items: list[tuple[str, object, float]] = []
            if ipc_server is not None:
                # PERF-SHUTDOWN-002: the ipc_server.stop budget was 5.0s
                # pre-quit-latency-fix. ``stop()`` gates its pool drains
                # on ``app._shutting_down`` (always True on this path),
                # so it returns in milliseconds; 2.0s is now a generous
                # hard ceiling that still bounds teardown if a future
                # regression re-introduces a blocking path.
                early_items.append(("ipc_server.stop", ipc_server.stop, 2.0))

            if ws_pool is not None and hasattr(ws_pool, "shutdown"):

                def _drain_ws_pool() -> None:
                    # ``shutdown(wait=False, cancel_futures=True)`` only
                    # cancels QUEUED (not-yet-started) tasks; RUNNING handlers
                    # continue. Without a bounded join, teardown races any
                    # in-flight WS handler that touches the recorder /
                    # history_db / crash_recovery subsystems. Spawn a
                    # daemon-thread ``shutdown(wait=True)`` and join the
                    # spawner with a 5s hard deadline (generous for any single
                    # handler, short enough to bound teardown). If the drain
                    # doesn't complete in 5s, log + proceed.
                    ws_pool.shutdown(wait=False, cancel_futures=True)
                    log.debug("[SHUTDOWN] WS dispatch pool shut down (cancel_futures=True)")
                    join_thread = threading.Thread(
                        target=ws_pool.shutdown,
                        kwargs={"wait": True},
                        daemon=True,
                    )
                    join_thread.start()
                    join_thread.join(timeout=5.0)
                    if join_thread.is_alive():
                        log.warning("[SHUTDOWN] ws_dispatch_pool did not drain in 5s — proceeding anyway")

                early_items.append(("ws_dispatch_pool.drain", _drain_ws_pool, 5.0))

            if early_items:
                _run_parallel_with_timeout(early_items)

            # explicit ``threading.Event`` coordination between the WS
            # dispatch path and ``_do_cleanup``. The pool's ``shutdown(wait=True)``
            # (run above) only guarantees that the ThreadPoolExecutor drained
            # its worker queue — it does NOT guarantee that the per-dispatch
            # coroutine body finished its DB write (the WS ``dispatch``
            # coroutine may still be in its ``await loop.run_in_executor``
            # unwind / result-serialisation tail when the pool reports drained).
            # ``sidecar_ws._make_dispatch`` clears ``_ws_drained_event`` on
            # entry to each dispatch and sets it when the in-flight count drops
            # to zero (after the dispatch body fully returns — including the
            # post-Future unwind). We wait on that Event here, bounded by 2s,
            # BEFORE allowing the parallel teardown batch to proceed. If the
            # wait times out, we log and proceed (the in-flight handler is on
            # its own).
            if ipc_server is not None:
                ws_drained_event = getattr(ipc_server, "_ws_drained_event", None)
                if ws_drained_event is not None:
                    # Skip the 2s wait when the WS pool is already idle
                    # (``_ws_inflight_count == 0``). The
                    # ``sidecar_ws._make_dispatch`` lazily attaches
                    # ``_ws_inflight_count`` (an int, initially 0) on
                    # first dispatch; before any dispatch has ever
                    # fired, the attribute is missing —
                    # ``getattr(..., 0)`` falls back to 0 and the wait
                    # is skipped (no in-flight handler can race DB
                    # teardown when the pool has never been used).
                    # When ``_ws_inflight_count > 0``, the original 2s
                    # bounded wait is kept so an in-flight handler
                    # gets its bounded window to finish its DB write
                    # before ``_teardown_history_db`` starts.
                    ws_inflight = getattr(ipc_server, "_ws_inflight_count", 0)
                    if ws_inflight == 0:
                        log.debug(
                            "[SHUTDOWN] ws_drained_event.wait skipped "
                            "(_ws_inflight_count=0 — no in-flight WS handler "
                            "can race DB teardown)"
                        )
                    else:
                        drained = ws_drained_event.wait(timeout=2.0)
                        if not drained:
                            in_flight = getattr(ipc_server, "_ws_inflight_count", 0)
                            # drain-timeout branch — log at WARNING and
                            # proceed (never block) so an in-flight write can't
                            # stall shutdown.
                            log.warning(
                                "[SHUTDOWN] DJ-9: WS dispatch drain Event did not "
                                "fire in 2s — %s in-flight handler(s) may race DB "
                                "teardown; proceeding with cleanup (the in-flight "
                                "write may silently fail)",
                                in_flight,
                            )
        except Exception:
            log.debug(
                "[SHUTDOWN] early bookend (ipc_server.stop + WS drain) failed",
                exc_info=True,
            )

    # ─── Sequenced plan builder — ────────────────────

    def _build_sequenced_plan(
        self,
        deadline: float,
        skipped: list[str],
    ) -> ShutdownPlan:
        """Build the sequenced critical-teardown plan.

        Extracted from ``_do_cleanup``. The sequenced phase
        runs the dependent teardowns IN ORDER, each wrapped in
        ``_run_with_timeout`` so a stuck helper doesn't block the rest
        of cleanup:

          1. ``_teardown_timers_and_recording`` — cancel timers, pop
             the streaming session, signal cancel. SKIPPED when the
             deadline is near (non-critical).
          2. ``_teardown_recorder`` — ``recorder.stop()`` + join the
             transcription thread (3s timeout). Sets
             ``_recorder_teardown_done`` so the downstream
             ``_teardown_sounddevice`` (in the parallel batch) gets a
             happens-before guarantee on ``_recorder_force_closed``.
          3. ``_teardown_history_db`` — ``flush()`` + ``close()`` to
             drain pending writes (including the one the transcription
             thread just enqueued).
          4. ``_teardown_crash_recovery`` — ``flush()`` + ``shutdown()``
             to drain pending crash-recovery snapshots.

        ``_teardown_asr_models`` stays in the parallel batch (built by
        ``_build_parallel_plan``): the sequenced phase completes BEFORE
        the parallel batch starts, so the transcription thread is
        already joined by the time the ASR model is unloaded.

        The list-of-tuples form (rather than direct ``ShutdownStep``
        construction) is kept so source-text contract tests
        (``tests/test_shutdown_fast_path.py::TestSequentialHistoryAndCrashRecovery``
        and ``tests/test_shutdown_asr_unload.py::TestTeardownAsrModelsContract``)
        continue to find the sequenced / parallel symbols + the
        ``("teardown_<name>",`` entry pattern.

        Parameters
        ----------
        deadline:
            The overall 20s shutdown deadline (``time.monotonic() +
            20.0``), used to decide whether to skip the non-critical
            ``teardown_timers_and_recording`` step.
        skipped:
            Mutable list of skipped step names; appended to in place so
            ``_do_cleanup`` can emit a single summary WARNING at the end.
        """
        # Overall-deadline skip: when the 20s deadline is near (< 5s
        # remaining) at the start of the sequenced phase,
        # ``teardown_timers_and_recording`` is SKIPPED (non-critical).
        # ``teardown_recorder``, ``teardown_history_db``, and
        # ``teardown_crash_recovery`` ALWAYS run — they contain critical
        # flushes.
        sequenced_items: list[tuple[str, object, float, str | None, bool]] = []
        # SESSION-STATE: clear the session-active marker FIRST so a kill
        # later in teardown (watchdog ``os._exit(0)``, SIGKILL fallback)
        # still counts as a clean shutdown. Cheap + idempotent; always
        # runs regardless of deadline pressure.
        sequenced_items.append(
            ("teardown_session_marker", self._teardown_session_marker, 5.0, None, False),
        )
        if _shutdown_deadline_near(deadline):
            log.warning(
                "[SHUTDOWN] deadline near (%.1fs remaining) at sequenced "
                "phase entry — skipping teardown_timers_and_recording (non-critical)",
                _shutdown_remaining(deadline),
            )
            skipped.append("teardown_timers_and_recording")
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
        return sequenced_plan

    # ─── Parallel plan builder — ─────────────────────

    def _build_parallel_plan(
        self,
        deadline: float,
        timed_out: frozenset[str],
        skipped: list[str],
    ) -> ShutdownPlan | None:
        """Build the parallel-batch plan, applying deadline-near skips.

        Extracted from ``_do_cleanup``. Each helper is
        isolated — a failure in one does NOT propagate
        (``_run_parallel_with_timeout`` captures per-call exceptions).
        Shared 10s deadline: each helper is wrapped in
        ``_run_with_timeout(..., timeout=10.0)`` by
        ``_run_parallel_with_timeout``; if a helper exceeds 10s, the
        worker thread is leaked as a daemon and the orchestrator moves
        on.

        ``_teardown_asr_models`` is placed FIRST in the parallel batch
        so the (potentially slow) CUDA context teardown starts as
        early as possible. It runs AFTER the sequenced critical phase
        (which joins the transcription thread), so the ASR model is
        only unloaded once the thread's inference has completed — no
        race between ``registry.unload()`` and mid-inference torch
        state.

        Barrier: ``teardown_sounddevice`` declares
        ``depends_on="teardown_recorder"`` + ``skip_if_dep_timed_out=
        True``. When the recorder's PortAudio stream failed to close
        in time, the leaked worker is still accessing the stream and
        a concurrent ``sd.stop()`` can deadlock on WASAPI backends
        (stream lock held). The ``_run_plan`` driver skips the step
        when the dependency is in ``timed_out``.

        Overall-deadline skip: when the 20s deadline is near (< 5s
        remaining), skip NON-CRITICAL parallel helpers. The critical
        set is ``{teardown_pid_file, teardown_mutex_handle}`` — they
        release the single-instance PID file + mutex so the next
        launch isn't blocked. Everything else is non-critical under a
        tight deadline — the OS will reap those resources at process
        exit.

        Parameters
        ----------
        deadline:
            The overall 20s shutdown deadline, used to decide which
            non-critical helpers to skip.
        timed_out:
            Step names that timed out in the sequenced plan (used by
            ``_run_plan`` for the barrier — NOT used directly here but
            threaded through for the subsequent ``_run_plan`` call).
        skipped:
            Mutable list of skipped step names; appended to in place.

        Returns
        -------
        ShutdownPlan | None
            The parallel plan, or ``None`` if every helper was skipped
            (defensive — the critical set ensures at least 2 items
            always run, so ``None`` is never returned in practice).
        """
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
            if _shutdown_deadline_near(deadline) and _desc not in _shutdown_critical_parallel:
                log.warning(
                    "[SHUTDOWN] deadline near (%.1fs remaining) — skipping non-critical %s",
                    _shutdown_remaining(deadline),
                    _desc,
                )
                skipped.append(_desc)
                continue
            parallel_items.append((_desc, _func, _timeout, _dep, _skip))
        # Guard against empty parallel_items (defensive — critical set
        # ensures at least 2 items always run).
        if not parallel_items:
            return None
        parallel_plan = ShutdownPlan(
            phase="parallel",
            steps=tuple(ShutdownStep(*item) for item in parallel_items),
        )
        return parallel_plan

    # ─── Late bookend helper — ───────────────────────

    def _late_bookend_tray_stop(self, app) -> None:
        """Late bookend: ``tray.stop()`` — MUST be the LAST step in cleanup.

        Extracted from ``_do_cleanup``. ``tray.stop()`` MUST
        be the LAST step. Previously it was step 13 of 19, which broke
        the pystray loop on the main thread (blocked in ``tray.run()``
        via ``ipc_server.main()``) before the remaining cleanups could
        finish. Moving ``tray.stop()`` to the end ensures the main
        thread stays alive (blocked in ``tray.run()``) until every
        other cleanup has completed. Idempotent — wrapped in
        try-except so a second call after the tray is already stopped
        doesn't propagate. 5s timeout.

        If ``tray.stop()`` times out AND we're on a non-main thread,
        call ``os._exit(0)`` immediately. The main thread is parked in
        pystray's ``tray.run()`` event loop and relies on
        ``tray.stop()`` breaking that loop to return. If
        ``tray.stop()`` hangs, the main thread never returns and the
        process is unkillable via the normal path — ``sys.exit(0)`` in
        ``quit()`` only raises ``SystemExit`` in THIS worker thread.
        ``os._exit(0)`` bypasses Python's orderly shutdown but is safe
        here because every other subsystem has already been torn down
        by the cleanup steps above. On the main thread, we just log
        and continue — ``quit()``'s ``sys.exit(0)`` will handle exit.

        When ``tray.stop()`` RAISES (not times out), the failure is
        logged at ERROR (was DEBUG pre-fix) so operators can see why
        the main thread stayed parked in ``tray.run()``.
        """
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

        Thin delegate to :func:`voice_typer.server.shutdown.plan.run_plan`.
        The driver body (per-step timeout wrapping, pre-flight barrier
        skip, degraded-step summary) is owned by the extracted
        :mod:`shutdown.plan` module so it can be unit-tested in
        isolation — see ``tests/test_shutdown_plan_zr17.py``. The
        delegate keeps this method on ``ShutdownController`` so the
        existing ``self._run_plan(plan, prior_timed_out)`` call sites
        in ``_do_cleanup`` (and the source-inspection tests in
        ``tests/test_shutdown_recording_fixes.py``) continue to work
        unchanged.

        The extracted driver performs a lazy import of
        ``_run_with_timeout`` / ``_run_parallel_with_timeout`` from
        THIS module at call time, so tests that monkeypatch
        ``shutdown_controller._run_with_timeout`` (or
        ``shutdown_controller._run_parallel_with_timeout``) still
        take effect through the delegate.
        """
        return run_plan(self, plan, prior_timed_out)

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

        # Session-liveness marker: Windows logoff/shutdown is a clean
        # system-initiated shutdown (the OS force-kills us after ~5s and
        # ``os._exit(0)`` bypasses atexit). Clear the marker BEFORE the
        # critical flushes so the next launch (e.g. autostart after
        # boot) does not report a crash. Best-effort.
        try:
            from voice_typer.server import app as _app_module, session_state

            session_state.clear_session_marker(_app_module._config_dir())
        except Exception:
            log.debug("[SHUTDOWN] fast-path could not clear session marker", exc_info=True)

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

    def _teardown_session_marker(self) -> None:
        """clear the session-active marker so the next launch treats the
        previous session as clean (no crash notification).

        Runs as the FIRST sequenced teardown (see
        ``_build_sequenced_plan``). Body lives in
        :func:`voice_typer.server.shutdown.teardowns.session_marker.teardown_session_marker`.
        """
        from voice_typer.server.shutdown.teardowns.session_marker import (
            teardown_session_marker,
        )

        teardown_session_marker(self)

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
    #
    # extraction: the bodies of ``quit`` and
    # ``_arm_shutdown_watchdog`` now live in
    # :mod:`voice_typer.server.shutdown.lifecycle`. The methods below
    # are thin delegates that preserve the instance-method API used by
    # tests (``controller.quit()``, ``controller._arm_shutdown_watchdog(
    # timeout_s)``) and the ``VoiceTyperApp`` wiring (tray menu callbacks
    # invoke ``quit_app`` which calls ``quit``; the watchdog is armed
    # from ``quit`` on a non-main thread).
    #
    # The delegate indirection is kept so:
    #   * tests that ``monkeypatch.setattr(controller, "_arm_shutdown_watchdog",
    #     spy)`` (see ``tests/test_shutdown_controller.py::
    #     TestShutdownWatchdog``) still intercept the call; and
    #   * tests that ``monkeypatch.setattr("voice_typer.server.
    #     shutdown_controller.join_leaked_workers", fake_join)`` (see
    #     ``tests/test_shutdown_parallel_pool_drain.py::
    #     TestWatchdogJoinLeakedWorkers``) still intercept the call
    #     — :func:`voice_typer.server.shutdown.lifecycle.arm_shutdown_watchdog`
    #     looks up ``join_leaked_workers`` DYNAMICALLY from
    #     ``voice_typer.server.shutdown_controller`` (lazy import) so the
    #     patched attribute is what the body sees.

    def quit(self):
        """Shut down the application cleanly.

        body lives in :func:`voice_typer.server.shutdown.lifecycle.quit`.
        This delegate preserves the instance-method API used by tests
        (``controller.quit()``) and the ``VoiceTyperApp`` wiring
        (tray-menu callbacks invoke ``quit_app`` which calls ``quit``).
        """
        from voice_typer.server.shutdown.lifecycle import quit as _quit

        _quit(self)

    def _arm_shutdown_watchdog(self, timeout_s: float) -> None:
        """arm a daemon-thread watchdog that calls
        ``os._exit(0)`` after ``timeout_s`` seconds if the process is
        still alive.

        body lives in
        :func:`voice_typer.server.shutdown.lifecycle.arm_shutdown_watchdog`.
        This delegate preserves the instance-method API used by tests
        (``controller._arm_shutdown_watchdog(timeout_s)`` — see
        ``tests/test_shutdown_controller.py::TestShutdownWatchdog``) and
        the call site inside :func:`lifecycle.quit` (which calls
        ``controller._arm_shutdown_watchdog(...)`` so test spies that
        ``monkeypatch.setattr(controller, "_arm_shutdown_watchdog", spy)``
        still intercept the call).
        """
        from voice_typer.server.shutdown.lifecycle import (
            arm_shutdown_watchdog,
        )

        arm_shutdown_watchdog(self, timeout_s)

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
