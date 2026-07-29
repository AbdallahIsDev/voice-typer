"""XZ-R17-06 + XZ-R17-11 regression tests for shutdown_controller.py.

XZ-R17-06: Windows logoff/shutdown fast cleanup path.
- ``_do_fast_cleanup()`` runs ONLY critical-resource cleanup with 1s
  timeouts (crash_recovery.flush, history_db.flush, recorder.stop,
  _clear_backend_pid_file, mutex release) targeting <3s total.
- Idempotent with ``_do_cleanup()`` via the shared ``_cleanup_done`` guard.

XZ-R17-11: null hotkey backend refs after parallel stop.
- ``_teardown_hotkeys()`` nulls ``_hotkey_backend``, ``_esc_backend``,
  ``_repaste_backend`` after the parallel stop so a subsequent
  ``_do_cleanup`` pass does NOT re-enter ``stop()`` on torn-down backends.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.shutdown_controller import ShutdownController


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


# ── XZ-R17-06: _do_fast_cleanup ──────────────────────────────────────


class TestXZ_R17_06_FastCleanup:  # noqa: N801
    """XZ-R17-06: critical-only cleanup for Windows logoff/shutdown."""

    def test_do_fast_cleanup_method_exists(self):
        """The _do_fast_cleanup method must exist on ShutdownController."""
        controller, _ = _make_controller_with_app()
        assert hasattr(controller, "_do_fast_cleanup"), (
            "ShutdownController must have _do_fast_cleanup method (XZ-R17-06)"
        )
        assert callable(controller._do_fast_cleanup)

    def test_do_fast_cleanup_sets_cleanup_done(self):
        """_do_fast_cleanup sets _cleanup_done so _do_cleanup is a no-op after."""
        controller, app = _make_controller_with_app()
        assert app._cleanup_done is False
        controller._do_fast_cleanup()
        assert app._cleanup_done is True, (
            "_do_fast_cleanup must set _cleanup_done so a subsequent "
            "_do_cleanup call is a no-op (idempotency guard)"
        )

    def test_do_fast_cleanup_idempotent(self):
        """Calling _do_fast_cleanup twice is a no-op on the second call."""
        controller, app = _make_controller_with_app()
        controller._do_fast_cleanup()
        # Second call should short-circuit at the _cleanup_done guard.
        # We verify by checking that crash_recovery.flush is NOT called
        # on the second call (it would be called on the first if present).
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_not_called(), (
            "second _do_fast_cleanup call must short-circuit at _cleanup_done"
        )

    def test_do_fast_cleanup_flushes_crash_recovery(self):
        """_do_fast_cleanup calls crash_recovery.flush with 1s timeout."""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)

    def test_do_fast_cleanup_flushes_history_db(self):
        """_do_fast_cleanup calls history_db.flush."""
        controller, app = _make_controller_with_app()
        app.history_db = MagicMock()
        controller._do_fast_cleanup()
        app.history_db.flush.assert_called_once()

    def test_do_fast_cleanup_stops_recorder(self):
        """_do_fast_cleanup calls recorder.stop when recording is True."""
        controller, app = _make_controller_with_app()
        app.recorder = MagicMock()
        app.recorder.recording = True
        controller._do_fast_cleanup()
        app.recorder.stop.assert_called_once()

    def test_do_fast_cleanup_skips_recorder_when_not_recording(self):
        """_do_fast_cleanup skips recorder.stop when recording is False."""
        controller, app = _make_controller_with_app()
        app.recorder = MagicMock()
        app.recorder.recording = False
        controller._do_fast_cleanup()
        app.recorder.stop.assert_not_called()

    def test_do_fast_cleanup_releases_mutex_handle(self):
        """_do_fast_cleanup releases the mutex handle (POSIX path)."""
        controller, app = _make_controller_with_app()
        mutex = MagicMock()
        app._mutex_handle = mutex
        # is_windows() is looked up dynamically from the app module;
        # MagicMock returns falsy for is_windows() by default.
        controller._do_fast_cleanup()
        mutex.release.assert_called_once()
        assert app._mutex_handle is None

    def test_do_fast_cleanup_never_raises(self):
        """_do_fast_cleanup must never propagate exceptions (best-effort)."""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = RuntimeError("simulated failure")
        # Must not raise.
        controller._do_fast_cleanup()


# ── XZ-R17-11: null hotkey backend refs ──────────────────────────────


class TestXZ_R17_11_NullHotkeyRefs:  # noqa: N801
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

        # XZ-R17-11: refs should now be None.
        assert app.hotkeys._hotkey_backend is None, (
            "_hotkey_backend must be nulled after _teardown_hotkeys (XZ-R17-11)"
        )
        assert app.hotkeys._esc_backend is None, (
            "_esc_backend must be nulled after _teardown_hotkeys (XZ-R17-11)"
        )
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


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
