"""Tests for Sub-Agent G (Round R8) backend perf + RT-safety + leak fixes.

Covers four c-review findings:
- PERF-02 (G1): ``Recorder._vad_enabled`` cached instead of recomputed
  on every access. Refreshed by ``on_config_changed()`` + 5-second TTL
  safety net.
- PERF-03 (G2): ``level_monitor`` PortAudio callback does ONLY
  ``deque.append`` + ``Event.set()``. Heavy work runs on a worker
  thread. Test verifies the callback completes in <1 ms even with a
  50 ms slow filter chain.
- MEM-01 (G3): ``AsrBackendRegistry.load_with_fallback`` calls
  ``backend.unload()`` on the failed backend BEFORE unregistering +
  falling back, so partially-allocated torch/CUDA/model resources are
  released.
- CPU-02 (G4): ``VolumeDucker.initialize`` disables smart-duck when
  the active macOS backend is osascript (not CoreAudio).
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# G1 (PERF-02): _vad_enabled cache
# ═══════════════════════════════════════════════════════════════════════════


class TestVadEnabledCache:
    """``Recorder._vad_enabled`` is cached + refreshed via ``on_config_changed``."""

    def _make_recorder(self, **config_overrides):
        from voice_typer.server.recording import Recorder

        config = SimpleNamespace(
            sample_rate=16000,
            use_silero_vad=False,  # avoid loading torch
            noise_filter_highpass=False,
            noise_filter_gate=False,
            noise_filter_eq=False,
            noise_filter_compressor=False,
            noise_filter_limiter=False,
            noise_filter_notch=False,
            noise_suppression_method="none",
            **config_overrides,
        )
        return Recorder(config, audio_processor=None, thread_registry=None)

    def test_vad_enabled_returns_cached_value(self):
        """The property returns a cached bool instead of recomputing on every access."""
        rec = self._make_recorder()
        # First access computes the cache.
        v1 = rec._vad_enabled
        # Second access returns the cached value without re-running the
        # 6 getattr() calls.
        v2 = rec._vad_enabled
        assert v1 is v2
        # Sanity: with all noise filters off + method="none", VAD is disabled.
        assert v1 is False
        # Cache attribute is populated.
        assert rec._vad_enabled_cached is False

    def test_on_config_changed_refreshes_cache(self):
        """Calling on_config_changed picks up new config values immediately."""
        rec = self._make_recorder()
        assert rec._vad_enabled is False  # initial: nothing enabled

        # Flip a noise filter on.
        rec.config.noise_filter_highpass = True
        # WITHOUT on_config_changed, the cache still holds the old value
        # (within the 5-second TTL window).
        assert rec._vad_enabled is False, "cache should NOT refresh without explicit hook"

        # Call the explicit refresh hook.
        rec.on_config_changed()
        assert rec._vad_enabled is True, "cache must refresh after on_config_changed"

    def test_vad_enabled_ttl_safety_net_refreshes_stale_cache(self):
        """If on_config_changed is never called, the TTL safety net refreshes the cache."""
        rec = self._make_recorder()
        assert rec._vad_enabled is False

        # Force the cached timestamp into the past so the TTL is exceeded.
        rec._vad_enabled_cache_ts = time.perf_counter() - rec._VAD_ENABLED_CACHE_TTL_S - 1.0

        # Flip a noise filter on.
        rec.config.noise_filter_gate = True

        # The next access should detect the stale cache and re-compute.
        assert rec._vad_enabled is True, "TTL safety net must refresh stale cache"

    def test_vad_enabled_cache_is_bool_not_none_after_init(self):
        """After __init__, the cache attribute is a bool (not None).

        ``Recorder.__init__`` calls ``self._vad_enabled`` once (to log
        whether VAD is disabled) — so the cache is populated before
        ``__init__`` returns. This test guards against a regression
        that leaves the cache as ``None`` (which would force every
        subsequent access to recompute).
        """
        rec = self._make_recorder()
        assert isinstance(rec._vad_enabled_cached, bool)


# ═══════════════════════════════════════════════════════════════════════════
# G2 (PERF-03): level_monitor RT-safety
# ═══════════════════════════════════════════════════════════════════════════


def _reset_level_monitor_state():
    """Reset all module-level state in level_monitor between tests."""
    import voice_typer.server.level_monitor as lm

    lm._test_mode = False
    lm._test_chunks.clear()
    lm._test_raw_chunks.clear()
    lm._test_start_time = 0.0
    lm._test_duration = 10.0
    lm._monitor_sample_rate = 16000
    lm._monitor_active = False
    lm._monitor_stream = None
    lm._monitor_level = 0.0
    lm._monitor_peak = 0.0
    lm._monitor_mic_id = None
    lm._level_processor = None
    lm._dropped_level_chunks = 0
    lm._level_ring_buffer.clear()
    # Stop any worker thread from a previous test.
    lm._stop_level_worker()


@pytest.fixture(autouse=True)
def _reset_level_monitor():
    _reset_level_monitor_state()
    yield
    _reset_level_monitor_state()


def _wire_stream_with_callback_capture(monkeypatch):
    """Wire a mock sd.InputStream that captures the callback for direct invocation.

    Returns ``(mock_stream, captured_callback_holder)`` where the holder is a
    one-element list the test can read the captured callback from.
    """
    import sounddevice as sd

    holder = {"callback": None}

    class _Stream:
        def __init__(self, *args, **kwargs):
            holder["callback"] = kwargs.get("callback")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.InputStream = _Stream  # type: ignore[assignment]
    sd.query_devices.return_value = {
        "name": "Mock Mic",
        "default_samplerate": 16000,
        "max_input_channels": 1,
        "hostapi": 0,
    }
    return holder


class TestLevelMonitorRTSafety:
    """The level_monitor PortAudio callback must complete in <1ms even with a slow filter."""

    def test_callback_returns_in_under_1ms_with_slow_filter(self, monkeypatch):
        """RT-SAFE-001 (c-review PERF-03): the callback does ONLY deque.append + Event.set().

        With a 50 ms slow filter processor installed, the callback must
        still return in <1 ms. The slow filter runs on the worker
        thread, not the callback thread.
        """
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # Install a level processor that sleeps 50 ms — simulates
        # RNNoise CPU cost. If the callback ran this, the callback
        # would take >50 ms and miss the PortAudio deadline.
        slow_processor = MagicMock()
        slow_processor.process_chunk.side_effect = lambda chunk: (
            __import__("time").sleep(0.05),
            chunk,
        )[-1]
        lm._level_processor = slow_processor

        # Start monitoring — this also starts the worker thread.
        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        assert holder["callback"] is not None, "PortAudio callback must be captured"

        # Invoke the callback directly with a 512-sample mono chunk.
        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        t0 = time.perf_counter()
        holder["callback"](chunk, 512, None, None)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # The callback must complete in well under the ~32ms PortAudio
        # deadline — assert <5ms (generous upper bound for CI jitter;
        # the actual work is ~10µs).
        assert elapsed_ms < 5.0, (
            f"PortAudio callback took {elapsed_ms:.2f}ms — must be <5ms "
            "(RT-SAFE-001: heavy filter chain must run on worker thread, "
            "not the callback). The slow filter is still on the RT thread."
        )

        # Stop monitoring to clean up the worker thread.
        lm.stop_monitoring()

    def test_callback_pushes_chunk_to_ring_buffer(self, monkeypatch):
        """The callback enqueues the chunk for the worker to process."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True

        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        holder["callback"](chunk, 512, None, None)

        # The ring buffer should have at least one entry (the worker may
        # have already drained it — wait briefly for the worker to
        # process it, then verify the level was updated).
        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if lm._monitor_level > 0:
                break
            time.sleep(0.01)

        assert lm._monitor_level > 0, "Worker thread must process the chunk and update _monitor_level"
        lm.stop_monitoring()

    def test_worker_processes_chunks_under_lock(self, monkeypatch):
        """The worker thread, not the callback, runs the filter chain + RMS/peak."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # Push 5 chunks via the callback.
        for _ in range(5):
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            holder["callback"](chunk, 512, None, None)

        # Wait for the worker to drain + process all chunks.
        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if len(lm._level_ring_buffer) == 0 and lm._monitor_level > 0:
                break
            time.sleep(0.01)

        assert lm._monitor_level > 0
        # The smoothed level for a 0.25-amplitude chunk should be
        # roughly 0.25 * 0.4 = 0.1 (first iteration) and grow with
        # smoothing. Just verify it's positive and reasonable.
        assert 0 < lm._monitor_level < 1.0
        lm.stop_monitoring()

    def test_callback_does_not_block_on_lock_contention(self, monkeypatch):
        """Even if get_level() is holding _monitor_lock, the callback must return quickly."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)
        lm.start_monitoring(mic_id=None)

        # Hold the lock from another thread to simulate contention.
        contention_active = threading.Event()

        def hold_lock():
            with lm._monitor_lock:
                contention_active.set()
                # Hold for 100ms.
                time.sleep(0.1)

        t = threading.Thread(target=hold_lock)
        t.start()
        # Wait for the lock-holder thread to actually acquire the lock.
        contention_active.wait(timeout=1.0)

        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        t0 = time.perf_counter()
        # The callback should NOT block on _monitor_lock — it only
        # does deque.append + Event.set().
        holder["callback"](chunk, 512, None, None)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        t.join(timeout=2.0)

        # Even with lock contention, the callback must return quickly.
        # deque.append + Event.set() don't touch _monitor_lock.
        assert elapsed_ms < 50.0, (
            f"Callback took {elapsed_ms:.2f}ms under lock contention — "
            "the callback must NOT acquire _monitor_lock (RT-SAFE-001)."
        )
        lm.stop_monitoring()


# ═══════════════════════════════════════════════════════════════════════════
# G3 (MEM-01): ASR registry unload leak
# ═══════════════════════════════════════════════════════════════════════════


class TestAsrRegistryUnloadOnLoadFailure:
    """``load_with_fallback`` calls ``unload()`` on the failed backend before unregistering."""

    def _make_backend(self, load_raises: bool = True):
        """Build a mock backend that allocates a 'tensor' in load() then optionally raises.

        The ``unload()`` method clears the fake tensor, so the test can
        assert the tensor was released by checking the attribute after
        ``load_with_fallback`` returns.
        """
        backend = MagicMock()
        backend.allocated_tensor = None  # tracks whether unload freed the tensor

        def fake_load(progress_callback=None):
            # Simulate a partial load: allocate a 'tensor' (real engines
            # allocate torch tensors / CUDA contexts here).
            backend.allocated_tensor = object()
            if load_raises:
                raise RuntimeError("simulated load failure after partial allocation")

        def fake_unload():
            # Simulate releasing the tensor. Real engines set self._model = None.
            backend.allocated_tensor = None

        backend.load.side_effect = fake_load
        backend.unload.side_effect = fake_unload
        backend.is_loaded = False
        return backend

    def test_failed_backend_unload_called_before_unregister(self):
        """MEM-01: unload() is called on the failed backend BEFORE unregister + fallback."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        config = SimpleNamespace(asr_backend="qwen")
        registry = AsrBackendRegistry(config)

        failed_backend = self._make_backend(load_raises=True)
        whisper_backend = self._make_backend(load_raises=False)
        registry.register("qwen", failed_backend)
        registry.register("whisper", whisper_backend)

        result = registry.load_with_fallback()

        # Fallback returned the whisper backend.
        assert result is whisper_backend
        # MEM-01: the failed backend's unload() was called.
        failed_backend.unload.assert_called_once()
        # The fake tensor was released by unload().
        assert failed_backend.allocated_tensor is None, (
            "unload() must release the partially-allocated tensor — "
            "without it, the failed backend leaks the tensor until GC"
        )
        # The failed backend was unregistered.
        assert registry.get("qwen") is None
        # The whisper fallback's load was called and succeeded.
        whisper_backend.load.assert_called_once()

    def test_unload_failure_does_not_prevent_unregister(self):
        """If unload() itself raises, unregister + fallback still proceed."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        config = SimpleNamespace(asr_backend="qwen")
        registry = AsrBackendRegistry(config)

        failed_backend = self._make_backend(load_raises=True)
        # Make unload() also raise — simulates a corrupted model handle.
        failed_backend.unload.side_effect = RuntimeError("unload also broken")
        whisper_backend = self._make_backend(load_raises=False)
        registry.register("qwen", failed_backend)
        registry.register("whisper", whisper_backend)

        # Must not raise — the unload failure is caught.
        result = registry.load_with_fallback()

        assert result is whisper_backend
        # unload() was attempted (and failed).
        failed_backend.unload.assert_called_once()
        # The failed backend was STILL unregistered despite the unload failure.
        assert registry.get("qwen") is None

    def test_whisper_fallback_unload_on_failure(self):
        """MEM-01: if the whisper fallback ALSO fails, its unload() is called too."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        config = SimpleNamespace(asr_backend="qwen")
        registry = AsrBackendRegistry(config)

        failed_qwen = self._make_backend(load_raises=True)
        failed_whisper = self._make_backend(load_raises=True)
        registry.register("qwen", failed_qwen)
        registry.register("whisper", failed_whisper)

        result = registry.load_with_fallback()
        assert result is None  # both failed

        # Both backends must have been unloaded.
        failed_qwen.unload.assert_called_once()
        failed_whisper.unload.assert_called_once()
        # Both tensors released.
        assert failed_qwen.allocated_tensor is None
        assert failed_whisper.allocated_tensor is None

    def test_successful_load_does_not_call_unload(self):
        """If load() succeeds, unload() is NOT called (regression guard)."""
        from voice_typer.server.asr_registry import AsrBackendRegistry

        config = SimpleNamespace(asr_backend="whisper")
        registry = AsrBackendRegistry(config)

        ok_backend = self._make_backend(load_raises=False)
        registry.register("whisper", ok_backend)

        result = registry.load_with_fallback()
        assert result is ok_backend
        ok_backend.load.assert_called_once()
        ok_backend.unload.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# G4 (CPU-02): osascript smart-duck disable
# ═══════════════════════════════════════════════════════════════════════════


class TestOsascriptSmartDuckDisable:
    """``VolumeDucker.initialize`` disables smart-duck when backend is osascript."""

    def _make_backend(self, name: str, recommended_poll_ms: int = 500):
        """Build a duck-typed backend with the given ``name``.

        We don't extend ``VolumeBackend`` because the production check
        is duck-typed (it uses ``getattr(self._backend, "name", "")``).
        """
        backend = MagicMock()
        backend.name = name
        backend.recommended_poll_interval_ms = recommended_poll_ms
        backend.supports_per_session = False
        backend.initialize.return_value = True
        return backend

    def test_osascript_backend_disables_smart_duck(self):
        """CPU-02: when backend name contains 'osascript', smart-duck is disabled."""
        from voice_typer.server.volume_ducker import VolumeDucker

        backend = self._make_backend(name="osascript")
        ducker = VolumeDucker(backend=backend)
        # Default smart-duck state.
        assert ducker.smart_duck_enabled is True

        ducker.initialize()

        assert ducker.smart_duck_enabled is False, (
            "smart-duck must be disabled when the macOS osascript backend "
            "is active (CPU-02: osascript polling wastes 40-100% CPU)"
        )

    def test_coreaudio_backend_keeps_smart_duck(self):
        """CPU-02: CoreAudio (pyobjc) backend keeps smart-duck enabled."""
        from voice_typer.server.volume_ducker import VolumeDucker

        backend = self._make_backend(name="CoreAudio (pyobjc)", recommended_poll_ms=100)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        assert ducker.smart_duck_enabled is True, "smart-duck must remain enabled when CoreAudio (pyobjc) is active"

    def test_non_macos_backend_keeps_smart_duck(self):
        """CPU-02: non-macOS backends (Linux/Windows) are unaffected."""
        from voice_typer.server.volume_ducker import VolumeDucker

        # Simulate a Linux pactl backend (50ms per call — slower than
        # CoreAudio but not as catastrophic as osascript 200-500ms).
        backend = self._make_backend(name="pulseaudio", recommended_poll_ms=50)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        assert ducker.smart_duck_enabled is True, (
            "Non-macOS backends must keep smart-duck enabled — only the "
            "osascript path is slow enough to warrant disabling"
        )

    def test_osascript_disables_smart_duck_even_if_user_enabled_it(self):
        """CPU-02: the disable is unconditional when osascript is the backend."""
        from voice_typer.server.volume_ducker import VolumeDucker

        backend = self._make_backend(name="osascript")
        ducker = VolumeDucker(backend=backend)
        # User explicitly enabled smart-duck before initialize.
        ducker.set_smart_duck_enabled(True)
        assert ducker.smart_duck_enabled is True

        ducker.initialize()
        assert ducker.smart_duck_enabled is False


# ═══════════════════════════════════════════════════════════════════════════
# MEM-02: level_monitor test-chunk deques are bounded (maxlen) so a
# forgotten stop_test_recording() can't accumulate unbounded audio.
# ═══════════════════════════════════════════════════════════════════════════


class TestLevelMonitorTestChunkBounds:
    """MEM-02: _test_chunks / _test_raw_chunks are bounded deques.

    Guards against the regression where they were plain (unbounded)
    ``list[np.ndarray]`` that lingered in memory if the IPC client
    crashed mid-test and never called stop/cancel.

    The maxlen must be DYNAMIC — derived from the CURRENT device
    sample rate (16k / 44.1k / 48k) and the requested duration —
    NOT a hardcoded constant. A 48 kHz / 30 s test holds far more
    chunks than a 16 kHz / 10 s one.
    """

    def test_queues_are_bounded_deques_at_import(self):
        """At import, both queues are collections.deque with a maxlen set."""
        import collections

        import voice_typer.server.level_monitor as lm

        assert isinstance(lm._test_chunks, collections.deque)
        assert isinstance(lm._test_raw_chunks, collections.deque)
        # maxlen must be a positive int (the absolute hard cap ~2813
        # for 30s @ 48kHz), never None (None == unbounded).
        assert lm._test_chunks.maxlen is not None
        assert lm._test_chunks.maxlen > 0
        assert lm._test_raw_chunks.maxlen == lm._test_chunks.maxlen

    def test_capacity_tracks_requested_duration(self, monkeypatch):
        """start_test_recording sizes maxlen to duration * sr / 512 (+1)."""
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=10.0)
        # 10s * 16000 / 512 + 1 = 313
        assert lm._test_chunks.maxlen == int(10 * 16000 / 512) + 1
        # raw queue matches
        assert lm._test_raw_chunks.maxlen == lm._test_chunks.maxlen
        lm.cancel_test_recording()

    def test_capacity_tracks_device_sample_rate(self, monkeypatch):
        """Higher native rate => larger maxlen (more chunks per second)."""
        import voice_typer.server.level_monitor as lm

        holder = _wire_stream_with_callback_capture(monkeypatch)

        # 16 kHz
        holder["callback"] = None  # reset captured callback (unused here)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=10.0)
        cap_16k = lm._test_chunks.maxlen
        lm.cancel_test_recording()

        # 48 kHz — same 10s duration must yield a larger cap
        lm._monitor_sample_rate = 48000
        lm.start_test_recording(duration=10.0)
        cap_48k = lm._test_chunks.maxlen
        lm.cancel_test_recording()

        assert cap_48k == int(10 * 48000 / 512) + 1
        assert cap_48k > cap_16k * 2  # 3x rate => ~3x chunks

    def test_forgotten_stop_cannot_exceed_capacity(self, monkeypatch):
        """Even without stop/cancel, appends past maxlen drop the oldest.

        Simulates the IPC-client-crash scenario: start a test, push
        MANY more chunks than the capacity, and verify the deque never
        grows past maxlen (bounded memory, no leak).
        """
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)  # cap = 5*16000/512+1 = 157
        cap = lm._test_chunks.maxlen
        assert cap == int(5 * 16000 / 512) + 1

        # Push 10x the capacity worth of chunks.
        for i in range(cap * 10):
            chunk = np.ones((512, 1), dtype=np.float32) * (i % 256) / 256
            lm._test_chunks.append(chunk)
            lm._test_raw_chunks.append(chunk.copy())

        # Bounded: length never exceeds maxlen.
        assert len(lm._test_chunks) == cap
        assert len(lm._test_raw_chunks) == cap
        # raw + processed stay in lockstep (concatenation safety).
        assert len(lm._test_chunks) == len(lm._test_raw_chunks)

        # Now a real stop must still return exactly `cap` chunks of audio.
        result = lm.stop_test_recording()
        assert result["success"] is True
        assert result["audio_base64"] != ""

    def test_stop_preserves_bounded_deque_type(self, monkeypatch):
        """stop_test_recording clears in place — it must NOT reassign to [].

        A naive ``_test_chunks = []`` would clobber the deque back to
        an unbounded list and reintroduce the leak. Verify the type and
        maxlen survive a stop+restart cycle.
        """
        import collections

        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=10.0)
        cap_before = lm._test_chunks.maxlen
        _push = np.ones((512, 1), dtype=np.float32) * 0.25
        lm._test_chunks.append(_push)
        lm._test_raw_chunks.append(_push.copy())

        result = lm.stop_test_recording()
        assert result["success"] is True

        # After stop: still a bounded deque, cleared, maxlen preserved.
        assert isinstance(lm._test_chunks, collections.deque)
        assert lm._test_chunks.maxlen == cap_before
        assert len(lm._test_chunks) == 0

        # Restart: capacity recomputed, still bounded.
        lm.start_test_recording(duration=3.0)
        assert isinstance(lm._test_chunks, collections.deque)
        assert lm._test_chunks.maxlen == int(3 * 16000 / 512) + 1
        lm.cancel_test_recording()

    def test_auto_stop_does_not_clear_chunks_until_retrieved(self, monkeypatch):
        """Auto-stop must NOT clear chunks — frontend retrieves them after.

        Regression guard: if _do_auto_stop_test cleared _test_chunks,
        the post-autostop stop_test_recording() (used by the frontend
        to fetch audio) would return no audio. The deque bound caps the
        lingering audio at one test's worth, which is the intended fix.
        """
        import voice_typer.server.level_monitor as lm

        _wire_stream_with_callback_capture(monkeypatch)
        lm._monitor_sample_rate = 16000
        lm.start_test_recording(duration=5.0)
        for _ in range(3):
            _push = np.ones((512, 1), dtype=np.float32) * 0.25
            lm._test_chunks.append(_push)
            lm._test_raw_chunks.append(_push.copy())

        # Fire auto-stop directly (simulates the Timer callback).
        lm._do_auto_stop_test()
        assert lm._test_mode is False  # test ended
        # Chunks must STILL be present for retrieval.
        assert len(lm._test_chunks) == 3

        # Frontend now retrieves the audio.
        result = lm.stop_test_recording()
        assert result["success"] is True
        assert result["audio_base64"] != ""
        # After retrieval, cleared (and still a bounded deque).
        assert len(lm._test_chunks) == 0
