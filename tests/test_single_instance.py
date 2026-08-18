# Regression tests for , ,
#
# These tests pin the three findings assigned to fix sub-agent :
#
# (Medium): POSIX single-instance PID-recycling false positive.
#     On ``O_EXCL`` failure, ``_ensure_single_instance_posix`` must
#     attempt ``fcntl.flock(fd, LOCK_EX | LOCK_NB)`` on the existing
#     lockfile FIRST — before any PID liveness check. ``flock`` is the
#     crash-safe primitive (kernel auto-releases on process death);
#     the old PID-check-first behavior was fooled by PID recycling
#     after a hard crash (SIGKILL, OOM, power loss).
#
# (Medium): POSIX single-instance flock fd never closed in
#     ``_do_cleanup``. The POSIX fd must now be wrapped in a
#     ``_PosixSingleInstanceHandle`` (int subclass) whose ``release()``
#     method closes the fd (and best-effort unlinks the lockfile).
#     ``release()`` is idempotent and safe to call after the underlying
#     fd has been closed by other means.
#
# (Low): Startup sequence futures awaited sequentially
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

import pytest

# ``fcntl`` is POSIX-only; skip the entire module on
# Windows. The Windows mutex path is exercised in
# tests/regressions/security_test.py instead.
pytest.importorskip("fcntl")
import fcntl  # noqa: E402

from voice_typer.server import single_instance as si_mod  # noqa: E402

# Re-use the heavyweight ``app_for_startup`` fixture from
# test_startup_sequence.py rather than duplicating ~30 lines of
# VoiceTyperApp construction + hardware mocking. Pytest discovers
# fixtures imported into a test module's namespace.
from tests.test_startup_sequence import app_for_startup  # noqa: E402,F401,F811

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
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


# flock-first on stale O_EXCL ─────────────────────────────────


class TestFlockAcquiredAfterStaleOExcl:
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
            # handle is a _PosixSingleInstanceHandle (int subclass).
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

    def test_flock_failure_ewouldblock_exits_with_pid_diagnostic(self, isolated_config_dir, capsys):
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


# _PosixSingleInstanceHandle.release() ────────────────────────


class TestPosixSingleInstanceHandleRelease:
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


# shared 10s budget for startup parallel work ──────────────


class TestStartupSharedBudget:
    """GT-A1-3 → DJ-4: startup parallel work must NOT let a slow
    ``sync_prewarm_task`` delay startup.

    The pre-GT-A1-3 code called ``fut.result(timeout=10)`` per future,
    which summed the timeouts: a stuck ``prewarm`` task could consume
    10s, then a stuck ``mic`` task could consume ANOTHER 10s — total
    worst-case 20s. GT-A1-3 originally moved both futures under a
    single ``concurrent.futures.wait({f1, f2}, timeout=10)`` shared
    budget; the DJ-4 refactor then went further and removed the wait
    entirely — ``sync_prewarm_task`` runs on a fire-and-forget daemon
    thread (no wait, no timeout) and only ``load_microphones`` runs in
    the bounded parallel pool (5s budget). These tests pin the current
    DJ-4 design.
    """

    def test_prewarm_fire_and_forget_and_mic_alone_in_bounded_pool(
        self,
        app_for_startup,  # noqa: F811 - pytest fixture injected by name (imported at module top)
        monkeypatch,
    ):
        """The parallel work must (a) dispatch ``sync_prewarm_task`` on
        a fire-and-forget daemon thread and (b) run ONLY
        ``load_microphones`` through ``_run_parallel_with_timeout``
        with the 5s budget — NOT both futures via
        ``concurrent.futures.wait``.
        """
        import threading

        from voice_typer.server import _timeout_utils, startup_tasks

        # Stub the heavy IO tasks so they complete instantly.
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)

        # Spy on ``_run_parallel_with_timeout`` (startup_sequence imports
        # it function-locally from ``_timeout_utils``, so patch the
        # source module — the local import resolves at call time).
        pool_calls: list[list] = []
        real_run = _timeout_utils._run_parallel_with_timeout

        def spy_run(items):
            pool_calls.append(items)
            return real_run(items)

        monkeypatch.setattr(_timeout_utils, "_run_parallel_with_timeout", spy_run)

        # Spy on Thread.start to catch the prewarm dispatch.
        started_threads: list[tuple[str, bool]] = []

        def spy_start(self):
            started_threads.append((self.name, self.daemon))
            return threading.Thread.start(self)

        monkeypatch.setattr(threading.Thread, "start", spy_start)

        # Run the startup sequence. Configure_corrections would
        # normally load corrections.json; stub it.
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        # DJ-4: prewarm sync must be dispatched on a daemon thread...
        prewarm_spawns = [t for t in started_threads if t[0] == "startup-prewarm-sync"]
        assert len(prewarm_spawns) == 1, (
            f"DJ-4: sync_prewarm_task must be dispatched on a fire-and-forget "
            f"daemon thread named 'startup-prewarm-sync'. Got {started_threads!r}."
        )
        assert prewarm_spawns[0][1] is True, (
            "DJ-4: the prewarm sync thread must be a daemon (must not block process exit)."
        )

        # ...and must NOT appear in the bounded parallel pool: only the
        # mic task runs there, with the 5s budget.
        assert len(pool_calls) == 1, (
            f"DJ-4: _run_parallel_with_timeout must be called exactly once (mic only). Got {len(pool_calls)} calls."
        )
        items = pool_calls[0]
        assert len(items) == 1, (
            f"DJ-4: the bounded pool must contain ONLY the mic task — "
            f"prewarm is fire-and-forget. Got {len(items)} items."
        )
        label, _task, budget = items[0]
        assert label == "mic", f"DJ-4: pool item label must be 'mic'. Got {label!r}."
        assert budget == 5.0, f"DJ-4: mic task must use the 5s budget. Got {budget}."

    def test_prewarm_slowness_does_not_delay_startup(self, app_for_startup, monkeypatch):  # noqa: F811 - pytest fixture injected by name (imported at module top)
        """Behavioral test: with BOTH tasks slow, startup returns within
        the mic budget (~0.5s, monkeypatched) — the fire-and-forget
        prewarm thread must not be waited on.

        The old summed design (per-future ``result(timeout=0.5)`` for
        prewarm THEN mic) took ~1.0s; the DJ-4 design takes ~0.5s.
        """
        from voice_typer.server import _timeout_utils, startup_tasks

        # Both tasks sleep 1.5s — well over the (patched) 0.5s budget.
        def slow_task(app, evt=None):
            time.sleep(1.5)

        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", slow_task)
        monkeypatch.setattr(startup_tasks, "load_microphones", slow_task)
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, s: None)

        # Patch the pool budget 5.0 → 0.5 so the test runs fast. The
        # behavior under test (single bounded budget vs summed) is
        # identical with any budget value.
        real_run = _timeout_utils._run_parallel_with_timeout

        def fast_run(items):
            fast_items = [(label, task, 0.5) for label, task, _budget in items]
            return real_run(fast_items)

        monkeypatch.setattr(_timeout_utils, "_run_parallel_with_timeout", fast_run)

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        from voice_typer.server.startup_sequence import StartupSequence

        start = time.monotonic()
        StartupSequence(app_for_startup).run()
        elapsed = time.monotonic() - start

        # Assert elapsed is close to the single mic budget (0.5s), not
        # 2x (1.0s) and not the slow-task duration (1.5s). Allow
        # generous tolerance for CI scheduling jitter.
        assert elapsed < 1.0, (
            "DJ-4: startup must NOT wait on the fire-and-forget prewarm "
            "thread — elapsed {elapsed:.2f}s suggests the prewarm task "
            "was waited on (old per-future behavior). Expected < 1.0s "
            "with the patched 0.5s mic budget."
        )
