"""Focused tests for the ``AppConstruction`` mixin
(``voice_typer/server/app_construction.py``) — the eager
subsystem-builder slice extracted from ``VoiceTyperApp``.

Covers the mixin's public API on a minimal host class (external
dependencies stubbed at their OWNING submodule seams — the canonical
C-ARCH-2 patch targets for the new module), mirroring how
``tests/app/test_recording_init.py`` exercises the
``AppRecordingInit`` mixin surface:

- ``_register_startup_i18n_fallbacks`` registers the three English
  startup keys (idempotently) — the module-level helper re-exported
  from ``voice_typer.server.app``.
- ``_init_config`` happy path (Config.load() result stored, failure
  flag False) and corrupt-file path (load raises → fallback defaults,
  failure flag True, best-effort rename attempt against the tmp config
  dir).
- ``_init_threading_and_crash`` installs both excepthooks through the
  crash-handler module object, and an install failure is best-effort
  (logged at DEBUG on the shared app logger, construction continues).
- ``_log_startup_banner`` emits the ``APP_NAME starting -- model=...``
  line at the ``voice_typer.server.app`` logger (the sibling-module
  convention), honours the no-model honest "none" description, and
  ends with the ``[STARTUP]`` banner call.
- ``_init_audio`` / ``_init_models`` / ``_init_tray`` /
  ``_init_controllers`` / ``_init_history_crash_volume`` /
  ``_init_misc_backings`` construct their subsystem through the
  deferred seams and declare their lazy backings.
"""

from __future__ import annotations

import logging
import types
from unittest.mock import MagicMock

from voice_typer.server import app_construction
from voice_typer.server.app_construction import AppConstruction

_STARTUP_KEYS = {
    "error.config_load_failed.title": "Config load failed",
    "error.config_load_failed.body": "Settings were reset to defaults. Check the logs for details.",
    "state.app.starting": "Starting...",
}


class _Host(AppConstruction):
    """Minimal host exercising the mixin without VoiceTyperApp."""

    def __init__(self) -> None:
        self.config = MagicMock()
        self._thread_registry = MagicMock()


class _FakeController:
    """Stand-in for the controller classes constructed by the builders.

    Records positional + keyword construction args so the tests can
    assert the app passes itself / its config / its registry through.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


class TestRegisterStartupI18nFallbacks:
    def test_registers_missing_english_fallbacks(self):
        from voice_typer.server import i18n

        removed: dict[str, str] = {}
        with i18n._LOCK:
            en = i18n._REGISTRY.setdefault("en", {})
            for key in _STARTUP_KEYS:
                value = en.pop(key, None)
                if value is not None:
                    removed[key] = value
        try:
            app_construction._register_startup_i18n_fallbacks()
            with i18n._LOCK:
                en = i18n._REGISTRY.setdefault("en", {})
                for key, expected in _STARTUP_KEYS.items():
                    assert en.get(key) == expected, f"{key} must be registered"
        finally:
            with i18n._LOCK:
                en = i18n._REGISTRY.setdefault("en", {})
                for key, value in removed.items():
                    en.setdefault(key, value)

    def test_idempotent_setdefault(self):
        from voice_typer.server import i18n

        sentinel_body = "custom body wins (setdefault contract)"
        with i18n._LOCK:
            i18n._REGISTRY.setdefault("en", {})["error.config_load_failed.body"] = sentinel_body
        try:
            app_construction._register_startup_i18n_fallbacks()
            with i18n._LOCK:
                assert i18n._REGISTRY["en"]["error.config_load_failed.body"] == sentinel_body
        finally:
            with i18n._LOCK:
                i18n._REGISTRY["en"].pop("error.config_load_failed.body", None)


class TestInitConfig:
    def test_happy_path_stores_loaded_config(self, monkeypatch):
        loaded = MagicMock(name="loaded_config")
        fallback = MagicMock(name="fallback_config")

        class _FakeConfig:
            calls = 0

            @staticmethod
            def load():
                return loaded

            def __new__(cls):
                return fallback

        monkeypatch.setattr(app_construction, "Config", _FakeConfig)
        host = _Host()

        host._init_config()

        assert host.config is loaded
        assert host._config_load_failed is False

    def test_load_failure_falls_back_and_flags(self, monkeypatch, tmp_config_dir):
        """Config.load() raising falls back to defaults, flags the
        failure for the tray toast, and attempts the best-effort
        corrupt-file rename against the (tmp) canonical config dir."""
        fallback = MagicMock(name="fallback_config")

        class _FakeConfig:
            @staticmethod
            def load():
                raise KeyError("corrupt schema")

            def __new__(cls):
                return fallback

        monkeypatch.setattr(app_construction, "Config", _FakeConfig)
        host = _Host()

        host._init_config()

        assert host.config is fallback
        assert host._config_load_failed is True

    def test_load_failure_logs_at_app_logger(self, monkeypatch, tmp_config_dir, caplog):
        class _FakeConfig:
            @staticmethod
            def load():
                raise ValueError("boom")

        monkeypatch.setattr(app_construction, "Config", _FakeConfig)
        host = _Host()

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.app"):
            host._init_config()

        assert any(
            "[INIT] Config.load() raised" in record.message
            for record in caplog.records
            if record.name == "voice_typer.server.app"
        )


class TestInitThreadingAndCrash:
    def test_installs_both_excepthooks(self, monkeypatch):
        installed: list[str] = []
        fake_crash_handler = types.SimpleNamespace(
            install_python_excepthook=lambda: installed.append("python"),
            install_threading_excepthook=lambda: installed.append("threading"),
        )
        monkeypatch.setattr(app_construction, "_crash_handler", fake_crash_handler)
        monkeypatch.setattr(app_construction, "ThreadRegistry", object)
        host = _Host()

        host._init_threading_and_crash()

        assert installed == ["python", "threading"]

    def test_install_failure_is_best_effort(self, monkeypatch, caplog):
        def _boom():
            raise RuntimeError("restricted interpreter")

        fake_crash_handler = types.SimpleNamespace(
            install_python_excepthook=_boom,
            install_threading_excepthook=_boom,
        )
        monkeypatch.setattr(app_construction, "_crash_handler", fake_crash_handler)
        monkeypatch.setattr(app_construction, "ThreadRegistry", object)
        host = _Host()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            host._init_threading_and_crash()  # must not raise

        assert any(
            "[INIT] excepthook install failed" in record.message
            for record in caplog.records
            if record.name == "voice_typer.server.app"
        )


class TestLogStartupBanner:
    def test_banner_line_and_startup_banner_call(self, monkeypatch, caplog):
        from voice_typer.server.branding import APP_NAME

        host = _Host()
        host.config.model_size = "small"
        host.config.hotkey = "ctrl+shift+d"
        host.config.microphone = None
        host.config.sample_rate = 16000
        banners: list[int] = []
        monkeypatch.setattr("voice_typer.server.tray_models.is_active_model_downloaded", lambda config: True)
        monkeypatch.setattr("voice_typer.server.startup_timeline.log_launch_timeline", lambda log: None)
        monkeypatch.setattr(app_construction, "_emit_startup_banner", lambda: banners.append(1))

        with caplog.at_level(logging.INFO, logger="voice_typer.server.app"):
            host._log_startup_banner()

        assert any(
            record.message.startswith(f"{APP_NAME} starting -- model=")
            and "hotkey=ctrl+shift+d" in record.message
            and "mic=default" in record.message
            and "sample_rate=16000" in record.message
            for record in caplog.records
            if record.name == "voice_typer.server.app"
        )
        assert banners == [1]

    def test_no_model_selection_reports_none_honestly(self, monkeypatch, caplog):
        from voice_typer.server.model_registry import NO_MODEL_SIZE

        host = _Host()
        host.config.model_size = NO_MODEL_SIZE
        host.config.hotkey = "ctrl+shift+d"
        host.config.microphone = "Some Mic"
        host.config.sample_rate = 48000
        monkeypatch.setattr("voice_typer.server.tray_models.is_active_model_downloaded", lambda config: True)
        monkeypatch.setattr("voice_typer.server.startup_timeline.log_launch_timeline", lambda log: None)
        monkeypatch.setattr(app_construction, "_emit_startup_banner", lambda: None)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.app"):
            host._log_startup_banner()

        assert any("model=none" in record.message for record in caplog.records)

    def test_stale_model_suffixes_not_installed(self, monkeypatch, caplog):
        host = _Host()
        host.config.model_size = "large"
        host.config.hotkey = "ctrl+shift+d"
        host.config.microphone = None
        host.config.sample_rate = 16000
        monkeypatch.setattr("voice_typer.server.tray_models.is_active_model_downloaded", lambda config: False)
        monkeypatch.setattr("voice_typer.server.startup_timeline.log_launch_timeline", lambda log: None)
        monkeypatch.setattr(app_construction, "_emit_startup_banner", lambda: None)

        with caplog.at_level(logging.INFO, logger="voice_typer.server.app"):
            host._log_startup_banner()

        assert any("model=large (not installed)" in record.message for record in caplog.records)


class TestInitAudio:
    def test_declares_lazy_backing_and_quality_analyzer(self, monkeypatch):
        resets: list[int] = []

        class _FakeAnalyzer:
            def reset(self):
                resets.append(1)

        monkeypatch.setattr(app_construction, "AudioQualityAnalyzer", _FakeAnalyzer)
        host = _Host()

        host._init_audio()

        assert host._audio_processor_backing is None
        assert isinstance(host._audio_quality, _FakeAnalyzer)
        assert resets == [1]


class TestInitModels:
    def test_constructs_model_manager_with_self(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.model_manager.ModelManager", _FakeController)
        host = _Host()

        host._init_models()

        assert isinstance(host.models, _FakeController)
        assert host.models.args == (host,)


class TestInitTray:
    def test_constructs_tray_with_controller_and_config(self, monkeypatch):
        monkeypatch.setattr(app_construction, "TrayIcon", _FakeController)
        host = _Host()
        host._config_load_failed = False

        host._init_tray()

        assert isinstance(host.tray, _FakeController)
        assert host.tray.kwargs["controller"] is host
        assert host.tray.kwargs["config"] is host.config
        assert host._clipboard_backing is None

    def test_config_load_failure_surfaces_toast(self, monkeypatch):
        notified: list[tuple[str, str]] = []

        class _FakeTray:
            def __init__(self, controller, config):
                self.controller = controller
                self.config = config

            def notify(self, title, body):
                notified.append((title, body))

        monkeypatch.setattr(app_construction, "TrayIcon", _FakeTray)
        host = _Host()
        host._config_load_failed = True

        host._init_tray()

        assert len(notified) == 1
        title, body = notified[0]
        assert isinstance(title, str) and title
        assert isinstance(body, str) and body

    def test_failing_notify_does_not_crash_init(self, monkeypatch):
        class _FakeTray:
            def __init__(self, controller, config):
                pass

            def notify(self, title, body):
                raise RuntimeError("notification daemon not ready")

        monkeypatch.setattr(app_construction, "TrayIcon", _FakeTray)
        host = _Host()
        host._config_load_failed = True

        host._init_tray()  # must not raise

        assert isinstance(host.tray, _FakeTray)


class TestInitControllers:
    def test_wires_controllers_and_declares_lazy_backings(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.settings_controller.SettingsController", _FakeController)
        monkeypatch.setattr("voice_typer.server.shutdown_controller.ShutdownController", _FakeController)
        monkeypatch.setattr("voice_typer.server.shutdown_controller.SHUTDOWN_WATCHDOG_TIMEOUT_S", 42.0)
        monkeypatch.setattr("voice_typer.server.app_lifecycle.LifecycleController", _FakeController)
        monkeypatch.setattr(
            "voice_typer.server.controllers.config_editor_launcher.ConfigEditorLauncher", _FakeController
        )
        host = _Host()

        host._init_controllers()

        assert isinstance(host.settings, _FakeController) and host.settings.args == (host,)
        assert isinstance(host.shutdown, _FakeController) and host.shutdown.args == (host,)
        assert isinstance(host.lifecycle, _FakeController) and host.lifecycle.args == (host,)
        assert isinstance(host._config_editor_launcher, _FakeController)
        assert host._config_editor_launcher.args == (host,)
        assert host._shutdown_watchdog_timeout_s == 42.0
        # lazy-controller backing declarations
        assert host._undo_backing is None
        assert host._undo_failed_at is None
        assert host._audio_quality_backing is None
        assert host._audio_quality_failed_at is None


class TestInitHistoryCrashVolume:
    def test_wires_crash_recovery_and_volume_controller(self, monkeypatch):
        monkeypatch.setattr(app_construction, "CrashRecovery", _FakeController)
        monkeypatch.setattr("voice_typer.server.volume_controller.VolumeController", _FakeController)
        host = _Host()

        host._init_history_crash_volume()

        assert isinstance(host._crash_recovery, _FakeController)
        assert host._crash_recovery.kwargs["thread_registry"] is host._thread_registry
        assert isinstance(host.volume, _FakeController)
        assert host.volume.args == (host,)
        # lazy backings + failure timestamps
        assert host._history_db_backing is None
        assert host._history_db_failed_at is None
        assert host._duck_crash_recovery_backing is None
        assert host._duck_crash_recovery_failed_at is None
        assert host._volume_ducker_backing is None
        assert host._volume_ducker_failed_at is None


class TestInitMiscBackings:
    def test_declares_remaining_lazy_backings(self):
        host = _Host()

        host._init_misc_backings()

        assert host._waveform_bubble_backing is None
        assert host._waveform_wiring_backing is None
        assert host._last_transcription == ""
        assert host._ipc_server is None
        assert host._template_manager_backing is None
        assert host._vocabulary_manager_backing is None
        assert host._llm_polisher is None
        assert host._cloud_engine is None
