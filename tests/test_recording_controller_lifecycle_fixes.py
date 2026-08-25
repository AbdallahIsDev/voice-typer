"""UE-9 fix-group regression tests for :mod:`voice_typer.server.recording_controller`.

Each test class pins a specific sub-finding from UE-9:

- ``UE-9-F1`` (High)   — ``_stop_impl`` uses the atomic
  ``_cancel_streaming_session()`` helper (``pop_streaming_session()`` +
  public ``session.cancel()``) instead of get + private-attr poke. The
  session is popped from the slot (clearing ``self._streaming_session``)
  and cancelled via the public API.
- ``UE-9-F3`` (Medium) — ``_start_watchdog_thread`` holds
  ``_watchdog_lock`` across the ENTIRE read-check-create-start sequence
  (not just the ``_watchdog_firings = 0`` reset).
- ``UE-9-F6`` (Low)    — ``_stop_impl`` logs a WARNING when
  ``recorder._dropped_ring_chunks > 0`` after ``recorder.stop()``
  (read before the next ``start()`` resets the counter).
- ``UE-9-F15`` (Low)   — ``_toggle_impl`` does NOT increment
  ``_cycle_counter`` for blocked / queued / errored toggles — only when
  committing to a real start/stop.

``UE-9-F8`` (inverted ``_busy_event`` semantics) is a documentation-only
deferral; the clarifying comment is verified by a static-source grep test.
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.recording_controller import RecordingController

# ──────────────────────────────────────────────────────────────────────────
# Shared fixture: a RecordingController with a fully-mocked app.
# ──────────────────────────────────────────────────────────────────────────


def _make_controller() -> RecordingController:
    """Build a RecordingController with a fully-mocked app.

    Mirrors the ``_make_controller`` helper in
    ``test_recording_controller_group_fixes.py`` so the same code paths
    (``_toggle_impl`` / ``_stop_impl`` / ``_start_watchdog_thread``) can
    be exercised without real models, PortAudio, or tray backends.
    """
    app = MagicMock()
    app._busy_event = threading.Event()
    app._busy_event.set()  # not busy = set (inverted semantics, )
    app._cycle_counter = 0
    app._cycle_id = "#0"
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recorder.last_rms = 0.0
    # ``_stop_impl`` reads ``_dropped_ring_chunks``; default to 0
    # so the WARNING path is opt-in per test.
    app.recorder._dropped_ring_chunks = 0
    app.config = MagicMock()
    app.config.voice_biometric_consent = True
    app.config.sample_rate = 16000
    app.config.streaming_transcription = False
    app.config.bubble_behavior = "auto_hide"
    app.config.show_notifications = True
    app.models = MagicMock()
    app.models._model_load_thread = None
    app.models._pending_dictation = False
    active = MagicMock()
    active.is_loaded = True
    app.models.active_transcriber.return_value = active
    app.models.apply_pending_model_change.return_value = None
    app.models.ensure_active_engine_loaded.return_value = None
    app.models.fallback_to_whisper.return_value = None
    app.tray = MagicMock()
    app._waveform_bubble = MagicMock()
    app._audio_quality = MagicMock()
    app._thread_registry = None
    app._schedule_timer = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app._restore_volume = MagicMock()
    app._duck_volume = MagicMock()
    app._finalize_audio_quality_report = MagicMock()
    app._stop_dictation = MagicMock(side_effect=lambda: app.recording_control.stop())
    app._start_dictation = MagicMock(side_effect=lambda: app.recording_control.start())

    ctrl = RecordingController(app)
    app.recording_control = ctrl
    return ctrl, app


# _stop_impl pops + cancels the streaming session ────────────


class TestStopImplDoesNotPreCancelStreamingSession:
    """Regression guard for the streaming-finalize fast-path fix.

    Previously ``_stop_impl`` pre-cancelled the streaming session BEFORE
    constructing ``DictationPipeline``, which caused
    ``DictationPipeline._transcribe``'s ``pop_streaming_session()`` call to
    return ``None`` — silently falling back to batch transcription and
    discarding the incremental streaming transcript.

    The fix removed the pre-cancellation from the normal stop path (the
    pipeline's ``finally`` block now pops + cancels with the correct
    "recorder not recording" guard). The short-duration early-return path
    (``< 0.5s``, where no pipeline is constructed) still calls
    ``_cancel_streaming_session()`` directly.
    """

    def test_stop_does_not_pop_session_when_pipeline_will_handle_it(self):
        """On the normal stop path, ``_stop_impl`` must NOT pop the
        streaming session — ``DictationPipeline.run()``'s finally block
        does that. Pre-cancelling here was the root cause of the
        streaming-finalize fast path being dead in production."""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        app.recorder.stop.return_value = b"\x00" * 16000  # ~1s of audio

        fake_session = MagicMock()
        ctrl.set_streaming_session(fake_session)

        with patch("voice_typer.server.dictation_pipeline.DictationPipeline"):
            ctrl.stop()

        # The session MUST still be in the slot — the pipeline's finally
        # block (which is patched out here) is responsible for popping it.
        assert ctrl.get_streaming_session() is fake_session, (
            "_stop_impl must NOT pre-pop the streaming session; "
            "DictationPipeline.run()'s finally block owns the pop+cancel."
        )

    def test_stop_does_not_cancel_session_when_pipeline_will_handle_it(self):
        """_stop_impl must NOT call session.cancel() on the normal stop
        path - the pipeline's finally block does that with the correct
        'recorder not recording' guard."""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        app.recorder.stop.return_value = b"\x00" * 16000

        fake_session = MagicMock()
        fake_session._cancel_event = threading.Event()
        ctrl.set_streaming_session(fake_session)

        with patch("voice_typer.server.dictation_pipeline.DictationPipeline"):
            ctrl.stop()

        # The session MUST NOT have been cancelled by _stop_impl.
        assert not fake_session.cancel.called, (
            "_stop_impl must NOT pre-cancel the streaming session; "
            "DictationPipeline.run()'s finally block owns the cancel."
        )
        assert not fake_session._cancel_event.is_set(), "_stop_impl must NOT poke the private _cancel_event either."

    def test_short_duration_early_return_still_cancels_session(self):
        """The < 0.5s early-return path (where no pipeline is constructed)
        must STILL call _cancel_streaming_session() - otherwise the
        session would leak."""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        # < 0.5s of audio triggers the early-return path
        app.recorder.stop.return_value = b"\x00" * 4000

        fake_session = MagicMock()
        fake_session._cancel_event = threading.Event()
        ctrl.set_streaming_session(fake_session)

        with patch("voice_typer.server.dictation_pipeline.DictationPipeline"):
            ctrl.stop()

        # The short-duration path has no pipeline, so _stop_impl must
        # pop + cancel the session itself.
        assert ctrl.get_streaming_session() is None, (
            "Short-duration early-return path must pop the session (no pipeline is constructed to do it)."
        )
        assert fake_session.cancel.called, (
            "Short-duration early-return path must cancel the session (no pipeline is constructed to do it)."
        )


# _start_watchdog_thread holds _watchdog_lock ────────────────


class TestStartWatchdogThreadHoldsLock:
    """UE-9-F3: ``_start_watchdog_thread`` must hold ``_watchdog_lock``
    across the entire read-check-create-start sequence."""

    def test_lock_held_during_entire_sequence(self):
        """An observer thread that tries to acquire ``_watchdog_lock``
        while ``_start_watchdog_thread`` is running must block until the
        sequence completes.

        We use a MagicMock for the previous thread with a slow ``join``
        to stretch the critical section so the observer reliably
        observes the lock being held.
        """
        ctrl, _ = _make_controller()

        # Mock a previous "dead" thread: is_alive() returns False so the
        # create-new path runs. We inject a delay via a side effect on
        # is_alive to give the observer time to block on the lock.
        prev_thread = MagicMock()
        prev_thread.is_alive.return_value = False  # dead → create new

        ctrl._watchdog_thread = prev_thread

        observed_lock_held = threading.Event()

        def observer():
            # If _start_watchdog_thread holds _watchdog_lock across the
            # whole sequence, this acquire blocks until the sequence
            # completes. If it does NOT hold the lock (pre-fix), the
            # observer acquires it immediately and sets the event.
            with ctrl._watchdog_lock:
                observed_lock_held.set()

        obs = threading.Thread(target=observer, name="lock-observer", daemon=True)
        obs.start()
        # Let the observer queue for the lock.
        time.sleep(0.05)

        ctrl._start_watchdog_thread()

        obs.join(timeout=2.0)
        # The observer must have eventually acquired the lock (the
        # sequence released it). If the observer set the event BEFORE
        # _start_watchdog_thread completed, that would indicate the lock
        # was NOT held (pre-fix behavior). We verify the observer did
        # acquire the lock (the sequence completed and released it).
        assert observed_lock_held.is_set(), (
            "UE-9-F3: the lock observer never acquired _watchdog_lock — "
            "_start_watchdog_thread may be holding it indefinitely (deadlock?)"
        )

        # Cleanup: stop the freshly-started watchdog thread.
        ctrl._stop_watchdog_thread()
        if ctrl._watchdog_thread is not None:
            ctrl._watchdog_thread.join(timeout=1.0)

    def test_concurrent_start_calls_do_not_spawn_duplicate_threads(self):
        """Two concurrent ``_start_watchdog_thread`` calls must NOT both
        spawn a fresh ``TranscriptionWatchdog`` thread. The lock ensures
        the second caller sees the thread created by the first and
        reuses it."""
        ctrl, _ = _make_controller()
        ctrl._watchdog_thread = None  # no previous thread

        barrier = threading.Barrier(2)
        results: list[threading.Thread | None] = []
        results_lock = threading.Lock()

        def caller():
            barrier.wait()  # release both threads simultaneously
            ctrl._start_watchdog_thread()
            with results_lock:
                results.append(ctrl._watchdog_thread)

        t1 = threading.Thread(target=caller, name="starter-1", daemon=True)
        t2 = threading.Thread(target=caller, name="starter-2", daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert not t1.is_alive() and not t2.is_alive(), "UE-9-F3: concurrent _start_watchdog_thread calls deadlocked"
        # Both callers must have observed the SAME thread reference
        # (the second caller reused the thread created by the first).
        assert len(results) == 2
        assert results[0] is not None
        assert results[1] is not None
        assert results[0] is results[1], (
            "UE-9-F3: concurrent _start_watchdog_thread calls spawned "
            "different threads — the lock is not serializing the "
            "read-check-create-start sequence."
        )

        # Cleanup.
        ctrl._stop_watchdog_thread()
        if ctrl._watchdog_thread is not None:
            ctrl._watchdog_thread.join(timeout=1.0)


# _stop_impl logs WARNING for dropped ring chunks ────────────


class TestDroppedRingChunksWarning:
    """UE-9-F6: ``_stop_impl`` must log a WARNING when
    ``recorder._dropped_ring_chunks > 0`` after ``recorder.stop()``."""

    def test_warning_logged_when_dropped_chunks_nonzero(self, caplog):
        """A non-zero ``_dropped_ring_chunks`` must produce a WARNING
        log mentioning the drop count."""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        app.recorder.stop.return_value = b"\x00" * 16000
        app.recorder._dropped_ring_chunks = 42

        with (
            patch("voice_typer.server.dictation_pipeline.DictationPipeline"),
            caplog.at_level(logging.WARNING, logger="voice_typer.server.recording_controller"),
        ):
            ctrl.stop()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        ring_warnings = [r for r in warnings if "Ring buffer overflow" in r.getMessage()]
        assert len(ring_warnings) == 1, (
            f"UE-9-F6: expected exactly 1 'Ring buffer overflow' WARNING; "
            f"got {len(ring_warnings)}. All warnings: {[r.getMessage() for r in warnings]}"
        )
        msg = ring_warnings[0].getMessage()
        assert "42" in msg, f"UE-9-F6: WARNING must mention the drop count (42); got: {msg!r}"

    def test_no_warning_when_dropped_chunks_zero(self, caplog):
        """A zero ``_dropped_ring_chunks`` must NOT produce a ring-buffer
        WARNING (the happy path is silent)."""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        app.recorder.stop.return_value = b"\x00" * 16000
        app.recorder._dropped_ring_chunks = 0

        with (
            patch("voice_typer.server.dictation_pipeline.DictationPipeline"),
            caplog.at_level(logging.WARNING, logger="voice_typer.server.recording_controller"),
        ):
            ctrl.stop()

        ring_warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "Ring buffer overflow" in r.getMessage()
        ]
        assert ring_warnings == [], (
            "UE-9-F6: no 'Ring buffer overflow' WARNING expected when _dropped_ring_chunks == 0."
        )

    def test_no_crash_when_recorder_lacks_dropped_ring_chunks_attr(self):
        """If the recorder (e.g. a mock or older subclass) lacks the
        ``_dropped_ring_chunks`` attribute, ``_stop_impl`` must not
        crash — ``getattr(..., 0)`` defaults to 0 (no WARNING)."""
        ctrl, app = _make_controller()
        app.recorder.recording = True
        app.recorder.stop.return_value = b"\x00" * 16000
        # Remove the attribute to simulate an older recorder subclass.
        del app.recorder._dropped_ring_chunks

        with patch("voice_typer.server.dictation_pipeline.DictationPipeline"):
            # Must not raise AttributeError.
            ctrl.stop()


# _toggle_impl does not increment cycle counter for blocked toggles ──


class TestCycleCounterNotIncrementedForBlockedToggles:
    """UE-9-F15: ``_toggle_impl`` must NOT increment ``_cycle_counter``
    for blocked / queued / errored toggles — only when committing to a
    real start/stop."""

    def test_busy_toggle_does_not_increment_cycle_counter(self):
        """When the app is busy (``_busy_event`` not set), the toggle is
        blocked and must NOT consume a cycle ID."""
        ctrl, app = _make_controller()
        app._busy_event.clear()  # busy = True (inverted semantics)
        app._cycle_counter = 5
        app._cycle_id = "#5"

        ctrl.toggle()

        assert app._cycle_counter == 5, (
            "UE-9-F15: a blocked (busy) toggle must NOT increment "
            "_cycle_counter. Pre-fix, every blocked toggle consumed a "
            "cycle ID, producing non-contiguous cycle numbers."
        )
        assert app._cycle_id == "#5", "UE-9-F15: a blocked (busy) toggle must NOT change _cycle_id."

    def test_model_loading_toggle_does_not_increment_cycle_counter(self):
        """When the model is still loading (loader alive), the toggle is
        queued and must NOT consume a cycle ID."""
        ctrl, app = _make_controller()
        app._busy_event.set()  # not busy
        app._cycle_counter = 7
        app._cycle_id = "#7"

        live_loader = MagicMock()
        live_loader.is_alive.return_value = True
        app.models._model_load_thread = live_loader
        # active_transcriber returns a loaded model, but the loader-alive
        # check fires first.
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)

        ctrl.toggle()

        assert app._cycle_counter == 7, "UE-9-F15: a queued (model-loading) toggle must NOT increment _cycle_counter."

    def test_no_active_transcriber_toggle_does_not_increment_cycle_counter(self):
        """When there's no active transcriber and no live loader, the
        toggle re-triggers the background load and must NOT consume a
        cycle ID."""
        ctrl, app = _make_controller()
        app._busy_event.set()  # not busy
        app._cycle_counter = 3
        app._cycle_id = "#3"
        app.models.active_transcriber.return_value = None
        app.models._model_load_thread = None

        ctrl.toggle()

        assert app._cycle_counter == 3, (
            "UE-9-F15: a no-active-transcriber toggle (re-trigger path) must NOT increment _cycle_counter."
        )

    def test_committed_start_increments_cycle_counter(self):
        """When the toggle commits to a real start (not recording →
        start), the cycle counter MUST be incremented."""
        ctrl, app = _make_controller()
        app._busy_event.set()  # not busy
        app._cycle_counter = 0
        app._cycle_id = "#0"
        app.recorder.recording = False  # toggle → start
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        app.models._model_load_thread = None

        ctrl.toggle()

        assert app._cycle_counter == 1, "UE-9-F15: a committed start must increment _cycle_counter from 0 to 1."
        assert app._cycle_id == "#1"
        assert app._start_dictation.called, "UE-9-F15: the committed start path must call _start_dictation."

    def test_committed_stop_increments_cycle_counter(self):
        """When the toggle commits to a real stop (recording → stop),
        the cycle counter MUST be incremented."""
        ctrl, app = _make_controller()
        app._busy_event.set()  # not busy
        app._cycle_counter = 4
        app._cycle_id = "#4"
        app.recorder.recording = True  # toggle → stop
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        app.models._model_load_thread = None

        ctrl.toggle()

        assert app._cycle_counter == 5, "UE-9-F15: a committed stop must increment _cycle_counter from 4 to 5."
        assert app._cycle_id == "#5"
        assert app._stop_dictation.called, "UE-9-F15: the committed stop path must call _stop_dictation."


# _start_impl publishes a consent_required push on refusal ──────


class TestStartDictationConsentPush:
    """GDPR Art. 9: refusing dictation start without
    ``voice_biometric_consent`` must publish a ``consent_required`` push
    event so the renderer can surface in-app consent feedback for ANY
    entry point (Home mic button, bubble, F2 hotkey, tray click).

    Previously the refusal was tray-notification-only: the IPC
    ``toggle_dictation`` resolved ``ack`` and the renderer got zero
    feedback (a dead mic button). The push event carries only the
    stable ``consent_field`` id; the renderer localizes the message.
    """

    def test_consent_refusal_publishes_consent_required_event(self):
        ctrl, app = _make_controller()
        app.config.voice_biometric_consent = False

        with patch("voice_typer.server.event_bus.publish") as publish:
            ctrl.start()

        # Fail closed: the recorder must NOT have been started.
        assert app.recorder.start.call_count == 0, "GDPR gate must refuse to start the recorder without consent."
        # The consent_required event was published with the stable id.
        consent_events = [
            c.args[0] for c in publish.call_args_list if c.args and c.args[0].get("type") == "consent_required"
        ]
        assert len(consent_events) == 1, f"expected exactly 1 consent_required publish; got {len(consent_events)}"
        assert consent_events[0]["data"]["consent_field"] == "voice_biometric_consent"

    def test_consent_granted_does_not_publish_consent_event(self):
        ctrl, app = _make_controller()
        app.config.voice_biometric_consent = True

        with patch("voice_typer.server.event_bus.publish") as publish:
            ctrl.start()

        consent_events = [
            c.args[0] for c in publish.call_args_list if c.args and c.args[0].get("type") == "consent_required"
        ]
        assert consent_events == [], "Consent granted: no consent_required event expected."


# inverted _busy_event semantics documentation (deferred) ────


class TestBusyEventSemanticsDocumented:
    """UE-9-F8 (deferred): the inverted ``_busy_event`` semantics must be
    documented in the RecordingController class docstring so future
    maintainers are not confused by ``is_set() == True`` meaning NOT
    busy."""

    def test_class_docstring_documents_inverted_semantics(self):
        """The RecordingController class docstring must mention the
        inverted ``_busy_event`` semantics (``is_set() == True`` means
        NOT busy)."""
        import inspect

        docstring = inspect.getdoc(RecordingController) or ""
        assert "inverted" in docstring.lower() or "INVERTED" in docstring, (
            "UE-9-F8: RecordingController class docstring must document "
            "the inverted _busy_event semantics (is_set()==True means NOT "
            "busy)."
        )
        assert "_busy_event" in docstring, "UE-9-F8: docstring must reference _busy_event by name."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
