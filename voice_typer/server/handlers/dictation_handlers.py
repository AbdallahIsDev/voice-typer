"""Dictation IPC handler mixin: toggle_dictation, undo_last.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import _validate_dict_payload


class DictationHandlersMixin(HandlerBase):
    """Mixin: dictation IPC handlers (toggle_dictation / undo_last).

    CR-20: this mixin is one of the four "representative" handlers
    migrated to :meth:`HandlerBase._respond_with_error` for the
    catch-all ``except Exception`` path. See
    ``voice_typer/server/handlers/_base.py`` for the migration plan.
    """

    # The ``service`` / ``app`` / ``_send`` annotations are
    # inherited from :class:`HandlerMixinBase` — no per-mixin
    # re-declaration needed (the duplicate block removed here was one
    # of four that the R4-F3 centralization refactor missed).

    def _handle_toggle_dictation(self, data, resp) -> dict | None:
        """Handle the ``toggle_dictation`` IPC command."""
        try:
            # IPC-3: even though ``toggle_dictation`` reads no fields
            # from ``data``, invoke ``_validate_dict_payload`` with an
            # empty schema so the ADR-0020 §2 claim ("every handler
            # re-validates via _validate_dict_payload") holds and a
            # non-dict payload (e.g. ``{"data": "not-a-dict"}`` — a
            # protocol violation) is rejected with ``invalid_payload``
            # rather than silently accepted.
            #
            # Pre-coerce ``None`` (the value ``msg.get("data")``
            # returns when the ``data`` key is absent, as in
            # ``{"id": 1, "type": "toggle_dictation"}`` — a common
            # shape in existing tests and Electron callers that omit
            # the ``data`` key for no-arg commands) to ``{}`` so the
            # validation passes cleanly. Without this pre-coercion,
            # every existing caller that omits ``data`` would get
            # ``invalid_payload`` instead of the expected ``ack``.
            if data is None:
                data = {}
            _, error = _validate_dict_payload(data, {})
            if error:
                return error
            self.service.toggle_dictation()
            resp["type"] = "ack"
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "toggle_dictation")
        return resp

    def _handle_undo_last(self, data, resp) -> dict | None:
        """Handle the ``undo_last`` IPC command."""
        # UX-003: undo last transcription via backspace keystrokes
        try:
            self.service.undo_last()
            resp["type"] = "ack"
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "undo_last")
        return resp

    def _handle_force_cancel_transcription(self, data, resp) -> dict | None:
        """Handle the ``force_cancel_transcription`` IPC command (PR-2 Finding #3).

        Calls ``service.force_cancel_transcription()`` which invokes
        ``_force_recover_from_stuck_transcription(force=True)`` to
        reset the busy flag and tray state.  Gives the user a manual
        escape hatch when the automatic 3×90s watchdog timeout is too
        slow.
        """
        try:
            result = self.service.force_cancel_transcription()
            resp["type"] = "force_cancel_transcription_result"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "force_cancel_transcription")
        return resp
