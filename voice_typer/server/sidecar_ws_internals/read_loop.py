"""Sidecar WS reader loop internals."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from voice_typer.server.sidecar_ws_internals import outbound as _outbound_mod

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")

# Heartbeat fast-path rate cap.
_HEARTBEAT_RATE_WINDOW_SECONDS = 10.0
_HEARTBEAT_RATE_MAX_PER_WINDOW = 100


async def _read_loop(websocket, server: IPCServer, dispatch) -> None:
    """Read/dispatch loop body (extraction from ``_handle_connection_inner``)."""
    # Per-connection heartbeat sliding-window rate cap. The fast-path
    heartbeat_window: deque[float] = deque()
    # In-flight pipelined dispatch tasks (see the creation site). The
    dispatch_tasks: set[asyncio.Task] = set()
    async for raw in websocket:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            # Namespaced form (canonical).
                            "code": "client.invalid_payload",
                            "message": "invalid JSON",
                        },
                    }
                )
            )
            continue

        if not isinstance(msg, dict):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            # Namespaced form (canonical).
                            "code": "client.invalid_payload",
                            "message": "frame must be an object",
                        },
                    }
                )
            )
            continue

        # The frame may carry an optional "id" for request/response
        request_id = msg.get("id")
        # DEBUG wire-trace (C-TAURI-3 diagnosis aid): one line per
        log.debug(
            "[SIDECAR-WS] RX frame type=%s id=%s",
            msg.get("type"),
            request_id,
        )
        # heartbeat fast-path. Handle heartbeat INLINE in
        if msg.get("type") == "heartbeat":
            # Cheap heartbeat-specific rate cap. The fast-path bypasses
            now = time.monotonic()
            window_edge = now - _HEARTBEAT_RATE_WINDOW_SECONDS
            while heartbeat_window and heartbeat_window[0] < window_edge:
                heartbeat_window.popleft()
            if len(heartbeat_window) >= _HEARTBEAT_RATE_MAX_PER_WINDOW:
                log.warning(
                    "[SIDECAR-WS] heartbeat rate cap exceeded (%d in %.0fs), dropping (no ack)",
                    _HEARTBEAT_RATE_MAX_PER_WINDOW,
                    _HEARTBEAT_RATE_WINDOW_SECONDS,
                )
                continue
            heartbeat_window.append(now)
            # Mirror ``_handle_heartbeat``'s update of
            with contextlib.suppress(AttributeError):
                server._last_heartbeat_at = now
            ack: dict[str, object] = {"type": "heartbeat_ack"}
            if request_id is not None:
                ack["id"] = request_id
            try:
                await websocket.send(json.dumps(ack))
            except Exception:
                log.warning("[SIDECAR-WS] heartbeat ack send failed", exc_info=True)
                break
            continue
        # PERF-005 fast-path (2026-08-30 tray-Restart postmortem): the
        if msg.get("type") == "relaunch_ack":
            with contextlib.suppress(AttributeError):
                server._relaunch_ack_event.set()
            continue
        # PIPELINED DISPATCH (2026-08-30 tray-Restart postmortem): the
        task = asyncio.create_task(_dispatch_and_respond(msg, request_id, websocket, dispatch))
        dispatch_tasks.add(task)
        task.add_done_callback(dispatch_tasks.discard)

    # Drain in-flight dispatch tasks on every exit path (normal stream
    if dispatch_tasks:
        results = await asyncio.gather(*dispatch_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                log.debug(
                    "[SIDECAR-WS] dispatch task raised during drain",
                    exc_info=(type(r), r, r.__traceback__),
                )


async def _dispatch_and_respond(msg: dict, request_id, websocket, dispatch) -> None:
    """Run one pipelined dispatch + send its response (read-loop task body)."""
    result = await dispatch(msg, websocket)
    if result is not None:
        if request_id is not None and isinstance(result, dict):
            result = {**result, "id": request_id}
        # Route the dispatch response through ``_safe_send`` so it
        send_status = await _outbound_mod._safe_send(websocket, result)
        log.debug(
            "[SIDECAR-WS] TX response id=%s status=%s",
            request_id,
            send_status,
        )
        if send_status != "sent":
            # ``"dropped"`` (oversized) or ``"failed"`` (timeout /
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="dispatch response not sent")
