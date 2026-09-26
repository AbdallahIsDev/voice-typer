"""regression tests for the recorder + capture fixes."""

from __future__ import annotations

import collections
import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_recorder():
    """Build a real ``Recorder`` with a MagicMock config (headless)."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder()


class TestCallbackExceptionCapture:
    """: ``dispatch_callback_body`` must wrap its body in try/except,"""

    def test_last_callback_error_attr_declared_in_init(self):
        """``_last_callback_error`` is declared on the owning"""
        r = _make_recorder()
        assert hasattr(r._capture, "_last_callback_error"), (
            ": AudioCallbackDispatcher.__init__ must declare _last_callback_error"
        )
        assert r._capture._last_callback_error is None, ": _last_callback_error must initialize to None"

    def test_dispatch_callback_body_stores_exception_and_reraises(self):
        """When the inner body raises, ``dispatch_callback_body`` must"""
        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        class _BoomEvent:
            def is_set(self) -> bool:
                raise RuntimeError(" simulated RT-callback bug")

        class _FakeRecorder:
            def __init__(self) -> None:
                self._recording_event = _BoomEvent()

        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros(4, dtype=np.float32)

        # The wrapper must re-raise the RuntimeError.
        with pytest.raises(RuntimeError, match=" simulated RT-callback bug"):
            dispatcher.dispatch_callback_body(fake, indata, 4, "t", "s")

        assert dispatcher._last_callback_error is not None, (
            "dispatch_callback_body did not store the exception on "
            "AudioCallbackDispatcher._last_callback_error, "
            "_stream_finished_callback cannot log the true cause of "
            "the stream abort."
        )
        assert isinstance(dispatcher._last_callback_error, RuntimeError)
        assert " simulated RT-callback bug" in str(dispatcher._last_callback_error)

    def test_dispatch_callback_body_no_exception_leaves_attr_none(self):
        """When the inner body succeeds, ``_last_callback_error`` stays"""
        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        class _FakeRecorder:
            def __init__(self) -> None:
                self._recording_event = threading.Event()
                self._recording_event.set()  # recording active → payload path
                self._ring_buffer = collections.deque(maxlen=64)
                self._dropped_ring_chunks = 0

            @staticmethod
            def _ensure_mono(arr):
                return arr

        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros(4, dtype=np.float32)
        payload = dispatcher.dispatch_callback_body(fake, indata, 4, "t", "s")
        assert payload is not None, "happy path must return a payload"
        assert dispatcher._last_callback_error is None, ": _last_callback_error must stay None on the happy path"

    def test_stream_finished_callback_logs_error_and_clears_attr(self, caplog):
        """``_stream_finished_callback`` must log the captured exception"""
        r = _make_recorder()
        # Simulate a captured RT-callback exception.
        captured_exc = RuntimeError(" simulated RT-callback bug")
        r._capture._last_callback_error = captured_exc

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.recording"):
            r._stream_finished_callback()

            # ERROR record with the exact message was emitted.
        error_records = [
            rec for rec in caplog.records if rec.levelname == "ERROR" and rec.name == "voice_typer.server.recording"
        ]
        assert any("stream finished due to callback exception" in rec.getMessage() for rec in error_records), (
            "_stream_finished_callback must log '[RECORDER] stream finished due to callback exception' at ERROR"
        )
        # The exc_info was attached (full traceback).
        assert any(rec.exc_info is not None and rec.exc_info[1] is captured_exc for rec in error_records), (
            "the ERROR record must carry the captured exception as exc_info"
        )
        # The attribute was cleared after logging.
        assert r._capture._last_callback_error is None, (
            "_stream_finished_callback must clear the owning dispatcher's "
            "_last_callback_error after logging so a subsequent genuine "
            "disconnect is not masked."
        )

    def test_stream_finished_callback_skips_disconnect_handler_when_error_set(self, monkeypatch):
        """
        When the owning dispatcher's ``_last_callback_error`` is set,
        ``_stream_finished_callback`` must NOT spawn the disconnect-retry
        """
        r = _make_recorder()
        r._capture._last_callback_error = RuntimeError(" bug")
        spawn_calls: list[str] = []
        monkeypatch.setattr(
            r,
            "_spawn_device_thread",
            lambda **kw: spawn_calls.append(kw.get("name", "?")),
        )
        r._stream_finished_callback()
        assert spawn_calls == [], (
            "_stream_finished_callback must NOT spawn the disconnect "
            "handler when _last_callback_error is set, the stream aborted "
            "because of a code bug, not a device disconnect."
        )

    def test_stream_finished_callback_no_error_falls_through_to_disconnect_path(self, monkeypatch):
        """When the owning dispatcher's ``_last_callback_error`` is None,"""
        r = _make_recorder()
        # No error set. Simulate the "unexpected disconnect" branch:
        r._capture._last_callback_error = None
        r._devices._device_disconnected = False
        r._user_stop_pending = False
        r._stream_lifecycle._stream = MagicMock()  # not None
        r._recording_event.clear()  # not set → unexpected disconnect
        spawn_calls: list[str] = []
        monkeypatch.setattr(
            r,
            "_spawn_device_thread",
            lambda **kw: spawn_calls.append(kw.get("name", "?")),
        )
        r._stream_finished_callback()
        # The disconnect handler WAS spawned (existing behavior preserved).
        assert any(name == "stream-finished-handler" for name in spawn_calls), (
            "regression: when _last_callback_error is None, the "
            "existing disconnect-detection path must still spawn the "
            "stream-finished-handler."
        )


class TestDiscardIdleFastPath:
    """``discard`` on an idle recorder (not recording)"""

    def test_discard_on_idle_recorder_is_noop(self, monkeypatch):
        """``discard()`` on a recorder that is NOT recording is a no-op:"""
        r = _make_recorder()
        # Idle state: not recording, no stream.
        assert not r._recording_event.is_set()
        r._stream_lifecycle._stream = None

        gen_before = r._stop_generation
        user_stop_before = r._user_stop_pending

        from voice_typer.server.recording import recording_lifecycle

        full_body_calls: list[int] = []

        def _tracking_discard(recorder):
            full_body_calls.append(1)

        monkeypatch.setattr(recording_lifecycle, "discard_recording", _tracking_discard)

        r.discard()

        # The full body was NOT invoked (idle fast-path).
        assert full_body_calls == [], (
            "discard on an idle recorder must NOT invoke discard_recording, the is_set() fast-path should return early."
        )
        # _stop_generation was NOT bumped.
        assert r._stop_generation == gen_before, (
            "discard on an idle recorder must NOT bump _stop_generation, that would race with a concurrent start()."
        )
        # _user_stop_pending was NOT flipped.
        assert r._user_stop_pending == user_stop_before, (
            "discard on an idle recorder must NOT set _user_stop_pending, that would race with a concurrent start()."
        )

    def test_discard_on_recording_recorder_runs_full_body(self, monkeypatch):
        """full body (regression guard, the idle fast-path must not"""
        r = _make_recorder()
        # Recording state.
        r._recording_event.set()
        r._stream_lifecycle._stream = MagicMock()
        r._effective_sr = 16000

        gen_before = r._stop_generation

        # Stub out the heavy parts of discard_recording so it doesn't
        from voice_typer.server.recording import recording_lifecycle

        def _stub_discard(recorder):
            recorder._recording_event.clear()
            recorder._user_stop_pending = True
            recorder._stop_generation += 1
            recorder._stream_lifecycle._stream = None

        monkeypatch.setattr(recording_lifecycle, "discard_recording", _stub_discard)

        r.discard()

        assert r._stop_generation == gen_before + 1, (
            "regression: discard on a recording recorder must still "
            "bump _stop_generation (the idle fast-path must not fire here)."
        )
        assert r._user_stop_pending is True
        assert not r._recording_event.is_set()

    def test_discard_idle_does_not_touch_stream(self, monkeypatch):
        """an idle recorder must NOT close it (the idle fast-path returns"""
        r = _make_recorder()
        # Idle state but with a stream reference (simulates start()
        assert not r._recording_event.is_set()
        mock_stream = MagicMock()
        r._stream_lifecycle._stream = mock_stream

        close_calls: list[int] = []
        original_close = mock_stream.close

        def _tracking_close():
            close_calls.append(1)
            return original_close()

        mock_stream.close = _tracking_close

        from voice_typer.server.recording import recording_lifecycle

        monkeypatch.setattr(recording_lifecycle, "discard_recording", lambda rec: None)

        r.discard()

        assert close_calls == [], (
            "discard on an idle recorder must NOT close the streama concurrent start() may have just opened it."
        )
        assert r._stream_lifecycle._stream is mock_stream, ": discard on an idle recorder must NOT null _stream."


class TestExplicitEventsInAudioWorkerLoop:
    """: ``audio_worker_loop`` accepts ``stop_event`` / ``wake_event``"""

    def test_worker_uses_explicit_stop_event_not_dynamic_attr(self):
        """When the worker is started with explicit ``stop_event`` /"""

        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        class _FakeRecorder:
            def __init__(self) -> None:
                self._old_stop = threading.Event()
                self._old_wake = threading.Event()
                self._new_stop = threading.Event()
                self._new_wake = threading.Event()
                self._worker_stop_event = self._new_stop
                self._worker_wake_event = self._new_wake
                self._ring_buffer = collections.deque(maxlen=64)
                self._process_calls: list = []

            def _process_audio_chunk(self, *args):
                self._process_calls.append(args)

        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)

        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake, fake._old_stop, fake._old_wake),
            name="test-wm8-worker",
            daemon=True,
        )
        t.start()

        fake._old_stop.set()
        fake._old_wake.set()

        t.join(timeout=2.0)
        assert not t.is_alive(), (
            "regression: the worker did NOT exit when its captured "
            "OLD stop_event was set. It is reading "
            "recorder._worker_stop_event dynamically (the NEW cleared "
            "event) and resuming its loop. SPSC invariant violation."
        )

    def test_worker_falls_back_to_dynamic_attr_when_events_none(self):
        """Backward compat: when ``stop_event`` / ``wake_event`` are None"""
        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        class _FakeRecorder:
            def __init__(self) -> None:
                self._worker_stop_event = threading.Event()
                self._worker_wake_event = threading.Event()
                self._ring_buffer = collections.deque(maxlen=64)

            def _process_audio_chunk(self, *args):
                pass

        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        # Start with no explicit events, should fall back.
        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake,),  # only recorder, no events
            name="test-wm8-fallback",
            daemon=True,
        )
        t.start()
        # Set the recorder's events (the fallback path reads these).
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()
        t.join(timeout=2.0)
        assert not t.is_alive(), (
            "backward-compat regression: when called without explicit "
            "events, the worker must fall back to recorder._worker_stop_event."
        )

    def test_start_audio_worker_body_passes_explicit_events_to_thread(self):
        """read ``recorder._worker_stop_event`` dynamically."""
        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        class _FakeRecorder:
            def __init__(self) -> None:
                self._worker_thread = None
                self._worker_stop_event = threading.Event()
                self._worker_wake_event = threading.Event()
                self._ring_buffer = collections.deque(maxlen=64)
                self._thread_registry = None
                # Record the args the thread was started with.
                self._captured_args: tuple = ()

            def record_loop_args(self, recorder, stop_event, wake_event):
                # Record the args so we can assert they were passed.
                self._captured_args = (stop_event, wake_event)
                # Exit immediately so the test doesn't hang.
                return

        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        dispatcher.audio_worker_loop = fake.record_loop_args
        dispatcher.start_audio_worker_body(fake)
        try:
            assert fake._worker_thread is not None
            fake._worker_thread.join(timeout=1.0)
            assert fake._captured_args == (
                fake._worker_stop_event,
                fake._worker_wake_event,
            ), (
                "start_audio_worker_body must pass the current "
                "stop_event / wake_event as explicit args to the thread "
                f"target. Got args={fake._captured_args!r}."
            )
        finally:
            # Best-effort cleanup.
            if fake._worker_thread is not None:
                fake._worker_stop_event.set()
                fake._worker_wake_event.set()
                fake._worker_thread.join(timeout=0.5)


class TestWarmUpResamplerNoneGuard:
    """: ``warm_up_resampler`` must handle ``_get_resample_poly``"""

    def test_warm_up_resampler_handles_none_poly(self, monkeypatch, caplog):
        """When ``_get_resample_poly()`` returns None, the method logs"""
        r = _make_recorder()
        # Patch the OWNING module: ``warm_up_resampler`` resolves
        monkeypatch.setattr("voice_typer.server.recording.resampling._get_resample_poly", lambda: None)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            # Must NOT raise TypeError.
            r.warm_up_resampler()

        warning_records = [
            rec for rec in caplog.records if rec.levelname == "WARNING" and rec.name == "voice_typer.server.recording"
        ]
        assert any("scipy not available" in rec.getMessage() for rec in warning_records), (
            "when _get_resample_poly returns None, warm_up_resampler must log the 'scipy not available' warning."
        )

    def test_warm_up_resampler_none_poly_does_not_log_as_failure(self, monkeypatch, caplog):
        """The None case must NOT be logged as \"Resampler warm-up failed\""""
        r = _make_recorder()
        # Patch the OWNING module (see the sibling test above).
        monkeypatch.setattr("voice_typer.server.recording.resampling._get_resample_poly", lambda: None)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            r.warm_up_resampler()

        # The misleading "Resampler warm-up failed" message must NOT appear.
        assert not any(
            "Resampler warm-up failed" in rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"
        ), (
            "the None case must NOT be logged as "
            "'Resampler warm-up failed', it's the expected scipy-unavailable "
            "state, not a transient failure."
        )


class TestEventWorkerNonDictWarning:
    """: the event worker loop must log a WARNING when it"""

    def test_non_dict_event_logs_warning(self, monkeypatch, caplog):
        """worker (start_event_worker_body drains stale events before"""
        r = _make_recorder()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            with r._worker_lifecycle_lock:
                r._capture.start_event_worker_body(r)
            # Push a non-dict, non-sentinel item onto the event queue.
            r._event_queue.put_nowait("not-a-dict-event")  # type: ignore[arg-type]
            import time as _time

            _time.sleep(0.3)
            with r._worker_lifecycle_lock:
                r._capture.stop_event_worker_body(r, timeout=1.0, drain=True)

        warning_records = [
            rec for rec in caplog.records if rec.levelname == "WARNING" and rec.name == "voice_typer.server.recording"
        ]
        assert any("Event worker skipped non-dict event" in rec.getMessage() for rec in warning_records), (
            "a non-dict event on the queue must be logged at "
            "WARNING with the 'Event worker skipped non-dict event' message. "
            f"Got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    def test_non_dict_event_warning_includes_type(self, monkeypatch, caplog):
        """The WARNING message must include the type of the skipped"""
        r = _make_recorder()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            with r._worker_lifecycle_lock:
                r._capture.start_event_worker_body(r)
            r._event_queue.put_nowait(42)  # type: ignore[arg-type]
            import time as _time

            _time.sleep(0.3)
            with r._worker_lifecycle_lock:
                r._capture.stop_event_worker_body(r, timeout=1.0, drain=True)

        # The message must mention the type, either "<class 'int'>" (Python 3)
        warning_messages = [
            rec.getMessage()
            for rec in caplog.records
            if rec.levelname == "WARNING"
            and rec.name == "voice_typer.server.recording"
            and "Event worker skipped non-dict event" in rec.getMessage()
        ]
        assert warning_messages, "expected a WARNING about the non-dict event"
        assert any("int" in msg for msg in warning_messages), (
            f": the WARNING must include the type of the skipped event. Got: {warning_messages}"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--no-cov", "--timeout=30"]))
