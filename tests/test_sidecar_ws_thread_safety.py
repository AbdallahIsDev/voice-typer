"""CR-4 regression: ``sidecar_ws`` outbound queue must be thread-safe.

Before CR-4, ``sidecar_ws._push_to_ws`` was registered as an
``event_bus`` subscriber and called ``outbound.full() / get_nowait()
/ put_nowait()`` directly on an ``asyncio.Queue`` from whatever
thread invoked ``event_bus.publish()``. Since ``event_bus.publish``
is called from many non-event-loop threads — transcription, hotkey,
tray, IPC dispatch workers (``server._dispatch`` runs via
``loop.run_in_executor``), and the audio-worker deferred path — and
``asyncio.Queue`` is explicitly NOT thread-safe, the queue's
internal deque + ``_getawaiter`` / ``_putawaiter`` futures could
corrupt. Observed symptoms (per CR-4 in comprehensive-review.md):

- silently dropped events (``transcription_final`` never reached the
  Tauri host → user sees no dictated text),
- a deadlocked writer task (``await outbound.get()`` never wakes
  after a cross-thread ``put_nowait``),
- a hard asyncio loop crash killing the sidecar (→ FT-1 respawn
  loop).

The fix captures ``loop = asyncio.get_running_loop()`` once at
connection setup and marshals the enqueue via
``loop.call_soon_threadsafe(_enqueue_safe, outbound, event)`` so
every queue mutation happens on the event-loop thread.

These regression tests publish events from multiple non-event-loop
threads and verify:

1. **No events are lost under low load** (total < maxsize=256) —
   every published event is delivered to ``websocket.send``. This
   proves the queue's internal state is not corrupted by cross-thread
   mutation (pre-fix, ``put_nowait`` from a non-loop thread could
   fail to wake the writer's ``await outbound.get()``).

2. **Writer does not deadlock under overflow** (total >> maxsize=256)
   — the writer task stays alive and continues making progress after
   thousands of concurrent publishes. Pre-fix, the queue's internal
   deque could corrupt, hanging the writer forever.

3. **Structural guard**: ``_push_to_ws`` no longer touches the queue
   directly — it delegates to ``_enqueue_safe`` via
   ``call_soon_threadsafe``. A grep-level assertion locks this in.

4. **Shutdown safety**: publishing during loop shutdown raises
   ``RuntimeError`` from ``call_soon_threadsafe``; ``_push_to_ws``
   must swallow it (no traceback per published event during FT-1
   respawn).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import threading
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import event_bus, sidecar_ws  # noqa: E402

# ─── Helpers ───────────────────────────────────────────────────────────


def _make_fake_server() -> MagicMock:
    """Build a fake IPCServer with the attributes _handle_connection needs.

    ``_ready_emitted=True`` skips the post-auth ``ready`` emission so
    the tests can focus on the queue path (and so the spied
    ``event_bus.publish`` doesn't see a stray ``ready`` from setup).
    """
    server = MagicMock()
    server._dispatch = MagicMock(return_value=None)
    server.app = MagicMock()
    server.push = MagicMock()
    server._ready_emitted = True
    return server


class _BlockingAsyncIter:
    """Async iterator that never yields — keeps the dispatch loop parked.

    The ``async for raw in websocket:`` loop in ``_handle_connection``
    blocks on ``__anext__`` until the connection task is cancelled.
    This lets the tests hold the connection OPEN (so the writer task
    and ``_push_to_ws`` subscriber stay installed) while publishing
    events from non-loop threads.
    """

    def __aiter__(self) -> _BlockingAsyncIter:
        return self

    async def __anext__(self):
        # Never resolves — the only way out is cancellation, which
        # raises CancelledError (a BaseException, NOT caught by the
        # connection's `except Exception:` clause, so the `finally:`
        # cleanup still runs).
        await asyncio.Future()
        raise StopAsyncIteration  # pragma: no cover - unreachable


def _make_fake_websocket(auth_token: str) -> MagicMock:
    """Build a mock websocket that authenticates then blocks forever.

    The mock's ``recv`` returns the auth frame once (consumed by
    ``_authenticate``), then the async-iter protocol parks the
    dispatch loop. ``send`` is left unset so each test can wire its
    own tracking coroutine.
    """
    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": auth_token}).encode()

    async def _fake_recv() -> bytes:
        return auth_frame

    ws.recv = _fake_recv
    ws.close = MagicMock()
    ws.remote_address = ("127.0.0.1", 12345)
    # MagicMock wraps an assigned magic-method function as a method on
    # the type, so the lambda receives `self` (the mock instance) as
    # its first positional arg. We accept and ignore it.
    ws.__aiter__ = lambda self: _BlockingAsyncIter()  # noqa: E731
    return ws


# ─── Structural guard ─────────────────────────────────────────────────


def test_push_to_ws_does_not_touch_queue_directly() -> None:
    """CR-4 structural lock: ``_push_to_ws`` must marshal via ``call_soon_threadsafe``.

    Before the fix, ``_push_to_ws`` called ``outbound.full()``,
    ``outbound.get_nowait()``, and ``outbound.put_nowait()`` inline.
    Those calls happen in the publisher's thread (transcription,
    hotkey, tray, IPC workers), and ``asyncio.Queue`` is not
    thread-safe — direct mutation corrupts its internal deque + Future
    state.

    The fix moves the queue dance into a separate ``_enqueue_safe``
    helper that runs ON the event-loop thread, scheduled via
    ``loop.call_soon_threadsafe``. This test inspects the source of
    ``_handle_connection`` (where ``_push_to_ws`` is defined as a
    closure) and asserts that:

    - ``_push_to_ws`` references ``loop.call_soon_threadsafe`` and
      ``_enqueue_safe`` (marshal pattern in place).
    - ``_push_to_ws`` does NOT reference ``outbound.full``,
      ``outbound.get_nowait``, or ``outbound.put_nowait`` (the
      cross-thread mutations are gone).
    - ``_enqueue_safe`` exists as a module-level callable that
      performs the dance.
    """
    src = inspect.getsource(sidecar_ws._handle_connection)
    # The marshal pattern is in place.
    assert "loop.call_soon_threadsafe" in src, (
        "_push_to_ws must marshal the enqueue via loop.call_soon_threadsafe (CR-4: asyncio.Queue is not thread-safe)"
    )
    assert "_enqueue_safe" in src, "_push_to_ws must delegate to _enqueue_safe (the loop-thread helper)"
    # The cross-thread mutations are gone from _push_to_ws. We isolate
    # the _push_to_ws body by slicing between its `def` and the next
    # `def`/`from`/top-level statement.
    push_start = src.index("def _push_to_ws")
    # Find the end: the next `from voice_typer` import that follows
    # _push_to_ws in _handle_connection.
    push_end = src.index("from voice_typer.server import event_bus", push_start)
    push_body = src[push_start:push_end]
    assert "outbound.full" not in push_body, (
        "_push_to_ws must NOT call outbound.full() directly — that is a "
        "cross-thread mutation of an asyncio.Queue (CR-4 regression)"
    )
    assert "outbound.get_nowait" not in push_body, (
        "_push_to_ws must NOT call outbound.get_nowait() directly — that is "
        "a cross-thread mutation of an asyncio.Queue (CR-4 regression)"
    )
    assert "outbound.put_nowait" not in push_body, (
        "_push_to_ws must NOT call outbound.put_nowait() directly — that is "
        "a cross-thread mutation of an asyncio.Queue (CR-4 regression)"
    )
    # _enqueue_safe is a module-level callable that does the dance.
    assert callable(getattr(sidecar_ws, "_enqueue_safe", None)), "sidecar_ws._enqueue_safe must exist as a callable"
    enqueue_src = inspect.getsource(sidecar_ws._enqueue_safe)
    assert "outbound.full" in enqueue_src and "outbound.put_nowait" in enqueue_src, (
        "_enqueue_safe must perform the full/get_nowait/put_nowait dance (now safely on the loop thread)"
    )


# ─── Behavioral: no events lost under low load ────────────────────────


async def test_concurrent_publish_no_events_lost(monkeypatch) -> None:
    """CR-4 regression: every event from non-loop threads is delivered.

    Publishes ``N_THREADS * N_PER_THREAD`` events from non-event-loop
    threads while the writer is paused (so the queue fills up). With
    the total kept under ``maxsize=256``, NO events should be dropped
    by the drop-oldest policy — every published event must reach
    ``websocket.send``. After unblocking the writer, we count the
    delivered events and assert equality.

    Before the fix, ``put_nowait`` from a non-loop thread could
    corrupt the queue's internal deque + ``_putawaiter`` Future,
    causing ``await outbound.get()`` in the writer to miss the
    wakeup. The result: silently dropped events (e.g.
    ``transcription_final`` never reaching the Tauri host → the
    user's dictated text vanishes).

    The writer is paused via an ``asyncio.Event`` so we deterministically
    fill the queue without timing-dependent races against the writer's
    drain rate.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_fake_server()
    ws = _make_fake_websocket("test-token")

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
        await asyncio.sleep(0.15)

        # Sanity: _push_to_ws IS subscribed (otherwise the test is moot).
        assert event_bus._subscriber_count() >= 1

        N_THREADS = 4  # noqa: N806
        N_PER_THREAD = 50  # noqa: N806  200 total < maxsize=256 → no drops
        TOTAL = N_THREADS * N_PER_THREAD  # noqa: N806

        # Barrier so all threads start publishing simultaneously,
        # maximizing the chance of cross-thread contention on the
        # queue (the exact scenario CR-4 fixes).
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
        # did not hang inside _push_to_ws).
        dead = [t.name for t in threads if t.is_alive()]
        assert not dead, f"publish threads deadlocked: {dead}"

        # Give the loop a moment to process the marshaled
        # call_soon_threadsafe callbacks (they fire on the next
        # loop tick, not synchronously).
        await asyncio.sleep(0.2)

        # Unblock the writer — let it drain the queue.
        send_block.set()

        # Wait for all events to be delivered.
        deadline = asyncio.get_event_loop().time() + 5.0
        while len(sent_events) < TOTAL and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        assert len(sent_events) == TOTAL, (
            f"expected {TOTAL} events delivered, got {len(sent_events)} — "
            f"events lost (asyncio.Queue corrupted by cross-thread mutation, "
            f"CR-4 regression)"
        )

        # Every delivered event must carry the published shape
        # (no corruption of the event dict itself).
        for ev in sent_events:
            assert ev["type"] == "test_event"
            assert isinstance(ev["tid"], int)
            assert isinstance(ev["seq"], int)
    finally:
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


# ─── Behavioral: writer does not deadlock under overflow ──────────────


async def test_concurrent_publish_writer_alive_under_overflow(monkeypatch) -> None:
    """CR-4 regression: writer task stays alive under heavy queue overflow.

    Publishes far more events than the queue can hold (maxsize=256)
    from 8 non-event-loop threads simultaneously. The drop-oldest
    policy kicks in, but the writer task must NOT deadlock or crash.

    Before the fix, concurrent ``put_nowait`` / ``get_nowait`` from
    non-loop threads could corrupt the queue's internal deque +
    ``_getawaiter`` Future, causing the writer's
    ``await outbound.get()`` to hang forever (no wakeup) — a
    deadlock that nothing in the system recovers from until the
    process is killed.

    The assertion is twofold:

    1. The connection task (which owns the writer task) is STILL
       alive after all publish threads have returned — proving the
       writer didn't crash and the dispatch loop didn't error out.
    2. Some events were delivered (``len(sent_events) > 0``) —
       proving the writer was making progress and not parked on a
       dead Future.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_fake_server()
    ws = _make_fake_websocket("test-token")

    sent_events: list[dict] = []

    async def _tracking_send(raw: str) -> None:
        sent_events.append(json.loads(raw))

    ws.send = _tracking_send

    dispatch = sidecar_ws._make_dispatch(server)
    conn_task = asyncio.create_task(sidecar_ws._handle_connection(ws, server, dispatch))

    try:
        # Wait for the connection to authenticate + install _push_to_ws.
        await asyncio.sleep(0.15)

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

        # All publish threads must have returned — _push_to_ws did
        # not hang (call_soon_threadsafe is non-blocking from the
        # caller's perspective).
        dead = [t.name for t in threads if t.is_alive()]
        assert not dead, f"publish threads deadlocked: {dead}"

        # Wait for the writer to drain the queue. After publishing
        # stops, the writer should drain the remaining ~maxsize events
        # quickly (the mock send is just a list append).
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
                    break  # count stable for 0.3s — queue drained
            else:
                stable_since = None
                prev_count = current
            await asyncio.sleep(0.05)

        # The connection task (parent of the writer) must STILL be
        # alive — the writer did not crash, the dispatch loop did not
        # raise, the asyncio loop did not die.
        assert not conn_task.done(), (
            "connection/writer task exited during concurrent publish — "
            "CR-4 regression (queue corruption crashed the loop or "
            "deadlocked the writer)"
        )
        # Some events must have been delivered (the writer was making
        # progress, not parked on a dead Future).
        assert len(sent_events) > 0, (
            "no events delivered — writer deadlocked after cross-thread queue mutation (CR-4 regression)"
        )
        # We can't assert an exact count (drop-oldest under overflow
        # is timing-dependent), but we CAN assert the writer drained
        # at least maxsize events (the steady-state queue depth when
        # the writer is keeping up under heavy publish load).
        assert len(sent_events) >= 200, (
            f"writer delivered only {len(sent_events)} events under overflow "
            f"— expected at least ~maxsize (256); writer may have stalled"
        )
    finally:
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task


# ─── Behavioral: shutdown safety ──────────────────────────────────────


async def test_push_to_ws_swallows_runtime_error_on_closed_loop(monkeypatch) -> None:
    """CR-4: publishing during loop shutdown must not raise.

    When the sidecar is shutting down (FT-1 respawn, host kill), the
    event loop is closed. ``loop.call_soon_threadsafe`` raises
    ``RuntimeError`` in that state. ``_push_to_ws`` must swallow it
    (drop the event at DEBUG level) — otherwise every event published
    during teardown would propagate a traceback through
    ``event_bus._deliver`` (which itself swallows exceptions, but the
    log noise per published event during shutdown is unacceptable).

    This test synthesizes the closed-loop condition by constructing
    a fresh, non-running loop and verifying ``_push_to_ws`` returns
    cleanly when ``call_soon_threadsafe`` raises ``RuntimeError``.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")

    server = _make_fake_server()
    ws = _make_fake_websocket("test-token")

    async def _noop_send(raw: str) -> None:
        return None

    ws.send = _noop_send

    dispatch = sidecar_ws._make_dispatch(server)
    conn_task = asyncio.create_task(sidecar_ws._handle_connection(ws, server, dispatch))

    try:
        await asyncio.sleep(0.15)

        # Capture the loop reference _push_to_ws closed over. We
        # can't reach it directly, but we can replace
        # loop.call_soon_threadsafe on the running loop with a stub
        # that raises RuntimeError (mimicking a closed loop).
        running_loop = asyncio.get_running_loop()

        raised: list[bool] = []

        def _raise_runtime_error(*args, **kwargs):
            raised.append(True)
            raise RuntimeError("Event Loop is closed")

        # Patch the bound method on the loop instance via monkeypatch
        # (auto-restored at test teardown). The closure in _push_to_ws
        # captures `loop` (the same instance), so its
        # `loop.call_soon_threadsafe(...)` attribute lookup hits our
        # stub.
        monkeypatch.setattr(running_loop, "call_soon_threadsafe", _raise_runtime_error)

        # Publish from a non-loop thread (mimicking real publishers).
        # _push_to_ws is called synchronously in that thread; it MUST
        # swallow the RuntimeError and return cleanly.
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
            "test setup failed: the stub call_soon_threadsafe was never invoked — _push_to_ws did not actually marshal"
        )
    finally:
        conn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await conn_task
