import flet as ft
from .styles import Colors
from voice_typer.history_db import HistoryDB


class HistoryScreen:
    """History screen for viewing past transcriptions."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self.history_db = HistoryDB()
        self.history_items = []
        self._load_history()

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("History", size=24, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            ft.ElevatedButton(
                                "Clear All",
                                icon=ft.Icons.DELETE_SWEEP,
                                on_click=self._clear_all,
                                bgcolor=ft.Colors.RED_100,
                                color=ft.Colors.RED_900,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    self._build_search_bar(),
                    ft.Container(height=10),
                    self._build_history_list(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_search_bar(self) -> ft.Control:
        return ft.TextField(
            hint_text="Search history...",
            prefix_icon=ft.Icons.SEARCH,
            border_radius=8,
            bgcolor=ft.Colors.GREY_100,
            on_change=self._search_history,
        )

    def _build_history_list(self) -> ft.Control:
        # Placeholder for history items
        if not self.history_items:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.HISTORY, size=48, color=ft.Colors.GREY_400),
                        ft.Text(
                            "No transcriptions yet",
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            "Your voice transcriptions will appear here",
                            size=14,
                            color=ft.Colors.GREY_500,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[
                self._history_item(item) for item in self.history_items
            ],
            spacing=8,
        )

    def _history_item(self, item: dict) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    item.get("text", "")[:100],
                                    size=14,
                                    weight=ft.FontWeight.W_500,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.Text(
                                    item.get("timestamp", ""),
                                    size=12,
                                    color=ft.Colors.GREY_600,
                                ),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        ft.Row(
                            [
                                ft.IconButton(
                                    ft.Icons.COPY,
                                    tooltip="Copy",
                                    on_click=lambda e, t=item.get("text", ""): self._copy_text(t),
                                ),
                                ft.IconButton(
                                    ft.Icons.DELETE,
                                    tooltip="Delete",
                                    on_click=lambda e, i=item: self._delete_item(i),
                                ),
                            ],
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )
        )

    def _copy_text(self, text: str):
        self.page.set_clipboard(text)
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Copied to clipboard"),
            bgcolor=Colors.SUCCESS,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_item(self, item: dict):
        """Delete a transcription from database."""
        if self.history_db.delete(item.get("id")):
            self.history_items.remove(item)
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Item deleted"),
                bgcolor=Colors.WARNING,
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _clear_all(self, e):
        """Clear all transcriptions from database."""
        if self.history_db.clear_all():
            self.history_items.clear()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("History cleared"),
                bgcolor=Colors.WARNING,
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _search_history(self, e):
        """Search transcriptions by text."""
        query = e.control.value if e.control else ""
        if query:
            self.history_items = self.history_db.search(query)
        else:
            self.history_items = self.history_db.get_recent()
        self.page.update()
    
    def _load_history(self):
        """Load recent transcriptions from database."""
        self.history_items = self.history_db.get_recent()
