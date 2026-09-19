"""Shared app-level test helpers for VoiceTyperApp and audio test data."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def make_voice_typer_app(tmp_config_dir: Any, monkeypatch: Any) -> Any:
    """Build a ``VoiceTyperApp`` with mocked hardware/GUI dependencies."""
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        lambda *a, **k: None,
    )

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # (revised): RecordingController.start() now enforces
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is created in _do_startup (background), not
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    # Never duck the developer's REAL system volume. The volume
    instance._volume_ducker = MagicMock()
    instance._volume_ducker.initialize.return_value = False
    return instance


def make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5):
    """Generate a 1-D float32 numpy sine wave."""
    import numpy as np

    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def join_model_load_thread(app: Any, timeout: float = 2.0) -> None:
    """Best-effort join of ``app.models._model_load_thread`` after a test."""
    models = getattr(app, "models", None)
    if models is None:
        return
    loader = getattr(models, "_model_load_thread", None)
    if loader is None:
        return
    if not getattr(loader, "is_alive", lambda: False)():
        return
    loader.join(timeout=timeout)


__all__ = [
    "make_voice_typer_app",
    "make_sine",
    "join_model_load_thread",
]
