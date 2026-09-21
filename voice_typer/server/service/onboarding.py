"""Onboarding service mixin."""

import logging

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.service._app_internals import app_config_mutation_lock
from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class OnboardingMixin(ServiceMixinBase):
    """Onboarding-wizard service methods."""

    def onboarding_is_first_run(self) -> dict:
        """Check if this is the first run (onboarding needed)."""
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController()
        return {"is_first_run": ctrl.is_first_run()}

    def onboarding_start(self) -> dict:
        """Start the onboarding wizard. Returns step info."""
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController()
        self._onboarding = ctrl
        return {
            "step": ctrl.current_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_check_permissions(self) -> dict:
        """Delegates to :meth:`OnboardingController.check_permissions`, which"""
        try:
            from voice_typer.server.onboarding import OnboardingController

            ctrl = getattr(self, "_onboarding", None)
            if ctrl is None:
                ctrl = OnboardingController()
                self._onboarding = ctrl
            return ctrl.check_permissions()
        except Exception as exc:  # defensive, never block the wizard
            log.exception("[SERVICE] onboarding_check_permissions failed: %s", exc)
            return {"platform": "unknown", "state": "unknown", "needed": False, "instructions": None}

    def onboarding_get_step(self) -> dict:
        """Get current onboarding step info."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        return {
            "step": ctrl.current_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_next_step(self) -> dict:
        """Advance to next onboarding step."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        new_step = ctrl.next_step()
        return {
            "step": new_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_prev_step(self) -> dict:
        """Go back to previous onboarding step."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        new_step = ctrl.prev_step()
        return {
            "step": new_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_set_microphone(self, mic_id: str | None) -> dict:
        """Set the microphone choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.set_microphone(mic_id)
        return {"ok": True}

    def onboarding_set_hotkey(self, hotkey: str) -> dict:
        """Set the hotkey choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.set_hotkey(hotkey)
        return {"ok": True}

    def onboarding_set_model(self, model: str) -> dict:
        """Set the model choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.set_model(model)
        return {"ok": True}

    def onboarding_set_backend(self, backend: str) -> dict:
        """Set the local-vs-cloud backend choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        try:
            ctrl.set_backend(backend)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True}

    def onboarding_skip(self) -> dict:
        """Skip onboarding entirely."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.skip()
        return {"ok": True}

    def onboarding_apply(self) -> dict:
        """Apply onboarding settings and mark complete."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        app = self._app
        # Snapshot the config fields that ``apply_settings``
        _cfg = getattr(app, "config", None)
        _cfg_snapshot = {
            "microphone": getattr(_cfg, "microphone", None) if _cfg is not None else None,
            "hotkey": getattr(_cfg, "hotkey", None) if _cfg is not None else None,
            "model_size": getattr(_cfg, "model_size", None) if _cfg is not None else None,
            "onboarding_completed": (getattr(_cfg, "onboarding_completed", False) if _cfg is not None else False),
        }
        try:
            # Capture the previous model_size so we can skip the
            prev_model_size = getattr(app.config, "model_size", None)

            # Build the updates dict for apply_config_side_effects.
            updates: dict = {
                "hotkey": ctrl.selected_hotkey,
                "model_size": ctrl.selected_model,
            }
            if ctrl.selected_microphone is not None:
                updates["microphone"] = ctrl.selected_microphone

            # RACE-011: hold the app's config-mutation lock for the
            with app_config_mutation_lock(app):
                ctrl.apply_settings(app.config)
                app.config.onboarding_completed = True
                # Apply side effects inside the lock so any Config
                self.apply_config_side_effects(updates)
                app.config.save()

            # invalidate the tray menu cache so the next
            try:
                app.tray.invalidate_menu_cache()
            except Exception:
                log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)

            # 17-H-: reload the model if the user picked a
            new_model = ctrl.selected_model
            if new_model != prev_model_size:
                try:
                    self.change_model(new_model)
                except Exception as e:
                    log.warning("[SERVICE] onboarding model change failed: %s", e)

            # Push a config_changed event so the renderer (App.tsx)
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "config_changed",
                        "data": updates,
                    }
                )
            except Exception:
                log.debug("[SERVICE] onboarding config_changed push failed", exc_info=True)

            return {"ok": True}
        except Exception as exc:
            # Roll back the in-place config mutation so a failed
            try:
                _cfg = getattr(app, "config", None)
                if _cfg is not None:
                    for _key, _val in _cfg_snapshot.items():
                        setattr(_cfg, _key, _val)
            except Exception:
                log.debug("[SERVICE] onboarding_apply rollback failed", exc_info=True)
            # redact exc string before returning to IPC layer.
            redacted = redact_secret(redact_url(str(exc)))
            log.exception("[SERVICE] onboarding_apply failed: %s", redacted)
            return {"error": redacted}

    def onboarding_get_microphones(self) -> dict:
        """Get available microphones for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController

        ctrl = getattr(self, "_onboarding", OnboardingController())
        return {"microphones": ctrl.get_microphones()}

    def onboarding_get_model_options(self) -> dict:
        """Get model options for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController

        return {"models": OnboardingController.MODEL_OPTIONS}

    def onboarding_get_model_catalog(self) -> dict:
        """Get the full rich-metadata model catalog for the"""
        from voice_typer.server.onboarding import OnboardingController

        return {"models": OnboardingController.get_model_catalog()}

    def onboarding_get_hotkey_presets(self) -> dict:
        """Get hotkey presets for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController

        return {"presets": OnboardingController.HOTKEY_PRESETS}
