import flet as ft
import json
import pyperclip
import sys
from .styles import Tokens, SETTINGS_MAX_WIDTH, is_windows_dark_mode
from .icons import icon


class PrivacyScreen:
    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self._history_db = None
        self._vocab_manager = None

    def _is_dark(self) -> bool:
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _get_history_db(self):
        if self._history_db is None:
            try:
                from voice_typer.history_db import HistoryDB
                self._history_db = HistoryDB()
            except Exception:
                pass
        return self._history_db

    def _get_privacy_stats(self) -> dict:
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
                if self.config and getattr(self.config, "asr_backend", "whisper") != "whisper":
                    stats["local_processing"] = "0%"
                    stats["cloud_calls"] = str(db_stats.get("total_count", 0))
                    duration_mins = db_stats.get("total_duration", 0) / 60
                    data_kb = round(duration_mins * 160)
                    stats["data_sent"] = f"{data_kb} KB"
        except Exception:
            pass
        return stats

    def build(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
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
                                ft.Text("Privacy", size=24, weight=ft.FontWeight.BOLD, color=tp),
                                ft.Container(expand=True),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                        ft.Text("Your data stays on your device", size=14, color=ts),
                        ft.Container(height=10),
                        self._build_privacy_dashboard(),
                        ft.Container(height=20),
                        self._build_data_management(),
                    ],
                ),
            ),
        )

    def _build_privacy_dashboard(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        stats = self._get_privacy_stats()
        bg_sidebar = Tokens.bg_sidebar(dark)

        return ft.Container(
            bgcolor=bg_sidebar,
            border_radius=10,
            padding=20,
            content=ft.Column(
                [
                    ft.Text("Privacy Dashboard", size=18, weight=ft.FontWeight.W_600, color=tp),
                    ft.Container(height=10),
                    ft.Row(
                        [self._stat_card("Local Processing", stats["local_processing"], "computer"),
                         self._stat_card("Cloud Calls", stats["cloud_calls"], "cloud-off"),
                         self._stat_card("Data Sent", stats["data_sent"], "send")],
                        spacing=16,
                    ),
                    ft.Container(height=10),
                    ft.Row(
                        [self._stat_card("Transcriptions", str(stats["total_transcriptions"]), "speech-to-text"),
                         self._stat_card("Characters", str(stats["total_chars"]), "file"),
                         self._stat_card("Duration (s)", str(stats["total_duration"]), "volume-up")],
                        spacing=16,
                    ),
                    ft.Container(height=10),
                    ft.Text(
                        "All transcription happens locally using Whisper" if stats["local_processing"] == "100%"
                        else "Cloud transcription is active \u2014 audio is sent to external APIs",
                        size=14, color=Tokens.SUCCESS_DARK if stats["local_processing"] == "100%" else Tokens.WARNING_DARK,
                        weight=ft.FontWeight.W_500,
                    ),
                ],
                spacing=8,
            ),
        )

    def _stat_card(self, label: str, value: str, icon_name: str) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        bg_sidebar = Tokens.bg_sidebar(dark)
        return ft.Container(
            content=ft.Column(
                [
                    icon(icon_name, size=24, color=ap),
                    ft.Text(value, size=20, weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER, color=tp),
                    ft.Text(label, size=12, color=ts,
                            text_align=ft.TextAlign.CENTER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            expand=True,
            padding=16,
            border_radius=8,
            bgcolor=bg_sidebar,
        )

    def _build_data_management(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ad = Tokens.accent_danger(dark)
        bg_sidebar = Tokens.bg_sidebar(dark)

        try:
            from voice_typer.config import _config_dir
            data_path = str(_config_dir())
        except Exception:
            if sys.platform == "win32":
                data_path = "%APPDATA%\\voice-typer\\"
            else:
                data_path = "~/.config/voice-typer/"

        return ft.Container(
            bgcolor=bg_sidebar,
            border_radius=10,
            padding=20,
            content=ft.Column(
                [
                    ft.Text("Data Management", size=18, weight=ft.FontWeight.W_600, color=tp),
                    ft.Container(height=10),
                    ft.Button(
                        content=ft.Row([icon("import-export", size=16), ft.Text("Export Transcriptions")], spacing=8),
                        on_click=self._export_data,
                    ),
                    ft.Button(
                        content=ft.Row([icon("import-export", size=16), ft.Text("Export Vocabulary")], spacing=8),
                        on_click=self._export_vocabulary,
                    ),
                    ft.Container(height=10),
                    ft.Container(
                        content=ft.Row(
                            [icon("delete-sweep", color="#FFFFFF", size=16),
                             ft.Text("Clear All Data", color="#FFFFFF", size=13, weight=ft.FontWeight.W_500)],
                            spacing=8,
                        ),
                        on_click=self._clear_data,
                        bgcolor=ad,
                        padding=ft.Padding(left=16, right=16, top=10, bottom=10),
                        border_radius=8,
                    ),
                    ft.Container(height=10),
                    ft.Text(f"Data is stored in: {data_path}", size=12, color=ts),
                ],
                spacing=8,
            ),
        )

    def _export_data(self, e):
        try:
            db = self._get_history_db()
            if db:
                entries = db.get_recent(limit=10000)
                export_json = json.dumps(entries, indent=2, ensure_ascii=False, default=str)
                pyperclip.copy(export_json)
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Exported {len(entries)} transcriptions to clipboard"),
                    bgcolor=Tokens.SUCCESS_DARK,
                )
            else:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("No history database available"),
                    bgcolor=Tokens.WARNING_DARK,
                )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Export failed: {exc}"),
                bgcolor=Tokens.ACCENT_DANGER_DARK,
            )
        self.page.snack_bar.open = True
        self.page.update()

    def _export_vocabulary(self, e):
        try:
            if self._vocab_manager is None:
                from voice_typer.vocabulary import VocabularyManager
                self._vocab_manager = VocabularyManager()
            export_json = self._vocab_manager.export_json()
            pyperclip.copy(export_json)
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Vocabulary exported to clipboard"),
                bgcolor=Tokens.SUCCESS_DARK,
            )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Export failed: {exc}"),
                bgcolor=Tokens.ACCENT_DANGER_DARK,
            )
        self.page.snack_bar.open = True
        self.page.update()

    def _clear_data(self, e):
        def _do_clear(dialog_e):
            self.page.dialog.open = False
            try:
                db = self._get_history_db()
                if db:
                    db.clear_all()
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("All data cleared"),
                    bgcolor=Tokens.WARNING_DARK,
                )
            except Exception as exc:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Clear failed: {exc}"),
                    bgcolor=Tokens.ACCENT_DANGER_DARK,
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
