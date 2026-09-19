"""Microphone-test IPC handler mixin: 4 microphone_test_* commands."""

from typing import cast

from voice_typer.server.asr_errors import ConsentRequiredError
from voice_typer.server.handlers._base import HandlerBase, log
from voice_typer.server.ipc.validation import ResponseEnvelope, _error_response, _validate_dict_payload


class MicrophoneTestHandlersMixin(HandlerBase):
    """Mixin: microphone-test IPC handlers (start / stop / cancel / get_level)."""

    def _handle_microphone_test_start(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``microphone_test_start`` IPC command."""

        def body(d: dict) -> dict:
            # capturing any test audio. The mic test returns up to 30s
            try:
                if not getattr(self.app.config, "voice_biometric_consent", False):
                    raise ConsentRequiredError(
                        "voice biometric consent required to start microphone test",
                        engine_name="microphone_test",
                        consent_field="voice_biometric_consent",
                    )
            except ConsentRequiredError:
                raise
            except Exception:
                log.exception("[IPC] microphone_test_start: failed to read voice_biometric_consent, failing open")

            # validate ``mic_id`` and ``filters`` types via the
            validated, error = _validate_dict_payload(
                d,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": False,
                        "default": None,
                    },
                    # ADR 0007 filter-config contract: ``filters`` is
                    "filters": {
                        "type": (dict, type(None)),
                        "required": False,
                        "default": None,
                    },
                    "duration": {
                        "type": (int, float, str),
                        "required": False,
                        "default": 10.0,
                        "clamp_range": (1.0, 30.0),
                    },
                },
            )
            if error:
                return error
            mic_id = validated.get("mic_id")
            filters = validated.get("filters")
            # already clamped int/float values, but strings bypass it
            duration = float(cast(float | int | str, validated["duration"]))
            duration = max(1.0, min(duration, 30.0))
            result = self.service.microphone_test_start(mic_id=mic_id, duration=duration, filters=filters)
            return {"type": "microphone_test_result", "data": result}

        return self._wrap(
            cmd_name="microphone_test_start",
            resp_type="microphone_test_result",
            data=data,
            resp=resp,
            body=body,
        )

    def _handle_microphone_test_stop(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``microphone_test_stop`` IPC command."""
        try:
            result = self.service.microphone_test_stop()
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_stop")
        return resp

    def _handle_microphone_test_read_audio(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``microphone_test_read_audio`` IPC command."""
        try:
            if not isinstance(data, dict):
                return _error_response(resp, "microphone_test_read_audio requires data: object", code="invalid_payload")
            validated, error = _validate_dict_payload(
                data,
                {
                    "path": {"type": str, "required": True},
                    "offset": {"type": int, "required": False, "default": 0, "clamp_range": (0, 100_000_000)},
                    "length": {"type": int, "required": False, "default": 255 * 1024, "clamp_range": (1, 256 * 1024)},
                },
            )
            if error:
                return error
            assert validated is not None
            result = self.service.microphone_test_read_audio(
                path=cast(str, validated["path"]),
                offset=int(cast(int | str, validated["offset"])),
                length=int(cast(int | str, validated["length"])),
            )
            resp["type"] = "microphone_test_audio_chunk"
            resp["data"] = result
        except Exception as exc:
            self._respond_with_error(resp, exc, "microphone_test_read_audio")
        return resp

    def _handle_microphone_test_cancel(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``microphone_test_cancel`` IPC command."""
        try:
            result = self.service.microphone_test_cancel()
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_cancel")
        return resp

    def _handle_microphone_test_get_level(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``microphone_test_get_level`` IPC command."""
        try:
            result = self.service.microphone_test_get_level()
            resp["type"] = "microphone_test_level"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "microphone_test_get_level")
        return resp
