"""Tauri sidecar WebSocket transport — server side.

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
    ws://127.0.0.1:<n>, sends the HMAC auth frame, then forwards
    invoke('dispatch', {cmd, data}) envelopes over the WS.

Why a separate module (not a flag on ipc_server.py)?
----------------------------------------------------
- The TCP path (ipc_server._accept_tcp) and the WS path share the
  same _COMMAND_REGISTRY + _dispatch + _validate_dict_payload, but
  the listen/accept loops are completely different (raw socket vs
  asyncio websockets). Putting them in the same function would be a
  parallel-systems hazard.
- This module is additive: the TCP path stays intact for the
  Electron fallback, the WS path is opt-in via ``--ws`` on
  ``ipc_server.py`` (which delegates here).
- The dispatch + handler mixins are 100% reused — only the transport
  changes (per ADR-0020 §2).

Cross-platform
--------------
- ``websockets.serve`` binds on 127.0.0.1:0 on all three platforms
  (Windows/macOS/Linux). The OS assigns the port.
- stdout is force-set to line-buffered at the top of :func:`run` so
  the ``server_started`` JSON is flushed immediately — the host's
  pipe would otherwise block-buffer it and the host would hang
  waiting for the line (ADR-0020 §1 Phase-0 blocker).
- All non-handshake logs go to **stderr** (or the rotating file log
  via ``log.py``), never stdout — keeps the stdout JSON protocol
  unambiguous.

Rate limiting
-------------
ADR-0019's per-connection rate limiter
(:mod:`voice_typer.server.log_rate_limit`) is applied on every
incoming WS frame, mirroring the TCP path. A client that exceeds
200 burst / 60 sustained msg/s gets ``{"type":"error","code":
"rate_limited","data":{"retry_after_ms":...}}`` and the connection
stays open.

Heartbeat
---------
ADR-0018's heartbeat watchdog is DISABLED on the Tauri path via the
``TAURI_SIDECAR=1`` env var (see ``ipc_server.py``). The Rust
supervisor detects sidecar death via WS-close / process exit and
runs FT-1 respawn (ADR-0020 §10), replacing the 120-second
heartbeat-timeout watchdog.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

# websockets is a hard new dep under ADR-0020 §14. Import lazily
# inside run() so the module imports cleanly without the dep
# installed (e.g. on the Electron-only build path); the runtime
# check produces a clean ImportError with an actionable message.

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

log = logging.getLogger("voice_typer.server.sidecar_ws")

# Hard loopback-only bind (ADR-0020 §1). Binding 0.0.0.0 / :: would
# (a) pop a Windows Defender Firewall prompt, (b) trigger an macOS
# Application Firewall prompt, (c) expose the authed-but-localhost
# IPC to the LAN. Fail the launch if the configured bind is not
# loopback.
_LOOPBACK_HOST = "127.0.0.1"

# ADR-0020 §10: 1 MiB WS frame cap. download_progress and
# vocabulary_suggestion can carry large payloads; without a cap a
# malformed/huge frame can OOM the client.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Auth frame timeout (seconds). A client that connects but never
# sends the auth frame must not hold the connection indefinitely —
# matches the TCP path's 5-second auth timeout (PR-3-FIX-1).
_AUTH_TIMEOUT_SECONDS = 5.0

# Cooperative shutdown hard timeout (ADR-0020 §10). When the host
# sends {"type":"shutdown"} the sidecar must release the mic, ack,
# and exit within this window; if it doesn't, the host force-kills
# the process tree via kill_children.
_SHUTDOWN_ACK_TIMEOUT_SECONDS = 2.0


def _force_line_buffered_stdout() -> None:
    """Force stdout to line buffering (ADR-0020 §1 Phase-0 blocker).

    When the Tauri host pipes the sidecar's stdout, CPython switches
    to block buffering, so the ``server_started`` JSON is held in
    the buffer and the host hangs forever waiting. ``reconfigure``
    flips the stream back to line buffering so each ``\\n`` flushes.

    Python 3.7+ supports ``sys.stdout.reconfigure``; we guard for
    older interpreters (the project floor is 3.10 per pyproject.toml
    so this is always available, but the guard is defensive).
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        # Fallback: reopen stdout with buffering=1 (line-buffered).
        # This loses the original fd's write-only-on-flush semantics
        # but is the standard pattern for unbuffered stdio.
        with contextlib.suppress(Exception):
            sys.stdout = open(  # noqa: SIM115 - intentional reopen
                sys.stdout.fileno(),
                "w",
                buffering=1,
                encoding="utf-8",
                closefd=False,
            )


def _emit_server_started(port: int) -> None:
    """Write the one structured stdout line the host is parsing for.

    Per ADR-0020 §1, this is the ONLY thing that ever goes to stdout
    from the sidecar. Every other log goes to stderr / the rotating
    file log. The host blocks reading stdout until it parses this
    JSON, then opens a WS client to ws://127.0.0.1:<port>.
    """
    payload = json.dumps({"event": "server_started", "port": int(port)})
    print(payload, flush=True)


async def _authenticate(websocket) -> bool:
    """Read the first WS frame and validate the HMAC token.

    Per ADR-0020 §3, the client's first frame must be::

        {"type": "auth", "token": "<token>"}

    The token is compared with :func:`hmac.compare_digest` (constant
    time) against the ``VOICE_TYPER_IPC_TOKEN`` env var set by the
    Rust host at spawn. On mismatch, the socket is closed immediately
    and the connection is rejected (the host treats this as a crash
    → FT-1 respawn with a fresh token, ADR-0020 §10).

    Returns ``True`` if authenticated, ``False`` if rejected.
    """
    expected_token = os.environ.get("VOICE_TYPER_IPC_TOKEN", "")
    if not expected_token:
        log.error(
            "[SIDECAR-WS] VOICE_TYPER_IPC_TOKEN not set — refusing to "
            "accept connections (the host must always set this env var)."
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=_AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning("[SIDECAR-WS] auth frame timeout — closing connection")
        return False
    except Exception:
        log.warning("[SIDECAR-WS] auth frame read failed", exc_info=True)
        return False

    try:
        if isinstance(first_raw, bytes):
            first_raw = first_raw.decode("utf-8")
        first = json.loads(first_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("[SIDECAR-WS] auth frame is not valid JSON")
        return False

    if not isinstance(first, dict) or first.get("type") != "auth":
        log.warning("[SIDECAR-WS] first frame is not an auth frame")
        return False

    provided = first.get("token", "")
    if not isinstance(provided, str) or not provided:
        log.warning("[SIDECAR-WS] auth frame missing token")
        return False

    if not hmac.compare_digest(provided, expected_token):
        log.warning("[SIDECAR-WS] auth token mismatch — rejecting")
        return False

    log.info("[SIDECAR-WS] auth accepted")
    return True


def _make_dispatch(server: IPCServer):
    """Build a coroutine that dispatches a single WS frame.

    Reuses ``server._dispatch`` (the same path the TCP loop uses),
    so the 68-command registry + _validate_dict_payload + every
    handler mixin is exercised unchanged (ADR-0020 §2).
    """
    # ADR-0019: per-connection rate limiter. Reuse the same private
    # _RateLimiter class the TCP path uses (ipc_server.py:215) so the
    # burst/sustained semantics are identical — 200 burst, 600 sustained
    # over a 10s window (RELIABILITY-006-FIX-10).
    from voice_typer.server.ipc_server import _RateLimiter

    rate_limiter = _RateLimiter()

    async def dispatch(msg: dict, websocket) -> dict | None:
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            return {
                "type": "error",
                "data": {"code": "invalid_payload", "message": "missing 'type'"},
            }

        # ADR-0020 §10: cooperative shutdown.
        if msg_type == "shutdown":
            log.info("[SIDECAR-WS] shutdown received — releasing mic and exiting")
            # Delegate to the app's quit path so mic/volume/mutex
            # cleanup runs identically to the Electron quit path.
            try:
                # Schedule the shutdown on a background thread so we
                # can ack first; the host's hard timeout is 2.0s.
                import threading

                def _do_shutdown() -> None:
                    try:
                        server.app.quit()
                    except Exception:
                        log.exception("[SIDECAR-WS] shutdown handler raised")

                threading.Thread(
                    target=_do_shutdown,
                    name="sidecar-shutdown",
                    daemon=True,
                ).start()
            except Exception:
                log.exception("[SIDECAR-WS] failed to schedule shutdown")
            return {"type": "result", "data": {"ack": True}}

        # ADR-0019 rate limit check. _RateLimiter.allow() returns a
        # bool (no retry-after); the host backs off via FT-1 backoff
        # on repeated rate-limit hits.
        if not rate_limiter.allow():
            rate_limiter.reject()
            return {
                "type": "error",
                "data": {
                    "code": "rate_limited",
                    "message": "rate limit exceeded; backing off",
                },
            }

        # Dispatch on the worker thread pool so a slow handler
        # (e.g. download_model) doesn't block the WS reader.
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, server._dispatch, msg)
        except Exception:
            log.exception("[SIDECAR-WS] _dispatch raised")
            return {
                "type": "error",
                "data": {"code": "internal_error", "message": "dispatch raised"},
            }

        # _dispatch returns None for fire-and-forget commands (e.g.
        # restart_app, which sends its own response). Don't send a
        # frame in that case.
        return result

    return dispatch


async def _handle_connection(websocket, server: IPCServer, dispatch) -> None:
    """Per-connection WS handler: auth + read/dispatch loop.

    ADR-0020 §1: the sidecar is the WS SERVER; the Rust host is the
    WS CLIENT. Multiple connections are allowed (e.g. the host may
    reconnect after a transient WS drop), but only one authenticated
    connection at a time is meaningful — the host uses FT-1 respawn
    rather than reconnect, so a duplicate connection implies a
    protocol bug worth logging.
    """
    peer = websocket.remote_address
    log.info("[SIDECAR-WS] client connected from %s", peer)

    if not await _authenticate(websocket):
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="auth failed")
        return

    # Subscribe server.push (which forwards event_bus.publish) to
    # this WS so server-initiated events flow back to the host.
    # The TCP path installs a single _tcp_client and writes to it
    # under self._lock; the WS path uses a per-connection asyncio
    # Queue + a writer task so we don't block the dispatch loop.
    outbound: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)

    def _push_to_ws(event: dict) -> None:
        """Subscriber for event_bus.publish — enqueues for the writer task."""
        # Don't let a slow host block the publisher thread (event_bus
        # is process-global). Drop the oldest pending event if the
        # queue is full — the host will recover via state snapshots.
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

    from voice_typer.server import event_bus

    event_bus.subscribe(_push_to_ws)

    async def _writer() -> None:
        """Drain the outbound queue and write each event as a WS frame."""
        try:
            while True:
                event = await outbound.get()
                if event is None:
                    return
                try:
                    raw = json.dumps(event, ensure_ascii=False)
                    if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:
                        log.error(
                            "[SIDECAR-WS] outbound frame exceeds %d bytes — dropping",
                            _MAX_FRAME_BYTES,
                        )
                        continue
                    await websocket.send(raw)
                except Exception:
                    log.warning("[SIDECAR-WS] send failed", exc_info=True)
                    return
        except asyncio.CancelledError:
            return

    writer_task = asyncio.create_task(_writer(), name="sidecar-ws-writer")

    try:
        async for raw in websocket:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {
                                "code": "invalid_payload",
                                "message": f"frame exceeds {_MAX_FRAME_BYTES} bytes",
                            },
                        }
                    )
                )
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {"code": "invalid_payload", "message": "invalid JSON"},
                        }
                    )
                )
                continue

            if not isinstance(msg, dict):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {"code": "invalid_payload", "message": "frame must be an object"},
                        }
                    )
                )
                continue

            # The frame may carry an optional "id" for request/response
            # correlation (ADR-0020 §7 — the host's dispatch() command
            # assigns a per-request id). Echo it back on the response.
            request_id = msg.get("id")
            result = await dispatch(msg, websocket)
            if result is not None:
                if request_id is not None and isinstance(result, dict):
                    result = {**result, "id": request_id}
                try:
                    await websocket.send(json.dumps(result, ensure_ascii=False))
                except Exception:
                    log.warning("[SIDECAR-WS] response send failed", exc_info=True)
                    break
    except Exception:
        log.info("[SIDECAR-WS] connection ended", exc_info=True)
    finally:
        event_bus.unsubscribe(_push_to_ws)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer_task
        log.info("[SIDECAR-WS] connection closed (peer=%s)", peer)


def run(server: IPCServer) -> int:
    """Bind a localhost WS server on an ephemeral port and run forever.

    Returns the bound port (also emitted to stdout via
    :func:`_emit_server_started` before the accept loop starts).

    Per ADR-0020 §1, this is the canonical Tauri-sidecar entrypoint.
    The host (Rust) reads the ``server_started`` JSON from the
    sidecar's stdout, then opens a WS client to the reported port.

    The function blocks until the asyncio loop is cancelled (e.g.
    SIGTERM from the host's kill_children backstop).
    """
    _force_line_buffered_stdout()

    # Local import so the module imports cleanly without `websockets`
    # installed (the Electron-only build path doesn't need it).
    try:
        import websockets
        from websockets.asyncio.server import serve
    except ImportError as exc:
        log.error(
            "[SIDECAR-WS] the `websockets` package is required for the "
            "Tauri sidecar path. Install with: uv pip install websockets. "
            "Original error: %s",
            exc,
        )
        return 2

    dispatch = _make_dispatch(server)

    async def _main() -> int:
        # bind on 127.0.0.1:0 → OS assigns an ephemeral port.
        # max_size enforces the 1 MiB frame cap (ADR-0020 §10).
        async with serve(
            _handler,
            _LOOPBACK_HOST,
            0,
            max_size=_MAX_FRAME_BYTES,
        ) as ws_server:
            # Read back the OS-assigned port. websockets.asyncio.server
            # exposes the underlying socket via .sockets.
            socks = ws_server.sockets
            if not socks:
                log.error("[SIDECAR-WS] no sockets bound — aborting")
                return 3
            port = socks[0].getsockname()[1]
            _emit_server_started(port)
            log.info("[SIDECAR-WS] listening on %s:%d", _LOOPBACK_HOST, port)

            # Run until cancelled.
            await asyncio.Future()

        return 0

    async def _handler(websocket) -> None:
        await _handle_connection(websocket, server, dispatch)

    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("[SIDECAR-WS] interrupted — shutting down")
        return 0
    except Exception:
        log.exception("[SIDECAR-WS] fatal error in run()")
        return 1
