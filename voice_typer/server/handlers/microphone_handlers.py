"""Microphone IPC handler mixin: get_microphones.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.

(2026-07-30): ``_handle_refresh_microphones`` was REMOVED — the
``refresh_microphones`` command was dropped from ``_COMMAND_REGISTRY``
and the renderer allowlist during the Tauri migration (the renderer
calls ``get_microphones`` to refresh). The service-layer method
``service.refresh_microphones`` still exists for internal callers;
only the IPC dispatch route was deleted.
"""

from voice_typer.server.handlers._base import HandlerBase


class MicrophoneHandlersMixin(HandlerBase):
    """Mixin: microphone-listing IPC handlers (get_microphones).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak).
    """

    def _handle_get_microphones(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_microphones`` IPC command."""
        try:
            resp["type"] = "microphones"
            resp["data"] = self.service.get_microphones()
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_microphones")
        return resp
