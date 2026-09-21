"""Single-instance mutex/guard so concurrent launches do not double-start."""

import contextlib
import errno
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from voice_typer.server._security_attributes import (
    _create_restrictive_security_attributes,
)
from voice_typer.server.backend_pid import (  # noqa: F401, re-exported for backwards compatibility
    _backend_pid_file,
    _clear_backend_pid_file,
    _is_pid_alive,
)
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_windows

# POSIX-only flock import. ``fcntl`` is part of the stdlib on
if TYPE_CHECKING:
    from types import ModuleType

    fcntl: ModuleType | None
else:
    try:
        import fcntl  # POSIX-only stdlib
    except ImportError:  # pragma: no cover - Windows path
        fcntl = None

# module-level binding of ``_config_dir`` so tests can monkeypatch
from voice_typer.server.config import (  # noqa: E402,F401, re-exported for monkeypatching
    _config_dir,
)

log = logging.getLogger(__name__)


def _startup_line(level: str, msg: str) -> None:
    """Emit a startup line BEFORE logging is configured.

    ``_ensure_single_instance`` now runs before ``_setup_logging()``
    (ipc/entrypoint.py reordered it so a duplicate launch exits without
    initializing logging, installing the VEH crash handler, or printing
    the startup banner). At that point the ``log`` logger has no
    handlers, so ``log.info``/``log.warning`` would be silently dropped
    (or hit the ugly ``logging.lastResort`` fallback formatter). Write a
    clean terminal-style line to stderr instead, the same
    ``HH:MM:SS  [WARN ]msg`` shape the terminal formatter produces
    (C-LOG-1: time-only on the terminal, INFO level label omitted).
    Harmless under ``pythonw.exe`` where stderr is devnull, the line is
    simply invisible there, exactly as before.
    """
    try:
        stamp = time.strftime("%H:%M:%S")
        if sys.stderr is None or sys.stderr.closed:
            return
        if level == "WARN":
            print(f"{stamp}  WARN {msg}", file=sys.stderr, flush=True)
        else:
            print(f"{stamp}  {msg}", file=sys.stderr, flush=True)
    except (OSError, ValueError):
        # Best-effort only: stderr may be closed/devnull (pythonw.exe).
        pass


class _PosixSingleInstanceHandle(int):
    """Wrapper around the POSIX single-instance lockfile fd.

        Subclasses ``int`` so existing callers/tests that treat the return
        value of ``_ensure_single_instance_posix`` as a raw fd continue to
        work (``isinstance(handle, int)`` is True, ``handle > 0`` works,
        ``os.close(handle)`` works on the int value).

        The wrapper exists so graceful shutdown can explicitly close the fd
        (releasing the ``fcntl.flock``), mirroring the Windows path's
        ``CloseHandle`` step. Without it, the POSIX fd was stored on
        ``ipc_server._single_instance_mutex`` and only released implicitly
        by process exit, which on graceful restart could leave the fd
        dangling long enough to interfere with a fast re-launch.

        ``release()`` is idempotent: subsequent calls are no-ops. It is
        safe to call after the underlying fd has already been closed
        (e.g., by a test's ``os.close(handle)`` cleanup), the
        ``OSError`` from the double-close is suppressed at DEBUG level.

    coordination note: ``shutdown_controller._do_cleanup``
    (owned by ) should call
        ``app._single_instance_handle.release()`` for the POSIX branch
    after the Windows ``CloseHandle`` step.  will add that
        call; this module only exposes the handle and the ``release()``
        API. The handle SHOULD be stored on
        ``app._single_instance_handle`` by ``app.py``'s startup path so
    ``_do_cleanup`` can find it (that assignment is also 's
        responsibility since ``app.py`` is owned by another fix agent).
    """

    # NOTE: ``int`` subclasses do NOT support non-empty ``__slots__``
    _lock_path: Path | None
    _released: bool

    def __new__(cls, fd: int, lock_path: Path | None = None):
        instance = super().__new__(cls, fd)
        instance._lock_path = lock_path
        instance._released = False
        return instance

    def release(self) -> None:
        """Close the underlying fd (releasing the ``flock``) and unlink
        the lockfile (best-effort).

        Idempotent and best-effort: errors from ``os.close`` /
        ``os.unlink`` are suppressed at DEBUG level. Safe to call
        after the underlying fd has already been closed by other
        means (e.g. ``os.close(handle)`` in a test teardown).
        """
        if self._released:
            return
        self._released = True
        try:
            os.close(int(self))
        except OSError:
            log.debug(
                "[SHUTDOWN] POSIX single-instance fd close failed",
                exc_info=True,
            )
        # Best-effort unlink, if the file was already removed (e.g.,
        if self._lock_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(self._lock_path)


def _write_backend_pid_file() -> None:
    """Write our PID to the backend PID file (best-effort)."""
    try:
        from voice_typer.server.config import _secure_atomic_write

        pid_file = _backend_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        # durability=False, the PID file is recreated on every
        _secure_atomic_write(pid_file, f"{os.getpid()}\n", durability=False)
    except OSError as exc:
        log.warning("[STARTUP] could not write backend PID file: %s", exc)
    except Exception:
        log.debug("[STARTUP] could not write backend PID file", exc_info=True)


def _record_backend_ipc_port(port: int) -> None:
    """Record the bound IPC port in the backend PID file (best-effort)."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return
    if not 1 <= port <= 65535:
        return
    try:
        from voice_typer.server.config import _secure_atomic_write

        pid_file = _backend_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        _secure_atomic_write(pid_file, f"{os.getpid()}\nport={port}\n", durability=False)
    except OSError as exc:
        log.warning("[STARTUP] could not record backend IPC port: %s", exc)
    except Exception:
        log.debug("[STARTUP] could not record backend IPC port", exc_info=True)


def _read_stale_backend_pid() -> int | None:
    """Return the PID from the backend PID file if it's stale, else None."""
    try:
        pid_file = _backend_pid_file()
        if not pid_file.exists():
            return None
        content = pid_file.read_text().strip()
        if not content:
            return None
        # PID-first format: the first line is always the PID (a later
        pid = int(content.splitlines()[0].strip())
        if _is_pid_alive(pid):
            return None
        return pid
    except (OSError, ValueError):
        return None
    except Exception:
        # (Fix-B + Fix-I coordination): surface unexpected
        log.debug(
            "[STARTUP] Unexpected error reading stale backend PID file",
            exc_info=True,
        )
        return None


def _ensure_single_instance(silent: bool = False):
    """Enforce single-instance via platform-specific locking.

    dispatches to the Windows named-mutex path or the POSIX
        flock path. The ``VOICE_TYPER_RESTART`` env var is a hint that a
        restart is in progress (no time-limited token bypass, the
        old instance must release the mutex/flock before the new instance
        can acquire it).

        Returns
        -------
        On Windows: the mutex handle (kept alive to hold the lock).
        On POSIX: a ``_PosixSingleInstanceHandle`` whose ``release()``
        method closes the lockfile fd (releasing the flock) and clears
        the backend PID file.
        On duplicate launch (either platform): the function exits the
        process via ``sys.exit(1)`` (Windows) or returns ``None`` (POSIX
       , the caller decides whether to exit).
        On restart bypass: returns ``None``.

        Parameters
        ----------
        silent : bool
            If True, skip the Windows MessageBoxW dialog (the caller, e.g.
            the predecessor frontend, handles the "already running" UX). The
            stderr line always prints regardless of ``silent``, it is the
            single user-facing "already running" diagnostic in a terminal;
            under ``pythonw.exe`` stderr is devnull so it is invisible
            there (no packaged-app UX regression).

        On duplicate launch on Windows, ``CreateMutexW`` returns
        ``error_already_exists`` (183), the authoritative signal that
        another instance owns the lock. We bail immediately.
        (Previously the code second-guessed Windows with a flaky
        ``wmic``-based process scan and, when that scan returned False,
        proceeded to create a *new* mutex: which let duplicate backends
        run simultaneously, causing each recording to be transcribed and
        pasted N times.)

        SEC-001: Uses "Local\\VoiceTyperSingleInstance" with a restrictive
        DACL (only current user SID) to prevent cross-session mutex attacks.
        The ``VOICE_TYPER_RESTART`` env var is honored as a restart hint
        only, there is no time-limited token file; the old instance must
        release the mutex/flock before the new instance can acquire it.
    """
    # dispatch to the platform-specific enforcement path via
    if is_windows():
        return _ensure_windows_single_instance(silent)
    # POSIX single-instance enforcement via lockfile.
    return _ensure_single_instance_posix(silent=silent)


def _ensure_windows_single_instance(silent: bool = False):
    """Windows named-mutex single-instance enforcement ().

    Returns the mutex handle (kept alive to hold the lock) on success,
    or exits the process via ``sys.exit(1)`` on duplicate launch.

    The mutex name is ``"Local\\VoiceTyperSingleInstance"`` (SEC-001)
    with a restrictive DACL so only the current user SID can open it.
    ``error_already_exists`` (183) from ``CreateMutexW`` is the
    authoritative duplicate signal, we exit immediately, no retry.
    """
    import ctypes
    from ctypes import wintypes

    error_already_exists = 183
    error_access_denied = 5

    # SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL that
    mutex_name = "Local\\VoiceTyperSingleInstance"

    # Build a restrictive DACL for the mutex
    sa = _create_restrictive_security_attributes()
    lp_mutex_attributes = ctypes.byref(sa) if sa is not None else None

    # Use CreateMutexW with bInitialOwner=True so WE own the handle.
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(lp_mutex_attributes, True, mutex_name)
    last_error = kernel32.GetLastError()

    # Clear HANDLE_FLAG_INHERIT on the mutex handle
    HANDLE_FLAG_INHERIT = 0x00000001  # noqa: N806
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
                "[STARTUP] SetHandleInformation(HANDLE_FLAG_INHERIT=0) failed, "
                "mutex handle remains inheritable by child processes",
                exc_info=True,
            )

    if last_error == error_already_exists:
        # Belt-and-suspenders check.  Windows guarantees that
        stale_pid = _read_stale_backend_pid()
        if stale_pid is not None:
            _startup_line(
                "WARN",
                "[STARTUP] Another instance's lock exists, but its PID file "
                f"points to a dead process ({stale_pid}), reclaiming the lock "
                "and proceeding",
            )
            _clear_backend_pid_file()
        # The probe below (WaitForSingleObject) is the authoritative check
        wait_abandoned = 0x00000080
        wait_object_0 = 0x00000000
        if mutex:
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
            if wait_result == wait_abandoned:
                # Previous instance crashed.  The mutex is now OURS.
                _startup_line(
                    "WARN",
                    "[STARTUP] The previous instance's lock was abandoned (it crashed), acquired ownership, proceeding",
                )
                _write_backend_pid_file()
                return mutex
            elif wait_result == wait_object_0:
                # Unexpectedly acquired the mutex.  Proceed anyway.
                _startup_line(
                    "WARN",
                    "[STARTUP] Mutex unexpectedly acquired after duplicate signal, proceeding",
                )
                _write_backend_pid_file()
                return mutex
            # WAIT_TIMEOUT (or any other result) → genuine duplicate.
        _startup_line(
            "INFO",
            "[STARTUP] Voice Typer is already running, only one instance is allowed",
        )
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
    # Mutex acquired, write our PID so the next launch can
    _write_backend_pid_file()
    return mutex


def _ensure_single_instance_posix(silent: bool = False):
    """POSIX single-instance enforcement via lockfile."""
    import fcntl

    from voice_typer.server import config as _config_module

    cdir = _config_module._config_dir()
    try:
        # The defensive ``os.chmod`` after creation handles the case
        cdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            os.chmod(cdir, 0o700)
        # O3: transient runtime state (pids, locks, session markers)
        run_dir = cdir / "run"
        run_dir.mkdir(exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            os.chmod(run_dir, 0o700)
    except OSError:
        if not silent and sys.stderr is not None:
            print("Voice Typer: cannot create config dir.", file=sys.stderr)
        sys.exit(1)

    lock_path = run_dir / "backend.lock"

    def _try_acquire(path):
        try:
            # add ``O_NOFOLLOW`` so ``os.open`` fails with ELOOP
            fd = os.open(
                str(path),
                os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            return fd
        except FileExistsError:
            return None
        except OSError as exc:
            # ``O_NOFOLLOW`` raises ``ELOOP`` (errno 40) on Linux when
            if not silent and sys.stderr is not None:
                print(f"Voice Typer: cannot create lock file: {exc}", file=sys.stderr)
            sys.exit(1)

    def _read_pid_from_lockfile(path):
        try:
            with open(path) as f:
                pid_str = f.read().strip()
            return int(pid_str) if pid_str.isdigit() else None
        except (OSError, ValueError):
            return None

    fd = _try_acquire(lock_path)
    if fd is None:
        # O_EXCL failed, lockfile exists. Try flock FIRST.
        existing_fd: int | None = None
        try:
            # Add ``O_NOFOLLOW`` to close a TOCTOU symlink race.
            existing_fd = os.open(
                str(lock_path),
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
        except OSError as exc:
            # Cannot open the existing lockfile (e.g., restrictive
            if exc.errno == errno.ELOOP:
                # Explicit ELOOP handling so operators grep-ing
                log.debug(
                    "single_instance: backend.lock is a symlink (ELOOP on "
                    "secondary open), falling through to legacy PID-check path"
                )
            existing_fd = None

        if existing_fd is not None:
            try:
                fcntl.flock(existing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # flock succeeded → previous holder is dead
                try:
                    os.lseek(existing_fd, 0, os.SEEK_SET)
                    os.ftruncate(existing_fd, 0)
                    os.write(existing_fd, str(os.getpid()).encode("ascii"))
                    os.fsync(existing_fd)
                except OSError:
                    pass
                _write_backend_pid_file()
                return _PosixSingleInstanceHandle(existing_fd, lock_path)
            except OSError as exc:
                # Close the existing_fd we opened; we won't use it.
                with contextlib.suppress(OSError):
                    os.close(existing_fd)
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    # Another LIVE process holds the flock.
                    pid = _read_pid_from_lockfile(lock_path)
                    if pid is not None:
                        msg = f"Voice Typer: another instance is already running (pid={pid})."
                    else:
                        msg = "Voice Typer: another instance is already running (lock held)."
                    if not silent and sys.stderr is not None:
                        print(msg, file=sys.stderr)
                    sys.exit(1)
                # Unexpected flock errno, fall through to the legacy

        # Legacy fallback: PID check + unlink + retry. Used when
        pid = _read_pid_from_lockfile(lock_path)
        if pid is not None and _is_pid_alive(pid):
            msg = f"Voice Typer: another instance is already running (pid={pid})."
            if not silent and sys.stderr is not None:
                print(msg, file=sys.stderr)
            sys.exit(1)

        # Stale lock, unlink and retry once.
        with contextlib.suppress(OSError):
            os.unlink(lock_path)
        fd = _try_acquire(lock_path)
        if fd is None:
            msg = "Voice Typer: another instance is already running."
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
        # Another process holds the flock, exit.
        with contextlib.suppress(OSError):
            os.close(fd)
        msg = "Voice Typer: another instance is already running (lock held)."
        if not silent and sys.stderr is not None:
            print(msg, file=sys.stderr)
        sys.exit(1)

    # also write the backend PID file on POSIX (previously
    _write_backend_pid_file()
    # wrap the fd in a ``_PosixSingleInstanceHandle`` so the
    return _PosixSingleInstanceHandle(fd, lock_path)
