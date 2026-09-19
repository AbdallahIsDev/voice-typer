"""regression test: ``available_backends`` @property must NOT be"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from voice_typer.server.model_manager import ModelManager


def _make_mm_with_failing_registry() -> tuple[ModelManager, MagicMock]:
    """Construct a ModelManager whose registry's"""
    # Build a minimal mock app with the attributes ModelManager.__init__
    app = MagicMock(name="app")
    app.config.asr_backend = "whisper"
    app.config.model_size = "tiny"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.hotkey = "<f2>"
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()

    mm = ModelManager(app)

    # Replace the registry with a mock whose ``load_with_fallback``
    mock_registry = MagicMock(name="registry")
    mock_registry.load_with_fallback.return_value = None  # falsy → fail path
    mock_registry.available_backends = ["whisper", "parakeet"]
    mock_registry.active_name = "whisper"
    mock_registry.get_active.return_value = None
    mm._registry = mock_registry

    # Stub _ensure_engine so we don't actually try to construct a real
    mm._ensure_engine = MagicMock()
    # Stub touch_model + _evict_lru_model so they don't touch LRU state.
    mm.touch_model = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app


class TestAvailableBackendsPropertyNoParens:
    """``available_backends`` is a @property, must be accessed"""

    def test_source_does_not_call_available_backends_with_parens(self):
        """Source guard: ``load_background`` must NOT call"""
        import inspect

        src = inspect.getsource(ModelManager.load_background)
        assert "available_backends()" not in src, (
            "regression: load_background calls "
            "self._registry.available_backends() with parens, but "
            "available_backends is a @property. Calling it with parens "
            "raises TypeError: 'list' object is not callable, masking "
            "the diagnostic log.warning on the all-backends-fail path."
        )
        # The fixed form: property access without parens.
        assert "available_backends" in src, (
            "load_background must access "
            "self._registry.available_backends (no parens) to list "
            "attempted backends in the diagnostic log.warning."
        )

    def test_all_backends_fail_emits_warning_with_backend_names(self, caplog):
        """End-to-end: when ``load_with_fallback`` returns falsy (all"""
        mm, app = _make_mm_with_failing_registry()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.model_manager"):
            mm.load_background()

        diagnostic_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "All backends failed to load" in r.getMessage()
        ]
        assert diagnostic_warnings, (
            "when all backends fail to load, load_background must "
            "emit a log.warning listing the attempted backends + primary. "
            "Pre-fix, this warning was masked by a TypeError raised when "
            "calling available_backends() (a @property) with parens."
        )
        warning_msg = diagnostic_warnings[0].getMessage()
        assert "whisper" in warning_msg and "parakeet" in warning_msg, (
            f"the diagnostic log.warning must list the attempted backends (whisper, parakeet). Got: {warning_msg!r}"
        )
        # The primary backend name must also be present.
        assert "primary=whisper" in warning_msg, (
            f"the diagnostic log.warning must include the primary backend name. Got: {warning_msg!r}"
        )

    def test_all_backends_fail_does_not_raise_typeerror(self, caplog):
        """the all-backends-fail path must NOT raise"""
        mm, app = _make_mm_with_failing_registry()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.model_manager"):
            mm.load_background()

        crashed_logs = [r for r in caplog.records if "Background model load crashed" in r.getMessage()]
        assert not crashed_logs, (
            "load_background's outer ``except Exception`` caught "
            "an exception (logged as 'Background model load crashed'). "
            "Pre-fix, this was a TypeError from calling available_backends() "
            "with parens. The fixed code must reach the diagnostic "
            "log.warning without raising."
        )


# These tests exercise the constructor wiring and the high-level


def _make_mm_with_mock_registry():
    """Build a ModelManager via the real ``__init__`` with a MagicMock"""
    import threading

    app = MagicMock(name="app")
    app.config.asr_backend = "whisper"
    app.config.model_size = "tiny"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.hotkey = "<f2>"
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    # ``_config_mutation_lock`` is acquired as a context manager by
    app._config_mutation_lock = threading.RLock()

    mm = ModelManager(app)

    # Swap the real registry for a mock, but keep its ``available_backends``
    mock_registry = MagicMock(name="registry")
    mock_registry.available_backends = ["whisper", "parakeet"]
    mock_registry.active_name = "whisper"
    mock_registry.get_active.return_value = None
    mm._registry = mock_registry

    # Stub the LRU-touch + eviction helpers so the load paths don't
    mm.touch_model = MagicMock()
    mm._evict_lru_model = MagicMock()

    return mm, app


class TestInitWiring:
    """``__new__`` so this wiring was never asserted."""

    def test_init_creates_registry_and_locks(self):
        """four locks (``_model_change_lock`` is an RLock so"""
        from unittest.mock import MagicMock

        from voice_typer.server.asr_registry import AsrBackendRegistry

        app = MagicMock()
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        app.config.device = "cpu"
        app.config.language = "en"
        app.config.beam_size = 1
        app.config.best_of = 1
        app.config.condition_on_previous_text = False

        mm = ModelManager(app)

        # Registry is constructed eagerly (not lazy).
        assert isinstance(mm._registry, AsrBackendRegistry), "__init__ must construct an AsrBackendRegistry eagerly"
        assert hasattr(mm._model_change_lock, "_is_owned"), "_model_change_lock must be an RLock (re-entrant)"
        # _model_lru_lock is a plain Lock (no re-entrancy needed).
        assert not hasattr(mm._model_lru_lock, "_is_owned"), "_model_lru_lock must be a plain Lock"
        assert hasattr(mm, "_lazy_init_lock"), "__init__ must set _lazy_init_lock (LAZY-INIT-LOCK-FIX)"

    def test_init_initializes_lru_and_pending_state(self):
        """pending-state fields so the first ``change_model`` /"""
        from unittest.mock import MagicMock

        app = MagicMock()
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        app.config.device = "cpu"
        app.config.language = "en"
        app.config.beam_size = 1
        app.config.best_of = 1
        app.config.condition_on_previous_text = False

        mm = ModelManager(app)

        assert mm._model_access_times == {}, "__init__ must start with an empty LRU tracking dict"
        assert mm._model_load_thread is None
        assert mm._model_load_attempted is False
        assert mm._pending_dictation is False
        assert mm._pending_model_change is None
        assert mm._pending_backend_change is None


class TestFallbackToWhisper:
    """registry, and update tray state, OR refuse with \"no model"""

    def test_success_path_switches_to_installed_model_and_sets_idle_tray(self):
        from voice_typer.server.tray_types import AppState

        mm, app = _make_mm_with_mock_registry()
        # An installed model is found → fall back to it (not hardcoded tiny).
        mm._find_installed_model = MagicMock(return_value=("whisper", "large-v3"))
        mm._registry.load_with_fallback.return_value = MagicMock(name="active")

        mm.fallback_to_whisper(notify_on_failure=False)

        # Config mutated to the INSTALLED model and persisted.
        assert app.config.asr_backend == "whisper"
        assert app.config.model_size == "large-v3"
        app.config.save.assert_called_once()
        assert mm._registry.load_with_fallback.called
        # LRU touched + eviction considered on success.
        mm.touch_model.assert_called_once()
        mm._evict_lru_model.assert_called_once()
        # Tray transitioned to IDLE on success.
        tray_states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert AppState.IDLE in tray_states, f"fallback_to_whisper success must set tray to IDLE; got {tray_states}"

    def test_failure_path_sets_error_tray_and_notifies(self):
        from voice_typer.server.tray_types import AppState

        mm, app = _make_mm_with_mock_registry()
        # An installed model is found, but its load fails.
        mm._find_installed_model = MagicMock(return_value=("whisper", "large-v3"))
        mm._registry.load_with_fallback.return_value = None

        mm.fallback_to_whisper(notify_on_failure=True)

        # Tray transitioned to ERROR on failure.
        tray_states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert AppState.ERROR in tray_states, f"fallback_to_whisper failure must set tray to ERROR; got {tray_states}"
        app.tray.notify_safety.assert_called_once()

    def test_no_installed_model_refuses_with_not_downloaded(self):
        """Nothing on disk → refuse with the 'open Models' error; do NOT"""
        from voice_typer.server.tray_types import AppState

        mm, app = _make_mm_with_mock_registry()
        mm._find_installed_model = MagicMock(return_value=None)
        mm._notify_model_load_refused = MagicMock()

        mm.fallback_to_whisper(notify_on_failure=True)

        mm._notify_model_load_refused.assert_called_once()
        mm._registry.load_with_fallback.assert_not_called()
        tray_states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert AppState.IDLE not in tray_states


class TestChangeModelBlocking:
    """``asr_backend_ready`` event on completion."""

    def test_blocking_change_runs_unload_then_load_and_publishes(self):
        mm, app = _make_mm_with_mock_registry()
        # Not recording, not busy -> change executes immediately (not deferred).
        app.recorder.recording = False
        app._busy_event.is_set.return_value = True  # not busy
        app.config.save.return_value = True
        # Load succeeds.
        mm._registry.load_active.return_value = True
        active = MagicMock(name="active-engine")
        active.device_info = "cpu"
        mm._registry.get_active.return_value = active
        # Stub _ensure_engine so it doesn't actually construct a backend.
        mm._ensure_engine = MagicMock()
        # Stub the event publish so we can assert it ran.
        mm._publish_backend_ready_event = MagicMock()
        # Stub cancel_idle_unload_timer (called at the top).
        mm.cancel_idle_unload_timer = MagicMock()

        mm._change_model_blocking("parakeet")

        assert app.config.asr_backend == "parakeet"
        assert app.config.model_size == "parakeet"
        app.config.save.assert_called_once()
        mm._registry.unload.assert_called_once_with("whisper")
        unregister_calls = [c.args[0] for c in mm._registry.unregister.call_args_list]
        assert unregister_calls == ["whisper"], (
            f"Expected unregister('whisper') once (direct only, the legacy "
            f"setter-call branch was dead code, removed); got {unregister_calls}"
        )
        mm._ensure_engine.assert_called_once_with("parakeet")
        mm._registry.load_active.assert_called_once()
        # LRU touched for the new backend, eviction considered.
        mm.touch_model.assert_called_once_with("parakeet")
        mm._evict_lru_model.assert_called_once()
        # Event published with the new backend + model_size.
        mm._publish_backend_ready_event.assert_called_once_with("parakeet", "parakeet")

    def test_blocking_change_defers_when_recording(self):
        """When a recording is in progress, ``_change_model_setattr_phase``"""
        mm, app = _make_mm_with_mock_registry()
        app.recorder.recording = True  # recording in progress
        app._busy_event.is_set.return_value = True
        app.config.save.return_value = True

        mm._ensure_engine = MagicMock()
        mm._registry.load_active = MagicMock()
        mm._publish_backend_ready_event = MagicMock()
        mm.cancel_idle_unload_timer = MagicMock()

        mm._change_model_blocking("qwen")

        # Config still mutated + saved (so the next boot reflects the request).
        assert app.config.asr_backend == "qwen"
        app.config.save.assert_called_once()
        # But load phase skipped, _ensure_engine NOT called.
        mm._ensure_engine.assert_not_called()
        mm._registry.load_active.assert_not_called()
        # Pending change captured.
        assert mm._pending_model_change == "qwen"
        # Event NOT published (load didn't happen).
        mm._publish_backend_ready_event.assert_not_called()


class TestChangeModelAckShape:
    """``change_model`` (the IPC entry point) must return an ack dict"""

    def test_returns_loading_ack_with_previous_and_pending(self):
        mm, app = _make_mm_with_mock_registry()
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny"
        mm.cancel_idle_unload_timer = MagicMock()

        mm._change_model_background = MagicMock()

        ack = mm.change_model("parakeet")

        assert ack["status"] == "loading"
        assert ack["previous"] == {"backend": "whisper", "model_size": "tiny"}
        assert ack["pending"] == {"backend": "parakeet", "model_size": "parakeet"}
        # Background spawn invoked exactly once.
        mm._change_model_background.assert_called_once_with("parakeet")

    def test_change_model_size_routing(self):
        """``change_model`` routes ``model_size`` to a backend name:"""
        mm, app = _make_mm_with_mock_registry()
        mm.cancel_idle_unload_timer = MagicMock()
        mm._change_model_background = MagicMock()

        # parakeet
        ack = mm.change_model("parakeet")
        assert ack["pending"]["backend"] == "parakeet"
        # qwen
        ack = mm.change_model("qwen")
        assert ack["pending"]["backend"] == "qwen"
        ack = mm.change_model("base.en")
        assert ack["pending"]["backend"] == "whisper"
        assert ack["pending"]["model_size"] == "base.en"


class TestChangeModelNoop:
    """Re-selecting the already-LOADED model must not unload + fully"""

    def test_same_loaded_model_returns_ready_without_spawn(self):
        mm, app = _make_mm_with_mock_registry()
        mm.cancel_idle_unload_timer = MagicMock()
        mm._change_model_background = MagicMock()
        # Fixture: config tiny + mock engine truthy-loaded.
        engine = mm._registry.get("whisper")
        engine.is_loaded = True

        ack = mm.change_model("tiny")

        assert ack["status"] == "ready"
        assert ack["previous"] == {"backend": "whisper", "model_size": "tiny"}
        assert ack["pending"] == {"backend": "whisper", "model_size": "tiny"}
        mm._change_model_background.assert_not_called()

    def test_same_size_but_unloaded_proceeds(self):
        """Same size with NO loaded engine is a legitimate retry (e.g."""
        mm, app = _make_mm_with_mock_registry()
        mm.cancel_idle_unload_timer = MagicMock()
        mm._change_model_background = MagicMock()
        mm._registry.get.return_value = None

        ack = mm.change_model("tiny")

        assert ack["status"] == "loading"
        mm._change_model_background.assert_called_once_with("tiny")

    def test_different_size_proceeds_despite_loaded_engine(self):
        """A different size on the same backend still reloads (fresh"""
        mm, app = _make_mm_with_mock_registry()
        mm.cancel_idle_unload_timer = MagicMock()
        mm._change_model_background = MagicMock()
        engine = mm._registry.get("whisper")
        engine.is_loaded = True

        ack = mm.change_model("base.en")

        assert ack["status"] == "loading"
        mm._change_model_background.assert_called_once_with("base.en")


class TestLRUEviction:
    """``_evict_lru_model`` must unload the oldest backend when more than"""

    def test_no_eviction_when_at_or_below_max(self):
        """When ``len(_model_access_times) <= _MAX_LOADED_MODELS``,"""
        import time
        from unittest.mock import MagicMock

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model
        # Exactly _MAX_LOADED_MODELS entries -> no eviction.
        mm._model_access_times = {
            "whisper": time.monotonic(),
            "parakeet": time.monotonic(),
        }
        mm._registry.get = MagicMock(return_value=MagicMock())

        mm._evict_lru_model()

        mm._registry.get.assert_not_called()

    def test_evicts_oldest_backend_when_over_max(self):
        """entry with the OLDEST timestamp is unloaded + unregistered +"""
        import time
        from unittest.mock import MagicMock

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model  # restore real method (helper stubs it)
        # Three entries, whisper is the oldest (timestamp in the past).
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100.0,  # oldest
            "parakeet": now - 10.0,
            "qwen": now,
        }
        mm._registry.get = MagicMock(return_value=MagicMock(name="oldest-engine"))

        mm._evict_lru_model()

        # Oldest backend was unloaded + unregistered via the registry.
        mm._registry.unload.assert_called_once_with("whisper")
        mm._registry.unregister.assert_called_once_with("whisper")
        # Oldest backend removed from tracking.
        assert "whisper" not in mm._model_access_times
        assert "parakeet" in mm._model_access_times
        assert "qwen" in mm._model_access_times

    def test_eviction_respects_busy_check(self):
        """If the registry reports the oldest backend as busy"""
        import time

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model  # restore real method
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100.0,  # oldest, but busy
            "parakeet": now - 10.0,
            "qwen": now,
        }
        mm._registry.unload.side_effect = RuntimeError("cannot unload busy backend: whisper")

        mm._evict_lru_model()

        mm._registry.unload.assert_called_once_with("whisper")
        mm._registry.unregister.assert_not_called()
        # Tracking dict is unchanged, eviction was skipped, NOT
        assert "whisper" in mm._model_access_times
        assert "parakeet" in mm._model_access_times
        assert "qwen" in mm._model_access_times

    def test_eviction_survives_engine_without_unload_method(self):
        """Eviction must not raise even if ``registry.unload`` /"""
        import time

        mm, _app = _make_mm_with_mock_registry()
        del mm._evict_lru_model  # restore real method (helper stubs it)
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100.0,
            "parakeet": now - 10.0,
            "qwen": now,
        }
        # unload() raised ValueError). Eviction must NOT raise, it
        mm._registry.unload.side_effect = ValueError("backend unload crashed")

        # Must NOT raise.
        mm._evict_lru_model()

        assert "whisper" not in mm._model_access_times


class TestLRUModelEviction:
    """PERF-015: Verify LRU model eviction in ModelManager."""

    def test_evict_method_exists(self):
        """ModelManager should have _evict_lru_model and touch_model methods."""
        from voice_typer.server.model_manager import ModelManager

        assert hasattr(ModelManager, "_evict_lru_model")
        assert hasattr(ModelManager, "touch_model")

    def test_no_eviction_below_limit(self):
        """Eviction should not happen when models <= _MAX_LOADED_MODELS."""
        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        mm._model_access_times = {"whisper": 1.0, "qwen": 2.0}
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)
        mm._MAX_LOADED_MODELS = 2
        mm._registry = MagicMock()
        # Should not try to unload anything
        mm._evict_lru_model()
        mm._registry.get.assert_not_called()

    def test_eviction_unloads_oldest(self):
        """Eviction should unload the least recently used model."""
        import time

        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100,  # oldest
            "qwen": now - 10,
            "parakeet": now,
        }
        mm._MAX_LOADED_MODELS = 2
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)

        mm._registry = MagicMock()

        mm._evict_lru_model()
        # Should have unloaded the oldest (whisper) through the registry.
        mm._registry.unload.assert_called_once_with("whisper")
        # Should have removed the oldest from access times
        assert "whisper" not in mm._model_access_times

    def test_touch_updates_timestamp(self):
        """touch_model should update the access timestamp."""
        import time

        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        mm._model_access_times = {}
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)

        before = time.monotonic()
        mm.touch_model("whisper")
        after = time.monotonic()

        assert "whisper" in mm._model_access_times
        assert before <= mm._model_access_times["whisper"] <= after
