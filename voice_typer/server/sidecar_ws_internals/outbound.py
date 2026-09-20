"""This module OWNS the outbound wire defenses (the C-WS-2 TEXT-frame
Patch-path contract (C-ARCH-2 canonical form): the observers that
surface (``sidecar_ws._encode_ws_frame``,
``sidecar_ws._safe_send(ws, event)``,
``sidecar_ws._start_writer(ws, queue)``: tests/test_sidecar_ws.py,
``inspect.getsource`` pins on the function objects keep working.
contract lives in ``_safe_send``'s WIRE CONTRACT comment):
- ``_MAX_FRAME_BYTES``: the ADR-0020 §10 1 MiB frame cap. The
  canonical module keeps a value alias (for ``run()``'s
  ``serve(max_size=_MAX_FRAME_BYTES)`` bare-name read and the value
- ``_WS_SEND_TIMEOUT_SECONDS``: the send timeout observed by
  ``_safe_send``. Tests that lower it patch THIS module (the
  pre-split direct assignment on ``sidecar_ws`` became a no-op when
- ``_encode_ws_frame``: the single ``json.dumps`` + UTF-8 encode,
  offloaded to the encode pool by ``_safe_send``.
- ``_safe_send``: encode-offload + size-cap + send-timeout send
- ``_start_writer``: the per-connection writer task draining the
  outbound queue through ``_safe_send``.
call into this module (``_dispatch_and_respond`` /
``_handle_connection_inner`` in the canonical module) resolve
``_safe_send`` / ``_start_writer`` via the ``_outbound_mod``
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging

from voice_typer.server.sidecar_ws_internals.encode_pool import _get_ws_encode_pool

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")


# ADR-0020 §10: 1 MiB WS frame cap. download_progress and
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Outbound ``websocket.send`` timeout (seconds). A send that has not
_WS_SEND_TIMEOUT_SECONDS = 5.0


def _encode_ws_frame(event: dict) -> bytes:
    """Serialize ``event`` to a WS TEXT-frame payload (UTF-8 bytes)."""
    return json.dumps(event, ensure_ascii=False).encode("utf-8")


async def _safe_send(websocket, event: dict) -> str:
    """Encode + size-cap + timeout-protected send for one outbound WS frame.

    Returns one of three status strings so the caller can distinguish
    """
    loop = asyncio.get_running_loop()
    raw_bytes = await loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)
    # Byte count is authoritative: Rust tungstenite reader enforces max_size on receive.
    if len(raw_bytes) > _MAX_FRAME_BYTES:
        log.error(
            "[SIDECAR-WS] outbound frame exceeds %d bytes, dropping",
            _MAX_FRAME_BYTES,
        )
        return "dropped"
    try:
        # WIRE CONTRACT (do not "optimize" this decode away): dispatch
        await asyncio.wait_for(
            websocket.send(raw_bytes.decode("utf-8")),
            timeout=_WS_SEND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning(
            "[SIDECAR-WS] send timed out after %.1fs, closing connection",
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
    """Create the per-connection writer task that drains the outbound"""

    async def _writer() -> None:
        """Drain the outbound queue and write each event as a WS frame."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                event = await outbound.get()
                if event is None:
                    return
                raw_str = await loop.run_in_executor(
                    _get_ws_encode_pool(), functools.partial(json.dumps, event, ensure_ascii=False)
                )
                if len(raw_str.encode("utf-8")) > _MAX_FRAME_BYTES:
                    log.error(
                        "[SIDECAR-WS] outbound frame exceeds %d bytes, dropping",
                        _MAX_FRAME_BYTES,
                    )
                    continue
                try:
                    await asyncio.wait_for(
                        websocket.send(raw_str),
                        timeout=_WS_SEND_TIMEOUT_SECONDS,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    log.warning(
                        "[SIDECAR-WS] send timed out after %.1fs, closing connection",
                        _WS_SEND_TIMEOUT_SECONDS,
                    )
                    with contextlib.suppress(Exception):
                        await websocket.close(code=1011, reason="send timeout")
                    return
                except Exception:
                    log.warning("[SIDECAR-WS] send failed", exc_info=True)
                    return
        except asyncio.CancelledError:
            return

    return asyncio.create_task(_writer(), name="sidecar-ws-writer")
