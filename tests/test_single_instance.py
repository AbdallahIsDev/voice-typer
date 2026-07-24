# GT-FIX-08: Regression tests for GT-41, GT-42, GT-A1-3.
#
# These tests pin the three findings assigned to fix sub-agent GT-FIX-08:
#
#   GT-41 (Medium): POSIX single-instance PID-recycling false positive.
#     On ``O_EXCL`` failure, ``_ensure_single_instance_posix`` must
#     attempt ``fcntl.flock(fd, LOCK_EX | LOCK_NB)`` on the existing
#     lockfile FIRST — before any PID liveness check. ``flock`` is the
#     crash-safe primitive (kernel auto-releases on process death);
#     the old PID-check-first behavior was fooled by PID recycling
#     after a hard crash (SIGKILL, OOM, power loss).
#
#   GT-42 (Medium): POSIX single-instance flock fd never closed in
#     ``_do_cleanup``. The POSIX fd must now be wrapped in a
#     ``_PosixSingleInstanceHandle`` (int subclass) whose ``release()``
#     method closes the fd (and best-effort unlinks the lockfile).
#     ``release()`` is idempotent and safe to call after the underlying
#     fd has been closed by other means.
#
#   GT-A1-3 (Low): Startup sequence futures awaited sequentially
#     summing timeouts. The prewarm + mic parallel work must use
#     ``concurrent.futures.wait({f1, f2}, timeout=10)`` to enforce a
#     SINGLE shared 10s budget — not per-future ``result(timeout=10)``
#     which could sum to 20s on stuck tasks.
#
# Run: python -m pytest tests/test_single_instance.py -q --no-cov

from __future__ import annotations

import contextlib
import os
import time
from unittest.mock import patch

import pytest

# GT-41/GT-42: ``fcntl`` is POSIX-only; skip the entire module on
# Windows. The Windows mutex path is exercised in
# tests/regressions/security_test.py instead.
pytest.importorskip("fcntl")
import fcntl  # noqa: E402

from voice_typer.server import single_instance as si_mod  # noqa: E402

# Re-use the heavyweight ``app_for_startup`` fixture from
# test_startup_sequence.py rather than duplicating ~30 lines of
# VoiceTyperApp construction + hardware mocking. Pytest discovers
# fixtures imported into a test module's namespace.
from tests.test_startup_sequence import app_for_startup  # noqa: E402,F401


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config_dir(monkeypatch, tmp_path):
    """Redirect ``_config_dir()`` to a tmp path so tests don't clobber
    the real config dir.

    Mirrors the fixture in ``tests/test_single_instance_posix.py`` —
    duplicated here so this test file is self-contained and doesn't
    depend on import-time ordering of the other file.
    """
    from voice_typer.server import app as app_mod

    monkeypatch.setattr(app_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "voice_typer.server.single_instance._backend_pid_file",
        lambda: tmp_path / "backend.pid",
    )
    return tmp_path


@contextlib.contextmanager
def _hold_flock(lock_path):
    """Open ``lock_path`` and hold ``flock(LOCK_EX)`` for the duration
    of the ``with`` block.

    Used to simulate a LIVE process holding the lockfile's flock —
    which is what the GT-41 logic checks FIRST.
    """
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


# ── GT-41: flock-first on stale O_EXCL ─────────────────────────────────


class TestGT41FlockAcquiredAfterStaleOExcl:
    """GT-41: On ``O_EXCL`` failure, ``flock`` must be attempted FIRST.

    The old code read the PID and called ``_is_pid_alive`` before
    trying ``flock``. If the OS had recycled the dead process's PID
    to an unrelated process, the next launch falsely exited with
    "another instance is already running".

    The new code opens the existing lockfile, attempts
    ``flock(LOCK_EX | LOCK_NB)``. If flock succeeds, the previous
    holder is dead (kernel released the flock) and we proceed. If
    flock fails with ``EWOULDBLOCK``, another LIVE process holds it
    and we exit.
    """

    def test_flock_succeeds_when_previous_holder_dead(self, isolated_config_dir):
        """A stale lockfile (dead PID, no live flock holder) is
        reclaimed via flock — no sys.exit, handle returned.

        This is the GT-41 fix: even though the lockfile contains a
        recycled PID that happens to be alive (we use our own PID),
        the flock check correctly determines the previous holder is
        dead (no one holds the flock) and proceeds.
        """
        lock_file = isolated_config_dir / "backend.lock"
        # Write OUR OWN (alive!) PID into the lockfile — this is the
        # PID-recycling false-positive scenario. The old code would
        # exit here; the new code checks flock first and proceeds.
        lock_file.write_text(f"{os.getpid()}\n")

        handle = None
        try:
            handle = si_mod._ensure_single_instance_posix(silent=True)
            # GT-42: handle is a _PosixSingleInstanceHandle (int subclass).
            assert isinstance(handle, int)
            assert handle > 0
            assert hasattr(handle, "release")
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.release()

    def test_lockfile_pid_refreshed_after_flock_reclaim(self, isolated_config_dir):
        """After flock reclaim, the lockfile contains OUR PID (not the
        dead process's recycled PID)."""
        # Use a bogus (definitely dead) PID — the simplest case.
        bogus_pid = 2_000_000
        lock_file = isolated_config_dir / "backend.lock"
        lock_file.write_text(f"{bogus_pid}\n")

        handle = None
        try:
            handle = si_mod._ensure_single_instance_posix(silent=True)
            content = lock_file.read_text().strip()
            assert int(content) == os.getpid()
            assert int(content) != bogus_pid
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.release()

    def test_flock_failure_ewouldblock_exits_with_pid_diagnostic(
        self, isolated_config_dir, capsys
    ):
        """When another LIVE process holds the flock (EWOULDBLOCK),
        the new code reads the PID for a diagnostic message and exits.

        This is the correct duplicate-launch rejection path — but
        driven by flock (crash-safe), not by PID liveness (which can
        be fooled by PID recycling).
        """
        lock_file = isolated_config_dir / "backend.lock"
        lock_file.write_text(f"{os.getpid()}\n")

        # Hold the flock on another fd to simulate a live process.
        with _hold_flock(lock_file):
            with pytest.raises(SystemExit) as exc_info:
                si_mod._ensure_single_instance_posix(silent=False)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        # Diagnostic must mention "already running" and include the PID.
        assert "already running" in captured.err.lower()
        assert str(os.getpid()) in captured.err

    def test_no_pid_liveness_check_before_flock(self, isolated_config_dir, monkeypatch):
        """GT-41: ``_is_pid_alive`` must NOT be called when flock is
        available. We monkeypatch ``_is_pid_alive`` to raise — if the
        new code calls it on the flock-first path, the test fails.

        Note: ``_is_pid_alive`` MAY still be called on the legacy
        fallback path (when ``os.open(O_RDWR)`` on the existing
        lockfile fails). To ensure we hit the flock-first path, we
        make ``os.open(O_RDWR)`` succeed normally.
        """
        lock_file = isolated_config_dir / "backend.lock"
        lock_file.write_text(f"{os.getpid()}\n")

        # Sentinel: if _is_pid_alive is called, raise.
        def _explode(pid):
            raise AssertionError(
                "GT-41: _is_pid_alive must NOT be called on the "
                "flock-first path (flock is the authoritative crash-"
                "safe primitive; PID liveness can be fooled by PID "
                "recycling)."
            )

        monkeypatch.setattr(si_mod, "_is_pid_alive", _explode)

        handle = None
        try:
            # Must NOT raise — flock succeeds (no live holder), so
            # _is_pid_alive is never called.
            handle = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(handle, int)
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.release()


# ── GT-42: _PosixSingleInstanceHandle.release() ────────────────────────


class TestGT42PosixSingleInstanceHandleRelease:
    """GT-42: The POSIX fd is wrapped in a ``_PosixSingleInstanceHandle``
    (int subclass) whose ``release()`` method closes the fd and
    best-effort unlinks the lockfile.

    This enables ``shutdown_controller._do_cleanup`` (owned by
    GT-FIX-07) to call ``app._single_instance_handle.release()`` to
    explicitly release the POSIX lock, mirroring the Windows
    ``CloseHandle`` step.
    """

    def test_handle_is_int_subclass(self, isolated_config_dir):
        """The returned handle subclasses ``int`` so existing callers
        that treat it as a raw fd (``isinstance(h, int)``,
        ``os.close(h)``) continue to work."""
        handle = None
        try:
            handle = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(handle, int)
            assert isinstance(handle, si_mod._PosixSingleInstanceHandle)
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.release()

    def test_release_closes_fd(self, isolated_config_dir):
        """``release()`` closes the underlying fd — subsequent
        ``os.fsync(fd)`` raises ``OSError(EBADF)``."""
        handle = si_mod._ensure_single_instance_posix(silent=True)
        # Sanity: the fd is valid before release.
        os.fsync(int(handle))

        handle.release()

        # After release, the fd is closed — os.fsync raises EBADF.
        with pytest.raises(OSError):
            os.fsync(int(handle))

    def test_release_unlinks_lockfile(self, isolated_config_dir):
        """``release()`` best-effort unlinks the lockfile so the next
        launch sees a clean state."""
        lock_file = isolated_config_dir / "backend.lock"
        handle = si_mod._ensure_single_instance_posix(silent=True)
        assert lock_file.exists()

        handle.release()

        # Lockfile is unlinked (best-effort).
        assert not lock_file.exists()

    def test_release_is_idempotent(self, isolated_config_dir):
        """``release()`` is idempotent — subsequent calls are no-ops
        (no OSError propagates)."""
        handle = si_mod._ensure_single_instance_posix(silent=True)
        handle.release()
        # Second call must NOT raise.
        handle.release()
        # Third call must NOT raise.
        handle.release()

    def test_release_safe_after_manual_os_close(self, isolated_config_dir):
        """``release()`` is safe to call after the underlying fd has
        already been closed by other means (e.g. test teardown via
        ``os.close(handle)``). The OSError from the double-close is
        suppressed at DEBUG level."""
        handle = si_mod._ensure_single_instance_posix(silent=True)
        # Close the fd directly (bypassing release).
        os.close(int(handle))
        # release() must NOT raise OSError on the already-closed fd.
        handle.release()


# ── GT-A1-3: shared 10s budget for startup parallel work ──────────────


class TestGTA13StartupSharedBudget:
    """GT-A1-3: ``startup_sequence._startup_parallel_work`` must enforce
    a SINGLE shared 10s budget across both the prewarm and mic
    futures via ``concurrent.futures.wait({f1, f2}, timeout=10)``.

    The old code called ``fut.result(timeout=10)`` sequentially,
    which summed the timeouts: a stuck ``prewarm`` task could
    consume 10s, then a stuck ``mic`` task could consume ANOTHER
    10s — total worst-case 20s, not 10s.
    """

    def test_concurrent_futures_wait_called_with_shared_timeout(
        self, app_for_startup, monkeypatch
    ):
        """The parallel work must call ``concurrent.futures.wait`` with
        ``timeout=10`` and a set containing BOTH futures — NOT
        ``fut.result(timeout=10)`` per future.
        """
        import concurrent.futures

        from voice_typer.server import startup_tasks

        # Stub the heavy IO tasks so they complete instantly.
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)

        # Spy on ``concurrent.futures.wait`` to capture the call.
        # Patch the actual ``concurrent.futures.wait`` function (not
        # a module-local alias) because startup_sequence.py does
        # ``import concurrent.futures`` and calls ``concurrent.futures.wait``
        # — the attribute lookup happens at call time, so patching the
        # real function in the ``concurrent.futures`` namespace works.
        wait_calls: list[dict] = []
        real_wait = concurrent.futures.wait

        def spy_wait(futures, timeout=None, return_when=concurrent.futures.ALL_COMPLETED):
            wait_calls.append(
                {
                    "futures": set(futures),
                    "timeout": timeout,
                    "return_when": return_when,
                    "num_futures": len(futures),
                }
            )
            return real_wait(futures, timeout=timeout, return_when=return_when)

        monkeypatch.setattr(concurrent.futures, "wait", spy_wait)

        # Run the startup sequence. Configure_corrections would
        # normally load corrections.json; stub it.
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        # Assert ``wait`` was called exactly once with the shared
        # 10s timeout and BOTH futures.
        assert len(wait_calls) == 1, (
            "GT-A1-3: concurrent.futures.wait must be called exactly once "
            f"(shared budget). Got {len(wait_calls)} calls."
        )
        call = wait_calls[0]
        assert call["timeout"] == 10, (
            "GT-A1-3: timeout must be 10 (single shared budget). "
            f"Got {call['timeout']}."
        )
        assert call["num_futures"] == 2, (
            "GT-A1-3: wait must be called with BOTH futures (prewarm + mic). "
            f"Got {call['num_futures']} futures."
        )
        assert call["return_when"] == concurrent.futures.ALL_COMPLETED

    def test_shared_budget_does_not_sum_timeouts(self, app_for_startup, monkeypatch):
        """Behavioral test: with BOTH tasks exceeding the budget, the
        total wait time is the SINGLE budget (~0.5s, monkeypatched),
        NOT 2x the budget (~1.0s).

        We monkeypatch ``concurrent.futures.wait`` to use a 0.5s
        timeout (instead of the hardcoded 10s) so the test runs fast.
        Both tasks sleep 1.5s — exceeding the 0.5s budget. With the
        OLD code (per-future ``result(timeout=10)``), this would take
        ~1.0s. With the NEW code (shared ``wait(timeout=0.5)``), it
        takes ~0.5s.
        """
        import concurrent.futures

        from voice_typer.server import startup_tasks

        # Both tasks sleep 1.5s — well over the (patched) 0.5s budget.
        def slow_task(app, evt=None):
            time.sleep(1.5)

        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", slow_task)
        monkeypatch.setattr(startup_tasks, "load_microphones", slow_task)
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)

        # Patch wait to use 0.5s timeout (instead of 10s) so the test
        # runs fast. The behavior under test (shared budget vs summed)
        # is identical with any timeout value.
        real_wait = concurrent.futures.wait
        patched_budget = 0.5

        def fast_wait(futures, timeout=None, return_when=concurrent.futures.ALL_COMPLETED):
            return real_wait(futures, timeout=patched_budget, return_when=return_when)

        monkeypatch.setattr(concurrent.futures, "wait", fast_wait)

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        from voice_typer.server.startup_sequence import StartupSequence

        start = time.monotonic()
        StartupSequence(app_for_startup).run()
        elapsed = time.monotonic() - start

        # Assert elapsed is close to the single budget (0.5s), not 2x
        # (1.0s). Allow generous tolerance for CI scheduling jitter.
        assert elapsed < 1.0, (
            "GT-A1-3: shared budget must enforce a SINGLE timeout — "
            f"elapsed {elapsed:.2f}s suggests timeouts were summed "
            "(old per-future behavior). Expected < 1.0s with patched "
            "0.5s shared budget."
        )
