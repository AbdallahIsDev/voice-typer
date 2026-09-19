"""Tests for the smart-duck background monitor (v2.3)."""

from __future__ import annotations

import threading
import time

import pytest
from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_ducker import VolumeDucker

from tests.fixtures.wait_helpers import wait_until


class ControllableBackend(VolumeBackend):
    """FakeBackend whose is_speaker_active() return value can be flipped"""

    def __init__(self, current: float = 0.5, muted: bool = False, speaker_active: bool = False) -> None:
        self._current = current
        self._muted = muted
        self._speaker_active = speaker_active
        self.set_calls: list[tuple[float, bool | None]] = []
        self.fade_calls: list[tuple[float, int]] = []
        self.is_speaker_active_calls: int = 0
        # Track whether is_speaker_active has ever returned True
        self.speaker_ever_active: bool = speaker_active

    @property
    def name(self) -> str:
        return "controllable"

    @property
    def supports_per_session(self) -> bool:
        return False

    def initialize(self) -> bool:
        return True

    def get_state(self) -> VolumeState | None:
        return VolumeState(linear=self._current, muted=self._muted)

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        self._current = max(0.0, min(1.0, level))
        if muted is not None:
            self._muted = muted
        self.set_calls.append((level, muted))
        return True

    def fade_to(self, target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
        self._current = max(0.0, min(1.0, target_linear))
        self.fade_calls.append((target_linear, duration_ms))
        return True

    def is_speaker_active(self) -> bool:
        self.is_speaker_active_calls += 1
        if self._speaker_active:
            self.speaker_ever_active = True
        return self._speaker_active

    # Test helper, flip the speaker activity at runtime.
    def set_speaker_active(self, active: bool) -> None:
        self._speaker_active = active


class TestMonitorLifecycle:
    """Monitor starts/stops at the right times."""

    def test_monitor_starts_on_smart_duck_skip(self):
        """When smart-duck skips (no audio), the monitor should start."""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        # Use a short poll interval so the monitor is responsive in tests.
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        assert not ducker.is_monitor_running
        ducker.duck(0.25)
        assert ducker.is_monitor_running, "Monitor should start after smart-duck skip"

        # Clean up.
        ducker.restore()
        assert not ducker.is_monitor_running

    def test_monitor_does_not_start_when_smart_duck_disabled(self):
        """If smart-duck is disabled, duck() proceeds normally, no monitor."""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.set_smart_duck_enabled(False)
        ducker.initialize()

        ducker.duck(0.25)
        assert not ducker.is_monitor_running, "Monitor should NOT start when smart-duck is disabled"
        assert ducker.actually_ducked is True  # normal duck happened
        ducker.restore()

    def test_monitor_does_not_start_when_audio_already_playing(self):
        """If audio was already playing at duck time, duck() proceeds"""
        backend = ControllableBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        ducker.duck(0.25)
        assert not ducker.is_monitor_running, "Monitor should NOT start when audio was already playing"
        assert ducker.actually_ducked is True
        ducker.restore()

    def test_monitor_stops_on_restore(self):
        """restore() should stop the monitor."""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)
        assert ducker.is_monitor_running

        ducker.restore()
        # Give the monitor thread a moment to exit.
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=1.0), "Monitor should stop after restore()"


class TestRetroactiveDuck:
    """The monitor retroactively ducks when audio starts mid-dictation."""

    def test_audio_starts_mid_dictation_triggers_retroactive_duck(self):
        """The user's scenario: start dictation (no audio), then play"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)  # fast poll for tests
        ducker.initialize()

        # Start dictation, smart-duck skips (no audio).
        ducker.duck(0.25)
        assert not ducker.actually_ducked
        assert ducker.is_monitor_running
        assert backend.fade_calls == []  # no fade yet

        # Simulate the user starting music mid-dictation.
        backend.set_speaker_active(True)

        # Wait for the monitor to detect audio and retroactively duck.
        assert wait_until(lambda: ducker.actually_ducked, timeout=2.0), (
            "Monitor should have retroactively ducked after audio started"
        )

        # Verify the duck actually happened.
        assert (0.25, 150) in backend.fade_calls, (
            f"Retroactive duck should have faded to 0.25; got {backend.fade_calls}"
        )
        assert ducker.actually_ducked is True

        # Monitor should have exited (its job is done).
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=1.0)

        ducker.restore()

    def test_retroactive_duck_writes_crash_recovery(self, tmp_path):
        """The retroactive duck must write the crash-recovery file —"""
        from voice_typer.server.duck_crash_recovery import DuckCrashRecovery

        cr = DuckCrashRecovery(config_dir=tmp_path)
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend, crash_recovery=cr)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        ducker.duck(0.25)
        assert not cr.path.exists()  # smart-duck skip → no file

        backend.set_speaker_active(True)
        assert wait_until(lambda: ducker.actually_ducked, timeout=2.0)
        assert cr.path.exists(), "Retroactive duck should have written crash-recovery file"

        ducker.restore()
        assert not cr.path.exists()  # restore clears it

    def test_audio_never_starts_no_retroactive_duck(self):
        """If audio never starts during dictation, the monitor should"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        ducker.duck(0.25)
        # Negative wait: poll for several poll intervals, audio never
        assert not wait_until(lambda: ducker.actually_ducked, timeout=1.0), (
            "Monitor should NOT have ducked if audio never started"
        )

        assert not ducker.actually_ducked, "Monitor should NOT have ducked if audio never started"
        assert backend.fade_calls == []
        assert backend._current == 0.5  # volume unchanged

        ducker.restore()
        assert backend.fade_calls == []  # restore is also a no-op

    def test_monitor_exits_after_retroactive_duck(self):
        """After the monitor retroactively ducks, it should exit —"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)

        backend.set_speaker_active(True)
        assert wait_until(lambda: ducker.actually_ducked, timeout=2.0)
        # Monitor should exit after the successful retroactive duck.
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=1.0), (
            "Monitor should exit after retroactive duck succeeds"
        )

        ducker.restore()


class TestMonitorDisableMidDictation:
    """If the user disables smart-duck while the monitor is running."""

    def test_disable_stops_monitor(self):
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)
        assert ducker.is_monitor_running

        ducker.set_smart_duck_enabled(False)
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=1.0), (
            "Monitor should stop when smart-duck is disabled"
        )

        # Volume should still be unchanged (we didn't retroactively duck).
        assert not ducker.actually_ducked
        assert backend.fade_calls == []

        ducker.restore()

    def test_disable_then_audio_starts_no_retroactive_duck(self):
        """After disabling, even if audio starts, no retroactive duck."""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)
        ducker.set_smart_duck_enabled(False)
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=1.0)

        # Audio starts, but monitor is gone, so no retroactive duck.
        backend.set_speaker_active(True)
        # Negative wait: monitor is gone, so no duck.
        assert not wait_until(lambda: ducker.actually_ducked, timeout=1.0), (
            "retroactive duck must not happen after smart-duck was disabled"
        )
        assert not ducker.actually_ducked
        assert backend.fade_calls == []

        ducker.restore()


class TestMonitorSecondDuck:
    """If duck() is called again while the monitor is running (e.g. config"""

    def test_second_duck_updates_level_for_monitor(self):
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)
        assert ducker.is_monitor_running

        # Change the duck level mid-dictation.
        ducker.duck(0.15)
        assert ducker.is_monitor_running

        # Audio starts, retroactive duck should use the NEW level (0.15).
        backend.set_speaker_active(True)
        assert wait_until(lambda: ducker.actually_ducked, timeout=2.0)
        assert (0.15, 150) in backend.fade_calls, (
            f"Retroactive duck should use the updated level 0.15; got {backend.fade_calls}"
        )

        ducker.restore()


class TestMonitorErrorHandling:
    def test_is_speaker_active_exception_retries(self):
        """If is_speaker_active() raises, the monitor should log + retry"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)

        # Make is_speaker_active raise on the next call.
        call_count = [0]
        original = backend.is_speaker_active

        def flaky():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("simulated backend error")
            # After 2 errors, behave normally.
            backend._speaker_active = True
            return original()

        backend.is_speaker_active = flaky

        # Monitor should survive the errors and eventually duck.
        assert wait_until(lambda: ducker.actually_ducked, timeout=3.0), (
            "Monitor should retry after is_speaker_active exceptions and eventually duck when audio starts"
        )

        ducker.restore()


class TestPollInterval:
    def test_default_poll_interval(self):
        ducker = VolumeDucker(backend=ControllableBackend())
        assert ducker._smart_duck_poll_ms == 500

    def test_set_poll_interval(self):
        ducker = VolumeDucker(backend=ControllableBackend())
        ducker.set_smart_duck_poll_interval(250)
        assert ducker._smart_duck_poll_ms == 250

    def test_poll_interval_clamped_low(self):
        """Below 50ms is too aggressive, clamp."""
        ducker = VolumeDucker(backend=ControllableBackend())
        ducker.set_smart_duck_poll_interval(10)
        assert ducker._smart_duck_poll_ms == 50

    def test_poll_interval_clamped_high(self):
        """Above 5000ms is too slow, clamp."""
        ducker = VolumeDucker(backend=ControllableBackend())
        ducker.set_smart_duck_poll_interval(10000)
        assert ducker._smart_duck_poll_ms == 5000

    def test_faster_poll_catches_audio_sooner(self):
        """Sanity: a 50ms poll catches audio faster than a 500ms poll."""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)

        # Start audio immediately.
        backend.set_speaker_active(True)
        start = time.monotonic()
        assert wait_until(lambda: ducker.actually_ducked, timeout=2.0)
        elapsed = time.monotonic() - start

        # With 50ms poll, should duck within ~150ms (one poll + fade).
        assert elapsed < 0.5, f"50ms poll should catch audio within 500ms; took {elapsed:.3f}s"
        ducker.restore()


class TestMonitorConcurrency:
    def test_monitor_and_restore_race(self):
        """If restore() fires while the monitor is polling, no errors"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)

        # Restore immediately, race with the monitor's first poll.
        ducker.restore()
        # Wait for the monitor to stop (poll instead of fixed sleep).
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=2.0)

        assert not ducker.is_monitor_running
        assert not ducker.actually_ducked
        assert backend.fade_calls == []  # no duck happened

    @pytest.mark.slow
    def test_concurrent_duck_and_restore_with_monitor(self):
        """Stress test: multiple threads calling duck/restore while the"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        errors: list[Exception] = []

        def cycle():
            try:
                for _ in range(5):
                    ducker.duck(0.25)
                    time.sleep(0.05)  # intentional fixed delay (stress-test pacing)
                    ducker.restore()
                    time.sleep(0.02)  # intentional fixed delay (stress-test pacing)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=cycle) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors
        # Monitor should not be running after all cycles complete.
        assert wait_until(lambda: not ducker.is_monitor_running, timeout=1.0)

    @pytest.mark.slow
    def test_thread_join_before_start_race_regression(self):
        """Regression test for the v2.3 bug where _start_smart_duck_monitor"""
        backend = ControllableBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        errors: list[Exception] = []
        stop = threading.Event()

        def duck_loop():
            """Hammer duck() to create+start monitors rapidly."""
            while not stop.is_set():
                try:
                    ducker.duck(0.25)
                except Exception as e:
                    errors.append(e)
                    return
                # Brief sleep so restore can race with us.
                time.sleep(0.001)  # intentional fixed delay (race-stress pacing)

        def restore_loop():
            """Hammer restore() to try to catch an unstarted thread."""
            while not stop.is_set():
                try:
                    ducker.restore()
                except Exception as e:
                    errors.append(e)
                    return
                time.sleep(0.001)  # intentional fixed delay (race-stress pacing)

        # 4 duckers + 4 restorers = 8 threads racing briefly.
        duckers = [threading.Thread(target=duck_loop, daemon=True) for _ in range(4)]
        restorers = [threading.Thread(target=restore_loop, daemon=True) for _ in range(4)]
        for t in duckers + restorers:
            t.start()

        time.sleep(0.5)  # intentional fixed delay (stress-test duration)
        stop.set()
        for t in duckers + restorers:
            t.join(timeout=5.0)

        # The key assertion: NO RuntimeError("cannot join thread before
        join_errors = [e for e in errors if "cannot join thread" in str(e)]
        assert not join_errors, (
            f"Reproduced the thread-join-before-start race: "
            f"{len(join_errors)} RuntimeErrors out of {len(errors)} total errors. "
            f"First: {join_errors[0]}"
        )
        # Other errors (e.g. from overlapping duck/restore state) are
