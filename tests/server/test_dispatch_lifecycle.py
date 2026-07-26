"""IPC dispatch tests for lifecycle / control commands.

Classes:
- TestDispatchToggleDictation       — toggle_dictation dispatcher
- TestDispatchRestartApp            — restart_app dispatcher
- TestDispatchQuitApp               — quit_app dispatcher
- TestDispatchUnknownCommand        — unknown command handling
- TestDispatchNoId                  — commands without an id field
- TestDispatchNonDictDataRobustness — TEST-039 non-dict data handling

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401
    mock_app,
    server,
)


class TestDispatchToggleDictation:
    def test_calls_toggle_and_returns_ack(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        # NEW-IPC-006: ack responses now always include ``data: {}`` for
        # shape consistency.  Previously this returned just
        # ``{"id": 1, "type": "ack"}`` with no data, forcing the renderer
        # to defensively guard against ``undefined``.
        assert result == {"id": 1, "type": "ack", "data": {}}
        assert mock_app.toggle_called is True

    def test_exception_returns_error_response(self, server, mock_app):
        """toggle_dictation raising an exception should return error, not crash."""

        def failing_toggle():
            raise RuntimeError("toggle failed")

        mock_app.toggle_dictation = failing_toggle
        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result["type"] == "error"
        assert result["id"] == 1
        # PVT-G5-021 (CR-20): handler now uses _respond_with_error which emits
        # a generic envelope (code=internal_error, message=internal error) to
        # avoid leaking str(e) to the renderer.
        assert result["data"]["code"] == "server.internal_error"
        assert result["data"]["message"] == "internal error"


class TestDispatchRestartApp:
    def test_calls_restart_and_returns_ack(self, server, mock_app):
        server._send = MagicMock()
        result = server._dispatch({"id": 1, "type": "restart_app"})
        # Returns None because ack was already sent
        assert result is None
        # NEW-IPC-006: ack now includes explicit ``data: {}``.
        server._send.assert_called_once_with({"id": 1, "type": "ack", "data": {}})
        assert mock_app.restart_called is True


class TestDispatchQuitApp:
    def test_calls_quit_and_returns_ack(self, server, mock_app):
        server._send = MagicMock()
        result = server._dispatch({"id": 1, "type": "quit_app"})
        assert result is None
        server._send.assert_called_once_with({"id": 1, "type": "ack", "data": {}})
        assert mock_app.quit_called is True


class TestDispatchUnknownCommand:
    def test_returns_error(self, server):
        result = server._dispatch({"id": 1, "type": "frobnicate"})
        assert result["type"] == "error"
        assert result["id"] == 1
        assert "Unknown command" in result["data"]["message"]
        assert "frobnicate" in result["data"]["message"]


class TestDispatchNoId:
    def test_push_event_no_id_in_response(self, server):
        """Commands with no id should still work and omit id from response."""
        result = server._dispatch({"type": "get_status"})
        assert "id" not in result
        assert result["type"] == "status"

    def test_unknown_no_id(self, server):
        result = server._dispatch({"type": "frobnicate"})
        assert "id" not in result
        assert result["type"] == "error"


class TestDispatchNonDictDataRobustness:
    """TEST-039: _dispatch must handle non-dict `data` gracefully for
    every command, not just set_config. Previously the audit noted that
    ``data = msg.get("data")`` could be a list, string, or None, and
    only set_config had an isinstance guard. We now test multiple
    commands with non-dict data to verify they don't raise.
    """

    def test_get_history_with_list_data_does_not_crash(self, server, mock_app):
        """get_history with data=[1,2,3] should fall back to defaults."""
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        result = server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": [1, 2, 3],
            }
        )
        assert result["type"] == "history"
        # Default limit=50, offset=0 should be used
        mock_app.history_db.get_recent.assert_called_once()

    def test_get_history_with_string_data_does_not_crash(self, server, mock_app):
        """get_history with data="bad" should fall back to defaults."""
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        result = server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": "bad",
            }
        )
        assert result["type"] == "history"

    def test_get_history_with_none_data_does_not_crash(self, server, mock_app):
        """get_history with data=None should fall back to defaults."""
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        result = server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": None,
            }
        )
        assert result["type"] == "history"

    def test_delete_history_with_non_dict_data_returns_error(self, server, mock_app):
        """delete_history with data=[1,2] should return an error, not crash."""
        result = server._dispatch(
            {
                "id": 1,
                "type": "delete_history",
                "data": [1, 2],
            }
        )
        assert result["type"] == "error"
        # ADR-0008 refactor: _validate_dict_payload now returns a structural
        # "data must be an object" message for non-dict input (previously it
        # returned "Missing 'id'" because the old validator only checked for
        # the 'id' key after assuming dict-ness). Both messages are valid
        # error responses; the new one is more precise about the root cause.
        assert "data must be an object" in result["data"]["message"]

    def test_toggle_favorite_with_string_data_returns_error(self, server, mock_app):
        """toggle_favorite with data="bad" should return an error."""
        result = server._dispatch(
            {
                "id": 1,
                "type": "toggle_favorite",
                "data": "bad",
            }
        )
        assert result["type"] == "error"
        assert "data must be an object" in result["data"]["message"]

    def test_search_history_with_list_data_does_not_crash(self, server, mock_app):
        """search_history with data=[1,2] should fall back to empty query."""
        mock_app.history_db.search = MagicMock(return_value=[])
        result = server._dispatch(
            {
                "id": 1,
                "type": "search_history",
                "data": [1, 2],
            }
        )
        assert result["type"] == "history"
