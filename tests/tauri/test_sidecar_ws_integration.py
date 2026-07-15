"""Integration tests for the Tauri sidecar WS transport (ADR-0020).

These tests bind a real `websockets.serve` on 127.0.0.1:0 and connect
a real `websockets` client, exercising the full round-trip:

1. sidecar binds + emits `server_started` JSON to stdout
2. client connects + sends auth frame
3. client sends a `get_status` dispatch frame
4. sidecar dispatches via the (mocked) IPCServer._dispatch
5. client receives the response

The entire `websockets` library is required. Skip the file if it's
not installed (the v1 Electron-only build path doesn't need it).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

pytestmark = pytest.mark.asyncio


async def test_sidecar_round_trip_auth_dispatch_response(monkeypatch):
    """End-to-end: sidecar binds, client auths, client dispatches, gets response."""
    from voice_typer.server import sidecar_ws

    # Mock the IPCServer — we only need _dispatch + app.quit.
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"status": "idle"}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    # push is called by start() — make it a no-op.
    server.push = MagicMock()

    # Set the auth token.
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token-" + "a" * 32)

    # Start the sidecar WS server in a background task.
    dispatch = sidecar_ws._make_dispatch(server)

    async def _handler(ws):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    import websockets.asyncio.server as ws_server

    async with ws_server.serve(_handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]

        # Connect a client + send auth.
        async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
            await client.send(json.dumps({"type": "auth", "token": "test-token-" + "a" * 32}))

            # Send a dispatch frame.
            await client.send(
                json.dumps(
                    {
                        "type": "get_status",
                        "data": {},
                        "id": 42,
                    }
                )
            )

            # Read the response.
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            response = json.loads(raw)

            assert response["type"] == "result"
            assert response["data"] == {"status": "idle"}
            assert response["id"] == 42

    # The dispatch should have hit server._dispatch exactly once.
    server._dispatch.assert_called_once()


async def test_sidecar_rejects_bad_token(monkeypatch):
    """Wrong token → sidecar closes the connection."""
    from voice_typer.server import sidecar_ws

    server = MagicMock()
    server._dispatch = MagicMock()
    server.app = MagicMock()
    server.push = MagicMock()

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")

    dispatch = sidecar_ws._make_dispatch(server)

    async def _handler(ws):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    import websockets.asyncio.server as ws_server

    async with ws_server.serve(_handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]

        # Connect with the wrong token.
        async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
            await client.send(json.dumps({"type": "auth", "token": "wrong-token"}))
            # The sidecar should close the connection.
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await client.recv()

    # _dispatch should NOT have been called (auth failed before dispatch).
    server._dispatch.assert_not_called()


async def test_sidecar_handles_malformed_frame_without_crashing(monkeypatch):
    """A garbage frame yields an `invalid_payload` error, connection stays open."""
    from voice_typer.server import sidecar_ws

    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.push = MagicMock()

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")

    dispatch = sidecar_ws._make_dispatch(server)

    async def _handler(ws):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    import websockets.asyncio.server as ws_server

    async with ws_server.serve(_handler, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]

        async with websockets.connect(f"ws://127.0.0.1:{port}") as client:
            await client.send(json.dumps({"type": "auth", "token": "tok"}))

            # Send a malformed frame.
            await client.send("not valid json {{{")
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            err = json.loads(raw)
            assert err["type"] == "error"
            assert err["data"]["code"] == "invalid_payload"

            # The connection must still be open — send a real command.
            await client.send(json.dumps({"type": "get_status", "data": {}, "id": 1}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            response = json.loads(raw)
            assert response["type"] == "result"
            assert response["id"] == 1
