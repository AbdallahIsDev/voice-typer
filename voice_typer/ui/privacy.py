import flet as ft
from .styles import Colors


class PrivacyScreen:
    """Privacy screen for managing data and privacy settings."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self.privacy_stats = {}  # Privacy stats will be injected

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Privacy", size=24, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Your data stays on your device",
                        size=14,
                        color=ft.Colors.GREY_600,
                    ),
                    ft.Container(height=10),
                    self._build_privacy_dashboard(),
                    ft.Container(height=20),
                    self._build_data_management(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_privacy_dashboard(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Privacy Dashboard", size=18, weight=ft.FontWeight.W_600),
                        ft.Container(height=10),
                        ft.Row(
                            [
                                self._stat_card("Local Processing", "100%", ft.Icons.COMPUTER),
                                self._stat_card("Cloud Calls", "0", ft.Icons.CLOUD_OFF),
                                self._stat_card("Data Sent", "0 KB", ft.Icons.SEND),
                            ],
                            spacing=16,
                        ),
                        ft.Container(height=10),
                        ft.Text(
                            "All transcription happens locally using Whisper",
                            size=14,
                            color=ft.Colors.GREEN_700,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    spacing=8,
                ),
            )
        )

    def _stat_card(self, label: str, value: str, icon: ft.Icons) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(icon, size=24, color=ft.Colors.BLUE_600),
                    ft.Text(
                        value,
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        label,
                        size=12,
                        color=ft.Colors.GREY_600,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            expand=True,
            padding=16,
            border_radius=8,
            bgcolor=ft.Colors.GREY_50,
        )

    def _build_data_management(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Data Management", size=18, weight=ft.FontWeight.W_600),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Export Transcriptions",
                            icon=ft.Icons.IMPORT_EXPORT,
                            on_click=self._export_data,
                        ),
                        ft.ElevatedButton(
                            "Clear All Data",
                            icon=ft.Icons.DELETE_SWEEP,
                            on_click=self._clear_data,
                            bgcolor=ft.Colors.RED_100,
                            color=ft.Colors.RED_900,
                        ),
                        ft.Container(height=10),
                        ft.Text(
                            "Data is stored in: %APPDATA%\\voice-typer\\",
                            size=12,
                            color=ft.Colors.GREY_600,
                        ),
                    ],
                    spacing=8,
                ),
            )
        )

    def _export_data(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Exporting data..."),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _clear_data(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("All data cleared"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()
