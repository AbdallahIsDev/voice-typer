"""Tests for the level_monitor worker / monitoring fixes.

Covers three behaviour changes in ``voice_typer/server/level_monitor``:

1. **Scaled blocksize**: ``start_monitoring`` now derives the PortAudio
   block size from the device native sample rate via
   ``max(512, int(native_rate * 0.032))`` so a chunk always represents
   ~32 ms of audio regardless of whether the device runs at 16 / 44.1 /
   48 kHz. Previously a fixed ``blocksize=512`` produced ~10.7 ms chunks
   (≈94 Hz callback rate) on a 48 kHz device, which flooded the 64-entry
   ring buffer with only ~0.68 s of audio.

2. **Locked update_level_processor**: ``update_level_processor`` now
   acquires ``_monitor_lock`` for both the snapshot read of
   ``_monitor_sample_rate`` and the assignment of ``_level_processor``,
   so the level worker thread sees a consistent pair (processor built
   against the rate of the stream that will feed it). The
   ``AudioProcessor`` construction itself runs OUTSIDE the lock so a
   slow ``__init__`` (RNNoise model load) doesn't block ``get_level()``.

3. **Stuck-worker slot preservation**: ``_stop_level_worker`` no longer
   clears ``_level_worker_thread`` when the worker fails to exit within
   the 1-second join timeout. Leaving the slot occupied prevents
   ``_ensure_level_worker_running`` from spawning a duplicate worker
   that would race the stuck thread for the SPSC ring buffer and
   double-publish ``mic_level`` events.

All ``sounddevice`` calls are mocked so the tests run on any platform
(no real audio hardware required).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Test fixtures
# ═══════════════════════════════════════════════════════════════════════════


def _reset_level_monitor_state():
    """Reset all module-level state in level_monitor between tests."""
    import voice_typer.server.level_monitor as lm

    lm._test_mode = False
    lm._test_chunks.clear()
    lm._test_raw_chunks.clear()
    lm._test_filtered_chunks.clear()
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
    lm._last_drop_log_time = 0.0
    lm._level_ring_buffer.clear()
    # Stop any worker thread from a previous test.
    lm._stop_level_worker()
    lm._stop_mic_level_worker()
    # Reset disconnect-detection state.
    lm._consecutive_zero_chunks = 0
    lm._device_lost_emitted = False
    # Reset quality metrics.
    lm._test_peak_history.clear()
    lm._test_rms_history.clear()
    lm._test_clip_count = 0
    lm._test_silence_blocks = 0


@pytest.fixture(autouse=True)
def _reset_level_monitor():
    _reset_level_monitor_state()
    yield
    _reset_level_monitor_state()


def _wire_stream_with_kwargs_capture(monkeypatch):
    """Wire a mock ``sd.InputStream`` that captures EVERY constructor kwarg.

    Returns a holder dict with ``callback``, ``finished_callback``,
    ``blocksize``, and ``samplerate`` keys the test can read directly.
    """
    import sounddevice as sd

    holder = {
        "callback": None,
        "finished_callback": None,
        "blocksize": None,
        "samplerate": None,
        "stream_calls": [],
    }

    class _Stream:
        def __init__(self, *args, **kwargs):
            holder["callback"] = kwargs.get("callback")
            holder["finished_callback"] = kwargs.get("finished_callback")
            holder["blocksize"] = kwargs.get("blocksize")
            holder["samplerate"] = kwargs.get("samplerate")

        def start(self):
            holder["stream_calls"].append("start")

        def stop(self):
            holder["stream_calls"].append("stop")

        def close(self):
            holder["stream_calls"].append("close")

    sd.InputStream = _Stream  # type: ignore[assignment]
    return holder


def _set_native_rate(monkeypatch, native_rate: int) -> None:
    """Configure the mocked ``sd.query_devices`` to report ``native_rate``."""
    import sounddevice as sd

    sd.query_devices.return_value = {
        "name": "Mock Mic",
        "default_samplerate": native_rate,
        "max_input_channels": 1,
        "hostapi": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Scaled blocksize: max(512, int(native_rate * 0.032))
# ═══════════════════════════════════════════════════════════════════════════


class TestScaledBlocksize:
    """``start_monitoring`` scales the PortAudio blocksize with the
    device native sample rate so every chunk represents ~32 ms of audio.

    Pre-fix: ``blocksize=512`` was hardcoded, producing ~94 Hz callback
    rate on a 48 kHz device (only ~0.68 s of audio in the 64-entry ring
    buffer). Post-fix: ``blocksize = max(512, int(native_rate * 0.032))``
    yields ~31 Hz chunk rate on every device.
    """

    def test_blocksize_at_16khz_is_512(self, monkeypatch):
        """At 16 kHz the scaled blocksize is exactly 512 (the floor)."""
        import voice_typer.server.level_monitor as lm

        _set_native_rate(monkeypatch, 16000)
        holder = _wire_stream_with_kwargs_capture(monkeypatch)

        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        # int(16000 * 0.032) = int(512.0) = 512; max(512, 512) = 512.
        assert holder["blocksize"] == 512, (
            "At 16 kHz the scaled blocksize should be 512 (matches the "
            "previous hardcoded value so existing 16 kHz behaviour is "
            f"unchanged); got {holder['blocksize']!r}"
        )
        lm.stop_monitoring()

    def test_blocksize_at_48khz_is_1536(self, monkeypatch):
        """At 48 kHz the scaled blocksize is 1536 (32 ms blocks)."""
        import voice_typer.server.level_monitor as lm

        _set_native_rate(monkeypatch, 48000)
        holder = _wire_stream_with_kwargs_capture(monkeypatch)

        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        # int(48000 * 0.032) = int(1536.0) = 1536; max(512, 1536) = 1536.
        # Pre-fix this was 512 → ~10.7 ms chunks (≈94 Hz callback rate).
        assert holder["blocksize"] == 1536, (
            "At 48 kHz the scaled blocksize should be 1536 (32 ms blocks); "
            "the pre-fix hardcoded 512 produced ~10.7 ms chunks (≈94 Hz "
            f"callback rate); got {holder['blocksize']!r}"
        )
        # Sanity: samplerate must match the device native rate.
        assert holder["samplerate"] == 48000
        lm.stop_monitoring()

    def test_blocksize_at_44_1khz_is_1411(self, monkeypatch):
        """At 44.1 kHz the scaled blocksize is 1411 (~32 ms blocks)."""
        import voice_typer.server.level_monitor as lm

        _set_native_rate(monkeypatch, 44100)
        holder = _wire_stream_with_kwargs_capture(monkeypatch)

        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        # int(44100 * 0.032) = int(1411.2) = 1411; max(512, 1411) = 1411.
        assert holder["blocksize"] == 1411, (
            f"At 44.1 kHz the scaled blocksize should be 1411 (~32 ms blocks); got {holder['blocksize']!r}"
        )
        lm.stop_monitoring()

    def test_blocksize_floor_512_on_low_rate_device(self, monkeypatch):
        """On a low-rate device (e.g. 8 kHz) the floor keeps blocksize >= 512.

        Without the ``max(512, ...)`` floor, an 8 kHz device would get
        ``int(8000 * 0.032) = 256`` — a pathologically small block that
        PortAudio may reject or that would drive an excessive callback
        rate. The floor preserves sane block sizes on low-rate devices.
        """
        import voice_typer.server.level_monitor as lm

        _set_native_rate(monkeypatch, 8000)
        holder = _wire_stream_with_kwargs_capture(monkeypatch)

        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        # int(8000 * 0.032) = 256; max(512, 256) = 512.
        assert holder["blocksize"] == 512, (
            "On an 8 kHz device the floor should clamp blocksize to 512 "
            f"(avoids pathologically small blocks); got {holder['blocksize']!r}"
        )
        lm.stop_monitoring()

    def test_chunk_rate_approx_31hz_at_48khz(self, monkeypatch):
        """At 48 kHz the chunk rate is ~31 Hz (48000 / 1536 ≈ 31.25 Hz).

        Pre-fix the rate was ~94 Hz (48000 / 512). This is the core
        user-visible benefit: a 64-entry ring buffer now holds ~2 s of
        audio instead of ~0.68 s.
        """
        import voice_typer.server.level_monitor as lm

        _set_native_rate(monkeypatch, 48000)
        holder = _wire_stream_with_kwargs_capture(monkeypatch)

        result = lm.start_monitoring(mic_id=None)
        assert result["success"] is True
        blocksize = holder["blocksize"]
        samplerate = holder["samplerate"]
        chunk_rate = samplerate / blocksize
        # 48000 / 1536 = 31.25 Hz. Allow a small tolerance for the
        # int() truncation in the blocksize formula.
        assert 28.0 <= chunk_rate <= 33.0, (
            "Chunk rate at 48 kHz should be ~31 Hz (32 ms blocks); "
            f"got {chunk_rate:.1f} Hz (blocksize={blocksize}, sr={samplerate})"
        )
        lm.stop_monitoring()


# ═══════════════════════════════════════════════════════════════════════════
# Locked update_level_processor: snapshot + assign under _monitor_lock
# ═══════════════════════════════════════════════════════════════════════════


class TestUpdateLevelProcessorLocking:
    """``update_level_processor`` must acquire ``_monitor_lock`` for both
    the snapshot read of ``_monitor_sample_rate`` and the assignment of
    ``_level_processor``.

    Pre-fix: the function read ``_monitor_sample_rate`` bare and assigned
    ``_level_processor`` bare, racing a concurrent ``start_monitoring``
    that could swap the sample rate mid-call and yield a processor tuned
    to the wrong rate.
    """

    def test_disabled_branch_clears_processor_under_lock(self, monkeypatch):
        """The ``noise_filter_enabled=False`` branch must clear
        ``_level_processor`` to None while holding ``_monitor_lock``.

        Strategy: hold ``_monitor_lock`` from another thread; the
        ``update_level_processor`` call must BLOCK on the lock before
        the assignment completes. If the lock is missing, the
        assignment races and the test observes the cleared value before
        the worker thread releases the lock.
        """
        import voice_typer.server.level_monitor as lm

        # Install a non-None processor so the clear branch has work to do.
        lm._level_processor = MagicMock()

        # Hold the lock from another thread.
        real_lock = lm._monitor_lock
        lock_held = threading.Event()
        release_lock = threading.Event()

        def hold_lock():
            with real_lock:
                lock_held.set()
                release_lock.wait(timeout=5.0)

        worker = threading.Thread(target=hold_lock, daemon=True)
        worker.start()
        assert lock_held.wait(timeout=2.0), "worker did not acquire lock"

        # Try to clear the processor. With the lock, this must block.
        update_done = threading.Event()

        def call_update():
            lm.update_level_processor({"noise_filter_enabled": False})
            update_done.set()

        update_thread = threading.Thread(target=call_update, daemon=True)
        update_thread.start()

        # Give the update thread time to reach the lock acquisition.
        time.sleep(0.1)

        # The update call must NOT have completed (the lock is held).
        assert not update_done.is_set(), (
            "update_level_processor returned before the lock was "
            "released — the disabled branch is not acquiring "
            "_monitor_lock for the _level_processor = None assignment."
        )

        # Release the lock — the update should now complete.
        release_lock.set()
        assert update_done.wait(timeout=2.0), "update_level_processor did not return after lock release — deadlock?"

        # The processor must have been cleared.
        assert lm._level_processor is None, "update_level_processor(disabled) should clear _level_processor to None"

        worker.join(timeout=2.0)
        update_thread.join(timeout=2.0)

    def test_enabled_branch_snapshots_sample_rate_under_lock(self, monkeypatch):
        """The enabled branch must snapshot ``_monitor_sample_rate`` under
        ``_monitor_lock``.

        Strategy: wrap ``_monitor_lock`` with a counting proxy that
        records each ``__enter__`` call. Then call
        ``update_level_processor`` and assert the lock was acquired at
        least once BEFORE the ``AudioProcessor`` constructor ran (so
        the sample_rate snapshot is lock-protected).
        """
        import voice_typer.server.level_monitor as lm

        # Stub ``AudioProcessor`` so we can capture the sample_rate it's
        # constructed with and synchronise with the lock-entry events.
        captured_sr: list[int] = []
        constructor_entry_count: list[int] = []

        class _FakeAudioProcessor:
            def __init__(self, config, sample_rate, **kwargs):
                # Record how many times the lock has been entered by
                # the time the constructor runs. The snapshot read in
                # update_level_processor acquires the lock once, and
                # the assignment acquires it again — so by the time
                # the constructor runs (between those two acquisitions),
                # the count must be >= 1.
                constructor_entry_count.append(len(enter_calls))
                captured_sr.append(sample_rate)

            def reset(self):
                pass

        # Inject the fake into the audio_processor module's namespace.
        import sys

        fake_module = MagicMock()
        fake_module.AudioProcessor = _FakeAudioProcessor
        original_module = sys.modules.get("voice_typer.server.audio_processor")
        sys.modules["voice_typer.server.audio_processor"] = fake_module

        # Wrap _monitor_lock with a counting proxy. The proxy delegates
        # to the real lock so behaviour is unchanged, but it records
        # every __enter__ call so we can assert the lock was acquired
        # before the AudioProcessor constructor ran.
        enter_calls: list[float] = []
        real_lock = lm._monitor_lock

        class _CountingLock:
            def __enter__(self):
                enter_calls.append(time.perf_counter())
                return real_lock.__enter__()

            def __exit__(self, *exc):
                return real_lock.__exit__(*exc)

        counting_lock = _CountingLock()
        monkeypatch.setattr(lm, "_monitor_lock", counting_lock)
        # Also patch the worker submodule's view of the lock, since
        # ``_state._monitor_lock`` is the singleton reference and the
        # ``_LevelMonitorModule`` routing means writes via ``lm._monitor_lock``
        # propagate to ``_state._monitor_lock`` automatically.
        from voice_typer.server.level_monitor._state import _state as _state_singleton

        original_state_lock = _state_singleton._monitor_lock
        _state_singleton._monitor_lock = counting_lock
        try:
            lm._monitor_sample_rate = 48000

            lm.update_level_processor(
                {
                    "noise_filter_enabled": True,
                    "noise_filter_highpass": True,
                    "noise_filter_gate": True,
                    "noise_suppression_method": "rnnoise",
                },
            )

            # The processor must have been constructed exactly once.
            assert len(captured_sr) == 1, (
                f"AudioProcessor should be constructed exactly once; got {len(captured_sr)} constructions"
            )

            # The snapshot read must reflect the value at the time of
            # the locked read.
            assert captured_sr[0] == 48000, (
                "update_level_processor must read _monitor_sample_rate "
                "under _monitor_lock so the AudioProcessor is built "
                f"against the rate of the stream that will feed it; "
                f"expected 48000, got {captured_sr[0]}"
            )

            # The lock must have been entered at least once BEFORE the
            # AudioProcessor constructor ran (proving the snapshot read
            # happened under the lock).
            assert len(constructor_entry_count) == 1
            assert constructor_entry_count[0] >= 1, (
                "The AudioProcessor constructor ran before any lock "
                "acquisition — update_level_processor is not snapshotting "
                "_monitor_sample_rate under _monitor_lock"
            )

            # The lock must have been entered at least TWICE total: once
            # for the snapshot read, once for the final assignment.
            assert len(enter_calls) >= 2, (
                "update_level_processor must acquire _monitor_lock twice: "
                "once for the sample_rate snapshot, once for the "
                "_level_processor assignment; "
                f"got {len(enter_calls)} acquisitions"
            )
        finally:
            _state_singleton._monitor_lock = original_state_lock
            if original_module is not None:
                sys.modules["voice_typer.server.audio_processor"] = original_module
            else:
                sys.modules.pop("voice_typer.server.audio_processor", None)

    def test_assignment_under_lock(self, monkeypatch):
        """The ``_level_processor = new_processor`` assignment happens
        under ``_monitor_lock``.

        Strategy: hold the lock from another thread; the
        ``update_level_processor`` call must BLOCK on the lock before
        the assignment completes (the construction may run outside the
        lock, but the final assignment must be locked).
        """
        import sys

        import voice_typer.server.level_monitor as lm

        class _FastAudioProcessor:
            def __init__(self, config, sample_rate, **kwargs):
                pass

            def reset(self):
                pass

        fake_module = MagicMock()
        fake_module.AudioProcessor = _FastAudioProcessor
        original_module = sys.modules.get("voice_typer.server.audio_processor")
        sys.modules["voice_typer.server.audio_processor"] = fake_module
        try:
            real_lock = lm._monitor_lock
            lock_held = threading.Event()
            release_lock = threading.Event()

            def hold_lock():
                with real_lock:
                    lock_held.set()
                    release_lock.wait(timeout=5.0)

            worker = threading.Thread(target=hold_lock, daemon=True)
            worker.start()
            assert lock_held.wait(timeout=2.0)

            update_done = threading.Event()

            def call_update():
                lm.update_level_processor({"noise_filter_enabled": True, "noise_filter_highpass": True})
                update_done.set()

            update_thread = threading.Thread(target=call_update, daemon=True)
            update_thread.start()

            # The update should be blocked on the lock (the final
            # assignment acquires the lock).
            time.sleep(0.2)
            assert not update_done.is_set(), (
                "update_level_processor returned while _monitor_lock was "
                "held by another thread — the final _level_processor "
                "assignment is not under the lock."
            )

            release_lock.set()
            assert update_done.wait(timeout=2.0), "update_level_processor did not complete after lock release"

            # The new processor must have been assigned.
            assert lm._level_processor is not None, "update_level_processor should assign a new _level_processor"

            worker.join(timeout=2.0)
            update_thread.join(timeout=2.0)
        finally:
            if original_module is not None:
                sys.modules["voice_typer.server.audio_processor"] = original_module
            else:
                sys.modules.pop("voice_typer.server.audio_processor", None)


# ═══════════════════════════════════════════════════════════════════════════
# Stuck-worker slot preservation: don't clear _level_worker_thread on timeout
# ═══════════════════════════════════════════════════════════════════════════


class TestStopLevelWorkerStuckSlot:
    """``_stop_level_worker`` must NOT clear ``_level_worker_thread`` when
    the worker fails to exit within the 1-second join timeout.

    Pre-fix: the slot was unconditionally cleared, so the next
    ``_ensure_level_worker_running`` call spawned a DUPLICATE worker
    that raced the stuck thread for ``_level_ring_buffer`` pops (SPSC
    contract violation) and double-published ``mic_level`` events.
    Post-fix: the slot is left occupied; ``_ensure_level_worker_running``
    reuses the (still-alive) stuck thread instead of spawning a new one.
    """

    def test_slot_cleared_when_worker_exits_cleanly(self, monkeypatch):
        """Sanity: when the worker exits within the join timeout, the
        slot IS cleared (the happy path is unchanged)."""
        import voice_typer.server.level_monitor as lm

        # Start a real worker (no chunks to process — it will idle).
        lm._level_worker_stop_event.clear()
        lm._level_worker_wake_event.clear()
        from voice_typer.server.level_monitor.worker import (
            _ensure_level_worker_running,
        )

        _ensure_level_worker_running()
        thread = lm._level_worker_thread
        assert thread is not None
        assert thread.is_alive()

        # Stop it — should join cleanly within the 1s timeout.
        lm._stop_level_worker()

        # Slot must be cleared on the happy path.
        assert lm._level_worker_thread is None, (
            "Happy-path stop should clear _level_worker_thread (the "
            "stuck-slot preservation must NOT affect normal shutdown)"
        )

    def test_slot_preserved_when_worker_does_not_exit(self, monkeypatch, caplog):
        """When the worker fails to exit within the 1s join timeout, the
        slot is LEFT OCCUPIED (the stuck worker thread reference is
        retained) and an ERROR is logged.

        Strategy: replace ``threading.Thread.join`` with a no-op so the
        join "times out" instantly (the thread is still alive). Then
        verify the slot is preserved and an ERROR was logged.
        """
        import logging

        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        # Start a real worker that will idle.
        lm._level_worker_stop_event.clear()
        lm._level_worker_wake_event.clear()
        worker._ensure_level_worker_running()
        thread = lm._level_worker_thread
        assert thread is not None and thread.is_alive()

        # Patch the SPECIFIC thread instance's ``join`` method to be a
        # no-op (simulates the worker not exiting within the timeout
        # without actually waiting 1s). The thread will still be
        # ``is_alive()`` after our no-op join, which is what the
        # stuck-slot branch checks.
        original_join = thread.join
        thread.join = lambda timeout=None: None  # type: ignore[method-assign]

        try:
            with caplog.at_level(logging.ERROR, logger="voice_typer.server.level_monitor"):
                lm._stop_level_worker()

            # The slot must NOT have been cleared — the stuck worker
            # is still alive and the slot is preserved to prevent a
            # duplicate-worker spawn.
            assert lm._level_worker_thread is thread, (
                "Stuck-worker slot preservation: _level_worker_thread "
                "should still reference the (alive) stuck thread after "
                "_stop_level_worker returns; got "
                f"{lm._level_worker_thread!r}"
            )

            # The stop event + ring buffer must NOT have been cleared
            # either (those operations are skipped on the stuck path).
            assert lm._level_worker_stop_event.is_set(), (
                "The stop event should remain set on the stuck path so the "
                "worker still sees the stop signal if it eventually unblocks"
            )

            # An ERROR log must have been emitted so operators can see
            # the stuck worker.
            stuck_errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "did not exit" in r.message]
            assert len(stuck_errors) >= 1, (
                "Stuck-worker path must log an ERROR so the stuck thread "
                "isn't silently leaked; got records: "
                f"{[(r.levelname, r.message) for r in caplog.records]}"
            )
        finally:
            # Restore the real join and actually stop the worker so the
            # test fixture's reset doesn't hang.
            thread.join = original_join  # type: ignore[method-assign]
            lm._level_worker_stop_event.set()
            lm._level_worker_wake_event.set()
            original_join(timeout=2.0)
            lm._level_worker_thread = None
            lm._level_ring_buffer.clear()
            lm._level_worker_stop_event.clear()

    def test_ensure_level_worker_running_reuses_stuck_slot(self, monkeypatch):
        """``_ensure_level_worker_running`` reuses the (still-alive)
        stuck worker instead of spawning a duplicate when the slot is
        preserved by the stuck-path branch of ``_stop_level_worker``.

        Strategy: simulate the stuck-path outcome (slot occupied by an
        alive thread) and verify ``_ensure_level_worker_running`` does
        NOT spawn a new thread.
        """
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        # Install a stub thread that reports as alive but does nothing.
        stub_thread = MagicMock()
        stub_thread.is_alive.return_value = True
        lm._level_worker_thread = stub_thread

        # ``_ensure_level_worker_running`` should detect the alive
        # thread and return early (no new thread spawned).
        before = lm._level_worker_thread
        worker._ensure_level_worker_running()
        after = lm._level_worker_thread

        assert before is after, (
            "_ensure_level_worker_running must reuse the (still-alive) "
            "stuck worker instead of spawning a duplicate; the slot "
            "should be unchanged"
        )
        assert after is stub_thread, "The slot should still reference the original stuck thread"

        # Clean up the stub so the fixture's _stop_level_worker doesn't
        # try to join a MagicMock (which has no real join semantics).
        lm._level_worker_thread = None
