"""XZ-IPC-003 regression: WS server must enforce a concurrent-connection cap."""

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402
from voice_typer.server.ipc.validation import ErrorCodes  # noqa: E402

from tests.fixtures.sidecar_ws_test_helpers import (  # noqa: E402
    make_fake_server_with_semaphore,
    make_fake_websocket,
)


@pytest.mark.asyncio
async def test_under_cap_connection_proceeds_to_auth(monkeypatch) -> None:
    """When the semaphore has capacity, the connection proceeds to auth."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}))
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    # Auth ran (and failed) → auth_failed frame, NOT max_connections_reached.
    assert len(ws._sent_frames) == 1
    frame = json.loads(ws._sent_frames[0])
    assert frame["data"]["code"] == ErrorCodes.AUTH_FAILED, (
        f"expected auth_failed (connection was under cap), got {frame['data']['code']!r}"
    )

    # Close was called with 1008 (auth-failed close code).
    assert len(ws._closed_with) == 1
    _, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008


@pytest.mark.asyncio
async def test_at_cap_rejects_with_1008_before_auth(monkeypatch) -> None:
    """When the semaphore is exhausted, the connection is rejected with 1008."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    # Provide a valid auth frame, but it should NEVER be read because
    ws = make_fake_websocket(json.dumps({"type": "auth", "token": "good-token"}))
    # Semaphore(0) → at cap, all slots held.
    server = make_fake_server_with_semaphore(0)
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    # Exactly one error frame was sent with max_connections_reached.
    assert len(ws._sent_frames) == 1, f"expected exactly one max_connections_reached frame, got {ws._sent_frames}"
    frame = json.loads(ws._sent_frames[0])
    assert frame["type"] == "error"
    assert frame["data"]["code"] == ErrorCodes.MAX_CONNECTIONS_REACHED, (
        f"expected code='max_connections_reached', got {frame['data']['code']!r}"
    )
    assert "message" in frame["data"]

    # Close was called with code 1008.
    assert len(ws._closed_with) == 1
    _, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008, f"expected close(code=1008), got kwargs={close_kwargs}"

    # Auth was NEVER invoked, the recv mock was never awaited.
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_semaphore_released_after_auth_fail(monkeypatch) -> None:
    """After an auth-failed rejection, the semaphore slot is released."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    dispatch = MagicMock()

    ws1 = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}))
    await sidecar_ws._handle_connection(ws1, server, dispatch)

    sem = server._ws_connection_semaphore
    # The semaphore should be back to full capacity (not locked).
    assert not sem.locked(), "semaphore should be released after auth-failed connection completed"

    # A second connection should proceed normally (not be rejected).
    ws2 = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}))
    await sidecar_ws._handle_connection(ws2, server, dispatch)
    frame = json.loads(ws2._sent_frames[0])
    assert frame["data"]["code"] == ErrorCodes.AUTH_FAILED, (
        f"second connection should proceed to auth (semaphore was released), got {frame['data']['code']!r}"
    )


@pytest.mark.asyncio
async def test_semaphore_released_after_clean_disconnect(monkeypatch) -> None:
    """After a successful auth + clean disconnect, the semaphore is released."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = make_fake_websocket(json.dumps({"type": "auth", "token": "good-token"}))

    class _EmptyAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    ws.__aiter__ = lambda self: _EmptyAsyncIter()  # noqa: E731

    sent_frames: list[str] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _no_close(*args, **kwargs):
        return None

    ws.send = _track_send
    ws.close = _no_close

    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    dispatch = MagicMock()
    dispatch.return_value = None

    with contextlib.suppress(Exception):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    sem = server._ws_connection_semaphore
    assert not sem.locked(), "semaphore should be released after clean disconnect"


def test_max_ws_connections_constant_is_sane() -> None:
    """The cap must be a positive integer (sanity check on the constant)."""
    assert isinstance(sidecar_ws._MAX_WS_CONNECTIONS, int)
    assert sidecar_ws._MAX_WS_CONNECTIONS > 0
    # The cap should be at least 1 (allow one connection) and not so high
    assert 1 <= sidecar_ws._MAX_WS_CONNECTIONS <= 1024


def test_get_ws_connection_semaphore_creates_real_semaphore() -> None:
    """The helper returns a real ``asyncio.Semaphore`` even for MagicMock servers."""
    server = MagicMock()
    # The helper must NOT return that child mock.
    sem = sidecar_ws._get_ws_connection_semaphore(server)
    assert isinstance(sem, asyncio.Semaphore), f"expected a real asyncio.Semaphore, got {type(sem).__name__}"

    # A second call returns the SAME semaphore (idempotent, the helper
    sem2 = sidecar_ws._get_ws_connection_semaphore(server)
    assert sem is sem2, "second call should return the cached semaphore"


if __name__ == "__main__":
    # Allow running this test file directly for quick iteration.
    pytest.main([__file__, "-v", "-o", "addopts="])
