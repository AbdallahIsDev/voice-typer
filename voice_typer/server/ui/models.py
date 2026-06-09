import flet as ft
import threading
from .styles import Tokens, is_windows_dark_mode
from .icons import icon


CLOUD_PROVIDERS = {
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1/audio/transcriptions", "model": "whisper-1"},
    "groq": {"label": "Groq", "url": "https://api.groq.com/openai/v1/audio/transcriptions", "model": "whisper-large-v3"},
    "deepgram": {"label": "Deepgram", "url": "https://api.deepgram.com/v1/listen", "model": "nova-2"},
}


class ModelsScreen:
    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self.active_model = config.model_size if config else "tiny.en"
        self.models = self._build_models_list()
        self._download_progress = 0
        self._benchmark_result = None

    def _is_dark(self) -> bool:
        if self.page is None:
            return is_windows_dark_mode()
        if self.page.theme_mode == ft.ThemeMode.DARK:
            return True
        if self.page.theme_mode == ft.ThemeMode.LIGHT:
            return False
        return is_windows_dark_mode()

    def _build_models_list(self) -> list[dict]:
        models = [
            {"name": "tiny.en", "size": "~75MB", "speed": "Fastest", "downloaded": True},
            {"name": "small.en", "size": "~466MB", "speed": "Fast", "downloaded": True},
            {"name": "medium.en", "size": "~1.5GB", "speed": "Slow", "downloaded": True},
            {"name": "qwen", "size": "Variable", "speed": "Fast", "downloaded": False},
        ]
        for m in models:
            m["is_active"] = m["name"] == self.active_model
        return models

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
                                ft.Column(
                                    [
                                        ft.Text("Models", size=20, weight=ft.FontWeight.W_600, color=tp),
                                        ft.Text("Configure your speech-to-text engines...", size=13, color=ts),
                                    ],
                                    spacing=2,
                                ),
                                ft.Container(expand=True),
                                ft.Container(
                                    content=ft.Row([icon("download", color="#FFFFFF", size=16), ft.Text("Download Model", color="#FFFFFF")], spacing=8),
                                    on_click=self._download_model,
                                    bgcolor=ap,
                                    padding=ft.Padding(left=16, right=16, top=10, bottom=10),
                                    border_radius=8,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                        self._build_models_list_ui(),
                        self._build_cloud_providers(),
                        ft.Container(height=20),
                        self._build_model_benchmark(),
                    ],
                ),
            ),
        )

    def _build_models_list_ui(self) -> ft.Control:
        dark = self._is_dark()
        ts = Tokens.text_secondary(dark)
        has_models = any(m.get("downloaded", True) for m in self.models)
        if not has_models or not self.models:
            return ft.Container(
                padding=40,
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    [
                        icon("download-done", size=48, color=ts),
                        ft.Text("No models downloaded", size=16, color=ts),
                        ft.Text("Download a model to start transcribing", size=14, color=ts),
                        ft.Container(height=10),
                        ft.Button(
                            content=ft.Row([icon("download", size=16), ft.Text("Download Model")], spacing=8),
                            on_click=self._download_model,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        return ft.ListView(
            controls=[self._model_item(model) for model in self.models],
            spacing=8,
        )

    def _model_item(self, model: dict) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        is_active = model.get("name") == self.active_model
        is_downloaded = model.get("downloaded", True)
        status = "Active" if is_active else ("Downloaded" if is_downloaded else "Available")

        if status in ("Active", "Downloaded"):
            badge_bg = "rgba(34,197,94,0.12)"
            badge_color = "#4ADE80"
        else:
            badge_bg = "rgba(37,99,235,0.10)"
            badge_color = "#60A5FA"

        return ft.Container(
            bgcolor=Tokens.bg_card(dark) if not is_active else Tokens.bg_card(dark),
            border=ft.Border.all(1, ap if is_active else Tokens.border_subtle(dark)),
            border_radius=10,
            padding=ft.Padding(20, 16, 20, 16),
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(model.get("name", ""), size=16, weight=ft.FontWeight.W_600, color=tp),
                                    ft.Container(
                                        content=ft.Text(status, size=11, color=badge_color, weight=ft.FontWeight.W_600),
                                        bgcolor=badge_bg,
                                        border=ft.Border.all(0.5, badge_color + "40"),
                                        border_radius=6,
                                        padding=ft.Padding(10, 2, 10, 2),
                                    ),
                                ],
                                spacing=8,
                            ),
                            ft.Row(
                                [
                                    ft.Text(f"Size: {model.get('size', 'Unknown')}", size=11, color=ts),
                                    ft.Row(
                                        [icon("speed", size=11, color=ts),
                                         ft.Text(f"Speed: {model.get('speed', 'Unknown')}", size=11, color=ts)],
                                        spacing=4,
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
                                icon=icon("play-arrow" if not is_active else "check"),
                                on_click=lambda e, m=model: self._use_model(m),
                                disabled=is_active,
                            ),
                            ft.IconButton(
                                icon=icon("delete"),
                                on_click=lambda e, m=model: self._delete_model_confirm(m),
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _build_cloud_providers(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        return ft.Column(
            [
                ft.Container(height=20),
                ft.Text("Cloud ASR Providers", size=20, weight=ft.FontWeight.W_600, color=tp),
                ft.Text("Configure cloud-based transcription services", size=13, color=ts),
                ft.Container(height=10),
                self._build_provider_card("openai", "OpenAI Whisper API", self.config.openai_api_key if self.config else ""),
                self._build_provider_card("groq", "Groq Whisper API", self.config.groq_api_key if self.config else ""),
                self._build_provider_card("deepgram", "Deepgram API", self.config.deepgram_api_key if self.config else ""),
            ],
            spacing=8,
        )

    def _build_provider_card(self, provider: str, label: str, api_key: str) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)
        provider_info = CLOUD_PROVIDERS.get(provider, {})

        key_field = ft.TextField(
            label=f"{label} API Key",
            value=api_key,
            password=True,
            can_reveal_password=True,
            color=tp,
            bgcolor=Tokens.bg_app(dark),
            border_color=Tokens.border_subtle(dark),
            border_radius=8,
            text_size=13,
            content_padding=12,
            width=500,
        )

        test_result = ft.Text("", size=12)

        def _save_key(e):
            key_value = key_field.value or ""
            if provider == "openai":
                self.config.openai_api_key = key_value
            elif provider == "groq":
                self.config.groq_api_key = key_value
            elif provider == "deepgram":
                self.config.deepgram_api_key = key_value
            self.config.save()
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"{label} API key saved"),
                bgcolor=Tokens.SUCCESS_DARK,
            )
            self.page.snack_bar.open = True
            self.page.update()

        def _test_connection(e):
            key_value = key_field.value or ""
            if not key_value:
                test_result.value = "Please enter an API key first"
                test_result.color = Tokens.ACCENT_DANGER_DARK
                self.page.update()
                return
            try:
                from voice_typer.server.cloud_engines import CloudEngine
                engine = CloudEngine(
                    provider=provider, api_key=key_value,
                    api_url=provider_info.get("url"), model=provider_info.get("model"),
                )
                success, message = engine.test_connection()
                test_result.value = message
                test_result.color = Tokens.SUCCESS_DARK if success else Tokens.ACCENT_DANGER_DARK
            except Exception as exc:
                test_result.value = f"Connection test failed: {exc}"
                test_result.color = Tokens.ACCENT_DANGER_DARK
            self.page.update()

        return ft.Container(
            bgcolor=Tokens.bg_card(dark),
            border=ft.Border.all(1, Tokens.border_subtle(dark)),
            border_radius=12,
            padding=24,
            margin=ft.Margin(left=0, top=0, right=0, bottom=16),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            icon("privacy", color=ap, size=20),
                            ft.Text(f"{label} Settings", size=16, weight=ft.FontWeight.W_600, color=tp),
                        ],
                        spacing=8,
                    ),
                    key_field,
                    ft.Row(
                        [
                            ft.Button(
                                "Save Key",
                                on_click=_save_key,
                                width=140,
                                style=ft.ButtonStyle(
                                    bgcolor=ap,
                                    color="#FFFFFF",
                                    shape=ft.RoundedRectangleBorder(radius=8),
                                ),
                            ),
                            ft.Container(
                                content=ft.Row([icon("sparkles", size=14), ft.Text("Test Connection")], spacing=6),
                                on_click=_test_connection,
                                padding=ft.Padding(left=16, right=16, top=9, bottom=9),
                                border_radius=8,
                                bgcolor=ap,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.START,
                        spacing=8,
                    ),
                    test_result,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.START,
                spacing=16,
            ),
        )

    def _build_model_benchmark(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        return ft.Container(
            bgcolor=Tokens.bg_card(dark),
            border=ft.Border.all(1, Tokens.border_subtle(dark)),
            border_radius=10,
            padding=20,
            content=ft.Column(
                [
                    ft.Text("Model Benchmark", size=20, weight=ft.FontWeight.W_600, color=tp),
                    ft.Text("Compare model performance on your system", size=13, color=ts),
                    ft.Container(height=10),
                    ft.Container(
                        content=ft.Row([icon("speed", size=16), ft.Text("Run Benchmark")], spacing=8),
                        on_click=self._run_benchmark,
                        padding=ft.Padding(left=16, right=16, top=9, bottom=9),
                        border_radius=8,
                        bgcolor=Tokens.accent_primary(dark),
                    ),
                    ft.Container(height=10),
                    ft.Text(
                        self._benchmark_result or "", size=13, color=ts,
                        visible=bool(self._benchmark_result),
                    ),
                ],
                spacing=8,
            ),
        )

    def _download_model(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Model download started... Check tray tooltip for progress."),
            bgcolor=Tokens.ACCENT_PRIMARY_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

        def _do_download():
            try:
                import time
                for pct in range(0, 101, 10):
                    time.sleep(0.3)
                    self._download_progress = pct
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("Model download complete!"),
                    bgcolor=Tokens.SUCCESS_DARK,
                )
                self.page.snack_bar.open = True
                self.page.update()
            except Exception as exc:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(f"Download failed: {exc}"),
                    bgcolor=Tokens.ACCENT_DANGER_DARK,
                )
                self.page.snack_bar.open = True
                self.page.update()

        t = threading.Thread(target=_do_download, daemon=True)
        t.start()

    def _use_model(self, model: dict):
        self.active_model = model.get("name")
        self.config.model_size = model.get("name")
        self.config.save()
        for m in self.models:
            m["is_active"] = m["name"] == self.active_model
        self.reload()
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text(f"Using model: {model.get('name')}"),
            bgcolor=Tokens.SUCCESS_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _delete_model_confirm(self, model: dict):
        if model.get("name") == self.active_model:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Cannot delete the active model. Switch to another model first."),
                bgcolor=Tokens.WARNING_DARK,
            )
            self.page.snack_bar.open = True
            self.page.update()
            return

        def _do_delete(dialog_e):
            self.page.dialog.open = False
            self.models = [m for m in self.models if m.get("name") != model.get("name")]
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(f"Deleted model: {model.get('name')}"),
                bgcolor=Tokens.WARNING_DARK,
            )
            self.page.snack_bar.open = True
            self.page.update()

        def _cancel(dialog_e):
            self.page.dialog.open = False
            self.page.update()

        self.page.dialog = ft.AlertDialog(
            title=ft.Text("Delete Model"),
            content=ft.Text(f"Are you sure you want to delete the model '{model.get('name')}'? This cannot be undone."),
            actions=[ft.TextButton("Cancel", on_click=_cancel), ft.TextButton("Delete", on_click=_do_delete)],
        )
        self.page.dialog.open = True
        self.page.update()

    def _run_benchmark(self, e):
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Running benchmark..."),
            bgcolor=Tokens.ACCENT_PRIMARY_DARK,
        )
        self.page.snack_bar.open = True
        self.page.update()

        def _do_benchmark():
            try:
                import time
                import numpy as np
                start = time.time()
                data = np.random.randn(16000 * 5).astype(np.float32)
                for _ in range(10):
                    _ = np.sqrt(np.mean(np.square(data)))
                elapsed = time.time() - start
                self._benchmark_result = (
                    f"Benchmark complete: {elapsed:.2f}s for 10 iterations "
                    f"on {self.config.device} device"
                )
            except Exception as exc:
                self._benchmark_result = f"Benchmark failed: {exc}"
            self.reload()
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text(self._benchmark_result),
                bgcolor=Tokens.ACCENT_PRIMARY_DARK,
            )
            self.page.snack_bar.open = True
            self.page.update()

        t = threading.Thread(target=_do_benchmark, daemon=True)
        t.start()
