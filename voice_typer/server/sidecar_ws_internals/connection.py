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
  ``tests/test_sidecar_ws_handle_connection_split.py`` — moved verbatim.
- The lazy ``from voice_typer.server import event_bus`` imports inside
  ``_install_subscriber`` / ``_emit_ready_if_first`` /
  ``_emit_initial_state_snapshot`` stay call-time (tests patch
  ``event_bus.publish``) — preserved by the verbatim move.
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
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")


def _enqueue_safe(outbound: asyncio.Queue, event: dict) -> None:
    """Drop-oldest enqueue — MUST run on the event-loop thread.

    ``_push_to_ws`` is an ``event_bus`` subscriber, so it is
    invoked from whatever thread called ``event_bus.publish()``. The
    publishers are non-event-loop threads: transcription, hotkey, tray,
    IPC dispatch workers (``_dispatch`` runs via
    ``loop.run_in_executor``), and the audio-worker deferred path.
    ``asyncio.Queue`` is explicitly NOT thread-safe — mutating its
    internal deque + ``_getawaiter`` / ``_putawaiter`` futures from a
    non-loop thread corrupts state. Symptoms seen pre-fix: silently
    dropped events (transcription_final never reached the Tauri host),
    a deadlocked writer task (``await outbound.get()`` never wakes
    after a cross-thread ``put_nowait``), or a hard asyncio loop
    crash killing the sidecar (→ respawn loop).

    This helper does the drop-oldest dance (``full`` / ``get_nowait``
    / ``put_nowait``). It is marshaled onto the event-loop thread via
    ``loop.call_soon_threadsafe`` from ``_push_to_ws``, so every
    queue mutation happens on the loop thread and the asyncio
    invariants are preserved.

    Notes
    -----
    - ``call_soon_threadsafe`` is the documented asyncio API for
      cross-thread wakeup. It schedules the callback on the loop's
      ready queue and wakes the loop's selector if it is blocked in
      ``select()``. This is the same primitive ``asyncio.run_coroutine_threadsafe``
      is built on.
    - Drop-oldest (not drop-newest) is preserved so a slow host
      receives the most RECENT state snapshots (bubble_level,
      transcription_final) rather than stale ones buffered behind
      the drop. The host recovers consistency via state_changed
      re-snapshots on reconnect.
    """
    if outbound.full():
        try:
            outbound.get_nowait()
            log.debug("[SIDECAR-WS] outbound queue full — dropped oldest event")
        except asyncio.QueueEmpty:
            pass
    try:
        outbound.put_nowait(event)
    except asyncio.QueueFull:
        log.warning("[SIDECAR-WS] outbound queue still full — dropping event")


def _get_ws_connection_semaphore(server: IPCServer) -> asyncio.Semaphore:
    """lazily create / return the per-server connection semaphore."""
    # Resolve the cap from the canonical module at CALL time (a
    # module-top import would be circular: sidecar_ws imports this leaf
    # at its own module top). Call-time resolution preserves the
    # pre-split patch seam — an assignment to
    # ``sidecar_ws._MAX_WS_CONNECTIONS`` is observed here exactly as it
    # was when this body lived in that module.
    from voice_typer.server import sidecar_ws as _canonical

    sem = getattr(server, "_ws_connection_semaphore", None)
    if not isinstance(sem, asyncio.Semaphore):
        sem = asyncio.Semaphore(_canonical._MAX_WS_CONNECTIONS)
        with contextlib.suppress(Exception):
            server._ws_connection_semaphore = sem
    return sem


async def _check_duplicate_auth(websocket, server: IPCServer, peer) -> bool:
    """enforce single-authenticated-connection invariant.

    The host (Rust / Electron) uses respawn rather than reconnect —
    a second authenticated WS implies a protocol bug (stale socket
    in the host's connect loop, a race between supervisor respawn
    and the old sidecar's accept loop, etc.). Both connections
    would have separate outbound queues + writer tasks + event_bus
    subscribers, causing duplicate event delivery (every
    ``event_bus.publish`` reaches both writers). The cleaner fix is
    to REJECT the new connection with 1008 ("Policy Violation") so
    the existing authenticated connection continues uninterrupted.
    The host's reconnect logic treats 1008 as a fatal-sidecar signal
    and respawns, which is the correct response to a duplicate-auth
    protocol bug. The previous connection is cleared from
    ``server._active_ws_connection`` in the ``finally`` block of
    :func:`_handle_connection_inner` (only if it still points at THIS
    socket — a race-safe compare).

    Defensive: ``server`` may be a ``MagicMock`` in tests, where
    ``getattr(server, "_active_ws_connection", None)`` auto-vivifies
    a child MagicMock (which would falsely trip the duplicate
    check). The ``is_closed`` probe below treats a non-bool
    ``.closed`` attribute (or any error reading it) as "closed" so
    the invariant is enforced only against a REAL open websocket.

    Returns ``True`` if the connection should proceed (no duplicate,
    and the active-connection slot was claimed). Returns ``False`` if
    the connection was rejected (duplicate_connection frame sent +
    socket closed) — the caller MUST return immediately.
    """
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
            "[SIDECAR-WS] duplicate authenticated connection from %s — "
            "an existing authenticated connection is already active; "
            "rejecting new connection with 1008 to preserve the "
            "single-connection invariant",
            peer,
        )
        # INTENTIONALLY INLINE (pre-existing moved code, kept as-is):
        # this one-shot rejection frame goes through a direct
        # ``websocket.send(json.dumps(...))`` instead of
        # ``outbound._safe_send``. Deliberate: (1) the frame is a tiny
        # fixed-size error envelope, so the 1 MiB cap / off-loop encode
        # defenses are moot for it; (2) the close code contract here is
        # 1008 (Policy Violation — the host's reconnect logic branches
        # on it), and routing through ``_safe_send`` would introduce a
        # competing 1011 close on its timeout/error paths, racing this
        # authoritative 1008 close; (3) the send + close are both
        # wrapped in ``contextlib.suppress`` so a half-dead socket
        # cannot crash the handler. C-WS-2 is satisfied either way —
        # ``json.dumps`` produces a ``str``, so the frame goes out as
        # a TEXT frame.
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
    # block of ``_handle_connection_inner`` (only if it still points at
    # THIS socket — a concurrent rejection path may have already
    # swapped it).
    with server._lock:
        server._active_ws_connection = websocket
    return True


def _emit_ready_if_first(server: IPCServer) -> None:
    """ADR-0020 round-2: emit ``ready`` on the first authenticated
    connection for this ``IPCServer`` instance.

    The Tauri host waits for this event before hydrating the UI
    (mirrors the Electron path's ``ready`` push at
    ``ipc_server.py:1899``). Using ``event_bus.publish`` (not
    ``server.push``) because the WS writer task subscribes to
    event_bus — ``server.push`` would go to the TCP path's
    ``_tcp_client`` which is ``None`` in WS mode.

    the caller (:func:`_handle_connection_inner`) MUST call
    :func:`_install_subscriber` BEFORE this function so the WS
    subscriber (``_push_to_ws``) is registered on ``event_bus`` when
    ``publish({"type": "ready"})`` runs. Pre- the order was
    reversed and the ``ready`` event was published to an empty
    subscriber set (modulo other transports), so the WS writer task
    never received it and the Tauri host never got ``ready`` over
    the WS on first connection.

    the flag is per-instance (``server._ready_emitted``), not
    module-level, so each fresh ``IPCServer`` starts with the flag
    ``False`` and emits ``ready`` on its first connection. This was
    previously a module-level global which leaked state between test
    runs that reused the same module.

    the read-then-write is guarded by ``server._lock`` (an
    ``RLock`` defined on ``IPCServer`` at ``__init__``). Two
    concurrent first-time authentications would otherwise both see
    ``_ready_emitted == False`` and both publish ``ready``. The host
    tolerates duplicates, but the duplicate broadcast is wasted work
    and a minor protocol smell. The lock is also used elsewhere on
    the server (e.g. ``_send`` / ``push``), so this re-uses an
    existing primitive rather than adding a new one.
    """
    with server._lock:
        if getattr(server, "_ready_emitted", False):
            already_emitted = True
        else:
            already_emitted = False
            server._ready_emitted = True
    if not already_emitted:
        from voice_typer.server import event_bus

        log.info("[SIDECAR-WS] first authenticated connection — emitting `ready` event")
        event_bus.publish({"type": "ready"})


def _install_subscriber(server: IPCServer, loop: asyncio.AbstractEventLoop, outbound: asyncio.Queue) -> object:
    """register ``_push_to_ws`` as an
    ``event_bus`` subscriber (sync API) and emit the initial
    ``state_changed`` snapshot.

    ``_push_to_ws`` is invoked from WHATEVER thread
    ``event_bus.publish`` runs on — typically a domain thread (tray,
    transcription, dictation_pipeline, audio-worker, IPC dispatch
    workers) that is NOT the asyncio loop thread. ``asyncio.Queue``
    is documented as NOT thread-safe; the GIL makes immediate deque
    ops atomic but the ``_getters``/``_putters`` future-scheduling
    path can miss wakeups. The captured ``loop`` (captured ONCE here
    —  cleanup: previously re-captured three times with a
    dead ``_ws_loop`` local) is closed over in ``_push_to_ws`` so the
    sync subscriber can route all queue mutations through
    ``loop.call_soon_threadsafe`` (the documented way to bridge a
    sync caller to an asyncio primitive from a non-loop thread).

    The previous ``server._ws_loop = loop`` write was removed — it
    had zero production readers (the per-connection closure-captured
    ``loop`` is the only source of truth used by ``_push_to_ws``),
    and writing it without ``server._lock`` created a race-on-write
    hazard for any future diagnostic reader. The WS path runs ONE
    accept loop on ONE asyncio event loop, so all connections share
    the same loop; if a future refactor permits multiple loops, the
    closure-captured ``loop`` remains the per-connection source of
    truth.

     ( /  parity): the connect-time ``state_changed`` snapshot used to
    be emitted HERE (right after subscribing). It moved to
    :func:`_emit_initial_state_snapshot`, which
    ``_handle_connection_inner`` calls AFTER
    :func:`_emit_ready_if_first` — see that function for why the
    order matters (the Tauri host's auth handshake requires ``ready``
    as the FIRST post-auth frame).

    Returns the ``_push_to_ws`` subscriber callable so the caller can
    ``event_bus.unsubscribe`` it in the connection ``finally`` block.
    """

    def _push_to_ws(event: dict) -> None:
        """Subscriber for event_bus.publish — enqueues for the writer task.

         this subscriber is invoked synchronously in the
        publisher's thread (``event_bus._deliver`` calls ``fn(event)``
        directly, modulo the RT-thread deferred path). Because the
        publisher is typically a non-event-loop thread, we MUST NOT
        touch ``outbound`` (an ``asyncio.Queue``) here — ``asyncio.Queue``
        is documented as NOT thread-safe and direct mutation from a
        non-loop thread corrupts the queue's internal deque + Future
        state. Instead we schedule ``_enqueue_safe`` on the loop thread
        via ``call_soon_threadsafe`` — the only documented thread-safe
        way to hand work to an asyncio loop from outside it. The
        drop-oldest dance (``full`` / ``get_nowait`` / ``put_nowait``)
        lives in ``_enqueue_safe`` and runs entirely on the loop thread.

        removed the pre-marshaling queue-overflow check and
        the ``put_nowait`` fallback. They touched the asyncio.Queue from
        the publisher's thread — exactly the corruption ``_enqueue_safe``
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
            log.debug("[SIDECAR-WS] event dropped during shutdown — event loop closed")

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
    post-auth frame — any other frame type is treated as a protocol
    violation and triggers a supervisor respawn loop. This snapshot
    previously lived inside :func:`_install_subscriber`, so it raced
    in AHEAD of ``ready`` on the wire and killed every Tauri
    handshake with "WS auth unexpected frame type: state_changed".
    Binding rule: AGENTS.md constraint C-WS-1.

    Defensive: the tray may not be initialized yet on the very first
    connection (the app boots the IPC server before the tray icon is
    constructed). ``getattr(..., None)`` + the ``is not None`` guard
    skip the emit in that case — the host will pick up the next state
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
    """Reject WS connections that carry an ``Origin`` header.

    The Rust host (Tauri ``externalBin``) opens its WS client with a raw
    TCP socket and never sends an ``Origin`` header; browsers ALWAYS
    attach one. Rejecting any connection WITH an ``Origin`` header closes
    the browser-attacker CSWSH slot-starvation vector (a malicious page
    calling ``new WebSocket("ws://127.0.0.1:<port>")`` many times to
    park the single-connection auth-wait slot) while preserving legit
    host connections.

    Returns ``None`` to allow the handshake (no Origin header present),
    or a :class:`websockets.http11.Response` with HTTP 403 to abort the
    handshake before the auth-wait window even opens (Origin present).

    ``process_request`` callback contract per
    :mod:`websockets.asyncio.server`.
    """
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
