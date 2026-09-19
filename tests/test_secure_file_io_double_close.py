"""``_secure_atomic_write`` and ``_secure_read_text``."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="FR-50: double-close bug is in the POSIX O_NOFOLLOW + os.fdopen branch",
)


class TestOwnedFdSentinelInSource:
    """
    FR-50: the source code must use the ``owned_fd`` sentinel
    This is a static source-level check that pins the fix.  A
    """

    def test_secure_atomic_write_uses_owned_fd_sentinel(self):
        import inspect

        from voice_typer.server.secure_file_io import _secure_atomic_write

        src = inspect.getsource(_secure_atomic_write)
        # The sentinel assignment must be present.
        assert "owned_fd = -1" in src, (
            "FR-50 regression: _secure_atomic_write does not set "
            "`owned_fd = -1` after os.fdopen succeeds.  The pre-fix "
            "code called os.close(fd) in the except path AFTER the "
            "with-block's __exit__ had already closed it (double-close)."
        )
        # The except path must guard with `if owned_fd != -1` (NOT
        assert "owned_fd != -1" in src, (
            "FR-50 regression: _secure_atomic_write except path does "
            "not guard os.close with `if owned_fd != -1`.  Without "
            "the guard, the except path would double-close the fd."
        )

    def test_secure_read_text_uses_owned_fd_sentinel(self):
        import inspect

        from voice_typer.server.secure_file_io import _secure_read_text

        src = inspect.getsource(_secure_read_text)
        # The POSIX branch must use the owned_fd sentinel.
        assert "owned_fd = -1" in src, (
            "FR-50 regression: _secure_read_text does not set "
            "`owned_fd = -1` after os.fdopen succeeds.  The pre-fix "
            "code called os.close(fd) in the except path AFTER "
            "f.close() in the finally block had already closed it "
            "(double-close)."
        )
        assert "owned_fd != -1" in src, (
            "FR-50 regression: _secure_read_text except path does not guard os.close with `if owned_fd != -1`."
        )


class TestSecureAtomicWriteNoDoubleClose:
    """FR-50: ``_secure_atomic_write`` must not double-close the fd"""

    def test_no_extra_os_close_on_write_failure(self, tmp_path, monkeypatch):
        """If ``f.write`` raises, Python-level ``os.close`` must NOT"""
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"

        # Capture the fd that tempfile.mkstemp returns so we can spy
        captured_fd: list[int] = []
        real_mkstemp = tempfile.mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            captured_fd.append(fd)
            return fd, name

        monkeypatch.setattr(tempfile, "mkstemp", capturing_mkstemp)

        closes: list[int] = []

        def spy_close(fd: int) -> None:
            closes.append(fd)
            # Intentionally DON'T call real_close, we want to count

        monkeypatch.setattr(os, "close", spy_close)

        real_fdopen = os.fdopen

        def sabotaging_fdopen(fd, *args, **kwargs):
            f = real_fdopen(fd, *args, **kwargs)

            def raise_on_write(_data):
                raise OSError("simulated write failure (FR-50 test)")

            f.write = raise_on_write  # type: ignore[method-assign]
            return f

        monkeypatch.setattr(os, "fdopen", sabotaging_fdopen)

        # The helper should raise (the write failure propagates).
        with pytest.raises(OSError, match="simulated write failure"):
            _secure_atomic_write(target, '{"x": 1}')

        # the original mkstemp fd must NOT appear in the
        assert len(captured_fd) == 1, f"expected 1 mkstemp call, got {len(captured_fd)}"
        fd = captured_fd[0]
        assert fd not in closes, (
            f"FR-50 regression: Python-level os.close was called on "
            f"the mkstemp fd {fd} in the failure path (call list: "
            f"{closes}). The except path should NOT call os.close on "
            f"the fd because owned_fd is -1 (the fd is owned by f, "
            f"closed by f.close() in the finally block via the C-level "
            f"buffer). Pre-fix the except path unconditionally called "
            f"os.close(fd), a double-close."
        )

    def test_no_extra_os_close_on_fsync_failure(self, tmp_path, monkeypatch):
        """runs after write + flush succeed).  The except path must NOT"""
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"

        captured_fd: list[int] = []
        real_mkstemp = tempfile.mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            captured_fd.append(fd)
            return fd, name

        monkeypatch.setattr(tempfile, "mkstemp", capturing_mkstemp)

        closes: list[int] = []

        def spy_close(fd: int) -> None:
            closes.append(fd)

        monkeypatch.setattr(os, "close", spy_close)

        # the except path must NOT close it again.
        def raise_on_fsync(_fd):
            raise OSError("simulated fsync failure (FR-50 test)")

        monkeypatch.setattr(os, "fsync", raise_on_fsync)

        with pytest.raises(OSError, match="simulated fsync failure"):
            _secure_atomic_write(target, '{"x": 1}', durability=True)

        assert len(captured_fd) == 1
        fd = captured_fd[0]
        assert fd not in closes, (
            f"FR-50 regression: Python-level os.close was called on "
            f"the mkstemp fd {fd} after fsync failure (call list: "
            f"{closes}). The except path should NOT call os.close on "
            f"the fd because owned_fd is -1."
        )

    def test_successful_write_still_works(self, tmp_path):
        """Sanity check: on the success path, the helper still writes"""
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"
        _secure_atomic_write(target, '{"x": 1}')
        assert target.read_text() == '{"x": 1}'

    def test_successful_write_closes_fd_no_leak(self, tmp_path):
        """FR-50: on the success path, the mkstemp fd must be closed"""
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"

        captured_fd: list[int] = []

        # Use a plain monkeypatch via pytest's monkeypatch fixture
        import tempfile as _tempfile

        original_mkstemp = _tempfile.mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, name = original_mkstemp(*args, **kwargs)
            captured_fd.append(fd)
            return fd, name

        _tempfile.mkstemp = capturing_mkstemp
        try:
            _secure_atomic_write(target, '{"x": 1}')
        finally:
            _tempfile.mkstemp = original_mkstemp

        assert len(captured_fd) == 1
        fd = captured_fd[0]
        # The fd must be closed (os.fstat raises EBADF on a closed fd).
        with pytest.raises(OSError):
            os.fstat(fd)
        assert target.read_text() == '{"x": 1}'


@_POSIX_ONLY
class TestSecureReadTextNoDoubleClose:
    """FR-50: ``_secure_read_text`` (POSIX branch) must not double-"""

    def test_no_extra_os_close_on_read_failure(self, tmp_path, monkeypatch):
        """
        If ``f.read`` raises (after ``os.fdopen`` succeeded),
        Python-level ``os.close`` must NOT be called on the original
        """
        from voice_typer.server.secure_file_io import _secure_read_text

        target = tmp_path / "in.json"
        target.write_text('{"x": 1}')

        # Capture the fd that os.open returns.
        captured_fd: list[int] = []
        real_open = os.open

        def capturing_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            captured_fd.append(fd)
            return fd

        monkeypatch.setattr(os, "open", capturing_open)

        # Spy on os.close, record every call.
        closes: list[int] = []

        def spy_close(fd: int) -> None:
            closes.append(fd)

        monkeypatch.setattr(os, "close", spy_close)

        # Force f.read to raise.  We patch os.fdopen to return a file
        real_fdopen = os.fdopen

        def sabotaging_fdopen(fd, *args, **kwargs):
            f = real_fdopen(fd, *args, **kwargs)

            def raise_on_read(_n=-1):
                raise OSError("simulated read failure (FR-50 test)")

            f.read = raise_on_read  # type: ignore[method-assign]
            return f

        monkeypatch.setattr(os, "fdopen", sabotaging_fdopen)

        with pytest.raises(OSError, match="simulated read failure"):
            _secure_read_text(target)

        assert len(captured_fd) == 1
        fd = captured_fd[0]
        assert fd not in closes, (
            f"FR-50 regression: Python-level os.close was called on "
            f"the read fd {fd} in the failure path (call list: "
            f"{closes}). The except path should NOT call os.close on "
            f"the fd because owned_fd is -1 (the fd is owned by f, "
            f"closed by f.close() in the finally block via the C-level "
            f"buffer). Pre-fix the except path unconditionally called "
            f"os.close(fd), a double-close."
        )

    def test_no_extra_os_close_on_inode_mismatch(self, tmp_path, monkeypatch):
        """except path must NOT call ``os.close`` on the original fd"""
        from voice_typer.server.secure_file_io import _secure_read_text

        target = tmp_path / "in.json"
        target.write_text('{"x": 1}')

        captured_fd: list[int] = []
        real_open = os.open

        def capturing_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            captured_fd.append(fd)
            return fd

        monkeypatch.setattr(os, "open", capturing_open)

        closes: list[int] = []

        def spy_close(fd: int) -> None:
            closes.append(fd)

        monkeypatch.setattr(os, "close", spy_close)

        # Force the inode-mismatch check to fire by making the second
        real_fstat = os.fstat
        call_count = {"n": 0}

        class FakeStat:
            def __init__(self, ino, dev):
                self.st_ino = ino
                self.st_dev = dev

        def sabotaging_fstat(fd):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return real_fstat(fd)
            return FakeStat(ino=999999, dev=999999)

        monkeypatch.setattr(os, "fstat", sabotaging_fstat)

        with pytest.raises(ValueError, match="inode changed"):
            _secure_read_text(target)

        assert len(captured_fd) == 1
        fd = captured_fd[0]
        assert fd not in closes, (
            f"FR-50 regression: Python-level os.close was called on "
            f"the read fd {fd} after inode-mismatch ValueError "
            f"(call list: {closes}). The except path should NOT call "
            f"os.close on the fd because owned_fd is -1."
        )

    def test_successful_read_still_works(self, tmp_path):
        """Sanity check: on the success path, the helper still reads"""
        from voice_typer.server.secure_file_io import _secure_read_text

        target = tmp_path / "in.json"
        target.write_text("hello world")
        assert _secure_read_text(target) == "hello world"

    def test_successful_read_closes_fd_no_leak(self, tmp_path):
        """FR-50: on the success path, the read fd must be closed"""
        from voice_typer.server.secure_file_io import _secure_read_text

        target = tmp_path / "in.json"
        target.write_text("hello world")

        captured_fd: list[int] = []
        real_open = os.open

        def capturing_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            captured_fd.append(fd)
            return fd

        original_open = os.open
        os.open = capturing_open
        try:
            _secure_read_text(target)
        finally:
            os.open = original_open

        assert len(captured_fd) == 1
        fd = captured_fd[0]
        with pytest.raises(OSError):
            os.fstat(fd)
