import flet as ft
from .styles import Colors


class VocabularyScreen:
    """Vocabulary screen for managing custom words and corrections."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self.vocabulary = []  # Vocabulary will be injected

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Custom Vocabulary", size=24, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            ft.ElevatedButton(
                                "Add Word",
                                icon=ft.Icons.ADD,
                                on_click=self._add_word,
                                bgcolor=ft.Colors.BLUE_600,
                                color=ft.Colors.WHITE,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Add custom words and corrections to improve accuracy",
                        size=14,
                        color=ft.Colors.GREY_600,
                    ),
                    ft.Container(height=10),
                    self._build_vocabulary_list(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_vocabulary_list(self) -> ft.Control:
        if not self.vocabulary:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.SCHOOL, size=48, color=ft.Colors.GREY_400),
                        ft.Text(
                            "No custom vocabulary",
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            "Add words or phrases that Whisper often gets wrong",
                            size=14,
                            color=ft.Colors.GREY_500,
                        ),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Add First Word",
                            icon=ft.Icons.ADD,
                            on_click=self._add_word,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[
                self._vocabulary_item(item) for item in self.vocabulary
            ],
            spacing=8,
        )

    def _vocabulary_item(self, item: dict) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Container(
                                            content=ft.Text(
                                                item.get("original", ""),
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=ft.Colors.RED_800,
                                            ),
                                            bgcolor=ft.Colors.RED_50,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                            border_radius=4,
                                        ),
                                        ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=ft.Colors.GREY_400),
                                        ft.Container(
                                            content=ft.Text(
                                                item.get("correction", ""),
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=ft.Colors.GREEN_800,
                                            ),
                                            bgcolor=ft.Colors.GREEN_50,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                            border_radius=4,
                                        ),
                                    ],
                                    spacing=8,
                                ),
                                ft.Text(
                                    f"Used {item.get('count', 0)} times",
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
                                    ft.Icons.EDIT,
                                    tooltip="Edit",
                                    on_click=lambda e, i=item: self._edit_word(i),
                                ),
                                ft.IconButton(
                                    ft.Icons.DELETE,
                                    tooltip="Delete",
                                    on_click=lambda e, i=item: self._delete_word(i),
                                ),
                            ],
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )
        )

    def _add_word(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Add word dialog opened"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _edit_word(self, item: dict):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Editing: {item.get('original', '')}"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_word(self, item: dict):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Deleted: {item.get('original', '')}"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()
