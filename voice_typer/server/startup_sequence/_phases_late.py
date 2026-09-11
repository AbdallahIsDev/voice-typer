"""Late startup phases (5-8), platform warnings through finalize.

Second half of the ``StartupSequence`` phase decomposition, extracted
verbatim from the former ``startup_sequence.py`` monolith:

- phase 5, Wayland hotkey warnings + macOS Accessibility permission
- phase 6, autostart sync + prewarm / offline-pack / dictation hotkey
  registration (BEFORE the mic task) + mic enumeration
- phase 7, background model load
- phase 8, restart detection + bubble show + startup-complete line +
  post-ready stale-file sweep dispatch

Owns the module-level Wayland-warning state holder (``_MODULE_STATE``).
Behavior and log lines unchanged (C-LOG-1 / C-LOG-2, the
``Startup complete`` duration suffix is emitted here verbatim).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, cast

from voice_typer.server.branding import APP_NAME
from voice_typer.server.duration import format_duration
from voice_typer.server.platform_utils import is_linux, is_macos, is_wayland_session
from voice_typer.server.server_platform import autostart as _autostart_facade
from voice_typer.server.startup_sequence._phases_early import StageResult

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the package
    # docstring (same discipline as ``_phases_early``).
    from voice_typer.server.app import VoiceTyperApp

# Explicit logger name (not ``__name__``): tests capture logs at the
# pre-split ``voice_typer.server.startup_sequence`` logger (same
# convention as ``app_lifecycle.py``), and this module's records must
# keep landing there after the split.
log = logging.getLogger("voice_typer.server.startup_sequence")


# Wayland warning state: module-level state holder for the Wayland warning
# structured-dict + last-warned-version. Implemented as a tiny class
# (rather than bare module globals) so tests can ``setattr`` a fresh
# instance on the ``_MODULE_STATE`` holder to reset the state
# between test cases without polluting the module namespace.
# ``wayland_warning_state`` is a dict with the keys
# ``{session_type, wtype_available, ydotool_available, warned_at}``;
# ``wayland_warned_version`` is the ``voice_typer.__version__`` string
# captured the last time the warning fired. Both default to ``None``
# (no previous state → first-run always re-warns).
class _ModuleState:
    """Container for module-level mutable state (Wayland warning etc)."""

    wayland_warning_state: dict | None = None
    wayland_warned_version: str | None = None


_MODULE_STATE = _ModuleState()


class LatePhases:
    """Phases 5-8 of the startup sequence (mixin for ``StartupSequence``).

    ``app`` is a back-reference so the phases can read/write the app's
    state (config, tray, models, hotkeys, etc.), same attribute surface
    as the pre-extraction monolith, just renamed from ``self.X`` to
    ``self._app.X``.
    """

    # Back-reference to the owning ``VoiceTyperApp`` (assigned by
    # ``StartupSequence.__init__`` in the package ``__init__``), plus the
    # total-startup duration anchor set by ``run`` (C-LOG-2).
    _app: VoiceTyperApp
    _t0: float

    def _phase_5_platform_warnings(self) -> StageResult:
        """Phase 5, emit Wayland + macOS accessibility platform warnings.

        Wayland: warn if global hotkeys may not work, suggest
        wtype/ydotool as fallback. The structured warning state
        (module-level ``_MODULE_STATE.wayland_warning_state``)
        re-emits whenever session type, wtype availability, or
        ydotool availability changes, and once per app version
        upgrade.

        macOS: probe Accessibility permission via the
        AXIsProcessTrusted() ctypes call (no PyObjC dependency); a
        load failure is treated as "not granted" and the A11yPulse
        periodic task re-probes within 60s. A missing-grant surfaces
        a tray notification (critical, bypasses the toggle because
        hotkeys are broken without Accessibility permission).
        """
        app = self._app
        # PLAT-WAYLAND: Warn if running on Wayland and
        # suggest wtype/ydotool as fallback for global hotkeys.
        #
        # Wayland warning state: the previous ``app.config.wayland_warned`` boolean
        # was a one-shot latch, once set, the warning NEVER re-fired,
        # even if the user uninstalled wtype/ydotool after the first
        # warning. We now use a structured ``wayland_warning_state``
        # dict (stored as a module-level attribute on
        # ``startup_sequence`` so we don't require a new config field
        # , config is owned by another agent) and re-emit the warning
        # whenever any field changes (session type changed, wtype
        # availability changed, ydotool availability changed). The
        # module-level state is reset on every app restart, so a
        # version upgrade (where the user runs a new build) re-warns
        # by default, matching the "alternatively" approach from the
        # finding (re-warn once per app version). The boolean
        # ``app.config.wayland_warned`` is still set for backwards
        # compat with older configs that may already have it set.
        #
        # Delegate Wayland detection to platform_utils.is_wayland_session
        # (single source of truth, handles XDG_SESSION_TYPE +
        # WAYLAND_DISPLAY, case-insensitive).
        if is_linux() and is_wayland_session():
            log.warning("[STARTUP] Wayland detected -- global hotkeys may not work")
            # check if wtype or ydotool is available as a fallback
            import shutil

            wtype_available = shutil.which("wtype") is not None
            ydotool_available = shutil.which("ydotool") is not None

            # Wayland warning state: structured state, re-warn whenever any field
            # changes vs. the previous run. ``_wayland_warning_state``
            # is a module-level dict on ``startup_sequence`` (NOT on
            # ``app.config``) because config is owned by another
            # agent. The state survives across calls within the same
            # process but is reset on app restart, matching the
            # finding's "alternatively" approach (re-warn once per
            # app version, since the version changes per restart
            # only when the user upgrades).
            from datetime import datetime

            current_state = {
                "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
                "wtype_available": wtype_available,
                "ydotool_available": ydotool_available,
            }
            previous_state = getattr(_MODULE_STATE, "wayland_warning_state", None)
            state_changed = (
                previous_state is None
                or previous_state.get("session_type") != current_state["session_type"]
                or previous_state.get("wtype_available") != current_state["wtype_available"]
                or previous_state.get("ydotool_available") != current_state["ydotool_available"]
            )
            # Also re-warn if the app version changed since the last
            # warning (``wayland_warned_version`` stored alongside the
            # structured state, uses ``getattr`` so it's forward-
            # compatible if a future config field with the same name
            # is added).
            try:
                import voice_typer as _vt

                _current_vt_version = getattr(_vt, "__version__", None)
            except Exception:
                _current_vt_version = None
            _last_warned_version = getattr(_MODULE_STATE, "wayland_warned_version", None)
            version_changed = _current_vt_version is not None and _last_warned_version != _current_vt_version
            should_warn = not app.config.wayland_warned or state_changed or version_changed
            if should_warn:
                if not wtype_available and not ydotool_available:
                    log.warning(
                        "[STARTUP] Neither wtype nor ydotool found. "
                        "Install one for hotkey support on Wayland: "
                        "'sudo apt install wtype' or 'sudo apt install ydotool'"
                    )
                    # critical, bypass toggle (hotkeys broken).
                    app.tray.notify_safety(
                        f"{APP_NAME}, Wayland Hotkeys",
                        "Global hotkeys may not work on Wayland. "
                        "Install 'wtype' or 'ydotool' for hotkey support, "
                        "or use the tray menu's Start Dictation option.",
                    )
                else:
                    log.info(
                        "[STARTUP] Wayland hotkey fallback available: %s",
                        "wtype" if wtype_available else "ydotool",
                    )
                # Persist the structured state + version for the next
                # run's diff check.
                _MODULE_STATE.wayland_warning_state = {
                    **current_state,
                    "warned_at": datetime.now().isoformat(),
                }
                if _current_vt_version is not None:
                    _MODULE_STATE.wayland_warned_version = _current_vt_version
                # Backwards compat: keep setting the legacy boolean so
                # older configs that read it still see "warned".
                app.config.wayland_warned = True
                app.config.save()

        # macOS accessibility permission check.
        # On macOS, global hotkeys require Accessibility permission.
        # The app can't request it directly, but we can detect it's
        # missing and notify the user.
        _has_accessibility = False
        if is_macos():
            try:
                # Use AXIsProcessTrusted() via ctypes for the
                # definitive check.  AXIsProcessTrusted() is the official
                # API, it returns True iff the process has Accessibility
                # permission.  We load it from ApplicationServices.framework
                # via ctypes (no PyObjC dependency required).
                #
                # the prior fallback to `osascript -e 'tell
                # application "System Events" to keystroke " "'` was
                # removed because:
                #   1. It runs on the critical startup thread (before
                #      hotkey registration at line 528 and before the
                #      parallel prewarm/mic work at line 516) and blocks
                #      for up to 3s on the subprocess + osascript
                #      interpreter startup.
                #   2. It synthesizes a REAL keystroke via System Events
                #      (a space character), which is invasive, it
                #      focuses the frontmost app and types into whatever
                #      has keyboard focus. A user running Voice Typer
                #      at login could see the space land in their
                #      password prompt or terminal.
                #   3. The osascript path is reached whenever ctypes
                #      ApplicationServices load fails (stripped-down
                #      macOS installs, code-signed bundles with
                #      restricted dyld env, CI runners), so it's a
                #      real production path, not just a dev fallback.
                #
                # Replacement strategy: if the ctypes probe fails (load
                # error / symbol missing), treat the result as
                # "permission not granted" (False) and let the periodic
                # A11yPulse (started at line 406 below) detect the grant
                # within 60s. A11yPulse uses the SAME ctypes probe but
                # runs it off the startup hot path, so a transient
                # load failure on startup doesn't wedge the user, the
                # next A11yPulse tick re-tries and updates the tray.
                #
                # VALIDATE ON MACOS HOST: AXIsProcessTrusted() returns
                # the correct value on real macOS (the existing
                # tests/test_a11y_pulse.py suite exercises the same
                # ctypes probe on macOS CI).
                try:
                    import ctypes

                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    _has_accessibility = bool(app_services.AXIsProcessTrusted())
                except Exception as exc:
                    # drop the osascript fallback entirely. Treat
                    # ctypes-load failure as "permission not granted"
                    # (False), the A11yPulse task (started below at
                    # line 406) will re-probe within 60s and update
                    # `_has_accessibility` + the tray icon when the
                    # grant is detected. This avoids the 3s osascript
                    # subprocess on the startup hot path AND avoids
                    # the invasive keystroke synthesis.
                    log.warning(
                        "[STARTUP] macOS AXIsProcessTrusted ctypes load "
                        "failed (%s); treating as not-granted. A11yPulse "
                        "will re-probe within 60s.",
                        exc,
                    )
                    _has_accessibility = False

                if not _has_accessibility:
                    log.warning("[STARTUP] macOS Accessibility permission not granted")
                    # critical, bypass toggle (hotkeys broken).
                    app.tray.notify_safety(
                        f"{APP_NAME}, Accessibility Permission",
                        "Global hotkeys require Accessibility permission. "
                        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility "
                        f"and add {APP_NAME} (or Terminal).",
                    )
            except Exception:
                # M-67: promote debug→warning so the failure surfaces in
                # the default log; include the traceback so operators can
                # diagnose why the macOS accessibility check failed.
                log.warning("[STARTUP] macOS accessibility check failed", exc_info=True)

            # Start a periodic accessibility health monitor.
            # If the user grants permission AFTER startup, the app will
            # detect it within 60 seconds and clear the warning. If the
            # user revokes permission mid-session, the app will re-warn.
            # Phase 2: invoke startup_tasks directly. The
            # ``app._start_accessibility_pulse`` delegate was removed; callers
            # now target startup_tasks (and tests monkeypatch startup_tasks).
            from voice_typer.server import startup_tasks

            startup_tasks.start_accessibility_pulse(app, _has_accessibility)

        return StageResult(success=True)

    def _phase_6_autostart_prewarm_mics(self) -> StageResult:
        """Phase 6, sync autostart + hotkey registration + mic enumeration.

        The OS-level autostart sync runs on a fire-and-forget daemon
        thread (BP-129: ``sync_autostart`` shells out to schtasks
        /Query on Windows, up to 30s against a hung Task Scheduler
        service, so it must never sit on the hotkey-registration
        critical path). Its completion applies
        ``tray.set_autostart_enabled``, which invalidates the menu
        cache, so even a late result corrects the first menu paint.
        The dictation hotkey is registered BEFORE the mic task (the
        only step with a wait budget): the mic enumeration runs under a
        5s timeout, and a hung audio stack must not keep the hotkey
        dead through that window, late mic results already recover by
        design (``startup_tasks.load_microphones`` pushes
        ``microphones_changed`` on first population / device-set change
        and rebuilds the tray's Microphone submenu via
        ``app.tray.set_microphones``). A final RACE-020 shutdown check
        aborts before Phase 7 if ``app._shutting_down`` was set during
        the parallel work. (The OS-level prewarm-task sync is gone
        entirely: prewarm is a worker startup phase: see 1b below.)
        """
        app = self._app
        # 1. Sync autostart config with platform. OFF the critical
        # path (see docstring). Phase 2: invoke startup_tasks
        # directly. The ``app._sync_autostart`` delegate was removed;
        # callers now target startup_tasks (and tests monkeypatch
        # startup_tasks).
        from voice_typer.server import startup_tasks

        def _autostart_task() -> None:
            try:
                autostart_result = startup_tasks.sync_autostart(app)
            except Exception:
                log.warning("[STARTUP] autostart sync failed", exc_info=True)
                return
            # (a): sync_autostart returns a result dict whose
            # ``actual_post_sync`` field carries the post-sync OS-level
            # autostart state (True iff the OS-level autostart entry is
            # currently registered). Use that field instead of calling
            # ``is_autostart_enabled()`` a second time, the pre- path
            # called the platform helper twice back-to-back on every startup,
            # and the second call always returned the same value as the one
            # sync_autostart already read internally. Falling back to a direct
            # ``is_autostart_enabled()`` call only when sync_autostart's
            # result lacks the field (older test stubs that monkeypatch
            # sync_autostart to return ``None``).
            if isinstance(autostart_result, dict) and "actual_post_sync" in autostart_result:
                autostart_enabled = bool(autostart_result["actual_post_sync"])
            else:
                # Test-stub fallback: the monkeypatched sync_autostart returned
                # ``None`` (or a dict without the field). Fall back to the
                # direct platform read so the tray menu shows the real state.
                try:
                    autostart_enabled = _autostart_facade.is_autostart_enabled()
                except Exception:
                    log.warning("[STARTUP] autostart state read failed", exc_info=True)
                    return
            app.tray.set_autostart_enabled(autostart_enabled)

        autostart_thread = threading.Thread(
            target=_autostart_task,
            name="startup-autostart-sync",
            daemon=True,
        )
        autostart_thread.start()
        log.debug(
            "[STARTUP] autostart sync dispatched to fire-and-forget "
            "daemon thread (no wait, no timeout), hotkey registration "
            "proceeds without waiting on it"
        )

        # RACE-020: check for shutdown after each major step
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after autostart sync")
            return StageResult(success=False, data={"shutdown": True})

        # 1b. (BP-129) The OS-level prewarm scheduled-task sync is gone:
        # prewarm became a worker startup phase (master plan §6.2 P-1),
        # so there is no scheduled task to register and no schtasks
        # round-trip to run. The trigger-regime INFO line and the
        # fire-and-forget dispatch thread that used to call the (now
        # no-op) ``sync_prewarm_task`` stub were deleted with it —
        # neither the 30s schtasks ceiling nor dead ceremony sits
        # between startup and hotkey registration anymore.
        # (``sync_prewarm_task`` itself stays as a no-op stub: the
        # ``set_config`` IPC response still carries its
        # ``prewarm_status`` field.)

        # Shared prelude for the bounded parallel work below: the
        # timeout-utils runner (daemon workers never block process
        # exit) and the RACE-020 shutdown event so executor tasks can
        # abort early if the app is quitting during startup.
        from voice_typer.server._timeout_utils import (
            TIMEOUT as _TIMEOUT_SENTINEL,
            _run_parallel_with_timeout,
        )

        # RACE-020: pass the shutdown event to executor tasks so they
        # can abort early if the app is quitting during startup.
        _shutdown_event = app._shutting_down_event if hasattr(app, "_shutting_down_event") else None

        # Phase 2d (§8.10, §8.16): launch-time offline-pack existence
        # check. Fire-and-forget daemon thread, the cheap
        # ``pack-manifest.json`` existence scan + the optional
        # consent-gated re-download must never delay hotkey
        # registration or the window. When the pack is present the
        # full SHA-256 checksum runs on its own daemon thread
        # (BackgroundChecksum); startup only ever does the cheap
        # check synchronously (§8.16).
        def _pack_check_task() -> None:
            # The concrete VoiceTyperApp exposes several AppProtocol
            # members (history_db, recording, recorder, …) as lazy
            # properties while the protocol declares them as plain
            # attributes, so the concrete class isn't structurally
            # assignable to AppProtocol (pyrefly). This function only
            # reads `config`; the cast is a documented assertion of
            # that narrow surface (same class of workaround as
            # startup_tasks.py's `setattr` on a non-protocol member).
            #
            # RUNTIME-FIX: ``AppProtocol`` is imported under
            # TYPE_CHECKING at module scope, but ``cast()`` evaluates
            # its type argument at RUNTIME: without a runtime
            # binding this thread died with ``NameError`` before ever
            # calling ``check_offline_pack_on_launch``, silently
            # disabling the launch-time offline-pack check (observed
            # as PytestUnhandledThreadExceptionWarning in the suite).
            # Function-local import (module-level would violate the
            # import-cycle discipline documented in the module
            # docstring; ``providers`` is already a runtime
            # dependency via ``startup_tasks``).
            from voice_typer.server.providers import AppProtocol as _AppProtocol

            startup_tasks.check_offline_pack_on_launch(cast(_AppProtocol, app), _shutdown_event)

        pack_thread = threading.Thread(
            target=_pack_check_task,
            name="startup-pack-check",
            daemon=True,
        )
        pack_thread.start()
        log.debug(
            "[STARTUP] Phase 2d pack existence check dispatched to fire-and-forget daemon thread (no wait, no timeout)"
        )

        # 2. Register the dictation hotkey BEFORE the mic task. The mic
        # enumeration (below) runs in a bounded parallel pool under a 5s
        # timeout; a hung/slow audio stack must not keep the dictation
        # hotkey dead for that window. Late mic results already recover
        # by design: ``startup_tasks.load_microphones`` pushes a
        # ``microphones_changed`` event on first population and on
        # device-set change, and calls ``app.tray.set_microphones`` so
        # the tray's Microphone submenu rebuilds, nothing requires
        # mics to finish before the hotkey is bound. The hotkey is also
        # still registered BEFORE the model load (phase 7), preserving
        # the F2-works-even-if-model-fails invariant.
        log.debug("[STARTUP] Registering hotkey")
        # Phase 2: invoke HotkeyDispatcher directly. The
        # ``app._register_hotkey`` delegate was removed; callers now target
        # ``app.hotkeys`` (and tests monkeypatch app.hotkeys.register).
        app.hotkeys.register()

        # RACE-020: check for shutdown after hotkey registration
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after hotkey registration")
            return StageResult(success=False, data={"shutdown": True})

        def _mic_task() -> None:
            startup_tasks.load_microphones(app, _shutdown_event)

        items = [
            ("mic", _mic_task, 5.0),
        ]
        results = _run_parallel_with_timeout(items)
        for label, value in results:
            # ``_run_parallel_with_timeout`` captures per-call
            # failures into the result tuple (caller decides
            # whether to re-raise / log / ignore). ``TIMEOUT``
            # means the task did not finish within its budget;
            # the daemon worker is leaked (and will be reaped at
            # process exit by virtue of being a daemon).
            if value is _TIMEOUT_SENTINEL:
                log.warning(
                    "[STARTUP] %s task did not complete within 5s budget "
                    "(daemon worker leaked; will not block process exit)",
                    label,
                )
            elif isinstance(value, BaseException):
                log.warning("[STARTUP] %s task failed: %s", label, value)
            else:
                # Task completed successfully (return value is
                # whatever the task function returned, typically
                # ``None`` for these two startup tasks).
                pass
        # PERF-: the 30s ``sd.query_devices()`` device-change
        # poller (``_start_device_change_poller``) was removed from
        # startup because it is fully redundant with the
        # event-driven ``MicrophoneDeviceWatcher`` started in
        # ``Recorder.__init__`` (WM_DEVICECHANGE on Windows,
        # ``/dev/snd`` polling on Linux, CoreAudio property-listener
        # on macOS). The watcher is the sole source of truth; the
        # 30s poller was a defence-in-depth fallback that cost
        # ~1-5ms of CPU every 30s and allocated a fresh
        # ``threading.Event()`` object every second.  Phase 1
        # also deleted the now-orphaned ``_start_device_change_poller``
        # delegate from this class: see test_bugfix_regressions.py
        # ``TestAudioMicDeviceChangePoller`` for the full history.

        # 1b. Create desktop launcher shortcut on first run (if absent).
        # Dispatched on a fire-and-forget daemon thread (same precedent
        # as the pack check above): the COM fast path is quick, but when
        # the COM API is unavailable the fallback spawns PowerShell with a
        # 30s ceiling plus a second PowerShell icon-stamp step that takes
        # seconds, all of which previously ran on the startup critical path
        # BEFORE hotkey registration, delaying the dictation hotkey. The
        # shortcut is independent of mic enumeration and idempotent, so a
        # hung/slow creation has no correctness impact (the next launch
        # re-checks).
        def _desktop_shortcut_task() -> None:
            try:
                startup_tasks.ensure_desktop_shortcut(app)
            except Exception:
                log.debug("[STARTUP] desktop shortcut creation failed", exc_info=True)

        shortcut_thread = threading.Thread(
            target=_desktop_shortcut_task,
            name="startup-desktop-shortcut",
            daemon=True,
        )
        shortcut_thread.start()
        log.debug(
            "[STARTUP] desktop shortcut creation dispatched to fire-and-forget "
            "daemon thread, hotkey registration proceeds without waiting on it"
        )

        log.debug("[STARTUP] Running mic enumeration (bounded pool)")

        # RACE-020: check for shutdown after parallel work
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after mic enumeration")
            return StageResult(success=False, data={"shutdown": True})

        return StageResult(success=True)

    def _phase_7_hotkey_and_model_load(self) -> StageResult:
        """Phase 7, start the background model load.

        The dictation hotkey is registered in phase 6 (before the mic
        task), still BEFORE this model load, so F2 works even if the
        model fails to load (RACE-020 invariant: see package docstring
        (a)). A RACE-020 shutdown check after the load start aborts
        before the next phase. The model load itself runs on a daemon
        thread owned by ``ModelManager`` (the dominant cold-boot cost,
        ~30-45s on first run after Windows starts); running it in the
        background lets the app reach "Loading model…" within ~1s of
        launch.
        """
        app = self._app
        # Warmup handled synchronously in recording.py on first recording start.

        # 4. Create transcription engine and load model -- IN THE BACKGROUND.
        #
        # The model load is the dominant cost on a cold boot (~30-45s the
        # first time after Windows starts, dominated by reading ~6 GB of
        # torch + model-weight files off disk).  Running it in a daemon
        # thread lets the app reach "Ready" (well, "Loading model…") within
        # ~1s of launch; the user sees the tray icon, can open settings,
        # and -- if they press F2 before the load finishes -- gets queued
        # and auto-started once it completes.  See toggle_dictation().
        #
        # #2 ModelManager owns the load thread now; the
        # ``self._model_load_thread`` property delegate on VoiceTyperApp
        # reads/writes through to ``self.models._model_load_thread`` so
        # existing code that checks the thread (e.g. toggle_dictation)
        # keeps working.
        log.debug("[STARTUP] Loading model in background")
        # Root-cause reconciliation: if the configured model isn't
        # installed, clear ``config.model_size`` to the "no model
        # selected" sentinel and persist it BEFORE the background load
        # reads it.  Previously the config kept a concrete model name
        # (default ``"tiny"``) even with zero models on disk, so every
        # consumer reading ``config.model_size`` surfaced a phantom
        # model, each surface was patched individually, but the config
        # still carried the stale name.  This makes the CONFIG the
        # single source of truth: after the first launch, ``model_size``
        # is ``""`` and all consumers report "no model selected".
        try:
            from voice_typer.server import startup_tasks

            startup_tasks.reconcile_configured_model(app)
        except Exception:
            log.debug(
                "[STARTUP] model reconciliation failed (non-fatal, load precheck still guards)",
                exc_info=True,
            )
        app.models.start_background_load()

        # RACE-020: check for shutdown after background model load start
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after model load start")
            return StageResult(success=False, data={"shutdown": True})

        return StageResult(success=True)

    def _phase_8_finalize_and_signal(self) -> StageResult:
        """Phase 8, restart detection + bubble show + startup-complete log.

        After-restart: auto-opens the Electron window so it appears
        fresh once the new instance is fully ready (the
        ``VOICE_TYPER_RESTART`` env var is set by ``restart_app``
        before launching the new process). Then, when the user's
        preference is ``always_visible`` + ``bubble_show_on_startup``,
        shows the waveform bubble at startup and pushes the
        bubble-relevant config to the sandboxed bubble renderer.

        Emits the canonical ``[STARTUP] Startup complete (model still
        loading in background)`` log line with the C-LOG-2 duration
        suffix (anchored at ``self._t0`` set in :meth:`run`).
        """
        app = self._app
        # After restart: auto-open the Electron window so it appears fresh
        # once the new instance is fully ready.  The VOICE_TYPER_RESTART
        # env var is set by restart_app() before launching the new process.
        if os.environ.get("VOICE_TYPER_RESTART"):
            log.info("[STARTUP] Restart detected -- opening Electron window")
            try:
                app.tray.open_electron_window()
            except Exception as e:
                log.warning("[STARTUP] Failed to open Electron window after restart: %s", e)

        # Show the bubble at startup if always_visible mode is enabled AND
        # bubble_show_on_startup is True (user's preference in Settings).
        if app.config.bubble_behavior == "always_visible" and app.config.bubble_show_on_startup:
            try:
                app._waveform_bubble.show()
                log.info("[STARTUP] Bubble shown at startup (always_visible mode)")
            except Exception as e:
                log.warning("[STARTUP] Failed to show bubble at startup: %s", e)
            # push the bubble-relevant config (bubble_behavior
            # bubble_click_to_toggle / bubble_mic_button) so the bubble
            # renderer knows whether to show its mic button. The bubble
            # is sandboxed and cannot call get_config itself.
            try:
                cb = app._waveform_bubble.on_config
                if cb is not None:
                    cb(app.config)
            except Exception as e:
                log.debug("[STARTUP] Failed to push bubble config: %s", e)

        log.info(
            "[STARTUP] Startup complete (model still loading in background)%s",
            format_duration(time.perf_counter() - self._t0),
        )

        # Post-ready maintenance: the stale backup/``.tmp`` sweeps run on
        # a fire-and-forget daemon thread AFTER the ready line above —
        # nothing reads a swept directory before ready, so the sweeps
        # must not sit on the pre-ready critical path (they previously
        # ran synchronously inside phase 2). The config dir is resolved
        # via the ``app`` module attribute so tests that monkeypatch
        # ``voice_typer.server.app._config_dir`` (the ``tmp_config_dir``
        # fixture) are honored. The app's thread registry is passed
        # along so the sweep worker is spawned via ``spawn_and_register``
        # (mirroring the phase-1 vad-preload worker), shutdown gets a
        # clean bounded join; the worker stays fire-and-forget daemon.
        # Best-effort: the sweep helpers swallow per-file errors and the
        # dispatch is suppressed, so a sweep problem can never affect a
        # booted app.
        with contextlib.suppress(Exception):
            from voice_typer.server import app as _app_module
            from voice_typer.server.startup_sequence import _maintenance

            _maintenance._sweep_stale_files_after_ready(
                _app_module._config_dir(),
                getattr(app, "_thread_registry", None),
            )

        return StageResult(success=True)
