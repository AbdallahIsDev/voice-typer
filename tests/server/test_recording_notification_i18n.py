"""Server-side localization pins for the ``recording_controller``"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from voice_typer.server.i18n import _INITIAL_LABELS
from voice_typer.server.recording_controller import RecordingController
from voice_typer.server.recording_lifecycle import RecordingLifecycle


class TestKeysRegisteredInServerRegistry:
    """Every notification key used by the recording surface must exist"""

    @pytest.mark.parametrize(
        "key",
        [
            "notify.recording_controller.xrun_title",
            "notify.recording_controller.xrun_body",
            "notify.recording_controller.mic_unplugged",
            "notify.recording_controller.still_running",
            "notify.recording_controller.start_failed_with_reason",
        ],
    )
    def test_key_registered(self, key: str) -> None:
        assert key in _INITIAL_LABELS, f"{key} must be registered in the server i18n registry"

    def test_xrun_placeholders_format(self) -> None:
        """The xrun templates format with ``app`` + ``count`` without"""
        from voice_typer.server import i18n
        from voice_typer.server.branding import APP_NAME

        title = i18n.t("notify.recording_controller.xrun_title", app=APP_NAME)
        body = i18n.t("notify.recording_controller.xrun_body", count=7)
        assert APP_NAME in title and "7" in body
        assert "{app}" not in title and "{count}" not in body

    def test_start_failed_with_reason_formats_reason(self) -> None:
        from voice_typer.server import i18n

        text = i18n.t("notify.recording_controller.start_failed_with_reason", reason="microphone busy")
        assert "microphone busy" in text
        assert "{reason}" not in text


class _RecordingControllerAppStub:
    """Minimal app stub for the controller's notification callbacks."""

    def __init__(self) -> None:
        import threading

        from voice_typer.server.tray_types import AppState

        self.config = MagicMock()
        self.config.show_notifications = True
        self.tray = MagicMock()
        self._schedule_timer_calls: list[tuple[float, object]] = []
        self._cycle_id = "#1"
        self._AppState = AppState
        # Inverted busy semantics (is_set() == not busy), the watchdog's
        self._busy_event = threading.Event()
        self._busy_event.set()

    def _schedule_timer(self, delay: float, fn) -> None:  # noqa: ANN001
        self._schedule_timer_calls.append((delay, fn))

    def _stop_dictation(self) -> None:
        pass


class TestOnXrunThresholdUsesI18n:
    """``on_xrun_threshold`` previously hardcoded the English title/body"""

    def test_notification_uses_i18n_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voice_typer.server import recording_controller as rc_mod

        calls: list[tuple[str, dict]] = []
        real_t = rc_mod.i18n.t

        def _spy(key: str, **fmt) -> str:
            calls.append((key, fmt))
            return real_t(key, **fmt)

        monkeypatch.setattr(rc_mod.i18n, "t", _spy)

        app = _RecordingControllerAppStub()
        controller = RecordingController.__new__(RecordingController)
        controller._app = app
        controller.on_xrun_threshold(3)

        keys_used = [k for k, _ in calls]
        assert "notify.recording_controller.xrun_title" in keys_used
        assert "notify.recording_controller.xrun_body" in keys_used
        app.tray.notify.assert_called_once()
        title, body = app.tray.notify.call_args[0]
        assert "3" in body, "the xrun body must carry the {count} value"


class TestMicUnpluggedUsesI18n:
    """``MicLifecycleHooks.on_active_mic_lost`` carried a hardcoded"""

    def test_notification_uses_mic_unplugged_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voice_typer.server import mic_lifecycle_hooks as hooks_mod

        calls: list[str] = []
        real_t = hooks_mod.i18n.t

        def _spy(key: str, **fmt) -> str:
            calls.append(key)
            return real_t(key, **fmt)

        monkeypatch.setattr(hooks_mod.i18n, "t", _spy)

        app = _RecordingControllerAppStub()
        controller = RecordingController.__new__(RecordingController)
        controller._app = app
        controller._mic_hooks.on_active_mic_lost(controller)

        assert "notify.recording_controller.mic_unplugged" in calls


class TestWatchdogStillRunningUsesI18n:
    """The watchdog's second-firing \"still running\" notification carried"""

    def test_notification_uses_still_running_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voice_typer.server import transcription_watchdog as wd_mod

        calls: list[str] = []
        real_t = wd_mod.i18n.t

        def _spy(key: str, **fmt) -> str:
            calls.append(key)
            return real_t(key, **fmt)

        monkeypatch.setattr(wd_mod.i18n, "t", _spy)

        app = _RecordingControllerAppStub()
        controller = RecordingController.__new__(RecordingController)
        controller._app = app
        controller._watchdog_lock = __import__("threading").Lock()
        # The "still running" branch requires a LIVE transcription worker:
        worker_release = __import__("threading").Event()
        controller._transcription_thread = __import__("threading").Thread(
            target=worker_release.wait, name="stuck-worker", daemon=True
        )
        controller._transcription_thread.start()
        controller._watchdog_firings = 2
        controller._watchdog_max_firings = 3
        controller._watchdog_event = __import__("threading").Event()
        controller._watchdog_stop_event = __import__("threading").Event()
        controller._watchdog_thread = None
        controller._cancelled_cycle_ids_lock = __import__("threading").Lock()
        controller._cancelled_cycle_ids = {}
        # Busy semantics are inverted: clear = busy. The watchdog's
        app._busy_event.clear()
        try:
            controller._watchdog_helper.force_recover(controller, force=False)
        finally:
            worker_release.set()
            controller._transcription_thread.join(timeout=2)

        assert "notify.recording_controller.still_running" in calls


class TestStartFailureReasonUsesI18n:
    """Both start-failure branches (``_start_impl`` and the start"""

    def test_start_impl_uses_key(self) -> None:
        src = inspect.getsource(RecordingLifecycle._publish_start_failure_notification)
        assert "notify.recording_controller.start_failed_with_reason" in src
        caller = inspect.getsource(RecordingLifecycle._start_impl)
        assert "_publish_start_failure_notification" in caller

    def test_start_worker_uses_key(self) -> None:
        src = inspect.getsource(RecordingLifecycle._publish_start_failure_notification)
        assert "notify.recording_controller.start_failed_with_reason" in src
        caller = inspect.getsource(RecordingLifecycle._start_dictation_worker_entry)
        assert "_publish_start_failure_notification" in caller


class TestPublicForceRecoverWrapper:
    """``RecordingController.force_recover`` is the sanctioned public"""

    def test_public_wrapper_delegates_with_force_kwarg(self) -> None:
        controller = RecordingController.__new__(RecordingController)
        delegated: list[tuple[object, bool]] = []

        class _SpyWatchdog:
            def force_recover(self, ctrl, *, force: bool = False) -> None:
                delegated.append((ctrl, force))

        object.__setattr__(controller, "_watchdog_helper", _SpyWatchdog())

        controller.force_recover(force=True)
        controller.force_recover()

        assert delegated == [(controller, True), (controller, False)], (
            "the public wrapper must forward the force flag positionally-clean as a keyword to the watchdog helper"
        )

    def test_service_layer_calls_public_method(self) -> None:
        from voice_typer.server.service import dictation as dictation_mod

        src = inspect.getsource(dictation_mod)
        assert "recording.force_recover(force=True)" in src, (
            "the service layer must use the PUBLIC RecordingController.force_recover"
        )
        assert "recording._force_recover_from_stuck_transcription" not in src, (
            "the service layer must not reach into the private controller method"
        )
