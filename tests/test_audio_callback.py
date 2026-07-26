"""RW-7 / RW-8 regression tests for the audio callback hot path.

RW-7: Audio-callback thread-spawn storm on silent (zero-filled) input.
  The device-disconnect handler was spawned once per zero-filled chunk
  after the warmup window, causing a thread-spawn storm on a truly
  silent (or disconnected) microphone. The fix is a re-entrancy guard
  on ``_device_disconnected`` at the top of the disconnect-detection
  block in ``_process_audio_chunk``.

RW-8: Audio callback blocks on IPC push + module imports.
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

import inspect
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

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
    monkeypatch.setattr(
        recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"}
    )


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


# ── RW-7: thread-spawn storm on silent input ──────────────────────


class TestRW7SilentInputThreadStorm:
    """RW-7: zero-filled indata must not spawn a thread-per-chunk storm."""

    def test_zero_filled_indata_does_not_spawn_disconnect_handler_storm(
        self, monkeypatch
    ):
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
            # short-circuited by the RW-7 guard.
            indata = np.zeros((512, 1), dtype=np.float32)
            for _ in range(100):
                r._current_callback(indata, 512, None, 0)

            # Wait for the audio worker to drain the ring buffer.
            deadline = time.perf_counter() + 3.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.01)

            assert len(r._ring_buffer) == 0, (
                "audio worker should drain all 100 zero-filled chunks"
            )
            assert spawn_count["n"] <= 1, (
                f"RW-7 regression: 100 zero-filled callbacks spawned "
                f"{spawn_count['n']} device-disconnect-handler threads "
                f"(expected at most 1). The re-entrancy guard on "
                f"_device_disconnected in _process_audio_chunk is "
                f"missing or broken."
            )
        finally:
            r.stop()

    def test_first_zero_chunk_after_warmup_does_spawn_one_handler(
        self, monkeypatch
    ):
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
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.01)
            assert spawn_count["n"] == 0, (
                f"non-zero chunks should not spawn disconnect handlers, "
                f"got {spawn_count['n']}"
            )

            # First zero-filled chunk: _chunk_count > 10 → enters
            # disconnect block → _device_disconnected is False →
            # spawns 1 handler.
            r._current_callback(np.zeros((512, 1), dtype=np.float32), 512, None, 0)
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.01)
            assert spawn_count["n"] == 1, (
                f"first zero-filled chunk after warmup should spawn "
                f"exactly 1 disconnect handler (the guard must not "
                f"suppress the FIRST detection), got {spawn_count['n']}"
            )
        finally:
            r.stop()

    def test_guard_does_not_suppress_redisconnect_after_restart(
        self, monkeypatch
    ):
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
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.01)

            zero = np.zeros((512, 1), dtype=np.float32)
            r._current_callback(zero, 512, None, 0)
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.01)
            assert spawn_count["n"] == 1, (
                f"first zero-filled chunk should spawn 1 handler, "
                f"got {spawn_count['n']}"
            )

            # Simulate successful restart: clear the disconnect flag
            # (this is what _handle_device_disconnect does on line ~804
            # when the stream restart succeeds).
            r._device_disconnected = False

            # Next zero-filled chunk should trigger a NEW disconnect
            # detection (the guard must not permanently suppress).
            r._current_callback(zero, 512, None, 0)
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.01)
            assert spawn_count["n"] == 2, (
                f"after successful restart (flag cleared), the next "
                f"zero-filled chunk should spawn a NEW handler, got "
                f"{spawn_count['n']} total"
            )
        finally:
            r.stop()


# ── RW-8: event worker thread lifecycle ───────────────────────────


class TestRW8EventWorkerLifecycle:
    """RW-8: the IPC event worker thread starts on start(), joins on
    stop()/discard(). Mirrors the ``TestAudioWorkerThreadLifecycle``
    suite (RT-SAFE-001) so the two workers are held to the same
    contract."""

    def test_event_worker_thread_not_running_before_start(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r._event_worker_thread is None, (
            "event worker thread must not exist before start()"
        )
        assert r._event_queue is not None, (
            "event queue must exist after __init__ (so _process_audio_chunk "
            "can unconditionally enqueue)"
        )

    def test_event_worker_thread_starts_on_start(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        try:
            assert r._event_worker_thread is not None, (
                "start() must create the event worker thread"
            )
            assert r._event_worker_thread.is_alive(), (
                "event worker thread must be alive after start()"
            )
            assert r._event_worker_thread.daemon, (
                "event worker thread must be a daemon so it never blocks "
                "process exit"
            )
            assert r._event_worker_thread.name == "event-worker", (
                "event worker thread must be named 'event-worker' for "
                "diagnostics (matches _EVENT_WORKER_THREAD_NAME)"
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

        assert r._event_worker_thread is None, (
            "stop() must set _event_worker_thread to None after joining"
        )

    def test_event_worker_thread_stops_on_discard(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.start()
        assert r._event_worker_thread is not None

        r.discard()

        assert r._event_worker_thread is None, (
            "discard() must set _event_worker_thread to None after joining"
        )

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
        assert r._event_worker_thread is None

        # Second session — must start a NEW thread
        r.start()
        second = r._event_worker_thread
        assert second is not None
        assert second.is_alive()
        assert second is not first, (
            "start() after stop() must create a NEW event worker thread, "
            "not reuse the dead one"
        )
        r.stop()
        assert r._event_worker_thread is None


# ── RW-8: non-blocking audio worker ────────────────────────────────


class TestRW8NonBlockingCallback:
    """RW-8: the audio worker must not block on event_bus.publish."""

    def test_audio_worker_does_not_block_on_slow_publish(self, monkeypatch):
        """Mock event_bus.publish to sleep 1 second; the audio worker
        must still drain the ring buffer within milliseconds.

        Pre-RW-8, ``event_bus.publish`` was called synchronously from
        ``_process_audio_chunk``. With a slow subscriber (mocked here
        to sleep 1s), the worker would block for 1s per clipping chunk,
        causing the ring buffer to overflow.

        Post-RW-8, the publish is routed through ``_event_queue`` and
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
        # import in recording.py (hoisted by RW-8).
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
            # If _process_audio_chunk blocked on publish (pre-RW-8),
            # the ring buffer would not drain for ~1 second.
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline:
                if len(r._ring_buffer) == 0:
                    break
                time.sleep(0.005)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            assert len(r._ring_buffer) == 0, (
                "audio worker should drain the ring buffer promptly even "
                "when event_bus.publish is slow"
            )
            assert elapsed_ms < 500, (
                f"RW-8 regression: audio worker took {elapsed_ms:.1f}ms "
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
        audio_clip_events = [
            e for e in published_events if e.get("type") == "audio_clip"
        ]
        assert len(audio_clip_events) >= 1, (
            "the queued audio_clip event should eventually be published "
            "by the event worker thread during stop()"
        )


# ── RW-8: all queued events are eventually published ──────────────


class TestRW8AllEventsPublished:
    """RW-8: all events pushed to the queue are eventually published."""

    def test_all_queued_events_are_eventually_published_on_stop(
        self, monkeypatch
    ):
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
                deadline = time.perf_counter() + 2.0
                while time.perf_counter() < deadline:
                    if len(r._ring_buffer) == 0:
                        break
                    time.sleep(0.005)
        finally:
            # stop() drains the event queue (drain=True) and publishes
            # every queued event before returning.
            r.stop()

        audio_clip_events = [
            e for e in published_events if e.get("type") == "audio_clip"
        ]
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

        test_events = [
            e for e in published_events if e.get("type") == "test_event"
        ]
        assert len(test_events) == 10, (
            f"stop() should drain all 10 queued events, got "
            f"{len(test_events)}. Events: {published_events}"
        )


# ── RW-8: hoisted imports (source inspection) ─────────────────────


class TestRW8HoistedImports:
    """RW-8: event_bus and compute_vad_prob are hoisted to module top,
    removing the per-chunk inline import from _process_audio_chunk."""

    @staticmethod
    def _module_top_source():
        from voice_typer.server import recording

        src = inspect.getsource(recording)
        lines = src.splitlines()
        first_def_idx = None
        for i, line in enumerate(lines):
            if line.startswith("class ") or line.startswith("def "):
                first_def_idx = i
                break
        assert first_def_idx is not None, "module has no top-level class/def"
        return "\n".join(lines[:first_def_idx])

    def test_event_bus_imported_at_module_top(self):
        module_top_src = self._module_top_source()
        assert "from voice_typer.server import event_bus" in module_top_src, (
            "RW-8: event_bus must be imported at module top, not inline"
        )

    def test_compute_vad_prob_imported_at_module_top(self):
        module_top_src = self._module_top_source()
        assert (
            "from voice_typer.server.vad import compute_vad_prob"
            in module_top_src
        ), (
            "RW-8: compute_vad_prob must be imported at module top, not inline"
        )

    def test_no_inline_event_bus_import_in_process_audio_chunk(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._process_audio_chunk)
        assert "from voice_typer.server import event_bus" not in src, (
            "RW-8: event_bus must not be imported inline in "
            "_process_audio_chunk (hoist to module top)"
        )

    def test_no_inline_vad_import_in_process_audio_chunk(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._process_audio_chunk)
        assert (
            "from voice_typer.server.vad import compute_vad_prob" not in src
        ), (
            "RW-8: compute_vad_prob must not be imported inline in "
            "_process_audio_chunk (hoist to module top)"
        )

    def test_event_bus_publish_not_called_in_process_audio_chunk(self):
        """RW-8: _process_audio_chunk must NOT call event_bus.publish
        directly — it must enqueue via self._event_queue.put instead.

        ZR-60: the ``self._event_queue.put_nowait`` call site was
        extracted from ``_process_audio_chunk`` into
        ``_detect_and_emit_clipping`` (which the orchestrator
        delegates to). The regression guard now inspects BOTH the
        orchestrator and the clipping helper — the original intent
        (no direct ``event_bus.publish`` from the audio worker, only
        ``self._event_queue.put*``) is preserved as long as the
        clipping helper uses ``put_nowait``.
        """
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._process_audio_chunk)
        assert "event_bus.publish" not in src, (
            "RW-8: _process_audio_chunk must not call event_bus.publish "
            "directly — route through self._event_queue.put instead"
        )
        # ZR-60: the put_nowait call site lives in the clipping helper
        # (extracted from _process_audio_chunk).
        clipping_src = inspect.getsource(Recorder._detect_and_emit_clipping)
        assert "event_bus.publish" not in clipping_src, (
            "RW-8: _detect_and_emit_clipping must not call event_bus.publish "
            "directly — route through self._event_queue.put instead"
        )
        assert "self._event_queue.put" in clipping_src, (
            "RW-8: _detect_and_emit_clipping must enqueue events via "
            "self._event_queue.put"
        )

    def test_event_worker_loop_calls_event_bus_publish(self):
        """RW-8: the event worker thread (not _process_audio_chunk) is
        where event_bus.publish is called."""
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._event_worker_loop)
        assert "event_bus.publish" in src, (
            "RW-8: _event_worker_loop must call event_bus.publish (the "
            "publish was moved off the audio worker onto this thread)"
        )
