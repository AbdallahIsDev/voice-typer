"""Admin/diagnostics helpers mixin for VoiceTyperApp. Logger: voice_typer.server.app."""

from __future__ import annotations

import logging

# module docstring.
log = logging.getLogger("voice_typer.server.app")


class AppAdmin:
    """Declares NO ``__init__``: construction order and the backing
    attributes stay entirely in ``app.py``; only the accessors live
    """

    def _toggle_autostart(self):
        """Toggle autostart on/off from the tray menu. Delegates to SettingsController."""
        self.settings.toggle_autostart()

    def _set_autostart(self, enabled: bool):
        """body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_autostart`.
        """
        self.settings.set_autostart(enabled)

    def _set_notifications(self, enabled: bool):
        """body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_notifications`.
        """
        self.settings.set_notifications(enabled)

    def _select_microphone(self, mic_name: str | None):
        """body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.select_microphone`.
        """
        self.settings.select_microphone(mic_name)

    def _open_config_file(self):
        """body extracted to
        :class:`voice_typer.server.controllers.config_editor_launcher.ConfigEditorLauncher`.
        """
        with self._config_mutation_lock:
            self._config_editor_launcher.open()

    def change_microphone(self, mic_id: str | None) -> None:
        """TrayController protocol: select microphone."""
        self._select_microphone(mic_id)

    @property
    def active_microphone_id(self) -> str | None:
        """TrayController protocol. Return the currently selected
        microphone ID from ``config.microphone`` (None = system default)."""
        mic = getattr(self.config, "microphone", None)
        return str(mic) if mic else None

    def refresh_microphones(self) -> None:
        """TrayController protocol, re-enumerate microphones
        and refresh the tray menu by delegating to startup_tasks."""
        from voice_typer.server import startup_tasks
        from voice_typer.server.server_platform import invalidate_microphone_list_cache

        try:
            # An explicit user refresh must bypass the 5 s TTL cache —
            invalidate_microphone_list_cache()
            startup_tasks.load_microphones(self)
        except Exception:
            log.warning("[TRAY] refresh_microphones failed", exc_info=True)

    def change_model(self, model_size: str) -> None:
        """parameter renamed from ``model`` to"""
        self.models.change_model(model_size)

    # the following 6 TrayController protocol methods were

    def quit_app(self) -> None:
        """body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController.quit_app`.
        """
        return self.lifecycle.quit_app()

    def restart_app(self) -> None:
        """body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController.restart_app`.
        """
        # re-entry guard (must be the first executable
        if self._shutting_down_event.is_set():
            log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return
        return self.lifecycle.restart_app()

    def _wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController._wait_for_relaunch_ack`.
        """
        return self.lifecycle._wait_for_relaunch_ack(timeout)
