"""Home screen — main dashboard with large record button."""

import flet as ft
from voice_typer.ui.styles import (
    Colors,
    STATUS_COLORS,
    STATUS_LABELS,
    RECORD_BUTTON_SIZE,
    RECORD_BUTTON_COLOR,
    RECORD_BUTTON_STOP_COLOR,
)


def build_home_page(
    status: str = "idle",
    last_text: str = "",
    model_info: str = "",
    device_info: str = "",
    total_today: int = 0,
    total_chars: int = 0,
    on_toggle_dictation=None,
    on_open_history=None,
    on_test_mic=None,
    on_manage_models=None,
) -> ft.Column:
    """Build the Home screen content."""

    is_recording = status == "recording"

    status_color = STATUS_COLORS.get(status, STATUS_COLORS["idle"])
    status_label = STATUS_LABELS.get(status, STATUS_LABELS["idle"])

    record_button = ft.Container(
        width=RECORD_BUTTON_SIZE,
        height=RECORD_BUTTON_SIZE,
        border_radius=RECORD_BUTTON_SIZE // 2,
        bgcolor=RECORD_BUTTON_STOP_COLOR if is_recording else RECORD_BUTTON_COLOR,
        alignment=ft.Alignment.CENTER,
        on_click=lambda e: on_toggle_dictation() if on_toggle_dictation else None,
        animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
        shadow=ft.BoxShadow(
            spread_radius=2,
            blur_radius=15,
            color=ft.Colors.with_opacity(0.3, RECORD_BUTTON_COLOR if not is_recording else RECORD_BUTTON_STOP_COLOR),
        ),
        content=ft.Icon(
            icon=ft.Icons.STOP if is_recording else ft.Icons.MIC,
            color=ft.Colors.WHITE,
            size=48,
        ),
    )

    status_indicator = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        controls=[
            ft.Container(
                width=10,
                height=10,
                border_radius=5,
                bgcolor=status_color,
            ),
            ft.Text(
                status_label,
                size=14,
                color=status_color,
                weight=ft.FontWeight.W_500,
            ),
        ],
    )

    last_text_preview = ft.Container(
        padding=ft.Padding.all(12),
        border_radius=8,
        bgcolor=ft.Colors.GREY_100,
        width=400,
        height=60,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=ft.Text(
            last_text if last_text else "No transcription yet",
            size=13,
            color=ft.Colors.GREY_600 if not last_text else ft.Colors.GREY_800,
            italic=not last_text,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )

    device_card = ft.Card(
        elevation=1,
        content=ft.Container(
        padding=ft.Padding.all(12),
            content=ft.Column(
                spacing=4,
                controls=[
                    ft.Text("Device Info", size=12, weight=ft.FontWeight.W_600, color=ft.Colors.GREY_500),
                    ft.Text(model_info if model_info else "No model loaded", size=13),
                    ft.Text(device_info if device_info else "", size=12, color=ft.Colors.GREY_500),
                ],
            ),
        ),
    )

    stats_row = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=30,
        controls=[
            ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(str(total_today), size=20, weight=ft.FontWeight.BOLD, color=Colors.PRIMARY),
                    ft.Text("Today", size=11, color=ft.Colors.GREY_500),
                ],
            ),
            ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(str(total_chars), size=20, weight=ft.FontWeight.BOLD, color=Colors.PRIMARY),
                    ft.Text("Characters", size=11, color=ft.Colors.GREY_500),
                ],
            ),
        ],
    )

    quick_actions = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=12,
        controls=[
            ft.ElevatedButton(
                "History",
                icon=ft.Icons.HISTORY,
                on_click=lambda e: on_open_history() if on_open_history else None,
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
            ),
            ft.ElevatedButton(
                "Test Mic",
                icon=ft.Icons.MIC_EXTERNAL_ON,
                on_click=lambda e: on_test_mic() if on_test_mic else None,
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
            ),
            ft.ElevatedButton(
                "Models",
                icon=ft.Icons.MODEL_TRAINING,
                on_click=lambda e: on_manage_models() if on_manage_models else None,
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
            ),
        ],
    )

    return ft.Column(
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=20,
        controls=[
            ft.Text("Home", size=24, weight=ft.FontWeight.BOLD),
            status_indicator,
            record_button,
            ft.Text("Press F2 or click to dictate", size=12, color=ft.Colors.GREY_500),
            last_text_preview,
            stats_row,
            device_card,
            quick_actions,
        ],
    )
