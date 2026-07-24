"""Onboarding domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). First-run wizard state, step navigation, and
the apply-settings flow that mirrors ``set_config``.
"""

import logging

log = logging.getLogger(__name__)


class OnboardingMixin:
    """Onboarding-wizard service methods.

    Wraps :class:`voice_typer.server.onboarding.OnboardingController`
    and holds the live controller on ``self._onboarding`` between
    ``onboarding_start`` and ``onboarding_apply``.
    """

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
        """Probe OS-level keyboard-monitoring permission state (UX-4 / UX-27).

        Delegates to :meth:`OnboardingController.check_permissions`, which
        returns a renderer-friendly dict describing the current platform,
        whether permission is still needed, and (on macOS / Linux)
        the setup walkthrough (incl. the Linux ``input`` group +
        udev-rule commands). The frontend's Permissions step calls
        this on entry so it can show the right instructions.
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            ctrl = getattr(self, "_onboarding", None)
            if ctrl is None:
                ctrl = OnboardingController()
                self._onboarding = ctrl
            return ctrl.check_permissions()
        except Exception as exc:  # defensive — never block the wizard
            log.error("[SERVICE] onboarding_check_permissions failed: %s", exc)
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

    def onboarding_skip(self) -> dict:
        """Skip onboarding entirely."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.skip()
        return {"ok": True}

    def onboarding_apply(self) -> dict:
        """Apply onboarding settings and mark complete.

        17-H-FIX-1: previously this only called ``ctrl.apply_settings``
        (which does ``config.save()``) — it never invoked
        ``apply_config_side_effects``, so the user's hotkey and model
        choices made in the first-run wizard didn't take effect until
        app restart. We now mirror the canonical ``set_config`` flow
        in ``config_handlers.py``: hold the config-mutation lock,
        invalidate the tray menu cache, re-register the dictation
        hotkey via ``apply_config_side_effects``, optionally reload
        the model, and push a ``config_changed`` event so the
        renderer doesn't need its bespoke re-fetch in
        ``handleOnboardingComplete``.
        """
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        app = self._app
        try:
            # Capture the previous model_size so we can skip the
            # (potentially expensive) model reload when the user kept
            # the default. Hotkey/mic changes are always safe to
            # re-apply.
            prev_model_size = getattr(app.config, "model_size", None)

            # Build the updates dict for apply_config_side_effects.
            # Only include keys that were actually set by the wizard.
            # Built BEFORE the lock so the critical section is short
            # (reading ctrl.* doesn't touch app.config).
            updates: dict = {
                "hotkey": ctrl.selected_hotkey,
                "model_size": ctrl.selected_model,
            }
            if ctrl.selected_microphone is not None:
                updates["microphone"] = ctrl.selected_microphone

            # RACE-011: hold the app's config-mutation lock for the
            # full apply+side-effects+save sequence so a concurrent
            # set_config call can't interleave attribute writes with
            # our onboarding update. Parity with
            # config_handlers.py:_handle_set_config and
            # service.apply_config.
            #
            # MED-H / SERVICE-2: previously apply_config_side_effects
            # was called OUTSIDE the lock, after config.save(). A
            # concurrent set_config IPC call could interleave and
            # corrupt the side-effects (e.g. the hotkey backend would
            # be re-registered against a stale hotkey value, or the
            # audio-preset filter toggles would be persisted to disk
            # in a torn state). Now run inside the lock, BEFORE save,
            # matching apply_config's pattern (so any Config mutations
            # performed by side-effects are persisted to disk).
            with app._config_mutation_lock:
                ctrl.apply_settings(app.config)
                app.config.onboarding_completed = True
                # Apply side effects inside the lock so any Config
                # mutations performed by side-effects (e.g. audio
                # preset filter toggles) are visible to save().
                self.apply_config_side_effects(updates)
                app.config.save()

            # ARCH-043: invalidate the tray menu cache so the next
            # menu build picks up the new hotkey/model/mic.
            try:
                app.tray.invalidate_menu_cache()
            except Exception:
                log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)

            # 17-H-FIX-1: reload the model if the user picked a
            # different one. ModelManager.change_model internally
            # handles the case where the background loader hasn't
            # finished yet — it queues the change via
            # _pending_model_change (model_manager.py:456) and
            # applies it on the next _start_dictation. If the loader
            # HAS finished, the full unload/load cycle runs
            # immediately.
            new_model = ctrl.selected_model
            if new_model != prev_model_size:
                try:
                    app.models.change_model(new_model)
                except Exception as e:
                    log.warning("[SERVICE] onboarding model change failed: %s", e)

            # Push a config_changed event so the renderer (App.tsx)
            # can update UI-local state (theme, font-scale, hotkey
            # label, etc.) immediately instead of waiting for the
            # next mount or issuing a bespoke get_config round-trip.
            # Parity with set_config in config_handlers.py.
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
            return {"error": str(exc)}

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
        """UX-32: Get the full rich-metadata model catalog for the
        onboarding wizard.

        Unlike :meth:`onboarding_get_model_options` (which returns the
        short curated :attr:`MODEL_OPTIONS` subset), this returns the
        full catalog from :meth:`OnboardingController.get_model_catalog`
        (every Whisper variant, distilled/turbo/Parakeet models with VRAM
        / language / speed / accuracy metadata).
        """
        from voice_typer.server.onboarding import OnboardingController

        return {"models": OnboardingController.get_model_catalog()}

    def onboarding_get_hotkey_presets(self) -> dict:
        """Get hotkey presets for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController

        return {"presets": OnboardingController.HOTKEY_PRESETS}
