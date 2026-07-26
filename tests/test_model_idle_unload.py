"""TY-11: idle-unload timer for the active ASR backend.

After ``config.model_idle_unload_minutes`` minutes with no
``touch_active_model()`` call (i.e. no dictation activity),
``ModelManager`` unloads the active backend + calls
``release_gpu_memory()`` so the ~2.4 GB of VRAM held by Parakeet (or
the Whisper weights / CUDA caching allocator blocks) is returned to
the OS. The model is reloaded on the next ``toggle_dictation`` via
:meth:`ModelManager.ensure_active_engine_loaded`'s reload-after-unload
path.

Constraint summary (from the fix sub-agent task description):

1. ``model_idle_unload_minutes = 0`` MUST preserve current behaviour
   — no idle unload by default.
2. The idle-unload timer must be cancellable — when toggle_dictation
   is pressed, the timer must be cancelled and the model reloaded.
   Race conditions handled via a lock.
3. ``release_gpu_memory()`` must be called after ``unload()``.
4. Tray state transition: "Idle — model unloaded" → "Loading
   model..." → "Ready" (emit via the existing ``set_state``
   mechanism — do NOT touch tray.py).

These tests mock the heavy torch / transformers / huggingface_hub
dependencies (mirroring ``tests/test_model_manager.py``) so they run
headless on the Linux sandbox. The actual VRAM release can ONLY be
verified on a real CUDA host — see VALIDATE ON CUDA HOST in the fix
report.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.model_manager import ModelManager


def _make_mm_with_mock_backend(
    *,
    idle_minutes: int = 0,
    is_loaded: bool = True,
    backend_name: str = "parakeet",
) -> tuple[ModelManager, MagicMock, MagicMock, MagicMock]:
    """Construct a ModelManager backed by a mock registry + mock engine.

    Parameters
    ----------
    idle_minutes :
        Value of ``app.config.model_idle_unload_minutes``.
    is_loaded :
        Initial value of the mock engine's ``is_loaded`` flag.
    backend_name :
        Name of the active backend (default ``"parakeet"``).

    Returns
    -------
    (mm, app, engine, mock_registry)
        The constructed ModelManager, the mock app, the mock engine,
        and the mock registry (replaced on ``mm._registry``) so tests
        can assert on registry-level calls.
    """
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

    # Mock engine — has ``unload`` and ``is_loaded``.
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

    # Stub _ensure_engine + _sync_registry_from_fields so we don't
    # actually try to construct a real ParakeetEngine.
    mm._ensure_engine = MagicMock()
    mm._sync_registry_from_fields = MagicMock()
    # Stub _evict_lru_model so it doesn't fire on touch.
    mm._evict_lru_model = MagicMock()

    return mm, app, engine, mock_registry


# ─── Constraint #1: model_idle_unload_minutes = 0 preserves current behaviour ──


class TestIdleUnloadDisabledByDefault:
    """TY-11 constraint #1: ``model_idle_unload_minutes = 0`` MUST
    preserve current behaviour — no idle unload by default.

    The default config value is 0; users with abundant VRAM can leave
    it at 0; users who want the feature set it to e.g. 10 or 15."""

    def test_zero_config_never_starts_timer(self):
        """When ``model_idle_unload_minutes == 0``, calling
        ``touch_active_model()`` MUST NOT arm any timer."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=0)
        assert mm._idle_unload_timer is None
        mm.touch_active_model()
        assert mm._idle_unload_timer is None, (
            "TY-11: model_idle_unload_minutes=0 must NOT arm the "
            "idle-unload timer (current behaviour preserved)."
        )

    def test_zero_config_does_not_unload_even_after_long_wait(self):
        """Even after a delay, with minutes=0 the engine must remain
        loaded (no unload happens)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=0)
        mm.touch_active_model()
        time.sleep(0.05)
        engine.unload.assert_not_called()
        mm._registry.unload.assert_not_called()

    def test_default_config_value_is_zero(self):
        """The Config dataclass default for
        ``model_idle_unload_minutes`` must be 0 (disabled)."""
        from voice_typer.server.config import Config

        cfg = Config()
        assert cfg.model_idle_unload_minutes == 0, (
            "TY-11: the default value of model_idle_unload_minutes "
            "must be 0 (disabled) to preserve current behaviour. Users "
            "who want the feature set it to e.g. 10 or 15."
        )

    def test_zero_config_setting_to_zero_cancels_existing_timer(self):
        """If the user changes the config value from N to 0 via IPC,
        any previously-armed timer must be cancelled (the next
        ``touch_active_model`` sees minutes=0 and cancels)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=5)
        mm.touch_active_model()
        assert mm._idle_unload_timer is not None
        # User sets it to 0 via IPC.
        app.config.model_idle_unload_minutes = 0
        mm.touch_active_model()
        assert mm._idle_unload_timer is None, (
            "TY-11: setting model_idle_unload_minutes back to 0 must "
            "cancel any previously-armed timer (feature can be "
            "disabled at runtime)."
        )


# ─── Constraint #2: timer must be cancellable ───────────────────────────


class TestCancelIdleUnloadTimer:
    """TY-11 constraint #2: the timer must be cancellable when
    ``toggle_dictation`` is pressed (or any model-mutation path is
    entered)."""

    def test_cancel_no_op_when_no_timer_armed(self):
        """``cancel_idle_unload_timer()`` is a no-op when no timer is
        armed (safe to call from any path)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        # No timer armed yet — cancel must not raise.
        mm.cancel_idle_unload_timer()
        assert mm._idle_unload_timer is None

    def test_cancel_armed_timer_prevents_unload(self):
        """After ``cancel_idle_unload_timer()``, the timer's callback
        (even if it somehow runs in a race window) must abort without
        unloading."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._schedule_idle_unload_timer()
        timer = mm._idle_unload_timer
        assert timer is not None
        mm.cancel_idle_unload_timer()
        assert mm._idle_unload_timer is None
        # Even if the timer's callback runs (race window), it must
        # abort because _idle_unload_timer is None.
        timer.function()
        mm._registry.unload.assert_not_called()
        engine.unload.assert_not_called()

    def test_ensure_active_engine_loaded_cancels_timer(self):
        """``ensure_active_engine_loaded()`` (the toggle_dictation
        path) must cancel the idle-unload timer before doing anything
        else, so the model isn't unloaded mid-dictation."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_timer is not None
        # Call ensure_active_engine_loaded — should cancel the timer.
        # The mock engine has is_loaded=True, so no reload happens.
        mm.ensure_active_engine_loaded()
        assert mm._idle_unload_timer is None, (
            "TY-11: ensure_active_engine_loaded must cancel the "
            "idle-unload timer so the model isn't unloaded mid-dictation."
        )

    def test_change_model_cancels_timer(self):
        """``change_model()`` must cancel the idle-unload timer before
        starting the unload/reload cycle, so the timer doesn't fire
        mid-switch and unload the NEW model."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_timer is not None
        # change_model requires app.recorder.recording and
        # app._busy_event.is_set() — set them so the model change is
        # NOT deferred. busy_event.is_set() == True means "not busy"
        # (the event is SET when idle). recorder.recording == False
        # means we're not currently recording.
        app.recorder.recording = False
        app._busy_event = MagicMock()
        app._busy_event.is_set.return_value = True
        # config.save must return truthy.
        app.config.save.return_value = True
        # Stub out the heavy _change_model_load_phase so we don't
        # actually load anything.
        mm._change_model_load_phase = MagicMock()
        mm._change_model_unload_phase = MagicMock()
        # change_model may do more than we've mocked; the
        # important assertion is that the timer was cancelled.
        with contextlib.suppress(Exception):
            mm.change_model("parakeet")
        assert mm._idle_unload_timer is None, (
            "TY-11: change_model must cancel the idle-unload timer "
            "before starting the unload/reload cycle."
        )

    def test_set_active_backend_cancels_timer(self):
        """``set_active_backend()`` must cancel the idle-unload timer
        before switching backends.

        Note: ``set_active_backend`` ends by calling ``touch_model``
        on the freshly-loaded new backend, which RESCHEDULES a fresh
        timer. So the assertion is that the OLD timer (captured
        before the switch) is no longer the current one — proving
        the cancel-then-reschedule cycle ran. Without the cancel,
        the OLD timer would still be alive and could fire mid-switch
        (the race TY-11 guards against)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(
            idle_minutes=1, backend_name="whisper"
        )
        mm._schedule_idle_unload_timer()
        old_timer = mm._idle_unload_timer
        assert old_timer is not None
        # Stub out the heavy load phase.
        app.config.save.return_value = True
        mm._change_model_unload_phase = MagicMock()
        mm._ensure_engine = MagicMock()
        mm._sync_registry_from_fields = MagicMock()
        # load_active returns truthy → success path.
        mm._registry.load_active.return_value = engine
        with contextlib.suppress(Exception):
            mm.set_active_backend("parakeet")
        # The OLD timer must NO LONGER be the current one — either
        # cancelled (None) or replaced by a new Timer. Both prove the
        # cancel ran.
        assert mm._idle_unload_timer is not old_timer, (
            "TY-11: set_active_backend must cancel the OLD idle-unload "
            "timer before switching backends. The OLD timer is still "
            "the current one — the cancel did NOT run."
        )
        # Cleanup: cancel any new timer that was rescheduled by the
        # post-load touch_model.
        mm.cancel_idle_unload_timer()


# ─── Constraint #3: release_gpu_memory called after unload ──────────────


class TestIdleUnloadFiresAndReleasesGpu:
    """TY-11 constraint #3: when the timer fires, the active backend
    is unloaded AND ``release_gpu_memory()`` is called."""

    def test_timer_fire_unloads_active_backend_and_releases_gpu(self):
        """When the timer fires, the registry's ``unload()`` must be
        called with the active backend name AND
        ``release_gpu_memory()`` must be called.

        We call ``_do_idle_unload()`` directly (the timer callback
        ``_on_idle_unload_fire`` is a thin wrapper that does the
        identity check then delegates here — splitting the two lets
        tests exercise the unload path without spawning a Timer
        thread)."""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)

        # ``release_gpu_memory`` is imported lazily inside
        # ``_do_idle_unload`` via ``from voice_typer.server.asr_utils
        # import release_gpu_memory``. Patch it at the source module.
        with patch(
            "voice_typer.server.asr_utils.release_gpu_memory"
        ) as mock_release:
            mm._do_idle_unload()

            # The registry's unload() must have been called with the
            # active backend name.
            mock_registry.unload.assert_called_once_with("parakeet")
            # release_gpu_memory must have been called at least once
            # (defense in depth — parakeet_engine.unload also calls
            # it, but the ModelManager calls it explicitly too).
            mock_release.assert_called()

    def test_timer_fire_sets_tray_state_to_idle_unloaded(self):
        """When the timer fires, the tray state must transition to
        ``AppState.IDLE`` with the "Idle — model unloaded" message
        (no new enum value — we reuse IDLE per the TY-11 constraint
        of not touching tray.py / tray_types.py)."""
        from voice_typer.server.tray_types import AppState

        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        mm._do_idle_unload()

        # The tray.set_state call must include AppState.IDLE.
        states_called = [
            c.args[0] if c.args else c.kwargs.get("state")
            for c in app.tray.set_state.call_args_list
        ]
        assert AppState.IDLE in states_called, (
            f"TY-11: tray.set_state must be called with AppState.IDLE. "
            f"Got: {states_called}"
        )
        # At least one call must include the "Idle — model unloaded" msg.
        msgs = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", ""))
            for c in app.tray.set_state.call_args_list
        ]
        assert any("Idle — model unloaded" in (m or "") for m in msgs), (
            f"TY-11: tray.set_state must be called with the 'Idle — model "
            f"unloaded' message. Got: {msgs}"
        )

    def test_timer_fire_skipped_when_shutting_down(self):
        """If ``app._shutting_down`` is True when the timer fires, the
        unload must be skipped (avoids racing with teardown)."""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        app._shutting_down = True
        mm._do_idle_unload()
        mock_registry.unload.assert_not_called()
        engine.unload.assert_not_called()

    def test_timer_fire_skipped_when_already_unloaded(self):
        """If ``is_loaded`` is already False when the timer fires, the
        unload must be skipped (no double-unload)."""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(
            idle_minutes=1, is_loaded=False
        )
        mm._do_idle_unload()
        mock_registry.unload.assert_not_called()

    def test_timer_fire_aborts_when_rescheduled_race(self):
        """If the timer was rescheduled (a new Timer replaced the old
        one) before the old timer's callback ran, the old callback
        must abort without unloading.

        This exercises the identity check in ``_on_idle_unload_fire``
        — the OLD timer's callback runs but ``_idle_unload_timer`` now
        points at the NEW timer, so the identity check fails and the
        callback returns without unloading."""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        # Arm a timer (so _idle_unload_timer is set).
        mm._schedule_idle_unload_timer()
        first_timer = mm._idle_unload_timer
        assert first_timer is not None
        # Reschedule — replaces _idle_unload_timer with a new Timer.
        mm._schedule_idle_unload_timer()
        assert mm._idle_unload_timer is not first_timer
        # First timer's callback runs — should abort because
        # _idle_unload_timer no longer points at it.
        first_timer.function()
        mock_registry.unload.assert_not_called()
        # Cleanup: cancel the new timer.
        mm.cancel_idle_unload_timer()

    def test_timer_fire_logs_unload_at_info_level(self, caplog):
        """The unload must be logged at INFO level so the user can see
        when VRAM is being released (TY-11 constraint #5)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.model_manager"):
            mm._do_idle_unload()
        info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("TY-11" in m and "idle-unload" in m for m in info_msgs), (
            f"TY-11: idle-unload must be logged at INFO level. "
            f"Got: {info_msgs}"
        )


# ─── Timer scheduling on touch_active_model ─────────────────────────────


class TestTouchReschedulesTimer:
    """TY-11: each ``touch_active_model()`` call reschedules the timer
    (cancels the old, starts a new one). The deadline is pushed out
    to N minutes after the most recent touch."""

    def test_touch_active_model_arms_timer_with_correct_delay(self):
        """``touch_active_model()`` with minutes=1 must arm a
        ``threading.Timer`` with a 60-second delay."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        captured_delays: list[float] = []
        real_timer_cls = threading.Timer

        def _spy_timer(delay, callback, *args, **kwargs):
            captured_delays.append(delay)
            # Return a real Timer (with the original delay) so the
            # caller's .start() works. We cancel it in the finally
            # block below so the test doesn't actually wait 60s.
            return real_timer_cls(delay, callback)

        try:
            with patch(
                "voice_typer.server.model_manager.threading.Timer",
                side_effect=_spy_timer,
            ):
                mm.touch_active_model()
        finally:
            mm.cancel_idle_unload_timer()

        assert len(captured_delays) == 1, (
            f"TY-11: touch_active_model must arm exactly one Timer. "
            f"Got {len(captured_delays)} Timer() calls."
        )
        assert captured_delays[0] == pytest.approx(60.0), (
            f"TY-11: minutes=1 must produce a 60-second delay. "
            f"Got {captured_delays[0]}."
        )

    def test_second_touch_replaces_first_timer(self):
        """A second ``touch_active_model()`` call must cancel the
        first timer and start a new one (the deadline is pushed out)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        try:
            mm.touch_active_model()
            first = mm._idle_unload_timer
            assert first is not None
            mm.touch_active_model()
            second = mm._idle_unload_timer
            assert second is not None
            assert second is not first, (
                "TY-11: second touch must replace the first timer (reschedule)."
            )
        finally:
            mm.cancel_idle_unload_timer()

    def test_inactive_backend_touch_does_not_arm_timer(self):
        """``touch_model(<inactive backend>)`` must NOT arm the timer
        (the timer is only for the *active* backend)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(
            idle_minutes=1, backend_name="parakeet"
        )
        try:
            mm.touch_model("whisper")  # different from active (parakeet)
            assert mm._idle_unload_timer is None, (
                "TY-11: touching an inactive backend must not arm the "
                "idle-unload timer."
            )
        finally:
            mm.cancel_idle_unload_timer()

    def test_active_backend_touch_arms_timer(self):
        """``touch_model(<active backend>)`` (called directly, not via
        touch_active_model) must arm the timer — every load path uses
        touch_model so the timer is armed after a successful load
        too."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        try:
            mm.touch_model("parakeet")  # matches active backend
            assert mm._idle_unload_timer is not None
        finally:
            mm.cancel_idle_unload_timer()


# ─── Constraint #4: reload on next toggle_dictation ─────────────────────


class TestReloadAfterIdleUnload:
    """TY-11 constraint #2 + #4: after the idle-unload fires, the next
    ``toggle_dictation`` must reload the SAME backend (not silently
    switch to Whisper fallback). The tray state transitions
    "Idle — model unloaded" → "Loading model..." → "Ready"."""

    def test_ensure_active_engine_loaded_reloads_after_idle_unload(self):
        """After the idle-unload fires (is_loaded=False), calling
        ``ensure_active_engine_loaded()`` must reload the active
        backend via ``_registry.load_active()`` (not switch to
        Whisper)."""
        from voice_typer.server.tray_types import AppState

        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        # Simulate the idle-unload having fired: engine.is_loaded=False.
        engine.is_loaded = False
        mm.ensure_active_engine_loaded()
        # load_active must have been called (reload).
        mock_registry.load_active.assert_called_once()
        # Tray must transition through LOADING ("Loading model...")
        # then back to IDLE ("Ready -- ...").
        states = [
            c.args[0] if c.args else c.kwargs.get("state")
            for c in app.tray.set_state.call_args_list
        ]
        msgs = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", ""))
            for c in app.tray.set_state.call_args_list
        ]
        assert AppState.LOADING in states, (
            f"TY-11: reload path must set tray to LOADING. Got: {states}"
        )
        assert any("Loading model..." in (m or "") for m in msgs), (
            f"TY-11: reload path must show 'Loading model...' message. Got: {msgs}"
        )
        assert any("Ready" in (m or "") for m in msgs), (
            f"TY-11: reload path must end with 'Ready -- ...' message. Got: {msgs}"
        )

    def test_reload_after_idle_unload_reschedules_timer(self):
        """After the reload, the idle-unload timer must be re-armed
        (so the cycle can repeat on the next idle period)."""
        mm, app, engine, _ = _make_mm_with_mock_backend(idle_minutes=1)
        engine.is_loaded = False
        try:
            mm.ensure_active_engine_loaded()
            # After reload, touch_model is called → timer re-armed.
            assert mm._idle_unload_timer is not None, (
                "TY-11: after reload, the idle-unload timer must be re-armed."
            )
        finally:
            mm.cancel_idle_unload_timer()

    def test_reload_failure_does_not_raise(self):
        """If the reload fails, ``ensure_active_engine_loaded`` must
        not raise (the recording_controller's fallback_to_whisper
        path handles the failure downstream)."""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        engine.is_loaded = False
        mock_registry.load_active.return_value = None  # falsy → fail
        # Must not raise.
        mm.ensure_active_engine_loaded()
        # The reload attempt must have been made.
        mock_registry.load_active.assert_called_once()

    def test_reload_uses_progress_callback(self):
        """The reload path must register a progress_callback with
        ``load_active`` so the tray shows progress messages during
        the (potentially slow) reload."""
        mm, app, engine, mock_registry = _make_mm_with_mock_backend(idle_minutes=1)
        engine.is_loaded = False
        mm.ensure_active_engine_loaded()
        # load_active must have been called with a progress_callback
        # keyword arg.
        call_kwargs = mock_registry.load_active.call_args.kwargs
        assert "progress_callback" in call_kwargs, (
            "TY-11: reload path must pass a progress_callback to "
            "load_active so the tray shows progress during reload."
        )
        assert callable(call_kwargs["progress_callback"])


# ─── Config + IPC allowlist ─────────────────────────────────────────────


class TestConfigField:
    """TY-11: the new ``model_idle_unload_minutes`` config field must
    be on the Config dataclass, in the int_fields coercion set, and
    in the IPC_CONFIG_ALLOWLIST so the renderer can set it via IPC."""

    def test_field_exists_on_config_dataclass(self):
        """``Config()`` must have a ``model_idle_unload_minutes``
        attribute (default 0)."""
        from voice_typer.server.config import Config

        cfg = Config()
        assert hasattr(cfg, "model_idle_unload_minutes")
        assert isinstance(cfg.model_idle_unload_minutes, int)
        assert cfg.model_idle_unload_minutes == 0

    def test_field_in_int_fields_coercion_set(self):
        """The field must be in the ``int_fields`` set inside
        ``Config._validate_non_numeric_fields`` so legacy config.json
        files that stored it as a numeric string or float are coerced
        to int on load."""
        from voice_typer.server.config import Config

        # Load a config dict with a string value — must be coerced to int.
        # We exercise _validate_non_numeric_fields directly.
        data = {"model_idle_unload_minutes": "15"}
        validated = Config._validate_non_numeric_fields(data)
        assert validated["model_idle_unload_minutes"] == 15
        assert isinstance(validated["model_idle_unload_minutes"], int)

    def test_field_in_ipc_allowlist(self):
        """The field must be in ``IPC_CONFIG_ALLOWLIST`` so the
        renderer can set it via IPC ``set_config``."""
        from voice_typer.server.config_validators import IPC_CONFIG_ALLOWLIST

        assert "model_idle_unload_minutes" in IPC_CONFIG_ALLOWLIST, (
            "TY-11: model_idle_unload_minutes must be in "
            "IPC_CONFIG_ALLOWLIST so the renderer can change it via IPC."
        )

    def test_ipc_allowlist_validates_int_range(self):
        """The IPC validator must reject negative values and values
        above 1440 (24 hours — anything above is almost certainly a
        typo)."""
        from voice_typer.server.config_validators import validate_config_update

        # Negative rejected.
        validated, errors = validate_config_update(
            {"model_idle_unload_minutes": -1}
        )
        assert "model_idle_unload_minutes" not in validated
        assert errors  # some error was reported

        # 0 accepted (disable sentinel).
        validated, errors = validate_config_update(
            {"model_idle_unload_minutes": 0}
        )
        assert validated.get("model_idle_unload_minutes") == 0
        assert not errors

        # 15 accepted (typical value).
        validated, errors = validate_config_update(
            {"model_idle_unload_minutes": 15}
        )
        assert validated.get("model_idle_unload_minutes") == 15
        assert not errors

        # 1440 accepted (24 h — upper bound).
        validated, errors = validate_config_update(
            {"model_idle_unload_minutes": 1440}
        )
        assert validated.get("model_idle_unload_minutes") == 1440
        assert not errors

        # 1441 rejected (above 24 h).
        validated, errors = validate_config_update(
            {"model_idle_unload_minutes": 1441}
        )
        assert "model_idle_unload_minutes" not in validated
        assert errors
