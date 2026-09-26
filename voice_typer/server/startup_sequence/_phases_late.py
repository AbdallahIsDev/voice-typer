"""Late startup phase steps."""

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
    from voice_typer.server.app import LausuApp

# pre-split ``voice_typer.server.startup_sequence`` logger (same
log = logging.getLogger("voice_typer.server.startup_sequence")


# Wayland warning state: module-level state holder for the Wayland warning
class _ModuleState:
    """Container for module-level mutable state (Wayland warning etc)."""

    wayland_warning_state: dict | None = None
    wayland_warned_version: str | None = None


_MODULE_STATE = _ModuleState()


class LatePhases:
    """Phases 5-8 of the startup sequence (mixin for ``StartupSequence``)."""

    # total-startup duration anchor set by ``run`` (C-LOG-2).
    _app: LausuApp
    _t0: float

    def _phase_5_platform_warnings(self) -> StageResult:
        """Emit Wayland + macOS accessibility platform warnings."""
        app = self._app
        # PLAT-WAYLAND: Warn if running on Wayland and
        if is_linux() and is_wayland_session():
            log.warning("[STARTUP] Wayland detected -- global hotkeys may not work")
            # check if wtype or ydotool is available as a fallback
            import shutil

            wtype_available = shutil.which("wtype") is not None
            ydotool_available = shutil.which("ydotool") is not None

            # Wayland warning state: structured state, re-warn whenever any field
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
                _MODULE_STATE.wayland_warning_state = {
                    **current_state,
                    "warned_at": datetime.now().isoformat(),
                }
                if _current_vt_version is not None:
                    _MODULE_STATE.wayland_warned_version = _current_vt_version
                # Backwards compat: keep setting the legacy boolean so
                app.config.wayland_warned = True
                app.config.save()

        # macOS accessibility permission check.
        _has_accessibility = False
        if is_macos():
            try:
                # Use AXIsProcessTrusted() via ctypes for the
                try:
                    import ctypes

                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    _has_accessibility = bool(app_services.AXIsProcessTrusted())
                except Exception as exc:
                    # drop the osascript fallback entirely. Treat
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
                # Promote debug→warning so the failure surfaces in
                log.warning("[STARTUP] macOS accessibility check failed", exc_info=True)

            # Start a periodic accessibility health monitor.
            from voice_typer.server import startup_tasks

            startup_tasks.start_accessibility_pulse(app, _has_accessibility)

        return StageResult(success=True)

    def _phase_6_autostart_prewarm_mics(self) -> StageResult:
        """``app.tray.set_microphones``). A final RACE-020 shutdown check"""
        app = self._app
        # 1. Sync autostart config with platform. OFF the critical
        from voice_typer.server import startup_tasks

        def _autostart_task() -> None:
            try:
                autostart_result = startup_tasks.sync_autostart(app)
            except Exception:
                log.warning("[STARTUP] autostart sync failed", exc_info=True)
                return
            # (a): sync_autostart returns a result dict whose
            if isinstance(autostart_result, dict) and "actual_post_sync" in autostart_result:
                autostart_enabled = bool(autostart_result["actual_post_sync"])
            else:
                # Test-stub fallback: the monkeypatched sync_autostart returned
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

        # 1b. The OS-level prewarm scheduled-task sync is gone:

        # exit) and the RACE-020 shutdown event so executor tasks can
        from voice_typer.server._timeout_utils import (
            TIMEOUT as _TIMEOUT_SENTINEL,
            _run_parallel_with_timeout,
        )

        # RACE-020: pass the shutdown event to executor tasks so they
        _shutdown_event = app._shutting_down_event if hasattr(app, "_shutting_down_event") else None

        # Runtime-pack split (§8.10, §8.16): launch-time offline-pack existence
        def _pack_check_task() -> None:
            # The concrete LausuApp exposes several AppProtocol
            from voice_typer.server.providers import AppProtocol as _AppProtocol

            startup_tasks.check_offline_pack_on_launch(cast(_AppProtocol, app), _shutdown_event)

        pack_thread = threading.Thread(
            target=_pack_check_task,
            name="startup-pack-check",
            daemon=True,
        )
        pack_thread.start()
        log.debug("[STARTUP] Pack existence check dispatched to fire-and-forget daemon thread (no wait, no timeout)")

        # ADR-0023: extractor freshness is CHECK-ONLY at launch (metadata
        # probe; installs stay user-initiated).
        def _extractor_refresh_task() -> None:
            from voice_typer.server.providers import AppProtocol as _AppProtocol

            startup_tasks.check_media_extractor_refresh(cast(_AppProtocol, app), _shutdown_event)

        extractor_thread = threading.Thread(
            target=_extractor_refresh_task,
            name="startup-extractor-refresh",
            daemon=True,
        )
        extractor_thread.start()
        log.debug("[STARTUP] Extractor refresh check dispatched to fire-and-forget daemon thread (no wait, no timeout)")

        # enumeration (below) runs in a bounded parallel pool under a 5s
        log.debug("[STARTUP] Registering hotkey")
        # Step 2: invoke HotkeyDispatcher directly. The
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
                pass
        # PERF-: the 30s ``sd.query_devices()`` device-change

        # 1b. Create desktop launcher shortcut on first run (if absent).
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
        """model fails to load (RACE-020 invariant: see package docstring
        (a)). A RACE-020 shutdown check after the load start aborts
        """
        app = self._app
        # Warmup handled synchronously in recording.py on first recording start.

        # 4. Create transcription engine and load model -- IN THE BACKGROUND.
        log.debug("[STARTUP] Loading model in background")
        # Root-cause reconciliation: if the configured model isn't
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
        """with the C-LOG-2 duration suffix (anchored at ``self._t0`` set"""
        app = self._app
        # After restart: auto-open the app window so it appears fresh
        if os.environ.get("VOICE_TYPER_RESTART"):
            log.info("[STARTUP] Restart detected -- opening app window")
            try:
                app.tray.open_app_window()
            except Exception as e:
                log.warning("[STARTUP] Failed to open app window after restart: %s", e)

        # Show the bubble at startup if always_visible mode is enabled AND
        if app.config.bubble_behavior == "always_visible" and app.config.bubble_show_on_startup:
            try:
                app._waveform_bubble.show()
                log.info("[STARTUP] Bubble shown at startup (always_visible mode)")
            except Exception as e:
                log.warning("[STARTUP] Failed to show bubble at startup: %s", e)
            # push the bubble-relevant config (bubble_behavior
            try:
                cb = app._waveform_bubble.on_config
                if cb is not None:
                    cb(app.config)
            except Exception as e:
                log.debug("[STARTUP] Failed to push bubble config: %s", e)

        # Honest completion line: the old static "(model still loading
        from voice_typer.server.i18n import t as _t

        model_size = getattr(getattr(app, "config", None), "model_size", "") or ""
        load_thread = getattr(getattr(app, "models", None), "_model_load_thread", None)
        loading = bool(getattr(load_thread, "is_alive", lambda: False)())
        if not model_size:
            complete_msg = "[STARTUP] Startup complete (no speech model selected)%s"
        elif loading:
            complete_msg = "[STARTUP] Startup complete (model still loading in background)%s"
        else:
            complete_msg = "[STARTUP] Startup complete%s"
        log.info(
            complete_msg,
            format_duration(time.perf_counter() - self._t0),
        )

        # Terminal tray reconcile (no-model hole): with no speech model
        if not model_size and not loading and not getattr(app, "_shutting_down", False):
            try:
                from voice_typer.server.tray_types import AppState as _AppState

                app.tray.set_state(
                    _AppState.ERROR,
                    _t("state.model_manager.no_model_selected"),
                )
            except Exception:
                log.debug(
                    "[STARTUP] terminal tray reconcile failed (non-fatal)",
                    exc_info=True,
                )

        # Post-ready maintenance: the stale backup/``.tmp`` sweeps run on
        with contextlib.suppress(Exception):
            from voice_typer.server import app as _app_module
            from voice_typer.server.startup_sequence import _maintenance

            _maintenance._sweep_stale_files_after_ready(
                _app_module._config_dir(),
                getattr(app, "_thread_registry", None),
            )

        return StageResult(success=True)
