"""FR-2 regression: the WS subscriber MUST receive the ``ready`` event.

Before FR-2, ``sidecar_ws._handle_connection_inner`` called
``_emit_ready_if_first(server)`` BEFORE ``_install_subscriber(server,
loop, outbound)``. The ``ready`` event was broadcast via
``event_bus.publish`` synchronously to the subscriber set, which did
NOT yet include the WS subscriber (``_push_to_ws``). The event was
delivered only to other transports' subscribers (e.g. ``server.push``
on the TCP path, which writes to ``_pending_tcp`` — a buffer with no
consumer in WS mode). The WS writer task's outbound queue never
received ``ready``, so the Tauri host never got ``ready`` over the WS
on first connection and the UI stayed un-hydrated until the next push
event arrived.

The fix swaps the order: ``_install_subscriber`` runs FIRST (registering
``_push_to_ws`` on ``event_bus``), THEN ``_emit_ready_if_first``
publishes ``ready``. Now the WS subscriber is in the set when
``publish`` runs, so the event flows through the outbound queue →
writer task → WS frame → host.

These tests pin the ordering by capturing the ``_push_to_ws`` callback
(via a spy on ``_install_subscriber``) and asserting it received the
``ready`` event. The existing ``test_sidecar_ready_emitted.py`` tests
only verify that ``event_bus.publish`` was CALLED (via a publish spy
that replaces the real publish) — they do NOT verify the WS subscriber
actually received the event, so they would pass against the pre-FR-2
buggy ordering. These tests close that gap.
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import event_bus, sidecar_ws  # noqa: E402
from voice_typer.server.ipc_server import IPCServer  # noqa: E402

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service  # noqa: E402


@pytest.fixture
def isolated_event_bus_subscribers():
    """Snapshot + clear ``event_bus._subscribers`` for the test, restore after.

    Mirrors the pattern in ``tests/server/conftest.py`` and
    ``tests/test_event_bus.py``. Without this, leftover subscribers
    from prior tests (e.g. an uncleaned ``server.push``) would receive
    the ``ready`` event published by ``_emit_ready_if_first`` and could
    raise (a bare ``MagicMock`` is fine, but a configured mock with
    side_effect would break the test). Clearing isolates the test to
    ONLY the subscribers registered during this test (the WS
    ``_push_to_ws`` captured by the spy + any test-local subscribers).
    """
    original = set(event_bus._subscribers)
    event_bus._subscribers.clear()
    try:
        yield
    finally:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


def _build_authenticating_websocket(token: str) -> MagicMock:
    """Build a fake websocket that authenticates with *token* then yields no frames."""
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 12345)
    auth_frame = json.dumps({"type": "auth", "token": token}).encode()

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

    async def _noop_send(*a, **kw):
        return None

    ws.send = _noop_send
    return ws


@pytest.mark.asyncio
async def test_ws_subscriber_receives_ready_event_on_first_auth(monkeypatch, isolated_event_bus_subscribers) -> None:
    """FR-2: the WS subscriber (``_push_to_ws``) MUST receive the
    ``ready`` event on the first authenticated WS connection.

    Pre-FR-2 the emit ran BEFORE the subscriber was registered, so the
    event was published to a subscriber set that did not include
    ``_push_to_ws`` — the WS writer task's outbound queue never received
    it and the Tauri host never got ``ready`` over the WS.

    The test captures the ``_push_to_ws`` callback by spying on
    ``_install_subscriber`` (wrapping the returned callable with a
    recorder), then asserts the recorder saw ``{"type": "ready"}``.
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True
    # ``server.push`` may be auto-invoked by event_bus publishes if a
    # previous test left it subscribed; the isolated_event_bus_subscribers
    # fixture clears the set, but we also stub push to be safe.
    server.push = MagicMock()

    # Spy on _install_subscriber: capture the real _push_to_ws callback,
    # unsubscribe the original, and re-subscribe a wrapper that records
    # every event it receives. The wrapper delegates to the original so
    # the outbound queue still gets the event (preserving production
    # behavior for the writer task).
    captured_wrappers: list = []
    original_install = sidecar_ws._install_subscriber

    def _spy_install(s, loop, outbound):
        push_to_ws = original_install(s, loop, outbound)
        # Replace the original with a recording wrapper so the caller's
        # finally-block ``event_bus.unsubscribe(_push_to_ws)`` correctly
        # removes the wrapper (not a dangling original).
        event_bus.unsubscribe(push_to_ws)

        def _wrapped(event: dict) -> None:
            _wrapped.received.append(event)  # type: ignore[attr-defined]
            return push_to_ws(event)

        _wrapped.received = []  # type: ignore[attr-defined]
        event_bus.subscribe(_wrapped)
        captured_wrappers.append(_wrapped)
        return _wrapped

    monkeypatch.setattr(sidecar_ws, "_install_subscriber", _spy_install)

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")
    ws = _build_authenticating_websocket("test-token")

    dispatch = sidecar_ws._make_dispatch(server)
    with contextlib.suppress(Exception):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    # The WS subscriber MUST have been registered exactly once.
    assert len(captured_wrappers) == 1, (
        f"expected exactly one _push_to_ws subscriber to be registered, got {len(captured_wrappers)}"
    )
    # The WS subscriber MUST have received the ``ready`` event.
    received = captured_wrappers[0].received  # type: ignore[attr-defined]
    ready_events = [e for e in received if e.get("type") == "ready"]
    assert len(ready_events) == 1, (
        f"WS subscriber (_push_to_ws) MUST receive the `ready` event on "
        f"first auth (FR-2 regression). Events received by subscriber: "
        f"{received}"
    )
    # The per-instance flag must now be True (ready was emitted).
    assert server._ready_emitted is True


@pytest.mark.asyncio
async def test_ws_subscriber_not_registered_before_ready_emit_regression_guard(
    monkeypatch, isolated_event_bus_subscribers
) -> None:
    """FR-2 ordering guard: ``_install_subscriber`` is called BEFORE
    ``_emit_ready_if_first`` publishes the ``ready`` event.

    This is a defense-in-depth test that pins the call order directly
    (independent of the subscriber-receipt assertion above). If a future
    refactor reverts the order, this test fails immediately with a clear
    message about the FR-2 contract.
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._ready_emitted = False
    server._running = True
    server.push = MagicMock()

    call_log: list[str] = []

    # Spy on _install_subscriber: record the call, delegate to the real
    # implementation.
    original_install = sidecar_ws._install_subscriber

    def _spy_install(s, loop, outbound):
        call_log.append("install_subscriber")
        return original_install(s, loop, outbound)

    monkeypatch.setattr(sidecar_ws, "_install_subscriber", _spy_install)

    # Spy on _emit_ready_if_first: record the call, delegate to the real
    # implementation (which publishes ``ready`` if the flag is False).
    original_emit = sidecar_ws._emit_ready_if_first

    def _spy_emit(s):
        call_log.append("emit_ready")
        return original_emit(s)

    monkeypatch.setattr(sidecar_ws, "_emit_ready_if_first", _spy_emit)

    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token")
    ws = _build_authenticating_websocket("test-token")

    dispatch = sidecar_ws._make_dispatch(server)
    with contextlib.suppress(Exception):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    # Both must have been called exactly once.
    assert call_log.count("install_subscriber") == 1, (
        f"expected _install_subscriber called once, got call_log={call_log}"
    )
    assert call_log.count("emit_ready") == 1, f"expected _emit_ready_if_first called once, got call_log={call_log}"
    # install_subscriber MUST come BEFORE emit_ready.
    install_idx = call_log.index("install_subscriber")
    emit_idx = call_log.index("emit_ready")
    assert install_idx < emit_idx, (
        f"FR-2 contract: _install_subscriber MUST run BEFORE "
        f"_emit_ready_if_first so the WS subscriber is registered when "
        f"the `ready` event is published. Got call_log={call_log} "
        f"(install at {install_idx}, emit at {emit_idx})"
    )
