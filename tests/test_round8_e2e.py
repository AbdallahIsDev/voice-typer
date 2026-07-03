"""Round 8 E2E verification — exercises the 10 fixes together.

This is a sanity check that the fixes don't interfere with each other
and the core flows still work end-to-end.
"""
import sys
import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/home/z/my-project/voice-typer-repo')


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point voice_typer config to a temp dir."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestRound8EndToEndSmoke:
    """End-to-end smoke tests for Round 8's 10 fixes."""

    def test_startup6_no_spurious_warning(self, temp_config, caplog):
        """STARTUP-6: loading default config does NOT log a spurious warning."""
        import logging
        from voice_typer.server.config import Config
        # Write a default config (the case that previously triggered the bug)
        (temp_config / "config.json").write_text(json.dumps({
            "volume_duck_smart_poll_interval_ms": 500,
        }))
        with caplog.at_level(logging.WARNING):
            cfg = Config.load()
        assert cfg.volume_duck_smart_poll_interval_ms == 500
        assert not any(
            "volume_duck_smart_poll_interval_ms" in r.message
            and "invalid value" in r.message
            for r in caplog.records
        )

    def test_startup1_task_xml_uses_pythonw_directly(self):
        """STARTUP-1: Task Scheduler XML uses pythonw.exe, not cmd.exe /c."""
        from voice_typer.server import task_scheduler
        xml = task_scheduler._build_task_xml('C:\\path\\pythonw.exe')
        assert "cmd.exe" not in xml
        assert "C:\\path\\pythonw.exe" in xml
        assert "-m voice_typer.server.prewarm" in xml

    def test_startup2_logon_delay_is_zero(self):
        """STARTUP-2: prewarm fires at logon+0 (was PT45S)."""
        from voice_typer.server import task_scheduler
        assert task_scheduler._LOGON_DELAY == "PT0S"

    def test_startup4_prewarm_filters_to_active_model(self, temp_config, monkeypatch):
        """STARTUP-4: prewarm only warms active model + tiny.en fallback."""
        from voice_typer.server import prewarm
        # Set up HF cache with multiple model dirs
        hf_cache = temp_config / "huggingface" / "hub"
        hf_cache.mkdir(parents=True)
        (hf_cache / "models--nvidia--parakeet-tdt-0.6b-v3" / "snapshots" / "abc").mkdir(parents=True)
        (hf_cache / "models--Systran--faster-whisper-tiny.en" / "snapshots" / "def").mkdir(parents=True)
        # Inactive Whisper variants
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
        assert "models--Systran--faster-whisper-tiny.en" in names
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
        from voice_typer.server.service import VoiceTyperService
        import voice_typer.server.ipc_server as ipc_mod
        events = []
        monkeypatch.setattr(ipc_mod, "_push_event_now", lambda msg: events.append(msg) or True)
        # Mock Qwen with existing path → success path
        app = MagicMock()
        app.config.qwen_model_path = str(temp_config)
        os.makedirs(temp_config, exist_ok=True)
        service = VoiceTyperService(app)
        result = service.download_model("qwen")
        assert result["success"] is True
        progress_events = [e for e in events if e.get("type") == "download_progress"]
        assert any(e["data"]["progress"] == 100 for e in progress_events)

    def test_recorder_rms_forwards_audio_chunk_to_waveform(self):
        """T021: app._on_recorder_rms forwards audio_chunk to update_level."""
        import inspect
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.waveform import WaveformBubble
        # Check signatures
        app_sig = inspect.signature(VoiceTyperApp._on_recorder_rms)
        assert "audio_chunk" in app_sig.parameters
        bubble_sig = inspect.signature(WaveformBubble.update_level)
        assert "audio_chunk" in bubble_sig.parameters
        # Check the recorder forwards 3 args
        from voice_typer.server import recording
        rec_src = inspect.getsource(recording)
        assert "rms_callback(chunk_rms, chunk_peak, filtered)" in rec_src

    def test_asr_registry_create_handler_exists(self):
        """ARCH-007: AsrBackendRegistry.create() method exists."""
        from voice_typer.server.asr_registry import AsrBackendRegistry
        assert hasattr(AsrBackendRegistry, "create")
        assert callable(AsrBackendRegistry.create)

    def test_asr_registry_initialized_in_app_init(self, tmp_path, monkeypatch):
        """ARCH-008: registry is set in VoiceTyperApp.__init__ (now via
        ModelManager._registry, accessed as app.models.registry)."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])
        from voice_typer.server.app import VoiceTyperApp
        app = VoiceTyperApp()
        # ARCH-REFAC-003: registry now lives on ModelManager; the legacy
        # app._asr_registry @property delegate was removed.
        assert app.models._registry is not None
        assert app.models.registry is not None

    def test_issue13_tray_menu_module_exists(self):
        """#13: tray_menu module extracted with build_menu, display_hotkey, wrap_callback."""
        from voice_typer.server import tray_menu
        assert hasattr(tray_menu, "build_menu")
        assert hasattr(tray_menu, "display_hotkey")
        assert hasattr(tray_menu, "wrap_callback")
        # tray.py should delegate to tray_menu
        from voice_typer.server import tray
        assert tray.build_menu is tray_menu.build_menu
        assert tray.display_hotkey is tray_menu.display_hotkey
        assert tray.wrap_callback is tray_menu.wrap_callback

    def test_startup2_autostart_launcher_parses_delay(self):
        """STARTUP-2: autostart_launcher --delay flag is parsed correctly."""
        from voice_typer.server.autostart_launcher import _parse_delay
        assert _parse_delay([]) == 0.0
        assert _parse_delay(["--delay", "30"]) == 30.0
        assert _parse_delay(["--delay=45"]) == 45.0
        assert _parse_delay(["--delay", "abc"]) == 0.0  # invalid → 0
