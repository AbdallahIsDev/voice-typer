"""FR-50: regression tests for the double-close bug in
``_secure_atomic_write`` and ``_secure_read_text``.

Pre-fix, both helpers wrapped ``os.fdopen(fd, ...)`` in a try/except
that called ``os.close(fd)`` on any exception.  But the ``with``-block
(or the manual ``finally: f.close()``) ALREADY closes the fd, so the
except's ``os.close(fd)`` was a DOUBLE-CLOSE.

On a quiet fd-table the double-close only emits EBADF (suppressed by
``contextlib.suppress(OSError)``).  But under concurrent load the
closed fd number can be REUSED by another thread's ``os.open`` /
``socket`` / ``pipe`` / etc., and the second ``os.close(fd)`` would
close that UNRELATED fd — silent corruption of an unrelated resource.

The fix uses an ``owned_fd`` sentinel (set to ``-1`` immediately after
``os.fdopen`` succeeds) so the except path only closes the fd if
``os.fdopen`` ITSELF failed (i.e. the fd is still owned by this
function, not by the file object ``f``).

Test approach:

1. **Source-level check** — pin the presence of the ``owned_fd = -1``
   sentinel and the ``if owned_fd != -1`` guard in the except path.
   This is the strongest static assertion: the pre-fix code had
   ``os.close(fd)`` unconditionally in the except; the fix has the
   sentinel + guard.  A regression that removes either would be
   caught.

2. **Behavioural check (failure path)** — force ``f.write`` /
   ``os.fsync`` / ``f.read`` to raise AFTER ``os.fdopen`` succeeds,
   and assert that the helper:
     (a) propagates the exception (no silent swallow), AND
     (b) does NOT call Python-level ``os.close`` on the original fd
         in the except path (proving the sentinel guard works).
   Note: ``f.close()`` in the finally block closes the fd via the
   C-level buffer (it does NOT call Python's ``os.close``), so the
   spy sees ZERO calls on the original fd post-fix.  Pre-fix the
   except path called Python's ``os.close(fd)`` once → spy would see
   ONE call.

3. **Behavioural check (success path)** — verify the helper still
   writes / reads correctly on the success path (no over-correction
   that would leak the fd by never closing it).

These tests are POSIX-only where noted because the
``_secure_read_text`` double-close bug is in the POSIX
``O_NOFOLLOW + os.fdopen`` branch; the Windows branch uses the
high-level ``open()`` and doesn't have the bug.  ``_secure_atomic_write``
has the same pattern on both platforms but the fsync-of-parent-dir
branch is POSIX-only.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# POSIX-only: the _secure_read_text double-close bug is in the
# O_NOFOLLOW + os.fdopen branch, which is POSIX-only.  The Windows
# branch uses the high-level open() and doesn't have the bug.
_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="FR-50: double-close bug is in the POSIX O_NOFOLLOW + os.fdopen branch",
)


# ---------------------------------------------------------------------------
# source-level sentinel check (strongest static assertion)
# ---------------------------------------------------------------------------


class TestOwnedFdSentinelInSource:
    """FR-50: the source code must use the ``owned_fd`` sentinel
    pattern (set to ``-1`` after ``os.fdopen`` succeeds) rather than
    the pre-fix unconditional ``os.close(fd)`` in the except path.

    This is a static source-level check that pins the fix.  A
    regression that reintroduces the unconditional ``os.close(fd)``
    in the except path would be caught here.
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
        # unconditionally call os.close).
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


# ---------------------------------------------------------------------------
# _secure_atomic_write — failure path does NOT double-close
# ---------------------------------------------------------------------------


class TestSecureAtomicWriteNoDoubleClose:
    """FR-50: ``_secure_atomic_write`` must not double-close the fd
    when ``f.write`` / ``f.flush`` / ``os.fsync`` raises inside the
    ``os.fdopen``-wrapped block.

    Post-fix, the except path checks ``owned_fd != -1`` — since
    ``owned_fd`` was set to ``-1`` right after ``os.fdopen`` succeeded,
    the except path does NOT call ``os.close`` on the fd.  The fd is
    closed exactly once by ``f.close()`` in the finally block (which
    goes through the C-level buffer, not Python's ``os.close``).

    Pre-fix, the except path unconditionally called ``os.close(fd)``,
    which was a double-close (``f.close()`` had already closed it via
    the C-level buffer).
    """

    def test_no_extra_os_close_on_write_failure(self, tmp_path, monkeypatch):
        """If ``f.write`` raises, Python-level ``os.close`` must NOT
        be called on the original mkstemp fd (the fd is closed by
        ``f.close()`` via the C-level buffer, NOT via ``os.close``).

        Pre-fix, the except path called ``os.close(fd)`` AFTER
        ``f.close()`` had already closed it — a double-close.
        """
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"

        # Capture the fd that tempfile.mkstemp returns so we can spy
        # on os.close calls against it.
        captured_fd: list[int] = []
        real_mkstemp = tempfile.mkstemp

        def capturing_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            captured_fd.append(fd)
            return fd, name

        monkeypatch.setattr(tempfile, "mkstemp", capturing_mkstemp)

        # Spy on os.close — record every call.  We do NOT call the
        # real os.close here (the test will leak the fd, but that's
        # acceptable for a regression test).
        closes: list[int] = []

        def spy_close(fd: int) -> None:
            closes.append(fd)
            # Intentionally DON'T call real_close — we want to count
            # calls, not perform real cleanup.

        monkeypatch.setattr(os, "close", spy_close)

        # Force f.write to raise.  We patch os.fdopen to return a
        # file-like object whose write raises.
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
        # os.close call list.  Pre-fix, the except path called
        # os.close(fd) AFTER f.close() had already closed it via
        # the C-level buffer — a double-close that under concurrent
        # load could close an unrelated fd (fd-number reuse).
        assert len(captured_fd) == 1, f"expected 1 mkstemp call, got {len(captured_fd)}"
        fd = captured_fd[0]
        assert fd not in closes, (
            f"FR-50 regression: Python-level os.close was called on "
            f"the mkstemp fd {fd} in the failure path (call list: "
            f"{closes}). The except path should NOT call os.close on "
            f"the fd because owned_fd is -1 (the fd is owned by f, "
            f"closed by f.close() in the finally block via the C-level "
            f"buffer). Pre-fix the except path unconditionally called "
            f"os.close(fd) — a double-close."
        )

    def test_no_extra_os_close_on_fsync_failure(self, tmp_path, monkeypatch):
        """Same as above but the failure is in ``os.fsync`` (which
        runs after write + flush succeed).  The except path must NOT
        call ``os.close`` on the original fd."""
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

        # Force os.fsync to raise.  The write + flush succeed, so the
        # file object's __exit__ / close() will close the fd; then
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
        """Sanity check: on the success path, the helper still writes
        the file correctly.  Guards against an over-correction that
        would break the success path."""
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"
        _secure_atomic_write(target, '{"x": 1}')
        assert target.read_text() == '{"x": 1}'

    def test_successful_write_closes_fd_no_leak(self, tmp_path):
        """FR-50: on the success path, the mkstemp fd must be closed
        (no leak).  We verify by checking that ``os.fstat(fd)`` raises
        ``OSError`` (EBADF) after the helper returns — proving the fd
        was closed by ``f.close()`` in the finally block."""
        from voice_typer.server.secure_file_io import _secure_atomic_write

        target = tmp_path / "out.json"

        captured_fd: list[int] = []

        # Use a plain monkeypatch via pytest's monkeypatch fixture
        # would require it as a param; here we just patch + restore
        # manually for the inline check.
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


# ---------------------------------------------------------------------------
# _secure_read_text — failure path does NOT double-close
# ---------------------------------------------------------------------------


@_POSIX_ONLY
class TestSecureReadTextNoDoubleClose:
    """FR-50: ``_secure_read_text`` (POSIX branch) must not double-
    close the fd when ``f.read`` raises."""

    def test_no_extra_os_close_on_read_failure(self, tmp_path, monkeypatch):
        """If ``f.read`` raises (after ``os.fdopen`` succeeded),
        Python-level ``os.close`` must NOT be called on the original
        fd (the fd is closed by ``f.close()`` via the C-level buffer).

        Pre-fix, the except path called ``os.close(fd)`` AFTER
        ``f.close()`` had already closed it — a double-close.
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

        # Spy on os.close — record every call.
        closes: list[int] = []

        def spy_close(fd: int) -> None:
            closes.append(fd)

        monkeypatch.setattr(os, "close", spy_close)

        # Force f.read to raise.  We patch os.fdopen to return a file
        # object whose read raises.
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
            f"os.close(fd) — a double-close."
        )

    def test_no_extra_os_close_on_inode_mismatch(self, tmp_path, monkeypatch):
        """If the inode-mismatch check raises ``ValueError``, the
        except path must NOT call ``os.close`` on the original fd
        (the fd is closed by ``f.close()`` in the finally block)."""
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
        # fstat return a different inode.
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
        """Sanity check: on the success path, the helper still reads
        the file correctly."""
        from voice_typer.server.secure_file_io import _secure_read_text

        target = tmp_path / "in.json"
        target.write_text("hello world")
        assert _secure_read_text(target) == "hello world"

    def test_successful_read_closes_fd_no_leak(self, tmp_path):
        """FR-50: on the success path, the read fd must be closed
        (no leak).  Verified by checking ``os.fstat(fd)`` raises
        ``OSError`` (EBADF) after the helper returns."""
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
