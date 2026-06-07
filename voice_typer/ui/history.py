import flet as ft
import json
import pyperclip
import threading
from .styles import Colors, format_relative_time, is_windows_dark_mode
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
        self._load_history()

    def _is_dark_mode(self) -> bool:
        """Return True if the current theme is dark."""
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def build(self) -> ft.Control:
        dark = self._is_dark_mode()
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("History", size=20, weight=ft.FontWeight.W_600, color="#F1F1F3"),
                            ft.Container(expand=True),
                            ft.TextButton(
                                content=ft.Row([icon("import-export", size=16, color="rgba(241,241,243,0.55)"), ft.Text("Export", color="rgba(241,241,243,0.55)", size=13)], spacing=8),
                                on_click=self._export_history,
                                tooltip="Export transcription history",
                            ),
                            ft.TextButton(
                                content=ft.Row([icon("filter", color="rgba(241,241,243,0.55)", size=16), ft.Text("Favorites", color="rgba(241,241,243,0.55)", size=13)], spacing=8),
                                on_click=self._show_favorites,
                                tooltip="Show favorited transcriptions only",
                            ),
                            ft.Container(
                                content=ft.Button(
                                    content=ft.Row([icon("delete-sweep", color="#FFFFFF", size=16), ft.Text("Clear All", color="#FFFFFF", size=13, weight=ft.FontWeight.W_500)], spacing=8),
                                    on_click=self._clear_all_confirm,
                                    tooltip="Delete all transcriptions",
                                    style=ft.ButtonStyle(
                                        padding=ft.Padding(left=16, right=16, top=7, bottom=7),
                                    ),
                                ),
                                bgcolor="#DC2626",
                                border_radius=8,
                            ),
                        ],
                        spacing=8,
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    self._build_search_bar(),
                    ft.Container(height=10),
                    self._build_history_list(),
                ],
            ),
        )

    def _build_search_bar(self) -> ft.Control:
        dark = self._is_dark_mode()
        return ft.TextField(
            hint_text="Search history...",
            hint_style=ft.TextStyle(color="rgba(241,241,243,0.55)"),
            prefix=icon("search", color="rgba(241,241,243,0.30)", size=15),
            border_radius=8,
            bgcolor="rgba(255,255,255,0.05)",
            border_color="rgba(255,255,255,0.10)",
            color="#F1F1F3",
            width=280,
            on_change=self._search_history_debounced,
        )

    def _build_history_list(self) -> ft.Control:
        dark = self._is_dark_mode()
        if not self.history_items:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("history", size=48, color="rgba(241,241,243,0.30)"),
                        ft.Text(
                            "No transcriptions yet",
                            size=16,
                            color="rgba(241,241,243,0.55)",
                        ),
                        ft.Text(
                            "Your voice transcriptions will appear here",
                            size=14,
                            color="rgba(241,241,243,0.30)",
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[
                self._history_item(item) for item in self.history_items
            ],
            spacing=0,
        )

    def _history_item(self, item: dict) -> ft.Control:
        dark = self._is_dark_mode()
        is_fav = bool(item.get("favorite", 0))
        # UX-027: Use relative time formatter
        timestamp = item.get("timestamp", "")
        display_time = format_relative_time(timestamp)
        return ft.Container(
            padding=ft.Padding(left=20, right=20, top=14, bottom=14),
            border=ft.Border(
                bottom=ft.BorderSide(0.5, "rgba(255,255,255,0.05)"),
            ),
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                item.get("text", "")[:100],
                                size=13,
                                weight=ft.FontWeight.W_500,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                color="rgba(241,241,243,0.85)",
                            ),
                            ft.Text(
                                display_time,
                                size=11,
                                color="rgba(241,241,243,0.28)",
                                tooltip=timestamp,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ft.Row(
                        [
                            ft.IconButton(
                                icon=icon("sparkles" if is_fav else "tick"),
                                tooltip="Unfavorite" if is_fav else "Favorite",
                                on_click=lambda e, i=item: self._toggle_favorite(i),
                            ),
                            ft.IconButton(
                                icon=icon("copy-01"),
                                tooltip="Copy",
                                on_click=lambda e, t=item.get("text", ""): self._copy_text(t),
                            ),
                            ft.IconButton(
                                icon=icon("delete"),
                                icon_color="#F87171",
                                tooltip="Delete",
                                on_click=lambda e, i=item: self._delete_item(i),
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _copy_text(self, text: str):
        pyperclip.copy(text)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Copied to clipboard"),
            bgcolor=Colors.SUCCESS,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_item(self, item: dict):
        """UX-010: Delete with undo support."""
        item_id = item.get("id")
        if self.history_db.delete(item_id):
            self._last_deleted = item
            if item in self.history_items:
                self.history_items.remove(item)
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Item deleted"),
                bgcolor=Colors.WARNING,
                action="Undo",
                on_action=lambda e: self._undo_delete(),
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _undo_delete(self):
        """UX-010: Undo last delete."""
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
                    bgcolor=Colors.SUCCESS,
                )
                self.page.snack_bar.open = True
                self.page.update()
            except Exception:
                pass

    def _toggle_favorite(self, item: dict):
        if self.history_db.toggle_favorite(item.get("id")):
            item["favorite"] = 0 if item.get("favorite", 0) else 1
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Favorite toggled"),
                bgcolor=Colors.INFO,
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _show_favorites(self, e):
        self.history_items = self.history_db.get_favorites()
        self.reload()
        self.page.update()

    def _clear_all_confirm(self, e):
        """UX-005: Confirm before clearing all history."""
        def _do_clear(dialog_e):
            self.page.dialog.open = False
            if self.history_db.clear_all():
                self.history_items.clear()
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("History cleared"),
                    bgcolor=Colors.WARNING,
                )
                self.page.snack_bar.open = True
            self.page.update()

        def _cancel(dialog_e):
            self.page.dialog.open = False
            self.page.update()

        self.page.dialog = ft.AlertDialog(
            title=ft.Text("Clear All History"),
            content=ft.Text("Are you sure you want to delete all transcriptions? This cannot be undone."),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel),
                ft.TextButton("Clear All", on_click=_do_clear),
            ],
        )
        self.page.dialog.open = True
        self.page.update()

    def _clear_all(self, e):
        """Direct clear without confirm (legacy, kept for compatibility)."""
        if self.history_db.clear_all():
            self.history_items.clear()
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("History cleared"),
                bgcolor=Colors.WARNING,
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _export_history(self, e):
        """UX-010: Export transcription history as JSON."""
        try:
            entries = self.history_db.get_recent(limit=10000)
            export_json = json.dumps(entries, indent=2, ensure_ascii=False, default=str)
            pyperclip.copy(export_json)
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Exported {len(entries)} transcriptions to clipboard"),
                bgcolor=Colors.SUCCESS,
            )
        except Exception as exc:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Export failed: {exc}"),
                bgcolor=Colors.ERROR,
            )
        self.page.snack_bar.open = True
        self.page.update()

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
