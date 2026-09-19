"""Tests for voice_typer.onboarding: OnboardingController wizard."""

import json
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE
from voice_typer.server.onboarding_status import read_status


@pytest.fixture
def onboarding_dir(tmp_config_dir):
    """Point config to a temp directory."""
    return tmp_config_dir


@pytest.fixture
def ctrl(onboarding_dir):
    """Create an OnboardingController with temp dir."""
    from voice_typer.server.onboarding import OnboardingController

    return OnboardingController(config_dir=onboarding_dir)


class TestOnboardingFirstRun:
    def test_is_first_run_no_config(self, ctrl):
        assert ctrl.is_first_run() is True

    def test_not_first_run_with_config(self, onboarding_dir):
        """#8: A config.json with onboarding_completed=True is NOT first run."""
        (onboarding_dir / "config.json").write_text(json.dumps({"onboarding_completed": True}), encoding="utf-8")
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is False

    def test_first_run_when_config_has_onboarding_false(self, onboarding_dir):
        """#8: config.json exists but onboarding_completed=False → first run."""
        (onboarding_dir / "config.json").write_text(json.dumps({"onboarding_completed": False}), encoding="utf-8")
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True

    def test_not_first_run_after_mark_complete(self, ctrl):
        ctrl.mark_complete()
        assert ctrl.is_first_run() is False


class TestOnboardingSteps:
    def test_initial_step(self, ctrl):
        # 4-step essentials flow (2026-09-14): Welcome → Consent →
        assert ctrl.current_step == 0
        assert ctrl.total_steps == 4

    def test_step_names(self, ctrl):
        # 4-step layout: Welcome(0), Consent(1), Model(2), Hotkey(3).
        names = [
            "Welcome",
            "Consent",
            "Model",
            "Hotkey",
        ]
        for i, name in enumerate(names):
            ctrl._current_step = i
            assert ctrl.step_name == name

    def test_next_step(self, ctrl):
        assert ctrl.next_step() == 1
        assert ctrl.current_step == 1

    def test_next_step_capped(self, ctrl):
        ctrl._current_step = 3  # last step index (4 total)
        assert ctrl.next_step() == 3  # Already at last step

    def test_prev_step(self, ctrl):
        ctrl._current_step = 2
        assert ctrl.prev_step() == 1

    def test_prev_step_capped(self, ctrl):
        ctrl._current_step = 0
        assert ctrl.prev_step() == 0

    def test_step_change_advances_step(self, ctrl):
        """callbacks were removed; verify step still advances."""
        assert ctrl.next_step() == 1
        assert ctrl._current_step == 1

    def test_next_step_does_not_mark_complete(self, ctrl):
        """reaching the last step via next_step() must NOT mark"""
        ctrl._current_step = 2  # second-to-last step (Model)
        ctrl.next_step()  # advances to step 3 (Hotkey, final)
        assert ctrl.current_step == 3
        # Wizard is NOT complete, apply_settings or skip is required.
        assert ctrl.is_first_run() is True


class TestOnboardingSkip:
    def test_skip_marks_complete(self, ctrl):
        """callbacks were removed; verify skip still"""
        ctrl.skip()
        assert ctrl.is_first_run() is False


class TestOnboardingSelections:
    def test_set_microphone(self, ctrl):
        ctrl.set_microphone("mic-1")
        assert ctrl.selected_microphone == "mic-1"

    def test_set_microphone_none(self, ctrl):
        ctrl.set_microphone(None)
        assert ctrl.selected_microphone is None

    def test_set_hotkey(self, ctrl):
        ctrl.set_hotkey("<f4>")
        assert ctrl.selected_hotkey == "<f4>"

    def test_hotkey_presets(self, ctrl):
        assert len(ctrl.HOTKEY_PRESETS) == 12  # Caps Lock + F2-F12
        assert ctrl.HOTKEY_PRESETS[0] == "<caps_lock>"  # first = recommended default

    def test_set_model(self, ctrl):
        ctrl.set_model("tiny")
        assert ctrl.selected_model == "tiny"

    def test_model_options(self, ctrl):
        # Catalog pruned 2026-08-15 to the kept Whisper variants
        assert len(ctrl.MODEL_OPTIONS) == 4


class TestOnboardingApplySettings:
    def test_apply_settings(self, ctrl, onboarding_dir):
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "tiny"

            def save(self):
                pass

        config = MockConfig()
        ctrl.apply_settings(config)
        assert config.microphone == "mic-1"
        assert config.hotkey == "<f4>"
        assert config.model_size == "tiny"

    def test_apply_settings_no_mic(self, ctrl):
        ctrl.set_microphone(None)
        ctrl.set_hotkey("<f2>")
        ctrl.set_model("small.en")

        class MockConfig:
            microphone = "old-mic"
            hotkey = "<f2>"
            model_size = "tiny"

            def save(self):
                pass

        config = MockConfig()
        ctrl.apply_settings(config)
        # Should not overwrite when None
        assert config.microphone == "old-mic"


class TestOnboardingWizard:
    """#8: End-to-end test of the wizard flow through the service layer."""

    def test_full_wizard_flow(self, onboarding_dir):
        """Simulate the React wizard's IPC call sequence."""
        from voice_typer.server.onboarding import OnboardingController

        # 1) Backend: first-run detection
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True, "Wizard should appear when onboarding_completed is False"

        # 2) Wizard starts (4-step essentials flow: Welcome → Consent
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.current_step == 0
        assert ctrl.total_steps == 4

        # 3) Step 2: grant consents (persisted by the renderer via
        ctrl.next_step()  # advance to step 1 (Consent)
        assert ctrl.step_name == "Consent"

        # 4) Step 3: select model
        ctrl.next_step()
        ctrl.set_model("tiny")
        assert ctrl.selected_model == "tiny"

        # 5) Step 4: select hotkey (final step; its Continue applies)
        ctrl.next_step()
        ctrl.set_hotkey("<f4>")
        assert ctrl.selected_hotkey == "<f4>"

        # 6) Apply settings to a mock config (mirrors service.onboarding_apply).
        from voice_typer.server.config import Config

        cfg = Config()
        cfg.microphone = None
        cfg.hotkey = "<f2>"
        cfg.model_size = "tiny"
        ctrl.apply_settings(cfg)
        ctrl.mark_complete()
        cfg.onboarding_completed = True
        cfg.save()

        # 8) Verify the wizard won't reappear
        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False, "Wizard should NOT reappear after apply_settings + mark_complete"

        # 9) Verify the user's choices were persisted. The Microphone
        cfg2 = Config.load()
        assert cfg2.microphone is None
        assert cfg2.hotkey == "<f4>"
        assert cfg2.model_size == "tiny"
        assert cfg2.onboarding_completed is True

    def test_skip_flow(self, onboarding_dir):
        """Skip path: user clicks 'Skip' on step 0, defaults are kept."""
        from voice_typer.server.config import Config
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True

        # Skip immediately
        ctrl.skip()
        ctrl.mark_complete()

        # Wizard won't reappear
        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False

        # Config retains defaults (wizard was skipped before any set_* call)
        cfg = Config.load()
        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()
        # Since the 2026-08-28 no-default-model change there is NO
        assert cfg.model_size == DEFAULT_MODEL_SIZE  # default


# 17-H-: service-layer onboarding_apply side effects ────────────


@pytest.fixture
def app_with_service(tmp_config_dir, monkeypatch):
    """Build a real VoiceTyperApp + VoiceTyperService with mocked deps."""
    # Mock heavy hardware/GUI deps (in addition to conftest's autouse
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    monkeypatch.setitem(sys.modules, "faster_whisper", MagicMock())
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    monkeypatch.setitem(sys.modules, "pynput", MagicMock())
    monkeypatch.setitem(sys.modules, "pynput.keyboard", MagicMock())
    monkeypatch.setitem(sys.modules, "pystray", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
    monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

    # Prevent the app's atexit handler from polluting test output.
    monkeypatch.setattr("atexit.register", lambda *a, **kw: None)

    # Stub autostart helpers so __init__ doesn't touch the OS.
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])

    # Force PynputHotkey backend so tests can assert hotkey_str
    from voice_typer.server.hotkeys import PynputHotkey

    def _force_pynput(hotkey_str, **kwargs):
        return PynputHotkey(hotkey_str)

    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        _force_pynput,
    )

    from voice_typer.server.app import VoiceTyperApp
    from voice_typer.server.service import VoiceTyperService

    app = VoiceTyperApp()
    # Deterministic test behavior: no ESC hotkey, opt into voice consent.
    app.config.esc_cancel_enabled = False
    app.config.voice_biometric_consent = True
    # Mock the transcriber so ModelManager doesn't try to load a real model.
    app.models.transcriber = MagicMock()
    app.models.transcriber.is_loaded = True

    service = VoiceTyperService(app)
    return app, service


@pytest.fixture
def captured_events(monkeypatch):
    """Capture all events pushed via event_bus.publish."""
    events: list[dict] = []
    import voice_typer.server.event_bus as event_bus_mod

    monkeypatch.setattr(event_bus_mod, "publish", lambda msg: events.append(msg) or True)
    return events


class TestOnboardingApplySideEffects:
    """push a config_changed event so the user's wizard choices take"""

    def test_hotkey_re_registered_without_restart(self, app_with_service, captured_events):
        """The dictation hotkey backend reflects the wizard's choice"""
        app, service = app_with_service

        # Wizard flow: start, pick a non-default hotkey, apply.
        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        result = service.onboarding_apply()

        assert result == {"ok": True}, f"onboarding_apply failed: {result}"

        # The hotkey dispatcher should have a live backend whose
        backend = app.hotkeys._hotkey_backend
        assert backend is not None, (
            "Hotkey backend was not registered by onboarding_apply, apply_config_side_effects was not invoked"
        )
        assert backend.hotkey_str == "<f6>", (
            f"Expected hotkey_str='<f6>' after onboarding_apply, got {backend.hotkey_str!r}"
        )

    def test_config_changed_event_pushed(self, app_with_service, captured_events):
        """renderer can refresh UI-local state without a bespoke"""
        app, service = app_with_service

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_apply()

        config_events = [e for e in captured_events if e.get("type") == "config_changed"]
        assert len(config_events) >= 1, f"Expected at least one config_changed event, got: {captured_events}"
        data = config_events[-1].get("data", {})
        assert data.get("hotkey") == "<f6>", f"config_changed event data should include hotkey='<f6>', got: {data}"
        assert "model_size" in data, "config_changed event data should include model_size"

    def test_onboarding_completed_persisted(self, app_with_service, captured_events):
        """The existing onboarding_completed=True + config.save()"""
        app, service = app_with_service

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_apply()

        assert app.config.onboarding_completed is True
        assert app.config.hotkey == "<f6>"

    def test_model_change_invoked_when_model_differs(self, app_with_service, captured_events, monkeypatch):
        """When the user picks a non-default model, onboarding_apply"""
        app, service = app_with_service

        # Spy on change_model, don't actually run the unload/load
        change_model_calls: list[str] = []
        monkeypatch.setattr(
            app.models,
            "change_model",
            lambda model_size: change_model_calls.append(model_size),
        )

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_set_model("large-v3-turbo")  # non-default
        service.onboarding_apply()

        assert change_model_calls == ["large-v3-turbo"], (
            f"Expected change_model('large-v3-turbo'), got: {change_model_calls}"
        )

    def test_model_change_skipped_when_model_unchanged(self, app_with_service, captured_events, monkeypatch):
        """When the user keeps the default model, onboarding_apply"""
        app, service = app_with_service

        change_model_calls: list[str] = []
        monkeypatch.setattr(
            app.models,
            "change_model",
            lambda model_size: change_model_calls.append(model_size),
        )

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        # Don't call onboarding_set_model, OnboardingController's
        service.onboarding_apply()

        assert change_model_calls == [], (
            f"change_model should NOT be called when model is unchanged, got: {change_model_calls}"
        )


class TestApplySettingsMarksComplete:
    """onboarding must NOT mark itself complete until the user's"""

    def test_apply_settings_marks_complete_after_save(self, ctrl, onboarding_dir):
        """``apply_settings`` writes the marker after ``config.save()``."""
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "tiny"

            def save(self):
                pass

        # Before apply: first run.
        assert ctrl.is_first_run() is True
        ctrl.apply_settings(MockConfig())
        # After apply: marker file exists, is_first_run False.
        assert read_status(onboarding_dir).get("completed") is True
        assert ctrl.is_first_run() is False

    def test_apply_settings_does_not_mark_complete_on_save_failure(self, ctrl, onboarding_dir):
        """If ``config.save()`` raises, the marker must NOT be written"""
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny")

        class FlakyConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "tiny"

            def save(self):
                raise OSError("disk full")

        with pytest.raises(OSError):
            ctrl.apply_settings(FlakyConfig())
        assert read_status(onboarding_dir).get("completed") is not True
        assert ctrl.is_first_run() is True

    def test_next_step_does_not_call_mark_complete(self, ctrl, monkeypatch):
        """Regression: ``next_step`` must not invoke ``mark_complete``"""
        called = []
        monkeypatch.setattr(ctrl, "mark_complete", lambda: called.append(True))
        # Walk all the way to the last step.
        for _ in range(ctrl.total_steps):
            ctrl.next_step()
        assert called == [], "next_step() must not call mark_complete(), the fix"

    def test_skip_still_marks_complete(self, ctrl, onboarding_dir):
        """``skip`` is the other valid completion path."""
        assert ctrl.is_first_run() is True
        ctrl.skip()
        assert read_status(onboarding_dir).get("completed") is True
        assert ctrl.is_first_run() is False


class TestMarkCompleteFailurePropagation:
    """if the onboarding marker write fails (disk full, read-only"""

    def test_mark_complete_raises_on_marker_write_failure(self, ctrl, onboarding_dir, monkeypatch):
        """``mark_complete`` re-raises ``OSError`` from"""
        import voice_typer.server.secure_file_io as sio

        def _boom(path, content, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(sio, "_secure_atomic_write", _boom)
        with pytest.raises(OSError, match="disk full"):
            ctrl.mark_complete()
        # Status was NOT written.
        assert read_status(onboarding_dir).get("completed") is not True

    def test_apply_settings_sets_onboarding_completed_before_save(self, ctrl, onboarding_dir):
        """``apply_settings`` sets ``config.onboarding_completed = True``"""
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny")

        saved_state = {}

        class CapturingConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "tiny"
            onboarding_completed = False

            def save(self):
                # Snapshot the flag value at save() time.
                saved_state["onboarding_completed_at_save"] = self.onboarding_completed
                return True

        config = CapturingConfig()
        ctrl.apply_settings(config)
        # The flag was True when save() was called (before the marker write).
        assert saved_state["onboarding_completed_at_save"] is True, (
            "config.onboarding_completed must be set to True BEFORE config.save() "
            "so the persisted config flag is the source of truth"
        )
        # And it's still True after apply_settings returns.
        assert config.onboarding_completed is True

    def test_apply_settings_surfaces_marker_write_failure(self, ctrl, onboarding_dir, monkeypatch):
        """if ``mark_complete`` fails (marker write error),"""
        import voice_typer.server.secure_file_io as sio

        def _boom(path, content, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(sio, "_secure_atomic_write", _boom)

        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "tiny"
            onboarding_completed = False

            def save(self):
                # Real Config.save() would persist onboarding_completed=True
                return True

        config = MockConfig()
        with pytest.raises(OSError, match="read-only filesystem"):
            ctrl.apply_settings(config)

        # Config flag was set to True BEFORE save() was called, so even
        assert config.onboarding_completed is True, (
            "config.onboarding_completed must be set to True BEFORE config.save() "
            "so the wizard doesn't reappear when the marker write fails"
        )
        # Status was NOT written (the write raised).
        assert read_status(onboarding_dir).get("completed") is not True

    def test_apply_settings_marker_failure_does_not_reappear(self, ctrl, onboarding_dir, monkeypatch):
        """end-to-end: when the marker write fails but the config"""
        import json as _json
        from pathlib import Path

        import voice_typer.server.secure_file_io as sio
        from voice_typer.server.config import Config

        real_write = sio._secure_atomic_write

        def _boom_on_marker(path, content, **kwargs):
            if Path(path).name == ".onboarding_status.json":
                raise OSError("read-only filesystem")
            return real_write(path, content, **kwargs)

        monkeypatch.setattr(sio, "_secure_atomic_write", _boom_on_marker)

        ctrl.set_microphone("mic-usb")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny")

        cfg = Config()
        cfg.microphone = None
        cfg.hotkey = "<f2>"
        cfg.model_size = "tiny"
        cfg.onboarding_completed = False

        with pytest.raises(OSError, match="read-only filesystem"):
            ctrl.apply_settings(cfg)

        assert (onboarding_dir / "config.json").exists()
        persisted = _json.loads((onboarding_dir / "config.json").read_text(encoding="utf-8"))
        assert persisted.get("onboarding_completed") is True
        # ...but the status file was not written (the write raised).
        assert read_status(onboarding_dir).get("completed") is not True

        # Simulate next launch: fresh controller reads disk state.
        from voice_typer.server.onboarding import OnboardingController

        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False, (
            "Wizard must NOT reappear when config.onboarding_completed=True "
            "is persisted, even if the .onboarding_complete marker is missing"
        )

    def test_skip_propagates_marker_write_failure(self, ctrl, onboarding_dir, monkeypatch):
        """``skip`` propagates marker write failures so the IPC"""
        import voice_typer.server.secure_file_io as sio

        def _boom(path, content, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(sio, "_secure_atomic_write", _boom)
        with pytest.raises(OSError, match="disk full"):
            ctrl.skip()
        assert read_status(onboarding_dir).get("completed") is not True


class TestModelOptionsIncludeMultilingualAndParakeet:
    """the wizard's curated ``MODEL_OPTIONS`` list previously"""

    def test_english_only_variants_removed(self, ctrl):
        """The English-only Whisper variants (tiny.en / small.en /"""
        names = {opt["name"] for opt in ctrl.MODEL_OPTIONS}
        for removed in ("tiny.en", "small.en", "medium.en"):
            assert removed not in names, f"{removed} was removed from the catalog but is still in MODEL_OPTIONS"

    def test_includes_kept_multilingual_whisper_variants(self, ctrl):
        """``tiny``, ``large-v3``, and ``large-v3-turbo``"""
        names = {opt["name"] for opt in ctrl.MODEL_OPTIONS}
        assert "tiny" in names
        assert "large-v3" in names
        assert "large-v3-turbo" in names

    def test_includes_parakeet(self, ctrl):
        """NVIDIA Parakeet must be a first-class wizard option."""
        names = {opt["name"] for opt in ctrl.MODEL_OPTIONS}
        assert "parakeet" in names

    def test_get_model_catalog_delegates_to_registry(self):
        """``get_model_catalog()`` returns the full registry"""
        from voice_typer.server.model_registry import MODEL_REGISTRY
        from voice_typer.server.onboarding import OnboardingController

        catalog = OnboardingController.get_model_catalog()
        # Catalog is a list of dicts.
        assert isinstance(catalog, list)
        assert len(catalog) >= len(MODEL_REGISTRY)
        # Every MODEL_REGISTRY key is in the catalog.
        catalog_names = {m["name"] for m in catalog}
        for name in MODEL_REGISTRY:
            assert name in catalog_names, f"registry model {name!r} missing from get_model_catalog()"

    def test_get_model_catalog_entries_have_rich_metadata(self):
        """catalog entries must carry the rich metadata"""
        from voice_typer.server.onboarding import OnboardingController

        catalog = OnboardingController.get_model_catalog()
        assert catalog, "catalog should not be empty"
        required_fields = {
            "name",
            "download_size_mb",
            "required_vram_mb",
            "backend",
            "multilingual",
            "supported_languages",
            "repo_id",
            "speed_rating",
            "accuracy_rating",
        }
        for entry in catalog:
            missing = required_fields - set(entry.keys())
            assert not missing, f"catalog entry {entry.get('name')!r} missing fields: {missing}"

    def test_get_model_catalog_returns_empty_on_import_failure(self, monkeypatch):
        """Defensive: if ``model_registry`` can't be imported, the"""
        # Force the lazy import inside get_model_catalog to fail.
        import sys

        import voice_typer.server.model_registry  # noqa: F401 -- register in sys.modules
        from voice_typer.server.onboarding import OnboardingController

        monkeypatch.setitem(sys.modules, "voice_typer.server.model_registry", None)
        assert OnboardingController.get_model_catalog() == []


class TestModelOptionsVramAndLanguages:
    """``languages`` fields so the renderer can render VRAM / language"""

    def test_all_entries_have_vram_gb(self, ctrl):
        for opt in ctrl.MODEL_OPTIONS:
            assert "vram_gb" in opt, f"MODEL_OPTIONS entry {opt['name']!r} missing vram_gb"
            assert isinstance(opt["vram_gb"], int | float)
            assert opt["vram_gb"] > 0

    def test_all_entries_have_languages(self, ctrl):
        for opt in ctrl.MODEL_OPTIONS:
            assert "languages" in opt, f"MODEL_OPTIONS entry {opt['name']!r} missing languages"
            # None (multilingual) or a non-empty list of language codes.
            lang = opt["languages"]
            assert lang is None or (isinstance(lang, list) and len(lang) > 0), (
                f"MODEL_OPTIONS entry {opt['name']!r} has invalid languages: {lang!r}"
            )

    def test_english_only_variants_have_en_languages(self, ctrl):
        for opt in ctrl.MODEL_OPTIONS:
            if opt["name"].endswith(".en"):
                assert opt["languages"] == ["en"], (
                    f"English-only model {opt['name']!r} should have languages=['en'], got {opt['languages']!r}"
                )

    def test_multilingual_variants_have_none_languages(self, ctrl):
        """the multilingual variants (tiny/small/medium without"""
        multilingual_names = {"tiny", "small", "medium", "parakeet"}
        for opt in ctrl.MODEL_OPTIONS:
            if opt["name"] in multilingual_names:
                assert opt["languages"] is None, (
                    f"Multilingual model {opt['name']!r} should have languages=None, got {opt['languages']!r}"
                )


class TestStepLayout:
    """4-step essentials layout (2026-09-14)."""

    def test_step_order_welcome_consent_model_hotkey(self, ctrl):
        """Step order: Welcome(0), Consent(1), Model(2), Hotkey(3)."""
        assert ctrl.step_name == "Welcome"
        ctrl.next_step()
        assert ctrl.step_name == "Consent"
        ctrl.next_step()
        assert ctrl.step_name == "Model"
        ctrl.next_step()
        assert ctrl.step_name == "Hotkey"

    def test_total_steps_is_four(self, ctrl):
        """7 → 4: essentials flow (2026-09-14)."""
        assert ctrl.total_steps == 4

    def test_check_permissions_returns_dict_shape(self, ctrl):
        """``check_permissions`` returns a renderer-friendly dict with"""
        result = ctrl.check_permissions()
        assert set(result.keys()) == {"platform", "state", "needed", "instructions"}
        assert result["platform"] in {"windows", "macos", "linux", "unknown"}
        assert result["state"] in {"granted", "denied", "unknown"}
        assert isinstance(result["needed"], bool)

    def test_check_permissions_windows_no_instructions(self, ctrl, monkeypatch):
        """on Windows, no permission is needed → ``needed=False``,"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: True)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: False)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.GRANTED,
        )

        result = ctrl.check_permissions()
        assert result["platform"] == "windows"
        assert result["state"] == "granted"
        assert result["needed"] is False
        assert result["instructions"] is None

    def test_check_permissions_macos_denied_returns_instructions(self, ctrl, monkeypatch):
        """on macOS with Accessibility denied, return the"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: True)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.DENIED,
        )

        result = ctrl.check_permissions()
        assert result["platform"] == "macos"
        assert result["state"] == "denied"
        assert result["needed"] is True
        instructions = result["instructions"]
        assert instructions is not None
        # (session NH): server returns i18n keys (title_key / steps_keys)
        assert "title_key" in instructions or "title" in instructions
        assert "steps_keys" in instructions or "steps" in instructions
        steps = instructions["steps_keys"] if "steps_keys" in instructions else instructions["steps"]
        assert isinstance(steps, list)
        assert len(steps) >= 1
        # ``steps_keys`` shape is still the documented IPC contract
        for k in steps:
            assert isinstance(k, str) and k.startswith("onboarding."), f"expected a dotted i18n key, got {k!r}"
        assert len(steps) == 3

    def test_check_permissions_macos_granted_no_instructions(self, ctrl, monkeypatch):
        """on macOS with Accessibility already granted, no"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: True)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.GRANTED,
        )

        result = ctrl.check_permissions()
        assert result["platform"] == "macos"
        assert result["state"] == "granted"
        assert result["needed"] is False
        assert result["instructions"] is None

    def test_check_permissions_linux_denied_returns_input_group_instructions(self, ctrl, monkeypatch):
        """``sudo usermod -aG input $USER`` command and the udev rule"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: False)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: True)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.DENIED,
        )

        result = ctrl.check_permissions()
        assert result["platform"] == "linux"
        assert result["state"] == "denied"
        assert result["needed"] is True
        instructions = result["instructions"]
        assert instructions is not None
        # (session NH): server returns i18n keys (title_key / steps_keys).
        assert "title_key" in instructions or "title" in instructions
        assert "steps_keys" in instructions or "steps" in instructions
        assert "commands" in instructions
        commands = instructions["commands"] or []
        joined_cmds = " ".join(commands)
        assert "usermod" in joined_cmds
        assert "input" in joined_cmds
        assert "KERNEL" in joined_cmds or "udev" in joined_cmds.lower(), (
            f"Linux instructions should include the udev rule snippet, got commands: {commands}"
        )

    def test_check_permissions_linux_granted_no_instructions(self, ctrl, monkeypatch):
        """on Linux with input-group access already granted,"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: False)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: True)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.GRANTED,
        )

        result = ctrl.check_permissions()
        assert result["platform"] == "linux"
        assert result["state"] == "granted"
        assert result["needed"] is False
        assert result["instructions"] is None
