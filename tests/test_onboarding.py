"""Tests for voice_typer.onboarding — OnboardingController wizard."""

import json
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def onboarding_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def ctrl(onboarding_dir):
    """Create an OnboardingController with temp dir."""
    from voice_typer.server.onboarding import OnboardingController

    return OnboardingController(config_dir=onboarding_dir)


class TestOnboardingFirstRun:
    def test_is_first_run_no_config(self, ctrl):
        assert ctrl.is_first_run() is True

    def test_not_first_run_with_config(self, onboarding_dir):
        """#8: A config.json with onboarding_completed=True is NOT first run.

        Previously this test used an empty ``{}`` config which would have
        onboarding_completed=False (default), so is_first_run() now returns
        True. The wizard should appear whenever onboarding_completed is
        False, regardless of whether config.json exists.
        """
        (onboarding_dir / "config.json").write_text(json.dumps({"onboarding_completed": True}), encoding="utf-8")
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is False

    def test_first_run_when_config_has_onboarding_false(self, onboarding_dir):
        """#8: config.json exists but onboarding_completed=False → first run.

        This is the case after app.py saves defaults on the very first
        launch. The wizard should still appear so the user can pick
        their microphone, hotkey, and model.
        """
        (onboarding_dir / "config.json").write_text(json.dumps({"onboarding_completed": False}), encoding="utf-8")
        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True

    def test_not_first_run_after_mark_complete(self, ctrl):
        ctrl.mark_complete()
        assert ctrl.is_first_run() is False


class TestOnboardingSteps:
    def test_initial_step(self, ctrl):
        # total_steps bumped 5 → 6 (Permissions step added).
        assert ctrl.current_step == 0
        assert ctrl.total_steps == 6

    def test_step_names(self, ctrl):
        # "Permissions" inserted between Microphone and Hotkey.
        names = [
            "Welcome",
            "Microphone",
            "Permissions",
            "Hotkey",
            "Model",
            "Done",
        ]
        for i, name in enumerate(names):
            ctrl._current_step = i
            assert ctrl.step_name == name

    def test_next_step(self, ctrl):
        assert ctrl.next_step() == 1
        assert ctrl.current_step == 1

    def test_next_step_capped(self, ctrl):
        ctrl._current_step = 5  # last step index (6 total)
        assert ctrl.next_step() == 5  # Already at last step

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
        """reaching the last step via next_step() must NOT mark
        onboarding complete. Completion is now triggered only by
        apply_settings() (after config.save() succeeds) or skip().

        Previously next_step() called mark_complete() when it reached
        the final step, which meant a user who walked through the
        wizard and reached "Done" but never clicked Apply would be
        treated as onboarded — losing their selections on next launch.
        """
        ctrl._current_step = 4  # second-to-last step
        ctrl.next_step()  # advances to step 5 (Done)
        assert ctrl.current_step == 5
        # Wizard is NOT complete — apply_settings or skip is required.
        assert ctrl.is_first_run() is True


class TestOnboardingSkip:
    def test_skip_marks_complete(self, ctrl):
        """callbacks were removed; verify skip still
        marks the onboarding as complete."""
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
        ctrl.set_model("tiny.en")
        assert ctrl.selected_model == "tiny.en"

    def test_model_options(self, ctrl):
        # list now includes multilingual variants (tiny, small,
        # medium without .en) and Parakeet, in addition to the original
        # 3 English-only Whisper variants.
        assert len(ctrl.MODEL_OPTIONS) == 7


class TestOnboardingApplySettings:
    def test_apply_settings(self, ctrl, onboarding_dir):
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "small.en"

            def save(self):
                pass

        config = MockConfig()
        ctrl.apply_settings(config)
        assert config.microphone == "mic-1"
        assert config.hotkey == "<f4>"
        assert config.model_size == "tiny.en"

    def test_apply_settings_no_mic(self, ctrl):
        ctrl.set_microphone(None)
        ctrl.set_hotkey("<f2>")
        ctrl.set_model("small.en")

        class MockConfig:
            microphone = "old-mic"
            hotkey = "<f2>"
            model_size = "small.en"

            def save(self):
                pass

        config = MockConfig()
        ctrl.apply_settings(config)
        # Should not overwrite when None
        assert config.microphone == "old-mic"


class TestOnboardingWizard:
    """#8: End-to-end test of the wizard flow through the service layer.

    Verifies that:
    - is_first_run() returns True when onboarding_completed is False
    - The wizard can set microphone, hotkey, and model selections
    - apply_settings persists the choices and marks onboarding complete
    - After apply, is_first_run() returns False (wizard won't reappear)
    """

    def test_full_wizard_flow(self, onboarding_dir):
        """Simulate the React wizard's IPC call sequence."""
        from voice_typer.server.onboarding import OnboardingController

        # 1) Backend: first-run detection
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True, "Wizard should appear when onboarding_completed is False"

        # 2) Wizard starts
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.current_step == 0
        # total_steps bumped 5 → 6 (Permissions step added).
        assert ctrl.total_steps == 6

        # 3) Step 1: select microphone
        ctrl.next_step()  # advance to step 1 (Microphone)
        ctrl.set_microphone("mic-usb")
        assert ctrl.selected_microphone == "mic-usb"

        # 4) Step 2: Permissions (/) — no user action required
        #    in this unit test; the renderer probes via the
        #    onboarding_check_permissions IPC and either shows the
        #    platform walkthrough or auto-advances.
        ctrl.next_step()
        assert ctrl.step_name == "Permissions"

        # 5) Step 3: select hotkey
        ctrl.next_step()
        ctrl.set_hotkey("<f4>")
        assert ctrl.selected_hotkey == "<f4>"

        # 6) Step 4: select model
        ctrl.next_step()
        ctrl.set_model("tiny.en")
        assert ctrl.selected_model == "tiny.en"

        # 7) Step 5: apply settings (final step before Done)
        ctrl.next_step()

        # 8) Apply settings to a mock config (mirrors service.onboarding_apply).
        # apply_settings() now calls mark_complete() internally
        #    after config.save() succeeds, so the explicit ctrl.mark_complete()
        #    below is a redundant no-op (kept for clarity / backward compat).
        from voice_typer.server.config import Config

        cfg = Config()
        cfg.microphone = None
        cfg.hotkey = "<f2>"
        cfg.model_size = "small.en"
        ctrl.apply_settings(cfg)
        ctrl.mark_complete()
        cfg.onboarding_completed = True
        cfg.save()

        # 8) Verify the wizard won't reappear
        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False, "Wizard should NOT reappear after apply_settings + mark_complete"

        # 9) Verify the user's choices were persisted
        cfg2 = Config.load()
        assert cfg2.microphone == "mic-usb"
        assert cfg2.hotkey == "<f4>"
        assert cfg2.model_size == "tiny.en"
        assert cfg2.onboarding_completed is True

    def test_skip_flow(self, onboarding_dir):
        """Skip path: user clicks 'Skip' on step 0 — defaults are kept."""
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
        # default hotkey is platform-aware
        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()
        assert cfg.model_size == "small.en"  # default


# 17-H-: service-layer onboarding_apply side effects ────────────


@pytest.fixture
def app_with_service(tmp_path, monkeypatch):
    """Build a real VoiceTyperApp + VoiceTyperService with mocked deps.

    Mirrors the ``app`` fixture in tests/test_app.py: heavy imports
    are mocked, pynput is forced as the hotkey backend, and autostart
    helpers are stubbed so __init__ doesn't touch the OS.
    """
    # Point _config_dir at tmp_path so Config.save() is isolated.
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

    # Mock heavy hardware/GUI deps (in addition to conftest's autouse
    # mock_heavy_imports, which doesn't run for this module-scope
    # override — be defensive and set them up explicitly here).
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
    monkeypatch.setattr("voice_typer.server.app.atexit.register", lambda *a, **kw: None)

    # Stub autostart helpers so __init__ doesn't touch the OS.
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    # Force PynputHotkey backend so tests can assert hotkey_str
    # without depending on native binaries. Patch BOTH app and
    # hotkey_dispatcher namespaces (see  / fix in
    # tests/test_app.py for why both are required).
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
    """17-H-onboarding_apply must re-register the hotkey and
    push a config_changed event so the user's wizard choices take
    effect immediately (without app restart).

    Previously onboarding_apply only called config.save(), so the
    hotkey/model/mic chosen in the first-run wizard were ignored
    until the next app launch.
    """

    def test_hotkey_re_registered_without_restart(self, app_with_service, captured_events):
        """The dictation hotkey backend reflects the wizard's choice
        immediately after onboarding_apply — no restart needed."""
        app, service = app_with_service

        # Wizard flow: start, pick a non-default hotkey, apply.
        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        result = service.onboarding_apply()

        assert result == {"ok": True}, f"onboarding_apply failed: {result}"

        # The hotkey dispatcher should have a live backend whose
        # hotkey_str matches the user's selection. Before 17-H-,
        # apply_config_side_effects was never called so the backend
        # would still be None (or hold the default hotkey).
        backend = app.hotkeys._hotkey_backend
        assert backend is not None, (
            "Hotkey backend was not registered by onboarding_apply — apply_config_side_effects was not invoked"
        )
        assert backend.hotkey_str == "<f6>", (
            f"Expected hotkey_str='<f6>' after onboarding_apply, got {backend.hotkey_str!r}"
        )

    def test_config_changed_event_pushed(self, app_with_service, captured_events):
        """onboarding_apply pushes a config_changed event so the
        renderer can refresh UI-local state without a bespoke
        get_config round-trip (parity with set_config)."""
        app, service = app_with_service

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_apply()

        config_events = [e for e in captured_events if e.get("type") == "config_changed"]
        assert len(config_events) >= 1, f"Expected at least one config_changed event, got: {captured_events}"
        # The event data must carry the wizard's hotkey choice so the
        # renderer can update its hotkey label without re-fetching.
        data = config_events[-1].get("data", {})
        assert data.get("hotkey") == "<f6>", f"config_changed event data should include hotkey='<f6>', got: {data}"
        assert "model_size" in data, "config_changed event data should include model_size"

    def test_onboarding_completed_persisted(self, app_with_service, captured_events):
        """The existing onboarding_completed=True + config.save()
        behavior must be preserved by the refactor."""
        app, service = app_with_service

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_apply()

        assert app.config.onboarding_completed is True
        assert app.config.hotkey == "<f6>"

    def test_model_change_invoked_when_model_differs(self, app_with_service, captured_events, monkeypatch):
        """When the user picks a non-default model, onboarding_apply
        must invoke ModelManager.change_model so the new model loads
        immediately (or queues via _pending_model_change if the
        background loader hasn't finished)."""
        app, service = app_with_service

        # Spy on change_model — don't actually run the unload/load
        # cycle (which would try to load a real model in the test env).
        change_model_calls: list[str] = []
        monkeypatch.setattr(
            app.models,
            "change_model",
            lambda model_size: change_model_calls.append(model_size),
        )

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_set_model("tiny.en")  # non-default
        service.onboarding_apply()

        assert change_model_calls == ["tiny.en"], f"Expected change_model('tiny.en'), got: {change_model_calls}"

    def test_model_change_skipped_when_model_unchanged(self, app_with_service, captured_events, monkeypatch):
        """When the user keeps the default model, onboarding_apply
        must NOT invoke change_model (avoids an expensive no-op
        unload/load cycle)."""
        app, service = app_with_service

        change_model_calls: list[str] = []
        monkeypatch.setattr(
            app.models,
            "change_model",
            lambda model_size: change_model_calls.append(model_size),
        )

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        # Don't call onboarding_set_model — OnboardingController's
        # default is "small.en", which matches Config's default.
        service.onboarding_apply()

        assert change_model_calls == [], (
            f"change_model should NOT be called when model is unchanged, got: {change_model_calls}"
        )


# onboarding bug regressions ( /  /  /    ──
# )                                                    ──


class TestApplySettingsMarksComplete:
    """onboarding must NOT mark itself complete until the user's
    selections are actually persisted via ``apply_settings`` (or
    explicitly discarded via ``skip``).

    The previous implementation called ``mark_complete()`` from
    ``next_step()`` as soon as the user reached the final "Done"
    step — meaning a user who walked through the wizard but never
    clicked Apply (or whose ``config.save()`` later failed) would
    be treated as onboarded, losing their selections on next launch.
    """

    def test_apply_settings_marks_complete_after_save(self, ctrl, onboarding_dir):
        """``apply_settings`` writes the marker after ``config.save()``."""
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "small.en"

            def save(self):
                pass

        # Before apply: first run.
        assert ctrl.is_first_run() is True
        ctrl.apply_settings(MockConfig())
        # After apply: marker file exists, is_first_run False.
        assert (onboarding_dir / ".onboarding_complete").exists()
        assert ctrl.is_first_run() is False

    def test_apply_settings_does_not_mark_complete_on_save_failure(self, ctrl, onboarding_dir):
        """If ``config.save()`` raises, the marker must NOT be written
        so the wizard reappears on next launch and the user gets
        another chance to complete setup."""
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        class FlakyConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "small.en"

            def save(self):
                raise OSError("disk full")

        # save() raises → mark_complete() is never reached.
        with pytest.raises(OSError):
            ctrl.apply_settings(FlakyConfig())
        assert not (onboarding_dir / ".onboarding_complete").exists()
        assert ctrl.is_first_run() is True

    def test_next_step_does_not_call_mark_complete(self, ctrl, monkeypatch):
        """Regression: ``next_step`` must not invoke ``mark_complete``
        even when it reaches the last step."""
        called = []
        monkeypatch.setattr(ctrl, "mark_complete", lambda: called.append(True))
        # Walk all the way to the last step.
        for _ in range(ctrl.total_steps):
            ctrl.next_step()
        assert called == [], "next_step() must not call mark_complete() — the fix"

    def test_skip_still_marks_complete(self, ctrl, onboarding_dir):
        """``skip`` is the other valid completion path."""
        assert ctrl.is_first_run() is True
        ctrl.skip()
        assert (onboarding_dir / ".onboarding_complete").exists()
        assert ctrl.is_first_run() is False


class TestMarkCompleteFailurePropagation:
    """if the onboarding marker write fails (disk full, read-only
    ``config_dir``, permission revoked), the wizard must surface the
    error rather than silently swallowing it.

    Root cause: ``mark_complete`` caught all exceptions via
    ``except Exception: log.exception(...)`` without re-raising, AND
    ``apply_settings`` never set ``config.onboarding_completed = True``
    before ``config.save()``. Result: settings were saved to
    ``config.json`` but the marker was missing and
    ``onboarding_completed`` stayed ``False`` — so ``is_first_run()``
    returned ``True`` on every launch, trapping the user in an infinite
    wizard-reappear loop.

    Fix (two halves):
    1. ``apply_settings`` sets ``config.onboarding_completed = True``
       BEFORE ``config.save()`` — the config flag becomes the source of
       truth; the marker file becomes a fast-path cache.
    2. ``mark_complete`` re-raises on failure so the IPC layer can
       surface the disk error to the user.
    """

    def test_mark_complete_raises_on_marker_write_failure(self, ctrl, onboarding_dir, monkeypatch):
        """``mark_complete`` re-raises ``OSError`` from
        ``_secure_atomic_write`` instead of swallowing it."""
        from voice_typer.server import config as config_mod

        def _boom(path, content, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _boom)
        with pytest.raises(OSError, match="disk full"):
            ctrl.mark_complete()
        # Marker was NOT written.
        assert not (onboarding_dir / ".onboarding_complete").exists()

    def test_apply_settings_sets_onboarding_completed_before_save(self, ctrl, onboarding_dir):
        """``apply_settings`` sets ``config.onboarding_completed = True``
        BEFORE calling ``config.save()`` so the config flag is persisted
        even if the marker write later fails."""
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        saved_state = {}

        class CapturingConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "small.en"
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
        """if ``mark_complete`` fails (marker write error),
        ``apply_settings`` propagates the exception so the IPC layer
        can surface the error to the user. ``config.onboarding_completed``
        was set to ``True`` and persisted via ``config.save()`` BEFORE
        the marker write, so the wizard will NOT reappear on the next
        launch even though the marker file is missing."""
        from voice_typer.server import config as config_mod

        def _boom(path, content, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _boom)

        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "small.en"
            onboarding_completed = False

            def save(self):
                # Real Config.save() would persist onboarding_completed=True
                # to disk here. The mock returns True (success); the test
                # asserts below that the flag was flipped before save.
                return True

        config = MockConfig()
        # apply_settings propagates the OSError from mark_complete (:
        # no longer swallowed).
        with pytest.raises(OSError, match="read-only filesystem"):
            ctrl.apply_settings(config)

        # Config flag was set to True BEFORE save() was called — so even
        # though the marker write failed, the persisted config flag breaks
        # the infinite wizard-reappear loop on next launch.
        assert config.onboarding_completed is True, (
            "config.onboarding_completed must be set to True BEFORE config.save() "
            "so the wizard doesn't reappear when the marker write fails"
        )
        # Marker was NOT written (the write raised).
        assert not (onboarding_dir / ".onboarding_complete").exists()

    def test_apply_settings_marker_failure_does_not_reappear(self, ctrl, onboarding_dir, monkeypatch):
        """end-to-end: when the marker write fails but the config
        flag was persisted, ``is_first_run()`` returns ``False`` on the
        next launch (simulated by writing the persisted config to disk
        and constructing a fresh controller). This is the core
        acceptance criterion — the infinite wizard-reappear loop is
        broken."""
        import json as _json
        from pathlib import Path

        from voice_typer.server import config as config_mod
        from voice_typer.server.config import Config

        # Capture the real _secure_atomic_write BEFORE patching so the
        # patched version can delegate non-marker writes to it.
        real_write = config_mod._secure_atomic_write

        def _boom_on_marker(path, content, **kwargs):
            if Path(path).name == ".onboarding_complete":
                raise OSError("read-only filesystem")
            return real_write(path, content, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _boom_on_marker)

        ctrl.set_microphone("mic-usb")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        cfg = Config()
        cfg.microphone = None
        cfg.hotkey = "<f2>"
        cfg.model_size = "small.en"
        cfg.onboarding_completed = False

        # apply_settings sets cfg.onboarding_completed=True, saves (real
        # write to config.json succeeds), then mark_complete raises
        # OSError for the marker path.
        with pytest.raises(OSError, match="read-only filesystem"):
            ctrl.apply_settings(cfg)

        # config.json was persisted with onboarding_completed=True...
        assert (onboarding_dir / "config.json").exists()
        persisted = _json.loads((onboarding_dir / "config.json").read_text(encoding="utf-8"))
        assert persisted.get("onboarding_completed") is True
        # ...but the marker is missing.
        assert not (onboarding_dir / ".onboarding_complete").exists()

        # Simulate next launch: fresh controller reads disk state.
        from voice_typer.server.onboarding import OnboardingController

        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False, (
            "Wizard must NOT reappear when config.onboarding_completed=True "
            "is persisted, even if the .onboarding_complete marker is missing"
        )

    def test_skip_propagates_marker_write_failure(self, ctrl, onboarding_dir, monkeypatch):
        """``skip`` propagates marker write failures so the IPC
        layer can surface the error to the user (instead of silently
        swallowing it and leaving the wizard in an inconsistent state)."""
        from voice_typer.server import config as config_mod

        def _boom(path, content, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(config_mod, "_secure_atomic_write", _boom)
        with pytest.raises(OSError, match="disk full"):
            ctrl.skip()
        assert not (onboarding_dir / ".onboarding_complete").exists()


class TestModelOptionsIncludeMultilingualAndParakeet:
    """the wizard's curated ``MODEL_OPTIONS`` list previously
    excluded multilingual Whisper variants (tiny/small/medium without
    ``.en``) and the NVIDIA Parakeet model, so non-English users had
    no in-wizard path to pick a multilingual model."""

    def test_includes_english_only_variants(self, ctrl):
        names = {opt["name"] for opt in ctrl.MODEL_OPTIONS}
        assert "tiny.en" in names
        assert "small.en" in names
        assert "medium.en" in names

    def test_includes_multilingual_whisper_variants(self, ctrl):
        """``tiny``, ``small``, ``medium`` (without ``.en``)
        must be present so non-English users can pick a multilingual
        model from the wizard."""
        names = {opt["name"] for opt in ctrl.MODEL_OPTIONS}
        assert "tiny" in names
        assert "small" in names
        assert "medium" in names

    def test_includes_parakeet(self, ctrl):
        """NVIDIA Parakeet must be a first-class wizard option."""
        names = {opt["name"] for opt in ctrl.MODEL_OPTIONS}
        assert "parakeet" in names

    def test_get_model_catalog_delegates_to_registry(self):
        """``get_model_catalog()`` returns the full registry
        catalog (rich metadata: VRAM, languages, speed/accuracy,
        repo_id, backend, is_distilled) — superset of MODEL_OPTIONS."""
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
        """catalog entries must carry the rich metadata
        fields (download_size_mb, required_vram_mb, backend,
        multilingual, supported_languages, repo_id, speed_rating,
        accuracy_rating) so the renderer can render VRAM / language /
        speed / accuracy badges."""
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
        """Defensive: if ``model_registry`` can't be imported, the
        class method returns an empty list instead of raising."""
        # Force the lazy import inside get_model_catalog to fail.
        import sys

        from voice_typer.server.onboarding import OnboardingController

        real_module = sys.modules.pop("voice_typer.server.model_registry", None)
        monkeypatch.setitem(sys.modules, "voice_typer.server.model_registry", None)
        try:
            assert OnboardingController.get_model_catalog() == []
        finally:
            if real_module is not None:
                sys.modules["voice_typer.server.model_registry"] = real_module


class TestModelOptionsVramAndLanguages:
    """each ``MODEL_OPTIONS`` entry must carry ``vram_gb`` and
    ``languages`` fields so the renderer can render VRAM / language
    badges on each model card.

    ``languages`` semantics:
    - ``["en"]`` → English-only (renderer renders an "EN" badge).
    - ``None``   → multilingual (renderer renders a "Multilingual" badge).
    - list of strings → supported language codes.
    """

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
        """the multilingual variants (tiny/small/medium without
        ``.en``) and Parakeet must have ``languages=None`` so the
        renderer renders a 'Multilingual' badge."""
        multilingual_names = {"tiny", "small", "medium", "parakeet"}
        for opt in ctrl.MODEL_OPTIONS:
            if opt["name"] in multilingual_names:
                assert opt["languages"] is None, (
                    f"Multilingual model {opt['name']!r} should have languages=None, got {opt['languages']!r}"
                )


class TestPermissionsStep:
    """onboarding wizard must include a platform-
    conditional Permissions step between Microphone and Hotkey that
    detects OS-level keyboard-monitoring permission state and shows
    platform-specific setup instructions.

    - **Windows**: no permission needed (``needed=False``).
    - **macOS**: Accessibility permission walkthrough (the fix).
    - **Linux**: input group + udev rule walkthrough (the fix).
    """

    def test_permissions_step_exists_between_mic_and_hotkey(self, ctrl):
        """Step order: Welcome(0), Microphone(1), Permissions(2),
        Hotkey(3), Model(4), Done(5)."""
        ctrl._current_step = 1
        assert ctrl.step_name == "Microphone"
        ctrl.next_step()
        assert ctrl.step_name == "Permissions"
        ctrl.next_step()
        assert ctrl.step_name == "Hotkey"

    def test_total_steps_is_six(self, ctrl):
        """bumped from 5 → 6 to add Permissions step."""
        assert ctrl.total_steps == 6

    def test_check_permissions_returns_dict_shape(self, ctrl):
        """``check_permissions`` returns a renderer-friendly dict with
        ``platform``, ``state``, ``needed``, ``instructions`` keys."""
        result = ctrl.check_permissions()
        assert set(result.keys()) == {"platform", "state", "needed", "instructions"}
        assert result["platform"] in {"windows", "macos", "linux", "unknown"}
        assert result["state"] in {"granted", "denied", "unknown"}
        assert isinstance(result["needed"], bool)

    def test_check_permissions_windows_no_instructions(self, ctrl, monkeypatch):
        """on Windows, no permission is needed → ``needed=False``,
        ``instructions=None``."""
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
        """on macOS with Accessibility denied, return the
        System Settings → Accessibility walkthrough."""
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
        # so the renderer can localize the title + step text. The literal
        # `title` / `steps` fields remain available as a legacy fallback for
        # older backends. Assert on the i18n-key fields first, then fall
        # back to literals if absent.
        assert "title_key" in instructions or "title" in instructions
        assert "steps_keys" in instructions or "steps" in instructions
        steps = instructions["steps_keys"] if "steps_keys" in instructions else instructions["steps"]
        assert isinstance(steps, list)
        assert len(steps) >= 1
        # Resolve the i18n keys to their English values via en.json and
        # check the macOS walkthrough mentions Accessibility.
        import json
        from pathlib import Path

        en_path = (
            Path(__file__).parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "translations"
            / "en.json"
        )
        en = json.loads(en_path.read_text(encoding="utf-8"))

        def flat(d, p=""):
            out = {}
            for k, v in d.items():
                key = f"{p}.{k}" if p else k
                if isinstance(v, dict):
                    out.update(flat(v, key))
                else:
                    out[key] = v
            return out

        en_flat = flat(en)
        joined_parts = [en_flat.get(k, k) for k in steps]
        joined = " ".join(joined_parts).lower()
        assert "accessibility" in joined

    def test_check_permissions_macos_granted_no_instructions(self, ctrl, monkeypatch):
        """on macOS with Accessibility already granted, no
        instructions needed (``needed=False``)."""
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
        """on Linux without input-group access, return the
        ``sudo usermod -aG input $USER`` command and the udev rule
        snippet so the user can grant access manually."""
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
        # must include the usermod command for the input group.
        commands = instructions["commands"] or []
        joined_cmds = " ".join(commands)
        assert "usermod" in joined_cmds
        assert "input" in joined_cmds
        # must include the udev rule snippet.
        assert "KERNEL" in joined_cmds or "udev" in joined_cmds.lower(), (
            f"Linux instructions should include the udev rule snippet, got commands: {commands}"
        )

    def test_check_permissions_linux_granted_no_instructions(self, ctrl, monkeypatch):
        """on Linux with input-group access already granted,
        no instructions needed (``needed=False``)."""
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
