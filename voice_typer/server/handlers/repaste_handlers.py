"""Repaste IPC handler mixin: repaste_last."""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import ResponseEnvelope


class RepasteHandlersMixin(HandlerBase):
    """Mixin: repaste IPC handlers (repaste_last)."""

    def _handle_repaste_last(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``repaste_last`` IPC command ()."""
        try:
            result = self.service.repaste_last()
            resp["type"] = "ack"
            resp["data"] = {"result": result}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "repaste_last")
        return resp
