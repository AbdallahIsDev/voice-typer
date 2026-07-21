"""Templates IPC handler mixin: get_templates, save_templates.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import logging

from voice_typer.server.handlers._base import HandlerMixinBase
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload

log = logging.getLogger("voice_typer.server.ipc_server")


class TemplatesHandlersMixin(HandlerMixinBase):
    """Mixin: templates IPC handlers (get_templates / save_templates)."""

    def _handle_get_templates(self, data, resp) -> dict | None:
        """Handle the ``get_templates`` IPC command."""
        try:
            templates = self.service.get_templates()
            resp["type"] = "templates"
            resp["data"] = {"templates": templates}
        except Exception as e:
            log.error("[IPC] get_templates failed: %s", e, exc_info=True)
            # R13-F3: route through ``_error_response`` so the envelope
            # carries the structured ``code: "handler_error"`` field.
            _error_response(resp, str(e))
        return resp

    def _handle_save_templates(self, data, resp) -> dict | None:
        """Handle the ``save_templates`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "templates": {"type": list, "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            self.service.save_templates(validated["templates"])
            resp["type"] = "ack"
            resp["data"] = {"saved": len(validated["templates"])}
        except Exception as e:
            log.error("[IPC] save_templates failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp
