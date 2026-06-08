"""Standalone status bar component for Voice Typer."""

import flet as ft
from .styles import Tokens, STATUS_COLORS
from voice_typer import __version__


def build_status_bar(config, current_status: str, dark: bool) -> ft.Control:
    """Build the bottom status bar showing device, model, version, and status indicator."""
    ts = Tokens.text_secondary(dark)
    try:
        model = getattr(config, 'model_size', 'unknown')
        device = getattr(config, 'device', 'unknown')
        device_label = "GPU" if device == "cuda" else "CPU" if device == "cpu" else device.upper()
        return ft.Container(
            height=28,
            bgcolor=Tokens.bg_sidebar(dark),
            padding=ft.Padding.symmetric(horizontal=14, vertical=0),
            content=ft.Row(
                [
                    ft.Container(
                        width=6, height=6, border_radius=3,
                        bgcolor=STATUS_COLORS.get(current_status, "#22C55E"),
                    ),
                    ft.Container(width=6),
                    ft.Text(device_label, size=11, color=ts),
                    ft.Container(width=12),
                    ft.Text(model, size=11, color=ts),
                    ft.Container(expand=True),
                    ft.Text(f"v{__version__}", size=11, color=ts),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=0,
            ),
        )
    except Exception:
        ts = Tokens.text_secondary(dark)
        return ft.Container(
            height=28,
            bgcolor=Tokens.bg_sidebar(dark),
            content=ft.Row(
                [
                    ft.Container(expand=True),
                    ft.Text(
                        f"{getattr(config, 'model_size', 'unknown')}  \u00b7  {getattr(config, 'device', 'unknown')}",
                        size=11, color=ts,
                    ),
                    ft.Container(expand=True),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
