"""Sidecar WS dispatch internals."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")


def _make_dispatch(server: IPCServer):
    """Build a coroutine that dispatches a single WS frame."""
    # ADR-0019 + : per-process rate limiter. Reuse the same private
    from voice_typer.server.ipc_server import _get_rate_limiter

    # Resolve the rate limiter ONCE in the closure body so
    rate_limiter = _get_rate_limiter(server)

    ws_dispatch_pool = getattr(server, "_ws_dispatch_pool", None)
    if ws_dispatch_pool is None:
        # Only reachable on test doubles that bypass ``__init__``.
        from concurrent.futures import ThreadPoolExecutor

        ws_dispatch_pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-dispatch",
        )
        server._ws_dispatch_pool = ws_dispatch_pool

    # Reserved readonly pool: readonly/instant commands must keep flowing
    # (status polls, heartbeat-adjacent reads) even when every main-pool
    # worker is queued behind a stuck mutating handler holding
    # ``_dispatch_lock`` — otherwise one wedged save starves status polls
    # and the host sees the all-commands-timeout outage class.
    from voice_typer.server.ipc.registry import (
        _INSTANT_CONTROL_COMMANDS,
        _READONLY_COMMANDS,
    )

    ws_readonly_pool = getattr(server, "_ws_readonly_pool", None)
    if ws_readonly_pool is None:
        from concurrent.futures import ThreadPoolExecutor

        ws_readonly_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="sidecar-ws-readonly",
        )
        server._ws_readonly_pool = ws_readonly_pool

    # explicit ``threading.Event`` coordination between the WS
    ws_drained_event = getattr(server, "_ws_drained_event", None)
    ws_inflight_lock = getattr(server, "_ws_inflight_lock", None)
    if ws_drained_event is None or ws_inflight_lock is None:
        import threading as _threading

        if ws_drained_event is None:
            ws_drained_event = _threading.Event()
            ws_drained_event.set()  # initially drained, count is 0
            server._ws_drained_event = ws_drained_event
        if ws_inflight_lock is None:
            ws_inflight_lock = _threading.Lock()
            server._ws_inflight_lock = ws_inflight_lock
        if getattr(server, "_ws_inflight_count", None) is None:
            server._ws_inflight_count = 0

    async def dispatch(msg: dict, websocket) -> dict | None:
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical): see
                    "code": "client.invalid_payload",
                    "message": "missing 'type'",
                },
            }

        # cooperative shutdown gate. Once ``app._shutting_down``
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
            log.debug("[SIDECAR-WS] rejecting %s, server shutting down", msg_type)
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # Per-frame gate: rate_limiter.allow() with per-command cost.
        if msg_type != "shutdown" and not rate_limiter.allow(command=msg_type):
            # allow() already increments _rejected atomically when
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical).
                    "code": "client.rate_limited",
                    "message": "rate limit exceeded; backing off",
                },
            }

        # TOCTOU re-check: the early ``_shutting_down`` gate
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
            log.debug(
                "[SIDECAR-WS] TOCTOU re-check rejecting %s, server shutting down",
                msg_type,
            )
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # mark this dispatch as in-flight + clear the drain Event
        with ws_inflight_lock:
            server._ws_inflight_count = server._ws_inflight_count + 1
            ws_drained_event.clear()

        # Dispatch on the worker thread pool so a slow handler
        loop = asyncio.get_running_loop()
        # Pre-bind ``result`` to None so the ``return result`` line
        result: dict | None = None
        try:
            # Pre-executor TOCTOU re-check: the early ``_shutting_down``
            if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
                log.debug(
                    "[SIDECAR-WS] pre-executor TOCTOU re-check rejecting %s, server shutting down",
                    msg_type,
                )
                return {
                    "type": "error",
                    "data": {
                        "code": "server.shutting_down",
                        "message": "server is shutting down; please retry later",
                    },
                }
            # use the dedicated ``_ws_dispatch_pool`` (not the
            # readonly commands ride the reserved pool so a
            if msg_type in _READONLY_COMMANDS or msg_type in _INSTANT_CONTROL_COMMANDS:
                pool = ws_readonly_pool
            else:
                pool = ws_dispatch_pool
            result = await loop.run_in_executor(pool, server._dispatch, msg)
        except Exception:
            log.exception("[SIDECAR-WS] _dispatch raised")
            #  (2026-07-18): the error envelope now matches the
            return_error = {
                "type": "error",
                "data": {"code": "server.internal_error", "message": "internal error"},
            }
        else:
            return_error = None
        finally:
            # decrement the in-flight count and re-set the drain
            with ws_inflight_lock:
                server._ws_inflight_count = server._ws_inflight_count - 1
                if server._ws_inflight_count <= 0:
                    server._ws_inflight_count = 0
                    ws_drained_event.set()

        if return_error is not None:
            return return_error

        # _dispatch returns None for fire-and-forget commands (e.g.
        return result

    return dispatch
