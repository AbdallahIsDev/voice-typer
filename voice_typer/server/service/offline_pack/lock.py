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
# process" (contention). NOT "native lock API unavailable". See
# :meth:`OfflinePackLock._try_native_lock` for why misrouting these to
# the PID-file fallback was a fail-open bug.
_LOCK_CONTENTION_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
        getattr(errno, "EDEADLK", errno.EACCES),
    }
)


class OfflinePackLock:
    """Cross-process lock file for the pack downloader (§8.13).

    Acquires an exclusive lock on ``<pack-root>/pack-<version>.lock`` —
    a SIBLING of the version directory, so the §8.3 atomic swap
    (version dir → ``.trash``) cannot carry the lock's inode away
    mid-download. The lock is held for the lifetime of the
    ``OfflinePackLock`` context manager. On POSIX this uses ``fcntl.flock``
    (advisory); on Windows it uses ``msvcrt.locking`` (mandatory).
    Both fall back to a best-effort PID-file + sleep loop if the
    native API is unavailable.

    The lock file contains the holding process's PID + start time so a
    stale lock (process crashed without releasing) can be detected and
    broken. The lock file itself is deliberately NOT deleted on
    release (see :meth:`release`).
    """

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
        # :meth:`acquire` opens it (or after a failed acquire closes it).
        # Annotated so type checkers narrow the non-None accesses in
        # :meth:`_try_native_lock` / :meth:`_release` correctly.
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
        """Acquire the OS-native exclusive lock on ``self._fh``.

        Return ``True`` when acquired, ``False`` when the lock is held
        by another process (contention, the caller's retry loop waits
        until the timeout). Only a genuinely UNAVAILABLE native API
        (ImportError / AttributeError) falls back to the PID-file path.

        Contention is detected via errno, NOT via the exception type:
        ``fcntl.flock`` with ``LOCK_NB`` raises ``BlockingIOError``
        (an ``OSError`` subclass with EAGAIN/EWOULDBLOCK, CPython
        translates EAGAIN-family errnos) or a plain ``OSError`` with
        EACCES. Catching the broad ``OSError`` as "API unavailable"
        misrouted contention to the PID-file fallback, which fails
        OPEN while the holder is between its ``flock`` and the PID
        write (empty lock file), both instances then proceeded.
        """
        fh = self._fh
        if fh is None:
            # No open file handle, cannot lock (caller should have
            # opened it in :meth:`acquire` first).
            return False
        try:
            if platform.system() == "Windows":
                import msvcrt

                # Lock the first byte of the file. ``LK_NBLCK`` is
                # non-blocking, we retry on failure.
                #
                # MUST ``seek(0)`` first: ``msvcrt.locking`` locks the
                # byte range at the CURRENT file position, and the lock
                # file is opened in append mode ("a+b"), so the position
                # sits at EOF, a second opener would lock a DIFFERENT
                # (non-overlapping) range and both lockers would
                # succeed, defeating the exclusive lock. Locking byte 0
                # always keeps every contender contending for the same
                # range.
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
                        # waits. Do NOT fall through to the PID-file
                        # fallback (see docstring).
                        return False
                    raise  # unexpected, surface to acquire()'s handler
        except (ImportError, AttributeError) as exc:
            log.debug("[PACK] native lock unavailable: %s, using PID-file fallback", exc)
            return self._pid_file_fallback()

    def _pid_file_fallback(self) -> bool:
        """Best-effort PID-file lock when native APIs are unavailable.

        Reads the existing PID + start time from the lock file; if the
        PID is dead (or stale by >1 day), the lock is considered
        abandoned and we steal it.
        """
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
        """Release the lock. Safe to call when not acquired (no-op).

        The lock FILE is deliberately NOT unlinked here:

        * Native locks (``flock`` / ``msvcrt.locking``) are released by
          ``close()`` alone, a leftover file is instantly re-lockable
          and harmless.
        * The PID-file fallback detects stale holders via PID + start
          time (dead process or >1 day → steal), so unblocking waiters
          never depended on the unlink.
        * Unlinking raced with waiters: between the holder's close and
          the unlink, a waiting instance's next ``open()`` could create
          a fresh inode, and while the file is absent, a second waiter
          and the unlink can interleave arbitrarily. A stable inode
          also keeps the sibling lock's identity intact across the
          §8.3 version-dir swap.
        """
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
                    # that was locked (msvcrt.locking is position-based;
                    # after ``_write_pid`` the position sits at EOF).
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
            # ``subprocess.run(['tasklist', ...])`` for a real check.
            # For tests we accept the simpler ``OpenProcess`` path.
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

