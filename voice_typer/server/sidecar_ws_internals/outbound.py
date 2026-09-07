"""Outbound WS frame path: encode, size-cap, send timeout, writer task.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`; the
canonical module re-exports every name below so the direct-call test
surface (``sidecar_ws._encode_ws_frame``,
``sidecar_ws._safe_send(ws, event)``,
``sidecar_ws._start_writer(ws, queue)`` — tests/test_sidecar_ws.py,
tests/test_sidecar_ws_safe_send_text_frames.py,
tests/test_ws_frame_size_check.py,
tests/test_sidecar_ws_permissions_fixes.py) and the
``inspect.getsource`` pins on the function objects keep working.

This module OWNS the outbound wire defenses (the C-WS-2 TEXT-frame
contract lives in ``_safe_send``'s WIRE CONTRACT comment):

- ``_MAX_FRAME_BYTES`` — the ADR-0020 §10 1 MiB frame cap. The
  canonical module keeps a value alias (for ``run()``'s
  ``serve(max_size=_MAX_FRAME_BYTES)`` bare-name read and the value
  assertions in the mig15-17 / unit suites); the LIVE constant is
  here — nothing rebinds it in production, and no test patches it.
- ``_WS_SEND_TIMEOUT_SECONDS`` — the send timeout observed by
  ``_safe_send``. Tests that lower it patch THIS module (the
  pre-split direct assignment on ``sidecar_ws`` became a no-op when
  the function moved; tests/test_sidecar_ws_permissions_fixes.py now
  patches the owning submodule).
- ``_encode_ws_frame`` — the single ``json.dumps`` + UTF-8 encode,
  offloaded to the encode pool by ``_safe_send``.
- ``_safe_send`` — encode-offload + size-cap + send-timeout send
  helper shared by the dispatch-response path and the writer task.
- ``_start_writer`` — the per-connection writer task draining the
  outbound queue through ``_safe_send``.

Patch-path contract (C-ARCH-2 canonical form): the observers that
call into this module (``_dispatch_and_respond`` /
``_handle_connection_inner`` in the canonical module) resolve
``_safe_send`` / ``_start_writer`` via the ``_outbound_mod``
module-object read at call time, so owning-submodule patches are
observed by production.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from voice_typer.server.sidecar_ws_internals.encode_pool import _get_ws_encode_pool

# Same logger object as the canonical module (``logging.getLogger`` is
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")


# ADR-0020 §10: 1 MiB WS frame cap. download_progress and
# vocabulary_suggestion can carry large payloads; without a cap a
# malformed/huge frame can OOM the client.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Outbound ``websocket.send`` timeout (seconds). A send that has not
# completed within this window is treated as a stuck peer (TCP send
# buffer full, slow consumer, half-open socket) — the connection is
# closed so the host's reconnect path can take over instead of
# letting the WS writer task block the event loop indefinitely on a
# single ``await websocket.send``. 5s is generous for a 1 MiB frame
# over loopback (sub-millisecond RTT, multi-GiB/s throughput) but
# bounded enough that a wedged peer doesn't tie up the writer task
# (and the asyncio loop thread) forever.
_WS_SEND_TIMEOUT_SECONDS = 5.0


def _encode_ws_frame(event: dict) -> bytes:
    """Serialize ``event`` to a WS TEXT-frame payload (UTF-8 bytes).

    Runs ``json.dumps`` + ``.encode`` together so the whole O(n)
    encode cost stays OFF the asyncio loop thread — the writer task
    in :func:`_start_writer` calls this via
    ``loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)``.
    For near-cap frames (~1 MiB) the in-line encode was 50-100 ms of
    pure CPU on the loop thread, stalling every other connection's
    reads + the heartbeat fast-path.

    ``ensure_ascii=False`` keeps multi-byte UTF-8 (e.g. CJK / emoji
    dictation) as-is on the wire instead of escaping to ``\\uXXXX``
    (the default ``ensure_ascii=True`` would bloat CJK payloads ~3x).
    """
    return json.dumps(event, ensure_ascii=False).encode("utf-8")


async def _safe_send(websocket, event: dict) -> str:
    """Encode + size-cap + timeout-protected send for one outbound WS frame.

    Shared by the dispatch-response path in :func:`_read_loop` and the
    writer-task path in :func:`_start_writer._writer`. Both paths MUST
    apply the same three defenses against an oversized / wedged-peer
    DoS — pre-fix the dispatch-response path applied NONE of them,
    so a handler returning a multi-MiB response (e.g. ``get_history``
    / ``list_models`` / ``get_vocabulary`` for a user with thousands
    of entries) would (1) block the asyncio loop thread with
    synchronous ``json.dumps`` (50-100 ms per MiB — stalls every
    other connection's reads + the heartbeat fast-path), (2) block
    forever if the peer's TCP send buffer fills (no timeout), and
    (3) exceed the 1 MiB ``_MAX_FRAME_BYTES`` cap that ADR-0020 §10
    mandates (the websockets library's ``max_size`` is enforced on
    INBOUND frames only — the OUTBOUND side had no cap).

    The three defenses:

    (1) Offload ``json.dumps`` + UTF-8 encode to a worker thread via
        :func:`loop.run_in_executor`. For near-cap frames (~1 MiB) the
        in-line encode was 50-100 ms of pure CPU on the asyncio loop
        thread, stalling every other connection's reads + the heartbeat
        fast-path.
    (2) Drop the frame pre-emptively with an ERROR log if
        ``len(raw_bytes) > _MAX_FRAME_BYTES`` — otherwise the Rust
        host's tungstenite ``max_size`` receive enforcement would close
        the connection on its end (with a 1009) and surface a
        misleading transport error. Measuring the actual UTF-8 byte
        count (not the char count) catches multi-byte-heavy frames
        (CJK / emoji dictation) the pre-fix char-count check missed.
    (3) Wrap ``websocket.send`` in :func:`asyncio.wait_for` with
        ``_WS_SEND_TIMEOUT_SECONDS`` so a wedged peer (full TCP send
        buffer, half-open socket) cannot tie up the calling coroutine
        (and the asyncio loop thread) indefinitely. On timeout the
        connection is closed with code 1011 so the host's reconnect
        path takes over.

    Returns one of three status strings so the caller can distinguish
    "drop and keep going" (oversized) from "drop and bail out"
    (timeout / send error):

    - ``"sent"`` — the frame was sent successfully.
    - ``"dropped"`` — the frame exceeded ``_MAX_FRAME_BYTES`` and was
      dropped with an ERROR log. The connection is still healthy.
    - ``"failed"`` — the send timed out (connection closed with 1011)
      OR raised an unexpected exception. The connection is unreliable
      or already closing.

    The caller MUST NOT re-attempt the send on ``"failed"`` — the
    timeout path already initiated a WS close, and a re-attempt would
    race the close handshake.
    """
    loop = asyncio.get_running_loop()
    raw_bytes = await loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)
    if len(raw_bytes) > _MAX_FRAME_BYTES:
        log.error(
            "[SIDECAR-WS] outbound frame exceeds %d bytes — dropping",
            _MAX_FRAME_BYTES,
        )
        return "dropped"
    try:
        # WIRE CONTRACT (do not "optimize" this decode away): dispatch
        # responses MUST go out as WS **TEXT** frames carrying a numeric
        # top-level ``id``. The Rust host's reader parses
        # ``Message::Text`` only and silently ignores ``Message::Binary``
        # — sending the encoded bytes directly produces BINARY frames,
        # every dispatch response vanishes inside the host, and ALL
        # renderer commands time out while heartbeat acks (sent inline
        # as ``str``) keep flowing ("Lost connection to Python backend",
        # first Windows host run 2026-08-21). Binding rule:
        # AGENTS.md constraint C-WS-2.
        await asyncio.wait_for(
            websocket.send(raw_bytes.decode("utf-8")),
            timeout=_WS_SEND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning(
            "[SIDECAR-WS] send timed out after %.1fs — closing connection",
            _WS_SEND_TIMEOUT_SECONDS,
        )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="send timeout")
        return "failed"
    except Exception:
        log.warning("[SIDECAR-WS] send failed", exc_info=True)
        return "failed"
    return "sent"


def _start_writer(websocket, outbound: asyncio.Queue) -> asyncio.Task:
    """Create the per-connection writer task that drains the outbound
    queue and writes each event as a WS frame.

    The TCP path installs a single ``_tcp_client`` and writes to it
    under ``self._lock``; the WS path uses a per-connection asyncio
    Queue + a writer task so we don't block the dispatch loop.
    """

    async def _writer() -> None:
        """Drain the outbound queue and write each event as a WS frame.

        Delegates each send to :func:`_safe_send`, which applies the
        three safeguards that previously lived inline in this
        coroutine: offloads ``json.dumps`` to ``loop.run_in_executor``,
        wraps ``websocket.send`` in ``asyncio.wait_for(timeout=
        _WS_SEND_TIMEOUT_SECONDS)``, and on ``TimeoutError`` calls
        ``websocket.close(code=1011, reason="send timeout")``. The
        shared helper is also used by :func:`_read_loop`'s
        dispatch-response path so the two outbound paths cannot
        diverge on the DoS defenses again (regression guard for
        the finding where the dispatch response bypassed the
        writer's size cap + timeout + off-loop encode).
        """
        try:
            while True:
                event = await outbound.get()
                if event is None:
                    return
                send_status = await _safe_send(websocket, event)
                if send_status == "failed":
                    # Timeout or send error — ``_safe_send`` already
                    # logged + initiated the close (timeout path) or
                    # the connection is otherwise unreliable. Return
                    # so the orchestrator's finally block cleans up
                    # (subscriber unsubscribe + writer-task cancel +
                    # active-connection slot clear).
                    return
                # ``"sent"`` → keep draining the queue.
                # ``"dropped"`` → the frame exceeded
                # ``_MAX_FRAME_BYTES`` and was logged + skipped by
                # ``_safe_send``. Preserve the pre-fix ``continue``
                # behaviour so one pathological event does not kill
                # the whole outbound stream (subsequent events on the
                # queue are independent and may be perfectly
                # sendable).
        except asyncio.CancelledError:
            return

    return asyncio.create_task(_writer(), name="sidecar-ws-writer")
