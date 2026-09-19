"""Shutdown cleanup steps."""

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
__all__ = ["do_cleanup", "do_fast_cleanup"]


def do_cleanup(controller) -> None:
    """shared cleanup body used by ``quit()``, ``restart_app()``,"""
    app = controller._app
    # guard the check-then-set on ``_cleanup_done`` with
    with controller._quit_lock:
        if getattr(app, "_cleanup_done", False):
            return
        app._cleanup_done = True

    # C-LOG-2: shutdown is a timed operation, the completion line
    _cleanup_t0 = time.perf_counter()

    # Session-liveness marker: the session ends HERE. The marker is

    #  reset the shared state between ``_teardown_recorder``
    controller._recorder_teardown_done.clear()
    controller._recorder_force_closed = False

    # Overall deadline for the entire ``_do_cleanup`` body. The
    _shutdown_deadline: float = time.monotonic() + 20.0
    _shutdown_skipped: list[str] = []
    # Publish the deadline on the instance so ``_run_plan`` can
    controller._shutdown_deadline = _shutdown_deadline
    # Publish the skipped-list on the instance so the extracted
    controller._shutdown_skipped = _shutdown_skipped

    # Stop the IPC server EARLY so inbound
    controller._drain_ws_dispatch_pool(app)

    # The transcription thread (spawned by ``recorder.stop()``) runs
    sequenced_plan = controller._build_sequenced_plan(_shutdown_deadline, _shutdown_skipped)
    _timed_out = controller._run_plan(sequenced_plan, frozenset())

    # Each helper is isolated, a failure in one does NOT propagate
    parallel_plan = controller._build_parallel_plan(_shutdown_deadline, _timed_out, _shutdown_skipped)
    if parallel_plan is not None:
        controller._run_plan(parallel_plan, _timed_out)

    # Overall-deadline summary: emit a single WARNING listing every
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

    # ``tray.stop()`` MUST be the LAST step in ``_do_cleanup()``.
    controller._late_bookend_tray_stop(app)

    # Signal shutdown completion so the heartbeat force-exit
    _completed_event = getattr(getattr(app, "_ipc_server", None), "_shutdown_completed_event", None)
    if _completed_event is not None:
        _completed_event.set()


def do_fast_cleanup(controller) -> None:
    """critical-only cleanup for Windows logoff/shutdown."""
    app = controller._app
    # Set ``_cleanup_done`` so a concurrent / subsequent
    with controller._quit_lock:
        app._cleanup_done = True

    # Session-liveness marker: Windows logoff/shutdown is a clean
    try:
        from voice_typer.server import app as _app_module, session_state

        session_state.clear_session_marker(_app_module._config_dir())
    except Exception:
        log.debug("[SHUTDOWN] fast-path could not clear session marker", exc_info=True)

    log.warning(
        "[SHUTDOWN] fast cleanup path (Windows logoff/shutdown "
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

    # 3. recorder.stop(), release the PortAudio stream.
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
                log.warning("[SHUTDOWN] recorder.stop() timed out in fast-path")
    except Exception:
        log.debug("[SHUTDOWN] fast-path recorder.stop failed", exc_info=True)

    # 4. _clear_backend_pid_file()
    try:
        # Resolve through the owning module object at call time so tests
        from voice_typer.server import backend_pid as _backend_pid_module

        _backend_pid_module._clear_backend_pid_file()
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
    with contextlib.suppress(Exception):
        _restore_result = _run_with_timeout(
            "restore_volume (fast-path)",
            lambda: app._restore_volume(fade_ms=0),
            timeout=1.0,
        )
        if _restore_result is TIMEOUT:
            log.warning("[SHUTDOWN] restore_volume timed out in fast-path, system volume may remain ducked")
    with contextlib.suppress(Exception):
        app._duck_crash_recovery.clear()

    log.warning("[SHUTDOWN] fast cleanup path complete")

    # Bypass atexit, the OS is killing us (Windows logoff/shutdown
    os._exit(0)
