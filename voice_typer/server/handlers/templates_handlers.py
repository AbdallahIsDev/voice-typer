"""Templates IPC handler mixin: get_templates, save_templates.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

this mixin's ``except Exception`` catch-alls call
:meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
no ``str(e)`` leak). Per-command VALIDATION errors (``missing_field``,
``invalid_field``, ``invalid_payload``, ``payload_too_large``) remain
EXPLICIT and are NOT routed through ``_respond_with_error`` — they are
part of the documented IPC contract that the renderer switches on.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload


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
            resp["type"] = "templates"
            resp["data"] = {"templates": templates}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_templates")
        return resp

    def _handle_save_templates(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``save_templates`` IPC command.

        The schema declares a 256 KB whole-payload cap via
        ``max_payload_bytes`` so a multi-MB template list can't pin
        the IPC thread or blow up the on-disk JSON store. After the
        schema check, an inline loop rejects any single ``trigger``
        or ``output`` string longer than 1024 chars (defense-in-depth
        IPC guard that runs BEFORE the templates module's stricter
        per-field caps ``MAX_TRIGGER_LENGTH=200`` /
        ``MAX_OUTPUT_LENGTH=2000``). Oversized values return the
        explicit ``client.invalid_field`` envelope (with the offending
        field name) so the renderer can highlight the bad row.
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "templates": {"type": list, "required": True},
                    # 256 KB payload cap. ``_payload`` is a
                    # sentinel field name — ``max_payload_bytes`` is a
                    # whole-payload rule, not a per-field rule, but the
                    # schema is keyed by field name so we use a ``_``
                    # prefix to signal "not a real field" (same idiom
                    # as ``vocabulary_handlers.save_vocabulary``).
                    "_payload": {"max_payload_bytes": 256 * 1024},
                },
            )
            if error:
                resp["type"] = "error"
                resp["data"] = error["data"]
                # Narrow error["data"] to dict before indexing.
                # ``_validate_dict_payload`` has no return-type annotation,
                # so pyrefly infers its error return as
                # ``dict[str, str | dict[str, str]]`` (unifying all the
                # ``"type": "error", "data": {...}`` branches). At runtime
                # ``error["data"]`` is always a dict, but the type system
                # can't prove it — narrow with ``isinstance`` so the
                # ``["code"]`` / ``["message"]`` indexing type-checks.
                _err_data = error.get("data")
                if isinstance(_err_data, dict) and _err_data.get("code") == "invalid_payload":
                    log.warning(
                        "[IPC] save_templates rejected: %s",
                        _err_data.get("message"),
                    )
                return resp
            assert validated is not None  # narrowed by the error guard above
            templates = validated["templates"]

            # Per-field length cap (1024 chars). The templates
            # module enforces tighter per-field caps downstream (200 for
            # trigger, 2000 for output), but this IPC-level guard lets
            # the renderer distinguish "client sent an obviously bogus
            # 50 KB trigger" from "server failed to persist a valid
            # template" — without it, the oversized value would propagate
            # into ``templates.save`` and surface as a generic
            # ``internal_error`` () envelope.
            _max_field_len = 1024
            for idx, entry in enumerate(templates):
                if not isinstance(entry, dict):
                    continue
                for field_name in ("trigger", "output"):
                    value = entry.get(field_name)
                    if isinstance(value, str) and len(value) > _max_field_len:
                        _error_response(
                            resp,
                            (f"'{field_name}' value too long in templates[{idx}] ({len(value)} > {_max_field_len})"),
                            code="client.invalid_field",
                        )
                        # Log at WARNING so operators can see
                        # rejection rates (a spike suggests a renderer
                        # bug producing oversized templates).
                        log.warning(
                            "[IPC] save_templates rejected: %s value too long in templates[%d] (%d > %d)",
                            field_name,
                            idx,
                            len(value),
                            _max_field_len,
                        )
                        return resp

            self.service.save_templates(templates)
            resp["type"] = "ack"
            resp["data"] = {"saved": len(templates)}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "save_templates")
        return resp
