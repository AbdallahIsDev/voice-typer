"""Startup reconciliation of the configured ASR model selection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from voice_typer.server import startup_tasks


def make_app(
    model_size: str,
    *,
    asr_backend: str = "whisper",
    save_ok: bool = True,
) -> tuple[Any, list[bool]]:
    """Minimal app double exposing just what reconciliation touches."""
    saves: list[bool] = []

    def save() -> bool:
        saves.append(True)
        return save_ok

    config = SimpleNamespace(model_size=model_size, asr_backend=asr_backend, save=save)
    app = SimpleNamespace(config=config)
    return app, saves


def test_clears_model_size_when_configured_model_not_installed() -> None:
    """No model on disk + concrete model_size → clear to NO_MODEL_SIZE + save."""
    app, saves = make_app("tiny")
    with patch(
        "voice_typer.server.tray_models.is_active_model_downloaded",
        return_value=False,
    ):
        changed = startup_tasks.reconcile_configured_model(app)

    assert changed is True
    assert app.config.model_size == ""
    assert saves == [True]


def test_noop_when_model_is_installed() -> None:
    """Configured model on disk → left untouched, no save."""
    app, saves = make_app("tiny")
    with patch(
        "voice_typer.server.tray_models.is_active_model_downloaded",
        return_value=True,
    ):
        changed = startup_tasks.reconcile_configured_model(app)

    assert changed is False
    assert app.config.model_size == "tiny"
    assert saves == []


def test_noop_when_already_no_model_selected() -> None:
    """``model_size == \"\"`` (NO_MODEL_SIZE) → no write even if nothing installed."""
    app, saves = make_app("")
    with patch(
        "voice_typer.server.tray_models.is_active_model_downloaded",
        return_value=False,
    ):
        changed = startup_tasks.reconcile_configured_model(app)

    assert changed is False
    assert app.config.model_size == ""
    assert saves == []


def test_noop_for_cloud_backends() -> None:
    """Cloud providers have no local model to install, never touched."""
    for backend in ("openai", "groq", "deepgram", "custom"):
        app, saves = make_app("tiny", asr_backend=backend)
        with patch(
            "voice_typer.server.tray_models.is_active_model_downloaded",
            return_value=False,
        ):
            changed = startup_tasks.reconcile_configured_model(app)

        assert changed is False, f"cloud backend {backend} must not be reconciled"
        assert app.config.model_size == "tiny"
        assert saves == []


def test_save_failure_returns_false_without_raising() -> None:
    """A failed persist must not crash startup, returns False, no exception."""
    app, _saves = make_app("tiny", save_ok=False)
    with patch(
        "voice_typer.server.tray_models.is_active_model_downloaded",
        return_value=False,
    ):
        changed = startup_tasks.reconcile_configured_model(app)

    assert changed is False
    assert app.config.model_size == ""
