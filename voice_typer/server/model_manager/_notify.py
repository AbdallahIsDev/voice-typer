"""Load-refusal notifications and suppression gates."""

from __future__ import annotations

import logging

from voice_typer.server import i18n
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_registry import NO_MODEL_SIZE
from voice_typer.server.tray_types import AppState

log = logging.getLogger("voice_typer.server.model_manager")


class LastResortNotifyMixin:
    def _notify_model_load_refused(self, exc: Exception, backend: str | None = None) -> str:
        """Surface a model-load refusal (not downloaded / integrity failed).

        Returns a short human-readable ``failure_reason`` string for
        """
        backend_name = (backend or getattr(self._app.config, "asr_backend", "unknown")).title()
        # Distinguish the two load-refusal flavors so the tray surfaces
        no_model_selected = isinstance(exc, ModelNotDownloadedError) and exc.model_size == NO_MODEL_SIZE
        if isinstance(exc, ModelIntegrityError):
            reason = i18n.t(
                "state.model_manager.model_integrity_failed",
                backend=backend_name,
            )
        elif no_model_selected:
            reason = i18n.t("state.model_manager.no_model_selected")
        else:
            reason = i18n.t(
                "state.model_manager.model_not_downloaded",
                backend=backend_name,
            )
        log_method = log.info if no_model_selected else log.warning
        log_method("[MODEL] %s load refused: %s", backend_name, exc)
        try:
            self._app.tray.set_state(AppState.ERROR, reason)
            self._app.tray.notify(
                APP_NAME,
                i18n.t(
                    "notify.model_manager.no_model_selected"
                    if no_model_selected
                    else "notify.model_manager.model_not_downloaded",
                    backend=backend_name,
                ),
            )
        except Exception:
            # A tray failure must never break the load-refusal path,
            log.warning(
                "[MODEL] tray update for load refusal failed (non-fatal, tray may show stale state): %s",
                reason,
                exc_info=True,
            )
        return reason

    def _model_downloaded_precheck(self) -> bool:
        """Fast filesystem probe: is the active backend's model on disk?

        Returns True (proceed with load) when the config is not a real
        """
        from voice_typer.server.tray_models import is_active_model_downloaded

        return is_active_model_downloaded(self._app.config)

    def _mark_deliberately_unloaded(self, backend_name: str | None) -> None:
        """``AttributeError``: mirrors the defensive ``getattr`` pattern"""
        if not backend_name:
            return
        s = getattr(self, "_deliberately_unloaded", None)
        if s is None:
            s = self._deliberately_unloaded = set()
        s.add(backend_name)

    def _clear_deliberately_unloaded(self, backend_name: str | None) -> None:
        """Clear the deliberate-unload flag after a successful load."""
        s = getattr(self, "_deliberately_unloaded", None)
        if s is not None and backend_name:
            s.discard(backend_name)

    def _was_deliberately_unloaded(self, backend_name: str) -> bool:
        """Return True if ``backend_name`` was deliberately unloaded"""
        s = getattr(self, "_deliberately_unloaded", None)
        return bool(s) and backend_name in s

    def _model_load_in_progress(self) -> bool:
        """Return True while a background load / model-change /"""
        for thread in (
            self._model_load_thread,
            self._model_change_thread,
            self._backend_change_thread,
        ):
            if thread is not None and thread.is_alive():
                return True
        return bool(self._sync_load_in_progress)

    def _in_deliberate_unload_window(self, backend_name: str) -> bool:
        """Return True while the app is in a deliberate-unload window for"""
        if getattr(self._app, "_shutting_down", False):
            return True
        if self._model_load_in_progress():
            return True
        return self._was_deliberately_unloaded(backend_name)

    def _should_suppress_backend_disabled_notification(self, backend_name: str) -> bool:
        """Return True when the ``asr_backend_disabled`` alert must be"""
        if self._in_deliberate_unload_window(backend_name):
            log.debug(
                "[MODEL] backend-disabled %s suppressed (deliberate-unload window)",
                backend_name,
            )
            return True
        return False

    def _should_suppress_last_resort_notification(self, backend_name: str) -> bool:
        """Return True when the last-resort alert must be suppressed for"""
        if self._in_deliberate_unload_window(backend_name):
            if self._was_deliberately_unloaded(backend_name):
                log.debug(
                    "[MODEL] last-resort unloaded %s suppressed (deliberate unload, model on disk)",
                    backend_name,
                )
            return True
        import time

        now = time.monotonic()
        # ``None`` sentinel (not ``0.0``): monotonic() on Windows is
        last = getattr(self, "_last_resort_notified_at", {}).get(backend_name)
        return last is not None and now - last < self._LAST_RESORT_NOTIFY_COOLDOWN_SECS

    def _on_last_resort_unloaded(self, backend_name: str) -> None:
        """Show a tray notification when ``get_active()`` finds no loaded"""
        if self._should_suppress_last_resort_notification(backend_name):
            return
        import time

        # Record the cooldown timestamp here (the alert is about to be
        self._last_resort_notified_at[backend_name] = time.monotonic()
        # Respect the user's notifications toggle (mirrors
        if not getattr(self._app.tray, "_notifications_enabled", True):
            return
        message = i18n.t(
            "notify.model_manager.last_resort_unloaded",
            backend=backend_name,
        )
        # Prefer a CLICKABLE host notification: when an predecessor (or
        from voice_typer.server import event_bus

        live = False
        try:
            live = event_bus.has_live_transport()
        except Exception:
            live = False
        if live:
            try:
                # NOTE: ``event_bus.publish`` returning True only means an
                ok = event_bus.publish(
                    {
                        "type": "notification",
                        "data": {
                            "title": APP_NAME,
                            "message": message,
                            "duration_ms": 0,
                            "critical": False,
                            "click_path": "/models",
                        },
                    }
                )
                if not ok:
                    log.debug(
                        "[MODEL] last-resort notification event publish returned False (no subscriber accepted)",
                    )
                return
            except Exception:
                log.debug(
                    "[MODEL] last-resort notification event publish failed, falling back to tray balloon",
                    exc_info=True,
                )
        try:
            self._app.tray.notify(APP_NAME, message)
        except Exception:
            # A tray failure must never break the last-resort path.
            log.debug(
                "[MODEL] last-resort tray notification failed (non-fatal)",
                exc_info=True,
            )
