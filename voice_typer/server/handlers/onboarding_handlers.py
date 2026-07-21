"""Onboarding IPC handler mixin: 13 onboarding_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import _validate_dict_payload


class OnboardingHandlersMixin(HandlerBase):
    """Mixin: onboarding-wizard IPC handlers (onboarding_start / onboarding_apply / ...).

    CR-20: this mixin is one of the four "representative" handlers
    migrated to :meth:`HandlerBase._respond_with_error` for the
    catch-all ``except Exception`` path. See
        ``voice_typer/server/handlers/_base.py`` for the migration plan.
    """

    # ARCH-REFAC-002 / TASK-10: pyrefly null-safety fix.
    # These attributes are provided at runtime by the IPCServer host
    # class via multiple inheritance. Declaring them as ``Any`` here
    # lets pyrefly type-check the mixin methods in isolation without
    # requiring a Protocol that would couple the mixin to a specific
    # service/app implementation (MagicMock fixtures in tests rely on
    # the loose typing).
    service: "Any"
    app: "Any"
    _send: "Any"

    def _handle_onboarding_is_first_run(self, data, resp) -> dict | None:
        """Handle the ``onboarding_is_first_run`` IPC command."""
        try:
            result = self.service.onboarding_is_first_run()
            resp["type"] = "onboarding_first_run"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "onboarding_is_first_run")
        return resp

    def _handle_onboarding_start(self, data, resp) -> dict | None:
        """Handle the ``onboarding_start`` IPC command."""
        try:
            result = self.service.onboarding_start()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_start")
        return resp

    def _handle_onboarding_get_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_step`` IPC command."""
        try:
            result = self.service.onboarding_get_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_step")
        return resp

    def _handle_onboarding_next_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_next_step`` IPC command."""
        try:
            result = self.service.onboarding_next_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_next_step")
        return resp

    def _handle_onboarding_prev_step(self, data, resp) -> dict | None:
        """Handle the ``onboarding_prev_step`` IPC command."""
        try:
            result = self.service.onboarding_prev_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_prev_step")
        return resp

    def _handle_onboarding_set_microphone(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_microphone`` IPC command.

        CR-64: ``mic_id`` is allowed to be ``None`` (no microphone
        detected case). The renderer sends ``mic_id: null`` when no
        microphones are present, so the validator accepts both ``str``
        and ``NoneType``. The ``OnboardingController.set_microphone``
        stores ``None`` verbatim, which :meth:`apply_settings` then
        skips writing to the config (preserving the default).
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": True,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_microphone(validated["mic_id"])
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_microphone")
        return resp

    def _handle_onboarding_set_hotkey(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_hotkey`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "hotkey": {"type": str, "required": False, "default": "<f2>"},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_hotkey(validated["hotkey"])
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_hotkey")
        return resp

    def _handle_onboarding_set_model(self, data, resp) -> dict | None:
        """Handle the ``onboarding_set_model`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "model": {"type": str, "required": False, "default": "small.en"},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.onboarding_set_model(validated["model"])
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_set_model")
        return resp

    def _handle_onboarding_skip(self, data, resp) -> dict | None:
        """Handle the ``onboarding_skip`` IPC command."""
        try:
            result = self.service.onboarding_skip()
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_skip")
        return resp

    def _handle_onboarding_apply(self, data, resp) -> dict | None:
        """Handle the ``onboarding_apply`` IPC command."""
        try:
            result = self.service.onboarding_apply()
            resp["type"] = "ack" if "error" not in result else "error"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_apply")
        return resp

    def _handle_onboarding_get_microphones(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_microphones`` IPC command."""
        try:
            result = self.service.onboarding_get_microphones()
            resp["type"] = "onboarding_microphones"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_microphones")
        return resp

    def _handle_onboarding_get_model_options(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_model_options`` IPC command."""
        try:
            result = self.service.onboarding_get_model_options()
            resp["type"] = "onboarding_models"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_model_options")
        return resp

    def _handle_onboarding_get_model_catalog(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_model_catalog`` IPC command (UX-32).

        Returns the full rich-metadata model catalog (a superset of the
        curated ``MODEL_OPTIONS`` subset). Does NOT delegate to
        ``self.service`` — the catalog is pure static metadata from
        :mod:`voice_typer.server.model_registry`, shared with the Models
        page's ``get_model_catalog`` IPC via
        :meth:`OnboardingController.get_model_catalog`.
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            result = {"models": OnboardingController.get_model_catalog()}
            resp["type"] = "onboarding_model_catalog"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_model_catalog")
        return resp

    def _handle_onboarding_get_hotkey_presets(self, data, resp) -> dict | None:
        """Handle the ``onboarding_get_hotkey_presets`` IPC command."""
        try:
            result = self.service.onboarding_get_hotkey_presets()
            resp["type"] = "onboarding_hotkey_presets"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_hotkey_presets")
        return resp

    def _handle_onboarding_check_permissions(self, data, resp) -> dict | None:
        """Handle the ``onboarding_check_permissions`` IPC command (UX-4 / UX-27).

        Returns the platform-conditional permission state so the
        Permissions step can render the right setup walkthrough
        (macOS Accessibility / Linux ``input`` group + udev rule).

        Does NOT delegate to ``self.service`` — the permission probe
        lives in :mod:`voice_typer.server.permissions` (via
        :meth:`OnboardingController.check_permissions`) and is shared
        with the hotkey-adapter runtime path.
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            result = OnboardingController().check_permissions()
            resp["type"] = "onboarding_permissions"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_check_permissions")
        return resp
