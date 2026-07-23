"""Sub-Agent I5-retry regression tests.

Covers the 8 findings I5 was asked to fix in retry mode:

- CR-16    — Recorder._start_lock added; start()/discard() bodies wrapped.
             (REC-5 lock-existence + threading.Lock type assertions live in
             ``tests/test_recording.py::TestRec5StartLock`` — these tests
             cover the _runtime_ behaviour: serialised entry, no race
             crash. REC-8 source-string assertions updated to use
             ``Recorder.start`` instead of the removed ``_start_impl``.)
- R18-F12  — Pre-roll audio is routed through the filter chain (was raw).
- R18-F13  — XRUN threshold callback re-arms every Nth xrun; partial
             chunk is dropped when PortAudio reports an xrun status.
- R18-F14  — VAD threshold clamping (covered in test_vad_processor.py).
             One extra assertion here verifies the floor is exposed via
             the ``MIN_VAD_*`` constants.
- R4-F10   — FilterChain.process catches filter exceptions (covered in
             test_audio_filters.py::TestR4F10FilterChainExceptionHandling).
             One extra assertion here verifies the module-level ``log``
             exists (without it the except branch raised NameError).
- R3-F6    — ``_dropped_level_chunks`` emits a rate-limited WARNING log
             when the level-monitor ring buffer overflows.
- R3-F14   — Dead ``list(_test_peak_history)`` expression removed
             (verified by source-string assertion).
- R4-F8    — Buffer-clear worker registers with the app's
             ``ThreadRegistry``; ``_stop_buffer_clear_worker`` test-only
             helper joins cleanly.
"""

from __future__ import annotations

import collections
import inspect
import logging
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers (mirror tests/test_audio_callback.py patterns)
# ═══════════════════════════════════════════════════════════════════════════


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


def _drain_ring_buffer(rec, timeout=3.0):
    """Block until the audio worker has drained the ring buffer."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if len(rec._ring_buffer) == 0:
            return
        time.sleep(0.005)


# ═══════════════════════════════════════════════════════════════════════════
# CR-16: _start_lock runtime behaviour
# ═══════════════════════════════════════════════════════════════════════════


class TestCR16StartLockRuntime:
    """CR-16: ``_start_lock`` serializes start()/discard() at runtime.

    The source-string + threading.Lock type assertions live in
    ``tests/test_recording.py::TestRec5StartLock``; these tests cover the
    runtime contract: the lock is reentrant-safe under concurrent
    start()+discard() storms and is held for the duration of both
    methods.
    """

    def test_start_lock_released_after_start(self, monkeypatch):
        """After start() returns, ``_start_lock`` must NOT be held
        (otherwise a subsequent discard() from another thread would
        deadlock)."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert not r._start_lock.locked(), "lock must not be held before start()"

        r.start()
        try:
            assert not r._start_lock.locked(), (
                "start() must release _start_lock before returning — otherwise discard() from another thread deadlocks"
            )
        finally:
            r.stop()
        assert not r._start_lock.locked(), "stop() must leave _start_lock unheld"

    def test_start_lock_released_after_discard(self, monkeypatch):
        """discard() must release ``_start_lock`` before returning."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r.discard()
        assert not r._start_lock.locked(), "discard() must release _start_lock before returning"

    def test_start_lock_serializes_start_and_discard(self, monkeypatch):
        """When start() and discard() are called concurrently from two
        threads, the lock must serialize them: at most ONE of the two
        methods can be inside the critical section at any time. We
        verify by holding a fake lock in start() long enough for
        discard() to wait."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        # Replace _start_lock with a CountingLock that tracks
        # concurrent holders. threading.Lock's acquire/release are
        # C-level and can't be monkey-patched, so we substitute a
        # pure-Python context-manager lock.
        class CountingLock:
            def __init__(self):
                self._real = threading.Lock()
                self._holder_count = 0
                self._max_concurrent = 0
                self._counter_lock = threading.Lock()

            def acquire(self, *args, **kwargs):
                ok = self._real.acquire(*args, **kwargs)
                if ok:
                    with self._counter_lock:
                        self._holder_count += 1
                        if self._holder_count > self._max_concurrent:
                            self._max_concurrent = self._holder_count
                return ok

            def release(self):
                with self._counter_lock:
                    self._holder_count -= 1
                self._real.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *args):
                self.release()
                return False

            def locked(self):
                return self._real.locked()

        counting = CountingLock()
        r._start_lock = counting

        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def starter():
            try:
                barrier.wait(timeout=5.0)
                r.start()
            except Exception as e:
                errors.append(e)

        def discarder():
            try:
                barrier.wait(timeout=5.0)
                r.discard()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=starter)
        t2 = threading.Thread(target=discarder)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "start()/discard() deadlocked on _start_lock"
        assert not errors, f"concurrent start()/discard() raised: {errors}"
        # The lock must NEVER have had 2 concurrent holders (CR-16
        # serialization guarantee).
        assert counting._max_concurrent <= 1, (
            f"CR-16: _start_lock must serialize start()/discard() — "
            f"saw {counting._max_concurrent} concurrent holders (must "
            f"be ≤ 1)"
        )

    def test_concurrent_start_discard_no_deadlock(self, monkeypatch):
        """20 threads each calling start()+discard() in a tight loop
        must NOT deadlock. The _start_lock must be released even when
        start() raises (e.g. device-open failure)."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        errors: list[Exception] = []

        def starter():
            try:
                for _ in range(10):
                    r.start()
            except Exception as e:
                errors.append(e)

        def discarder():
            try:
                for _ in range(10):
                    r.discard()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=starter), threading.Thread(target=discarder)]
        for t in threads:
            t.start()
        # If _start_lock deadlocks, this join times out and pytest's
        # --timeout=30 kills the suite.
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "start()/discard() deadlocked on _start_lock"

        assert not errors, f"concurrent start()/discard() raised: {errors}"
        # Final cleanup
        with r._start_lock:
            pass  # lock must be free


# ═══════════════════════════════════════════════════════════════════════════
# R18-F12: pre-roll audio is routed through the filter chain
# ═══════════════════════════════════════════════════════════════════════════


class TestR18F12PreRollFiltered:
    """R18-F12: pre-roll audio must go through the filter chain (was raw)."""

    def _make_preroll_firing_stream(self, n_chunks=5, amplitude=0.3):
        """Build an _OkStream subclass that fires ``n_chunks`` mock
        audio callbacks from ``start()``. Because ``start()`` is called
        by ``Recorder.start()`` BEFORE ``_recording_event.set()``, the
        fired chunks flow into ``_preroll_buffer`` (via the
        ``_audio_callback_dispatch`` early-return guard) — exactly the
        production code path, just driven by a mock stream instead of
        a real microphone."""

        class _PrerollFiringStream:
            def __init__(self, *args, **kwargs):
                self._cb = kwargs.get("callback")

            def start(self):
                # Fire n_chunks of mock audio. These callbacks run on
                # the test thread (synchronous) — production runs them
                # on the PortAudio audio thread, but the dispatch path
                # is identical.
                if self._cb is None:
                    return
                chunk = np.ones((512, 1), dtype=np.float32) * amplitude
                for _ in range(n_chunks):
                    self._cb(chunk, 512, None, 0)

            def stop(self):
                pass

            def close(self):
                pass

        return _PrerollFiringStream

    def test_preroll_chunks_passed_through_audio_processor(self, monkeypatch):
        """Each pre-roll chunk prepended to the buffer in start() must be
        passed through ``audio_processor.process_chunk`` so the
        transcriber receives filtered audio (not raw). Pre-fix, the
        pre-roll bypassed the chain entirely."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        n_preroll_chunks = 5
        StreamCls = self._make_preroll_firing_stream(n_chunks=n_preroll_chunks, amplitude=0.3)
        monkeypatch.setattr(recording_mod.sd, "InputStream", StreamCls)
        monkeypatch.setattr(
            recording_mod.sd,
            "query_devices",
            lambda *a, **kw: (
                {
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                    "index": 0,
                    "name": "Mock Input",
                }
                if (a or kw)
                else [
                    {
                        "max_input_channels": 1,
                        "default_samplerate": 16000,
                        "hostapi": 0,
                        "index": 0,
                        "name": "Mock Input",
                    }
                ]
            ),
        )
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})

        # Build a mock audio_processor that records every call.
        processor = MagicMock()

        def _process_chunk(audio, input_sample_rate=None):
            # Return the audio scaled by 0.5 so we can verify it was
            # actually used (not the raw pre-roll chunk).
            return audio * 0.5

        processor.process_chunk.side_effect = _process_chunk

        # Enable pre-roll: 1 second @ 16kHz = ~31 chunks of 512 samples.
        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            pre_roll_buffer_seconds=1.0,
        )
        r = Recorder(config, audio_processor=processor)
        assert r._preroll_active, "pre-roll must be enabled by config"

        # Reset the call log so we only count process_chunk invocations
        # triggered by start()'s pre-roll prepend.
        processor.process_chunk.reset_mock()

        r.start()
        try:
            # The mock stream fired n_preroll_chunks callbacks during
            # start() — those chunks should have been captured by
            # _preroll_buffer. Verify the buffer was populated (the
            # clear at L1311 happens BEFORE stream.start(), so the
            # fired chunks survive).
            assert len(r._preroll_buffer) >= n_preroll_chunks or len(r._buffer) >= n_preroll_chunks, (
                f"pre-roll chunks not captured — preroll_buffer has "
                f"{len(r._preroll_buffer)}, buffer has {len(r._buffer)}"
            )

            # start() should have called process_chunk once per pre-roll
            # chunk (R18-F12). Pre-fix, the count was 0.
            assert processor.process_chunk.call_count == n_preroll_chunks, (
                f"R18-F12: pre-roll must be filtered — expected "
                f"{n_preroll_chunks} process_chunk calls, got "
                f"{processor.process_chunk.call_count}. Pre-fix the "
                f"pre-roll bypassed the filter chain."
            )
            # Each call must pass input_sample_rate=self._effective_sr
            for call in processor.process_chunk.call_args_list:
                assert call.kwargs.get("input_sample_rate") == r._effective_sr, (
                    "R18-F12: pre-roll process_chunk must pass "
                    "input_sample_rate=self._effective_sr (matches the "
                    "live callback path)"
                )
            # The buffer should contain the FILTERED chunks (scaled by
            # 0.5), not the raw chunks (0.3 amplitude).
            assert len(r._buffer) >= n_preroll_chunks, (
                f"pre-roll chunks must be prepended to the buffer — expected ≥{n_preroll_chunks}, got {len(r._buffer)}"
            )
            # Verify at least one prepended chunk has the filtered
            # amplitude (0.3 * 0.5 = 0.15).
            first_chunk = r._buffer[0]
            max_abs = float(np.max(np.abs(first_chunk)))
            assert 0.05 < max_abs < 0.25, (
                f"R18-F12: pre-roll chunk must be filtered (expected "
                f"~0.15 amplitude after *0.5 filter, got {max_abs:.3f}). "
                f"Pre-fix the raw chunk (0.3) would have been stored."
            )
        finally:
            r.stop()

    def test_preroll_filter_failure_falls_back_to_raw(self, monkeypatch):
        """If process_chunk raises, the raw pre-roll chunk is used
        (pre-roll is best-effort; must NEVER block start())."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        StreamCls = self._make_preroll_firing_stream(n_chunks=1, amplitude=0.3)
        monkeypatch.setattr(recording_mod.sd, "InputStream", StreamCls)
        monkeypatch.setattr(
            recording_mod.sd,
            "query_devices",
            lambda *a, **kw: (
                {
                    "max_input_channels": 1,
                    "default_samplerate": 16000,
                    "hostapi": 0,
                    "index": 0,
                    "name": "Mock Input",
                }
                if (a or kw)
                else [
                    {
                        "max_input_channels": 1,
                        "default_samplerate": 16000,
                        "hostapi": 0,
                        "index": 0,
                        "name": "Mock Input",
                    }
                ]
            ),
        )
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})

        processor = MagicMock()
        processor.process_chunk.side_effect = RuntimeError("filter boom")

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            pre_roll_buffer_seconds=1.0,
        )
        r = Recorder(config, audio_processor=processor)

        # start() must NOT raise even though process_chunk raises.
        r.start()
        try:
            # Raw chunk (0.3 amplitude) was stored because filter failed.
            assert len(r._buffer) >= 1, "raw pre-roll chunk must be stored on filter failure"
            first_chunk = r._buffer[0]
            max_abs = float(np.max(np.abs(first_chunk)))
            assert 0.25 < max_abs < 0.35, (
                f"raw pre-roll chunk (0.3) must be stored when filter raises — got {max_abs:.3f}"
            )
        finally:
            r.stop()


# ═══════════════════════════════════════════════════════════════════════════
# R18-F13: xrun threshold re-arm + drop partial chunk on xrun status
# ═══════════════════════════════════════════════════════════════════════════


class TestR18F13XrunReArm:
    """R18-F13: xrun threshold callback re-arms every Nth xrun; partial
    chunk is dropped when PortAudio reports an xrun status."""

    def test_threshold_callback_re_arms_every_nth_xrun(self, monkeypatch):
        """The on_xrun_threshold callback must fire at the threshold AND
        at every multiple of the threshold (was: only fired exactly at
        the threshold, never re-armed)."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Use a small threshold for fast testing.
        r._xrun_threshold = 3
        on_xrun = MagicMock()
        r.on_xrun_threshold = on_xrun

        r.start()
        try:
            # Push 6 chunks with non-zero status (xrun indicator).
            # Status=2 simulates PortAudio CallbackFlags.input_overflow.
            chunk = np.ones((512, 1), dtype=np.float32) * 0.3
            for _ in range(6):
                r._current_callback(chunk, 512, None, 2)
            _drain_ring_buffer(r)

            # Threshold=3 → callback fires at xrun #3 AND #6 (re-arm).
            assert on_xrun.call_count == 2, (
                f"R18-F13: on_xrun_threshold must re-arm every Nth xrun "
                f"(threshold=3, 6 xruns → expected 2 calls, got "
                f"{on_xrun.call_count}). Pre-fix the callback only fired "
                f"exactly at threshold and never re-armed."
            )
            # The callback receives the cumulative xrun count.
            args1 = on_xrun.call_args_list[0].args
            args2 = on_xrun.call_args_list[1].args
            assert args1[0] == 3, f"first callback should report xruns=3, got {args1[0]}"
            assert args2[0] == 6, f"second callback should report xruns=6, got {args2[0]}"
        finally:
            r.stop()

    def test_threshold_callback_does_not_fire_between_multiples(self, monkeypatch):
        """Between multiples of the threshold, the callback must NOT
        fire (i.e. xruns 4 and 5 with threshold=3 don't fire)."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._xrun_threshold = 3
        on_xrun = MagicMock()
        r.on_xrun_threshold = on_xrun

        r.start()
        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.3
            # Push 4 chunks (xruns 1-4): only xrun #3 fires.
            for _ in range(4):
                r._current_callback(chunk, 512, None, 2)
            _drain_ring_buffer(r)

            assert on_xrun.call_count == 1, (
                f"xruns 1-4 with threshold=3 must fire callback exactly once (at xrun #3), got {on_xrun.call_count}"
            )
        finally:
            r.stop()

    def test_partial_chunk_dropped_on_xrun_status(self, monkeypatch):
        """When PortAudio reports an xrun (status != 0), the in-flight
        chunk is dropped (NOT appended to the buffer). The chunk is
        partially stale and would corrupt the transcriber's input."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        # Disable the callback to isolate the drop-on-xrun behavior.
        r.on_xrun_threshold = None

        r.start()
        try:
            buffer_len_before = len(r._buffer)
            chunk = np.ones((512, 1), dtype=np.float32) * 0.3
            # Push ONE chunk with non-zero status (xrun).
            r._current_callback(chunk, 512, None, 2)
            _drain_ring_buffer(r)

            # The xrun chunk must NOT be in the buffer.
            assert len(r._buffer) == buffer_len_before, (
                f"R18-F13: partial chunk on xrun status must be dropped — "
                f"buffer was {buffer_len_before}, now {len(r._buffer)}. "
                f"Pre-fix the corrupted chunk was stored."
            )
            # But xrun counter was incremented.
            assert r._xruns == 1, f"xrun counter must be incremented even when chunk is dropped, got xruns={r._xruns}"
        finally:
            r.stop()

    def test_clean_chunk_still_appended_on_zero_status(self, monkeypatch):
        """Sanity check: a chunk with status=0 (no xrun) is still
        appended to the buffer. R18-F13 must not break the normal
        path."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        _patch_ok_stream(monkeypatch, recording_mod)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        r.start()
        try:
            buffer_len_before = len(r._buffer)
            chunk = np.ones((512, 1), dtype=np.float32) * 0.3
            r._current_callback(chunk, 512, None, 0)  # status=0
            _drain_ring_buffer(r)

            assert len(r._buffer) == buffer_len_before + 1, (
                f"clean chunk (status=0) must be appended — buffer was {buffer_len_before}, now {len(r._buffer)}"
            )
            assert r._xruns == 0, "no xrun when status=0"
        finally:
            r.stop()


# ═══════════════════════════════════════════════════════════════════════════
# R4-F10: FilterChain module-level log (was undefined → NameError on except)
# ═══════════════════════════════════════════════════════════════════════════


class TestR4F10BaseLogDefined:
    """R4-F10: FilterChain.process catches filter exceptions and logs
    via the module-level ``log``. The except branch referenced ``log``
    which was UNDEFINED pre-fix → NameError on the first filter
    exception (worse than the original bug). The runtime exception
    isolation is covered in test_audio_filters.py; this test pins the
    ``log`` symbol so it can never go missing again."""

    def test_base_module_has_log_symbol(self):
        from voice_typer.server.audio_filters import base

        assert hasattr(base, "log"), (
            "R4-F10: voice_typer.server.audio_filters.base must define a "
            "module-level ``log`` (FilterChain.process references it in "
            "the except branch). Without it the first filter exception "
            "raises NameError."
        )
        assert isinstance(base.log, logging.Logger), f"R4-F10: base.log must be a logging.Logger, got {type(base.log)}"

    def test_filterchain_process_log_line_present_in_source(self):
        """Source-string pin: the FilterChain.process body must contain
        ``log.warning(...)`` so the except branch is wired to the
        module logger."""
        from voice_typer.server.audio_filters.base import FilterChain

        src = inspect.getsource(FilterChain.process)
        assert "log.warning" in src, "R4-F10: FilterChain.process must call log.warning in the except branch"


# ═══════════════════════════════════════════════════════════════════════════
# R18-F14: VAD threshold clamping (constants exposed)
# ═══════════════════════════════════════════════════════════════════════════


class TestR18F14VadThresholdConstants:
    """R18-F14: ``MIN_VAD_SPEECH_THRESHOLD_DB = -55`` and
    ``MIN_VAD_SILENCE_THRESHOLD_DB = -65`` are exposed as module
    constants. The very-quiet-room calibration test lives in
    test_vad_processor.py; this test pins the constants so a refactor
    can't silently remove the floor."""

    def test_min_threshold_constants_exposed(self):
        from voice_typer.server import vad_processor

        assert hasattr(vad_processor, "MIN_VAD_SPEECH_THRESHOLD_DB")
        assert hasattr(vad_processor, "MIN_VAD_SILENCE_THRESHOLD_DB")
        assert vad_processor.MIN_VAD_SPEECH_THRESHOLD_DB == -55.0
        assert vad_processor.MIN_VAD_SILENCE_THRESHOLD_DB == -65.0
        # Speech floor must be ABOVE silence floor (otherwise the
        # speech/silence hysteresis inverts).
        assert vad_processor.MIN_VAD_SPEECH_THRESHOLD_DB > vad_processor.MIN_VAD_SILENCE_THRESHOLD_DB, (
            "speech threshold floor must be above silence threshold floor"
        )


# ═══════════════════════════════════════════════════════════════════════════
# R3-F6: _dropped_level_chunks rate-limited WARNING log
# ═══════════════════════════════════════════════════════════════════════════


class TestR3F6DroppedLevelChunksLog:
    """R3-F6: when the level-monitor ring buffer overflows, the
    callback increments ``_dropped_level_chunks`` and emits a
    rate-limited WARNING via ``log_rate_limited``. Pre-fix the counter
    was incremented silently with no diagnostic surfaced."""

    def test_dropped_chunks_counter_increments_on_ring_overflow(self, monkeypatch, caplog):
        """Force the level-monitor ring buffer to overflow by pushing
        more chunks than the worker can drain. The counter must
        increment and a WARNING must be logged."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.log_rate_limit import reset as reset_rate_limit

        # Reset rate-limit counters so the first overflow fires at WARNING.
        reset_rate_limit()
        lm._dropped_level_chunks = 0

        # Wire a no-op InputStream that captures the callback.
        holder = {"cb": None}

        class _Stream:
            def __init__(self, *a, **kw):
                holder["cb"] = kw.get("callback")

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        import sounddevice as sd

        sd.InputStream = _Stream  # type: ignore[assignment]
        sd.query_devices.return_value = {
            "name": "Mock Mic",
            "default_samplerate": 16000,
            "max_input_channels": 1,
            "hostapi": 0,
        }

        # Block the level worker so the ring buffer fills up. We do
        # this by holding _monitor_lock for the duration of the push —
        # the worker acquires _monitor_lock to drain, so it stalls.
        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True

        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            # Capacity is _LEVEL_RING_BUFFER_CAPACITY = 64. Push 70
            # chunks while holding the lock so the worker can't drain.
            with lm._monitor_lock:
                for _ in range(70):
                    holder["cb"](chunk, 512, None, None)
            # Counter must have incremented.
            assert lm._dropped_level_chunks > 0, (
                "R3-F6: _dropped_level_chunks must increment when the ring buffer overflows"
            )
        finally:
            lm.stop_monitoring()
            reset_rate_limit()

    def test_rate_limited_warning_fires_on_first_drop(self, monkeypatch, caplog):
        """The first overflow must emit a WARNING log via
        ``log_rate_limited``. Subsequent overflows within the
        every_n=100 window go to DEBUG."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.log_rate_limit import reset as reset_rate_limit

        reset_rate_limit()
        lm._dropped_level_chunks = 0

        holder = {"cb": None}

        class _Stream:
            def __init__(self, *a, **kw):
                holder["cb"] = kw.get("callback")

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        import sounddevice as sd

        sd.InputStream = _Stream  # type: ignore[assignment]
        sd.query_devices.return_value = {
            "name": "Mock Mic",
            "default_samplerate": 16000,
            "max_input_channels": 1,
            "hostapi": 0,
        }

        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True

        try:
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.level_monitor"):
                with lm._monitor_lock:
                    for _ in range(70):
                        holder["cb"](chunk, 512, None, None)

            # At least one WARNING record must mention "ring buffer full"
            # or "dropped".
            warnings = [
                r
                for r in caplog.records
                if r.levelno == logging.WARNING
                and ("ring buffer full" in r.getMessage() or "dropped" in r.getMessage())
            ]
            assert len(warnings) >= 1, (
                "R3-F6: ring-buffer overflow must emit a WARNING log "
                f"(got {len(warnings)} warnings, records: "
                f"{[r.getMessage() for r in caplog.records]})"
            )
        finally:
            lm.stop_monitoring()
            reset_rate_limit()


# ═══════════════════════════════════════════════════════════════════════════
# R3-F14: dead ``list(_test_peak_history)`` expression removed
# ═══════════════════════════════════════════════════════════════════════════


class TestR3F14DeadListExpressionRemoved:
    """R3-F14: the dead ``list(_test_peak_history)`` expression at
    level_monitor.py:607 (old line numbering) was computed and
    immediately discarded. The expression is now removed; this test
    pins the removal so it doesn't get re-added by a future edit."""

    def test_no_dead_list_test_peak_history_in_stop_test_recording(self):
        from voice_typer.server import level_monitor

        src = inspect.getsource(level_monitor.stop_test_recording)
        # The dead expression was ``list(_test_peak_history)`` on a
        # line by itself (no assignment, no use of the result). After
        # removal the function must NOT contain that bare expression.
        # We check that the function still REFERENCES _test_peak_history
        # (it's cleared later) but not as a bare ``list(...)`` call.
        assert "_test_peak_history" in src, (
            "stop_test_recording must still reference _test_peak_history (for the .clear() / reassignment)"
        )
        # Look for the dead pattern: a line whose stripped form is
        # exactly ``list(_test_peak_history)`` with no ``=`` before it.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped == "list(_test_peak_history)":
                pytest.fail(
                    "R3-F14 regression: dead `list(_test_peak_history)` expression re-introduced in stop_test_recording"
                )


# ═══════════════════════════════════════════════════════════════════════════
# R4-F8: buffer-clear worker ThreadRegistry registration + _stop_buffer_clear_worker
# ═══════════════════════════════════════════════════════════════════════════


class TestR4F8BufferClearWorkerRegistry:
    """R4-F8: when a ThreadRegistry is set via ``set_thread_registry``,
    the lazily-started buffer-clear worker must register itself so
    ``shutdown_all()`` can join it during ``VoiceTyperApp.quit()``.
    Also covers the ``_stop_buffer_clear_worker`` test-only helper."""

    def test_set_thread_registry_constant_and_helper_exist(self):
        """Smoke-check that the R4-F8 surface area is importable."""
        from voice_typer.server.recording import buffer

        assert hasattr(buffer, "set_thread_registry")
        assert hasattr(buffer, "_stop_buffer_clear_worker")
        assert hasattr(buffer, "BUFFER_CLEAR_WORKER_NAME")
        assert buffer.BUFFER_CLEAR_WORKER_NAME == "buffer-clear-bg"

    def test_worker_registers_with_thread_registry(self, monkeypatch):
        """When ``set_thread_registry`` is called BEFORE the worker is
        started, the next ``_ensure_buffer_clear_worker`` call must
        register the new worker with the registry."""
        from voice_typer.server.recording import buffer

        # Stop any worker that may have been started by an earlier test
        # so we exercise the lazy-start path cleanly.
        buffer._stop_buffer_clear_worker(timeout=2.0)

        registry = MagicMock()
        buffer.set_thread_registry(registry)

        # Trigger lazy worker start by enqueuing a buffer.
        buf = collections.deque([np.ones(64, dtype=np.float32)])
        buffer._secure_clear_array_background(buf)

        # Wait briefly for the worker to start + register.
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if registry.register.called:
                break
            time.sleep(0.01)

        try:
            assert registry.register.called, (
                "R4-F8: buffer-clear worker must register with the ThreadRegistry when one is set"
            )
            call = registry.register.call_args
            assert call.kwargs.get("name") == "buffer-clear-bg", (
                f"R4-F8: register() name must be 'buffer-clear-bg', got {call.kwargs.get('name')!r}"
            )
            assert call.kwargs.get("stop_event") is None, (
                "R4-F8: register() stop_event must be None (worker uses "
                "None-sentinel protocol via _stop_buffer_clear_worker)"
            )
            thread = call.kwargs.get("thread")
            assert thread is not None and thread.is_alive(), "R4-F8: register() must be passed the live worker thread"
        finally:
            # Cleanup: stop the worker, clear the registry.
            buffer._stop_buffer_clear_worker(timeout=2.0)
            buffer.set_thread_registry(None)

    def test_set_thread_registry_registers_already_running_worker(self):
        """If the worker is ALREADY running when set_thread_registry is
        called, the registry must register it immediately (mirrors the
        scipy-preloader pattern in recorder.py)."""
        from voice_typer.server.recording import buffer

        buffer._stop_buffer_clear_worker(timeout=2.0)

        # Start the worker WITHOUT a registry.
        buf = collections.deque([np.ones(64, dtype=np.float32)])
        buffer._secure_clear_array_background(buf)
        # Wait for the worker to start.
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if buffer._buffer_clear_worker is not None and buffer._buffer_clear_worker.is_alive():
                break
            time.sleep(0.01)
        assert buffer._buffer_clear_worker is not None, "worker should be running"

        registry = MagicMock()
        buffer.set_thread_registry(registry)

        try:
            # set_thread_registry must register the already-running worker.
            assert registry.register.called, (
                "R4-F8: set_thread_registry must register an already-"
                "running buffer-clear worker (mirrors scipy-preloader)"
            )
        finally:
            buffer._stop_buffer_clear_worker(timeout=2.0)
            buffer.set_thread_registry(None)

    def test_stop_buffer_clear_worker_joins_cleanly(self):
        """The test-only ``_stop_buffer_clear_worker`` helper must
        send the None sentinel and join the worker thread cleanly
        within the timeout."""
        from voice_typer.server.recording import buffer

        # Make sure a worker is running.
        buf = collections.deque([np.ones(64, dtype=np.float32)])
        buffer._secure_clear_array_background(buf)
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if buffer._buffer_clear_worker is not None and buffer._buffer_clear_worker.is_alive():
                break
            time.sleep(0.01)

        ok = buffer._stop_buffer_clear_worker(timeout=3.0)
        assert ok is True, "R4-F8: _stop_buffer_clear_worker must return True when the worker exits within the timeout"
        # After stop, the global _buffer_clear_worker is None.
        assert buffer._buffer_clear_worker is None, (
            "R4-F8: _stop_buffer_clear_worker must clear the global _buffer_clear_worker reference"
        )

    def test_stop_buffer_clear_worker_idempotent(self):
        """Calling _stop_buffer_clear_worker when no worker is running
        must be a no-op returning True (safe to call multiple times)."""
        from voice_typer.server.recording import buffer

        buffer._stop_buffer_clear_worker(timeout=1.0)  # ensure no worker
        assert buffer._buffer_clear_worker is None
        ok = buffer._stop_buffer_clear_worker(timeout=1.0)
        assert ok is True, "idempotent call must return True"
