"""regression tests for the audio callback hot path."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from tests.fixtures.recorder_test_helpers import snapshot_worker_threads, wait_for_workers_stopped
from tests.fixtures.wait_helpers import wait_until

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
        # No-args call → enumerate (returns iterable of devices).
        if not args and not kwargs:
            return [device_dict]
        return device_dict

    monkeypatch.setattr(recording_mod.sd, "query_devices", _query_devices)
    monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})


def _patch_count_disconnect_handler_spawns(monkeypatch):
    """suppress ``device-disconnect-handler`` thread spawns."""
    spawn_count = {"n": 0}
    real_thread_init = threading.Thread.__init__
    real_thread_start = threading.Thread.start

    def counting_init(self, *args, **kwargs):
        real_thread_init(self, *args, **kwargs)
        if self.name == "device-disconnect-handler":
            spawn_count["n"] += 1

    def counting_start(self):
        if self.name == "device-disconnect-handler":
            return
        real_thread_start(self)

    monkeypatch.setattr(threading.Thread, "__init__", counting_init)
    monkeypatch.setattr(threading.Thread, "start", counting_start)
    return spawn_count


class TestSilentInputThreadStorm:
    """zero-filled indata must not spawn a thread-per-chunk storm."""

    def test_zero_filled_indata_does_not_spawn_disconnect_handler_storm(self, monkeypatch):
        """100 zero-filled callbacks must spawn at most 1 disconnect handler."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)
        spawn_count = _patch_count_disconnect_handler_spawns(monkeypatch)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Push 100 zero-filled chunks. The first ~11 chunks warm up
            indata = np.zeros((512, 1), dtype=np.float32)
            for _ in range(100):
                r._current_callback(indata, 512, None, 0)

            # Wait for the audio worker to drain the ring buffer.
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=3.0)

            assert len(r._ring_buffer) == 0, "audio worker should drain all 100 zero-filled chunks"
            assert spawn_count["n"] <= 1, (
                f"regression: 100 zero-filled callbacks spawned "
                f"{spawn_count['n']} device-disconnect-handler threads "
                f"(expected at most 1). The re-entrancy guard on "
                f"_device_disconnected in _process_audio_chunk is "
                f"missing or broken."
            )
        finally:
            r.stop()

    def test_first_zero_chunk_after_warmup_does_spawn_one_handler(self, monkeypatch):
        """Sanity check: the guard must NOT suppress the FIRST legitimate"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)
        spawn_count = _patch_count_disconnect_handler_spawns(monkeypatch)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            loud = np.ones((512, 1), dtype=np.float32) * 0.1
            for _ in range(12):
                r._current_callback(loud, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            assert spawn_count["n"] == 0, (
                f"non-zero chunks should not spawn disconnect handlers, got {spawn_count['n']}"
            )

            # First zero-filled chunk: _chunk_count > 10 → enters
            r._current_callback(np.zeros((512, 1), dtype=np.float32), 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            assert spawn_count["n"] == 1, (
                f"first zero-filled chunk after warmup should spawn "
                f"exactly 1 disconnect handler (the guard must not "
                f"suppress the FIRST detection), got {spawn_count['n']}"
            )
        finally:
            r.stop()

    def test_guard_does_not_suppress_redisconnect_after_restart(self, monkeypatch):
        """subsequent zero-filled chunk must trigger a NEW disconnect"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)
        spawn_count = _patch_count_disconnect_handler_spawns(monkeypatch)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Warm up + trigger first disconnect.
            loud = np.ones((512, 1), dtype=np.float32) * 0.1
            for _ in range(12):
                r._current_callback(loud, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)

            zero = np.zeros((512, 1), dtype=np.float32)
            r._current_callback(zero, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            assert spawn_count["n"] == 1, f"first zero-filled chunk should spawn 1 handler, got {spawn_count['n']}"

            # Simulate successful restart: clear the disconnect flag
            r._devices._device_disconnected = False

            r._current_callback(zero, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            assert spawn_count["n"] == 2, (
                f"after successful restart (flag cleared), the next "
                f"zero-filled chunk should spawn a NEW handler, got "
                f"{spawn_count['n']} total"
            )
        finally:
            r.stop()


class TestEventWorkerLifecycle:
    """stop()/discard(). Mirrors the ``TestAudioWorkerThreadLifecycle``"""

    def test_event_worker_thread_not_running_before_start(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r._event_worker_thread is None, "event worker thread must not exist before start()"
        assert r._event_queue is not None, (
            "event queue must exist after __init__ (so _process_audio_chunk can unconditionally enqueue)"
        )

    def test_event_worker_thread_starts_on_start(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            assert r._event_worker_thread is not None, "start() must create the event worker thread"
            assert r._event_worker_thread.is_alive(), "event worker thread must be alive after start()"
            assert r._event_worker_thread.daemon, "event worker thread must be a daemon so it never blocks process exit"
            assert r._event_worker_thread.name == "event-worker", (
                "event worker thread must be named 'event-worker' for diagnostics (matches _EVENT_WORKER_THREAD_NAME)"
            )
        finally:
            r.stop()

    def test_event_worker_thread_stops_on_stop(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        # Thread-ownership baseline (S5 fix): snapshot BEFORE this test spawns
        baseline = snapshot_worker_threads()
        r = Recorder(config)
        r.start()
        assert r._event_worker_thread is not None

        r.stop()

        # -style load guard: a worker that outlived a timed-out join
        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            "stop() must set _event_worker_thread to None after joining"
        )

    def test_event_worker_thread_stops_on_discard(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        # Thread-ownership baseline (S5 fix): see the stop() variant above.
        baseline = snapshot_worker_threads()
        r = Recorder(config)
        r.start()
        assert r._event_worker_thread is not None

        r.discard()

        # -style load guard: see the stop() variant above.
        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), (
            "discard() must set _event_worker_thread to None after joining"
        )

    def test_event_worker_can_restart_after_stop(self, monkeypatch):
        """After stop(), a subsequent start() must start a NEW event"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        # Thread-ownership baseline (S5 fix): see the stop() variant above.
        baseline = snapshot_worker_threads()
        r = Recorder(config)

        # First session
        r.start()
        first = r._event_worker_thread
        assert first is not None
        assert first.is_alive()
        r.stop()
        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), "event worker must stop after stop()"

        # Second session, must start a NEW thread
        r.start()
        second = r._event_worker_thread
        assert second is not None
        assert second.is_alive()
        assert second is not first, "start() after stop() must create a NEW event worker thread, not reuse the dead one"
        r.stop()
        assert wait_for_workers_stopped(r, stop=r.stop, baseline=baseline), "event worker must stop after stop()"


class TestNonBlockingCallback:
    """the audio worker must not block on event_bus.publish."""

    def test_audio_worker_does_not_block_on_slow_publish(self, monkeypatch):
        """Mock event_bus.publish to sleep 1 second; the audio worker"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server import event_bus
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # Mock publish to sleep 1 second (the task-specified value).
        published_events = []
        publish_lock = threading.Lock()

        def slow_publish(event):
            time.sleep(1.0)
            with publish_lock:
                published_events.append(event)

        # Patch the module-level event_bus.publish reference. The event
        monkeypatch.setattr(event_bus, "publish", slow_publish)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            clipping = np.ones((512, 1), dtype=np.float32)
            t0 = time.perf_counter()
            r._current_callback(clipping, 512, None, 0)

            # Wait for the audio worker to drain the ring buffer.
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            assert len(r._ring_buffer) == 0, (
                "audio worker should drain the ring buffer promptly even when event_bus.publish is slow"
            )
            assert elapsed_ms < 500, (
                f"regression: audio worker took {elapsed_ms:.1f}ms "
                f"to process a clipping chunk with a 1s-slow publish. "
                f"The worker is blocking on event_bus.publish instead "
                f"of offloading to the event queue."
            )
        finally:
            r.stop()

        # The event was eventually published (after the 1s sleep) by
        audio_clip_events = [e for e in published_events if e.get("type") == "audio_clip"]
        assert len(audio_clip_events) >= 1, (
            "the queued audio_clip event should eventually be published by the event worker thread during stop()"
        )


class TestAllEventsPublished:
    """all events pushed to the queue are eventually published."""

    def test_all_queued_events_are_eventually_published_on_stop(self, monkeypatch):
        """Multiple clipping chunks enqueue multiple events; stop()"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server import event_bus
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        published_events = []
        publish_lock = threading.Lock()

        def recording_publish(event):
            with publish_lock:
                published_events.append(event)

        monkeypatch.setattr(event_bus, "publish", recording_publish)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Push 5 clipping chunks. The clipping event is throttled
            clipping = np.ones((512, 1), dtype=np.float32)
            for _ in range(5):
                # STATE-OWNERSHIP: the clip-throttle timestamp is
                r._audio_pipeline._last_clip_log_time = 0.0  # reset 1 Hz throttle
                r._current_callback(clipping, 512, None, 0)
                # Wait for this chunk to be processed by the worker.
                assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
        finally:
            r.stop()

        audio_clip_events = [e for e in published_events if e.get("type") == "audio_clip"]
        assert len(audio_clip_events) == 5, (
            f"expected 5 audio_clip events (one per clipping chunk), "
            f"got {len(audio_clip_events)}. Events: {published_events}"
        )

    def test_event_worker_drains_queue_on_stop(self, monkeypatch):
        """stop() must wait for the event worker to publish queued"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server import event_bus
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        published_events = []
        publish_lock = threading.Lock()

        def recording_publish(event):
            with publish_lock:
                published_events.append(event)

        monkeypatch.setattr(event_bus, "publish", recording_publish)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            for i in range(10):
                r._event_queue.put({"type": "test_event", "data": {"i": i}})
        finally:
            r.stop()

        test_events = [e for e in published_events if e.get("type") == "test_event"]
        assert len(test_events) == 10, (
            f"stop() should drain all 10 queued events, got {len(test_events)}. Events: {published_events}"
        )


class TestHoistedImports:
    """event_bus and compute_vad_prob are hoisted to module top,"""

    def test_event_bus_imported_at_module_top(self):
        """Behavioral: ``event_bus`` is accessible as a module-level"""
        import voice_typer.server.recording as recording
        from voice_typer.server import event_bus

        assert recording.event_bus is event_bus, (
            "event_bus must be imported at module top of the recording "
            "package (accessible as a module-level attribute, not inline)."
        )

    def test_compute_vad_prob_imported_at_module_top(self):
        """Behavioral: ``compute_vad_prob`` is accessible as a module-level"""
        import voice_typer.server.recording as recording
        from voice_typer.server.vad import compute_vad_prob

        assert recording.compute_vad_prob is compute_vad_prob, (
            "compute_vad_prob must be imported at module top of the "
            "recording package (accessible as a module-level attribute, not "
            "inline in _process_audio_chunk)."
        )

    def test_no_inline_event_bus_import_in_process_audio_chunk(self, monkeypatch):
        """Behavioral: pushing an audio chunk through the worker must NOT"""
        import builtins

        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        real_import = builtins.__import__
        event_bus_imports: list[str] = []

        def tracking_import(name, *args, **kwargs):
            if name == "voice_typer.server.event_bus" or name.endswith(".event_bus"):
                event_bus_imports.append(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", tracking_import)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Clear after init so only hot-path imports are counted.
            event_bus_imports.clear()

            # Push a clipping chunk through the audio worker.
            clipping = np.ones((512, 1), dtype=np.float32)
            r._last_clip_log_time = 0.0  # bypass the 1 Hz throttle
            r._current_callback(clipping, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)

            assert event_bus_imports == [], (
                "_process_audio_chunk triggered an inline import of "
                f"event_bus ({event_bus_imports}). event_bus must be imported "
                "at module top so the audio hot path doesn't pay per-chunk "
                "import overhead."
            )
        finally:
            r.stop()

    def test_no_inline_vad_import_in_process_audio_chunk(self, monkeypatch):
        """Behavioral: pushing an audio chunk through the worker must NOT"""
        import builtins

        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        real_import = builtins.__import__
        vad_imports: list[str] = []

        def tracking_import(name, *args, **kwargs):
            if name == "voice_typer.server.vad" or name.endswith(".vad"):
                vad_imports.append(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", tracking_import)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Clear after init so only hot-path imports are counted.
            vad_imports.clear()

            # Push a clipping chunk, exercises the full pipeline
            clipping = np.ones((512, 1), dtype=np.float32)
            r._last_clip_log_time = 0.0  # bypass the 1 Hz throttle
            r._current_callback(clipping, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)

            assert vad_imports == [], (
                "_process_audio_chunk triggered an inline import of "
                f"vad ({vad_imports}). compute_vad_prob must be imported at "
                "module top so the audio hot path doesn't pay per-chunk "
                "import overhead."
            )
        finally:
            r.stop()

    def test_event_bus_publish_not_called_in_process_audio_chunk(self, monkeypatch):
        """Behavioral: ``_process_audio_chunk`` must NOT call"""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server import event_bus
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # Spy on event_bus.publish, recording the calling thread name.
        publish_calls: list = []
        publish_lock = threading.Lock()

        def spy_publish(event):
            with publish_lock:
                publish_calls.append((threading.current_thread().name, event))

        monkeypatch.setattr(event_bus, "publish", spy_publish)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            clipping = np.ones((512, 1), dtype=np.float32)
            r._last_clip_log_time = 0.0  # bypass the 1 Hz throttle
            r._current_callback(clipping, 512, None, 0)

            # Wait for the audio worker to drain the ring buffer —
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)

            # event_bus.publish must NOT have been called from the
            with publish_lock:
                audio_worker_publishes = [c for c in publish_calls if c[0] == "audio-worker"]
            assert audio_worker_publishes == [], (
                "_process_audio_chunk called event_bus.publish "
                f"directly on the audio-worker thread ({len(audio_worker_publishes)} "
                f"calls: {audio_worker_publishes}). It must route via "
                "self._event_queue.put instead, so a slow IPC subscriber "
                "cannot stall the audio worker thread."
            )
        finally:
            r.stop()

    def test_event_worker_loop_calls_event_bus_publish(self):
        """``_event_queue`` and calls ``event_bus.publish`` for each queued"""
        from voice_typer.server import event_bus
        from voice_typer.server.recording import Recorder

        published: list = []
        original_publish = event_bus.publish

        def spy_publish(event):
            published.append(event)

        event_bus.publish = spy_publish
        try:
            config = MagicMock(sample_rate=16000, microphone=None)
            r = Recorder(config)

            test_event = {"type": "test_event", "data": {"i": 1}}
            r._event_queue.put(test_event)

            # Setting _event_stop_event makes the event worker loop
            r._event_stop_event.set()
            r._capture.event_worker_loop(r)

            assert test_event in published, (
                "_event_worker_loop must call event_bus.publish for "
                "each queued event. The publish call lives here (not in "
                "_process_audio_chunk) so a slow IPC subscriber cannot "
                "stall the audio worker thread."
            )
        finally:
            event_bus.publish = original_publish
