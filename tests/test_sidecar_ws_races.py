"""Race-condition regression tests for ``sidecar_ws.py``."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402
from voice_typer.server.ipc.validation import ErrorCodes  # noqa: E402

from tests.fixtures.sidecar_ws_test_helpers import (  # noqa: E402
    make_fake_server_with_semaphore,
    make_fake_websocket,
)


def _extract_error_code(ws: MagicMock) -> str | None:
    """Parse the first sent frame on *ws* and return its ``data.code``."""
    if not ws._sent_frames:
        return None
    frame = json.loads(ws._sent_frames[0])
    return frame.get("data", {}).get("code")


@pytest.mark.asyncio
async def test_concurrent_connections_at_one_slot_one_rejected_not_blocked(
    monkeypatch,
) -> None:
    """When the semaphore has 1 slot and 2 connections arrive concurrently,"""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    # Semaphore value=1: one slot remaining (the TOCTOU race window).
    server = make_fake_server_with_semaphore(1)
    dispatch = MagicMock()

    # Both websockets use a WRONG token so the one that acquires proceeds
    ws_a = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}), yield_before_recv=True)
    ws_b = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}), yield_before_recv=True)

    # Run both concurrently. With the fix, both complete in milliseconds.
    start = time.monotonic()
    await asyncio.wait_for(
        asyncio.gather(
            sidecar_ws._handle_connection(ws_a, server, dispatch),
            sidecar_ws._handle_connection(ws_b, server, dispatch),
        ),
        timeout=3.0,
    )
    elapsed = time.monotonic() - start
    # The cap-rejection path is synchronous (no blocking on acquire);
    assert elapsed < 1.5, (
        f"concurrent cap check took {elapsed:.3f}s, expected sub-second "
        f"(non-blocking acquire should reject immediately, not block on "
        f"sem.acquire())"
    )

    codes = [_extract_error_code(ws) for ws in (ws_a, ws_b)]
    # With the fix: one ``auth_failed`` (acquired, auth failed) + one
    assert sorted(codes) == ["auth_failed", ErrorCodes.MAX_CONNECTIONS_REACHED], (
        f"expected one auth_failed + one max_connections_reached (fix), "
        f"got {codes}, if both are auth_failed, the cap TOCTOU race is "
        f"back (the second connection blocked on sem.acquire() instead "
        f"of being rejected at cap)"
    )

    # Both should have closed with code 1008.
    for ws in (ws_a, ws_b):
        assert len(ws._closed_with) == 1, f"expected exactly one close call, got {ws._closed_with}"
        _, close_kwargs = ws._closed_with[0]
        assert close_kwargs.get("code") == 1008, f"expected close(code=1008), got kwargs={close_kwargs}"


@pytest.mark.asyncio
async def test_concurrent_connections_at_zero_cap_all_rejected_quickly(
    monkeypatch,
) -> None:
    """When the semaphore is fully at cap (value=0), N concurrent acquire"""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    # Semaphore value=0: at cap, all slots held.
    server = make_fake_server_with_semaphore(0)
    dispatch = MagicMock()

    n = 5
    wss = [make_fake_websocket(json.dumps({"type": "auth", "token": "good-token"})) for _ in range(n)]

    start = time.monotonic()
    await asyncio.wait_for(
        asyncio.gather(*[sidecar_ws._handle_connection(ws, server, dispatch) for ws in wss]),
        timeout=3.0,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"concurrent at-cap rejections took {elapsed:.3f}s, expected "
        f"sub-second (all should reject immediately via non-blocking acquire)"
    )

    # Every websocket should have been rejected with max_connections_reached.
    for ws in wss:
        code = _extract_error_code(ws)
        assert code == ErrorCodes.MAX_CONNECTIONS_REACHED, (
            f"expected max_connections_reached for every concurrent at-cap connection, got {code!r}"
        )
        assert len(ws._closed_with) == 1
        _, close_kwargs = ws._closed_with[0]
        assert close_kwargs.get("code") == 1008

    # No auth should have run (the rejection happens BEFORE auth).
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_duplicate_auth_only_one_succeeds() -> None:
    """When two websockets race to claim the active-connection slot,"""
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)

    # Two fake websockets, both "open" (closed=False so the duplicate
    ws_a = make_fake_websocket()
    ws_a.closed = False
    ws_b = make_fake_websocket()
    ws_b.closed = False

    results = await asyncio.gather(
        sidecar_ws._check_duplicate_auth(ws_a, server, ("127.0.0.1", 1)),
        sidecar_ws._check_duplicate_auth(ws_b, server, ("127.0.0.1", 2)),
    )

    # Exactly one returns True, the other returns False.
    assert sorted(results) == [False, True], (
        f"expected one True + one False, got {results}, both succeeded "
        f"means the duplicate-auth read-probe-write race is back (B "
        f"overwrote A's claim on the active-connection slot)"
    )

    # Identify the winner (True) and loser (False).
    if results[0]:
        winner_ws, loser_ws = ws_a, ws_b
    else:
        winner_ws, loser_ws = ws_b, ws_a

    # The winner sent no error frame (it proceeds to the dispatch loop).
    assert len(winner_ws._sent_frames) == 0, f"winner should not have sent an error frame, got {winner_ws._sent_frames}"

    # The loser sent a duplicate_connection error frame + closed with 1008.
    assert len(loser_ws._sent_frames) == 1, (
        f"loser should have sent one duplicate_connection frame, got {loser_ws._sent_frames}"
    )
    frame = json.loads(loser_ws._sent_frames[0])
    assert frame["type"] == "error"
    assert frame["data"]["code"] == ErrorCodes.DUPLICATE_CONNECTION, (
        f"expected code='duplicate_connection', got {frame['data']['code']!r}"
    )
    assert "message" in frame["data"]
    assert len(loser_ws._closed_with) == 1
    _, close_kwargs = loser_ws._closed_with[0]
    assert close_kwargs.get("code") == 1008, f"expected close(code=1008), got kwargs={close_kwargs}"

    # The active-connection slot points at the winner (not the loser).
    assert server._active_ws_connection is winner_ws, (
        "active-connection slot should point at the winner, not the loser "
        "— if it points at the loser, B's write overwrote A's claim (the "
        "read-probe-write race)"
    )


@pytest.mark.asyncio
async def test_duplicate_auth_rejects_when_existing_open() -> None:
    """Baseline: when an existing authenticated connection is OPEN, a new"""
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Pre-populate the active-connection slot with an OPEN websocket.
    existing_ws = make_fake_websocket()
    existing_ws.closed = False
    server._active_ws_connection = existing_ws

    new_ws = make_fake_websocket()
    new_ws.closed = False

    result = await sidecar_ws._check_duplicate_auth(new_ws, server, ("127.0.0.1", 9999))

    assert result is False, "should reject when an open existing connection is active"

    # Sent a duplicate_connection error frame + closed with 1008.
    assert len(new_ws._sent_frames) == 1
    frame = json.loads(new_ws._sent_frames[0])
    assert frame["data"]["code"] == ErrorCodes.DUPLICATE_CONNECTION
    assert len(new_ws._closed_with) == 1
    _, close_kwargs = new_ws._closed_with[0]
    assert close_kwargs.get("code") == 1008

    # The active-connection slot is UNCHANGED (still the existing ws).
    assert server._active_ws_connection is existing_ws, (
        "active-connection slot should be unchanged when the new connection is rejected as a duplicate"
    )


@pytest.mark.asyncio
async def test_duplicate_auth_proceeds_when_existing_closed() -> None:
    """new ``_check_duplicate_auth`` call proceeds (claims the slot)."""
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Pre-populate with a CLOSED websocket.
    existing_ws = make_fake_websocket()
    existing_ws.closed = True
    server._active_ws_connection = existing_ws

    new_ws = make_fake_websocket()
    new_ws.closed = False

    result = await sidecar_ws._check_duplicate_auth(new_ws, server, ("127.0.0.1", 9999))

    assert result is True, "should proceed when existing connection is closed"
    # No error frame sent.
    assert len(new_ws._sent_frames) == 0, f"should not send an error frame when proceeding, got {new_ws._sent_frames}"
    # Active-connection slot now points at the new websocket (compare-and-set).
    assert server._active_ws_connection is new_ws, (
        "active-connection slot should be updated to the new websocket when the existing connection is closed"
    )


@pytest.mark.asyncio
async def test_duplicate_auth_proceeds_when_no_existing() -> None:
    """Baseline: when there is no existing connection (slot is ``None``),"""
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Slot is None (no existing connection).
    server._active_ws_connection = None

    new_ws = make_fake_websocket()
    new_ws.closed = False

    result = await sidecar_ws._check_duplicate_auth(new_ws, server, ("127.0.0.1", 9999))

    assert result is True, "should proceed when no existing connection"
    assert len(new_ws._sent_frames) == 0
    assert server._active_ws_connection is new_ws


@pytest.mark.asyncio
async def test_dispatch_pre_executor_toctou_recheck_rejects_when_flag_flips_after_inflight_recheck(
    monkeypatch,
) -> None:
    """The dispatch coroutine re-checks ``app._shutting_down``"""
    try:
        from voice_typer.server.ipc_server import _get_rate_limiter  # noqa: F401
    except Exception as exc:  # noqa: BLE001, broad on purpose
        pytest.skip(f"ipc_server.py not importable: {exc}")

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    # Build a MagicMock server with the attributes ``_make_dispatch``
    server = MagicMock()
    server._ready_emitted = True
    server._lock = MagicMock()
    server._lock.__enter__ = MagicMock(return_value=None)
    server._lock.__exit__ = MagicMock(return_value=False)
    server.app.tray._state = None
    server.push = MagicMock()

    import concurrent.futures

    real_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    server._ws_dispatch_pool = real_pool
    server._ws_dispatch_futures = set()

    # Track whether the handler was called. The pre-executor re-check
    dispatch_called: list[bool] = []

    def _real_dispatch(msg):
        dispatch_called.append(True)
        return {"type": "ok"}

    server._dispatch = _real_dispatch

    # Start with the flag ``False`` so the early gate, the rate-limiter
    server.app._shutting_down = False

    import threading as _threading

    real_inflight_lock = _threading.Lock()

    class _FlagFlippingInflightLock:
        def __enter__(self):
            real_inflight_lock.acquire()
            # Flip the flag AFTER the in-flight-count re-check has
            server.app._shutting_down = True
            return self

        def __exit__(self, *_exc):
            real_inflight_lock.release()
            return False

    server._ws_inflight_lock = _FlagFlippingInflightLock()
    # Pre-set the count + drain Event so ``_make_dispatch`` does NOT
    server._ws_inflight_count = 0
    server._ws_drained_event = _threading.Event()
    server._ws_drained_event.set()

    # Rate limiter: allow without flipping the flag. The lock wrapper
    monkeypatch.setattr(
        "voice_typer.server.ipc_server._get_rate_limiter",
        lambda s: type("_L", (), {"allow": lambda self, command=None: True})(),
    )

    dispatch = sidecar_ws._make_dispatch(server)

    # Drive the dispatch coroutine directly with a single message.
    msg = {"type": "get_status", "id": "test-pre-executor-toctou"}
    result = await dispatch(msg, websocket=MagicMock())

    # The pre-executor re-check should have rejected the dispatch.
    assert result is not None, "dispatch should have returned a server.shutting_down error envelope"
    assert result.get("type") == "error", f"expected type='error', got {result!r}"
    data = result.get("data", {})
    assert data.get("code") == "server.shutting_down", (
        f"expected code='server.shutting_down' (pre-executor re-check), got {data!r}"
    )

    # The handler must NOT have been called, the re-check short-circuited
    assert dispatch_called == [], (
        "the pre-executor re-check should have short-circuited BEFORE "
        "loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg), "
        "the handler ran, which means the re-check is missing or broken"
    )

    # The in-flight count must be back to 0, the ``finally`` block
    assert server._ws_inflight_count == 0, (
        f"expected _ws_inflight_count == 0 (finally block should have "
        f"decremented after the pre-executor re-check's early return), "
        f"got {server._ws_inflight_count}"
    )

    # The drain Event must be set, the ``finally`` block re-sets it
    assert server._ws_drained_event.is_set(), (
        "expected _ws_drained_event to be set (finally block should have "
        "re-set it when count dropped to 0 after the early return)"
    )

    real_pool.shutdown(wait=True)


if __name__ == "__main__":
    # Allow running this test file directly for quick iteration.
    pytest.main([__file__, "-v", "-o", "addopts="])
