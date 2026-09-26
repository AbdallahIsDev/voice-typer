"""Integration tests for the Tauri sidecar WS transport (ADR-0020)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc.validation import ErrorCodes

from tests.fixtures.ipc_test_helpers import make_fake_sidecar_ws_server

websockets = pytest.importorskip("websockets")

pytestmark = pytest.mark.asyncio


async def test_sidecar_round_trip_auth_dispatch_response(monkeypatch):
    """End-to-end: sidecar binds, client auths, client dispatches, gets response."""
    from voice_typer.server import sidecar_ws

    server = make_fake_sidecar_ws_server(
        _dispatch=MagicMock(return_value={"type": "result", "data": {"status": "idle"}})
    )

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
    """Wrong token → sidecar sends ``auth_failed`` error frame, then closes."""
    from voice_typer.server import sidecar_ws

    server = make_fake_sidecar_ws_server()

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
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            err = json.loads(raw)
            assert err["type"] == "error"
            assert err["data"]["code"] == ErrorCodes.AUTH_FAILED
            # The next recv() MUST raise ConnectionClosed (the sidecar
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await client.recv()

    # _dispatch should NOT have been called (auth failed before dispatch).
    server._dispatch.assert_not_called()


async def test_sidecar_handles_malformed_frame_without_crashing(monkeypatch):
    """A garbage frame yields an `invalid_payload` error, connection stays open."""
    from voice_typer.server import sidecar_ws

    server = make_fake_sidecar_ws_server()

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
            # The dispatch loop emits the namespaced form (``client.*``);
            assert err["data"]["code"] == "client.invalid_payload"

            # The connection must still be open, send a real command.
            await client.send(json.dumps({"type": "get_status", "data": {}, "id": 1}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            response = json.loads(raw)
            assert response["type"] == "result"
            assert response["id"] == 1
