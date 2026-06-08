"""Home screen — main dashboard with large record button."""

import re
import flet as ft
from voice_typer.ui.styles import Tokens, SETTINGS_MAX_WIDTH, STATUS_COLORS, STATUS_LABELS, RECORD_BUTTON_SIZE, RECORD_BUTTON_COLOR
from voice_typer.ui.icons import icon


def build_home_page(
    status: str = "idle",
    last_text: str = "",
    model_info: str = "",
    device_info: str = "",
    total_today: int = 0,
    total_chars: int = 0,
    on_toggle_dictation=None,
    on_start_dictation=None,
    on_stop_dictation=None,
    hotkey_hint: str = "Press F2 or click to dictate",
    dark: bool = False,
) -> ft.Column:
    is_recording = status == "recording"
    status_color = STATUS_COLORS.get(status, STATUS_COLORS["idle"])
    status_label = STATUS_LABELS.get(status, STATUS_LABELS["idle"])
    tp = Tokens.text_primary(dark)
    ts = Tokens.text_secondary(dark)
    ap = Tokens.accent_primary(dark)
    bg_sidebar = Tokens.bg_sidebar(dark)
    bg_card = Tokens.bg_card(dark)

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

    status_indicator = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
        controls=[
            ft.Container(
                width=8, height=8, border_radius=4,
                bgcolor=status_color,
                animate_opacity=ft.Animation(2000),
            ),
            ft.Text(status_label, size=11, color=ts, weight=ft.FontWeight.W_500),
        ],
    )

    record_button = ft.Container(
        width=72, height=72, border_radius=36,
        bgcolor=RECORD_BUTTON_COLOR,
        alignment=ft.Alignment.CENTER,
        on_click=_on_record_click,
        animate=ft.Animation(1200, ft.AnimationCurve.EASE_IN_OUT),
        content=icon("microphone", color="#FFFFFF", size=28),
    )

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
                    ft.Text("Press", size=12, color=ts),
                    ft.Container(
                        content=ft.Text(key_name, size=11, color=tp, font_family="Consolas"),
                        padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                        border_radius=4,
                        bgcolor=Tokens.bg_card(dark),
                        border=ft.Border.all(0.5, Tokens.border_subtle(dark)),
                    ),
                    ft.Text(right_part, size=12, color=ts),
                ],
            )
        else:
            hint = ft.Text(hotkey_hint, size=12, color=ts)
    else:
        hint = ft.Text(hotkey_hint, size=12, color=ts)

    last_text_preview = ft.Container(
        visible=bool(last_text),
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        border_radius=10,
        bgcolor=bg_card,
        width=520,
        content=ft.Text(
            last_text if last_text else "",
            size=13,
            color=ts,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )

    stats_row = ft.Row(
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=12,
        controls=[
            ft.Container(
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                    controls=[
                        ft.Text(str(total_today), size=24, weight=ft.FontWeight.W_600, color=ap),
                        ft.Text("Today", size=11, color=ts),
                    ],
                ),
                padding=ft.Padding.symmetric(horizontal=24, vertical=14),
                border_radius=10,
                bgcolor=bg_card,
                width=140,
            ),
            ft.Container(
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                    controls=[
                        ft.Text(str(total_chars), size=24, weight=ft.FontWeight.W_600, color=ap),
                        ft.Text("Characters", size=11, color=ts),
                    ],
                ),
                padding=ft.Padding.symmetric(horizontal=24, vertical=14),
                border_radius=10,
                bgcolor=bg_card,
                width=140,
            ),
        ],
    )

    return ft.Container(
        expand=True,
        padding=ft.Padding.symmetric(horizontal=24, vertical=32),
        alignment=ft.Alignment(0, -1),
        content=ft.Container(
            width=SETTINGS_MAX_WIDTH,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=20,
                controls=[
                    status_indicator,
                    record_button,
                    hint,
                    last_text_preview,
                    stats_row,
                ],
            ),
        ),
    )
