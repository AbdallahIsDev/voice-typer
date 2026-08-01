"""XV-7 / XV-8 / XV-10: ShutdownController parallel-teardown + timeout fallbacks.

These tests pin the GROUP-2 fixes applied to
``voice_typer/server/shutdown_controller.py``:

* **XV-7 (High)** — ``_do_cleanup`` groups the independent middle
  teardowns (cancel-timers, recorder, level_monitor, restore_volume,
  hotkeys, crash_recovery, history_db, waveform_wiring, sounddevice,
  electron, pid_file, mutex_handle, devnull_files, event_bus) into a
  ``concurrent.futures.ThreadPoolExecutor`` with a shared 10 s deadline.
  ``ipc_server.stop`` and ``tray.stop`` remain as sequential bookends.

* **XV-8 (Medium)** — Electron termination is wrapped in
  ``_run_with_timeout(timeout=5.0)``; the legacy tray_window fallback
  path now does SIGTERM → 2 s wait → SIGKILL on POSIX.

* **XV-10 (Medium)** — If ``tray.stop()`` times out AND we're on a
  non-main thread, the cleanup thread calls ``os._exit(0)`` to unblock
  the main thread (which is parked in pystray's ``run()`` loop).

The tests stub every external dependency (the real ``VoiceTyperApp``,
filesystem PID/devnull paths, Win32 kernel32, the ``event_bus`` module)
so they run headless on Linux without touching real subsystems. They
do NOT import ``voice_typer.server.app`` (which is broken by an
unrelated parallel-agent change to ``clipboard/__init__.py``) —
instead they construct a ``_FakeApp`` duck-typed stand-in that
satisfies the surface ``ShutdownController._do_cleanup`` touches.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# Direct import — does NOT pull in voice_typer.server.app, so the
# clipboard_target_safety circular-import breakage in a parallel
# agent's WIP doesn't block these tests.
from voice_typer.server.shutdown_controller import ShutdownController

# ── Override the autouse ``mock_heavy_imports`` conftest fixture ───────
#
# The shared ``tests/conftest.py::mock_heavy_imports`` fixture is
# autouse and tries to ``monkeypatch.setattr("voice_typer.server.app.
# atexit.register", ...)``. That ``setattr`` call triggers an import
# of ``voice_typer.server.app``, which (during a parallel agent's WIP
# on ``clipboard_target_safety.py``) raises ``ImportError`` because
# ``clipboard/__init__.py`` imports symbols (``_PYATSPI_STATE_ACTIVE``,
# ``_INIT_LOCK``, etc.) that don't exist yet in the WIP
# ``clipboard_target_safety.py``.
#
# These  /  /  tests don't need ``voice_typer.server.app``
# at all — they use a ``_FakeApp`` duck-typed stand-in. We override the
# autouse fixture with a no-op so the broken import doesn't break our
# test setup. The override is scoped to this module only.


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture.

    These tests don't need heavy-import mocking — they use a
    ``_FakeApp`` and inject mock modules into ``sys.modules`` directly.
    Overriding here avoids the broken ``voice_typer.server.app`` import
    in the shared conftest.
    """
    yield


# ── Fake app ───────────────────────────────────────────────────────────


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Mirrors the collaborator surface that ``ShutdownController._do_cleanup``
    and the XV-7 teardown helpers touch. Every subsystem is a ``MagicMock``
    so we can assert call counts without running real teardown code.
    """

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._electron_pid: int | None = None
        self._mutex_handle = None

        self.recorder = MagicMock()
        self.recorder.recording = False  # skip recorder.stop() branch by default
        self.recording = MagicMock()
        self.recording._transcription_thread = None
        self.hotkeys = MagicMock()
        self.hotkeys._hotkey_backend = MagicMock()
        self.hotkeys._esc_backend = MagicMock()
        self.hotkeys._repaste_backend = MagicMock()
        self.history_db = MagicMock()
        self._crash_recovery = MagicMock()
        self.tray = MagicMock()
        self._thread_registry = MagicMock()
        self.waveform_wiring = MagicMock()

        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()


@pytest.fixture
def fake_app(monkeypatch):
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed.

    The XV-7 teardown helpers do ``from voice_typer.server import app``
    inside their bodies (for ``_clear_backend_pid_file`` /
    ``_close_devnull_files``). We pre-install ``voice_typer.server.app``
    as a ``MagicMock`` so the dynamic import succeeds without pulling
    in the real (broken) app module. ``sys.modules`` injection is the
    standard way to short-circuit a ``from X import Y`` statement.
    """
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-xv7"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)

    # event_bus.shutdown is imported dynamically inside the helper.
    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)

    # level_monitor.stop_monitoring is imported dynamically inside the helper.
    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)

    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``.

    Wires ``fake_app._do_cleanup`` to delegate to the controller's real
    body (via ``side_effect``), mirroring the post-extraction delegate
    on ``VoiceTyperApp``.
    """
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


# parallel teardown batch ──────────────────────────────────────


class TestParallelTeardownBatch:
    """XV-7: ``_do_cleanup`` runs the independent middle teardowns in a
    ``ThreadPoolExecutor`` with a shared 10 s deadline. The bookends
    (``ipc_server.stop`` + WS pool drain at the start, ``tray.stop`` at
    the end) remain sequential."""

    def test_do_cleanup_invokes_all_teardown_helpers(self, controller, fake_app):
        """Every XV-7 teardown helper must be wired into the parallel
        batch — calling ``_do_cleanup`` should exercise each one. We
        assert that each helper exists on the controller and that
        ``_do_cleanup`` completes without raising (which it would if a
        helper was referenced but not defined)."""
        # All 14 helpers must be callable attributes on the controller.
        helper_names = [
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_level_monitor",
            "_teardown_restore_volume",
            "_teardown_hotkeys",
            "_teardown_crash_recovery",
            "_teardown_history_db",
            "_teardown_waveform_wiring",
            "_teardown_sounddevice",
            "_teardown_electron",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_devnull_files",
            "_teardown_event_bus",
        ]
        for name in helper_names:
            assert hasattr(controller, name), f"missing teardown helper: {name}"
            assert callable(getattr(controller, name)), f"{name} is not callable"

        # _do_cleanup should run the parallel batch + bookends without
        # raising. If any helper was missing, the ThreadPoolExecutor
        # submit would raise AttributeError, surfacing as a Future
        # exception (which _do_cleanup logs at DEBUG but doesn't
        # propagate — so this assertion is a smoke test, not a
        # completeness guarantee).
        controller._do_cleanup()

        # tray.stop is the late bookend — must have been called exactly
        # once (the parallel batch doesn't touch it).
        fake_app.tray.stop.assert_called_once_with()

    def test_subsystem_teardowns_run_concurrently(self, controller, fake_app):
        """XV-7: independent teardowns must run CONCURRENTLY, not
        sequentially. We instrument two slow teardowns that are STILL
        in the parallel batch (DJ-9 moved history_db + crash_recovery
        to a sequential post-drain phase; ``teardown_recorder`` and
        ``teardown_hotkeys`` remain in the parallel batch). We make
        each helper sleep 0.3 s by patching the controller's bound
        methods directly. If they run sequentially, total wall time is
        ~0.6 s; if concurrently, ~0.3 s.

        We assert the parallel speedup is at least 1.5x (a conservative
        threshold that filters out scheduling jitter while still proving
        concurrency).
        """
        import time as _time

        # history_db + crash_recovery are now sequential. Use
        # two helpers that remain in the parallel batch.
        def _slow_teardown(*args, **kwargs):
            _time.sleep(0.3)

        # Patch the bound methods on the controller (NOT on fake_app —
        # the parallel batch invokes ``self._teardown_recorder`` etc.
        # directly, not via the app).
        controller._teardown_recorder = MagicMock(side_effect=_slow_teardown)
        controller._teardown_hotkeys = MagicMock(side_effect=_slow_teardown)

        start = _time.monotonic()
        controller._do_cleanup()
        elapsed = _time.monotonic() - start

        # Concurrent: ~0.3 s + bookends. Sequential: ~0.6 s + bookends.
        # Threshold: 0.5 s — comfortably below the sequential 0.6 s,
        # comfortably above the concurrent 0.3 s.
        assert elapsed < 0.5, (
            f"XV-7: parallel teardown took {elapsed:.2f}s — expected "
            f"<0.5s (concurrent). Sequential would be ~0.6s. If this "
            f"assertion fires, the ThreadPoolExecutor batch is not "
            f"running helpers concurrently."
        )

    def test_teardown_failure_does_not_propagate(self, controller, fake_app):
        """XV-7: a single helper raising must NOT propagate out of
        ``_do_cleanup`` — the thread pool isolates each helper, and
        the orchestrator logs the exception at DEBUG level. Other
        helpers + the tray.stop bookend must still run."""
        # Make history_db.flush raise — this propagates out of
        # _teardown_history_db (no try/except around the inner
        # _run_with_timeout call... actually there IS one, but the
        # _run_with_timeout itself can raise if the worker raises).
        # To be safe, make the WHOLE _teardown_history_db raise by
        # patching it to raise directly.
        controller._teardown_history_db = MagicMock(side_effect=RuntimeError("boom"))

        # _do_cleanup must not raise — the Future.exception() is logged
        # at DEBUG but not re-raised.
        controller._do_cleanup()

        # tray.stop (the late bookend) must still have been called.
        fake_app.tray.stop.assert_called_once_with()

    def test_tray_stop_runs_after_parallel_batch(self, controller, fake_app, monkeypatch):
        """XV-7 / PVT-G5-003: ``tray.stop()`` is the late bookend — it
        must run AFTER the parallel batch completes (specifically,
        after ``event_bus.shutdown`` which is in the batch). This pins
        the call ordering guarantee that
        ``test_tray_stop_is_called_after_event_bus_shutdown`` in
        ``test_shutdown_controller.py`` also covers."""
        call_order: list[str] = []

        # Spy on event_bus.shutdown — patch the already-injected
        # MagicMock module's shutdown attribute.
        fake_event_bus = sys.modules["voice_typer.server.event_bus"]

        def _spy_eb_shutdown():
            call_order.append("event_bus.shutdown")

        fake_event_bus.shutdown = _spy_eb_shutdown

        # Spy on tray.stop.
        original_tray_stop = fake_app.tray.stop

        def _spy_tray_stop():
            call_order.append("tray.stop")
            original_tray_stop()

        fake_app.tray.stop = _spy_tray_stop

        controller._do_cleanup()

        assert "tray.stop" in call_order, "tray.stop() must be called"
        assert "event_bus.shutdown" in call_order, "event_bus.shutdown() must be called"
        eb_idx = call_order.index("event_bus.shutdown")
        tray_idx = call_order.index("tray.stop")
        assert eb_idx < tray_idx, (
            f"XV-7: event_bus.shutdown (at {eb_idx}) must run BEFORE tray.stop (at {tray_idx}); got order: {call_order}"
        )


# Electron termination timeout + SIGKILL escalation ────────────


class TestElectronTerminationTimeout:
    """XV-8: ``_teardown_electron`` wraps BOTH branches in
    ``_run_with_timeout(timeout=5.0)`` and adds SIGKILL escalation
    after 2 s on POSIX for the legacy tray_window fallback path."""

    def test_terminate_electron_wrapped_in_timeout(self, controller, fake_app, monkeypatch):
        """When ``_electron_pid`` is set, ``_teardown_electron`` must
        call ``electron_launcher.terminate_electron(pid)`` via
        ``_run_with_timeout`` (so a hung terminate_electron doesn't
        hang the whole shutdown). We verify the wrapping by making
        ``terminate_electron`` block forever and asserting the helper
        returns within ~6 s (5 s timeout + scheduling slack)."""
        # Skip on Windows — the test relies on POSIX-only behavior.
        if os.name != "posix":
            pytest.skip("XV-8 POSIX-only test")

        fake_app._electron_pid = 99999

        # Inject a fake electron_launcher module whose terminate_electron
        # blocks indefinitely. _run_with_timeout should abandon it after
        # 5 s and return.
        blocked = threading.Event()

        def _blocking_terminate(pid):
            # Block until the test tears us down (or _run_with_timeout
            # abandons us as a daemon thread).
            blocked.wait(timeout=30.0)

        fake_electron_launcher = MagicMock()
        fake_electron_launcher.terminate_electron = _blocking_terminate
        monkeypatch.setitem(sys.modules, "voice_typer.server.electron_launcher", fake_electron_launcher)

        start = time.monotonic()
        controller._teardown_electron()
        elapsed = time.monotonic() - start

        # Must return within ~6 s (5 s timeout + scheduling slack).
        assert elapsed < 6.0, (
            f"XV-8: _teardown_electron took {elapsed:.2f}s — expected "
            f"<6.0s (5s _run_with_timeout + slack). The helper is not "
            f"wrapping terminate_electron in _run_with_timeout."
        )
        # Unblock the worker thread so it doesn't linger.
        blocked.set()
        # _electron_pid must have been cleared even though the
        # terminate_electron call didn't complete (the helper clears it
        # after the _run_with_timeout call returns, regardless of
        # whether the worker actually finished).
        # NOTE: the current implementation clears _electron_pid AFTER
        # the _run_with_timeout call, so if the call timed out, the
        # clear DID happen (the wrapper returned None, then the next
        # line ran). Verify.
        assert fake_app._electron_pid is None, (
            "_teardown_electron must clear _electron_pid after the terminate_electron call (even on timeout)"
        )

    def test_legacy_tray_window_path_uses_sigkill_escalation_on_posix(self, controller, fake_app, monkeypatch):
        """XV-8: when ``_electron_pid`` is None, the legacy
        tray_window path sends SIGTERM, waits 2 s, then SIGKILL on
        POSIX. We mock ``os.kill`` and ``os.waitpid`` to verify both
        signals are sent."""
        # Skip on Windows — SIGKILL doesn't exist there.
        if os.name != "posix":
            pytest.skip("XV-8 POSIX-only test")

        fake_app._electron_pid = None  # force legacy path

        # Inject a fake tray_window module whose get_electron_pid
        # returns a fake PID.
        fake_tray_window = MagicMock()
        fake_tray_window.get_electron_pid = lambda: 12345
        monkeypatch.setitem(sys.modules, "voice_typer.server.tray_window", fake_tray_window)

        # Mock os.kill to record the signals sent. Make waitpid return
        # (0, 0) (process still running) so the SIGKILL escalation
        # fires.
        signals_sent: list[int] = []

        def _mock_kill(pid, sig):
            signals_sent.append(sig)
            # Don't actually kill anything — just record.

        # Mock os.waitpid to always report "still running" so the
        # 2 s deadline elapses and SIGKILL fires.
        def _mock_waitpid(pid, options):
            return (0, 0)  # 0 = not yet reaped

        monkeypatch.setattr(os, "kill", _mock_kill)
        monkeypatch.setattr(os, "waitpid", _mock_waitpid)

        # Speed up the test by monkeypatching time.sleep inside the
        # helper to be a no-op (the 2 s deadline still elapses because
        # we check time.time(), but we don't actually sleep).
        # Actually, we can't easily monkeypatch time.sleep just for
        # this call. Instead, let the real 2 s sleep run — the test
        # takes ~2 s but that's acceptable.
        start = time.monotonic()
        controller._teardown_electron()
        elapsed = time.monotonic() - start

        # Must have sent SIGTERM first, then SIGKILL.
        import signal as _sig

        assert _sig.SIGTERM in signals_sent, f"XV-8: legacy tray_window path must send SIGTERM; got {signals_sent}"
        assert _sig.SIGKILL in signals_sent, (
            f"XV-8: legacy tray_window path must escalate to SIGKILL after 2s; got {signals_sent}"
        )
        # SIGTERM must come before SIGKILL.
        term_idx = signals_sent.index(_sig.SIGTERM)
        kill_idx = signals_sent.index(_sig.SIGKILL)
        assert term_idx < kill_idx, (
            f"XV-8: SIGTERM (at {term_idx}) must precede SIGKILL (at {kill_idx}); got order: {signals_sent}"
        )
        # Total elapsed should be ~2 s (the SIGTERM wait deadline) + slack.
        assert 1.5 < elapsed < 6.0, f"XV-8: SIGKILL escalation should fire after ~2s; took {elapsed:.2f}s"


# tray.stop() timeout fallback ────────────────────────────────


class TestTrayStopTimeoutFallback:
    """XV-10: if ``tray.stop()`` times out AND we're on a non-main
    thread, the cleanup thread calls ``os._exit(0)`` to unblock the
    main thread (parked in pystray's ``run()`` loop)."""

    def test_os_exit_called_when_tray_stop_times_out_on_non_main_thread(self, controller, fake_app, monkeypatch):
        """When ``tray.stop()`` blocks past the 5 s timeout AND the
        current thread is NOT the main thread, ``_do_cleanup`` must
        call ``os._exit(0)``. We mock ``os._exit`` so the process
        doesn't actually die."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # Make tray.stop block past the 5 s timeout. We use a real
        # 6 s sleep so the _run_with_timeout (5 s) actually fires.
        # Speed up the test by reducing the timeout via a spy: patch
        # _run_with_timeout to use a 0.1 s timeout for tray.stop only.
        # We can't easily patch _run_with_timeout selectively, so we
        # make tray.stop block for 0.5 s and patch the timeout to 0.1 s
        # by intercepting the _run_with_timeout call.
        #
        # Simpler approach: make tray.stop block for 6 s (real), but
        # the test takes 6 s. That's acceptable for one test.
        #
        # Even simpler: spawn a non-main thread that calls _do_cleanup,
        # and make tray.stop block just past the 5 s timeout. To keep
        # the test fast, we monkeypatch ``_run_with_timeout`` to use a
        # 0.1 s timeout for the tray.stop call only.
        import voice_typer.server.shutdown_controller as _sc

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "tray.stop":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_tray_stop():
            # Block past the 0.1 s timeout.
            blocked.wait(timeout=5.0)

        fake_app.tray.stop = _blocking_tray_stop

        # Run _do_cleanup on a NON-MAIN thread so the os._exit path
        # fires. Use a thread + Event to capture the result.
        done = threading.Event()
        error_holder: list = []

        def _run_cleanup():
            try:
                controller._do_cleanup()
            except Exception as exc:
                error_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_run_cleanup, name="test-cleanup-thread")
        t.start()
        # Wait for the thread to finish (or for os._exit to be called,
        # which would prevent done.set() — but we mocked os._exit so
        # it returns normally).
        done.wait(timeout=5.0)
        # Unblock the tray.stop worker thread so it doesn't linger.
        blocked.set()

        assert exit_calls == [0], (
            f"XV-10: _do_cleanup must call os._exit(0) when tray.stop "
            f"times out on a non-main thread; got exit_calls={exit_calls}"
        )

    def test_no_os_exit_when_tray_stop_completes_on_main_thread(self, controller, fake_app, monkeypatch):
        """XV-10: when ``tray.stop()`` completes normally AND we're on
        the main thread, ``_do_cleanup`` must NOT call ``os._exit``.
        The normal quit() path will call ``sys.exit(0)`` afterwards."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # tray.stop completes immediately (MagicMock default).
        # We're on the main thread (pytest runs tests on the main thread).
        controller._do_cleanup()

        assert exit_calls == [], (
            f"XV-10: _do_cleanup must NOT call os._exit when tray.stop "
            f"completes normally on the main thread; got exit_calls={exit_calls}"
        )
        fake_app.tray.stop.assert_called_once_with()

    def test_no_os_exit_when_tray_stop_times_out_on_main_thread(self, controller, fake_app, monkeypatch):
        """XV-10: when ``tray.stop()`` times out BUT we're on the main
        thread, ``_do_cleanup`` must NOT call ``os._exit`` — the main
        thread's ``quit()`` will call ``sys.exit(0)`` afterwards. We
        just log a warning and continue."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # Speed up the test by patching _run_with_timeout to use a 0.1 s
        # timeout for tray.stop only.
        import voice_typer.server.shutdown_controller as _sc

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "tray.stop":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_tray_stop():
            blocked.wait(timeout=5.0)

        fake_app.tray.stop = _blocking_tray_stop

        # Run on the MAIN thread (pytest's thread).
        controller._do_cleanup()
        # Unblock the worker.
        blocked.set()

        assert exit_calls == [], (
            f"XV-10: _do_cleanup must NOT call os._exit when tray.stop "
            f"times out on the MAIN thread (quit()'s sys.exit handles it); "
            f"got exit_calls={exit_calls}"
        )
