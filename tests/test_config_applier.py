"""regression guard: verify ``config_applier`` module extraction."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest


def _has_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


@pytest.fixture
def fake_app() -> MagicMock:
    """Build a fake LausuApp with the minimal attribute surface"""
    app = MagicMock()
    app.config.autostart = False
    app.config.hotkey = "<f2>"
    app.config.recording_mode = "toggle"
    app.config.esc_cancel_enabled = True
    app.config.repaste_enabled = False
    app.config.repaste_hotkey = None
    app.config.show_notifications = True
    app.config.bubble_behavior = "always_visible"
    app.config.tray_left_click_action = "toggle_dictation"
    app.config.active_audio_preset = "auto"
    app.config.noise_filter_enabled = True
    app.config.active_model = "small.en"
    app.config.noise_suppressor_backend = "rnnoise"
    app.config.vad_enabled = True
    app.config.vad_threshold = 0.5
    app.config.llm_polish_enabled = False
    app.hotkeys.register_esc = MagicMock()
    app.hotkeys.unregister_esc = MagicMock()
    app.hotkeys.register_repaste = MagicMock()
    app.hotkeys.restart = MagicMock()
    app.tray.invalidate_menu_cache = MagicMock()
    app.tray.set_notifications_enabled = MagicMock()
    app._waveform_bubble = MagicMock()
    return app


def test_config_applier_module_exists() -> None:
    """The extracted module must exist after Fix-D."""
    assert _has_module("voice_typer.server.config_applier"), (
        "voice_typer.server.config_applier module not found, "
        "Fix-D should extract apply_config_side_effects into this "
        "module."
    )


def test_config_applier_exposes_callable(fake_app) -> None:
    """The module must expose a callable entry point."""
    if not _has_module("voice_typer.server.config_applier"):
        pytest.skip("Fix-D not yet landed")

    mod = importlib.import_module("voice_typer.server.config_applier")
    has_func = callable(getattr(mod, "apply_config_side_effects", None))
    has_class = hasattr(mod, "ConfigApplier")
    assert has_func or has_class, "config_applier must expose either apply_config_side_effects() or ConfigApplier class"


def test_service_apply_config_delegates_to_module(fake_app) -> None:
    """extracted module, calling it should invoke the ``ConfigApplier``"""
    if not _has_module("voice_typer.server.config_applier"):
        pytest.skip("Fix-D not yet landed")

    from voice_typer.server.service import LausuService

    mod = importlib.import_module("voice_typer.server.config_applier")
    svc = LausuService(fake_app)

    captured: list = []
    if hasattr(mod, "ConfigApplier"):
        # Service delegates to ``self._config_applier.apply_config_side_effects``
        def _spy(*args, **kwargs):
            captured.append((args, kwargs))
            return {"autostart_status": None, "prewarm_status": None}

        svc._config_applier.apply_config_side_effects = _spy  # type: ignore[assignment]
    elif hasattr(mod, "apply_config_side_effects"):

        def _spy(*args, **kwargs):
            captured.append((args, kwargs))
            return None

        mod.apply_config_side_effects = _spy  # type: ignore[assignment]

    updates = {"hotkey": "<f3>"}
    svc.apply_config_side_effects(updates)
    assert captured, (
        "Expected LausuService.apply_config_side_effects to delegate to the extracted config_applier module."
    )


def test_extraction_preserves_hotkey_restart_behavior(fake_app) -> None:
    """After extraction, a hotkey change must still trigger"""
    from voice_typer.server.service import LausuService

    svc = LausuService(fake_app)
    svc.apply_config_side_effects({"hotkey": "<f3>"})
    fake_app.hotkeys.restart.assert_called()


def test_extraction_preserves_esc_hotkey_register(fake_app) -> None:
    """``esc_cancel_enabled=True`` must trigger ``register_esc``."""
    from voice_typer.server.service import LausuService

    svc = LausuService(fake_app)
    svc.apply_config_side_effects({"esc_cancel_enabled": True})
    fake_app.hotkeys.register_esc.assert_called()


def test_extraction_preserves_esc_hotkey_unregister(fake_app) -> None:
    """``esc_cancel_enabled=False`` must trigger ``unregister_esc``."""
    from voice_typer.server.service import LausuService

    svc = LausuService(fake_app)
    svc.apply_config_side_effects({"esc_cancel_enabled": False})
    fake_app.hotkeys.unregister_esc.assert_called()


def test_extraction_preserves_tray_invalidation(fake_app) -> None:
    """``tray_left_click_action`` change must invalidate tray menu cache."""
    from voice_typer.server.service import LausuService

    svc = LausuService(fake_app)
    svc.apply_config_side_effects({"tray_left_click_action": "open_settings"})
    fake_app.tray.invalidate_menu_cache.assert_called()


def test_extraction_preserves_notifications_toggle(fake_app) -> None:
    """``show_notifications=False`` must disable notifications."""
    from voice_typer.server.service import LausuService

    svc = LausuService(fake_app)
    svc.apply_config_side_effects({"show_notifications": False})
    fake_app.tray.set_notifications_enabled.assert_called_with(False)
