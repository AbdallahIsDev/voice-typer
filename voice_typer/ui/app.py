import flet as ft
from .styles import Colors, NAV_ITEMS
from .icons import icon
from .home import build_home_page
from .history import HistoryScreen
from .templates import TemplatesScreen
from .vocabulary import VocabularyScreen
from .models import ModelsScreen
from .microphone import MicrophoneScreen
from .privacy import PrivacyScreen
from .settings import SettingsScreen

# Backend imports
from voice_typer.config import Config
from voice_typer.settings import SettingsController


class VoiceTyperApp:
    """Main Flet application for Voice Typer."""

    def __init__(self):
        self.page = None
        self.current_view = "home"
        self.screens = {}
        self.nav_buttons = {}
        
        # Backend components
        self.config = Config.load()
        self.settings_controller = SettingsController(
            self.config,
            on_hotkey_changed=self._on_hotkey_changed,
            on_model_changed=self._on_model_changed,
            on_microphone_changed=self._on_microphone_changed,
            on_autostart_changed=self._on_autostart_changed,
            on_notifications_changed=self._on_notifications_changed,
        )
    
    def _on_hotkey_changed(self, hotkey: str):
        """Handle hotkey change from settings."""
        self.config.hotkey = hotkey
        self.config.save()
    
    def _on_model_changed(self, model: str):
        """Handle model change from settings."""
        self.config.model_size = model
        self.config.save()
    
    def _on_microphone_changed(self, mic_id: str | None):
        """Handle microphone change from settings."""
        self.config.microphone = mic_id
        self.config.save()
    
    def _on_autostart_changed(self, enabled: bool):
        """Handle autostart change from settings."""
        self.config.autostart = enabled
        self.config.save()
    
    def _on_notifications_changed(self, enabled: bool):
        """Handle notifications change from settings."""
        self.config.show_notifications = enabled
        self.config.save()

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
        page.theme_mode = ft.ThemeMode.LIGHT
        page.padding = 0

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
            )
            self.nav_buttons[item_id] = btn
            nav_items.append(btn)

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
            # Update button appearance
            btn.content.controls[0].color = ft.Colors.WHITE if is_selected else ft.Colors.GREY_600
            btn.content.controls[1].color = ft.Colors.WHITE if is_selected else ft.Colors.GREY_700
            btn.content.controls[1].weight = ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL
            btn.bgcolor = ft.Colors.BLUE_600 if is_selected else ft.Colors.TRANSPARENT

        # Update content area
        content_container = self.page.controls[0].controls[1].content
        screen = self.screens[view_id]
        
        if view_id == "home":
            # Get actual data from backend
            from voice_typer.history_db import HistoryDB
            history_db = HistoryDB()
            today_stats = history_db.get_today_stats()
            
            content_container.content = build_home_page(
                status="idle",  # This would come from the app state
                last_text="",
                model_info=f"Model: {self.config.model_size}",
                device_info=f"Device: {self.config.device}",
                total_today=today_stats.get("count", 0),
                total_chars=today_stats.get("chars", 0),
            )
        elif callable(screen) and not hasattr(screen, 'build'):
            # It's a function (like build_home_page)
            content_container.content = screen()
        else:
            # It's a class with build method
            content_container.content = screen.build()

        self.page.update()


def main():
    """Entry point for the Flet application."""
    app = VoiceTyperApp()
    ft.run(app.main)


if __name__ == "__main__":
    main()
