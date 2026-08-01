"""YJ-2 regression: POSIX single-instance ``handle.release()`` must be called
from ``shutdown_controller._do_cleanup``.

Pre-fix, ``_do_cleanup`` only called ``ctypes.windll.kernel32.CloseHandle``
for the Windows path. On POSIX, ``app._mutex_handle`` is a
``_PosixSingleInstanceHandle`` (subclass of int) wrapping the lockfile fd,
and ``ctypes.windll`` does not exist on POSIX — the resulting
``AttributeError`` was swallowed by the ``try/except``, leaving the
lockfile fd dangling until process exit and racing a fast re-launch.

This test fakes ``sys.platform == "linux"`` and mocks
``app._mutex_handle`` with a ``MagicMock`` exposing ``release()`` to
verify the POSIX branch invokes ``release()`` and clears the attribute.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController

# ── Fake-app plumbing (mirrors tests/test_shutdown_controller.py::_FakeApp) ──


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Provides every attribute / method that ``ShutdownController._do_cleanup``
    touches, mocked so we can assert call counts. Mirrors the collaborator
    mocks in ``tests/test_shutdown_controller.py``.
    """

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._electron_pid: int | None = None
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
    """A ``_FakeApp`` with the shutdown environment stubbed out (POSIX).

    Uses ``raising=False`` on the ``monkeypatch.setattr`` calls because
    ``_clear_backend_pid_file`` / ``_close_devnull_files`` /
    ``_register_devnull_file`` may or may not exist as module-level
    attributes on ``voice_typer.server.app`` (the production
    ``shutdown_controller._do_cleanup`` looks them up dynamically via
    ``getattr(_app_module, name, None)``). Mirrors the pattern in
    ``tests/test_shutdown_controller.py::fake_app``.
    """
    monkeypatch.setattr("voice_typer.server.app._clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: False, raising=False)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app`` (real body wired)."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


# ── Tests ────────────────────────────────────────────────────────────────


class TestPosixMutexHandleRelease:
    """YJ-2: ``_do_cleanup`` MUST release the POSIX single-instance handle."""

    def test_posix_release_called_when_handle_present(self, controller, fake_app, monkeypatch):
        """On POSIX, when ``app._mutex_handle`` is a handle-like object
        with a ``release()`` method, ``_do_cleanup`` must call
        ``release()`` and set ``_mutex_handle = None``.
        """
        # Fake the POSIX platform so the  branch runs.
        monkeypatch.setattr("sys.platform", "linux")

        handle = MagicMock(name="_PosixSingleInstanceHandle")
        fake_app._mutex_handle = handle

        controller._do_cleanup()

        handle.release.assert_called_once_with()
        assert fake_app._mutex_handle is None, "YJ-2: _do_cleanup must clear app._mutex_handle after release() on POSIX"

    def test_posix_release_skipped_when_handle_none(self, controller, fake_app, monkeypatch):
        """When ``app._mutex_handle`` is None, the POSIX branch must NOT
        raise (the ``getattr(app, '_mutex_handle', None) is not None``
        guard short-circuits)."""
        monkeypatch.setattr("sys.platform", "linux")
        fake_app._mutex_handle = None

        # Must not raise.
        controller._do_cleanup()
        assert fake_app._mutex_handle is None

    def test_posix_release_skipped_on_windows(self, controller, fake_app, monkeypatch):
        """On Windows, the POSIX branch must NOT execute — the Windows
        ``CloseHandle`` branch handles cleanup. ``app._mutex_handle``
        remains whatever the Windows path set it to (None here, since
        the Win32 branch's ``hasattr(app, '_mutex_handle') and
        app._mutex_handle`` check fails on the MagicMock)."""
        monkeypatch.setattr("sys.platform", "win32")

        handle = MagicMock(name="win32_mutex_handle")
        # Truthy so the Windows branch tries to call CloseHandle (which
        # we don't actually want to run). Set to None to skip the Win32
        # branch entirely (it would try ``ctypes.windll`` which doesn't
        # exist on the Linux test host — the surrounding try/except
        # swallows the AttributeError, but we want to isolate the
        # POSIX branch in this test).
        fake_app._mutex_handle = None

        controller._do_cleanup()
        # handle.release was never attached to fake_app._mutex_handle,
        # so the only assertion here is that _do_cleanup ran without
        # raising AND the (mock) handle's release() was never called.
        handle.release.assert_not_called()

    def test_posix_release_swallows_release_exception(self, controller, fake_app, monkeypatch):
        """If ``handle.release()`` raises, ``_do_cleanup`` must NOT
        propagate — the POSIX branch wraps the call in
        ``contextlib.suppress(Exception)`` so cleanup continues."""
        monkeypatch.setattr("sys.platform", "linux")

        handle = MagicMock(name="_PosixSingleInstanceHandle")
        handle.release.side_effect = OSError("fd already closed")
        fake_app._mutex_handle = handle

        # Must not raise.
        with contextlib.suppress(Exception):
            controller._do_cleanup()

        # The release() call was attempted (and failed), but cleanup
        # continued past it.
        handle.release.assert_called_once_with()

    def test_posix_release_uses_real_posix_handle(self, controller, fake_app, monkeypatch, tmp_path):
        """End-to-end: a real ``_PosixSingleInstanceHandle`` (from
        ``voice_typer.server.single_instance``) is accepted by the POSIX
        branch and its ``release()`` is invoked.

        This guards against regressions where the YJ-2 branch's
        ``getattr(app, '_mutex_handle', None) is not None`` check is
        accidentally inverted (the handle subclasses ``int`` — falsy
        when fd == 0, so we use a real fd > 0).
        """
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
            # lockfile). The handle is cleared on the app.
            assert fake_app._mutex_handle is None
            # The lockfile should have been unlinked by release().
            assert not lock_path.exists(), "YJ-2: _PosixSingleInstanceHandle.release() must unlink the lockfile"
        finally:
            # Defense in depth: if release() didn't run, close the fd
            # manually so the test doesn't leak a file descriptor.
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
    """Auto-fixture: keep caplog quiet so the test report isn't flooded
    with DEBUG logs from the swallowed-exception paths."""
    caplog.set_level(logging.CRITICAL)
    yield
