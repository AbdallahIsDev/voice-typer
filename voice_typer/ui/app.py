import ctypes
import flet as ft
import logging
import threading
from .styles import Colors, NAV_ITEMS, STATUS_COLORS, STATUS_LABELS, get_theme, is_windows_dark_mode
from .icons import icon
from .home import build_home_page
from .history import HistoryScreen
from .templates import TemplatesScreen
from .vocabulary import VocabularyScreen
from .models import ModelsScreen
from .microphone import MicrophoneScreen
from .privacy import PrivacyScreen
from .settings import SettingsScreen
from voice_typer import __version__

log = logging.getLogger(__name__)

# CQ-001: No tkinter imports — SettingsController moved to local Flet-based implementation
from voice_typer.config import Config


class FletSettingsController:
    """Flet-based settings controller that replaces the tkinter SettingsController."""

    def __init__(self, config, on_hotkey_changed=None, on_model_changed=None,
                 on_microphone_changed=None, on_autostart_changed=None,
                 on_notifications_changed=None):
        self.config = config
        self.on_hotkey_changed = on_hotkey_changed
        self.on_model_changed = on_model_changed
        self.on_microphone_changed = on_microphone_changed
        self.on_autostart_changed = on_autostart_changed
        self.on_notifications_changed = on_notifications_changed


class VoiceTyperApp:
    """Main Flet application for Voice Typer."""

    def __init__(self, app_controller=None):
        self.page = None
        self.current_view = "home"
        self.screens = {}
        self.nav_buttons = {}
        self.app_controller = app_controller
        self._status_poll_timer = None
        self._current_status = "idle"

        # Backend components
        self.config = Config.load()
        self.settings_controller = FletSettingsController(
            self.config,
            on_hotkey_changed=self._on_hotkey_changed,
            on_model_changed=self._on_model_changed,
            on_microphone_changed=self._on_microphone_changed,
            on_autostart_changed=self._on_autostart_changed,
            on_notifications_changed=self._on_notifications_changed,
        )

    def _on_hotkey_changed(self, hotkey: str):
        self.config.hotkey = hotkey
        self.config.save()

    def _on_model_changed(self, model: str):
        self.config.model_size = model
        self.config.save()

    def _on_microphone_changed(self, mic_id: str | None):
        self.config.microphone = mic_id
        self.config.save()

    def _on_autostart_changed(self, enabled: bool):
        self.config.autostart = enabled
        self.config.save()

    def _on_notifications_changed(self, enabled: bool):
        self.config.show_notifications = enabled
        self.config.save()

    def _is_dark_mode(self) -> bool:
        """Return True if the current theme is dark."""
        if self.page is None:
            theme_mode = getattr(self.config, 'theme_mode', 'system')
            if theme_mode == 'dark':
                return True
            if theme_mode == 'light':
                return False
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _read_flet_state(self) -> dict:
        """Read shared state from the JSON file written by the parent process."""
        import json
        import time
        for _ in range(3):
            try:
                from voice_typer.config import _config_dir
                path = _config_dir() / "flet_state.json"
                with open(path, "r") as f:
                    return json.load(f)
            except FileNotFoundError:
                return {}
            except (json.JSONDecodeError, PermissionError):
                time.sleep(0.05)
        return {}

    def _get_status(self) -> str:
        state = self._read_flet_state()
        status = state.get("status")
        if status:
            mapping = {
                "idle": "idle",
                "recording": "recording",
                "transcribing": "transcribing",
                "loading": "loading",
                "error": "error",
                "paused": "paused",
                "warming_up": "warming_up",
                "downloading": "downloading",
                "processing": "processing",
                "cancelling": "cancelling",
                "setup": "setup",
                "not_configured": "not_configured",
            }
            return mapping.get(status, "idle")
        return "idle"

    def _get_last_transcription(self) -> str:
        state = self._read_flet_state()
        return state.get("last_text", "")

    def _simulate_hotkey(self) -> None:
        """Simulate the configured hotkey via keybd_event.

        The Flet UI runs as a subprocess with no access to app_controller.
        The parent process polls GetAsyncKeyState at ~20Hz, so the key
        must be held briefly (150ms) to guarantee detection.
        """
        import time
        import sys
        from voice_typer.hotkeys import parse_hotkey_to_win32
        try:
            parsed = parse_hotkey_to_win32(self.config.hotkey)
            if parsed is None:
                print("[UI] _simulate_hotkey: parse returned None", flush=True, file=sys.stderr)
                return
            vk, mod = parsed

            user32 = ctypes.windll.user32

            mod_vk_map = {
                1: 0x12,  # _MOD_ALT → VK_MENU
                2: 0x11,  # _MOD_CONTROL → VK_CONTROL
                4: 0x10,  # _MOD_SHIFT → VK_SHIFT
                8: 0x5B,  # _MOD_WIN → VK_LWIN
            }

            pressed_mods = []
            for mod_bit, mod_vk in mod_vk_map.items():
                if mod & mod_bit:
                    user32.keybd_event(mod_vk, 0, 0, 0)
                    pressed_mods.append(mod_vk)

            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.15)
            user32.keybd_event(vk, 0, 2, 0)

            for mod_vk in reversed(pressed_mods):
                user32.keybd_event(mod_vk, 0, 2, 0)
        except Exception as exc:
            print(f"[UI] _simulate_hotkey: {exc}", flush=True, file=sys.stderr)

    def _poll_now(self):
        """Immediate one-shot status check — bypasses the 1s polling timer.

        Retries with short sleeps to catch state chains like
        recording→transcribing→idle that complete within ~1s.
        """
        import time
        for _ in range(4):
            try:
                new_status = self._get_status()
                if new_status != self._current_status:
                    self._current_status = new_status
                    if self.current_view == "home":
                        self._set_view("home")
            except Exception:
                pass
            time.sleep(0.15)

    def _on_toggle_dictation(self):
        self._simulate_hotkey()
        self._poll_now()

    def _on_start_dictation(self):
        self._simulate_hotkey()
        self._poll_now()

    def _on_stop_dictation(self):
        self._simulate_hotkey()
        self._poll_now()

    def _on_repaste_last(self):
        if self.app_controller and hasattr(self.app_controller, '_last_transcription'):
            last_text = self.app_controller._last_transcription
            if last_text:
                try:
                    self.app_controller.clipboard.copy(last_text)
                    self.app_controller.clipboard.paste()
                except Exception:
                    pass

    def _cancel_dictation(self):
        """Cancel current dictation (ESC to cancel feature)."""
        if self.app_controller and hasattr(self.app_controller, '_cancel_dictation'):
            self.app_controller._cancel_dictation()
        elif self.app_controller and hasattr(self.app_controller, 'recorder'):
            try:
                if self.app_controller.recorder.recording:
                    self.app_controller.recorder.discard()
            except Exception:
                pass

    def _start_status_polling(self):
        """UX-014: Poll app status periodically to update UI live."""
        if self._status_poll_timer is not None:
            self._status_poll_timer.cancel()

        def _poll():
            if self.page is None:
                return
            try:
                new_status = self._get_status()
                if new_status != self._current_status:
                    self._current_status = new_status
                    if self.current_view == "home":
                        self._set_view("home")
            except Exception:
                pass
            self._status_poll_timer = threading.Timer(1.0, _poll)
            self._status_poll_timer.daemon = True
            self._status_poll_timer.start()

        self._status_poll_timer = threading.Timer(1.0, _poll)
        self._status_poll_timer.daemon = True
        self._status_poll_timer.start()

    def _stop_status_polling(self):
        if self._status_poll_timer is not None:
            self._status_poll_timer.cancel()
            self._status_poll_timer = None

    def main(self, page: ft.Page):
        """Main entry point for the Flet app."""
        self.page = page
        page.title = ""

        # ── Immediately hide the native window via Windows API ──────────
        # Flet creates the window before main(page) runs, so it appears at
        # the default (corner) position first.  We hide it instantly via
        # ShowWindow(SW_HIDE) — no Flet protocol round-trip — then position
        # and show it at center at the end.
        _window_handle = None
        _screen_w, _screen_h = 0, 0
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            our_pid = ctypes.windll.kernel32.GetCurrentProcessId()

            def _enum_cb(hwnd, lparam):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == our_pid and user32.IsWindowVisible(hwnd):
                    nonlocal _window_handle
                    _window_handle = hwnd
                    return False
                return True

            WNDENUMPROC = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

            if _window_handle:
                user32.ShowWindow(_window_handle, 0)  # SW_HIDE — instant, no round-trip
                _screen_w = user32.GetSystemMetrics(0)
                _screen_h = user32.GetSystemMetrics(1)
        except Exception:
            pass

        # Register Hugeicons font
        import os
        assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))
        page.fonts = {
            "hgi": os.path.join(assets_dir, "fonts", "hgi-stroke-rounded.ttf")
        }

        page.window.width = 1000
        page.window.height = 700
        page.window.min_width = 800
        page.window.min_height = 600

        # UX-008/031: Theme from config instead of hardcoded LIGHT
        theme_mode = getattr(self.config, 'theme_mode', 'system')
        if theme_mode == 'light':
            page.theme_mode = ft.ThemeMode.LIGHT
            is_dark = False
        elif theme_mode == 'dark':
            page.theme_mode = ft.ThemeMode.DARK
            is_dark = True
        else:
            page.theme_mode = ft.ThemeMode.SYSTEM
            # Detect OS dark mode at startup via Windows registry
            is_dark = False
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                    value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                    is_dark = value == 0
            except Exception:
                pass

        page.theme = get_theme(dark=is_dark)

        page.padding = 0
        page.vertical_alignment = ft.MainAxisAlignment.START
        page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH

        # Allow the Flet window to close normally when X is clicked.
        # The main Voice Typer tray app continues running independently.

        # Initialize screens
        self._init_screens()

        # Build layout — outer Column wraps main Row + bottom status bar.
        # CRITICAL: Column MUST have expand=True so it fills the full page height.
        # Without expand=True, Flet only allocates the column its children's natural
        # height, leaving no room for proper layout of expand=True children.
        status_bar = self._build_status_bar()

        page.add(
            ft.Column(
                [
                    ft.Container(
                        content=self._build_main_row(),
                        expand=True,
                    ),
                    status_bar,
                ],
                expand=True,
                spacing=0,
            )
        )

        # Set initial view (wrapped in try/except so the Flet window doesn't
        # appear blank if a screen fails to render)
        try:
            self._set_view("home")
        except Exception as exc:
            log.error("[UI] Failed to set initial view: %s", exc)
            # Show a visible error message instead of a blank page
            try:
                self._show_error_view(str(exc))
            except Exception:
                pass

        # ── Position at center and show the window ─────────────────────
        # We already hid the window at the start.  Now position it at the
        # center via the Windows API (instant, no round-trip) and show it.
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            if _window_handle:
                new_left = max(0, (_screen_w - page.window.width) // 2)
                new_top = max(0, (_screen_h - page.window.height) // 2)

                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_SHOWWINDOW = 0x0040
                user32.SetWindowPos(
                    _window_handle, 0, new_left, new_top, 0, 0,
                    SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW,
                )
                log.info("[CENTER] Window positioned at center (%d, %d)", new_left, new_top)

                # ── Title bar: force light theme to match sidebar ──────
                try:
                    dark_mode = wintypes.BOOL(0)  # 0 = light
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        _window_handle,
                        20,  # DWMWA_USE_IMMERSIVE_DARK_MODE
                        ctypes.byref(dark_mode),
                        ctypes.sizeof(wintypes.BOOL),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        # UX-014: Start live status polling
        self._start_status_polling()

        # UX-032: Show "running in background" indicator
        self._show_background_indicator()

    def _show_background_indicator(self):
        """UX-032: Show a small indicator that the app runs in the background."""
        pass  # The tray icon itself serves as the indicator

    def _init_screens(self):
        """Initialize all screens."""
        self.screens = {
            "home": build_home_page,
            "history": HistoryScreen(self.page, self.config, reload=lambda: self._set_view("history")),
            "templates": TemplatesScreen(self.page, self.config, reload=lambda: self._set_view("templates")),
            "vocabulary": VocabularyScreen(self.page, self.config, reload=lambda: self._set_view("vocabulary")),
            "models": ModelsScreen(self.page, self.config, reload=lambda: self._set_view("models")),
            "microphone": MicrophoneScreen(self.page, self.config, reload=lambda: self._set_view("microphone")),
            "privacy": PrivacyScreen(self.page, self.config, reload=lambda: self._set_view("privacy")),
            "settings": SettingsScreen(self.page, self.config, self.settings_controller, reload=lambda: self._set_view("settings")),
        }

    def _build_sidebar(self) -> ft.Control:
        """Build the navigation sidebar."""
        dark = self._is_dark_mode()
        nav_items = []

        for item_id, item_config in NAV_ITEMS.items():
            is_selected = item_id == self.current_view
            btn = ft.Container(
                content=ft.Row(
                    [
                        icon(
                            item_config["icon"],
                            color=ft.Colors.WHITE if is_selected else Colors.text_secondary(dark),
                            size=20,
                        ),
                        ft.Text(
                            item_config["title"],
                            color=ft.Colors.WHITE if is_selected else Colors.text_primary(dark),
                            size=14,
                            weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL,
                        ),
                    ],
                    spacing=12,
                ),
                padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                border_radius=8,
                bgcolor=ft.Colors.BLUE_600 if is_selected else ft.Colors.TRANSPARENT,
                on_click=lambda e, vid=item_id: self._set_view(vid),
                tooltip=item_config.get("description", item_config["title"]),
            )
            self.nav_buttons[item_id] = btn
            nav_items.append(btn)

        # UX-008: Theme toggle button
        current_theme = getattr(self.config, 'theme_mode', 'system')

        self._sidebar_container = ft.Container(
            width=220,
            bgcolor=Colors.sidebar_bg(dark),
            border=ft.Border(right=ft.BorderSide(1, Colors.divider(dark))),
            padding=ft.Padding.all(8),
            content=ft.Column(
                [
                    # Logo/Title
                    ft.Row(
                        [
                            icon("microphone", color=Colors.PRIMARY, size=28),
                            ft.Text(
                                "Voice Typer",
                                size=20,
                                weight=ft.FontWeight.BOLD,
                                color=Colors.PRIMARY,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    # Navigation items
                    ft.Column(nav_items, spacing=4),
                ],
                spacing=0,
            ),
        )
        return self._sidebar_container

    def _build_content_area(self) -> ft.Control:
        """Build the main content area."""
        dark = self._is_dark_mode()
        self._content_column = ft.Column(
            key="content",
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self._content_area = ft.Container(
            expand=True,
            bgcolor=Colors.surface(dark),
            content=self._content_column,
        )
        return self._content_area

    def _build_main_row(self) -> ft.Row:
        """Build the Row containing sidebar + content area."""
        return ft.Row(
            [
                self._build_sidebar(),
                self._build_content_area(),
            ],
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _show_error_view(self, error_message: str = "") -> None:
        """Display a fallback error message when the home page fails to render."""
        try:
            dark = self._is_dark_mode()
            self._content_column.scroll = None
            self._content_column.controls = [
                ft.Container(expand=1),
                ft.Column(
                    [
                        ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.RED_400),
                        ft.Text(
                            "Something went wrong loading this page",
                            size=16,
                            color=ft.Colors.RED_700,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(
                            error_message or "Please try restarting the application",
                            size=13,
                            color=Colors.text_secondary(dark),
                        ),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Retry",
                            on_click=lambda e: self._set_view(self.current_view),
                            icon=ft.Icons.REFRESH,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(expand=1),
            ]
            self.page.update()
        except Exception:
            pass

    def _build_status_bar(self) -> ft.Control:
        """Build the bottom status bar with model name, device info, and version.

        Spans the full window width below both sidebar and content area.
        Shows model name (left) and device type (left), separated by a dot
        with 8px gaps, and app version on the far right, on a white
        background with a top border.

        This method NEVER raises — any internal failure falls back to a minimal
        visible status bar so the user always sees something at the bottom.
        """
        dark = self._is_dark_mode()
        try:
            model = getattr(self.config, 'model_size', 'unknown')
            device = getattr(self.config, 'device', 'unknown')
            # Map internal device names to user-friendly labels
            device_label = "GPU" if device == "cuda" else "CPU" if device == "cpu" else device.upper()

            text_color = Colors.text_primary(dark)
            secondary_color = Colors.text_secondary(dark)
            bg_color = Colors.surface(dark)
            divider_color = Colors.divider(dark)

            result = ft.Container(
                height=28,
                bgcolor=bg_color,
                border=ft.Border(top=ft.BorderSide(1, divider_color)),
                padding=ft.Padding.symmetric(horizontal=14, vertical=0),
                content=ft.Row(
                    [
                        ft.Text(
                            model,
                            size=12,
                            color=text_color,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Container(width=8),
                        ft.Text(
                            "·",
                            size=12,
                            color=secondary_color,
                        ),
                        ft.Container(width=8),
                        ft.Text(
                            device_label,
                            size=12,
                            color=text_color,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Container(expand=True),
                        ft.Text(
                            f"v{__version__}",
                            size=11,
                            color=secondary_color,
                            weight=ft.FontWeight.W_400,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=0,
                ),
            )
            log.info("[UI] Status bar built OK (model=%s, device=%s, version=%s)", model, device_label, __version__)
            return result
        except Exception as e:
            log.error("[UI] _build_status_bar failed: %s", e)
            # NEVER re-raise: return a minimal visible fallback status bar
            dark = self._is_dark_mode()
            bg_color = Colors.surface(dark)
            text_color = Colors.text_primary(dark)
            divider_color = Colors.divider(dark)
            return ft.Container(
                height=28,
                bgcolor=bg_color,
                border=ft.Border(top=ft.BorderSide(1, divider_color)),
                content=ft.Row(
                    [
                        ft.Container(expand=True),
                        ft.Text(
                            f"{getattr(self.config, 'model_size', 'unknown')}  ·  {getattr(self.config, 'device', 'unknown')}",
                            size=12,
                            color=text_color,
                        ),
                        ft.Container(expand=True),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

    def _set_view(self, view_id: str):
        """Switch to a different view."""
        self.current_view = view_id

        # Update nav button states
        dark = self._is_dark_mode()
        if hasattr(self, "_content_area"):
            self._content_area.bgcolor = Colors.surface(dark)
        if hasattr(self, "_sidebar_container"):
            self._sidebar_container.bgcolor = Colors.sidebar_bg(dark)
        for item_id, btn in self.nav_buttons.items():
            is_selected = item_id == view_id
            btn.content.controls[0].color = ft.Colors.WHITE if is_selected else Colors.text_secondary(dark)
            btn.content.controls[1].color = ft.Colors.WHITE if is_selected else Colors.text_primary(dark)
            btn.content.controls[1].weight = ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL
            btn.bgcolor = ft.Colors.BLUE_600 if is_selected else ft.Colors.TRANSPARENT

        # Update content area.
        # Home view bypasses the scrollable Column (self._content_column)
        # because expand=True children inside a scrollable Column in Flet
        # 0.85.x are not properly constrained.  Other views keep the
        # scrollable Column.
        if not hasattr(self, "_content_area"):
            log.error("[UI] Content area not initialized")
            return

        screen = self.screens.get(view_id)
        if screen is None:
            log.warning("[UI] Unknown view: %s", view_id)
            return

        try:
            if view_id == "home":
                from voice_typer.history_db import HistoryDB
                try:
                    history_db = HistoryDB()
                    today_stats = history_db.get_today_stats()
                except Exception:
                    today_stats = {}

                # UX-007: Dynamic hotkey hint from config
                try:
                    from voice_typer.settings import display_hotkey
                    hotkey_display = display_hotkey(self.config.hotkey)
                except Exception:
                    hotkey_display = "F2"

                dark = self._is_dark_mode()
                home_page = build_home_page(
                    status=self._get_status(),
                    last_text=self._get_last_transcription(),
                    model_info=f"Model: {self.config.model_size}",
                    device_info=f"Device: {self.config.device}",
                    total_today=today_stats.get("count", 0),
                    total_chars=today_stats.get("chars", 0),
                    on_toggle_dictation=self._on_toggle_dictation,
                    on_start_dictation=self._on_start_dictation,
                    on_stop_dictation=self._on_stop_dictation,
                    on_repaste_last=self._on_repaste_last,
                    hotkey_hint=f"Press {hotkey_display} or click to dictate",
                    dark=dark,
                )
                # Center home page using expand spacers inside the
                # content Column.  _content_area (no alignment) gives
                # tight constraints to _content_column, which gets the
                # full viewport height.  Two Container(expand=1) spacers
                # push the content Column to vertical center.
                self._content_column.scroll = None
                self._content_column.controls = [
                    ft.Container(expand=1),
                    home_page,
                    ft.Container(expand=1),
                ]
                self._content_area.update()
            else:
                self._content_column.scroll = ft.ScrollMode.AUTO
                if callable(screen) and not hasattr(screen, 'build'):
                    self._content_column.controls = [screen()]
                else:
                    self._content_column.controls = [screen.build()]
                self._content_area.update()
        except Exception as exc:
            log.error("[UI] Failed to build view %s: %s", view_id, exc)
            self._content_column.scroll = None
            self._content_column.controls = [
                ft.Container(expand=1),
                ft.Column(
                    [
                        ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.RED_400),
                        ft.Text(
                            f"Failed to load {view_id}",
                            size=16,
                            color=ft.Colors.RED_700,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(
                            str(exc) or "Please try restarting the application",
                            size=13,
                            color=Colors.text_secondary(dark),
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(expand=1),
            ]

        self.page.update()


def main(app_controller=None):
    """Entry point for the Flet application.

    ``app_controller`` is the main ``VoiceTyperApp`` instance from
    ``voice_typer.app``.  When provided, the Flet UI can invoke backend
    actions (record, stop, history, models) through a direct in-process
    reference — no IPC, no subprocess, no serialization.
    """
    # Window centering is handled inside VoiceTyperApp.main() via the
    # native Windows API — no watcher thread needed.
    app = VoiceTyperApp(app_controller=app_controller)
    ft.run(app.main)


if __name__ == "__main__":
    main()
