"""Dictation IPC handler mixin: toggle_dictation, undo_last.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log


class DictationHandlersMixin:
    """Mixin: dictation IPC handlers (toggle_dictation / undo_last)."""

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

    def _handle_toggle_dictation(self, data, resp) -> dict | None:
        """Handle the ``toggle_dictation`` IPC command."""
        try:
            self.service.toggle_dictation()
            resp["type"] = "ack"
        except Exception as e:
            log.error("[IPC] toggle_dictation failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_undo_last(self, data, resp) -> dict | None:
        """Handle the ``undo_last`` IPC command."""
        # UX-003: undo last transcription via backspace keystrokes
        try:
            self.service.undo_last()
            resp["type"] = "ack"
        except Exception as e:
            log.error("[IPC] undo_last failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
