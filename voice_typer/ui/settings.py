import flet as ft
from .styles import Colors
from .icons import icon


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
                    self._build_llm_settings(),
                    self._build_audio_quality_settings(),
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
                                on_change=lambda e: self.settings.on_autostart_changed(e.control.value) if self.settings.on_autostart_changed else None,
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
                            "Maximum recording duration in seconds (0 = auto)",
                        ),
                        self._setting_row(
                            "Show Notifications",
                            ft.Switch(
                                value=self.config.show_notifications,
                                on_change=lambda e: self.settings.on_notifications_changed(e.control.value) if self.settings.on_notifications_changed else None,
                            ),
                            "Show desktop notifications for transcriptions",
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
                            "Recording Mode",
                            ft.Dropdown(
                                width=160,
                                options=[
                                    ft.dropdown.Option("toggle", "Toggle (F2)"),
                                    ft.dropdown.Option("push_to_talk", "Push-to-Talk"),
                                ],
                                value=self.config.recording_mode,
                                on_change=lambda e: self._update_config("recording_mode", e.control.value),
                            ),
                            "Toggle: press to start/stop. Push-to-talk: hold to record",
                        ),
                        self._setting_row(
                            "Repaste Last",
                            ft.Switch(
                                value=self.config.paste_on_stop,
                                on_change=lambda e: self._update_config("paste_on_stop", e.control.value),
                            ),
                            "Re-paste last transcription when idle",
                        ),
                        self._setting_row(
                            "ESC to Cancel",
                            ft.Switch(
                                value=self.config.esc_cancel_enabled,
                                on_change=lambda e: self._update_config("esc_cancel_enabled", e.control.value),
                            ),
                            "Press Escape to cancel current recording",
                        ),
                        self._setting_row(
                            "Snippets / Templates",
                            ft.Switch(
                                value=self.config.templates_enabled,
                                on_change=lambda e: self._update_config("templates_enabled", e.control.value),
                            ),
                            "Enable text snippets with variables",
                        ),
                        self._setting_row(
                            "Vocabulary Correction",
                            ft.Switch(
                                value=self.config.vocabulary_enabled,
                                on_change=lambda e: self._update_config("vocabulary_enabled", e.control.value),
                            ),
                            "Apply custom vocabulary corrections",
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
                            ft.TextField(value=self._display_hotkey(self.config.hotkey), width=80),
                            "Key to toggle recording",
                        ),
                        self._setting_row(
                            "Cancel Recording",
                            ft.TextField(value="Escape", width=80),
                            "Key to cancel current recording",
                        ),
                        self._setting_row(
                            "Repaste Hotkey",
                            ft.TextField(value=self._display_hotkey(self.config.repaste_hotkey), width=120),
                            "Hotkey for repasting last transcription",
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
                            ft.Switch(
                                value=self.config.auto_punctuation,
                                on_change=lambda e: self._update_config("auto_punctuation", e.control.value),
                            ),
                            "Add punctuation automatically after transcription",
                        ),
                        self._setting_row(
                            "Text Cleanup",
                            ft.Switch(
                                value=self.config.text_cleanup_enabled,
                                on_change=lambda e: self._update_config("text_cleanup_enabled", e.control.value),
                            ),
                            "Remove filler words, fix capitalization",
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _build_llm_settings(self) -> ft.Control:
        """Build LLM text polishing settings section."""
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("LLM Text Polishing", size=18, weight=ft.FontWeight.W_600),
                        self._setting_row(
                            "Enable LLM Polishing",
                            ft.Switch(
                                value=self.config.llm_polish,
                                on_change=lambda e: self._update_config("llm_polish", e.control.value),
                            ),
                            "Use LLM to improve text quality (requires API key)",
                        ),
                        self._setting_row(
                            "LLM API Key",
                            ft.TextField(
                                value=self.config.llm_api_key,
                                width=300,
                                password=True,
                                can_reveal_password=True,
                                on_change=lambda e: self._update_config("llm_api_key", e.control.value or ""),
                            ),
                            "OpenAI-compatible API key for LLM polishing",
                        ),
                        self._setting_row(
                            "LLM API URL",
                            ft.TextField(
                                value=self.config.llm_api_url,
                                width=300,
                                on_change=lambda e: self._update_config("llm_api_url", e.control.value or ""),
                            ),
                            "API endpoint URL for LLM service",
                        ),
                        self._setting_row(
                            "LLM Model",
                            ft.TextField(
                                value=self.config.llm_model,
                                width=200,
                                on_change=lambda e: self._update_config("llm_model", e.control.value or ""),
                            ),
                            "Model name (e.g., gpt-4o-mini)",
                        ),
                        self._setting_row(
                            "LLM Preset",
                            ft.Dropdown(
                                width=160,
                                options=[
                                    ft.dropdown.Option("professional", "Professional"),
                                    ft.dropdown.Option("casual", "Casual"),
                                    ft.dropdown.Option("email", "Email"),
                                    ft.dropdown.Option("code", "Code"),
                                ],
                                value=self.config.llm_preset,
                                on_change=lambda e: self._update_config("llm_preset", e.control.value or "professional"),
                            ),
                            "Polishing style preset",
                        ),
                    ],
                    spacing=10,
                ),
            )
        )

    def _build_audio_quality_settings(self) -> ft.Control:
        """Build audio quality and crash recovery settings section."""
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Audio & Recovery", size=18, weight=ft.FontWeight.W_600),
                        self._setting_row(
                            "Crash Recovery",
                            ft.Switch(
                                value=self.config.crash_recovery_enabled,
                                on_change=lambda e: self._update_config("crash_recovery_enabled", e.control.value),
                            ),
                            "Save unpasted transcriptions for recovery after crash",
                        ),
                        self._setting_row(
                            "Audio Quality Warnings",
                            ft.Switch(
                                value=self.config.audio_quality_warnings,
                                on_change=lambda e: self._update_config("audio_quality_warnings", e.control.value),
                            ),
                            "Warn about clipping, low volume, or noise",
                        ),
                        self._setting_row(
                            "Clipping Warning",
                            ft.Switch(
                                value=self.config.audio_clipping_warning,
                                on_change=lambda e: self._update_config("audio_clipping_warning", e.control.value),
                            ),
                            "Warn when audio is clipping (too loud)",
                        ),
                        self._setting_row(
                            "Low Volume Warning",
                            ft.Switch(
                                value=self.config.audio_low_volume_warning,
                                on_change=lambda e: self._update_config("audio_low_volume_warning", e.control.value),
                            ),
                            "Warn when audio is too quiet",
                        ),
                        self._setting_row(
                            "Noise Warning",
                            ft.Switch(
                                value=self.config.audio_noise_warning,
                                on_change=lambda e: self._update_config("audio_noise_warning", e.control.value),
                            ),
                            "Warn when background noise is detected",
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
                            content=ft.Row([icon("microphone", size=16), ft.Text("Test Microphone")], spacing=8),
                            on_click=self._test_microphone,
                        ),
                        ft.ElevatedButton(
                            content=ft.Row([icon("description", size=16), ft.Text("View Logs")], spacing=8),
                            on_click=self._view_logs,
                        ),
                        ft.ElevatedButton(
                            content=ft.Row([icon("refresh", size=16), ft.Text("Reset to Defaults")], spacing=8),
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

    def _update_config(self, field: str, value):
        """Update a config field and save."""
        try:
            setattr(self.config, field, value)
            self.config.save()
        except Exception:
            pass

    def _display_hotkey(self, hotkey: str) -> str:
        """Convert internal hotkey format to display format."""
        try:
            from voice_typer.settings import display_hotkey
            return display_hotkey(hotkey)
        except Exception:
            return hotkey

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
        """Reset config to defaults."""
        try:
            from voice_typer.config import Config
            defaults = Config()
            # Reset all configurable fields to defaults
            for field in [
                "recording_mode", "esc_cancel_enabled", "auto_punctuation",
                "templates_enabled", "vocabulary_enabled", "llm_polish",
                "crash_recovery_enabled", "audio_quality_warnings",
                "audio_clipping_warning", "audio_low_volume_warning",
                "audio_noise_warning", "paste_on_stop", "text_cleanup_enabled",
                "silence_warning_seconds", "max_recording_seconds",
                "autostart", "show_notifications",
            ]:
                if hasattr(self.config, field):
                    setattr(self.config, field, getattr(defaults, field))
            self.config.save()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Settings reset to defaults"),
                bgcolor=Colors.WARNING,
            )
        except Exception:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Failed to reset settings"),
                bgcolor=Colors.ERROR,
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
            if 0 <= seconds <= 7200:
                self.config.max_recording_seconds = seconds
                self.config.save()
        except ValueError:
            pass
