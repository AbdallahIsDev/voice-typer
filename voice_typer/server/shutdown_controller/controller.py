"""The ``ShutdownController`` class (composition root).

Split verbatim out of the pre-split ``shutdown_controller`` module.
This module holds ONLY the class declaration (docstring + class-level
constants + ``__init__``); every method body lives on one of the
mixins in :mod:`._cleanup`, :mod:`._plans`, :mod:`._teardowns`, and
:mod:`._lifecycle_signals`.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    # ``shutdown_controller`` indirectly via the ``ShutdownController(self)``
    # call inside ``VoiceTyperApp.__init__``).  At runtime, ``app`` is
    # whatever object was passed to ``__init__`` (always a
    # ``VoiceTyperApp`` in production, but tests pass mocks that satisfy
    # the same duck-typed surface).
    from voice_typer.server.app import VoiceTyperApp

from ._cleanup import CleanupMixin
from ._lifecycle_signals import SignalsMixin
from ._plans import SequencingMixin
from ._teardowns import TeardownsMixin


class ShutdownController(CleanupMixin, SequencingMixin, TeardownsMixin, SignalsMixin):
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
    # ``tests/regressions/test_electron.py::TestShutdownControllerPhasesContract::
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
