import flet as ft
import threading
from .styles import Tokens, SETTINGS_MAX_WIDTH, is_windows_dark_mode
from .icons import icon


class MicrophoneScreen:
    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self.microphones = self._load_microphones()
        self.active_microphone_id = config.microphone if config else None
        self._test_running = False
        self._level_bar = None
        self._level_text = None

    def _is_dark(self) -> bool:
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _load_microphones(self) -> list[dict]:
        try:
            from voice_typer.platform import list_microphones
            mics = list_microphones()
            seen = set()
            deduped = []
            for mic in mics:
                mid = mic.get("id")
                if mid is None or mid not in seen:
                    if mid is not None:
                        seen.add(mid)
                    deduped.append(mic)
            return deduped
        except Exception:
            return []

    def build(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        self._level_bar = ft.ProgressBar(value=0, color=ap, bgcolor="rgba(37,99,235,0.2)")
        self._level_text = ft.Text("Level: 0%", size=12, color=ts)

        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=24, vertical=32),
            alignment=ft.Alignment(0, -1),
            content=ft.Container(
                width=SETTINGS_MAX_WIDTH,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text("Microphone", size=20, weight=ft.FontWeight.W_600, color=tp),
                                ft.Container(expand=True),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Container(height=8),
                        ft.Text("Select and test your microphone", size=13, color=ts),
                        ft.Container(height=20),
                        self._build_system_default(),
                        ft.Container(height=16),
                        self._build_test_area(),
                        ft.Container(height=24),
                        self._build_microphone_list(),
                    ],
                ),
            ),
        )

    def _active_badge(self) -> ft.Container:
        return ft.Container(
            content=ft.Text("Active", size=11, weight=ft.FontWeight.W_600, color="#10B981"),
            padding=ft.Padding(left=8, right=8, top=4, bottom=4),
            border_radius=4,
            bgcolor="rgba(16,185,129,0.1)",
            border=ft.Border.all(1, "rgba(16,185,129,0.2)"),
        )

    def _build_system_default(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        is_active = self.active_microphone_id is None
        card = ft.Container(
            bgcolor=Tokens.bg_card(dark),
            border=ft.Border.all(1, Tokens.border_subtle(dark)),
            border_radius=10,
            padding=ft.Padding(left=20, right=20, top=16, bottom=16),
            content=ft.Row(
                [
                    ft.Row(
                        [
                            icon("microphone", color="#10B981" if is_active else ts),
                            ft.Column(
                                [
                                    ft.Text("System Default", size=14, weight=ft.FontWeight.W_500, color=tp),
                                    ft.Text("Use the operating system's default input device", size=12, color=ts),
                                ],
                                spacing=2,
                            ),
                        ],
                        spacing=12,
                        expand=True,
                    ),
                    self._active_badge() if is_active else ft.Container(
                        content=ft.Text("Use", size=12, weight=ft.FontWeight.W_500, color=ts),
                        bgcolor="transparent",
                        border=ft.Border.all(0.5, Tokens.border_subtle(dark)),
                        border_radius=7,
                        padding=ft.Padding(left=14, right=14, top=5, bottom=5),
                        on_click=lambda e: self._use_microphone(None),
                        ink=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

        controls = [card]
        if is_active:
            level_bar = ft.Container(
                height=3,
                border_radius=2,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                content=self._level_bar,
            )
            controls.append(level_bar)

        return ft.Column(controls, spacing=0, margin=ft.Margin(left=0, right=0, top=0, bottom=24))

    def _build_microphone_list(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        if not self.microphones:
            return ft.Container(
                padding=40,
                alignment=ft.alignment.center,
                content=ft.Column(
                    [
                        icon("mic-off", size=48, color=ts),
                        ft.Text("No microphones found", size=16, color=ts),
                        ft.Text("Connect a microphone and restart", size=14, color=ts),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.Column(
            [
                ft.Container(
                    content=ft.Text("AVAILABLE MICROPHONES", size=11, weight=ft.FontWeight.W_600, color=ts),
                    padding=ft.Padding(left=20, right=20, top=12, bottom=8),
                ),
                *[self._microphone_item(mic) for mic in self.microphones],
            ],
            spacing=0,
        )

    def _microphone_item(self, mic: dict) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        is_active = mic.get("id") == self.active_microphone_id
        is_system_default = mic.get("default", False)
        return ft.Container(
            padding=ft.Padding(left=20, right=20, top=12, bottom=12),
            border=ft.Border(bottom=ft.BorderSide(0.5, Tokens.border_subtle(dark))),
            content=ft.Row(
                [
                    ft.Row(
                        [
                            icon("microphone" if is_active else "mic-outlined",
                                 color="#10B981" if is_active else ts),
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(mic.get("name", "Unknown"), size=13, weight=ft.FontWeight.W_500, color=tp),
                                            ft.Container(
                                                content=ft.Text("Default", size=10, color="#FFFFFF"),
                                                bgcolor=ap,
                                                padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                                                border_radius=6,
                                                visible=is_system_default,
                                            ),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.Container(
                                        content=ft.Row(
                                            [
                                                ft.Text(f"Channels: {mic.get('channels', 1)}", size=11, color=ts),
                                                ft.Text(f"Rate: {mic.get('rate', 44100)}Hz", size=11, color=ts),
                                            ],
                                            spacing=10,
                                        ),
                                        margin=ft.Margin(top=3),
                                    ),
                                ],
                                spacing=2,
                            ),
                        ],
                        spacing=12,
                        expand=True,
                    ),
                    self._active_badge() if is_active else ft.Container(
                        content=ft.Text("Use", size=12, weight=ft.FontWeight.W_500, color=ts),
                        bgcolor="transparent",
                        border=ft.Border.all(0.5, Tokens.border_subtle(dark)),
                        border_radius=7,
                        padding=ft.Padding(left=14, right=14, top=5, bottom=5),
                        on_click=lambda e, m=mic: self._use_microphone(m),
                        ink=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _build_test_area(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        return ft.Row(
            [
                ft.Container(
                    content=ft.Row([icon("play-arrow", size=14, color="#FFFFFF"), ft.Text("Start Test", size=12, color="#FFFFFF")], spacing=6),
                    bgcolor=ap,
                    padding=ft.Padding(left=14, right=16, top=8, bottom=8),
                    border_radius=7,
                    on_click=self._start_test,
                    ink=True,
                ),
                ft.Container(
                    content=ft.Row([icon("stop", size=14, color=ts), ft.Text("Stop Test", size=12, color=ts)], spacing=6),
                    bgcolor=Tokens.bg_card(dark),
                    border=ft.Border.all(0.5, Tokens.border_subtle(dark)),
                    border_radius=7,
                    padding=ft.Padding(left=14, right=16, top=8, bottom=8),
                    on_click=self._stop_test,
                    ink=True,
                ),
                ft.Container(expand=True),
                self._level_text,
            ],
            alignment=ft.MainAxisAlignment.START,
        )

    def _use_microphone(self, mic: dict | None):
        if mic is None:
            self.active_microphone_id = None
            self.config.microphone = None
        else:
            self.active_microphone_id = mic.get("id")
            self.config.microphone = mic.get("id")
        self.config.save()
        self.reload()
        label = mic.get("name", "System Default") if mic else "System Default"
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Using: {label}"),
            bgcolor=Tokens.SUCCESS_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _start_test(self, e):
        self._test_running = True
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Microphone test started \u2014 speak into your mic"),
            bgcolor=Tokens.ACCENT_PRIMARY_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

        def _capture_levels():
            try:
                import numpy as np
                import sounddevice as sd

                def _callback(indata, frames, time_info, status):
                    if not self._test_running:
                        raise sd.CallbackStop
                    rms = float(np.sqrt(np.mean(np.square(indata))))
                    level = min(1.0, rms * 10)
                    try:
                        self._level_bar.value = level
                        self._level_text.value = f"Level: {int(level * 100)}%"
                        if level > 0.7:
                            self._level_bar.color = Tokens.ACCENT_DANGER_DARK
                        elif level > 0.3:
                            self._level_bar.color = "#F59E0B"
                        else:
                            self._level_bar.color = Tokens.ACCENT_PRIMARY_DARK
                        self.page.update()
                    except Exception:
                        pass

                with sd.InputStream(
                    callback=_callback, channels=1, samplerate=16000,
                    blocksize=1024, device=self.active_microphone_id,
                ):
                    while self._test_running:
                        import time
                        time.sleep(0.1)
            except Exception as exc:
                try:
                    self._level_text.value = f"Test error: {exc}"
                    self.page.update()
                except Exception:
                    pass

        t = threading.Thread(target=_capture_levels, daemon=True)
        t.start()

    def _stop_test(self, e):
        self._test_running = False
        if self._level_bar:
            self._level_bar.value = 0
            self._level_bar.color = Tokens.ACCENT_PRIMARY_DARK
        if self._level_text:
            self._level_text.value = "Level: 0%"
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Microphone test stopped"),
            bgcolor=Tokens.WARNING_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()
