"""Level-monitor worker lifecycle tests.

Covers three behaviour changes in ``voice_typer/server/level_monitor``:

1. **Drain stop-check interval**: ``_level_worker_loop`` checks the
   stop event every 4 chunks during its ring-buffer drain (mirroring
   ``recording/capture.py``), so a stop during a long RNNoise backlog
   is noticed within ~200 ms instead of burning the full 64-chunk
   drain (~3.2 s) and tripping the 1 s join-timeout ERROR.

2. **ThreadRegistry registration**: both worker threads register on
   spawn and unregister on stop / idle-exit when a registry is
   installed via ``set_thread_registry``.

3. **mic_level worker backstop + idle-exit**: the publish worker uses
   the shared 250 ms backstop (was 1.0 s) and exits when the monitor
   stream is inactive and its queue is empty, so an orphaned stream
   cannot leave a 1 Hz wakeup thread behind.

All ``sounddevice`` calls are mocked so the tests run on any platform.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest


def _reset_level_monitor_state():
    """Reset level_monitor session state between tests."""
    import voice_typer.server.level_monitor as lm

    lm._stop_level_worker()
    lm._stop_mic_level_worker()
    lm._reset_state_for_tests()
    # ``reset_for_tests`` also wipes ``_thread_registry``; tests that
    # need one call ``set_thread_registry`` AFTER this helper.


@pytest.fixture(autouse=True)
def _reset_level_monitor():
    _reset_level_monitor_state()
    yield
    _reset_level_monitor_state()


class _FakeRegistry:
    """Minimal ThreadRegistry stand-in recording register/unregister calls."""

    def __init__(self) -> None:
        self.registered: dict[str, threading.Thread] = {}
        self.register_calls: list[str] = []
        self.unregister_calls: list[str] = []

    def register(self, name, thread, stop_event, join_timeout, **kwargs):
        self.registered[name] = thread
        self.register_calls.append(name)

    def unregister(self, name):
        self.registered.pop(name, None)
        self.unregister_calls.append(name)


# ─── Drain stop-check (mirrors recording/capture.py) ───────────────────


class TestDrainStopCheckInterval:
    """The level worker bails out of a long drain when stop is set."""

    def test_drain_stop_check_constant_is_four(self):
        from voice_typer.server.level_monitor import worker

        assert worker._DRAIN_STOP_CHECK_INTERVAL == 4

    def test_stop_during_backlog_drain_exits_promptly(self, monkeypatch):
        """A stop signal during a full-ring drain is noticed within a
        few chunks instead of processing the entire backlog."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        # Make each chunk slow enough that a full 64-chunk drain would
        # take >>1 s if the stop check were absent.
        def slow_process(indata, status):
            time.sleep(0.05)

        monkeypatch.setattr(worker, "_process_level_chunk", slow_process)

        # Fill the ring buffer to capacity with dummy chunks.
        chunk = np.zeros((512, 1), dtype=np.float32)
        for _ in range(lm._LEVEL_RING_BUFFER_CAPACITY):
            lm._level_ring_buffer.append((chunk, 0))

        # Start the worker (monitor must look active so chunks are processed).
        lm._monitor_active = True
        worker._ensure_level_worker_running()
        thread = lm._level_worker_thread
        assert thread is not None

        # Let the worker start draining, then signal stop.
        time.sleep(0.02)
        started = time.monotonic()
        worker._stop_level_worker()
        elapsed = time.monotonic() - started

        # Join timeout is 1.0 s; with the stop-check the worker should
        # exit well before that. Allow generous CI headroom.
        assert elapsed < 0.9, (
            f"stop took {elapsed:.3f}s; drain stop-check should bound latency to ~200ms even under a full-ring backlog"
        )
        # Slot must be cleared (clean exit, not the stuck-worker path).
        assert lm._level_worker_thread is None

    def test_stuck_worker_error_not_logged_on_normal_backlog_stop(self, monkeypatch, caplog):
        """A healthy drain-then-stop must not emit the 'did not exit
        within 1s join timeout' ERROR."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        def slow_process(indata, status):
            time.sleep(0.02)

        monkeypatch.setattr(worker, "_process_level_chunk", slow_process)
        chunk = np.zeros((512, 1), dtype=np.float32)
        for _ in range(32):
            lm._level_ring_buffer.append((chunk, 0))

        lm._monitor_active = True
        with caplog.at_level("ERROR", logger="voice_typer.server.level_monitor"):
            worker._ensure_level_worker_running()
            time.sleep(0.01)
            worker._stop_level_worker()

        assert not any("did not exit within" in r.message for r in caplog.records), (
            "stop during a healthy drain must not log the stuck-worker ERROR"
        )


# ─── ThreadRegistry registration ───────────────────────────────────────


class TestThreadRegistryRegistration:
    """Both workers register on spawn and unregister on stop."""

    def test_level_worker_registers_and_unregisters(self):
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        registry = _FakeRegistry()
        worker.set_thread_registry(registry)

        worker._ensure_level_worker_running()
        assert worker.LEVEL_WORKER_NAME in registry.register_calls
        assert lm._level_worker_thread is registry.registered.get(
            worker.LEVEL_WORKER_NAME,
        )

        worker._stop_level_worker()
        assert worker.LEVEL_WORKER_NAME in registry.unregister_calls
        assert worker.LEVEL_WORKER_NAME not in registry.registered

    def test_mic_level_worker_registers_and_unregisters(self):
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        registry = _FakeRegistry()
        worker.set_thread_registry(registry)

        lm._ensure_mic_level_worker_running()
        assert worker.MIC_LEVEL_WORKER_NAME in registry.register_calls

        lm._stop_mic_level_worker()
        assert worker.MIC_LEVEL_WORKER_NAME in registry.unregister_calls
        assert worker.MIC_LEVEL_WORKER_NAME not in registry.registered

    def test_no_registry_is_safe(self):
        """Without a registry installed, spawn/stop still work."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        worker.set_thread_registry(None)
        worker._ensure_level_worker_running()
        assert lm._level_worker_thread is not None
        assert lm._level_worker_thread.is_alive()
        worker._stop_level_worker()
        assert lm._level_worker_thread is None

    def test_set_thread_registry_exported_from_package(self):
        import voice_typer.server.level_monitor as lm

        assert callable(lm.set_thread_registry)
        assert lm.LEVEL_WORKER_NAME == "level-monitor-worker"
        assert lm.MIC_LEVEL_WORKER_NAME == "level-monitor-mic-level-worker"

    def test_idle_exit_unregisters_level_worker(self, monkeypatch):
        """The level worker's idle-timeout exit path drops its registry
        entry so ``shutdown_all()`` doesn't join a finished thread."""
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        registry = _FakeRegistry()
        worker.set_thread_registry(registry)

        # Force idle-timeout to fire immediately on the next worker tick.
        monkeypatch.setattr(lm, "_monitor_active", True)
        # Seed BOTH activity clocks POSITIVE and older than the zeroed idle
        # window. They must stay positive: ``_idle_timeout_auto_stop`` treats
        # a non-positive timestamp as "no poll has ever been recorded yet"
        # (``if last_activity_ts <= 0.0: return False``) so the timeout would
        # never fire. Absolute arithmetic like ``time.monotonic() - 9999.0``
        # is NEGATIVE whenever the host's monotonic clock is younger than
        # 9999 s (a freshly booted machine or CI runner), which is exactly
        # how this test used to fail. A small positive epsilon is older than
        # a 0 s window on any host that has been up for a millisecond.
        monkeypatch.setattr(lm, "_last_get_level_poll_ts", 0.001)
        monkeypatch.setattr(lm, "_mic_level_last_push_ts", 0.001)
        monkeypatch.setattr(lm, "_LEVEL_IDLE_TIMEOUT_SEC", 0.0)

        worker._ensure_level_worker_running()
        thread = lm._level_worker_thread
        assert thread is not None
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert worker.LEVEL_WORKER_NAME in registry.unregister_calls


# ─── mic_level worker backstop + idle-exit ─────────────────────────────


class TestMicLevelWorkerIdleExit:
    """mic_level worker uses the shared 250 ms backstop and exits when
    the monitor is inactive with an empty queue."""

    def test_backstop_timeout_matches_level_worker(self):
        import voice_typer.server.level_monitor as lm

        assert pytest.approx(0.25) == lm._LEVEL_WORKER_BACKSTOP_TIMEOUT_SEC

    def test_idle_exit_when_monitor_inactive_and_queue_empty(self):
        import voice_typer.server.level_monitor as lm

        lm._monitor_active = False
        lm._ensure_mic_level_worker_running()
        thread = lm._mic_level_worker_thread
        assert thread is not None

        # Two consecutive empty+inactive backstop ticks (≈500 ms)
        # trigger the idle-exit without any explicit wake.
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "mic_level worker should idle-exit after two empty+inactive ticks"
        assert lm._mic_level_worker_thread is None

    def test_worker_keeps_running_while_monitor_active(self):
        import voice_typer.server.level_monitor as lm

        lm._monitor_active = True
        lm._ensure_mic_level_worker_running()
        thread = lm._mic_level_worker_thread
        assert thread is not None

        # Give it a couple of backstop ticks; it must stay alive.
        time.sleep(0.6)
        assert thread.is_alive(), "mic_level worker must stay alive while the monitor is active"
        assert lm._mic_level_worker_thread is thread

        lm._monitor_active = False
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    def test_ensure_respawns_after_idle_exit(self):
        import voice_typer.server.level_monitor as lm

        lm._monitor_active = False
        lm._ensure_mic_level_worker_running()
        first = lm._mic_level_worker_thread
        first.join(timeout=2.0)
        assert not first.is_alive()

        lm._monitor_active = True
        lm._ensure_mic_level_worker_running()
        second = lm._mic_level_worker_thread
        assert second is not None
        assert second is not first
        assert second.is_alive()
        # Cleanup: let it idle-exit.
        lm._monitor_active = False
        second.join(timeout=2.0)


# ─── Regression: existing ensure/stop contract still holds ─────────────


class TestWorkerLifecycleRegression:
    """Existing SPSC / reuse contracts must not regress."""

    def test_ensure_reuses_live_worker(self):
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        worker._ensure_level_worker_running()
        first = lm._level_worker_thread
        worker._ensure_level_worker_running()
        assert lm._level_worker_thread is first

    def test_stop_when_not_running_is_noop(self):
        from voice_typer.server.level_monitor import worker

        worker._stop_level_worker()  # must not raise

    def test_stop_twice_is_safe(self):
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor import worker

        worker._ensure_level_worker_running()
        worker._stop_level_worker()
        worker._stop_level_worker()
        assert lm._level_worker_thread is None
