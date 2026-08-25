"""Cleanup orchestration bodies (extracted from ``shutdown_controller``).

Houses the bodies of :meth:`ShutdownController._do_cleanup` (the shared,
idempotent full-cleanup orchestrator) and
:meth:`ShutdownController._do_fast_cleanup` (the Windows logoff/shutdown
critical-only path) as free functions taking the owning controller.

The controller keeps thin delegates on :class:`CleanupMixin`
(``shutdown_controller/_cleanup.py``) so the instance-method API used by
callers (``quit()`` / ``restart_app()`` / ``_atexit_cleanup()`` route
through ``app._do_cleanup()``) and by tests (spies that
``monkeypatch.setattr(controller, "_do_cleanup", spy)`` or patch the
``_teardown_*`` helpers by name) continues to intercept the call — same
convention as :mod:`voice_typer.server.shutdown.teardowns`.

Patch-path note: ``_run_with_timeout`` / ``TIMEOUT`` are imported at module
level from :mod:`voice_typer.server._timeout_utils` — the same binding
strategy the body had in ``shutdown_controller/_cleanup.py`` (the
``shutdown_controller`` package attribute is a separate binding that the
plan driver resolves lazily; it is unaffected).

The logger keeps the pre-extraction name
(``voice_typer.server.shutdown_controller``) so log records emitted by the
moved bodies land on the same logger as before (tests filter caplog records
by that logger name and operators' log configs key off it).
"""

from __future__ import annotations

import contextlib
import logging
import os
import time

from voice_typer.server._timeout_utils import TIMEOUT, _run_with_timeout
from voice_typer.server.duration import format_duration
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger("voice_typer.server.shutdown_controller")

# ``__all__`` is declared directly after the imports (not at the end of
# the module) so ``do_fast_cleanup``'s body — which must END with the
# terminal ``os._exit(0)`` call — is the last executable code in the
# file (source-inspection contract in
# ``tests/test_shutdown_fast.py::TestFastCleanupSource``).
__all__ = ["do_cleanup", "do_fast_cleanup"]


def do_cleanup(controller) -> None:
    """shared cleanup body used by ``quit()``, ``restart_app()``,
    and ``_atexit_cleanup()``.

    Performs ALL the cleanup that ``quit()`` previously did inline,
    EXCEPT the final ``sys.exit(0)``.  Every operation is guarded by
    a None-check or try-except so the method is IDEMPOTENT — calling
    it twice (e.g. once from ``quit()`` and once from the atexit
    safety net) is a no-op on the second call.

    The caller is responsible for setting ``app._shutting_down = True``
    and ``app._shutting_down_event.set()`` BEFORE calling this
    method so the atexit safety net doesn't double-cleanup. The
    ``_cleanup_done`` flag below is the hard guarantee: once set,
    every subsequent call returns immediately.

    ``do_cleanup`` ALSO drains / cancels the WS dispatch
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
    app = controller._app
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
    with controller._quit_lock:
        if getattr(app, "_cleanup_done", False):
            return
        app._cleanup_done = True

    # C-LOG-2: shutdown is a timed operation — the completion line
    # carries the total cleanup duration.
    _cleanup_t0 = time.perf_counter()

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
    controller._recorder_teardown_done.clear()
    controller._recorder_force_closed = False

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
    # text ``_timed_out = controller._run_plan(sequenced_plan,
    # frozenset())`` is pinned by static contract tests in
    # ``tests/test_shutdown_recording_fixes.py``). ``_do_cleanup``
    # is idempotent (gated by ``_cleanup_done``) so the body runs
    # at most once per controller; direct ``_run_plan`` invocations
    # from tests use a fresh controller where the attribute is
    # ``None`` (initialised in ``__init__``).
    controller._shutdown_deadline = _shutdown_deadline
    # Publish the skipped-list on the instance so the extracted
    # ``run_plan`` driver (in ``shutdown/plan.py``) can append
    # inter-step deadline-skip entries to the SAME list that
    # ``_build_sequenced_plan`` / ``_build_parallel_plan`` append
    # to. The single shared list is then summarised in the
    # ``if _shutdown_skipped:`` WARNING block below. ``None``
    # outside an active ``_do_cleanup`` call (mirrors
    # ``_shutdown_deadline``).
    controller._shutdown_skipped = _shutdown_skipped

    # ── Early bookend (parallel ipc_server.stop + WS drain) ─────────
    # Stop the IPC server EARLY so inbound
    # requests can't resurrect torn-down subsystems, and drain / cancel
    # in-flight WS dispatch requests BEFORE any subsystem teardown —
    # CONCURRENTLY, in a single ``_run_parallel_with_timeout`` batch
    # batch. They touch disjoint pools (the TCP worker pool and the
    # WS dispatch pool), so parallelisation is safe. Body extracted to
    # ``_drain_ws_dispatch_pool`` — preserves the exact
    # WS-pool drain logic including the ``if join_thread.is_alive():``
    # timeout branch.
    controller._drain_ws_dispatch_pool(app)

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
    sequenced_plan = controller._build_sequenced_plan(_shutdown_deadline, _shutdown_skipped)
    _timed_out = controller._run_plan(sequenced_plan, frozenset())

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
    parallel_plan = controller._build_parallel_plan(_shutdown_deadline, _timed_out, _shutdown_skipped)
    if parallel_plan is not None:
        controller._run_plan(parallel_plan, _timed_out)

    # Overall-deadline summary: emit a single WARNING listing every
    # teardown that was skipped due to the 20s deadline.
    if _shutdown_skipped:
        log.warning(
            "[SHUTDOWN] skipped %d teardowns due to 20s deadline: %s",
            len(_shutdown_skipped),
            ", ".join(_shutdown_skipped),
        )

    if _shutdown_skipped:
        log.info(
            "[SHUTDOWN] Shutdown complete, exiting with %d teardowns skipped%s",
            len(_shutdown_skipped),
            format_duration(time.perf_counter() - _cleanup_t0),
        )
    else:
        log.info(
            "[SHUTDOWN] Shutdown complete, exiting successfully%s",
            format_duration(time.perf_counter() - _cleanup_t0),
        )

    # ── Late bookend (sequential) ────────────────────────────────
    # ``tray.stop()`` MUST be the LAST step in ``_do_cleanup()``.
    # Body extracted to ``_late_bookend_tray_stop`` —
    # preserves the timeout branch + the non-main-thread
    # ``os._exit(0)`` fallback.
    controller._late_bookend_tray_stop(app)


def do_fast_cleanup(controller) -> None:
    """critical-only cleanup for Windows logoff/shutdown.

    Windows CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT give the process
    ~5 seconds before the OS forcibly terminates it. The full
    ``do_cleanup`` body has a cumulative worst-case of ~85s.
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

    This function ends with ``os._exit(0)`` — bypassing atexit
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
    app = controller._app
    # Set ``_cleanup_done`` so a concurrent / subsequent
    # ``_do_cleanup`` call short-circuits. The flag does NOT gate
    # the critical flushes below — they run unconditionally so a
    # quit-during-logoff doesn't lose the user's last write
    # (the writes are idempotent; running them twice is safe).
    with controller._quit_lock:
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
