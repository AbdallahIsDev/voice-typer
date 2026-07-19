"""Repaste IPC handler mixin: repaste_last.

UX-23: wires the ``repaste_last`` IPC command to the existing
``VoiceTyperService.repaste_last()`` method (defined at
``voice_typer/server/service.py:137-139``).  The service method already
delegates to ``app.repaste_last()`` — this handler is a thin envelope
that calls it and wraps the result in the standard
``{"ok": True, "result": <return_value>}`` envelope.

ARCH-REFAC-002: follows the same mixin pattern as the other handler
modules in this package (e.g. ``dictation_handlers.py``).  The mixin
accesses ``self.app`` / ``self.service`` via the host :class:`IPCServer`
instance — it has no state of its own.
"""

import logging
from typing import Any

log = logging.getLogger("voice_typer.server.ipc_server")


class RepasteHandlersMixin:
    """Mixin: repaste IPC handlers (repaste_last)."""

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

    def _handle_repaste_last(self, data, resp) -> dict | None:
        """Handle the ``repaste_last`` IPC command (UX-23).

        Re-pastes the last transcription via
        :meth:`VoiceTyperService.repaste_last` (which delegates to
        ``app.repaste_last()``).  The service method returns ``None``
        on success; any failure raises and is caught by the standard
        error envelope below.

        The response is the standard ``ack`` envelope used by other
        no-result handlers (e.g. ``_handle_undo_last``); the
        ``result`` key is included for forward-compatibility if the
        service ever grows a return value (e.g. the pasted text).
        """
        try:
            result = self.service.repaste_last()
            resp["type"] = "ack"
            resp["data"] = {"ok": True, "result": result}
        except Exception as e:
            log.error("[IPC] repaste_last failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
