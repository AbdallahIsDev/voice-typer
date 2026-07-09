"""Templates IPC handler mixin: get_templates, save_templates.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import (
    log,
    _validate_dict_payload,
)


class TemplatesHandlersMixin:
    """Mixin: templates IPC handlers (get_templates / save_templates)."""

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

    def _handle_get_templates(self, data, resp) -> dict | None:
        """Handle the ``get_templates`` IPC command."""
        try:
            templates = self.service.get_templates()
            resp["type"] = "templates"
            resp["data"] = {"templates": templates}
        except Exception as e:
            log.error("[IPC] get_templates failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_save_templates(self, data, resp) -> dict | None:
        """Handle the ``save_templates`` IPC command."""
        try:
            validated, error = _validate_dict_payload(data, {
                "templates": {"type": list, "required": True},
            })
            if error:
                return error
            self.service.save_templates(validated["templates"])
            resp["type"] = "ack"
            resp["data"] = {"saved": len(validated["templates"])}
        except Exception as e:
            log.error("[IPC] save_templates failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
