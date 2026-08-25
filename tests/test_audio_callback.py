"""regression tests for the audio callback hot path.

Audio-callback thread-spawn storm on silent (zero-filled) input.
  The device-disconnect handler was spawned once per zero-filled chunk
  after the warmup window, causing a thread-spawn storm on a truly
  silent (or disconnected) microphone. The fix is a re-entrancy guard
  on ``_device_disconnected`` at the top of the disconnect-detection
  block in ``_process_audio_chunk``.

Audio callback blocks on IPC push + module imports.
  The ``event_bus.publish`` call and the ``vad`` / ``event_bus``
  imports were inline in ``_process_audio_chunk`` (the audio worker
  hot path). The publish call blocked the worker on the IPC transport
  (a slow TCP subscriber could stall the pipeline and cause
  ring-buffer overflows). The fix:
    1. Hoist ``event_bus`` and ``compute_vad_prob`` imports to module top.
    2. Route the publish call through ``self._event_queue`` (a
       non-blocking ``queue.Queue``) drained by a dedicated
       ``_event_worker_thread``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from tests.fixtures.recorder_test_helpers import wait_for_workers_stopped
from tests.fixtures.wait_helpers import wait_until

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Test helpers ───────────────────────────────────────────────────


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
    """Patch sounddevice with a no-op InputStream + permissive device query.

    The query_devices mock accepts any call signature (positional or
    keyword) and returns either a single device dict or a one-element
    list, matching the two PortAudio call shapes used by ``start()``:
    ``sd.query_devices()`` (enumerate) and ``sd.query_devices(device)``
    / ``sd.query_devices(kind="input")`` (single device).
    """
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
    """Wrap ``threading.Thread.__init__`` / ``start`` to count and
    suppress ``device-disconnect-handler`` thread spawns.

    The count is incremented synchronously in ``__init__`` (before
    ``.start()``), so there is no race between the spawn and the count
    check. Other threads (audio-worker, event-worker, scipy-preloader)
    start normally — only ``device-disconnect-handler`` threads are
    suppressed (so the real handler doesn't restart the stream and
    clear ``_device_disconnected`` mid-test).
    """
    spawn_count = {"n": 0}
    real_thread_init = threading.Thread.__init__
    real_thread_start = threading.Thread.start

    def counting_init(self, *args, **kwargs):
        real_thread_init(self, *args, **kwargs)
        if self.name == "device-disconnect-handler":
            spawn_count["n"] += 1

    def counting_start(self):
        if self.name == "device-disconnect-handler":
            # Suppress the real disconnect handler — it would try to
            # restart the stream and clear _device_disconnected,
            # defeating the test.
            return
        real_thread_start(self)

    monkeypatch.setattr(threading.Thread, "__init__", counting_init)
    monkeypatch.setattr(threading.Thread, "start", counting_start)
    return spawn_count


# thread-spawn storm on silent input ──────────────────────


class TestSilentInputThreadStorm:
    """zero-filled indata must not spawn a thread-per-chunk storm."""

    def test_zero_filled_indata_does_not_spawn_disconnect_handler_storm(self, monkeypatch):
        """100 zero-filled callbacks must spawn at most 1 disconnect handler.

        Pre-fix, after ``_chunk_count`` exceeded 10, every subsequent
        zero-filled chunk re-entered the disconnect-detection block and
        spawned a new ``device-disconnect-handler`` thread (no
        re-entrancy guard on ``_device_disconnected``). With 100
        zero-filled callbacks, this spawned ~89 threads.

        Post-fix, the ``if self._device_disconnected: return`` guard at
        the top of the disconnect-detection block ensures at most 1
        handler is spawned; all subsequent zero-filled chunks return
        immediately.
        """
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)
        spawn_count = _patch_count_disconnect_handler_spawns(monkeypatch)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Push 100 zero-filled chunks. The first ~11 chunks warm up
            # _chunk_count past 10; chunk 12 triggers the first (and
            # only) disconnect detection. Chunks 13-100 must be
            # short-circuited by the  guard.
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
        """Sanity check: the guard must NOT suppress the FIRST legitimate
        disconnect detection. If it did, real device disconnects would
        be silently ignored."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)
        spawn_count = _patch_count_disconnect_handler_spawns(monkeypatch)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Warm up _chunk_count past 10 with non-zero chunks so the
            # first zero-filled chunk immediately enters the disconnect
            # detection block.
            loud = np.ones((512, 1), dtype=np.float32) * 0.1
            for _ in range(12):
                r._current_callback(loud, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            assert spawn_count["n"] == 0, (
                f"non-zero chunks should not spawn disconnect handlers, got {spawn_count['n']}"
            )

            # First zero-filled chunk: _chunk_count > 10 → enters
            # disconnect block → _device_disconnected is False →
            # spawns 1 handler.
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
        """After a successful restart clears ``_device_disconnected``, a
        subsequent zero-filled chunk must trigger a NEW disconnect
        detection. The guard must not permanently suppress detection."""
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
            # (this is what _handle_device_disconnect does on line ~804
            # when the stream restart succeeds).
            r._device_disconnected = False

            # Next zero-filled chunk should trigger a NEW disconnect
            # detection (the guard must not permanently suppress).
            r._current_callback(zero, 512, None, 0)
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
            assert spawn_count["n"] == 2, (
                f"after successful restart (flag cleared), the next "
                f"zero-filled chunk should spawn a NEW handler, got "
                f"{spawn_count['n']} total"
            )
        finally:
            r.stop()


# event worker thread lifecycle ───────────────────────────


class TestEventWorkerLifecycle:
    """the IPC event worker thread starts on start(), joins on
    stop()/discard(). Mirrors the ``TestAudioWorkerThreadLifecycle``
    suite (RT-SAFE-001) so the two workers are held to the same
    contract."""

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
        r = Recorder(config)
        r.start()
        assert r._event_worker_thread is not None

        r.stop()

        # GT-23-style load guard: a worker that outlived a timed-out join
        # leaves a stale ref (stop() fast-paths when idle and cannot reap
        # it) — poll the shared guard before asserting the ref cleared.
        assert wait_for_workers_stopped(r, stop=r.stop), "stop() must set _event_worker_thread to None after joining"

    def test_event_worker_thread_stops_on_discard(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        assert r._event_worker_thread is not None

        r.discard()

        # GT-23-style load guard — see the stop() variant above.
        assert wait_for_workers_stopped(r, stop=r.stop), "discard() must set _event_worker_thread to None after joining"

    def test_event_worker_can_restart_after_stop(self, monkeypatch):
        """After stop(), a subsequent start() must start a NEW event
        worker thread. Mirrors the audio-worker restart test."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        # First session
        r.start()
        first = r._event_worker_thread
        assert first is not None
        assert first.is_alive()
        r.stop()
        assert wait_for_workers_stopped(r, stop=r.stop), "event worker must stop after stop()"

        # Second session — must start a NEW thread
        r.start()
        second = r._event_worker_thread
        assert second is not None
        assert second.is_alive()
        assert second is not first, "start() after stop() must create a NEW event worker thread, not reuse the dead one"
        r.stop()
        assert wait_for_workers_stopped(r, stop=r.stop), "event worker must stop after stop()"


# non-blocking audio worker ────────────────────────────────


class TestNonBlockingCallback:
    """the audio worker must not block on event_bus.publish."""

    def test_audio_worker_does_not_block_on_slow_publish(self, monkeypatch):
        """Mock event_bus.publish to sleep 1 second; the audio worker
        must still drain the ring buffer within milliseconds.

        ``event_bus.publish`` was called synchronously from
        ``_process_audio_chunk``. With a slow subscriber (mocked here
        to sleep 1s), the worker would block for 1s per clipping chunk,
        causing the ring buffer to overflow.

        the publish is routed through ``_event_queue`` and
        drained by the event worker thread. The audio worker's
        ``_process_audio_chunk`` just does ``queue.put`` (non-blocking),
        so it returns in milliseconds even when publish is slow.
        """
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
        # worker thread calls ``event_bus.publish`` via the module-level
        # import in recording.py (hoisted by ).
        monkeypatch.setattr(event_bus, "publish", slow_publish)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            # Push a clipping chunk (peak >= 0.99) to trigger the
            # audio_clip IPC event enqueue.
            clipping = np.ones((512, 1), dtype=np.float32)
            t0 = time.perf_counter()
            r._current_callback(clipping, 512, None, 0)

            # Wait for the audio worker to drain the ring buffer.
            # If _process_audio_chunk blocked on publish (pre-),
            # the ring buffer would not drain for ~1 second.
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
            # stop() drains the event queue — with the 1s-slow publish,
            # this takes ~1s. The default _EVENT_WORKER_JOIN_TIMEOUT_S
            # (2.0s) covers it.
            r.stop()

        # The event was eventually published (after the 1s sleep) by
        # the event worker thread during stop()'s drain.
        audio_clip_events = [e for e in published_events if e.get("type") == "audio_clip"]
        assert len(audio_clip_events) >= 1, (
            "the queued audio_clip event should eventually be published by the event worker thread during stop()"
        )


# all queued events are eventually published ──────────────


class TestAllEventsPublished:
    """all events pushed to the queue are eventually published."""

    def test_all_queued_events_are_eventually_published_on_stop(self, monkeypatch):
        """Multiple clipping chunks enqueue multiple events; stop()
        drains the queue and publishes all of them."""
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
            # to 1 Hz in _process_audio_chunk, so to get multiple
            # events we reset _last_clip_log_time to 0 before each
            # chunk (bypasses the throttle for the test).
            clipping = np.ones((512, 1), dtype=np.float32)
            for _ in range(5):
                r._last_clip_log_time = 0.0  # reset 1 Hz throttle
                r._current_callback(clipping, 512, None, 0)
                # Wait for this chunk to be processed by the worker.
                assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)
        finally:
            # stop() drains the event queue (drain=True) and publishes
            # every queued event before returning.
            r.stop()

        audio_clip_events = [e for e in published_events if e.get("type") == "audio_clip"]
        assert len(audio_clip_events) == 5, (
            f"expected 5 audio_clip events (one per clipping chunk), "
            f"got {len(audio_clip_events)}. Events: {published_events}"
        )

    def test_event_worker_drains_queue_on_stop(self, monkeypatch):
        """stop() must wait for the event worker to publish queued
        events before returning. Verifies the drain=True path."""
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
            # Enqueue 10 events directly via the queue (bypasses the
            # audio worker entirely — tests the event worker drain
            # in isolation).
            for i in range(10):
                r._event_queue.put({"type": "test_event", "data": {"i": i}})
        finally:
            r.stop()

        test_events = [e for e in published_events if e.get("type") == "test_event"]
        assert len(test_events) == 10, (
            f"stop() should drain all 10 queued events, got {len(test_events)}. Events: {published_events}"
        )


# hoisted imports (behavioral) ───────────────────────────


class TestHoistedImports:
    """event_bus and compute_vad_prob are hoisted to module top,
    removing the per-chunk inline import from _process_audio_chunk.

    Behavioral equivalents of the original source-string tests:
    instead of grepping the source for the ``import`` statements, we
    verify at runtime that (1) the names are accessible as module-level
    attributes (which they would be only if imported at module top),
    (2) calling ``_process_audio_chunk`` does NOT trigger a fresh
    ``__import__`` of either module (which it would if the imports
    were inline), (3) ``_process_audio_chunk`` does NOT call
    ``event_bus.publish`` directly (events route through
    ``self._event_queue``), and (4) ``_event_worker_loop`` IS the
    single consumer that calls ``event_bus.publish``.
    """

    def test_event_bus_imported_at_module_top(self):
        """Behavioral: ``event_bus`` is accessible as a module-level
        attribute on the recording package. If it were only imported
        inline inside ``_process_audio_chunk``, the module-level
        reference would not exist (and the audio pipeline could not
        see it without re-importing)."""
        import voice_typer.server.recording as recording
        from voice_typer.server import event_bus

        assert recording.event_bus is event_bus, (
            "event_bus must be imported at module top of the recording "
            "package (accessible as a module-level attribute, not inline)."
        )

    def test_compute_vad_prob_imported_at_module_top(self):
        """Behavioral: ``compute_vad_prob`` is accessible as a module-level
        attribute on the recording package (and on the audio pipeline
        module that actually calls it)."""
        import voice_typer.server.recording as recording
        from voice_typer.server.vad import compute_vad_prob

        assert recording.compute_vad_prob is compute_vad_prob, (
            "compute_vad_prob must be imported at module top of the "
            "recording package (accessible as a module-level attribute, not "
            "inline in _process_audio_chunk)."
        )

    def test_no_inline_event_bus_import_in_process_audio_chunk(self, monkeypatch):
        """Behavioral: pushing an audio chunk through the worker must NOT
        trigger a fresh ``__import__`` of ``voice_typer.server.event_bus``.
        If the import were inline in ``_process_audio_chunk`` (the audio
        hot path), every chunk would re-enter the import system — defeating
        the hot-path optimization. We spy on ``builtins.__import__``
        during one chunk's processing and assert no event_bus import fires.

        The init-time imports (``VadProcessor.__init__``, etc.) happen
        during ``Recorder()`` / ``start()`` — we clear the spy log AFTER
        init and BEFORE pushing the chunk so only hot-path imports are
        counted.
        """
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
        """Behavioral: pushing an audio chunk through the worker must NOT
        trigger a fresh ``__import__`` of ``voice_typer.server.vad`` (or
        any submodule of it). If ``compute_vad_prob`` were imported inline
        in ``_process_audio_chunk``, every chunk would re-enter the import
        system. We spy on ``builtins.__import__`` during one chunk's
        processing and assert no vad import fires.

        The init-time vad imports (``VadProcessor.__init__`` /
        ``VadProcessor.reset``) happen during ``Recorder()`` / ``start()``
        — we clear the spy log AFTER init and BEFORE pushing the chunk so
        only hot-path imports are counted.
        """
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

            # Push a clipping chunk — exercises the full pipeline
            # (filter chain, RMS/peak, clipping detection, VAD state
            # machine) which would trigger a vad import if it were inline.
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
        """Behavioral: ``_process_audio_chunk`` must NOT call
        ``event_bus.publish`` directly. Instead, it routes events through
        ``self._event_queue.put_nowait`` (drained by the event worker
        thread). Verified by spying on ``event_bus.publish`` and recording
        the calling thread — no publish call may come from the
        ``audio-worker`` thread. Publishes from the ``event-worker``
        thread are expected (that's where the publish was moved to).

        The original source-string test also checked the clipping helper
        ``_detect_and_emit_clipping`` — the behavioral test exercises
        that helper implicitly (a clipping chunk triggers it).
        """
        import voice_typer.server.recording as recording_mod
        from voice_typer.server import event_bus
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        # Spy on event_bus.publish, recording the calling thread name.
        # The audio worker thread ("audio-worker") must NEVER call publish
        # — it routes via _event_queue.put_nowait. The event worker thread
        # ("event-worker") is the legitimate caller.
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
            # Push a clipping chunk (peak >= 0.99) to trigger the
            # audio_clip IPC event enqueue path inside
            # _detect_and_emit_clipping.
            clipping = np.ones((512, 1), dtype=np.float32)
            r._last_clip_log_time = 0.0  # bypass the 1 Hz throttle
            r._current_callback(clipping, 512, None, 0)

            # Wait for the audio worker to drain the ring buffer —
            # this guarantees _process_audio_chunk has finished.
            assert wait_until(lambda: len(r._ring_buffer) == 0, timeout=2.0)

            # event_bus.publish must NOT have been called from the
            # audio-worker thread. Any call from "audio-worker" means
            # _process_audio_chunk called publish directly (the regression).
            # Publishes from "event-worker" are expected.
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
        """Behavioral: ``_event_worker_loop`` is the single consumer of
        ``_event_queue`` and calls ``event_bus.publish`` for each queued
        event. Verified by enqueuing an event directly, setting the stop
        event (so the loop drains and returns), and running one iteration
        of the loop — the event must be published."""
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

            # Enqueue a test event directly on the queue (bypasses the
            # audio worker entirely — tests the event worker publish
            # path in isolation).
            test_event = {"type": "test_event", "data": {"i": 1}}
            r._event_queue.put(test_event)

            # Setting _event_stop_event makes _event_worker_loop drain
            # the queue via get_nowait and return on Empty — so the
            # loop runs to completion (publishes our event, then exits).
            r._event_stop_event.set()
            r._event_worker_loop()

            assert test_event in published, (
                "_event_worker_loop must call event_bus.publish for "
                "each queued event. The publish call lives here (not in "
                "_process_audio_chunk) so a slow IPC subscriber cannot "
                "stall the audio worker thread."
            )
        finally:
            event_bus.publish = original_publish
