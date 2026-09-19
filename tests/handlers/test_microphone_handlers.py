"""Unit tests for ``MicrophoneHandlersMixin`` (CR-12)."""

from __future__ import annotations


class TestGetMicrophones:
    """``_handle_get_microphones``, returns the cached mic list."""

    def test_happy_path_returns_microphones_type(self, ipc_server, fake_service):
        fake_service.get_microphones.return_value = [
            {"id": 0, "name": "Built-in Mic"},
            {"id": 2, "name": "USB Mic"},
        ]
        resp = ipc_server._handle_get_microphones({}, {})
        assert resp["type"] == "microphones"
        assert resp["data"] == [
            {"id": 0, "name": "Built-in Mic"},
            {"id": 2, "name": "USB Mic"},
        ]
        fake_service.get_microphones.assert_called_once_with()

    def test_empty_list_is_valid(self, ipc_server, fake_service):
        """No microphones → empty list (not an error)."""
        fake_service.get_microphones.return_value = []
        resp = ipc_server._handle_get_microphones({}, {})
        assert resp["type"] == "microphones"
        assert resp["data"] == []

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_microphones.side_effect = RuntimeError("portaudio init failed")
        resp = ipc_server._handle_get_microphones({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
