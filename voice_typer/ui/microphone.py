import flet as ft
from .styles import Colors
from .icons import icon


class MicrophoneScreen:
    """Microphone screen for testing and configuring audio input."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self.microphones = self._load_microphones()
        # Use config.microphone (ID string) or None for System Default
        self.active_microphone_id = config.microphone if config else None
    
    def _load_microphones(self) -> list[dict]:
        """Load available microphones from the platform."""
        try:
            from voice_typer.platform import list_microphones
            return list_microphones()
        except Exception:
            return []

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Microphone", size=24, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            ft.ElevatedButton(
                                content=ft.Row([icon("refresh", color=ft.Colors.WHITE, size=16), ft.Text("Refresh", color=ft.Colors.WHITE)], spacing=8),
                                on_click=self._refresh,
                                bgcolor=ft.Colors.BLUE_600,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Select and test your microphone",
                        size=14,
                        color=ft.Colors.GREY_600,
                    ),
                    ft.Container(height=10),
                    # System Default option
                    self._build_system_default(),
                    ft.Container(height=10),
                    self._build_microphone_list(),
                    ft.Container(height=20),
                    self._build_test_area(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_system_default(self) -> ft.Control:
        """Build the System Default microphone option."""
        is_active = self.active_microphone_id is None
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                icon(
                                    "microphone",
                                    color=ft.Colors.GREEN_600 if is_active else ft.Colors.GREY_600,
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            "System Default",
                                            size=14,
                                            weight=ft.FontWeight.W_500,
                                        ),
                                        ft.Text(
                                            "Use the operating system's default input device",
                                            size=12,
                                            color=ft.Colors.GREY_600,
                                        ),
                                    ],
                                    spacing=2,
                                ),
                            ],
                            spacing=12,
                            expand=True,
                        ),
                        ft.ElevatedButton(
                            "Active" if is_active else "Use",
                            on_click=lambda e: self._use_microphone(None),
                            bgcolor=ft.Colors.GREEN_600 if is_active else None,
                            color=ft.Colors.WHITE if is_active else None,
                            disabled=is_active,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )
        )

    def _build_microphone_list(self) -> ft.Control:
        if not self.microphones:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("mic-off", size=48, color=ft.Colors.GREY_400),
                        ft.Text(
                            "No microphones found",
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            "Connect a microphone and click Refresh",
                            size=14,
                            color=ft.Colors.GREY_500,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.Column(
            [
                ft.Text("Available Microphones", size=16, weight=ft.FontWeight.W_600),
                ft.Container(height=10),
                *[self._microphone_item(mic) for mic in self.microphones],
            ],
            spacing=8,
        )

    def _microphone_item(self, mic: dict) -> ft.Control:
        # Fix ID comparison: compare mic["id"] (str) with config.microphone (str or None)
        is_active = mic.get("id") == self.active_microphone_id
        is_system_default = mic.get("default", False)
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                icon(
                                    "microphone" if is_active else "mic-outlined",
                                    color=ft.Colors.GREEN_600 if is_active else ft.Colors.GREY_600,
                                ),
                                ft.Column(
                                    [
                                        ft.Row(
                                            [
                                                ft.Text(
                                                    mic.get("name", "Unknown"),
                                                    size=14,
                                                    weight=ft.FontWeight.W_500,
                                                ),
                                                ft.Container(
                                                    content=ft.Text(
                                                        "Default" if is_system_default else "",
                                                        size=10,
                                                        color=ft.Colors.WHITE,
                                                    ),
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
                                            color=ft.Colors.GREY_600,
                                        ),
                                    ],
                                    spacing=2,
                                ),
                            ],
                            spacing=12,
                            expand=True,
                        ),
                        ft.ElevatedButton(
                            "Use" if not is_active else "Active",
                            on_click=lambda e, m=mic: self._use_microphone(m),
                            bgcolor=ft.Colors.GREEN_600 if is_active else None,
                            color=ft.Colors.WHITE if is_active else None,
                            disabled=is_active,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )
        )

    def _build_test_area(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Test Microphone", size=18, weight=ft.FontWeight.W_600),
                        ft.Text(
                            "Speak into your microphone to test audio levels",
                            size=14,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Container(height=10),
                        ft.Row(
                            [
                                ft.ElevatedButton(
                                    content=ft.Row([icon("play-arrow", size=16), ft.Text("Start Test")], spacing=8),
                                    on_click=self._start_test,
                                ),
                                ft.ElevatedButton(
                                    content=ft.Row([icon("stop", size=16), ft.Text("Stop Test")], spacing=8),
                                    on_click=self._stop_test,
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Container(height=10),
                        ft.ProgressBar(value=0, color=ft.Colors.GREEN_600),
                        ft.Text(
                            "Level: 0%",
                            size=12,
                            color=ft.Colors.GREY_600,
                        ),
                    ],
                    spacing=8,
                ),
            )
        )

    def _refresh(self, e):
        """Refresh the list of available microphones."""
        self.microphones = self._load_microphones()
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Found {len(self.microphones)} microphone(s)"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _use_microphone(self, mic: dict | None):
        """Select a microphone for use. None means System Default."""
        if mic is None:
            # System Default
            self.active_microphone_id = None
            self.config.microphone = None
        else:
            # Compare by ID (string), not by name
            self.active_microphone_id = mic.get("id")
            self.config.microphone = mic.get("id")
        self.config.save()
        label = mic.get("name", "System Default") if mic else "System Default"
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Using: {label}"),
            bgcolor=Colors.SUCCESS,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _start_test(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Testing microphone..."),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _stop_test(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Test stopped"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()
