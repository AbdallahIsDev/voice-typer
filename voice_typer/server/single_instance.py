"""Single-instance enforcement via a Win32 named mutex.

Extracted from ``voice_typer/server/app.py`` (REF-3) so the entry file can
stay focused on orchestration. The functions here are re-exported from
``app.py`` for backwards compatibility — tests and other modules that do
``from voice_typer.server.app import _ensure_single_instance`` continue to
work.

SEC-001: the mutex name is ``"Local\\VoiceTyperSingleInstance"`` and is
secured with a restrictive DACL built by
:func:`voice_typer.server._security_attributes._create_restrictive_security_attributes`.
The ``VOICE_TYPER_RESTART`` bypass is time-limited to 30 seconds — the
restart token file must have been modified within the last 30 seconds for
the bypass to be accepted.

PLAT-011: on ``error_already_exists`` we exit IMMEDIATELY — no retry loop.
"""

import logging
import os
import sys
import time
from pathlib import Path

from voice_typer.server._security_attributes import (
    _create_restrictive_security_attributes,
)
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.security import (
    consume_restart_token as _consume_restart_token,
)
from voice_typer.server.security import (
    verify_restart_token as _verify_restart_token,
)

log = logging.getLogger(__name__)


def _backend_pid_file() -> Path:
    """Return the path to the backend PID file (``<config_dir>/backend.pid``).

    P1-1.4: written by ``_ensure_single_instance`` after the mutex is
    acquired, removed by ``_clear_backend_pid_file`` during shutdown.
    Used as a belt-and-suspenders check: on Windows the named mutex is
    the authoritative single-instance guard, but if a previous instance
    crashed hard (BSOD, power loss) the OS may not have released the
    mutex yet when the next launch tries to acquire it.  The PID file
    lets us detect a stale lock and proceed.

    COMPAT-REFAC: ``_config_dir`` is resolved lazily via
    ``voice_typer.server.app`` so tests that monkeypatch
    ``voice_typer.server.app._config_dir`` are honored by
    ``_write_backend_pid_file`` / ``_clear_backend_pid_file`` /
    ``_read_stale_backend_pid`` (which all call this helper).
    """
    from voice_typer.server.app import _config_dir

    return _config_dir() / "backend.pid"


def _write_backend_pid_file() -> None:
    """Write our PID to the backend PID file (best-effort)."""
    try:
        from voice_typer.server.config import _secure_atomic_write

        pid_file = _backend_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        _secure_atomic_write(pid_file, f"{os.getpid()}\n")
    except OSError as exc:
        log.warning("[STARTUP] could not write backend PID file: %s", exc)
    except Exception:
        log.debug("[STARTUP] could not write backend PID file", exc_info=True)


def _clear_backend_pid_file() -> None:
    """Remove the backend PID file (best-effort)."""
    try:
        pid_file = _backend_pid_file()
        if pid_file.exists():
            pid_file.unlink()
    except OSError as exc:
        log.debug("[SHUTDOWN] could not remove backend PID file: %s", exc)
    except Exception:
        log.debug("[SHUTDOWN] could not remove backend PID file", exc_info=True)


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running.

    Cross-platform: uses ``os.kill(pid, 0)`` on POSIX and ``OpenProcess``
    on Windows.  Returns False if the PID is invalid or the process has
    exited.  On Windows, error_access_denied (5) is treated as "alive"
    (the process exists but is owned by another session — better to
    block a duplicate than to proceed when unsure).
    """
    if pid <= 0:
        return False
    if is_windows():
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            still_active = wintypes.DWORD()
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                # error_access_denied (5) means the process exists but is
                # owned by another user/session — treat as alive.
                return kernel32.GetLastError() == 5
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(still_active)):
                    return False
                # STILL_ACTIVE == 259 means the process is running.
                return still_active.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True


def _read_stale_backend_pid() -> int | None:
    """Return the PID from the backend PID file if it's stale, else None.

    A PID is "stale" if the file exists but no process with that PID is
    alive.  Returns None if the file doesn't exist, is unreadable, or
    the PID is still alive.
    """
    try:
        pid_file = _backend_pid_file()
        if not pid_file.exists():
            return None
        content = pid_file.read_text().strip()
        if not content:
            return None
        pid = int(content)
        if _is_pid_alive(pid):
            return None
        return pid
    except (OSError, ValueError):
        return None
    except Exception:
        return None


def _ensure_single_instance(silent=False):
    """Enforce single-instance via a Windows named mutex.

    Returns the mutex handle (kept alive to hold the lock) on Windows,
    or None on other platforms.
    Skipped when VOICE_TYPER_RESTART env var is set (restart flow).

    Parameters
    ----------
    silent : bool
        If True, skip the MessageBoxW dialog (caller handles UX).

    On duplicate launch, Windows returns ``error_already_exists`` from
    ``CreateMutexW`` — this is the authoritative signal that another
    instance owns the lock.  We bail immediately.  (Previously the code
    second-guessed Windows with a flaky ``wmic``-based process scan and,
    when that scan returned False, proceeded to create a *new* mutex —
    which let duplicate backends run simultaneously, causing each
    recording to be transcribed and pasted N times.)

    SEC-001: Uses "Local\\VoiceTyperSingleInstance" with a restrictive
    DACL (only current user SID) to prevent cross-session mutex attacks.
    The VOICE_TYPER_RESTART bypass is time-limited to 30 seconds — the
    restart token file must have been modified within the last 30 seconds
    for the bypass to be accepted.
    """
    if not is_windows():
        return None

    # Skip mutex check during restart -- old instance releases mutex on quit
    if os.environ.get("VOICE_TYPER_RESTART"):
        if _verify_restart_token():
            # SEC-001 (revised): Time-limit the restart bypass — only
            # allow if the restart token was generated within the last
            # 30 seconds. The previous code used ``time.time() - mtime``
            # which is vulnerable to system clock jumps (NTP sync,
            # daylight saving, manual changes). If the clock jumps
            # backward, age goes negative (silently bypassing the 30s
            # window); if forward, age gets inflated (false denials).
            #
            # Fix: detect clock-jump anomalies (negative age or age > 1 day)
            # and deny the bypass in those cases. The 30s window is short
            # enough that legitimate restarts won't be affected, but a
            # 1-day cap catches clock-jump corruption.
            try:
                from voice_typer.server.config import _config_dir

                token_path = _config_dir() / ".restart_token"
                if token_path.exists():
                    mtime = token_path.stat().st_mtime
                    age = time.time() - mtime
                    # SEC-001: detect clock jumps
                    if age < 0:
                        log.warning(
                            "[STARTUP] Restart token age is negative (%.1fs) — "
                            "system clock may have jumped backward. Blocking "
                            "duplicate launch to be safe.",
                            age,
                        )
                        if not silent and sys.stderr is not None:
                            print(
                                "Voice Typer: clock jump detected, duplicate launch blocked.",
                                file=sys.stderr,
                            )
                        sys.exit(1)
                    if age > 86400.0:  # > 1 day — almost certainly a clock jump
                        log.warning(
                            "[STARTUP] Restart token age is suspiciously large "
                            "(%.1fs > 86400s) — system clock may have jumped "
                            "forward. Blocking duplicate launch.",
                            age,
                        )
                        if not silent and sys.stderr is not None:
                            print(
                                "Voice Typer: clock jump detected, duplicate launch blocked.",
                                file=sys.stderr,
                            )
                        sys.exit(1)
                    if age > 30.0:
                        log.warning(
                            "[STARTUP] Restart token too old (%.1fs > 30s) — blocking duplicate launch",
                            age,
                        )
                        # Don't consume the token; let it expire naturally
                        if not silent and sys.stderr is not None:
                            print(
                                "Voice Typer: restart token expired, duplicate launch blocked.",
                                file=sys.stderr,
                            )
                        sys.exit(1)
            except SystemExit:
                raise  # don't catch sys.exit
            except Exception:
                # If we can't check the time, deny the bypass (safe default)
                log.warning("[STARTUP] Cannot verify restart token age — blocking duplicate")
                sys.exit(1)
            # Valid and recent restart token — consume it
            _consume_restart_token()
            return None
        # Invalid token — treat as duplicate launch
        log.warning("[STARTUP] VOICE_TYPER_RESTART set but token invalid — blocking duplicate")

    import ctypes

    error_already_exists = 183
    error_access_denied = 5

    # SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL that
    # only allows the current user to access the mutex. This prevents
    # other sessions/users from opening or manipulating our mutex.
    # PLAT-RUN-FIXED: The mutex name is now a fixed string so ALL
    # VoiceTyper processes (regardless of Python executable) share the
    # same mutex. Previously it included sys.executable hash, which let
    # different Python executables (python.exe vs pythonw.exe, dev venv
    # vs production install) run as separate instances.
    mutex_name = "Local\\VoiceTyperSingleInstance"

    # Build a restrictive DACL for the mutex
    sa = _create_restrictive_security_attributes()
    lp_mutex_attributes = ctypes.byref(sa) if sa is not None else None

    # Use CreateMutexW with bInitialOwner=True so WE own the handle.
    # The Windows mutex handle is inheritable across CreateProcess /
    # subprocess.Popen, so a child spawned by the parent will see the
    # mutex as already owned.  We can't disable handle inheritance from
    # Python; the inheritance concern is real but handled separately:
    # Electron's main process kills stale backends before spawning, and
    # the restart path sets VOICE_TYPER_RESTART to skip this check.
    mutex = ctypes.windll.kernel32.CreateMutexW(lp_mutex_attributes, True, mutex_name)
    last_error = ctypes.windll.kernel32.GetLastError()

    if last_error == error_already_exists:
        # P1-1.4: belt-and-suspenders check.  Windows guarantees that
        # error_already_exists means another process holds the mutex
        # RIGHT NOW.  But if that process is actually a zombie (BSOD,
        # power loss, kill -9 leaving the mutex in a transitional state),
        # the PID file lets us detect the stale state and proceed.
        stale_pid = _read_stale_backend_pid()
        if stale_pid is not None:
            log.warning(
                "[STARTUP] mutex reports duplicate, but PID file points to dead "
                "process %d — clearing stale PID file and proceeding",
                stale_pid,
            )
            _clear_backend_pid_file()
        else:
            log.warning(
                "[STARTUP] mutex reports duplicate; PID file missing or PID "
                "still alive — retrying anyway in case mutex was abandoned",
            )
        # Use WaitForSingleObject with zero timeout to check if the
        # mutex is genuinely owned by another live process or was
        # abandoned (previous process crashed).  This is the correct
        # Windows API for distinguishing abandoned mutexes from live
        # ones — CloseHandle+CreateMutexW doesn't work because the
        # named kernel object persists in the \BaseNamedObjects        # namespace even after all handles are closed.
        #
        # WaitForSingleObject return values:
        #   wait_abandoned (0x00000080): previous owner died, WE now
        #     own the mutex → proceed.
        #   WAIT_TIMEOUT  (0x00000102): another live process owns it
        #     → genuine duplicate, exit.
        #   wait_object_0 (0x00000000): we acquired it (unexpected
        #     since CreateMutexW returned error_already_exists).
        wait_abandoned = 0x00000080
        wait_object_0 = 0x00000000
        if mutex:
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
            if wait_result == wait_abandoned:
                # Previous instance crashed.  The mutex is now OURS.
                log.warning(
                    "[STARTUP] Mutex was abandoned (previous instance crashed) — acquired ownership, proceeding"
                )
                _write_backend_pid_file()
                return mutex
            elif wait_result == wait_object_0:
                # Unexpectedly acquired the mutex.  Proceed anyway.
                log.warning("[STARTUP] Mutex unexpectedly acquired after error_already_exists")
                _write_backend_pid_file()
                return mutex
            # WAIT_TIMEOUT (or any other result) → genuine duplicate.
            # Fall through to sys.exit(1) below.
        # Windows guarantees: this means another process holds the mutex
        # RIGHT NOW.  Trust it — no need to scan for the competing
        # process (DEAD-013: the old _another_voice_typer_alive() scan
        # had zero decision power — the mutex already proved a
        # duplicate, and the scan result only affected a log message).
        log.info("[STARTUP] Duplicate launch blocked (mutex already held)")
        if not silent:
            msg = "Voice Typer is already running. Only one instance is allowed."
            try:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    msg,
                    APP_NAME,
                    0x00000030 | 0x00000000,  # MB_ICONWARNING | MB_OK
                )
            except Exception:
                if sys.stderr is not None:
                    print(msg, file=sys.stderr)
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)
        sys.exit(1)
    elif last_error == error_access_denied:
        # Couldn't even open the mutex; bail safely.
        if not silent and sys.stderr is not None:
            print("Voice Typer: mutex access denied.", file=sys.stderr)
        sys.exit(1)
    # P1-1.4: mutex acquired — write our PID so the next launch can
    # detect a stale lock if we crash hard.
    _write_backend_pid_file()
    return mutex


# DEAD-013: _another_voice_typer_alive() deleted.
# The Win32 named mutex (VoiceTyperSingleInstance) already proves a
# duplicate exists when error_already_exists is returned — the scan
# had zero decision power (its result only affected a log message).
