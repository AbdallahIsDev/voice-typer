"""Phase 7 regression tests for the ``ShutdownController`` extraction.

The 7 shutdown/cleanup methods (``_do_cleanup``, ``quit``,
``_atexit_log``, ``_atexit_cleanup``, ``_install_signal_handlers``,
``_install_win32_console_handler``, ``_win32_console_handler``) were
extracted from ``VoiceTyperApp`` to
``voice_typer/server/shutdown_controller.py``. ``VoiceTyperApp`` keeps
thin delegate methods so ``app.start()`` (which registers the atexit
handlers via ``atexit.register(self._atexit_log)``), tray menu callbacks
(via ``quit_app`` → ``self.quit()``), and tests calling
``app._do_cleanup()`` directly all keep working unchanged.

These tests pin the contract of the extraction:

1. ``ShutdownController`` is wired into ``VoiceTyperApp.__init__`` as
   ``self.shutdown`` (XFAIL until the primary agent wires it).
2. ``ShutdownController.quit`` calls ``_do_cleanup`` and ``sys.exit(0)``.
3. ``_do_cleanup`` is idempotent via the ``_cleanup_done`` flag.
4. ``_do_cleanup`` calls shutdown on each subsystem (recording, hotkeys,
   recorder, tray, history_db, _crash_recovery, _thread_registry,
   _bubble_level_worker_*, _electron_pid).
5. ``_install_signal_handlers`` registers SIGTERM/SIGINT handlers on POSIX.
6. ``_atexit_cleanup`` is safe to call multiple times.
7. ``_atexit_cleanup`` short-circuits when ``_shutting_down`` is True
   (no spurious emergency cleanup after intentional shutdown).
8. ``_atexit_cleanup`` never raises (even if ``_do_cleanup`` raises).
9. ``_install_win32_console_handler`` is a no-op when ``is_windows()``
   returns False.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest
import voice_typer.server.shutdown_controller
from voice_typer.server.shutdown_controller import ShutdownController

# ── Fixtures ────────────────────────────────────────────────────────────


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Provides every attribute / method that ``ShutdownController._do_cleanup``
    and ``quit`` touch, mocked so we can assert call counts. Mirrors the
    collaborators mocked by ``tests/test_app_cleanup.py::
    _stub_restart_environment``.
    """

    def __init__(self):
        # Shutdown state (mirrors VoiceTyperApp.__init__)
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._electron_pid: int | None = None
        self._mutex_handle = None
        # Stashed by ``quit_app()`` after it publishes ``quit_app`` so
        # ``quit()`` (which publishes it itself for the Ctrl+C / signal
        # paths) does not send a redundant second write.
        self._quit_app_published = False

        # Subsystem collaborators (MagicMock so any attribute/method call
        # is recorded).
        self.recorder = MagicMock()
        self.recorder.recording = True
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

        # Methods on VoiceTyperApp that _do_cleanup calls (kept on the
        # app as delegates to other controllers).
        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()

        # Bubble level worker (optional on VoiceTyperApp — _do_cleanup
        # guards with hasattr; initialize to None so the worker-stop
        # branch is skipped by default).
        self._bubble_level_worker_stop = None
        self._bubble_level_queue = None
        self._bubble_level_worker = None

        # ``_do_cleanup`` delegate on VoiceTyperApp. Default to a no-op
        # MagicMock; per-test (or the ``controller`` fixture) wires it to
        # the real body via ``side_effect``.
        self._do_cleanup = MagicMock()


@pytest.fixture
def fake_app(tmp_config_dir, monkeypatch):
    """A ``_FakeApp`` with the shutdown environment stubbed out.

    Stubs (so ``_do_cleanup`` doesn't touch the real filesystem / Win32
    API / devnull FDs):

    - ``voice_typer.server.app._clear_backend_pid_file`` — no-op recorder.
    - ``voice_typer.server.app._close_devnull_files`` — no-op.
    - ``voice_typer.server.app._register_devnull_file`` — no-op.
    - ``voice_typer.server.platform_utils.is_windows`` — returns False (POSIX test env).
    """
    monkeypatch.setattr("voice_typer.server.app._clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False, raising=False)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``.

    Wires ``fake_app._do_cleanup`` to delegate to the controller's real
    body (via ``side_effect``), mirroring the post-extraction delegate
    on ``VoiceTyperApp``. Per-test can override ``fake_app._do_cleanup``
    (e.g. replace with a plain ``MagicMock()``) to assert call counts
    without running the real body.
    """
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


# ── (1) Wiring: VoiceTyperApp.__init__ constructs self.shutdown ────────


class TestShutdownControllerWiring:
    """Verify ``VoiceTyperApp.__init__`` wires up ``ShutdownController``.

    the wiring has landed — ``VoiceTyperApp.__init__``
    constructs ``self.shutdown = ShutdownController(self)`` (see
    ``voice_typer/server/app.py:228``). These tests now run unmarked.
    """

    def test_app_has_shutdown_attribute(self, tmp_config_dir, monkeypatch):
        """``self.shutdown`` must be a ``ShutdownController`` instance."""
        # raising=False — these app-module attributes may have
        # been removed/renamed in a prior refactor; the monkeypatch is
        # a defensive no-op when they're absent.
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False, raising=False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True, raising=False)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True, raising=False)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [], raising=False)

        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert hasattr(instance, "shutdown"), "VoiceTyperApp.__init__ must construct self.shutdown (ShutdownController)"
        assert isinstance(instance.shutdown, ShutdownController), "self.shutdown must be a ShutdownController instance"

    def test_shutdown_back_references_app(self, tmp_config_dir, monkeypatch):
        """``ShutdownController._app`` must be the ``VoiceTyperApp`` instance."""
        # raising=False — see test_app_has_shutdown_attribute.
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False, raising=False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True, raising=False)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True, raising=False)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [], raising=False)

        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance.shutdown._app is instance, (
            "ShutdownController._app must be the VoiceTyperApp instance that "
            "constructed it (back-reference for state access)"
        )


# ── (2) quit() calls _do_cleanup and sys.exit ──────────────────────────


class TestQuitCallsDoCleanupAndExits:
    """``ShutdownController.quit`` must delegate to ``_do_cleanup`` and
    call ``sys.exit(0)`` when invoked from the main thread."""

    def test_quit_calls_app_do_cleanup_delegate(self, controller, fake_app, monkeypatch):
        """``quit()`` must call ``app._do_cleanup()`` (the delegate on
        VoiceTyperApp) — NOT ``self._do_cleanup()`` (the body on the
        controller) — so test spies that
        ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercept
        the call."""
        # Replace the delegate with a plain MagicMock so we can assert
        # the call WITHOUT running the real body (we only care that quit
        # routes through the delegate).
        fake_app._do_cleanup = MagicMock()
        # sys.exit must raise SystemExit so quit() returns control.
        monkeypatch.setattr(sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

        with contextlib.suppress(SystemExit):
            controller.quit()

        fake_app._do_cleanup.assert_called_once_with()

    def test_quit_calls_sys_exit_zero_in_main_thread(self, controller, fake_app, monkeypatch):
        """When called from the main thread, ``quit()`` must call
        ``sys.exit(0)`` after ``_do_cleanup``."""
        fake_app._do_cleanup = MagicMock()
        exit_calls = []
        monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))

        controller.quit()

        assert exit_calls == [0], f"quit() must call sys.exit(0) when invoked from the main thread; got {exit_calls}"

    def test_quit_is_idempotent_when_already_shutting_down(self, controller, fake_app, monkeypatch):
        """If ``_shutting_down`` is already True, ``quit()`` must
        short-circuit — no ``_do_cleanup``, no ``sys.exit``."""
        fake_app._shutting_down = True
        fake_app._do_cleanup = MagicMock()
        exit_calls = []
        monkeypatch.setattr(sys, "exit", lambda code=0: exit_calls.append(code))

        controller.quit()

        fake_app._do_cleanup.assert_not_called()
        assert exit_calls == [], "quit() must not call sys.exit when _shutting_down is already True"

    def test_quit_sets_shutting_down_before_cleanup(self, controller, fake_app, monkeypatch):
        """``quit()`` must set ``_shutting_down = True`` BEFORE calling
        ``_do_cleanup()`` so the atexit safety net doesn't double-clean.

        Mirrors ``tests/test_app_cleanup.py::
        test_restart_app_sets_shutting_down_before_cleanup``.
        """
        flag_values_at_cleanup_entry = []

        def spy_do_cleanup():
            flag_values_at_cleanup_entry.append(fake_app._shutting_down)

        fake_app._do_cleanup = MagicMock(side_effect=spy_do_cleanup)
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        controller.quit()

        assert flag_values_at_cleanup_entry == [True], (
            "quit() must set _shutting_down=True BEFORE calling _do_cleanup(); "
            f"got sequence: {flag_values_at_cleanup_entry}"
        )

    def test_quit_calls_thread_registry_shutdown_all(self, controller, fake_app, monkeypatch):
        """``quit()`` must call ``thread_registry.shutdown_all()`` BEFORE
        ``_do_cleanup()`` so the registry's centralized signal-and-join
        runs first (THREAD-REGISTRY)."""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        controller.quit()

        fake_app._thread_registry.shutdown_all.assert_called_once_with()

    def test_quit_publishes_quit_app_event_when_not_published(self, controller, fake_app, monkeypatch):
        """``quit()`` must publish the ``quit_app`` event over the TCP
        channel when the caller did NOT go through ``quit_app()`` (the
        Win32 Ctrl+C handler / POSIX signal watcher call ``quit()``
        directly). This makes Electron close its window immediately on
        every shutdown path — not just the tray menu — instead of being
        force-killed by ``_teardown_electron`` seconds later."""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        fake_app._quit_app_published = False

        controller.quit()

        assert any(m.get("type") == "quit_app" for m in pushed), (
            f"quit() must publish quit_app when not invoked via quit_app(); got pushes: {pushed!r}"
        )

    def test_quit_skips_quit_app_publish_when_already_published(self, controller, fake_app, monkeypatch):
        """When ``quit()`` is reached via ``quit_app()`` (tray menu / IPC
        handler), the event was already published there — ``quit()`` must
        NOT send a redundant second write."""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        fake_app._quit_app_published = True

        controller.quit()

        assert not pushed, f"quit() must skip the quit_app publish when quit_app() already published; got: {pushed!r}"

    def test_quit_publish_failure_does_not_block_shutdown(self, controller, fake_app, monkeypatch):
        """A raising ``event_bus.publish`` must be swallowed — shutdown
        must never be blocked by a broken TCP client / subscriber."""
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        def _raising(_msg):
            raise RuntimeError("transport gone")

        monkeypatch.setattr("voice_typer.server.event_bus.publish", _raising)
        fake_app._quit_app_published = False

        controller.quit()

        fake_app._do_cleanup.assert_called_once_with()


# ── (3) _do_cleanup idempotency ────────────────────────────────────────


class TestDoCleanupIdempotency:
    """``_do_cleanup`` must be safe to call multiple times — the
    ``_cleanup_done`` flag is the hard guarantee."""

    def test_do_cleanup_twice_is_noop(self, controller, fake_app):
        """Calling ``_do_cleanup()`` twice must invoke each subsystem
        exactly once — the second call is a true no-op."""
        # capture backend refs BEFORE _do_cleanup() because the
        # fix nulls _hotkey_backend/_esc_backend/_repaste_backend
        # after _teardown_hotkeys (production code is correct; tests must
        # capture refs before they're nulled).
        hk = fake_app.hotkeys._hotkey_backend
        esc = fake_app.hotkeys._esc_backend
        repaste = fake_app.hotkeys._repaste_backend
        controller._do_cleanup()
        controller._do_cleanup()

        fake_app._cancel_pending_timers.assert_called_once()
        fake_app.recorder.stop.assert_called_once()
        fake_app.recorder.shutdown_mic_watcher.assert_called_once()
        fake_app.history_db.flush.assert_called_once()
        fake_app.history_db.close.assert_called_once()
        fake_app._crash_recovery.flush.assert_called_once()
        fake_app._crash_recovery.shutdown.assert_called_once()
        hk.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        fake_app.tray.stop.assert_called_once()
        fake_app._restore_volume.assert_called_once_with(fade_ms=0)

    def test_do_cleanup_sets_cleanup_done_flag(self, controller, fake_app):
        """After ``_do_cleanup()`` returns, ``_cleanup_done`` must be True
        so subsequent calls short-circuit."""
        assert fake_app._cleanup_done is False
        controller._do_cleanup()
        assert fake_app._cleanup_done is True

    def test_do_cleanup_idempotent_when_recorder_stop_raises(self, controller, fake_app):
        """Idempotency must hold even when an inner operation raises.

        Mirrors ``tests/test_app_cleanup.py::
        test_do_cleanup_idempotent_when_recorder_stop_raises``.
        """
        fake_app.recorder.stop.side_effect = RuntimeError("PortAudio already closed")
        fake_app.recorder.discard.side_effect = RuntimeError("already discarded")

        # First call: recorder.stop() raises, discard() is called as
        # fallback (also raises — both caught by try-except). Must not
        # propagate.
        controller._do_cleanup()
        # Second call must be a no-op.
        controller._do_cleanup()

        fake_app.recorder.stop.assert_called_once()
        fake_app.recorder.discard.assert_called_once()
        # history_db.flush still ran exactly once on the first call,
        # despite the recorder errors.
        fake_app.history_db.flush.assert_called_once()

    def test_do_cleanup_concurrent_callers_only_one_runs_body(self, controller, fake_app):
        """PVT-G5-026: the check-then-set on ``_cleanup_done`` must be
        atomic under concurrent callers. Two threads calling
        ``_do_cleanup()`` at the same time must NOT both execute the
        cleanup body — only one wins the check-then-set race; the
        other short-circuits.

        Pre-fix, the check-then-set was unsynchronized, so two callers
        (e.g. signal-watcher thread + atexit) could both read False,
        both set True, and both execute the body concurrently. This
        would double-call ``CloseHandle(_mutex_handle)`` (closing a
        wrong handle if the kernel reused the value) and
        ``history_db.close()`` (corrupting SQLite state).
        """
        import threading as _threading

        # Barrier so both threads reach the check-then-set at the same
        # time (maximizes the chance of racing if the lock guard is
        # missing).
        barrier = _threading.Barrier(2)

        def _spy_cancel():
            # No-op; we just want to count calls.
            pass

        fake_app._cancel_pending_timers = _spy_cancel

        # Replace _cancel_pending_timers with a counter that's
        # incremented atomically.
        call_count = [0]
        call_lock = _threading.Lock()

        def _counting_cancel():
            with call_lock:
                call_count[0] += 1

        fake_app._cancel_pending_timers = _counting_cancel

        def _call_cleanup():
            barrier.wait()
            controller._do_cleanup()

        t1 = _threading.Thread(target=_call_cleanup)
        t2 = _threading.Thread(target=_call_cleanup)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # ``_cancel_pending_timers`` (the FIRST operation
        # in the cleanup body) must have been called EXACTLY ONCE —
        # proving only one of the two concurrent callers entered the
        # body. Pre-fix, both could enter and call_count would be 2.
        assert call_count[0] == 1, (
            f"Concurrent _do_cleanup() callers must not both execute the body; "
            f"_cancel_pending_timers was called {call_count[0]} times (expected 1)."
        )
        # ``_cleanup_done`` is set (the winner set it).
        assert fake_app._cleanup_done is True


# ── (4) _do_cleanup calls shutdown on each subsystem ───────────────────


class TestDoCleanupSubsystemCoverage:
    """``_do_cleanup`` must call shutdown on every subsystem — no
    subsystem should be silently skipped (the RW-3 bug)."""

    def test_calls_cancel_pending_timers(self, controller, fake_app):
        controller._do_cleanup()
        fake_app._cancel_pending_timers.assert_called_once_with()

    def test_calls_recording_stop_watchdog_thread(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.recording._stop_watchdog_thread.assert_called_once_with()

    def test_calls_recorder_stop_when_recording(self, controller, fake_app):
        """When ``recorder.recording`` is truthy, ``_do_cleanup`` must
        call ``recorder.stop()`` (falling back to ``discard()`` on
        failure) to close the PortAudio stream."""
        fake_app.recorder.recording = True
        controller._do_cleanup()
        fake_app.recorder.stop.assert_called_once_with()

    def test_calls_recorder_shutdown_mic_watcher(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.recorder.shutdown_mic_watcher.assert_called_once_with()

    def test_calls_restore_volume_with_zero_fade(self, controller, fake_app):
        controller._do_cleanup()
        fake_app._restore_volume.assert_called_once_with(fade_ms=0)

    def test_calls_all_three_hotkey_backend_stops(self, controller, fake_app):
        # capture backend refs BEFORE _do_cleanup() because the
        # fix nulls them after _teardown_hotkeys.
        hk = fake_app.hotkeys._hotkey_backend
        esc = fake_app.hotkeys._esc_backend
        repaste = fake_app.hotkeys._repaste_backend
        controller._do_cleanup()
        hk.stop.assert_called_once_with()
        esc.stop.assert_called_once_with()
        repaste.stop.assert_called_once_with()

    def test_calls_crash_recovery_flush_and_shutdown(self, controller, fake_app):
        controller._do_cleanup()
        fake_app._crash_recovery.flush.assert_called_once_with(timeout=2.0)
        fake_app._crash_recovery.shutdown.assert_called_once_with()

    def test_calls_history_db_flush_and_close(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.history_db.flush.assert_called_once_with()
        fake_app.history_db.close.assert_called_once_with()

    def test_calls_tray_stop(self, controller, fake_app):
        controller._do_cleanup()
        fake_app.tray.stop.assert_called_once_with()

    def test_tray_stop_is_called_after_event_bus_shutdown(self, controller, fake_app, monkeypatch):
        """PVT-G5-003: ``tray.stop()`` MUST be the LAST step in
        ``_do_cleanup()`` (after ``event_bus.shutdown()``). Previously
        it was step 13 of 19, which broke the pystray loop on the main
        thread before the remaining cleanups (sd.stop, electron
        terminate, PID file clear, CloseHandle, devnull close,
        event_bus.shutdown) could finish — the daemon TCP worker
        thread running ``_do_cleanup()`` was killed mid-cleanup when
        the main thread returned.

        This test records the call order of ``tray.stop()`` and
        ``event_bus.shutdown()`` and asserts the latter precedes the
        former, AND that ``tray.stop()`` is the LAST recorded call.
        """
        call_order: list[str] = []

        original_tray_stop = fake_app.tray.stop

        def _spy_tray_stop():
            call_order.append("tray.stop")
            original_tray_stop()

        fake_app.tray.stop = _spy_tray_stop

        # Spy on event_bus.shutdown() — patch the module-level
        # function so the call is recorded. Don't run the real
        # shutdown (it mutates module-global state other tests need).
        import voice_typer.server.event_bus as _eb

        def _spy_eb_shutdown():
            call_order.append("event_bus.shutdown")

        monkeypatch.setattr(_eb, "shutdown", _spy_eb_shutdown)

        controller._do_cleanup()

        assert "tray.stop" in call_order, "tray.stop() must be called"
        assert "event_bus.shutdown" in call_order, "event_bus.shutdown() must be called"
        tray_idx = call_order.index("tray.stop")
        eb_idx = call_order.index("event_bus.shutdown")
        assert eb_idx < tray_idx, (
            f"PVT-G5-003: event_bus.shutdown() (at index {eb_idx}) must be "
            f"called BEFORE tray.stop() (at index {tray_idx}); got order: {call_order}"
        )
        assert call_order[-1] == "tray.stop", (
            f"PVT-G5-003: tray.stop() must be the LAST step in _do_cleanup(); got order: {call_order}"
        )

    def test_calls_clear_backend_pid_file(self, controller, fake_app, monkeypatch):
        """``_do_cleanup`` must call the dynamic-lookup
        ``voice_typer.server.app._clear_backend_pid_file`` so the PID
        file is removed before the process exits."""
        clear_calls: list[bool] = []
        monkeypatch.setattr(
            "voice_typer.server.app._clear_backend_pid_file",
            lambda: clear_calls.append(True),
        )
        controller._do_cleanup()
        assert clear_calls == [True], (
            "_do_cleanup must call _clear_backend_pid_file() so the next "
            "launch isn't falsely blocked by a stale PID file"
        )

    def test_calls_close_devnull_files(self, controller, fake_app, monkeypatch):
        close_calls: list[bool] = []
        monkeypatch.setattr(
            "voice_typer.server.app._close_devnull_files",
            lambda: close_calls.append(True),
        )
        controller._do_cleanup()
        assert close_calls == [True]

    def test_terminates_electron_subprocess_when_pid_tracked(self, controller, fake_app, monkeypatch):
        """When ``_electron_pid`` is set, ``_do_cleanup`` must call
        ``electron_launcher.terminate_electron(pid)`` to clean up the
        subprocess."""
        terminate_calls: list[int] = []
        # Patch the real electron_launcher.terminate_electron function
        # (the module is already imported at app.py import time, so
        # monkeypatching the attribute on the real module is what
        # actually intercepts the call — mirrors the convention used
        # for ``_clear_backend_pid_file``).
        monkeypatch.setattr(
            "voice_typer.server.electron_launcher.terminate_electron",
            lambda pid: terminate_calls.append(pid),
        )
        # Avoid the legacy tray_window fallback path (only runs when
        # _electron_pid is None).
        fake_app._electron_pid = 99999

        controller._do_cleanup()

        assert terminate_calls == [99999], (
            "_do_cleanup must call electron_launcher.terminate_electron(pid) when _electron_pid is set"
        )
        assert fake_app._electron_pid is None, "_do_cleanup must clear _electron_pid after terminating"

    def test_stops_bubble_level_worker_when_present(self, controller, fake_app):
        """When the bubble level worker is wired, ``_do_cleanup`` must
        delegate to ``app.waveform_wiring.stop()`` (Phase 7: the
        worker / queue / stop_event now live on WaveformBubbleWiring)."""
        waveform_wiring = MagicMock()
        fake_app.waveform_wiring = waveform_wiring

        controller._do_cleanup()

        waveform_wiring.stop.assert_called_once_with()


# ── (5) _install_signal_handlers registers SIGINT/SIGTERM on POSIX ─────


class TestInstallSignalHandlers:
    """``_install_signal_handlers`` must register SIGINT/SIGTERM handlers
    on POSIX so Ctrl+C / ``kill`` triggers graceful shutdown."""

    @pytest.mark.skipif(
        not hasattr(signal, "SIGTERM"),
        reason="SIGTERM not available on this platform (Windows)",
    )
    def test_registers_sigint_and_sigterm_handlers(self, controller, monkeypatch):
        """After ``_install_signal_handlers()``, ``signal.getsignal`` for
        SIGINT and SIGTERM must return the installed handler (not the
        default SIG_DFL / SIG_IGN)."""
        # Save the original handlers so we can restore them after the
        # test (signal handlers are process-global).
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        try:
            controller._install_signal_handlers()
            new_sigint = signal.getsignal(signal.SIGINT)
            new_sigterm = signal.getsignal(signal.SIGTERM)
            assert new_sigint is not signal.SIG_DFL, "_install_signal_handlers must register a SIGINT handler"
            assert new_sigint is not signal.SIG_IGN, "_install_signal_handlers must register a SIGINT handler"
            assert new_sigterm is not signal.SIG_DFL, "_install_signal_handlers must register a SIGTERM handler"
            assert new_sigterm is not signal.SIG_IGN, "_install_signal_handlers must register a SIGTERM handler"
            assert new_sigint is new_sigterm, "Both signals should share the same handler closure"
        finally:
            # Restore the original handlers.
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGINT, original_sigint)
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGTERM, original_sigterm)

    @pytest.mark.skipif(
        not hasattr(signal, "SIGHUP"),
        reason="SIGHUP not available on this platform (Windows)",
    )
    def test_registers_sighup_handler_on_posix(self, controller, monkeypatch):
        """PVT-G5-014: ``_install_signal_handlers`` must also register
        a SIGHUP handler on POSIX so terminal close / SSH disconnect
        triggers graceful shutdown (default action terminates
        immediately without running atexit handlers)."""
        original_sighup = signal.getsignal(signal.SIGHUP)

        try:
            controller._install_signal_handlers()
            new_sighup = signal.getsignal(signal.SIGHUP)
            assert new_sighup is not signal.SIG_DFL, "_install_signal_handlers must register a SIGHUP handler on POSIX"
            assert new_sighup is not signal.SIG_IGN, "_install_signal_handlers must register a SIGHUP handler on POSIX"
            # SIGHUP shares the same handler closure as SIGINT/SIGTERM.
            new_sigint = signal.getsignal(signal.SIGINT)
            assert new_sighup is new_sigint, "SIGHUP should share the same handler closure as SIGINT/SIGTERM"
        finally:
            with contextlib.suppress(Exception):
                signal.signal(signal.SIGHUP, original_sighup)


# ── (6) _atexit_cleanup safety net ─────────────────────────────────────


class TestAtexitCleanupSafetyNet:
    """``_atexit_cleanup`` must be safe to call multiple times, must
    short-circuit when ``_shutting_down`` is True, and must NEVER
    raise — even if ``_do_cleanup`` raises."""

    def test_atexit_cleanup_when_not_shutting_down_runs_do_cleanup(self, controller, fake_app):
        """When ``_shutting_down`` is False, ``_atexit_cleanup`` must
        invoke ``app._do_cleanup()`` (the delegate)."""
        fake_app._shutting_down = False
        controller._atexit_cleanup()
        fake_app._do_cleanup.assert_called_once_with()

    def test_atexit_cleanup_when_shutting_down_short_circuits(self, controller, fake_app):
        """When ``_shutting_down`` is True (quit/restart already ran),
        ``_atexit_cleanup`` must early-return WITHOUT calling
        ``_do_cleanup()`` again — avoids the spurious "[ATEXIT] Running
        emergency cleanup" log line on every intentional shutdown."""
        fake_app._shutting_down = True
        controller._atexit_cleanup()
        fake_app._do_cleanup.assert_not_called()

    def test_atexit_cleanup_safe_to_call_multiple_times(self, controller, fake_app):
        """Calling ``_atexit_cleanup`` multiple times must not raise.

        The first call (with ``_shutting_down=False``) runs
        ``_do_cleanup``. The second call ALSO runs ``_do_cleanup`` (the
        delegate on the app), but the delegate's side_effect is
        ``controller._do_cleanup`` which short-circuits via the
        ``_cleanup_done`` flag set on the first call."""
        fake_app._shutting_down = False
        controller._atexit_cleanup()
        controller._atexit_cleanup()
        # The delegate was called twice (atexit doesn't know about
        # _shutting_down unless set), but the body ran exactly once.
        assert fake_app._do_cleanup.call_count == 2
        fake_app.history_db.flush.assert_called_once()

    def test_atexit_cleanup_never_raises_when_do_cleanup_raises(self, controller, fake_app):
        """If ``app._do_cleanup()`` raises, ``_atexit_cleanup`` must
        catch the exception and log it — NEVER propagate out of an
        atexit handler (would mask the original exit cause).

        Mirrors ``tests/test_app_cleanup.py::
        test_atexit_cleanup_never_raises``.
        """
        fake_app._shutting_down = False
        fake_app._do_cleanup = MagicMock(side_effect=RuntimeError("boom"))
        # Must not raise.
        controller._atexit_cleanup()


# ── (7) _atexit_log ────────────────────────────────────────────────────


class TestAtexitLog:
    """``_atexit_log`` must warn when the process exits without
    ``quit()`` having been called (``_shutting_down_event`` not set)."""

    def test_atexit_log_warns_when_not_shutting_down(self, controller, fake_app, caplog):
        """When ``_shutting_down_event`` is not set, ``_atexit_log`` must
        log a warning so operators can see the process was likely killed
        externally."""
        fake_app._shutting_down_event.clear()
        with caplog.at_level("WARNING"):
            controller._atexit_log()
        assert any("likely killed externally" in rec.message for rec in caplog.records), (
            "_atexit_log must warn that the process likely exited without quit()"
        )

    def test_atexit_log_silent_when_shutting_down(self, controller, fake_app, caplog):
        """When ``_shutting_down_event`` is set (intentional shutdown),
        ``_atexit_log`` must NOT warn — the exit was expected."""
        fake_app._shutting_down_event.set()
        with caplog.at_level("WARNING"):
            controller._atexit_log()
        assert not any("likely killed externally" in rec.message for rec in caplog.records), (
            "_atexit_log must not warn when _shutting_down_event is set"
        )


# ── (8) _install_win32_console_handler is a no-op off-Windows ──────────


class TestInstallWin32ConsoleHandler:
    """``_install_win32_console_handler`` must short-circuit on non-Windows
    platforms (the SetConsoleCtrlHandler API doesn't exist there)."""

    def test_noop_when_not_windows(self, controller, fake_app, monkeypatch):
        """When ``is_windows()`` returns False, the handler must return
        immediately without setting ``_console_handler`` / ``_kernel32``."""
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
        controller._install_win32_console_handler()
        # _console_handler / _kernel32 should NOT have been set on the app.
        assert not hasattr(fake_app, "_console_handler") or fake_app._console_handler is None
        assert not hasattr(fake_app, "_kernel32") or fake_app._kernel32 is None

    def test_noop_when_pythonw_exe(self, controller, fake_app, monkeypatch):
        """Even on Windows, ``_install_win32_console_handler`` must skip
        when running under ``pythonw.exe`` (no console attached)."""
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
        monkeypatch.setattr(sys, "executable", "/fake/path/pythonw.exe")
        controller._install_win32_console_handler()
        # _console_handler / _kernel32 should NOT have been set.
        assert not hasattr(fake_app, "_console_handler") or fake_app._console_handler is None


# ── (9) _win32_console_handler ctrl_type routing ───────────────────────


class TestWin32ConsoleHandlerRouting:
    """``_win32_console_handler`` must return True for handled ctrl-types
    (close/logoff/shutdown/ctrl-c/ctrl-break) and False for unknown
    types. The close branch must call ``FreeConsole``; the logoff /
    shutdown / ctrl-c / ctrl-break branches must spawn a ``quit`` thread."""

    def test_close_event_calls_free_console_and_returns_true(self, controller, fake_app):
        """ctrl_close_event (2) must call ``_kernel32.FreeConsole()`` and
        return True so the tray app survives console closure."""
        fake_app._kernel32 = MagicMock()
        # Ensure _devnull is already open so the open() branch is skipped.
        fake_app._devnull = MagicMock()
        fake_app._devnull.closed = False

        result = controller._win32_console_handler(2)
        assert result is True
        fake_app._kernel32.FreeConsole.assert_called_once_with()

    def test_unknown_ctrl_type_returns_false(self, controller, fake_app):
        """An unknown ctrl_type (e.g. 99) must return False so Windows
        falls back to the next handler in the chain."""
        result = controller._win32_console_handler(99)
        assert result is False


# post-cleanup shutdown watchdog (non-main-thread quit) ──────


class TestShutdownWatchdog:
    """GT-43: when ``quit()`` runs on a non-main thread, ``_do_cleanup()``
    completes, and the main thread is still parked in ``tray.run()``, a
    daemon-thread watchdog fires ``os._exit(0)`` after
    ``SHUTDOWN_WATCHDOG_TIMEOUT_S`` seconds as a last-resort hard kill.
    """

    def test_watchdog_armed_when_quit_runs_on_non_main_thread(self, controller, fake_app, monkeypatch):
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        armed_calls: list[float] = []

        def _spy_arm(timeout_s: float) -> None:
            armed_calls.append(timeout_s)

        monkeypatch.setattr(controller, "_arm_shutdown_watchdog", _spy_arm)

        done = threading.Event()
        error_holder: list = []

        def _run_quit():
            try:
                controller.quit()
            except BaseException as exc:
                error_holder.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=_run_quit, name="test-quit-thread")
        t.start()
        done.wait(timeout=5.0)

        assert not error_holder, f"quit() on non-main thread raised: {error_holder}"
        assert armed_calls == [voice_typer.server.shutdown_controller.SHUTDOWN_WATCHDOG_TIMEOUT_S], (
            f"GT-43: quit() on non-main thread must arm the watchdog; got armed_calls={armed_calls}"
        )

    def test_watchdog_NOT_armed_when_quit_runs_on_main_thread(self, controller, fake_app, monkeypatch):  # noqa: N802
        fake_app._do_cleanup = MagicMock()
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        armed_calls: list[float] = []
        monkeypatch.setattr(
            controller,
            "_arm_shutdown_watchdog",
            lambda timeout_s: armed_calls.append(timeout_s),
        )

        controller.quit()

        assert armed_calls == [], (
            f"GT-43: quit() on main thread must NOT arm the watchdog; got armed_calls={armed_calls}"
        )

    def test_watchdog_calls_os_exit_after_timeout(self, monkeypatch):
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = MagicMock()
        ctrl = ShutdownController(fake_app)

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        start = time.monotonic()
        ctrl._arm_shutdown_watchdog(0.2)
        time.sleep(0.6)
        elapsed = time.monotonic() - start

        assert exit_calls == [0], (
            f"GT-43: watchdog must call os._exit(0) after the timeout; got exit_calls={exit_calls}"
        )
        assert elapsed >= 0.2, f"GT-43: watchdog fired too early — expected ≥0.2s, got {elapsed:.2f}s"

    def test_drain_shutdown_watchdogs_cancels_before_os_exit(self, monkeypatch):
        """A leaked shutdown watchdog (armed by a non-main-thread
        restart/quit test that never lets the process exit) must be
        disarmable via ``_drain_shutdown_watchdogs()`` before it fires
        the real ``os._exit(0)`` — otherwise it kills the whole xdist
        worker mid-suite with no traceback."""
        from voice_typer.server.shutdown.lifecycle import (
            _LIVE_SHUTDOWN_WATCHDOG_THREADS,
            _WATCHDOG_CANCEL_EVENTS,
            _drain_shutdown_watchdogs,
        )
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = MagicMock()
        ctrl = ShutdownController(fake_app)

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code=0: exit_calls.append(code))

        # Arm a real watchdog with a LONG timeout (like a leaked one).
        ctrl._arm_shutdown_watchdog(30.0)
        assert len(list(_LIVE_SHUTDOWN_WATCHDOG_THREADS)) == 1, (
            "the armed watchdog must be registered so the drain can find it"
        )

        # Drain must cancel it without waiting 30s and without os._exit.
        _drain_shutdown_watchdogs()

        assert exit_calls == [], f"drain must cancel the watchdog before os._exit; got exit_calls={exit_calls}"
        assert not list(_LIVE_SHUTDOWN_WATCHDOG_THREADS), "drained watchdog threads must be removed from the registry"
        assert not list(_WATCHDOG_CANCEL_EVENTS), "drained watchdog cancel events must be removed from the registry"


# recorder _force_closed shutdown barrier ────────────────────


class TestRecorderForceClosedBarrier:
    """GT-70: when ``recorder.stop()`` (or ``recorder.discard()``) times
    out, ``_do_cleanup()`` must set a ``_force_closed`` flag on the
    recorder and SKIP the subsequent ``recorder.shutdown_mic_watcher()``
    call."""

    def test_shutdown_mic_watcher_skipped_when_recorder_stop_times_out(self, controller, fake_app, monkeypatch):
        fake_app.recorder.recording = True

        import voice_typer.server.shutdown_controller as _sc

        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "recorder.stop":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_stop():
            blocked.wait(timeout=5.0)

        fake_app.recorder.stop = _blocking_stop

        controller._do_cleanup()

        blocked.set()

        fake_app.recorder.shutdown_mic_watcher.assert_not_called()
        assert getattr(fake_app.recorder, "_force_closed", None) is True, (
            "GT-70: recorder.stop() timeout must set app.recorder._force_closed = True"
        )

    def test_shutdown_mic_watcher_called_when_recorder_stop_completes(self, controller, fake_app):
        fake_app.recorder.recording = True

        controller._do_cleanup()

        fake_app.recorder.stop.assert_called_once_with()
        fake_app.recorder.shutdown_mic_watcher.assert_called_once_with()

    def test_timeout_sentinel_distinct_from_none(self):
        from voice_typer.server.shutdown_controller import TIMEOUT

        assert TIMEOUT is not None, "GT-70: TIMEOUT sentinel must not be None"
        assert TIMEOUT is TIMEOUT, "TIMEOUT sentinel identity check"


# _force_closed read-side behavior test ──────────────────────


class TestForceClosedReadSideGuard:
    """ZR-35: the ``_force_closed`` flag must be a REAL behavior gate, not
    a write-only test-visible attribute.

    Pre-fix, ``shutdown_controller._do_cleanup()`` set
    ``app.recorder._force_closed = True`` under a
    ``contextlib.suppress(Exception)`` wrapper when ``recorder.stop()``
    timed out — but the ``Recorder`` class never declared, read, nor
    used ``_force_closed``. The attribute was dead write-only state,
    and the old test (TestRecorderForceClosedBarrier above) only
    asserted the WRITE happened, not that any behavior depended on it.
    The comment in ``shutdown_controller.py`` promised "so the
    recorder itself can [use it]" — the read side never landed.

    The fix (landed in ``recorder.py`` by another agent) implements
    the read side: ``Recorder.__init__`` declares
    ``self._force_closed: bool = False`` and
    ``Recorder.shutdown_mic_watcher`` short-circuits with ``return``
    when ``self._force_closed`` is True. These tests pin the
    behavior contract — that calling ``shutdown_mic_watcher`` on a
    force-closed recorder does NOT delegate to
    ``_devices.shutdown_mic_watcher()`` (so the leaked worker thread
    still touching the PortAudio stream is not raced).

    The tests construct a real ``Recorder`` instance (not a MagicMock)
    so the read-side guard in the production code path is exercised.
    """

    def _make_test_recorder(self):
        """Construct a real ``Recorder`` with mocked collaborators.

        Avoids PortAudio / sounddevice initialization — we only need
        the ``shutdown_mic_watcher`` method to run, which delegates to
        ``self._devices.shutdown_mic_watcher()``. The ``_devices``
        attribute is replaced with a MagicMock so we can assert
        whether the delegate was called.
        """
        from voice_typer.server.recording.recorder import Recorder

        # Recorder.__init__ signature: (config, audio_processor, thread_registry)
        # The body uses ``config`` only to read attributes; pass a MagicMock
        # so any attribute access returns a mock by default.
        config = MagicMock()
        recorder = Recorder(
            config=config,
            audio_processor=MagicMock(),
            thread_registry=MagicMock(),
        )
        # Replace the DeviceManager delegate with a MagicMock so we can
        # assert call counts on ``shutdown_mic_watcher`` without touching
        # the real PortAudio device-watcher teardown.
        recorder._devices = MagicMock()
        return recorder

    def test_shutdown_mic_watcher_short_circuits_when_force_closed_set(self):
        """ZR-35: when ``_force_closed = True``, calling
        ``shutdown_mic_watcher`` must NOT delegate to
        ``_devices.shutdown_mic_watcher()``.

        This is the core read-side guard. Without it, the flag is dead
        state — ``shutdown_controller`` writes True but nothing reads
        it, so a subsequent cleanup call would race the leaked worker
        thread still touching the PortAudio stream.
        """
        recorder = self._make_test_recorder()
        # Sanity: flag starts False (declared in Recorder.__init__).
        assert recorder._force_closed is False, "ZR-35: Recorder.__init__ must declare _force_closed: bool = False"

        # Simulate the force-closed condition (as shutdown_controller
        # does when recorder.stop() times out).
        recorder._force_closed = True

        # Call shutdown_mic_watcher — should short-circuit.
        recorder.shutdown_mic_watcher()

        # The delegate MUST NOT have been called.
        recorder._devices.shutdown_mic_watcher.assert_not_called()

    def test_shutdown_mic_watcher_delegates_when_force_closed_false(self):
        """ZR-35 (companion): when ``_force_closed = False`` (the
        default), ``shutdown_mic_watcher`` MUST delegate to
        ``_devices.shutdown_mic_watcher()`` as usual. Pins that the
        guard doesn't false-positive (skipping the delegate when it
        shouldn't).
        """
        recorder = self._make_test_recorder()
        assert recorder._force_closed is False

        recorder.shutdown_mic_watcher()

        # The delegate MUST have been called exactly once.
        recorder._devices.shutdown_mic_watcher.assert_called_once_with()

    def test_force_closed_is_declared_in_init(self):
        """ZR-35 (init contract): ``Recorder.__init__`` must declare
        ``_force_closed: bool = False`` so the attribute exists on
        every instance (not just when ``shutdown_controller`` writes
        it). Without this declaration, accessing
        ``recorder._force_closed`` before any timeout would raise
        ``AttributeError``.
        """
        recorder = self._make_test_recorder()
        # The attribute must exist with the default False value —
        # NOT raise AttributeError.
        assert hasattr(recorder, "_force_closed"), (
            "ZR-35: Recorder.__init__ must declare _force_closed so the "
            "attribute exists before shutdown_controller writes it"
        )
        assert recorder._force_closed is False, "ZR-35: _force_closed must default to False"


# in-flight timer drain after _cancel_pending_timers ──────────


class TestInFlightTimerDrain:
    """GT-72: ``_do_cleanup()`` must drain in-flight timer threads with a
    short bounded timeout AFTER ``_cancel_pending_timers()``. The
    delegate only calls ``Timer.cancel()`` (a no-op for already-fired
    timers); a timer whose ``guarded_func`` has already passed the
    generation check but hasn't yet called ``func()`` would race the
    subsystem teardown below."""

    def test_do_cleanup_joins_in_flight_timer_threads(self, controller, fake_app, monkeypatch):
        import threading as _threading

        in_flight_started = _threading.Event()
        in_flight_can_finish = _threading.Event()

        def _slow_func():
            in_flight_started.set()
            in_flight_can_finish.wait(timeout=5.0)

        timer = _threading.Timer(0.01, _slow_func)
        timer.daemon = True

        timers_coord = MagicMock()
        timers_coord._pending_timers_lock = _threading.Lock()
        timers_coord._pending_timers = [timer]
        fake_app.timers = timers_coord

        timer.start()
        assert in_flight_started.wait(timeout=2.0), "GT-72: test setup failed — in-flight timer never started"

        cleanup_done = _threading.Event()
        cleanup_errors: list = []

        def _run_cleanup():
            try:
                controller._do_cleanup()
            except BaseException as exc:
                cleanup_errors.append(exc)
            finally:
                cleanup_done.set()

        cleanup_thread = _threading.Thread(target=_run_cleanup, name="test-cleanup-thread-gt72")
        cleanup_thread.start()

        time.sleep(0.1)
        in_flight_can_finish.set()

        assert cleanup_done.wait(timeout=5.0), "GT-72: _do_cleanup didn't complete after releasing in-flight timer"
        assert not cleanup_errors, f"GT-72: _do_cleanup raised: {cleanup_errors}"

        fake_app._cancel_pending_timers.assert_called_once_with()

    def test_do_cleanup_does_not_deadlock_when_no_timers_coord(self, controller, fake_app):
        fake_app.timers = None

        controller._do_cleanup()

        fake_app._cancel_pending_timers.assert_called_once_with()
