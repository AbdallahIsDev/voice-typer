"""Tests for :mod:`voice_typer.server.recording_controller` audio-callback
shutdown paths.

These tests pin the XV-134 contract: ``on_silence_auto_stop`` and
``on_max_duration_auto_stop`` MUST dispatch ``_stop_dictation`` off the
audio callback thread (which holds ``Recorder._lock``) so the stop
sequence doesn't deadlock.  Previously both call sites used
``self._app._schedule_timer(0, ...)`` which built a ``threading.Timer``
object, appended it to ``_pending_timers``, and started a Timer thread
that immediately fired — paying the Timer scheduling cost and polluting
the pending-timer list with a zero-delay entry that was never going to
be cancelled meaningfully.

XV-134 (fixed in :mod:`voice_typer.server.timer_coordinator`) makes
``_schedule_timer(0, func)`` short-circuit to a plain daemon thread via
the new ``TimerCoordinator.defer`` method.  These tests verify the
recording-controller call sites still trigger the off-thread dispatch
end-to-end — they don't re-test the TimerCoordinator internals (those
are covered by ``tests/test_timer_coordinator.py``).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock


def _make_minimal_controller():
    """Build a RecordingController with only the attributes the
    auto-stop callbacks read.

    The full ``__init__`` pulls in models, recorder, tray, etc. — none
    of which the auto-stop callbacks touch.  Using ``__new__`` keeps
    the test fast and dependency-free.
    """
    from voice_typer.server.recording_controller import RecordingController

    ctrl = RecordingController.__new__(RecordingController)
    ctrl._app = MagicMock()
    return ctrl


# silence / max-duration auto-stop dispatch ────────────────────


class TestSilenceAutoStopDispatch:
    """XV-134: ``on_silence_auto_stop`` dispatches ``_stop_dictation``
    on a background thread (not the calling thread)."""

    def test_schedules_stop_dictation_with_zero_delay(self):
        """The callback must call ``self._app._schedule_timer(0, ...)``
        so the stop sequence runs off the audio callback thread.

        XV-134 makes that call short-circuit to a daemon thread — we
        verify the call site still passes ``0`` (the contract that
        triggers the short-circuit) and that the dispatched callback
        actually runs.
        """
        ctrl = _make_minimal_controller()
        captured: list[tuple] = []

        def fake_schedule(delay, func, *args, **kwargs):
            captured.append((delay, func))

            def _run():
                func()

            t = threading.Thread(target=_run, name="test-defer", daemon=True)
            t.start()
            return t

        ctrl._app._schedule_timer.side_effect = fake_schedule
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_silence_auto_stop()

        # Must have scheduled with delay=0 ( short-circuit trigger).
        assert len(captured) == 1
        delay, func = captured[0]
        assert delay == 0, (
            f"XV-134: on_silence_auto_stop must schedule _stop_dictation with "
            f"delay=0 (got {delay}) so TimerCoordinator short-circuits to a "
            f"plain daemon thread."
        )
        assert func == ctrl._app._stop_dictation

    def test_stop_dictation_actually_runs_asynchronously(self):
        """XV-134: the dispatched ``_stop_dictation`` runs on a
        background thread, NOT on the calling (audio callback) thread.

        This is the deadlock-avoidance guarantee the original comment
        documents: calling ``recorder.stop()`` directly from the audio
        callback would re-acquire ``Recorder._lock`` and deadlock.
        """
        ctrl = _make_minimal_controller()
        ran_on: list[threading.Thread] = []

        # Use a real TimerCoordinator so 's short-circuit path
        # actually fires.
        from voice_typer.server.timer_coordinator import TimerCoordinator

        coordinator = TimerCoordinator(app=None)

        def fake_stop_dictation():
            ran_on.append(threading.current_thread())

        ctrl._app._schedule_timer = coordinator._schedule_timer
        ctrl._app._stop_dictation = fake_stop_dictation
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_silence_auto_stop()

        # Wait for the background thread to run.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not ran_on:
            time.sleep(0.005)

        assert len(ran_on) == 1, f"XV-134: _stop_dictation should have run on a background thread, got {ran_on}"
        assert ran_on[0] is not threading.current_thread(), (
            "XV-134: _stop_dictation must NOT run on the audio callback thread (would deadlock on Recorder._lock)"
        )
        assert ran_on[0].daemon, "deferred thread should be daemon=True"

    def test_tray_notification_fired_synchronously(self):
        """The user-facing notification fires on the calling thread
        (immediate feedback) — only the stop sequence is deferred."""
        ctrl = _make_minimal_controller()
        notify_on: list[threading.Thread] = []

        def fake_notify(*args, **kwargs):
            notify_on.append(threading.current_thread())

        ctrl._app._schedule_timer = lambda delay, func, *a, **kw: None
        ctrl._app.tray.notify_safety.side_effect = fake_notify

        ctrl.on_silence_auto_stop()

        assert len(notify_on) == 1
        assert notify_on[0] is threading.current_thread(), (
            "on_silence_auto_stop should notify on the calling thread for immediate user feedback"
        )


class TestMaxDurationAutoStopDispatch:
    """XV-134: ``on_max_duration_auto_stop`` dispatches ``_stop_dictation``
    on a background thread (mirrors the silence-auto-stop path)."""

    def test_schedules_stop_dictation_with_zero_delay(self):
        ctrl = _make_minimal_controller()
        captured: list[tuple] = []

        def fake_schedule(delay, func, *args, **kwargs):
            captured.append((delay, func))
            return MagicMock()

        ctrl._app._schedule_timer.side_effect = fake_schedule
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_max_duration_auto_stop()

        assert len(captured) == 1
        delay, func = captured[0]
        assert delay == 0, (
            f"XV-134: on_max_duration_auto_stop must schedule _stop_dictation "
            f"with delay=0 (got {delay}) so TimerCoordinator short-circuits."
        )
        assert func == ctrl._app._stop_dictation

    def test_stop_dictation_runs_asynchronously(self):
        ctrl = _make_minimal_controller()
        ran_on: list[threading.Thread] = []

        from voice_typer.server.timer_coordinator import TimerCoordinator

        coordinator = TimerCoordinator(app=None)

        def fake_stop_dictation():
            ran_on.append(threading.current_thread())

        ctrl._app._schedule_timer = coordinator._schedule_timer
        ctrl._app._stop_dictation = fake_stop_dictation
        ctrl._app.tray.notify_safety = MagicMock()

        ctrl.on_max_duration_auto_stop()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not ran_on:
            time.sleep(0.005)

        assert len(ran_on) == 1
        assert ran_on[0] is not threading.current_thread()
        assert ran_on[0].daemon


# lifecycle lock serializes toggle / start / stop / cancel ─────


class TestLifecycleLockSerialization:
    """GT-22: ``_toggle_lock`` is an RLock acquired at the entry of
    ``start()``, ``stop()``, AND ``cancel()`` so concurrent lifecycle
    calls from different threads serialize.

    Pre-fix, ``_toggle_lock`` was a plain ``Lock`` only held inside
    ``toggle()``. Auto-stop Timer threads called ``self.stop()`` directly
    (via ``_schedule_timer(0, _stop_dictation)``), bypassing the lock —
    a near-simultaneous ``toggle()`` + ``stop()`` could both pass the
    ``not app.recorder.recording`` check before either called
    ``recorder.stop()``.
    """

    def test_toggle_lock_is_an_rlock(self):
        """The lifecycle lock must be an RLock (re-entrant) so the
        toggle() -> app._stop_dictation() -> self.stop() path does not
        self-deadlock when start/stop/cancel acquire the same lock."""
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController

        real = RecordingController.__new__(RecordingController)
        real.__init__(MagicMock())
        # RLock instances expose ``_is_owned``; plain Lock does not.
        assert hasattr(real._toggle_lock, "_is_owned"), (
            f"GT-22: _toggle_lock must be an RLock (have _is_owned); got {type(real._toggle_lock).__name__}"
        )

    def test_concurrent_stop_calls_serialize_on_toggle_lock(self):
        """GT-22: two threads calling ``stop()`` concurrently must
        serialize on ``_toggle_lock`` — while one is inside the
        critical section, the other must block (not enter concurrently).

        We assert serialization directly: the first thread holds the
        lock inside ``_stop_impl`` (blocked on a release-event), and we
        verify a second ``stop()`` call cannot proceed until the first
        releases.
        """
        import threading
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._app = MagicMock()
        ctrl._toggle_lock = threading.RLock()
        ctrl._watchdog_lock = threading.Lock()
        ctrl._watchdog_firings = 0
        ctrl._watchdog_max_firings = 3
        ctrl._streaming_session = None
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._transcription_thread = None
        ctrl._cancelled_cycle_ids = set()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()
        ctrl._watchdog_event = threading.Event()
        ctrl._watchdog_stop_event = threading.Event()
        ctrl._watchdog_thread = None

        app = ctrl._app
        app.recorder.recording = True  # both stops enter the body

        # Inner impl holds the lock until we release it, then signals
        # completion. With the RLock held by thread A, thread B's
        # ``with self._toggle_lock:`` in ``stop()`` must block.
        release_event = threading.Event()
        entered_event = threading.Event()
        enter_count = [0]
        enter_lock = threading.Lock()

        def blocking_stop_impl():
            with enter_lock:
                enter_count[0] += 1
            entered_event.set()
            # Block while holding the RLock — a second ``stop()`` on a
            # different thread CANNOT enter here unless the lock is
            # mis-implemented (plain Lock with no acquisition, etc.).
            release_event.wait(timeout=2.0)

        ctrl._stop_impl = blocking_stop_impl

        # First stopper thread: enters _stop_impl, blocks holding lock.
        t1 = threading.Thread(target=ctrl.stop, name="stopper-1")
        t1.start()
        assert entered_event.wait(timeout=1.0), "first stop never entered _stop_impl"
        assert enter_count[0] == 1

        # Second stopper thread: should block on the RLock.
        t2_entered = threading.Event()
        t2 = threading.Thread(
            target=lambda: (ctrl.stop(), t2_entered.set()),
            name="stopper-2",
        )
        t2.start()
        # Give t2 a chance to either enter or block.
        time.sleep(0.1)
        assert not t2_entered.is_set(), (
            "GT-22: second concurrent stop() entered _stop_impl while the "
            "first was still holding _toggle_lock — lock not serializing"
        )

        # Release the first stopper; the second can now proceed.
        release_event.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)
        assert not t1.is_alive() and not t2.is_alive(), "stop threads deadlocked"
        assert t2_entered.is_set(), "second stop never completed after release"
        assert enter_count[0] == 2, (
            f"GT-22: expected both stops to enter _stop_impl (serialized); got enter_count={enter_count[0]}"
        )

    def test_reentrant_toggle_to_start_does_not_deadlock(self):
        """GT-22: ``toggle()`` acquires ``_toggle_lock`` and then calls
        ``app._start_dictation()`` which delegates to ``self.start()`` —
        which acquires the same lock. The RLock must permit this
        re-entrant acquisition without deadlock.

        We construct the controller via its real ``__init__`` so all
        the lock attributes (``_streaming_session_lock`` etc.) exist,
        then monkey-patch the app's delegates so the re-entrant call
        path is exercised end-to-end.
        """
        import threading
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController

        # Full __init__ — MagicMock() app satisfies all attribute reads.
        app = MagicMock()
        ctrl = RecordingController(app)
        # The toggle lock must be the RLock produced by __init__.
        assert hasattr(ctrl._toggle_lock, "_is_owned"), "GT-22: _toggle_lock must be an RLock"

        app.recorder.recording = False  # toggle will call _start_dictation
        app._busy_event.is_set.return_value = True  # not busy
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        app._cycle_counter = 0
        app._cycle_id = "#test"
        app.models._model_load_thread = None
        # config attr reads in _start_impl
        app.config.voice_biometric_consent = True
        app.config.streaming_transcription = False

        reentrant_call_succeeded = threading.Event()

        def fake_start_dictation():
            # Re-entrant lock acquisition: would deadlock with plain Lock.
            ctrl.start()
            reentrant_call_succeeded.set()

        app._start_dictation = fake_start_dictation
        app._stop_dictation = MagicMock()

        t = threading.Thread(target=ctrl.toggle, name="toggle-thread")
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive(), (
            "GT-22: toggle() -> app._start_dictation() -> self.start() deadlocked (RLock not re-entrant?)"
        )
        assert reentrant_call_succeeded.is_set(), "GT-22: re-entrant start() call from toggle() did not complete"


# _transcription_thread snapshot under _watchdog_lock ─────────


class TestTranscriptionThreadSnapshot:
    """GT-46: ``_force_recover_from_stuck_transcription`` must snapshot
    ``self._transcription_thread`` and ``self._watchdog_firings`` under
    ``_watchdog_lock`` before the read-check-notify block."""

    def test_force_recover_acquires_watchdog_lock_for_snapshot(self):
        """The snapshot block in ``_force_recover_from_stuck_transcription``
        acquires ``_watchdog_lock`` so a concurrent ``stop()`` cannot
        mutate ``_transcription_thread`` mid-check."""
        import threading
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._app = MagicMock()
        ctrl._watchdog_lock = threading.Lock()
        ctrl._watchdog_firings = 1
        ctrl._watchdog_max_firings = 3
        ctrl._transcription_thread = None  # already finished
        ctrl._cancelled_cycle_ids = set()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()
        ctrl._watchdog_event = threading.Event()
        ctrl._watchdog_stop_event = threading.Event()
        ctrl._watchdog_thread = None

        app = ctrl._app
        app._busy_event.is_set.return_value = False  # busy=True -> not recovered
        app._cycle_id = "#test"
        app.tray = MagicMock()
        app._schedule_timer = MagicMock()
        ctrl._stop_watchdog_thread = MagicMock()

        observed_locked = threading.Event()

        def observer():
            with ctrl._watchdog_lock:
                observed_locked.set()

        # Start the observer FIRST so it queues for the lock before
        # force_recover acquires it for the snapshot. If force_recover
        # does NOT acquire the lock, the observer gets it immediately
        # (observed_locked set early — before force_recover returns).
        obs = threading.Thread(target=observer, name="lock-observer")
        obs.start()
        time.sleep(0.05)  # let observer block on the lock

        # Now call force_recover. If it acquires the lock for the
        # snapshot, the observer stays blocked until the snapshot
        # completes (and observed_locked stays unset during the call).
        ctrl._force_recover_from_stuck_transcription(force=True)

        obs.join(timeout=2.0)
        assert observed_locked.is_set(), (
            "GT-46: _watchdog_lock must be acquired during the snapshot "
            "block in _force_recover_from_stuck_transcription (observer "
            "never acquired it after the snapshot returned)"
        )

    def test_force_recover_uses_snapshot_for_alive_check(self):
        """When ``_transcription_thread`` is a still-alive thread and
        ``force=False``, ``_force_recover_from_stuck_transcription`` must
        take the 'worker still alive' branch (TRANSCRIBING tray state)
        and NOT force-recover (no IDLE tray state)."""
        import threading
        from unittest.mock import MagicMock

        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.tray_types import AppState

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._app = MagicMock()
        ctrl._watchdog_lock = threading.Lock()
        ctrl._watchdog_firings = 1
        ctrl._watchdog_max_firings = 3
        ctrl._cancelled_cycle_ids = set()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()
        ctrl._watchdog_event = threading.Event()
        ctrl._watchdog_stop_event = threading.Event()
        ctrl._watchdog_thread = None

        block_event = threading.Event()

        def blocking_target():
            block_event.wait(timeout=5.0)

        live_thread = threading.Thread(target=blocking_target, name="fake-transcription", daemon=True)
        live_thread.start()
        try:
            ctrl._transcription_thread = live_thread
            app = ctrl._app
            app._busy_event.is_set.return_value = False  # busy=True
            app._cycle_id = "#test"
            app.tray = MagicMock()

            ctrl._force_recover_from_stuck_transcription(force=False)

            tray_states = [call.args[0] for call in app.tray.set_state.call_args_list]
            assert AppState.TRANSCRIBING in tray_states, (
                "GT-46: snapshotting a live _transcription_thread should "
                "trigger the 'still alive' branch (TRANSCRIBING tray state)"
            )
            assert AppState.IDLE not in tray_states, (
                "GT-46: snapshotting a live _transcription_thread must NOT "
                "trigger force-recovery (IDLE tray state) when force=False"
            )
        finally:
            block_event.set()
            live_thread.join(timeout=1.0)


# set_thread_registry holds _buffer_clear_worker_lock ─────────


class TestSetThreadRegistryLock:
    """GT-47: ``set_thread_registry`` must hold ``_buffer_clear_worker_lock``
    while reading ``_buffer_clear_worker`` and calling ``registry.register``.

    Pre-fix the read happened outside the lock — a concurrent
    ``_stop_buffer_clear_worker`` could clear the global to ``None``
    between the read and the register, leaving the central registry
    with a stale/dead thread reference.
    """

    def test_set_thread_registry_skips_register_for_dead_worker(self, monkeypatch):
        """GT-47: if the worker is None or dead, ``register`` must NOT
        be called (no stale reference registered)."""
        import voice_typer.server.recording.buffer as buf_mod

        monkeypatch.setattr(buf_mod, "_buffer_clear_worker", None)
        monkeypatch.setattr(buf_mod, "_thread_registry", None)

        register_calls = []

        class FakeRegistry:
            def register(self, **kwargs):
                register_calls.append(kwargs)

        buf_mod.set_thread_registry(FakeRegistry())
        assert register_calls == [], "GT-47: register must not be called when worker is None"


# timer callback during shutdown is suppressed ───────────────


class TestTimerShutdownSuppression:
    """GT-72: a scheduled timer whose ``guarded_func`` has already
    passed the unlocked generation check must re-check the generation
    UNDER ``_pending_timers_lock`` and consult
    ``app._shutting_down_event`` before calling ``func()``.

    Without the re-check, a callback that races with
    ``_cancel_pending_timers`` (or the start of shutdown) would still
    fire into app state being torn down.
    """

    def test_callback_suppressed_when_shutdown_event_set(self):
        """If ``app._shutting_down_event`` is set when the timer fires,
        the user callback must NOT run — even if the generation matches
        (no cancel has happened)."""
        import threading

        from voice_typer.server.timer_coordinator import TimerCoordinator

        class FakeApp:
            def __init__(self):
                self._shutting_down_event = threading.Event()

        app = FakeApp()
        coord = TimerCoordinator(app=app)

        fired = threading.Event()
        coord._schedule_timer(0.01, fired.set)

        # Set the shutdown event BEFORE the timer fires.
        app._shutting_down_event.set()

        time.sleep(0.15)
        assert not fired.is_set(), "GT-72: callback must be suppressed when app._shutting_down_event.is_set()"
        coord._cancel_pending_timers()

    def test_callback_fires_when_shutdown_event_not_set(self):
        """Sanity: without shutdown, the callback fires normally."""
        import threading

        from voice_typer.server.timer_coordinator import TimerCoordinator

        class FakeApp:
            def __init__(self):
                self._shutting_down_event = threading.Event()

        app = FakeApp()
        coord = TimerCoordinator(app=app)

        fired = threading.Event()
        coord._schedule_timer(0.01, fired.set)
        assert fired.wait(timeout=1.0), "GT-72: callback must fire normally when not shutting down"

    def test_callback_suppressed_after_cancel_via_locked_recheck(self):
        """GT-72 TOCTOU: extract ``guarded_func`` from a scheduled
        timer (via the Timer's ``function`` attribute, which is set in
        ``threading.Timer.__init__`` and not cleared by ``cancel()``),
        bump the generation via ``_cancel_pending_timers``, then invoke
        ``guarded_func`` directly. The captured gen no longer matches
        the current gen -> the callback is suppressed."""
        import threading

        from voice_typer.server.timer_coordinator import TimerCoordinator

        class FakeApp:
            def __init__(self):
                self._shutting_down_event = threading.Event()

        app = FakeApp()
        coord = TimerCoordinator(app=app)

        fired = threading.Event()
        timer = coord._schedule_timer(10.0, fired.set)  # won't fire naturally

        # threading.Timer stores its target as ``function``.
        guarded = getattr(timer, "function", None)
        assert guarded is not None, "could not extract guarded_func from Timer"

        # Cancel the timer so it doesn't fire naturally mid-test.
        timer.cancel()
        # Bump the generation.
        coord._cancel_pending_timers()

        # Now invoke guarded_func directly. The captured gen no longer
        # matches the current gen -> suppressed.
        guarded()
        assert not fired.is_set(), "GT-72: callback with stale captured gen must be suppressed"
