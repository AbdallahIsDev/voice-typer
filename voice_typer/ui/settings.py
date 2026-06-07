import flet as ft
import os
import sys
import logging
from .styles import Colors, PAGE_MAX_WIDTH, get_theme
from .icons import icon

log = logging.getLogger(__name__)


class SettingsScreen:
    """Settings screen for the Voice Typer desktop app."""

    def __init__(self, page: ft.Page, config, settings_controller, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self.settings = settings_controller
        self._dark = True

    def _is_dark_mode(self) -> bool:
        if self.page is None:
            return True
        try:
            if self.page.theme_mode == ft.ThemeMode.DARK:
                return True
            if self.page.theme_mode == ft.ThemeMode.LIGHT:
                return False
            from .styles import is_windows_dark_mode
            return is_windows_dark_mode()
        except Exception:
            return True

    def _section_card(self, title: str, controls: list) -> ft.Control:
        rows = [
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=20, vertical=14),
                border=ft.Border(bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.06)")),
                content=ft.Text(title, size=11, weight=ft.FontWeight.W_600,
                                color="rgba(241,241,243,0.35)",
                                style=ft.TextStyle(letter_spacing=2.5)),
            ),
        ]
        for i, ctrl in enumerate(controls):
            is_last = i == len(controls) - 1
            b = None if is_last else ft.Border(bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.05)"))
            rows.append(
                ft.Container(padding=ft.Padding.symmetric(horizontal=20, vertical=14), border=b, content=ctrl)
            )
        return ft.Container(
            border_radius=12,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            bgcolor="rgba(255,255,255,0.03)",
            border=ft.Border(
                left=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                top=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                right=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
                bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.07)"),
            ),
            content=ft.Column(rows, spacing=0),
        )

    def _setting_row(self, label: str, control: ft.Control, description: str) -> ft.Control:
        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(label, size=13, weight=ft.FontWeight.W_500,
                                color="rgba(241,241,243,0.82)"),
                        ft.Text(description, size=12,
                                color="rgba(241,241,243,0.28)"),
                    ],
                    spacing=2,
                    expand=True,
                ),
                control,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _premium_switch(self, value: bool, on_change) -> ft.Switch:
        return ft.Switch(
            value=value,
            on_change=on_change,
            active_color="#3B82F6",
            active_track_color="#3B82F6",
            track_color={ft.ControlState.DEFAULT: "rgba(255,255,255,0.14)"},
            width=40,
            height=22,
            thumb_color="#FFFFFF",
        )

    def _input_with_unit(self, value: str, unit: str, on_change, width=80) -> ft.Row:
        return ft.Row(
            [
                ft.TextField(
                    value=value,
                    width=width,
                    border_radius=8,
                    text_size=13,
                    text_align=ft.TextAlign.CENTER,
                    content_padding=ft.Padding.symmetric(horizontal=12, vertical=7),
                    bgcolor="rgba(255,255,255,0.05)",
                    border_color="rgba(255,255,255,0.10)",
                    on_change=on_change,
                ),
                ft.Text(unit, size=13, color="rgba(241,241,243,0.28)"),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _dropdown(self, **kwargs) -> ft.Dropdown:
        return ft.Dropdown(
            bgcolor="rgba(255,255,255,0.06)",
            border_radius=8,
            border_color="rgba(255,255,255,0.12)",
            text_size=13,
            **kwargs,
        )

    def _text_field(self, **kwargs) -> ft.TextField:
        return ft.TextField(
            bgcolor="rgba(255,255,255,0.05)",
            border_radius=8,
            border_color="rgba(255,255,255,0.10)",
            text_size=13,
            content_padding=ft.Padding.symmetric(horizontal=12, vertical=7),
            **kwargs,
        )

    def _hotkey_field(self, **kwargs) -> ft.TextField:
        return ft.TextField(
            bgcolor="rgba(255,255,255,0.06)",
            border_radius=8,
            border_color="rgba(255,255,255,0.12)",
            text_size=13,
            content_padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            font_family="monospace",
            text_align=ft.TextAlign.CENTER,
            **kwargs,
        )

    def build(self) -> ft.Control:
        self._dark = self._is_dark_mode()
        page_bg = "#0F1117" if self._dark else Colors.surface(False)

        return ft.Container(
            expand=True,
            bgcolor=page_bg,
            padding=ft.Padding.symmetric(horizontal=40, vertical=32),
            content=ft.Column(
                [
                    ft.Container(
                        width=PAGE_MAX_WIDTH,
                        content=ft.Column(
                            [
                                self._build_header(),
                                ft.Container(height=24),
                                self._build_application_section(),
                                ft.Container(height=16),
                                self._build_recording_section(),
                                ft.Container(height=16),
                                self._build_speech_section(),
                                ft.Container(height=16),
                                self._build_audio_recovery_section(),
                                ft.Container(height=16),
                                self._build_accessibility_section(),
                                ft.Container(height=16),
                                self._build_troubleshooting(),
                                ft.Container(height=32),
                            ],
                            spacing=0,
                        ),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_header(self) -> ft.Control:
        return ft.Column(
            [
                ft.Text("Settings", size=20, weight=ft.FontWeight.W_600,
                        color="#FFFFFF" if self._dark else Colors.text_primary(False)),
                ft.Text("Manage Voice Typer preferences and behavior.",
                        size=13, color="#94A3B8" if self._dark else Colors.text_secondary(False)),
                ft.Container(height=16),
                self._build_search(),
            ],
            spacing=4,
        )

    def _build_search(self) -> ft.Control:
        return ft.Container(
            content=ft.TextField(
                hint_text="Search settings...",
                hint_style=ft.TextStyle(size=13, color="rgba(241,241,243,0.28)"),
                prefix=icon("search", color="rgba(241,241,243,0.28)", size=16),
                border_radius=8,
                bgcolor="rgba(255,255,255,0.05)",
                border_color="rgba(255,255,255,0.10)",
                text_size=13,
                content_padding=ft.Padding(left=36, right=14, top=8, bottom=8),
                width=280,
                on_change=self._filter_settings,
            ),
            border_radius=8,
        )

    def _build_application_section(self) -> ft.Control:
        current_theme = getattr(self.config, 'theme_mode', 'system')
        return self._section_card("Application", [
            self._setting_row(
                "Launch at Startup",
                self._premium_switch(
                    value=self.config.autostart,
                    on_change=lambda e: self.settings.on_autostart_changed(e.control.value) if self.settings.on_autostart_changed else None,
                ),
                "Start Voice Typer when Windows starts",
            ),
            self._setting_row(
                "Show Notifications",
                self._premium_switch(
                    value=self.config.show_notifications,
                    on_change=lambda e: self.settings.on_notifications_changed(e.control.value) if self.settings.on_notifications_changed else None,
                ),
                "Show desktop notifications for transcriptions",
            ),
            self._setting_row(
                "Tray Left-click Action",
                self._dropdown(
                    value=self.config.tray_left_click_action,
                    width=160,
                    options=[
                        ft.dropdown.Option("open_app", "Open App"),
                        ft.dropdown.Option("toggle_dictation", "Toggle Dictation"),
                    ],
                    on_select=lambda e: self._on_tray_click_change(e.control.value),
                ),
                "What happens when you left-click the tray icon",
            ),
            self._setting_row(
                "Theme",
                self._dropdown(
                    value=current_theme,
                    width=160,
                    options=[
                        ft.dropdown.Option("system", "System Default"),
                        ft.dropdown.Option("light", "Light"),
                        ft.dropdown.Option("dark", "Dark"),
                    ],
                    on_select=lambda e: self._change_theme(e.control.value),
                ),
                "Choose light, dark, or system theme",
            ),
        ])

    def _on_tray_click_change(self, value: str):
        self._update_config("tray_left_click_action", value)

    def _build_recording_section(self) -> ft.Control:
        return self._section_card("Recording", [
            self._setting_row(
                "Recording Mode",
                self._dropdown(
                    width=160,
                    options=[
                        ft.dropdown.Option("toggle", "Toggle (F2)"),
                        ft.dropdown.Option("push_to_talk", "Push-to-Talk"),
                    ],
                    value=self.config.recording_mode,
                    on_select=lambda e: self._update_config("recording_mode", e.control.value),
                ),
                "Toggle: press to start/stop. Push-to-talk: hold to record",
            ),
            self._setting_row(
                "Silence Warning Timeout",
                self._input_with_unit(
                    value=str(self.config.silence_warning_seconds),
                    unit="sec",
                    on_change=lambda e: self._update_silence_warning(e.control.value),
                ),
                "Seconds before showing silence warning",
            ),
            self._setting_row(
                "Max Recording Timeout",
                self._input_with_unit(
                    value=str(self.config.max_recording_seconds),
                    unit="sec",
                    on_change=lambda e: self._update_max_recording(e.control.value),
                ),
                "Maximum recording duration in seconds (0 = auto)",
            ),
            self._setting_row(
                "Auto-paste on Stop",
                self._premium_switch(
                    value=self.config.paste_on_stop,
                    on_change=lambda e: self._update_config("paste_on_stop", e.control.value),
                ),
                "Paste transcribed text into the focused field when recording stops",
            ),
            self._setting_row(
                "ESC to Cancel",
                self._premium_switch(
                    value=self.config.esc_cancel_enabled,
                    on_change=lambda e: self._update_config("esc_cancel_enabled", e.control.value),
                ),
                "Press Escape to cancel current recording",
            ),
            self._setting_row(
                "Start/Stop Hotkey",
                self._hotkey_field(
                    value=self._display_hotkey(self.config.hotkey),
                    width=80,
                    on_change=lambda e: self._on_hotkey_change(e.control.value, "hotkey"),
                    tooltip="Key to toggle recording",
                ),
                "Key to toggle recording",
            ),
            self._setting_row(
                "Repaste Hotkey",
                self._hotkey_field(
                    value=self._display_hotkey(self.config.repaste_hotkey),
                    width=80,
                    on_change=lambda e: self._on_hotkey_change(e.control.value, "repaste_hotkey"),
                    tooltip="Hotkey for repasting last transcription",
                ),
                "Hotkey for repasting last transcription",
            ),
            self._setting_row(
                "Snippets / Templates",
                self._premium_switch(
                    value=self.config.templates_enabled,
                    on_change=lambda e: self._update_config("templates_enabled", e.control.value),
                ),
                "Enable text snippets with variables",
            ),
            self._setting_row(
                "Vocabulary Correction",
                self._premium_switch(
                    value=self.config.vocabulary_enabled,
                    on_change=lambda e: self._update_config("vocabulary_enabled", e.control.value),
                ),
                "Apply custom vocabulary corrections",
            ),
        ])

    def _build_speech_section(self) -> ft.Control:
        return self._section_card("Speech Processing", [
            self._setting_row(
                "Auto-Punctuation",
                self._premium_switch(
                    value=self.config.auto_punctuation,
                    on_change=lambda e: self._update_config("auto_punctuation", e.control.value),
                ),
                "Add punctuation automatically after transcription",
            ),
            self._setting_row(
                "Text Cleanup",
                self._premium_switch(
                    value=self.config.text_cleanup_enabled,
                    on_change=lambda e: self._update_config("text_cleanup_enabled", e.control.value),
                ),
                "Remove filler words, fix capitalization",
            ),
            self._setting_row(
                "LLM Polishing",
                self._premium_switch(
                    value=self.config.llm_polish,
                    on_change=lambda e: self._update_config("llm_polish", e.control.value),
                ),
                "Use LLM to improve text quality (requires API key)",
            ),
            self._setting_row(
                "LLM API Key",
                self._text_field(
                    value=self.config.llm_api_key,
                    width=220,
                    password=True,
                    can_reveal_password=True,
                    on_change=lambda e: self._update_config("llm_api_key", e.control.value or ""),
                ),
                "OpenAI-compatible API key for LLM polishing",
            ),
            self._setting_row(
                "LLM API URL",
                self._text_field(
                    value=self.config.llm_api_url,
                    width=220,
                    on_change=lambda e: self._update_config("llm_api_url", e.control.value or ""),
                ),
                "API endpoint URL for LLM service",
            ),
            self._setting_row(
                "LLM Model",
                self._text_field(
                    value=self.config.llm_model,
                    width=180,
                    on_change=lambda e: self._update_config("llm_model", e.control.value or ""),
                ),
                "Model name (e.g., gpt-4o-mini)",
            ),
            self._setting_row(
                "LLM Preset",
                self._dropdown(
                    width=160,
                    options=[
                        ft.dropdown.Option("professional", "Professional"),
                        ft.dropdown.Option("casual", "Casual"),
                        ft.dropdown.Option("email", "Email"),
                        ft.dropdown.Option("code", "Code"),
                    ],
                    value=self.config.llm_preset,
                    on_select=lambda e: self._update_config("llm_preset", e.control.value or "professional"),
                ),
                "Polishing style preset",
            ),
        ])

    def _build_audio_recovery_section(self) -> ft.Control:
        return self._section_card("Audio & Recovery", [
            self._setting_row(
                "Crash Recovery",
                self._premium_switch(
                    value=self.config.crash_recovery_enabled,
                    on_change=lambda e: self._update_config("crash_recovery_enabled", e.control.value),
                ),
                "Save unpasted transcriptions for recovery after crash",
            ),
            self._setting_row(
                "Audio Quality Warnings",
                self._premium_switch(
                    value=self.config.audio_quality_warnings,
                    on_change=lambda e: self._update_config("audio_quality_warnings", e.control.value),
                ),
                "Warn about clipping, low volume, or noise",
            ),
            self._setting_row(
                "Clipping Warning",
                self._premium_switch(
                    value=self.config.audio_clipping_warning,
                    on_change=lambda e: self._update_config("audio_clipping_warning", e.control.value),
                ),
                "Warn when audio is clipping (too loud)",
            ),
            self._setting_row(
                "Low Volume Warning",
                self._premium_switch(
                    value=self.config.audio_low_volume_warning,
                    on_change=lambda e: self._update_config("audio_low_volume_warning", e.control.value),
                ),
                "Warn when audio is too quiet",
            ),
            self._setting_row(
                "Noise Warning",
                self._premium_switch(
                    value=self.config.audio_noise_warning,
                    on_change=lambda e: self._update_config("audio_noise_warning", e.control.value),
                ),
                "Warn when background noise is detected",
            ),
        ])

    def _build_accessibility_section(self) -> ft.Control:
        high_contrast = getattr(self.config, 'high_contrast', False)
        text_size = getattr(self.config, 'text_size', 14)
        return self._section_card("Accessibility", [
            self._setting_row(
                "High Contrast",
                self._premium_switch(
                    value=high_contrast,
                    on_change=lambda e: self._update_config("high_contrast", e.control.value),
                ),
                "Enable high-contrast mode for better visibility",
            ),
            self._setting_row(
                "Text Size",
                ft.Slider(
                    min=12,
                    max=24,
                    divisions=6,
                    label="{value}",
                    value=text_size,
                    on_change=lambda e: self._update_config("text_size", int(e.control.value)),
                    width=200,
                    tooltip="Adjust text size throughout the app",
                ),
                "Adjust base text size (12-24px)",
            ),
        ])

    def _build_troubleshooting(self) -> ft.Control:
        return self._section_card("Troubleshooting", [
            ft.Row(
                [
                    ft.Container(
                        content=ft.Row(
                            [icon("microphone", size=16), ft.Text("Test Microphone")],
                            spacing=8,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
                        border_radius=10,
                        bgcolor="rgba(255,255,255,0.06)",
                        on_click=self._test_microphone,
                        tooltip="Test your microphone input levels",
                    ),
                    ft.Container(
                        content=ft.Row(
                            [icon("description", size=16), ft.Text("View Logs")],
                            spacing=8,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
                        border_radius=10,
                        bgcolor="rgba(255,255,255,0.06)",
                        on_click=self._view_logs,
                        tooltip="Open the application log file",
                    ),
                    ft.Container(
                        content=ft.Row(
                            [icon("refresh", color=Colors.ERROR, size=16),
                             ft.Text("Reset to Defaults", color=Colors.ERROR)],
                            spacing=8,
                        ),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
                        border_radius=10,
                        bgcolor="rgba(239,68,68,0.1)",
                        on_click=self._reset_defaults,
                        tooltip="Reset all settings to default values",
                    ),
                ],
                spacing=12,
                wrap=True,
            ),
        ])

    def _update_config(self, field: str, value):
        try:
            setattr(self.config, field, value)
            self.config.save()
            self.reload()
        except Exception:
            pass

    def _on_hotkey_change(self, display_value: str, field: str):
        try:
            from voice_typer.settings import format_function_hotkey
            internal = format_function_hotkey(display_value)
            setattr(self.config, field, internal)
            self.config.save()
            self.reload()
            if field == "hotkey" and self.settings.on_hotkey_changed:
                self.settings.on_hotkey_changed(internal)
        except ValueError:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Invalid hotkey: {display_value}"),
                bgcolor=Colors.ERROR,
            )
            self.page.snack_bar.open = True
            self.page.update()
        except Exception:
            pass

    def _display_hotkey(self, hotkey: str) -> str:
        try:
            from voice_typer.settings import display_hotkey
            return display_hotkey(hotkey)
        except Exception:
            return hotkey

    def _change_theme(self, theme_mode: str):
        self.config.theme_mode = theme_mode
        self.config.save()
        if self.page:
            if theme_mode == "light":
                self.page.theme_mode = ft.ThemeMode.LIGHT
                is_dark = False
            elif theme_mode == "dark":
                self.page.theme_mode = ft.ThemeMode.DARK
                is_dark = True
            else:
                self.page.theme_mode = ft.ThemeMode.SYSTEM
                is_dark = False
                try:
                    import winreg
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                        is_dark = value == 0
                except Exception:
                    pass
            self.page.theme = get_theme(dark=is_dark)
        self.reload()
        if self.page:
            self.page.update()

    def _filter_settings(self, e):
        pass

    def _test_microphone(self, e):
        try:
            from voice_typer.platform import list_microphones
            mics = list_microphones()
            if mics:
                names = [m.get('name', 'Unknown') for m in mics[:5]]
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Found {len(mics)} mic(s): {', '.join(names)}"),
                    bgcolor=Colors.SUCCESS,
                )
            else:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("No microphones detected"),
                    bgcolor=Colors.WARNING,
                )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Mic test failed: {exc}"),
                bgcolor=Colors.ERROR,
            )
        self.page.snack_bar.open = True
        self.page.update()

    def _view_logs(self, e):
        try:
            from voice_typer.config import _config_dir
            config_dir = _config_dir()
            if sys.platform == "win32":
                os.startfile(str(config_dir))
            elif sys.platform == "darwin":
                os.system(f'open "{config_dir}"')
            else:
                os.system(f'xdg-open "{config_dir}"')
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Log folder opened: {config_dir}"),
                bgcolor=Colors.INFO,
            )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Could not open logs: {exc}"),
                bgcolor=Colors.ERROR,
            )
        self.page.snack_bar.open = True
        self.page.update()

    def _reset_defaults(self, e):
        try:
            from voice_typer.config import Config
            defaults = Config()
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
            self.reload()
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
        try:
            seconds = float(value)
            if 3 <= seconds <= 30:
                self.config.silence_warning_seconds = seconds
                self.config.save()
                self.reload()
        except ValueError:
            pass

    def _update_max_recording(self, value: str):
        try:
            seconds = int(value)
            if 0 <= seconds <= 7200:
                self.config.max_recording_seconds = seconds
                self.config.save()
                self.reload()
        except ValueError:
            pass
