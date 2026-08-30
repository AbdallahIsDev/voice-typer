"""2026-08-30 tray-Restart postmortem regression guards.

Three fixes live here (each mirrors one observation from the incident):

1. ``relaunch_ack`` INLINE fast-path in ``_read_loop`` — the host's ack
   used to be routed through the dispatch closure (rate limiter +
   ``ws_dispatch_pool`` executor round-trip) and raced the sidecar's
   0.5 s ``wait_for_relaunch_ack`` timeout, producing a double-restart.
2. ``run()`` clean stop — ``ws_graceful_shutdown`` stops the asyncio
   loop via ``loop.stop()`` while ``_main``'s ``await asyncio.Future()``
   is pending; asyncio.run raises "Event loop stopped before Future
   completed". That is the DESIGNED stop path and must log INFO +
   return 0, not an ERROR traceback + exit 1.
3. ``_ws_graceful_stop_requested`` flag — set by
   ``ws_graceful_shutdown`` before scheduling ``loop.stop``, read by
   ``_is_graceful_loop_stop``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from voice_typer.server.sidecar_ws import (
    _is_graceful_loop_stop,
    _read_loop,
)
from voice_typer.server.sidecar_ws_internals.graceful_shutdown import (
    _attach_ws_graceful_shutdown,
)


class _FrameIterableWebsocket:
    """Fake websocket yielding pre-scripted inbound frames (as JSON
    text, like the real ``websockets`` server does) and recording every
    outbound ``send``."""

    def __init__(self, *frames: dict) -> None:
        self._frames = [json.dumps(f) for f in frames]
        self.sent: list[object] = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for raw in self._frames:
            yield raw

    async def send(self, data: object) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_relaunch_ack_sets_event_inline_without_dispatch() -> None:
    """``relaunch_ack`` must set ``_relaunch_ack_event`` INLINE — never
    reach the dispatch closure (that pool round-trip is what raced the
    0.5 s timeout in the postmortem)."""
    event = threading.Event()
    server = SimpleNamespace(_relaunch_ack_event=event)
    dispatch_calls: list[dict] = []

    async def dispatch(msg, websocket):  # pragma: no cover — must NOT run
        dispatch_calls.append(msg)
        return None

    ws = _FrameIterableWebsocket({"type": "relaunch_ack", "id": 30})

    await asyncio.wait_for(_read_loop(ws, server, dispatch), timeout=3.0)

    assert event.is_set(), "relaunch_ack fast-path must set _relaunch_ack_event"
    assert dispatch_calls == [], (
        "relaunch_ack must NOT be routed through the dispatch closure "
        "(executor round-trip raced the 0.5s ack timeout in the tray-"
        "Restart postmortem)"
    )
    assert ws.sent == [], "relaunch_ack is fire-and-forget — no response frame"


@pytest.mark.asyncio
async def test_other_frames_still_go_through_dispatch() -> None:
    """Guard against over-broadening the fast-path: a normal command
    frame must still reach the dispatch closure."""
    server = SimpleNamespace(_relaunch_ack_event=threading.Event())
    dispatch_calls: list[dict] = []

    async def dispatch(msg, websocket):
        dispatch_calls.append(msg)
        return {"type": "result", "data": {"ok": True}}

    ws = _FrameIterableWebsocket({"type": "get_status", "id": 7})

    await asyncio.wait_for(_read_loop(ws, server, dispatch), timeout=3.0)

    assert [m["type"] for m in dispatch_calls] == ["get_status"]
    # The result was echoed back with the request id, via _safe_send →
    # TEXT frame (C-WS-2).
    assert len(ws.sent) == 1 and isinstance(ws.sent[0], str)
    assert json.loads(ws.sent[0])["id"] == 7


def test_is_graceful_loop_stop_classification() -> None:
    """``_is_graceful_loop_stop`` requires BOTH the shutdown flag AND
    the canonical asyncio message — any other RuntimeError (or an
    unrequested loop stop) stays fatal."""
    exc = RuntimeError("Event loop stopped before Future completed.")

    flagged = SimpleNamespace(_ws_graceful_stop_requested=True)
    unflagged = SimpleNamespace(_ws_graceful_stop_requested=False)
    unset = SimpleNamespace()

    assert _is_graceful_loop_stop(flagged, exc) is True
    assert _is_graceful_loop_stop(unflagged, exc) is False
    assert _is_graceful_loop_stop(unset, exc) is False
    # Right message but no flag → fatal (unknown stop origin).
    assert _is_graceful_loop_stop(flagged, RuntimeError("other")) is False


def test_ws_graceful_shutdown_sets_stop_requested_flag() -> None:
    """``ws_graceful_shutdown`` must set ``_ws_graceful_stop_requested``
    BEFORE scheduling ``loop.stop()`` — ``run()`` keys its clean-exit
    classification on this flag. (``_ws_loop=None`` exercises the
    no-loop branch; the flag is set unconditionally before the loop
    checks, which is exactly the contract ``run()`` relies on when the
    loop stop lands.)"""
    server = SimpleNamespace(
        _ws_authenticated_conns=set(),
        _ws_dispatch_futures=set(),
        _ws_loop=None,
        stop=lambda: None,
    )
    _attach_ws_graceful_shutdown(server)

    server.ws_graceful_shutdown()

    assert getattr(server, "_ws_graceful_stop_requested", False) is True
