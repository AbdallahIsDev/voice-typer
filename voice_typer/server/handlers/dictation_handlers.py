"""Dictation IPC handler mixin: toggle_dictation, undo_last.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.ipc_server import (
    _validate_dict_payload,
    log,
)


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
        except Exception as e:
            log.error("[IPC] force_cancel_transcription failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
