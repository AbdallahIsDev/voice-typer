"""
EC-FIX-2 / EC-9 regression test: ``shutdown`` is in ``_COMMAND_REGISTRY``.
EC-FIX-2 (this review) registers ``shutdown`` in the shared dispatch
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from voice_typer.server.ipc_server import IPCServer


class TestShutdownCommandRegistry:
    """backed by a real handler method on :class:`IPCServer`."""

    def test_shutdown_is_in_command_registry(self) -> None:
        """``shutdown`` MUST be registered in :data:`_COMMAND_REGISTRY`."""
        assert "shutdown" in IPCServer._COMMAND_REGISTRY, (
            "EC-9 regression: 'shutdown' is not registered in "
            "_COMMAND_REGISTRY, the TCP/stdin transports cannot "
            "handle a cooperative shutdown request from the host."
        )

    def test_registry_maps_shutdown_to_handle_shutdown(self) -> None:
        """
        The registry entry MUST map to ``_handle_shutdown``.
        ``unknown_command``. Pinning the mapping catches that drift.
        """
        assert IPCServer._COMMAND_REGISTRY["shutdown"] == "_handle_shutdown", (
            "EC-9 regression: _COMMAND_REGISTRY['shutdown'] maps to "
            f"{IPCServer._COMMAND_REGISTRY['shutdown']!r}, expected "
            "'_handle_shutdown'. The handler name and registry entry "
            "have drifted out of sync."
        )

    def test_handle_shutdown_exists_on_ipc_server(self) -> None:
        """``IPCServer._handle_shutdown`` MUST exist as a callable."""
        assert hasattr(IPCServer, "_handle_shutdown"), (
            "EC-9 regression: IPCServer has no '_handle_shutdown' "
            "method, the registry entry points at a non-existent "
            "handler and dispatch will raise AttributeError."
        )
        assert callable(IPCServer._handle_shutdown), "EC-9 regression: IPCServer._handle_shutdown is not callable."

    def test_handle_shutdown_delegates_to_service_quit(self) -> None:
        """``_handle_shutdown`` MUST call ``self.service.quit()`` (NOT"""
        app = MagicMock()
        service = MagicMock()
        server = IPCServer(app, service=service)

        # Invoke the handler directly with the dispatch-level resp
        resp: dict = {"id": 1}
        result = server._handle_shutdown(data=None, resp=resp)

        deadline = time.monotonic() + 5.0
        while service.quit.call_count == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        service.quit.assert_called_once_with()
        # ``self.app.quit()`` must NOT be called, that's the
        app.quit.assert_not_called()

        assert result is not None, (
            "_handle_shutdown must return the resp envelope (not None) "
            "so the dispatcher sends the ack back to the host."
        )
        assert result["type"] == "result", (
            f"_handle_shutdown must set resp['type'] = 'result'; got {result.get('type')!r}."
        )
        assert result["data"] == {"ack": True}, (
            f"_handle_shutdown must set resp['data'] = {{'ack': True}}; got {result.get('data')!r}."
        )

    def test_handle_shutdown_returns_ack_even_if_service_quit_raises(self) -> None:
        """If ``service.quit()`` raises, the ack MUST still be returned."""
        app = MagicMock()
        service = MagicMock()
        service.quit.side_effect = RuntimeError("simulated teardown failure")
        server = IPCServer(app, service=service)

        resp: dict = {"id": 42}
        # Must NOT raise, the handler catches the exception, logs it
        result = server._handle_shutdown(data=None, resp=resp)

        service.quit.assert_called_once_with()
        assert result is not None
        assert result["type"] == "result"
        assert result["data"] == {"ack": True}
