"""E2E verification — exercises the 10 fixes together.

This is a sanity check that the fixes don't interfere with each other
and the core flows still work end-to-end.
"""

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

        # Write a default config (the case that previously triggered the bug)
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

    # (Wave 3, 2026-08-14): STARTUP-1 (``_build_task_xml``) and
    # STARTUP-2 (``_LOGON_DELAY``) tests were deleted — prewarm became
    # a worker startup phase (master plan §6.2 P-1), so the OS-level
    # scheduled-task XML builder (Windows Task Scheduler LogonTrigger)
    # and the logon-delay constant were removed from ``task_scheduler.py``
    # entirely. The worker exe now warms imports in its own startup
    # phase (no OS-level scheduling), so there is no task XML or
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
        from voice_typer.server.service import VoiceTyperService

        events = []
        monkeypatch.setattr(event_bus_mod, "publish", lambda msg: events.append(msg) or True)
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
        """T021: RecordingController.on_recorder_rms forwards audio_chunk to update_level.

        The RMS callback was moved from VoiceTyperApp._on_recorder_rms to
        RecordingController.on_recorder_rms as part of the RecordingController
        extraction (commit 9e53ffe). The forwarded audio_chunk is what
        WaveformBubble.update_level uses to run Silero VAD on the live stream.

        S2-CR-64: the previous test asserted
        ``"rms_callback(chunk_rms, chunk_peak, filtered)" in inspect.getsource(recording)``
        — but production code uses the 2-arg call ``rms_callback(chunk_rms, chunk_peak)``
        (per G4-L-04 comment); the 3-arg form appears only in a comment near the
        call site, giving false coverage. Replaced with a behavioral test that
        constructs a RecordingController, invokes ``on_recorder_rms`` with a
        sentinel chunk, and asserts the bubble's ``update_level`` received it
        by identity.
        """
        import inspect
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.waveform import WaveformBubble

        # Check signatures — these are stable shape assertions, not source text.
        app_sig = inspect.signature(RecordingController.on_recorder_rms)
        assert "audio_chunk" in app_sig.parameters
        bubble_sig = inspect.signature(WaveformBubble.update_level)
        assert "audio_chunk" in bubble_sig.parameters

        # Behavioral check: the controller must forward the exact chunk
        # object it received (identity, not string match) to the bubble's
        # update_level — proving the wiring is intact end-to-end.
        sentinel_chunk = object()
        mock_app = MagicMock()
        controller = RecordingController(mock_app)
        controller.on_recorder_rms(0.42, 0.7, audio_chunk=sentinel_chunk)
        mock_app._waveform_bubble.update_level.assert_called_once_with(0.42, 0.7, audio_chunk=sentinel_chunk)

    def test_asr_registry_create_handler_exists(self):
        """ARCH-007: AsrBackendRegistry.create() method exists."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        assert hasattr(AsrBackendRegistry, "create")
        assert callable(AsrBackendRegistry.create)

    def test_asr_registry_initialized_in_app_init(self, tmp_config_dir, monkeypatch):
        """ARCH-008: registry is set in VoiceTyperApp.__init__ (now via
        ModelManager._registry, accessed as app.models.registry)."""
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # registry now lives on ModelManager; the legacy
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


class TestBrandingConstants:
    """Smoke tests for ``voice_typer/server/branding.py``.

    branding.py is a tiny constants-only module (BRAND-001: single source
    of truth for the app name) — its coverage value is low on its own, so
    we fold the assertions into the existing e2e smoke file rather than
    maintaining a dedicated test module.
    """

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

        assert APP_NAME == "Voice Typer"

    def test_app_module_uses_branding_app_name(self, monkeypatch):
        """``voice_typer.server.app`` must source its APP_NAME from branding.

        This is the BRAND-001 consistency guarantee: a single rename in
        branding.py should propagate to every consumer, including the app
        module's startup banner / tray notifications.

        S2-CR-64: the previous test asserted the import line appeared
        verbatim in ``inspect.getsource(app)`` — brittle to cosmetic
        refactor (e.g. switching to ``from voice_typer.server import
        branding`` + ``branding.APP_NAME`` would break the test even
        though the invariant holds). Replaced with a behavioral check
        that mutates ``branding.APP_NAME`` to a sentinel value and
        verifies the app module observes the mutation when it
        re-resolves the attribute — proving app.APP_NAME is bound to
        branding's namespace, not a local literal.
        """
        from voice_typer.server import app, branding

        # The app module must expose an APP_NAME attribute that points to
        # the branding constant (i.e. it was imported, not redefined).
        assert hasattr(app, "APP_NAME"), "app module does not expose APP_NAME"
        assert app.APP_NAME is branding.APP_NAME, (
            "app.APP_NAME is not the branding.APP_NAME object — app module "
            "appears to have redefined the constant instead of importing it."
        )

        # Behavioral check: if app.APP_NAME is bound to branding.APP_NAME
        # via the ``from voice_typer.server.branding import APP_NAME`` form,
        # then ``app.APP_NAME`` is a *reference* taken at import time —
        # mutating ``branding.APP_NAME`` afterwards would NOT propagate to
        # ``app.APP_NAME``. To distinguish a fresh ``branding.APP_NAME``
        # lookup (the desired pattern after migration) from a stale import
        # binding, we reload the app module after mutating branding and
        # verify the reload picks up the new value.
        import importlib

        sentinel = "VT-BRAND-SENTINEL-9f3a"
        original = branding.APP_NAME
        monkeypatch.setattr(branding, "APP_NAME", sentinel)
        try:
            # A module reload re-executes the ``from ... import APP_NAME``
            # line, so app.APP_NAME should now point at the *new*
            # branding.APP_NAME sentinel. If the app module instead
            # redefined APP_NAME as a local literal, the reload would
            # re-bind it to the same literal — not the sentinel.
            importlib.reload(app)
            assert app.APP_NAME is sentinel or sentinel == app.APP_NAME, (
                "After reloading app with branding.APP_NAME mutated, "
                "app.APP_NAME did not pick up the sentinel value — the app "
                "module appears to redefine APP_NAME locally instead of "
                "importing from branding (BRAND-001 invariant broken)."
            )
        finally:
            # Restore branding.APP_NAME and reload app so other tests see
            # the original constant.
            monkeypatch.setattr(branding, "APP_NAME", original)
            importlib.reload(app)
