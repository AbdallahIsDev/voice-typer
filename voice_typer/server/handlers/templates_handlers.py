"""Templates IPC handler mixin: get_templates, save_templates."""

from typing import cast

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    ResponseEnvelope,
    _enforce_payload_size_cap,
    _validate_dict_payload,
)
from voice_typer.server.templates import (
    MAX_OUTPUT_LENGTH,
    MAX_TRIGGER_LENGTH,
)


class TemplatesHandlersMixin(HandlerBase):
    """Mixin: templates IPC handlers (get_templates / save_templates)."""

    def _handle_get_templates(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_templates`` IPC command."""
        try:
            templates = self.service.get_templates()
            # Size-cap guard: the template store is unbounded at the
            payload = {"templates": templates}
            cap_error = _enforce_payload_size_cap(
                payload,
                error_message="Templates are too large to transfer over IPC",
            )
            if cap_error is not None:
                resp["type"] = cap_error["type"]
                resp["data"] = cap_error["data"]
                log.warning(
                    "[IPC] get_templates response exceeds payload cap; returning clear error instead of a dropped frame"
                )
                return resp
            resp["type"] = "templates"
            resp["data"] = payload
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_templates")
        return resp

    def _handle_save_templates(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``save_templates`` IPC command."""

        def body(d: dict) -> dict:
            validated, error = _validate_dict_payload(
                d,
                {
                    "templates": {"type": list, "required": True},
                    # 256 KB payload cap. ``_payload`` is a
                    "_payload": {"max_payload_bytes": 256 * 1024},
                },
            )
            if error:
                _err_data = error.get("data")
                if isinstance(_err_data, dict) and _err_data.get("code") == "invalid_payload":
                    log.warning(
                        "[IPC] save_templates rejected: %s",
                        _err_data.get("message"),
                    )
                return error
            templates = cast(list, validated["templates"])

            # Per-field length caps. The templates module enforces the
            _field_length_caps = {
                "trigger": MAX_TRIGGER_LENGTH,
                "output": MAX_OUTPUT_LENGTH,
            }
            for idx, entry in enumerate(templates):
                if not isinstance(entry, dict):
                    continue
                for field_name, field_cap in _field_length_caps.items():
                    value = entry.get(field_name)
                    if isinstance(value, str) and len(value) > field_cap:
                        # Log at WARNING so operators can see
                        log.warning(
                            "[IPC] save_templates rejected: %s value too long in templates[%d] (%d > %d)",
                            field_name,
                            idx,
                            len(value),
                            field_cap,
                        )
                        return self._error_response(
                            resp,
                            (f"'{field_name}' value too long in templates[{idx}] ({len(value)} > {field_cap})"),
                            code=ErrorCodes.INVALID_FIELD,
                        )

            self.service.save_templates(templates)
            return {"type": "ack", "data": {"saved": len(templates)}}

        return self._wrap(
            cmd_name="save_templates",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            pre_coerce=False,
        )
