"""YJ-2 regression: POSIX single-instance ``handle.release()`` must be called"""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController


class _FakeApp:
    """Minimal duck-typed stand-in for ``LausuApp``."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
        self._mutex_handle = None

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

        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()

        self._bubble_level_worker_stop = None
        self._bubble_level_queue = None
        self._bubble_level_worker = None

        self._do_cleanup = MagicMock()


@pytest.fixture
def fake_app(tmp_config_dir, monkeypatch):
    """A ``_FakeApp`` with the shutdown environment stubbed out (POSIX)."""
    monkeypatch.setattr("voice_typer.server.backend_pid._clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False, raising=False)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app`` (real body wired)."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


class TestPosixMutexHandleRelease:
    """YJ-2: ``_do_cleanup`` MUST release the POSIX single-instance handle."""

    def test_posix_release_called_when_handle_present(self, controller, fake_app, monkeypatch):
        """On POSIX, when ``app._mutex_handle`` is a handle-like object"""
        # Fake the POSIX platform so the  branch runs.
        monkeypatch.setattr("sys.platform", "linux")

        handle = MagicMock(name="_PosixSingleInstanceHandle")
        fake_app._mutex_handle = handle

        controller._do_cleanup()

        handle.release.assert_called_once_with()
        assert fake_app._mutex_handle is None, "YJ-2: _do_cleanup must clear app._mutex_handle after release() on POSIX"

    def test_posix_release_skipped_when_handle_none(self, controller, fake_app, monkeypatch):
        """When ``app._mutex_handle`` is None, the POSIX branch must NOT"""
        monkeypatch.setattr("sys.platform", "linux")
        fake_app._mutex_handle = None

        # Must not raise.
        controller._do_cleanup()
        assert fake_app._mutex_handle is None

    def test_posix_release_skipped_on_windows(self, controller, fake_app, monkeypatch):
        """On Windows, the POSIX branch must NOT execute, the Windows"""
        monkeypatch.setattr("sys.platform", "win32")

        handle = MagicMock(name="win32_mutex_handle")
        fake_app._mutex_handle = None

        controller._do_cleanup()
        handle.release.assert_not_called()

    def test_posix_release_swallows_release_exception(self, controller, fake_app, monkeypatch):
        """If ``handle.release()`` raises, ``_do_cleanup`` must NOT"""
        monkeypatch.setattr("sys.platform", "linux")

        handle = MagicMock(name="_PosixSingleInstanceHandle")
        handle.release.side_effect = OSError("fd already closed")
        fake_app._mutex_handle = handle

        # Must not raise.
        with contextlib.suppress(Exception):
            controller._do_cleanup()

        # The release() call was attempted (and failed), but cleanup
        handle.release.assert_called_once_with()

    def test_posix_release_uses_real_posix_handle(self, controller, fake_app, monkeypatch, tmp_path):
        """``voice_typer.server.single_instance``) is accepted by the POSIX"""
        from voice_typer.server.single_instance import _PosixSingleInstanceHandle

        monkeypatch.setattr("sys.platform", "linux")

        # Create a throwaway lockfile + open an fd for the handle.
        lock_path = tmp_path / "test_yj2.lock"
        fd = _open_lockfile_fd(lock_path)
        try:
            handle = _PosixSingleInstanceHandle(fd, lock_path)
            fake_app._mutex_handle = handle

            controller._do_cleanup()

            # ``release()`` was called (idempotent: closes fd + unlinks
            assert fake_app._mutex_handle is None
            # The lockfile should have been unlinked by release().
            assert not lock_path.exists(), "YJ-2: _PosixSingleInstanceHandle.release() must unlink the lockfile"
        finally:
            # Defense in depth: if release() didn't run, close the fd
            with contextlib.suppress(OSError):
                import os

                os.close(fd)


def _open_lockfile_fd(lock_path) -> int:
    """Open a fresh fd for ``lock_path`` (O_CREAT | O_EXCL | O_RDWR)."""
    import os

    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    return os.open(str(lock_path), flags, 0o600)


# Silence the noisy caplog warnings from the swallowed OSError test.
@pytest.fixture(autouse=True)
def _silence_cleanup_debug_logs(caplog):
    """Auto-fixture: keep caplog quiet so the test report isn't flooded"""
    caplog.set_level(logging.CRITICAL)
    yield
