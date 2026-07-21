"""Unit tests for ``DictationHandlersMixin`` (CR-12).

Covers the 3 dictation IPC handlers defined in
``voice_typer/server/handlers/dictation_handlers.py``:

- ``_handle_toggle_dictation`` — start/stop the recording loop.
- ``_handle_undo_last`` — undo the last transcription via backspace keystrokes.
- ``_handle_force_cancel_transcription`` — force-reset a stuck
  transcription (PR-2 Finding #3 — manual escape hatch when the
  3×90s watchdog timeout is too slow).

All three handlers delegate to the service layer and return either
``{type: ack}`` (toggle/undo) or ``{type: <cmd>_result, data: <result>}``
(force_cancel).  Each has a service-raises path that produces the
CR-20 generic WS-path error envelope
``{type: error, data: {code: "internal_error", message: "internal error"}}``.
"""

from __future__ import annotations


class TestToggleDictation:
    """``_handle_toggle_dictation`` — start/stop the recording loop."""

    def test_happy_path_returns_ack(self, ipc_server, fake_service):
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "ack"
        fake_service.toggle_dictation.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.toggle_dictation.side_effect = RuntimeError("mic in use")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"


class TestUndoLast:
    """``_handle_undo_last`` — undo the last transcription."""

    def test_happy_path_returns_ack(self, ipc_server, fake_service):
        resp = ipc_server._handle_undo_last({}, {})
        assert resp["type"] == "ack"
        fake_service.undo_last.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.undo_last.side_effect = RuntimeError("nothing to undo")
        resp = ipc_server._handle_undo_last({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"


class TestForceCancelTranscription:
    """``_handle_force_cancel_transcription`` — manual escape hatch (PR-2 #3)."""

    def test_happy_path_returns_force_cancel_result(self, ipc_server, fake_service):
        fake_service.force_cancel_transcription.return_value = {
            "success": True,
            "message": "Transcription cancelled.",
        }
        resp = ipc_server._handle_force_cancel_transcription({}, {})
        assert resp["type"] == "force_cancel_transcription_result"
        assert resp["data"] == {
            "success": True,
            "message": "Transcription cancelled.",
        }
        fake_service.force_cancel_transcription.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.force_cancel_transcription.side_effect = RuntimeError("no transcription in progress")
        resp = ipc_server._handle_force_cancel_transcription({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_failure_result_is_passed_through_not_converted_to_error(self, ipc_server, fake_service):
        """A ``{success: False}`` return value is NOT converted to an error
        response — the renderer distinguishes "cancel succeeded" from
        "cancel failed but the IPC call worked" using ``data.success``.
        """
        fake_service.force_cancel_transcription.return_value = {
            "success": False,
            "message": "Nothing to cancel.",
        }
        resp = ipc_server._handle_force_cancel_transcription({}, {})
        assert resp["type"] == "force_cancel_transcription_result", (
            "a False-success result must stay as the *_result type, not be converted to an error response"
        )
        assert resp["data"]["success"] is False
