"""IPC dispatch-layer regression tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from voice_typer.server.ipc_server import IPCServer


# _dispatch stamps request id on validation-error responses ──


def _make_server() -> IPCServer:
    """Build a minimal IPCServer instance for _dispatch unit tests."""
    from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

    server = make_bare_ipc_server()
    server.app._shutting_down = False
    return server


class TestRequestIdPreservedOnValidationErrors:
    """every response, including validation-error responses that bypass"""

    def test_validation_error_preserves_request_id(self) -> None:
        """A handler that returns a validation-error dict (no id)"""
        server = _make_server()
        # Pick a registered command whose handler uses
        msg = {
            "type": "onboarding_set_microphone",
            "id": 4242,
            "data": {"mic_id": 12345},  # int → validation rejection
        }
        result = server._dispatch(msg)
        assert result is not None, ": _dispatch must return a response dict"
        assert result.get("id") == 4242, (
            ": validation-error response MUST carry the inbound "
            "request id so the renderer can correlate the rejection. "
            f"Got: {result!r}"
        )
        assert result.get("type") == "error"
        # The validation error code should be present (namespaced form
        data = result.get("data", {})
        assert data.get("code") == "client.invalid_field"
        assert data.get("field") == "mic_id"

    def test_validation_error_preserves_string_request_id(self) -> None:
        """The id can be a string (JSON-RPC style), preserved too."""
        server = _make_server()
        msg = {
            "type": "onboarding_set_microphone",
            "id": "req-abc-001",
            "data": {"mic_id": False},  # bool → validation rejection
        }
        result = server._dispatch(msg)
        assert result is not None
        assert result.get("id") == "req-abc-001"

    def test_no_id_no_stamp(self) -> None:
        """If the inbound message has no ``id``, the response has no"""
        server = _make_server()
        msg = {
            "type": "onboarding_set_microphone",
            # no "id" key
            "data": {"mic_id": 12345},  # int → validation rejection
        }
        result = server._dispatch(msg)
        assert result is not None
        assert "id" not in result, ": when the inbound message has no id, the response must not synthesize one either."

    def test_successful_response_preserves_request_id(self) -> None:
        """A handler that mutates ``resp`` directly (the normal path)"""
        server = _make_server()
        # ``service.onboarding_is_first_run`` returns a dict (no error
        server.service.onboarding_is_first_run.return_value = {"is_first_run": True}
        msg = {"type": "onboarding_is_first_run", "id": 99}
        result = server._dispatch(msg)
        assert result is not None
        assert result.get("id") == 99
        assert result.get("type") == "onboarding_first_run"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
