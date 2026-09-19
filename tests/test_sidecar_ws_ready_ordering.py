"""FR-2 regression: the WS subscriber MUST receive the ``ready`` event."""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import event_bus, sidecar_ws  # noqa: E402
from voice_typer.server.ipc_server import IPCServer  # noqa: E402

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service  # noqa: E402
from tests.fixtures.sidecar_ws_test_helpers import make_fake_websocket_parked_after_auth  # noqa: E402


@pytest.fixture
def isolated_event_bus_subscribers():
    """Snapshot + clear ``event_bus._subscribers`` for the test, restore after."""
    original = set(event_bus._subscribers)
    event_bus._subscribers.clear()
    try:
        yield
    finally:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


def _build_authenticating_websocket(token: str) -> MagicMock:
    """Build a fake websocket that authenticates with *token* then yields no frames."""
    return make_fake_websocket_parked_after_auth(token, park_dispatch=False)


@pytest.mark.asyncio
async def test_ws_subscriber_receives_ready_event_on_first_auth(monkeypatch, isolated_event_bus_subscribers) -> None:
    """``ready`` event on the first authenticated WS connection."""
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True
    server.push = MagicMock()

    # Spy on _install_subscriber: capture the real _push_to_ws callback,
    captured_wrappers: list = []
    original_install = sidecar_ws._install_subscriber

    def _spy_install(s, loop, outbound):
        push_to_ws = original_install(s, loop, outbound)
        # Replace the original with a recording wrapper so the caller's
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
    """
    FR-2 ordering guard: ``_install_subscriber`` is called BEFORE
    This is a defense-in-depth test that pins the call order directly
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._ready_emitted = False
    server._running = True
    server.push = MagicMock()

    call_log: list[str] = []

    # Spy on _install_subscriber: record the call, delegate to the real
    original_install = sidecar_ws._install_subscriber

    def _spy_install(s, loop, outbound):
        call_log.append("install_subscriber")
        return original_install(s, loop, outbound)

    monkeypatch.setattr(sidecar_ws, "_install_subscriber", _spy_install)

    # Spy on _emit_ready_if_first: record the call, delegate to the real
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
    install_idx = call_log.index("install_subscriber")
    emit_idx = call_log.index("emit_ready")
    assert install_idx < emit_idx, (
        f"FR-2 contract: _install_subscriber MUST run BEFORE "
        f"_emit_ready_if_first so the WS subscriber is registered when "
        f"the `ready` event is published. Got call_log={call_log} "
        f"(install at {install_idx}, emit at {emit_idx})"
    )
