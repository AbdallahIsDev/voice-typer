"""regression: ``sidecar_ws`` outbound queue must be thread-safe."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import threading

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import event_bus, sidecar_ws  # noqa: E402

from tests.fixtures.sidecar_ws_test_helpers import (  # noqa: E402
    _make_fake_server,
    make_fake_websocket_parked_after_auth,
)


@pytest.fixture(autouse=True)
def _reset_event_bus_subscribers():
    """Clear leaked global ``event_bus`` subscribers before each test."""
    event_bus._subscribers = event_bus._SubscriberSet()
    yield
    event_bus._subscribers = event_bus._SubscriberSet()


def test_push_to_ws_does_not_touch_queue_directly() -> None:
    """structural lock: ``_push_to_ws`` must marshal via ``call_soon_threadsafe``."""
    src = inspect.getsource(sidecar_ws._install_subscriber)
    # The marshal pattern is in place.
    assert "loop.call_soon_threadsafe" in src, (
        "_push_to_ws must marshal the enqueue via loop.call_soon_threadsafe (asyncio.Queue is not thread-safe)"
    )
    assert "_enqueue_safe" in src, "_push_to_ws must delegate to _enqueue_safe (the loop-thread helper)"
    # The cross-thread mutations are gone from _push_to_ws. We isolate
    push_start = src.index("def _push_to_ws")
    # Find the end: the next `from voice_typer` import that follows
    push_end = src.index("from voice_typer.server import event_bus", push_start)
    push_body = src[push_start:push_end]
    assert "outbound.full" not in push_body, (
        "_push_to_ws must NOT call outbound.full() directly: that is a "
        "cross-thread mutation of an asyncio.Queue (CR-4 regression)"
    )
    assert "outbound.get_nowait" not in push_body, (
        "_push_to_ws must NOT call outbound.get_nowait() directly: that is "
        "a cross-thread mutation of an asyncio.Queue (CR-4 regression)"
    )
    assert "outbound.put_nowait" not in push_body, (
        "_push_to_ws must NOT call outbound.put_nowait() directly: that is "
        "a cross-thread mutation of an asyncio.Queue (CR-4 regression)"
    )
    # _enqueue_safe is a module-level callable that does the dance.
    assert callable(getattr(sidecar_ws, "_enqueue_safe", None)), "sidecar_ws._enqueue_safe must exist as a callable"
    enqueue_src = inspect.getsource(sidecar_ws._enqueue_safe)
    assert "outbound.full" in enqueue_src and "outbound.put_nowait" in enqueue_src, (
        "_enqueue_safe must perform the full/get_nowait/put_nowait dance (now safely on the loop thread)"
    )


async def test_concurrent_publish_no_events_lost(monkeypatch) -> None:
    """regression: every event from non-loop threads is delivered."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_fake_server()
    ws = make_fake_websocket_parked_after_auth("test-token", park_dispatch=True)

    sent_events: list[dict] = []
    send_block = asyncio.Event()
    send_block.clear()  # writer parks on `await send_block.wait()`

    async def _tracking_send(raw: str) -> None:
        await send_block.wait()
        sent_events.append(json.loads(raw))

    ws.send = _tracking_send

    dispatch = sidecar_ws._make_dispatch(server)
    conn_task = asyncio.create_task(sidecar_ws._handle_connection(ws, server, dispatch))

    try:
        # Wait for the connection to authenticate + install _push_to_ws.
        loop = asyncio.get_event_loop()
        auth_deadline = loop.time() + 1.5
        while loop.time() < auth_deadline:
            if event_bus._subscriber_count() >= 1:
                break
            await asyncio.sleep(0.005)

        # Sanity: _push_to_ws IS subscribed (otherwise the test is moot).
        assert event_bus._subscriber_count() >= 1

        N_THREADS = 4  # noqa: N806
        N_PER_THREAD = 50  # noqa: N806  200 total < maxsize=256 → no drops
        TOTAL = N_THREADS * N_PER_THREAD  # noqa: N806

        # Barrier so all threads start publishing simultaneously,
        barrier = threading.Barrier(N_THREADS)

        def publish_batch(tid: int) -> None:
            barrier.wait()
            for i in range(N_PER_THREAD):
                event_bus.publish({"type": "test_event", "tid": tid, "seq": i})

        threads = [threading.Thread(target=publish_batch, args=(t,), name=f"pub-{t}") for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # All publish threads must have returned (event_bus.publish
        dead = [t.name for t in threads if t.is_alive()]
        assert not dead, f"publish threads deadlocked: {dead}"

        # Yield to the loop until all 200
        loop = asyncio.get_event_loop()
        loop_settle_deadline = loop.time() + 0.5
        while loop.time() < loop_settle_deadline:
            await asyncio.sleep(0)
            ready = getattr(loop, "_ready", None)
            if ready is None or len(ready) == 0:
                break

        # Unblock the writer, let it drain the queue.
        send_block.set()

        # Wait for all events to be delivered.
        deadline = asyncio.get_event_loop().time() + 5.0
        while len(sent_events) < TOTAL and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert len(sent_events) == TOTAL, (
            f"expected {TOTAL} events delivered, got {len(sent_events)}, "
            f"events lost (asyncio.Queue corrupted by cross-thread mutation, "
            f"CR-4 regression)"
        )

        # Every delivered event must carry the published shape
        for ev in sent_events:
            assert ev["type"] == "test_event"
            assert isinstance(ev["tid"], int)
            assert isinstance(ev["seq"], int)
    finally:
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


async def test_concurrent_publish_writer_alive_under_overflow(monkeypatch) -> None:
    """regression: writer task stays alive under heavy queue overflow."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_fake_server()
    ws = make_fake_websocket_parked_after_auth("test-token", park_dispatch=True)

    sent_events: list[dict] = []

    async def _tracking_send(raw: str) -> None:
        sent_events.append(json.loads(raw))

    ws.send = _tracking_send

    dispatch = sidecar_ws._make_dispatch(server)
    conn_task = asyncio.create_task(sidecar_ws._handle_connection(ws, server, dispatch))

    try:
        # Wait for the connection to authenticate + install _push_to_ws.
        loop = asyncio.get_event_loop()
        auth_deadline = loop.time() + 1.5
        while loop.time() < auth_deadline:
            if event_bus._subscriber_count() >= 1:
                break
            await asyncio.sleep(0.005)

        N_THREADS = 8  # noqa: N806
        N_PER_THREAD = 500  # noqa: N806  4000 total >> 256 maxsize → heavy overflow
        N_THREADS * N_PER_THREAD

        barrier = threading.Barrier(N_THREADS)

        def publish_batch(tid: int) -> None:
            barrier.wait()
            for i in range(N_PER_THREAD):
                event_bus.publish({"type": "test_event", "tid": tid, "seq": i})

        threads = [threading.Thread(target=publish_batch, args=(t,), name=f"pub-{t}") for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # All publish threads must have returned, _push_to_ws did
        dead = [t.name for t in threads if t.is_alive()]
        assert not dead, f"publish threads deadlocked: {dead}"

        # Wait for the writer to drain the queue. After publishing
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 5.0
        prev_count = -1
        stable_since: float | None = None
        while loop.time() < deadline:
            current = len(sent_events)
            if current == prev_count:
                if stable_since is None:
                    stable_since = loop.time()
                elif loop.time() - stable_since > 0.3:
                    break  # count stable for 0.3s, queue drained
            else:
                stable_since = None
                prev_count = current
            await asyncio.sleep(0.05)

        # The connection task (parent of the writer) must STILL be
        assert not conn_task.done(), (
            "connection/writer task exited during concurrent publish, "
            "CR-4 regression (queue corruption crashed the loop or "
            "deadlocked the writer)"
        )
        # Some events must have been delivered (the writer was making
        assert len(sent_events) > 0, (
            "no events delivered, writer deadlocked after cross-thread queue mutation (CR-4 regression)"
        )
        # We can't assert an exact count (drop-oldest under overflow
        assert len(sent_events) >= 200, (
            f"writer delivered only {len(sent_events)} events under overflow "
            f"— expected at least ~maxsize (256); writer may have stalled"
        )
    finally:
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


async def test_push_to_ws_swallows_runtime_error_on_closed_loop(monkeypatch) -> None:
    """publishing during loop shutdown must not raise."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_fake_server()
    ws = make_fake_websocket_parked_after_auth("test-token", park_dispatch=True)

    async def _noop_send(raw: str) -> None:
        return None

    ws.send = _noop_send

    dispatch = sidecar_ws._make_dispatch(server)
    conn_task = asyncio.create_task(sidecar_ws._handle_connection(ws, server, dispatch))

    try:
        # Poll ``event_bus._subscriber_count()`` (the subscriber
        loop = asyncio.get_event_loop()
        auth_deadline = loop.time() + 1.5
        while loop.time() < auth_deadline:
            if event_bus._subscriber_count() >= 1:
                break
            await asyncio.sleep(0.005)

        # Capture the loop reference _push_to_ws closed over. We
        running_loop = asyncio.get_running_loop()

        raised: list[bool] = []

        def _raise_runtime_error(*args, **kwargs):
            raised.append(True)
            raise RuntimeError("Event Loop is closed")

        # Patch the bound method on the loop instance via monkeypatch
        monkeypatch.setattr(running_loop, "call_soon_threadsafe", _raise_runtime_error)

        # Publish from a non-loop thread (mimicking real publishers).
        error_in_publish: list[BaseException] = []

        def publish_from_thread() -> None:
            try:
                event_bus.publish({"type": "shutdown_test"})
            except BaseException as exc:  # noqa: BLE001 - test-only
                error_in_publish.append(exc)

        t = threading.Thread(target=publish_from_thread, name="shutdown-pub")
        t.start()
        t.join(timeout=2.0)

        assert not t.is_alive(), "publish thread blocked"
        assert error_in_publish == [], f"_push_to_ws propagated exception during shutdown: {error_in_publish}"
        assert raised, (
            "test setup failed: the stub call_soon_threadsafe was never invoked, _push_to_ws did not actually marshal"
        )
    finally:
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task
