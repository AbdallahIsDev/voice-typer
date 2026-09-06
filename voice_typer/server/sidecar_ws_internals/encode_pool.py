"""WS frame-encode ThreadPoolExecutor — singleton cache + lifecycle.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws` (the
``_ws_encode_pool_singleton`` / ``_get_ws_encode_pool`` /
``shutdown_encode_pool`` block); the canonical module re-exports these
names so ``sidecar_ws._get_ws_encode_pool`` and
``sidecar_ws.shutdown_encode_pool`` keep resolving. The encode FUNCTION
(``_encode_ws_frame``) stays in the canonical module — its body is
pinned by ``tests/test_ipc_server.py::TestWriterEncodesOnce``
(``inspect.getsource(sidecar_ws)`` whole-module source check).

The singleton global lives HERE (with its accessor functions) — it is
the only module-level mutable state sidecar_ws.py ever had.
"""

from __future__ import annotations

import contextlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")


# Dedicated ThreadPoolExecutor for WS frame ENCODE offload (json.dumps +
# .encode on a worker thread). Mirrors the dispatch pool pattern in
# ``_make_dispatch`` (the ``_ws_dispatch_pool`` block): created lazily
# on first use, stored on the IPC server as ``server._ws_encode_pool``
# so ``ShutdownController._do_cleanup`` can reach it via
# ``app._ipc_server._ws_encode_pool`` and
# ``shutdown(wait=False, cancel_futures=True)`` to drain / cancel
# in-flight encodes BEFORE tearing down the recorder / history DB /
# crash-recovery writer.
#
# A module-level singleton cache (``_ws_encode_pool_singleton``) is
# also kept so the per-connection ``_writer`` task — which has no
# server reference (its signature is locked to ``(websocket, outbound)``
# by ``tests/test_sidecar_ws_handle_connection_split.py``) — can reach
# the same pool. The single-process / single-server lifecycle makes
# the singleton safe: there is exactly one encode pool per sidecar
# process. ``_make_dispatch(server)`` seeds the singleton on first
# call, before any WS connection is accepted.
#
# Pre- this used ``loop.run_in_executor(None, _encode_ws_frame, event)``
# — the asyncio loop's DEFAULT executor, which has no handle
# ``ShutdownController`` can reach. A long-running encode (a near-cap
# 1 MiB ``vocabulary_suggestion`` frame at shutdown) would race
# teardown, half-flush the history DB, and leak a partially-written
# crash-recovery snapshot. The dedicated pool lets the shutdown path
# bounded-wait for in-flight encodes (mirroring the dispatch-pool
# drain).
_ws_encode_pool_singleton: ThreadPoolExecutor | None = None


def _get_ws_encode_pool(server: IPCServer | None = None) -> ThreadPoolExecutor:
    """Lazily create / return the WS frame-encode thread pool.

    Mirrors the dispatch pool pattern in ``_make_dispatch`` (lines
    ~550-558): created on first use, stored on the IPC server as
    ``server._ws_encode_pool`` so the shutdown path can reach it via
    ``app._ipc_server._ws_encode_pool``. When called WITHOUT a server
    (the ``_writer`` task's case — its signature is locked to
    ``(websocket, outbound)``), the module-level singleton cache is
    used. The first call WITH a server seeds the singleton; subsequent
    calls without a server reuse it.

    ``max_workers=4`` matches the WS dispatch pool size
    (``_ws_dispatch_pool``, also 4). The encode workload (~50-100 ms
    per near-cap frame) can saturate 2 workers under concurrent
    connections each pushing near-cap frames at 1-5 Hz — a third
    in-flight encode would queue behind the two workers, stalling the
    writer task's outbound drain and back-pressuring the dispatch
    path's response serialization. Aligning the encode pool to 4
    workers gives headroom for 3-4 concurrent near-cap encodes without
    contending with the dispatch pool (which spends its CPU on
    handler work, not encode work).
    """
    global _ws_encode_pool_singleton
    if server is not None:
        pool = getattr(server, "_ws_encode_pool", None)
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="sidecar-ws-encode",
            )
            # ``setattr`` on a real IPCServer stores the attribute; on
            # a MagicMock test double it overrides the auto-vivified
            # child (same pattern as ``_ws_dispatch_pool`` above).
            server._ws_encode_pool = pool
        # Seed / refresh the module-level singleton so the ``_writer``
        # task (which has no server reference) can reach the same pool.
        _ws_encode_pool_singleton = pool
        return pool
    # No server reference (called from ``_writer`` which only has
    # websocket + outbound, or from ``_read_loop``'s response path
    # where we want to avoid the ``getattr`` overhead on every frame).
    # Use the module-level cache; create lazily if no server-bearing
    # call has happened yet (defensive — in production
    # ``_make_dispatch(server)`` runs in ``run(server)`` BEFORE any
    # connection is accepted, so the singleton is seeded before
    # ``_writer`` is started).
    if _ws_encode_pool_singleton is None:
        _ws_encode_pool_singleton = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-encode",
        )
    return _ws_encode_pool_singleton


def shutdown_encode_pool(server: IPCServer | None = None) -> None:
    """Drain / cancel in-flight WS frame encodes.

    Called by ``ShutdownController._do_cleanup`` (in
    ``shutdown_controller.py`` — NOT this file) to drain / cancel
    in-flight encodes BEFORE tearing down the recorder / history DB /
    crash-recovery writer. Mirrors the dispatch-pool drain
    (``_ws_dispatch_pool.shutdown(wait=False, cancel_futures=True)``).

    NOTE: ``shutdown_controller.py`` is OUTSIDE this module's file
    ownership boundary. A matching ``sidecar_ws.shutdown_encode_pool(
    ipc_server)`` call must be added to ``_do_cleanup`` in a follow-up
    by the orchestrator (or by the shutdown_controller.py owner);
    until then the pool's worker threads are leaked on shutdown
    (acceptable — the process is exiting anyway, the OS reclaims the
    threads, and any in-flight encode is aborted by process exit
    regardless of pool state).
    """
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
        except Exception:  # noqa: BLE001 — defensive, never fatal
            log.warning("[SIDECAR-WS] encode pool shutdown failed", exc_info=True)
