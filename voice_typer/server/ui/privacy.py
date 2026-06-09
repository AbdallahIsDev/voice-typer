import flet as ft
import json
import os
import pyperclip
import sys
from pathlib import Path
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
                from voice_typer.server.history_db import HistoryDB
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
        sec = int(stats["total_duration"])
        m, s = divmod(sec, 60)
        stats["duration_str"] = f"{m}m {s}s" if m > 0 else f"{s}s"
        stats["chars_str"] = f"{stats['total_chars']:,}"
        try:
            from voice_typer.server.config import _config_dir
            config_path = _config_dir()
            total = 0
            for f in Path(config_path).rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
            if total > 1048576:
                stats["cache_size"] = f"{total / 1048576:.1f} MB"
            elif total > 1024:
                stats["cache_size"] = f"{total / 1024:.1f} KB"
            else:
                stats["cache_size"] = f"{total} B"
        except Exception:
            stats["cache_size"] = "0 B"
        return stats

    def build(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        stats = self._get_privacy_stats()
        bg = Tokens.bg_card(dark)
        bs = Tokens.border_subtle(dark)
        ap = Tokens.accent_primary(dark)
        ad = Tokens.accent_danger(dark)

        try:
            from voice_typer.server.config import _config_dir
            data_path = str(_config_dir())
        except Exception:
            if sys.platform == "win32":
                data_path = "%APPDATA%\\voice-typer\\"
            else:
                data_path = "~/.config/voice-typer/"

        return ft.Container(
            expand=True,
            padding=ft.Padding(left=24, top=40, right=24, bottom=24),
            alignment=ft.alignment.Alignment(0, -1),
            content=ft.Column(
                scroll=ft.ScrollMode.ADAPTIVE,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        width=800,
                        content=ft.Column(
                            spacing=8,
                            controls=[
                                ft.Column(
                                    horizontal_alignment=ft.CrossAxisAlignment.START,
                                    spacing=8,
                                    controls=[
                                        ft.Text("Privacy", size=24, weight=ft.FontWeight.BOLD, color=tp),
                                        ft.Text("Your data stays on your device", size=14, color=ts),
                                    ],
                                ),
                                ft.ResponsiveRow(
                                    spacing=8,
                                    run_spacing=8,
                                    controls=[
                                        self._stat_card("Local Processing", stats["local_processing"], "shield-energy", bg, bs, ap),
                                        self._stat_card("Total Transcription Time", stats["duration_str"], "time-02", bg, bs, ap),
                                        self._stat_card("API Cloud Calls", stats["cloud_calls"], "cloud", bg, bs, ap),
                                        self._stat_card("Characters Transcribed", stats["chars_str"], "speech-to-text", bg, bs, ap),
                                        self._stat_card("Total Transcripts", str(stats["total_transcriptions"]), "file-02", bg, bs, ap),
                                        ft.Container(
                                            col={"sm": 12, "md": 4},
                                            bgcolor=bg,
                                            border_radius=12,
                                            padding=20,
                                            content=ft.Column(
                                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                                alignment=ft.MainAxisAlignment.CENTER,
                                                spacing=8,
                                                controls=[
                                                    ft.Container(
                                                        content=icon("database-02", color=ap, size=24),
                                                        width=24,
                                                        height=24,
                                                    ),
                                                    ft.Text(
                                                        stats["cache_size"],
                                                        size=24,
                                                        weight=ft.FontWeight.BOLD,
                                                        color=tp,
                                                        text_align=ft.TextAlign.CENTER,
                                                    ),
                                                    ft.Text(
                                                        "Local Cache Size",
                                                        size=12,
                                                        color=ts,
                                                        text_align=ft.TextAlign.CENTER,
                                                    ),
                                                ],
                                            ),
                                        ),
                                    ],
                                ),
                                ft.Container(
                                    bgcolor=bg,
                                    border_radius=12,
                                    padding=24,
                                    width=800,
                                    content=ft.Column(
                                        horizontal_alignment=ft.CrossAxisAlignment.START,
                                        spacing=16,
                                        controls=[
                                            ft.Text("Data Management", size=18, weight=ft.FontWeight.W_600, color=tp),
                                            ft.Row(
                                                spacing=16,
                                                controls=[
                                                    self._build_action_button("Export Transcriptions", "import-export", ts, self._export_data),
                                                    self._build_action_button("Export Vocabulary", "folder", ts, self._export_vocabulary),
                                                    self._build_action_button("Clear All Data", "delete-sweep", ad, self._clear_data),
                                                ],
                                            ),
                                        ],
                                    ),
                                ),
                                ft.Text(
                                    f"Data is stored in: {data_path}",
                                    text_align=ft.TextAlign.LEFT,
                                    size=11,
                                    color=ts,

                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )

    def _stat_card(self, label: str, value: str, icon_name: str, bg: str, bs: str, ap: str) -> ft.Control:
        dark = self._is_dark()
        return ft.Container(
            col={"sm": 12, "md": 4},
            bgcolor=bg,
            border_radius=12,
            padding=20,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
                controls=[
                    ft.Container(
                        content=icon(icon_name, color=ap, size=24),
                        width=24,
                        height=24,
                    ),
                    ft.Text(
                        value,
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        color=Tokens.text_primary(dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        label,
                        size=12,
                        color=Tokens.text_secondary(dark),
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

    def _build_action_button(self, label: str, icon_name: str, color: str, handler) -> ft.Control:
        return ft.Container(
            on_click=handler,
            content=ft.Row(
                [icon(icon_name, size=16, color=color), ft.Text(label, size=13, color=color)],
                spacing=8,
            ),
            padding=ft.Padding(left=14, right=14, top=8, bottom=8),
            border_radius=8,
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
                from voice_typer.server.vocabulary import VocabularyManager
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
