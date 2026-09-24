"""Dispatch resilience: bounded lock acquisition + reserved readonly pool.

A stuck mutating handler must degrade to a loud retryable ``server.busy``
error (never an infinite hang), and readonly commands must keep flowing on
their reserved pool even when every main-pool worker is queued behind the
dispatch lock (the all-commands-timeout outage class).
"""

from __future__ import annotations

import threading
import time
import types

from voice_typer.server.ipc import dispatcher as dispatcher_mod
from voice_typer.server.ipc.dispatcher import DispatcherMixin
from voice_typer.server.sidecar_ws_internals import dispatch as ws_dispatch_mod


class _StubServer(DispatcherMixin):
    _COMMAND_REGISTRY = {"ping_mut": "_handle_ping_mut"}

    def __init__(self) -> None:
        self._dispatch_lock = threading.RLock()
        self._cached_shutting_down = False

    def _handle_ping_mut(self, data, resp):
        resp["type"] = "result"
        resp["data"] = {"ok": True}
        return resp


def _result_code(result) -> str:
    assert isinstance(result, dict)
    assert isinstance(result.get("data"), dict)
    return result["data"].get("code", "")


def test_mutating_command_succeeds_when_lock_free() -> None:
    server = _StubServer()
    result = server._dispatch({"type": "ping_mut", "id": 1})
    assert result is not None
    assert result.get("type") == "result"
    assert _result_code(result) == ""


def test_mutating_command_busy_when_lock_held(monkeypatch) -> None:
    # Production runs handlers on pool worker threads, so hold the lock on
    # the main thread and dispatch from a worker (same-thread acquire would
    # re-enter the RLock and never block).
    monkeypatch.setattr(dispatcher_mod, "_DISPATCH_LOCK_ACQUIRE_SLICE_S", 0.05)
    monkeypatch.setattr(dispatcher_mod, "_DISPATCH_LOCK_GIVE_UP_S", 0.2)
    server = _StubServer()
    server._dispatch_lock.acquire()
    outcome: dict = {}
    try:
        started = time.monotonic()
        worker = threading.Thread(
            target=lambda: outcome.setdefault("result", server._dispatch({"type": "ping_mut", "id": 2}))
        )
        worker.start()
        worker.join(timeout=10.0)
        elapsed = time.monotonic() - started
    finally:
        server._dispatch_lock.release()
    assert not worker.is_alive(), "dispatch must return, not hang"
    result = outcome["result"]
    assert result is not None
    assert result.get("type") == "error"
    assert _result_code(result) == "server.busy"
    assert elapsed < 5.0, "lock wait must be bounded, not infinite"


def test_lock_released_after_normal_dispatch() -> None:
    server = _StubServer()
    server._dispatch({"type": "ping_mut", "id": 3})
    assert server._dispatch_lock.acquire(blocking=False), "lock must be released"
    server._dispatch_lock.release()


def _stub_ws_server() -> types.SimpleNamespace:
    def _dispatch(msg, websocket=None):
        # Mirror the real server._dispatch id echo (dispatcher.py).
        result = {"type": "result", "data": {"thread": threading.current_thread().name}}
        if isinstance(msg, dict) and "id" in msg:
            result["id"] = msg["id"]
        return result

    return types.SimpleNamespace(app=types.SimpleNamespace(_shutting_down=False), _dispatch=_dispatch)


async def test_readonly_uses_reserved_pool() -> None:
    server = _stub_ws_server()
    dispatch = ws_dispatch_mod._make_dispatch(server)
    assert getattr(server, "_ws_readonly_pool", None) is not None
    result = await dispatch({"type": "get_status", "id": 10}, None)
    assert result is not None
    assert result["data"]["thread"].startswith("sidecar-ws-readonly")
    assert result["id"] == 10


async def test_mutating_uses_main_pool() -> None:
    server = _stub_ws_server()
    dispatch = ws_dispatch_mod._make_dispatch(server)
    result = await dispatch({"type": "save_vocabulary", "id": 11}, None)
    assert result is not None
    assert result["data"]["thread"].startswith("sidecar-ws-dispatch")
    assert result["id"] == 11


def test_busy_code_registered() -> None:
    from voice_typer.server.ipc.validation import ALL_ERROR_CODES

    assert "server.busy" in ALL_ERROR_CODES
