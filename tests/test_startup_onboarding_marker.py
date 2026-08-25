"""Regression tests for XA-11-2 — startup_sequence honors .onboarding_started.

``voice_typer/server/startup_sequence.py`` runs an "auto-heal" pass on every
startup when ``config.onboarding_completed`` is False. The auto-heal marks
onboarding complete to prevent the wizard from re-running on every restart
and clobbering the user's hotkey/model/microphone selections.

The original PVT-006 fix had a hole: if the user started the wizard
(writing ``config.json`` to disk) and then crashed mid-wizard before
flipping ``onboarding_completed`` to True, the auto-heal would silently
mark onboarding complete on next startup — the wizard would never resume,
and the user's partial onboarding state would be discarded.

The XA-11-2 fix adds a ``.onboarding_started`` marker check:
  - if ``config.json`` exists AND ``.onboarding_started`` does NOT exist
    → this is a *stale* onboarding state (the marker was lost/deleted) →
    auto-heal is correct.
  - if ``config.json`` exists AND ``.onboarding_started`` DOES exist
    → the user is *mid-wizard* → do NOT auto-heal; preserve the wizard
      state so it can resume on next startup.

These tests pin both branches so a future refactor cannot silently
reintroduce the wizard-clobbering auto-heal.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_for_onboarding(tmp_path, monkeypatch):
    """Build a minimal VoiceTyperApp suitable for exercising the
    StartupSequence onboarding-auto-heal branch.

    Mirrors the ``app_for_startup`` fixture in
    ``tests/test_startup_sequence.py`` but is intentionally lighter — we
    only need the onboarding branch to execute, not the full boot
    sequence (autostart, mic enumeration, hotkey, model load). All
    downstream startup phases are short-circuited via
    ``app._shutting_down = True`` immediately after the onboarding block.
    """
    # Patch BOTH the canonical config._config_dir attribute AND the
    # already-imported reference in startup_sequence. The function is
    # ``functools.lru_cache``-wrapped, so monkeypatching the attribute on
    # ``config`` alone is NOT enough — startup_sequence._config_dir is a
    # separate bound reference. Also clear the cache on the original
    # function so any prior resolution is forgotten.
    from voice_typer.server import config as _config_mod, startup_sequence as _startup_seq

    _config_mod._config_dir.cache_clear()
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: tmp_path)
    monkeypatch.setattr(_startup_seq, "_config_dir", lambda: tmp_path)

    monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
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
        "voice_typer.server.startup_sequence.configure_corrections",
        lambda config_dir: None,
    )


def _stub_startup_tasks(monkeypatch) -> None:
    """Stub the heavy startup_tasks helpers so we don't actually register
    hotkeys or load models during these focused tests.

    These functions are imported lazily inside ``StartupSequence.run``;
    patching them on ``startup_tasks`` directly is enough.
    """
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
        """If ``config.json`` exists but ``.onboarding_started`` does NOT
        exist, the auto-heal must fire (mark onboarding complete + save).

        This is the "stale onboarding state" path — the marker was
        lost/deleted but the user has clearly been using the app (config
        exists), so we mark onboarding complete to prevent the wizard
        from showing on every restart and overwriting the user's settings.
        """
        _stub_configure_corrections(monkeypatch)
        _stub_startup_tasks(monkeypatch)

        # Simulate the stale state: config.json exists, no .onboarding_started.
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        assert not (tmp_path / ".onboarding_started").exists()

        # Short-circuit downstream startup phases — we only care about
        # the onboarding branch.
        # (original_run removed — the monkeypatch fixture auto-undoes
        # patches at test teardown, so an explicit restore isn't needed.)

        # Set _shutting_down AFTER the onboarding block runs. Easiest way
        # is to set it before _do_startup; the onboarding block runs
        # before the _shutting_down check (see startup_sequence.py:129+).
        # We use a side-effect monkeypatch on app.config.save to flip
        # _shutting_down True after the onboarding save completes.
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
        """If ``config.json`` exists AND ``.onboarding_started`` exists,
        the auto-heal must NOT fire — the user is mid-wizard and the
        wizard state must be preserved so it can resume on next startup.

        This is the XA-11-2 fix: previously, the auto-heal fired whenever
        ``config.json`` existed, silently clobbering a mid-wizard crash.
        """
        _stub_configure_corrections(monkeypatch)
        _stub_startup_tasks(monkeypatch)

        # Simulate the mid-wizard state: config.json exists AND the
        # status document records started=True.
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        from voice_typer.server import onboarding_status

        onboarding_status.write_status(tmp_path, started=True)

        # Set _shutting_down after the onboarding branch so the rest of
        # startup is short-circuited (we only care about the onboarding
        # branch here).
        original_save = app_for_onboarding.config.save

        def _save_then_signal_shutdown():
            original_save()
            app_for_onboarding._shutting_down = True

        app_for_onboarding.config.save = _save_then_signal_shutdown  # type: ignore[assignment]

        with caplog.at_level(logging.INFO, logger="voice_typer.server.startup_sequence"):
            app_for_onboarding._do_startup()

        # The auto-heal must NOT have fired: onboarding_completed is still False.
        assert app_for_onboarding.config.onboarding_completed is False, (
            "Mid-wizard state (.onboarding_started exists) must NOT auto-heal — "
            "the wizard state must be preserved so it can resume on next startup"
        )
