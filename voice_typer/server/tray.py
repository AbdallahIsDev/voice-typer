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
import subprocess
import sys
import tempfile
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

        # Flet window tracking (runs as a separate subprocess so Flet's
        # asyncio / signal / COM infrastructure doesn't interfere with
        # pystray's Windows message loop on the main thread)
        self._flet_process: Optional[subprocess.Popen] = None

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
        self._write_flet_state_file(state.name.lower(), message)

    def _check_pending_model_change(self) -> None:
        """Read flet_state.json and apply any pending model change."""
        try:
            import json
            from voice_typer.server.config import _config_dir
            path = _config_dir() / "flet_state.json"
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return
            name = data.pop("pending_model_name", None)
            backend = data.pop("pending_model_backend", None)
            if name is None:
                return
            # Clear the pending fields so we don't re-apply
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, str(path))
            except BaseException:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
            log.info("[TRAY] Pending model change detected: %s (%s)", name, backend)
            self._controller.change_model(name)
        except Exception as exc:
            log.warning("[TRAY] Failed to apply pending model change: %s", exc)

    def _write_flet_state_file(self, status: str, message: str = "") -> None:
        """Write current state to a shared JSON file that the Flet subprocess reads."""
        try:
            import json
            import os
            import tempfile
            from voice_typer.server.config import _config_dir
            path = _config_dir() / "flet_state.json"
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}
            data["status"] = status
            data["message"] = message
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, str(path))
            except BaseException:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                raise
        except Exception:
            pass

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

    @staticmethod
    def _kill_all_flet_processes(tracked_pid: int | None = None) -> None:
        """Find and kill ALL Flet processes, using multiple strategies:

        1. Window-title sweep: find top-level windows with "Voice Typer" in
           their title and kill their owning process.

        2. Command-line sweep: search for any Python process whose
           command line contains ``voice_typer.server.ui.app`` on Windows via
           ``tasklist`` (more reliable than deprecated wmic).

        3. Explicit tracked PID: ensure the PID we tracked is dead.

        This catches orphaned Flet processes that the tracked
        ``_flet_process`` might have lost track of (e.g. from an earlier
        restart that failed to fully clean up, or when the window is in a
        "loading" phase before ``page.title`` is set).
        """
        if sys.platform != "win32":
            return

        killed_pids: set[int] = set()

        # Always ensure tracked_pid is in the kill set
        if tracked_pid is not None:
            killed_pids.add(tracked_pid)

        # ── Strategy 1: Window-title sweep ────────────────────────────
        try:
            import ctypes
            from ctypes import wintypes

            def _enum_cb(hwnd, lparam):
                buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
                title = buf.value
                if title and "Voice Typer" in title:
                    pid = wintypes.DWORD()
                    ctypes.windll.user32.GetWindowThreadProcessId(
                        hwnd, ctypes.byref(pid)
                    )
                    if pid.value:
                        killed_pids.add(pid.value)
                return True

            WNDENUMPROC = ctypes.CFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )
            ctypes.windll.user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        except Exception as e:
            log.warning("EnumWindows cleanup failed: %s", e)

        # ── Strategy 2: Command-line sweep via tasklist ───────────────
        # tasklist is available on all modern Windows versions, unlike wmic
        # which is deprecated/removed in Windows 11 22H2+.
        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    "IMAGENAME eq python.exe",
                    "/FI",
                    "IMAGENAME eq pythonw.exe",
                    "/FO",
                    "CSV",
                    "/V",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            import re
            # CSV format: "Image Name","PID","Session Name","Session#","Mem Usage","Status","User Name","CPU Time","Window Title","Command Line"
            for line in result.stdout.splitlines()[1:]:  # skip header
                if "voice_typer.server.ui.app" in line:
                    # Parse CSV line
                    parts = line.split('","')
                    if len(parts) >= 2:
                        pid_str = parts[1].strip('"')
                        if pid_str.isdigit():
                            killed_pids.add(int(pid_str))
        except Exception as e:
            log.warning("tasklist command-line sweep failed: %s", e)

        if not killed_pids:
            return

        log.info(
            "Killing %d orphaned Flet process(es): %s",
            len(killed_pids),
            sorted(killed_pids),
        )
        for pid in sorted(killed_pids):
            try:
                # Use /T for tree kill to also terminate child processes
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                )
            except Exception as e:
                log.warning(
                    "Failed to kill orphaned PID %d: %s", pid, e
                )

    def close_flet_window(self) -> None:
        """Kill the tracked Flet subprocess (if any) without stopping the
        tray icon, then sweep for any remaining Flet windows that might
        have been orphaned from a previous incomplete restart.

        Used during restart so the old Flet window is gone before the new
        VoiceTyper process starts — prevents stale windows showing "loading".
        """
        proc = self._flet_process
        tracked_pid = None
        if proc is not None and proc.poll() is None:
            tracked_pid = proc.pid
            log.info("Closing Flet window (PID=%d)", tracked_pid)

            # 1. Try graceful terminate first
            try:
                proc.terminate()
                proc.wait(timeout=3)
                log.info("Flet window terminated gracefully (PID=%d)", tracked_pid)
                self._flet_process = None
            except Exception:
                pass

            # 2. Try kill()
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                    log.info("Flet window killed (PID=%d)", tracked_pid)
                    self._flet_process = None
                except Exception:
                    pass

            # 3. taskkill /F /T on the tracked PID (tree kill)
            if sys.platform == "win32" and proc.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(tracked_pid)],
                        capture_output=True,
                        timeout=5,
                    )
                    proc.wait(timeout=2)
                    log.info("Flet window killed via taskkill /T (PID=%d)", tracked_pid)
                except Exception as e:
                    log.warning(
                        "taskkill /T fallback failed for PID %d: %s", tracked_pid, e
                    )

        self._flet_process = None

        # 4. Sweep: kill any remaining Flet windows not tracked by us
        #    (orphans from previous incomplete restarts)
        # Pass tracked_pid so we can double-ensure it's dead
        self._kill_all_flet_processes(tracked_pid)

    def stop(self) -> None:
        """Stop the tray icon, kill the Flet subprocess, and exit the event loop."""
        self.close_flet_window()

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
        """Open the Flet desktop window in a separate subprocess.

        Flet uses asyncio, signal handlers, and COM internally, all of which
        can interfere with pystray's Windows message loop when running in the
        same process (even in a daemon thread).  Launching Flet as a subprocess
        fully isolates it — no interference, no signal stubbing.

        ``app_controller`` is NOT passed (it lives in the parent process), so
        direct-in-process actions (record button, etc.) are unavailable.  The
        tray menu, hotkeys, and all other features continue to work.
        """
        # Check if Flet process is already running
        if self._flet_process is not None and self._flet_process.poll() is None:
            log.info("Flet window already open (PID=%d), bringing to front", self._flet_process.pid)
            self._bring_window_to_front(self._flet_process.pid)
            return

        try:
            from voice_typer.server.asr_setup import get_voice_typer_python
            python_exe = get_voice_typer_python()
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self._flet_process = subprocess.Popen(
                [python_exe, "-m", "voice_typer.server.ui.app"],
                cwd=project_root,
            )
            log.info("Flet window opened in subprocess (PID=%d)", self._flet_process.pid)

            # Watch for subprocess exit so we can log it and clean up
            # the process reference so open_flet_window() correctly detects
            # the window is gone on the next call.
            def _watch_flet(proc):
                try:
                    proc.wait()
                    log.info("Flet window closed (PID=%d)", proc.pid)
                except Exception:
                    pass
                finally:
                    # Clear the reference so open_flet_window() doesn't
                    # see a stale Popen object and skip launching.
                    if self._flet_process is proc:
                        self._flet_process = None

            threading.Thread(target=_watch_flet, args=(self._flet_process,), daemon=True).start()
        except Exception as e:
            log.error("Failed to open Flet window: %s", e)

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale so it rebuilds on next right-click."""
        self._menu_cache_valid = False

    @staticmethod
    def _bring_window_to_front(pid: int) -> None:
        """Restore and focus the Flet window.

        Searches by window title (\"Voice Typer\") instead of PID because
        Flet's embedded webview window is owned by a different process
        than the Python subprocess.
        """
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
                log.warning("No Voice Typer window found for PID %d", pid)
                return

            # Restore if minimized
            if ctypes.windll.user32.IsIconic(found_hwnd):
                ctypes.windll.user32.ShowWindow(found_hwnd, 9)  # SW_RESTORE

            # Attach thread input to bypass Windows foreground-lock
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

        except Exception as exc:
            log.warning("Failed to bring window to front: %s", exc)

    def _get_left_click_action(self) -> str:
        """Read tray_left_click_action from config on disk (live, not cached)."""
        try:
            from voice_typer.server.config import _config_dir
            import json
            path = _config_dir() / "config.json"
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
                return data.get("tray_left_click_action", "open_app")
        except Exception:
            pass
        return "open_app"

    def _build_menu(self) -> tuple:
        """Build the Phase 2 minimal tray menu with Models submenu."""
        if self._menu_cache_valid and self._cached_menu is not None:
            return self._cached_menu

        # Apply any pending model change from Flet UI
        self._check_pending_model_change()

        items = []
        hotkey = self._display_hotkey()

        left_click_action = self._get_left_click_action()

        if left_click_action == 'toggle_dictation':
            items.append(
                pystray.MenuItem(
                    f"Toggle Dictation ({hotkey})",
                    self._wrap(self._controller.toggle_dictation),
                    default=True,
                )
            )
            items.append(pystray.MenuItem("Open App", self._wrap(self.open_flet_window)))
        else:
            items.append(
                pystray.MenuItem(
                    "Open App",
                    self._wrap(self.open_flet_window),
                    default=True,
                )
            )
            items.append(
                pystray.MenuItem(
                    f"Toggle Dictation ({hotkey})",
                    self._wrap(self._controller.toggle_dictation),
                )
            )

        # Settings (TRAY-002)
        items.append(pystray.MenuItem("Settings", self._wrap(self.open_flet_window)))

        items.append(pystray.Menu.SEPARATOR)

        # Models submenu — only show downloaded models
        models_sub = self._build_models_submenu()
        items.append(pystray.MenuItem("Models", pystray.Menu(*models_sub)))

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

            label = name
            is_active = (name == current_model and cfg.get("asr_backend", "whisper") == backend) or (
                name == "parakeet" and cfg.get("asr_backend") == "parakeet"
            ) or (
                name == "qwen" and cfg.get("asr_backend") == "qwen"
            )

            items.append(
                pystray.MenuItem(
                    label,
                    self._wrap(lambda n=name: self._controller.change_model(n)),
                    checked=lambda active=is_active: active,
                )
            )

        items.append(pystray.Menu.SEPARATOR)
        items.append(
            pystray.MenuItem(
                "More models...",
                self._wrap(self.open_flet_window),
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
