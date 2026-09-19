"""regression: ``sidecar_ws`` graceful-shutdown path."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

from tests.fixtures.sidecar_ws_test_helpers import (  # noqa: E402
    make_fake_websocket_for_close,
    make_fake_websocket_for_read_loop,
    make_real_server_for_graceful_shutdown,
)

# Per-test xfail mark retained for any future temporary xfail need
_GRACEFUL_SHUTDOWN_NOT_LANDED = pytest.mark.xfail(
    reason=(
        "graceful-shutdown implementation not yet landed in sidecar_ws.py, "
        "tests reference sidecar_ws._attach_ws_graceful_shutdown and "
        "ws_graceful_shutdown which do not exist"
    ),
    strict=True,
)


def test_graceful_shutdown_sends_close_1001_to_all_authenticated_conns(
    monkeypatch,
) -> None:
    """``ws_graceful_shutdown`` sends ``close(code=1001, 'going away')``"""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    # Dedicated loop in a thread: ``ws_graceful_shutdown`` calls
    loop = asyncio.new_event_loop()
    # Store the loop on the server so ``ws_graceful_shutdown`` can
    server._ws_loop = loop

    fake_ws_1 = make_fake_websocket_for_close()
    fake_ws_2 = make_fake_websocket_for_close()
    server._ws_authenticated_conns.add(fake_ws_1)
    server._ws_authenticated_conns.add(fake_ws_2)

    def _run_loop() -> None:
        try:
            loop.run_forever()
        finally:
            loop.close()

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()

    # Invoke from the main thread, the production caller
    server.ws_graceful_shutdown()
    t.join(timeout=5.0)

    assert not t.is_alive(), (
        "ws_graceful_shutdown should have stopped the loop and the loop "
        "thread should have exited, it is still alive after 5s"
    )

    # BOTH authenticated connections must have received close(1001).
    assert len(fake_ws_1._close_calls) == 1, f"conn 1: expected one close call, got {fake_ws_1._close_calls}"
    assert len(fake_ws_2._close_calls) == 1, f"conn 2: expected one close call, got {fake_ws_2._close_calls}"
    for ws in (fake_ws_1, fake_ws_2):
        args, kwargs = ws._close_calls[0]
        assert kwargs.get("code") == 1001, f"expected close(code=1001), got kwargs={kwargs}"
        assert kwargs.get("reason") == "going away", f"expected reason='going away', got kwargs={kwargs}"


def test_graceful_shutdown_stops_loop_within_budget(monkeypatch) -> None:
    """``ws_graceful_shutdown`` stops the asyncio loop within"""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    loop = asyncio.new_event_loop()
    # Store the loop on the server so ``ws_graceful_shutdown`` can
    server._ws_loop = loop

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
        "ws_graceful_shutdown should have stopped the loop, the loop "
        "thread is still alive after 5s, which means loop.stop() was "
        "never scheduled or never observed"
    )
    # 500ms handshake + 0.5s future.result margin + 2.0s drain (no-op
    assert elapsed < 4.0, (
        f"graceful shutdown took {elapsed:.2f}s, expected well under 4s "
        f"(500ms handshake + drain no-op + slack). If this is slow, "
        f"loop.stop() may not be firing."
    )


def test_stop_hook_installed_into_ws_stop_hook_slot(monkeypatch) -> None:
    """
    ``_attach_ws_graceful_shutdown`` installs the shutdown wrapper into
    Ordering contract unchanged: ``LifecycleMixin.stop`` runs the hook
    BEFORE the TCP teardown (pinned by the lifecycle test below), but
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = make_real_server_for_graceful_shutdown()
    stop_before = server.stop

    sidecar_ws._attach_ws_graceful_shutdown(server)

    assert server._ws_stop_hook is not None, "install must set the _ws_stop_hook slot"
    assert server.stop == stop_before, "install must NOT replace the bound stop method"

    # The hook delegates to ``ws_graceful_shutdown`` (dynamically looked
    calls: list[str] = []
    server.ws_graceful_shutdown = lambda: calls.append("ws_graceful_shutdown")
    server._ws_stop_hook()
    assert calls == ["ws_graceful_shutdown"]


def test_stop_runs_hook_before_tcp_teardown(monkeypatch) -> None:
    """TCP teardown -- the GT-27 \"WS shutdown BEFORE ``ipc_server.stop()``\""""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    call_log: list[str] = []
    server.ws_graceful_shutdown = lambda: call_log.append("ws_graceful_shutdown")  # type: ignore[method-assign]

    # Drive the REAL ``LifecycleMixin.stop`` with the fixture server:
    from voice_typer.server.ipc import lifecycle as lifecycle_mod

    lifecycle_mod.LifecycleMixin.stop(server)  # type: ignore[arg-type]

    ws_hook_ran = call_log == ["ws_graceful_shutdown"]
    assert ws_hook_ran, f"hook did not invoke ws_graceful_shutdown: {call_log!r}"
    # The teardown body flipped the fixture's shutdown bookkeeping (mock
    assert server._running is False, "TCP teardown body must run after the hook"


def test_stop_hook_swallows_ws_graceful_shutdown_exceptions(monkeypatch) -> None:
    """if ``ws_graceful_shutdown`` raises, the hook logs at DEBUG"""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = make_real_server_for_graceful_shutdown()

    sidecar_ws._attach_ws_graceful_shutdown(server)

    def _exploding_ws_graceful_shutdown():
        raise RuntimeError("simulated WS loop already gone")

    server.ws_graceful_shutdown = _exploding_ws_graceful_shutdown

    from voice_typer.server.ipc import lifecycle as lifecycle_mod

    # Must NOT raise, the hook catches the exception, teardown continues.
    lifecycle_mod.LifecycleMixin.stop(server)  # type: ignore[arg-type]
    assert server._running is False, "TCP teardown body must still run"


def test_graceful_shutdown_drains_inflight_dispatch_futures(monkeypatch) -> None:
    """GT-C2-2: ``ws_graceful_shutdown`` bounded-waits for in-flight"""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = make_real_server_for_graceful_shutdown()
    sidecar_ws._attach_ws_graceful_shutdown(server)

    loop = asyncio.new_event_loop()
    # Store the loop on the server so ``ws_graceful_shutdown`` can
    server._ws_loop = loop

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
    assert cf_future.done(), "in-flight dispatch future should have completed within the drain's 2.0s bounded wait"
    assert cf_future.result() == "done"
    # Sanity: the drain happened, the future completed BEFORE
    assert elapsed < 3.0, (
        f"ws_graceful_shutdown took {elapsed:.2f}s, drain should have completed within 2.0s + handshake"
    )


# TOCTOU re-check ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_toctou_recheck_rejects_after_shutting_down_flips(
    monkeypatch,
) -> None:
    """(partial): ``_make_dispatch``'s ``dispatch`` coroutine"""
    try:
        from voice_typer.server.ipc_server import _get_rate_limiter  # noqa: F401
    except Exception as exc:  # noqa: BLE001, broad on purpose
        pytest.skip(f"ipc_server.py not importable (transient GT-FIX-05 mid-edit state): {exc}")

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    # Build a server with a real dispatch pool so ``_make_dispatch``
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

    dispatch_called: list[bool] = []

    def _real_dispatch(msg):
        dispatch_called.append(True)
        return {"type": "ok"}

    server._dispatch = _real_dispatch

    # The gate uses ``getattr(server.app, "_shutting_down", False) is True``
    server.app._shutting_down = False

    def _flipping_allow(*args, **kwargs):
        # Flip the flag AFTER the early gate has passed but BEFORE
        server.app._shutting_down = True
        return True

    monkeypatch.setattr(
        "voice_typer.server.ipc_server._get_rate_limiter",
        lambda s: type("_L", (), {"allow": _flipping_allow})(),
    )

    dispatch = sidecar_ws._make_dispatch(server)

    # Build a fake websocket that is iterated once with a single
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
        "pool.submit(server._dispatch, msg), the handler ran, which "
        "means the re-check is missing or broken"
    )

    real_pool.shutdown(wait=True)


@pytest.mark.asyncio
async def test_auth_handshake_still_works_with_graceful_shutdown_installed(
    monkeypatch,
) -> None:
    """IPCServer must NOT break the existing auth handshake path."""
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
    with contextlib.suppress(Exception):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    # No auth_failed frame should have been sent on a successful auth.
    auth_failed_frames = [f for f in sent_frames if "auth_failed" in f]
    assert auth_failed_frames == [], f"successful auth must NOT send an auth_failed frame, got {auth_failed_frames}"

    assert ws not in server._ws_authenticated_conns, (
        "websocket should have been removed from _ws_authenticated_conns when _handle_connection's finally block ran"
    )


def test_attach_ws_graceful_shutdown_is_idempotent() -> None:
    """``_attach_ws_graceful_shutdown`` is idempotent, calling it"""
    server = make_real_server_for_graceful_shutdown()

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


class TestSafeSendSizeCapRegression:
    """Regression for the dispatch-response DoS finding."""

    @pytest.mark.asyncio
    async def test_dispatch_response_over_size_cap_is_dropped_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A dispatch response whose JSON-encoded form exceeds"""
        cap_value = sidecar_ws._MAX_FRAME_BYTES

        # Build a dispatch response whose JSON-encoded UTF-8 byte count
        huge_payload = "x" * (cap_value + 1024)
        huge_response = {
            "type": "result",
            "data": {"items": huge_payload},
        }
        encoded = json.dumps(huge_response, ensure_ascii=False).encode("utf-8")
        assert len(encoded) > cap_value, (
            f"test setup: the dispatch response must exceed _MAX_FRAME_BYTES ({cap_value}); got {len(encoded)} bytes"
        )

        # One inbound dispatch frame: ``type="get_history"`` is NOT
        dispatch_frame = json.dumps({"type": "get_history", "id": "req-1"})
        ws, sent_payloads = make_fake_websocket_for_read_loop([dispatch_frame])

        # Fake dispatch coroutine that returns the huge response.
        async def _dispatch(msg, websocket):
            return huge_response

        server = MagicMock()

        # Capture logs at ERROR level, the drop must be logged at
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.sidecar_ws"):
            await sidecar_ws._read_loop(ws, server, _dispatch)

        # The huge dispatch response must NOT have been sent —
        assert sent_payloads == [], (
            f"the oversized dispatch response must be DROPPED, not sent, "
            f"websocket.send was called with {sent_payloads!r}"
        )

        # An ERROR log must have been emitted mentioning the size cap.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "the oversized dispatch response drop must be logged at ERROR level"
        # The log message must reference the size cap so operators can
        assert any("outbound frame exceeds" in r.getMessage() for r in error_records), (
            f"the ERROR log must mention 'outbound frame exceeds'; got "
            f"{[(r.levelname, r.getMessage()) for r in error_records]!r}"
        )

    @pytest.mark.asyncio
    async def test_writer_over_size_cap_is_dropped_and_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """An outbound event on the writer's queue whose JSON-encoded"""
        cap_value = sidecar_ws._MAX_FRAME_BYTES

        # Build a huge event whose JSON-encoded UTF-8 byte count
        huge_event = {
            "type": "test_oversized",
            "data": {"blob": "x" * (cap_value + 1024)},
        }
        small_event = {"type": "test_small", "data": {"ok": True}}

        # Sanity: the huge event exceeds the cap, the small one does not.
        huge_encoded = json.dumps(huge_event, ensure_ascii=False).encode("utf-8")
        small_encoded = json.dumps(small_event, ensure_ascii=False).encode("utf-8")
        assert len(huge_encoded) > cap_value, (
            f"test setup: huge_event must exceed _MAX_FRAME_BYTES ({cap_value}); got {len(huge_encoded)} bytes"
        )
        assert len(small_encoded) <= cap_value, (
            f"test setup: small_event must NOT exceed _MAX_FRAME_BYTES ({cap_value}); got {len(small_encoded)} bytes"
        )

        outbound: asyncio.Queue = asyncio.Queue()
        ws = MagicMock()
        sent_payloads: list = []

        async def _track_send(payload):
            sent_payloads.append(payload)

        ws.send = _track_send

        async def _track_close(*args, **kwargs):
            return None

        ws.close = _track_close

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.sidecar_ws"):
            writer_task = sidecar_ws._start_writer(ws, outbound)
            # Enqueue the huge event first, then the small event, then
            await outbound.put(huge_event)
            await outbound.put(small_event)
            await outbound.put(None)
            # Wait for the writer to drain all three (with a timeout
            try:
                await asyncio.wait_for(writer_task, timeout=3.0)
            except TimeoutError:
                writer_task.cancel()
                pytest.fail(
                    "writer task did not exit within 3s, the oversized "
                    "drop likely killed the writer instead of continuing "
                    "to drain the queue"
                )

        assert len(sent_payloads) == 1, (
            f"expected exactly one send (the small event); the huge event must be dropped. Got {sent_payloads!r}"
        )
        sent_bytes = sent_payloads[0]
        # C-WS-2 wire contract: _safe_send must emit WS TEXT frames —
        assert isinstance(sent_bytes, str), (
            f"_safe_send must send str (WS TEXT frame) per C-WS-2, not bytes/BINARY; got {type(sent_bytes).__name__}"
        )
        # The sent frame must be the small event (not the huge one).
        sent_str = sent_bytes
        sent_obj = json.loads(sent_str)
        assert sent_obj.get("type") == "test_small", f"expected the small event to be sent; got {sent_obj!r}"

        # An ERROR log must have been emitted for the oversized drop.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "the oversized writer event drop must be logged at ERROR level"
        assert any("outbound frame exceeds" in r.getMessage() for r in error_records), (
            f"the ERROR log must mention 'outbound frame exceeds'; got "
            f"{[(r.levelname, r.getMessage()) for r in error_records]!r}"
        )
