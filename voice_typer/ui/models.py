import flet as ft
from .styles import Colors
from .icons import icon


# Cloud provider configuration
CLOUD_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
    },
    "groq": {
        "label": "Groq",
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
    },
    "deepgram": {
        "label": "Deepgram",
        "url": "https://api.deepgram.com/v1/listen",
        "model": "nova-2",
    },
}


class ModelsScreen:
    """Models screen for managing Whisper models and cloud providers."""

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
                                content=ft.Row([icon("download", color=ft.Colors.WHITE, size=16), ft.Text("Download Model", color=ft.Colors.WHITE)], spacing=8),
                                on_click=self._download_model,
                                bgcolor=ft.Colors.BLUE_600,
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
                    self._build_cloud_providers(),
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
                        icon("download-done", size=48, color=ft.Colors.GREY_400),
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
                                    icon=icon("play-arrow" if not is_active else "check"),
                                    tooltip="Use" if not is_active else "Active",
                                    on_click=lambda e, m=model: self._use_model(m),
                                    disabled=is_active,
                                ),
                                ft.IconButton(
                                    icon=icon("delete"),
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

    def _build_cloud_providers(self) -> ft.Control:
        """Build cloud provider API key configuration section."""
        return ft.Card(
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text("Cloud ASR Providers", size=18, weight=ft.FontWeight.W_600),
                        ft.Text(
                            "Configure cloud-based transcription services",
                            size=14,
                            color=ft.Colors.GREY_600,
                        ),
                        ft.Container(height=10),
                        # OpenAI
                        self._build_provider_section(
                            "openai",
                            "OpenAI Whisper API",
                            self.config.openai_api_key if self.config else "",
                        ),
                        ft.Container(height=10),
                        # Groq
                        self._build_provider_section(
                            "groq",
                            "Groq Whisper API",
                            self.config.groq_api_key if self.config else "",
                        ),
                        ft.Container(height=10),
                        # Deepgram
                        self._build_provider_section(
                            "deepgram",
                            "Deepgram API",
                            self.config.deepgram_api_key if self.config else "",
                        ),
                    ],
                    spacing=8,
                ),
            )
        )

    def _build_provider_section(self, provider: str, label: str, api_key: str) -> ft.Control:
        """Build a cloud provider API key input with Test Connection button."""
        provider_info = CLOUD_PROVIDERS.get(provider, {})

        key_field = ft.TextField(
            label=f"{label} API Key",
            value=api_key,
            width=350,
            password=True,
            can_reveal_password=True,
        )

        test_result = ft.Text("", size=12, color=ft.Colors.GREY_600)

        def _save_key(e):
            """Save the API key to config."""
            key_value = key_field.value or ""
            if provider == "openai":
                self.config.openai_api_key = key_value
            elif provider == "groq":
                self.config.groq_api_key = key_value
            elif provider == "deepgram":
                self.config.deepgram_api_key = key_value
            self.config.save()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"{label} API key saved"),
                bgcolor=Colors.SUCCESS,
            )
            self.page.snack_bar.open = True
            self.page.update()

        def _test_connection(e):
            """Test the cloud provider connection."""
            key_value = key_field.value or ""
            if not key_value:
                test_result.value = "Please enter an API key first"
                test_result.color = ft.Colors.RED_600
                self.page.update()
                return

            try:
                from voice_typer.cloud_engines import CloudEngine
                engine = CloudEngine(
                    provider=provider,
                    api_key=key_value,
                    api_url=provider_info.get("url"),
                    model=provider_info.get("model"),
                )
                success, message = engine.test_connection()
                test_result.value = message
                test_result.color = ft.Colors.GREEN_600 if success else ft.Colors.RED_600
            except Exception as exc:
                test_result.value = f"Connection test failed: {exc}"
                test_result.color = ft.Colors.RED_600
            self.page.update()

        return ft.Column(
            [
                ft.Text(label, size=14, weight=ft.FontWeight.W_500),
                ft.Row(
                    [
                        key_field,
                        ft.ElevatedButton(
                            "Save Key",
                            on_click=_save_key,
                        ),
                        ft.ElevatedButton(
                            content=ft.Row([icon("sparkles", size=14), ft.Text("Test Connection")], spacing=6),
                            on_click=_test_connection,
                        ),
                    ],
                    spacing=8,
                    wrap=True,
                ),
                test_result,
            ],
            spacing=4,
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
                            content=ft.Row([icon("speed", size=16), ft.Text("Run Benchmark")], spacing=8),
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
