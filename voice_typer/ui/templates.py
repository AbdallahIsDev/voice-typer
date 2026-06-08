import flet as ft
from .styles import Tokens, SETTINGS_MAX_WIDTH, is_windows_dark_mode
from .icons import icon

from voice_typer.templates import TemplateManager


class TemplatesScreen:
    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self._template_manager = None
        self._load_templates()

    def _is_dark(self) -> bool:
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _get_manager(self) -> TemplateManager:
        if self._template_manager is None:
            self._template_manager = TemplateManager()
        return self._template_manager

    def _load_templates(self):
        try:
            mgr = self._get_manager()
            raw = mgr.templates
            self.templates = []
            for idx, t in enumerate(raw):
                self.templates.append({
                    "index": idx,
                    "trigger": t.get("trigger", ""),
                    "expansion": t.get("output", ""),
                    "match_mode": t.get("match_mode", "exact"),
                    "variables": sum(1 for v in ("{today}", "{now}", "{clipboard}", "{username}")
                                    if v in t.get("output", "")),
                })
        except Exception:
            self.templates = []

    def build(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
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
                                ft.Text("Templates", size=20, weight=ft.FontWeight.W_600, color=tp),
                                ft.Container(expand=True),
                                ft.Container(
                                    content=ft.Row([icon("add", color="#FFFFFF", size=16), ft.Text("Add Template", color="#FFFFFF")], spacing=8),
                                    on_click=self._add_template,
                                    bgcolor=ap,
                                    padding=ft.Padding(left=16, right=16, top=10, bottom=10),
                                    border_radius=8,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Text("Create voice shortcuts that expand into full text", size=13, color=ts),
                        ft.Container(height=24),
                        self._build_templates_list(),
                    ],
                ),
            ),
        )

    def _build_templates_list(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        if not self.templates:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.ARTICLE_OUTLINED, size=52, color=ts),
                        ft.Text("No templates yet", size=16, weight=ft.FontWeight.W_500, color=ts),
                        ft.Text("Say a phrase to trigger a text expansion", size=13, color=ts),
                        ft.Container(height=6),
                        ft.Button(
                            content=ft.Row([icon("add", color="#FFFFFF", size=16), ft.Text("Create First Template", color="#FFFFFF", size=13, weight=ft.FontWeight.W_500)], spacing=8),
                            on_click=self._add_template,
                            style=ft.ButtonStyle(
                                bgcolor=ap,
                                shape=ft.RoundedRectangleBorder(radius=8),
                                padding=ft.Padding.symmetric(vertical=8, horizontal=20),
                            ),
                        ),
                        ft.Container(height=20),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[self._template_item(template) for template in self.templates],
            spacing=0,
        )

    def _template_item(self, template: dict) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=14, horizontal=20),
            border=ft.Border(bottom=ft.BorderSide(0.5, Tokens.border_subtle(dark))),
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(template.get("trigger", ""), size=14, weight=ft.FontWeight.W_600, color=tp),
                            ft.Row(
                                [
                                    ft.Text(template.get("expansion", "")[:50], size=13, color=ts, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(f"Variables: {template.get('variables', 0)}", size=12, color=ts),
                                    ft.Text(f"Mode: {template.get('match_mode', 'exact')}", size=12, color=ts),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ft.Row(
                        [
                            ft.IconButton(icon=icon("edit"),
                                          on_click=lambda e, t=template: self._edit_template(t)),
                            ft.IconButton(icon=icon("delete"),
                                          on_click=lambda e, t=template: self._delete_template(t)),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _add_template(self, e):
        trigger_field = ft.TextField(label="Trigger phrase", width=300)
        output_field = ft.TextField(label="Output text", width=300, multiline=True, min_lines=2)
        mode_dropdown = ft.Dropdown(
            label="Match mode", width=300,
            options=[ft.dropdown.Option("exact", "Exact match"), ft.dropdown.Option("contains", "Contains")],
            value="exact",
        )

        def _save(dialog_e):
            trigger = trigger_field.value
            output = output_field.value
            mode = mode_dropdown.value or "exact"
            if trigger and output:
                mgr = self._get_manager()
                mgr.add(trigger, output, match_mode=mode)
                self._load_templates()
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Template added: {trigger}"), bgcolor=Tokens.SUCCESS_DARK,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Add Template"),
            content=ft.Column([trigger_field, output_field, mode_dropdown], tight=True, spacing=10),
            actions=[ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()), ft.TextButton("Save", on_click=_save)],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _edit_template(self, template: dict):
        idx = template.get("index", -1)
        trigger_field = ft.TextField(label="Trigger phrase", width=300, value=template.get("trigger", ""))
        output_field = ft.TextField(label="Output text", width=300, multiline=True, min_lines=2, value=template.get("expansion", ""))
        mode_dropdown = ft.Dropdown(
            label="Match mode", width=300,
            options=[ft.dropdown.Option("exact", "Exact match"), ft.dropdown.Option("contains", "Contains")],
            value=template.get("match_mode", "exact"),
        )

        def _save(dialog_e):
            trigger = trigger_field.value
            output = output_field.value
            mode = mode_dropdown.value or "exact"
            if trigger and output:
                mgr = self._get_manager()
                mgr.update(idx, trigger, output, match_mode=mode)
                self._load_templates()
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Template updated: {trigger}"), bgcolor=Tokens.SUCCESS_DARK,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Edit Template"),
            content=ft.Column([trigger_field, output_field, mode_dropdown], tight=True, spacing=10),
            actions=[ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()), ft.TextButton("Save", on_click=_save)],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _delete_template(self, template: dict):
        idx = template.get("index", -1)
        trigger = template.get("trigger", "")
        mgr = self._get_manager()
        if mgr.delete(idx):
            self._load_templates()
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Deleted: {trigger}"), bgcolor=Tokens.WARNING_DARK,
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _close_dialog(self):
        if hasattr(self.page, 'dialog') and self.page.dialog:
            self.page.dialog.open = False
            self.page.update()
