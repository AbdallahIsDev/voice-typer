import flet as ft
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

    def main(self, page: ft.Page):
        """Main entry point for the Flet app."""
        self.page = page
        page.title = "Voice Typer"

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

        # Allow the Flet subprocess window to close normally when X is clicked.
        # The main Voice Typer tray app continues running independently.

        # Initialize screens
        self._init_screens()

        # Build layout
        page.add(
            ft.Row(
                [
                    self._build_sidebar(),
                    self._build_content_area(),
                ],
                expand=True,
                spacing=0,
            )
        )

        # Set initial view
        self._set_view("home")

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
        nav_items = []

        for item_id, item_config in NAV_ITEMS.items():
            is_selected = item_id == self.current_view
            btn = ft.Container(
                content=ft.Row(
                    [
                        icon(
                            item_config["icon"],
                            color=ft.Colors.WHITE if is_selected else ft.Colors.GREY_600,
                            size=20,
                        ),
                        ft.Text(
                            item_config["title"],
                            color=ft.Colors.WHITE if is_selected else ft.Colors.GREY_700,
                            size=14,
                            weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL,
                        ),
                    ],
                    spacing=12,
                ),
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                border_radius=8,
                bgcolor=ft.Colors.BLUE_600 if is_selected else ft.Colors.TRANSPARENT,
                on_click=lambda e, vid=item_id: self._set_view(vid),
                ink=True,
                tooltip=item_config.get("description", item_config["title"]),
            )
            self.nav_buttons[item_id] = btn
            nav_items.append(btn)

        # UX-008: Theme toggle button
        current_theme = getattr(self.config, 'theme_mode', 'system')

        return ft.Container(
            width=220,
            bgcolor=ft.Colors.GREY_50,
            padding=ft.Padding.all(16),
            content=ft.Column(
                [
                    # Logo/Title
                    ft.Row(
                        [
                            icon("microphone", color=ft.Colors.BLUE_600, size=28),
                            ft.Text(
                                "Voice Typer",
                                size=20,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.BLUE_800,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    # Navigation items
                    ft.Column(nav_items, spacing=4),
                    ft.Container(expand=True),
                    # UX-032: Background indicator
                    ft.Container(
                        content=ft.Row(
                            [
                                icon("radio-button-checked", color=ft.Colors.GREEN_600, size=14),
                                ft.Text("Running", size=11, color=ft.Colors.GREEN_700),
                            ],
                            spacing=6,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=4),
                    ),
                    # Settings at bottom
                    ft.Divider(color=ft.Colors.GREY_300),
                    ft.Container(
                        content=ft.Row(
                            [
                                icon("settings", color=ft.Colors.GREY_600, size=20),
                                ft.Text(
                                    "Settings",
                                    color=ft.Colors.GREY_700,
                                    size=14,
                                ),
                            ],
                            spacing=12,
                        ),
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                        border_radius=8,
                        on_click=lambda e: self._set_view("settings"),
                        ink=True,
                        tooltip="Application settings",
                    ),
                ],
                spacing=0,
            ),
        )

    def _build_content_area(self) -> ft.Control:
        """Build the main content area."""
        return ft.Container(
            expand=True,
            bgcolor=ft.Colors.WHITE,
            content=ft.Container(
                key="content",
                expand=True,
            ),
        )

    def _set_view(self, view_id: str):
        """Switch to a different view."""
        self.current_view = view_id

        # Update nav button states
        for item_id, btn in self.nav_buttons.items():
            is_selected = item_id == view_id
            btn.content.controls[0].color = ft.Colors.WHITE if is_selected else ft.Colors.GREY_600
            btn.content.controls[1].color = ft.Colors.WHITE if is_selected else ft.Colors.GREY_700
            btn.content.controls[1].weight = ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL
            btn.bgcolor = ft.Colors.BLUE_600 if is_selected else ft.Colors.TRANSPARENT

        # Update content area
        content_container = self.page.controls[0].controls[1].content
        screen = self.screens[view_id]

        if view_id == "home":
            from voice_typer.history_db import HistoryDB
            history_db = HistoryDB()
            today_stats = history_db.get_today_stats()

            # UX-007: Dynamic hotkey hint from config
            from voice_typer.settings import display_hotkey
            hotkey_display = display_hotkey(self.config.hotkey)

            content_container.content = build_home_page(
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
                on_open_history=lambda: self._set_view("history"),
                on_test_mic=lambda: self._set_view("microphone"),
                on_manage_models=lambda: self._set_view("models"),
                hotkey_hint=f"Press {hotkey_display} or click to dictate",
            )
        elif callable(screen) and not hasattr(screen, 'build'):
            content_container.content = screen()
        else:
            content_container.content = screen.build()

        self.page.update()


def main():
    """Entry point for the Flet application."""
    app = VoiceTyperApp()
    ft.run(app.main)


if __name__ == "__main__":
    main()
