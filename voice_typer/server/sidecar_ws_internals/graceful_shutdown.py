"""Graceful WS shutdown. Close-all-connections + server hook installer."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
log = logging.getLogger("voice_typer.server.sidecar_ws")


async def _graceful_close_all_conns(server: IPCServer) -> None:
    """Send ``close(code=1001, reason="going away")`` to every
    authenticated WS connection, then sleep for the close-handshake
    budget so the peer has time to receive the close frame before the
    asyncio loop is stopped.

    Runs on the WS loop via :func:`asyncio.run_coroutine_threadsafe`
    from :func:`ws_graceful_shutdown` (which is invoked from a
    non-loop thread, the ``ShutdownController._do_cleanup`` thread).
    Each ``ws.close()`` is awaited sequentially so the close frames
    are emitted in arrival order; a single wedged peer cannot block
    the whole close pass because the outer
    :func:`asyncio.run_coroutine_threadsafe` ``.result(timeout=...)``
    bounds the total close pass.

    Failures on individual connections are logged at DEBUG and the
    close pass continues, one dead peer must not prevent the close
    frame from reaching the other (still-alive) peer.
    """
    # Resolve the handshake budget from the canonical module at CALL
    from voice_typer.server import sidecar_ws as _canonical

    conns = list(getattr(server, "_ws_authenticated_conns", set()))
    for ws in conns:
        try:
            await ws.close(code=1001, reason="going away")
        except Exception:
            log.debug(
                "[SIDECAR-WS] graceful close failed for one connection",
                exc_info=True,
            )
    # Allow time for the WS close handshake to complete on the wire
    await asyncio.sleep(_canonical._WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS)


def _attach_ws_graceful_shutdown(server: IPCServer) -> None:
    """Install graceful-shutdown hooks on the IPCServer.

    Adds three pieces of WS-state to the server (idempotently —
    existing values are preserved) and installs a
    ``ws_graceful_shutdown`` callable plus a ``server.stop`` wrapper:

    - ``server._ws_authenticated_conns``: ``set`` of authenticated
      websockets, populated by :func:`_handle_connection_inner` after a
      successful auth and discarded in the connection ``finally`` block.
      ``ws_graceful_shutdown`` iterates this set to send ``close(1001)``.
    - ``server._ws_dispatch_futures``: ``set`` of in-flight
      ``concurrent.futures.Future`` objects. The dispatch path may
      register futures here so ``ws_graceful_shutdown`` can
      bounded-wait for them.
    - ``server._ws_loop``: the asyncio loop running :func:`run._main`.
      Set in :func:`_handle_connection_inner` (per-connection, but the
      loop is shared across all connections) and read by
      ``ws_graceful_shutdown`` to schedule the close coroutine +
      ``loop.stop``. Without this reference, ``ws_graceful_shutdown``
      (invoked from a non-loop thread) would have no way to stop the
      WS loop, the loop would stay alive until process exit, defeating
      the graceful-shutdown contract.

    The ``server.stop`` wrapper calls ``server.ws_graceful_shutdown()``
    FIRST (looked up dynamically so tests can replace it post-install),
    then delegates to the original ``server.stop``. Exceptions from
    ``ws_graceful_shutdown`` are logged at DEBUG and the original
    ``stop`` STILL runs, a failure in the WS close path must not
    prevent the TCP teardown. This satisfies the "BEFORE
    ``ipc_server.stop()``" requirement WITHOUT modifying
    ``shutdown_controller.py`` or ``ipc_server.py`` (file ownership
    boundary: this module owns all WS-state).

    Idempotent: a second call is a no-op (detected via the
    ``_ws_graceful_shutdown_installed`` marker). Without this guard, a
    double-install would wrap ``server.stop`` twice, creating a chain
    of wrappers calling each other on every shutdown.
    """
    if getattr(server, "_ws_graceful_shutdown_installed", False):
        return
    server._ws_graceful_shutdown_installed = True

    # Initialize the WS-state attributes ONLY if they are not already
    if getattr(server, "_ws_authenticated_conns", None) is None:
        server._ws_authenticated_conns = set()
    if getattr(server, "_ws_dispatch_futures", None) is None:
        server._ws_dispatch_futures = set()

    def ws_graceful_shutdown() -> None:
        """Send close(1001) to all authenticated conns, bounded-wait
        for in-flight dispatch futures, then stop the WS loop.

        Invoked from a non-loop thread (the
        ``ShutdownController._do_cleanup`` thread via the
        ``server.stop`` wrapper). The close coroutine is scheduled on
        the WS loop via :func:`asyncio.run_coroutine_threadsafe` so it
        runs on the loop that owns the websockets (calling
        ``ws.close()`` on a different loop is unsafe for real
        ``websockets`` library connections, their internal state is
        tied to the loop that created them).

        The dispatch-future drain uses
        ``concurrent.futures.Future.result(timeout=...)`` which is a
        blocking call safe to invoke from any thread. Each future gets
        its own timeout, a single stuck handler cannot block the
        whole drain pass.

        The loop stop is scheduled via
        ``loop.call_soon_threadsafe(loop.stop)``: the only
        documented thread-safe way to hand work to an asyncio loop
        from outside it. ``loop.stop`` causes ``loop.run_forever()``
        (in :func:`run`) to return, which lets ``asyncio.run()``
        finalize the loop and ``run()`` return to its caller.

        If ``server._ws_loop`` is unset or already closed, the close
        and stop are skipped (logged at DEBUG), the drain still runs
        so any in-flight futures are bounded-waited. This makes
        ``ws_graceful_shutdown`` safe to call even when the WS path
        was never entered (e.g. the server ran in TCP-only mode).
        """
        # Resolve the shutdown budgets from the canonical module at
        from voice_typer.server import sidecar_ws as _canonical

        loop = getattr(server, "_ws_loop", None)

        # 1. Send close(1001, "going away") to each authenticated conn
        if loop is not None and not loop.is_closed():
            try:
                close_future = asyncio.run_coroutine_threadsafe(
                    _graceful_close_all_conns(server),
                    loop,
                )
                # Bounded-wait: handshake sleep (0.5 s) + per-conn
                close_future.result(
                    timeout=(
                        _canonical._WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS
                        + _canonical._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS
                        + 0.5
                    ),
                )
            except Exception:
                log.debug(
                    "[SIDECAR-WS] graceful close pass failed or timed out, continuing to drain + loop stop",
                    exc_info=True,
                )
        else:
            log.debug("[SIDECAR-WS] no WS loop reference (or loop closed), skipping close pass")

        # 2. Bounded-wait for in-flight dispatch futures. Each future
        futures = list(getattr(server, "_ws_dispatch_futures", set()))
        for future in futures:
            try:
                future.result(timeout=_canonical._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS)
            except Exception:
                log.debug(
                    "[SIDECAR-WS] dispatch future did not complete within %.1fs drain timeout, proceeding to loop stop",
                    _canonical._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS,
                    exc_info=True,
                )

        # 3. Stop the WS loop. ``call_soon_threadsafe`` is the only
        with contextlib.suppress(Exception):
            server._ws_graceful_stop_requested = True
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                log.debug(
                    "[SIDECAR-WS] loop.stop() scheduling failed, loop already closed",
                    exc_info=True,
                )
        else:
            log.debug("[SIDECAR-WS] no WS loop reference (or loop closed), cannot stop loop")

    server.ws_graceful_shutdown = ws_graceful_shutdown  # type: ignore[attr-defined]

    # Install ``ws_graceful_shutdown`` as an EXPLICIT stop hook (the
    def _stop_hook() -> None:
        try:
            # Dynamic lookup: see comment above.
            hook = server.ws_graceful_shutdown
            if hook is not None:
                hook()
        except Exception:
            log.debug(
                "[SIDECAR-WS] ws_graceful_shutdown raised, continuing to original stop",
                exc_info=True,
            )

    server._ws_stop_hook = _stop_hook
