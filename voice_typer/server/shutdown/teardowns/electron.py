"""Teardown helper for the Electron subprocess.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_electron`. The body is unchanged;
only the class boundary moved.

Cross-helper state
------------------
This helper acquires ``controller._electron_pid_lock`` (initialized in
:class:`ShutdownController.__init__`) around the read-terminate-clear
critical section so concurrent ``quit()`` callers don't double-terminate
or clobber a freshly-installed PID.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_electron(controller) -> None:
    """terminate the Electron subprocess.

    P1-1.3: prefer the dedicated ``electron_launcher.terminate_electron``
    helper (which kills the entire process tree on Windows and uses
    SIGTERM → SIGKILL on POSIX) when we have a tracked PID. Fall
    back to the legacy ``tray_window`` path for PID discovery.

    the read-terminate-clear sequence is guarded by
    ``controller._electron_pid_lock`` so concurrent ``quit()`` callers
    don't double-terminate or clobber a freshly-installed PID.

    both branches are wrapped in ``_run_with_timeout(5.0)``.
    The legacy ``tray_window`` path now does SIGTERM → 2s wait →
    SIGKILL on POSIX (was SIGTERM-only with a 5s timeout that
    ``os.kill`` never actually blocks on).
    """
    app = controller._app
    try:
        from voice_typer.server import electron_launcher

        # hold the lock only across the read-terminate-clear
        # critical section so a concurrent caller observes the
        # cleared PID and skips. The lock is non-reentrant; the
        # terminate_electron call inside the critical section does
        # not re-acquire it.
        with controller._electron_pid_lock:
            launched_pid = getattr(app, "_electron_pid", None)
            if launched_pid:
                log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", launched_pid)
                _term_result = _run_with_timeout(
                    "electron_launcher.terminate_electron",
                    lambda: electron_launcher.terminate_electron(launched_pid),
                    timeout=5.0,
                )
                if _term_result is TIMEOUT:
                    # UE-1-F6: escalate on timeout — POSIX gets
                    # SIGKILL; Windows gets a ctypes
                    # TerminateProcess fallback. Pre-fix the POSIX
                    # branch had SIGKILL escalation but the Windows
                    # branch was a silent no-op on timeout (the
                    # electron process tree would keep running).
                    if sys.platform == "win32":
                        # Best-effort: a failure here must NOT
                        # prevent the PID clear below (stale PID
                        # would block the next launch's
                        # single-instance check).
                        try:
                            import ctypes
                            from ctypes import wintypes

                            # PROCESS_TERMINATE (0x0001) access right.
                            process_terminate = 0x0001
                            kernel32 = ctypes.windll.kernel32
                            kernel32.OpenProcess.argtypes = [
                                wintypes.DWORD,
                                wintypes.BOOL,
                                wintypes.DWORD,
                            ]
                            kernel32.OpenProcess.restype = wintypes.HANDLE
                            handle = kernel32.OpenProcess(process_terminate, False, launched_pid)
                            if handle:
                                kernel32.TerminateProcess(handle, 1)
                                kernel32.CloseHandle(handle)
                        except Exception:
                            log.debug(
                                "[SHUTDOWN] Windows TerminateProcess fallback failed",
                                exc_info=True,
                            )
                    else:
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
                    # SIGTERM → 2s waitpid poll → SIGKILL on POSIX.
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


__all__ = ["teardown_electron"]
