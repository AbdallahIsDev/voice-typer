import flet as ft
import logging
import threading
from .styles import Colors, NAV_ITEMS, STATUS_COLORS, STATUS_LABELS, get_theme
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
            # Fallback to config before page is initialized
            theme_mode = getattr(self.config, 'theme_mode', 'system')
            if theme_mode == 'dark':
                return True
            if theme_mode == 'light':
                return False
            # SYSTEM mode - check system preference
            try:
                import ctypes
                # Check Windows AppsUseLightTheme registry value
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                    value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                    return value == 0  # 0 = dark, 1 = light
            except Exception:
                return False  # default to light
        # Page is initialized - use its theme_mode
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        # SYSTEM mode - Flet should handle this, but check page's actual brightness
        return getattr(self.page.theme, 'brightness', ft.Brightness.LIGHT) == ft.Brightness.DARK

    def _get_status(self) -> str:
        if self.app_controller and hasattr(self.app_controller, 'tray'):
            try:
                state = self.app_controller.tray._state
                if hasattr(state, 'name'):
                    state_name = state.name.lower()
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
                    return mapping.get(state_name, "idle")
            except Exception:
                pass
        return "idle"

    def _get_last_transcription(self) -> str:
        if self.app_controller and hasattr(self.app_controller, '_last_transcription'):
            return self.app_controller._last_transcription or ""
        return ""

    def _on_toggle_dictation(self):
        if self.app_controller and hasattr(self.app_controller, 'toggle_dictation'):
            self.app_controller.toggle_dictation()

    def _on_start_dictation(self):
        if self.app_controller and hasattr(self.app_controller, '_start_dictation'):
            self.app_controller._start_dictation()

    def _on_stop_dictation(self):
        if self.app_controller and hasattr(self.app_controller, '_stop_dictation'):
            self.app_controller._stop_dictation()

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

    @staticmethod
    def _ensure_window_icon() -> str:
        """Generate a microphone .ico file for the window title bar / taskbar.

        Returns the path to the .ico file (cached after first generation).
        """
        from voice_typer.config import _config_dir
        from PIL import Image, ImageDraw

        ico_path = _config_dir() / "voice-typer.ico"
        if ico_path.exists():
            return str(ico_path)

        # Vibrant blue microphone — static logo (not state-dependent like the tray icon)
        size = 64
        color = (52, 152, 219, 255)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        cx, cy = size // 2, size // 2
        mic_w, mic_h = size // 5, size // 3
        draw.rounded_rectangle(
            [cx - mic_w, cy - mic_h, cx + mic_w, cy + mic_h // 3],
            radius=mic_w // 2,
            fill=color,
        )
        stand_radius = size // 3
        draw.arc(
            [cx - stand_radius, cy - stand_radius + mic_h // 4, cx + stand_radius, cy + stand_radius],
            start=0, end=180,
            fill=color, width=max(2, size // 20),
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
        """Main entry point for the Flet app."""
        self.page = page
        page.title = "Voice Typer"

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

        # Set the window icon (title bar + taskbar) to the microphone logo
        try:
            page.window.icon = self._ensure_window_icon()
        except Exception:
            pass  # Non-critical — fall back to default Flet icon

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
        elif theme_mode == 'dark':
            page.theme_mode = ft.ThemeMode.DARK
        else:
            page.theme_mode = ft.ThemeMode.SYSTEM

        # UX-002: Wire get_theme into page
        page.theme = get_theme(dark=(page.theme_mode == ft.ThemeMode.DARK))

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
            "history": HistoryScreen(self.page, self.config),
            "templates": TemplatesScreen(self.page, self.config),
            "vocabulary": VocabularyScreen(self.page, self.config),
            "models": ModelsScreen(self.page, self.config),
            "microphone": MicrophoneScreen(self.page, self.config),
            "privacy": PrivacyScreen(self.page, self.config),
            "settings": SettingsScreen(self.page, self.config, self.settings_controller),
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

        return ft.Container(
            width=220,
            bgcolor=Colors.sidebar_bg(dark),
            padding=ft.Padding.all(16),
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

    def _build_content_area(self) -> ft.Control:
        """Build the main content area.

        Single Column that holds either:
        - Home view: a centered Container wrapping the home page (set in _set_view)
        - Other views: the screen directly (scrollable Column)

        Content is swapped in :meth:`_set_view`.
        """
        self._content_column = ft.Column(
            key="content",
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )
        return ft.Container(
            expand=True,
            bgcolor=ft.Colors.WHITE,
            content=self._content_column,
        )

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
            content_column = self._content_column
            content_column.controls = [
                ft.Container(
                    expand=True,
                    alignment=ft.Alignment(0, 0),
                    content=ft.Column(
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
                ),
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
        for item_id, btn in self.nav_buttons.items():
            is_selected = item_id == view_id
            btn.content.controls[0].color = ft.Colors.WHITE if is_selected else Colors.text_secondary(dark)
            btn.content.controls[1].color = ft.Colors.WHITE if is_selected else Colors.text_primary(dark)
            btn.content.controls[1].weight = ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL
            btn.bgcolor = ft.Colors.BLUE_600 if is_selected else ft.Colors.TRANSPARENT

        # Update content area.
        # content_column is a single Column. We replace its controls entirely:
        # - Home: a centered Container (expands, centers its content)
        # - Other views: the screen directly (scrollable Column)
        if not hasattr(self, "_content_column"):
            log.error("[UI] Content area not initialized")
            return

        content_column = self._content_column

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
                # Wrap home page in a full-size centered Container
                content_column.controls = [
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment(0, 0),
                        content=home_page,
                    )
                ]
            else:
                if callable(screen) and not hasattr(screen, 'build'):
                    content_column.controls = [screen()]
                else:
                    content_column.controls = [screen.build()]
        except Exception as exc:
            log.error("[UI] Failed to build view %s: %s", view_id, exc)
            content_column.controls = [
                ft.Container(
                    expand=True,
                    alignment=ft.Alignment(0, 0),
                    content=ft.Column(
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
                ),
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
