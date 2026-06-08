import flet as ft
import json
import pyperclip
import threading
import tkinter as tk
from tkinter import filedialog
from .styles import Tokens, SETTINGS_MAX_WIDTH, format_relative_time, is_windows_dark_mode
from voice_typer.history_db import HistoryDB
from .icons import icon


class HistoryScreen:
    """History screen for viewing past transcriptions."""

    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self.history_db = HistoryDB()
        self.history_items = []
        self._search_timer = None
        self._search_delay = 0.3
        self._last_deleted = None
        self._favorites_active = False
        self._load_history()

    def _is_dark(self) -> bool:
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def build(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        ad = Tokens.accent_danger(dark)

        return ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=24, vertical=32),
            alignment=ft.Alignment(0, -1),
            content=ft.Container(
                width=800,
                content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text("History", size=20, weight=ft.FontWeight.W_600, color=tp),
                                        ft.Container(expand=True),
                                        ft.Container(
                                            on_click=self._export_history,
                                            content=ft.Row([icon("import-export", size=16, color=ts), ft.Text("Export", color=ts, size=13)], spacing=8),
                                            padding=ft.Padding(left=12, right=12, top=8, bottom=8),
                                            border_radius=8,
                                        ),
                                        ft.Container(
                                            on_click=self._toggle_favorites_filter,
                                            content=ft.Row([icon("heart-filled" if self._favorites_active else "heart", size=16, color=ap if self._favorites_active else ts), ft.Text("Favorites", color=ap if self._favorites_active else ts, size=13)], spacing=8),
                                            padding=ft.Padding(left=12, right=12, top=8, bottom=8),
                                            border_radius=8,
                                        ),
                                        ft.Container(
                                            on_click=self._clear_all_confirm,
                                            content=ft.Row([icon("delete-sweep", color="#FFFFFF", size=16), ft.Text("Clear All", color="#FFFFFF", size=13, weight=ft.FontWeight.W_500)], spacing=8),
                                            padding=ft.Padding(left=16, right=16, top=8, bottom=8),
                                            border_radius=8,
                                            bgcolor=ad,
                                        ),
                                    ],
                                    spacing=8,
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Container(height=16),
                                self._build_search_bar(),
                                ft.Container(height=12),
                                self._build_history_list(),
                            ],
                        ),
                    ),
                )

    def _build_search_bar(self) -> ft.Control:
        dark = self._is_dark()
        ts = Tokens.text_secondary(dark)
        return ft.Container(
            bgcolor=Tokens.bg_card(dark),
            border=ft.Border(
                left=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                top=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                right=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                bottom=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
            ),
            border_radius=10,
            padding=ft.Padding(left=16, right=16, top=0, bottom=0),
            content=ft.Row(
                [
                    icon("search", color=ts, size=16),
                    ft.Container(width=16),
                    ft.TextField(
                        hint_text="Search history...",
                        hint_style=ft.TextStyle(color=ts),
                        color=Tokens.text_primary(dark),
                        border=ft.InputBorder.NONE,
                        expand=True,
                        content_padding=ft.Padding(left=0, right=0, top=12, bottom=12),
                        on_change=self._search_history_debounced,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _build_history_list(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        if not self.history_items:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("history", size=48, color=ts),
                        ft.Text("No transcriptions yet", size=16, color=ts),
                        ft.Text("Your voice transcriptions will appear here", size=14, color=ts),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[self._history_item(item) for item in self.history_items],
            spacing=14,
        )

    def _history_item(self, item: dict) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        is_fav = bool(item.get("favorite", 0))
        timestamp = item.get("timestamp", "")
        display_time = format_relative_time(timestamp)
        return ft.Container(
            padding=ft.Padding(left=20, right=20, top=16, bottom=16),
            bgcolor=Tokens.bg_card(dark),
            border=ft.Border(
                left=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                top=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                right=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                bottom=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
            ),
            border_radius=12,
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                item.get("text", ""),
                                size=13,
                                weight=ft.FontWeight.W_500,
                                max_lines=3,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                color=tp,
                            ),
                            ft.Text(
                                display_time,
                                size=12,
                                color=ts,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ft.Row(
                        spacing=0,
                        controls=[
                            self._build_history_icon("heart-filled" if is_fav else "heart", item, self._toggle_favorite, ap, ap if is_fav else ts),
                            self._build_history_icon("copy-01", item, lambda i: self._copy_text(i.get("text", "")), ap, ts),
                            self._build_history_icon("delete", item, self._delete_item, Tokens.accent_danger(dark), ts),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _build_history_icon(self, icon_name: str, item: dict, action, hover_color: str, default_color: str) -> ft.Control:
        icon_ctrl = icon(icon_name, color=default_color, size=18)
        btn = ft.Container(
            content=icon_ctrl,
            on_click=lambda e, i=item: action(i),
            padding=ft.Padding(left=6, right=6, top=6, bottom=6),
            border_radius=6,
        )
        def on_hover(e, ico=icon_ctrl):
            if e.data == "true":
                ico.color = hover_color
            else:
                ico.color = default_color
            btn.update()
        btn.on_hover = on_hover
        return btn

    def _copy_text(self, text: str):
        pyperclip.copy(text)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Copied to clipboard"),
            bgcolor=Tokens.SUCCESS_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_item(self, item: dict):
        item_id = item.get("id")
        if self.history_db.delete(item_id):
            self._last_deleted = item
            if item in self.history_items:
                self.history_items.remove(item)
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Item deleted"),
                bgcolor=Tokens.WARNING_DARK,
                action="Undo",
                on_action=lambda e: self._undo_delete(),
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _undo_delete(self):
        if self._last_deleted:
            try:
                self.history_db.add_transcription(
                    self._last_deleted.get("text", ""),
                    duration=self._last_deleted.get("duration", 0),
                    model=self._last_deleted.get("model", ""),
                    device=self._last_deleted.get("device", ""),
                )
                self._load_history()
                self._last_deleted = None
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("Item restored"),
                    bgcolor=Tokens.SUCCESS_DARK,
                )
                self.page.snack_bar.open = True
                self.page.update()
            except Exception:
                pass

    def _toggle_favorite(self, item: dict):
        if self.history_db.toggle_favorite(item.get("id")):
            item["favorite"] = 0 if item.get("favorite", 0) else 1
            self.reload()
            self.page.update()

    def _toggle_favorites_filter(self, e):
        self._favorites_active = not self._favorites_active
        if self._favorites_active:
            self.history_items = self.history_db.get_favorites()
        else:
            self.history_items = self.history_db.get_recent()
        self.reload()
        self.page.update()

    def _clear_all_confirm(self, e):
        try:
            dark = self._is_dark()

            def _do_clear(dialog_e):
                self.page.pop_dialog()
                try:
                    if self.history_db.clear_all():
                        self.history_items.clear()
                        self.reload()
                        self.page.snack_bar = ft.SnackBar(
                            content=ft.Text("History cleared"),
                            bgcolor=Tokens.WARNING_DARK,
                        )
                        self.page.snack_bar.open = True
                    self.page.update()
                except Exception as exc:
                    self._show_error_dialog(f"Clear failed: {exc}")

            def _cancel(dialog_e):
                self.page.pop_dialog()
                self.page.update()

            dialog = ft.AlertDialog(
                title=ft.Text("Clear All History"),
                content=ft.Text("Are you sure you want to delete all transcriptions? This cannot be undone."),
                bgcolor=Tokens.bg_sidebar(dark),
                actions=[
                    ft.TextButton("Cancel", on_click=_cancel),
                    ft.TextButton("Clear All", on_click=_do_clear),
                ],
            )
            self.page.show_dialog(dialog)
        except Exception as exc:
            self._show_error_dialog(f"Could not open dialog: {exc}")

    def _show_error_dialog(self, message: str):
        try:
            dlg = ft.AlertDialog(
                title=ft.Text("Error"),
                content=ft.Text(message),
                actions=[ft.TextButton("OK", on_click=lambda e: self._close_dialog())],
            )
            self.page.show_dialog(dlg)
        except Exception:
            import traceback
            traceback.print_exc()

    def _export_history(self, e):
        try:
            entries = self.history_db.get_recent(limit=10000)
            export_json = json.dumps(entries, indent=2, ensure_ascii=False, default=str)

            root = tk.Tk()
            root.withdraw()
            try:
                file_path = filedialog.asksaveasfilename(
                    defaultextension=".json",
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                    title="Export History",
                    initialfile="voice_typing_history.json",
                )
            finally:
                root.destroy()

            if not file_path:
                return

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(export_json)

            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Exported {len(entries)} transcriptions to {file_path}"),
                bgcolor=Tokens.SUCCESS_DARK,
            )
            self.page.snack_bar.open = True
            self.page.update()
        except Exception as exc:
            self._show_error_dialog(f"Export failed: {exc}")

    def _close_dialog(self):
        self.page.pop_dialog()

    def _search_history_debounced(self, e):
        if self._search_timer is not None:
            self._search_timer.cancel()

        query = e.control.value if e.control else ""

        def _do_search():
            if query:
                self.history_items = self.history_db.search(query)
            else:
                self.history_items = self.history_db.get_recent()
            self.reload()
            self.page.update()

        self._search_timer = threading.Timer(self._search_delay, _do_search)
        self._search_timer.start()

    def _load_history(self):
        self.history_items = self.history_db.get_recent()
