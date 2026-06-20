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
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional, Callable

import pystray
from PIL import Image

# ARCH-003: types extracted to tray_types.py; icon rendering to tray_icon.py
from voice_typer.server.tray_types import AppState, TrayController
from voice_typer.server.tray_icon import _get_dpi_aware_icon_size, _make_icon, _icon_cache
from voice_typer.server.tray_hotkey import format_hotkey_label
from voice_typer.server.tray_models import build_models_submenu_data

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

    def start(self, bg_work: Optional[Callable] = None) -> None:
        """Create the tray icon and start background work."""
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
        """Build the Phase 2 minimal tray menu with Models submenu."""
        if self._menu_cache_valid and self._cached_menu is not None:
            return self._cached_menu

        items = []
        hotkey = self._display_hotkey()

        items.append(
            pystray.MenuItem(
                f"Toggle Dictation ({hotkey})",
                self._wrap(self._controller.toggle_dictation),
                default=True,
            )
        )
        items.append(
            pystray.MenuItem(
                "Open App",
                self._wrap(self.open_electron_window),
            )
        )

        items.append(pystray.Menu.SEPARATOR)

        # Models submenu — only show downloaded models
        models_sub = self._build_models_submenu()
        items.append(pystray.MenuItem("Models", pystray.Menu(*models_sub)))

        items.append(pystray.Menu.SEPARATOR)

        # Restart
        items.append(pystray.MenuItem("Restart", self._wrap(self._controller.restart_app)))

        # Quit
        items.append(pystray.MenuItem("Quit", self._wrap(self._controller.quit_app)))

        result = tuple(items)
        self._cached_menu = result
        self._menu_cache_valid = True
        return result

    def _build_models_submenu(self) -> list:
        """Build a list of model MenuItems — only cached models + More models link.

        Data gathering is delegated to tray_models.build_models_submenu_data().
        UI glue (pystray MenuItem construction) remains here because it
        depends on the pystray API and the _wrap() helper.
        """
        from voice_typer.server.config import _config_dir

        items = []
        for name, downloaded, is_active, change_fn in build_models_submenu_data(
            _config_dir, self._controller.change_model
        ):
            if not downloaded:
                continue
            items.append(
                pystray.MenuItem(
                    f"{'• ' if is_active else '  '}{name}",
                    self._wrap(change_fn),
                )
            )

        items.append(pystray.Menu.SEPARATOR)
        items.append(
            pystray.MenuItem(
                "More models...",
                self._wrap(self.open_electron_window),
            )
        )
        return items

    def _display_hotkey(self) -> str:
        """Return the configured hotkey in a user-facing form.

        Delegates to tray_hotkey.format_hotkey_label() so the formatting
        logic is shared and testable without a TrayIcon instance.
        """
        hotkey = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        return format_hotkey_label(hotkey)

    @staticmethod
    def _wrap(fn):
        """Wrap callback so pystray doesn't break on extra args.

        RELIABILITY-001: previously this wrapper silently swallowed
        ``SystemExit``, which forced ``quit_app`` and ``restart_app``
        to use ``os._exit(0)`` to actually terminate the process.
        That bypassed Python cleanup (atexit, ``__del__``, ``finally``)
        and leaked the Win32 mutex, PortAudio handles, and
        ``RegisterHotKey`` registrations until the OS reaped them.

        We now log and re-raise ``SystemExit`` so the process can exit
        cleanly via the normal ``sys.exit(0)`` path.  ``self.quit()``
        (called by ``quit_app``) and ``restart_app`` both call
        ``self.tray.stop()`` before raising ``SystemExit``, which
        breaks the pystray event loop so ``_icon.run()`` returns and
        the main thread can exit.
        """
        log = logging.getLogger("voice_typer.server.tray")
        def wrapper(icon, item):
            try:
                fn()
            except SystemExit as _se:
                log.info("[TRAY] callback %r raised SystemExit(%s); re-raising",
                         getattr(fn, "__name__", "<lambda>"), _se.code)
                raise
        return wrapper


