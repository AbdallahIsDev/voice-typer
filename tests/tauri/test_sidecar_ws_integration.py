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
    # ``dispatch`` increments / decrements the in-flight counter (and
    # compares it to ints), so the mock must carry a real int, not a
    # MagicMock (which would fail the ``<= 0`` comparison).
    server._ws_inflight_count = 0
    # force the lazy-create branch in
    # ``_make_dispatch`` to run (creates a real ThreadPoolExecutor so
    # ``loop.run_in_executor`` doesn't fail the ``wrap_future``
    # isinstance assertion on a MagicMock.submit() return).
    server._ws_dispatch_pool = None

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
    """Wrong token → sidecar sends ``auth_failed`` error frame, then closes.

    RT-FIX-9 / EC-11 (cross-transport parity, 2026-07-24): the WS path
    now mirrors the TCP path's ``auth_failed`` error frame BEFORE
    closing the WS with code 1008. Pre-EC-FIX-3 the WS path closed with
    1008 and sent NO error frame; now both transports emit the same
    ``{"type":"error","data":{"code":"auth_failed",...}}`` frame before
    closing so clients can branch on the error code without sniffing
    the close reason.
    """
    from voice_typer.server import sidecar_ws

    server = MagicMock()
    server._dispatch = MagicMock()
    server.app = MagicMock()
    server.push = MagicMock()
    # force the lazy-create branch in
    # ``_make_dispatch`` to run.
    server._ws_dispatch_pool = None

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
            # the sidecar now sends an ``auth_failed``
            # error frame BEFORE closing the WS with code 1008. Read
            # the error frame first, then expect the connection to
            # close on the next recv().
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            err = json.loads(raw)
            assert err["type"] == "error"
            assert err["data"]["code"] in ("auth_failed", "server.auth_failed")
            # The next recv() MUST raise ConnectionClosed (the sidecar
            # closed the WS with code 1008 after sending the error frame).
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
    # ``dispatch`` increments / decrements the in-flight counter (and
    # compares it to ints), so the mock must carry a real int, not a
    # MagicMock (which would fail the ``<= 0`` comparison).
    server._ws_inflight_count = 0
    # force the lazy-create branch in
    # ``_make_dispatch`` to run.
    server._ws_dispatch_pool = None

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
            # the bare-form ``legacy_code`` alias was removed once the
            # renderer migrated to the namespaced form.
            assert err["data"]["code"] == "client.invalid_payload"

            # The connection must still be open — send a real command.
            await client.send(json.dumps({"type": "get_status", "data": {}, "id": 1}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            response = json.loads(raw)
            assert response["type"] == "result"
            assert response["id"] == 1
