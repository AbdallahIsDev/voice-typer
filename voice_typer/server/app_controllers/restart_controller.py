"""CR-24: RestartController — extracted from
``VoiceTyperApp.restart_app``.

Owns the "restart the app" tray-menu action: pushes a
``relaunch_electron`` event to Electron over the active TCP channel,
waits for Electron to ack (or falls back to a 300ms pause), runs the
shared audited cleanup body via ``_do_cleanup()``, and exits the
current process via the clean ``sys.exit(0)`` path so Python atexit
handlers / ``__del__`` / ``finally`` blocks all run.

The actual logic lived on ``VoiceTyperApp.restart_app`` (924-1068 in
the pre-CR-24 ``app.py``).  The behaviour is preserved verbatim —
only the class boundary moved — with three bug fixes folded in:

    - APP-1: re-entry guard at the top of the method.  When
      ``self._shutting_down`` is already ``True`` (e.g. user
      double-clicks the tray restart item, or a tray restart races
      with a SIGTERM-triggered quit), restart_app now short-circuits
      BEFORE any side effect (no duplicate ``relaunch_electron``
      push, no re-entry into ``_do_cleanup()``, no second
      ``sys.exit(0)``).  Previously a duplicate call would re-push
      the event, re-acquire ``_config_mutation_lock``, and fire a
      second ``sys.exit(0)`` while the first call's finally blocks
      were still draining.
    - APP-2: ``log.info("[RESTART] Restarting %s...")`` now passes
      ``APP_NAME`` as the format argument so the ``%s`` placeholder
      is substituted in the formatted log line (was leaving a
      literal ``%s`` in the output).
    - APP-11: removed the redundant ``self._restore_volume(fade_ms=0)``
      call.  ``_do_cleanup()`` (called later in restart_app via the
      shared ShutdownController body) already invokes the
      volume-restore path; the direct call was a double-restore that
      could race with the in-flight cleanup if the volume backend
      wasn't reentrant.

``VoiceTyperApp`` keeps a thin 1-line delegation
(``def restart_app(self): return self.restart.restart_app()``) so
tests that do ``monkeypatch.setattr("voice_typer.server.app.
restart_app", ...)`` or ``app.restart_app()`` keep working unchanged.

LOG-NOTE: this controller logs via the app module's logger
(``voice_typer.server.app.log``, fetched lazily inside
``restart_app`` so the import-time circular dependency is avoided).
Tests that do ``caplog.at_level(logging.INFO, logger="voice_typer.
server.app")`` (e.g. ``TestAppRestartLogMessage`` in
``tests/app/test_quit_restart.py``) then capture the records without
needing to know the controller's own logger name.  Mirrors the
convention used by ``settings_controller.py`` for platform-helper
lookups.

RELIABILITY-001 / RELIABILITY-003 / RELIABILITY-006 / RACE-020 /
PERF-005 / RW-3 / HIGH-36 / XCUT-1.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp

# Module-level logger for non-test-facing diagnostic logs (e.g. the
# APP-1 re-entry-guard debug log).  The user-facing ``[RESTART]
# Restarting %s...`` info log is emitted via the app module's logger
# so ``caplog.at_level(logger="voice_typer.server.app")`` test seams
# keep working — see LOG-NOTE in the module docstring.
log = logging.getLogger(__name__)


class RestartController:
    """Owns the "restart the app" tray-menu action.

    CR-24: extracted from ``VoiceTyperApp.restart_app``.  The app
    passes itself (``app``) so the controller can read/write
    ``app.config``, ``app._shutting_down`` /
    ``app._shutting_down_event``, ``app._ipc_server`` (for the
    ``_relaunch_ack_event``), ``app._thread_registry``, and call the
    shared ``app._do_cleanup()`` body.
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app

    def restart_app(self) -> None:
        """TrayController protocol: restart the app.

        Sends a ``relaunch_electron`` event to Electron over the active
        TCP channel, then exits the current instance via the clean
        ``sys.exit(0)`` path.  Electron's handler calls
        ``app.relaunch()`` + ``app.exit(0)``, which spawns a fresh
        Electron process (which in turn spawns a fresh Python backend).
        If the ``relaunch_electron`` event is lost (TCP race),
        Electron's ``pythonProcess.on("exit")`` handler sees exit code
        0 and triggers the same relaunch as a fallback — see
        ``client/src/main/index.ts``.

        This replaces the old ``restart_ack`` design which tried to
        keep Electron alive while swapping only the Python backend.
        That design had multiple race conditions:

          1. The TCP 'close' event could fire before the 'data' event
             delivering ``restart_ack`` was processed, causing spurious
             "Python socket closed" errors.
          2. ``tcpConnect()`` set ``tcpSocket = client`` BEFORE the
             socket connected, so IPC calls during the reconnection
             window were written to the unconnected socket, buffered,
             and sent BEFORE the auth handshake — causing auth failures
             and cascading "Error: Timeout" errors.
          3. The ``_restarting`` flag was cleared too early (in
             ``startPython``, before the new process was up), leaving a
             window where ``sendToPython`` wrote to a stale/dying socket.

        The full-relaunch approach eliminates all of these: the entire
        OS process is replaced, so there's no state to coordinate. The
        user's explicit request was "close the entire process, the
        entire backend, and the entire Electron application; everything
        should be closed and opened again."

        RELIABILITY-001: was ``os._exit(0)`` which skipped atexit
        handlers + ``__del__``, leaking the Win32 mutex, PortAudio
        handles, and ``RegisterHotKey`` registrations. RELIABILITY-003:
        also stops ``_esc_backend`` and ``_repaste_backend`` so the new
        instance can re-register them. RELIABILITY-006: marks
        ``_shutting_down`` before cleanup so atexit doesn't log "likely
        killed externally" for an intentional restart.

        APP-1 (CR-24 bug fix): re-entry guard at the top of the method.
        When ``self._shutting_down`` is already ``True``, restart_app
        short-circuits BEFORE any side effect (no duplicate
        ``relaunch_electron`` push, no re-entry into ``_do_cleanup()``,
        no second ``sys.exit(0)``).

        APP-2 (CR-24 bug fix): ``log.info("[RESTART] Restarting %s...")``
        now passes ``APP_NAME`` as the format argument so the ``%s``
        placeholder is substituted.

        APP-11 (CR-24 bug fix): removed the redundant
        ``self._restore_volume(fade_ms=0)`` call — ``_do_cleanup()``
        already invokes the volume-restore path via the shared
        ShutdownController body.
        """
        app = self._app
        # Look up the app module's logger + time/sys helpers at call
        # time so tests that monkeypatch ``voice_typer.server.app.time.
        # sleep`` / ``voice_typer.server.app.sys.exit`` (e.g. via
        # ``_stub_restart_environment`` in tests/test_app_cleanup.py
        # and tests/app/test_quit_restart.py) take effect, AND so the
        # ``[RESTART] Restarting %s...`` INFO log reaches the
        # ``caplog.at_level(logger="voice_typer.server.app")`` test
        # seam (LOG-NOTE in the module docstring).
        from voice_typer.server import app as _app_module

        _log = _app_module.log

        # APP-1: re-entry guard. Short-circuit BEFORE any side effect
        # so a duplicate restart_app call (user double-clicks the tray
        # restart item, or a tray restart races with a SIGTERM-triggered
        # quit) is a true no-op — no duplicate ``relaunch_electron``
        # push, no re-entry into ``_do_cleanup()``, no second
        # ``sys.exit(0)`` while the first call's finally blocks are
        # still draining.
        if app._shutting_down:
            _log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return

        # APP-2: pass APP_NAME so the %s placeholder is substituted
        # (was leaving a literal ``%s`` in the formatted log line).
        _log.info("[RESTART] Restarting %s...", APP_NAME)

        # ── THEME-RESTART-FIX: save the config before push ───────────
        # Save any pending in-memory config changes (e.g. a theme preset
        # change that was set via `set_config` but whose save completed
        # while the user navigated to the tray menu) to disk before the
        # restart sequence begins.  This ensures the new Python process
        # loads the latest config, preventing the theme from reverting
        # to default after a restart.
        if not app.config.save():
            _log.warning("[RESTART] config.save() before push failed")

        # ── CRITICAL ORDERING FIX ────────────────────────────────────
        #
        # _push_event_now() MUST be called BEFORE _shutting_down is set
        # to True.  The _send() method in ipc_server.py checks
        # _shutting_down and if True, closes the TCP socket WITHOUT
        # writing the event — silently dropping it.  This was the root
        # cause of the "restart does nothing" bug: the relaunch_electron
        # event was never received by Electron, so _relaunching stayed
        # false, and the fallback exit handler also failed because the
        # Python process never actually exited (SystemExit was caught
        # by wrap_callback without tray.stop() breaking the loop).
        #
        # 1. Push relaunch_electron BEFORE marking _shutting_down.
        from voice_typer.server import event_bus

        try:
            event_bus.publish({"type": "relaunch_electron"})
            _log.info("[RESTART] relaunch_electron pushed to Electron via TCP")
        except Exception as e:
            _log.warning("[RESTART] failed to push relaunch_electron: %s", e)

        # 2. NOW mark as shutting down and wait (event-driven) for
        #    Electron to process the relaunch event before we close the
        #    socket.  PERF-005: replaced the fixed time.sleep(0.3) with
        #    a bounded wait on the ``relaunch_ack`` event that Electron
        #    sets when it receives ``relaunch_electron``.  This unblocks
        #    the (tray) calling thread as soon as Electron acks, instead
        #    of always blocking 300ms; if no ack arrives (e.g. Electron
        #    already gone), we fall back to the original 300ms pause so
        #    behaviour is unchanged.
        #
        #    APP-11 (CR-24): the redundant ``self._restore_volume(
        #    fade_ms=0)`` call that used to live here is REMOVED —
        #    ``_do_cleanup()`` (called below) already invokes the
        #    volume-restore path via the shared ShutdownController body,
        #    so the direct call was a double-restore that could race
        #    with the in-flight cleanup if the volume backend wasn't
        #    reentrant.
        app._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can
        # check it (matches quit()'s shutdown signaling — important now
        # that restart_app() shares the same _do_cleanup() body).
        app._shutting_down_event.set()
        _relaunch_ack_event = (
            getattr(app._ipc_server, "_relaunch_ack_event", None) if app._ipc_server is not None else None
        )
        if _relaunch_ack_event is not None:
            _relaunch_ack_event.clear()
            _log.info("[RESTART] Waiting for relaunch_ack from Electron (timeout 2.0s)")
            _relaunch_ack_event.wait(timeout=2.0)
        else:
            _log.info("[RESTART] No IPC server available; pausing 300ms for Electron")
            _app_module.time.sleep(0.3)

        # 3. RW-3: run the SAME audited cleanup as quit() — flushes
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
        #    HIGH-36 / XCUT-1: mirror quit()'s ordering by running
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
            _log.warning(
                "[RESTART] thread_registry.shutdown_all failed",
                exc_info=True,
            )
        app._do_cleanup()

        # 4. Exit cleanly — electron will relaunch us.
        _log.info("[RESTART] Old process exiting via sys.exit(0)")
        # Access ``sys`` via the app module so tests that monkeypatch
        # ``voice_typer.server.app.sys.exit`` (e.g. test_app_cleanup.py
        # via ``_stub_restart_environment``) take effect. The patched
        # ``sys.exit`` raises ``SystemExit(code)``, which the test
        # catches via ``contextlib.suppress(SystemExit)``.
        _app_module.sys.exit(0)
