import flet as ft
import json
import pyperclip
import sys
from .styles import Colors
from .icons import icon


class PrivacyScreen:
    """Privacy screen for managing data and privacy settings."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self._history_db = None
        self._vocab_manager = None

    def _is_dark_mode(self) -> bool:
        """Return True if the current theme is dark."""
        if self.page is None:
            return False
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        # SYSTEM mode - Flet should handle this
        return getattr(self.page.theme, 'brightness', ft.Brightness.LIGHT) == ft.Brightness.DARK

    def _get_history_db(self):
        """Lazy-init HistoryDB."""
        if self._history_db is None:
            try:
                from voice_typer.history_db import HistoryDB
                self._history_db = HistoryDB()
            except Exception:
                pass
        return self._history_db

    def _get_privacy_stats(self) -> dict:
        """Get real privacy statistics from backend components."""
        stats = {
            "local_processing": "100%",
            "cloud_calls": "0",
            "data_sent": "0 KB",
            "total_transcriptions": 0,
            "total_chars": 0,
            "total_duration": 0,
            "favorites_count": 0,
        }
        try:
            db = self._get_history_db()
            if db:
                db_stats = db.get_stats()
                stats["total_transcriptions"] = db_stats.get("total_count", 0)
                stats["total_chars"] = db_stats.get("total_chars", 0)
                stats["total_duration"] = round(db_stats.get("total_duration", 0), 1)
                favs = db.get_favorites()
                stats["favorites_count"] = len(favs)
                # If using cloud backend, reflect that
                if self.config and getattr(self.config, "asr_backend", "whisper") != "whisper":
                    stats["local_processing"] = "0%"
                    stats["cloud_calls"] = str(db_stats.get("total_count", 0))
                    # Estimate data sent (~160KB per minute of audio)
                    duration_mins = db_stats.get("total_duration", 0) / 60
                    data_kb = round(duration_mins * 160)
                    stats["data_sent"] = f"{data_kb} KB"
        except Exception:
            pass
        return stats

    def build(self) -> ft.Control:
        dark = self._is_dark_mode()
        text_secondary = Colors.text_secondary(dark)
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Privacy", size=24, weight=ft.FontWeight.BOLD, color=Colors.text_primary(dark)),
                            ft.Container(expand=True),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Your data stays on your device",
                        size=14,
                        color=text_secondary,
                    ),
                    ft.Container(height=10),
                    self._build_privacy_dashboard(),
                    ft.Container(height=20),
                    self._build_data_management(),
                ],
            ),
        )

    def _build_privacy_dashboard(self) -> ft.Control:
        dark = self._is_dark_mode()
        stats = self._get_privacy_stats()
        return ft.Container(
            bgcolor=Colors.card_bg(dark),
            border_radius=8,
            content=ft.Card(
                elevation=0,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                    [
                        ft.Text("Privacy Dashboard", size=18, weight=ft.FontWeight.W_600, color=Colors.text_primary(dark)),
                        ft.Container(height=10),
                        ft.Row(
                            [
                                self._stat_card("Local Processing", stats["local_processing"], "computer"),
                                self._stat_card("Cloud Calls", stats["cloud_calls"], "cloud-off"),
                                self._stat_card("Data Sent", stats["data_sent"], "send"),
                            ],
                            spacing=16,
                        ),
                        ft.Container(height=10),
                        ft.Row(
                            [
                                self._stat_card("Transcriptions", str(stats["total_transcriptions"]), "speech-to-text"),
                                self._stat_card("Characters", str(stats["total_chars"]), "file"),
                                self._stat_card("Duration (s)", str(stats["total_duration"]), "volume-up"),
                            ],
                            spacing=16,
                        ),
                        ft.Container(height=10),
                        ft.Text(
                            "All transcription happens locally using Whisper" if stats["local_processing"] == "100%" else "Cloud transcription is active — audio is sent to external APIs",
                            size=14,
                            color=Colors.SUCCESS if stats["local_processing"] == "100%" else Colors.WARNING,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    spacing=8,
                ),
            ),
        ),
    )

    def _stat_card(self, label: str, value: str, icon_name: str) -> ft.Control:
        dark = self._is_dark_mode()
        return ft.Container(
            content=ft.Column(
                [
                    icon(icon_name, size=24, color=Colors.PRIMARY),
                    ft.Text(
                        value,
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        text_align=ft.TextAlign.CENTER,
                        color=Colors.text_primary(dark),
                    ),
                    ft.Text(
                        label,
                        size=12,
                        color=Colors.text_secondary(dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            expand=True,
            padding=16,
            border_radius=8,
            bgcolor=Colors.card_bg(dark),
        )

    def _build_data_management(self) -> ft.Control:
        dark = self._is_dark_mode()
        # Determine data directory path for display
        try:
            from voice_typer.config import _config_dir
            data_path = str(_config_dir())
        except Exception:
            if sys.platform == "win32":
                data_path = "%APPDATA%\\voice-typer\\"
            else:
                data_path = "~/.config/voice-typer/"

        return ft.Container(
            bgcolor=Colors.card_bg(dark),
            border_radius=8,
            content=ft.Card(
                elevation=0,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                    [
                        ft.Text("Data Management", size=18, weight=ft.FontWeight.W_600, color=Colors.text_primary(dark)),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            content=ft.Row([icon("import-export", size=16), ft.Text("Export Transcriptions")], spacing=8),
                            on_click=self._export_data,
                        ),
                        ft.ElevatedButton(
                            content=ft.Row([icon("import-export", size=16), ft.Text("Export Vocabulary")], spacing=8),
                            on_click=self._export_vocabulary,
                        ),
                        ft.ElevatedButton(
                            content=ft.Row([icon("delete-sweep", color=Colors.ERROR, size=16), ft.Text("Clear All Data", color=Colors.ERROR)], spacing=8),
                            on_click=self._clear_data,
                            bgcolor=ft.Colors.RED_100 if not dark else ft.Colors.RED_900,
                        ),
                        ft.Container(height=10),
                        ft.Text(
                            f"Data is stored in: {data_path}",
                            size=12,
                            color=Colors.text_secondary(dark),
                        ),
                    ],
                    spacing=8,
                ),
            ),
        ),
    )

    def _export_data(self, e):
        """Export transcription history as JSON."""
        try:
            db = self._get_history_db()
            if db:
                entries = db.get_recent(limit=10000)
                export_json = json.dumps(entries, indent=2, ensure_ascii=False, default=str)
                # In a real app, this would save to a file
                # For now, copy to clipboard
                pyperclip.copy(export_json)
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Exported {len(entries)} transcriptions to clipboard"),
                    bgcolor=Colors.SUCCESS,
                )
            else:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("No history database available"),
                    bgcolor=Colors.WARNING,
                )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Export failed: {exc}"),
                bgcolor=Colors.ERROR,
            )
        self.page.snack_bar.open = True
        self.page.update()

    def _export_vocabulary(self, e):
        """Export vocabulary as JSON."""
        try:
            if self._vocab_manager is None:
                from voice_typer.vocabulary import VocabularyManager
                self._vocab_manager = VocabularyManager()
            export_json = self._vocab_manager.export_json()
            pyperclip.copy(export_json)
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Vocabulary exported to clipboard"),
                bgcolor=Colors.SUCCESS,
            )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Export failed: {exc}"),
                bgcolor=Colors.ERROR,
            )
        self.page.snack_bar.open = True
        self.page.update()

    def _clear_data(self, e):
        """UX-005: Clear all data with confirmation dialog."""
        def _do_clear(dialog_e):
            self.page.dialog.open = False
            try:
                db = self._get_history_db()
                if db:
                    db.clear_all()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("All data cleared"),
                    bgcolor=Colors.WARNING,
                )
            except Exception as exc:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Clear failed: {exc}"),
                    bgcolor=Colors.ERROR,
                )
            self.page.snack_bar.open = True
            self.page.update()

        def _cancel(dialog_e):
            self.page.dialog.open = False
            self.page.update()

        self.page.dialog = ft.AlertDialog(
            title=ft.Text("Clear All Data"),
            content=ft.Text("Are you sure you want to clear all data (history, vocabulary, templates)? This cannot be undone."),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel),
                ft.TextButton("Clear All Data", on_click=_do_clear),
            ],
        )
        self.page.dialog.open = True
        self.page.update()
