"""``refresh_microphones`` command was dropped from ``_COMMAND_REGISTRY``"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import ResponseEnvelope


class MicrophoneHandlersMixin(HandlerBase):
    """Mixin: microphone-listing IPC handlers (get_microphones)."""

    def _handle_get_microphones(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_microphones`` IPC command."""
        try:
            resp["type"] = "microphones"
            resp["data"] = self.service.get_microphones()
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_microphones")
        return resp
