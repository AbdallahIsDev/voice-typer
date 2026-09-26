"""/ GT-24 regression tests for the recorder worker & stream lifecycle."""

from __future__ import annotations

import contextlib
import inspect
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.fixtures.recorder_test_helpers import (
    WORKER_THREAD_NAMES,
    snapshot_worker_threads,
    wait_for_workers_stopped,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class _OkStream:
    """No-op InputStream mock for tests that don't touch real audio."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _patch_ok_stream(monkeypatch, recording_mod):
    """Patch sounddevice with a no-op InputStream + permissive device query."""
    monkeypatch.setattr(recording_mod.sd, "InputStream", _OkStream)

    def _query_devices(*args, **kwargs):
        device_dict = {
            "max_input_channels": 1,
            "default_samplerate": 16000,
            "hostapi": 0,
            "index": 0,
            "name": "Mock Input",
        }
        if not args and not kwargs:
            return [device_dict]
        return device_dict

    monkeypatch.setattr(recording_mod.sd, "query_devices", _query_devices)
    monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})


class TestWorkerLifecycleLock:
    """``_worker_lifecycle_lock`` serializes the read-modify-write"""

    def test_lock_attribute_exists(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert isinstance(r._worker_lifecycle_lock, type(threading.Lock())), (
            "GT-23: _worker_lifecycle_lock must be a threading.Lock instance"
        )

    def test_start_audio_worker_holds_lock(self, monkeypatch):
        """Behavioral: ``_start_audio_worker`` must acquire"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        # Hold the lock, _start_audio_worker must block waiting for it.
        r._worker_lifecycle_lock.acquire()
        try:
            done_flag = threading.Event()

            def call_start():
                r._start_audio_worker()
                done_flag.set()

            t = threading.Thread(target=call_start, name="test-start-audio-worker")
            t.start()

            blocked = not done_flag.wait(timeout=0.3)
            assert blocked, (
                "GT-23: _start_audio_worker did NOT block when "
                "_worker_lifecycle_lock was held by another thread, it "
                "must acquire the lock around the read-check-create-start "
                "sequence so a concurrent _stop_audio_worker cannot "
                "observe a stale None mid-create."
            )

            r._worker_lifecycle_lock.release()
            completed = done_flag.wait(timeout=2.0)
            assert completed, "GT-23: _start_audio_worker did not complete after the lock was released."
            t.join(timeout=1.0)
        finally:
            with contextlib.suppress(RuntimeError):
                r._worker_lifecycle_lock.release()
            with contextlib.suppress(Exception):
                r._stop_audio_worker(timeout=0.5, drain=False)

    def test_stop_audio_worker_holds_lock(self, monkeypatch):
        """Behavioral: ``_stop_audio_worker`` must acquire"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._start_audio_worker()
        assert r._worker_thread is not None

        r._worker_lifecycle_lock.acquire()
        try:
            done_flag = threading.Event()

            def call_stop():
                r._stop_audio_worker(timeout=0.5, drain=False)
                done_flag.set()

            t = threading.Thread(target=call_stop, name="test-stop-audio-worker")
            t.start()

            blocked = not done_flag.wait(timeout=0.3)
            assert blocked, (
                "GT-23: _stop_audio_worker did NOT block when "
                "_worker_lifecycle_lock was held, it must acquire the "
                "lock around the read-check-clear-join-unregister sequence."
            )

            r._worker_lifecycle_lock.release()
            completed = done_flag.wait(timeout=2.0)
            assert completed, "GT-23: _stop_audio_worker did not complete after the lock was released."
            t.join(timeout=1.0)
        finally:
            with contextlib.suppress(RuntimeError):
                r._worker_lifecycle_lock.release()
            with contextlib.suppress(Exception):
                r._stop_audio_worker(timeout=0.5, drain=False)

    def test_stop_audio_worker_does_not_hold_self_lock_across_join(self, monkeypatch):
        """Behavioral: ``_stop_audio_worker`` must NOT acquire"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._start_audio_worker()
        assert r._worker_thread is not None

        real_lock = r._audio_pipeline._lock
        main_thread = threading.current_thread()
        main_thread_acquires: list[str] = []

        class GuardedLock:
            def acquire(self, blocking=True, timeout=-1):
                if threading.current_thread() is main_thread:
                    main_thread_acquires.append("acquire")
                    raise RuntimeError(
                        "GT-23: _stop_audio_worker attempted to acquire "
                        "self._lock, it must NOT hold self._lock across "
                        "thread.join() (would deadlock with the worker's "
                        "buffer-append critical section)."
                    )
                return real_lock.acquire(blocking=blocking, timeout=timeout)

            def release(self):
                return real_lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *args):
                self.release()
                return False

        r._audio_pipeline._lock = GuardedLock()
        try:
            r._stop_audio_worker(timeout=1.0, drain=False)
            assert main_thread_acquires == [], (
                "GT-23: _stop_audio_worker acquired self._lock (entries: "
                f"{main_thread_acquires}). It must NOT acquire self._lock, "
                "holding it across thread.join() would deadlock with "
                "_process_audio_chunk's buffer-append critical section."
            )
        finally:
            r._audio_pipeline._lock = real_lock
            if r._worker_thread is not None and r._worker_thread.is_alive():
                r._worker_stop_event.set()
                r._worker_thread.join(timeout=1.0)

    def test_start_event_worker_lock_is_acquired_at_call_site(self):
        """Collaborator-source: the event-worker start sequence must be"""
        import inspect

        from voice_typer.server.recording.recording_lifecycle import start_recording

        src = inspect.getsource(start_recording)
        lock_idx = src.find("with recorder._worker_lifecycle_lock:")
        assert lock_idx >= 0, (
            "GT-23: start_recording must acquire _worker_lifecycle_lock "
            "around the event-worker read-check-create-start sequence."
        )
        body_idx = src.find("start_event_worker_body", lock_idx)
        assert body_idx >= 0, (
            "GT-23: start_event_worker_body must be invoked INSIDE the ``with recorder._worker_lifecycle_lock:`` block."
        )

    def test_stop_event_worker_lock_is_acquired_at_call_sites(self):
        """Collaborator-source: the event-worker stop sequence must be"""
        import inspect

        from voice_typer.server.recording.recording_lifecycle import discard_recording, stop_recording

        for fn in (stop_recording, discard_recording):
            src = inspect.getsource(fn)
            lock_idx = src.find("with recorder._worker_lifecycle_lock:")
            assert lock_idx >= 0, (
                f"GT-23: {fn.__name__} must acquire _worker_lifecycle_lock "
                "around the event-worker read-check-clear-join-unregister sequence."
            )
            body_idx = src.find("stop_event_worker_body", lock_idx)
            assert body_idx >= 0, (
                f"GT-23: stop_event_worker_body must be invoked INSIDE the "
                f"``with recorder._worker_lifecycle_lock:`` block in {fn.__name__}."
            )

    def test_stop_event_worker_body_does_not_hold_self_lock_across_join(self, monkeypatch):
        """Behavioral: the event-worker stop body must NOT acquire"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        with r._worker_lifecycle_lock:
            r._capture.start_event_worker_body(r)
        assert r._event_worker_thread is not None

        real_lock = r._audio_pipeline._lock
        main_thread = threading.current_thread()
        main_thread_acquires: list[str] = []

        class GuardedLock:
            def acquire(self, blocking=True, timeout=-1):
                if threading.current_thread() is main_thread:
                    main_thread_acquires.append("acquire")
                    raise RuntimeError(
                        "GT-23: stop_event_worker_body attempted to acquire "
                        "self._lock, it must NOT hold self._lock across "
                        "thread.join() (would deadlock with the worker's "
                        "buffer-append critical section)."
                    )
                return real_lock.acquire(blocking=blocking, timeout=timeout)

            def release(self):
                return real_lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *args):
                self.release()
                return False

        r._audio_pipeline._lock = GuardedLock()
        try:
            r._capture.stop_event_worker_body(r, timeout=1.0, drain=False)
            assert main_thread_acquires == [], (
                "GT-23: stop_event_worker_body acquired self._lock (entries: "
                f"{main_thread_acquires}). It must NOT acquire self._lock, "
                "holding it across thread.join() would deadlock."
            )
        finally:
            r._audio_pipeline._lock = real_lock
            if r._event_worker_thread is not None and r._event_worker_thread.is_alive():
                r._event_stop_event.set()
                r._event_worker_thread.join(timeout=1.0)


class TestConcurrentStartStopNoLeak:
    """hammer ``start()`` and ``stop()`` from two threads; verify"""

    def test_concurrent_start_stop_no_leak(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # S5 thread-ownership: snapshot BEFORE the hammer spawns any
        baseline = snapshot_worker_threads()

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        errors: list[Exception] = []
        stop_flag = threading.Event()

        def starter():
            try:
                while not stop_flag.is_set():
                    r.start()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def stopper():
            try:
                while not stop_flag.is_set():
                    r.stop()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=starter, name="gt23-starter")
        t2 = threading.Thread(target=stopper, name="gt23-stopper")
        t1.start()
        t2.start()
        # Hammer for 500ms, long enough to expose the race pre-fix.
        stop_flag.wait(0.5)
        stop_flag.set()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors, f"GT-23: concurrent start()/stop() raised: {errors}"

        # Final cleanup: ensure no worker thread is left running.
        r.stop()
        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            f"GT-23 regression: worker threads still alive after "
            f"concurrent start()/stop(): "
            f"{[(t.name, t.is_alive()) for t in threading.enumerate() if t.name in WORKER_THREAD_NAMES]} "
            f"(refs: worker={r._worker_thread!r}, event={r._event_worker_thread!r})."
        )

    def test_concurrent_start_discard_no_leak(self, monkeypatch):
        """Same as above but with ``discard()`` instead of ``stop()`` —"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # S5 thread-ownership baseline: see test_concurrent_start_stop_no_leak.
        baseline = snapshot_worker_threads()

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        errors: list[Exception] = []
        stop_flag = threading.Event()

        def starter():
            try:
                while not stop_flag.is_set():
                    r.start()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def discarder():
            try:
                while not stop_flag.is_set():
                    r.discard()
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=starter, name="gt23-starter-d")
        t2 = threading.Thread(target=discarder, name="gt23-discarder")
        t1.start()
        t2.start()
        # Same fixed-duration hammer window as the stop() variant above.
        stop_flag.wait(0.5)
        stop_flag.set()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not errors, f"GT-23: concurrent start()/discard() raised: {errors}"

        r.stop()
        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            f"GT-23 regression: worker threads still alive after "
            f"concurrent start()/discard(): "
            f"{[(t.name, t.is_alive()) for t in threading.enumerate() if t.name in WORKER_THREAD_NAMES]} "
            f"(refs: worker={r._worker_thread!r}, event={r._event_worker_thread!r})."
        )


class TestIdleStopStopsOrphanedWorkers:
    """GT-23R: a start()/discard() race can leave ``_recording_event``"""

    def test_stop_stops_live_event_worker_when_event_cleared(self, monkeypatch):
        """A live ``event-worker`` with ``_recording_event`` cleared must"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # S5 thread-ownership: snapshot BEFORE the test spawns any
        baseline = snapshot_worker_threads()

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        assert not r._recording_event.is_set()
        with r._worker_lifecycle_lock:
            r._capture.start_event_worker_body(r)
        assert r._event_worker_thread is not None
        assert r._event_worker_thread.is_alive()

        r.stop()

        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            "GT-23R: stop() fast-pathed on the cleared recording event "
            "and left the event worker running. stop() must stop live "
            "workers even when idle (start/discard race end-state)."
        )

    def test_stop_stops_live_audio_worker_when_event_cleared(self, monkeypatch):
        """Same contract for the ``audio-worker``: a live worker with"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # S5 thread-ownership: snapshot BEFORE the test spawns any
        baseline = snapshot_worker_threads()

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        assert not r._recording_event.is_set()
        r._start_audio_worker()
        assert r._worker_thread is not None
        assert r._worker_thread.is_alive()

        r.stop()

        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            "GT-23R: stop() fast-pathed on the cleared recording event "
            "and left the audio worker running. stop() must stop live "
            "workers even when idle (start/discard race end-state). "
            f"Threads: {[(t.name, t.is_alive()) for t in threading.enumerate() if t.name in WORKER_THREAD_NAMES]} "
            f"(refs: worker={r._worker_thread!r}, event={r._event_worker_thread!r})."
        )

    def test_discard_stops_live_worker_when_event_cleared(self, monkeypatch):
        """Same contract for ``discard()`` (the production ESC-cancel"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # S5 thread-ownership: snapshot BEFORE the test spawns any
        baseline = snapshot_worker_threads()

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        assert not r._recording_event.is_set()
        with r._worker_lifecycle_lock:
            r._capture.start_event_worker_body(r)
        assert r._event_worker_thread is not None
        assert r._event_worker_thread.is_alive()

        r.discard()

        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            "GT-23R: discard() fast-pathed on the cleared recording "
            "event and left the event worker running. discard() must "
            "stop live workers even when idle (start/discard race "
            "end-state)."
        )


class TestStreamFinishedCallbackGeneration:
    """``_stream_finished_callback`` must capture"""

    def test_stream_finished_callback_passes_captured_generation(self, monkeypatch):
        """current ``_stop_generation`` at scheduling time and pass it via"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        captured: dict = {}

        def capturing_spawn(*args, **kwargs):
            captured.update(kwargs)
            return False

        r._spawn_device_thread = capturing_spawn

        r._devices._device_disconnected = False
        r._user_stop_pending = False
        r._stream_lifecycle._stream = MagicMock()
        r._recording_event.clear()
        r._stop_generation = 7

        r._stream_finished_callback()

        assert "kwargs" in captured, "GT-24: _stream_finished_callback must pass kwargs to _spawn_device_thread."
        assert "_captured_generation" in captured["kwargs"], (
            "GT-24: _stream_finished_callback must pass _captured_generation "
            "in kwargs. Pre-fix the handler was scheduled with the default 0, "
            "defeating the bouncer on the first session."
        )
        assert captured["kwargs"]["_captured_generation"] == 7, (
            "GT-24: _stream_finished_callback must capture the CURRENT "
            f"_stop_generation (7), got {captured['kwargs']['_captured_generation']}."
        )

    @pytest.mark.skip(
        reason="behavioral equivalent is covered by "
        "test_stream_finished_callback_passes_captured_generation above "
        "— the kwargs contract (passing _captured_generation) is verified "
        "at runtime by intercepting _spawn_device_thread."
    )
    def test_stream_finished_callback_does_not_use_default_zero(self):
        """defeat the bouncer on the first session)."""
        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        spawn_src = inspect.getsource(DisconnectHandler.spawn_device_thread)
        callback_src = inspect.getsource(DisconnectHandler.stream_finished_callback_body)
        assert "threading.Thread(" in spawn_src
        assert "kwargs=" in callback_src, (
            "GT-24: the stream-finished scheduling body must pass kwargs= "
            "to the device-thread spawn helper, pre-fix the handler was "
            "scheduled with the default _captured_generation=0 which "
            "matched the initial _stop_generation=0, defeating the "
            "bouncer on the first session."
        )

    def test_handle_device_disconnect_bouncer_intact(self, monkeypatch):
        """Behavioral: ``_handle_device_disconnect`` must bail out"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        r._stop_generation = 10
        r._recording_event.set()
        initial_retries = r._devices._device_disconnect_retries

        r._handle_device_disconnect(_captured_generation=3)

        assert r._devices._device_disconnect_retries == initial_retries, (
            "GT-24: _handle_device_disconnect incremented "
            f"_device_disconnect_retries (from {initial_retries} to "
            f"{r._devices._device_disconnect_retries}) despite _captured_generation "
            "(3) != _stop_generation (10). The bouncer must bail out "
            "before the retry/restart block."
        )

    def test_first_session_handler_bails_when_stop_runs_after_schedule(self, monkeypatch):
        """Behavioral: on the first session (``_stop_generation=0``),"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            assert r._stop_generation == 0, "first session must start with _stop_generation=0"

            # Simulate the callback capturing gen=0, then stop() running
            captured_gen = r._stop_generation  # this is what
            # _stream_finished_callback captures at scheduling time.

            r._stop_generation += 1

            # The bouncer should bail out, the handler was scheduled
            restarted_flag = {"called": False}
            original_resolve = r._devices._resolve_effective_sample_rate

            def tracking_resolve(*args, **kwargs):
                restarted_flag["called"] = True
                return original_resolve(*args, **kwargs)

            r._devices._resolve_effective_sample_rate = tracking_resolve

            r._handle_device_disconnect(_captured_generation=captured_gen)

            assert not restarted_flag["called"], (
                "GT-24 regression: _handle_device_disconnect proceeded to "
                "the restart block despite _stop_generation changing "
                f"({captured_gen} != {r._stop_generation}). The bouncer "
                "must bail out when a stop/start cycle happened between "
                "scheduling and execution."
            )
        finally:
            r._stop_generation = max(r._stop_generation - 1, 0)
            r.stop()


# _stream_lifecycle_lock structural checks ────────────────


class TestStreamLifecycleLock:
    """``_stream_lifecycle_lock`` serializes stream teardown"""

    def test_lock_attribute_exists(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert isinstance(r._stream_lifecycle_lock, type(threading.Lock())), (
            "GT-24: _stream_lifecycle_lock must be a threading.Lock instance"
        )

    def test_teardown_stream_uses_lock(self):
        """Behavioral: ``_teardown_stream`` must acquire"""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._stream_lifecycle._stream = MagicMock()

        real_lock = r._stream_lifecycle_lock
        acquire_calls: list = []

        class CountingLock:
            def acquire(self, blocking=True, timeout=-1):
                acquire_calls.append(("acquire", blocking))
                return real_lock.acquire(blocking=blocking, timeout=timeout)

            def release(self):
                acquire_calls.append("release")
                return real_lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *args):
                self.release()
                return False

        r._stream_lifecycle_lock = CountingLock()

        r._teardown_stream()

        assert any(isinstance(c, tuple) and c[0] == "acquire" for c in acquire_calls), (
            "GT-24: _teardown_stream did not acquire _stream_lifecycle_lock. "
            "It must serialize teardown against concurrent "
            "_handle_device_disconnect restart."
        )
        assert r._stream_lifecycle._stream is None, "GT-24: _teardown_stream did not tear down the stream."

    def test_handle_device_disconnect_restart_uses_lock(self, monkeypatch):
        """Behavioral: the restart block of ``_handle_device_disconnect``"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        r._stop_generation = 0
        r._recording_event.set()
        r._stream_lifecycle._stream = MagicMock()

        r._stream_lifecycle_lock.acquire()

        handler_done = threading.Event()

        def run_handler():
            r._handle_device_disconnect(_captured_generation=0)
            handler_done.set()

        t = threading.Thread(target=run_handler, name="test-restart-lock")
        t.start()

        try:
            blocked = not handler_done.wait(timeout=0.5)
            assert blocked, (
                "GT-24: _handle_device_disconnect did NOT block at the "
                "restart block when _stream_lifecycle_lock was held, the "
                "restart block must acquire the lock (blocking) so a "
                "concurrent stop() cannot mutate self._stream_lifecycle._stream mid-restart."
            )
            r._stop_generation = 1
        finally:
            r._stream_lifecycle_lock.release()
            completed = handler_done.wait(timeout=3.0)
            if not completed:
                r._recording_event.clear()
                r._stop_generation = 99
            t.join(timeout=1.0)

        assert completed, "GT-24: _handle_device_disconnect did not complete after the lock was released."

    def test_handle_device_disconnect_rechecks_bouncer_under_lock(self, monkeypatch):
        """Behavioral: the restart block must re-check"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        r._stop_generation = 0
        r._recording_event.set()
        r._stream_lifecycle._stream = MagicMock()

        restart_calls: list = []
        original_restart = r._disconnect_handler.restart_stream

        def tracking_restart(*args, **kwargs):
            restart_calls.append(args)
            return original_restart(*args, **kwargs)

        r._disconnect_handler.restart_stream = tracking_restart

        r._stream_lifecycle_lock.acquire()

        handler_done = threading.Event()

        def run_handler():
            r._handle_device_disconnect(_captured_generation=0)
            handler_done.set()

        t = threading.Thread(target=run_handler, name="test-recheck-bouncer")
        t.start()

        try:
            # Fixed grace, intentionally kept: the handler must be parked
            time.sleep(0.3)
            r._stop_generation = 1
        finally:
            r._stream_lifecycle_lock.release()
            completed = handler_done.wait(timeout=3.0)
            if not completed:
                r._recording_event.clear()
                r._stop_generation = 99
            t.join(timeout=1.0)

        assert completed, "GT-24: _handle_device_disconnect did not complete after the lock was released."
        assert restart_calls == [], (
            "GT-24: _handle_device_disconnect called restart_stream "
            f"({len(restart_calls)} calls) despite _stop_generation "
            "changing between teardown and restart. The re-check under "
            "_stream_lifecycle_lock must bail out."
        )

    def test_teardown_stream_returns_without_blocking_when_lock_held(self):
        """``_teardown_stream`` must use non-blocking acquire so"""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Hold the lock from this thread.
        r._stream_lifecycle_lock.acquire()
        try:
            # _teardown_stream must NOT block, it should return immediately.
            done_flag = threading.Event()

            def call_teardown():
                r._teardown_stream()
                done_flag.set()

            t = threading.Thread(target=call_teardown, name="gt24-teardown")
            t.start()
            # If _teardown_stream blocks (regression), this assertion
            acquired = done_flag.wait(timeout=2.0)
            assert acquired, (
                "GT-24 regression: _teardown_stream blocked for >2s "
                "while another thread held _stream_lifecycle_lock. "
                "It must use non-blocking acquire so __del__ can't "
                "deadlock on a long-running stop()/discard()."
            )
        finally:
            r._stream_lifecycle_lock.release()
            t.join(timeout=1.0)

    def test_teardown_stream_idempotent_when_uncontended(self, monkeypatch):
        """when the lock is uncontended, ``_teardown_stream``"""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._stream_lifecycle._stream = MagicMock()
        # First call tears down.
        r._teardown_stream()
        assert r._stream_lifecycle._stream is None
        # Second call is a no-op (idempotent).
        r._teardown_stream()
        assert r._stream_lifecycle._stream is None


class TestStreamFinishedCallbackFirstSession:
    """``_stream_finished_callback`` must NOT spawn a disconnect handler"""

    def test_callback_does_not_spawn_handler_when_recording_active(self, monkeypatch):
        """callback must NOT spawn a disconnect handler, there's nothing"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # Count stream-finished-handler spawns.
        spawn_count = {"n": 0}
        real_thread_init = threading.Thread.__init__
        real_thread_start = threading.Thread.start

        def counting_init(self, *args, **kwargs):
            real_thread_init(self, *args, **kwargs)
            if self.name == "stream-finished-handler":
                spawn_count["n"] += 1

        def counting_start(self):
            if self.name == "stream-finished-handler":
                # Suppress the real handler, we only want to count
                return
            real_thread_start(self)

        monkeypatch.setattr(threading.Thread, "__init__", counting_init)
        monkeypatch.setattr(threading.Thread, "start", counting_start)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # First-session invariant: _stop_generation is 0.
            assert r._stop_generation == 0
            # Recording is active.
            assert r._recording_event.is_set()
            # No stop requested.
            assert r._user_stop_pending is False

            # Fire the callback, it must NOT spawn a handler because
            r._stream_finished_callback()

            assert spawn_count["n"] == 0, (
                "GT-24 regression: _stream_finished_callback spawned a "
                f"disconnect handler ({spawn_count['n']} spawns) on the "
                "first session while recording was active. The callback "
                "must only spawn a handler when recording was unambiguously "
                "stopped (not while it's still active)."
            )
        finally:
            monkeypatch.setattr(threading.Thread, "__init__", real_thread_init)
            monkeypatch.setattr(threading.Thread, "start", real_thread_start)
            r.stop()

    def test_callback_does_not_spawn_handler_when_user_stop_pending(self, monkeypatch):
        """callback must NOT spawn a handler, the stream finished because"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        spawn_count = {"n": 0}
        real_thread_init = threading.Thread.__init__
        real_thread_start = threading.Thread.start

        def counting_init(self, *args, **kwargs):
            real_thread_init(self, *args, **kwargs)
            if self.name == "stream-finished-handler":
                spawn_count["n"] += 1

        def counting_start(self):
            if self.name == "stream-finished-handler":
                return
            real_thread_start(self)

        monkeypatch.setattr(threading.Thread, "__init__", counting_init)
        monkeypatch.setattr(threading.Thread, "start", counting_start)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Simulate stop() having set _user_stop_pending but not yet
            r._user_stop_pending = True

            r._stream_finished_callback()

            assert spawn_count["n"] == 0, (
                "GT-24 regression: _stream_finished_callback spawned a "
                f"handler ({spawn_count['n']} spawns) when "
                "_user_stop_pending was True. The callback must suppress "
                "the spawn when stop() is in flight."
            )
        finally:
            monkeypatch.setattr(threading.Thread, "__init__", real_thread_init)
            monkeypatch.setattr(threading.Thread, "start", real_thread_start)
            r._user_stop_pending = False
            r.stop()

    def test_callback_spawns_handler_with_captured_generation_when_recording_cleared(self, monkeypatch):
        """When recording was unexpectedly cleared (``_recording_event``"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        captured_kwargs: dict = {}
        real_thread_init = threading.Thread.__init__
        real_thread_start = threading.Thread.start

        def capturing_init(self, *args, **kwargs):
            real_thread_init(self, *args, **kwargs)
            if self.name == "stream-finished-handler":
                # Capture the kwargs passed to the handler target.
                captured_kwargs["kwargs"] = dict(getattr(self, "_kwargs", {}) or {})

        def suppressing_start(self):
            if self.name == "stream-finished-handler":
                return
            real_thread_start(self)

        monkeypatch.setattr(threading.Thread, "__init__", capturing_init)
        monkeypatch.setattr(threading.Thread, "start", suppressing_start)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Bump _stop_generation to a non-zero value so we can verify
            r._stop_generation = 7

            # Simulate the unexpected-disconnect path: _recording_event
            r._recording_event.clear()
            r._user_stop_pending = False
            assert r._stream_lifecycle._stream is not None

            r._stream_finished_callback()

            assert "_captured_generation" in captured_kwargs["kwargs"], (
                "GT-24 regression: _stream_finished_callback spawned a "
                "handler without the _captured_generation kwarg. Pre-fix "
                "the handler was scheduled with the default 0, defeating "
                "the bouncer. Captured kwargs: "
                f"{captured_kwargs['kwargs']}"
            )
            assert captured_kwargs["kwargs"]["_captured_generation"] == 7, (
                "GT-24 regression: _stream_finished_callback captured "
                f"_captured_generation="
                f"{captured_kwargs['kwargs']['_captured_generation']} "
                "(expected 7, the current _stop_generation). Pre-fix the "
                "handler was scheduled with the default 0, which matched "
                "the initial _stop_generation=0 on the first session and "
                "defeated the bouncer."
            )
        finally:
            monkeypatch.setattr(threading.Thread, "__init__", real_thread_init)
            monkeypatch.setattr(threading.Thread, "start", real_thread_start)
            # Restore recording_event so stop() can clean up properly.
            r._recording_event.set()
            r._stop_generation = 0
            r._devices._device_disconnected = False
            r.stop()
