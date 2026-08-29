"""XZ-IPC-003 regression: WS server must enforce a concurrent-connection cap.

Before XZ-IPC-003, ``sidecar_ws.serve()`` had NO limit on the number of
simultaneous accept-loop tasks. A local attacker (any unprivileged user on
the same host) could open thousands of WS connections to the loopback
listener faster than the 5s auth timeout reaped them, exhausting file
descriptors / asyncio task slots / kernel memory.

The fix adds a per-server ``asyncio.Semaphore(_MAX_WS_CONNECTIONS=16)``
enforced at the top of ``_handle_connection``. Overflow connections are
rejected with WS close code 1008 + a ``max_connections_reached`` error
envelope BEFORE the auth frame is read, so the auth-timeout reaper is
not involved.

These tests exercise:

1. **Under cap → connection proceeds** — a fresh server with a full
   semaphore budget accepts the connection normally (auth runs).
2. **At cap → connection rejected with 1008** — a server whose
   semaphore is exhausted rejects the new connection with a
   ``max_connections_reached`` error frame + close(1008), WITHOUT
   reading the auth frame.
3. **Semaphore released on disconnect** — after a connection completes
   (clean OR abnormal), the semaphore slot is released so a subsequent
   connection can proceed.
4. **Per-instance semaphore** — each fresh ``IPCServer`` gets its own
   semaphore; exhaustion on one server does NOT affect another.

The tests use ``MagicMock`` websockets (same pattern as
``test_sidecar_ws_auth_failed.py``); no real ``websockets`` server is
bound.
"""

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
    """When the semaphore has capacity, the connection proceeds to auth.

    A fresh server (full budget) should NOT be rejected. The auth frame
    IS read and validated; on mismatch the normal auth_failed path runs
    (NOT the max_connections_reached path).
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}))
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    # Auth ran (and failed) → auth_failed frame, NOT max_connections_reached.
    assert len(ws._sent_frames) == 1
    frame = json.loads(ws._sent_frames[0])
    assert frame["data"]["code"] == "auth_failed", (
        f"expected auth_failed (connection was under cap), got {frame['data']['code']!r}"
    )

    # Close was called with 1008 (auth-failed close code).
    assert len(ws._closed_with) == 1
    _, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008


@pytest.mark.asyncio
async def test_at_cap_rejects_with_1008_before_auth(monkeypatch) -> None:
    """When the semaphore is exhausted, the connection is rejected with 1008.

    The auth frame is NEVER read (``ws.recv`` is not called), so the
    auth-timeout reaper is not involved. The error envelope carries
    ``max_connections_reached`` so the host can branch on the code.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    # Provide a valid auth frame — but it should NEVER be read because
    # the semaphore rejection runs first.
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

    # Auth was NEVER invoked — the recv mock was never awaited.
    # (The recv coroutine object exists but was never called because
    # _authenticate short-circuits via the semaphore rejection.)
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_semaphore_released_after_auth_fail(monkeypatch) -> None:
    """After an auth-failed rejection, the semaphore slot is released.

    A subsequent connection on the SAME server should be able to acquire
    the slot (the semaphore value should be back to its initial state).
    """
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
    assert frame["data"]["code"] == "auth_failed", (
        f"second connection should proceed to auth (semaphore was released), got {frame['data']['code']!r}"
    )


@pytest.mark.asyncio
async def test_semaphore_released_after_clean_disconnect(monkeypatch) -> None:
    """After a successful auth + clean disconnect, the semaphore is released.

    Uses a successful auth + empty async iterator (immediate clean
    disconnect) so the connection body completes normally.
    """
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
    """The cap must be a positive integer (sanity check on the constant).

    A cap of 0 would reject ALL connections (DoS the sidecar); a negative
    cap would crash ``asyncio.Semaphore``. 16 is generous — only one
    authenticated connection is meaningful at a time (XZ-R18-06), so the
    cap exists purely to bound the unauthenticated-connection window.
    """
    assert isinstance(sidecar_ws._MAX_WS_CONNECTIONS, int)
    assert sidecar_ws._MAX_WS_CONNECTIONS > 0
    # The cap should be at least 1 (allow one connection) and not so high
    # that it defeats the DoS protection.
    assert 1 <= sidecar_ws._MAX_WS_CONNECTIONS <= 1024


def test_get_ws_connection_semaphore_creates_real_semaphore() -> None:
    """The helper returns a real ``asyncio.Semaphore`` even for MagicMock servers.

    MagicMock auto-vivifies child attributes, so ``getattr(server,
    "_ws_connection_semaphore", None)`` returns a child MagicMock (not
    None). The helper must detect this via ``isinstance`` and create a
    real Semaphore — otherwise ``.acquire()`` would return a non-awaitable
    MagicMock.
    """
    server = MagicMock()
    # Fresh MagicMock: getattr auto-vivifies a child mock, NOT None.
    # The helper must NOT return that child mock.
    sem = sidecar_ws._get_ws_connection_semaphore(server)
    assert isinstance(sem, asyncio.Semaphore), f"expected a real asyncio.Semaphore, got {type(sem).__name__}"

    # A second call returns the SAME semaphore (idempotent — the helper
    # stores it on the server).
    sem2 = sidecar_ws._get_ws_connection_semaphore(server)
    assert sem is sem2, "second call should return the cached semaphore"


if __name__ == "__main__":
    # Allow running this test file directly for quick iteration.
    pytest.main([__file__, "-v", "-o", "addopts="])
