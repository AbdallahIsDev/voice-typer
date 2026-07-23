"""CR-4 regression: ``_ready_emitted`` must be per-IPCServer-instance.

Before CR-4, ``sidecar_ws._ready_emitted`` was a module-level boolean.
That was correct for production (one ``ready`` event per process), but
it leaked state between test runs that imported the module once and
called ``sidecar_ws.run(server)`` multiple times — even with DIFFERENT
``IPCServer`` instances. A test that ran second would see ``ready`` NOT
emitted because the first test had already set the flag.

The fix moves the flag to ``IPCServer._ready_emitted`` (per-instance),
initialized to ``False`` in ``IPCServer.__init__``. Two fresh
``IPCServer`` instances each get their own ``_ready_emitted = False``
and each emit ``ready`` on their first authenticated WS connection.

This module exercises:

- ``IPCServer._ready_emitted`` is ``False`` on a freshly-constructed
  instance (without the test having to touch the module).
- Two ``IPCServer`` instances do NOT share ``_ready_emitted`` — setting
  one to ``True`` leaves the other ``False``.
- ``_reset_ready_emitted()`` resets the per-instance flag (test-only
  helper).
- ``sidecar_ws._handle_connection`` reads/writes ``server._ready_emitted``
  (not the module-level global), and emits ``ready`` exactly once per
  fresh server instance.
- The module-level ``_ready_emitted`` global is GONE (no leak).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server.ipc_server import IPCServer

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service

# ── Per-instance attribute ─────────────────────────────────────────────


def test_ready_emitted_is_false_on_fresh_instance() -> None:
    """A freshly-constructed IPCServer must have ``_ready_emitted = False``.

    Before CR-4, the flag was module-level — a fresh ``IPCServer`` had
    no per-instance state, so the only way to reset between tests was
    to assign ``sidecar_ws._ready_emitted = False`` manually (which is
    exactly what leaked state between tests that forgot the reset).
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)

    assert hasattr(server, "_ready_emitted"), (
        "IPCServer must have a per-instance _ready_emitted attribute (CR-4: moved from module-level to instance-level)"
    )
    assert server._ready_emitted is False


def test_two_instances_have_independent_ready_emitted() -> None:
    """Two IPCServer instances must NOT share ``_ready_emitted``.

    This is the core CR-4 regression: before the fix, both instances
    shared the module-level global. Setting one to ``True`` (via a
    first-authenticated WS connection) made the other instance also
    appear to have emitted ``ready`` — so a test that constructed a
    fresh ``IPCServer`` after another test's ``IPCServer`` had already
    emitted ``ready`` would silently skip the ``ready`` emission.
    """
    app1 = make_fake_app()
    service1 = make_fake_service()
    server1 = IPCServer(app1, service=service1)

    app2 = make_fake_app()
    service2 = make_fake_service()
    server2 = IPCServer(app2, service=service2)

    # Initially both False.
    assert server1._ready_emitted is False
    assert server2._ready_emitted is False

    # Simulate the first WS connection on server1.
    server1._ready_emitted = True

    # server2 must STILL be False — independent state.
    assert server1._ready_emitted is True
    assert server2._ready_emitted is False, (
        "two IPCServer instances must not share _ready_emitted state — "
        "this was the CR-4 bug (module-level global leaked between tests)"
    )


def test_reset_ready_emitted_sets_flag_back_to_false() -> None:
    """``_reset_ready_emitted()`` is the test-only helper to reset the flag.

    In production we NEVER reset — once ``ready`` is emitted, it stays
    emitted. But tests that reuse the same ``IPCServer`` across multiple
    ``sidecar_ws.run(server)`` calls need to reset between runs. The
    helper is documented as test-only.
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)

    # Simulate ``ready`` having been emitted.
    server._ready_emitted = True
    assert server._ready_emitted is True

    # Reset.
    server._reset_ready_emitted()
    assert server._ready_emitted is False


def test_module_level_ready_emitted_is_gone() -> None:
    """The module-level ``_ready_emitted`` global must NOT exist.

    CR-4 moved the flag to per-instance state. Leaving the module-level
    global around would be a footgun — code that imports
    ``sidecar_ws._ready_emitted`` would silently keep working but read
    a stale value that's never updated. Asserting the attribute is
    gone locks the migration in.
    """
    from voice_typer.server import sidecar_ws

    assert not hasattr(sidecar_ws, "_ready_emitted"), (
        "sidecar_ws._ready_emitted module-level global must be removed "
        "(CR-4: moved to IPCServer._ready_emitted per-instance attribute)"
    )


# ── sidecar_ws._handle_connection uses the per-instance flag ──────────


async def test_handle_connection_emits_ready_on_first_auth(monkeypatch) -> None:
    """``sidecar_ws._handle_connection`` emits ``ready`` on first auth.

    After CR-4, the flag it checks is ``server._ready_emitted``, not
    a module-level global. A fresh ``IPCServer`` instance (which has
    ``_ready_emitted = False`` by default) MUST emit ``ready`` on its
    first authenticated WS connection.
    """
    from voice_typer.server import event_bus, sidecar_ws

    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True
    server.push = MagicMock()  # event_bus subscriber install path

    # Track ready emissions via event_bus.publish spy.
    published: list[dict] = []
    original_publish = event_bus.publish

    def spy(event: dict) -> None:
        published.append(event)
        # Don't actually publish — we don't want other subscribers
        # to fire during this test.
        return None

    monkeypatch.setattr(event_bus, "publish", spy)

    # Set the auth token + build a fake websocket.
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")
    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": "test-token"}).encode()
    ws.recv = MagicMock(return_value=auth_frame)
    # Use an AsyncMock-style approach: _authenticate awaits websocket.recv()

    async def _fake_recv():
        return auth_frame

    ws.recv = _fake_recv
    ws.close = MagicMock()

    # Make websocket iterable: yield nothing (the test only cares about
    # the post-auth ``ready`` emission, not the dispatch loop).
    async def _aiter_nothing():
        if False:  # pragma: no cover - never enters
            yield b""

    # The `async for raw in websocket:` form calls __aiter__ on the
    # websocket object. Build a tiny async iterator that yields nothing.
    class _EmptyAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    # Patch __aiter__ on the MagicMock — MagicMock doesn't auto-build
    # async iter protocol, so we attach it explicitly.
    ws.__aiter__ = lambda: _EmptyAsyncIter()
    ws.remote_address = ("127.0.0.1", 12345)
    ws.send = MagicMock()  # don't actually await anything

    # _handle_connection also creates a writer task that calls
    # websocket.send. We don't want that to hang, so make send a no-op
    # coroutine.
    async def _noop_send(*a, **kw):
        return None

    ws.send = _noop_send

    dispatch = sidecar_ws._make_dispatch(server)

    try:
        await sidecar_ws._handle_connection(ws, server, dispatch)
    except Exception:
        # Connection-cleanup exceptions are fine for this test — we
        # only care that ``ready`` was published BEFORE the dispatch
        # loop started.
        pass

    # The ``ready`` event must have been published exactly once.
    ready_events = [e for e in published if e.get("type") == "ready"]
    assert len(ready_events) == 1, (
        f"expected exactly one `ready` event published on first auth, got {len(ready_events)}: {ready_events}"
    )
    # The per-instance flag must now be True.
    assert server._ready_emitted is True


async def test_handle_connection_does_not_re_emit_ready_on_reconnect(
    monkeypatch,
) -> None:
    """If ``server._ready_emitted`` is already True, ``ready`` is NOT re-emitted.

    This is the original production guarantee: a transient WS reconnect
    after a drop does NOT re-emit ``ready`` (the host already hydrated
    the UI on the first connection). The per-instance flag (was
    module-level) preserves this.
    """
    from voice_typer.server import event_bus, sidecar_ws

    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True
    server.push = MagicMock()
    # Simulate a PRIOR connection having already emitted ``ready``.
    server._ready_emitted = True

    published: list[dict] = []

    def spy(event: dict) -> None:
        published.append(event)
        return None

    monkeypatch.setattr(event_bus, "publish", spy)

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")
    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": "test-token"}).encode()

    async def _fake_recv():
        return auth_frame

    ws.recv = _fake_recv
    ws.close = MagicMock()

    class _EmptyAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    ws.__aiter__ = lambda: _EmptyAsyncIter()
    ws.remote_address = ("127.0.0.1", 12345)

    async def _noop_send(*a, **kw):
        return None

    ws.send = _noop_send

    dispatch = sidecar_ws._make_dispatch(server)

    try:
        await sidecar_ws._handle_connection(ws, server, dispatch)
    except Exception:
        pass

    ready_events = [e for e in published if e.get("type") == "ready"]
    assert len(ready_events) == 0, (
        "ready event must NOT be re-emitted on reconnect — per-instance "
        "flag preserves the production 'one ready per server' guarantee"
    )
    # Flag still True.
    assert server._ready_emitted is True


async def test_two_fresh_servers_each_emit_ready(monkeypatch) -> None:
    """Two fresh IPCServer instances each emit ``ready`` exactly once.

    This is the integration-level CR-4 regression: before the fix, the
    module-level global meant the SECOND fresh server would NOT emit
    ``ready`` because the first had already set the flag. With per-instance
    state, both emit ``ready`` on their first connection.
    """
    from voice_typer.server import event_bus, sidecar_ws

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "shared-token")

    async def _emit_ready_for_fresh_server() -> int:
        """Construct a fresh IPCServer + run a single auth → count `ready`."""
        app = make_fake_app()
        service = make_fake_service()
        server = IPCServer(app, service=service)
        server._running = True
        server.push = MagicMock()

        ready_count = 0
        original_publish = event_bus.publish

        def spy(event: dict) -> None:
            nonlocal ready_count
            if event.get("type") == "ready":
                ready_count += 1
            return None

        monkeypatch.setattr(event_bus, "publish", spy)

        ws = MagicMock()
        auth_frame = json.dumps({"type": "auth", "token": "shared-token"}).encode()

        async def _fake_recv():
            return auth_frame

        ws.recv = _fake_recv
        ws.close = MagicMock()

        class _EmptyAsyncIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        ws.__aiter__ = lambda: _EmptyAsyncIter()
        ws.remote_address = ("127.0.0.1", 12345)

        async def _noop_send(*a, **kw):
            return None

        ws.send = _noop_send

        dispatch = sidecar_ws._make_dispatch(server)
        try:
            await sidecar_ws._handle_connection(ws, server, dispatch)
        except Exception:
            pass

        # Restore publish for the next iteration.
        monkeypatch.setattr(event_bus, "publish", original_publish)
        return ready_count

    count1 = await _emit_ready_for_fresh_server()
    count2 = await _emit_ready_for_fresh_server()

    assert count1 == 1, f"first fresh server should emit ready once (got {count1})"
    assert count2 == 1, (
        f"second fresh server should ALSO emit ready once (got {count2}) — "
        f"this is the CR-4 regression: module-level global leaked state "
        f"between tests, suppressing the second emission"
    )
