"""System tray icon using pystray, with dynamic state and menu.

Phase 2: Minimal right-click menu:
- Toggle Dictation (hotkey)
- Models
- Restart
- Quit

Left-click + "Open App" launches the Electron app (or focuses it if already running).
All settings, history, templates, etc. live in the Electron window only.

CQ-004: This module is ~670 lines. It was considered for splitting but
kept as a single module because:
  - The TrayIcon class is a single cohesive unit (lifecycle + state + menu)
  - Splitting would create tight cross-file coupling (menu ↔ state ↔ notify)
  - The internal sections are clearly delineated with comment headers
  - Related logic (e.g. notification handling) stays together

Threading model:
- ``start()`` creates the icon and launches background work (model loading,
  hotkey registration, etc.) in a daemon thread.  It does NOT block.
- ``run()`` blocks the **main** thread with ``pystray.Icon.run()``.
  Call it from the main thread after ``start()``.
- State updates (icon, title, notifications) from the background thread are
  dispatched safely by pystray.
- Before ``run()`` starts, state / notification calls are queued and flushed
  once the event loop is live.
"""

import logging
import threading
from typing import Callable, Optional

import pystray

from voice_typer.server.tray_icon import _make_icon

# #13: menu building extracted to tray_menu.py (display_hotkey, wrap_callback,
# build_menu). tray.py now owns only pystray icon lifecycle + state queuing.
from voice_typer.server.tray_menu import build_menu, display_hotkey, wrap_callback

# ARCH-003: types extracted to tray_types.py; icon rendering to tray_icon.py
from voice_typer.server.tray_types import AppState, TrayController

# TRAY-008: Minimal localization dict for tray menu labels.
# Uses English as default. Wrap hardcoded strings with _() function.
_TRAY_LABELS: dict[str, str] = {
    "toggle_dictation": "Toggle Dictation",
    "open_app": "Open App",
    "models": "Models",
    "restart": "Restart",
    "quit": "Quit",
    "about": "About",
    "diagnostics": "Diagnostics",
    "show_last_notification": "Show Last Notification",
    "recording_active": "Recording active",
    "confirm_quit": "Quit while recording?",
    "confirm_quit_message": "A recording is in progress. Are you sure you want to quit?",
    "update_available": "Update Available",
    "version": "version",
}


def _(key: str) -> str:
    """TRAY-008: Return the localized tray label for the given key.

    Falls back to the key itself if not found. This mirrors the i18n
    approach used in the Electron frontend (i18n.ts). To add a new
    language, extend _TRAY_LABELS with a locale-prefixed dict and
    look up the current locale here.
    """
    return _TRAY_LABELS.get(key, key)


log = logging.getLogger(__name__)


# ARCH-003: types extracted to tray_types.py; icon rendering to tray_icon.py


class TrayIcon:
    """Cross-platform system tray icon with Phase 2 minimal menu."""

    def __init__(
        self,
        controller: TrayController,
        config=None,
    ) -> None:
        self._controller = controller
        self._config = config  # reference to live Config object

        self._icon: Optional[pystray.Icon] = None
        # ARCH-045: set to True if pystray.Icon() raised OSError at start()
        # so callers can decide to skip tray-related operations.
        self._tray_unavailable: bool = False
        self._state = AppState.IDLE
        self._message = ""
        self._notifications_enabled = True
        # NEW-CQ-008: _microphones cache removed — was write-only
        self._autostart_enabled = False

        # TRAY-025 / TRAY-035: Store the last notification text so the user
        # can re-display it via the tray menu. This works around the OS
        # limitation that notification duration is controlled by the OS, not
        # the app (TRAY-035), and the pystray limitation that drag-then-release
        # misses notifications (TRAY-025).
        self._last_notification_title: str = ""
        self._last_notification_message: str = ""

        # TRAY-015: Periodic update check state
        self._update_check_timer: Optional[threading.Timer] = None
        self._check_updates: bool = getattr(config, 'check_updates', True) if config else True

        # Pre-run state queue — flushed once pystray event loop is live
        self._pending_states: list[tuple[AppState, str]] = []
        self._pending_notifications: list[tuple[str, str]] = []
        self._queue_lock = threading.Lock()
        self._bg_work_fn: Optional[Callable] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._hotkey: str = getattr(config, 'hotkey', '<f2>') or '<f2>'

        # P4 #30: Menu cache
        self._cached_menu = None
        self._menu_cache_valid = False

    # ─── Public API ─────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        """Return the current tray application state.

        Returns:
            The current AppState enum value (e.g. IDLE, RECORDING, PROCESSING).
        """
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
        """Update tray icon state and tooltip.

        PERF-005: previously this invalidated the menu cache on every
        state change (recording start/stop), causing the full menu to
        be rebuilt.  The menu structure doesn't change on state
        transitions — only the icon does.  Menu cache is now only
        invalidated by explicit config changes (microphone list,
        autostart toggle, etc.).

        Args:
            state: The new AppState to set.
            message: Optional status message for the tray tooltip.
        """
        self._state = state
        self._message = message
        # PERF-005: don't invalidate menu cache on state change —
        # the icon is updated via _apply_state, not via menu rebuild.
        if self._icon:
            self._apply_state(state, message)
        else:
            with self._queue_lock:
                self._pending_states.append((state, message))

    def set_microphones(self, mics: list[dict]) -> None:
        """No-op: microphone cache removed (NEW-CQ-008).

        Args:
            mics: List of microphone device dicts (ignored).
        """
        pass

    def set_autostart_enabled(self, enabled: bool) -> None:
        """Update the cached autostart state.

        Args:
            enabled: Whether autostart is enabled.
        """
        self._autostart_enabled = enabled
        self._menu_cache_valid = False

    def set_notifications_enabled(self, enabled: bool) -> None:
        """Update the cached notifications state.

        Args:
            enabled: Whether notifications are enabled.
        """
        self._notifications_enabled = enabled
        self._menu_cache_valid = False

    def set_hotkey(self, hotkey: str) -> None:
        """Update the stored hotkey string for the next menu rebuild.

        Args:
            hotkey: The new hotkey string (e.g. '<f2>').
        """
        self._hotkey = hotkey
        self._menu_cache_valid = False

    @staticmethod
    def _is_linux_wayland_without_sni() -> bool:
        """NEW-XPLAT-002: detect Linux Wayland without StatusNotifierItem.

        Returns True if ALL of the following are true:
          1. We're on Linux (sys.platform starts with "linux").
          2. The session is Wayland (XDG_SESSION_TYPE=wayland).
          3. No StatusNotifierItem watcher is registered on the D-Bus
             session bus.

        Detection of (3) is best-effort: we try to call the
        ``org.kde.StatusNotifierWatcher`` service via D-Bus.  If the
        call fails (service unknown, bus unavailable, dbus module
        missing), we assume SNI is not available — which matches the
        user's complaint that "the tray silently fails" on Sway/Hyprland.

        We DON'T try to detect specific compositors by name (Sway,
        Hyprland, etc.) because new compositors appear regularly and
        the SNI-availability check is the actual contract that
        matters.
        """
        import sys
        import os
        if not sys.platform.startswith("linux"):
            return False
        if os.environ.get("XDG_SESSION_TYPE") != "wayland":
            return False
        # Try to detect the StatusNotifierItem watcher service on D-Bus.
        try:
            import dbus  # type: ignore[import-untyped]
        except ImportError:
            # No dbus module — we can't detect SNI programmatically.
            # Conservative: assume SNI is NOT available (matches the
            # user's complaint of "silent failure" on minimal Wayland
            # setups that typically don't have python-dbus installed).
            log.debug(
                "[TRAY] Wayland session detected but python-dbus not installed; "
                "assuming StatusNotifierItem is unavailable."
            )
            return True
        try:
            bus = dbus.SessionBus()
            # The SNI watcher is the well-known name registered by
            # the compositor's tray (e.g. waybar, swaync, KDE's
            # plasma-workspace).  If it's not registered, the
            # NameHasOwner call returns False.
            proxy = bus.get_object(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
            )
            has_owner = bool(proxy.NameHasOwner(
                "org.kde.StatusNotifierWatcher",
                dbus_interface="org.freedesktop.DBus",
            ))
            if not has_owner:
                log.info(
                    "[TRAY] Wayland session detected and org.kde.StatusNotifierWatcher "
                    "is NOT registered on the D-Bus session bus. Tray will be skipped."
                )
                return True
            return False
        except Exception as exc:
            log.debug(
                "[TRAY] D-Bus check for StatusNotifierItem failed: %s — "
                "assuming SNI is unavailable.", exc,
            )
            return True

    def refresh_config(self, config) -> None:
        """Replace the cached Config reference and rebuild the menu.

        ARCH-043: tray held a reference to the original ``Config`` instance.
        IPC handlers like ``set_config`` call ``setattr`` on the live object,
        but if the caller ever swapped the *instance* (e.g. by reloading
        from disk), the tray kept rendering the stale object's attributes.
        Now callers can hand us the fresh instance + invalidate the menu.
        """
        self._config = config
        # Mirror set_hotkey(): pick up the new hotkey string immediately so
        # the next menu rebuild reflects the user's current setting.
        self._hotkey = getattr(config, "hotkey", self._hotkey) or self._hotkey
        self._menu_cache_valid = False

    def start(self, bg_work: Optional[Callable] = None) -> None:
        """Create the tray icon and start background work.

        ARCH-045: pystray.Icon() can raise OSError on Windows Server /
        headless sessions without a system tray (no explorer.exe, RDP
        with /admin, etc.). Previously this crashed the background
        thread silently. We now catch the OSError, log it, and notify
        the user that the tray is unavailable — the app keeps running
        so the hotkey + IPC server still work.

        NEW-XPLAT-002: on Linux Wayland compositors that don't implement
        the StatusNotifierItem D-Bus interface (Sway, Hyprland, dwl,
        river, bare wlroots), pystray has no tray to attach to.  We
        detect this case proactively and skip tray creation rather
        than letting pystray hang on ``icon.run()``.  The app remains
        usable via hotkey + IPC + Electron window.
        """
        self._bg_work_fn = bg_work

        # NEW-XPLAT-002: Linux Wayland without StatusNotifierItem.
        # pystray's GTK backend hangs forever on `icon.run()` when
        # there's no StatusNotifierItem service to register with.
        # Detect this case and skip tray creation entirely.
        if self._is_linux_wayland_without_sni():
            log.warning(
                "[TRAY] Linux Wayland session without StatusNotifierItem detected "
                "(common on Sway/Hyprland/dwl/river). Tray icon will not be created. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            # Still start background work so the app boots normally.
            # MINOR FIX: previously this path assigned a *different*
            # attribute name from the one used everywhere else (the
            # canonical name is ``self._bg_thread``).  Neither name
            # is referenced in stop() today, so no runtime failure —
            # but if stop() is ever extended to join the background
            # thread, the Wayland path's thread would be orphaned.
            # Use the canonical name for consistency.
            if self._bg_work_fn:
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # Phase 2: Build minimal menu
        menu = pystray.Menu(self._build_menu)

        try:
            self._icon = pystray.Icon(
                name="voice-typer",
                icon=_make_icon(AppState.IDLE),
                title="Voice Typer",
                menu=menu,
            )
        except TypeError as e:
            raise RuntimeError(
                f"Failed to create tray icon (pystray Menu construction error): {e}"
            ) from e
        except OSError as e:
            # ARCH-045: headless / Windows Server / no-explorer sessions.
            log.warning(
                "[TRAY] Could not create system tray icon (no tray available?). "
                "Hotkey and IPC server will continue to work, but tray menu "
                "and notifications are disabled. Original error: %s",
                e,
            )
            self._icon = None
            self._tray_unavailable = True
            # Still start background work so the app boots normally.
            if self._bg_work_fn:
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # Start background work
        if self._bg_work_fn:
            self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
            self._bg_thread.start()

        log.info("[TRAY] Tray icon created, background work started")

    def run(self) -> None:
        """Block the main thread with pystray's event loop."""
        if self._icon is None:
            raise RuntimeError("call start() before run()")

        # Flush queued state
        with self._queue_lock:
            for state, msg in self._pending_states:
                self._apply_state(state, msg)
            self._pending_states.clear()

        with self._queue_lock:
            for title, message in self._pending_notifications:
                self._do_notify(title, message)
            self._pending_notifications.clear()

        log.info("[TRAY] Tray event loop starting (main thread)")
        self._icon.run()

    def stop(self) -> None:
        """Stop the tray icon and exit the event loop."""
        if self._icon:
            self._icon.stop()
            self._icon = None
        log.info("[SHUTDOWN] Tray icon stopped")

    def notify(self, title: str, message: str) -> None:
        """Show a notification if notifications are enabled.

        TRAY-025 / TRAY-035: Also stores the notification text so the
        user can re-display it via the tray menu.
        """
        self._last_notification_title = title
        self._last_notification_message = message
        if not self._notifications_enabled:
            return
        if self._icon:
            self._do_notify(title, message)
        else:
            with self._queue_lock:
                self._pending_notifications.append((title, message))

    # ─── Internals ──────────────────────────────────────────────────────

    def _apply_state(self, state: AppState, message: str) -> None:
        """Apply state to the live icon (safe from any thread)."""
        if not self._icon:
            return
        try:
            self._icon.icon = _make_icon(state)
        except OSError:
            # pystray Windows bug: DestroyIcon on stale handle (WinError 1402)
            # during rapid icon updates.  The stale handle prevents any future
            # icon updates from working, so clear it to let pystray re-create
            # the icon handle on the next call.
            if hasattr(self._icon, '_icon_handle'):
                self._icon._icon_handle = None
        title = "Voice Typer"
        if message:
            title += f" — {message}"
        elif state != AppState.IDLE:
            title += f" — {state.value}"
        # TRAY-022: Include model name and hotkey in tooltip
        if self._config:
            model = getattr(self._config, 'model_size', '')
            if model:
                title += f" [{model}]"
        hotkey = self._display_hotkey()
        if hotkey:
            title += f" ({hotkey})"
        self._icon.title = title

    def notify_safety(self, title: str, message: str) -> None:
        """Show a notification that bypasses the notification toggle.

        RACE-022: guard _pending_notifications append with _queue_lock
        to prevent race with the flush in run().
        """
        if self._icon:
            self._do_notify(title, message)
        else:
            with self._queue_lock:
                self._pending_notifications.append((title, message))

    def _do_notify(self, title: str, message: str) -> None:
        """Send a notification through the icon."""
        try:
            self._icon.notify(message, title)
        except Exception as e:
            log.warning("[TRAY] Notification failed: %s", e)

    @staticmethod
    def _bring_electron_to_front() -> bool:
        """#13: Delegates to tray_window.bring_electron_to_front()."""
        from voice_typer.server.tray_window import bring_electron_to_front
        return bring_electron_to_front()

    def open_electron_window(self) -> None:
        """Open (or focus) the Electron dashboard window.

        #13: Delegates to tray_window.open_electron_window() which handles
        TCP push, Win32 focus, and Electron launch in order.
        """
        from voice_typer.server.tray_window import open_electron_window as _open
        _open()

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale so it rebuilds on next right-click."""
        self._menu_cache_valid = False


    def _build_menu(self) -> tuple:
        """Build the Phase 2 minimal tray menu with Models submenu.

        #13: delegates to tray_menu.build_menu(). tray.py owns the
        cache (so it can invalidate on config change); tray_menu.py
        owns the menu structure.
        """
        if self._menu_cache_valid and self._cached_menu is not None:
            return self._cached_menu

        # BUGFIX: tray_left_click_action was never passed to build_menu,
        # so the tray always defaulted to toggle_dictation on left-click.
        left_click = getattr(self._config, "tray_left_click_action", "open_app") or "open_app"
        result = build_menu(
            hotkey=self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>",
            toggle_dictation=self._controller.toggle_dictation,
            open_app=self.open_electron_window,
            restart_app=self._controller.restart_app,
            quit_app=self._confirm_quit_while_recording,
            build_models_submenu=self._build_models_submenu,
            left_click_action=left_click,
            # TRAY-014: About and Diagnostics entries
            about_callback=self._show_about,
            diagnostics_callback=self._run_diagnostics,
            # TRAY-025 / TRAY-035: Re-show last notification
            show_last_notification_callback=self._show_last_notification,
            # TRAY-008: localization function
            localize=_,
        )
        self._cached_menu = result
        self._menu_cache_valid = True
        return result

    def _build_models_submenu(self) -> list:
        """Build a list of model MenuItems — only cached models + More models link.

        #13: Fully delegates to tray_models.build_models_menu_items().

        ARCH-037: previously the menu builder re-parsed config.json from
        disk, which is stale under rapid config updates. We now pass
        the in-memory Config object via a config_provider callable so
        the menu always reflects the live state.
        """
        from voice_typer.server.config import _config_dir
        from voice_typer.server.tray_models import build_models_menu_items
        # ARCH-037: pass a config provider that returns the live Config
        # instance, so the menu doesn't read stale config.json from disk.
        config_provider = getattr(self, "_config", None)
        return build_models_menu_items(
            _config_dir,
            self._controller.change_model,
            wrap_callback,  # use the shared wrapper from tray_menu
            self.open_electron_window,
            config_provider=config_provider,
        )

    def _display_hotkey(self) -> str:
        """Return the configured hotkey in a user-facing form.

        #13: delegates to tray_menu.display_hotkey() so the formatting
        logic is shared and testable without a TrayIcon instance.
        """
        hotkey = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        return display_hotkey(hotkey)

    # #13: _wrap moved to tray_menu.wrap_callback (shared helper).
    # Kept here as a static-method alias for backwards compatibility with
    # any code that calls TrayIcon._wrap(fn) directly.
    _wrap = staticmethod(wrap_callback)

    # ─── TRAY-003/028: Confirm quit while recording ─────────────────────

    def _confirm_quit_while_recording(self) -> None:
        """TRAY-003/028: When quit is selected while recording, show a
        confirmation dialog. If confirmed, stop recording then quit.
        """
        if self._state == AppState.RECORDING:
            # Try to show a confirmation dialog. On platforms where
            # messageboxes are available, use them. Otherwise just quit.
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                confirmed = messagebox.askyesno(
                    _("confirm_quit"),
                    _("confirm_quit_message"),
                    icon='warning',
                )
                root.destroy()
                if not confirmed:
                    return
            except Exception:
                # If tkinter is unavailable, proceed with quit
                log.info("[TRAY] Could not show confirmation dialog; proceeding with quit")
            # Stop recording first if still active
            try:
                self._controller.toggle_dictation()
            except Exception:
                pass
        self._controller.quit_app()

    # ─── TRAY-014: About and Diagnostics ────────────────────────────────

    def _show_about(self) -> None:
        """TRAY-014: Show About dialog with version info."""
        try:
            import tkinter as tk
            from tkinter import messagebox
            try:
                from voice_typer import __version__ as version
            except ImportError:
                version = "1.0.0"
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                f"About Voice Typer",
                f"Voice Typer {version}\n\nA background voice-to-text utility.\nhttps://github.com/AbdallahIsDev/voice-typer",
            )
            root.destroy()
        except Exception as e:
            log.warning("[TRAY] Could not show About dialog: %s", e)

    def _run_diagnostics(self) -> None:
        """TRAY-014: Trigger diagnostic bundle generation."""
        try:
            from voice_typer.server.crash_recovery import CrashRecovery
            recovery = CrashRecovery()
            path = recovery.create_diagnostic_bundle()
            if path:
                self.notify(_("diagnostics"), f"Diagnostic bundle saved: {path}")
            else:
                self.notify(_("diagnostics"), "Could not generate diagnostic bundle")
        except Exception as e:
            log.warning("[TRAY] Diagnostics failed: %s", e)
            self.notify(_("diagnostics"), f"Diagnostics error: {e}")

    # ─── TRAY-025 / TRAY-035: Re-show last notification ─────────────────

    def _show_last_notification(self) -> None:
        """TRAY-025 / TRAY-035: Re-display the last notification.

        Workaround for:
        - TRAY-025: pystray drag-then-release miss (pystray library
          limitation — notifications can be lost if the user drags
          the tray icon and releases. Adding a click handler that
          re-shows the last notification gives the user a way to
          recover the missed information.)
        - TRAY-035: notification duration is controlled by the OS,
          not the app. This is an OS limitation. The user can
          re-display the notification via this menu item.
        """
        if self._last_notification_title or self._last_notification_message:
            self._do_notify(self._last_notification_title, self._last_notification_message)
        else:
            self._do_notify("Voice Typer", "No recent notifications")

    # ─── TRAY-015: Periodic update check ────────────────────────────────

    def start_update_checker(self) -> None:
        """TRAY-015: Start a periodic update check (once per day).

        Compares the current version against the latest GitHub release.
        If a new version is available, shows a notification.
        Does NOT auto-download — just notifies. Config option
        ``check_updates`` (default True) controls whether this runs.
        """
        if not self._check_updates:
            return
        self._schedule_update_check()

    def _schedule_update_check(self) -> None:
        """Schedule the next update check (24 hours from now)."""
        if self._update_check_timer:
            self._update_check_timer.cancel()
        self._update_check_timer = threading.Timer(
            86400.0,  # 24 hours
            self._do_update_check,
        )
        self._update_check_timer.daemon = True
        self._update_check_timer.start()

    def _do_update_check(self) -> None:
        """Check GitHub for the latest release and notify if newer."""
        try:
            import urllib.request
            import json as _json
            url = "https://api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "voice-typer"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
            latest_tag = data.get("tag_name", "").lstrip("v")
            if not latest_tag:
                return
            try:
                from voice_typer import __version__ as current
            except ImportError:
                current = "1.0.0"
            if latest_tag != current:
                self.notify(
                    _("update_available"),
                    f"Voice Typer {latest_tag} is available (you have {current})",
                )
        except Exception as e:
            log.debug("[TRAY] Update check failed: %s", e)
        finally:
            # Schedule next check regardless of success/failure
            self._schedule_update_check()
