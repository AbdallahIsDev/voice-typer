"""IPC dispatch tests for device-polling commands."""

from tests.server.conftest import (  # noqa: F401
    mock_app,
    server,
)


class TestDispatchGetMicrophones:
    def test_returns_microphone_list(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "get_microphones"})
        assert result["type"] == "microphones"
        assert result["id"] == 1
        assert len(result["data"]) == 2
        assert result["data"][0]["name"] == "Microphone (Realtek Audio)"


class TestDispatchGetVolumeBackendStatus:
    """Tests for the get_volume_backend_status IPC handler."""

    def test_returns_backend_name_and_availability(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        assert result["type"] == "volume_backend_status"
        assert result["id"] == 1
        data = result["data"]
        assert data["available"] is True
        assert data["name"] == "fake (test)"
        assert data["supports_per_session"] is False
        import sys as _sys

        assert data["is_windows"] == (_sys.platform == "win32")

    def test_calls_initialize_to_detect_backend(self, server, mock_app):
        """The handler should call initialize() so the backend name is"""
        mock_app._volume_ducker.initialize.reset_mock()
        server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        mock_app._volume_ducker.initialize.assert_called_once()

    def test_handles_missing_volume_ducker_gracefully(self, server, mock_app):
        """If the app doesn't have a _volume_ducker (e.g. during early"""
        del mock_app._volume_ducker
        result = server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        assert result["type"] == "volume_backend_status"
        data = result["data"]
        assert data["available"] is False
        assert data["name"] == "disabled"
        assert data["supports_per_session"] is False

    def test_handles_initialize_exception(self, server, mock_app):
        """If initialize() raises (e.g. backend init fails), the handler"""
        mock_app._volume_ducker.initialize.side_effect = RuntimeError("init failed")
        result = server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        # Should NOT be an error response, best-effort status.
        assert result["type"] == "volume_backend_status"
        data = result["data"]
        # Backend name still comes through.
        assert data["name"] == "fake (test)"
