import flet as ft
import threading
from .styles import Colors, is_windows_dark_mode
from .icons import icon


class MicrophoneScreen:
    """Microphone screen for testing and configuring audio input."""

    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self.microphones = self._load_microphones()
        self.active_microphone_id = config.microphone if config else None
        self._test_running = False
        self._level_bar = None
        self._level_text = None

    def _is_dark_mode(self) -> bool:
        """Return True if the current theme is dark."""
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
            return list_microphones()
        except Exception:
            return []

    def build(self) -> ft.Control:
        # UX-013: Real level bar and text for microphone test
        dark = self._is_dark_mode()
        self._level_bar = ft.ProgressBar(value=0, color=ft.Colors.GREEN_600, width=400)
        self._level_text = ft.Text("Level: 0%", size=12, color=Colors.text_secondary(dark))

        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Microphone", size=24, weight=ft.FontWeight.BOLD, color=Colors.text_primary(dark)),
                            ft.Container(expand=True),
                            ft.Button(
                                content=ft.Row([icon("refresh", color=ft.Colors.WHITE, size=16), ft.Text("Refresh", color=ft.Colors.WHITE)], spacing=8),
                                on_click=self._refresh,
                                bgcolor=ft.Colors.BLUE_600,
                                tooltip="Refresh microphone list",
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Select and test your microphone",
                        size=14,
                        color=Colors.text_secondary(dark),
                    ),
                    ft.Container(height=10),
                    self._build_system_default(),
                    ft.Container(height=10),
                    self._build_microphone_list(),
                    ft.Container(height=20),
                    self._build_test_area(),
                ],
            ),
        )

    def _build_system_default(self) -> ft.Control:
        dark = self._is_dark_mode()
        is_active = self.active_microphone_id is None
        return ft.Container(
            bgcolor=Colors.card_bg(dark),
            border_radius=8,
            content=ft.Card(
                elevation=0,
                content=ft.Container(
                    padding=16,
                    content=ft.Row(
                    [
                        ft.Row(
                            [
                                icon(
                                    "microphone",
                                    color=ft.Colors.GREEN_600 if is_active else Colors.text_secondary(dark),
                                ),
                                ft.Column(
                                    [
                                        ft.Text("System Default", size=14, weight=ft.FontWeight.W_500, color=Colors.text_primary(dark)),
                                        ft.Text("Use the operating system's default input device", size=12, color=Colors.text_secondary(dark)),
                                    ],
                                    spacing=2,
                                ),
                            ],
                            spacing=12,
                            expand=True,
                        ),
                        ft.Button(
                            "Active" if is_active else "Use",
                            on_click=lambda e: self._use_microphone(None),
                            bgcolor=ft.Colors.GREEN_600 if is_active else None,
                            color=ft.Colors.WHITE if is_active else None,
                            disabled=is_active,
                            tooltip="Use system default microphone",
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ),
        ),
    )

    def _build_microphone_list(self) -> ft.Control:
        dark = self._is_dark_mode()
        if not self.microphones:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("mic-off", size=48, color=ft.Colors.GREY_400 if not dark else ft.Colors.GREY_500),
                        ft.Text("No microphones found", size=16, color=ft.Colors.GREY_600 if not dark else ft.Colors.GREY_400),
                        ft.Text("Connect a microphone and click Refresh", size=14, color=ft.Colors.GREY_500 if not dark else ft.Colors.GREY_500),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.Column(
            [
                ft.Text("Available Microphones", size=16, weight=ft.FontWeight.W_600, color=Colors.text_primary(dark)),
                ft.Container(height=10),
                *[self._microphone_item(mic) for mic in self.microphones],
            ],
            spacing=8,
        )

    def _microphone_item(self, mic: dict) -> ft.Control:
        dark = self._is_dark_mode()
        is_active = mic.get("id") == self.active_microphone_id
        is_system_default = mic.get("default", False)
        return ft.Container(
            bgcolor=Colors.card_bg(dark),
            border_radius=8,
            content=ft.Card(
                elevation=0,
                content=ft.Container(
                    padding=16,
                    content=ft.Row(
                    [
                        ft.Row(
                            [
                                icon("microphone" if is_active else "mic-outlined",
                                     color=ft.Colors.GREEN_600 if is_active else Colors.text_secondary(dark)),
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Text(mic.get("name", "Unknown"), size=14, weight=ft.FontWeight.W_500, color=Colors.text_primary(dark)),
                                                ft.Container(
                                                    content=ft.Text("Default" if is_system_default else "", size=10, color=ft.Colors.WHITE),
                                                    bgcolor=ft.Colors.BLUE_600 if is_system_default else ft.Colors.TRANSPARENT,
                                                    padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                                                    border_radius=6,
                                                    visible=is_system_default,
                                                ),
                                            ],
                                            spacing=6,
                                        ),
                                        ft.Text(
                                            f"Channels: {mic.get('channels', 1)} | Rate: {mic.get('rate', 44100)}Hz",
                                            size=12,
                                            color=Colors.text_secondary(dark),
                                        ),
                                    ],
                                    spacing=2,
                                ),
                            ],
                            spacing=12,
                            expand=True,
                        ),
                        ft.Button(
                            "Use" if not is_active else "Active",
                            on_click=lambda e, m=mic: self._use_microphone(m),
                            bgcolor=ft.Colors.GREEN_600 if is_active else None,
                            color=ft.Colors.WHITE if is_active else None,
                            disabled=is_active,
                            tooltip=f"Use {mic.get('name', 'microphone')}",
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ),
        ),
    )

    def _build_test_area(self) -> ft.Control:
        dark = self._is_dark_mode()
        return ft.Container(
            bgcolor=Colors.card_bg(dark),
            border_radius=8,
            content=ft.Card(
                elevation=0,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                    [
                        ft.Text("Test Microphone", size=18, weight=ft.FontWeight.W_600, color=Colors.text_primary(dark)),
                        ft.Text(
                            "Speak into your microphone to test audio levels",
                            size=14,
                            color=Colors.text_secondary(dark),
                        ),
                        ft.Container(height=10),
                        ft.Row(
                            [
                                ft.Button(
                                    content=ft.Row([icon("play-arrow", size=16), ft.Text("Start Test")], spacing=8),
                                    on_click=self._start_test,
                                    tooltip="Start microphone test",
                                ),
                                ft.Button(
                                    content=ft.Row([icon("stop", size=16), ft.Text("Stop Test")], spacing=8),
                                    on_click=self._stop_test,
                                    tooltip="Stop microphone test",
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Container(height=10),
                        self._level_bar,
                        self._level_text,
                    ],
                    spacing=8,
                ),
            ),
        ),
    )


    def _refresh(self, e):
        self.microphones = self._load_microphones()
        self.reload()
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Found {len(self.microphones)} microphone(s)"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

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
            bgcolor=Colors.SUCCESS,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _start_test(self, e):
        """UX-013: Real microphone test with audio level meter."""
        self._test_running = True
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Microphone test started — speak into your mic"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

        def _capture_levels():
            """Capture audio levels in background and update the level meter."""
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
                            self._level_bar.color = ft.Colors.RED_600
                        elif level > 0.3:
                            self._level_bar.color = ft.Colors.AMBER_600
                        else:
                            self._level_bar.color = ft.Colors.GREEN_600
                        self.page.update()
                    except Exception:
                        pass

                with sd.InputStream(
                    callback=_callback,
                    channels=1,
                    samplerate=16000,
                    blocksize=1024,
                    device=self.active_microphone_id,
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
        """Stop the microphone test."""
        self._test_running = False
        if self._level_bar:
            self._level_bar.value = 0
        if self._level_text:
            self._level_text.value = "Level: 0%"
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Microphone test stopped"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()
