"""System tray icon using pystray, with dynamic state and menu.

Phase 2: Minimal right-click menu:
- Toggle Dictation (hotkey)
- Models
- Restart
- Quit

Left-click + "Open App" launches the Electron app (or focuses it if already running).
All settings, history, templates, etc. live in the Electron window only.

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
        self._microphones: list[dict] = []  # populated by app
        self._autostart_enabled = False

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
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
        """Update tray icon state and tooltip.

        PERF-005: previously this invalidated the menu cache on every
        state change (recording start/stop), causing the full menu to
        be rebuilt.  The menu structure doesn't change on state
        transitions — only the icon does.  Menu cache is now only
        invalidated by explicit config changes (microphone list,
        autostart toggle, etc.).
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
        """Update the cached microphone list."""
        self._microphones = mics
        self._menu_cache_valid = False

    def set_autostart_enabled(self, enabled: bool) -> None:
        """Update the cached autostart state."""
        self._autostart_enabled = enabled
        self._menu_cache_valid = False

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._notifications_enabled = enabled
        self._menu_cache_valid = False

    def set_hotkey(self, hotkey: str) -> None:
        """Update the stored hotkey string for the next menu rebuild."""
        self._hotkey = hotkey
        self._menu_cache_valid = False

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
        """
        self._bg_work_fn = bg_work

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
        """Show a notification if notifications are enabled."""
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
        """Show a notification that bypasses the notification toggle."""
        if self._icon:
            self._do_notify(title, message)
        else:
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

        result = build_menu(
            hotkey=self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>",
            toggle_dictation=self._controller.toggle_dictation,
            open_app=self.open_electron_window,
            restart_app=self._controller.restart_app,
            quit_app=self._controller.quit_app,
            build_models_submenu=self._build_models_submenu,
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
