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
from voice_typer.ui.icons import icon


def build_home_page(
    status: str = "idle",
    last_text: str = "",
    model_info: str = "",
    device_info: str = "",
    total_today: int = 0,
    total_chars: int = 0,
    on_toggle_dictation=None,
    on_repaste_last=None,
    on_start_dictation=None,
    on_stop_dictation=None,
    hotkey_hint: str = "Press F2 or click to dictate",
    dark: bool = False,
) -> ft.Column:
    """Build the Home screen content."""

    is_recording = status == "recording"

    status_color = STATUS_COLORS.get(status, STATUS_COLORS["idle"])
    status_label = STATUS_LABELS.get(status, STATUS_LABELS["idle"])

    def _on_record_click(e):
        if is_recording:
            if on_stop_dictation:
                on_stop_dictation()
            elif on_toggle_dictation:
                on_toggle_dictation()
        else:
            if on_start_dictation:
                on_start_dictation()
            elif on_toggle_dictation:
                on_toggle_dictation()

    record_button = ft.Container(
        width=RECORD_BUTTON_SIZE,
        height=RECORD_BUTTON_SIZE,
        border_radius=RECORD_BUTTON_SIZE // 2,
        bgcolor=RECORD_BUTTON_STOP_COLOR if is_recording else RECORD_BUTTON_COLOR,
        alignment=ft.Alignment.CENTER,
        on_click=_on_record_click,
        animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
        shadow=ft.BoxShadow(
            spread_radius=2,
            blur_radius=15,
            color=ft.Colors.with_opacity(0.3, RECORD_BUTTON_COLOR if not is_recording else RECORD_BUTTON_STOP_COLOR),
        ),
        content=icon(
            "stop" if is_recording else "microphone",
            color=ft.Colors.WHITE,
            size=48,
        ),
        tooltip="Stop recording" if is_recording else "Start recording",
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
        bgcolor=ft.Colors.GREY_200 if not dark else ft.Colors.GREY_700,
        width=400,
        height=60,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=ft.Text(
            last_text if last_text else "No transcription yet",
            size=13,
            color=ft.Colors.GREY_600 if not dark else ft.Colors.GREY_400 if not last_text else ft.Colors.GREY_800 if not dark else ft.Colors.GREY_200,
            italic=not last_text,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
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
                    ft.Text("Today", size=11, color=ft.Colors.GREY_500 if not dark else ft.Colors.GREY_400),
                ],
            ),
            ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(str(total_chars), size=20, weight=ft.FontWeight.BOLD, color=Colors.PRIMARY),
                    ft.Text("Characters", size=11, color=ft.Colors.GREY_500 if not dark else ft.Colors.GREY_400),
                ],
            ),
        ],
    )

    # Repaste last transcription button (only if there's a last text)
    repaste_button = ft.OutlinedButton(
        content=ft.Row([icon("copy-01", size=16), ft.Text("Repaste Last")], spacing=8),
        on_click=lambda e: on_repaste_last() if on_repaste_last else None,
        visible=bool(last_text),
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        tooltip="Repaste last transcription",
    )

    # ── Elements wrapper ─────────────────────────────────────────────
    # Groups every home-screen element in a single Column.  Centering
    # is handled by the parent Container (_content_area) in app.py which
    # gets alignment=ft.Alignment(0,0) for the home view.
    return ft.Column(
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=20,
        controls=[
            status_indicator,
            record_button,
            ft.Text(hotkey_hint, size=12, color=ft.Colors.GREY_500 if not dark else ft.Colors.GREY_400),
            last_text_preview,
            repaste_button,
            stats_row,
        ],
    )
