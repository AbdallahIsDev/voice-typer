# Regression tests for , ,

from __future__ import annotations

import contextlib
import os
import time

import pytest

pytest.importorskip("fcntl")
import fcntl  # noqa: E402

from voice_typer.server import single_instance as si_mod  # noqa: E402
from voice_typer.server._paths import RUN_SUBDIR  # noqa: E402


def _lock_file(config_dir):
    """Canonical lockfile path: ``<config_dir>/run/backend.lock``."""
    lock = config_dir / RUN_SUBDIR / "backend.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    return lock


from tests.test_startup_sequence import app_for_startup  # noqa: E402,F401,F811


@pytest.fixture
def isolated_config_dir(monkeypatch, tmp_path):
    """Redirect ``_config_dir()`` to a tmp path so tests don't clobber"""
    from voice_typer.server import app as app_mod, config as config_mod

    # Redirect the OWNING module's binding (C-ARCH-2 canonical contract):
    monkeypatch.setattr(config_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(app_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "voice_typer.server.single_instance._backend_pid_file",
        lambda: tmp_path / "backend.pid",
    )
    return tmp_path


@contextlib.contextmanager
def _hold_flock(lock_path):
    """Open ``lock_path`` and hold ``flock(LOCK_EX)`` for the duration"""
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


class TestFlockAcquiredAfterStaleOExcl:
    """On ``O_EXCL`` failure, ``flock`` must be attempted FIRST."""

    def test_flock_succeeds_when_previous_holder_dead(self, isolated_config_dir):
        """A stale lockfile (dead PID, no live flock holder) is"""
        lock_file = _lock_file(isolated_config_dir)
        lock_file.write_text(f"{os.getpid()}\n")

        handle = None
        try:
            handle = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(handle, int)
            assert handle > 0
            assert hasattr(handle, "release")
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.release()

    def test_lockfile_pid_refreshed_after_flock_reclaim(self, isolated_config_dir):
        """After flock reclaim, the lockfile contains OUR PID (not the"""
        # Use a bogus (definitely dead) PID, the simplest case.
        bogus_pid = 2_000_000
        lock_file = _lock_file(isolated_config_dir)
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

    def test_flock_failure_ewouldblock_exits_with_pid_diagnostic(self, isolated_config_dir, capsys):
        """When another LIVE process holds the flock (EWOULDBLOCK),"""
        lock_file = _lock_file(isolated_config_dir)
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
        """``_is_pid_alive`` must NOT be called when flock is"""
        lock_file = _lock_file(isolated_config_dir)
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
            handle = si_mod._ensure_single_instance_posix(silent=True)
            assert isinstance(handle, int)
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.release()


# _PosixSingleInstanceHandle.release() ────────────────────────


class TestPosixSingleInstanceHandleRelease:
    """The POSIX fd is wrapped in a ``_PosixSingleInstanceHandle``"""

    def test_handle_is_int_subclass(self, isolated_config_dir):
        """The returned handle subclasses ``int`` so existing callers"""
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
        """``release()`` closes the underlying fd, subsequent"""
        handle = si_mod._ensure_single_instance_posix(silent=True)
        # Sanity: the fd is valid before release.
        os.fsync(int(handle))

        handle.release()

        # After release, the fd is closed, os.fsync raises EBADF.
        with pytest.raises(OSError):
            os.fsync(int(handle))

    def test_release_unlinks_lockfile(self, isolated_config_dir):
        """``release()`` best-effort unlinks the lockfile so the next"""
        lock_file = _lock_file(isolated_config_dir)
        handle = si_mod._ensure_single_instance_posix(silent=True)
        assert lock_file.exists()

        handle.release()

        # Lockfile is unlinked (best-effort).
        assert not lock_file.exists()

    def test_release_is_idempotent(self, isolated_config_dir):
        """``release()`` is idempotent, subsequent calls are no-ops"""
        handle = si_mod._ensure_single_instance_posix(silent=True)
        handle.release()
        # Second call must NOT raise.
        handle.release()
        # Third call must NOT raise.
        handle.release()

    def test_release_safe_after_manual_os_close(self, isolated_config_dir):
        """``release()`` is safe to call after the underlying fd has"""
        handle = si_mod._ensure_single_instance_posix(silent=True)
        # Close the fd directly (bypassing release).
        os.close(int(handle))
        # release() must NOT raise OSError on the already-closed fd.
        handle.release()


class TestStartupSharedBudget:
    """BP-129: the dead prewarm ceremony is gone; autostart sync is"""

    def test_no_prewarm_thread_and_mic_alone_in_bounded_pool(
        self,
        app_for_startup,  # noqa: F811 - pytest fixture injected by name (imported at module top)
        monkeypatch,
    ):
        """Startup must (a) NOT dispatch any prewarm thread, (b) dispatch"""
        import threading

        from voice_typer.server import _timeout_utils, startup_tasks

        prewarm_calls: list = []
        monkeypatch.setattr(
            startup_tasks,
            "sync_prewarm_task",
            lambda app, evt=None: prewarm_calls.append(1),
        )
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(
            startup_tasks,
            "sync_autostart",
            lambda app: {"registered": False, "error": None, "actual_post_sync": False},
        )
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)

        # Spy on ``_run_parallel_with_timeout`` (startup_sequence imports
        pool_calls: list[list] = []
        real_run = _timeout_utils._run_parallel_with_timeout

        def spy_run(items):
            pool_calls.append(items)
            return real_run(items)

        monkeypatch.setattr(_timeout_utils, "_run_parallel_with_timeout", spy_run)

        # Spy on Thread.start to catch daemon dispatches.
        started_threads: list[tuple[str, bool]] = []
        _orig_thread_start = threading.Thread.start

        def spy_start(self):
            started_threads.append((self.name, self.daemon))
            return _orig_thread_start(self)

        monkeypatch.setattr(threading.Thread, "start", spy_start)

        # Run the startup sequence. Configure_corrections would
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        # BP-129: no prewarm thread may be spawned and the stub must
        prewarm_spawns = [t for t in started_threads if t[0] == "startup-prewarm-sync"]
        assert prewarm_spawns == [], (
            "BP-129: the prewarm sync ceremony is deleted, no "
            f"'startup-prewarm-sync' thread may spawn. Got {started_threads!r}."
        )
        assert prewarm_calls == [], "BP-129: startup must never call sync_prewarm_task (no-op stub)."

        # ...but the autostart sync IS dispatched on a daemon thread...
        autostart_spawns = [t for t in started_threads if t[0] == "startup-autostart-sync"]
        assert len(autostart_spawns) == 1, (
            "BP-129: sync_autostart must be dispatched on a fire-and-forget "
            f"daemon thread named 'startup-autostart-sync'. Got {started_threads!r}."
        )
        assert autostart_spawns[0][1] is True, (
            "BP-129: the autostart sync thread must be a daemon (must not block process exit)."
        )

        # ...and must NOT appear in the bounded parallel pool: only the
        assert len(pool_calls) == 1, (
            f"BP-129: _run_parallel_with_timeout must be called exactly once (mic only). Got {len(pool_calls)} calls."
        )
        items = pool_calls[0]
        assert len(items) == 1, f"BP-129: the bounded pool must contain ONLY the mic task. Got {len(items)} items."
        label, _task, budget = items[0]
        assert label == "mic", f"BP-129: pool item label must be 'mic'. Got {label!r}."
        assert budget == 5.0, f"BP-129: mic task must use the 5s budget. Got {budget}."

    def test_slow_autostart_does_not_delay_startup(self, app_for_startup, monkeypatch):  # noqa: F811 - pytest fixture injected by name (imported at module top)
        """Behavioral test: with BOTH autostart sync and mic enumeration"""
        from voice_typer.server import _timeout_utils, startup_tasks

        # Slow tasks, far beyond the (patched) 0.5s budget, so every
        def slow_task(app, evt=None):
            time.sleep(4.0)

        def slow_autostart(app):
            time.sleep(4.0)
            return {"registered": False, "error": None, "actual_post_sync": False}

        monkeypatch.setattr(startup_tasks, "sync_autostart", slow_autostart)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", slow_task)
        monkeypatch.setattr(startup_tasks, "load_microphones", slow_task)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)

        real_run = _timeout_utils._run_parallel_with_timeout

        def fast_run(items):
            fast_items = [(label, task, 0.5) for label, task, _budget in items]
            return real_run(fast_items)

        monkeypatch.setattr(_timeout_utils, "_run_parallel_with_timeout", fast_run)

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        from voice_typer.server.startup_sequence import StartupSequence

        start = time.monotonic()
        StartupSequence(app_for_startup).run()
        elapsed = time.monotonic() - start

        # Assert elapsed stays near the single mic budget (0.5s) plus
        assert elapsed < 2.5, (
            f"startup must NOT wait on the fire-and-forget autostart "
            f"thread - elapsed {elapsed:.2f}s suggests a startup task "
            "was waited on. Expected < 2.5s with the patched 0.5s mic "
            "budget and 4.0s slow tasks."
        )
