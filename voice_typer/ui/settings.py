import flet as ft
from .styles import Colors


class SettingsScreen:
    """Settings screen for the Voice Typer desktop app."""

    def __init__(self, page: ft.Page, config, settings_controller):
        self.page = page
        self.config = config
        self.settings = settings_controller

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Text("Settings", size=24, weight=ft.FontWeight.BOLD),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    self._build_general_settings(),
                    self._build_recording_settings(),
                    self._build_hotkey_settings(),
                    self._build_text_processing(),
                    self._build_troubleshooting(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_general_settings(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("General", size=18, weight=ft.FontWeight.W_600),
                        self._setting_row(
                            "Launch at Startup",
                            ft.Switch(
                                value=self.config.autostart,
                                on_change=lambda e: self.settings.on_autostart_changed(e.control.value),
                            ),
                            "Start Voice Typer when Windows starts",
                        ),
                        self._setting_row(
                            "Silence Warning Timeout",
                            ft.TextField(
                                value=str(self.config.silence_warning_seconds),
                                width=80,
                                on_change=lambda e: self._update_silence_warning(e.control.value),
                            ),
                            "Seconds before showing silence warning",
                        ),
                        self._setting_row(
                            "Max Recording Timeout",
                            ft.TextField(
                                value=str(self.config.max_recording_seconds),
                                width=80,
                                on_change=lambda e: self._update_max_recording(e.control.value),
                            ),
                            "Maximum recording duration in seconds",
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _build_recording_settings(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Recording", size=18, weight=ft.FontWeight.W_600),
                        self._setting_row(
                            "Push-to-Talk",
                            ft.Switch(value=False),
                            "Hold key to record (vs. toggle)",
                        ),
                        self._setting_row(
                            "Repaste Last",
                            ft.Switch(value=True),
                            "Re-paste last transcription when idle",
                        ),
                        self._setting_row(
                            "Snippets",
                            ft.Switch(value=True),
                            "Enable text snippets with variables",
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _build_hotkey_settings(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Hotkeys", size=18, weight=ft.FontWeight.W_600),
                        self._setting_row(
                            "Start/Stop Recording",
                            ft.TextField(value="F2", width=80),
                            "Key to toggle recording",
                        ),
                        self._setting_row(
                            "Cancel Recording",
                            ft.TextField(value="Escape", width=80),
                            "Key to cancel current recording",
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _build_text_processing(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Text Processing", size=18, weight=ft.FontWeight.W_600),
                        self._setting_row(
                            "Auto-Punctuation",
                            ft.Switch(value=True),
                            "Add punctuation automatically",
                        ),
                        self._setting_row(
                            "Text Cleanup",
                            ft.Switch(value=True),
                            "Remove filler words, fix capitalization",
                        ),
                        self._setting_row(
                            "Vocabulary Correction",
                            ft.Switch(value=True),
                            "Apply custom vocabulary corrections",
                        ),
                        self._setting_row(
                            "Template Matching",
                            ft.Switch(value=True),
                            "Match text against voice templates",
                        ),
                        self._setting_row(
                            "LLM Text Polishing",
                            ft.Switch(value=False),
                            "Use LLM to improve text quality (requires API key)",
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _build_troubleshooting(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Troubleshooting", size=18, weight=ft.FontWeight.W_600),
                        ft.ElevatedButton(
                            "Test Microphone",
                            icon=ft.Icons.MIC,
                            on_click=self._test_microphone,
                        ),
                        ft.ElevatedButton(
                            "View Logs",
                            icon=ft.Icons.DESCRIPTION,
                            on_click=self._view_logs,
                        ),
                        ft.ElevatedButton(
                            "Reset to Defaults",
                            icon=ft.Icons.REFRESH,
                            on_click=self._reset_defaults,
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _setting_row(self, label: str, control: ft.Control, description: str) -> ft.Control:
        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(label, size=14, weight=ft.FontWeight.W_500),
                        ft.Text(description, size=12, color=ft.Colors.GREY_600),
                    ],
                    spacing=2,
                    expand=True,
                ),
                control,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    def _test_microphone(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Testing microphone..."),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _view_logs(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Opening log viewer..."),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _reset_defaults(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Settings reset to defaults"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()
    
    def _update_silence_warning(self, value: str):
        """Update silence warning timeout."""
        try:
            seconds = float(value)
            if 3 <= seconds <= 30:
                self.config.silence_warning_seconds = seconds
                self.config.save()
        except ValueError:
            pass
    
    def _update_max_recording(self, value: str):
        """Update max recording timeout."""
        try:
            seconds = int(value)
            if 60 <= seconds <= 7200:
                self.config.max_recording_seconds = seconds
                self.config.save()
        except ValueError:
            pass
