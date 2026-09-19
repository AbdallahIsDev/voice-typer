"""permission flow now uses ``onboarding_check_permissions`` + a"""

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ResponseEnvelope,
    _error_response,
)
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE


def _redact_service_error(result: dict) -> dict:
    """redact a service-returned ``{"error": str(exc)}`` dict

    Returns
    """
    err = result.get("error")
    if isinstance(err, str) and err:
        result["error"] = redact_url(redact_secret(err))
    return result


class OnboardingHandlersMixin(HandlerBase):
    """Mixin: onboarding-wizard IPC handlers (onboarding_start / onboarding_apply / ...)."""

    # The ``service`` / ``app`` / ``_send`` annotations are

    def _handle_onboarding_is_first_run(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_is_first_run`` IPC command."""
        try:
            result = self.service.onboarding_is_first_run()
            resp["type"] = "onboarding_first_run"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "onboarding_is_first_run")
        return resp

    def _handle_onboarding_start(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_start`` IPC command."""
        try:
            # re-run guard. ``data`` may be a non-dict (e.g. None
            data_dict = data if isinstance(data, dict) else {}
            force = bool(data_dict.get("force", False))
            first_run_result = self.service.onboarding_is_first_run()
            is_first_run = bool(first_run_result.get("is_first_run", True))
            if not is_first_run and not force:
                log.warning(
                    "[IPC] onboarding_start: rejected, onboarding already complete; pass {force: true} to re-run"
                )
                return _error_response(
                    resp,
                    "Onboarding already complete; pass {force: true} to re-run",
                    code="onboarding_already_complete",
                )
            result = self.service.onboarding_start()
            # Mark the wizard as started so auto-heal doesn't
            try:
                from voice_typer.server.onboarding import OnboardingController

                OnboardingController().mark_started()
            except Exception:
                # was ``pass``. Promoted to WARNING + exc_info so
                log.warning(
                    "[IPC] onboarding_start: mark_started failed, auto-heal may clobber in-progress onboarding",
                    exc_info=True,
                )
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_start")
        return resp

    def _handle_onboarding_next_step(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_next_step`` IPC command."""
        try:
            result = self.service.onboarding_next_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_next_step")
        return resp

    def _handle_onboarding_prev_step(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_prev_step`` IPC command."""
        try:
            result = self.service.onboarding_prev_step()
            resp["type"] = "onboarding_step"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_prev_step")
        return resp

    def _ack_or_error(self, cmd: str, result: dict) -> dict:
        """Build the ack-vs-error response envelope for a service result.

        Returns the response envelope ``{"type": "ack" | "error",
        """
        if result.get("error") is not None:
            # log the service-returned error at WARNING (raw,
            log.warning(
                "[IPC] %s: service returned error: %s",
                cmd,
                result.get("error"),
            )
            # redact the error string before forwarding to the
            result = _redact_service_error(result)
        # use ``result.get("error") is None`` so ``{"error": None}``
        return {"type": "ack" if result.get("error") is None else "error", "data": result}

    def _handle_onboarding_set_microphone(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_microphone`` IPC command."""

        def body(d: dict) -> dict:
            result = self.service.onboarding_set_microphone(d["mic_id"])
            # from that key, logs the failure at WARNING, and redacts the
            return self._ack_or_error("onboarding_set_microphone", result)

        return self._wrap(
            cmd_name="onboarding_set_microphone",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"mic_id": {"type": (str, type(None)), "required": True}},
            pre_coerce=False,
        )

    def _handle_onboarding_set_hotkey(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_hotkey`` IPC command."""

        def body(d: dict) -> dict:
            result = self.service.onboarding_set_hotkey(d["hotkey"])
            # redacts the error string before it reaches the renderer.
            return self._ack_or_error("onboarding_set_hotkey", result)

        return self._wrap(
            cmd_name="onboarding_set_hotkey",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"hotkey": {"type": str, "required": False, "default": "<caps_lock>"}},
            pre_coerce=False,
        )

    def _handle_onboarding_set_model(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_model`` IPC command."""

        def body(d: dict) -> dict:
            result = self.service.onboarding_set_model(d["model"])
            # redacts the error string before it reaches the renderer.
            return self._ack_or_error("onboarding_set_model", result)

        return self._wrap(
            cmd_name="onboarding_set_model",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"model": {"type": str, "required": False, "default": DEFAULT_MODEL_SIZE}},
            pre_coerce=False,
        )

    def _handle_onboarding_set_backend(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_set_backend`` IPC command."""

        def body(d: dict) -> dict:
            result = self.service.onboarding_set_backend(d["backend"])
            # Same ack-vs-error contract as the sibling onboarding
            return self._ack_or_error("onboarding_set_backend", result)

        return self._wrap(
            cmd_name="onboarding_set_backend",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"backend": {"type": str, "required": True}},
            pre_coerce=False,
        )

    def _handle_onboarding_skip(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_skip`` IPC command."""
        try:
            result = self.service.onboarding_skip()
            # key, logs the failure at WARNING, and redacts the error
            envelope = self._ack_or_error("onboarding_skip", result)
            resp["type"] = envelope["type"]
            resp["data"] = envelope["data"]
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_skip")
        return resp

    def _handle_onboarding_apply(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``onboarding_apply`` IPC command."""
        try:
            result = self.service.onboarding_apply()
            # :meth:`_ack_or_error` WARNING breadcrumb the (already
            envelope = self._ack_or_error("onboarding_apply", result)
            if envelope["data"].get("error") is not None:
                log.error(
                    "[IPC] onboarding_apply failed (redacted): %s",
                    envelope["data"].get("error"),
                )
            resp["type"] = envelope["type"]
            resp["data"] = envelope["data"]
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_apply")
        return resp

    def _handle_onboarding_get_microphones(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_get_microphones`` IPC command."""
        try:
            result = self.service.onboarding_get_microphones()
            resp["type"] = "onboarding_microphones"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_microphones")
        return resp

    def _handle_onboarding_get_model_options(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_get_model_options`` IPC command."""
        try:
            result = self.service.onboarding_get_model_options()
            resp["type"] = "onboarding_models"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_model_options")
        return resp

    def _handle_onboarding_get_hotkey_presets(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_get_hotkey_presets`` IPC command."""
        try:
            result = self.service.onboarding_get_hotkey_presets()
            resp["type"] = "onboarding_hotkey_presets"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_get_hotkey_presets")
        return resp

    def _handle_onboarding_check_permissions(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``onboarding_check_permissions`` IPC command ( / ).
        :meth:`OnboardingController.check_permissions`) and is shared
        """
        try:
            from voice_typer.server.onboarding import OnboardingController

            result = OnboardingController().check_permissions()
            resp["type"] = "onboarding_permissions"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "onboarding_check_permissions")
        return resp

    def _handle_onboarding_reset(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Registered in ``_COMMAND_REGISTRY`` under the ``onboarding_reset``"""
        try:
            from voice_typer.server.onboarding import OnboardingController

            OnboardingController().reset()
            resp["type"] = "ack"
            resp["data"] = {"ok": True}
        except Exception as exc:
            self._respond_with_error(resp, exc, "onboarding_reset")
        return resp
