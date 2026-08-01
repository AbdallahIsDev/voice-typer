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
``{type: error, data: {code: "server.internal_error", message: "internal error"}}``.
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
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


# typed cloud/LLM exception → IPC error code mapping ────────────


class TestCloudErrorMapping:
    """PI-17: when the service raises a typed ``CloudEngineError``
    subclass (e.g. ``CloudAuthError`` from a 401 cloud response), the
    dictation handler's catch-all routes the exception through
    ``HandlerBase._respond_with_error`` which ``isinstance``-checks
    the type and emits the matching namespaced IPC error code
    (``server.cloud_auth_failed`` / ``server.cloud_rate_limited`` /
    ``server.cloud_server_error`` / ``server.cloud_network_error`` /
    ``server.cloud_config_error`` / ``server.cloud_engine_error``).

    The fallback for a non-cloud ``RuntimeError`` (e.g. "mic in use")
    stays as ``server.internal_error`` — that's the existing CR-20
    behavior and remains correct for non-cloud errors.
    """

    def test_cloud_auth_error_maps_to_specific_code(self, ipc_server, fake_service):
        """A ``CloudAuthError`` from the service produces
        ``{code: "server.cloud_auth_failed", message: "cloud API key
        invalid or revoked"}`` — NOT the generic
        ``server.internal_error`` envelope.
        """
        from voice_typer.server.asr_errors import CloudAuthError

        fake_service.toggle_dictation.side_effect = CloudAuthError("401 from cloud provider")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.cloud_auth_failed"
        assert resp["data"]["message"] == "cloud API key invalid or revoked"

    def test_cloud_rate_limit_error_maps_to_specific_code(self, ipc_server, fake_service):
        from voice_typer.server.asr_errors import CloudRateLimitError

        fake_service.toggle_dictation.side_effect = CloudRateLimitError("429 from cloud provider")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.cloud_rate_limited"

    def test_cloud_network_error_maps_to_specific_code(self, ipc_server, fake_service):
        from voice_typer.server.asr_errors import CloudNetworkError

        fake_service.toggle_dictation.side_effect = CloudNetworkError("URLError: timeout")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.cloud_network_error"

    def test_cloud_config_error_maps_to_specific_code(self, ipc_server, fake_service):
        from voice_typer.server.asr_errors import CloudConfigError

        fake_service.toggle_dictation.side_effect = CloudConfigError("missing API key")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.cloud_config_error"

    def test_cloud_server_error_maps_to_specific_code(self, ipc_server, fake_service):
        from voice_typer.server.asr_errors import CloudServerError

        fake_service.toggle_dictation.side_effect = CloudServerError("503 from cloud provider")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.cloud_server_error"

    def test_cloud_engine_error_base_maps_to_specific_code(self, ipc_server, fake_service):
        """The typed base ``CloudEngineError`` (raised when the HTTP
        status doesn't fit one of the specific subclasses — e.g. 4xx
        other than 401/403/429) maps to ``server.cloud_engine_error``,
        NOT the generic ``server.internal_error``.
        """
        from voice_typer.server.asr_errors import CloudEngineError

        fake_service.toggle_dictation.side_effect = CloudEngineError("unknown cloud failure")
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.cloud_engine_error"

    def test_consent_required_error_maps_to_consent_code(self, ipc_server, fake_service):
        """A ``ConsentRequiredError`` from the service produces
        ``{code: "client.consent_required", ...}`` plus the structured
        consent fields (engine_name, consent_field, model_id) so the
        renderer can surface a consent dialog deep-linked to the exact
        toggle in Settings (NEW-PRIV-006).
        """
        from voice_typer.server.asr_errors import ConsentRequiredError

        fake_service.toggle_dictation.side_effect = ConsentRequiredError(
            "Cloud openai consent not given",
            engine_name="openai",
            consent_field="cloud_openai_consent",
            model_id=None,
        )
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.consent_required"
        assert resp["data"]["engine_name"] == "openai"
        assert resp["data"]["consent_field"] == "cloud_openai_consent"
