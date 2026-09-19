"""IPC dispatch tests for lifecycle / control commands."""

from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401
    mock_app,
    server,
)


class TestDispatchToggleDictation:
    def test_calls_toggle_and_returns_ack(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "toggle_dictation"})
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
        # (): handler now uses _respond_with_error which emits
        assert result["data"]["code"] == "server.internal_error"
        assert result["data"]["message"] == "internal error"


class TestDispatchRestartApp:
    def test_calls_restart_and_returns_ack(self, server, mock_app):
        server._send = MagicMock()
        result = server._dispatch({"id": 1, "type": "restart_app"})
        # Returns None because ack was already sent
        assert result is None
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
    """only set_config had an isinstance guard. We now test multiple"""

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
        """get_history with data=\"bad\" should fall back to defaults."""
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
        assert "data must be an object" in result["data"]["message"]

    def test_toggle_favorite_with_string_data_returns_error(self, server, mock_app):
        """toggle_favorite with data=\"bad\" should return an error."""
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
