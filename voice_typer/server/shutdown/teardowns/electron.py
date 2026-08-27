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

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401
from voice_typer.server.platform_utils import is_windows


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
    # ── RESTART guard ─────────────────────────────────────────────────
    # On a RESTART in standalone mode, Python spawned Electron as a
    # child and has already pushed ``relaunch_app`` to it — Electron
    # will respawn the Python backend.  Killing the Electron child here
    # would leave nothing to relaunch (the app would quit instead of
    # restarting — the user-visible "Restart quits" bug).  On quit
    # (``_is_restarting`` False) and in dev mode (no tracked
    # ``_electron_pid``) the normal teardown runs.
    #
    # EXCEPTION — IN-PLACE RESTART: when ``_in_place_restart`` is also
    # set (standalone Restart), the Electron child must be TERMINATED,
    # not left alive: the entrypoint loop re-initializes the app and
    # re-launches Electron itself.  Leaving the old Electron alive would
    # strand the old UI (single-instance lock) and orphan it from the
    # new backend.
    #
    # NOTE: read via ``vars(app)`` (instance dict), NOT ``getattr`` —
    # a ``MagicMock`` test app auto-creates truthy attributes on any
    # ``getattr``, which would spuriously trigger the guard.
    if vars(app).get("_is_restarting", False) and not vars(app).get("_in_place_restart", False):
        with contextlib.suppress(Exception):
            launched_pid = getattr(app, "_electron_pid", None)
            if launched_pid:
                log.info(
                    "[SHUTDOWN] Restart in progress — leaving Electron subprocess "
                    "(PID=%s) alive so it can relaunch the backend",
                    launched_pid,
                )
        return
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
                    # Escalate on timeout — POSIX gets
                    # SIGKILL; Windows gets a ctypes
                    # TerminateProcess fallback. Pre-fix the POSIX
                    # branch had SIGKILL escalation but the Windows
                    # branch was a silent no-op on timeout (the
                    # electron process tree would keep running).
                    if is_windows():
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

                        # ``signal.SIGKILL`` is absent on Windows Python;
                        # the POSIX branch only runs when
                        # ``is_windows()`` is False, but the constant
                        # itself is still resolved on every platform —
                        # fall back to the POSIX value (9) so the branch
                        # works even when executed on a Windows host
                        # (e.g. unit tests forcing the POSIX path).
                        with contextlib.suppress(OSError, ProcessLookupError):
                            os.kill(launched_pid, getattr(_sig_kill, "SIGKILL", 9))
                app._electron_pid = None
            else:
                from voice_typer.server.tray_window import get_electron_pid

                electron_pid = get_electron_pid()
                if electron_pid is not None:
                    # Dedupe: this fallback branch previously
                    # re-implemented the SIGTERM → grace-wait → SIGKILL
                    # escalation inline — a third parallel copy of the
                    # same logic that already lives in
                    # ``electron_launcher.terminate_electron`` (used by
                    # the tracked-PID branch above) and in the TS
                    # kill-python helper. Delegating to the shared
                    # helper keeps ONE escalator per runtime. The
                    # helper is best-effort by contract (all failures
                    # logged + swallowed), matching this branch's old
                    # suppress-everything behaviour; the outer
                    # ``_run_with_timeout(5.0)`` still bounds it.
                    log.info(
                        "[SHUTDOWN] Terminating Electron subprocess (PID=%s, shared helper)",
                        electron_pid,
                    )
                    _term_result = _run_with_timeout(
                        "electron_launcher.terminate_electron",
                        lambda: electron_launcher.terminate_electron(electron_pid),
                        timeout=5.0,
                    )
                    if _term_result is TIMEOUT:
                        with contextlib.suppress(OSError, ProcessLookupError):
                            os.kill(electron_pid, 9)  # SIGKILL
    except Exception:
        log.debug("[SHUTDOWN] Electron subprocess termination failed", exc_info=True)


__all__ = ["teardown_electron"]
