"""WS dispatch + encode pool drain (extracted from ``shutdown_controller``)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from voice_typer.server._timeout_utils import _run_parallel_with_timeout
from voice_typer.server.sidecar_ws_internals.encode_pool import shutdown_encode_pool

log = logging.getLogger("voice_typer.server.shutdown_controller")


def drain_ws_dispatch_pool(controller, app) -> None:
    """Early bookend: stop the IPC server + drain the WS dispatch + encode pools."""
    try:
        ipc_server = getattr(app, "_ipc_server", None)
        ws_pool = getattr(ipc_server, "_ws_dispatch_pool", None) if ipc_server is not None else None

        early_items: list[tuple[str, Callable[[], object], float]] = []
        if ipc_server is not None:
            # PERF-SHUTDOWN-002: the ipc_server.stop budget was 5.0s
            early_items.append(("ipc_server.stop", ipc_server.stop, 2.0))

        if ws_pool is not None and hasattr(ws_pool, "shutdown"):

            def _drain_ws_pool() -> None:
                # ``shutdown(wait=False, cancel_futures=True)`` only
                ws_pool.shutdown(wait=False, cancel_futures=True)
                log.debug("[SHUTDOWN] WS dispatch pool shut down (cancel_futures=True)")
                join_thread = threading.Thread(
                    target=ws_pool.shutdown,
                    kwargs={"wait": True},
                    daemon=True,
                )
                join_thread.start()
                # 4.5s, deliberately UNDER this item's 5.0s parallel
                join_thread.join(timeout=4.5)
                if join_thread.is_alive():
                    # Name BOTH bounds honestly (mirrors the encode-pool
                    log.warning(
                        "[SHUTDOWN] ws_dispatch_pool did not drain within "
                        "its 4.5s join (5.0s budget), proceeding anyway"
                    )

            early_items.append(("ws_dispatch_pool.drain", _drain_ws_pool, 5.0))

        encode_pool = getattr(ipc_server, "_ws_encode_pool", None)
        if encode_pool is not None and hasattr(encode_pool, "shutdown"):

            def _drain_encode_pool() -> None:
                # The WS frame-encode pool must be drained for the same
                shutdown_encode_pool(ipc_server)
                log.debug("[SHUTDOWN] WS encode pool shut down (cancel_futures=True)")
                join_thread = threading.Thread(
                    target=encode_pool.shutdown,
                    kwargs={"wait": True},
                    daemon=True,
                )
                join_thread.start()
                # 1.8s inner join, deliberately UNDER this item's 2.0s
                join_thread.join(timeout=1.8)
                if join_thread.is_alive():
                    # Name BOTH bounds honestly: the inner join is 1.8s
                    log.warning(
                        "[SHUTDOWN] ws_encode_pool did not drain within its 1.8s join (2.0s budget), proceeding anyway"
                    )

            # Early + bounded (~2s): encodes are pure CPU
            early_items.append(("ws_encode_pool.drain", _drain_encode_pool, 2.0))

        if early_items:
            _run_parallel_with_timeout(early_items)

        # explicit ``threading.Event`` coordination between the WS
        if ipc_server is not None:
            ws_drained_event = getattr(ipc_server, "_ws_drained_event", None)
            if ws_drained_event is not None:
                # Skip the 2s wait when the WS pool is already idle
                ws_inflight = getattr(ipc_server, "_ws_inflight_count", 0)
                if ws_inflight == 0:
                    log.debug(
                        "[SHUTDOWN] ws_drained_event.wait skipped "
                        "(_ws_inflight_count=0, no in-flight WS handler "
                        "can race DB teardown)"
                    )
                else:
                    drained = ws_drained_event.wait(timeout=2.0)
                    if not drained:
                        in_flight = getattr(ipc_server, "_ws_inflight_count", 0)
                        # drain-timeout branch, log at WARNING and
                        log.warning(
                            "[SHUTDOWN] WS dispatch drain Event did not "
                            "fire in 2s, %s in-flight handlers may race DB "
                            "teardown; proceeding with cleanup (the in-flight "
                            "write may silently fail)",
                            in_flight,
                        )
    except Exception:
        log.debug(
            "[SHUTDOWN] early bookend (ipc_server.stop + WS drain) failed",
            exc_info=True,
        )


__all__ = ["drain_ws_dispatch_pool"]
