"""Per-connection WS helpers: invariant, semaphore, origin, queue, session.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`; the
canonical module re-exports every name so both the direct-call test
surface (``sidecar_ws._check_duplicate_auth(...)``,
``sidecar_ws._get_ws_connection_semaphore(server)``,
``sidecar_ws._reject_browser_origins`` passed as ``process_request``,
``sidecar_ws._enqueue_safe``) and the monkeypatch seams keep working:

- ``_install_subscriber`` and ``_emit_ready_if_first`` are PATCHED by
  ``tests/test_sidecar_ws_ready_ordering.py`` and OBSERVED by
  ``_handle_connection_inner`` (which stays in the canonical module) —
  the re-export binding puts these names in the canonical module's
  globals, so a patch replaces exactly what the orchestrator calls.
  The C-WS-1 ordering (``_install_subscriber`` → ``_emit_ready_if_first``
  → ``_emit_initial_state_snapshot``) lives in the canonical module,
  untouched.
- Signatures are pinned by
  ``tests/test_sidecar_ws_handle_connection_split.py``, moved verbatim.
- The lazy ``from voice_typer.server import event_bus`` imports inside
  ``_install_subscriber`` / ``_emit_ready_if_first`` /
  ``_emit_initial_state_snapshot`` stay call-time (tests patch
  ``event_bus.publish``), preserved by the verbatim move.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

from voice_typer.server.ipc.validation import ErrorCodes

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")


def _enqueue_safe(outbound: asyncio.Queue, event: dict) -> None:
    """Drop-oldest enqueue. MUST run on the event-loop thread."""
    if outbound.full():
        try:
            outbound.get_nowait()
            log.debug("[SIDECAR-WS] outbound queue full, dropped oldest event")
        except asyncio.QueueEmpty:
            pass
    try:
        outbound.put_nowait(event)
    except asyncio.QueueFull:
        log.warning("[SIDECAR-WS] outbound queue still full, dropping event")


def _get_ws_connection_semaphore(server: IPCServer) -> asyncio.Semaphore:
    """lazily create / return the per-server connection semaphore."""
    # Resolve the cap from the canonical module at CALL time (a
    from voice_typer.server import sidecar_ws as _canonical

    sem = getattr(server, "_ws_connection_semaphore", None)
    if not isinstance(sem, asyncio.Semaphore):
        sem = asyncio.Semaphore(_canonical._MAX_WS_CONNECTIONS)
        with contextlib.suppress(Exception):
            server._ws_connection_semaphore = sem
    return sem


async def _check_duplicate_auth(websocket, server: IPCServer, peer) -> bool:
    """enforce single-authenticated-connection invariant."""
    with server._lock:
        existing = getattr(server, "_active_ws_connection", None)
    is_existing_open = False
    if existing is not None and existing is not websocket:
        try:
            is_existing_open = not bool(getattr(existing, "closed", True))
        except Exception:
            is_existing_open = False
    if is_existing_open:
        log.warning(
            "[SIDECAR-WS] duplicate authenticated connection from %s, "
            "an existing authenticated connection is already active; "
            "rejecting new connection with 1008 to preserve the "
            "single-connection invariant",
            peer,
        )
        # cannot crash the handler. C-WS-2 is satisfied either way —
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": ErrorCodes.DUPLICATE_CONNECTION,
                            "message": "another authenticated connection is already active",
                        },
                    }
                )
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="duplicate connection")
        return False
    # Mark this as the active connection. Cleared in the ``finally``
    with server._lock:
        server._active_ws_connection = websocket
    return True


def _emit_ready_if_first(server: IPCServer) -> None:
    """ADR-0020 round-2: emit ``ready`` on the first authenticated"""
    with server._lock:
        if getattr(server, "_ready_emitted", False):
            already_emitted = True
        else:
            already_emitted = False
            server._ready_emitted = True
    if not already_emitted:
        from voice_typer.server import event_bus

        log.info("[SIDECAR-WS] first authenticated connection, emitting `ready` event")
        event_bus.publish({"type": "ready"})


def _install_subscriber(server: IPCServer, loop: asyncio.AbstractEventLoop, outbound: asyncio.Queue) -> object:
    """register ``_push_to_ws`` as an"""

    def _push_to_ws(event: dict) -> None:
        """Subscriber for event_bus.publish, enqueues for the writer task.

         this subscriber is invoked synchronously in the
        publisher's thread (``event_bus._deliver`` calls ``fn(event)``
        directly, modulo the RT-thread deferred path). Because the
        publisher is typically a non-event-loop thread, we MUST NOT
        touch ``outbound`` (an ``asyncio.Queue``) here, ``asyncio.Queue``
        is documented as NOT thread-safe and direct mutation from a
        non-loop thread corrupts the queue's internal deque + Future
        state. Instead we schedule ``_enqueue_safe`` on the loop thread
        via ``call_soon_threadsafe``: the only documented thread-safe
        way to hand work to an asyncio loop from outside it. The
        drop-oldest dance (``full`` / ``get_nowait`` / ``put_nowait``)
        lives in ``_enqueue_safe`` and runs entirely on the loop thread.

        removed the pre-marshaling queue-overflow check and
        the ``put_nowait`` fallback. They touched the asyncio.Queue from
        the publisher's thread, exactly the corruption ``_enqueue_safe``
        was created to prevent. ``_enqueue_safe`` already does the
        drop-oldest dance ON the loop thread. Also removed the dead
        ``except asyncio.QueueFull`` clause (``call_soon_threadsafe``
        never raises QueueFull).

        ``RuntimeError`` is raised by ``call_soon_threadsafe`` when
        the loop has been closed (process shutdown / respawn).
        The writer task has already been cancelled by the connection
        ``finally`` block, so there is no consumer for the event —
        drop silently at DEBUG level. This is the documented
        shutdown contract; we do NOT want a traceback per published
        event during teardown.
        """
        try:
            loop.call_soon_threadsafe(_enqueue_safe, outbound, event)
        except RuntimeError:
            log.debug("[SIDECAR-WS] event dropped during shutdown, event loop closed")

    from voice_typer.server import event_bus

    event_bus.subscribe(_push_to_ws)

    return _push_to_ws


def _emit_initial_state_snapshot(server: IPCServer) -> None:
    """Emit the connect-time ``state_changed`` snapshot ( / parity).

    Published on EVERY authenticated connection (not just the first
    ``ready``). This mirrors the TCP path's connect-time snapshot at
    ``ipc_server.py:_handle_tcp_connection`` (~L1003-1017) so a WS
    reconnect after a transient drop immediately re-hydrates the
    renderer's tray state badge instead of leaving it stale until the
    next state transition.

    ORDERING CONTRACT (do not reorder): this MUST be called AFTER
    :func:`_emit_ready_if_first` in ``_handle_connection_inner``. The
    Tauri host's ``wait_for_auth_ok`` requires ``ready`` as the FIRST
    post-auth frame, any other frame type is treated as a protocol
    violation and triggers a supervisor respawn loop. This snapshot
    previously lived inside :func:`_install_subscriber`, so it raced
    in AHEAD of ``ready`` on the wire and killed every Tauri
    handshake with "WS auth unexpected frame type: state_changed".
    Binding rule: AGENTS.md constraint C-WS-1.

    Defensive: the tray may not be initialized yet on the very first
    connection (the app boots the IPC server before the tray icon is
    constructed). ``getattr(..., None)`` + the ``is not None`` guard
    skip the emit in that case, the host will pick up the next state
    transition via the normal ``status_change`` hook.
    """
    from voice_typer.server import event_bus

    try:
        current_state = getattr(server.app.tray, "_state", None)
        current_msg = getattr(server.app.tray, "_message", "")
        if current_state is not None:
            event_bus.publish(
                {
                    "type": "state_changed",
                    "data": {
                        "status": getattr(current_state, "value", str(current_state)),
                        "message": current_msg,
                    },
                }
            )
    except Exception:
        log.debug(
            "[SIDECAR-WS] failed to emit initial state_changed on connect",
            exc_info=True,
        )


async def _reject_browser_origins(connection, request):
    """Reject WS connections that carry an ``Origin`` header."""
    origin = request.headers.get("Origin")
    if origin is not None:
        log.debug("[WS] rejected connection with Origin: %s", origin)
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        return Response(
            403,
            "Forbidden",
            Headers(Connection="close"),
            b"origin not allowed\n",
        )
    return None
