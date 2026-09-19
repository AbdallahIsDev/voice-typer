"""WS frame-encode ThreadPoolExecutor, singleton cache + lifecycle."""

from __future__ import annotations

import contextlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")


# Dedicated ThreadPoolExecutor for WS frame ENCODE offload (json.dumps +
_ws_encode_pool_singleton: ThreadPoolExecutor | None = None


def _get_ws_encode_pool(server: IPCServer | None = None) -> ThreadPoolExecutor:
    """Lazily create / return the WS frame-encode thread pool."""
    global _ws_encode_pool_singleton
    if server is not None:
        pool = getattr(server, "_ws_encode_pool", None)
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="sidecar-ws-encode",
            )
            # ``setattr`` on a real IPCServer stores the attribute; on
            server._ws_encode_pool = pool
        # Seed / refresh the module-level singleton so the ``_writer``
        _ws_encode_pool_singleton = pool
        return pool
    # call has happened yet (defensive, in production
    if _ws_encode_pool_singleton is None:
        _ws_encode_pool_singleton = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-encode",
        )
    return _ws_encode_pool_singleton


def shutdown_encode_pool(server: IPCServer | None = None) -> None:
    """Drain / cancel in-flight WS frame encodes."""
    global _ws_encode_pool_singleton
    pool: ThreadPoolExecutor | None = None
    if server is not None:
        pool = getattr(server, "_ws_encode_pool", None)
        if pool is not None:
            # Defensive, never fatal.
            with contextlib.suppress(Exception):
                server._ws_encode_pool = None
    if pool is None and _ws_encode_pool_singleton is not None:
        pool = _ws_encode_pool_singleton
        _ws_encode_pool_singleton = None
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001, defensive, never fatal
            log.warning("[SIDECAR-WS] encode pool shutdown failed", exc_info=True)
