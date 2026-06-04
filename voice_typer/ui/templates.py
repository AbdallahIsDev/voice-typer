import flet as ft
from .styles import Colors


class TemplatesScreen:
    """Templates screen for managing voice templates."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self.templates = []  # Templates will be injected

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Templates", size=24, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            ft.ElevatedButton(
                                "Add Template",
                                icon=ft.Icons.ADD,
                                on_click=self._add_template,
                                bgcolor=ft.Colors.BLUE_600,
                                color=ft.Colors.WHITE,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Create voice shortcuts that expand into full text",
                        size=14,
                        color=ft.Colors.GREY_600,
                    ),
                    ft.Container(height=10),
                    self._build_templates_list(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_templates_list(self) -> ft.Control:
        if not self.templates:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.TEXT_SNIPPET, size=48, color=ft.Colors.GREY_400),
                        ft.Text(
                            "No templates yet",
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            "Say a phrase to trigger a text expansion",
                            size=14,
                            color=ft.Colors.GREY_500,
                        ),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Create First Template",
                            icon=ft.Icons.ADD,
                            on_click=self._add_template,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[
                self._template_item(template) for template in self.templates
            ],
            spacing=8,
        )

    def _template_item(self, template: dict) -> ft.Control:
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
                                                template.get("trigger", ""),
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=ft.Colors.BLUE_800,
                                            ),
                                            bgcolor=ft.Colors.BLUE_50,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                            border_radius=4,
                                        ),
                                        ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=ft.Colors.GREY_400),
                                        ft.Text(
                                            template.get("expansion", "")[:50],
                                            size=14,
                                            color=ft.Colors.GREY_700,
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                    spacing=8,
                                    wrap=True,
                                ),
                                ft.Text(
                                    f"Variables: {template.get('variables', 0)}",
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
                                    on_click=lambda e, t=template: self._edit_template(t),
                                ),
                                ft.IconButton(
                                    ft.Icons.DELETE,
                                    tooltip="Delete",
                                    on_click=lambda e, t=template: self._delete_template(t),
                                ),
                            ],
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )
        )

    def _add_template(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Template editor opened"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _edit_template(self, template: dict):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Editing: {template.get('trigger', '')}"),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_template(self, template: dict):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Deleted: {template.get('trigger', '')}"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()
