"""TY-11: idle-unload timer for the active ASR backend."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from unittest.mock import MagicMock, patch

from voice_typer.server.model_manager import ModelManager


def _make_mm_with_mock_backend(
    *,
    idle_minutes: int = 0,
    is_loaded: bool = True,
    backend_name: str = "parakeet",
) -> tuple[ModelManager, MagicMock, MagicMock, MagicMock]:
    """Construct a ModelManager backed by a mock registry + mock engine."""
    app = MagicMock(name="app")
    app.config.asr_backend = backend_name
    app.config.model_size = "small.en"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app.config.model_idle_unload_minutes = idle_minutes
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()

    mm = ModelManager(app)

    # Mock engine, has ``unload`` and ``is_loaded``.
    engine = MagicMock(name="engine")
    engine.is_loaded = is_loaded
    engine.device_info = f"{backend_name}/cpu"

    mock_registry = MagicMock(name="registry")
    mock_registry.active_name = backend_name
    mock_registry.get_active.return_value = engine
    mock_registry.get.return_value = engine
    mock_registry.load_active.return_value = engine  # truthy → success
    mock_registry.load_with_fallback.return_value = engine
    mock_registry.available_backends = [backend_name]
    mm._registry = mock_registry

    # Stub _ensure_engine so we don't actually try to construct a real
    mm._ensure_engine = MagicMock()
    # Stub _evict_lru_model so it doesn't fire on touch.
    mm._evict_lru_model = MagicMock()

    return mm, app, engine, mock_registry


class TestIdleUnloadZeroDisables:
    """TY-11 constraint #1: ``model_idle_unload_minutes = 0`` MUST"""

    def test_zero_config_never_arms_deadline_or_thread(self):
        """
        When ``model_idle_unload_minutes == 0``, calling
        ``touch_active_model()`` MUST NOT arm any deadline and MUST NOT
        """
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=0)
        assert getattr(mm, "_idle_unload_deadline", None) is None
        assert getattr(mm, "_idle_unload_thread", None) is None
        mm.touch_active_model()
        assert getattr(mm, "_idle_unload_deadline", None) is None, (
            "TY-11: model_idle_unload_minutes=0 must NOT arm the idle-unload deadline (current behaviour preserved)."
        )
        assert getattr(mm, "_idle_unload_thread", None) is None, (
            "the scheduler thread must not be started while the feature is disabled."
        )

    def test_zero_config_does_not_unload_even_after_long_wait(self):
        """Even after a delay, with minutes=0 the engine must remain"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=0)
        mm.touch_active_model()
        time.sleep(0.05)
        engine.unload.assert_not_called()
        mm._registry.unload.assert_not_called()

    def test_default_config_value_is_thirty(self):
        """``model_idle_unload_minutes`` must be 30 minutes."""
        from voice_typer.server.config import Config

        cfg = Config()
        assert cfg.model_idle_unload_minutes == 30, (
            "TY-11: the default value of model_idle_unload_minutes "
            "must be 30 minutes (sensible production default for memory "
            "management, keeps the model warm for short gaps, unloads "
            "for long ones). Users who need always-loaded behaviour "
            "can set it to 0."
        )

    def test_zero_config_setting_to_zero_cancels_existing_deadline(self):
        """If the user changes the config value from N to 0 via IPC,"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=5)
        mm.touch_active_model()
        assert mm._idle_unload_deadline is not None
        # User sets it to 0 via IPC.
        app.config.model_idle_unload_minutes = 0
        mm.touch_active_model()
        assert mm._idle_unload_deadline is None, (
            "TY-11: setting model_idle_unload_minutes back to 0 must "
            "cancel any previously-armed deadline (feature can be "
            "disabled at runtime)."
        )
        engine.unload.assert_not_called()


class TestCancelIdleUnloadTimer:
    """TY-11 constraint #2: the timer must be cancellable when"""

    def test_cancel_no_op_when_nothing_armed(self):
        """``cancel_idle_unload_timer()`` is a no-op when no deadline is"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        # Nothing armed yet, cancel must not raise.
        mm.cancel_idle_unload_timer()
        assert mm._idle_unload_deadline is None

    def test_cancel_armed_deadline_prevents_unload(self):
        """After ``cancel_idle_unload_timer()``, the scheduler must not"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_deadline is not None
        mm.cancel_idle_unload_timer()
        assert mm._idle_unload_deadline is None
        mm._idle_unload_wakeup.set()
        time.sleep(0.2)
        mm._registry.unload.assert_not_called()
        engine.unload.assert_not_called()

    def test_ensure_active_engine_loaded_cancels_timer(self):
        """``ensure_active_engine_loaded()`` (the toggle_dictation"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_deadline is not None
        # Call ensure_active_engine_loaded, should cancel the deadline.
        mm.ensure_active_engine_loaded()
        assert mm._idle_unload_deadline is None, (
            "TY-11: ensure_active_engine_loaded must cancel the "
            "idle-unload deadline so the model isn't unloaded mid-dictation."
        )

    def test_change_model_cancels_timer(self):
        """``change_model()`` must cancel the idle-unload timer before"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_deadline is not None
        app.recorder.recording = False
        app._busy_event = MagicMock()
        app._busy_event.is_set.return_value = True
        app.config.save.return_value = True
        # Stub out the heavy _change_model_load_phase so we don't
        mm._change_model_load_phase = MagicMock()
        mm._change_model_unload_phase = MagicMock()
        with contextlib.suppress(Exception):
            mm.change_model("parakeet")
        # Join the background ModelChange thread so its
        if mm._model_change_thread is not None:
            mm._model_change_thread.join(timeout=5.0)
        assert mm._idle_unload_deadline is None, (
            "TY-11: change_model must cancel the idle-unload deadline before starting the unload/reload cycle."
        )

    def test_set_active_backend_cancels_or_replaces_deadline(self):
        """``set_active_backend()`` must cancel the idle-unload deadline"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1, backend_name="whisper")
        mm._schedule_idle_unload_timer()
        old_deadline = mm._idle_unload_deadline
        assert old_deadline is not None
        # Stub out the heavy load phase.
        app.config.save.return_value = True
        mm._change_model_unload_phase = MagicMock()
        mm._ensure_engine = MagicMock()
        mm._registry.load_active.return_value = engine
        with contextlib.suppress(Exception):
            mm.set_active_backend("parakeet")
        # Join the background BackendChange thread so its
        if mm._backend_change_thread is not None:
            mm._backend_change_thread.join(timeout=5.0)
        assert mm._idle_unload_deadline is not old_deadline, (
            "TY-11: set_active_backend must cancel the OLD idle-unload "
            "deadline before switching backends. The OLD deadline is "
            "still the current one, the cancel did NOT run."
        )
        mm.cancel_idle_unload_timer()


class TestIdleUnloadFiresAndReleasesGpu:
    """TY-11 constraint #3: when the timer fires, the active backend"""

    def test_timer_fire_unloads_active_backend_and_releases_gpu(self):
        """When the timer fires, the registry's ``unload()`` must be"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)

        # ``release_gpu_memory`` is imported lazily inside
        with patch("voice_typer.server.asr_utils.release_gpu_memory") as mock_release:
            mm._do_idle_unload()

            mock_registry.unload.assert_called_once_with("parakeet")
            mock_release.assert_called()

    def test_timer_fire_sets_tray_state_to_idle_unloaded(self):
        """``AppState.IDLE`` with the \"Idle, model unloaded\" message"""
        from voice_typer.server.tray_types import AppState

        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._do_idle_unload()

        # The tray.set_state call must include AppState.IDLE.
        states_called = [c.args[0] if c.args else c.kwargs.get("state") for c in app.tray.set_state.call_args_list]
        assert AppState.IDLE in states_called, (
            f"TY-11: tray.set_state must be called with AppState.IDLE. Got: {states_called}"
        )
        # At least one call must include the "Idle, model unloaded" msg.
        msgs = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", "")) for c in app.tray.set_state.call_args_list
        ]
        assert any("Idle, model unloaded" in (m or "") for m in msgs), (
            f"TY-11: tray.set_state must be called with the 'Idle, model unloaded' message. Got: {msgs}"
        )

    def test_timer_fire_skipped_when_shutting_down(self):
        """If ``app._shutting_down`` is True when the timer fires, the"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        app._shutting_down = True
        mm._do_idle_unload()
        mock_registry.unload.assert_not_called()
        engine.unload.assert_not_called()

    def test_timer_fire_skipped_when_already_unloaded(self):
        """If ``is_loaded`` is already False when the timer fires, the"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1, is_loaded=False)
        mm._do_idle_unload()
        mock_registry.unload.assert_not_called()

    def test_touch_at_expiry_wakes_scheduler_without_unloading(self):
        """expiry from unloading (the deadline-reconfirmation replaces"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        # Arm, scheduler waits ~60s on the deadline.
        mm._schedule_idle_unload_timer()
        old_deadline = mm._idle_unload_deadline
        assert old_deadline is not None
        # Touch again BEFORE the deadline can expire, the scheduler
        time.sleep(0.05)
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_deadline is not old_deadline
        assert mm._idle_unload_deadline > old_deadline
        # must NOT unload: the deadline is in the future again.
        time.sleep(0.3)
        mock_registry.unload.assert_not_called()
        engine.unload.assert_not_called()
        # Cleanup.
        mm.cancel_idle_unload_timer()

    def test_timer_fire_logs_unload_at_info_level(self, caplog):
        """The unload must be logged at INFO level so the user can see"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.model_manager"):
            mm._do_idle_unload()
        info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("[MODEL]" in m and "idle-unload" in m for m in info_msgs), (
            f"TY-11: idle-unload must be logged at INFO level. Got: {info_msgs}"
        )


class TestTouchRearmsDeadline:
    """TY-11: each ``touch_active_model()`` call re-arms the deadline"""

    def test_touch_active_model_arms_deadline_with_correct_delay(self):
        """``touch_active_model()`` with minutes=1 must arm a deadline"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        try:
            before = time.monotonic()
            mm.touch_active_model()
            after = time.monotonic()
            deadline = mm._idle_unload_deadline
            assert deadline is not None
            assert before + 60.0 <= deadline <= after + 60.0, (
                f"TY-11: minutes=1 must produce a 60-second deadline. Got {deadline - before}s after the touch."
            )
            thread = mm._idle_unload_thread
            assert thread is not None and thread.is_alive(), (
                "arming the deadline must start the persistent scheduler thread."
            )
            assert thread.daemon is True, "the scheduler thread must be a daemon (never block process exit)."
        finally:
            mm.cancel_idle_unload_timer()

    def test_second_touch_reuses_thread_and_pushes_deadline_out(self):
        """whole point of the persistent scheduler)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        try:
            mm.touch_active_model()
            first_thread = mm._idle_unload_thread
            first_deadline = mm._idle_unload_deadline
            assert first_thread is not None
            assert first_deadline is not None
            time.sleep(0.05)
            mm.touch_active_model()
            second_thread = mm._idle_unload_thread
            second_deadline = mm._idle_unload_deadline
            assert second_deadline is not None
            assert second_deadline > first_deadline, "TY-11: second touch must push the deadline out (re-arm from NOW)."
            assert second_thread is first_thread, (
                "the scheduler thread must be reused across touches, a per-touch "
                "thread is the churn the persistent scheduler replaced."
            )
        finally:
            mm.cancel_idle_unload_timer()

    def test_thread_reused_across_cancel_and_rearm(self):
        """``cancel_idle_unload_timer`` must PARK the scheduler (not"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        try:
            mm.touch_active_model()
            thread = mm._idle_unload_thread
            assert thread is not None
            mm.cancel_idle_unload_timer()
            assert mm._idle_unload_deadline is None
            assert mm._idle_unload_thread is thread, "cancel must not drop the scheduler thread reference."
            time.sleep(0.05)
            mm.touch_active_model()
            assert mm._idle_unload_thread is thread, (
                "re-arm after cancel must REUSE the parked scheduler thread (no thread churn per dictation cycle)."
            )
        finally:
            mm.cancel_idle_unload_timer()

    def test_inactive_backend_touch_does_not_arm_deadline(self):
        """``touch_model(<inactive backend>)`` must NOT arm the deadline"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1, backend_name="parakeet")
        try:
            mm.touch_model("whisper")  # different from active (parakeet)
            assert getattr(mm, "_idle_unload_deadline", None) is None, (
                "TY-11: touching an inactive backend must not arm the idle-unload deadline."
            )
            assert getattr(mm, "_idle_unload_thread", None) is None
        finally:
            mm.cancel_idle_unload_timer()

    def test_active_backend_touch_arms_deadline(self):
        """``touch_model(<active backend>)`` (called directly, not via"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        try:
            mm.touch_model("parakeet")  # matches active backend
            assert mm._idle_unload_deadline is not None
        finally:
            mm.cancel_idle_unload_timer()


class TestPersistentSchedulerLoop:
    """The single persistent daemon thread owns the firing decision:"""

    def _forge_expired_deadline(self, mm: ModelManager) -> None:
        """Move the armed deadline into the past and wake the scheduler"""
        with mm._idle_unload_lock:
            mm._idle_unload_deadline = time.monotonic() - 0.001
        mm._idle_unload_wakeup.set()

    def test_expiry_fires_idle_unload(self):
        """When the deadline expires, the scheduler thread calls"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        fired = threading.Event()
        unload_calls: list[str] = []
        mm._do_idle_unload = MagicMock(side_effect=lambda: (unload_calls.append("fired"), fired.set()))
        try:
            mm._schedule_idle_unload_timer()  # arms + starts the thread
            assert mm._idle_unload_thread is not None
            self._forge_expired_deadline(mm)
            assert fired.wait(timeout=5.0), (
                "the scheduler thread must call _do_idle_unload within a bounded wait of the expired deadline."
            )
            assert unload_calls == ["fired"]
        finally:
            mm.cancel_idle_unload_timer()

    def test_after_fire_deadline_disarmed_and_next_touch_rearms(self):
        """After the expiry fires, the deadline is disarmed (None) —"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        fired = threading.Event()
        mm._do_idle_unload = MagicMock(side_effect=lambda: fired.set())
        try:
            mm._schedule_idle_unload_timer()
            self._forge_expired_deadline(mm)
            assert fired.wait(timeout=5.0)
            assert mm._idle_unload_deadline is None, (
                "after firing, the deadline must be disarmed so the scheduler parks (no repeat firing)."
            )
            mm.touch_active_model()
            assert mm._idle_unload_deadline is not None
            time.sleep(0.2)
            mm._do_idle_unload.assert_called_once()
        finally:
            mm.cancel_idle_unload_timer()

    def test_scheduler_survives_unload_raising(self):
        """If ``_do_idle_unload`` itself raises, the scheduler thread"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        fired_once = threading.Event()
        raised = threading.Event()

        def _exploding_unload() -> None:
            if fired_once.is_set():
                raised.set()
                return
            fired_once.set()
            raise RuntimeError("boom during unload")

        mm._do_idle_unload = MagicMock(side_effect=_exploding_unload)
        try:
            mm._schedule_idle_unload_timer()
            self._forge_expired_deadline(mm)
            assert fired_once.wait(timeout=5.0)
            # The thread must still be alive after the raise.
            thread = mm._idle_unload_thread
            assert thread is not None
            assert thread.is_alive(), "the scheduler thread must survive an unload exception."
            # Re-arm + a second expiry must still fire (the loop
            mm.touch_active_model()
            self._forge_expired_deadline(mm)
            assert raised.wait(timeout=5.0), "the scheduler must keep firing after surviving an unload exception."
        finally:
            mm.cancel_idle_unload_timer()


class TestReloadAfterIdleUnload:
    """TY-11 constraint #2 + #4: after the idle-unload fires, the next"""

    def test_ensure_active_engine_loaded_reloads_after_idle_unload(self):
        """After the idle-unload fires (is_loaded=False), calling"""
        from voice_typer.server.tray_types import AppState

        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        # Simulate the idle-unload having fired: engine.is_loaded=False.
        engine.is_loaded = False
        mm.ensure_active_engine_loaded()
        mock_registry.load_active.assert_called_once()
        # Tray must transition through LOADING ("Loading model...")
        states = [c.args[0] if c.args else c.kwargs.get("state") for c in app.tray.set_state.call_args_list]
        msgs = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", "")) for c in app.tray.set_state.call_args_list
        ]
        assert AppState.LOADING in states, f"TY-11: reload path must set tray to LOADING. Got: {states}"
        assert any("Loading model..." in (m or "") for m in msgs), (
            f"TY-11: reload path must show 'Loading model...' message. Got: {msgs}"
        )
        assert any("Ready" in (m or "") for m in msgs), (
            f"TY-11: reload path must end with 'Ready | ...' message. Got: {msgs}"
        )

    def test_reload_after_idle_unload_rearms_deadline(self):
        """After the reload, the idle-unload deadline must be re-armed"""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        engine.is_loaded = False
        try:
            mm.ensure_active_engine_loaded()
            # After reload, touch_model is called → deadline re-armed.
            assert mm._idle_unload_deadline is not None, (
                "TY-11: after reload, the idle-unload deadline must be re-armed."
            )
        finally:
            mm.cancel_idle_unload_timer()

    def test_reload_failure_does_not_raise(self):
        """If the reload fails, ``ensure_active_engine_loaded`` must"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        engine.is_loaded = False
        mock_registry.load_active.return_value = None  # falsy → fail
        # Must not raise.
        mm.ensure_active_engine_loaded()
        # The reload attempt must have been made.
        mock_registry.load_active.assert_called_once()

    def test_reload_uses_progress_callback(self):
        """``load_active`` so the tray shows progress messages during"""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        engine.is_loaded = False
        mm.ensure_active_engine_loaded()
        call_kwargs = mock_registry.load_active.call_args.kwargs
        assert "progress_callback" in call_kwargs, (
            "TY-11: reload path must pass a progress_callback to load_active so the tray shows progress during reload."
        )
        assert callable(call_kwargs["progress_callback"])


class TestConfigField:
    """TY-11: the new ``model_idle_unload_minutes`` config field must"""

    def test_field_exists_on_config_dataclass(self):
        """``Config()`` must have a ``model_idle_unload_minutes``"""
        from voice_typer.server.config import Config

        cfg = Config()
        assert hasattr(cfg, "model_idle_unload_minutes")
        assert isinstance(cfg.model_idle_unload_minutes, int)
        assert cfg.model_idle_unload_minutes == 30

    def test_field_in_int_fields_coercion_set(self):
        """The field must be in the ``int_fields`` set inside"""
        from voice_typer.server.config import Config

        # Load a config dict with a string value, must be coerced to int.
        data = {"model_idle_unload_minutes": "15"}
        validated = Config._validate_non_numeric_fields(data)
        assert validated["model_idle_unload_minutes"] == 15
        assert isinstance(validated["model_idle_unload_minutes"], int)

    def test_field_in_ipc_allowlist(self):
        """The field must be in ``IPC_CONFIG_ALLOWLIST`` so the"""
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        assert "model_idle_unload_minutes" in IPC_CONFIG_ALLOWLIST, (
            "TY-11: model_idle_unload_minutes must be in IPC_CONFIG_ALLOWLIST so the renderer can change it via IPC."
        )

    def test_ipc_allowlist_validates_int_range(self):
        """The IPC validator must reject negative values and values"""
        from voice_typer.server.config_validators import validate_config_update

        # Negative rejected.
        validated, errors = validate_config_update({"model_idle_unload_minutes": -1})
        assert "model_idle_unload_minutes" not in validated
        assert errors  # some error was reported

        # 0 accepted (disable sentinel).
        validated, errors = validate_config_update({"model_idle_unload_minutes": 0})
        assert validated.get("model_idle_unload_minutes") == 0
        assert not errors

        # 15 accepted (typical value).
        validated, errors = validate_config_update({"model_idle_unload_minutes": 15})
        assert validated.get("model_idle_unload_minutes") == 15
        assert not errors

        # 1440 accepted (24 h, upper bound).
        validated, errors = validate_config_update({"model_idle_unload_minutes": 1440})
        assert validated.get("model_idle_unload_minutes") == 1440
        assert not errors

        # 1441 rejected (above 24 h).
        validated, errors = validate_config_update({"model_idle_unload_minutes": 1441})
        assert "model_idle_unload_minutes" not in validated
        assert errors
