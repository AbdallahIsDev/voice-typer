"""Unit tests for ``MicrophoneHandlersMixin`` (CR-12).

Covers the 1 microphone-listing IPC handler defined in
``voice_typer/server/handlers/microphone_handlers.py``:

- ``_handle_get_microphones`` — returns ``{type: microphones, data: <list>}``.

The handler is a thin pass-through to the service layer with the
standard try/except that converts any exception into an
``{type: error, data: {message: str(e)}}`` response.

UE-15 (2026-07-30): ``_handle_refresh_microphones`` was deleted — the
``refresh_microphones`` command was dropped from ``_COMMAND_REGISTRY``
and the renderer allowlist during the Tauri migration. The
corresponding ``TestRefreshMicrophones`` class was removed in lockstep.
"""

from __future__ import annotations


class TestGetMicrophones:
    """``_handle_get_microphones`` — returns the cached mic list."""

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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
