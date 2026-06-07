import flet as ft
from .styles import Colors, is_windows_dark_mode
from .icons import icon

from voice_typer.vocabulary import VocabularyManager, CATEGORIES


class VocabularyScreen:
    """Vocabulary screen for managing custom words and corrections."""

    # Display labels for the 6 categories
    CATEGORY_LABELS = {
        "misspellings": "Misspellings",
        "phrase_corrections": "Phrase Corrections",
        "extra_word_patterns": "Extra Word Patterns",
        "technical_terms": "Technical Terms",
        "names": "Names",
        "products": "Products",
    }

    CATEGORY_DESCRIPTIONS = {
        "misspellings": "Common word misspellings → corrections",
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

    def _is_dark_mode(self) -> bool:
        """Return True if the current theme is dark."""
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _get_manager(self) -> VocabularyManager:
        """Lazy-init VocabularyManager."""
        if self._vocab_manager is None:
            self._vocab_manager = VocabularyManager()
        return self._vocab_manager

    def _load_vocabulary(self):
        """Load vocabulary from the manager for display."""
        try:
            mgr = self._get_manager()
            self.vocabulary = []
            self._vocab_data = mgr.get_all()
            # Flatten dict-based categories into display items
            for cat in CATEGORIES:
                cat_data = self._vocab_data.get(cat)
                if cat in ("misspellings", "technical_terms", "names", "products"):
                    if isinstance(cat_data, dict):
                        for key, val in cat_data.items():
                            self.vocabulary.append({
                                "category": cat,
                                "original": key,
                                "correction": val,
                                "count": 0,
                            })
                elif cat in ("phrase_corrections", "extra_word_patterns"):
                    if isinstance(cat_data, list):
                        for i, entry in enumerate(cat_data):
                            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                                self.vocabulary.append({
                                    "category": cat,
                                    "original": entry[0],
                                    "correction": entry[1],
                                    "index": i,
                                    "count": 0,
                                })
        except Exception:
            self.vocabulary = []

    def build(self) -> ft.Control:
        dark = self._is_dark_mode()
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Custom Vocabulary", size=24, weight=ft.FontWeight.BOLD, color=Colors.text_primary(dark)),
                            ft.Container(expand=True),
                            ft.Button(
                                content=ft.Row([icon("add", color=ft.Colors.WHITE, size=16), ft.Text("Add Word", color=ft.Colors.WHITE)], spacing=8),
                                on_click=self._add_word,
                                bgcolor=ft.Colors.BLUE_600,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Add custom words and corrections to improve accuracy",
                        size=14,
                        color=Colors.text_secondary(dark),
                    ),
                    ft.Container(height=10),
                    self._build_category_tabs(),
                    ft.Container(height=10),
                    self._build_vocabulary_list(),
                ],
            ),
        )

    def _build_category_tabs(self) -> ft.Control:
        """Build category filter tabs for the 6 vocabulary categories."""
        dark = self._is_dark_mode()
        tabs = []
        for cat in CATEGORIES:
            is_active = cat == self._active_category
            tabs.append(
                ft.Button(
                    content=ft.Text(self.CATEGORY_LABELS.get(cat, cat), color=ft.Colors.WHITE if is_active else Colors.text_primary(dark)),
                    on_click=lambda e, c=cat: self._select_category(c),
                    bgcolor=ft.Colors.BLUE_600 if is_active else (ft.Colors.GREY_200 if not dark else ft.Colors.GREY_700),
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=16)),
                )
            )
        return ft.Row(tabs, spacing=6, wrap=True)

    def _select_category(self, category: str):
        """Select a vocabulary category to filter."""
        self._active_category = category
        self.reload()
        self.page.update()

    def _build_vocabulary_list(self) -> ft.Control:
        dark = self._is_dark_mode()
        # Filter by active category
        filtered = [v for v in self.vocabulary if v.get("category") == self._active_category]

        if not filtered:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("vocabulary", size=48, color=ft.Colors.GREY_400 if not dark else ft.Colors.GREY_500),
                        ft.Text(
                            f"No {self.CATEGORY_LABELS.get(self._active_category, self._active_category).lower()}",
                            size=16,
                            color=ft.Colors.GREY_600 if not dark else ft.Colors.GREY_400,
                        ),
                        ft.Text(
                            self.CATEGORY_DESCRIPTIONS.get(self._active_category, ""),
                            size=14,
                            color=ft.Colors.GREY_500 if not dark else ft.Colors.GREY_500,
                        ),
                        ft.Container(height=10),
                        ft.Button(
                            content=ft.Row([icon("add", size=16), ft.Text("Add First Word")], spacing=8),
                            on_click=self._add_word,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[
                self._vocabulary_item(item) for item in filtered
            ],
            spacing=8,
        )

    def _vocabulary_item(self, item: dict) -> ft.Control:
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
                                                item.get("original", ""),
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=ft.Colors.RED_800 if not dark else ft.Colors.RED_200,
                                            ),
                                            bgcolor=ft.Colors.RED_50 if not dark else ft.Colors.RED_900,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                            border_radius=4,
                                        ),
                                        icon("arrow-forward", size=16, color=ft.Colors.GREY_400 if not dark else ft.Colors.GREY_500),
                                        ft.Container(
                                            content=ft.Text(
                                                item.get("correction", ""),
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                                color=ft.Colors.GREEN_800 if not dark else ft.Colors.GREEN_200,
                                            ),
                                            bgcolor=ft.Colors.GREEN_50 if not dark else ft.Colors.GREEN_900,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                            border_radius=4,
                                        ),
                                    ],
                                    spacing=8,
                                ),
                                ft.Text(
                                    self.CATEGORY_LABELS.get(item.get("category", ""), item.get("category", "")),
                                    size=12,
                                    color=Colors.text_secondary(dark),
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
                                    on_click=lambda e, i=item: self._edit_word(i),
                                ),
                                ft.IconButton(
                                    icon=icon("delete"),
                                    tooltip="Delete",
                                    on_click=lambda e, i=item: self._delete_word(i),
                                ),
                            ],
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ),
        ),
    )

    def _add_word(self, e):
        """Open a dialog to add a new vocabulary entry."""
        original_field = ft.TextField(label="Original (misrecognized word)", width=300)
        correction_field = ft.TextField(label="Correction", width=300)
        category_dropdown = ft.Dropdown(
            label="Category",
            width=300,
            options=[
                ft.dropdown.Option(cat, self.CATEGORY_LABELS.get(cat, cat))
                for cat in CATEGORIES
            ],
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
                    content=ft.Text(f"Added: {original} → {correction}"),
                    bgcolor=Colors.SUCCESS,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Add Vocabulary Entry"),
            content=ft.Column([
                original_field,
                correction_field,
                category_dropdown,
            ], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()),
                ft.TextButton("Save", on_click=_save),
            ],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _edit_word(self, item: dict):
        """Open a dialog to edit an existing vocabulary entry."""
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
                    content=ft.Text(f"Updated: {original} → {correction}"),
                    bgcolor=Colors.SUCCESS,
                )
                self.page.snack_bar.open = True
            self.page.dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text("Edit Vocabulary Entry"),
            content=ft.Column([
                original_field,
                correction_field,
            ], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: self._close_dialog()),
                ft.TextButton("Save", on_click=_save),
            ],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def _delete_word(self, item: dict):
        """Delete a vocabulary entry from the manager."""
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
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _close_dialog(self):
        """Close the active dialog."""
        if hasattr(self.page, 'dialog') and self.page.dialog:
            self.page.dialog.open = False
            self.page.update()
