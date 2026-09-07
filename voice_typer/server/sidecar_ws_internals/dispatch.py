"""WS dispatch factory: rate limiting, shutdown gates, in-flight drain.

Extracted verbatim from :mod:`voice_typer.server.sidecar_ws`; the
canonical module re-exports :func:`_make_dispatch` so the direct-call
test surface (``sidecar_ws._make_dispatch(server)`` — the mig15-17
ws_hmac suites, tests/test_ipc_server.py, the rate-limiter chokepoint
tests) keeps working, and ``inspect.getsource`` pins on the function
object follow it here automatically.

Patch-path contract (C-ARCH-2 canonical form): this module OWNS
``_make_dispatch``. The canonical module's ``run()`` resolves it via a
sibling MODULE-OBJECT read at call time
(``_dispatch_mod._make_dispatch(server)``), so a
``monkeypatch.setattr`` on THIS module is observed by production —
tests that stub the factory patch
``voice_typer.server.sidecar_ws_internals.dispatch._make_dispatch``
(tests/test_sidecar_ws_origin_check.py). Source-grep pins on the
factory body (tests/test_shutdown_ws_db_race.py drain/inflight
contracts, tests/tauri/mig19/test_wire_swap_recovery.py rate-limiter
contracts) read THIS file.

Everything the factory coordinates stays inside it verbatim: the
ADR-0019 per-frame rate-limit gate (shared ``_RateLimiter`` via
``ipc_server._get_rate_limiter``), the cooperative-shutdown
``_shutting_down`` gates (early + TOCTOU re-check + pre-executor
re-check), the dedicated ``_ws_dispatch_pool`` executor handoff, and
the ``_ws_drained_event`` / ``_ws_inflight_count`` in-flight
coordination that ``ShutdownController._do_cleanup`` waits on.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# Same logger object as the canonical module (``logging.getLogger`` is
# idempotent per name). Keeps every log record's ``name`` attribute
# byte-identical to the pre-split output — several tests pin
# ``caplog.at_level(..., logger="voice_typer.server.sidecar_ws")``.
log = logging.getLogger("voice_typer.server.sidecar_ws")


def _make_dispatch(server: IPCServer):
    """Build a coroutine that dispatches a single WS frame.

    Reuses ``server._dispatch`` (the same path the TCP loop uses),
    so the 61-command registry + _validate_dict_payload + every
    handler mixin is exercised unchanged (ADR-0020 §2).
    """
    # ADR-0019 + : per-process rate limiter. Reuse the same private
    # _RateLimiter class the TCP path uses (ipc_server.py:215) so the
    # burst/sustained semantics are identical — 200 burst, 600 sustained
    # over a 10s window (RELIABILITY-006-).
    #
    # the limiter is looked up lazily via _get_rate_limiter(server)
    # so it is shared across ALL WS connections to this server process.
    # A local attacker can no longer reset the 200-message burst budget
    # by dropping the WS and reconnecting — the 10s sliding window
    # continues to evict old timestamps across reconnects.
    # dedicated ThreadPoolExecutor for WS dispatch so
    # ``_do_cleanup`` can drain / cancel in-flight dispatch requests
    # BEFORE tearing down the recorder / history DB / crash-recovery
    # writer. Previously ``loop.run_in_executor(None, server._dispatch,
    # msg)`` used the asyncio loop's default executor, which has no
    # handle the shutdown path can reach — a long-running handler
    # (e.g. ``download_model``) would race teardown, half-flush the
    # history DB, and leak a partially-written crash-recovery snapshot.
    #
    # DEDUP (): the rate-limiter import is intentionally from
    # ``ipc_server`` (NOT from the leaf ``voice_typer.server.ipc.rate_limiter``).
    # ``_get_rate_limiter`` is defined LOCALLY in ``ipc_server.py`` (not
    # just re-exported) so it resolves ``_RateLimiter`` against
    # ``ipc_server``'s module globals at call time.  Tests that
    # monkey-patch ``ipc_server._RateLimiter`` observe the patched class
    # through this import (see
    # ``tests/test_ipc_rate_limiter_concurrent_init.py``).  Changing the
    # import to the leaf
    # module would BREAK the test monkey-patch contract.  The TCP path
    # (``ipc/transport_tcp.py``) also imports from ``ipc_server`` for
    # the same reason.
    #
    # Stored on the server instance (not the closure) so
    # ``ShutdownController._do_cleanup`` can reach it via
    # ``app._ipc_server._ws_dispatch_pool``. The pool / drained-event /
    # inflight-lock/count are PRE-CONSTRUCTED in ``IPCServer.__init__``
    # (the creation logic is pure constructor work with no WS-loop
    # dependency, so the lazy-init branch per dispatch was dead
    # weight). The MagicMock-compat ``getattr`` reads are kept so
    # test doubles that bypass ``__init__`` still work.
    from voice_typer.server.ipc_server import _get_rate_limiter

    # Resolve the rate limiter ONCE in the closure body so
    # ``dispatch()`` doesn't call ``_get_rate_limiter(server)`` per
    # frame. Per-frame resolution costs a module-globals traversal
    # + a dict-style getattr on every WS frame; resolved-once
    # captures the limiter in a local closure cell. The limiter
    # is still shared across all WS connections to this server
    # (it's the same ``_RateLimiter`` instance stored on the
    # server's ``_rate_limiter_instance`` slot, just resolved at
    # handler-creation time rather than per frame).
    rate_limiter = _get_rate_limiter(server)

    ws_dispatch_pool = getattr(server, "_ws_dispatch_pool", None)
    if ws_dispatch_pool is None:
        # Only reachable on test doubles that bypass ``__init__``.
        from concurrent.futures import ThreadPoolExecutor

        ws_dispatch_pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-dispatch",
        )
        server._ws_dispatch_pool = ws_dispatch_pool

    # explicit ``threading.Event`` coordination between the WS
    # dispatch path and ``ShutdownController._do_cleanup``. The pool's
    # ``shutdown(wait=True)`` only guarantees that the
    # ``ThreadPoolExecutor``'s worker queue has drained — it does NOT
    # guarantee that the per-dispatch coroutine body has finished its DB
    # write (the Future resolves on ``server._dispatch`` return, but the
    # WS ``dispatch`` coroutine may still be in its ``await
    # loop.run_in_executor`` unwind / result-serialisation tail when the
    # pool reports drained). That tail can race
    # ``_teardown_history_db`` / ``_teardown_crash_recovery`` in
    # ``_do_cleanup``, silently losing the user's final
    # transcription_final DB write.
    #
    # ``_ws_drained_event`` is SET when no dispatch is in-flight (the
    # initial state — no dispatch has started yet, so the drain is
    # trivially complete). ``_ws_inflight_count`` is the number of
    # dispatches currently between the entry point and the exit of the
    # ``dispatch`` coroutine body. ``_ws_inflight_lock`` guards the
    # count + Event mutation pair so two concurrent dispatches cannot
    # race the count into a wrong value or miss the Event-set on the
    # last exit.
    #
    # Pre-constructed in ``IPCServer.__init__`` — plain reads here (the
    # ``getattr`` fallbacks only fire on test doubles that bypass
    # ``__init__``).
    ws_drained_event = getattr(server, "_ws_drained_event", None)
    ws_inflight_lock = getattr(server, "_ws_inflight_lock", None)
    if ws_drained_event is None or ws_inflight_lock is None:
        import threading as _threading

        if ws_drained_event is None:
            ws_drained_event = _threading.Event()
            ws_drained_event.set()  # initially drained — count is 0
            server._ws_drained_event = ws_drained_event
        if ws_inflight_lock is None:
            ws_inflight_lock = _threading.Lock()
            server._ws_inflight_lock = ws_inflight_lock
        if getattr(server, "_ws_inflight_count", None) is None:
            server._ws_inflight_count = 0

    async def dispatch(msg: dict, websocket) -> dict | None:
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical) — see
                    # ``voice_typer/server/ipc/validation.py`` for the
                    # migration contract.
                    "code": "client.invalid_payload",
                    "message": "missing 'type'",
                },
            }

        # cooperative shutdown gate. Once ``app._shutting_down``
        # is True (set by ``ShutdownController.quit()`` before
        # ``_do_cleanup()`` runs), reject every new dispatch request
        # with a structured ``server.shutting_down`` error code so the
        # host can re-queue / surface a graceful "backend is exiting"
        # message instead of starting a long-running handler (e.g.
        # ``download_model``) that would race teardown. The
        # ``shutdown`` message itself is exempt — the host sends it to
        # TRIGGER shutdown, and it is now handled by the shared
        # ``_COMMAND_REGISTRY`` entry ``"shutdown": "_handle_shutdown"``
        # (registered in ipc_server.py by ) which delegates to
        # ``service.quit()`` — the SAME path the TCP ``quit_app``
        # command uses. Pre- the WS path special-cased
        # ``shutdown`` here and called ``server.app.quit()`` directly,
        # bypassing the service layer (so any future shutdown
        # side-effect added to ``service.quit()`` silently wouldn't run
        # on Tauri). The special-case is now removed; ``shutdown``
        # flows through ``server._dispatch`` like every other command.
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
            log.debug("[SIDECAR-WS] rejecting %s — server shutting down", msg_type)
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # ADR-0019 +  rate limit check. Look up the shared limiter
        # on every call (cheap — dict-style getattr) so all WS frames to
        # this server share the same sliding-window budget. _RateLimiter
        # .allow() returns a bool (no retry-after); the host backs off
        # via backoff on repeated rate-limit hits.
        #
        # pass ``command=msg_type`` so the per-command cost map
        # (``COMMAND_COSTS``) is applied — e.g. ``download_model``
        # consumes 50 of the 200 burst units, so a buggy client can fire
        # at most 4 expensive commands per second before the 5th is
        # rejected. Cheap commands (``heartbeat``, ``get_status``) keep
        # the pre- cost-1 behavior. The legacy
        # ``rate_limiter.allow()`` form (no ``command`` kwarg) is still
        # supported and treats the call as cost 1.
        #
        # ``shutdown`` is a CONTROL frame, not a dispatch frame — it
        # must bypass the rate limiter so a sidecar being spammed with
        # frames (over the 200-burst budget) can still shut down
        # cleanly (ADR-0020 §10). The TCP path's read loop applies the
        # same exemption (``shutdown`` skips its rate-limit gate); the
        # WS path must stay in parity.
        if msg_type != "shutdown" and not rate_limiter.allow(command=msg_type):
            # allow() already increments _rejected atomically when
            # it returns False — the separate .reject() call was removed
            # to eliminate the benign race where two threads could both
            # observe the same deque state, both decide to reject, and
            # double-count the rejection. This keeps WS-path rejected_count
            # consistent with the TCP path (both count via allow()).
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical).
                    "code": "client.rate_limited",
                    "message": "rate limit exceeded; backing off",
                },
            }

        # TOCTOU re-check: the early ``_shutting_down`` gate
        # above was read BEFORE the rate-limiter call. The flag can
        # flip in the gap between that read and the actual
        # ``pool.submit`` — e.g. ``ShutdownController.quit()`` runs
        # concurrently between the early gate and here, OR the
        # rate-limiter itself blocks long enough for the shutdown
        # sequence to start. Re-check immediately before the in-flight
        # count increment (so a TOCTOU-rejected dispatch does NOT
        # touch the count — net-zero) and short-circuit with the SAME
        # ``server.shutting_down`` error envelope as the early gate.
        # This shrinks (does NOT eliminate) the TOCTOU window: the
        # flag can still flip DURING the handler's execution, but that
        # residual race is owned by the handler's own
        # shutdown-awareness (e.g. ``download_model`` checks
        # ``_shutting_down`` between chunks). Placing the re-check
        # BEFORE the in-flight count increment (rather than
        # immediately before ``loop.run_in_executor``) avoids
        # incrementing then decrementing the count for a rejected
        # dispatch — the count is only touched for dispatches that
        # actually reach the executor.
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
            log.debug(
                "[SIDECAR-WS] TOCTOU re-check rejecting %s — server shutting down",
                msg_type,
            )
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # mark this dispatch as in-flight + clear the drain Event
        # so ``ShutdownController._do_cleanup`` knows to wait for us
        # before tearing down the DB / recorder / crash-recovery
        # subsystems. The increment-then-clear pair is under
        # ``_ws_inflight_lock`` so two concurrent dispatches cannot
        # interleave as ``inc → inc → clear → clear`` (both would clear
        # the Event, then the first exit would set it prematurely while
        # the second dispatch is still running — a TOCTOU on the count).
        # The lock is held for the minimum work needed (increment +
        # Event.clear); the dispatch body itself runs without the lock.
        with ws_inflight_lock:
            server._ws_inflight_count = server._ws_inflight_count + 1
            ws_drained_event.clear()

        # Dispatch on the worker thread pool so a slow handler
        # (e.g. download_model) doesn't block the WS reader.
        loop = asyncio.get_running_loop()
        # Pre-bind ``result`` to None so the ``return result`` line
        # below has a defined value to return even when
        # ``loop.run_in_executor`` raises (in which case
        # ``return_error`` is set to a non-None dict and we return
        # early at ``if return_error is not None:`` — but pyrefly
        # cannot track that early-return control flow).
        result: dict | None = None
        try:
            # Pre-executor TOCTOU re-check: the early ``_shutting_down``
            # gate above and the in-flight-count re-check both run BEFORE
            # the count increment. The flag can flip in the window
            # between that re-check and this point — e.g. during the
            # count increment + ``ws_drained_event.clear()`` under
            # ``ws_inflight_lock``, the ``asyncio.get_running_loop()``
            # call, or the ``try`` entry. Re-checking immediately before
            # ``loop.run_in_executor`` shrinks the TOCTOU window to just
            # the ``run_in_executor`` await itself (the residual race
            # during the handler's execution is owned by the handler's
            # own shutdown-awareness — e.g. ``download_model`` checks
            # ``_shutting_down`` between chunks). On rejection, the
            # ``finally`` block below decrements the in-flight count
            # (net-zero — the count was incremented above) and re-sets
            # the drain Event when the count drops to zero, so
            # ``_do_cleanup`` is not blocked on a dispatch that never
            # reached the executor.
            if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
                log.debug(
                    "[SIDECAR-WS] pre-executor TOCTOU re-check rejecting %s — server shutting down",
                    msg_type,
                )
                return {
                    "type": "error",
                    "data": {
                        "code": "server.shutting_down",
                        "message": "server is shutting down; please retry later",
                    },
                }
            # use the dedicated ``_ws_dispatch_pool`` (not the
            # asyncio default executor) so ``ShutdownController._do_cleanup``
            # can ``pool.shutdown(wait=False, cancel_futures=True)`` to
            # drain / cancel in-flight handlers before recorder / history
            # DB / crash-recovery teardown.
            result = await loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg)
        except Exception:
            log.exception("[SIDECAR-WS] _dispatch raised")
            #  (2026-07-18): the error envelope now matches the
            # TCP path (``ipc_server._handle_tcp_connection``'s
            #  block) verbatim — same ``code`` AND same
            # ``message`` ("internal error"). Pre- the WS path
            # used the message "dispatch raised" while TCP used
            # "internal error"; both messages were generic (neither
            # leaked ``str(exception)``) but the divergence meant a
            # client could not use the message text to confirm parity.
            # The contract: ``{"type":"error","data":{"code":
            # "server.internal_error","message":"internal error"}}``.
            #
            #  (): the ``code`` was migrated from the
            # legacy ``"internal_error"`` to the namespaced
            # ``"server.internal_error"`` form (matching the
            # ``ERROR_CODES`` registry in ``ipc/validation.py``). The
            # renderer accepts both forms (legacy treated as alias),
            # so this is a backward-compatible migration.
            # applies the same migration to the TCP path's
            # ``internal_error`` emissions.
            return_error = {
                "type": "error",
                "data": {"code": "server.internal_error", "message": "internal error"},
            }
        else:
            return_error = None
        finally:
            # decrement the in-flight count and re-set the drain
            # Event when the count drops to zero. The ``finally`` block
            # guarantees the Event is set even if ``run_in_executor``
            # raised (the in-flight count MUST be consistent with the
            # actual dispatch state, otherwise ``_do_cleanup`` would
            # wait on an Event that never fires — a deadlock).
            with ws_inflight_lock:
                server._ws_inflight_count = server._ws_inflight_count - 1
                if server._ws_inflight_count <= 0:
                    server._ws_inflight_count = 0
                    ws_drained_event.set()

        if return_error is not None:
            return return_error

        # _dispatch returns None for fire-and-forget commands (e.g.
        # restart_app, which sends its own response). Don't send a
        # frame in that case.
        return result

    return dispatch
