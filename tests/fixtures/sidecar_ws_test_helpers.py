"""Shared fake-server factory for the sidecar WS test suite."""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import MagicMock


def _make_fake_server() -> MagicMock:
    """Build a fake IPCServer with the attributes _make_dispatch / _handle_connection need."""
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    server._ws_dispatch_pool = None
    server._ws_drained_event = None
    server._ws_inflight_lock = None
    server._ws_inflight_count = None
    server.app._shutting_down = False
    # For tests that exercise ``_handle_connection`` (not just
    server.push = MagicMock()
    server._ready_emitted = True
    server.app.tray._state = None
    server.app.tray._message = ""
    return server


__all__ = ["_make_fake_server"]


# Peer address reported by every fake websocket. Tests only rely on it
_FAKE_PEER_ADDRESS = ("127.0.0.1", 12345)


class _BlockingAsyncIter:
    """Async iterator that never yields, parks the dispatch loop."""

    def __aiter__(self) -> _BlockingAsyncIter:
        return self

    async def __anext__(self) -> str:
        await asyncio.Future()
        raise StopAsyncIteration  # pragma: no cover - unreachable


class _EmptyAsyncIter:
    """Async iterator that ends immediately, clean disconnect."""

    def __aiter__(self) -> _EmptyAsyncIter:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


def make_fake_websocket(
    auth_frame: str | bytes | dict | None = None,
    *,
    yield_before_recv: bool = False,
) -> MagicMock:
    """Build the canonical recv-driven fake websocket."""
    ws = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS

    if auth_frame is None:

        async def _parked_recv() -> bytes:
            await asyncio.Future()

        ws.recv = _parked_recv
    else:
        if isinstance(auth_frame, dict):
            auth_bytes = json.dumps(auth_frame).encode()
        elif isinstance(auth_frame, str):
            auth_bytes = auth_frame.encode()
        else:
            auth_bytes = auth_frame

        async def _fake_recv() -> bytes:
            if yield_before_recv:
                # Yield once to simulate the I/O wait a real
                await asyncio.sleep(0)
            return auth_bytes

        ws.recv = _fake_recv

    sent_frames: list[str] = []
    closed_with: list[tuple[tuple, dict]] = []

    async def _track_send(payload: str) -> None:
        sent_frames.append(payload)

    async def _track_close(*args: object, **kwargs: object) -> None:
        closed_with.append((args, kwargs))

    ws.send = _track_send
    ws.close = _track_close
    ws._sent_frames = sent_frames
    ws._closed_with = closed_with
    ws.closed = False
    return ws


def make_fake_websocket_for_read_loop(frames: list[str]) -> tuple[MagicMock, list[str]]:
    """Build the canonical async-iter fake websocket that yields *frames*."""
    ws = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS

    async def _aiter():
        for frame in frames:
            yield frame

    ws.__aiter__ = lambda self: _aiter()  # noqa: E731

    sent: list[str] = []

    async def _send(payload: str) -> None:
        sent.append(payload)

    ws.send = _send

    async def _close(*args: object, **kwargs: object) -> None:
        return None

    ws.close = _close
    return ws, sent


def make_fake_server_with_semaphore(value: int) -> MagicMock:
    """Build the canonical fake server whose semaphore has *value* slots."""
    server = MagicMock()
    server._ws_connection_semaphore = asyncio.Semaphore(value)
    server._lock = threading.RLock()
    server._ready_emitted = True  # skip the ready emit
    server.app.tray._state = None  # skip the state_changed emit
    server.push = MagicMock()
    server._active_ws_connection = None
    return server


def make_real_server_for_graceful_shutdown() -> MagicMock:
    """Build a MagicMock IPCServer for ``_attach_ws_graceful_shutdown``."""
    server = MagicMock()
    server._ws_graceful_shutdown_installed = False
    server._ws_authenticated_conns = set()
    server._ws_dispatch_futures = set()
    return server


def make_fake_websocket_for_close() -> MagicMock:
    """Build a fake websocket whose ``close`` records its call args."""
    ws = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS
    close_calls: list[tuple[tuple, dict]] = []

    async def _track_close(*args: object, **kwargs: object) -> None:
        close_calls.append((args, kwargs))

    ws.close = _track_close
    ws._close_calls = close_calls  # type: ignore[attr-defined]
    return ws


def make_fake_websocket_parked_after_auth(auth_token: str, *, park_dispatch: bool) -> MagicMock:
    """Build a fake websocket that authenticates with *auth_token* then either"""
    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": auth_token}).encode()

    async def _fake_recv() -> bytes:
        return auth_frame

    ws.recv = _fake_recv
    ws.close = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS
    if park_dispatch:
        ws.__aiter__ = lambda self: _BlockingAsyncIter()  # noqa: E731
    else:
        ws.__aiter__ = lambda self: _EmptyAsyncIter()  # noqa: E731

    async def _noop_send(*args: object, **kwargs: object) -> None:
        return None

    ws.send = _noop_send
    return ws


__all__ = [
    "_make_fake_server",
    "make_fake_server_with_semaphore",
    "make_fake_websocket",
    "make_fake_websocket_for_close",
    "make_fake_websocket_for_read_loop",
    "make_fake_websocket_parked_after_auth",
    "make_real_server_for_graceful_shutdown",
]
