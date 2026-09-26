"""E2E verification: exercises the 10 fixes together."""

import json
import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def temp_config(tmp_config_dir):
    """Point voice_typer config to a temp dir."""
    return tmp_config_dir


class TestEndToEndSmoke:
    """End-to-end smoke tests."""

    def test_startup6_no_spurious_warning(self, temp_config, caplog):
        """STARTUP-6: loading default config does NOT log a spurious warning."""
        import logging

        from voice_typer.server.config import Config

        (temp_config / "config.json").write_text(
            json.dumps(
                {
                    "volume_duck_smart_poll_interval_ms": 500,
                }
            )
        )
        with caplog.at_level(logging.WARNING):
            cfg = Config.load()
        assert cfg.volume_duck_smart_poll_interval_ms == 500
        assert not any(
            "volume_duck_smart_poll_interval_ms" in r.message and "invalid value" in r.message for r in caplog.records
        )

    # logon delay to pin.

    def test_startup4_prewarm_filters_to_active_model(self, temp_config, monkeypatch):
        """STARTUP-4: prewarm only warms active model + tiny fallback."""
        from voice_typer.server import prewarm

        # Set up HF cache with multiple model dirs
        hf_cache = temp_config / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)
        (hf_cache / "models--nvidia--parakeet-tdt-0.6b-v3" / "snapshots" / "abc").mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-tiny" / "snapshots" / "def").mkdir(parents=True)
        # Inactive Whisper variants (removed from the catalog)
        (hf_cache / "models--Systran--faster-whisper-small.en" / "snapshots" / "ghi").mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-medium.en" / "snapshots" / "jkl").mkdir(parents=True)
        fake_cfg = MagicMock(asr_backend="parakeet", model_size="small.en")
        monkeypatch.setattr(
            "voice_typer.server.config.Config.load",
            classmethod(lambda cls: fake_cfg),
        )
        dirs = prewarm._active_model_cache_dirs()
        names = [d.name for d in dirs]
        assert "models--nvidia--parakeet-tdt-0.6b-v3" in names
        assert "models--Systran--faster-whisper-tiny" in names
        assert "models--Systran--faster-whisper-small.en" not in names
        assert "models--Systran--faster-whisper-medium.en" not in names

    def test_issue8_onboarding_wizard_first_run_detection(self, temp_config):
        """#8: OnboardingController.is_first_run detects wizard-should-show state."""
        from voice_typer.server.onboarding import OnboardingController

        # No config.json, no marker → first run
        ctrl = OnboardingController(config_dir=temp_config)
        assert ctrl.is_first_run() is True
        # After mark_complete → not first run
        ctrl.mark_complete()
        ctrl2 = OnboardingController(config_dir=temp_config)
        assert ctrl2.is_first_run() is False

    def test_download_model_pushes_progress_events(self, temp_config, monkeypatch):
        """UX-005: download_model pushes progress events via IPC."""
        import voice_typer.server.event_bus as event_bus_mod
        from voice_typer.server.service import LausuService

        events = []
        monkeypatch.setattr(event_bus_mod, "publish", lambda msg: events.append(msg) or True)
        # Mock Qwen with existing path → success path
        app = MagicMock()
        app.config.qwen_model_path = str(temp_config)
        os.makedirs(temp_config, exist_ok=True)
        service = LausuService(app)
        result = service.download_model("qwen")
        assert result["success"] is True
        progress_events = [e for e in events if e.get("type") == "download_progress"]
        assert any(e["data"]["progress"] == 100 for e in progress_events)

    def test_recorder_rms_forwards_to_waveform(self):
        """RecordingController.on_recorder_rms forwards (rms, peak) to update_level."""
        import inspect
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.waveform import WaveformBubble

        # Check signatures, these are stable shape assertions, not source text.
        app_sig = inspect.signature(RecordingController.on_recorder_rms)
        assert "audio_chunk" not in app_sig.parameters
        bubble_sig = inspect.signature(WaveformBubble.update_level)
        assert "audio_chunk" not in bubble_sig.parameters

        # Behavioral check: the controller must forward the exact values
        mock_app = MagicMock()
        controller = RecordingController(mock_app)
        controller.on_recorder_rms(0.42, 0.7)
        mock_app._waveform_bubble.update_level.assert_called_once_with(0.42, 0.7)

    def test_asr_registry_create_handler_exists(self):
        """ARCH-007: AsrBackendRegistry.create() method exists."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        assert hasattr(AsrBackendRegistry, "create")
        assert callable(AsrBackendRegistry.create)

    def test_asr_registry_initialized_in_app_init(self, tmp_config_dir, monkeypatch):
        """ARCH-008: registry is set in LausuApp.__init__ (now via"""
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
        from voice_typer.server.app import LausuApp

        app = LausuApp()
        assert app.models._registry is not None
        assert app.models.registry is not None

    def test_issue13_tray_menu_module_exists(self):
        """#13: tray_menu module extracted with build_menu_for_tray, display_hotkey, wrap_callback."""
        from voice_typer.server import tray_menu

        assert hasattr(tray_menu, "build_menu_for_tray")
        assert hasattr(tray_menu, "display_hotkey")
        assert hasattr(tray_menu, "wrap_callback")
        from voice_typer.server import tray

        assert tray.display_hotkey is tray_menu.display_hotkey
        assert tray.wrap_callback is tray_menu.wrap_callback

    def test_startup2_autostart_launcher_parses_delay(self):
        """STARTUP-2: autostart_launcher --delay flag is parsed correctly."""
        from voice_typer.server.autostart_launcher import _parse_delay

        assert _parse_delay([]) == 0.0
        assert _parse_delay(["--delay", "30"]) == 30.0
        assert _parse_delay(["--delay=45"]) == 45.0
        assert _parse_delay(["--delay", "abc"]) == 0.0  # invalid → 0


class TestBrandingConstants:
    """Smoke tests for ``voice_typer/server/branding.py``."""

    def test_branding_module_is_importable_in_isolation(self):
        """The branding module must be importable on its own (no heavy deps)."""
        import importlib

        mod = importlib.import_module("voice_typer.server.branding")
        assert mod is not None
        # Re-import should be idempotent and return the cached module.
        again = importlib.import_module("voice_typer.server.branding")
        assert again is mod

    def test_expected_branding_constants_exist(self):
        """All four branding constants are present on the module."""
        from voice_typer.server import branding

        for name in ("APP_NAME", "APP_DESCRIPTION", "APP_URL", "APP_REPO"):
            assert hasattr(branding, name), f"branding.{name} is missing"

    def test_branding_constants_are_non_empty_strings(self):
        """Branding constants must be non-empty ``str`` values (not bytes/None)."""
        from voice_typer.server import branding

        for name in ("APP_NAME", "APP_DESCRIPTION", "APP_URL", "APP_REPO"):
            value = getattr(branding, name)
            assert isinstance(value, str), f"branding.{name} must be str, got {type(value).__name__}"
            assert not isinstance(value, bytes | bytearray), f"branding.{name} must not be bytes/bytearray"
            assert value, f"branding.{name} must be a non-empty string"

    def test_app_name_matches_known_product(self):
        """APP_NAME is the documented product name (guards against silent renames)."""
        from voice_typer.server.branding import APP_NAME

        assert APP_NAME == "Lausu"

    def test_app_module_uses_branding_app_name(self, monkeypatch):
        """``voice_typer.server.app`` must source its APP_NAME from branding."""
        from voice_typer.server import app, branding

        assert hasattr(app, "APP_NAME"), "app module does not expose APP_NAME"
        assert app.APP_NAME is branding.APP_NAME, (
            "app.APP_NAME is not the branding.APP_NAME object, app module "
            "appears to have redefined the constant instead of importing it."
        )

        # Behavioral check: if app.APP_NAME is bound to branding.APP_NAME
        import importlib

        sentinel = "VT-BRAND-SENTINEL-9f3a"
        original = branding.APP_NAME
        monkeypatch.setattr(branding, "APP_NAME", sentinel)
        try:
            # A module reload re-executes the ``from ... import APP_NAME``
            importlib.reload(app)
            assert app.APP_NAME is sentinel or sentinel == app.APP_NAME, (
                "After reloading app with branding.APP_NAME mutated, "
                "app.APP_NAME did not pick up the sentinel value, the app "
                "module appears to redefine APP_NAME locally instead of "
                "importing from branding (BRAND-001 invariant broken)."
            )
        finally:
            # Restore branding.APP_NAME and reload app so other tests see
            monkeypatch.setattr(branding, "APP_NAME", original)
            importlib.reload(app)
