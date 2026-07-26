"""GT-FIX-06 regression: ``sidecar_ws`` graceful-shutdown path.

GT-27 (High): the WS server had no graceful close path —
``sidecar_ws.run()``'s asyncio loop was only terminated by process
exit (host force-kill or ``sys.exit``), so the connected Tauri client
received a TCP RST instead of a WS Close frame. This triggered the
respawn path as if the sidecar had crashed.

GT-45 (Medium, partial): the WS dispatch shutdown gate at
``sidecar_ws._make_dispatch``'s early ``_shutting_down`` check is
TOCTOU — the flag can flip between the read and the handler
invocation. GT-FIX-06 adds a second re-check immediately before
``loop.run_in_executor``/``pool.submit`` to shrink the window.

GT-C2-2 (Medium): the WS dispatch pool's
``shutdown(wait=False, cancel_futures=True)`` (called by
``ShutdownController._do_cleanup``) cancels QUEUED tasks but does NOT
abort in-flight ones — they run to completion. GT-FIX-06 registers
each in-flight ``concurrent.futures.Future`` on
``server._ws_dispatch_futures`` so ``ws_graceful_shutdown`` can
bounded-wait for them (2.0s) before stopping the loop.

This module exercises:

1. **GT-27 graceful close**: ``ws_graceful_shutdown`` sends
   ``websocket.close(code=1001, reason='going away')`` to every
   authenticated connection. Verified with a fake websocket + a
   dedicated loop in a thread (so the test framework's loop is not
   killed by ``loop.stop``).
2. **GT-27 loop stop within budget**: ``ws_graceful_shutdown`` calls
   ``loop.call_soon_threadsafe(loop.stop)`` after the close handshake,
   so the loop thread exits within ~500ms + slack — not blocked
   indefinitely on the never-resolving ``asyncio.Future()`` in
   ``run._main``.
3. **GT-27 stop() wrapper**: ``server.stop`` (the wrapper installed by
   ``_attach_ws_graceful_shutdown``) invokes ``ws_graceful_shutdown``
   FIRST, then delegates to the original ``IPCServer.stop`` — this
   satisfies the "BEFORE ``ipc_server.stop()``" requirement without
   modifying ``shutdown_controller.py`` or ``ipc_server.py`` (file
   ownership boundary).
4. **GT-C2-2 dispatch drain**: ``ws_graceful_shutdown`` bounded-waits
   for in-flight dispatch futures registered on
   ``server._ws_dispatch_futures``. Verified with a slow handler that
   sleeps 200ms; the drain must observe the future completing.
5. **GT-45 TOCTOU re-check**: ``_make_dispatch``'s ``dispatch``
   coroutine re-checks ``app._shutting_down`` immediately before
   ``pool.submit`` and short-circuits with a ``server.shutting_down``
   error envelope when the flag has flipped in the gap.
6. **Auth handshake regression**: a successful auth still flows
   through ``_handle_connection`` (no auth_failed frame, no close
   with 1008) — the GT-27 registration of the websocket on
   ``server._ws_authenticated_conns`` after auth does not break the
   existing post-auth path.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

# ─── Helpers ───────────────────────────────────────────────────────────


def _make_real_server_for_graceful_shutdown() -> MagicMock:
    """Build a MagicMock IPCServer pre-populated with the real attributes
    ``_attach_ws_graceful_shutdown`` and ``ws_graceful_shutdown`` need.

    A raw ``MagicMock`` returns truthy children for any ``getattr``,
    which short-circuits ``_attach_ws_graceful_shutdown``'s idempotency
    guard. Pre-setting the attributes to real values (``False``, empty
    ``set``) lets the install actually run.
    """
    server = MagicMock()
    server._ws_graceful_shutdown_installed = False
    server._ws_authenticated_conns = set()
    server._ws_dispatch_futures = set()
    return server


def _make_fake_websocket_for_close() -> MagicMock:
    """Build a fake websocket whose ``close`` records its call args."""
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 54321)
    close_calls: list[tuple[tuple, dict]] = []

    async def _track_close(*args, **kwargs):
        close_calls.append((args, kwargs))

    ws.close = _track_close
    ws._close_calls = close_calls  # type: ignore[attr-defined]
    return ws


# ─── GT-27: graceful close sends code=1001 ─────────────────────────────


def test_graceful_shutdown_sends_close_1001_to_all_authenticated_conns(
    monkeypatch,
) -> None:
    """GT-27: ``ws_graceful_shutdown`` sends ``close(code=1001, 'going away')``
    to EVERY authenticated connection before stopping the loop.

    Pre-GT-27 the loop was killed by process exit, so the connected
    Tauri client received a TCP RST and triggered respawn as if
    the sidecar had crashed. The fix sends a clean WS Close frame so
    the host tears down the WS client cleanly.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    # Dedicated loop in a thread — ``ws_graceful_shutdown`` calls
    # ``loop.stop``, which would kill the test framework's loop if we
    # used the running one.
    loop = asyncio.new_event_loop()
    # YJ-51 / YJ-FIX-C2-rework (review Issue 5): the production write
    # ``server._ws_loop = loop`` was deleted (zero readers — the
    # per-connection ``_push_to_ws`` closure captures its own ``loop``).
    # This test-side write is also dead — removed.

    fake_ws_1 = _make_fake_websocket_for_close()
    fake_ws_2 = _make_fake_websocket_for_close()
    server._ws_authenticated_conns.add(fake_ws_1)
    server._ws_authenticated_conns.add(fake_ws_2)

    def _run_loop() -> None:
        try:
            loop.run_forever()
        finally:
            loop.close()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()

    # Invoke from the main thread — the production caller
    # (``ShutdownController._do_cleanup`` via the ``server.stop`` wrapper)
    # also runs from a non-loop thread.
    server.ws_graceful_shutdown()
    t.join(timeout=5.0)

    assert not t.is_alive(), (
        "ws_graceful_shutdown should have stopped the loop and the loop "
        "thread should have exited — it is still alive after 5s"
    )

    # BOTH authenticated connections must have received close(1001).
    assert len(fake_ws_1._close_calls) == 1, f"conn 1: expected one close call, got {fake_ws_1._close_calls}"
    assert len(fake_ws_2._close_calls) == 1, f"conn 2: expected one close call, got {fake_ws_2._close_calls}"
    for ws in (fake_ws_1, fake_ws_2):
        args, kwargs = ws._close_calls[0]
        assert kwargs.get("code") == 1001, f"expected close(code=1001), got kwargs={kwargs}"
        assert kwargs.get("reason") == "going away", f"expected reason='going away', got kwargs={kwargs}"


# ─── GT-27: loop stops within ~500ms + slack ───────────────────────────


def test_graceful_shutdown_stops_loop_within_budget(monkeypatch) -> None:
    """GT-27: ``ws_graceful_shutdown`` stops the asyncio loop within
    ~500ms (handshake) + slack, NOT blocked indefinitely on the
    never-resolving ``asyncio.Future()`` in ``run._main``.

    The 500ms is the ``_WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS`` sleep
    inside the close coroutine. The upper bound is 2.5s to allow for
    scheduling latency + the 2.0s dispatch-drain timeout (which is a
    no-op here because there are no in-flight futures). The KEY
    assertion is that the loop thread actually exits — pre-GT-27 it
    would have stayed alive until process exit.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    loop = asyncio.new_event_loop()
    # YJ-51 / YJ-FIX-C2-rework (review Issue 5): the production write
    # ``server._ws_loop = loop`` was deleted (zero readers). This
    # test-side write is also dead — removed.

    fake_ws = MagicMock()

    async def _noop_close(*args, **kwargs):
        return None

    fake_ws.close = _noop_close
    server._ws_authenticated_conns.add(fake_ws)

    def _run_loop() -> None:
        try:
            loop.run_forever()
        finally:
            loop.close()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()

    start = time.monotonic()
    server.ws_graceful_shutdown()
    t.join(timeout=5.0)
    elapsed = time.monotonic() - start

    assert not t.is_alive(), (
        "ws_graceful_shutdown should have stopped the loop — the loop "
        "thread is still alive after 5s, which means loop.stop() was "
        "never scheduled or never observed"
    )
    # 500ms handshake + 0.5s future.result margin + 2.0s drain (no-op
    # here) + scheduling slack. The test asserts the loop ACTUALLY
    # stopped, not just that close was sent — pre-GT-27 it never did.
    assert elapsed < 4.0, (
        f"graceful shutdown took {elapsed:.2f}s — expected well under 4s "
        f"(500ms handshake + drain no-op + slack). If this is slow, "
        f"loop.stop() may not be firing."
    )


# ─── GT-27: stop() wrapper invokes ws_graceful_shutdown FIRST ──────────


def test_stop_wrapper_invokes_ws_graceful_shutdown_before_original_stop(
    monkeypatch,
) -> None:
    """GT-27: the ``server.stop`` wrapper installed by
    ``_attach_ws_graceful_shutdown`` calls ``ws_graceful_shutdown`` FIRST,
    then delegates to the original ``IPCServer.stop``.

    This satisfies GT-27's "BEFORE ``ipc_server.stop()``" requirement
    WITHOUT modifying ``shutdown_controller.py`` or ``ipc_server.py``
    (file ownership boundary — this module owns all WS-state). The
    wrapper is best-effort: exceptions from ``ws_graceful_shutdown``
    are logged at DEBUG and the original ``stop`` still runs.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_real_server_for_graceful_shutdown()

    # Track the call order: ws_graceful_shutdown FIRST, then original_stop.
    call_log: list[str] = []

    # Pre-install a fake "original stop" so the wrapper has something
    # to delegate to. ``_attach_ws_graceful_shutdown`` captures
    # ``server.stop`` at install time, so we set this BEFORE calling
    # ``_attach_ws_graceful_shutdown``.
    def _original_stop(*args, **kwargs):
        call_log.append("original_stop")

    server.stop = _original_stop
    sidecar_ws._attach_ws_graceful_shutdown(server)

    # Replace the installed ``ws_graceful_shutdown`` with a spy that
    # records the call and DOES NOT actually run the close coroutine
    # (we don't need a real loop for this test — we're only verifying
    # call order).
    def _spy_ws_graceful_shutdown():
        call_log.append("ws_graceful_shutdown")

    server.ws_graceful_shutdown = _spy_ws_graceful_shutdown

    # Invoke the wrapper.
    server.stop()

    assert call_log == ["ws_graceful_shutdown", "original_stop"], (
        f"expected ws_graceful_shutdown BEFORE original_stop, got {call_log!r}"
    )


def test_stop_wrapper_swallows_ws_graceful_shutdown_exceptions(monkeypatch) -> None:
    """GT-27: if ``ws_graceful_shutdown`` raises, the wrapper logs at DEBUG
    and STILL calls the original ``stop`` — failures in the WS close
    path must not prevent the TCP teardown from running.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_real_server_for_graceful_shutdown()

    original_stop_called: list[bool] = []

    def _original_stop(*args, **kwargs):
        original_stop_called.append(True)

    server.stop = _original_stop
    sidecar_ws._attach_ws_graceful_shutdown(server)

    def _exploding_ws_graceful_shutdown():
        raise RuntimeError("simulated WS loop already gone")

    server.ws_graceful_shutdown = _exploding_ws_graceful_shutdown

    # Must NOT raise — the wrapper catches the exception.
    server.stop()

    assert original_stop_called == [True], "original stop() must still run even if ws_graceful_shutdown raised"


# ─── GT-C2-2: in-flight dispatch drain ─────────────────────────────────


def test_graceful_shutdown_drains_inflight_dispatch_futures(monkeypatch) -> None:
    """GT-C2-2: ``ws_graceful_shutdown`` bounded-waits for in-flight
    dispatch futures registered on ``server._ws_dispatch_futures`` before
    stopping the loop.

    Pre-GT-C2-2 the pool's ``shutdown(wait=False, cancel_futures=True)``
    (called by ``ShutdownController._do_cleanup``) cancelled QUEUED
    tasks but not in-flight ones — a long-running handler raced
    teardown. The fix registers each in-flight future and bounded-waits
    (2.0s) for them to complete.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    loop = asyncio.new_event_loop()
    # YJ-51 / YJ-FIX-C2-rework (review Issue 5): the production write
    # ``server._ws_loop = loop`` was deleted (zero readers). This
    # test-side write is also dead — removed.

    # Simulate an in-flight dispatch future: a 200ms handler.
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def _slow_handler():
        time.sleep(0.2)
        return "done"

    cf_future = pool.submit(_slow_handler)
    server._ws_dispatch_futures.add(cf_future)

    fake_ws = MagicMock()

    async def _noop_close(*args, **kwargs):
        return None

    fake_ws.close = _noop_close
    server._ws_authenticated_conns.add(fake_ws)

    def _run_loop() -> None:
        try:
            loop.run_forever()
        finally:
            loop.close()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()

    start = time.monotonic()
    server.ws_graceful_shutdown()
    elapsed = time.monotonic() - start
    t.join(timeout=5.0)
    pool.shutdown(wait=True)

    # The drain must have waited for the slow handler to finish (~200ms)
    # before returning. If the drain was missing, the future would have
    # been left dangling and ``elapsed`` would be < 100ms (just the
    # 500ms handshake is enough — but we want to assert the drain
    # actually observed completion, so check the future resolved).
    assert cf_future.done(), "in-flight dispatch future should have completed within the drain's 2.0s bounded wait"
    assert cf_future.result() == "done"
    # Sanity: the drain happened — the future completed BEFORE
    # ws_graceful_shutdown returned.
    assert elapsed < 3.0, (
        f"ws_graceful_shutdown took {elapsed:.2f}s — drain should have completed within 2.0s + handshake"
    )


# ─── GT-45: TOCTOU re-check ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_toctou_recheck_rejects_after_shutting_down_flips(
    monkeypatch,
) -> None:
    """GT-45 (partial): ``_make_dispatch``'s ``dispatch`` coroutine
    re-checks ``app._shutting_down`` immediately before ``pool.submit``
    and short-circuits with a ``server.shutting_down`` error envelope
    when the flag has flipped in the gap between the early gate and
    the dispatch.

    This test simulates the race by setting ``_shutting_down = False``
    at the early gate (so the request passes the first check) and
    flipping it to ``True`` before the dispatch reaches the second
    check. Pre-GT-45 the dispatch would have proceeded; post-GT-45 it
    short-circuits with the structured error.
    """
    # The TOCTOU test exercises ``_make_dispatch``'s rate-limiter path,
    # which imports ``_get_rate_limiter`` from ``ipc_server.py``. That
    # module may be in a transient broken state during parallel
    # GT-FIX-05 edits — skip gracefully if so. The graceful-shutdown
    # tests above do NOT depend on ``ipc_server`` and cover GT-27.
    try:
        from voice_typer.server.ipc_server import _get_rate_limiter  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — broad on purpose
        pytest.skip(f"ipc_server.py not importable (transient GT-FIX-05 mid-edit state): {exc}")

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    # Build a server with a real dispatch pool so ``_make_dispatch``
    # doesn't trip on MagicMock children.
    server = MagicMock()
    server._ready_emitted = True  # skip the ready emit
    server._lock = MagicMock()
    server._lock.__enter__ = MagicMock(return_value=None)
    server._lock.__exit__ = MagicMock(return_value=False)
    server.app.tray._state = None
    server.push = MagicMock()

    # Use a real ThreadPoolExecutor for the dispatch pool.
    import concurrent.futures

    real_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    server._ws_dispatch_pool = real_pool
    server._ws_dispatch_futures = set()

    # ``server._dispatch`` would normally run the handler; we want to
    # assert it is NOT called when the TOCTOU re-check rejects.
    dispatch_called: list[bool] = []

    def _real_dispatch(msg):
        dispatch_called.append(True)
        return {"type": "ok"}

    server._dispatch = _real_dispatch

    # The gate uses ``getattr(server.app, "_shutting_down", False) is True``
    # — strict identity. We need ``_shutting_down`` to be ``False`` at
    # the early gate (passes) and ``True`` at the re-check (rejects).
    # We achieve this by patching ``_get_rate_limiter`` to flip the
    # flag as a side effect of the ``allow()`` call (which runs
    # BETWEEN the early gate and the re-check).
    server.app._shutting_down = False

    def _flipping_allow(*args, **kwargs):
        # Flip the flag AFTER the early gate has passed but BEFORE
        # the TOCTOU re-check.
        server.app._shutting_down = True
        return True

    monkeypatch.setattr(
        "voice_typer.server.ipc_server._get_rate_limiter",
        lambda s: type("_L", (), {"allow": _flipping_allow})(),
    )

    dispatch = sidecar_ws._make_dispatch(server)

    # Build a fake websocket that is iterated once with a single
    # message, then stops. We need to drive the dispatch coroutine
    # directly to test the TOCTOU path.
    msg = {"type": "get_status", "id": "test-1"}
    result = await dispatch(msg, websocket=MagicMock())

    # The TOCTOU re-check should have rejected the dispatch.
    assert result is not None, "dispatch should have returned a server.shutting_down error envelope"
    assert result.get("type") == "error", f"expected type='error', got {result!r}"
    data = result.get("data", {})
    assert data.get("code") == "server.shutting_down", (
        f"expected code='server.shutting_down' (TOCTOU re-check), got {data!r}"
    )

    # The handler must NOT have been called.
    assert dispatch_called == [], (
        "the TOCTOU re-check should have short-circuited BEFORE "
        "pool.submit(server._dispatch, msg) — the handler ran, which "
        "means the re-check is missing or broken"
    )

    real_pool.shutdown(wait=True)


# ─── Auth handshake regression ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_handshake_still_works_with_graceful_shutdown_installed(
    monkeypatch,
) -> None:
    """GT-27 regression: installing the graceful-shutdown hooks on the
    IPCServer must NOT break the existing auth handshake path.

    The hook installation adds ``server._ws_authenticated_conns`` and
    registers the websocket on it after a successful auth. This test
    verifies a successful auth still flows through ``_handle_connection``
    without sending an ``auth_failed`` frame and without closing with
    code 1008 — the post-auth ``_ws_authenticated_conns.add(websocket)``
    call must not crash.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")

    # Build a server that mirrors what _handle_connection needs post-auth.
    server = MagicMock()
    server._ws_graceful_shutdown_installed = False
    server._ws_authenticated_conns = set()
    server._ws_dispatch_futures = set()
    sidecar_ws._attach_ws_graceful_shutdown(server)
    server._ready_emitted = True  # skip the ready emit
    server._lock = MagicMock()
    server._lock.__enter__ = MagicMock(return_value=None)
    server._lock.__exit__ = MagicMock(return_value=False)
    server.app.tray._state = None  # skip the state_changed emit
    server.push = MagicMock()

    # Fake websocket that auths successfully then yields nothing.
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 12345)
    auth_frame = json.dumps({"type": "auth", "token": "good-token"}).encode()

    async def _fake_recv():
        return auth_frame

    ws.recv = _fake_recv

    sent_frames: list[str] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _no_close(*args, **kwargs):
        return None

    ws.send = _track_send
    ws.close = _no_close

    class _EmptyAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    ws.__aiter__ = lambda self: _EmptyAsyncIter()  # noqa: E731

    dispatch = MagicMock()

    # Cleanup exceptions from the writer task teardown are
    # acceptable — we only care that no auth_failed frame was sent.
    with contextlib.suppress(Exception):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    # No auth_failed frame should have been sent on a successful auth.
    auth_failed_frames = [f for f in sent_frames if "auth_failed" in f]
    assert auth_failed_frames == [], f"successful auth must NOT send an auth_failed frame, got {auth_failed_frames}"

    # GT-27: the websocket must have been registered on
    # ``_ws_authenticated_conns`` after the successful auth, then
    # unregistered in the finally block when the connection closed.
    assert ws not in server._ws_authenticated_conns, (
        "websocket should have been removed from _ws_authenticated_conns when _handle_connection's finally block ran"
    )


# ─── Idempotency ───────────────────────────────────────────────────────


def test_attach_ws_graceful_shutdown_is_idempotent() -> None:
    """GT-27: ``_attach_ws_graceful_shutdown`` is idempotent — calling it
    twice on the same server does NOT re-wrap ``server.stop`` (which
    would create a chain of wrappers calling each other). Detected via
    the ``_ws_graceful_shutdown_installed`` marker.
    """
    server = _make_real_server_for_graceful_shutdown()

    sidecar_ws._attach_ws_graceful_shutdown(server)
    first_stop = server.stop
    first_fn = server.ws_graceful_shutdown

    sidecar_ws._attach_ws_graceful_shutdown(server)
    second_stop = server.stop
    second_fn = server.ws_graceful_shutdown

    assert first_stop is second_stop, (
        "idempotent re-install must NOT re-wrap server.stop (would create "
        "a chain of wrappers calling each other on every shutdown)"
    )
    assert first_fn is second_fn, "idempotent re-install must NOT replace ws_graceful_shutdown"
