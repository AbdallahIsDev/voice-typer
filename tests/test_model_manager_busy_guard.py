"""UE-11 + UE-48 regression tests for ModelManager."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.model_manager import ModelManager


class _Config:
    """Minimal config stub matching ``ConfigProtocol``'s fields."""

    def __init__(self, asr_backend: str = "whisper", model_size: str = "tiny.en") -> None:
        self.asr_backend = asr_backend
        self.model_size = model_size
        self.device = "cpu"
        self.language = "en"
        self.beam_size = 1
        self.best_of = 1
        self.condition_on_previous_text = False
        self.save_calls: list[bool] = []

    def save(self) -> bool:
        self.save_calls.append(True)
        return True


def _make_mm(
    *,
    asr_backend: str = "whisper",
    recording: bool = False,
    busy: bool = False,
) -> tuple[ModelManager, MagicMock, _Config, AsrBackendRegistry]:
    """Build a ModelManager with a mock app + real registry."""
    config = _Config(asr_backend=asr_backend)
    registry = AsrBackendRegistry(config)

    app = MagicMock(name="app")
    app.config = config
    app.recorder.recording = recording
    busy_event = threading.Event()
    if not busy:
        busy_event.set()  # is_set() == True means NOT busy
    app._busy_event = busy_event
    app._config_mutation_lock = threading.RLock()
    app._shutting_down = False
    app._pending_dictation = False

    mm = ModelManager.__new__(ModelManager)
    mm._app = app
    mm._registry = registry
    mm._model_change_lock = threading.RLock()
    mm._lazy_init_lock = threading.Lock()
    mm._model_lru_lock = threading.Lock()
    mm._model_access_times = {}
    mm._pending_model_change = None
    mm._pending_backend_change = None
    mm._pending_dictation = False
    mm._model_load_attempted = False
    mm._model_load_thread = None
    mm._idle_unload_lock = threading.Lock()

    return mm, app, config, registry


class TestSetActiveBackendDefersWhenBusy:
    """UE-11: ``set_active_backend`` must defer when the user is"""

    def test_set_active_backend_defers_when_recording(self):
        """
        When ``recorder.recording`` is True, ``set_active_backend``
        MUST NOT run the unload phase, unloading the ctranslate2 model
        """
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=True, busy=False)
        # Pre-register a whisper engine so we can assert it was NOT
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine

        # Stub _ensure_engine so it doesn't try to actually import
        mm._ensure_engine = MagicMock()

        mm.set_active_backend("qwen")

        assert mm._pending_backend_change == "qwen", (
            "UE-11: set_active_backend must capture the requested backend "
            "in _pending_backend_change when recording, so the next "
            "apply_pending_model_change call can re-apply it. Pre-fix, "
            "set_active_backend unconditionally ran the unload phase "
            "mid-transcription."
        )
        # Config was persisted (matching change_model's setattr-before-
        assert config.save_calls, (
            "UE-11: set_active_backend must persist the new backend via "
            "config.save() even when deferring, a crash mid-recording "
            "shouldn't lose the user's intent."
        )
        assert config.asr_backend == "qwen"
        (
            whisper_engine.unload.assert_not_called(),
            (
                "UE-11: set_active_backend must NOT call _change_model_unload_phase "
                "when recording, unloading the ctranslate2 model mid-inference "
                "crashes the transcribe thread."
            ),
        )
        # The user was notified.
        app.tray.notify.assert_called()
        notify_args = app.tray.notify.call_args
        assert "qwen" in str(notify_args), (
            "UE-11: set_active_backend must notify the user that the backend will change after the current recording."
        )

    def test_set_active_backend_defers_when_busy_event_not_set(self):
        """When ``_busy_event.is_set()`` is False (busy, transcribe"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=True)
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine

        mm._ensure_engine = MagicMock()

        mm.set_active_backend("parakeet")

        assert mm._pending_backend_change == "parakeet", (
            "UE-11: set_active_backend must defer when _busy_event is not set (transcribe thread is running mid-call)."
        )
        whisper_engine.unload.assert_not_called()

    def test_set_active_backend_does_not_defer_when_not_busy(self):
        """When NOT recording AND not busy, ``set_active_backend`` MUST"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine

        # Stub _ensure_engine so it doesn't try to actually import
        def fake_ensure(backend_name):
            qwen_engine = MagicMock()
            qwen_engine.is_loaded = True
            registry.register(backend_name, qwen_engine)

        mm._ensure_engine = fake_ensure
        # Stub load_active to return a truthy backend without loading.
        registry.load_active = lambda progress_callback=None: registry.get("qwen")
        # Stub touch_model + _evict_lru_model (they touch LRU state).
        mm.touch_model = lambda name: None
        mm._evict_lru_model = lambda: None

        mm.set_active_backend("qwen")
        # Join the background BackendChange thread so its
        bg_thread = getattr(mm, "_backend_change_thread", None)
        if bg_thread is not None:
            bg_thread.join(timeout=5.0)

        # NOT deferred.
        assert mm._pending_backend_change is None, (
            "UE-11: set_active_backend must NOT defer when not recording "
            "and not busy, the existing immediate-apply path is preserved."
        )
        # Config was set + saved.
        assert config.asr_backend == "qwen"
        # Whisper engine WAS unloaded (immediate-apply path).
        whisper_engine.unload.assert_called()

    def test_set_active_backend_noop_when_already_active_is_unaffected(self):
        """UE-11 must NOT break the existing no-op short-circuit when"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # No save_calls spy needed, the no-op must NOT call save().
        config.save_calls.clear()

        mm.set_active_backend("whisper")

        assert mm._pending_backend_change is None
        assert config.save_calls == [], (
            "UE-11: set_active_backend must NOT call config.save() when "
            "the backend is already active (no-op short-circuit)."
        )


class TestApplyPendingBackendChange:
    """UE-11: ``apply_pending_model_change`` must apply a deferred"""

    def test_apply_pending_backend_change_invokes_set_active_backend(self):
        """When ``_pending_backend_change`` is set,"""
        mm, app, config, registry = _make_mm(asr_backend="qwen", recording=False, busy=False)
        # Simulate a deferred backend change captured during a previous
        mm._pending_backend_change = "whisper"

        set_backend_calls: list[str] = []
        original_set_active_backend_blocking = mm._set_active_backend_blocking

        def spy_set_active_backend(backend):
            set_backend_calls.append(backend)
            # Call the real method to actually apply the change.
            original_set_active_backend_blocking(backend)

        mm._set_active_backend_blocking = spy_set_active_backend

        # Stub _ensure_engine so it doesn't try to actually import
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)
        mm.transcriber = whisper_engine
        mm._ensure_engine = MagicMock()
        registry.load_active = lambda progress_callback=None: registry.get("whisper")
        mm.touch_model = lambda name: None
        mm._evict_lru_model = lambda: None

        result = mm.apply_pending_model_change()

        assert result is True, (
            "UE-11: apply_pending_model_change must return True when a deferred backend change was applied."
        )
        assert set_backend_calls == ["whisper"], (
            "UE-11: apply_pending_model_change must re-invoke "
            "_set_active_backend_blocking (the AB-10 blocking variant) "
            "with the deferred backend name."
        )
        # The pending field was cleared (no re-fire on the next recording).
        assert mm._pending_backend_change is None

    def test_apply_pending_model_change_applies_both_model_and_backend(self):
        """``_pending_backend_change`` are set, both must be applied"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        mm._pending_model_change = "medium.en"
        mm._pending_backend_change = "whisper"

        # Spy: track apply order on the BLOCKING variants (see
        apply_calls: list[str] = []
        mm._change_model_blocking = lambda model_size: apply_calls.append(("_change_model_blocking", model_size))
        mm._set_active_backend_blocking = lambda backend: apply_calls.append(("_set_active_backend_blocking", backend))

        result = mm.apply_pending_model_change()

        assert result is True
        assert apply_calls == [
            ("_change_model_blocking", "medium.en"),
            ("_set_active_backend_blocking", "whisper"),
        ], (
            "UE-11: apply_pending_model_change must apply the model "
            "change FIRST (because the blocking model-change variant "
            "re-evaluates the backend from model_size) and the backend "
            "change SECOND (so an explicit set_active_backend overrides "
            "the model-change-implied backend). Both apply via the AB-10 "
            "BLOCKING variants, not the public non-blocking wrappers."
        )
        # Both pending fields cleared.
        assert mm._pending_model_change is None
        assert mm._pending_backend_change is None

    def test_apply_pending_model_change_clears_both_before_apply(self):
        """Both pending fields must be cleared BEFORE either apply runs"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        mm._pending_model_change = "medium.en"
        mm._pending_backend_change = "whisper"

        # Make _change_model_blocking raise to simulate a crash mid-apply.
        def crashing_change_model_blocking(model_size):
            raise RuntimeError("simulated crash mid-apply")

        mm._change_model_blocking = crashing_change_model_blocking
        mm._set_active_backend_blocking = MagicMock()

        with pytest.raises(RuntimeError, match="simulated crash"):
            mm.apply_pending_model_change()

        # Both fields were cleared BEFORE the apply ran, so the crash
        assert mm._pending_model_change is None, (
            "UE-11: apply_pending_model_change must clear _pending_model_change "
            "BEFORE invoking _change_model_blocking, a crash mid-apply must "
            "not leave a stale request that re-fires on the next recording."
        )
        assert mm._pending_backend_change is None, (
            "UE-11: apply_pending_model_change must clear _pending_backend_change "
            "BEFORE invoking _change_model_blocking, even if _change_model_blocking "
            "crashes, the backend change must not re-fire on the next recording."
        )
        mm._set_active_backend_blocking.assert_not_called()

    def test_apply_pending_model_change_noop_when_neither_set(self):
        """When neither ``_pending_model_change`` nor"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        mm._pending_model_change = None
        mm._pending_backend_change = None

        result = mm.apply_pending_model_change()

        assert result is False, (
            "UE-11: apply_pending_model_change must return False when neither pending field is set (no-op)."
        )

    def test_apply_pending_model_change_handles_missing_backend_field(self):
        """Defensive: legacy test fixtures that construct ModelManager"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # Simulate a legacy fixture that skipped setting the new field.
        del mm._pending_backend_change
        mm._pending_model_change = None

        # Must NOT raise AttributeError.
        result = mm.apply_pending_model_change()
        assert result is False

    def test_apply_pending_model_change_applies_only_backend_when_model_none(self):
        """When only ``_pending_backend_change`` is set (no"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        mm._pending_model_change = None
        mm._pending_backend_change = "whisper"

        change_model_calls: list[str] = []
        set_backend_calls: list[str] = []
        mm._change_model_blocking = lambda model_size: change_model_calls.append(model_size)
        mm._set_active_backend_blocking = lambda backend: set_backend_calls.append(backend)

        result = mm.apply_pending_model_change()

        assert result is True
        assert change_model_calls == [], (
            "UE-11: apply_pending_model_change must NOT call _change_model_blocking when _pending_model_change is None."
        )
        assert set_backend_calls == ["whisper"]


class TestEnsureActiveEngineLoadedBusyRejection:
    """``ensure_active_engine_loaded`` must reject when the"""

    def test_rejects_when_active_backend_is_busy(self):
        """When the active backend's busy flag is set (inside"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        # Mark the active backend as busy (simulating a stuck
        registry.set_busy("parakeet")

        result = mm.ensure_active_engine_loaded()

        assert result is None, (
            "ensure_active_engine_loaded must return None when "
            "the active backend is busy (stuck transcription). Returning "
            "None causes recording_controller.start to fall through to "
            "fallback_to_whisper, which loads a SEPARATE backend rather "
            "than piling up on the stuck backend's ctranslate2 lock."
        )
        assert mm._pending_dictation is True, (
            "ensure_active_engine_loaded must set "
            "_pending_dictation = True when rejecting so the user's F2 "
            "press is queued and re-tried after the watchdog recovers."
        )

    def test_does_not_reject_when_active_backend_not_busy(self):
        """When the active backend is NOT busy,"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        mm._ensure_engine = MagicMock()
        mm.touch_model = MagicMock()
        # Make the registry return a non-None engine so the method
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)

        result = mm.ensure_active_engine_loaded()

        # Not rejected (returns the active transcriber, not None).
        assert result is not None, (
            "ensure_active_engine_loaded must NOT reject when the "
            "active backend is not busy, the existing lazy-init path is "
            "preserved."
        )
        assert mm._pending_dictation is False

    def test_busy_check_is_defensive_against_registry_errors(self):
        """doesn't implement the method),"""
        mm, app, config, registry = _make_mm(asr_backend="whisper", recording=False, busy=False)
        # Make is_busy raise.
        registry.is_busy = MagicMock(side_effect=RuntimeError("simulated registry error"))
        # Stub the rest so the method can complete successfully.
        mm._ensure_engine = MagicMock()
        mm.touch_model = MagicMock()
        whisper_engine = MagicMock()
        whisper_engine.is_loaded = True
        registry.register("whisper", whisper_engine)

        # Must NOT raise.
        result = mm.ensure_active_engine_loaded()

        # The busy-check failed open (defensive), the method proceeded
        assert result is not None, (
            "ensure_active_engine_loaded must be defensive "
            "against registry.is_busy errors, the busy-check must "
            "fail OPEN (continue with the existing path), not crash."
        )


class TestForceUnloadActive:
    """``force_unload_active`` is the watchdog's escalation"""

    def test_drops_registry_slot(self):
        """``force_unload_active`` must drop the active backend's"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        assert registry.get("parakeet") is None, (
            "force_unload_active must drop the _backends[name] slot "
            "so the next dictation cannot enter the same engine instance "
            "the stuck thread still occupies."
        )

    def test_does_not_destroy_live_engine(self):
        """``force_unload_active`` must NOT call ``backend.unload()`` —"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        (
            parakeet_engine.unload.assert_not_called(),
            (
                "force_unload_active must NOT destroy the engine object "
                "— the stuck thread may still be inside its C-level call; the "
                "backend is ejected from the registry instead."
            ),
        )

    def test_calls_force_clear_busy(self):
        """``force_unload_active`` must call"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)
        # Mark the backend as busy (simulating a stuck transcription).
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        mm.force_unload_active()

        assert registry.is_busy("parakeet") is False, (
            "force_unload_active must clear the busy flag so the "
            "next ensure_active_engine_loaded isn't rejected. Without "
            "this, the busy flag would remain set forever (the stuck "
            "transcription never returned to clear it) and every "
            "subsequent dictation would be rejected + queued indefinitely."
        )

    def test_best_effort_never_raises(self):
        """``force_unload_active`` must NEVER raise, even if every"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        # Make every layer raise.
        registry.unregister = MagicMock(side_effect=RuntimeError("registry unregister failed"))
        registry.force_clear_busy = MagicMock(side_effect=RuntimeError("force_clear_busy failed"))
        # Patch release_gpu_memory to raise.
        import sys
        from types import ModuleType

        fake_asr_utils = ModuleType("voice_typer.server.asr_utils")
        fake_asr_utils.release_gpu_memory = MagicMock(side_effect=RuntimeError("gpu release failed"))
        original_module = sys.modules.get("voice_typer.server.asr_utils")
        sys.modules["voice_typer.server.asr_utils"] = fake_asr_utils
        try:
            # Must NOT raise.
            mm.force_unload_active()
        finally:
            if original_module is not None:
                sys.modules["voice_typer.server.asr_utils"] = original_module
            else:
                sys.modules.pop("voice_typer.server.asr_utils", None)

    def test_idempotent_calling_twice_does_not_raise(self):
        """``force_unload_active`` must be idempotent, calling it"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        # First call.
        mm.force_unload_active()
        # Second call (backend is already ejected).
        mm.force_unload_active()

        # No exception raised, the test passing is the assertion.
        assert registry.get("parakeet") is None
        assert not parakeet_engine.unload.called

    def test_does_not_touch_config_asr_backend(self):
        """``force_unload_active`` must NOT touch ``config.asr_backend``"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        assert config.asr_backend == "parakeet", (
            "force_unload_active must NOT touch config.asr_backend "
            "— the watchdog's contract is to tear down the stuck model, "
            "not to switch backends."
        )

    def test_does_not_call_tray_set_state(self):
        """``force_unload_active`` must NOT call ``tray.set_state`` —"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)

        mm.force_unload_active()

        (
            app.tray.set_state.assert_not_called(),
            (
                "force_unload_active must NOT call tray.set_state, "
                "the watchdog has already set the tray state, and overwriting "
                "it would confuse the user (the recovery message is more "
                "specific than the TY-11 'Idle, model unloaded' message)."
            ),
        )


# +  integration: end-to-end stuck recovery ────────────


class TestStuckRecoveryIntegration:
    """UE-48 + UE-11 integration: simulate the watchdog's full stuck-"""

    def test_stuck_recovery_flow_clears_busy_and_allows_next_dictation(self):
        """End-to-end: a backend gets stuck (busy flag set),"""
        mm, app, config, registry = _make_mm(asr_backend="parakeet", recording=False, busy=False)
        parakeet_engine = MagicMock()
        parakeet_engine.is_loaded = True
        registry.register("parakeet", parakeet_engine)
        fresh_engine = MagicMock()
        fresh_engine.is_loaded = True

        def fake_ensure_engine(backend_name):
            # Mirrors the real ``_ensure_engine`` short-circuit: only
            if registry.get(backend_name) is None:
                registry.register(backend_name, fresh_engine)

        mm._ensure_engine = fake_ensure_engine
        mm.touch_model = MagicMock()

        # Step 1: the transcribe thread enters transcribe_with_fallback
        with registry.busy_context("parakeet"):
            assert registry.is_busy("parakeet") is True

            # Step 2: while the transcription is running, the user
            result = mm.ensure_active_engine_loaded()
            assert result is None
            assert mm._pending_dictation is True

        # Step 3: the transcribe thread is stuck (the busy_context
        registry.set_busy("parakeet")
        assert registry.is_busy("parakeet") is True

        # Step 4: the watchdog's force-recover path fires and calls
        mm.force_unload_active()

        # Step 5: the busy flag is cleared, the next dictation can
        assert registry.is_busy("parakeet") is False, (
            "UE-48 integration: after force_unload_active, the busy flag "
            "must be cleared so the next ensure_active_engine_loaded call "
            "isn't rejected."
        )

        # Step 6: the next ensure_active_engine_loaded call succeeds —
        mm._pending_dictation = False  # reset for the new attempt
        result = mm.ensure_active_engine_loaded()
        assert result is not None, (
            "UE-48 integration: after force_unload_active clears the busy "
            "flag, the next ensure_active_engine_loaded call must succeed "
            "(not be rejected by the busy-check)."
        )
        assert result is fresh_engine, (
            "UE-48 integration: after force_unload_active drops the "
            "registry slot, the next dictation must be served by a FRESH "
            "engine instance, never the ejected stuck one."
        )
        assert result is not parakeet_engine
        assert mm._pending_dictation is False
