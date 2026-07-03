"""Onboarding IPC handler mixin: 13 onboarding_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.ipc_server import log


class OnboardingHandlersMixin:
    """Mixin: onboarding-wizard IPC handlers (onboarding_start / onboarding_apply / ...)."""

    def _handle_onboarding_is_first_run(self, data, resp) -> dict | None:
        """Handle the ``onboarding_is_first_run`` IPC command."""
        try:
            result = self.service.onboarding_is_first_run()
            resp["type"] = "onboarding_first_run"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_is_first_run failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_start(self, data, resp) -> dict | None:
        """Handle the ``onboarding_start`` IPC command."""
        try:
            result = self.service.onboarding_start()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_start failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_get_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_step`` IPC command."""
        try:
            result = self.service.onboarding_get_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_get_step failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_next_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_next_step`` IPC command."""
        try:
            result = self.service.onboarding_next_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_next_step failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_prev_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_prev_step`` IPC command."""
        try:
            result = self.service.onboarding_prev_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_prev_step failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_set_microphone(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_microphone`` IPC command."""
        try:
            mic_id = (data or {}).get("mic_id") if isinstance(data, dict) else None
            result = self.service.onboarding_set_microphone(mic_id)
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_set_microphone failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_set_hotkey(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_hotkey`` IPC command."""
        try:
            hotkey = (data or {}).get("hotkey", "<f2>") if isinstance(data, dict) else "<f2>"
            result = self.service.onboarding_set_hotkey(hotkey)
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_set_hotkey failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_set_model(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_model`` IPC command."""
        try:
            model = (data or {}).get("model", "small.en") if isinstance(data, dict) else "small.en"
            result = self.service.onboarding_set_model(model)
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_set_model failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_skip(self, data, resp) -> dict | None:
        """Handle the ``onboarding_skip`` IPC command."""
        try:
            result = self.service.onboarding_skip()
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_skip failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_apply(self, data, resp) -> dict | None:
        """Handle the ``onboarding_apply`` IPC command."""
        try:
            result = self.service.onboarding_apply()
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_apply failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_get_microphones(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_microphones`` IPC command."""
        try:
            result = self.service.onboarding_get_microphones()
            resp["type"] = "onboarding_microphones"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_get_microphones failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_get_model_options(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_model_options`` IPC command."""
        try:
            result = self.service.onboarding_get_model_options()
            resp["type"] = "onboarding_models"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_get_model_options failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_onboarding_get_hotkey_presets(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_hotkey_presets`` IPC command."""
        try:
            result = self.service.onboarding_get_hotkey_presets()
            resp["type"] = "onboarding_hotkey_presets"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] onboarding_get_hotkey_presets failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
