"""XZ-R17-06 + XZ-R17-11 regression tests for shutdown_controller.py."""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController


@pytest.fixture(autouse=True)
def _stub_os_exit(monkeypatch):
    """UE-1: ``_do_fast_cleanup()`` ends with ``os._exit(0)``. Stub it"""
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
        """
        Calling _do_fast_cleanup twice is safe, the second invocation
        Windows logoff/shutdown callback must not return True without
        """
        controller, app = _make_controller_with_app()
        controller._do_fast_cleanup()
        # Second call: arm a spy on crash_recovery.flush, it MUST be
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
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
        if os.name != "posix":
            pytest.skip("XZ-R17-06 POSIX-only test (Windows uses CloseHandle, not flock release)")
        controller, app = _make_controller_with_app()
        mutex = MagicMock()
        app._mutex_handle = mutex
        controller._do_fast_cleanup()
        mutex.release.assert_called_once()
        assert app._mutex_handle is None

    def test_do_fast_cleanup_never_raises(self, _stub_os_exit):
        """_do_fast_cleanup must never propagate exceptions (best-effort)."""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = RuntimeError("simulated failure")
        # Must not raise.
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0]


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

        # Second call, no backends, should not raise.
        controller._teardown_hotkeys()
        backend.stop.assert_called_once()


# _do_fast_cleanup ends with os._exit(0) + win32 routing ─────


class TestFastCleanupOsExit:  # noqa: N801
    """could race our own cleanup)."""

    def test_do_fast_cleanup_calls_os_exit_zero(self, _stub_os_exit):
        """``_do_fast_cleanup()`` must call ``os._exit(0)`` exactly once"""
        controller, _ = _make_controller_with_app()
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0], (
            f"UE-1: _do_fast_cleanup must call os._exit(0) at the end; got os._exit called with {_stub_os_exit}"
        )

    def test_do_fast_cleanup_calls_os_exit_even_when_cleanup_done_already(self, _stub_os_exit):
        """When ``_cleanup_done`` is already True (prior cleanup ran),"""
        controller, app = _make_controller_with_app()
        app._cleanup_done = True
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        # MUST still call os._exit(0).
        assert _stub_os_exit == [0], (
            f"UE-1: _do_fast_cleanup must call os._exit(0) even when _cleanup_done is already True; got {_stub_os_exit}"
        )

    def test_do_fast_cleanup_calls_os_exit_after_all_cleanup_steps(self, _stub_os_exit, monkeypatch):
        """All critical cleanup steps must run BEFORE ``os._exit(0)``."""
        controller, app = _make_controller_with_app()
        call_order: list[str] = []

        def _record_crash_flush(*args, **kwargs):
            call_order.append("crash_recovery.flush")

        def _record_history_flush(*args, **kwargs):
            call_order.append("history_db.flush")

        def _record_os_exit(code=0):
            call_order.append("os._exit")
            # Don't actually exit (the autouse fixture already prevents

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


class TestWin32RoutingFastCleanup:  # noqa: N801
    """UE-1: ``win32_console_handler`` must route"""

    def test_logoff_event_routes_to_fast_cleanup(self, _stub_os_exit):
        """critical-only path that runs in <3s with a final ``os._exit(0)``"""
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
        """The fast-cleanup dispatch must be SYNCHRONOUS (not on a daemon"""
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
        """no OS-imposed deadline, so the slow path is correct."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        controller.quit = MagicMock()  # type: ignore[assignment]
        controller._do_fast_cleanup = MagicMock()  # type: ignore[assignment]

        for ctrl_type in (0, 1):
            controller.quit.reset_mock()
            controller._do_fast_cleanup.reset_mock()
            result = win32_console_handler(controller, ctrl_type)
            assert result is True, f"win32_console_handler must return True for ctrl_type={ctrl_type}"
            # daemon thread (RACE-016), so the call may not have landed
            deadline = time.monotonic() + 2.0
            while controller.quit.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
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
