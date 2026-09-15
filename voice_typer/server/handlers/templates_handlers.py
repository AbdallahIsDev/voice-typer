"""Templates IPC handler mixin: get_templates, save_templates.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

this mixin's ``except Exception`` catch-alls call
:meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
no ``str(e)`` leak). Per-command VALIDATION errors (``missing_field``,
``invalid_field``, ``invalid_payload``, ``payload_too_large``) remain
EXPLICIT and are NOT routed through ``_respond_with_error``, they are
part of the documented IPC contract that the renderer switches on.
"""

from typing import cast

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    _enforce_payload_size_cap,
    _validate_dict_payload,
)
from voice_typer.server.templates import (
    MAX_OUTPUT_LENGTH,
    MAX_TRIGGER_LENGTH,
)


class TemplatesHandlersMixin(HandlerBase):
    """Mixin: templates IPC handlers (get_templates / save_templates).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak).
    """

    def _handle_get_templates(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_templates`` IPC command."""
        try:
            templates = self.service.get_templates()
            # Size-cap guard: the template store is unbounded at the
            # service layer (accumulated across saves). An oversized
            # serialized response would be SILENTLY dropped by the 1 MiB
            # transport frame cap, fail fast with a clear structured
            # error instead.
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

    def _handle_save_templates(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``save_templates`` IPC command.

        The schema declares a 256 KB whole-payload cap via
        ``max_payload_bytes`` so a multi-MB template list can't pin
        the IPC thread or blow up the on-disk JSON store. After the
        schema check, an inline loop rejects any ``trigger`` or
        ``output`` string longer than the module's per-field caps
        (``MAX_TRIGGER_LENGTH`` / ``MAX_OUTPUT_LENGTH``), a
        defense-in-depth IPC guard that mirrors the templates module's
        own validation so the renderer gets a structured error with the
        offending field name. Oversized values return the explicit
        ``client.invalid_field`` envelope (with the offending field
        name) so the renderer can highlight the bad row.

        Migrated to :meth:`HandlerBase._wrap` with ``pre_coerce=False``:
        the on-disk save side effect and the per-field length loop live
        in ``body``. Schema validation stays inside ``body`` so the
        ``invalid_payload`` WARNING log is preserved.
        """

        def body(d: dict) -> dict:
            validated, error = _validate_dict_payload(
                d,
                {
                    "templates": {"type": list, "required": True},
                    # 256 KB payload cap. ``_payload`` is a
                    # sentinel field name, ``max_payload_bytes`` is a
                    # whole-payload rule, not a per-field rule, but the
                    # schema is keyed by field name so we use a ``_``
                    # prefix to signal "not a real field" (same idiom
                    # as ``vocabulary_handlers.save_vocabulary``).
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
            assert validated is not None  # narrowed by the error guard above
            templates = cast(list, validated["templates"])

            # Per-field length caps. The templates module enforces the
            # SAME caps downstream (MAX_TRIGGER_LENGTH / MAX_OUTPUT_LENGTH)
            #: this IPC-level guard mirrors them so the renderer gets a
            # structured ``client.invalid_field`` envelope (with the
            # offending field name) instead of an oversized value
            # propagating into ``templates.save`` and surfacing as a
            # generic ``internal_error`` () envelope.
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
                        # rejection rates (a spike suggests a renderer
                        # bug producing oversized templates).
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
