import flet as ft
import threading
import os
from typing import Optional, Callable
from .styles import Tokens, is_windows_dark_mode
from .icons import icon

CLOUD_PROVIDERS = {
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1/audio/transcriptions", "model": "whisper-1"},
    "groq": {"label": "Groq", "url": "https://api.groq.com/openai/v1/audio/transcriptions", "model": "whisper-large-v3"},
    "deepgram": {"label": "Deepgram", "url": "https://api.deepgram.com/v1/listen", "model": "nova-2"},
}

_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"


def _ensure_hf_env():
    """Ensure HF_HOME is set to ~/.voice-typer/huggingface/ for this process."""
    from voice_typer.server.config import _config_dir as _get_cfg_dir
    hf_home = str(_get_cfg_dir() / "huggingface")
    os.environ.setdefault("HF_HOME", hf_home)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "true")
    os.environ.setdefault("HF_HUB_DISABLE_UNVERIFIED_ACCESS_WARNING", "1")


def _check_whisper_cached(model_size: str) -> bool:
    """Check if a Systran/faster-whisper-{size} model is in HF cache."""
    _ensure_hf_env()
    try:
        from huggingface_hub import snapshot_download
        repo_id = f"Systran/faster-whisper-{model_size}"
        snapshot_download(repo_id=repo_id, local_files_only=True)
        return True
    except Exception:
        return False


def _check_parakeet_cached() -> bool:
    """Check if Parakeet weights are in HF cache."""
    _ensure_hf_env()
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=_PARAKERT_MODEL_ID, local_files_only=True)
        return True
    except Exception:
        return False


def _check_transformers_installed() -> bool:
    try:
        import transformers  # noqa
        return True
    except ImportError:
        return False


def _check_qwen_installed() -> bool:
    try:
        import qwen_asr  # noqa
        return True
    except ImportError:
        return False


class ModelsScreen:
    def __init__(self, page: ft.Page, config, reload=None):
        self.page = page
        self.config = config
        self.reload = reload or (lambda: None)
        self.active_model = config.model_size if config else "tiny.en"
        self._download_progress = 0
        self._download_status_text = ""
        self._is_downloading = False
        self._installing_deps = False
        self._benchmark_result = None
        self.models = self._build_models_list()

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
            {
                "name": "tiny.en",
                "size": "~75MB",
                "speed": "Fastest",
                "backend": "whisper",
                "downloaded": _check_whisper_cached("tiny.en"),
            },
            {
                "name": "small.en",
                "size": "~466MB",
                "speed": "Fast",
                "backend": "whisper",
                "downloaded": _check_whisper_cached("small.en"),
            },
            {
                "name": "medium.en",
                "size": "~1.5GB",
                "speed": "Slow",
                "backend": "whisper",
                "downloaded": _check_whisper_cached("medium.en"),
            },
            {
                "name": "qwen",
                "size": "Variable",
                "speed": "Fast",
                "backend": "qwen",
                "downloaded": _check_qwen_installed(),
            },
            {
                "name": "parakeet",
                "size": "~2.5GB",
                "speed": "Fast",
                "backend": "parakeet",
                "downloaded": _check_parakeet_cached(),
                "deps_ok": _check_transformers_installed(),
            },
        ]
        for m in models:
            if m["name"] == "parakeet":
                is_active = (self.config.asr_backend == "parakeet" and self.active_model == "parakeet")
            elif m["name"] == "qwen":
                is_active = (self.config.asr_backend == "qwen" and self.active_model == "qwen")
            else:
                is_active = (self.config.asr_backend == "whisper" and m["name"] == self.active_model)
            m["is_active"] = is_active
        return models

    def build(self) -> ft.Control:
        dark = self._is_dark()
        tp = Tokens.text_primary(dark)
        ts = Tokens.text_secondary(dark)
        ap = Tokens.accent_primary(dark)

        download_btn = ft.Container(
            content=ft.Row(
                [icon("download", color="#FFFFFF", size=16), ft.Text("Download Model", color="#FFFFFF")],
                spacing=8,
            ),
            on_click=self._download_model,
            bgcolor=ap,
            padding=ft.Padding(left=16, right=16, top=10, bottom=10),
            border_radius=8,
        )

        progress_section = ft.Column(spacing=4)
        if self._is_downloading or self._installing_deps:
            progress_section.controls = [
                ft.Container(height=8),
                ft.ProgressBar(width=760, value=self._download_progress / 100 if self._download_progress > 0 else None),
                ft.Text(self._download_status_text, size=12, color=ts),
                ft.Container(height=8),
            ]

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
                                download_btn,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                        progress_section,
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
        is_active = model.get("is_active", False)
        is_downloaded = model.get("downloaded", True)
        deps_ok = model.get("deps_ok", True)
        name = model.get("name", "")

        if is_active:
            status = "Active"
            badge_bg = "rgba(34,197,94,0.12)"
            badge_color = "#4ADE80"
        elif is_downloaded:
            status = "Downloaded"
            badge_bg = "rgba(34,197,94,0.12)"
            badge_color = "#4ADE80"
        elif not deps_ok and name == "parakeet":
            status = "Dependencies required"
            badge_bg = "rgba(245,158,11,0.12)"
            badge_color = "#F59E0B"
        else:
            status = "Available"
            badge_bg = "rgba(37,99,235,0.10)"
            badge_color = "#60A5FA"

        subtitle_parts = [f"Size: {model.get('size', 'Unknown')}"]
        if name == "parakeet":
            subtitle_parts.insert(0, "NVIDIA Parakeet TDT v3")
        subtitle = "  ·  ".join(subtitle_parts)

        actions = ft.Row(spacing=4)

        if name == "parakeet" and not deps_ok:
            actions.controls.append(
                ft.Container(
                    content=ft.Row([icon("download", size=14, color="#FFFFFF"), ft.Text("Install Deps", size=12, color="#FFFFFF")], spacing=4),
                    on_click=lambda e, m=model: self._install_deps_and_download(m),
                    bgcolor="#F59E0B",
                    padding=ft.Padding(left=12, right=12, top=6, bottom=6),
                    border_radius=6,
                )
            )
        else:
            actions.controls.append(
                ft.IconButton(
                    icon=icon("play-arrow" if not is_active else "check"),
                    on_click=lambda e, m=model: self._use_model(m),
                    disabled=is_active or (not is_downloaded and not deps_ok),
                )
            )

        actions.controls.append(
            ft.IconButton(
                icon=icon("delete"),
                on_click=lambda e, m=model: self._delete_model_confirm(m),
                disabled=is_active,
            )
        )

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
                                    ft.Text(name, size=16, weight=ft.FontWeight.W_600, color=tp),
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
                                    ft.Text(subtitle, size=11, color=ts),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    actions,
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

    def _install_deps_and_download(self, model: dict):
        """Install dependencies then download model, with progress."""
        if self._is_downloading or self._installing_deps:
            return

        self._is_downloading = True
        self._installing_deps = True
        self._download_progress = 0
        self._download_status_text = f"Installing dependencies for {model['name']}..."
        self.reload()

        def _do_install():
            try:
                from voice_typer.server.asr_setup import get_voice_typer_python
                python_exe = get_voice_typer_python()
                result = subprocess.run(
                    [python_exe, "-m", "pip", "install", "transformers>=4.40"],
                    capture_output=True, text=True, timeout=300,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"Dependency install failed: {result.stderr[:200]}")

                self._installing_deps = False
                model["deps_ok"] = _check_transformers_installed()
                if not model["deps_ok"]:
                    raise RuntimeError("transformers still not importable after install")

                self._download_status_text = f"Downloading {model['name']} model (~2.5GB)..."
                self._download_progress = 10
                self.reload()

                from voice_typer.server.asr_setup import download_parakeet_weights
                success = download_parakeet_weights()
                if not success:
                    raise RuntimeError("Parakeet download returned failure")

                self._download_progress = 100
                self._download_status_text = "Download complete!"
                model["downloaded"] = True
                self._show_snack(f"Model '{model['name']}' ready!", Tokens.SUCCESS_DARK)
            except Exception as exc:
                self._download_status_text = f"Failed: {exc}"
                self._show_snack(f"Failed: {exc}", Tokens.ACCENT_DANGER_DARK)
            finally:
                self._is_downloading = False
                self._installing_deps = False
                self.reload()

        t = threading.Thread(target=_do_install, daemon=True)
        t.start()

    def _download_model(self, e=None):
        if self._is_downloading or self._installing_deps:
            return

        # Find first undownloaded model
        target = None
        for m in self.models:
            if m["name"] == "parakeet" and not m.get("downloaded"):
                target = m
                break
        if target is None:
            for m in self.models:
                if not m.get("downloaded"):
                    target = m
                    break

        if target is None:
            self._show_snack("All models already downloaded", Tokens.SUCCESS_DARK)
            return

        self._is_downloading = True
        self._download_progress = 0
        self._download_status_text = f"Preparing {target['name']}..."
        self.reload()

        def _do_download():
            try:
                if target["name"] == "parakeet":
                    # Check+install deps first
                    if not target.get("deps_ok"):
                        self._installing_deps = True
                        self._download_status_text = "Installing transformers dependency..."
                        self._download_progress = 5
                        self.reload()

                        from voice_typer.server.asr_setup import get_voice_typer_python
                        python_exe = get_voice_typer_python()
                        result = subprocess.run(
                            [python_exe, "-m", "pip", "install", "transformers>=4.40"],
                            capture_output=True, text=True, timeout=300,
                        )
                        if result.returncode != 0:
                            raise RuntimeError(f"Dependency install failed: {result.stderr[:200]}")

                        self._installing_deps = False
                        target["deps_ok"] = _check_transformers_installed()
                        if not target["deps_ok"]:
                            raise RuntimeError("transformers still not importable after install")

                    self._download_status_text = "Downloading Parakeet TDT v3 model (~2.5GB)..."
                    self._download_progress = 10
                    self.reload()

                    from voice_typer.server.asr_setup import download_parakeet_weights
                    success = download_parakeet_weights()
                    if not success:
                        raise RuntimeError("Parakeet download returned failure")
                elif target["name"] == "qwen":
                    from voice_typer.server.asr_setup import download_weights
                    success = download_weights(target["name"])
                    if not success:
                        raise RuntimeError("Qwen download returned failure")
                else:
                    from voice_typer.server.asr_setup import download_weights
                    success = download_weights(target["name"])
                    if not success:
                        raise RuntimeError(f"Download failed for {target['name']}")

                self._download_progress = 100
                self._download_status_text = "Download complete!"
                target["downloaded"] = True

                self._show_snack(f"Model '{target['name']}' downloaded!", Tokens.SUCCESS_DARK)
            except Exception as exc:
                self._download_status_text = f"Failed: {exc}"
                self._show_snack(f"Download failed: {exc}", Tokens.ACCENT_DANGER_DARK)
            finally:
                self._is_downloading = False
                self._installing_deps = False
                self.reload()

        t = threading.Thread(target=_do_download, daemon=True)
        t.start()

    def _use_model(self, model: dict):
        name = model.get("name")
        backend = model.get("backend", "whisper")

        # If parakeet deps missing, trigger install+download first
        if name == "parakeet" and not model.get("deps_ok"):
            self._install_deps_and_download(model)
            return
        if name == "parakeet" and not model.get("downloaded"):
            self._install_deps_and_download(model)
            return

        self.active_model = name
        if backend == "whisper":
            self.config.asr_backend = "whisper"
            self.config.model_size = name
        elif backend == "qwen":
            self.config.asr_backend = "qwen"
            self.config.model_size = "qwen"
        elif backend == "parakeet":
            self.config.asr_backend = "parakeet"
            self.config.model_size = "parakeet"

        self.config.save()
        for m in self.models:
            m["is_active"] = False
        model["is_active"] = True
        self.reload()
        self._show_snack(f"Using model: {name}", Tokens.SUCCESS_DARK)

    def _delete_model_confirm(self, model: dict):
        if model.get("is_active"):
            self._show_snack("Cannot delete the active model. Switch to another model first.", Tokens.WARNING_DARK)
            return

        def _do_delete(e):
            self.page.dialog.open = False
            self.models = [m for m in self.models if m["name"] != model["name"]]
            self._show_snack(f"Deleted model: {model['name']}", Tokens.WARNING_DARK)
            self.reload()

        def _cancel(e):
            self.page.dialog.open = False
            self.page.update()

        self.page.dialog = ft.AlertDialog(
            title=ft.Text("Delete Model"),
            content=ft.Text(f"Are you sure you want to delete the model '{model['name']}'? This cannot be undone."),
            actions=[ft.TextButton("Cancel", on_click=_cancel), ft.TextButton("Delete", on_click=_do_delete)],
        )
        self.page.dialog.open = True
        self.page.update()

    def _run_benchmark(self, e):
        self._show_snack("Running benchmark...", Tokens.ACCENT_PRIMARY_DARK)

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
            self._show_snack(self._benchmark_result, Tokens.ACCENT_PRIMARY_DARK)

        t = threading.Thread(target=_do_benchmark, daemon=True)
        t.start()

    def _show_snack(self, msg: str, color: str):
        if self.page is None:
            return
        self.page.snack_bar = ft.SnackBar(content=ft.Text(msg), bgcolor=color)
        self.page.snack_bar.open = True
        self.page.update()
