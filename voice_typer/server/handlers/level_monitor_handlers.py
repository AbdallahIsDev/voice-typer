"""Level-monitor IPC handler mixin: 2 level_monitor_* commands."""

from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.handlers._base import HandlerBase, log
from voice_typer.server.ipc.validation import ResponseEnvelope, _validate_dict_payload


class LevelMonitorHandlersMixin(HandlerBase):
    """Mixin: level-monitor IPC handlers (start / stop)."""

    def _handle_level_monitor_start(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``level_monitor_start`` IPC command."""

        def body(d: dict) -> dict:
            # opening the InputStream. The monitor captures audio
            try:
                if not getattr(self.app.config, "voice_biometric_consent", False):
                    raise ConsentRequiredError(
                        "voice biometric consent required to start level monitor",
                        engine_name="level_monitor",
                        consent_field="voice_biometric_consent",
                    )
            except ConsentRequiredError:
                raise
            except Exception:
                log.exception("[IPC] level_monitor_start: failed to read voice_biometric_consent, failing open")

            # validate ``mic_id`` type via the shared
            validated, error = _validate_dict_payload(
                d,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": False,
                        "default": None,
                    },
                },
            )
            if error:
                return error
            mic_id = validated.get("mic_id")
            result = self.service.level_monitor_start(mic_id=mic_id)
            return {"type": "level_monitor_status", "data": result}

        return self._wrap(
            cmd_name="level_monitor_start",
            resp_type="level_monitor_status",
            data=data,
            resp=resp,
            body=body,
        )

    def _handle_level_monitor_stop(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``level_monitor_stop`` IPC command."""
        try:
            result = self.service.level_monitor_stop()
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "level_monitor_stop")
        return resp
