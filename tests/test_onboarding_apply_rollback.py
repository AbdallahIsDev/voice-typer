"""HU-23 regression — ``OnboardingMixin.onboarding_apply`` rollback.

Finding HU-23 (review.md): ``onboarding_apply`` called
``ctrl.apply_settings(app.config)`` which mutates the in-memory
Config dataclass in place (sets hotkey / model_size / microphone /
onboarding_completed). If ``apply_config_side_effects`` (e.g. hotkey
backend re-registration) or ``config.save()`` raised, the exception
propagated and the in-memory config kept the new values while the
on-disk config kept the pre-onboarding values — the user's choices
appeared applied but vanished on restart.

The fix snapshots the four mutated fields before the apply and
restores them in the except handler (write-then-swap semantics: a
failed apply must leave in-memory state identical to on-disk state).
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock


def _build_onboarding_service():
    """Build a minimal VoiceTyperService (via ``__new__``, no heavy
    init) wired to a real Config + real mutation lock.

    Mirrors the pattern in ``tests/test_onboarding_permissions.py``:
    the service mixin only needs ``_app`` (with ``config`` and
    ``_config_mutation_lock``) and the ``_onboarding`` controller.
    """
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    app = MagicMock()
    cfg = Config()
    app.config = cfg
    app._config_mutation_lock = threading.Lock()
    app.tray.invalidate_menu_cache = MagicMock()
    app.change_model = MagicMock()

    service = VoiceTyperService.__new__(VoiceTyperService)
    service._app = app  # type: ignore[attr-defined]
    return service, cfg


class _WizardCtrl:
    """Minimal stand-in for OnboardingController: holds the selected_*
    set and mutates the config in place in ``apply_settings`` (mirroring
    the real controller)."""

    def __init__(self, hotkey: str, model: str, mic: str):
        self.selected_hotkey = hotkey
        self.selected_model = model
        self.selected_microphone = mic

    def apply_settings(self, config) -> None:
        config.hotkey = self.selected_hotkey
        config.model_size = self.selected_model
        config.microphone = self.selected_microphone


def _assert_rolled_back(cfg, pre: dict) -> None:
    assert cfg.hotkey == pre["hotkey"]
    assert cfg.model_size == pre["model_size"]
    assert cfg.microphone == pre["microphone"]
    assert cfg.onboarding_completed == pre["onboarding_completed"]


def test_onboarding_apply_rolls_back_config_on_side_effect_failure(monkeypatch) -> None:
    """HU-23: when ``apply_config_side_effects`` raises (e.g. hotkey
    backend re-registration fails), the four mutated config fields must
    be restored to their pre-apply values."""
    from voice_typer.server import event_bus

    service, cfg = _build_onboarding_service()

    pre = {
        "hotkey": cfg.hotkey,
        "model_size": cfg.model_size,
        "microphone": cfg.microphone,
        "onboarding_completed": cfg.onboarding_completed,
    }
    service._onboarding = _WizardCtrl(  # type: ignore[attr-defined]
        hotkey="<f9>",
        model=cfg.model_size,  # same → change_model is skipped
        mic="mic-42",
    )

    def _boom(_updates):
        raise RuntimeError("hotkey re-registration failed")

    monkeypatch.setattr(service, "apply_config_side_effects", _boom)
    monkeypatch.setattr(event_bus, "publish", lambda *a, **k: None)

    result = service.onboarding_apply()

    assert "error" in result, "onboarding_apply must surface the failure"
    _assert_rolled_back(cfg, pre)


def test_onboarding_apply_rolls_back_config_on_save_failure(monkeypatch) -> None:
    """HU-23: when ``config.save()`` raises (disk full / permissions),
    the mutated fields must be restored too — the except handler is
    shared with the side-effect failure path."""
    from voice_typer.server import event_bus

    service, cfg = _build_onboarding_service()

    pre = {
        "hotkey": cfg.hotkey,
        "model_size": cfg.model_size,
        "microphone": cfg.microphone,
        "onboarding_completed": cfg.onboarding_completed,
    }
    service._onboarding = _WizardCtrl(  # type: ignore[attr-defined]
        hotkey="<f9>",
        model=cfg.model_size,
        mic="mic-42",
    )
    monkeypatch.setattr(
        service,
        "apply_config_side_effects",
        lambda updates: {"autostart_status": None, "prewarm_status": None},
    )
    monkeypatch.setattr(event_bus, "publish", lambda *a, **k: None)

    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(cfg, "save", _boom)

    result = service.onboarding_apply()

    assert "error" in result, "onboarding_apply must surface the failure"
    _assert_rolled_back(cfg, pre)


def test_onboarding_apply_success_persists_choices(monkeypatch) -> None:
    """Sanity: on success the config keeps the wizard's choices (the
    rollback must NOT fire)."""
    from voice_typer.server import event_bus

    service, cfg = _build_onboarding_service()

    service._onboarding = _WizardCtrl(  # type: ignore[attr-defined]
        hotkey="<f9>",
        model=cfg.model_size,
        mic="mic-42",
    )
    monkeypatch.setattr(
        service,
        "apply_config_side_effects",
        lambda updates: {"autostart_status": None, "prewarm_status": None},
    )
    monkeypatch.setattr(event_bus, "publish", lambda *a, **k: None)

    result = service.onboarding_apply()

    assert result == {"ok": True}
    assert cfg.hotkey == "<f9>"
    assert cfg.microphone == "mic-42"
    assert cfg.onboarding_completed is True
