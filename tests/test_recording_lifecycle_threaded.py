"""Regression tests for the  fix: F2 dispatch thread must return
quickly (sub-200ms) even when a model reload is in flight.

These tests pin the daemon-worker refactor of ``_start_impl``:

* ``ensure_active_engine_loaded()`` + post-load steps run on a daemon
  worker thread (``_start_dictation_worker_entry``), NOT on the F2
  dispatch thread.
* The F2 thread returns after a bounded ``join(timeout=...)`` — fast
  enough for tests with mocked models, slow enough to not block the
  dispatch thread in production (5-30s idle-unload reload).
* The worker signals ``_start_complete_event`` in its ``finally`` block
  so tests that need to assert model-loaded state can wait on the event.
* The ``recording`` flag is set synchronously by ``recorder.start()``
  on the F2 thread — tests that assert ``recording=True`` immediately
  after ``_start_dictation`` still pass.

The tests stub ``ensure_active_engine_loaded`` with a configurable
delay to simulate the 5-30s idle-unload reload path, then assert the
F2 thread returns within 200ms while the model load is still in flight.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

# ── Override the autouse ``mock_heavy_imports`` conftest fixture ───────


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture."""
    yield


# ── Fake app + controller ─────────────────────────────────────────────


def _make_app_with_mock_recorder() -> MagicMock:
    """Build a minimal mock app/controller for RecordingLifecycle tests.

    The app, controller, and all collaborators are MagicMocks so the
    test never touches real audio hardware, model loading, or tray UI.
    """
    app = MagicMock(name="app")
    app.recorder = MagicMock(name="recorder")
    app.recorder.recording = False
    # Simulate ``recorder.start()`` flipping ``recording`` to True
    # (the real Recorder does this via ``_recording_event.set()``).
    app.recorder.start = MagicMock(
        side_effect=lambda *a, **kw: setattr(app.recorder, "recording", True)
    )
    app.recorder.discard = MagicMock(
        side_effect=lambda *a, **kw: setattr(app.recorder, "recording", False)
    )
    app._busy_event = threading.Event()
    app._busy_event.set()  # not busy
    app._cycle_id = "#1"
    app._cycle_counter = 0
    app.config = MagicMock(name="config")
    app.config.voice_biometric_consent = True
    app.config.esc_cancel_enabled = False
    app.config.streaming_transcription = False
    app.config.microphone = None
    app.tray = MagicMock(name="tray")
    app._waveform_bubble = MagicMock(name="bubble")
    app._duck_volume = MagicMock(name="duck_volume")
    app._cancel_pending_timers = MagicMock()
    app._schedule_timer = MagicMock()
    app._audio_quality = MagicMock()
    app._restore_volume = MagicMock()
    app.models = MagicMock(name="models")
    # active_transcriber returns a mock that IS loaded by default.
    _active = MagicMock(name="transcriber")
    _active.is_loaded = True
    app.models.active_transcriber = MagicMock(return_value=_active)
    app.models.ensure_active_engine_loaded = MagicMock(return_value=_active)
    app.models.fallback_to_whisper = MagicMock()
    app.models.apply_pending_model_change = MagicMock()
    return app


def _make_controller_with_lifecycle(app: MagicMock) -> MagicMock:
    """Build a mock RecordingController with a real RecordingLifecycle.

    The controller is a MagicMock (so it has every attribute the
    lifecycle methods access), but ``_lifecycle`` is a REAL
    ``RecordingLifecycle`` instance so the method under test runs
    production code.
    """
    from voice_typer.server.recording_lifecycle import RecordingLifecycle

    controller = MagicMock(name="controller")
    controller._app = app
    controller._toggle_lock = threading.RLock()
    controller._watchdog_lock = threading.Lock()
    controller._transcription_thread = None
    controller._lifecycle = RecordingLifecycle()
    # Wire the delegator methods the lifecycle calls back to.
    controller._start_streaming_session_if_enabled = MagicMock()
    controller._cancel_streaming_session = MagicMock()
    controller._stop_level_monitor_for_recorder_start = MagicMock()
    controller.on_silence_warning = MagicMock()
    controller.on_silence_auto_stop = MagicMock()
    controller.on_max_duration_auto_stop = MagicMock()
    controller.on_microphone_permission_revoked = MagicMock()
    controller.on_recorder_rms = MagicMock()
    return controller


# ── Tests ──────────────────────────────────────────────────────────────


class TestGQ27FastF2ReturnDuringModelReload:
    """The F2 dispatch thread returns within 200ms even when
    ``ensure_active_engine_loaded()`` is slow (5-30s idle-unload reload)."""

    def test_f2_returns_within_200ms_when_model_reload_in_flight(self) -> None:
        """When ``ensure_active_engine_loaded()`` takes 5s (simulated),
        the F2 thread must return within 200ms. The model load continues
        on the daemon worker thread."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Simulate a 5s model reload (idle-unload path). The worker
        # will block in ``ensure_active_engine_loaded()`` for 5s.
        load_started = threading.Event()
        load_completed = threading.Event()

        def _slow_load():
            load_started.set()
            time.sleep(5.0)
            load_completed.set()
            return app.models.active_transcriber.return_value

        app.models.ensure_active_engine_loaded = MagicMock(side_effect=_slow_load)

        # Call ``_start_impl`` on the F2 thread and measure return time.
        # ``_start_impl`` is called under ``_toggle_lock`` by ``start()``;
        # we call it directly to avoid the ``with`` block (the lifecycle's
        # ``start`` method acquires the lock).
        start_time = time.monotonic()
        controller._lifecycle._start_impl(controller)
        elapsed = time.monotonic() - start_time

        # The F2 thread must return within 200ms.
        assert elapsed < 0.2, (
            f"F2 dispatch thread took {elapsed:.3f}s to return — "
            f"expected < 0.2s (200ms) even when model reload is in flight. "
            f"The daemon worker should handle the 5s load asynchronously."
        )

        # The model load must have started (on the worker thread).
        assert load_started.is_set(), (
            "ensure_active_engine_loaded() must have been called by the worker"
        )
        # The model load must NOT have completed yet (it takes 5s, the
        # F2 thread returned in <0.2s).
        assert not load_completed.is_set(), (
            "ensure_active_engine_loaded() should still be in progress "
            "(5s sleep) when the F2 thread returns — the worker is async"
        )

        # ``recorder.start()`` was called synchronously (before the
        # worker spawned).
        app.recorder.start.assert_called_once()

        # Wait for the worker to finish so we don't leak a thread.
        event = getattr(controller, "_start_complete_event", None)
        if event is not None:
            event.wait(timeout=10.0)
        assert load_completed.is_set(), (
            "Worker should eventually complete the model load"
        )

    def test_recording_flag_set_synchronously(self) -> None:
        """The ``recorder.recording`` flag is set to True synchronously
        by ``recorder.start()`` on the F2 thread — tests that assert
        ``recording=True`` immediately after ``_start_dictation`` still
        pass."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Even with a slow model load, recording is set synchronously.
        load_started = threading.Event()

        def _slow_load():
            load_started.set()
            time.sleep(2.0)
            return app.models.active_transcriber.return_value

        app.models.ensure_active_engine_loaded = MagicMock(side_effect=_slow_load)

        controller._lifecycle._start_impl(controller)

        # recording must be True immediately after _start_impl returns.
        assert app.recorder.recording is True, (
            "recorder.recording must be True immediately after _start_impl "
            "returns — recorder.start() is called synchronously on the F2 thread"
        )
        assert app.recorder.start.assert_called_once

        # Clean up: wait for worker.
        event = getattr(controller, "_start_complete_event", None)
        if event is not None:
            event.wait(timeout=5.0)

    def test_start_complete_event_signaled(self) -> None:
        """The worker signals ``_start_complete_event`` in its finally
        block so tests can wait on it."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Fast model load (already loaded).
        controller._lifecycle._start_impl(controller)

        # The event must exist on the controller.
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None, (
            "_start_complete_event must be published on the controller"
        )

        # Wait for the worker to signal (should be fast — model is
        # already loaded).
        waited = event.wait(timeout=2.0)
        assert waited, (
            "Worker must signal _start_complete_event within 2s when "
            "the model is already loaded (fast path)"
        )

    def test_start_worker_thread_is_daemon(self) -> None:
        """The worker thread must be a daemon so it doesn't block
        process exit if the user quits during the start cycle."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Slow load so the worker is still alive when we check.
        def _slow_load():
            time.sleep(2.0)
            return app.models.active_transcriber.return_value

        app.models.ensure_active_engine_loaded = MagicMock(side_effect=_slow_load)

        controller._lifecycle._start_impl(controller)

        worker = getattr(controller, "_start_worker_thread", None)
        assert worker is not None, (
            "_start_worker_thread must be published on the controller"
        )
        assert worker.daemon is True, (
            "start worker thread must be a daemon so it doesn't block "
            "process exit"
        )

        # Clean up.
        event = getattr(controller, "_start_complete_event", None)
        if event is not None:
            event.wait(timeout=5.0)

    def test_streaming_session_setup_runs_in_worker(self) -> None:
        """``_start_streaming_session_if_enabled`` runs in the worker
        thread, NOT on the F2 thread. After the worker completes, the
        streaming session setup must have been called."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        app.config.streaming_transcription = True

        controller._lifecycle._start_impl(controller)

        # Wait for the worker.
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None
        event.wait(timeout=2.0)

        # Streaming session setup was called by the worker.
        controller._start_streaming_session_if_enabled.assert_called_once()

    def test_worker_discards_recorder_on_model_fail(self) -> None:
        """When the model fails to load (fallback also fails), the
        worker calls ``recorder.discard()`` and sets ``recording=False``.
        Tests that wait on ``_start_complete_event`` can then assert
        the discard."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Model not loaded, fallback also fails.
        _unloaded = MagicMock(name="unloaded_transcriber")
        _unloaded.is_loaded = False
        app.models.active_transcriber = MagicMock(return_value=_unloaded)
        app.models.ensure_active_engine_loaded = MagicMock(return_value=_unloaded)
        # fallback_to_whisper doesn't change is_loaded (still False).
        app.models.fallback_to_whisper = MagicMock()

        controller._lifecycle._start_impl(controller)

        # Wait for the worker.
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None
        event.wait(timeout=5.0)

        # discard was called by the worker.
        app.recorder.discard.assert_called_once()
        # recording was reset to False.
        assert app.recorder.recording is False, (
            "recorder.recording must be False after model-fail discard"
        )

    def test_f2_returns_quickly_when_model_already_loaded(self) -> None:
        """When the model is already loaded (common case), the F2
        thread returns quickly AND the worker completes within the
        join window (sub-100ms path)."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Model already loaded — ensure_active_engine_loaded is a no-op.
        start_time = time.monotonic()
        controller._lifecycle._start_impl(controller)
        elapsed = time.monotonic() - start_time

        # F2 thread returns within 200ms.
        assert elapsed < 0.2, (
            f"F2 thread took {elapsed:.3f}s — expected < 0.2s when model "
            f"is already loaded (fast path)"
        )

        # Worker completed (event signaled).
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None
        assert event.wait(timeout=1.0), "Worker should complete quickly on fast path"
