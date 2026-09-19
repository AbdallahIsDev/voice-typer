"""Forced-recovery engine ejection: regression tests."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.model_manager import ModelManager
from voice_typer.server.recording_controller import RecordingController
from voice_typer.server.transcription_watchdog import TranscriptionWatchdog


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
        self.hotkey = "f2"
        self.save_calls: list[bool] = []

    def save(self) -> bool:
        self.save_calls.append(True)
        return True


class _FakeEngine:
    """Fake ASR backend: records unload calls; transcribable."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.is_loaded = True
        self.unload_calls = 0
        self.transcribe_fn: Callable[..., str] | None = None

    def unload(self) -> None:
        self.unload_calls += 1

    def transcribe_with_fallback(self, audio: bytes, *args: object, **kwargs: object) -> str:
        if self.transcribe_fn is not None:
            return self.transcribe_fn(audio, *args, **kwargs)
        return f"transcribed-by-{self.label}"


def _make_registry_and_manager(
    asr_backend: str,
) -> tuple[ModelManager, MagicMock, _Config, AsrBackendRegistry]:
    """Build ``(mm, app_mock, config, registry)`` wired like production."""
    config = _Config(asr_backend=asr_backend)
    registry = AsrBackendRegistry(config)

    app = MagicMock(name="app")
    app.config = config
    app.recorder.recording = False
    app._busy_event = threading.Event()
    app._busy_event.set()  # is_set() == True means NOT busy
    # Production resets via the coordinator (``_busyness.set_idle()``),
    app._busyness.set_idle.side_effect = lambda: app._busy_event.set()
    app._busyness.set_busy.side_effect = lambda: app._busy_event.clear()
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
    mm.touch_model = MagicMock()

    # Wire the manager onto the mock app exactly like VoiceTyperApp does
    app.models = mm

    return mm, app, config, registry


def _make_controller(app: MagicMock) -> RecordingController:
    """established test pattern for the extracted helper modules)."""
    controller = RecordingController.__new__(RecordingController)
    controller._app = app
    controller._transcription_thread = None
    controller._watchdog_lock = threading.Lock()
    controller._watchdog_thread = None
    controller._watchdog_event = threading.Event()
    controller._watchdog_stop_event = threading.Event()
    controller._watchdog_firings = 0
    controller._watchdog_max_firings = 3
    controller._cancelled_cycle_ids_lock = threading.Lock()
    controller._cancelled_cycle_ids = OrderedDict()
    controller._current_audio = None
    controller._stop_watchdog_thread = lambda: None
    controller._cancel_streaming_session = lambda: None
    return controller


class _HungTranscribe:
    """Monkeypatched slow callable: blocks until released, then returns."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, audio: bytes, *args: object, **kwargs: object) -> str:
        self.started.set()
        self.release.wait(timeout=10.0)
        return "late-result"


def _start_hung_worker(registry: AsrBackendRegistry, hung: _HungTranscribe, engine: _FakeEngine):
    """Run ``registry.transcribe_with_fallback`` on a worker thread"""
    original_transcribe_fn = engine.transcribe_fn
    engine.transcribe_fn = hung
    worker_done = threading.Event()

    def run() -> None:
        try:
            registry.transcribe_with_fallback(b"audio-bytes")
        finally:
            worker_done.set()

    worker = threading.Thread(target=run, name="stuck-transcription-worker", daemon=True)
    worker.start()
    assert hung.started.wait(timeout=5.0), "worker never entered the hung transcribe call"

    def finish() -> None:
        hung.release.set()
        worker.join(timeout=5.0)
        engine.transcribe_fn = original_transcribe_fn

    return finish, worker


def _fake_ensure_engine_factory(registry: AsrBackendRegistry, fresh_engine: _FakeEngine):
    """heavy imports: registers ``fresh_engine`` only when the slot is"""

    def fake_ensure_engine(backend_name: str) -> None:
        if registry.get(backend_name) is None:
            registry.register(backend_name, fresh_engine)

    return fake_ensure_engine


class TestForcedRecoveryEjectsEngine:
    """The core regression: forced recovery must fence the SAME-engine"""

    def test_forced_recovery_drops_stuck_engine_and_next_dictation_is_fresh(self):
        mm, app, config, registry = _make_registry_and_manager("whisper")
        stuck_engine = _FakeEngine("stuck")
        registry.register("whisper", stuck_engine)

        # Worker thread enters the (hung) transcribe call via the real
        hung = _HungTranscribe()
        finish_worker, worker = _start_hung_worker(registry, hung, stuck_engine)
        try:
            assert registry.is_busy("whisper") is True
            assert registry.get("whisper") is stuck_engine

            app._busy_event.clear()  # busy = True so force_recover proceeds
            controller = _make_controller(app)
            controller._transcription_thread = worker
            watchdog = TranscriptionWatchdog()

            watchdog.force_recover(controller, force=True)

            assert registry.get("whisper") is None, (
                "forced recovery must eject the stuck backend from the "
                "registry so the next dictation constructs a fresh engine"
            )
            # The engine object itself was NOT destroyed (the stuck
            assert stuck_engine.unload_calls == 0, (
                "forced recovery must not destroy the engine object while "
                "a worker thread may still be inside its C-level call"
            )
            # The busy flag was cleared so the next dictation isn't queued.
            assert registry.is_busy("whisper") is False
            assert app._busy_event.is_set() is True  # app no longer busy

            # The NEXT dictation constructs and receives a FRESH engine
            fresh_engine = _FakeEngine("fresh")
            mm._ensure_engine = _fake_ensure_engine_factory(registry, fresh_engine)
            result = mm.ensure_active_engine_loaded()
            assert result is fresh_engine, (
                "the dictation after a forced recovery must be served by a "
                "fresh engine instance, not the ejected stuck one"
            )
            assert result is not stuck_engine
        finally:
            finish_worker()

    def test_non_forced_recovery_reuses_warm_instance_unchanged(self):
        """the picture, the next dictation gets the SAME registered engine"""
        mm, app, config, registry = _make_registry_and_manager("whisper")
        warm_engine = _FakeEngine("warm")
        registry.register("whisper", warm_engine)
        mm._ensure_engine = _fake_ensure_engine_factory(registry, _FakeEngine("would-be-fresh"))

        result = mm.ensure_active_engine_loaded()

        assert result is warm_engine, (
            "normal (non-forced) operation must keep reusing the warm engine instance unchanged"
        )


class TestForcedRecoveryGating:
    """Ejection fires ONLY when a worker thread is genuinely alive."""

    def test_forced_recovery_after_worker_exit_keeps_warm_instance(self):
        """A forced recovery that raced past an already-exited worker"""
        mm, app, config, registry = _make_registry_and_manager("whisper")
        warm_engine = _FakeEngine("warm")
        registry.register("whisper", warm_engine)

        app._busy_event.clear()  # busy = True so force_recover proceeds
        controller = _make_controller(app)
        # No live transcription thread.
        controller._transcription_thread = None

        TranscriptionWatchdog().force_recover(controller, force=True)

        assert registry.get("whisper") is warm_engine, (
            "forced recovery with no live worker must leave the warm engine registered"
        )
        assert warm_engine.unload_calls == 0
        # Recovery state reset still happened.
        assert app._busy_event.is_set() is True

    def test_non_forced_watchdog_firing_leaves_stuck_backend_registered(self):
        """Below the force threshold with the worker alive, the watchdog"""
        mm, app, config, registry = _make_registry_and_manager("whisper")
        stuck_engine = _FakeEngine("stuck")
        registry.register("whisper", stuck_engine)

        hung = _HungTranscribe()
        finish_worker, _worker = _start_hung_worker(registry, hung, stuck_engine)
        try:
            app._busy_event.clear()  # busy = True
            controller = _make_controller(app)
            controller._transcription_thread = threading.current_thread()

            TranscriptionWatchdog().force_recover(controller, force=False)

            assert registry.get("whisper") is stuck_engine
            assert registry.is_busy("whisper") is True
            assert app._busy_event.is_set() is False  # still busy, no reset
            assert stuck_engine.unload_calls == 0
        finally:
            finish_worker()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
