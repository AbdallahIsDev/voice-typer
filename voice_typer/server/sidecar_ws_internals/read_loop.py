"""WS read/dispatch loop: inbound frames, fast-paths, pipelined dispatch.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`
(``_read_loop`` + ``_dispatch_and_respond`` + the heartbeat fast-path
rate-cap constants, which moved WITH the loop because its body
references them by bare name); the canonical module re-exports the
functions and keeps value aliases for the constants so every existing
import path and read/assert surface keeps working:

- Direct calls (``sidecar_ws._read_loop(ws, server, dispatch)`` —
  tests/test_sidecar_ws.py, tests/test_sidecar_ws_permissions_fixes.py,
  tests/test_sidecar_ws_relaunch_ack_fastpath.py) and the
  ``inspect.getsource(sidecar_ws._read_loop)`` pins follow the
  re-exported function object; the getsource assertions require the
  heartbeat rate-cap constant NAMES inside the body, which is why the
  constants live HERE (the canonical module's aliases are pure values,
  never read by this loop).
- Signature pins (``(websocket, server, dispatch)`` —
  tests/test_sidecar_ws_handle_connection_split.py) follow the
  function object unchanged.

Patch-path contract (C-ARCH-2 canonical form): this module OWNS
``_read_loop`` / ``_dispatch_and_respond`` / the rate-cap constants.
The canonical ``_handle_connection_inner`` resolves ``_read_loop``
through the sibling MODULE-OBJECT read at call time
(``_read_loop_mod._read_loop(...)``), so a ``monkeypatch.setattr`` on
THIS module is observed by production. No test patches the re-export
on the canonical module, so the module-object read is the canonical
observer form (same recipe as ``run()`` → ``dispatch._make_dispatch``).

The heartbeat sliding-window tests fake the clock by rebinding
``time.monotonic`` on the ``time`` module object (``sw.time`` in
tests/test_sidecar_ws_permissions_fixes.py and this module's ``time``
are the ONE stdlib module), so the fake clock is observed here
exactly as it was pre-split.

``_dispatch_and_respond`` resolves ``_safe_send`` through the
``_outbound_mod`` module-object read at call time (unchanged from the
canonical body it was extracted from): tests patch
``voice_typer.server.sidecar_ws_internals.outbound._safe_send`` /
``_WS_SEND_TIMEOUT_SECONDS`` and production observes the patch. The
C-WS-2 TEXT-frame wire contract's enforcement site stays in
``outbound._safe_send``.
"""

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
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")

# Heartbeat fast-path rate cap.
#
# The Rust host sends one ``heartbeat`` command every 10s (ADR-0018 /
# ADR-0020 §10) — i.e. the legitimate steady-state rate is 1 per 10s.
# The heartbeat fast-path in :func:`_read_loop` deliberately bypasses
# the dispatch pool (and therefore the ADR-0019 per-frame
# :class:`_RateLimiter` that lives inside ``_make_dispatch``) so the
# ack latency stays at the WS round-trip (~1 ms loopback) instead of
# the dispatch-pool queue depth — a slow ``download_model`` /
# ``transcribe`` running on the pool must not delay the ack and trip
# the host's "3 consecutive misses → respawn" liveness probe.
#
# But that bypass means a hostile or buggy client could spam
# ``{"type":"heartbeat"}`` at line rate (tens of thousands per
# second) and the read loop would ``await websocket.send(ack)`` for
# every one of them — starving every other connection's reads, since
# the read loop is single-threaded per connection and the event loop
# is shared across all connections.
#
# This cap is a CHEAP sliding-window (a ``deque`` of timestamps,
# popped from the left when older than the window). 100 per 10s is
# ~10x the legitimate rate — generous enough that a slightly
# over-eager host retry loop won't trip it, tight enough that a
# flood is dropped at the read loop instead of fanning out acks.
#
# The window is PER-CONNECTION (not shared like the ADR-0019
# limiter) because a heartbeat flood is a per-connection
# misbehaviour — sharing the budget would let one flapping client
# starve heartbeats from a well-behaved second connection. Each
# connection's read loop is single-threaded, so the deque is
# accessed without a lock from inside that coroutine.
_HEARTBEAT_RATE_WINDOW_SECONDS = 10.0
_HEARTBEAT_RATE_MAX_PER_WINDOW = 100


async def _read_loop(websocket, server: IPCServer, dispatch) -> None:
    """Read/dispatch loop body (extraction from ``_handle_connection_inner``).

    Reads inbound WS frames, validates JSON, and dispatches each frame
    via the ``dispatch`` coroutine. Fast-paths handled INLINE (so they
    cannot be starved by an in-flight long dispatch): heartbeat-ack and
    ``relaunch_ack``. Everything else is dispatched as a PIPELINED task
    (see the task-creation comment) so the loop keeps reading while a
    long handler runs on the dispatch pool; in-flight tasks are drained
    (responses flushed) before this function returns.

    NOTE: the inbound frame-size cap is enforced by the ``websockets``
    library itself via ``serve(..., max_size=...)`` in :func:`run` —
    the library rejects oversized frames at the transport layer with a
    1009 close. We do NOT re-check here (it would be dead code; the
    frame never arrives if it exceeds ``max_size``).

    Raises ``ConnectionClosedOK`` / ``ConnectionClosedError`` on WS
    close (the caller distinguishes clean vs abnormal close for
    log-level selection). Any other exception is propagated to the
    caller's catch-all.
    """
    # Per-connection heartbeat sliding-window rate cap. The fast-path
    # bypasses the dispatch-pool / ADR-0019 limiter, so a hostile or
    # buggy client could flood ``{"type":"heartbeat"}`` at line rate
    # and starve the event loop with ack sends. This deque holds the
    # timestamps of the last ``_HEARTBEAT_RATE_MAX_PER_WINDOW``
    # heartbeats; old entries are popleft when older than the window.
    # Single-threaded access from this coroutine — no lock needed.
    heartbeat_window: deque[float] = deque()
    # In-flight pipelined dispatch tasks (see the creation site). The
    # set is drained before return so every response is flushed.
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
        # correlation (ADR-0020 §7 — the host's dispatch() command
        # assigns a per-request id). Echo it back on the response.
        request_id = msg.get("id")
        # DEBUG wire-trace (C-TAURI-3 diagnosis aid): one line per
        # inbound frame so a host↔sidecar frame loss can be bisected
        # end-to-end against the Rust-side [dispatch] id= lines.
        log.debug(
            "[SIDECAR-WS] RX frame type=%s id=%s",
            msg.get("type"),
            request_id,
        )
        # heartbeat fast-path. Handle heartbeat INLINE in
        # the read loop BEFORE awaiting ``dispatch()`` so the
        # heartbeat-ack is not delayed by an in-flight long
        # dispatch (e.g. ``download_model``, ``transcribe``) running
        # on the dispatch pool. The Rust host's liveness probe
        # (3 consecutive misses ≥30s → respawn, see ADR-0018 /
        # ADR-0020 §10) would otherwise fire spuriously during a
        # legitimate long-running command — restarting the sidecar
        # mid-download and forcing the user to retry. Bypassing the
        # dispatch pool keeps the heartbeat-ack latency at the
        # ``websocket.send()`` round-trip (~1 ms loopback) instead
        # of the dispatch-pool queue depth.
        if msg.get("type") == "heartbeat":
            # Cheap heartbeat-specific rate cap. The fast-path bypasses
            # the dispatch-pool / ADR-0019 limiter, so without this cap
            # a flood of ``{"type":"heartbeat"}`` frames would be acked
            # at line rate, starving the event loop. Allow at most
            # ``_HEARTBEAT_RATE_MAX_PER_WINDOW`` per
            # ``_HEARTBEAT_RATE_WINDOW_SECONDS``; drop the rest WITHOUT
            # acking (a well-behaved host sending 1/10s will never
            # trip this — even a 10x-over-eager retry loop has room).
            now = time.monotonic()
            window_edge = now - _HEARTBEAT_RATE_WINDOW_SECONDS
            while heartbeat_window and heartbeat_window[0] < window_edge:
                heartbeat_window.popleft()
            if len(heartbeat_window) >= _HEARTBEAT_RATE_MAX_PER_WINDOW:
                log.warning(
                    "[SIDECAR-WS] heartbeat rate cap exceeded (%d in %.0fs) — dropping (no ack)",
                    _HEARTBEAT_RATE_MAX_PER_WINDOW,
                    _HEARTBEAT_RATE_WINDOW_SECONDS,
                )
                continue
            heartbeat_window.append(now)
            # Mirror ``_handle_heartbeat``'s update of
            # ``_last_heartbeat_at`` so the Python-side heartbeat
            # watchdog (if installed) sees fresh liveness.
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
        # host's ``relaunch_ack`` must set ``_relaunch_ack_event`` with
        # ~1 ms latency. Routing it through the dispatch closure
        # (rate-limiter + ``ws_dispatch_pool`` executor round-trip)
        # raced the sidecar's 0.5 s ``wait_for_relaunch_ack`` timeout —
        # observed as "relaunch_ack timed out after 0.500s" WHILE the
        # host log showed "relaunch_ack frame sent", producing a
        # double-restart. Handle INLINE like heartbeat: set the event,
        # charge no rate-limit cost, send nothing (fire-and-forget).
        if msg.get("type") == "relaunch_ack":
            with contextlib.suppress(AttributeError):
                server._relaunch_ack_event.set()
            continue
        # PIPELINED DISPATCH (2026-08-30 tray-Restart postmortem): the
        # read loop previously ``await``ed each dispatch INLINE, so a
        # long-running handler (``restart_app``'s 4.6 s teardown) BLOCKED
        # frame processing for its whole duration — the host's
        # ``relaunch_ack`` starved behind it and the sidecar timed out.
        # Handlers already run concurrently on the ``ws_dispatch_pool``
        # (4 workers); only the response send was serialized. Dispatch
        # each frame as its own task so the loop keeps reading; the task
        # sends the response itself. The host correlates by id, so
        # response ORDER across concurrent dispatches is irrelevant.
        # ``dispatch_tasks`` is drained before this function returns so
        # callers (and the tests) observe every response deterministically.
        task = asyncio.create_task(_dispatch_and_respond(msg, request_id, websocket, dispatch))
        dispatch_tasks.add(task)
        task.add_done_callback(dispatch_tasks.discard)

    # Drain in-flight dispatch tasks on every exit path (normal stream
    # end, heartbeat/`break` paths, connection close) so responses are
    # flushed before the connection teardown cancels the writer. A task
    # whose ``_safe_send`` failed closes the websocket itself; the
    # already-ended read loop is unaffected.
    if dispatch_tasks:
        results = await asyncio.gather(*dispatch_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                log.debug(
                    "[SIDECAR-WS] dispatch task raised during drain",
                    exc_info=(type(r), r, r.__traceback__),
                )


async def _dispatch_and_respond(msg: dict, request_id, websocket, dispatch) -> None:
    """Run one pipelined dispatch + send its response (read-loop task body).

    Extracted from ``_read_loop``'s inline tail when dispatches became
    pipelined tasks (see the comment at the task-creation site). The
    response keeps every ``_safe_send`` defense (off-loop encode, 1 MiB
    cap, send timeout); a non-``"sent"`` status closes the websocket —
    the task-based equivalent of the old inline ``break`` — because a
    dropped/failed response means the peer is waiting for an id that
    will never resolve, and a wedged connection must hand control back
    to the host's reconnect path immediately.
    """
    result = await dispatch(msg, websocket)
    if result is not None:
        if request_id is not None and isinstance(result, dict):
            result = {**result, "id": request_id}
        # Route the dispatch response through ``_safe_send`` so it
        # gets the SAME three DoS defenses the writer task applies
        # to outbound events: off-loop ``json.dumps``, the
        # ``_MAX_FRAME_BYTES`` 1 MiB cap, and the
        # ``_WS_SEND_TIMEOUT_SECONDS`` send timeout. Pre-fix the
        # dispatch response called ``websocket.send(json.dumps(...))``
        # directly, bypassing all three — a handler returning a
        # multi-MiB response (e.g. ``get_history`` /
        # ``get_vocabulary`` for a user with thousands of entries)
        # would (1) block the asyncio loop thread with synchronous
        # ``json.dumps``, (2) block forever on a wedged peer, and
        # (3) exceed the 1 MiB cap that ADR-0020 §10 mandates.
        send_status = await _outbound_mod._safe_send(websocket, result)
        log.debug(
            "[SIDECAR-WS] TX response id=%s status=%s",
            request_id,
            send_status,
        )
        if send_status != "sent":
            # ``"dropped"`` (oversized) or ``"failed"`` (timeout /
            # send error). For ``"dropped"`` the host is expecting
            # a response with this ``request_id`` and would hang
            # until its own timeout; the resulting WS close lets the
            # host's reconnect path take over immediately instead of
            # silently dropping the response. For ``"failed"`` the
            # connection is already unreliable or closing.
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="dispatch response not sent")
