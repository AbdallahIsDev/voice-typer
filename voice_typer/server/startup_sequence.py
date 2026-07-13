"""Startup sequence orchestration for VoiceTyperApp.

RW-9 Phase 5: extracted from ``VoiceTyperApp._do_startup`` (~340 lines)
to reduce the god-class size. Each phase is gated by
``app._shutting_down`` so a ``quit()`` during startup short-circuits
cleanly.

Phase ordering is FIXED — see the dependency graph in worklog.md.
Reordering risks:

(a) Hotkey registration before model load means F2 works even if the
    model fails to load — without this, a model-load failure leaves the
    user with no way to interact with the app.
(b) Mic enumeration before hotkey registration means the tray menu has
    mics available when the hotkey is bound (the menu is built lazily
    on first show, but the mic list is captured at startup).
(c) Onboarding auto-heal must run before any ``config.save()`` to avoid
    clobbering user settings — the wizard's ``apply_settings()`` overwrites
    the user's hotkey, model, and microphone selections with onboarding
    defaults (``<caps_lock>``, ``small.en``, ``None``).

The class does NOT import ``app.py`` at module load (would create an
import cycle: ``app`` imports ``startup_sequence`` indirectly via the
``StartupSequence(self)`` call inside ``_do_startup``).  The runtime
import is local to ``_do_startup`` itself, so this module never
appears in ``app.py``'s import-time graph.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import _config_dir
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows
from voice_typer.server.server_platform import is_autostart_enabled
from voice_typer.server.text_cleanup import configure_corrections

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle described in the module
    # docstring.  At runtime, ``app`` is whatever object was passed to
    # ``__init__`` (always a ``VoiceTyperApp`` in production, but tests
    # pass mocks that satisfy the same duck-typed surface).
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class StartupSequence:
    """Orchestrates the multi-phase background startup of VoiceTyperApp.

    The previous monolithic ``VoiceTyperApp._do_startup`` (~340 lines)
    is now ``StartupSequence(app).run()``.  ``app`` is a back-reference
    so the sequence can read/write the app's state (config, tray, models,
    hotkeys, etc.) — same attribute surface as before, just renamed
    from ``self.X`` to ``self._app.X``.
    """

    def __init__(self, app: VoiceTyperApp) -> None:
        self._app = app

    def run(self) -> None:
        """Top-level entry — equivalent to the old ``_do_startup`` body.

        RACE-020: checks ``self._app._shutting_down`` between each major
        step so that a ``quit()`` call during startup doesn't proceed
        with model downloads or background loads after the app has
        begun shutdown.
        """
        app = self._app
        log.info("[STARTUP] Initializing: autostart, microphones, hotkey, model…")

        if app._shutting_down:
            log.info("[STARTUP] _shutting_down is set, aborting startup")
            return

        # #8: Onboarding wizard — detect first run and let the React UI
        # show the wizard. Previously this auto-applied defaults and
        # marked onboarding complete, which prevented the wizard from
        # ever appearing (the 275-line Onboarding.tsx was dead code).
        # Now we just save the config with onboarding_completed=False
        # so the frontend can detect first-run via the
        # `onboarding_is_first_run` IPC route and route the user to
        # the wizard. The wizard's apply/skip handler flips the flag
        # to True and marks the .onboarding_complete marker.
        #
        # ONBOARDING-STALE-FIX: When config.json already exists on disk
        # (the user has been using the app) but onboarding_completed is
        # False and the .onboarding_complete marker is missing, we
        # auto-heal the state. Without this fix, is_first_run() returns
        # True on every restart, the frontend routes to the onboarding
        # wizard, and the wizard's apply_settings() overwrites the
        # user's custom hotkey, model, and microphone settings with
        # onboarding defaults (<caps_lock>, small.en, None).
        if not app.config.onboarding_completed:
            try:
                from voice_typer.server.onboarding import OnboardingController

                onboarding = OnboardingController()
                if onboarding.is_first_run():
                    # Check if config.json already exists on disk.
                    # If it does, this is NOT a genuine first install
                    # but a stale onboarding state where the marker was
                    # lost/deleted and onboarding_completed was never
                    # flipped to True. Auto-heal by marking onboarding
                    # complete to prevent the wizard from showing on
                    # every restart and overwriting the user's settings.
                    config_file = _config_dir() / "config.json"
                    if config_file.exists():
                        log.info(
                            "[STARTUP] Config file exists but onboarding "
                            "flag is False and marker is missing -- "
                            "fixing stale onboarding state to prevent "
                            "wizard from overwriting user settings"
                        )
                        app.config.onboarding_completed = True
                        onboarding.mark_complete()
                        app.config.save()
                    else:
                        # Genuine first run -- no config.json exists yet.
                        # Save the default config so the frontend can
                        # detect first-run and show the wizard.
                        log.info(
                            "[STARTUP] First run detected -- deferring to React "
                            "onboarding wizard (config.onboarding_completed=False)"
                        )
                        app.config.save()
            except Exception as e:
                # ERR-010: previously this was log.debug, which is
                # invisible at default log levels. If onboarding check
                # persistently fails the user is stuck on first-run
                # forever with no indication of why. Promote to
                # log.exception and notify the tray; after N consecutive
                # failures we mark onboarding completed with a failure
                # flag so the app remains usable.
                log.exception("[STARTUP] Onboarding check failed: %s", e)
                try:
                    app._onboarding_fail_count = getattr(app, "_onboarding_fail_count", 0) + 1
                    if app._onboarding_fail_count >= 3:
                        app.config.onboarding_completed = True
                        app.config.onboarding_failed = True
                        try:
                            app.config.save()
                        except Exception:
                            log.exception("[STARTUP] Could not save onboarding_failed flag")
                        # NEW-UX-018: critical — bypass show_notifications toggle.
                        with contextlib.suppress(Exception):
                            app.tray.notify_safety(
                                APP_NAME,
                                "Onboarding setup kept failing. The app will "
                                "start with default settings. Open Settings to "
                                "configure manually.",
                            )
                    elif app.config.show_notifications:
                        with contextlib.suppress(Exception):
                            app.tray.notify(
                                APP_NAME,
                                "Onboarding setup failed; will retry on next start.",
                            )
                except Exception:
                    log.exception("[STARTUP] Onboarding failure-handler itself failed")

        # Load external text corrections (if available) before any transcription
        # ARCH-004: surface load errors to the user via tray notification
        # so they know why their corrections aren't taking effect.
        try:
            err = configure_corrections(config_dir=app.config.config_dir)
            if err is not None:
                # NEW-UX-018: critical — bypass toggle (broken corrections file).
                try:
                    app.tray.notify_safety(
                        f"{APP_NAME} — Corrections Error",
                        f"{err}\nCorrections will use built-in defaults. Fix the file and restart.",
                    )
                except Exception:
                    log.debug("[STARTUP] Could not show corrections error notification")
        except Exception:
            log.debug("[STARTUP] External corrections load failed, using built-in defaults")

        # P2: Crash recovery -- check for unpasted transcriptions
        if app.config.crash_recovery_enabled:
            try:
                unpasted = app._crash_recovery.check_on_startup()
                if unpasted:
                    log.info("[STARTUP] Found %d unpasted transcriptions from previous session", len(unpasted))
                    # NEW-UX-018: critical — bypass toggle (recovered user data).
                    app.tray.notify_safety(
                        APP_NAME,
                        f"Recovered {len(unpasted)} transcription(s) from last session. Open History to view.",
                    )
            except Exception:
                log.debug("[STARTUP] Crash recovery check failed")

        # DEAD-012: apply history retention policy at startup.
        # Previously the config keys were saved but never read.
        try:
            app.history_db.apply_retention(
                retention_days=app.config.history_retention_days,
                max_entries=app.config.history_max_entries,
                retention_count=app.config.history_retention_count,
            )
        except Exception:
            log.debug("[STARTUP] History retention apply failed")

        # PLAT-WAYLAND / XPLAT-004: Warn if running on Wayland and
        # suggest wtype/ydotool as fallback for global hotkeys.
        if is_linux() and os.environ.get("XDG_SESSION_TYPE") == "wayland" and not app.config.wayland_warned:
            log.warning("[STARTUP] Wayland detected -- global hotkeys may not work")
            # XPLAT-004: check if wtype or ydotool is available as a fallback
            import shutil

            wtype_available = shutil.which("wtype") is not None
            ydotool_available = shutil.which("ydotool") is not None
            if not wtype_available and not ydotool_available:
                log.warning(
                    "[STARTUP] Neither wtype nor ydotool found. "
                    "Install one for hotkey support on Wayland: "
                    "'sudo apt install wtype' or 'sudo apt install ydotool'"
                )
                # NEW-UX-018: critical — bypass toggle (hotkeys broken).
                app.tray.notify_safety(
                    f"{APP_NAME} — Wayland Hotkeys",
                    "Global hotkeys may not work on Wayland. "
                    "Install 'wtype' or 'ydotool' for hotkey support, "
                    "or use the tray menu's Toggle Dictation option.",
                )
            else:
                log.info(
                    "[STARTUP] Wayland hotkey fallback available: %s",
                    "wtype" if wtype_available else "ydotool",
                )
            app.config.wayland_warned = True
            app.config.save()

        # XPLAT-002 / PLAT-030: macOS accessibility permission check.
        # On macOS, global hotkeys require Accessibility permission.
        # The app can't request it directly, but we can detect it's
        # missing and notify the user.
        _has_accessibility = False
        if is_macos():
            try:
                import subprocess as _sp

                # PLAT-030: Use AXIsProcessTrusted() via ctypes for the
                # definitive check.  AXIsProcessTrusted() is the official
                # API — it returns True iff the process has Accessibility
                # permission.  We load it from ApplicationServices.framework
                # via ctypes (no PyObjC dependency required).
                try:
                    import ctypes

                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    _has_accessibility = bool(app_services.AXIsProcessTrusted())
                except Exception:
                    # Fallback: osascript check (less reliable but works
                    # even if ctypes loading fails)
                    result = _sp.run(
                        ["osascript", "-e", 'tell application "System Events" to keystroke " "'],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    _has_accessibility = result.returncode == 0

                if not _has_accessibility:
                    log.warning("[STARTUP] macOS Accessibility permission not granted")
                    # NEW-UX-018: critical — bypass toggle (hotkeys broken).
                    app.tray.notify_safety(
                        f"{APP_NAME} — Accessibility Permission",
                        "Global hotkeys require Accessibility permission. "
                        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility "
                        f"and add {APP_NAME} (or Terminal).",
                    )
            except Exception:
                log.debug("[STARTUP] macOS accessibility check failed")

            # PLAT-009: Start a periodic accessibility health monitor.
            # If the user grants permission AFTER startup, the app will
            # detect it within 60 seconds and clear the warning. If the
            # user revokes permission mid-session, the app will re-warn.
            # RW-9 Phase 2: call startup_tasks directly (the
            # ``app._start_accessibility_pulse`` facade is kept for test seams).
            from voice_typer.server import startup_tasks

            startup_tasks.start_accessibility_pulse(app, _has_accessibility)

        # 1. Sync autostart config with platform
        log.debug("[STARTUP] Syncing autostart")
        # RW-9 Phase 2: call startup_tasks directly (the
        # ``app._sync_autostart`` facade is kept for test seams).
        from voice_typer.server import startup_tasks

        startup_tasks.sync_autostart(app)
        app.tray.set_autostart_enabled(is_autostart_enabled())

        # RACE-020: check for shutdown after each major step
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after autostart sync")
            return

        # 1b. Sync the OS-level prewarm scheduled task.
        #     fast_startup is always enabled; the prewarm task is registered
        #     at startup so the OS file cache is kept warm.  Cheap (a single
        #     schtasks /Query) and self-healing: if the user deleted the task
        #     or moved machines, it gets re-registered.
        #
        # PERF-NEW-030: prewarm sync + mic enumeration are independent
        # I/O-bound tasks. Run them in parallel on a ThreadPoolExecutor
        # so the total startup time is max(t_prewarm, t_mics) instead
        # of t_prewarm + t_mics.
        import concurrent.futures

        # RACE-020: pass the shutdown event to executor tasks so they
        # can abort early if the app is quitting during startup.
        _shutdown_event = app._shutting_down_event if hasattr(app, "_shutting_down_event") else None

        # PW-2: log the trigger regime that will be registered, so
        # operators can verify from the app-start logs which triggers
        # are in effect.  On Windows the XML task uses BootTrigger +
        # EventTrigger (both system-start), and the Run-key fallback
        # fires at logon.  On POSIX, prewarm_scheduler_posix uses
        # RunAtLoad (macOS) or OnBootSec (Linux).
        _triggers = (
            "boot + event (Task Scheduler XML)" if is_windows()
            else "logon (Run-key fallback) or OnBootSec/RunAtLoad (POSIX)"
        )
        log.info(
            "[STARTUP] Syncing prewarm task (triggers: %s)", _triggers,
        )

        def _startup_parallel_work() -> None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                # RW-9 Phase 2: call startup_tasks directly (the
                # ``app._sync_prewarm_task`` / ``app._load_microphones``
                # facades are kept for test seams).
                prewarm_future = pool.submit(startup_tasks.sync_prewarm_task, app, _shutdown_event)
                mic_future = pool.submit(startup_tasks.load_microphones, app, _shutdown_event)
                # RACE-020: reduced timeout from 30s to 10s so a stuck
                # task doesn't block the entire startup sequence.
                for label, fut in [("prewarm", prewarm_future), ("mic", mic_future)]:
                    try:
                        fut.result(timeout=10)
                    except Exception as exc:
                        log.warning("[STARTUP] %s task failed: %s", label, exc)
            # PERF-FIX-2: the 30s ``sd.query_devices()`` device-change
            # poller (``_start_device_change_poller``) was removed from
            # startup because it is fully redundant with the
            # event-driven ``MicrophoneDeviceWatcher`` started in
            # ``Recorder.__init__`` (WM_DEVICECHANGE on Windows,
            # ``/dev/snd`` polling on Linux, CoreAudio property-listener
            # on macOS). The watcher is the sole source of truth; the
            # 30s poller was a defence-in-depth fallback that cost
            # ~1-5ms of CPU every 30s and allocated a fresh
            # ``threading.Event()`` object every second. RW-9 Phase 1
            # also deleted the now-orphaned ``_start_device_change_poller``
            # delegate from this class — see test_bugfix_regressions.py
            # ``TestAudioMicDeviceChangePoller`` for the full history.

        # 1b. Create desktop launcher shortcut on first run (if absent)
        # (Run before parallel work so the shortcut exists before mic
        # enumeration — they're independent but shortcut creation is
        # fast and quick to fail.)
        # RW-9 Phase 2: call startup_tasks directly.
        startup_tasks.ensure_desktop_shortcut(app)

        log.debug("[STARTUP] Running prewarm sync + mic enumeration")
        _startup_parallel_work()

        # RACE-020: check for shutdown after parallel work
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted before hotkey registration")
            return

        # 3. Register hotkey BEFORE model load so F2 works even if model fails
        log.debug("[STARTUP] Registering hotkey")
        # RW-9 Phase 2: call HotkeyDispatcher directly (the
        # ``app._register_hotkey`` facade is kept for test seams).
        app.hotkeys.register()

        # RACE-020: check for shutdown after hotkey registration
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after hotkey registration")
            return

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
        app.models.start_background_load()

        # RACE-020: check for shutdown after background model load start
        if app._shutting_down:
            log.debug("[STARTUP] Interrupted after model load start")
            return

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

        log.info("[STARTUP] Startup complete, model still loading in background")
