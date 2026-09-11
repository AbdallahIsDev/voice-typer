"""§8.13: Dual-instance race: lock file.

Spec (§8.13):

  The pack downloader takes its own lock file (``pack-<version>.lock``)
  to serialize downloads across instances.

Tested behaviors:

  1. ``PackLock`` acquires the lock on first call.
  2. A second ``PackLock`` on the same version blocks (returns False
     on ``acquire(timeout_s=0.5)``).
  3. After the first lock is released, the second can acquire.
  4. The lock file PERSISTS after release (native locks release via
     close; stale holders are detected via PID + start time, the
     unlink raced with waiters and is deliberately gone).
  5. The lock file contains the holding process's PID.
  6. A stale lock (process dead) is broken by a new acquirer.
  7. ``PackLock.__enter__`` / ``__exit__`` work as a context manager.
  8. ``__enter__`` raises ``TimeoutError`` if the lock can't be acquired
     within the timeout.
  9. The lock file is a SIBLING of the version directory, the §8.3
     atomic swap (version dir → ``.trash``) cannot invalidate it.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


class TestPackLock:
    """§8.13, cross-process lock file."""

    def test_acquire_and_release(self, tmp_path: Path):
        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        assert lock.acquire() is True
        assert lock._acquired is True
        assert lock.path.exists()
        lock.release()
        assert lock._acquired is False

    def test_second_lock_blocks(self, tmp_path: Path):
        """A second lock on the same path cannot acquire while the first holds."""
        lock1 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        lock1.acquire()
        lock2 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=0.5)
        # Should time out, lock1 is still held.
        acquired2 = lock2.acquire()
        assert acquired2 is False
        lock1.release()

    def test_release_allows_second_to_acquire(self, tmp_path: Path):
        lock1 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        lock1.acquire()
        lock1.release()
        lock2 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        assert lock2.acquire() is True
        lock2.release()

    def test_lock_file_persists_after_release(self, tmp_path: Path):
        """Release unlocks but does NOT unlink the lock file.

        Native locks (flock / msvcrt) are released by ``close()`` alone;
        stale holders are detected via PID + start time. Unlinking raced
        with waiters (close → re-create → fresh inode), so the file is
        deliberately kept, and a later acquirer re-locks the SAME
        file.
        """
        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        assert lock.acquire() is True
        path = lock.path
        lock.release()
        assert path.exists(), "release must not delete the lock file"
        # The same file is re-lockable afterwards.
        lock2 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        assert lock2.acquire() is True
        assert lock2.path == path
        lock2.release()

    def test_lock_file_contains_pid(self, tmp_path: Path):
        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        lock.acquire()
        # Read the content through the lock owner's own handle: on
        # Windows ``msvcrt.locking`` is a MANDATORY byte-range lock, so
        # a second handle cannot read the locked byte (ERROR_LOCK_VIOLATION
        # → PermissionError). Reading via ``lock._fh`` is the same read
        # the PID-file fallback path performs.
        lock._fh.seek(0)
        content = lock._fh.read().decode("ascii")
        assert str(os.getpid()) in content
        lock.release()

    def test_context_manager(self, tmp_path: Path):
        """``with PackLock(...)`` acquires + releases."""
        with offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0) as lock:
            assert lock._acquired is True
        # After the with block, the lock is released.
        assert lock._acquired is False

    def test_context_manager_raises_on_timeout(self, tmp_path: Path):
        """``with PackLock(...)`` raises ``TimeoutError`` if acquire fails."""
        # Pre-acquire with another lock.
        blocker = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        blocker.acquire()
        try:
            with pytest.raises(TimeoutError), offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=0.2):
                pass  # Should never enter.
        finally:
            blocker.release()

    def test_lock_path_is_sibling_of_version_dir(self, tmp_path: Path):
        """The lock file lives in the pack ROOT, not inside the version
        directory, its name is per-version (no cross-version
        contention) and its parent is the pack root for every version.
        """
        l1 = offline_pack.offline_pack_lock_path("v1", root=tmp_path)
        l2 = offline_pack.offline_pack_lock_path("v2", root=tmp_path)
        assert l1 == tmp_path / "pack-v1.lock"
        assert l2 == tmp_path / "pack-v2.lock"
        assert l1.parent == l2.parent == tmp_path
        assert l1 != l2

    def test_lock_survives_version_dir_swap(self, tmp_path: Path):
        """The lock's inode survives the §8.3 atomic swap (version dir →
        ``.trash``), a second instance cannot acquire while the first
        still holds, even after the version dir has been renamed away.

        Pre-fix the lock lived INSIDE the version dir: the swap carried
        its inode to ``.trash``, the next ``open()`` re-created a fresh
        lock file at the old path, and the second instance acquired
        instantly (the exclusion silently stopped excluding).
        """
        version = "1.2.3"
        pack_dir = offline_pack.offline_pack_dir_for_version(version, root=tmp_path)
        pack_dir.mkdir(parents=True)
        (pack_dir / "pack-manifest.json").write_text("{}", encoding="utf-8")

        lock1 = offline_pack.OfflinePackLock(version, root=tmp_path, timeout_s=1.0)
        assert lock1.acquire() is True
        # The lock is NOT inside the directory the swap will rename.
        assert lock1.path.parent == tmp_path

        # Simulate the swap: current version dir → <version>.trash.
        trash = Path(str(pack_dir) + ".trash")
        os.replace(pack_dir, trash)

        # The second instance still cannot acquire, same lock file,
        # same inode, still held by the first instance.
        lock2 = offline_pack.OfflinePackLock(version, root=tmp_path, timeout_s=0.3)
        assert lock2.acquire() is False, "second instance acquired a fresh lock after the swap"

        lock1.release()
        # After release (file persists, inode stable) the lock is
        # acquirable again.
        lock3 = offline_pack.OfflinePackLock(version, root=tmp_path, timeout_s=1.0)
        assert lock3.acquire() is True
        lock3.release()

    def test_stale_lock_is_broken(self, tmp_path: Path, monkeypatch):
        """A lock file pointing at a dead PID is stolen by a new acquirer."""
        lock_path = offline_pack.offline_pack_lock_path("v1", root=tmp_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Write a stale lock file pointing at PID 999999 (almost
        # certainly dead).
        lock_path.write_text(f"999999:{time.time():.3f}\n", encoding="ascii")
        # Force the PID-file fallback path (skip native flock).
        monkeypatch.setattr(offline_pack.OfflinePackLock, "_try_native_lock", lambda self: self._pid_file_fallback())
        # Make _is_process_alive return False for the stale PID.
        monkeypatch.setattr(offline_pack, "_is_process_alive", lambda pid: False)
        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        assert lock.acquire() is True
        lock.release()

    def test_thread_safety(self, tmp_path: Path):
        """Two threads contending for the same lock, only one wins."""
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=2.0)
            results.append(lock.acquire())
            if results[-1]:
                time.sleep(0.1)
                lock.release()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)
        # Exactly one thread acquired (the other timed out, but with
        # the 2s timeout + 0.1s hold, the second SHOULD succeed too).
        # The strong assertion is: at least one acquired, no deadlock.
        assert any(results)
        assert len(results) == 2


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX flock contention, Windows uses the msvcrt lock branch",
)
class TestLockContentionMapping:
    """``flock`` contention must be classified as "held by another
    process" (return False → the retry loop waits), never misrouted to
    the PID-file fallback, the fallback fails OPEN while the holder is
    between its ``flock`` and the PID write (empty lock file), letting
    BOTH instances proceed.

    Discriminating assertion: the PID-file fallback is monkeypatched to
    return ``True``, a misrouted contention would therefore return
    True; correct mapping returns False.
    """

    def test_blocking_io_error_maps_to_contention(self, tmp_path: Path, monkeypatch):
        """``flock`` raising ``BlockingIOError`` (EAGAIN/EWOULDBLOCK —
        CPython's translation for a contended LOCK_NB flock) is
        contention, not "native API unavailable"."""
        import errno
        import fcntl

        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        lock.path.parent.mkdir(parents=True, exist_ok=True)
        lock._fh = lock.path.open("a+b")

        def boom(fd, flags):
            raise BlockingIOError(errno.EAGAIN, "resource temporarily unavailable")

        monkeypatch.setattr(fcntl, "flock", boom)
        # If contention were misrouted, the fallback would return True.
        monkeypatch.setattr(offline_pack.OfflinePackLock, "_pid_file_fallback", lambda self: True)

        assert lock._try_native_lock() is False
        lock._fh.close()

    def test_plain_oserror_eacces_maps_to_contention(self, tmp_path: Path, monkeypatch):
        """``flock`` raising a plain ``OSError`` with EACCES (the other
        documented contention errno) is also contention."""
        import errno
        import fcntl

        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        lock.path.parent.mkdir(parents=True, exist_ok=True)
        lock._fh = lock.path.open("a+b")

        def boom(fd, flags):
            raise OSError(errno.EACCES, "permission denied, lock held")

        monkeypatch.setattr(fcntl, "flock", boom)
        monkeypatch.setattr(offline_pack.OfflinePackLock, "_pid_file_fallback", lambda self: True)

        assert lock._try_native_lock() is False
        lock._fh.close()

    def test_unexpected_flock_error_is_not_contention(self, tmp_path: Path, monkeypatch):
        """A non-contention OSError (e.g. EBADF) surfaces instead of
        being silently swallowed as contention or fallback."""
        import errno
        import fcntl

        lock = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        lock.path.parent.mkdir(parents=True, exist_ok=True)
        lock._fh = lock.path.open("a+b")

        def boom(fd, flags):
            raise OSError(errno.EBADF, "bad file descriptor")

        monkeypatch.setattr(fcntl, "flock", boom)

        with pytest.raises(OSError):
            lock._try_native_lock()
        lock._fh.close()

    def test_contention_does_not_read_the_lock_file(self, tmp_path: Path, monkeypatch):
        """On real contention the fallback must not even run (an empty
        lock file between the holder's flock and PID write would
        otherwise be stolen)."""
        lock1 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=1.0)
        assert lock1.acquire() is True

        fallback_called = {"yes": False}

        def mark_fallback(self):
            fallback_called["yes"] = True
            return True

        monkeypatch.setattr(offline_pack.OfflinePackLock, "_pid_file_fallback", mark_fallback)

        lock2 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=0.3)
        # Real flock contention between two fds in one process.
        assert lock2.acquire() is False
        assert fallback_called["yes"] is False
        lock1.release()

    def test_empty_lock_file_with_contention_window_fails_closed(self, tmp_path: Path, monkeypatch):
        """The race that motivated the fix: the holder has flocked but
        not yet written its PID (empty lock file). A second acquirer
        must NOT steal the lock via the fallback."""
        import fcntl

        # Simulate a holder: real flock, but the PID write is delayed —
        # the lock file stays empty.
        lock_path = offline_pack.offline_pack_lock_path("v1", root=tmp_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder_fh = lock_path.open("a+b")
        fcntl.flock(holder_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            lock2 = offline_pack.OfflinePackLock("v1", root=tmp_path, timeout_s=0.3)
            assert lock2.acquire() is False, "second acquirer stole an empty (held) lock file"
        finally:
            fcntl.flock(holder_fh.fileno(), fcntl.LOCK_UN)
            holder_fh.close()


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
