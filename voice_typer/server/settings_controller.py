"""Settings mutations extracted from ``VoiceTyperApp`` (god-class split).

Owns the four settings actions (autostart toggle/set, notifications,
microphone selection) with behavior preserved verbatim; ``AppAdmin``
keeps thin ``_toggle_autostart`` / ``_set_autostart`` /
``_set_notifications`` / ``_select_microphone`` delegates so tray
callbacks keep working. ``_open_config_file`` stays app-side (it now
delegates to ``ConfigEditorLauncher``).

Platform helpers are imported at call time from the canonical
``server_platform`` modules so tests can patch those attributes.
"""

from __future__ import annotations

import logging
from typing import Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.recording import Recorder

log = logging.getLogger(__name__)


class SettingsController:
    """Phase 6: extracted from ``VoiceTyperApp``. The app passes itself"""

    def __init__(self, app: Any) -> None:
        self._app = app

    def toggle_autostart(self) -> None:
        """Toggle autostart on/off from the tray menu.

        Delegates to :meth:`set_autostart` (P2 dedup).
        """
        # Import the facade module at call time so tests that
        from voice_typer.server.server_platform import autostart as _autostart

        self.set_autostart(not _autostart.is_autostart_enabled())

    def set_autostart(self, enabled: bool) -> None:
        """Set autostart from the advanced settings window or tray toggle."""
        # Import the facade module at call time so tests that
        from voice_typer.server.server_platform import autostart as _autostart

        app = self._app
        with app._config_mutation_lock:
            try:
                if enabled:
                    _autostart.enable_autostart()
                else:
                    _autostart.disable_autostart()
                app.config.autostart = enabled
                if not app.config.save():
                    log.warning("[CONFIG] Failed to save autostart setting to disk")
                app.tray.set_autostart_enabled(enabled)
                log.info("[CONFIG] Autostart set to %s", enabled)
            except Exception as e:
                log.exception("[CONFIG] Failed to set autostart")
                app.tray.notify(APP_NAME, i18n.t("notify.settings_controller.autostart_failed", error=str(e)))

    def set_notifications(self, enabled: bool) -> None:
        """Set notification behavior from the settings window."""
        app = self._app
        with app._config_mutation_lock:
            app.config.show_notifications = enabled
            if not app.config.save():
                log.warning("[CONFIG] Failed to save notifications setting to disk")
            app.tray.set_notifications_enabled(enabled)
            log.info("[CONFIG] Notifications set to %s", enabled)

    def select_microphone(self, mic_name: str | None) -> None:
        """Handle microphone selection from tray menu."""
        app = self._app
        with app._config_mutation_lock:
            app.config.microphone = mic_name
            if not app.config.save():
                log.warning("[CONFIG] Failed to save microphone selection to disk")
                app.tray.notify(
                    APP_NAME,
                    i18n.t("notify.settings_controller.mic_save_failed"),
                )
            label = mic_name if mic_name else i18n.t("notify.settings_controller.system_default_device")

            try:
                active_recorder = app.recorder
            except Exception as exc:
                log.warning(
                    "[CONFIG] Prior recorder build failed (%s); continuing microphone change",
                    type(exc).__name__,
                )
                active_recorder = None

            if active_recorder is not None and active_recorder.recording:
                log.info("[CONFIG] Microphone changed to %s; applying after active recording", label)
                app.tray.notify(APP_NAME, i18n.t("notify.settings_controller.mic_next_recording", label=label))
                return

            # Re-create with new mic. NOTE: this intentionally does NOT pass
            # thread_registry (mirrors pre-refactor VoiceTyperApp._select_microphone).
            try:
                app.recorder = Recorder(app.config, audio_processor=app._audio_processor)
            except Exception:
                log.exception("[CONFIG] Failed to recreate recorder for microphone %s", label)
            log.info("[CONFIG] Microphone changed to: %s", label)
            app.tray.notify(APP_NAME, i18n.t("notify.settings_controller.mic_changed", label=label))
