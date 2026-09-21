"""Tauri sidecar WebSocket transport, server side.

ADR-0020 §1 + §2: this module turns the existing :class:`IPCServer`
dispatch layer into a localhost WebSocket server so the Tauri Rust
host can connect to it as a WS client.

Architecture
------------
::

    Tauri host (Rust)
        │  spawns sidecar via externalBin
        │  passes VOICE_TYPER_IPC_TOKEN env
        ▼
    sidecar_main.py (this module's run() entrypoint)
        │  binds websockets.serve on 127.0.0.1:0
        │  OS assigns an ephemeral port
        │  writes ONE structured line to stdout:
        │     {"event":"server_started","port":<n>}
        ▼
    Rust reads stdout, parses the JSON, opens a WS client to
    ws://127.0.0.1:<n>, sends the bearer-token auth frame, then forwards
    invoke('dispatch', {cmd, data}) envelopes over the WS.

    Auth model (ADR-0020 §3)
    -----------------------------------------------
    The handshake is a **one-shot bearer-token** check, NOT an HMAC
    scheme. The Rust host generates a 256-bit bearer token via
    ``secrets.token_bytes(32)`` and the Python sidecar compares it with
    :func:`hmac.compare_digest` (constant-time *comparison only*, no
    key derivation, no signing). There is no per-message MAC, no nonce,
    and no replay protection; subsequent frames skip re-auth (mirroring
    the TCP handshake-once model from ADR-0014).

    Compensating controls for the absence of per-message MAC:
      - **Loopback-only bind**: ``127.0.0.1:0``: never exposed to the
        network.
      - **Ephemeral port**: chosen by the OS at sidecar startup and
        reported to the host over stdout; not predictable ahead of time.
      - **Per-launch / per-respawn token rotation**: a new token is
        generated on every sidecar spawn, so a stolen token is useless
        after the process exits (ADR-0020 §3 rotation).
    The historical "HMAC" wording was carried over from ADR-0014's
    original design; ADR-0020 §3 has been reconciled and this
    module's docstrings mirror the corrected wording.

Why a separate module (not a flag on ipc_server.py)?
----------------------------------------------------
- The TCP path (ipc_server._accept_tcp) and the WS path share the
  same _COMMAND_REGISTRY + _dispatch + _validate_dict_payload, but
  the listen/accept loops are completely different (raw socket vs
  asyncio websockets). Putting them in the same function would be a
  parallel-systems hazard.
- This module is additive: the TCP path stays intact for the
  predecessor fallback, the WS path is opt-in via ``--ws`` on
  ``ipc_server.py`` (which delegates here).
- The dispatch + handler mixins are 100% reused, only the transport
  changes (per ADR-0020 §2).

Cross-platform
--------------
- ``websockets.serve`` binds on 127.0.0.1:0 on all three platforms
  (Windows/macOS/Linux). The OS assigns the port.
- stdout is force-set to line-buffered at the top of :func:`run` so
  the ``server_started`` JSON is flushed immediately, the host's
  pipe would otherwise block-buffer it and the host would hang
  waiting for the line (ADR-0020 §1 Phase-0 blocker).
- All non-handshake logs go to **stderr** (or the rotating file log
  via ``log.py``), never stdout, keeps the stdout JSON protocol
  unambiguous.

Rate limiting
-------------
ADR-0019's rate limiter
(:class:`voice_typer.server.ipc_server._RateLimiter`) is applied on
every incoming WS frame, mirroring the TCP path. A client that
exceeds 200 burst / 600 sustained (10s window, per
connection) gets ``{"type":"error","code":
"rate_limited","data":{"message":"rate limit exceeded; backing
off"}}`` and the connection stays open.

The limiter is shared across ALL WS connections to this server
process (looked up via ``_get_rate_limiter(server)``), so a local
attacker can no longer reset the 200-message burst budget by dropping
the WS and reconnecting.

Heartbeat
---------
ADR-0018's Python-side heartbeat watchdog is DISABLED on the Tauri
path via the ``TAURI_SIDECAR=1`` env var (see ``ipc_server.py``).
Liveness is instead owned by the Rust host, which combines two
detection mechanisms:

1. **Transport-level**: WS-close or process exit triggers supervisor
   respawn (ADR-0020 §10).
2. **Application-level**: the Rust host dispatches a ``heartbeat``
   command every 10s (handled here in Python by
   ``_handle_heartbeat``, registered in ``_COMMAND_REGISTRY``). On
   3 consecutive misses (≥30s of unresponsiveness, socket open but
   no response, e.g. GIL contention / infinite loop / blocking C
   call), the Rust host triggers respawn. This catches sidecar
   hangs that keep the TCP/WS socket open but don't respond to
   dispatches, a scenario the WS-close-only detection misses.

Together these replace the predecessor path's 120-second-heartbeat-
timeout watchdog with a faster, more accurate liveness probe.

Module layout
-------------
This module is the CANONICAL home of the WS transport: the entrypoint
(:func:`run`) and the connection orchestrators
(``_handle_connection`` / ``_handle_connection_inner``).
Focused helper concerns live in the
:mod:`voice_typer.server.sidecar_ws_internals` leaf package and are
re-exported here (see the "Split leaves" comment near the imports for
the pin map):

- ``connection``: per-connection helpers (duplicate-auth invariant,
  connection semaphore, browser-origin rejection, drop-oldest enqueue,
  ready emit, event-bus subscriber, initial state snapshot).
- ``dispatch``: the WS dispatch factory (``_make_dispatch``): the
  ADR-0019 per-frame rate-limit gate, the cooperative-shutdown
  gates, the dedicated dispatch thread pool, and the in-flight
  drain coordination consumed by ``ShutdownController._do_cleanup``.
- ``encode_pool``: WS frame-encode ThreadPoolExecutor lifecycle
  (``_get_ws_encode_pool``, ``shutdown_encode_pool``).
- ``graceful_shutdown``: ``_attach_ws_graceful_shutdown`` /
  ``_graceful_close_all_conns`` (close(1001) pass + loop stop).
- ``handshake``: the one-shot bearer-token auth handshake
  (``_authenticate``); its auth-read deadline resolves this module's
  ``_AUTH_TIMEOUT_SECONDS`` alias at call time.
- ``outbound``: the outbound frame path (``_encode_ws_frame``,
  ``_safe_send``, ``_start_writer``, the 1 MiB frame cap and the
  send timeout): the C-WS-2 TEXT-frame wire contract's enforcement
  site.
- ``read_loop``: the inbound read/dispatch loop (``_read_loop``
  + ``_dispatch_and_respond``) with the heartbeat fast-path and its
  per-connection sliding-window rate cap.
- ``stdout_banner``: ``_emit_server_started`` +
  ``_force_line_buffered_stdout`` (the stdout ``server_started`` JSON
  handshake + line buffering for it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from typing import TYPE_CHECKING

# Shared TCP/WS auth-handshake helpers: frame-shape validation
from voice_typer.server.ipc.auth import AUTH_READ_TIMEOUT_SECONDS

# Namespaced error code constants. Imported here so the bare-string
from voice_typer.server.ipc.validation import ErrorCodes

# up by ``run()`` AFTER the lazy websockets import there has already

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# _http_safety.py, _secrets.py, and this module). Aliased to the
from voice_typer.server._paths import LOOPBACK_HOST as _LOOPBACK_HOST

# (C-ARCH-2 canonical form) so owning-submodule patches are observed
from voice_typer.server.sidecar_ws_internals import (  # C-ARCH-2 canonical call-time observers
    dispatch as _dispatch_mod,
    handshake as _handshake_mod,
    outbound as _outbound_mod,
    read_loop as _read_loop_mod,
    stdout_banner as _stdout_banner_mod,
)

# by source-grep tests. C-ARCH-2: production resolves sibling names via
from voice_typer.server.sidecar_ws_internals.connection import (  # noqa: F401
    _check_duplicate_auth,
    _emit_initial_state_snapshot,
    _emit_ready_if_first,
    _enqueue_safe,
    _get_ws_connection_semaphore,
    _install_subscriber,
    _reject_browser_origins,
)
from voice_typer.server.sidecar_ws_internals.dispatch import _make_dispatch  # noqa: F401
from voice_typer.server.sidecar_ws_internals.encode_pool import (  # noqa: F401
    _get_ws_encode_pool,
    shutdown_encode_pool,
)
from voice_typer.server.sidecar_ws_internals.graceful_shutdown import (  # noqa: F401
    _attach_ws_graceful_shutdown,
    _graceful_close_all_conns,
)
from voice_typer.server.sidecar_ws_internals.handshake import _authenticate  # noqa: F401
from voice_typer.server.sidecar_ws_internals.outbound import (  # noqa: F401
    _WS_SEND_TIMEOUT_SECONDS,
    _encode_ws_frame,
    _safe_send,
    _start_writer,
)
from voice_typer.server.sidecar_ws_internals.read_loop import (  # noqa: F401
    _dispatch_and_respond,
    _read_loop,
)
from voice_typer.server.sidecar_ws_internals.stdout_banner import (  # noqa: F401
    _emit_server_started,
    _force_line_buffered_stdout,
)

log = logging.getLogger("voice_typer.server.sidecar_ws")

# C-WS-1: `ready` is emitted once per IPCServer, AFTER _install_subscriber,

# ADR-0020 §10: 1 MiB frame cap, owned by outbound leaf; run() greps
_MAX_FRAME_BYTES: int = _outbound_mod._MAX_FRAME_BYTES

# Auth frame timeout; value owned by ipc.auth.AUTH_READ_TIMEOUT_SECONDS.
_AUTH_TIMEOUT_SECONDS = AUTH_READ_TIMEOUT_SECONDS

# concurrent-connection limit (DoS protection).
_MAX_WS_CONNECTIONS = 16

# Heartbeat rate-cap aliases; owned by read_loop leaf (C-WS / DoS).
_HEARTBEAT_RATE_WINDOW_SECONDS: float = _read_loop_mod._HEARTBEAT_RATE_WINDOW_SECONDS
_HEARTBEAT_RATE_MAX_PER_WINDOW: int = _read_loop_mod._HEARTBEAT_RATE_MAX_PER_WINDOW

# Graceful WS shutdown: send close(1001) then wait for the handshake before
_WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS = 0.5

# Drain budget for in-flight dispatch futures; matches Rust SHUTDOWN_ACK_TIMEOUT_MS.
_WS_DISPATCH_DRAIN_TIMEOUT_SECONDS = 2.0


# Cooperative shutdown hard timeout (ADR-0020 §10). Host force-kills the

# NOTE: see docs/code-notes/ipc.md
from voice_typer.server.ipc.protocol_version import PROTOCOL_VERSION  # noqa: E402


async def _handle_connection(websocket, server: IPCServer, dispatch) -> None:
    """Per-connection WS handler: auth + read/dispatch loop."""
    peer = websocket.remote_address
    log.info("[SIDECAR-WS] client connected from %s", peer)

    sem = _get_ws_connection_semaphore(server)
    if sem.locked():
        log.warning(
            "[SIDECAR-WS] max_connections (%d) reached, rejecting %s with 1008",
            _MAX_WS_CONNECTIONS,
            peer,
        )
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": ErrorCodes.MAX_CONNECTIONS_REACHED,
                            "message": "server at max simultaneous connections",
                        },
                    }
                )
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="max connections reached")
        return
    await sem.acquire()
    try:
        await _handle_connection_inner(websocket, server, dispatch, peer)
    finally:
        sem.release()


async def _handle_connection_inner(websocket, server: IPCServer, dispatch, peer) -> None:
    """Auth + read/dispatch loop body ( extraction)."""
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

    if not await _handshake_mod._authenticate(websocket):
        # mirror the TCP path's
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": ErrorCodes.AUTH_FAILED,
                            "message": "authentication failed",
                        },
                    }
                )
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="auth failed")
        return

    if not await _check_duplicate_auth(websocket, server, peer):
        return

    loop = asyncio.get_running_loop()
    # ``ws_graceful_shutdown`` (invoked from a non-loop thread by the
    with contextlib.suppress(Exception):
        server._ws_loop = loop

    # Register the authenticated websocket on
    authed_conns = getattr(server, "_ws_authenticated_conns", None)
    if authed_conns is not None:
        with contextlib.suppress(Exception):
            authed_conns.add(websocket)

    outbound: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    # ``_install_subscriber`` MUST run BEFORE ``_emit_ready_if_first``
    _push_to_ws = _install_subscriber(server, loop, outbound)
    _emit_ready_if_first(server)
    _emit_initial_state_snapshot(server)
    writer_task = _outbound_mod._start_writer(websocket, outbound)

    from voice_typer.server import event_bus

    try:
        await _read_loop_mod._read_loop(websocket, server, dispatch)
    except ConnectionClosedOK:
        # Clean WebSocket close (1000/1001 normal close), log at DEBUG.
        log.debug("[SIDECAR-WS] client disconnected cleanly")
    except ConnectionClosedError as exc:
        # Abnormal WebSocket close (1006 / 1011, etc.), log at DEBUG.
        log.debug("[SIDECAR-WS] connection closed with error: %s", exc)
    except Exception:
        # Genuinely unexpected error, log at WARNING with traceback.
        log.warning("[SIDECAR-WS] connection ended unexpectedly", exc_info=True)
    finally:
        event_bus.unsubscribe(_push_to_ws)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer_task
        # Remove the websocket from the authenticated-conns set so
        authed_conns = getattr(server, "_ws_authenticated_conns", None)
        if authed_conns is not None:
            with contextlib.suppress(Exception):
                authed_conns.discard(websocket)
        # clear the active-connection slot ONLY if it still
        with server._lock:
            if getattr(server, "_active_ws_connection", None) is websocket:
                server._active_ws_connection = None
        log.info("[SIDECAR-WS] connection closed (peer=%s)", peer)


# and the C-WS-1 ready-first ordering is entirely untouched: the
_EARLY_BIND_BUFFER_CAP: int = 32


def _wrap_dispatch_for_early_bind(server: IPCServer, dispatch):
    """Wrap ``dispatch`` with the bounded pre-app buffer (early mode).

    Installed by :func:`run` ONLY when the entry point marked the server
    with ``_early_ws_bind``. Before the app is bound
    (``server._early_bind_app_ready``: set by the entry point's
    startup thread after the late bind), every dispatch frame is
    appended to a bounded in-memory buffer and the wrapper returns
    ``None`` (response deferred to the drain, the frame is NOT lost).
    On overflow the oldest entry is dropped and answered immediately
    with a retryable busy error through ``_safe_send`` (C-WS-2 TEXT
    frame, request id echoed). After the bind the wrapper is a pure
    passthrough (one attribute read per frame).

    The buffer is mutated ONLY on the asyncio loop thread: the wrapper
    runs inside dispatch coroutines, and the drain is scheduled via
    ``loop.call_soon_threadsafe``: the same single-thread-access
    invariant the read loop's heartbeat window relies on. The one
    cross-thread bit is the ``_early_bind_app_ready`` flag write (an
    atomic bool assignment from the startup thread).

    Documented tolerance (flag-gated mode, default OFF): pre-app frames
    bypass the per-renderer rate limiter (the wrapper sits OUTSIDE
    ``_make_dispatch``, which owns the limiter). The bound is the buffer
    itself (32 frames) and each overflow costs exactly one busy-error
    send for the DROPPED frame, so a misbehaving client cannot grow
    memory or spam beyond the cap. The surface is loopback-only and
    behind the authenticated WS handshake (ADR-0019 token boundary),
    so pre-app unthrottled dispatch is a trusted-inner-sender window of
    a few seconds at most (construction duration). Accepted for the
    escape-hatch mode; if the limiter must cover the pre-app window,
    install the wrapper inside ``_make_dispatch`` at flag-flip time.
    """
    buffer: deque = deque()
    server._early_bind_dispatch_buffer = buffer

    async def _early_bind_dispatch(msg: dict, websocket) -> dict | None:
        if getattr(server, "_early_bind_app_ready", False):
            return await dispatch(msg, websocket)
        dropped: tuple | None = None
        if len(buffer) >= _EARLY_BIND_BUFFER_CAP:
            dropped = buffer.popleft()
            log.warning(
                "[SIDECAR-WS] early-bind dispatch buffer full (%d), dropped oldest frame, busy error sent",
                _EARLY_BIND_BUFFER_CAP,
            )
        buffer.append((msg, websocket))
        if dropped is not None:
            # The dropped frame gets an immediate retryable error so its
            dropped_msg, dropped_ws = dropped
            busy: dict = {
                "type": "error",
                "data": {
                    "code": ErrorCodes.NOT_INITIALIZED,
                    "message": "backend is starting; retry shortly",
                },
            }
            dropped_id = dropped_msg.get("id") if isinstance(dropped_msg, dict) else None
            if dropped_id is not None:
                busy["id"] = dropped_id
            await _outbound_mod._safe_send(dropped_ws, busy)
        return None

    server._early_bind_dispatch = _early_bind_dispatch
    return _early_bind_dispatch


def flush_early_dispatch_buffer(server: IPCServer) -> None:
    """Drain the buffered pre-app frames onto the WS loop (thread-safe).

    Called by the entry point's startup thread right after it sets
    ``server._early_bind_app_ready``. The drain itself runs on the loop
    thread via ``call_soon_threadsafe``; each buffered frame is replayed
    through the read loop's own ``_dispatch_and_respond`` so the replay
    reuses verbatim the id echo + ``_safe_send`` (C-WS-2 TEXT frame) +
    close-on-failure semantics of the normal dispatch path.

    Documented tolerance (flag-gated mode, default OFF): between the
    flag flip and the scheduled drain, NEW frames pass straight through
    while older buffered frames still await replay, responses can
    arrive out of FIFO order within that sub-millisecond window. Every
    frame is id-correlated (the renderer resolves by id, not order), so
    the renderer sees every response exactly once; scheduling the drain
    BEFORE flipping the flag would invert the hazard instead (the drain
    would race frames arriving before it). Accepted for the escape-hatch
    mode; revisit if the flag ever flips default-ON.

    Safe when no loop exists yet (construction finished before ``run``
    even bound the listener, nothing was buffered, so there is nothing
    to drain: frames can only be buffered while the loop is live) and
    during loop teardown (RuntimeError → DEBUG log).
    """
    loop = getattr(server, "_ws_loop", None)
    if loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(_drain_early_dispatch_buffer, server)
    except RuntimeError:
        # The loop closed between the liveness checks above and the
        log.debug("[SIDECAR-WS] early-bind buffer drain skipped, event loop closed")


def _drain_early_dispatch_buffer(server: IPCServer) -> None:
    """Loop-thread body of :func:`flush_early_dispatch_buffer`."""
    buffer = getattr(server, "_early_bind_dispatch_buffer", None)
    dispatch = getattr(server, "_early_bind_dispatch", None)
    if not buffer or dispatch is None:
        return
    loop = asyncio.get_running_loop()
    while buffer:
        msg, websocket = buffer.popleft()
        request_id = msg.get("id") if isinstance(msg, dict) else None
        loop.create_task(_read_loop_mod._dispatch_and_respond(msg, request_id, websocket, dispatch))


def run(server: IPCServer) -> int:
    """Bind a localhost WS server on an ephemeral port and serve forever.

    Returns a process exit code: ``0`` on a clean stop (including the
    designed graceful shutdown), ``1`` on a fatal error, ``2`` when the
    ``websockets`` dependency is unavailable, ``3`` when no socket was
    bound. The bound port is emitted on stdout by ``_emit_server_started``.
    """
    _force_line_buffered_stdout()

    # Local import so the module imports cleanly without `websockets`
    try:
        import websockets  # noqa: F401, imported for availability probe
        from websockets.asyncio.server import serve
    except ImportError as exc:
        log.exception(
            "[SIDECAR-WS] the `websockets` package is required for the "
            "Tauri sidecar path. Install with: uv pip install websockets. "
            "Original error: %s",
            exc,
        )
        return 2

    # object at CALL time (C-ARCH-2 canonical patch form): a test that
    dispatch = _dispatch_mod._make_dispatch(server)

    # being constructed on the ws-startup thread, wrap the dispatch
    if getattr(server, "_early_ws_bind", False):
        dispatch = _wrap_dispatch_for_early_bind(server, dispatch)

    # Install the graceful-shutdown hooks BEFORE the loop starts so the
    _attach_ws_graceful_shutdown(server)

    async def _main() -> int:
        # ``ws_graceful_shutdown`` (invoked from a non-loop thread by
        with contextlib.suppress(Exception):
            server._ws_loop = asyncio.get_running_loop()

        # bind on 127.0.0.1:0 → OS assigns an ephemeral port.
        async with serve(
            _handler,
            _LOOPBACK_HOST,
            0,
            max_size=_MAX_FRAME_BYTES,
            process_request=_reject_browser_origins,
        ) as ws_server:
            # Read back the OS-assigned port. websockets.asyncio.server
            socks = ws_server.sockets
            if not socks:
                log.error("[SIDECAR-WS] no sockets bound, aborting")
                return 3
            port = socks[0].getsockname()[1]
            _stdout_banner_mod._emit_server_started(port, PROTOCOL_VERSION)
            log.info("[SIDECAR-WS] listening on %s:%d", _LOOPBACK_HOST, port)

            # Run until cancelled.
            await asyncio.Future()

        return 0

    async def _handler(websocket) -> None:
        await _handle_connection(websocket, server, dispatch)

    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("[SIDECAR-WS] interrupted, shutting down")
        return 0
    except RuntimeError as exc:
        # 2026-08-30: ``ws_graceful_shutdown`` stops the loop via
        if _is_graceful_loop_stop(server, exc):
            log.info("[SIDECAR-WS] loop stopped by graceful shutdown, clean WS stop")
            return 0
        log.exception("[SIDECAR-WS] fatal error in run()")
        return 1
    except Exception:
        log.exception("[SIDECAR-WS] fatal error in run()")
        return 1


def _is_graceful_loop_stop(server: IPCServer, exc: Exception) -> bool:
    """True when ``exc`` is the RuntimeError ``asyncio.run`` raises after
    our own graceful ``loop.stop()``.

    Classified by type + missing cause chain instead of matching CPython's
    error text, which can be reworded between Python versions.
    """
    if not getattr(server, "_ws_graceful_stop_requested", False):
        return False
    if not isinstance(exc, RuntimeError):
        return False
    return exc.__cause__ is None and exc.__context__ is None
