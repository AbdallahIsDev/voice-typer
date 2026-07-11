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

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

# PERF-COLDSTART-001: lazy import — pystray's xorg backend calls
# Xlib.display.Display() at module import time, costing ~48 ms on the
# tray cold-start path and failing entirely without an X display
# (headless CI, bare Wayland). pystray is only used inside start() to
# build the Icon/Menu, so defer the real import to first attribute
# access. The proxy re-reads sys.modules on every access, so tests that
# monkeypatch voice_typer.server.tray.pystray (or sys.modules["pystray"])
# keep working unchanged. The ``from __future__ import annotations``
# above also stringifies the ``Optional[pystray.Icon]`` annotation in
# TrayIcon.__init__ so it no longer forces an eager import.
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_linux
from voice_typer.server.tray_icon import _make_icon

# #13: menu building extracted to tray_menu.py (display_hotkey, wrap_callback,
# build_menu). tray.py now owns only pystray icon lifecycle + state queuing.
from voice_typer.server.tray_menu import build_menu, display_hotkey, wrap_callback

# ARCH-003: types extracted to tray_types.py; icon rendering to tray_icon.py
from voice_typer.server.tray_types import AppState, TrayController

pystray = lazy_module("pystray")

# TRAY-008: Localization for tray menu labels.
# Uses English as default. Wrap hardcoded strings with _() function.
# To add a new language, extend _TRAY_LABELS_LOCALES with a new locale
# dict and call set_tray_locale('es') from the IPC layer when the user
# changes the UI language in Settings.
_TRAY_LABELS_EN: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Toggle Dictation",
    "open_app": "Open App",
    "models": "Models",
    "restart": "Restart",
    "quit": "Quit",
    "about": "About",  # kept for potential in-app use
    "diagnostics": "Diagnostics",  # kept for potential in-app use
    "recording_active": "Recording active",
    "update_available": "Update Available",
    "version": "version",
    "force_cancel_transcription": "Cancel Transcription",
}

# TRAY-008: Spanish translations (proof of concept for tray i18n).
_TRAY_LABELS_ES: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Alternar Dictado",
    "open_app": "Abrir Aplicación",
    "models": "Modelos",
    "restart": "Reiniciar",
    "quit": "Salir",
    "about": "Acerca de",  # kept for potential in-app use
    "diagnostics": "Diagnósticos",  # kept for potential in-app use
    "recording_active": "Grabación activa",
    "update_available": "Actualización Disponible",
    "version": "versión",
    "force_cancel_transcription": "Cancelar Transcripción",
}

# TRAY-008: locale → label dict. Add new locales here.
_TRAY_LABELS_LOCALES: dict[str, dict[str, str]] = {
    "en": _TRAY_LABELS_EN,
    "es": _TRAY_LABELS_ES,
}

# TRAY-008: current tray locale (defaults to English).
_tray_locale: str = "en"


def set_tray_locale(locale: str) -> None:
    """TRAY-008: Set the tray menu locale.

    Called by the IPC layer when the user changes the UI language in
    Settings. Falls back to English if the locale is not supported.
    After calling this, the tray menu must be rebuilt for the new
    labels to take effect.
    """
    global _tray_locale
    _tray_locale = locale if locale in _TRAY_LABELS_LOCALES else "en"


def get_tray_locale() -> str:
    """TRAY-008: Return the current tray locale."""
    return _tray_locale


def _(key: str) -> str:
    """TRAY-008: Return the localized tray label for the given key.

    Looks up the key in the current locale's label dict, falling back
    to English, then to the key itself. This mirrors the i18n approach
    used in the Electron frontend (i18n.ts).
    """
    labels = _TRAY_LABELS_LOCALES.get(_tray_locale, _TRAY_LABELS_EN)
    if key in labels:
        return labels[key]
    # Fall back to English
    if key in _TRAY_LABELS_EN:
        return _TRAY_LABELS_EN[key]
    # Last resort: return the key itself
    return key


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

        self._icon: pystray.Icon | None = None
        # ARCH-045: set to True if pystray.Icon() raised OSError at start()
        # so callers can decide to skip tray-related operations.
        self._tray_unavailable: bool = False
        self._state = AppState.IDLE
        self._message = ""
        self._notifications_enabled = True
        # NEW-CQ-008: _microphones cache removed — was write-only
        self._autostart_enabled = False

    # TRAY-015: Periodic update check state
        self._update_check_timer: threading.Timer | None = None
        self._check_updates: bool = getattr(config, 'check_updates', True) if config else True

        # Pre-run state queue — flushed once pystray event loop is live
        self._pending_states: list[tuple[AppState, str]] = []
        self._pending_notifications: list[tuple[str, str]] = []
        self._queue_lock = threading.Lock()
        self._bg_work_fn: Callable | None = None
        self._bg_thread: threading.Thread | None = None
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
        import os
        if not is_linux():
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

    def start(self, bg_work: Callable | None = None) -> None:
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
                # RACE-008: daemon=True is acceptable because bg_work
                # only does model preloading/hotkey registration — no
                # critical cleanup. The tray's stop() method joins with
                # timeout; on force-kill the OS reclaims the thread.
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # Phase 2: Build minimal menu
        menu = pystray.Menu(self._build_menu)

        try:
            self._icon = pystray.Icon(
                name="voice-typer",
                icon=_make_icon(AppState.IDLE),
                # PLAT-010: title serves as both tooltip AND accessible
                # name for screen readers. pystray does not expose a
                # separate accessible_name parameter — title is the
                # canonical way to set the a11y label on all backends
                # (Windows: NIF_TIP, macOS: accessibilityLabel, Linux:
                # AppIndicator tooltip). The title is kept non-empty
                # and localized via _() so screen readers announce the
                # app name correctly.
                title=_("app_name"),
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
                # RACE-008: daemon=True — see comment at the canonical
                # bg_thread spawn site above.
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # Start background work
        if self._bg_work_fn:
            # RACE-008: daemon=True — see comment at the canonical
            # bg_thread spawn site above.
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

        TRAY-025 / TRAY-035: (removed) Notification re-display was
        previously stored and accessible via the tray menu; that menu
        item has been removed since the OS manages notification
        lifetime.
        """
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
        title = APP_NAME
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
        """Mark the menu cache as stale so it rebuilds on next right-click.

        ARCH-049: on Windows, pystray's ``_on_notify`` displays the menu via
        ``TrackPopupMenuEx`` with the STORED ``HMENU`` handle — it does NOT
        re-call the ``_build_menu`` callback on subsequent right-clicks because
        ``_update_menu()`` is only called during icon creation.  We must force
        pystray to rebuild its Win32 menu handle by calling
        ``_icon._update_menu()`` here, which triggers the ``_build_menu``
        callback and reads the latest config values.

        Thread safety: ``_update_menu()`` calls ``DestroyMenu`` /
        ``CreatePopupMenu`` / ``InsertMenuItem`` — all Win32 API calls that
        are thread-safe.  The old handle is destroyed and replaced atomically
        (CPython GIL protects the tuple assignment).  A concurrent right-click
        during the brief rebuild window simply shows the previous menu or
        nothing — the user can right-click again.
        """
        self._menu_cache_valid = False
        # ARCH-049: force pystray to rebuild its Win32 menu handle so the
        # next right-click reflects the current config state.
        if self._icon is not None:
            try:
                self._icon._update_menu()
            except Exception:
                log.debug("[TRAY] _icon._update_menu() failed", exc_info=True)


    def _build_menu(self) -> tuple:
        """Build the minimal tray menu with Models submenu.

        #13: delegates to tray_menu.build_menu(). tray.py owns the
        cache (so it can invalidate on config change); tray_menu.py
        owns the menu structure.

        Menu structure:
          - Open App (default/bold)
          - Toggle Dictation
          - --- separator ---
          - Models ▸
          - --- separator ---
          - Restart
          - Quit

        About, Diagnostics, and Show Last Notification have been
        removed from the tray menu (they remain available in the
        Electron app).
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
            # PR-2 Finding #3: manual escape hatch for stuck transcription.
            # Invokes _force_recover_from_stuck_transcription(force=True)
            # via the app's delegate method.  Safe to call when
            # transcription is not stuck (no-op).
            force_cancel_transcription=lambda: self._controller.recording._force_recover_from_stuck_transcription(
                force=True
            ),
            restart_app=self._controller.restart_app,
            quit_app=self._confirm_quit_while_recording,
            build_models_submenu=self._build_models_submenu,
            left_click_action=left_click,
            # TRAY-008: localization function
            localize=_,
        )
        self._cached_menu = result
        self._menu_cache_valid = True
        return result

    def _open_models_page(self) -> None:
        """Open the Electron window and navigate to the Models page.

        Called from the tray menu's "More models..." item. Opens/focuses
        the Electron window (same as open_electron_window) and sends a
        ``navigate`` push event so the renderer navigates to the Models
        page instead of staying on whatever page was last open.
        """
        # 1. Open/focus the Electron window
        from voice_typer.server.tray_window import open_electron_window as _open
        _open()

        # 2. Push a navigate event so the renderer navigates to /models
        from voice_typer.server.ipc_server import _push_event_now
        try:
            _push_event_now({"type": "navigate", "data": {"path": "/models"}})
            log.info("[TRAY] Navigate-to-models push sent to Electron")
        except Exception as e:
            log.warning("[TRAY] Failed to push navigate-to-models event: %s", e)

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
            self._open_models_page,  # use models-page callback (opens + navigates)
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

    def _confirm_quit_while_recording(self) -> None:
        """Quit immediately, regardless of recording state.

        The old confirmation dialog was removed because crash recovery
        already protects in-flight transcriptions, and quit_app()
        handles discarding active recordings and waiting for
        transcription to finish (with timeout).
        """
        self._controller.quit_app()

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
            import json as _json
            import urllib.request
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
                    f"{APP_NAME} {latest_tag} is available (you have {current})",
                )
        except Exception as e:
            log.debug("[TRAY] Update check failed: %s", e)
        finally:
            # Schedule next check regardless of success/failure
            self._schedule_update_check()
