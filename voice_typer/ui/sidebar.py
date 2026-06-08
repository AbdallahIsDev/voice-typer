"""Standalone sidebar navigation component for Voice Typer."""

import flet as ft
from .styles import Tokens, SIDEBAR_WIDTH, NAV_ITEMS
from .icons import icon


class Sidebar:
    """Standalone sidebar navigation component."""

    def __init__(self, page: ft.Page, config, on_navigate):
        self.page = page
        self.config = config
        self.on_navigate = on_navigate
        self.current_view = None
        self.nav_buttons = {}
        self.container = None

    def _is_dark(self) -> bool:
        if self.page is None:
            return False
        dm = self.page.theme_mode
        if dm == ft.ThemeMode.DARK:
            return True
        if dm == ft.ThemeMode.LIGHT:
            return False
        from .styles import is_windows_dark_mode
        return is_windows_dark_mode()

    def update_active_view(self, view_id: str, dark: bool):
        """Update active nav state without rebuilding the sidebar."""
        self.current_view = view_id
        ap = Tokens.accent_primary(dark)
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)

        if self.container is not None:
            self.container.bgcolor = Tokens.bg_sidebar(dark)

        for item_id, btn in self.nav_buttons.items():
            is_selected = item_id == view_id
            icon_ctrl = btn.content.controls[0].content
            text_ctrl = btn.content.controls[1]
            icon_ctrl.color = ap if is_selected else ts
            text_ctrl.color = tp if is_selected else ts
            btn.bgcolor = (
                Tokens.sidebar_active_bg(dark) if is_selected
                else ft.Colors.TRANSPARENT
            )

    def build(self, current_view: str) -> ft.Control:
        """Build and return the sidebar control."""
        self.current_view = current_view
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)

        nav_items = []
        for item_id, item_config in NAV_ITEMS.items():
            is_selected = item_id == self.current_view
            btn = ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=icon(
                                item_config["icon"],
                                color=ap if is_selected else ts,
                                size=18,
                            ),
                            width=20, height=20,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Text(
                            item_config["title"],
                            color=tp if is_selected else ts,
                            size=13,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=0),
                height=36,
                border_radius=8,
                bgcolor=(
                    Tokens.sidebar_active_bg(dark) if is_selected
                    else ft.Colors.TRANSPARENT
                ),
                on_click=lambda e, vid=item_id: self.on_navigate(vid),
                tooltip=item_config.get("description", item_config["title"]),
                animate=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT),
            )
            self.nav_buttons[item_id] = btn
            nav_items.append(btn)

        self.container = ft.Container(
            width=SIDEBAR_WIDTH,
            bgcolor=Tokens.bg_sidebar(dark),
            padding=ft.Padding.symmetric(horizontal=8, vertical=0),
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                icon("microphone", color=ap, size=18),
                                ft.Text(
                                    "Voice Typer",
                                    size=14,
                                    weight=ft.FontWeight.W_600,
                                    color=tp,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        height=56,
                        padding=ft.Padding.symmetric(horizontal=16, vertical=0),
                    ),
                    ft.Container(height=8),
                    ft.Column(nav_items, spacing=2),
                    ft.Container(expand=True),
                ],
                spacing=0,
            ),
        )
        return self.container
