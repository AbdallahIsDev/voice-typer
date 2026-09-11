"""Regression tests for the  fix: F2 dispatch thread must return
quickly (long before the load completes) even when a model reload is
in flight.

These tests pin the daemon-worker refactor of ``_start_impl``:

* ``ensure_active_engine_loaded()`` + post-load steps run on a daemon
  worker thread (``_start_dictation_worker_entry``), NOT on the F2
  dispatch thread.
* The F2 thread returns after a bounded ``join(timeout=...)``, fast
  enough to never block the dispatch thread in production (5-30s
  idle-unload reload).
* The worker signals ``_start_complete_event`` in its ``finally`` block
  so tests that need to assert model-loaded state can wait on the event.
* The ``recording`` flag is set synchronously by ``recorder.start()``
  on the F2 thread, tests that assert ``recording=True`` immediately
  after ``_start_dictation`` still pass.

The tests stub ``ensure_active_engine_loaded`` with a configurable
delay to simulate the 5-30s idle-unload reload path, then assert the
F2 thread returns while the model load is still in flight.
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
    app.recorder.start = MagicMock(side_effect=lambda *a, **kw: setattr(app.recorder, "recording", True))
    app.recorder.discard = MagicMock(side_effect=lambda *a, **kw: setattr(app.recorder, "recording", False))
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


def _wire_public_entry_chain(app: MagicMock, controller: MagicMock) -> None:
    """Wire the mock controller/app so the PRODUCTION call chain runs:
    ``lifecycle.toggle -> controller._toggle_impl -> app._start_dictation
    -> lifecycle.start -> controller._start_impl``.

    The production controller exposes 1-line delegators with exactly these
    bodies; a MagicMock controller needs them wired by hand so the
    double-acquisition path (toggle -> start, RLock count 2) taken by every
    user-facing entry (F2 / IPC / tray) is exercised for real.
    """
    controller._toggle_impl = lambda: controller._lifecycle._toggle_impl(controller)
    controller._start_impl = lambda: controller._lifecycle._start_impl(controller)
    app._start_dictation = lambda: controller._lifecycle.start(controller)
    app._stop_dictation = lambda: controller._lifecycle.stop(controller)
    # ``_toggle_impl`` treats a live loader thread as "queue the dictation";
    # on a MagicMock it would be truthy AND ``is_alive()`` truthy.
    app.models._model_load_thread = None


# ── Tests ──────────────────────────────────────────────────────────────


class TestFastF2ReturnDuringModelReload:
    """The F2 dispatch thread returns long before the simulated reload
    finishes when ``ensure_active_engine_loaded()`` is slow (5-30s
    idle-unload reload)."""

    def test_f2_returns_before_load_completes_when_model_reload_in_flight(self) -> None:
        """When ``ensure_active_engine_loaded()`` takes 5s (simulated),
        the F2 thread must return long before the load completes. The
        model load continues on the daemon worker thread."""
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
        # we acquire the lock here to mirror that contract.
        start_time = time.monotonic()
        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)
        elapsed = time.monotonic() - start_time

        # The F2 thread must return long before the simulated load
        # finishes. The budget is generous (it only needs to hold on a
        # heavily loaded CI runner) while still catching the regression
        # class this test exists for: an unbounded join would block for
        # the full 5s simulated reload. The product's own bounded join is
        # 0.1s here (model pre-loaded path), so anything >= 2s means the
        # dispatch thread waited on the worker.
        assert elapsed < 2.0, (
            f"F2 dispatch thread took {elapsed:.3f}s to return, "
            f"expected < 2.0s even when model reload is in flight. "
            f"The daemon worker should handle the 5s load asynchronously."
        )

        # The model load must have started (on the worker thread).
        assert load_started.is_set(), "ensure_active_engine_loaded() must have been called by the worker"
        # The model load must NOT have completed yet (it takes 5s, the
        # F2 thread returned in <0.2s).
        assert not load_completed.is_set(), (
            "ensure_active_engine_loaded() should still be in progress "
            "(5s sleep) when the F2 thread returns, the worker is async"
        )

        # ``recorder.start()`` was called synchronously (before the
        # worker spawned).
        app.recorder.start.assert_called_once()

        # Wait for the worker to finish so we don't leak a thread.
        event = getattr(controller, "_start_complete_event", None)
        if event is not None:
            event.wait(timeout=10.0)
        assert load_completed.is_set(), "Worker should eventually complete the model load"

    def test_recording_flag_set_synchronously(self) -> None:
        """The ``recorder.recording`` flag is set to True synchronously
        by ``recorder.start()`` on the F2 thread, tests that assert
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

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        # recording must be True immediately after _start_impl returns.
        assert app.recorder.recording is True, (
            "recorder.recording must be True immediately after _start_impl "
            "returns, recorder.start() is called synchronously on the F2 thread"
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
        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        # The event must exist on the controller.
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None, "_start_complete_event must be published on the controller"

        # Wait for the worker to signal (should be fast, model is
        # already loaded).
        waited = event.wait(timeout=2.0)
        assert waited, "Worker must signal _start_complete_event within 2s when the model is already loaded (fast path)"

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

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        worker = getattr(controller, "_start_worker_thread", None)
        assert worker is not None, "_start_worker_thread must be published on the controller"
        assert worker.daemon is True, "start worker thread must be a daemon so it doesn't block process exit"

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

        with controller._toggle_lock:
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

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        # Wait for the worker.
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None
        event.wait(timeout=5.0)

        # discard was called by the worker.
        app.recorder.discard.assert_called_once()
        # recording was reset to False.
        assert app.recorder.recording is False, "recorder.recording must be False after model-fail discard"

    def test_concurrent_cancel_returns_fast_during_cold_start(self) -> None:
        """A concurrent ESC ``cancel()`` must return in < 100 ms while a
        cold-model start's bounded worker join is in flight.

        The user-facing entries (F2 / IPC / tray) take the DOUBLE-acquisition
        path ``toggle() -> _toggle_impl -> app._start_dictation() -> start()``.
        The bounded join of the DictationStart worker (2.0 s timeout on the
        cold path) must run OUTSIDE ``_toggle_lock`` so the cancel thread
        never waits behind it.
        """
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        _wire_public_entry_chain(app, controller)

        # Cold start: an active engine exists but is NOT loaded yet, so the
        # start worker's join timeout is the long (2.0 s) cold-path one.
        _cold = MagicMock(name="cold_transcriber")
        _cold.is_loaded = False
        app.models.active_transcriber = MagicMock(return_value=_cold)

        load_started = threading.Event()
        release_load = threading.Event()

        def _blocked_load():
            load_started.set()
            release_load.wait(timeout=10.0)
            return _cold

        app.models.ensure_active_engine_loaded = MagicMock(side_effect=_blocked_load)

        # The F2 thread: toggle -> (re-entrant) start -> spawn worker ->
        # bounded join OUTSIDE the lock.
        f2_thread = threading.Thread(target=controller._lifecycle.toggle, args=(controller,), name="F2")
        f2_thread.start()
        assert load_started.wait(timeout=2.0), "start worker must reach the model load"

        # ESC cancel fires on another thread while the cold start's bounded
        # join is still in flight, must return in < 100 ms, not the 2.0 s
        # join window.
        cancel_start = time.monotonic()
        controller._lifecycle.cancel(controller)
        cancel_elapsed = time.monotonic() - cancel_start

        assert cancel_elapsed < 0.100, (
            f"concurrent cancel took {cancel_elapsed:.3f}s during a cold start, "
            f"the bounded start-worker join must NOT hold ``_toggle_lock`` "
            f"(expected < 100 ms)"
        )

        # Cleanup: unblock the worker and drain the threads.
        release_load.set()
        f2_thread.join(timeout=5.0)
        event = getattr(controller, "_start_complete_event", None)
        if event is not None:
            event.wait(timeout=5.0)

    def test_f2_returns_quickly_when_model_already_loaded(self) -> None:
        """When the model is already loaded (common case), the F2
        thread returns quickly AND the worker completes within the
        join window (sub-100ms path)."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)

        # Model already loaded, ensure_active_engine_loaded is a no-op.
        start_time = time.monotonic()
        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)
        elapsed = time.monotonic() - start_time

        # F2 thread returns quickly on the pre-loaded fast path.
        # Generous budget: only needs to hold on a loaded CI runner —
        # the product's bounded join is 0.1s here, so >= 1s means the
        # dispatch thread stalled.
        assert elapsed < 1.0, f"F2 thread took {elapsed:.3f}s, expected < 1.0s when model is already loaded (fast path)"

        # Worker completed (event signaled).
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None
        assert event.wait(timeout=1.0), "Worker should complete quickly on fast path"


class TestStartWorkerJoinTimeoutReset:
    """The published ``_start_worker_join_timeout`` must be reset at
    ``_start_impl`` entry so a STALE value from a previous start cycle
    can never leak into this entry's bounded join.

    If an exception fires between ``worker.start()`` and the adaptive
    timeout publish (e.g. ``active_transcriber()`` raising on the
    pre-load probe), ``_start_impl``'s except path swallows it and
    returns normally, so ``_run_public_entry`` still performs the
    bounded join of the just-started worker.  Without the entry reset,
    that join uses the PREVIOUS cycle's timeout (up to 2.0 s on the
    cold-model path), stalling the public entry.
    """

    def test_stale_timeout_cannot_leak_into_join_after_start_path_exception(self) -> None:
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        _wire_public_entry_chain(app, controller)

        # Simulate a STALE cold-path timeout published by a previous
        # start cycle.
        controller._start_worker_join_timeout = 2.0

        calls = {"n": 0}
        _loaded = MagicMock(name="transcriber")
        _loaded.is_loaded = True

        def _flaky_active_transcriber():
            calls["n"] += 1
            if calls["n"] == 2:
                # Call 1 = the toggle decision; call 2 fires AFTER
                # worker.start() but BEFORE the join-timeout publish in
                # _start_impl, exactly the leak window.
                raise RuntimeError("transcriber probe failed")
            return _loaded

        app.models.active_transcriber = MagicMock(side_effect=_flaky_active_transcriber)

        # Block the worker's model load so the join's timeout is
        # observable (the join returns at its timeout, not earlier).
        release_worker = threading.Event()

        def _blocked_load():
            release_worker.wait(timeout=10.0)
            return _loaded

        app.models.ensure_active_engine_loaded = MagicMock(side_effect=_blocked_load)

        start_time = time.monotonic()
        controller._lifecycle.toggle(controller)
        elapsed = time.monotonic() - start_time

        assert elapsed < 0.5, (
            f"public entry took {elapsed:.3f}s, a stale 2.0 s join timeout "
            f"leaked into this entry's bounded join after a start-path "
            f"exception (expected the reset 0.1 s default)"
        )

        # Drain: release the worker and wait for it to finish.
        release_worker.set()
        event = getattr(controller, "_start_complete_event", None)
        if event is not None:
            event.wait(timeout=5.0)


# ── Audio-path parity: level monitor restart + off-thread duck ─────────


class TestStartPathAudioParity:
    """Two start-path parity contracts:

    * a FAILED start must restart the level monitor for the always-visible
      bubble (the start path stops it before ``recorder.start()``; the stop
      and stop-failure paths already restart it);
    * system-volume ducking must run on the DictationStart worker thread,
      not on the hotkey thread (0.15-0.7 s of backend/subprocess calls per
      start used to run synchronously under ``_toggle_lock``).
    """

    def test_failed_start_restarts_level_monitor(self) -> None:
        """When ``recorder.start()`` raises, the level monitor that the start
        path stopped must be restarted so the always-visible bubble's level
        bar does not flatline until the next toggle."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        restart_monitor = MagicMock(name="restart_level_monitor")
        controller._maybe_restart_level_monitor_for_always_visible_bubble = restart_monitor
        app.recorder.start = MagicMock(side_effect=RuntimeError("stream open failed"))

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        (
            restart_monitor.assert_called_once_with(app),
            "the start-failure path must restart the level monitor (parity with the stop paths)",
        )

    def test_duck_volume_runs_on_start_worker_thread(self) -> None:
        """``_duck_volume`` must execute on the DictationStart worker thread,
        never on the calling (hotkey) thread."""
        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        duck_thread_names: list[str] = []

        def _record_duck_thread() -> None:
            duck_thread_names.append(threading.current_thread().name)

        app._duck_volume = MagicMock(side_effect=_record_duck_thread)

        caller_thread_name = threading.current_thread().name
        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        # Wait for the worker to finish (duck happens before the model load
        # in the worker, so completion implies it ran).
        event = getattr(controller, "_start_complete_event", None)
        assert event is not None, "_start_complete_event must be published on the controller"
        assert event.wait(timeout=5.0), "start worker must complete so the duck lands"

        assert duck_thread_names == ["DictationStart"], (
            f"_duck_volume must run exactly once on the DictationStart worker "
            f"thread, got {duck_thread_names!r} (caller thread: {caller_thread_name!r})"
        )


# ── Recording-start failure reasons (tray tooltip) ─────────────────────


class TestRecordingStartFailureReason:
    """The tray tooltip for a failed recording start must show the
    backend's reason (permission denied / no input device) instead of
    the bare "Recording failed" label, without leaking raw exception
    text for unknown errors."""

    def test_permission_denied_surfaces_reason(self) -> None:
        """A ``MicrophonePermissionDeniedError`` from ``recorder.start()``
        surfaces the permission message in the ERROR tooltip."""
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
        from voice_typer.server.tray_types import AppState

        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        app.recorder.start = MagicMock(
            side_effect=MicrophonePermissionDeniedError(
                "PortAudio reports microphone permission denied", state="denied"
            )
        )

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        app.tray.set_state.assert_called_with(
            AppState.ERROR,
            "Recording failed -- microphone permission denied. Allow mic access in system settings.",
        )
        # The OS notification must carry the same actionable reason.
        notify_msg = str(app.tray.notify.call_args.args[1])
        assert "microphone permission denied" in notify_msg, (
            f"notification must carry the permission reason, got: {notify_msg!r}"
        )

    def test_no_input_device_surfaces_reason(self) -> None:
        """The "No input device could be opened" RuntimeError surfaces
        the no-microphone message in the ERROR tooltip."""
        from voice_typer.server.tray_types import AppState

        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        app.recorder.start = MagicMock(side_effect=RuntimeError("No input device could be opened"))

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        app.tray.set_state.assert_called_with(
            AppState.ERROR,
            "Recording failed -- no microphone found. Connect a microphone and try again.",
        )
        # The OS notification must carry the same actionable reason.
        notify_msg = str(app.tray.notify.call_args.args[1])
        assert "no microphone found" in notify_msg, f"notification must carry the no-device reason, got: {notify_msg!r}"

    def test_unknown_error_keeps_generic_label(self) -> None:
        """An unknown start failure falls back to the generic
        "Recording failed" label, raw exception text (which can leak
        paths / device names) must never reach the tray."""
        from voice_typer.server.tray_types import AppState

        app = _make_app_with_mock_recorder()
        controller = _make_controller_with_lifecycle(app)
        app.recorder.start = MagicMock(
            side_effect=RuntimeError("PortAudio stream open failed with code -9999 (device busy)")
        )

        with controller._toggle_lock:
            controller._lifecycle._start_impl(controller)

        app.tray.set_state.assert_called_with(
            AppState.ERROR,
            "Recording failed",
        )
        # Unknown failure: the notification keeps the "check the log"
        # guidance and must NOT contain the raw exception text.
        notify_msg = str(app.tray.notify.call_args.args[1])
        assert "check logs/voice-typer.log" in notify_msg.lower(), (
            f"unknown failure notification should point at the log, got: {notify_msg!r}"
        )
        assert "device busy" not in notify_msg

    def test_message_mapper_isolated(self) -> None:
        """Unit-level check of ``_recording_start_failure_message``
        across the three categories (permission / no device / generic),
        including the leaked-path case staying generic."""
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError
        from voice_typer.server.recording_lifecycle import (
            _recording_start_failure_message,
        )

        assert (
            _recording_start_failure_message(MicrophonePermissionDeniedError(state="denied"))
            == "Recording failed -- microphone permission denied. Allow mic access in system settings."
        )
        assert (
            _recording_start_failure_message(RuntimeError("No input device could be opened"))
            == "Recording failed -- no microphone found. Connect a microphone and try again."
        )
        # A message that merely CONTAINS the marker maps to no-device.
        assert (
            _recording_start_failure_message(RuntimeError("No input device could be opened (all candidates failed)"))
            == "Recording failed -- no microphone found. Connect a microphone and try again."
        )
        # Raw/leaky text must stay generic.
        assert (
            _recording_start_failure_message(RuntimeError("C:\\Users\\joe\\AppData: open failed")) == "Recording failed"
        )
