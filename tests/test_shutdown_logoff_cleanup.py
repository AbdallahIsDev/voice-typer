"""XZ-R17-06 + XZ-R17-11 regression tests for shutdown_controller.py.

XZ-R17-06: Windows logoff/shutdown fast cleanup path.
- ``_do_fast_cleanup()`` runs ONLY critical-resource cleanup with 1s
  timeouts (crash_recovery.flush, history_db.flush, recorder.stop,
  _clear_backend_pid_file, mutex release) targeting <3s total.
- Idempotent with ``_do_cleanup()`` via the shared ``_cleanup_done`` guard.

UE-1 (XZ-R17-06 follow-up): ``_do_fast_cleanup()`` now ends with
``os._exit(0)`` (the OS is killing us; bypassing atexit is correct).
Tests that invoke ``_do_fast_cleanup()`` directly MUST mock
``os._exit`` via the autouse ``_stub_os_exit`` fixture below so the
test runner doesn't actually exit. ``win32_console_handler`` now
routes logoff/shutdown events to ``_do_fast_cleanup()`` instead of
``controller.quit()`` — see TestWin32RoutingFastCleanup below.

XZ-R17-11: null hotkey backend refs after parallel stop.
- ``_teardown_hotkeys()`` nulls ``_hotkey_backend``, ``_esc_backend``,
  ``_repaste_backend`` after the parallel stop so a subsequent
  ``_do_cleanup`` pass does NOT re-enter ``stop()`` on torn-down backends.
"""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController


@pytest.fixture(autouse=True)
def _stub_os_exit(monkeypatch):
    """UE-1: ``_do_fast_cleanup()`` ends with ``os._exit(0)``. Stub it
    so the test runner doesn't actually exit when tests invoke
    ``_do_fast_cleanup()`` directly. Tests that need to assert on the
    call can read the recorded calls from the same fixture by depending
    on ``_stub_os_exit``."""
    calls: list[int] = []
    monkeypatch.setattr(
        "voice_typer.server.shutdown_controller.os._exit",
        lambda code=0: calls.append(code),
    )
    yield calls


def _make_controller_with_app():
    """Build a ShutdownController with a MagicMock app for unit testing."""
    app = MagicMock()
    app._cleanup_done = False
    app._shutting_down = False
    app._shutting_down_event = MagicMock()
    app._shutting_down_event.set = MagicMock()
    app._crash_recovery = None
    app.history_db = None
    app.recorder = None
    app._mutex_handle = None
    app.hotkeys._hotkey_backend = None
    app.hotkeys._esc_backend = None
    app.hotkeys._repaste_backend = None
    controller = ShutdownController(app)
    return controller, app


# _do_fast_cleanup ──────────────────────────────────────


class TestFastCleanup:  # noqa: N801
    """XZ-R17-06: critical-only cleanup for Windows logoff/shutdown."""

    def test_do_fast_cleanup_method_exists(self):
        """The _do_fast_cleanup method must exist on ShutdownController."""
        controller, _ = _make_controller_with_app()
        assert hasattr(controller, "_do_fast_cleanup"), (
            "ShutdownController must have _do_fast_cleanup method (XZ-R17-06)"
        )
        assert callable(controller._do_fast_cleanup)

    def test_do_fast_cleanup_sets_cleanup_done(self, _stub_os_exit):
        """_do_fast_cleanup sets _cleanup_done so _do_cleanup is a no-op after."""
        controller, app = _make_controller_with_app()
        assert app._cleanup_done is False
        controller._do_fast_cleanup()
        assert app._cleanup_done is True, (
            "_do_fast_cleanup must set _cleanup_done so a subsequent _do_cleanup call is a no-op (idempotency guard)"
        )

    def test_do_fast_cleanup_idempotent(self, _stub_os_exit):
        """Calling _do_fast_cleanup twice is safe — the second invocation
        STILL runs the critical flushes (they are idempotent; running
        them twice is bounded by 1s timeouts and safe).

        UE-1: the second call still calls ``os._exit(0)`` (because the
        Windows logoff/shutdown callback must not return True without
        exiting). The previous ``if not already_done:`` gate skipped
        the second invocation's flushes — which created a false
        positive under quit-during-logoff (the slow ``_do_cleanup``
        had set ``_cleanup_done = True`` but not yet reached the
        parallel batch when the fast path fired; the fast path's
        flushes were skipped, and ``os._exit(0)`` killed the slow
        path mid-flight — both paths skipped the critical writes).
        The fix removes the gate so the flushes run unconditionally.
        """
        controller, app = _make_controller_with_app()
        controller._do_fast_cleanup()
        # Second call: arm a spy on crash_recovery.flush — it MUST be
        # called (unconditional flush — running twice is safe), AND
        # os._exit(0) MUST fire again.
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        # os._exit(0) must have been called twice (once per
        # invocation) — the second call still exits to honor the OS
        # force-kill window even though cleanup already ran.
        assert _stub_os_exit == [0, 0], (
            f"UE-1: both _do_fast_cleanup invocations must call os._exit(0); got {_stub_os_exit}"
        )

    def test_do_fast_cleanup_flushes_crash_recovery(self, _stub_os_exit):
        """_do_fast_cleanup calls crash_recovery.flush with 1s timeout."""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)

    def test_do_fast_cleanup_flushes_history_db(self, _stub_os_exit):
        """_do_fast_cleanup calls history_db.flush."""
        controller, app = _make_controller_with_app()
        app.history_db = MagicMock()
        controller._do_fast_cleanup()
        app.history_db.flush.assert_called_once()

    def test_do_fast_cleanup_stops_recorder(self, _stub_os_exit):
        """_do_fast_cleanup calls recorder.stop when recording is True."""
        controller, app = _make_controller_with_app()
        app.recorder = MagicMock()
        app.recorder.recording = True
        controller._do_fast_cleanup()
        app.recorder.stop.assert_called_once()

    def test_do_fast_cleanup_skips_recorder_when_not_recording(self, _stub_os_exit):
        """_do_fast_cleanup skips recorder.stop when recording is False."""
        controller, app = _make_controller_with_app()
        app.recorder = MagicMock()
        app.recorder.recording = False
        controller._do_fast_cleanup()
        app.recorder.stop.assert_not_called()

    def test_do_fast_cleanup_releases_mutex_handle(self, _stub_os_exit):
        """_do_fast_cleanup releases the mutex handle (POSIX path)."""
        # POSIX-only: on Windows ``_do_fast_cleanup`` takes the
        # ``ctypes.windll.kernel32.CloseHandle`` branch (the OS closes
        # the handle; there is no ``release()``). ``is_windows()`` is
        # the real module-level import in ``shutdown_controller.py``,
        # so passing a MagicMock into the real ctypes function triggers
        # unittest.mock's child-mock recursion (native stack overflow
        # on Windows). Skip on non-POSIX rather than exercising the
        # Windows branch with a mock.
        if os.name != "posix":
            pytest.skip("XZ-R17-06 POSIX-only test (Windows uses CloseHandle, not flock release)")
        controller, app = _make_controller_with_app()
        mutex = MagicMock()
        app._mutex_handle = mutex
        controller._do_fast_cleanup()
        mutex.release.assert_called_once()
        assert app._mutex_handle is None

    def test_do_fast_cleanup_never_raises(self, _stub_os_exit):
        """_do_fast_cleanup must never propagate exceptions (best-effort).

        UE-1: even if a cleanup step raises, the method must still
        reach ``os._exit(0)`` (the outer ``try/except`` in
        ``win32_console_handler`` is the safety net for the rare case
        where the exit itself is unreachable)."""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = RuntimeError("simulated failure")
        # Must not raise.
        controller._do_fast_cleanup()
        # os._exit(0) must still be called.
        assert _stub_os_exit == [0]


# null hotkey backend refs ──────────────────────────────


class TestNullHotkeyRefs:  # noqa: N801
    """XZ-R17-11: _teardown_hotkeys nulls backend refs after parallel stop."""

    def test_teardown_hotkeys_nulls_all_three_backends(self):
        """After _teardown_hotkeys, all three backend refs are None."""
        controller, app = _make_controller_with_app()

        # Give it real backends that have a stop() method.
        backend1 = MagicMock()
        backend2 = MagicMock()
        backend3 = MagicMock()
        app.hotkeys._hotkey_backend = backend1
        app.hotkeys._esc_backend = backend2
        app.hotkeys._repaste_backend = backend3

        controller._teardown_hotkeys()

        # All three backends should have been stopped.
        backend1.stop.assert_called_once()
        backend2.stop.assert_called_once()
        backend3.stop.assert_called_once()

        # refs should now be None.
        assert app.hotkeys._hotkey_backend is None, "_hotkey_backend must be nulled after _teardown_hotkeys (XZ-R17-11)"
        assert app.hotkeys._esc_backend is None, "_esc_backend must be nulled after _teardown_hotkeys (XZ-R17-11)"
        assert app.hotkeys._repaste_backend is None, (
            "_repaste_backend must be nulled after _teardown_hotkeys (XZ-R17-11)"
        )

    def test_teardown_hotkeys_idempotent_after_nulling(self):
        """A second _teardown_hotkeys call is safe (no backends to stop)."""
        controller, app = _make_controller_with_app()
        backend = MagicMock()
        app.hotkeys._hotkey_backend = backend

        controller._teardown_hotkeys()
        assert app.hotkeys._hotkey_backend is None

        # Second call — no backends, should not raise.
        controller._teardown_hotkeys()
        # backend.stop was called only once (first call).
        backend.stop.assert_called_once()


# _do_fast_cleanup ends with os._exit(0) + win32 routing ─────


class TestFastCleanupOsExit:  # noqa: N801
    """UE-1: ``_do_fast_cleanup()`` must end with ``os._exit(0)`` so the
    Windows logoff/shutdown callback returns control to the OS via the
    async-signal-safe exit primitive (bypassing atexit handlers that
    could race our own cleanup)."""

    def test_do_fast_cleanup_calls_os_exit_zero(self, _stub_os_exit):
        """``_do_fast_cleanup()`` must call ``os._exit(0)`` exactly once
        at the end (cleanup is complete; bypassing atexit is acceptable
        because the OS is killing us)."""
        controller, _ = _make_controller_with_app()
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0], (
            f"UE-1: _do_fast_cleanup must call os._exit(0) at the end; got os._exit called with {_stub_os_exit}"
        )

    def test_do_fast_cleanup_calls_os_exit_even_when_cleanup_done_already(self, _stub_os_exit):
        """When ``_cleanup_done`` is already True (prior cleanup ran),
        ``_do_fast_cleanup`` STILL runs its critical flushes
        UNCONDITIONALLY (the writes are idempotent — running them
        twice is safe) AND calls ``os._exit(0)`` — we're being
        invoked from the Windows logoff/shutdown callback and must
        not return True (which would let the OS re-evaluate us).

        The previous ``if not already_done:`` gate created a false
        positive under quit-during-logoff: the slow ``_do_cleanup``
        had set ``_cleanup_done = True`` at its start but had not yet
        reached the parallel batch when the fast path fired; the fast
        path's flushes were skipped, and ``os._exit(0)`` killed the
        slow path mid-flight — both paths skipped the critical writes.
        The fix removes the gate so the flushes run unconditionally."""
        controller, app = _make_controller_with_app()
        app._cleanup_done = True
        # crash_recovery.flush MUST be called even though _cleanup_done
        # is True (unconditional flush — running twice is safe).
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        # MUST still call os._exit(0).
        assert _stub_os_exit == [0], (
            f"UE-1: _do_fast_cleanup must call os._exit(0) even when _cleanup_done is already True; got {_stub_os_exit}"
        )

    def test_do_fast_cleanup_calls_os_exit_after_all_cleanup_steps(self, _stub_os_exit, monkeypatch):
        """All critical cleanup steps must run BEFORE ``os._exit(0)``.
        Verify by recording the order of calls."""
        controller, app = _make_controller_with_app()
        call_order: list[str] = []

        def _record_crash_flush(*args, **kwargs):
            call_order.append("crash_recovery.flush")

        def _record_history_flush(*args, **kwargs):
            call_order.append("history_db.flush")

        def _record_os_exit(code=0):
            call_order.append("os._exit")
            # Don't actually exit (the autouse fixture already prevents
            # the real exit; this spy is just for ordering).

        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = _record_crash_flush
        app.history_db = MagicMock()
        app.history_db.flush.side_effect = _record_history_flush
        # Replace the autouse stub with a recording stub.
        monkeypatch.setattr(
            "voice_typer.server.shutdown_controller.os._exit",
            _record_os_exit,
        )

        controller._do_fast_cleanup()

        assert "crash_recovery.flush" in call_order
        assert "history_db.flush" in call_order
        assert "os._exit" in call_order
        crash_idx = call_order.index("crash_recovery.flush")
        history_idx = call_order.index("history_db.flush")
        exit_idx = call_order.index("os._exit")
        assert crash_idx < exit_idx, f"UE-1: crash_recovery.flush must run BEFORE os._exit; got order: {call_order}"
        assert history_idx < exit_idx, f"UE-1: history_db.flush must run BEFORE os._exit; got order: {call_order}"


# win32_console_handler routing ──────────────────────────────


class TestWin32RoutingFastCleanup:  # noqa: N801
    """UE-1: ``win32_console_handler`` must route
    ``CTRL_LOGOFF_EVENT`` (5) / ``CTRL_SHUTDOWN_EVENT`` (6) to
    ``controller._do_fast_cleanup()`` (NOT ``controller.quit()``)."""

    def test_logoff_event_routes_to_fast_cleanup(self, _stub_os_exit):
        """CTRL_LOGOFF_EVENT (5) must invoke ``_do_fast_cleanup`` — the
        critical-only path that runs in <3s with a final ``os._exit(0)``
        (Windows force-kills the process after ~5s)."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        # Spy on _do_fast_cleanup (and confirm quit is NOT called).
        fast_cleanup_calls: list[int] = []

        def _spy_fast_cleanup():
            fast_cleanup_calls.append(1)

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]
        controller.quit = MagicMock()  # type: ignore[assignment]

        result = win32_console_handler(controller, 5)

        assert result is True, "UE-1: win32_console_handler must return True for CTRL_LOGOFF_EVENT"
        assert fast_cleanup_calls == [1], "UE-1: CTRL_LOGOFF_EVENT must invoke _do_fast_cleanup exactly once"
        (
            controller.quit.assert_not_called(),
            (
                "UE-1: CTRL_LOGOFF_EVENT must NOT invoke controller.quit() "
                "(the slow ~25-85s path would be force-killed by Windows "
                "before completing)"
            ),
        )

    def test_shutdown_event_routes_to_fast_cleanup(self, _stub_os_exit):
        """CTRL_SHUTDOWN_EVENT (6) must invoke ``_do_fast_cleanup``."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        fast_cleanup_calls: list[int] = []

        def _spy_fast_cleanup():
            fast_cleanup_calls.append(1)

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]
        controller.quit = MagicMock()  # type: ignore[assignment]

        result = win32_console_handler(controller, 6)

        assert result is True, "UE-1: win32_console_handler must return True for CTRL_SHUTDOWN_EVENT"
        assert fast_cleanup_calls == [1], "UE-1: CTRL_SHUTDOWN_EVENT must invoke _do_fast_cleanup exactly once"
        controller.quit.assert_not_called(), ("UE-1: CTRL_SHUTDOWN_EVENT must NOT invoke controller.quit()")

    def test_logoff_event_calls_fast_cleanup_synchronously(self, _stub_os_exit):
        """The fast-cleanup dispatch must be SYNCHRONOUS (not on a daemon
        thread) — the Win32 console-control callback runs on a dedicated
        OS thread and returning True signals "handled". Spawning a daemon
        thread would race the OS force-kill (~5s)."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()

        # Track which thread _do_fast_cleanup runs on.
        caller_threads: list[int] = []

        def _spy_fast_cleanup():
            caller_threads.append(threading.get_ident())

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]

        main_thread_id = threading.get_ident()
        win32_console_handler(controller, 5)

        assert caller_threads, "UE-1: _do_fast_cleanup must be called synchronously (not on a spawned thread)"
        assert caller_threads[0] == main_thread_id, (
            "UE-1: _do_fast_cleanup must run on the SAME thread as the "
            "win32_console_handler callback (synchronous dispatch)"
        )

    def test_ctrl_c_still_routes_to_quit(self):
        """Sanity: Ctrl+C (0) / Ctrl+Break (1) must STILL route to
        ``controller.quit()`` — only logoff/shutdown were changed to
        ``_do_fast_cleanup()``. Ctrl+C is a user-initiated signal with
        no OS-imposed deadline, so the slow path is correct."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        controller.quit = MagicMock()  # type: ignore[assignment]
        controller._do_fast_cleanup = MagicMock()  # type: ignore[assignment]

        for ctrl_type in (0, 1):
            controller.quit.reset_mock()
            controller._do_fast_cleanup.reset_mock()
            result = win32_console_handler(controller, ctrl_type)
            assert result is True, f"win32_console_handler must return True for ctrl_type={ctrl_type}"
            (
                controller.quit.assert_called_once_with(),
                (f"ctrl_type={ctrl_type} must route to controller.quit (unchanged)"),
            )
            (
                controller._do_fast_cleanup.assert_not_called(),
                (f"ctrl_type={ctrl_type} must NOT route to _do_fast_cleanup"),
            )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
