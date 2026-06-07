import flet as ft
from .styles import Colors
from .icons import icon

from voice_typer.templates import TemplateManager


class TemplatesScreen:
    """Templates screen for managing voice templates."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self._template_manager = None
        self._load_templates()

    def _is_dark_mode(self) -> bool:
        """Return True if the current theme is dark."""
        if self.page is None:
            return False
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return getattr(self.page.theme, 'brightness', ft.Brightness.LIGHT) == ft.Brightness.DARK

    def _get_manager(self) -> TemplateManager:
        """Lazy-init TemplateManager."""
        if self._template_manager is None:
            self._template_manager = TemplateManager()
        return self._template_manager

    def _load_templates(self):
        """Load templates from the manager."""
        try:
            mgr = self._get_manager()
            raw = mgr.templates
            # Convert to display format
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
        dark = self._is_dark_mode()
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Templates", size=24, weight=ft.FontWeight.BOLD, color=Colors.text_primary(dark)),
                            ft.Container(expand=True),
                            ft.ElevatedButton(
                                content=ft.Row([icon("add", color=ft.Colors.WHITE, size=16), ft.Text("Add Template", color=ft.Colors.WHITE)], spacing=8),
                                on_click=self._add_template,
                                bgcolor=ft.Colors.BLUE_600,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Create voice shortcuts that expand into full text",
                        size=14,
                        color=Colors.text_secondary(dark),
                    ),
                    ft.Container(height=10),
                    self._build_templates_list(),
                ],
            ),
        )

    def _build_templates_list(self) -> ft.Control:
        dark = self._is_dark_mode()
        if not self.templates:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                     [
                        icon("templates", size=48, color=ft.Colors.GREY_400 if not dark else ft.Colors.GREY_500),
                        ft.Text(
                            "No templates yet",
                            size=16,
                            color=ft.Colors.GREY_600 if not dark else ft.Colors.GREY_400,
                        ),
                        ft.Text(
                            "Say a phrase to trigger a text expansion",
                            size=14,
                            color=ft.Colors.GREY_500 if not dark else ft.Colors.GREY_500,
                        ),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            content=ft.Row([icon("add", size=16), ft.Text("Create First Template")], spacing=8),
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
        dark = self._is_dark_mode()
        return ft.Container(
            bgcolor=Colors.card_bg(dark),
            border_radius=8,
            content=ft.Card(
                elevation=0,
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
                                                color=ft.Colors.BLUE_800 if not dark else ft.Colors.BLUE_200,
                                            ),
                                            bgcolor=ft.Colors.BLUE_50 if not dark else ft.Colors.BLUE_900,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                            border_radius=4,
                                        ),
                                        icon("arrow-forward", size=16, color=ft.Colors.GREY_400 if not dark else ft.Colors.GREY_500),
                                        ft.Text(
                                            template.get("expansion", "")[:50],
                                            size=14,
                                            color=ft.Colors.GREY_700 if not dark else ft.Colors.GREY_300,
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                    spacing=8,
                                    wrap=True,
                                  ),
                                ft.Row(
                                    [
                                        ft.Text(
                                            f"Variables: {template.get('variables', 0)}",
                                            size=12,
                                            color=Colors.text_secondary(dark),
                                        ),
                                        ft.Text(
                                            f"Mode: {template.get('match_mode', 'exact')}",
                                            size=12,
                                            color=Colors.text_secondary(dark),
                                        ),
                                    ],
                                    spacing=12,
                                ),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        ft.Row(
                            [
                                ft.IconButton(
                                    icon=icon("edit"),
                                    tooltip="Edit",
                                    on_click=lambda e, t=template: self._edit_template(t),
                                ),
                                ft.IconButton(
                                    icon=icon("delete"),
                                    tooltip="Delete",
                                    on_click=lambda e, t=template: self._delete_template(t),
                                ),
                            ],
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ),
        ),
    )

    def _add_template(self, e):
        """Open a dialog to add a new template."""
        trigger_field = ft.TextField(label="Trigger phrase", width=300)
        output_field = ft.TextField(label="Output text", width=300, multiline=True, min_lines=2)
        mode_dropdown = ft.Dropdown(
            label="Match mode",
            width=300,
            options=[
                ft.dropdown.Option("exact", "Exact match"),
                ft.dropdown.Option("contains", "Contains"),
            ],
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
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Template added: {trigger}"),
                    bgcolor=Colors.SUCCESS,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Add Template"),
            content=ft.Column([
                trigger_field,
                output_field,
                mode_dropdown,
            ], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()),
                ft.TextButton("Save", on_click=_save),
            ],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _edit_template(self, template: dict):
        """Open a dialog to edit an existing template."""
        idx = template.get("index", -1)
        trigger_field = ft.TextField(label="Trigger phrase", width=300, value=template.get("trigger", ""))
        output_field = ft.TextField(label="Output text", width=300, multiline=True, min_lines=2, value=template.get("expansion", ""))
        mode_dropdown = ft.Dropdown(
            label="Match mode",
            width=300,
            options=[
                ft.dropdown.Option("exact", "Exact match"),
                ft.dropdown.Option("contains", "Contains"),
            ],
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
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Template updated: {trigger}"),
                    bgcolor=Colors.SUCCESS,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Edit Template"),
            content=ft.Column([
                trigger_field,
                output_field,
                mode_dropdown,
            ], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()),
                ft.TextButton("Save", on_click=_save),
            ],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _delete_template(self, template: dict):
        """Delete a template from the manager."""
        idx = template.get("index", -1)
        trigger = template.get("trigger", "")
        mgr = self._get_manager()
        if mgr.delete(idx):
            self._load_templates()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Deleted: {trigger}"),
                bgcolor=Colors.WARNING,
            )
            self.page.snack_bar.open = True
            self.page.update()

    def _close_dialog(self):
        """Close the active dialog."""
        if hasattr(self.page, 'dialog') and self.page.dialog:
            self.page.dialog.open = False
            self.page.update()
