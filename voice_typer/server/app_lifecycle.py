"""Restart / quit / relaunch-ack lifecycle for VoiceTyperApp.

Keeps thin delegates on VoiceTyperApp for tray/IPC/tests. Logger name is
``voice_typer.server.app`` (caplog). Call sites use live module attrs so
PERF-005: relaunch_ack wait short-circuits when no IPC server attached.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    pass

# see module docstring.
log = logging.getLogger("voice_typer.server.app")

# The ``[QUIT] Quitting ...`` line must appear ONCE per process. A quit
_quit_line_logged = False


class LifecycleController:
    """itself (``app``) so ``LifecycleController`` can:
    - Read/write ``app.config`` (save before push)
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def quit_app(self) -> None:
        """inline and ended with ``os._exit(0)`` because ``_wrap`` in
        ``tray.py`` swallowed ``SystemExit``, preventing the audited
        """
        app = self._app
        global _quit_line_logged
        if not _quit_line_logged:
            _quit_line_logged = True
            log.info("[QUIT] Quitting %s", APP_NAME)

        # Item 12: If recording, discard the recording before quitting
        try:
            if app.recorder and app.recorder.recording:
                log.info("[QUIT] Recording in progress, discarding before quit")
                app.recorder.discard()
        except Exception:
            log.debug("[QUIT] Could not discard recording", exc_info=True)

        # 0. Notify predecessor frontend over TCP so it can quit cleanly.
        from voice_typer.server import event_bus

        event_bus.publish({"type": "quit_app"})
        # Stash the publish so ``ShutdownController.quit()`` (called via
        app._quit_app_published = True

        # re-entry guard sits AFTER the push so the quit event
        if app._shutting_down_event.is_set():
            log.debug("[QUIT] Already shutting down, ignoring duplicate quit_app call")
            return

        # 1. Delegate to the audited cleanup path. self._app.quit()
        app.quit()

    def restart_app(self) -> None:
        """Sends a ``relaunch_app`` event to predecessor over the active
        TCP channel, then exits the current instance via the clean
        """
        app = self._app
        # re-entry guard (mirror the delegate on VoiceTyperApp
        if app._shutting_down_event.is_set():
            log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return
        # pass APP_NAME as the format argument.
        log.info("[RESTART] Restarting %s...", APP_NAME)

        # In standalone/terminal mode a host process was spawned as a child
        _in_place_restart = vars(app).get("_host_pid") is not None
        if _in_place_restart:
            log.info(
                "[RESTART] Standalone mode, in-place restart: tearing down and "
                "re-initializing in the same terminal (PID=%s)",
                app._host_pid,
            )
            # Mark as restarting so the shared cleanup body can
            app._is_restarting = True
            app._in_place_restart = True
            # In standalone in-place mode we do NOT push ``relaunch_app``
            app._shutting_down = True
            app._shutting_down_event.set()
            try:
                app._thread_registry.shutdown_all()
            except Exception:
                log.warning(
                    "[RESTART] thread_registry.shutdown_all failed",
                    exc_info=True,
                )
            app._do_cleanup()
            # Do NOT call ``sys.exit(0)``: the process must stay alive.
            log.info("[RESTART] In-place restart complete, entrypoint loop will re-initialize")
            return

        # Mark this shutdown as a RESTART (not a quit) so the shared
        app._is_restarting = True

        # Save any pending in-memory config changes (e.g. a theme
        try:
            save_ok = app.config.save()
        except Exception:
            log.warning("[RESTART] config.save() raised", exc_info=True)
        else:
            if not save_ok:
                log.warning("[RESTART] config.save() before push failed")

        # _push_event_now() MUST be called BEFORE _shutting_down is set
        from voice_typer.server import event_bus

        try:
            event_bus.publish({"type": "relaunch_app"})
            log.info("[RESTART] relaunch_app pushed to host via event_bus")
        except Exception as e:
            log.warning("[RESTART] failed to push relaunch_app: %s", e)

        # before we close the socket. PERF-005: replaced the fixed
        app._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can
        app._shutting_down_event.set()
        # ``self._restore_volume(fade_ms=0)`` call that lived here was
        self._wait_for_relaunch_ack(timeout=0.5)

        # 3. : run the SAME audited cleanup as quit(), flushes
        try:
            app._thread_registry.shutdown_all()
        except Exception:
            log.warning(
                "[RESTART] thread_registry.shutdown_all failed",
                exc_info=True,
            )
        # Call the delegate (not ShutdownController._do_cleanup directly)
        app._do_cleanup()

        # 4. Exit cleanly, the host will relaunch us.
        is_main_thread = threading.current_thread() is threading.main_thread()
        if not is_main_thread:
            try:
                app.shutdown._arm_shutdown_watchdog(app._shutdown_watchdog_timeout_s)
            except Exception:
                # This is the LAST line of defense against a hung
                log.error(
                    "[RESTART] failed to arm shutdown watchdog, restart may hang",
                    exc_info=True,
                )
        log.info("[RESTART] Old process exiting via sys.exit(0)")
        if is_main_thread:
            sys.exit(0)
        # else: rely on tray.stop() (called inside _do_cleanup) to

    def _wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Delegates to the IPCServer's public
        ``wait_for_relaunch_ack(timeout)`` wrapper so this module no
        """
        app = self._app
        ipc_server = app._ipc_server
        # Short-circuit when no IPC server is attached. No IPC server
        if ipc_server is None:
            log.debug("[RESTART] No IPC server attached; skipping relaunch_ack wait")
            return False
        log.info(
            "[RESTART] Waiting for relaunch_ack from host (timeout %.3fs)",
            timeout,
        )
        acked = False
        # Delegate to the IPC server's ack-wait primitive, but tolerate
        if not hasattr(ipc_server, "wait_for_relaunch_ack"):
            ack_event = getattr(ipc_server, "_relaunch_ack_event", None)
            if ack_event is None:
                log.debug("[RESTART] IPC server has no relaunch_ack event; skipping wait")
                return False
            ack_event.clear()
            acked = ack_event.wait(timeout=timeout)
        else:
            acked = ipc_server.wait_for_relaunch_ack(timeout=timeout)
        if not acked:
            log.debug(
                "[RESTART] relaunch_ack timed out after %.3fs, host may be dead or slow; proceeding with cleanup",
                timeout,
            )
        return acked
