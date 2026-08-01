"""Race-condition regression tests for ``sidecar_ws.py``.

Covers two concurrency findings:

1. **WS connection cap TOCTOU race** — ``_handle_connection`` previously
   checked ``sem.locked()`` to reject with 1008 when at cap, THEN called
   ``await sem.acquire()``. The check-then-acquire was not atomic:
   multiple concurrent connections could all pass the ``locked()``
   check (sem not yet locked) and then all block on ``acquire()``,
   exceeding the cap in the WAITING queue. The fix uses a non-blocking
   acquire via ``asyncio.wait_for(sem.acquire(), timeout=0.0)`` — if
   the semaphore has capacity, ``acquire()`` completes synchronously
   inside ``wait_for``; if it is exhausted, ``wait_for`` raises
   ``asyncio.TimeoutError`` and the connection is rejected with 1008.

2. **``_check_duplicate_auth`` read-probe-write race** — the function
   previously read ``server._active_ws_connection`` under
   ``server._lock``, RELEASED the lock, probed ``existing.closed``
   WITHOUT the lock, then re-acquired the lock to write. Two concurrent
   auths could both see ``existing=None``, both probe None→False, and
   both write (B overwriting A). The fix holds ``server._lock`` across
   the whole read-probe-write critical section (with the reject-path
   ``send`` / ``close`` ``await`` calls moved AFTER the lock release so
   the lock is not held across an ``await``).

The tests use ``MagicMock`` websockets (same pattern as
``test_sidecar_ws_connection_cap.py`` and
``test_sidecar_ws_auth_failed.py``); no real ``websockets`` server is
bound.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

# ─── Helpers ────────────────────────────────────────────────────────────


def _make_fake_websocket(
    auth_frame: str | None = None,
    *,
    yield_before_recv: bool = False,
) -> MagicMock:
    """Build a mock websocket that yields *auth_frame* on the first recv.

    If *auth_frame* is ``None``, ``recv()`` is parked on a never-resolving
    future so the auth path appears to hang (used to verify the semaphore
    rejection runs BEFORE auth).

    If *yield_before_recv* is ``True``, the mock ``recv()`` yields once
    (via ``asyncio.sleep(0)``) before returning the auth frame. This
    simulates the I/O yield a real ``websockets`` recv would perform
    (waiting on the loop's reader) and is needed for the concurrent-cap
    race test: without the yield, ``asyncio.wait_for(sync_recv, ...)``
    runs the mock recv synchronously inside the calling task (no loop
    yield), so the winning connection completes its entire auth +
    ``sem.release()`` before the losing connection's acquire Task runs —
    the race window never opens and the test cannot distinguish the fix
    from the original bug.
    """
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 12345)

    if auth_frame is not None:
        auth_bytes = auth_frame.encode() if isinstance(auth_frame, str) else auth_frame

        async def _fake_recv():
            if yield_before_recv:
                # Yield once to simulate the I/O wait a real
                # ``websockets`` recv performs. This gives concurrent
                # ``_try_acquire_semaphore`` callers a chance to run
                # their acquire Tasks before the winner releases.
                await asyncio.sleep(0)
            return auth_bytes

        ws.recv = _fake_recv
    else:

        async def _parked_recv():
            await asyncio.Future()

        ws.recv = _parked_recv

    sent_frames: list[str] = []
    closed_with: list[tuple[tuple, dict]] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _track_close(*args, **kwargs):
        closed_with.append((args, kwargs))

    ws.send = _track_send
    ws.close = _track_close
    ws._sent_frames = sent_frames
    ws._closed_with = closed_with
    # The websockets library's ``closed`` attribute is an int (0=open);
    # the duplicate-auth probe treats ``not bool(existing.closed)`` as
    # "is open". Set to ``False`` so the probe sees the mock as OPEN
    # (the duplicate-rejection scenario).
    ws.closed = False
    return ws


def _make_server_with_semaphore(value: int) -> MagicMock:
    """Build a MagicMock server whose semaphore has *value* slots remaining.

    *value* == ``sidecar_ws._MAX_WS_CONNECTIONS`` → full budget (under cap).
    *value* == 0 → exhausted (at cap, all slots held).
    *value* == 1 → one slot remaining (the TOCTOU race window).
    """
    server = MagicMock()
    sem = asyncio.Semaphore(value)
    server._ws_connection_semaphore = sem
    # MagicMock auto-vivifies ``_lock`` as a child mock; the production
    # code uses ``with server._lock:`` as a synchronous context manager.
    # Use a real ``threading.RLock`` so the ``with`` block actually
    # serializes the read-probe-write critical section.
    server._lock = threading.RLock()
    server._ready_emitted = True  # skip the ready emit
    server.app.tray._state = None  # skip the state_changed emit
    server.push = MagicMock()
    # Initialize the active-connection slot to ``None`` so the first
    # ``_check_duplicate_auth`` call sees no existing connection.
    # (Without this, MagicMock auto-vivifies a child mock for the
    # attribute, which the defensive probe treats as "closed" — but
    # explicit is better than implicit for race tests.)
    server._active_ws_connection = None
    return server


def _extract_error_code(ws: MagicMock) -> str | None:
    """Parse the first sent frame on *ws* and return its ``data.code``.

    Returns ``None`` if no frame was sent (e.g. the success path that
    proceeds to the dispatch loop without sending an error envelope).
    """
    if not ws._sent_frames:
        return None
    frame = json.loads(ws._sent_frames[0])
    return frame.get("data", {}).get("code")


# ─── OI-19: connection cap TOCTOU race ─────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_connections_at_one_slot_one_rejected_not_blocked(
    monkeypatch,
) -> None:
    """When the semaphore has 1 slot and 2 connections arrive concurrently,
    ONE acquires and proceeds to auth; the OTHER is rejected with 1008
    (``max_connections_reached``) — NOT blocked on ``sem.acquire()``.

    The original buggy code (``if sem.locked(): reject; await
    sem.acquire()``) had a check-then-acquire TOCTOU race: with 1 slot
    remaining, both connections would pass the ``locked()`` check (sem
    not yet locked), both would call ``acquire()``, and the second would
    BLOCK until the first releases. Both would eventually run auth (both
    getting ``auth_failed`` on a wrong token). With the fix
    (``_try_acquire_semaphore`` — non-blocking acquire via a Task +
    ``sleep(0)`` + ``done()`` check), the second is rejected immediately
    with ``max_connections_reached``.

    Asserts:
    - Both connections complete in < 1.5s (no blocking).
    - Exactly one gets ``auth_failed`` (acquired → auth failed → released).
    - Exactly one gets ``max_connections_reached`` (rejected at cap).
    - Both close with code 1008.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    # Semaphore value=1: one slot remaining (the TOCTOU race window).
    server = _make_server_with_semaphore(1)
    dispatch = MagicMock()

    # Both websockets use a WRONG token so the one that acquires proceeds
    # to auth, fails, and releases (so the test doesn't hang waiting for
    # the sem to drain). ``yield_before_recv=True`` simulates the I/O
    # yield a real ``websockets`` recv performs — without it, the mock
    # recv runs synchronously inside the calling task and the winner
    # releases the sem before the loser's acquire Task runs (the race
    # window never opens).
    ws_a = _make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}), yield_before_recv=True)
    ws_b = _make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}), yield_before_recv=True)

    # Run both concurrently. With the fix, both complete in milliseconds.
    # If the original bug were present, both would still complete (after
    # the first releases), but BOTH would get ``auth_failed`` — the
    # second would NOT get ``max_connections_reached``.
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
    # the auth-fail path is also fast (one recv + one send + one close).
    assert elapsed < 1.5, (
        f"concurrent cap check took {elapsed:.3f}s — expected sub-second "
        f"(non-blocking acquire should reject immediately, not block on "
        f"sem.acquire())"
    )

    codes = [_extract_error_code(ws) for ws in (ws_a, ws_b)]
    # With the fix: one ``auth_failed`` (acquired, auth failed) + one
    # ``max_connections_reached`` (rejected at cap). With the original
    # bug, both would be ``auth_failed`` (the second would have blocked
    # on acquire, then proceeded once the first released).
    assert sorted(codes) == ["auth_failed", "max_connections_reached"], (
        f"expected one auth_failed + one max_connections_reached (fix), "
        f"got {codes} — if both are auth_failed, the cap TOCTOU race is "
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
    """When the semaphore is fully at cap (value=0), N concurrent acquire
    attempts ALL reject with 1008 within milliseconds — not blocked.

    This is the "obvious" at-cap case (the original ``sem.locked()``
    check would catch it too), but the test locks in the fix's behavior:
    the non-blocking acquire rejects ALL overflow connections immediately
    regardless of how many arrive simultaneously.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    # Semaphore value=0: at cap, all slots held.
    server = _make_server_with_semaphore(0)
    dispatch = MagicMock()

    n = 5
    wss = [_make_fake_websocket(json.dumps({"type": "auth", "token": "good-token"})) for _ in range(n)]

    start = time.monotonic()
    await asyncio.wait_for(
        asyncio.gather(*[sidecar_ws._handle_connection(ws, server, dispatch) for ws in wss]),
        timeout=3.0,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"concurrent at-cap rejections took {elapsed:.3f}s — expected "
        f"sub-second (all should reject immediately via non-blocking acquire)"
    )

    # Every websocket should have been rejected with max_connections_reached.
    for ws in wss:
        code = _extract_error_code(ws)
        assert code == "max_connections_reached", (
            f"expected max_connections_reached for every concurrent at-cap connection, got {code!r}"
        )
        assert len(ws._closed_with) == 1
        _, close_kwargs = ws._closed_with[0]
        assert close_kwargs.get("code") == 1008

    # No auth should have run (the rejection happens BEFORE auth).
    dispatch.assert_not_called()


# ─── OI-20: _check_duplicate_auth read-probe-write race ───────────────


@pytest.mark.asyncio
async def test_concurrent_duplicate_auth_only_one_succeeds() -> None:
    """When two websockets race to claim the active-connection slot,
    only ONE succeeds; the OTHER is rejected with ``duplicate_connection``.

    The fix holds ``server._lock`` across the entire read-probe-write
    critical section, so the second caller sees the first's write and
    rejects. The reject-path ``send`` / ``close`` ``await`` calls run
    AFTER the lock is released, so the lock is not held across an
    ``await`` (which would block the event loop).
    """
    server = _make_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)

    # Two fake websockets, both "open" (closed=False so the duplicate
    # probe sees them as live).
    ws_a = _make_fake_websocket()
    ws_a.closed = False
    ws_b = _make_fake_websocket()
    ws_b.closed = False

    # Both call _check_duplicate_auth concurrently. With the fix, the
    # first to acquire the lock claims the slot; the second sees the
    # first's write and rejects.
    results = await asyncio.gather(
        sidecar_ws._check_duplicate_auth(ws_a, server, ("127.0.0.1", 1)),
        sidecar_ws._check_duplicate_auth(ws_b, server, ("127.0.0.1", 2)),
    )

    # Exactly one returns True, the other returns False.
    assert sorted(results) == [False, True], (
        f"expected one True + one False, got {results} — both succeeded "
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
    assert frame["data"]["code"] == "duplicate_connection", (
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
    """Baseline: when an existing authenticated connection is OPEN, a new
    ``_check_duplicate_auth`` call rejects with ``duplicate_connection``.

    This is the single-threaded (no-race) case — verifies the basic
    invariant still holds after the fix. The active-connection slot is
    UNCHANGED (still the existing ws).
    """
    server = _make_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Pre-populate the active-connection slot with an OPEN websocket.
    existing_ws = _make_fake_websocket()
    existing_ws.closed = False
    server._active_ws_connection = existing_ws

    new_ws = _make_fake_websocket()
    new_ws.closed = False

    result = await sidecar_ws._check_duplicate_auth(new_ws, server, ("127.0.0.1", 9999))

    assert result is False, "should reject when an open existing connection is active"

    # Sent a duplicate_connection error frame + closed with 1008.
    assert len(new_ws._sent_frames) == 1
    frame = json.loads(new_ws._sent_frames[0])
    assert frame["data"]["code"] == "duplicate_connection"
    assert len(new_ws._closed_with) == 1
    _, close_kwargs = new_ws._closed_with[0]
    assert close_kwargs.get("code") == 1008

    # The active-connection slot is UNCHANGED (still the existing ws).
    assert server._active_ws_connection is existing_ws, (
        "active-connection slot should be unchanged when the new connection is rejected as a duplicate"
    )


@pytest.mark.asyncio
async def test_duplicate_auth_proceeds_when_existing_closed() -> None:
    """Baseline: when the existing authenticated connection is CLOSED, a
    new ``_check_duplicate_auth`` call proceeds (claims the slot).

    This is the recovery path — the previous connection died without
    clearing the slot (e.g. process kill), and the new connection takes
    over. The probe treats a closed existing as "not open" so the new
    connection can claim the slot.
    """
    server = _make_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Pre-populate with a CLOSED websocket.
    existing_ws = _make_fake_websocket()
    existing_ws.closed = True
    server._active_ws_connection = existing_ws

    new_ws = _make_fake_websocket()
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
    """Baseline: when there is no existing connection (slot is ``None``),
    a new ``_check_duplicate_auth`` call proceeds (claims the slot).

    This is the first-connection case — the slot starts empty and the
    first auth claims it.
    """
    server = _make_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Slot is None (no existing connection).
    server._active_ws_connection = None

    new_ws = _make_fake_websocket()
    new_ws.closed = False

    result = await sidecar_ws._check_duplicate_auth(new_ws, server, ("127.0.0.1", 9999))

    assert result is True, "should proceed when no existing connection"
    assert len(new_ws._sent_frames) == 0
    assert server._active_ws_connection is new_ws


if __name__ == "__main__":
    # Allow running this test file directly for quick iteration.
    pytest.main([__file__, "-v", "-o", "addopts="])
