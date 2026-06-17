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
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, Protocol

import pystray
from PIL import Image

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
        """Find an existing Voice Typer Electron window and bring it to front.

        Returns True if a window was found and focused, False otherwise.
        Uses Win32 EnumWindows to search by window title.
        """
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            found_hwnd = None

            def _enum_cb(hwnd, _):
                nonlocal found_hwnd
                buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
                title = buf.value
                if title and "Voice Typer" in title:
                    found_hwnd = hwnd
                    return False
                return True

            WNDENUMPROC = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            ctypes.windll.user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

            if found_hwnd is None:
                return False

            # Restore from minimized, OR reveal if hidden via close-to-tray.
            # SW_SHOW (5) makes a hidden window visible without activating;
            # SW_RESTORE (9) both restores a minimized window and shows it.
            # We handle both states so the tray "Open app" works whether the
            # window was minimized normally or hidden to tray.
            if ctypes.windll.user32.IsIconic(found_hwnd):
                ctypes.windll.user32.ShowWindow(found_hwnd, 9)  # SW_RESTORE
            elif not ctypes.windll.user32.IsWindowVisible(found_hwnd):
                ctypes.windll.user32.ShowWindow(found_hwnd, 5)  # SW_SHOW

            our_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            target_tid = ctypes.windll.user32.GetWindowThreadProcessId(found_hwnd, None)
            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()

            if target_tid != our_tid:
                ctypes.windll.user32.AttachThreadInput(our_tid, target_tid, True)
            if fg_hwnd:
                fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)
                if fg_tid and fg_tid != target_tid and fg_tid != our_tid:
                    ctypes.windll.user32.AttachThreadInput(our_tid, fg_tid, False)

            ctypes.windll.user32.BringWindowToTop(found_hwnd)
            ctypes.windll.user32.SetForegroundWindow(found_hwnd)
            ctypes.windll.user32.SetActiveWindow(found_hwnd)

            if target_tid != our_tid:
                ctypes.windll.user32.AttachThreadInput(our_tid, target_tid, False)

            log.info("[TRAY] Electron window brought to front")
            return True
        except Exception as exc:
            log.warning("[TRAY] Failed to bring Electron window to front: %s", exc)
            return False

    def open_electron_window(self) -> None:
        """Open (or focus) the Electron dashboard window.

        Primary path (1 hop): push ``show_window`` over the TCP channel that
        is always up between us (the backend) and our parent Electron
        process.  Electron's ``showMainWindow()`` then shows + focuses the
        dashboard (creating it lazily if autostart started it hidden).

        Fallback: if the push doesn't land (TCP momentarily down, or this
        backend was started standalone without Electron), use the Win32
        ``_bring_electron_to_front`` focus path, then finally launch
        Electron dev mode as a last resort.
        """
        # 1. Primary: push show_window over TCP.  Cheap, cross-platform,
        #    and works whether the window is hidden (close-to-tray) or
        #    minimized.
        try:
            from voice_typer.server.ipc_server import _push_event_now
            if _push_event_now({"type": "show_window"}):
                log.info("[TRAY] show_window pushed to Electron")
                return
        except Exception:
            log.debug("[TRAY] show_window push failed, trying Win32 focus")

        # 2. Fallback: Win32 EnumWindows focus on an existing window.
        if self._bring_electron_to_front():
            return

        # 3. Last resort: Electron isn't running — build + launch with
        #    electron . (production path, no Vite).
        from voice_typer.server.autostart_launcher import _ensure_built_and_launch
        if _ensure_built_and_launch(hidden=False):
            log.info("[TRAY] Electron app launched (build-first)")
            return
        # If build-first also failed, try dev mode as absolute last resort.
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            client_dir = os.path.join(project_root, "voice_typer", "client")
            log.info("[TRAY] Build-first failed, trying dev mode from %s", client_dir)
            subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=client_dir,
                shell=True,
            )
            log.info("[TRAY] Electron app launched (dev mode fallback)")
        except Exception as e:
            log.error("[TRAY] Failed to launch Electron app: %s", e)

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale so it rebuilds on next right-click."""
        self._menu_cache_valid = False

    def rebuild_menu(self) -> None:
        """Force the tray menu to rebuild immediately.

        Unlike ``invalidate_menu_cache`` (which defers the rebuild until
        the next right-click), this rebuilds the ``pystray.Menu`` object
        right now so changes like ``tray_left_click_action`` take effect
        without requiring the user to right-click first.
        """
        self._menu_cache_valid = False
        if self._icon is not None:
            self._icon._menu = pystray.Menu(self._build_menu)

    def _build_menu(self) -> tuple:
        """Build the Phase 2 minimal tray menu with Models submenu."""
        if self._menu_cache_valid and self._cached_menu is not None:
            return self._cached_menu

        items = []
        hotkey = self._display_hotkey()

        # Determine which menu item is the left-click default based on
        # tray_left_click_action config ("toggle_dictation" or "open_app").
        left_click_action = getattr(self._config, "tray_left_click_action", "open_app")

        items.append(
            pystray.MenuItem(
                f"Toggle Dictation ({hotkey})",
                self._wrap(self._controller.toggle_dictation),
                default=(left_click_action == "toggle_dictation"),
            )
        )
        items.append(
            pystray.MenuItem(
                "Open App",
                self._wrap(self.open_electron_window),
                default=(left_click_action == "open_app"),
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
        """Build a list of model MenuItems — only cached models + More models link."""
        import json
        from voice_typer.server.config import _config_dir
        from voice_typer.server.asr_setup import ensure_hf_env
        ensure_hf_env()

        # Read current model from config
        config_path = _config_dir() / "config.json"
        current_model = "tiny.en"
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            current_model = cfg.get("model_size", "tiny.en")
        except Exception:
            pass

        # Models to check
        candidates = [
            ("tiny.en", "whisper", "Systran/faster-whisper-tiny.en"),
            ("small.en", "whisper", "Systran/faster-whisper-small.en"),
            ("medium.en", "whisper", "Systran/faster-whisper-medium.en"),
            ("parakeet", "parakeet", "nvidia/parakeet-tdt-0.6b-v3"),
            ("qwen", "qwen", None),
        ]

        items = []
        for name, backend, repo_id in candidates:
            downloaded = False
            if backend == "qwen":
                try:
                    import qwen_asr  # noqa
                    downloaded = True
                except ImportError:
                    pass
            elif repo_id:
                cache_dir = _config_dir() / "huggingface" / "hub"
                ref_file = cache_dir / f"models--{repo_id.replace('/', '--')}" / "refs" / "main"
                downloaded = ref_file.exists()
            else:
                downloaded = False

            if not downloaded:
                continue

            is_active = (name == current_model and cfg.get("asr_backend", "whisper") == backend) or (
                name == "parakeet" and cfg.get("asr_backend") == "parakeet"
            ) or (
                name == "qwen" and cfg.get("asr_backend") == "qwen"
            )

            items.append(
                pystray.MenuItem(
                    f"{'• ' if is_active else '  '}{name}",
                    self._wrap(lambda n=name: self._controller.change_model(n)),
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
    
    Uses pre-rendered white microphone PNG (from vt_logo.svg) and
    colorizes it per state.  TRAY-020: If size is 0, auto-detect DPI.
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

    try:
        # Load pre-rendered white microphone PNG (from vt_logo.svg)
        asset_dir = Path(__file__).resolve().parent / "assets"
        available = [16, 24, 32, 48, 64]
        best = min(available, key=lambda x: abs(x - size))
        mic_img = Image.open(str(asset_dir / f"tray-mic-{best}.png")).convert("RGBA")
        # Colorize: use mic's alpha channel as mask over solid state color
        colored = Image.new("RGBA", mic_img.size, color)
        colored.putalpha(mic_img.split()[3])
        if colored.size != (size, size):
            colored = colored.resize((size, size), Image.LANCZOS)
    except Exception:
        # Fallback: solid colored square
        colored = Image.new("RGBA", (size, size), color)

    _icon_cache[cache_key] = colored
    return colored
