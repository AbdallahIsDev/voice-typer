"""Custom title bar with native-style window controls and sidebar toggle."""

import threading
import flet as ft
from .styles import Tokens
from .icons import icon


class TitleBar:
    """Custom title bar component with native-style window controls."""

    def __init__(
        self,
        page: ft.Page,
        on_toggle_sidebar,
        dark: bool,
        sidebar_collapsed: bool = False,
    ):
        self.page = page
        self.on_toggle_sidebar = on_toggle_sidebar
        self.dark = dark
        self.sidebar_collapsed = sidebar_collapsed
        self._toggle_icon = None
        self._maximize_icon = None
        self.container = None
        self._click_timer = None

    def set_collapsed(self, collapsed: bool):
        self.sidebar_collapsed = collapsed

    def set_maximized(self, maximized: bool):
        pass

    def _minimize(self, e=None):
        self.page.window.minimized = True
        self.page.update()

    def _maximize_or_restore(self, e=None):
        self.page.window.maximized = not self.page.window.maximized
        self.page.update()

    def _on_drag_click(self, e=None):
        if self._click_timer is not None:
            self._click_timer.cancel()
            self._click_timer = None
            self._maximize_or_restore()
        else:
            self._click_timer = threading.Timer(0.3, self._reset_click)
            self._click_timer.daemon = True
            self._click_timer.start()

    def _reset_click(self):
        self._click_timer = None

    async def _close(self, e=None):
        await self.page.window.close()

    def build(self) -> ft.Control:
        """Build and return the title bar control."""
        icon_color = Tokens.text_primary(self.dark)
        minmax_hover = (
            "rgba(255,255,255,0.08)" if self.dark else "rgba(0,0,0,0.06)"
        )
        close_hover = "#C42B1C"

        # ── Sidebar toggle icon ───────────────────────────────────────
        self._toggle_icon = icon("panel-left", color=Tokens.text_secondary(self.dark), size=18)
        toggle_btn = ft.Container(
            width=40,
            height=40,
            alignment=ft.alignment.Alignment.CENTER,
            content=self._toggle_icon,
            on_click=lambda e: self.on_toggle_sidebar(),
        )
        def _toggle_hover(e):
            toggle_btn.bgcolor = minmax_hover if e.data == "true" else None
            toggle_btn.update()
        toggle_btn.on_hover = _toggle_hover

        # ── Minimize ───────────────────────────────────────────────────
        minimize_btn = ft.Container(
            width=46,
            height=32,
            alignment=ft.alignment.Alignment.CENTER,
            content=icon("line", color=icon_color, size=10),
            on_click=self._minimize,
        )
        def _minimize_hover(e):
            minimize_btn.bgcolor = minmax_hover if e.data == "true" else None
            minimize_btn.update()
        minimize_btn.on_hover = _minimize_hover

        # ── Maximize / Restore ────────────────────────────────────────
        self._maximize_icon = icon("rectangle", color=icon_color, size=10)
        maximize_btn = ft.Container(
            width=46,
            height=32,
            alignment=ft.alignment.Alignment.CENTER,
            content=self._maximize_icon,
            on_click=self._maximize_or_restore,
        )
        def _maximize_hover(e):
            maximize_btn.bgcolor = minmax_hover if e.data == "true" else None
            maximize_btn.update()
        maximize_btn.on_hover = _maximize_hover

        # ── Close ──────────────────────────────────────────────────────
        close_btn = ft.Container(
            width=46,
            height=32,
            alignment=ft.alignment.Alignment.CENTER,
            content=icon("close-icon", color=icon_color, size=10),
            on_click=self._close,
        )
        def _close_hover(e):
            close_btn.bgcolor = close_hover if e.data == "true" else None
            close_btn.update()
        close_btn.on_hover = _close_hover

        # ── Window controls grouped on the right ──────────────────────
        window_controls = ft.Row(
            [minimize_btn, maximize_btn, close_btn],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # ── Assemble bar ──────────────────────────────────────────────
        self.container = ft.Container(
            height=32,
            bgcolor=Tokens.bg_sidebar(self.dark),
            content=ft.Row(
                [
                    toggle_btn,
                    ft.WindowDragArea(
                        expand=True,
                        content=ft.GestureDetector(
                            mouse_cursor=ft.MouseCursor.BASIC,
                            on_tap=self._on_drag_click,
                            content=ft.Container(expand=True),
                        ),
                        maximizable=True,
                    ),
                    window_controls,
                ],
                expand=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        return self.container
