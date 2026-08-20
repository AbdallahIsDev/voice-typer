"""Single-instance lock for the worker process (master plan §7.2).

This module is an intentional extraction from ``voice_typer/worker/__main__.py``
per E3 (no spaghetti entry files). It mirrors
:mod:`voice_typer.server.single_instance`'s shape: a lock file in the
canonical app config dir, ``O_CREAT | O_EXCL | O_CLOEXEC`` + ``flock``
on POSIX, best-effort existence check on Windows, and stale-PID
recovery when the holder process is dead.

The worker single-instance lock file name (``worker.lock``) is distinct
from the slim-core sidecar's ``backend.lock`` so the two processes can
run side-by-side (master plan §7.1 "1-host ↔ 2-processes pattern").
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

log = logging.getLogger("voice_typer.worker")

# Worker single-instance lock file name. Distinct from the slim-core
# sidecar's ``backend.lock`` so the two processes can run side-by-side
# (master plan §7.1 "1-host ↔ 2-processes pattern"). Lives in the
# canonical app config dir so it's resolved per-platform (Windows:
# ``%APPDATA%/voice-typer``, macOS: ``~/Library/Application
# Support/voice-typer``, Linux: ``$XDG_DATA_HOME/voice-typer`` —
# resolved via :func:`voice_typer.server.config._config_dir`).
_WORKER_LOCK_NAME = "worker.lock"


def _worker_lock_path() -> Path:
    """Resolve the worker single-instance lock file path.

    Uses :func:`voice_typer.server.config._config_dir` (the canonical
    per-platform app data dir) so the lock file lives under the same
    O3 ``run/`` subdir as the slim-core sidecar's ``backend.lock`` —
    same dir, different file name, so the two processes do not contend
    on the same lock.
    """
    from voice_typer.server._paths import RUN_SUBDIR
    from voice_typer.server.config import _config_dir

    return _config_dir() / RUN_SUBDIR / _WORKER_LOCK_NAME


class _WorkerSingleInstanceHandle:
    """POSIX single-instance lock handle for the worker.

    Mirrors :class:`voice_typer.server.single_instance._PosixSingleInstanceHandle`:
    the fd is held for the process lifetime, ``release()`` closes it and
    unlinks the lockfile (idempotent, best-effort).

    On Windows the lock is best-effort (no named mutex is used here —
    the worker is always spawned by the Tauri host, which already
    enforces single-instance via ``tauri-plugin-single-instance``; the
    Python-side lock is defense-in-depth for dev-runs from a terminal).
    """

    __slots__ = ("_fd", "_path", "_released")

    def __init__(self, fd: int, path: Path) -> None:
        self._fd = fd
        self._path = path
        self._released = False

    def release(self) -> None:
        """Close the lockfile fd (POSIX) / unlink the lockfile (best-effort).

        Idempotent: subsequent calls are no-ops. Safe to call after the
        underlying fd has already been closed by other means (errors
        from ``os.close`` / ``os.unlink`` are suppressed at DEBUG
        level).
        """
        if self._released:
            return
        self._released = True
        if self._fd >= 0:
            with contextlib.suppress(OSError):
                os.close(self._fd)
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)


def _ensure_worker_single_instance() -> _WorkerSingleInstanceHandle | None:
    """Acquire the worker single-instance lock.

    Returns a handle whose ``release()`` method releases the lock (call
    on shutdown). On duplicate launch (POSIX: ``O_EXCL`` fails; Windows:
    existence check), logs at WARNING and returns ``None`` — the caller
    decides whether to exit.

    Stale-PID recovery (POSIX): if the lockfile exists but the PID
    inside is not alive, the lockfile is reclaimed (mirrors
    :func:`voice_typer.server.single_instance._ensure_single_instance_posix`'s
    stale-PID path).
    """
    lock_path = _worker_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.debug("[WORKER] could not create lockfile parent dir — single-instance is best-effort", exc_info=True)
        return None

    if os.name == "posix":
        import fcntl  # POSIX-only stdlib

        # O_CREAT | O_EXCL | O_CLOEXEC: primary mechanism. If the file
        # already exists, we fall through to the stale-PID recovery
        # path (mirrors ``_ensure_single_instance_posix``).
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
            try:
                os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            except OSError:
                log.debug("[WORKER] failed to write PID to lockfile — single-instance is best-effort", exc_info=True)
            # flock as defense-in-depth (mirrors single_instance.py).
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _WorkerSingleInstanceHandle(fd, lock_path)
        except FileExistsError:
            # Stale-PID recovery: read the PID, check liveness.
            try:
                pid_str = lock_path.read_text(encoding="ascii").strip()
                pid = int(pid_str)
            except (OSError, ValueError):
                log.warning("[WORKER] worker.lock exists but is unreadable — refusing to start (duplicate instance?)")
                return None
            # Check liveness via os.kill(pid, 0). On POSIX this returns
            # None if the process is alive, raises ProcessLookupError if
            # it's dead.
            try:
                os.kill(pid, 0)
                log.warning("[WORKER] worker already running (pid=%d) — refusing to start", pid)
                return None
            except ProcessLookupError:
                # Stale lockfile — reclaim it.
                log.info("[WORKER] reclaiming stale worker.lock (pid=%d was dead)", pid)
                with contextlib.suppress(OSError):
                    lock_path.unlink(missing_ok=True)
                # Retry once.
                try:
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
                    with contextlib.suppress(OSError):
                        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
                    with contextlib.suppress(OSError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return _WorkerSingleInstanceHandle(fd, lock_path)
                except OSError:
                    log.warning("[WORKER] could not reclaim worker.lock — refusing to start", exc_info=True)
                    return None
            except PermissionError:
                # PID is alive but owned by another user (rare).
                log.warning("[WORKER] worker.lock held by pid=%d (permission check) — refusing to start", pid)
                return None
    else:
        # Windows: best-effort existence check. The Tauri host's
        # ``tauri-plugin-single-instance`` is the authoritative gate;
        # this is defense-in-depth for dev-runs from a terminal.
        if lock_path.exists():
            try:
                pid_str = lock_path.read_text(encoding="ascii").strip()
                pid = int(pid_str)
                # On Windows there is no portable ``os.kill(pid, 0)`` —
                # we use the lockfile's existence as the signal. A stale
                # lockfile from a crashed worker is reclaimed below if
                # the PID's process tree is gone (checked via
                # ``os.kill``-equivalent on Windows; left as TODO since
                # the Tauri host owns authoritative single-instance).
                log.warning("[WORKER] worker.lock exists (pid=%d) — refusing to start", pid)
                return None
            except (OSError, ValueError):
                log.warning("[WORKER] worker.lock exists but is unreadable — refusing to start")
                return None
        try:
            lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        except OSError:
            log.debug("[WORKER] could not write worker.lock — single-instance is best-effort", exc_info=True)
            return None
        # fd=-1 (no POSIX fd to close); release() will just unlink.
        return _WorkerSingleInstanceHandle(-1, lock_path)
