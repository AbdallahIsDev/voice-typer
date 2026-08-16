"""(Phase 4.5 spaghetti split): LifecycleController — extracted
from VoiceTyperApp.

Owns the restart / quit / relaunch-ack lifecycle of ``VoiceTyperApp``:

    - ``restart_app`` — push ``relaunch_app`` event, save config, set
      ``_shutting_down``, wait for ack, run ``_do_cleanup``, exit via
      ``sys.exit(0)`` (only on main thread; non-main thread relies on
``tray.stop()`` inside ``_do_cleanup`` +  watchdog).
    - ``_wait_for_relaunch_ack`` — bounded wait on the IPC server's
      ``relaunch_ack`` event (PERF-005: 0ms short-circuit when no IPC
      server attached).
    - ``quit_app`` — push ``quit_app`` event over TCP so Electron
      quits cleanly, then guard-and-delegate to ``self._app.quit()``
      (the audited ``SystemExit`` path).

Previously all of this lived on ``VoiceTyperApp`` as ~360 LOC across 3
methods (``restart_app`` 208 LOC, ``_wait_for_relaunch_ack`` 84 LOC,
``quit_app`` 68 LOC). The behaviour is preserved verbatim — only the
class boundary moved. ``VoiceTyperApp`` keeps thin delegate methods so
callers (tray menu callbacks, IPC handlers, tests calling
``app.restart_app()`` / ``app.quit_app()`` directly) keep working
unchanged.

A note on logging: this module uses
``logging.getLogger("voice_typer.server.app")`` rather than the
conventional ``__name__``. Tests like
``tests/app/test_app_de_2i_fixes.py::TestDE47ConfigSaveRaisesInRestartApp``
and ``tests/app/test_quit_restart.py::TestAppRestartLogMessage``
capture logs at ``logger="voice_typer.server.app"`` — using ``__name__``
would route logs to the ``voice_typer.server.app_lifecycle`` logger
and break those caplog captures.

A note on monkeypatching (mirrors the convention in
``shutdown_controller.py`` and ``settings_controller.py``): tests like
the ``app`` fixture in ``tests/app/conftest.py`` and the regression
tests in ``tests/test_app_cleanup.py`` patch
``voice_typer.server.app.sys.exit`` /
``voice_typer.server.app.time.sleep`` /
``voice_typer.server.app.os._exit`` /
``voice_typer.server.event_bus.publish`` at call time. Because
``voice_typer.server.app.sys`` IS the global ``sys`` module (and
likewise for ``time`` / ``os`` / ``event_bus``), those patches
propagate to this module's calls too. The controllers do NOT capture
those names at import time — every call site uses the live module
attribute, so the patches take effect.

A note on the delegate indirection for ``_do_cleanup`` and ``quit``:
``restart_app`` calls ``self._app._do_cleanup()`` (the delegate on
``VoiceTyperApp``) rather than reaching into the body on
``ShutdownController`` directly. Same for ``quit_app`` calling
``self._app.quit()``. This is so test spies that
``monkeypatch.setattr(app, "_do_cleanup", spy)`` /
``monkeypatch.setattr(app, "quit", spy)`` still intercept the call —
see ``tests/test_app_cleanup.py::test_quit_calls_do_cleanup`` and
``tests/app/test_quit_restart.py::TestQuitAppCleanShutdown``.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (``app`` imports
    # ``app_lifecycle`` via the ``LifecycleController(self)`` call
    # inside ``VoiceTyperApp.__init__``). At runtime, ``app`` is
    # whatever object was passed to ``__init__`` (always a
    # ``VoiceTyperApp`` in production, but tests pass mocks that
    # satisfy the same duck-typed surface).
    pass

# Tests capture restart_app / quit_app logs at this logger name —
# see module docstring.
log = logging.getLogger("voice_typer.server.app")

# The ``[QUIT] Quitting ...`` line must appear ONCE per process. A quit
# can be triggered twice back-to-back (tray Quit + Electron's quit IPC,
# or SIGTERM racing the tray quit) and the re-entry guard in
# ``quit_app`` sits AFTER this log line (deliberately — the event push
# must run first, F-06), so without this flag both calls logged the
# identical line within the same second.
_quit_line_logged = False


class LifecycleController:
    """owns restart / quit / relaunch-ack lifecycle.

    Phase 4.5: extracted from ``VoiceTyperApp``. The app passes
        itself (``app``) so ``LifecycleController`` can:

        - Read/write ``app.config`` (save before push)
        - Call ``app._shutting_down_event.set()`` / read ``app._shutting_down``
    (: the threading.Event version provides cross-thread memory
          ordering; the plain boolean is kept in sync for legacy readers).
        - Call ``app._do_cleanup()`` (the delegate on ``VoiceTyperApp``)
          so test spies that ``monkeypatch.setattr(app, "_do_cleanup", spy)``
          still intercept the call — mirrors the ``ShutdownController``
          convention.
        - Call ``app.quit()`` (the delegate) so test spies that
          ``monkeypatch.setattr(app, "quit", spy)`` still intercept.
        - Access ``app._ipc_server`` for the relaunch-ack wait.
    Call ``app.shutdown._arm_shutdown_watchdog(...)`` (:
          non-main-thread restart exit safety net).
        - Read ``app._shutdown_watchdog_timeout_s`` (stashed on the
          instance by ``VoiceTyperApp.__init__`` when ``ShutdownController``
          is wired).
        - Access ``app.recorder`` / ``app.recorder.recording`` /
          ``app.recorder.discard()`` (in-progress recording discard before
          quit).
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    # ─── Quit ──────────────────────────────────────────────────────────

    def quit_app(self) -> None:
        """TrayController protocol: quit the app.

                RELIABILITY-001: previously this method duplicated cleanup
                inline and ended with ``os._exit(0)`` because ``_wrap`` in
                ``tray.py`` swallowed ``SystemExit``, preventing the audited
                ``self.quit()`` path from terminating the process. ``os._exit``
                skips Python atexit handlers, ``__del__`` methods, and
                ``finally`` blocks — leaking the Win32 named mutex, leaving
                PortAudio mic handles open, and not unregistering
                ``RegisterHotKey`` registrations.

        Now that ``_wrap`` suppresses ``SystemExit`` (see
                fix in ``tray.py`` — ``tray.stop()`` inside ``quit()`` already
                breaks the pystray loop, so re-raising just caused pystray to
                print a noisy traceback), we delegate to ``self._app.quit()``
                which does the full cleanup (cancel timers, signal streaming
                cancel, discard recorder, join transcription thread, stop all
                three hotkey backends, ``self.tray.stop()`` to break the
                pystray loop, close devnull FDs, ``sys.exit(0)``).

                Before cleanup, pushes a ``quit_app`` event over the TCP channel
                so the Electron frontend knows to call ``app.quit()`` and shut
                down cleanly (instead of being left orphaned with no backend).

        (F-06): the ``event_bus.publish({"type": "quit_app"})``
                call MUST come BEFORE the ``if self._shutting_down:`` re-entry
                guard. Pre-fix, the guard sat at the top of the method and a
                double-quit (e.g. user clicks the tray Quit item twice, or
                SIGTERM races with the tray quit) silently dropped the second
                push — leaving Electron with no shutdown signal if the first
                push was lost in a TCP race. The fix pushes unconditionally on
                every call and only guards the actual ``self._app.quit()`` call
                so cleanup isn't run twice.

        body lives here now; ``VoiceTyperApp.quit_app`` is a
                one-line delegate.
        """
        app = self._app
        global _quit_line_logged
        if not _quit_line_logged:
            _quit_line_logged = True
            log.info("[QUIT] Quitting %s", APP_NAME)

        # Item 12: If recording, discard the recording before quitting
        # so we don't leave the mic open or lose the in-flight audio.
        try:
            if app.recorder and app.recorder.recording:
                log.info("[QUIT] Recording in progress — discarding before quit")
                app.recorder.discard()
        except Exception:
            log.debug("[QUIT] Could not discard recording", exc_info=True)

        # 0. Notify Electron frontend over TCP so it can quit cleanly.
        # this MUST run BEFORE the _shutting_down guard so a
        # double-quit still pushes the event (the first push may have
        # been lost in a TCP race; the second push is the safety net).
        from voice_typer.server import event_bus

        event_bus.publish({"type": "quit_app"})
        # Stash the publish so ``ShutdownController.quit()`` (called via
        # ``app.quit()`` below) does NOT re-publish the event on this
        # path — quit() publishes ``quit_app`` itself for the Ctrl+C /
        # signal paths that bypass ``quit_app()``, and the flag lets it
        # skip here instead of sending a redundant second write.
        app._quit_app_published = True

        # re-entry guard sits AFTER the push so the quit event
        # is always published, even on a double-quit. Only the actual
        # ``self._app.quit()`` cleanup is skipped on the second call.
        # use _shutting_down_event.is_set() for cross-thread memory
        # ordering (the threading.Event version provides acquire/release
        # semantics — the plain boolean has no such guarantee).
        if app._shutting_down_event.is_set():
            log.debug("[QUIT] Already shutting down, ignoring duplicate quit_app call")
            return

        # 1. Delegate to the audited cleanup path.  self._app.quit()
        #    (the delegate on VoiceTyperApp) raises SystemExit(0) at
        #    the end; _wrap re-raises it, and pystray unwinds because
        #    self._app.tray.stop() was called inside quit(). Calling
        #    the delegate (not ShutdownController.quit directly) keeps
        #    test spies that monkeypatch app.quit intercepting the call.
        app.quit()

    # ─── Restart ──────────────────────────────────────────────────────

    def restart_app(self) -> None:
        """TrayController protocol: restart the app.

                Sends a ``relaunch_app`` event to Electron over the active
                TCP channel, then exits the current instance via the clean
                ``sys.exit(0)`` path. Electron's handler calls
                ``app.relaunch()`` + ``app.exit(0)``, which spawns a fresh
                Electron process (which in turn spawns a fresh Python backend).
                If the ``relaunch_app`` event is lost (TCP race), Electron's
                ``pythonProcess.on("exit")`` handler sees exit code 0 and
                triggers the same relaunch as a fallback — see
                ``client/src/main/index.ts``.

        (IMPROVE-mode run, 2026-07-21): re-entry guard at the
                top — mirror ``quit_app``. Pre-fix, a double-clicked tray
                "Restart" item or a tray restart racing with SIGTERM-triggered
                quit would push duplicate ``relaunch_app`` events, re-acquire
                ``_config_mutation_lock`` for a second ``config.save()``,
                re-enter ``_do_cleanup()``, and fire a second ``sys.exit(0)``
                while the first call's finally blocks were still draining.

        (same run): pass ``APP_NAME`` as the format argument to
                ``log.info("[RESTART] Restarting %s...", APP_NAME)``. Pre-fix,
                the ``%s`` placeholder was never substituted, producing a
                literal ``[RESTART] Restarting %s...`` log line.

        body lives here now; ``VoiceTyperApp.restart_app`` keeps
                the re-entry guard inline (to satisfy the source-level
                invariant pinned by
                ``tests/test_app_cleanup.py::test_restart_app_guard_is_first_statement_in_method``)
                and delegates the rest to this method. This guard is a mirror
                of the delegate's guard — idempotent, so the double-check is
                harmless and makes the controller safe for direct calls from
                future code.
        """
        app = self._app
        # re-entry guard (mirror the delegate on VoiceTyperApp
        # — idempotent if the delegate has already short-circuited).
        # use _shutting_down_event.is_set() for cross-thread
        # memory ordering (the threading.Event version provides
        # acquire/release semantics — the plain boolean has no such
        # guarantee).
        if app._shutting_down_event.is_set():
            log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return
        # pass APP_NAME as the format argument.
        log.info("[RESTART] Restarting %s...", APP_NAME)

        # ── THEME-RESTART-FIX: save the config before push ───────────
        # Save any pending in-memory config changes (e.g. a theme
        # preset change that was set via `set_config` but whose save
        # completed while the user navigated to the tray menu) to disk
        # before the restart sequence begins. This ensures the new
        # Python process loads the latest config, preventing the theme
        # from reverting to default after a restart.
        # wrap in try/except so an unexpected exception from
        # save() (e.g. RecursionError from asdict on a cyclic
        # dataclass) does not abort the restart sequence — the user's
        # "Restart" tray click must still work.
        try:
            save_ok = app.config.save()
        except Exception:
            log.warning("[RESTART] config.save() raised", exc_info=True)
        else:
            if not save_ok:
                log.warning("[RESTART] config.save() before push failed")

        # ── CRITICAL ORDERING FIX ────────────────────────────────────
        #
        # _push_event_now() MUST be called BEFORE _shutting_down is set
        # to True. The _send() method in ipc_server.py checks
        # _shutting_down and if True, closes the TCP socket WITHOUT
        # writing the event — silently dropping it. This was the root
        # cause of the "restart does nothing" bug: the relaunch_app
        # event was never received by Electron, so _relaunching stayed
        # false, and the fallback exit handler also failed because the
        # Python process never actually exited (SystemExit was caught
        # by wrap_callback without tray.stop() breaking the loop).
        #
        # 1. Push relaunch_app BEFORE marking _shutting_down.
        # cleanup (this change): the published event name is now
        # ``relaunch_app`` directly (no longer ``relaunch_electron``).
        # The Rust WS bridge no longer renames it (the rename arm in
        # ws.rs was dropped); main.rs listens for ``relaunch_app`` and
        # calls ``app.restart()``.
        from voice_typer.server import event_bus

        try:
            event_bus.publish({"type": "relaunch_app"})
            log.info("[RESTART] relaunch_app pushed to host via event_bus")
        except Exception as e:
            log.warning("[RESTART] failed to push relaunch_app: %s", e)

        # 2. NOW mark as shutting down, restore volume, and wait
        #    (event-driven) for Electron to process the relaunch event
        #    before we close the socket. PERF-005: replaced the fixed
        #    time.sleep(0.3) with a bounded wait on the
        #    ``relaunch_ack`` event that Electron sets when it receives
        #    ``relaunch_electron``. This unblocks the (tray) calling
        #    thread as soon as Electron acks, instead of always blocking
        #    300ms; if no ack arrives (e.g. Electron already gone), we
        #    fall back to the original 300ms pause so behaviour is
        #    unchanged.
        app._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can
        # check it (matches quit()'s shutdown signaling — important now
        # that restart_app() shares the same _do_cleanup() body).
        app._shutting_down_event.set()
        # (IMPROVE-mode run, 2026-07-21): the redundant
        # ``self._restore_volume(fade_ms=0)`` call that lived here was
        # deleted — ``_do_cleanup()`` (invoked further down via
        # ``ShutdownController._do_cleanup``) already invokes the
        # volume-restore path. Pre-fix, the double-restore wasted ~10ms
        # and produced confusing log noise (two "volume restored" lines
        # per restart). If the volume backend isn't reentrant, the
        # second call could see a stale "ducked" state and re-apply the
        # duck. : encapsulated the IPCServer's private
        # ``_relaunch_ack_event`` access in ``_wait_for_relaunch_ack``
        # so the backwards coupling (VoiceTyperApp reaching INTO the
        # IPCServer's private state) is at least named and easy to
        # migrate. The helper now delegates to the public
        # ``IPCServer.wait_for_relaunch_ack`` wrapper so app.py no
        # longer reaches into ``_relaunch_ack_event`` private state.
        # Lowered from 2.0s to 0.5s: the host acks in <100ms when it
        # works (Tauri ``main.rs``'s ``tokio::time::sleep(10ms)`` before
        # ``app.restart()``); 2.0s was the wrong ceiling — the worst
        # case is the host-is-dead case where waiting accomplishes
        # nothing and blocks the tray callback thread. 500ms is generous
        # for the happy path and bounds the dead-host stall at 4×
        # shorter. ``_wait_for_relaunch_ack`` itself short-circuits to
        # a 0ms wait when no IPC server is attached OR no live WS
        # dispatch pool is bound (see the helper below) so the 500ms
        # ceiling only applies when there's actually someone to ack.
        self._wait_for_relaunch_ack(timeout=0.5)

        # 3. : run the SAME audited cleanup as quit() — flushes
        #    history_db and _crash_recovery (so no pending writes are
        #    silently lost on restart), stops recorder + mic watcher
        #    (so PortAudio streams don't leak across the restart), stops
        #    all three hotkey backends + the bubble level worker,
        #    terminates any Electron subprocess we spawned, releases
        #    the single-instance mutex + PID file, and closes devnull
        #    streams.
        #
        #    Previously restart_app() did only a PARTIAL cleanup
        #    (timers + hotkeys + tray) and skipped the rest, leaking
        #    PortAudio streams / the Win32 mutex / the mic watcher
        #    daemon thread and silently losing pending history_db +
        #    crash_recovery writes on EVERY restart. The
        #    _atexit_cleanup safety net couldn't pick up the slack
        #    because its _shutting_down guard short-circuited as soon
        #    as restart_app() set _shutting_down = True above.
        #    Extracting the shared _do_cleanup() body fixes both bugs.
        #
        # XCUT-1: mirror quit()'s ordering by running
        #    ``self._thread_registry.shutdown_all()`` BEFORE
        #    ``_do_cleanup()``. The registry's centralized
        #    signal-and-join (per-thread stop_event + join-with-timeout)
        #    must run on the restart path too — otherwise the
        #    bubble-level-pusher and any other registered daemon threads
        #    never receive their stop signal and are only cleaned up
        #    implicitly when the process exits. The per-site shutdown
        #    methods inside ``_do_cleanup()`` are idempotent safety
        #    nets, but they don't cover every registered thread (e.g.
        #    future additions registered only with the registry).
        #    ``shutdown_all()`` is itself idempotent, so a subsequent
        #    call from ``_atexit_cleanup()`` is a no-op.
        try:
            app._thread_registry.shutdown_all()
        except Exception:
            log.warning(
                "[RESTART] thread_registry.shutdown_all failed",
                exc_info=True,
            )
        # Call the delegate (not ShutdownController._do_cleanup directly)
        # so test spies that monkeypatch app._do_cleanup intercept.
        app._do_cleanup()

        # 4. Exit cleanly — electron will relaunch us.
        # mirror ShutdownController.quit()'s threading-aware
        # exit. restart_app is invoked from the tray menu callback,
        # which runs on pystray's worker thread (NOT the main thread).
        # When sys.exit(0) is called from a non-main thread, CPython
        # raises SystemExit in THAT thread only — the process does not
        # exit. The tray's _wrap callback wrapper suppresses
        # SystemExit, so the sys.exit(0) is silently swallowed and the
        # process lingers for up to ~1s (holding the single-instance
        # mutex / IPC port) until tray.stop() (called inside
        # _do_cleanup above) breaks the pystray loop and app.start()
        # returns. On the main thread, sys.exit(0) works normally. The
        # conditional mirrors quit()'s pattern at
        # shutdown_controller.py:464,497-498.
        #
        # if restart_app() is running on a non-main thread (the
        # common case — pystray tray menu callback), arm the same
        # shutdown watchdog ``quit()`` uses. If ``tray.stop()`` (called
        # inside ``_do_cleanup`` above) failed to break the pystray
        # loop and the main thread is still parked in ``tray.run()``
        # after ``SHUTDOWN_WATCHDOG_TIMEOUT_S`` seconds, the watchdog
        # calls ``os._exit(0)`` to unblock the process. Without this,
        # a hung pystray backend leaves the old process unkillable
        # after a Restart click — and the new process can't claim the
        # single-instance mutex / IPC port, so the restart silently
        # fails. The watchdog is a daemon thread, so it never blocks
        # the normal exit path (if the main thread returns from
        # ``tray.run()`` promptly, the process exits and the daemon
        # is killed).
        is_main_thread = threading.current_thread() is threading.main_thread()
        if not is_main_thread:
            try:
                app.shutdown._arm_shutdown_watchdog(app._shutdown_watchdog_timeout_s)
            except Exception:
                # This is the LAST line of defense against a hung
                # restart — if the watchdog itself failed to arm (e.g.
                # ``app.shutdown`` is None because ShutdownController
                # lazy-init failed), the old process will never exit and
                # the new instance can't bind. Failure here MUST be loud
                # (ERROR) so it shows up in the default-INFO production
                # log; the previous DEBUG level was invisible to operators.
                log.error(
                    "[RESTART] failed to arm shutdown watchdog — restart may hang",
                    exc_info=True,
                )
        log.info("[RESTART] Old process exiting via sys.exit(0)")
        if is_main_thread:
            sys.exit(0)
        # else: rely on tray.stop() (called inside _do_cleanup) to
        # break the pystray loop so app.start() returns and
        # ipc_server.main() falls through to process exit. If that
        # doesn't happen within SHUTDOWN_WATCHDOG_TIMEOUT_S seconds,
        # the  watchdog will call os._exit(0) as a last resort.

    def _wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Wait for the host to ack the ``relaunch_app`` event.

        Delegates to the IPCServer's public
        ``wait_for_relaunch_ack(timeout)`` wrapper so this module no
        longer reaches into ``_relaunch_ack_event`` private state.
        The wrapper clears the event before waiting (preserving the
        stale-ack guard) and returns ``True``/``False`` on ack /
        timeout — same contract as the previous inline ``wait``.

        The previous implementation always blocked for 300ms
        (``time.sleep(0.3)``) when no IPC server / no ack event was
        attached, and ``restart_app`` waited up to 2.0s for the ack.
        Both timeouts penalised the dead-host case (host already gone,
        WS torn down, IPC server absent) where waiting accomplishes
        nothing — the tray callback thread just sits in a sleep while
        the IPC dispatch gate rejects all new requests for the same
        window. This helper now:

        1. Skips the wait entirely (0ms) when ``self._app._ipc_server``
           is ``None`` (early restart, no IPC wired yet) — no IPC
           server means no one is listening for the ``relaunch_app``
           event.
        2. Otherwise delegates to ``ipc_server.wait_for_relaunch_ack``
           which itself short-circuits to 0ms when the IPC server has
           no live WS dispatch pool, no ``_relaunch_ack_event``
           attribute, or the wait times out within ``timeout``
           seconds (``restart_app`` now passes ``0.5`` — was ``2.0``).

        Parameters
        ----------
        timeout :
            Maximum seconds to wait for the host's ack. Callers now
            pass ``0.5`` (was ``2.0``).

        Returns
        -------
        bool
            ``True`` if the ack event was signalled within ``timeout``;
            ``False`` if no IPCServer is attached, no live WS dispatch
            pool is bound, the IPCServer has no ``_relaunch_ack_event``
            attribute (e.g. a test double), or the wait timed out.
            Callers today ignore the return value (the original inline
            code didn't return one either) — the contract is preserved
            for future use.
        """
        app = self._app
        ipc_server = app._ipc_server
        # Short-circuit when no IPC server is attached. No IPC server
        # means no one is listening for the ``relaunch_app`` event —
        # waiting (the old ``time.sleep(0.3)`` fallback) just blocks
        # the tray callback thread for nothing. The previous 300ms
        # pause was a belt-and-suspenders fallback for the case where
        # the host might still observe the relaunch intent via the
        # ``pythonProcess.on("exit")`` handler — but that handler
        # triggers on PROCESS EXIT, not on a 300ms sleep, so the sleep
        # was pure waste.
        if ipc_server is None:
            log.debug("[RESTART] No IPC server attached; skipping relaunch_ack wait")
            return False
        log.info(
            "[RESTART] Waiting for relaunch_ack from host (timeout %.3fs)",
            timeout,
        )
        acked = False
        # Delegate to the IPC server's ack-wait primitive, but tolerate
        # test doubles / standalone modes that don't expose the public
        # ``wait_for_relaunch_ack`` method or the ``_relaunch_ack_event``
        # attribute. The defensive ``getattr`` lookups preserve the
        # encapsulation while not breaking tests that swap in a
        # minimal fake server.
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
                "[RESTART] relaunch_ack timed out after %.3fs — host may be dead or slow; proceeding with cleanup",
                timeout,
            )
        return acked
