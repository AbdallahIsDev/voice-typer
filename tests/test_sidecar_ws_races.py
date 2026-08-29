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

# ─── Helpers ────────────────────────────────────────────────────────────


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
    server = make_fake_server_with_semaphore(1)
    dispatch = MagicMock()

    # Both websockets use a WRONG token so the one that acquires proceeds
    # to auth, fails, and releases (so the test doesn't hang waiting for
    # the sem to drain). ``yield_before_recv=True`` simulates the I/O
    # yield a real ``websockets`` recv performs — without it, the mock
    # recv runs synchronously inside the calling task and the winner
    # releases the sem before the loser's acquire Task runs (the race
    # window never opens).
    ws_a = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}), yield_before_recv=True)
    ws_b = make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}), yield_before_recv=True)

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
    assert sorted(codes) == ["auth_failed", ErrorCodes.MAX_CONNECTIONS_REACHED], (
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
        f"concurrent at-cap rejections took {elapsed:.3f}s — expected "
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
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)

    # Two fake websockets, both "open" (closed=False so the duplicate
    # probe sees them as live).
    ws_a = make_fake_websocket()
    ws_a.closed = False
    ws_b = make_fake_websocket()
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
    """Baseline: when an existing authenticated connection is OPEN, a new
    ``_check_duplicate_auth`` call rejects with ``duplicate_connection``.

    This is the single-threaded (no-race) case — verifies the basic
    invariant still holds after the fix. The active-connection slot is
    UNCHANGED (still the existing ws).
    """
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
    """Baseline: when the existing authenticated connection is CLOSED, a
    new ``_check_duplicate_auth`` call proceeds (claims the slot).

    This is the recovery path — the previous connection died without
    clearing the slot (e.g. process kill), and the new connection takes
    over. The probe treats a closed existing as "not open" so the new
    connection can claim the slot.
    """
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
    """Baseline: when there is no existing connection (slot is ``None``),
    a new ``_check_duplicate_auth`` call proceeds (claims the slot).

    This is the first-connection case — the slot starts empty and the
    first auth claims it.
    """
    server = make_fake_server_with_semaphore(sidecar_ws._MAX_WS_CONNECTIONS)
    # Slot is None (no existing connection).
    server._active_ws_connection = None

    new_ws = make_fake_websocket()
    new_ws.closed = False

    result = await sidecar_ws._check_duplicate_auth(new_ws, server, ("127.0.0.1", 9999))

    assert result is True, "should proceed when no existing connection"
    assert len(new_ws._sent_frames) == 0
    assert server._active_ws_connection is new_ws


# ─── Dispatch pre-executor TOCTOU re-check ─────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_pre_executor_toctou_recheck_rejects_when_flag_flips_after_inflight_recheck(
    monkeypatch,
) -> None:
    """The dispatch coroutine re-checks ``app._shutting_down``
    immediately before ``loop.run_in_executor`` and short-circuits with
    a ``server.shutting_down`` error envelope when the flag has flipped
    in the window between the in-flight-count re-check and the actual
    executor submission.

    Pre-fix the dispatch would proceed to ``loop.run_in_executor`` and
    the handler would run during shutdown — racing
    ``ShutdownController._do_cleanup`` (which tears down the recorder /
    history DB / crash-recovery subsystems concurrently). Post-fix the
    pre-executor re-check short-circuits with the structured error and
    the handler is never called; the in-flight count is decremented
    (net-zero) by the ``finally`` block so ``_do_cleanup`` is not
    blocked.

    The test simulates the race by leaving the flag ``False`` through
    the early gate, the rate-limiter call, and the in-flight-count
    re-check (so all three pass), then flipping it to ``True`` as a
    side effect of acquiring ``_ws_inflight_lock`` — which the dispatch
    path acquires AFTER the in-flight-count re-check and BEFORE the
    pre-executor re-check. The lock wrapper delegates to a real
    ``threading.Lock`` so concurrent access (if any) is still
    serialized; the flip is the only observable side effect.
    """
    # The dispatch path imports ``_get_rate_limiter`` from
    # ``ipc_server.py``. Skip gracefully if the module is in a
    # transient broken state (mirrors the existing TOCTOU test in
    # ``test_sidecar_ws.py``).
    try:
        from voice_typer.server.ipc_server import _get_rate_limiter  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — broad on purpose
        pytest.skip(f"ipc_server.py not importable: {exc}")

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    # Build a MagicMock server with the attributes ``_make_dispatch``
    # touches. Pre-set ``_ready_emitted`` / ``_lock`` / ``app.tray._state``
    # so the dispatch path's ready-emit + state-emit side effects are
    # skipped (mirrors the existing TOCTOU test setup).
    server = MagicMock()
    server._ready_emitted = True
    server._lock = MagicMock()
    server._lock.__enter__ = MagicMock(return_value=None)
    server._lock.__exit__ = MagicMock(return_value=False)
    server.app.tray._state = None
    server.push = MagicMock()

    # Use a real ThreadPoolExecutor for the dispatch pool so the
    # ``loop.run_in_executor`` call would actually dispatch if the
    # re-check didn't short-circuit.
    import concurrent.futures

    real_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    server._ws_dispatch_pool = real_pool
    server._ws_dispatch_futures = set()

    # Track whether the handler was called. The pre-executor re-check
    # MUST short-circuit before ``loop.run_in_executor``, so the handler
    # is never invoked.
    dispatch_called: list[bool] = []

    def _real_dispatch(msg):
        dispatch_called.append(True)
        return {"type": "ok"}

    server._dispatch = _real_dispatch

    # Start with the flag ``False`` so the early gate, the rate-limiter
    # call, and the in-flight-count re-check all pass.
    server.app._shutting_down = False

    # Pre-set ``_ws_inflight_lock`` to a wrapper that flips the flag to
    # ``True`` on ``__enter__``. The dispatch path acquires this lock
    # AFTER the in-flight-count re-check (to increment the count +
    # clear the drain Event) and releases it BEFORE the pre-executor
    # re-check. So the flip lands in the TOCTOU window the new
    # pre-executor re-check is designed to close. The wrapper delegates
    # to a real ``threading.Lock`` so the count + Event mutation
    # critical section is still serialized.
    import threading as _threading

    real_inflight_lock = _threading.Lock()

    class _FlagFlippingInflightLock:
        def __enter__(self):
            real_inflight_lock.acquire()
            # Flip the flag AFTER the in-flight-count re-check has
            # passed but BEFORE the pre-executor re-check runs.
            server.app._shutting_down = True
            return self

        def __exit__(self, *_exc):
            real_inflight_lock.release()
            return False

    server._ws_inflight_lock = _FlagFlippingInflightLock()
    # Pre-set the count + drain Event so ``_make_dispatch`` does NOT
    # create new ones (which would shadow our lock wrapper — the
    # ``getattr(server, "_ws_inflight_lock", None)`` lookup would find
    # our pre-set wrapper and skip the lazy-create branch).
    server._ws_inflight_count = 0
    server._ws_drained_event = _threading.Event()
    server._ws_drained_event.set()

    # Rate limiter: allow without flipping the flag. The lock wrapper
    # does the flipping in the TOCTOU window between the
    # in-flight-count re-check and the pre-executor re-check.
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

    # The handler must NOT have been called — the re-check short-circuited
    # before ``loop.run_in_executor``.
    assert dispatch_called == [], (
        "the pre-executor re-check should have short-circuited BEFORE "
        "loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg) — "
        "the handler ran, which means the re-check is missing or broken"
    )

    # The in-flight count must be back to 0 — the ``finally`` block
    # decremented it after the early return (net-zero: incremented
    # before the try, decremented in the finally).
    assert server._ws_inflight_count == 0, (
        f"expected _ws_inflight_count == 0 (finally block should have "
        f"decremented after the pre-executor re-check's early return), "
        f"got {server._ws_inflight_count}"
    )

    # The drain Event must be set — the ``finally`` block re-sets it
    # when the count drops to 0, so ``_do_cleanup`` is not blocked on
    # a dispatch that never reached the executor.
    assert server._ws_drained_event.is_set(), (
        "expected _ws_drained_event to be set (finally block should have "
        "re-set it when count dropped to 0 after the early return)"
    )

    real_pool.shutdown(wait=True)


if __name__ == "__main__":
    # Allow running this test file directly for quick iteration.
    pytest.main([__file__, "-v", "-o", "addopts="])
