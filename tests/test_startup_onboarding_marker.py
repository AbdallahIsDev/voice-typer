"""Regression tests for XA-11-2: startup_sequence honors .onboarding_started."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_for_onboarding(tmp_path, monkeypatch):
    """StartupSequence onboarding-auto-heal branch."""
    # early-phases submodule (the OWNING submodule per C-ARCH-2 —
    from voice_typer.server import config as _config_mod
    from voice_typer.server.startup_sequence import _phases_early as _startup_early

    _config_mod._reset_config_dir_cache()
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(_startup_early, "_config_dir", lambda: tmp_path)

    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    from voice_typer.server.app import LausuApp

    instance = LausuApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    # Force the onboarding branch to execute.
    instance.config.onboarding_completed = False
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    return instance


def _stub_configure_corrections(monkeypatch) -> None:
    """Stub configure_corrections so it doesn't try to read corrections.json."""
    monkeypatch.setattr(
        "voice_typer.server.startup_sequence._phases_early.configure_corrections",
        lambda config_dir: None,
    )


def _stub_startup_tasks(monkeypatch) -> None:
    """Stub the heavy startup_tasks helpers so we don't actually register"""
    monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", lambda app: None)
    monkeypatch.setattr(
        "voice_typer.server.startup_tasks.load_microphones",
        lambda app, shutdown_event=None: None,
    )
    monkeypatch.setattr(
        "voice_typer.server.startup_tasks.sync_prewarm_task",
        lambda app, shutdown_event=None: None,
    )
    monkeypatch.setattr(
        "voice_typer.server.startup_tasks.ensure_desktop_shortcut",
        lambda app: None,
    )
    monkeypatch.setattr(
        "voice_typer.server.startup_tasks.start_accessibility_pulse",
        lambda app, initial_state: None,
    )


class TestOnboardingStartedMarker:
    """XA-11-2: startup_sequence must honor the ``.onboarding_started`` marker."""

    def test_stale_state_auto_heals_when_config_exists_but_no_started_marker(
        self, app_for_onboarding, tmp_path, monkeypatch, caplog
    ):
        """If ``config.json`` exists but ``.onboarding_started`` does NOT"""
        _stub_configure_corrections(monkeypatch)
        _stub_startup_tasks(monkeypatch)

        # Simulate the stale state: config.json exists, no .onboarding_started.
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        assert not (tmp_path / ".onboarding_started").exists()

        # Short-circuit downstream startup phases, we only care about

        # Set _shutting_down AFTER the onboarding block runs. Easiest way
        original_save = app_for_onboarding.config.save

        def _save_then_signal_shutdown():
            original_save()
            app_for_onboarding._shutting_down = True

        app_for_onboarding.config.save = _save_then_signal_shutdown  # type: ignore[assignment]

        with caplog.at_level(logging.INFO, logger="voice_typer.server.startup_sequence"):
            app_for_onboarding._do_startup()

        # The auto-heal must have fired: onboarding_completed is now True.
        assert app_for_onboarding.config.onboarding_completed is True, (
            "Stale onboarding state (config.json exists, .onboarding_started "
            "missing) must auto-heal by marking onboarding_completed=True"
        )
        # The log message must mention the stale-state fix.
        assert any(
            "stale onboarding state" in r.getMessage().lower() or "fixing stale onboarding" in r.getMessage().lower()
            for r in caplog.records
        ), f"Stale onboarding auto-heal must log the rationale; got: {[r.getMessage() for r in caplog.records]}"

    def test_mid_wizard_state_preserved_when_started_marker_exists(
        self, app_for_onboarding, tmp_path, monkeypatch, caplog
    ):
        """If ``config.json`` exists AND ``.onboarding_started`` exists,"""
        _stub_configure_corrections(monkeypatch)
        _stub_startup_tasks(monkeypatch)

        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        from voice_typer.server import onboarding_status

        onboarding_status.write_status(tmp_path, started=True)

        original_save = app_for_onboarding.config.save

        def _save_then_signal_shutdown():
            original_save()
            app_for_onboarding._shutting_down = True

        app_for_onboarding.config.save = _save_then_signal_shutdown  # type: ignore[assignment]

        with caplog.at_level(logging.INFO, logger="voice_typer.server.startup_sequence"):
            app_for_onboarding._do_startup()

        # The auto-heal must NOT have fired: onboarding_completed is still False.
        assert app_for_onboarding.config.onboarding_completed is False, (
            "Mid-wizard state (.onboarding_started exists) must NOT auto-heal, "
            "the wizard state must be preserved so it can resume on next startup"
        )
