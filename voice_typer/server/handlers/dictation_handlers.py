"""Dictation IPC handler mixin: toggle_dictation, undo_last."""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.ipc.validation import ResponseEnvelope, _validate_dict_payload


class DictationHandlersMixin(HandlerBase):
    """Mixin: dictation IPC handlers (toggle_dictation / undo_last)."""

    # The ``service`` / ``app`` / ``_send`` annotations are

    def _handle_toggle_dictation(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``toggle_dictation`` IPC command."""
        try:
            # empty schema so the ADR-0020 §2 claim ("every handler
            if data is None:
                data = {}
            _, error = _validate_dict_payload(data, {})
            if error:
                return error
            self.service.toggle_dictation()
            resp["type"] = "ack"
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "toggle_dictation")
        return resp

    def _handle_undo_last(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``undo_last`` IPC command."""
        # undo last transcription via backspace keystrokes
        try:
            self.service.undo_last()
            resp["type"] = "ack"
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "undo_last")
        return resp

    def _handle_force_cancel_transcription(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``force_cancel_transcription`` IPC command ( Finding #3)."""
        try:
            result = self.service.force_cancel_transcription()
            resp["type"] = "force_cancel_transcription_result"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "force_cancel_transcription")
        return resp
