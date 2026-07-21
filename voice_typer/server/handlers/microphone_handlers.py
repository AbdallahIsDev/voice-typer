"""Microphone IPC handler mixin: get_microphones, refresh_microphones.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import logging

from voice_typer.server.handlers._base import HandlerMixinBase
from voice_typer.server.ipc.validation import _error_response

log = logging.getLogger("voice_typer.server.ipc_server")


class MicrophoneHandlersMixin(HandlerMixinBase):
    """Mixin: microphone-listing IPC handlers (get_microphones / refresh_microphones)."""

    def _handle_get_microphones(self, data, resp) -> dict | None:
        """Handle the ``get_microphones`` IPC command."""
        try:
            resp["type"] = "microphones"
            resp["data"] = self.service.get_microphones()
        except Exception as e:
            log.error("[IPC] get_microphones failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
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
            _error_response(resp, str(e))
        return resp
