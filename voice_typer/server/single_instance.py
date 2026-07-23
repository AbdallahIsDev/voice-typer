"""Single-instance enforcement via platform-specific locking.

Extracted from ``voice_typer/server/app.py`` (REF-3) so the entry file can
stay focused on orchestration. The functions here are re-exported from
``app.py`` for backwards compatibility — tests and other modules that do
``from voice_typer.server.app import _ensure_single_instance`` continue to
work.

SEC-001: the Windows mutex name is ``"Local\\VoiceTyperSingleInstance"`` and is
secured with a restrictive DACL built by
:func:`voice_typer.server._security_attributes._create_restrictive_security_attributes`.
The ``VOICE_TYPER_RESTART`` bypass is time-limited to 30 seconds — the
restart token file must have been modified within the last 30 seconds for
the bypass to be accepted.

PLAT-011: on ``error_already_exists`` we exit IMMEDIATELY — no retry loop.

CR-11: on POSIX (Linux/macOS) single-instance is enforced via an exclusive
``fcntl.flock`` on ``<config_dir>/voice-typer.lock``. The fd is held for the
process lifetime (closed by ``_PosixSingleInstanceHandle.release()`` during
graceful shutdown). The kernel auto-releases the flock if the process dies,
so unlike the Windows named mutex there is no abandoned-lock recovery path
— ``flock`` is authoritative.
"""

import logging
import os
import sys
from pathlib import Path

from voice_typer.server._security_attributes import (
    _create_restrictive_security_attributes,
)
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows

# CR-11: POSIX-only flock import. ``fcntl`` is part of the stdlib on
# Linux/macOS but does not exist on Windows. The try/except keeps the
# module importable on Windows (where the POSIX path is never taken).
try:
    import fcntl  # type: ignore[import-not-found]  # POSIX-only stdlib
except ImportError:  # pragma: no cover - Windows path
    fcntl = None  # type: ignore[assignment]

# CR-11: module-level binding of ``_config_dir`` so tests can monkeypatch
# ``voice_typer.server.single_instance._config_dir`` and have the POSIX
# single-instance path honor it. ``_backend_pid_file()`` continues to
# resolve ``_config_dir`` lazily via ``voice_typer.server.app`` so the
# existing tests that monkeypatch ``voice_typer.server.app._config_dir``
# (test_electron_launcher.py::TestBackendPidFile) keep working unchanged.
from voice_typer.server.config import (  # noqa: E402,F401 — re-exported for monkeypatching
    _config_dir,
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
    # Resolve lazily via ``voice_typer.server.app`` (NOT via the
    # module-level ``_config_dir`` re-exported above) so monkeypatching
    # ``voice_typer.server.app._config_dir`` in tests takes effect at
    # call time.  Importing the module (rather than the name) avoids an
    # F811 redefinition warning against the module-level binding while
    # preserving the lazy-lookup semantics documented above.
    from voice_typer.server import app as _app_module

    return _app_module._config_dir() / "backend.pid"


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
        # CR-95 (Fix-B + Fix-I coordination): surface unexpected
        # programming bugs (e.g. ``_is_pid_alive`` raising something
        # other than OSError) at DEBUG level so the next launch's
        # "Only one instance can run" failure has a traceable root
        # cause in the log. Previously this was a bare ``return None``
        # which silently looked like "no stale PID" — the genuine-
        # duplicate path then exited with a confusing error message.
        #
        # ``single_instance.py`` is co-owned by Fix-B and Fix-I per
        # the disjoint ownership table (Fix-I owns the file; Fix-B
        # was permitted to touch this single function for CR-95).
        # Fix-B landed this fix first; Fix-I confirms it satisfies
        # CR-95 and adds this coordination note. No further change
        # needed here.
        log.debug(
            "[STARTUP] Unexpected error reading stale backend PID file",
            exc_info=True,
        )
        return None


def _ensure_single_instance(silent: bool = False):
    """Enforce single-instance via platform-specific locking.

    CR-11: dispatches to the Windows named-mutex path or the POSIX
    flock path. Both paths honor the ``VOICE_TYPER_RESTART`` bypass
    (time-limited to 30s, SEC-001) which is evaluated here so the
    logic is shared.

    Returns
    -------
    On Windows: the mutex handle (kept alive to hold the lock).
    On POSIX: a ``_PosixSingleInstanceHandle`` whose ``release()``
    method closes the lockfile fd (releasing the flock) and clears
    the backend PID file.
    On duplicate launch (either platform): the function exits the
    process via ``sys.exit(1)`` (Windows) or returns ``None`` (POSIX
    — the caller decides whether to exit).
    On restart bypass: returns ``None``.

    Parameters
    ----------
    silent : bool
        If True, skip the MessageBoxW / stderr dialog (caller handles UX).

    On duplicate launch on Windows, ``CreateMutexW`` returns
    ``error_already_exists`` (183) — the authoritative signal that
    another instance owns the lock. We bail immediately.
    (Previously the code second-guessed Windows with a flaky
    ``wmic``-based process scan and, when that scan returned False,
    proceeded to create a *new* mutex — which let duplicate backends
    run simultaneously, causing each recording to be transcribed and
    pasted N times.)

    SEC-001: Uses "Local\\VoiceTyperSingleInstance" with a restrictive
    DACL (only current user SID) to prevent cross-session mutex attacks.
    The VOICE_TYPER_RESTART bypass is time-limited to 30 seconds — the
    restart token file must have been modified within the last 30 seconds
    for the bypass to be accepted.
    """
    if not is_windows():
        # CR-16: POSIX single-instance enforcement via lockfile.
        # Previously returned None immediately on macOS/Linux, leaving
        # NO single-instance gate on the Python backend for POSIX. The
        # autostart launcher could spawn duplicate backends that
        # competed for the microphone, hotkeys, and volume control.
        # The lockfile is created with O_CREAT|O_EXCL|O_CLOEXEC; on
        # EEXIST we read the PID, check liveness, and either exit (alive)
        # or reclaim (stale). The fd is returned (held for process
        # lifetime, analogous to the Windows mutex handle).
        return _ensure_single_instance_posix(silent=silent)

    # Skip mutex check during restart -- old instance releases mutex on quit
    # CR-11: dispatch to the platform-specific enforcement path.
    if sys.platform == "win32":
        return _ensure_windows_single_instance(silent)
    return _ensure_single_instance_posix(silent=silent)


def _ensure_windows_single_instance(silent: bool = False):
    """Windows named-mutex single-instance enforcement (PLAT-011).

    Returns the mutex handle (kept alive to hold the lock) on success,
    or exits the process via ``sys.exit(1)`` on duplicate launch.

    The mutex name is ``"Local\\VoiceTyperSingleInstance"`` (SEC-001)
    with a restrictive DACL so only the current user SID can open it.
    ``error_already_exists`` (183) from ``CreateMutexW`` is the
    authoritative duplicate signal — we exit immediately, no retry.
    """
    import ctypes
    from ctypes import wintypes

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
    # MED-SSS / XCUT-7: the SECURITY_ATTRIBUTES from
    # ``_create_restrictive_security_attributes()`` set
    # ``bInheritHandle=TRUE``, which means the mutex HANDLE is
    # inheritable by child processes spawned via ``subprocess.Popen``
    # (Python's Popen defaults to ``close_fds=False`` on Windows for
    # stdin/stdout/stderr, and the handle is also marked inheritable).
    # A child Python backend spawned for diagnostics would falsely see
    # the mutex as already held (``error_already_exists``) and refuse
    # to start — or worse, would inherit a duplicate handle that kept
    # the named object alive even after the parent quit. We disable
    # inheritance via ``SetHandleInformation(..., HANDLE_FLAG_INHERIT, 0)``
    # immediately after ``CreateMutexW`` returns so no child ever
    # inherits this handle. (The restrictive DACL still applies; we're
    # only clearing the inheritance bit.)
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(lp_mutex_attributes, True, mutex_name)
    last_error = kernel32.GetLastError()

    # MED-SSS / XCUT-7: clear HANDLE_FLAG_INHERIT on the mutex handle
    # so ``subprocess.Popen`` children don't inherit it. Best-effort —
    # ``SetHandleInformation`` exists on every Windows version since
    # NT 3.5, but we wrap it defensively in case a stripped-down
    # kernel32 (e.g. Wine, Windows RT) lacks the export. Failure here
    # doesn't block startup; the original inheritable-handle behavior
    # is the pre-fix status quo and is no worse than before.
    HANDLE_FLAG_INHERIT = 0x00000001
    if mutex:
        try:
            kernel32.SetHandleInformation.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            kernel32.SetHandleInformation.restype = wintypes.BOOL
            kernel32.SetHandleInformation(mutex, HANDLE_FLAG_INHERIT, 0)
        except Exception:
            log.debug(
                "[STARTUP] SetHandleInformation(HANDLE_FLAG_INHERIT=0) failed — "
                "mutex handle remains inheritable by child processes",
                exc_info=True,
            )

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


def _ensure_single_instance_posix(silent: bool = False):
    """CR-16: POSIX single-instance enforcement via lockfile.

    Mirrors the Windows mutex path's contract: returns a handle (the
    lockfile fd) that must be held for the process lifetime; on
    duplicate-instance detection, exits the process.

    Lockfile path: ``<config_dir>/backend.lock`` (mode 0o600).
    On EEXIST: read the PID, check liveness via ``_is_pid_alive``. If
    alive, exit. If stale (dead/garbage/empty), unlink + retry once.
    On retry failure, exit.

    Also writes the backend PID file (previously Windows-only) so the
    autostart launcher's "backend running?" check works on POSIX.
    """
    import fcntl

    from voice_typer.server._paths import config_dir

    cdir = config_dir()
    try:
        cdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        if not silent and sys.stderr is not None:
            print("Voice Typer: cannot create config dir.", file=sys.stderr)
        sys.exit(1)

    lock_path = cdir / "backend.lock"

    def _try_acquire(path):
        try:
            fd = os.open(
                str(path),
                os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC,
                0o600,
            )
            return fd
        except FileExistsError:
            return None
        except OSError as exc:
            if not silent and sys.stderr is not None:
                print(f"Voice Typer: cannot create lock file: {exc}", file=sys.stderr)
            sys.exit(1)

    fd = _try_acquire(lock_path)
    if fd is None:
        # Lockfile exists — check if the holder is alive.
        try:
            with open(lock_path) as f:
                pid_str = f.read().strip()
            pid = int(pid_str) if pid_str.isdigit() else None
        except (OSError, ValueError):
            pid = None

        if pid is not None and _is_pid_alive(pid):
            msg = f"Voice Typer: another instance is running (pid={pid})."
            if not silent and sys.stderr is not None:
                print(msg, file=sys.stderr)
            sys.exit(1)

        # Stale lock — unlink and retry once.
        try:
            os.unlink(lock_path)
        except OSError:
            pass
        fd = _try_acquire(lock_path)
        if fd is None:
            msg = "Voice Typer: another instance is running."
            if not silent and sys.stderr is not None:
                print(msg, file=sys.stderr)
            sys.exit(1)

    # Write our PID into the lockfile for diagnostics.
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.fsync(fd)
    except OSError:
        pass

    # Acquire an advisory flock for extra safety (NFS etc.).
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another process holds the flock — exit.
        try:
            os.close(fd)
        except OSError:
            pass
        msg = "Voice Typer: another instance is running (lock held)."
        if not silent and sys.stderr is not None:
            print(msg, file=sys.stderr)
        sys.exit(1)

    # CR-16: also write the backend PID file on POSIX (previously
    # Windows-only) so the autostart launcher's PID-file check works.
    _write_backend_pid_file()
    return fd
