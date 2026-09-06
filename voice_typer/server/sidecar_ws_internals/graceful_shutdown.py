"""Graceful WS shutdown — close-all-connections + server hook installer.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`
(``_graceful_close_all_conns`` + ``_attach_ws_graceful_shutdown``);
the canonical module re-exports both names so
``sidecar_ws._attach_ws_graceful_shutdown(server)`` and the
``server.ws_graceful_shutdown`` / wrapped ``server.stop`` contract
keep working unchanged (``tests/test_sidecar_ws.py`` drives the
installer directly through the canonical attribute).

The shutdown budget constants (``_WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS``,
``_WS_DISPATCH_DRAIN_TIMEOUT_SECONDS``) stay in the canonical module —
they are part of sidecar_ws's constant surface — and are resolved here
at CALL time (never at import time) so the canonical module can import
this leaf at its own module top without a cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")


async def _graceful_close_all_conns(server: IPCServer) -> None:
    """Send ``close(code=1001, reason="going away")`` to every
    authenticated WS connection, then sleep for the close-handshake
    budget so the peer has time to receive the close frame before the
    asyncio loop is stopped.

    Runs on the WS loop via :func:`asyncio.run_coroutine_threadsafe`
    from :func:`ws_graceful_shutdown` (which is invoked from a
    non-loop thread — the ``ShutdownController._do_cleanup`` thread).
    Each ``ws.close()`` is awaited sequentially so the close frames
    are emitted in arrival order; a single wedged peer cannot block
    the whole close pass because the outer
    :func:`asyncio.run_coroutine_threadsafe` ``.result(timeout=...)``
    bounds the total close pass.

    Failures on individual connections are logged at DEBUG and the
    close pass continues — one dead peer must not prevent the close
    frame from reaching the other (still-alive) peer.
    """
    # Resolve the handshake budget from the canonical module at CALL
    # time (a module-top import would be circular: sidecar_ws imports
    # this leaf at its own module top). Call-time resolution also
    # preserves the pre-split patch seam exactly — an assignment to
    # ``sidecar_ws._WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS`` is observed
    # here, just as it was when this body lived in that module.
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
    # before ``loop.stop()`` fires — see
    # ``_WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS`` for the rationale.
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
      WS loop — the loop would stay alive until process exit, defeating
      the graceful-shutdown contract.

    The ``server.stop`` wrapper calls ``server.ws_graceful_shutdown()``
    FIRST (looked up dynamically so tests can replace it post-install),
    then delegates to the original ``server.stop``. Exceptions from
    ``ws_graceful_shutdown`` are logged at DEBUG and the original
    ``stop`` STILL runs — a failure in the WS close path must not
    prevent the TCP teardown. This satisfies the "BEFORE
    ``ipc_server.stop()``" requirement WITHOUT modifying
    ``shutdown_controller.py`` or ``ipc_server.py`` (file ownership
    boundary — this module owns all WS-state).

    Idempotent: a second call is a no-op (detected via the
    ``_ws_graceful_shutdown_installed`` marker). Without this guard, a
    double-install would wrap ``server.stop`` twice, creating a chain
    of wrappers calling each other on every shutdown.
    """
    if getattr(server, "_ws_graceful_shutdown_installed", False):
        return
    server._ws_graceful_shutdown_installed = True

    # Initialize the WS-state attributes ONLY if they are not already
    # set. Tests (and a future caller) may pre-populate these before
    # calling ``_attach_ws_graceful_shutdown``; the install must not
    # overwrite existing state. ``getattr(..., None)`` returns None for
    # an unset attribute on a real IPCServer, and returns a MagicMock
    # child on a MagicMock test double — both are "already set" from
    # the install's perspective, so we preserve them. The
    # ``_make_real_server_for_graceful_shutdown`` test helper explicitly
    # pre-sets these to real ``set()`` instances before calling install.
    # All attributes are declared on ``IPCServer.__init__``, so the
    # assignments below need no type-ignore suppression.
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
        ``websockets`` library connections — their internal state is
        tied to the loop that created them).

        The dispatch-future drain uses
        ``concurrent.futures.Future.result(timeout=...)`` which is a
        blocking call safe to invoke from any thread. Each future gets
        its own timeout — a single stuck handler cannot block the
        whole drain pass.

        The loop stop is scheduled via
        ``loop.call_soon_threadsafe(loop.stop)`` — the only
        documented thread-safe way to hand work to an asyncio loop
        from outside it. ``loop.stop`` causes ``loop.run_forever()``
        (in :func:`run`) to return, which lets ``asyncio.run()``
        finalize the loop and ``run()`` return to its caller.

        If ``server._ws_loop`` is unset or already closed, the close
        and stop are skipped (logged at DEBUG) — the drain still runs
        so any in-flight futures are bounded-waited. This makes
        ``ws_graceful_shutdown`` safe to call even when the WS path
        was never entered (e.g. the server ran in TCP-only mode).
        """
        # Resolve the shutdown budgets from the canonical module at
        # CALL time (module-top import would be circular). This keeps
        # the pre-split patch seam: assignments to
        # ``sidecar_ws._WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS`` /
        # ``sidecar_ws._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS`` are
        # observed here exactly as they were pre-split.
        from voice_typer.server import sidecar_ws as _canonical

        loop = getattr(server, "_ws_loop", None)

        # 1. Send close(1001, "going away") to each authenticated conn
        #    + sleep for the close-handshake budget. The whole close
        #    pass is one coroutine scheduled on the WS loop so the
        #    individual ``ws.close()`` calls run on the correct loop.
        if loop is not None and not loop.is_closed():
            try:
                close_future = asyncio.run_coroutine_threadsafe(
                    _graceful_close_all_conns(server),
                    loop,
                )
                # Bounded-wait: handshake sleep (0.5 s) + per-conn
                # close calls + slack. If the close pass hangs (e.g. a
                # wedged peer's ``ws.close()`` blocks), abandon it and
                # proceed to the drain + loop stop — the host's hard
                # timeout will force-kill the process anyway.
                close_future.result(
                    timeout=(
                        _canonical._WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS
                        + _canonical._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS
                        + 0.5
                    ),
                )
            except Exception:
                log.debug(
                    "[SIDECAR-WS] graceful close pass failed or timed out — continuing to drain + loop stop",
                    exc_info=True,
                )
        else:
            log.debug("[SIDECAR-WS] no WS loop reference (or loop closed) — skipping close pass")

        # 2. Bounded-wait for in-flight dispatch futures. Each future
        #    gets its own timeout so one stuck handler cannot block
        #    the whole drain. The set is snapshotted to avoid
        #    mutation-during-iteration if a dispatch completes and
        #    discards itself from the set while we iterate.
        futures = list(getattr(server, "_ws_dispatch_futures", set()))
        for future in futures:
            try:
                future.result(timeout=_canonical._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS)
            except Exception:
                log.debug(
                    "[SIDECAR-WS] dispatch future did not complete within "
                    "%.1fs drain timeout — proceeding to loop stop",
                    _canonical._WS_DISPATCH_DRAIN_TIMEOUT_SECONDS,
                    exc_info=True,
                )

        # 3. Stop the WS loop. ``call_soon_threadsafe`` is the only
        #    documented thread-safe way to schedule a callback on a
        #    running loop from a non-loop thread. ``loop.stop`` causes
        #    ``loop.run_forever()`` (in :func:`run`) to return.
        #    Flag the request FIRST: ``run()`` checks
        #    ``_ws_graceful_stop_requested`` to translate asyncio's
        #    "Event loop stopped before Future completed" RuntimeError
        #    into a clean INFO exit instead of a spurious ERROR
        #    traceback + exit code 1 (2026-08-30 tray-Restart noise).
        with contextlib.suppress(Exception):
            server._ws_graceful_stop_requested = True
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                log.debug(
                    "[SIDECAR-WS] loop.stop() scheduling failed — loop already closed",
                    exc_info=True,
                )
        else:
            log.debug("[SIDECAR-WS] no WS loop reference (or loop closed) — cannot stop loop")

    server.ws_graceful_shutdown = ws_graceful_shutdown  # type: ignore[attr-defined]

    # Install ``ws_graceful_shutdown`` as an EXPLICIT stop hook (the
    # ``_ws_stop_hook`` slot declared on ``IPCServer.__init__``) instead
    # of REPLACING the bound ``stop`` method at instance level. The
    # ``LifecycleMixin.stop`` implementation calls the hook FIRST (best-
    # effort: exceptions logged at DEBUG, teardown continues) and then
    # runs the original TCP teardown. Same ordering contract as the old
    # instance-level ``stop`` wrapper — ``ws_graceful_shutdown`` runs
    # BEFORE the TCP teardown — without mutating the class surface (a
    # monkeypatched/instance-replaced ``stop`` created a hidden call
    # chain invisible to the type checker).
    # The hook is looked up DYNAMICALLY at call time by ``stop`` so tests
    # that replace ``server.ws_graceful_shutdown`` post-install still
    # observe the replacement; the hook closure itself only references
    # the server object.
    def _stop_hook() -> None:
        try:
            # Dynamic lookup — see comment above.
            hook = server.ws_graceful_shutdown
            if hook is not None:
                hook()
        except Exception:
            log.debug(
                "[SIDECAR-WS] ws_graceful_shutdown raised — continuing to original stop",
                exc_info=True,
            )

    server._ws_stop_hook = _stop_hook
