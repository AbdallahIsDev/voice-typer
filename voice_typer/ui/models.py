import flet as ft
from .styles import Colors


class ModelsScreen:
    """Models screen for managing Whisper models."""

    def __init__(self, page: ft.Page, config):
        self.page = page
        self.config = config
        self.models = [
            {"name": "tiny.en", "size": "~75MB", "speed": "Fastest"},
            {"name": "small.en", "size": "~466MB", "speed": "Fast"},
            {"name": "medium.en", "size": "~1.5GB", "speed": "Slow"},
            {"name": "qwen", "size": "Variable", "speed": "Fast"},
        ]
        self.active_model = config.model_size if config else "tiny.en"

    def build(self) -> ft.Control:
        return ft.Container(
            padding=40,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Models", size=24, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            ft.ElevatedButton(
                                "Download Model",
                                icon=ft.Icons.DOWNLOAD,
                                on_click=self._download_model,
                                bgcolor=ft.Colors.BLUE_600,
                                color=ft.Colors.WHITE,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Manage Whisper models for transcription",
                        size=14,
                        color=ft.Colors.GREY_600,
                    ),
                    ft.Container(height=10),
                    self._build_models_list(),
                    ft.Container(height=20),
                    self._build_model_benchmark(),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

    def _build_models_list(self) -> ft.Control:
        if not self.models:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.DOWNLOAD_DONE, size=48, color=ft.Colors.GREY_400),
                        ft.Text(
                            "No models downloaded",
                            size=16,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Text(
                            "Download a model to start transcribing",
                            size=14,
                            color=ft.Colors.GREY_500,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[
                self._model_item(model) for model in self.models
            ],
            spacing=8,
        )

    def _model_item(self, model: dict) -> ft.Control:
        is_active = model.get("name") == self.active_model
        return ft.Card(
            content=ft.Container(
                padding=16,
                content=ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            model.get("name", ""),
                                            size=16,
                                            weight=ft.FontWeight.W_600,
                                        ),
                                        ft.Container(
                                            content=ft.Text(
                                                "Active" if is_active else "Downloaded",
                                                size=12,
                                                color=ft.Colors.WHITE,
                                            ),
                                            bgcolor=ft.Colors.GREEN_600 if is_active else ft.Colors.GREY_500,
                                            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                            border_radius=10,
                                        ),
                                    ],
                                    spacing=8,
                                ),
                                ft.Row(
                                    [
                                        ft.Text(
                                            f"Size: {model.get('size', 'Unknown')}",
                                            size=12,
                                            color=ft.Colors.GREY_600,
                                        ),
                                        ft.Text(
                                            f"Speed: {model.get('speed', 'Unknown')}",
                                            size=12,
                                            color=ft.Colors.GREY_600,
                                        ),
                                    ],
                                    spacing=16,
                                ),
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        ft.Row(
                            [
                                ft.IconButton(
                                    ft.Icons.PLAY_ARROW if not is_active else ft.Icons.CHECK,
                                    tooltip="Use" if not is_active else "Active",
                                    on_click=lambda e, m=model: self._use_model(m),
                                    disabled=is_active,
                                ),
                                ft.IconButton(
                                    ft.Icons.DELETE,
                                    tooltip="Delete",
                                    on_click=lambda e, m=model: self._delete_model(m),
                                ),
                            ],
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )
        )

    def _build_model_benchmark(self) -> ft.Control:
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Model Benchmark", size=18, weight=ft.FontWeight.W_600),
                        ft.Text(
                            "Compare model performance on your system",
                            size=14,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            "Run Benchmark",
                            icon=ft.Icons.SPEED,
                            on_click=self._run_benchmark,
                        ),
                    ],
                    spacing=8,
                ),
            )
        )

    def _download_model(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Model download started..."),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _use_model(self, model: dict):
        """Select a model for use."""
        self.active_model = model.get("name")
        self.config.model_size = model.get("name")
        self.config.save()
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Using model: {model.get('name')}"),
            bgcolor=Colors.SUCCESS,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_model(self, model: dict):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Deleted model: {model.get('name')}"),
            bgcolor=Colors.WARNING,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _run_benchmark(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Running benchmark..."),
            bgcolor=Colors.INFO,
        )
        self.page.snack_bar.open = True
        self.page.update()
