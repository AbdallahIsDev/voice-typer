"""god-class decomposition: SettingsController, extracted from VoiceTyperApp.
The actual logic lived on ``VoiceTyperApp`` as four private methods
class boundary moved. ``VoiceTyperApp`` keeps thin delegate methods so
    - ``_open_config_file``: stays on ``VoiceTyperApp`` because
      ``inspect.getsource(VoiceTyperApp._open_config_file)`` to pin
    - microphone selection (update config + recreate ``Recorder``)
(``_toggle_autostart``, ``_set_autostart``, ``_set_notifications``,
``_select_microphone``). The behaviour is preserved verbatim, only the
the tray menu callbacks (and tests that call ``app._select_microphone``
      ``tests/test_config_editor_lock.py`` and
      ``tests/test_bugfix_regressions.py:943`` use
      source-level invariants (macOS ``open -W`` branch, three platform
      branches acquiring ``_config_mutation_lock``, etc.). Moving it
``startup_tasks.py``): tests like the ``app`` fixture in
``tests/test_app.py`` replace
``voice_typer.server.server_platform.is_autostart_enabled`` /
``enable_autostart`` / ``disable_autostart`` at call time. To keep
each method (deferred import from the canonical ``server_platform``
"""

from __future__ import annotations

import logging
from typing import Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME

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
            label = mic_name if mic_name else "System Default"

            if app.recorder.recording:
                log.info("[CONFIG] Microphone changed to %s; applying after active recording", label)
                app.tray.notify(APP_NAME, i18n.t("notify.settings_controller.mic_next_recording", label=label))
                return

            # Re-create with new mic. NOTE: this intentionally does NOT pass
            log.info("[CONFIG] Microphone changed to: %s", label)
            app.tray.notify(APP_NAME, i18n.t("notify.settings_controller.mic_changed", label=label))
