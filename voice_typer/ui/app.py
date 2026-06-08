import ctypes
import flet as ft
import logging
import threading
from .styles import (
    Tokens,
    get_theme, is_windows_dark_mode,
)
from .sidebar import Sidebar
from .statusbar import build_status_bar
from .home import build_home_page
from .history import HistoryScreen
from .templates import TemplatesScreen
from .vocabulary import VocabularyScreen
from .models import ModelsScreen
from .microphone import MicrophoneScreen
from .privacy import PrivacyScreen
from .settings import SettingsScreen

log = logging.getLogger(__name__)

from voice_typer.config import Config


class FletSettingsController:
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
        self.sidebar = None
        self.app_controller = app_controller
        self._status_poll_timer = None
        self._current_status = "idle"

        self.config = Config.load()
        self.settings_controller = FletSettingsController(
            self.config,
            on_hotkey_changed=self._on_hotkey_changed,
            on_model_changed=self._on_model_changed,
            on_microphone_changed=self._on_microphone_changed,
            on_autostart_changed=self._on_autostart_changed,
            on_notifications_changed=self._on_notifications_changed,
        )

    def _is_dark(self) -> bool:
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

    def _read_flet_state(self) -> dict:
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
                "idle": "idle", "recording": "recording", "transcribing": "transcribing",
                "loading": "loading", "error": "error", "paused": "paused",
                "warming_up": "warming_up", "downloading": "downloading",
                "processing": "processing", "cancelling": "cancelling",
                "setup": "setup", "not_configured": "not_configured",
            }
            return mapping.get(status, "idle")
        return "idle"

    def _get_last_transcription(self) -> str:
        state = self._read_flet_state()
        return state.get("last_text", "")

    def _simulate_hotkey(self) -> None:
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
            mod_vk_map = {1: 0x12, 2: 0x11, 4: 0x10, 8: 0x5B}
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

    def _cancel_dictation(self):
        if self.app_controller and hasattr(self.app_controller, '_cancel_dictation'):
            self.app_controller._cancel_dictation()
        elif self.app_controller and hasattr(self.app_controller, 'recorder'):
            try:
                if self.app_controller.recorder.recording:
                    self.app_controller.recorder.discard()
            except Exception:
                pass

    def _start_status_polling(self):
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

    @staticmethod
    def _ensure_window_icon() -> str:
        from voice_typer.config import _config_dir
        from PIL import Image, ImageDraw
        ico_path = _config_dir() / "voice-typer.ico"
        if ico_path.exists():
            return str(ico_path)
        size = 64
        color = (37, 99, 235, 255)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2
        mic_w, mic_h = size // 5, size // 3
        draw.rounded_rectangle(
            [cx - mic_w, cy - mic_h, cx + mic_w, cy + mic_h // 3],
            radius=mic_w // 2, fill=color,
        )
        stand_radius = size // 3
        draw.arc(
            [cx - stand_radius, cy - stand_radius + mic_h // 4, cx + stand_radius, cy + stand_radius],
            start=0, end=180, fill=color, width=max(2, size // 20),
        )
        base_y = cy + stand_radius
        draw.line(
            [cx - stand_radius // 2, base_y, cx + stand_radius // 2, base_y],
            fill=color, width=max(2, size // 20),
        )
        ico_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(ico_path, format="ICO", sizes=[(64, 64)])
        return str(ico_path)

    def main(self, page: ft.Page):
        self.page = page
        page.title = ""
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
                user32.ShowWindow(_window_handle, 0)
                _screen_w = user32.GetSystemMetrics(0)
                _screen_h = user32.GetSystemMetrics(1)
        except Exception:
            pass

        try:
            page.window.icon = VoiceTyperApp._ensure_window_icon()
        except Exception:
            pass

        import os
        assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))
        page.fonts = {
            "hgi": os.path.join(assets_dir, "fonts", "hgi-stroke-rounded.ttf")
        }

        page.window.width = 1000
        page.window.height = 700
        page.window.min_width = 800
        page.window.min_height = 600

        theme_mode = getattr(self.config, 'theme_mode', 'system')
        if theme_mode == 'light':
            page.theme_mode = ft.ThemeMode.LIGHT
            is_dark = False
        elif theme_mode == 'dark':
            page.theme_mode = ft.ThemeMode.DARK
            is_dark = True
        else:
            page.theme_mode = ft.ThemeMode.SYSTEM
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

        self._init_screens()

        status_bar = build_status_bar(self.config, self._current_status, self._is_dark())

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

        try:
            self._set_view("home")
        except Exception as exc:
            log.error("[UI] Failed to set initial view: %s", exc)
            try:
                self._show_error_view(str(exc))
            except Exception:
                pass

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
                try:
                    dark_mode = wintypes.BOOL(0)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        _window_handle, 20,
                        ctypes.byref(dark_mode),
                        ctypes.sizeof(wintypes.BOOL),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        self._start_status_polling()

    def _init_screens(self):
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
        self.sidebar = Sidebar(self.page, self.config, self._set_view)
        return self.sidebar.build(self.current_view)

    def _build_content_area(self) -> ft.Control:
        dark = self._is_dark()
        self._content_column = ft.Column(
            key="content",
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        self._content_area = ft.Container(
            expand=True,
            bgcolor=Tokens.bg_app(dark),
            content=ft.Container(
                content=self._content_column,
                animate_opacity=ft.Animation(120, ft.AnimationCurve.EASE_IN_OUT),
            ),
        )
        return self._content_area

    def _build_main_row(self) -> ft.Row:
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
        try:
            dark = self._is_dark()
            tp = Tokens.text_primary(dark)
            ts = Tokens.text_secondary(dark)
            ad = Tokens.accent_danger(dark)
            self._content_column.scroll = None
            self._content_column.controls = [
                ft.Container(expand=1),
                ft.Column(
                    [
                        ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ad),
                        ft.Text("Something went wrong loading this page", size=16, color=ad,
                                weight=ft.FontWeight.W_500),
                        ft.Text(error_message or "Please try restarting the application", size=13, color=ts),
                        ft.Container(height=10),
                        ft.Button("Retry", on_click=lambda e: self._set_view(self.current_view),
                                  icon=ft.Icons.REFRESH),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(expand=1),
            ]
            self.page.update()
        except Exception:
            pass

    def _set_view(self, view_id: str):
        self.current_view = view_id
        dark = self._is_dark()

        if self.sidebar is not None:
            self.sidebar.update_active_view(view_id, dark)

        if not hasattr(self, "_content_area"):
            log.error("[UI] Content area not initialized")
            return

        self._content_area.bgcolor = Tokens.bg_app(dark)

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

                try:
                    from voice_typer.settings import display_hotkey
                    hotkey_display = display_hotkey(self.config.hotkey)
                except Exception:
                    hotkey_display = "F2"

                dark = self._is_dark()
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
                    hotkey_hint=f"Press {hotkey_display} or click to dictate",
                    dark=dark,
                )
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
                        ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=Tokens.accent_danger(dark)),
                        ft.Text(f"Failed to load {view_id}", size=16, color=Tokens.accent_danger(dark),
                                weight=ft.FontWeight.W_500),
                        ft.Text(str(exc) or "Please try restarting the application", size=13,
                                color=Tokens.text_secondary(dark)),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(expand=1),
            ]

        self.page.update()


def main(app_controller=None):
    app = VoiceTyperApp(app_controller=app_controller)
    ft.run(app.main)


if __name__ == "__main__":
    main()
