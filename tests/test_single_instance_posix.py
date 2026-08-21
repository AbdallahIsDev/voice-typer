# Regression tests for POSIX single-instance enforcement.
#
# These tests verify that ``_ensure_single_instance_posix`` (and the
# ``_ensure_single_instance`` dispatcher) correctly enforce single-instance
# on macOS/Linux via an ``O_CREAT | O_EXCL`` lockfile at
# ``<config_dir>/run/backend.lock`` with PID-based stale-lock recovery.
#
# Scenarios covered:
#   (a) First-instance success — lock acquired, fd returned, PID file written.
#   (b) Second-instance rejection — lockfile exists with an ALIVE PID;
#       ``sys.exit(1)`` is called, lockfile is NOT unlinked.
#   (c) Stale-lock recovery — lockfile exists with a DEAD PID; lockfile is
#       unlinked and reclaimed, fd returned.
#   (d) Retry-race failure — after stale recovery, the retry ``O_EXCL`` create
#       also fails with EEXIST (another process raced us); ``sys.exit(1)``.
#   (e) Unexpected OSError — ``os.open`` raises a non-EEXIST OSError;
#       ``sys.exit(1)``.
#   (f) Garbage PID in lockfile — treated as stale, reclaimed.
#   (g) Dispatcher routing — ``_ensure_single_instance`` calls
#       ``_ensure_single_instance_posix`` on non-Windows.
#
# The Windows mutex path is NOT exercised here (sandbox is Linux). See
# ``tests/regressions/security_test.py::TestMutexAcquisitionHasRetryAndTimeout``
# for the Windows source-string invariants.
#
# Run: python -m pytest tests/test_single_instance_posix.py -q --no-cov

from __future__ import annotations

import contextlib
import errno
import os

import pytest
from voice_typer.server import single_instance as si_mod

# ``fcntl`` is POSIX-only; skip the entire module on
# Windows (the Windows mutex path is exercised in regressions/
# security_test.py instead).
pytest.importorskip("fcntl")
import fcntl  # noqa: E402

from voice_typer.server._paths import RUN_SUBDIR  # noqa: E402


def _lock_file(config_dir):
    """Canonical lockfile path: ``<config_dir>/run/backend.lock``.

    Mirrors ``_ensure_single_instance_posix``, which keeps transient
    runtime state under the ``run/`` subdir of the config dir. The
    parent directory is created eagerly so tests that pre-seed a
    stale lockfile (or plant a symlink) have somewhere to put it.
    """
    lock = config_dir / RUN_SUBDIR / "backend.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    return lock


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config_dir(monkeypatch, tmp_path):
    """Redirect ``_config_dir()`` to a tmp path so tests don't clobber
    the real config dir.

    ``_ensure_single_instance_posix`` and ``_write_backend_pid_file`` both
    do ``from voice_typer.server.app import _config_dir`` at call time,
    so monkeypatching ``voice_typer.server.app._config_dir`` before the
    call is sufficient.
    """
    from voice_typer.server import app as app_mod

    monkeypatch.setattr(app_mod, "_config_dir", lambda: tmp_path)
    # Also patch single_instance module's view (it imports lazily, but
    # some tests may patch at the module level).
    monkeypatch.setattr(
        "voice_typer.server.single_instance._backend_pid_file",
        lambda: tmp_path / "backend.pid",
    )
    return tmp_path


def _cleanup_lock_fd(fd: int | None) -> None:
    """Close a lock fd if open (best-effort).

    Works with both raw ``int`` fds and ``_PosixSingleInstanceHandle``
    instances (which subclass ``int``). Calls ``release()`` if the
    handle exposes it, then falls back to ``os.close`` for safety.
    """
    if fd is None:
        return
    # prefer the handle's ``release()`` method (idempotent,
    # also unlinks the lockfile best-effort).
    release = getattr(fd, "release", None)
    if callable(release):
        try:
            release()
            return
        except OSError:
            pass
    with contextlib.suppress(OSError):
        os.close(int(fd))


@contextlib.contextmanager
def _hold_flock(lock_path):
    """GT-41: Open ``lock_path`` and hold ``flock(LOCK_EX)`` for the
    duration of the ``with`` block.

    Used by ``TestSecondInstanceRejected`` to simulate a LIVE process
    holding the lockfile's flock — which is what the new GT-41 logic
    checks FIRST (before any PID liveness check). Pre-writing a PID
    string into the lockfile is no longer enough to trigger the
    duplicate-launch rejection, because ``flock`` is the authoritative
    crash-safe primitive (the old PID-check-first behavior was the
    PID-recycling false positive that GT-41 fixes).
    """
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


# ── (a) First-instance success ─────────────────────────────────────────


class TestFirstInstanceAcquiresLock:
    """Scenario (a): no existing lockfile → O_EXCL create succeeds → fd returned."""

    def test_returns_int_fd(self, isolated_config_dir):
        """The returned lock handle is an open fd (int)."""
        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(fd, int)
            assert fd > 0  # 0 is stdin; a real fd is > 0
        finally:
            _cleanup_lock_fd(fd)

    def test_creates_backend_lock_file(self, isolated_config_dir):
        """``backend.lock`` is created under the config dir's ``run/`` subdir."""
        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            lock_file = _lock_file(isolated_config_dir)
            assert lock_file.exists()
        finally:
            _cleanup_lock_fd(fd)

    def test_writes_our_pid_into_lock_file(self, isolated_config_dir):
        """``backend.lock`` contains our PID (for diagnostics)."""
        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            lock_file = _lock_file(isolated_config_dir)
            content = lock_file.read_text().strip()
            assert int(content) == os.getpid()
        finally:
            _cleanup_lock_fd(fd)

    def test_writes_backend_pid_file(self, isolated_config_dir):
        """``backend.pid`` is also written (previously POSIX-only skipped this).

        The autostart launcher's "backend running?" check reads this file.
        """
        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            pid_file = isolated_config_dir / "backend.pid"
            assert pid_file.exists()
            content = pid_file.read_text().strip()
            assert int(content) == os.getpid()
        finally:
            _cleanup_lock_fd(fd)

    def test_lockfile_permissions_are_restricted(self, isolated_config_dir):
        """The lockfile is created with mode 0o600 (owner read/write only).

        This prevents another user on the same machine from racing the
        O_EXCL create (e.g., on a shared /tmp config dir).
        """
        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            lock_file = _lock_file(isolated_config_dir)
            # Mask out file-type bits; check the permission bits.
            mode = lock_file.stat().st_mode & 0o777
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
        finally:
            _cleanup_lock_fd(fd)


# ── (b) Second-instance rejection (alive PID) ──────────────────────────


class TestSecondInstanceRejected:
    """Scenario (b): lockfile exists AND another process holds the flock
    → ``sys.exit(1)``.

    GT-41: the previous tests pre-wrote our own PID into the lockfile
    and relied on ``_is_pid_alive`` returning True to trigger the
    rejection. That path is now the FALLBACK (only taken when
    ``os.open(O_RDWR)`` on the existing lockfile fails). The PRIMARY
    rejection signal is now ``flock(LOCK_EX | LOCK_NB)`` failing with
    ``EWOULDBLOCK`` — which we simulate here by holding the flock on
    another fd for the duration of the call.
    """

    def test_exits_when_flock_held_by_another_process(self, isolated_config_dir):
        """A live flock holder → SystemExit(1).

        GT-41: ``flock`` is the authoritative crash-safe primitive.
        Even though the PID in the lockfile may be our own (or a
        recycled unrelated PID), if another process holds the flock
        we must exit.
        """
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{os.getpid()}\n")
        assert lock_file.exists()

        with _hold_flock(lock_file):
            with pytest.raises(SystemExit) as exc_info:
                si_mod._ensure_single_instance_posix(silent=True)
            assert exc_info.value.code == 1

    def test_does_not_unlink_lockfile_when_flock_held(self, isolated_config_dir):
        """The lockfile is NOT removed when another process holds the flock."""
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{os.getpid()}\n")
        original_content = lock_file.read_text()

        with _hold_flock(lock_file), pytest.raises(SystemExit):
            si_mod._ensure_single_instance_posix(silent=True)

        # Lockfile must still exist with the original PID (we didn't steal it).
        assert lock_file.exists()
        assert lock_file.read_text() == original_content

    def test_does_not_write_backend_pid_file_on_rejection(self, isolated_config_dir):
        """On duplicate-launch rejection, ``backend.pid`` is NOT overwritten.

        We must not clobber the running instance's PID file.
        """
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{os.getpid()}\n")

        # Simulate the running instance having written its PID file.
        pid_file = isolated_config_dir / "backend.pid"
        pid_file.write_text(f"{os.getpid()}\n")

        with _hold_flock(lock_file), pytest.raises(SystemExit):
            si_mod._ensure_single_instance_posix(silent=True)

        # PID file must be unchanged.
        assert pid_file.read_text().strip() == str(os.getpid())

    def test_silent_suppresses_stderr_message(self, isolated_config_dir, capsys):
        """silent=True suppresses the stderr duplicate-launch message."""
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{os.getpid()}\n")

        with _hold_flock(lock_file), pytest.raises(SystemExit):
            si_mod._ensure_single_instance_posix(silent=True)

        captured = capsys.readouterr()
        assert captured.err == "", "silent=True must suppress stderr"
        assert captured.out == ""

    def test_non_silent_writes_stderr_message(self, isolated_config_dir, capsys):
        """silent=False writes a duplicate-launch message to stderr."""
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{os.getpid()}\n")

        with _hold_flock(lock_file), pytest.raises(SystemExit):
            si_mod._ensure_single_instance_posix(silent=False)

        captured = capsys.readouterr()
        assert "already running" in captured.err.lower() or "only one instance" in captured.err.lower()


# ── (c) Stale-lock recovery (dead PID) ─────────────────────────────────


class TestStaleLockRecovery:
    """Scenario (c): lockfile exists with a DEAD PID → unlink + retry → fd returned."""

    def test_reclaims_stale_lock(self, isolated_config_dir):
        """A dead PID in the lockfile → lockfile is reclaimed, fd returned."""
        # Use a PID that's extremely unlikely to be alive.
        bogus_pid = 2_000_000
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{bogus_pid}\n")
        assert lock_file.exists()

        # Sanity: the bogus PID is indeed dead.
        assert si_mod._is_pid_alive(bogus_pid) is False

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(fd, int)
            assert fd > 0
        finally:
            _cleanup_lock_fd(fd)

    def test_overwrites_stale_pid_with_our_pid(self, isolated_config_dir):
        """After stale recovery, ``backend.lock`` contains OUR PID."""
        bogus_pid = 2_000_000
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{bogus_pid}\n")

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            content = lock_file.read_text().strip()
            assert int(content) == os.getpid()
            assert int(content) != bogus_pid
        finally:
            _cleanup_lock_fd(fd)

    def test_writes_backend_pid_file_after_recovery(self, isolated_config_dir):
        """After stale recovery, ``backend.pid`` is written (CR-16)."""
        bogus_pid = 2_000_000
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{bogus_pid}\n")

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            pid_file = isolated_config_dir / "backend.pid"
            assert pid_file.exists()
            assert pid_file.read_text().strip() == str(os.getpid())
        finally:
            _cleanup_lock_fd(fd)

    def test_garbage_pid_treated_as_stale(self, isolated_config_dir):
        """A non-numeric PID in the lockfile → treated as stale → reclaimed."""
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text("not-a-number\n")

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(fd, int)
            # Lockfile now contains our PID.
            content = lock_file.read_text().strip()
            assert int(content) == os.getpid()
        finally:
            _cleanup_lock_fd(fd)

    def test_empty_pid_treated_as_stale(self, isolated_config_dir):
        """An empty lockfile → treated as stale → reclaimed."""
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text("")

        fd = None
        try:
            fd = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(fd, int)
        finally:
            _cleanup_lock_fd(fd)


# ── (d) Retry-race failure ─────────────────────────────────────────────


class TestRetryRaceFailure:
    """Scenario (d): after stale recovery, the retry O_EXCL create also
    fails with EEXIST (another process raced us between unlink and retry).

    This is the "thundering herd" edge case: two launches both detect a
    stale lock, both unlink it, and one wins the O_EXCL race. The loser
    must exit cleanly with a duplicate-launch error.
    """

    def test_retry_eexist_exits_with_duplicate(self, isolated_config_dir, monkeypatch):
        """Both the first and retry O_EXCL creates fail with EEXIST → SystemExit(1)."""
        # Pre-create the lockfile with a dead PID so the first os.open
        # raises EEXIST and we enter the stale-recovery path.
        bogus_pid = 2_000_000
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{bogus_pid}\n")

        # Mock os.open to ALWAYS raise EEXIST for backend.lock (simulating
        # another process winning the O_EXCL race after our unlink).
        real_os_open = os.open

        def fake_os_open(path, flags, mode=0o777, *args, **kwargs):
            path_str = str(path)
            if path_str.endswith("backend.lock"):
                raise OSError(errno.EEXIST, "File exists", path_str)
            return real_os_open(path, flags, mode, *args, **kwargs)

        monkeypatch.setattr(si_mod.os, "open", fake_os_open)

        with pytest.raises(SystemExit) as exc_info:
            si_mod._ensure_single_instance_posix(silent=True)
        assert exc_info.value.code == 1


# ── (e) Unexpected OSError ─────────────────────────────────────────────


class TestUnexpectedOSError:
    """Scenario (e): ``os.open`` raises a non-EEXIST OSError → SystemExit(1)."""

    def test_permission_denied_exits(self, isolated_config_dir, monkeypatch):
        """A PermissionError (not EEXIST) on os.open → SystemExit(1)."""
        real_os_open = os.open

        def fake_os_open(path, flags, mode=0o777, *args, **kwargs):
            path_str = str(path)
            if path_str.endswith("backend.lock"):
                raise PermissionError(errno.EACCES, "Permission denied", path_str)
            return real_os_open(path, flags, mode, *args, **kwargs)

        monkeypatch.setattr(si_mod.os, "open", fake_os_open)

        with pytest.raises(SystemExit) as exc_info:
            si_mod._ensure_single_instance_posix(silent=True)
        assert exc_info.value.code == 1


# ── (g) Dispatcher routing ─────────────────────────────────────────────


class TestDispatcherRouting:
    """Verify ``_ensure_single_instance`` dispatches to the POSIX helper on Linux."""

    def test_calls_posix_helper_on_non_windows(self, isolated_config_dir, monkeypatch):
        """On non-Windows, ``_ensure_single_instance`` calls ``_ensure_single_instance_posix``."""
        # Ensure no restart-token bypass.
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        call_log: list[bool] = []

        def fake_posix(silent=False):
            call_log.append(silent)
            return 42  # sentinel fd

        monkeypatch.setattr(si_mod, "_ensure_single_instance_posix", fake_posix)

        # Force is_windows() to return False (it already does on Linux,
        # but be explicit so the test is deterministic on any platform).
        monkeypatch.setattr(si_mod, "is_windows", lambda: False)

        result = si_mod._ensure_single_instance(silent=True)
        assert call_log == [True], "POSIX helper must be called with silent=True"
        assert result == 42

    def test_does_not_call_posix_helper_on_windows(self, isolated_config_dir, monkeypatch):
        """On Windows, ``_ensure_single_instance`` does NOT call the POSIX helper.

        This test forces ``is_windows()`` to return True and verifies the
        POSIX helper is never invoked. The Windows mutex path itself
        requires ctypes.windll which doesn't exist on Linux, so we expect
        an AttributeError or similar — but the key assertion is that the
        POSIX helper was NOT called before the Windows path errored out.
        """
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        posix_called: list[bool] = []

        def fake_posix(silent=False):
            posix_called.append(True)
            return 42

        monkeypatch.setattr(si_mod, "_ensure_single_instance_posix", fake_posix)
        monkeypatch.setattr(si_mod, "is_windows", lambda: True)

        # The Windows path will try to import ctypes.windll.kernel32,
        # which fails on Linux. We don't care about the exact error —
        # we just want to verify the POSIX helper was NOT called.
        with contextlib.suppress(SystemExit, AttributeError, Exception):
            si_mod._ensure_single_instance(silent=True)

        assert posix_called == [], "POSIX helper must NOT be called when is_windows() is True"

    def test_no_restart_token_does_not_short_circuit(self, isolated_config_dir, monkeypatch):
        """Without VOICE_TYPER_RESTART, the POSIX helper is called (no short-circuit)."""
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)
        monkeypatch.setattr(si_mod, "is_windows", lambda: False)

        called: list[bool] = []
        monkeypatch.setattr(
            si_mod,
            "_ensure_single_instance_posix",
            lambda silent=False: called.append(True) or 99,
        )

        result = si_mod._ensure_single_instance(silent=False)
        assert called == [True]
        assert result == 99


# ── Source-level invariants ────────────────────────────────────────────


class TestSourceInvariants:
    """Source-string checks that pin the CR-16 fix in place.

    These prevent a future refactor from accidentally re-introducing the
    ``if not is_windows(): return None`` early-return that disabled
    single-instance enforcement on POSIX.
    """

    def test_no_early_non_windows_return(self):
        """``_ensure_single_instance`` must NOT have a code-level ``if not is_windows(): return None``.

        this was the bug — the early return meant no single-instance
        guard existed on macOS/Linux. We check the AST (not raw source) so
        that docstring mentions of the buggy pattern don't false-positive.
        """
        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(inspect.getsource(si_mod._ensure_single_instance))
        tree = ast.parse(src)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)

        # Walk ALL if-statements in the function and check none of them
        # is `if not is_windows(): return None`.
        for node in ast.walk(func):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            # Match `not is_windows()`.
            if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
                continue
            call = test.operand
            if not isinstance(call, ast.Call):
                continue
            func_id = ""
            if isinstance(call.func, ast.Name):
                func_id = call.func.id
            if func_id != "is_windows":
                continue
            # Found `if not is_windows():` — check the body is `return None`.
            if len(node.body) != 1:
                continue
            stmt = node.body[0]
            if not isinstance(stmt, ast.Return):
                continue
            ret_val = stmt.value
            if ret_val is None or (isinstance(ret_val, ast.Constant) and ret_val.value is None):
                pytest.fail(
                    "_ensure_single_instance must NOT contain "
                    "`if not is_windows(): return None` — that disables "
                    "single-instance enforcement on POSIX. Use "
                    "`if is_windows(): ... else: _ensure_single_instance_posix(...)` instead."
                )

    def test_posix_helper_exists(self):
        """The ``_ensure_single_instance_posix`` function must exist."""
        assert hasattr(si_mod, "_ensure_single_instance_posix"), (
            "_ensure_single_instance_posix must exist for POSIX single-instance enforcement."
        )

    def test_posix_helper_uses_o_excl(self):
        """The POSIX helper must use O_CREAT|O_EXCL (the lockfile primitive)."""
        import inspect

        src = inspect.getsource(si_mod._ensure_single_instance_posix)
        assert "O_EXCL" in src, "POSIX single-instance must use O_EXCL to atomically create the lockfile."
        assert "O_CREAT" in src, "POSIX single-instance must use O_CREAT to create the lockfile."

    def test_posix_helper_writes_backend_pid_file(self):
        """The POSIX helper must call ``_write_backend_pid_file`` (CR-16 cross-platform fix)."""
        import inspect

        src = inspect.getsource(si_mod._ensure_single_instance_posix)
        assert "_write_backend_pid_file()" in src, (
            "POSIX helper must call _write_backend_pid_file() so the "
            "autostart launcher's 'backend running?' check works on macOS/Linux."
        )

    def test_posix_helper_checks_pid_liveness(self):
        """The POSIX helper must check PID liveness via ``_is_pid_alive``."""
        import inspect

        src = inspect.getsource(si_mod._ensure_single_instance_posix)
        assert "_is_pid_alive" in src, (
            "POSIX helper must call _is_pid_alive to distinguish "
            "stale lockfiles (dead PID) from genuine duplicates (alive PID)."
        )

    def test_posix_helper_unlinks_stale_lockfile(self):
        """The POSIX helper must unlink stale lockfiles before retrying."""
        import inspect

        src = inspect.getsource(si_mod._ensure_single_instance_posix)
        assert "unlink" in src, "POSIX helper must unlink stale lockfiles (dead PID) before retrying the O_EXCL create."

    def test_ensure_single_instance_dispatches_on_is_windows(self):
        """``_ensure_single_instance`` must dispatch to the POSIX helper via ``is_windows()``."""
        import inspect

        src = inspect.getsource(si_mod._ensure_single_instance)
        assert "is_windows()" in src, (
            "_ensure_single_instance must use is_windows() to dispatch between Windows mutex and POSIX lockfile paths."
        )
        assert "_ensure_single_instance_posix" in src, (
            "_ensure_single_instance must call _ensure_single_instance_posix on the non-Windows branch."
        )
