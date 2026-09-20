"""Focused behavior tests for the AppAdmin tray-protocol mixin."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from voice_typer.server.app_admin import AppAdmin


class _Harness(AppAdmin):
    def __init__(self) -> None:
        self.settings = MagicMock()
        self.config = SimpleNamespace(microphone=None)
        self.models = MagicMock()
        self.lifecycle = MagicMock()
        self._shutting_down_event = threading.Event()
        self._config_mutation_lock = threading.Lock()
        self._config_editor_launcher = MagicMock()


class TestSettingsDelegation:
    def test_toggle_autostart(self) -> None:
        app = _Harness()
        app._toggle_autostart()
        app.settings.toggle_autostart.assert_called_once_with()

    def test_set_autostart(self) -> None:
        app = _Harness()
        app._set_autostart(True)
        app.settings.set_autostart.assert_called_once_with(True)

    def test_set_notifications(self) -> None:
        app = _Harness()
        app._set_notifications(False)
        app.settings.set_notifications.assert_called_once_with(False)

    def test_select_and_change_microphone(self) -> None:
        app = _Harness()
        app._select_microphone("mic-1")
        app.settings.select_microphone.assert_called_once_with("mic-1")
        app.change_microphone(None)
        app.settings.select_microphone.assert_called_with(None)


class TestActiveMicrophoneId:
    def test_returns_configured_id(self) -> None:
        app = _Harness()
        app.config.microphone = "wasapi|mic#1"
        assert app.active_microphone_id == "wasapi|mic#1"

    def test_none_means_system_default(self) -> None:
        assert _Harness().active_microphone_id is None

    def test_empty_string_means_system_default(self) -> None:
        app = _Harness()
        app.config.microphone = ""
        assert app.active_microphone_id is None

    def test_missing_attr_means_system_default(self) -> None:
        app = _Harness()
        app.config = SimpleNamespace()
        assert app.active_microphone_id is None


class TestConfigEditor:
    def test_open_holds_mutation_lock(self) -> None:
        app = _Harness()
        assert app._config_mutation_lock.acquire(blocking=False)
        app._config_mutation_lock.release()
        app._open_config_file()
        app._config_editor_launcher.open.assert_called_once_with()


class TestModelsAndLifecycle:
    def test_change_model(self) -> None:
        app = _Harness()
        app.change_model("tiny")
        app.models.change_model.assert_called_once_with("tiny")

    def test_quit_delegates(self) -> None:
        app = _Harness()
        app.lifecycle.quit_app.return_value = "bye"
        assert app.quit_app() == "bye"

    def test_restart_delegates_when_alive(self) -> None:
        app = _Harness()
        app.lifecycle.restart_app.return_value = "restarting"
        assert app.restart_app() == "restarting"
        app.lifecycle.restart_app.assert_called_once_with()

    def test_restart_ignored_while_shutting_down(self) -> None:
        app = _Harness()
        app._shutting_down_event.set()
        assert app.restart_app() is None
        app.lifecycle.restart_app.assert_not_called()

    def test_wait_for_relaunch_ack_returns_lifecycle_value(self) -> None:
        app = _Harness()
        app.lifecycle._wait_for_relaunch_ack.return_value = True
        assert app._wait_for_relaunch_ack(2.5) is True
        app.lifecycle._wait_for_relaunch_ack.assert_called_once_with(2.5)


class TestRefreshMicrophones:
    def test_refresh_bypasses_cache_and_reloads(self) -> None:
        app = _Harness()
        with (
            patch("voice_typer.server.server_platform.invalidate_microphone_list_cache") as invalidate,
            patch("voice_typer.server.startup_tasks.load_microphones") as load,
        ):
            app.refresh_microphones()
        invalidate.assert_called_once_with()
        load.assert_called_once_with(app)

    def test_refresh_failure_is_swallowed(self) -> None:
        app = _Harness()
        with (
            patch(
                "voice_typer.server.server_platform.invalidate_microphone_list_cache",
                side_effect=RuntimeError("boom"),
            ),
            patch("voice_typer.server.startup_tasks.load_microphones"),
        ):
            app.refresh_microphones()
