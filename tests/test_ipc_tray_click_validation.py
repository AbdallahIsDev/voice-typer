"""regression tests: ``_handle_tray_click`` uses"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer


@pytest.fixture
def server():
    """Build a minimal IPCServer for unit-testing ``_handle_tray_click``."""
    app = MagicMock()
    return IPCServer(app)


def _base_resp():
    """The default response dict handlers receive from ``_dispatch``."""
    return {"type": "result", "id": 1, "data": {}}


class TestTrayClickUsesValidateDictPayload:
    """``_handle_tray_click`` must use ``_validate_dict_payload``."""

    def test_handler_source_uses_validate_dict_payload(self):
        """The source of ``_handle_tray_click`` must call"""
        src = inspect.getsource(IPCServer._handle_tray_click)
        assert "_validate_dict_payload" in src, (
            "_handle_tray_click must delegate validation to "
            "_validate_dict_payload. Found inline isinstance check "
            "instead, this bypasses the shared error envelope."
        )
        # The old inline check must NOT be present.
        assert 'isinstance(data, dict) or "id" not in data' not in src, (
            "_handle_tray_click must NOT use the inline isinstance check, "
            "it produces only missing_field for all bad inputs and bypasses "
            "the type check."
        )

    def test_handler_source_declares_id_as_str_required(self):
        """The schema passed to ``_validate_dict_payload`` must require"""
        src = inspect.getsource(IPCServer._handle_tray_click)
        assert '"id": {"type": str, "required": True}' in src or ('"id": {"type": str, "required": True,}' in src), (
            "_handle_tray_click schema must declare id as a required str."
        )


class TestTrayClickErrorEnvelopes:
    """each bad-input case must return the correct error envelope."""

    def test_non_dict_data_returns_invalid_payload(self, server):
        """When ``data`` is not a dict (e.g. a string), the response must"""
        resp = _base_resp()
        result = server._handle_tray_click("not a dict", resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_payload", (
            "Non-dict data must return invalid_payload (CR-12). The old "
            "inline check returned missing_field for this case, conflating "
            "'malformed request' with 'missing field'."
        )

    def test_missing_id_returns_missing_field(self, server):
        """When ``data`` is a dict but lacks ``id``, the response must"""
        resp = _base_resp()
        result = server._handle_tray_click({}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.missing_field"
        assert result["data"]["field"] == "id"

    def test_non_str_id_returns_invalid_field(self, server):
        """``int``), the response must be ``code: invalid_field``, NOT"""
        resp = _base_resp()
        result = server._handle_tray_click({"id": 42}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field", (
            "Non-str id must return invalid_field (CR-12). The old inline "
            "check let int ids slip through to tray.dispatch_tray_action."
        )
        assert result["data"]["field"] == "id"
        # The tray must NOT have been consulted.
        server.app.tray.dispatch_tray_action.assert_not_called()

    def test_none_id_returns_invalid_field(self, server):
        """``None`` is not a valid ``id``, must return ``invalid_field``."""
        resp = _base_resp()
        result = server._handle_tray_click({"id": None}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field"

    def test_list_id_returns_invalid_field(self, server):
        """A list is not a valid ``id``, must return ``invalid_field``."""
        resp = _base_resp()
        result = server._handle_tray_click({"id": ["a", "b"]}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field"


class TestTrayClickHappyPath:
    """a valid ``id`` must still reach ``tray.dispatch_tray_action``."""

    def test_valid_id_dispatches_to_tray(self, server):
        """When ``data == {\"id\": \"show_window\"}`` and the tray"""
        server.app.tray.dispatch_tray_action.return_value = True
        resp = _base_resp()
        result = server._handle_tray_click({"id": "show_window"}, resp)
        assert result == {"type": "result", "data": {"ok": True}}
        server.app.tray.dispatch_tray_action.assert_called_once_with("show_window")

    def test_unknown_id_returns_unknown_tray_item(self, server):
        """When the tray doesn't recognise the id, the handler must"""
        server.app.tray.dispatch_tray_action.return_value = False
        resp = _base_resp()
        result = server._handle_tray_click({"id": "nonsense"}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_tray_item"
        assert result["data"]["id"] == "nonsense"

    def test_no_tray_returns_unknown_tray_item(self, server):
        """When ``app.tray`` is None, the handler must return"""
        server.app.tray = None
        resp = _base_resp()
        result = server._handle_tray_click({"id": "anything"}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_tray_item"

    def test_tray_without_dispatch_method_returns_unknown_tray_item(self, server):
        """When ``app.tray`` exists but lacks ``dispatch_tray_action``"""
        server.app.tray = MagicMock(spec=[])  # no methods
        resp = _base_resp()
        result = server._handle_tray_click({"id": "anything"}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_tray_item"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
