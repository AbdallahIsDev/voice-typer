"""Microphone IPC handler mixin: get_microphones, refresh_microphones.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.handlers._base import HandlerBase


class MicrophoneHandlersMixin(HandlerBase):
    """Mixin: microphone-listing IPC handlers (get_microphones / refresh_microphones).

    CR-20: this mixin's ``except Exception`` catch-alls call
    :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
    no ``str(e)`` leak).
    """

    def _handle_get_microphones(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_microphones`` IPC command."""
        try:
            resp["type"] = "microphones"
            resp["data"] = self.service.get_microphones()
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_microphones")
        return resp

    def _handle_refresh_microphones(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``refresh_microphones`` IPC command."""
        # AUDIO-MIC: re-query PortAudio for available microphones.
        # Called when the user clicks "Refresh Microphones" in the
        # Electron UI after plugging in a new USB/BT device.
        try:
            mics = self.service.refresh_microphones()
            resp["type"] = "microphones"
            resp["data"] = mics
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "refresh_microphones")
        return resp
