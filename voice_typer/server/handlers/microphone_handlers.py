"""Microphone IPC handler mixin: get_microphones, refresh_microphones.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log


class MicrophoneHandlersMixin:
    """Mixin: microphone-listing IPC handlers (get_microphones / refresh_microphones)."""

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

    def _handle_get_microphones(self, data, resp) -> dict | None:
        """Handle the ``get_microphones`` IPC command."""
        try:
            resp["type"] = "microphones"
            resp["data"] = self.service.get_microphones()
        except Exception as e:
            log.error("[IPC] get_microphones failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_refresh_microphones(self, data, resp) -> dict | None:
        """Handle the ``refresh_microphones`` IPC command."""
        # AUDIO-MIC: re-query PortAudio for available microphones.
        # Called when the user clicks "Refresh Microphones" in the
        # Electron UI after plugging in a new USB/BT device.
        try:
            mics = self.service.refresh_microphones()
            resp["type"] = "microphones"
            resp["data"] = mics
        except Exception as e:
            log.error("[IPC] refresh_microphones failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
