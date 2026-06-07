"""Home screen — main dashboard with large record button."""

import re
import flet as ft
from voice_typer.ui.styles import Colors, STATUS_COLORS, STATUS_LABELS, RECORD_BUTTON_SIZE, RECORD_BUTTON_COLOR, RECORD_BUTTON_STOP_COLOR
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

    # Status dot + label
    status_indicator = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
        controls=[
            ft.Container(
                width=8,
                height=8,
                border_radius=4,
                bgcolor=status_color,
                animate_opacity=ft.Animation(2000),
            ),
            ft.Text(
                status_label,
                size=11,
                color="rgba(241,241,243,0.40)",
                weight=ft.FontWeight.W_500,
            ),
        ],
    )

    # Mic button
    record_button = ft.Container(
        width=72,
        height=72,
        border_radius=36,
        bgcolor=RECORD_BUTTON_COLOR,
        alignment=ft.Alignment.CENTER,
        on_click=_on_record_click,
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=10,
            color="rgba(220,38,38,0.20)",
        ) if is_recording else ft.BoxShadow(
            spread_radius=0,
            blur_radius=0,
            color="rgba(220,38,38,0)",
        ),
        animate=ft.Animation(1200, ft.AnimationCurve.EASE_IN_OUT),
        content=icon(
            "microphone",
            color="#FFFFFF",
            size=28,
        ),
        tooltip="Stop recording" if is_recording else "Start recording",
    )

    # Hint text with kbd badge for hotkey
    hint_parts = hotkey_hint.split("or")
    if len(hint_parts) == 2:
        left_part = hint_parts[0].strip()
        right_part = "or" + hint_parts[1]
        m = re.search(r'Press\s+(\S+)', left_part)
        if m:
            key_name = m.group(1)
            prefix = left_part.replace(m.group(0), "Press")
            hint = ft.Row(
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=6,
                controls=[
                    ft.Text("Press", size=12, color="rgba(241,241,243,0.28)"),
                    ft.Container(
                        content=ft.Text(key_name, size=11, color="rgba(241,241,243,0.60)", font_family="Consolas"),
                        padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                        border_radius=4,
                        bgcolor="rgba(255,255,255,0.08)",
                        border=ft.Border(
                            left=ft.BorderSide(0.5, "rgba(255,255,255,0.15)"),
                            top=ft.BorderSide(0.5, "rgba(255,255,255,0.15)"),
                            right=ft.BorderSide(0.5, "rgba(255,255,255,0.15)"),
                            bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.15)"),
                        ),
                    ),
                    ft.Text(right_part, size=12, color="rgba(241,241,243,0.28)"),
                ],
            )
        else:
            hint = ft.Text(hotkey_hint, size=12, color="rgba(241,241,243,0.28)")
    else:
        hint = ft.Text(hotkey_hint, size=12, color="rgba(241,241,243,0.28)")

    # Last transcript box
    last_text_preview = ft.Container(
        visible=bool(last_text),
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        border_radius=10,
        bgcolor="rgba(255,255,255,0.04)",
        border=ft.Border(
            left=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
            top=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
            right=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
            bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
        ),
        width=520,
        content=ft.Text(
            last_text if last_text else "",
            size=13,
            color="rgba(241,241,243,0.40)",
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )

    # Stats row — 2 cards
    stats_row = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=12,
        controls=[
            ft.Container(
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                    controls=[
                        ft.Text(str(total_today), size=24, weight=ft.FontWeight.W_600, color="#3B82F6"),
                        ft.Text("Today", size=11, color="rgba(241,241,243,0.35)"),
                    ],
                ),
                padding=ft.Padding.symmetric(horizontal=24, vertical=14),
                border_radius=10,
                bgcolor="rgba(255,255,255,0.04)",
                border=ft.Border(
                    left=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                    top=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                    right=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                    bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                ),
                width=140,
            ),
            ft.Container(
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                    controls=[
                        ft.Text(str(total_chars), size=24, weight=ft.FontWeight.W_600, color="#3B82F6"),
                        ft.Text("Characters", size=11, color="rgba(241,241,243,0.35)"),
                    ],
                ),
                padding=ft.Padding.symmetric(horizontal=24, vertical=14),
                border_radius=10,
                bgcolor="rgba(255,255,255,0.04)",
                border=ft.Border(
                    left=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                    top=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                    right=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                    bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                ),
                width=140,
            ),
        ],
    )

    # Repaste Last — floating pill
    repaste_button = ft.Container(
        content=ft.Row(
            [icon("copy-01", color="rgba(241,241,243,0.55)", size=12), ft.Text("Repaste Last", size=12, color="rgba(241,241,243,0.55)")],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        visible=bool(last_text),
        padding=ft.Padding.symmetric(horizontal=16, vertical=6),
        border_radius=20,
        bgcolor="rgba(255,255,255,0.06)",
        border=ft.Border(
            left=ft.BorderSide(0.5, "rgba(255,255,255,0.12)"),
            top=ft.BorderSide(0.5, "rgba(255,255,255,0.12)"),
            right=ft.BorderSide(0.5, "rgba(255,255,255,0.12)"),
            bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.12)"),
        ),
        on_click=lambda e: on_repaste_last() if on_repaste_last else None,
        tooltip="Repaste last transcription",
    )

    return ft.Column(
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=20,
        controls=[
            status_indicator,
            record_button,
            hint,
            last_text_preview,
            repaste_button,
            stats_row,
        ],
    )
