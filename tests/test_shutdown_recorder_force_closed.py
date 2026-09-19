"""SI-fix-6 regression tests: early publication of ``_recorder_force_closed``."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
import voice_typer.server.shutdown_controller
from voice_typer.server.shutdown_controller import ShutdownController


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``."""

    def __init__(self):
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
    """A ``_FakeApp`` with the shutdown environment stubbed out."""
    monkeypatch.setattr("voice_typer.server.backend_pid._clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False, raising=False)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``."""
    ctrl = ShutdownController(fake_app)
    ctrl._recorder_force_closed = False
    ctrl._recorder_teardown_done.clear()
    return ctrl


class TestForceClosedPublishedEarly:
    """transcription-thread join (i.e. before the end-of-function"""

    def test_force_closed_true_at_transcription_join_after_stop_timeout(self, controller, fake_app, monkeypatch):
        """When ``recorder.stop()`` times out, the flag must be"""
        fake_app.recorder.recording = True

        _sc = voice_typer.server.shutdown_controller
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

        flag_at_transcription_join: list = []

        def _capture_flag_then_block(timeout=3.0):
            flag_at_transcription_join.append(controller._recorder_force_closed)
            # Block briefly so the join's timeout path is exercised
            blocked.wait(timeout=2.0)

        transcription_thread = MagicMock()
        transcription_thread.is_alive.return_value = True
        transcription_thread.join.side_effect = _capture_flag_then_block
        fake_app.recording._transcription_thread = transcription_thread

        # Run _teardown_recorder directly (not via _do_cleanup) so we
        controller._teardown_recorder()
        blocked.set()

        assert flag_at_transcription_join == [True], (
            "SI-20: controller._recorder_force_closed must be True at the "
            "time the transcription-thread join runs (i.e. published "
            "immediately after the recorder.stop() timeout, NOT only at "
            f"end-of-function); got {flag_at_transcription_join}"
        )

    def test_force_closed_true_at_mic_watcher_skip_after_stop_timeout(self, controller, fake_app, monkeypatch):
        """When ``recorder.stop()`` times out, the flag must be True at"""
        fake_app.recorder.recording = True

        _sc = voice_typer.server.shutdown_controller
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

        # Capture the flag at the moment the 'skipping
        from voice_typer.server.shutdown.teardowns import recorder as _teardown_recorder_mod

        flag_at_warning: list = []
        original_warning = _teardown_recorder_mod.log.warning

        def _capture_warning(msg, *args, **kwargs):
            if isinstance(msg, str) and "skipping recorder.shutdown_mic_watcher" in msg:
                flag_at_warning.append(controller._recorder_force_closed)
            return original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(_teardown_recorder_mod.log, "warning", _capture_warning)

        controller._teardown_recorder()
        blocked.set()

        assert flag_at_warning == [True], (
            "SI-20: controller._recorder_force_closed must be True when "
            "the 'skipping recorder.shutdown_mic_watcher' warning fires "
            "(i.e. published before the mic-watcher skip path); got "
            f"{flag_at_warning}"
        )

    def test_force_closed_true_at_transcription_join_after_discard_timeout(self, controller, fake_app, monkeypatch):
        """Companion: the same early-publication contract must hold for"""
        fake_app.recorder.recording = True

        # Make recorder.stop() RAISE so the discard() fallback runs.
        fake_app.recorder.stop.side_effect = RuntimeError("PortAudio already closed")

        _sc = voice_typer.server.shutdown_controller
        original_run_with_timeout = _sc._run_with_timeout

        def _fast_run_with_timeout(description, func, timeout=5.0):
            if description == "recorder.discard":
                return original_run_with_timeout(description, func, timeout=0.1)
            return original_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_run_with_timeout)

        blocked = threading.Event()

        def _blocking_discard():
            blocked.wait(timeout=5.0)

        fake_app.recorder.discard = _blocking_discard

        flag_at_transcription_join: list = []

        def _capture_flag_then_block(timeout=3.0):
            flag_at_transcription_join.append(controller._recorder_force_closed)
            blocked.wait(timeout=2.0)

        transcription_thread = MagicMock()
        transcription_thread.is_alive.return_value = True
        transcription_thread.join.side_effect = _capture_flag_then_block
        fake_app.recording._transcription_thread = transcription_thread

        controller._teardown_recorder()
        blocked.set()

        assert flag_at_transcription_join == [True], (
            "SI-20: controller._recorder_force_closed must be True at the "
            "time the transcription-thread join runs after the "
            "recorder.discard() timeout; got "
            f"{flag_at_transcription_join}"
        )

    def test_force_closed_stays_false_when_stop_completes_normally(self, controller, fake_app):
        """Negative test: when ``recorder.stop()`` completes normally"""
        fake_app.recorder.recording = True

        flag_at_transcription_join: list = []

        def _capture_flag(timeout=3.0):
            flag_at_transcription_join.append(controller._recorder_force_closed)

        transcription_thread = MagicMock()
        transcription_thread.is_alive.return_value = True
        transcription_thread.join.side_effect = _capture_flag
        fake_app.recording._transcription_thread = transcription_thread

        controller._teardown_recorder()

        assert flag_at_transcription_join == [False], (
            "SI-20: controller._recorder_force_closed must remain False "
            "when recorder.stop() completes normally; got "
            f"{flag_at_transcription_join}"
        )
        # And remain False at end-of-function.
        assert controller._recorder_force_closed is False

    def test_recorder_teardown_done_event_set_at_end(self, controller, fake_app):
        """Sanity: the done-Event must still be set at end-of-function"""
        fake_app.recorder.recording = True

        assert not controller._recorder_teardown_done.is_set()
        controller._teardown_recorder()
        assert controller._recorder_teardown_done.is_set()
