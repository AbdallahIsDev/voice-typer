"""System tray icon using pystray, with dynamic state and menu.

Phase 2: Minimal 3-item right-click menu:
- Toggle Dictation (hotkey)
- Restart
- Quit

Left-click opens Flet window.
All settings, history, templates, etc. live in the Flet window only.

Threading model:
- ``start()`` creates the icon and launches background work (model loading,
  hotkey registration, etc.) in a daemon thread.  It does NOT block.
- ``run()`` blocks the **main** thread with ``pystray.Icon.run()``.
  Call it from the main thread after ``start()``.
- State updates (icon, title, notifications) from the background thread are
  dispatched safely by pystray.
- Before ``run()`` starts, state / notification calls are queued and flushed
  once the event loop is live.

CQ-005: No tkinter imports — all dialogs moved to Flet window.
"""

import logging
import os
import sys
import threading
from enum import Enum
from typing import Optional, Callable, Protocol

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)


class AppState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    LOADING = "loading"
    ERROR = "error"
    PAUSED = "paused"
    WARMING_UP = "warming_up"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    CANCELLING = "cancelling"
    SETUP = "setup"
    NOT_CONFIGURED = "not_configured"


class TrayController(Protocol):
    """Protocol that the tray controller (typically VoiceTyperApp) must implement."""

    def toggle_dictation(self) -> None: ...
    def change_microphone(self, mic_id: str | None) -> None: ...
    def change_model(self, model: str) -> None: ...
    def change_hotkey(self, hotkey: str) -> None: ...
    def quit_app(self) -> None: ...
    def toggle_autostart(self) -> None: ...
    def set_notifications(self, enabled: bool) -> None: ...
    def set_hotkey(self, hotkey: str) -> None: ...
    def set_silence_warning_seconds(self, seconds: float) -> None: ...
    def set_silence_auto_stop_seconds(self, seconds: float) -> None: ...
    def set_max_recording_seconds(self, seconds: int) -> None: ...
    def create_desktop_shortcut(self) -> None: ...
    def restart_app(self) -> None: ...
    def repaste_last(self) -> None: ...


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

        # Flet window tracking (runs in a daemon thread; signal.signal stubbed
        # inside the thread to satisfy Flet's main-thread requirement)
        self._flet_thread: Optional[threading.Thread] = None

    # ─── Public API ─────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
        """Update tray icon state and tooltip."""
        self._state = state
        self._message = message
        self._menu_cache_valid = False
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

        # Add left-click handler to open Flet window
        self._icon.on_click = self._on_icon_click

        # Start background work
        if self._bg_work_fn:
            self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
            self._bg_thread.start()

        log.info("Tray icon created, background work started")

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

        log.info("Tray event loop starting (main thread)")
        self._icon.run()

    def stop(self) -> None:
        """Stop the tray icon and exit the event loop."""
        if self._icon:
            self._icon.stop()
            self._icon = None
        log.info("Tray icon stopped")

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
        self._icon.icon = _make_icon(state)
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
            log.warning("Notification failed: %s", e)

    def open_flet_window(self) -> None:
        """Open the Flet desktop window in a daemon thread.

        pystray blocks the main thread with its event loop; Flet is started in a
        daemon thread so the two UIs coexist in one process.  ``app_controller``
        (the main ``VoiceTyperApp`` instance) is passed by direct reference —
        no IPC, no serialization, no port allocation, no subprocess.

        Flet's event-loop bootstrap calls ``signal.signal(signal.SIGINT, ...)``
        which raises ``ValueError`` in any non-main thread.  We stub
        ``signal.signal`` for the lifetime of the Flet thread so the launch
        succeeds.  This is a no-op for Flet (it never fires on Windows desktop
        apps) and restores the real handler on thread exit.
        """
        if self._flet_thread is not None and self._flet_thread.is_alive():
            log.info("Flet window already open")
            return

        try:
            from voice_typer.ui.app import main

            def _run_flet():
                import signal as _signal
                _orig_signal = _signal.signal
                _signal.signal = lambda *a, **kw: None
                try:
                    main(app_controller=self._controller)
                finally:
                    _signal.signal = _orig_signal
                    log.info("Flet window closed")

            self._flet_thread = threading.Thread(
                target=_run_flet,
                daemon=True,
            )
            self._flet_thread.start()
            log.info("Flet window opened in daemon thread")
        except Exception as e:
            log.error("Failed to open Flet window: %s", e)

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale so it rebuilds on next right-click."""
        self._menu_cache_valid = False

    def _build_menu(self) -> tuple:
        """Build the Phase 2 minimal tray menu."""
        if self._menu_cache_valid and self._cached_menu is not None:
            return self._cached_menu

        items = []
        hotkey = self._display_hotkey()

        # Toggle dictation
        items.append(
            pystray.MenuItem(
                f"Toggle Dictation ({hotkey})",
                self._wrap(self._controller.toggle_dictation),
                default=True,
            )
        )

        # Settings (TRAY-002)
        items.append(pystray.MenuItem("Settings", self._wrap(self.open_flet_window)))

        items.append(pystray.Menu.SEPARATOR)

        # Repaste Last (Feature)
        items.append(pystray.MenuItem("Repaste Last", self._wrap(self._controller.repaste_last if hasattr(self._controller, 'repaste_last') else lambda: None)))

        items.append(pystray.Menu.SEPARATOR)

        # Restart
        items.append(pystray.MenuItem("Restart", self._wrap(self._controller.restart_app)))

        # Quit
        items.append(pystray.MenuItem("Quit", self._wrap(self._controller.quit_app)))

        result = tuple(items)
        self._cached_menu = result
        self._menu_cache_valid = True
        return result

    def _display_hotkey(self) -> str:
        """Return the configured hotkey in a user-facing form."""
        hotkey = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        return self._format_hotkey_label(hotkey)

    @staticmethod
    def _format_hotkey_label(hotkey: str) -> str:
        parts = []
        for part in hotkey.split("+"):
            clean = part.strip().strip("<>").lower()
            if clean == "ctrl":
                parts.append("Ctrl")
            elif clean == "alt":
                parts.append("Alt")
            elif clean == "shift":
                parts.append("Shift")
            elif clean in {"cmd", "win", "super"}:
                parts.append("Win")
            else:
                parts.append(clean.upper())
        return "+".join(parts)

    def _on_icon_click(self, icon, item):
        """Handle left-click on tray icon to open Flet window."""
        log.info("Tray icon left-clicked, opening Flet window")
        self.open_flet_window()

    @staticmethod
    def _wrap(fn):
        """Wrap callback so pystray doesn't break on extra args.

        Catches ``SystemExit`` raised by ``quit()`` / ``restart_app()``
        so pystray doesn't log it as an unhandled error.
        """
        def wrapper(icon, item):
            try:
                fn()
            except SystemExit:
                pass
        return wrapper


_icon_cache: dict = {}


def _get_dpi_aware_icon_size() -> int:
    """TRAY-020: Query DPI scaling and adjust icon size accordingly."""
    base_size = 64
    if sys.platform == "win32":
        try:
            import ctypes
            hdc = ctypes.windll.user32.GetDC(0)
            if hdc:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                if dpi > 96:
                    scale = dpi / 96.0
                    return int(base_size * scale)
        except Exception:
            pass
    return base_size


def _make_icon(state: AppState, size: int = 0) -> Image.Image:
    """Generate a colored microphone icon based on state.
    
    TRAY-020: If size is 0, auto-detect DPI scaling on Windows.
    """
    if size == 0:
        size = _get_dpi_aware_icon_size()
    cache_key = (state, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]
    colors = {
        AppState.IDLE: (120, 120, 120, 255),
        AppState.RECORDING: (235, 64, 52, 255),
        AppState.TRANSCRIBING: (52, 152, 219, 255),
        AppState.LOADING: (243, 156, 18, 255),
        AppState.ERROR: (231, 76, 60, 255),
        AppState.PAUSED: (155, 89, 182, 255),
        AppState.WARMING_UP: (230, 126, 34, 255),
        AppState.DOWNLOADING: (52, 73, 94, 255),
        AppState.PROCESSING: (22, 160, 133, 255),
        AppState.CANCELLING: (192, 57, 43, 255),
        AppState.SETUP: (41, 128, 185, 255),
        AppState.NOT_CONFIGURED: (149, 165, 166, 255),
    }
    color = colors.get(state, (120, 120, 120, 255))

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2

    # Microphone body (rounded rect)
    mic_w, mic_h = size // 5, size // 3
    draw.rounded_rectangle(
        [cx - mic_w, cy - mic_h, cx + mic_w, cy + mic_h // 3],
        radius=mic_w // 2,
        fill=color,
    )

    # Stand arc
    stand_radius = size // 3
    draw.arc(
        [cx - stand_radius, cy - stand_radius + mic_h // 4, cx + stand_radius, cy + stand_radius],
        start=0, end=180,
        fill=color, width=max(2, size // 20),
    )

    # Base line
    base_y = cy + stand_radius
    draw.line(
        [cx - stand_radius // 2, base_y, cx + stand_radius // 2, base_y],
        fill=color, width=max(2, size // 20),
    )

    _icon_cache[cache_key] = img
    return img
