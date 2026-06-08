import flet as ft
from .styles import Tokens, SETTINGS_MAX_WIDTH, is_windows_dark_mode
from .icons import icon

from voice_typer.vocabulary import VocabularyManager, CATEGORIES


class VocabularyScreen:
    CATEGORY_LABELS = {
        "misspellings": "Misspellings",
        "phrase_corrections": "Phrase Corrections",
        "extra_word_patterns": "Extra Word Patterns",
        "technical_terms": "Technical Terms",
        "names": "Names",
        "products": "Products",
    }

    CATEGORY_DESCRIPTIONS = {
        "misspellings": "Common word misspellings \u2192 corrections",
        "phrase_corrections": "Phrase-level corrections",
        "extra_word_patterns": "Patterns to remove or replace",
        "technical_terms": "Technical jargon corrections",
        "names": "Proper name corrections",
        "products": "Product name corrections",
    }

    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self._vocab_manager = None
        self._active_category = CATEGORIES[0]
        self._load_vocabulary()

    def _is_dark(self) -> bool:
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _get_manager(self) -> VocabularyManager:
        if self._vocab_manager is None:
            self._vocab_manager = VocabularyManager()
        return self._vocab_manager

    def _load_vocabulary(self):
        try:
            mgr = self._get_manager()
            self.vocabulary = []
            self._vocab_data = mgr.get_all()
            for cat in CATEGORIES:
                cat_data = self._vocab_data.get(cat)
                if cat in ("misspellings", "technical_terms", "names", "products"):
                    if isinstance(cat_data, dict):
                        for key, val in cat_data.items():
                            self.vocabulary.append({
                                "category": cat, "original": key, "correction": val, "count": 0,
                            })
                elif cat in ("phrase_corrections", "extra_word_patterns"):
                    if isinstance(cat_data, list):
                        for i, entry in enumerate(cat_data):
                            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                                self.vocabulary.append({
                                    "category": cat, "original": entry[0], "correction": entry[1],
                                    "index": i, "count": 0,
                                })
        except Exception:
            self.vocabulary = []

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
                width=800,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text("Custom Vocabulary", size=20, weight=ft.FontWeight.W_600, color=tp),
                                ft.Container(expand=True),
                                ft.Container(
                                    content=ft.Row([icon("add", color="#FFFFFF", size=16), ft.Text("Add Word", color="#FFFFFF", size=14)], spacing=8),
                                    on_click=self._add_word,
                                    bgcolor=ap,
                                    padding=ft.Padding(left=16, right=16, top=10, bottom=10),
                                    border_radius=8,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Text("Add custom words and corrections to improve accuracy", size=13, color=ts),
                        ft.Container(height=20),
                        self._build_category_tabs(),
                        ft.Container(height=16),
                        self._build_vocabulary_list(),
                    ],
                ),
            ),
        )

    def _on_tab_hover(self, e, tab, cat):
        dark = self._is_dark()
        if e.data == "true":
            tab.bgcolor = Tokens.bg_card(dark)
            tab.scale = 1.05
            if isinstance(tab.content, ft.Text):
                tab.content.color = Tokens.text_primary(dark)
        else:
            tab.scale = 1.0
            if cat != self._active_category:
                tab.bgcolor = None
                if isinstance(tab.content, ft.Text):
                    tab.content.color = Tokens.text_secondary(dark)
        tab.update()

    def _build_category_tabs(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        tabs = []
        for cat in CATEGORIES:
            is_active = cat == self._active_category
            tab = ft.Container(
                content=ft.Text(
                    self.CATEGORY_LABELS.get(cat, cat),
                    size=12, weight=ft.FontWeight.W_500,
                    color=tp if is_active else ts,
                ),
                padding=ft.Padding(left=16, right=16, top=8, bottom=8),
                border_radius=6,
                bgcolor=Tokens.bg_card(dark) if is_active else None,
                shadow=ft.BoxShadow(blur_radius=4, spread_radius=0, color="rgba(0,0,0,0.12)") if is_active else None,
                on_click=lambda e, c=cat: self._select_category(c),
                data=cat,
            )
            if not is_active:
                tab.on_hover = lambda e, t=tab, c=cat: self._on_tab_hover(e, t, c)
            tabs.append(tab)
        return ft.Container(
            content=ft.Row(tabs, spacing=2),
            bgcolor=Tokens.bg_sidebar(dark),
            border_radius=8,
            padding=3,
        )

    def _select_category(self, category: str):
        self._active_category = category
        self.reload()
        self.page.update()

    def _build_vocabulary_list(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        filtered = [v for v in self.vocabulary if v.get("category") == self._active_category]

        if not filtered:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("vocabulary", size=48, color=ts),
                        ft.Text(
                            f"No {self.CATEGORY_LABELS.get(self._active_category, self._active_category).lower()}",
                            size=16, color=ts,
                        ),
                        ft.Text(self.CATEGORY_DESCRIPTIONS.get(self._active_category, ""), size=14, color=ts),
                        ft.Container(height=10),
                        ft.Container(
                            content=ft.Row([icon("add", size=16, color="#FFFFFF"), ft.Text("Add First Word", color="#FFFFFF", size=14)], spacing=8),
                            on_click=self._add_word,
                            bgcolor=Tokens.accent_primary(dark),
                            padding=ft.Padding(left=16, right=16, top=10, bottom=10),
                            border_radius=8,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[self._vocabulary_item(item) for item in filtered],
            spacing=12,
        )

    def _vocabulary_item(self, item: dict) -> ft.Control:
        dark = self._is_dark()
        ts = Tokens.text_secondary(dark)
        return ft.Container(
            padding=ft.Padding(left=20, right=20, top=14, bottom=14),
            bgcolor=Tokens.bg_sidebar(dark),
            border=ft.Border(
                left=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                top=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                right=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
                bottom=ft.BorderSide(0.5, Tokens.border_subtle(dark)),
            ),
            border_radius=10,
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(item.get("original", ""), size=13, weight=ft.FontWeight.W_500, color="#F87171"),
                                    ft.Text("\u2192", color=ts, size=14),
                                    ft.Text(item.get("correction", ""), size=13, weight=ft.FontWeight.W_600, color="#34D399"),
                                ],
                                spacing=8,
                            ),
                            ft.Text(
                                self.CATEGORY_LABELS.get(item.get("category", ""), item.get("category", "")),
                                size=11, color=ts,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    ft.Row(
                        spacing=0,
                        controls=[
                            self._build_icon_button("edit", item, self._edit_word, Tokens.accent_primary(dark)),
                            self._build_icon_button("delete", item, self._delete_word, Tokens.accent_danger(dark)),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _build_icon_button(self, icon_name: str, item: dict, action, hover_color: str) -> ft.Control:
        dark = self._is_dark()
        ts = Tokens.text_secondary(dark)
        icon_ctrl = icon(icon_name, color=ts, size=16)
        btn = ft.Container(
            content=icon_ctrl,
            on_click=lambda e, i=item: action(i),
            padding=ft.Padding(left=6, right=6, top=6, bottom=6),
            border_radius=6,
            tooltip="Edit" if icon_name == "edit" else "Delete",
        )
        def on_hover(e, icon=icon_ctrl):
            if e.data == "true":
                icon.color = hover_color
            else:
                icon.color = ts
            btn.update()
        btn.on_hover = on_hover
        return btn

    def _add_word(self, e):
        original_field = ft.TextField(label="Original (misrecognized word)", width=300)
        correction_field = ft.TextField(label="Correction", width=300)
        category_dropdown = ft.Dropdown(
            label="Category", width=300,
            options=[ft.dropdown.Option(cat, self.CATEGORY_LABELS.get(cat, cat)) for cat in CATEGORIES],
            value=self._active_category,
        )

        def _save(dialog_e):
            original = original_field.value
            correction = correction_field.value
            category = category_dropdown.value or self._active_category
            if original and correction:
                mgr = self._get_manager()
                if category in ("misspellings", "technical_terms", "names", "products"):
                    mgr.add_entry(category, original, correction)
                else:
                    mgr.add_phrase(category, original, correction)
                self._load_vocabulary()
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Added: {original} \u2192 {correction}"),
                    bgcolor=Tokens.SUCCESS_DARK,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Add Vocabulary Entry"),
            content=ft.Column([original_field, correction_field, category_dropdown], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()),
                ft.TextButton("Save", on_click=_save),
            ],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _edit_word(self, item: dict):
        original_field = ft.TextField(label="Original", width=300, value=item.get("original", ""))
        correction_field = ft.TextField(label="Correction", width=300, value=item.get("correction", ""))
        category = item.get("category", self._active_category)

        def _save(dialog_e):
            original = original_field.value
            correction = correction_field.value
            if original and correction:
                mgr = self._get_manager()
                if category in ("misspellings", "technical_terms", "names", "products"):
                    mgr.remove_entry(category, item.get("original", ""))
                    mgr.add_entry(category, original, correction)
                else:
                    idx = item.get("index", -1)
                    if idx >= 0:
                        mgr.remove_phrase(category, idx)
                    mgr.add_phrase(category, original, correction)
                self._load_vocabulary()
                self.reload()
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Updated: {original} \u2192 {correction}"),
                    bgcolor=Tokens.SUCCESS_DARK,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Edit Vocabulary Entry"),
            content=ft.Column([original_field, correction_field], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()),
                ft.TextButton("Save", on_click=_save),
            ],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _delete_word(self, item: dict):
        mgr = self._get_manager()
        category = item.get("category", self._active_category)
        original = item.get("original", "")
        if category in ("misspellings", "technical_terms", "names", "products"):
            mgr.remove_entry(category, original)
        else:
            idx = item.get("index", -1)
            if idx >= 0:
                mgr.remove_phrase(category, idx)
        self._load_vocabulary()
        self.reload()
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Deleted: {original}"),
            bgcolor=Tokens.WARNING_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _close_dialog(self):
        if hasattr(self.page, 'dialog') and self.page.dialog:
            self.page.dialog.open = False
            self.page.update()
