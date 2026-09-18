"""IPC dispatch-layer regression tests.

This file is the regression home for the request-id stamping fix:
``_dispatch`` now stamps the inbound request ``id``
onto the response envelope so clients using id-based request/response
correlation (the standard JSON-RPC-like pattern in the renderer
bridge) can match the response back to the originating request. Pre-fix,
``_validate_dict_payload`` returned a FRESH error-envelope dict with
no ``id`` field; every handler that did ``if error: return error``
discarded the ``resp`` dict (which had ``id`` pre-populated), so
validation rejections orphaned the pending request and the renderer
would time out instead of resolving the rejection.

Post-predecessor / post-TCP: the former ``_accept_tcp`` pool-race pins
and the predecessor ``ALLOWED_COMMANDS`` coverage gate were deleted with
the TS shell and TCP transport. Command parity lives in
``tests/test_ipc_command_parity.py`` (Python registry ↔ Rust allowlist).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from voice_typer.server.ipc_server import IPCServer


# _dispatch stamps request id on validation-error responses ──


def _make_server() -> IPCServer:
    """Build a minimal IPCServer instance for _dispatch unit tests.

    Uses the canonical bare ``__new__`` bypass (skips ``__init__``,
    which would construct a real VoiceTyperService and try to wire
    ``app.tray.set_state``): the factory supplies the fake app/service
    and the ``_dispatch_lock``; the only local adjustment is the
    explicit ``_shutting_down = False`` bool so any shutdown gate sees
    a real ``False`` instead of a truthy child mock.
    """
    from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

    server = make_bare_ipc_server()
    server.app._shutting_down = False
    return server


class TestRequestIdPreservedOnValidationErrors:
    """``_dispatch`` stamps the inbound request ``id`` on
    every response, including validation-error responses that bypass
    the ``resp`` dict pre-populated with ``id``.

    Pre-fix, ``_validate_dict_payload`` returned a FRESH error-envelope
    dict with no ``id`` field; every handler that did
    ``if error: return error`` discarded the ``resp`` dict, so
    validation rejections orphaned the pending request and the
    renderer's request/response correlation would time out instead of
    resolving the rejection.
    """

    def test_validation_error_preserves_request_id(self) -> None:
        """A handler that returns a validation-error dict (no id)
        still has ``id`` stamped by ``_dispatch`` before the response
        is sent."""
        server = _make_server()
        # Pick a registered command whose handler uses
        # ``_validate_dict_payload`` and returns the error directly.
        # ``onboarding_set_microphone`` validates ``mic_id`` is a str
        # or None, passing an int triggers the validation error path.
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
        # primary, legacy alias retained for backward compat).
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
        """If the inbound message has no ``id``, the response has no
        ``id`` either (push events / fire-and-forget notifications)."""
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
        """A handler that mutates ``resp`` directly (the normal path)
        keeps the id that ``_dispatch`` pre-populated."""
        server = _make_server()
        # ``service.onboarding_is_first_run`` returns a dict (no error
        # key), handler returns ``resp`` after mutation. ``resp`` was
        # pre-populated with id by ``_dispatch``.
        server.service.onboarding_is_first_run.return_value = {"is_first_run": True}
        msg = {"type": "onboarding_is_first_run", "id": 99}
        result = server._dispatch(msg)
        assert result is not None
        assert result.get("id") == 99
        assert result.get("type") == "onboarding_first_run"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
