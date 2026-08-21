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
                    # UE-1-F6: escalate on timeout — POSIX gets
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
                    import signal as _sig

                    log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", electron_pid)
                    # SIGTERM → 2s blocking waitpid → SIGKILL on POSIX.
                    # ``os.kill(SIGTERM)`` returns immediately (it just
                    # queues the signal); the 2s waitpid gives the child
                    # a grace period to exit cleanly before we escalate
                    # to SIGKILL.
                    #
                    # The previous implementation used a 2s busy-wait
                    # poll loop (``deadline = time.monotonic() + 2.0;
                    # while ...: os.waitpid(pid, WNOHANG);
                    # time.sleep(0.1)``) — 20 iterations × 0.1s sleep =
                    # 20 kernel wakeups for what is a single blocking
                    # wait. Replaced with a single
                    # ``os.waitpid(electron_pid, 0)`` (the blocking
                    # variant — ``WNOHANG`` removed) wrapped in
                    # ``_run_with_timeout(timeout=2.0)`` so the 2s grace
                    # period is enforced by a SINGLE waitpid syscall +
                    # at most one worker-thread join. On Windows the
                    # loop is already skipped (``not is_windows()``
                    # check below) so this change is POSIX-only.
                    with contextlib.suppress(OSError, ProcessLookupError):
                        os.kill(electron_pid, _sig.SIGTERM)
                    reaped = False
                    if not is_windows():
                        # Single blocking ``os.waitpid`` wrapped
                        # in ``_run_with_timeout``. ``_run_with_timeout``
                        # re-raises OSError (e.g. ECHILD when the child
                        # was already reaped by a prior call); catch
                        # that here and treat it as "reaped" (matching
                        # the original poll-loop's behaviour, which
                        # broke out of the loop on OSError).
                        try:
                            _waitpid_result = _run_with_timeout(
                                "electron.waitpid",
                                lambda: os.waitpid(electron_pid, 0),
                                timeout=2.0,
                            )
                            if _waitpid_result is not TIMEOUT:
                                # ``os.waitpid`` returns ``(pid, status)``;
                                # any non-error return means the child was
                                # reaped.
                                reaped = True
                        except OSError:
                            # Child already reaped or not a child of
                            # this process — treat as reaped (no need
                            # to SIGKILL).
                            reaped = True
                    if not reaped and not is_windows():
                        with contextlib.suppress(OSError, ProcessLookupError):
                            os.kill(electron_pid, getattr(_sig, "SIGKILL", 9))
    except Exception:
        log.debug("[SHUTDOWN] Electron subprocess termination failed", exc_info=True)


__all__ = ["teardown_electron"]
