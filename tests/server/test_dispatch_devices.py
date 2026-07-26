"""IPC dispatch tests for device-polling commands.

Classes:
- TestDispatchGetMicrophones         — get_microphones dispatcher
- TestDispatchGetVolumeBackendStatus — get_volume_backend_status dispatcher

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

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
    """Tests for the get_volume_backend_status IPC handler.

    This endpoint powers the Settings UI's "Volume Backend" status
    indicator and the gating of the Per-Session Duck toggle on
    non-Windows platforms.  See architecture doc §7.9.
    """

    def test_returns_backend_name_and_availability(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        assert result["type"] == "volume_backend_status"
        assert result["id"] == 1
        data = result["data"]
        assert data["available"] is True
        assert data["name"] == "fake (test)"
        assert data["supports_per_session"] is False
        # is_windows reflects the test runner's platform
        import sys as _sys

        assert data["is_windows"] == (_sys.platform == "win32")

    def test_calls_initialize_to_detect_backend(self, server, mock_app):
        """The handler should call initialize() so the backend name is
        populated even before the user starts their first dictation."""
        mock_app._volume_ducker.initialize.reset_mock()
        server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        mock_app._volume_ducker.initialize.assert_called_once()

    def test_handles_missing_volume_ducker_gracefully(self, server, mock_app):
        """If the app doesn't have a _volume_ducker (e.g. during early
        startup before __init__ completes), the handler should return
        a 'disabled' status rather than crashing."""
        del mock_app._volume_ducker
        result = server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        assert result["type"] == "volume_backend_status"
        data = result["data"]
        assert data["available"] is False
        assert data["name"] == "disabled"
        assert data["supports_per_session"] is False

    def test_handles_initialize_exception(self, server, mock_app):
        """If initialize() raises (e.g. backend init fails), the handler
        should still return a valid response using is_available=False."""
        mock_app._volume_ducker.initialize.side_effect = RuntimeError("init failed")
        result = server._dispatch({"id": 1, "type": "get_volume_backend_status"})
        # Should NOT be an error response — best-effort status.
        assert result["type"] == "volume_backend_status"
        data = result["data"]
        # Backend name still comes through.
        assert data["name"] == "fake (test)"
