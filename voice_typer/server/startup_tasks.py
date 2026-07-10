"""Extracted configuration-related startup methods from ``VoiceTyperApp``.

These functions were originally methods on ``VoiceTyperApp`` (in
``voice_typer/server/app.py``) and have been extracted into standalone
functions for testability. Each function takes ``app`` (the
``VoiceTyperApp`` instance) as its first parameter and accesses state via
``app.config``, ``app.tray``, ``app._microphones``, ``app._shutting_down``,
etc. — exactly the same attributes the original ``self.*`` references
resolved to.

The original methods on ``VoiceTyperApp`` are kept as thin delegates that
forward to these functions, so existing callers (and tests that
monkeypatch ``app._sync_autostart`` etc.) keep working unchanged.

A note on monkeypatching: tests like ``test_autostart_syncs_with_platform``
replace ``voice_typer.server.app.is_autostart_enabled`` /
``enable_autostart`` / ``disable_autostart`` / ``list_microphones`` at
call time. To keep those patches effective, the platform-helper names are
looked up dynamically from the ``voice_typer.server.app`` module inside
the relevant functions rather than being captured at import time.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from voice_typer.server import task_scheduler
from voice_typer.server.platform_utils import is_windows
from voice_typer.server.branding import APP_NAME
from voice_typer.server.server_platform import create_launcher_shortcut

log = logging.getLogger(__name__)


# PERF-FIX-2: cache the macOS ApplicationServices framework handle at
# module level instead of re-loading it every 60s in
# ``_check_accessibility``. ``ctypes.cdll.LoadLibrary`` is not free
# (it calls dlopen + resolves symbols), and the handle is safe to
# share across threads (the underlying C function ``AXIsProcessTrusted``
# is thread-safe). ``None`` means "not yet loaded" (or "load failed
# permanently" — in which case the pulse re-attempts on the first call
# of each process). The cache is best-effort: a permanent load failure
# (non-macOS, missing framework) leaves this as ``None`` and the pulse
# falls through to the "fail safe (assume not granted)" branch.
_APP_SERVICES_LIB: Optional[Any] = None
_APP_SERVICES_LIB_LOADED: bool = False


def sync_autostart(app: Any) -> None:
    """Ensure ``config.autostart`` matches the actual platform autostart state."""
    # Look up the platform helpers from the app module at call time so
    # tests that monkeypatch voice_typer.server.app.{is_autostart_enabled,
    # enable_autostart, disable_autostart} still take effect.
    from voice_typer.server import app as _app_module

    try:
        actual = _app_module.is_autostart_enabled()
        if app.config.autostart and not actual:
            log.info("[CONFIG] Config says autostart=true but it is disabled -- enabling")
            _app_module.enable_autostart()
        elif not app.config.autostart and actual:
            log.info("[CONFIG] Config says autostart=false but it is enabled -- disabling")
            _app_module.disable_autostart()
    except Exception as e:
        log.warning("[CONFIG] Autostart sync failed: %s", e)


def sync_prewarm_task(app: Any, shutdown_event: Optional[Any] = None) -> None:
    """Ensure the OS prewarm scheduled task is registered.

    fast_startup is always enabled (no user toggle). The prewarm
    task is registered at startup so the OS file cache is kept
    warm for fast cold-boot. Falls back gracefully if the platform
    doesn't support scheduled tasks (non-Windows).

    RACE-020: accepts an optional shutdown_event so the task can
    abort early if the app is quitting during startup.
    """
    if not task_scheduler.is_supported():
        return
    if shutdown_event is not None and shutdown_event.is_set():
        return
    try:
        registered = task_scheduler.is_prewarm_registered()
        if shutdown_event is not None and shutdown_event.is_set():
            return
        if not registered:
            log.info("[CONFIG] Registering prewarm scheduled task")
            task_scheduler.register_prewarm_task()
    except Exception as e:
        log.warning("[CONFIG] Prewarm task sync failed: %s", e)


def ensure_desktop_shortcut(app: Any) -> None:
    """Create the Desktop + Start Menu shortcuts on first run.

    Also migrates away the legacy backend-only ``Voice Typer.bat`` that
    pointed at ``pythonw -m voice_typer`` (which started the backend
    with no Electron, so the bubble overlay never worked).  That .bat
    is removed so the user is left with only the correct universal
    launcher shortcut.
    """
    if not is_windows():
        return
    desktop = Path.home() / "Desktop"
    lnk_path = desktop / "Voice Typer.lnk"
    legacy_bat = desktop / "Voice Typer.bat"

    # 1. Migrate: remove the legacy backend-only .bat so the broken
    #    "no bubble" shortcut stops shadowing the correct one.
    try:
        if legacy_bat.exists() and "-m voice_typer" in legacy_bat.read_text(
            encoding="utf-8", errors="replace"
        ):
            legacy_bat.unlink()
            log.info("[STARTUP] Removed legacy backend-only shortcut: %s", legacy_bat)
    except OSError:
        pass

    # 2. Ensure the universal-launcher shortcut exists.
    #    create_launcher_shortcut() skips .lnk files that already exist,
    #    so this is a no-op on subsequent startups.
    try:
        create_launcher_shortcut()
    except Exception as e:
        log.debug("[STARTUP] Desktop shortcut creation skipped: %s", e)


def load_microphones(app: Any, shutdown_event: Optional[Any] = None) -> None:
    """Enumerate microphones and update the tray menu.

    RACE-020: accepts an optional shutdown_event so the task can
    abort early if the app is quitting during startup.

    AUDIO-MIC: detects device changes by comparing the new list against
    the cached one. When the set of device IDs changes (USB mic
    plugged/unplugged), pushes a ``microphones_changed`` IPC event so
    the Electron renderer can refresh its microphone dropdown without
    a manual "Refresh" click. The comparison is done via ``old_ids``
    and ``new_ids`` sets.
    """
    # Look up list_microphones from the app module at call time so tests
    # that monkeypatch voice_typer.server.app.list_microphones still
    # take effect.
    from voice_typer.server import app as _app_module

    # RACE-020: abort early if shutting down
    if shutdown_event is not None and shutdown_event.is_set():
        return
    try:
        mics = _app_module.list_microphones()
        # AUDIO-MIC: detect device changes by comparing the new
        # list against the cached one. If the set of device IDs
        # changed (USB mic plugged/unplugged), notify the UI via
        # IPC push event so the Electron renderer can refresh its
        # microphone dropdown without a manual "Refresh" click.
        old_ids = {m["id"] for m in app._microphones} if app._microphones else set()
        new_ids = {m["id"] for m in mics}
        app._microphones = mics
        app.tray.set_microphones(mics)
        # Log INFO on first load or when device count changes.
        # Routine polls where nothing changed log nothing — the
        # microphones_changed IPC event handles UI updates.
        if not old_ids:
            log.info("[RECORDING] Found %d microphone(s)", len(mics))
        elif len(mics) != len(old_ids):
            log.info("[RECORDING] Microphone count changed: %d -> %d", len(old_ids), len(mics))
        # AUDIO-MIC: push a device-change IPC event if the device
        # set changed since the last enumeration.
        if old_ids and old_ids != new_ids:
            added = new_ids - old_ids
            removed = old_ids - new_ids
            log.info(
                "[AUDIO-MIC] Device set changed: +%d added, -%d removed",
                len(added),
                len(removed),
            )
            try:
                from voice_typer.server.ipc_server import _push_event_now

                _push_event_now(
                    {
                        "type": "microphones_changed",
                        "data": {"count": len(mics)},
                    }
                )
            except Exception:
                pass
    except Exception as e:
        log.warning("[RECORDING] Could not enumerate microphones: %s", e)


def start_device_change_poller(app: Any) -> None:
    """AUDIO-MIC: Start a background thread that periodically polls
    for audio device changes (every 30 seconds).

    Cross-platform fallback for the lack of WM_DEVICECHANGE (Windows),
    CoreAudio notifications (macOS), and PipeWire signals (Linux).
    When a device set change is detected, ``_load_microphones`` is
    called, which pushes a ``microphones_changed`` IPC event so the
    Electron UI can refresh its microphone dropdown.

    The poller is a daemon thread that exits when ``_shutting_down``
    is set. The 30-second interval is a trade-off between
    responsiveness (user plugs in a USB mic and waits for it to
    appear) and CPU cost (one ``sd.query_devices()`` call per
    poll, ~1-5 ms).
    """

    def _poll_loop() -> None:
        while not app._shutting_down:
            # Sleep in 1-second increments so we can exit quickly
            # when _shutting_down is set.
            for _ in range(30):
                if app._shutting_down:
                    return
                threading.Event().wait(1.0)
            if app._shutting_down:
                return
            try:
                # Re-enumerate; _load_microphones will detect
                # changes and push the IPC event.
                app._load_microphones()
            except Exception:
                log.debug("[AUDIO-MIC] Device-change poll failed", exc_info=True)

    t = threading.Thread(target=_poll_loop, daemon=True, name="AudioDevicePoller")
    # RACE-008: daemon=True is acceptable because the poller only
    # reads device state — no critical cleanup. On shutdown,
    # _shutting_down is set and the thread exits within 1 second.
    t.start()


def start_accessibility_pulse(app: Any, initial_state: bool) -> None:
    """PLAT-009: Periodically re-check macOS Accessibility permission.

    Runs on macOS only. Every 60 seconds, re-invokes
    ``AXIsProcessTrusted()`` and fires ``tray.notify_safety`` only
    on state transitions (granted→revoked or revoked→granted) so
    the user isn't spammed with repeated notifications.

    Pre-fix: accessibility was checked once at startup. If the user
    granted permission after startup, the app never recovered until
    restart. With this pulse, the app detects the change within 60s.

    PERF-FIX-2: two allocation patterns were cleaned up:

    - ``threading.Event().wait(1.0)`` in the 60-iteration sleep loop
      previously allocated a fresh ``Event`` object every second. We
      now create a single ``Event`` once and reuse it for the lifetime
      of the pulse thread.
    - ``ctypes.cdll.LoadLibrary(".../ApplicationServices")`` was called
      every 60s in ``_check_accessibility``. The handle is now cached
      at module level (``_APP_SERVICES_LIB``) and reused for the
      lifetime of the process — ``dlopen`` is idempotent on an already-
      loaded framework but still does a symbol-table lookup, which is
      wasted work on a 60s heartbeat.
    """

    def _check_accessibility() -> bool:
        """Return True if Accessibility permission is granted.

        PERF-FIX-2: uses the module-level cached ApplicationServices
        handle. The first call loads the framework; subsequent calls
        reuse the cached handle. A permanent load failure leaves the
        cache as ``None`` and this function returns False (fail safe).
        """
        global _APP_SERVICES_LIB, _APP_SERVICES_LIB_LOADED
        if not _APP_SERVICES_LIB_LOADED:
            try:
                import ctypes

                _APP_SERVICES_LIB = ctypes.cdll.LoadLibrary(
                    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                )
            except Exception:
                _APP_SERVICES_LIB = None
            finally:
                _APP_SERVICES_LIB_LOADED = True
        if _APP_SERVICES_LIB is None:
            return False  # fail safe (assume not granted)
        try:
            return bool(_APP_SERVICES_LIB.AXIsProcessTrusted())
        except Exception:
            return False  # fail safe (assume not granted)

    def _pulse_loop() -> None:
        # PERF-FIX-2: allocate ONE Event for the lifetime of the pulse
        # thread and reuse it. The previous code called
        # ``threading.Event().wait(1.0)`` in a 60-iteration loop, which
        # allocated a fresh Event object (and its underlying condition
        # variable + lock) every second — ~3.6k allocations/hour per
        # pulse thread.
        sleep_event = threading.Event()
        last_state = initial_state
        while not app._shutting_down:
            for _ in range(60):
                if app._shutting_down:
                    return
                sleep_event.wait(1.0)
            if app._shutting_down:
                return
            current = _check_accessibility()
            if current != last_state:
                if current:
                    log.info("[PLAT-009] macOS Accessibility permission granted")
                    try:
                        app.tray.notify(
                            APP_NAME,
                            "Accessibility permission granted. Hotkeys are now active.",
                        )
                    except Exception:
                        pass
                else:
                    log.warning("[PLAT-009] macOS Accessibility permission revoked")
                    try:
                        app.tray.notify_safety(
                            f"{APP_NAME} — Accessibility Revoked",
                            "Global hotkeys have been disabled. Open System Settings "
                            "\u2192 Privacy & Security \u2192 Accessibility to re-grant.",
                        )
                    except Exception:
                        pass
                last_state = current

    t = threading.Thread(target=_pulse_loop, daemon=True, name="A11yPulse")
    # RACE-008: daemon=True is acceptable — the pulse only reads
    # permission state, no critical cleanup. On shutdown, the thread
    # exits within 1 second.
    t.start()
