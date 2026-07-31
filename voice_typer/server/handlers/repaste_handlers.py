"""Repaste IPC handler mixin: repaste_last.

wires the ``repaste_last`` IPC command to the existing
``VoiceTyperService.repaste_last()`` method (defined at
``voice_typer/server/service.py:137-139``).  The service method already
delegates to ``app.repaste_last()`` — this handler is a thin envelope
that calls it and wraps the result in the standard
``{"result": <return_value>}`` envelope (forward-compat: if the
service ever grows a return value, the renderer can read it).

follows the same mixin pattern as the other handler
modules in this package (e.g. ``dictation_handlers.py``).  The mixin
accesses ``self.app`` / ``self.service`` via the host :class:`IPCServer`
instance — it has no state of its own.

(FA16, 2026-07-19): the ack envelope previously diverged
from sibling acks by including ``"ok": True`` — the only handler in
the package using that key. The envelope now matches ``undo_last``'s
shape: ``{"type": "ack", "data": {"result": <value>}}`` (the
``result`` key is retained for forward-compat in case the service
grows a return value; drop it entirely if a bare ``{"type": "ack"}``
is preferred).
"""

from voice_typer.server.handlers._base import HandlerBase


class RepasteHandlersMixin(HandlerBase):
    """Mixin: repaste IPC handlers (repaste_last).

    this mixin's ``except Exception`` catch-all calls
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak).
    """

    def _handle_repaste_last(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``repaste_last`` IPC command ().

                Re-pastes the last transcription via
                :meth:`VoiceTyperService.repaste_last` (which delegates to
                ``app.repaste_last()``).  The service method returns ``None``
                on success; any failure raises and is caught by the standard
                error envelope below.

                The response is the standard ``ack`` envelope used by other
                no-result handlers (e.g. ``_handle_undo_last``); the
                ``result`` key is included for forward-compatibility if the
                service ever grows a return value (e.g. the pasted text).
        the ``"ok": True`` key was dropped — it was the
                only handler in the package using that key, diverging from
                ``undo_last``'s bare ``{"type": "ack"}`` shape.
        """
        try:
            result = self.service.repaste_last()
            resp["type"] = "ack"
            resp["data"] = {"result": result}
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "repaste_last")
        return resp
