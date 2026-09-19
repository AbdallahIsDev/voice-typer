"""Cross-process pack lock (§8.13 dual-instance)."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import platform
import time
from pathlib import Path
from typing import BinaryIO

from .core import (
    OFFLINE_PACK_LOCK_POLL_S,
    OFFLINE_PACK_LOCK_TIMEOUT_S,
    offline_pack_lock_path,
)

log = logging.getLogger(__name__)

# ``flock``/``msvcrt.locking`` errnos that mean "locked by another
_LOCK_CONTENTION_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
        getattr(errno, "EDEADLK", errno.EACCES),
    }
)


class OfflinePackLock:
    """Cross-process lock file for the pack downloader (§8.13)."""

    def __init__(
        self,
        version: str,
        *,
        root: Path | None = None,
        timeout_s: float = OFFLINE_PACK_LOCK_TIMEOUT_S,
    ) -> None:
        self.version = version
        self.path = offline_pack_lock_path(version, root=root)
        self.timeout_s = timeout_s
        # ``BinaryIO`` file handle of the lock file. ``None`` until
        self._fh: BinaryIO | None = None
        self._native_handle = None
        self._acquired = False

    def acquire(self) -> bool:
        """Block up to ``timeout_s`` waiting for the lock. Return True on success."""
        deadline = time.monotonic() + self.timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fh = self.path.open("a+b")
                if self._try_native_lock():
                    self._acquired = True
                    self._write_pid()
                    return True
                # Locked by another process. Close our fh and retry.
                with contextlib.suppress(OSError):
                    self._fh.close()
                self._fh = None
            except OSError as exc:
                log.debug("[PACK] lock acquire failed: %s", exc)
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(OFFLINE_PACK_LOCK_POLL_S)

    def _try_native_lock(self) -> bool:
        """Acquire the OS-native exclusive lock on ``self._fh``."""
        fh = self._fh
        if fh is None:
            # No open file handle, cannot lock (caller should have
            return False
        try:
            if platform.system() == "Windows":
                import msvcrt

                # Lock the first byte of the file. ``LK_NBLCK`` is
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    return True
                except OSError as exc:
                    if exc.errno in _LOCK_CONTENTION_ERRNOS:
                        return False  # held by another process, wait
                    raise  # unexpected, surface to acquire()'s handler
            else:
                import fcntl

                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except OSError as exc:
                    if exc.errno in _LOCK_CONTENTION_ERRNOS:
                        # Contention, report "held" so the retry loop
                        return False
                    raise  # unexpected, surface to acquire()'s handler
        except (ImportError, AttributeError) as exc:
            log.debug("[PACK] native lock unavailable: %s, using PID-file fallback", exc)
            return self._pid_file_fallback()

    def _pid_file_fallback(self) -> bool:
        """Best-effort PID-file lock when native APIs are unavailable."""
        fh = self._fh
        if fh is None:
            return False
        try:
            fh.seek(0)
            existing = fh.read().decode("utf-8", errors="replace").strip()
            if existing:
                parts = existing.split(":", 1)
                pid = int(parts[0]) if parts[0].isdigit() else None
                started_at = float(parts[1]) if len(parts) > 1 and _is_float(parts[1]) else 0.0
                if pid is not None and _is_process_alive(pid) and started_at > 0 and (time.time() - started_at) < 86400:
                    return False  # live + recent, wait
                # Stale, truncate and steal.
                fh.seek(0)
                fh.truncate()
        except (OSError, ValueError):
            return False
        return True

    def _write_pid(self) -> None:
        """Write ``pid:start_time`` to the lock file."""
        fh = self._fh
        if fh is None:
            return
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}:{time.time():.3f}\n".encode("ascii"))
            fh.flush()
        except OSError:
            pass

    def release(self) -> None:
        """Release the lock. Safe to call when not acquired (no-op)."""
        if not self._acquired:
            return
        fh = self._fh
        if fh is None:
            return
        try:
            if platform.system() != "Windows":
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt

                with contextlib.suppress(OSError):
                    # seek(0) so the unlock covers the SAME byte range
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError, AttributeError):
            pass
        with contextlib.suppress(OSError):
            fh.close()
        self._acquired = False

    def __enter__(self) -> OfflinePackLock:
        if not self.acquire():
            raise TimeoutError(f"Could not acquire pack lock {self.path} within {self.timeout_s}s")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_process_alive(pid: int) -> bool:
    """Return True if *pid* is currently running (best-effort, cross-platform)."""
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            # ``os.kill`` on Windows with signal 0 doesn't work; use
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not h:
                return False
            kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError, AttributeError):
        return False
