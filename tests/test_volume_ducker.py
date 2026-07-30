"""Tests for VolumeDucker, VolumeBackend ABC, and DuckCrashRecovery.

Uses a FakeBackend that implements the VolumeBackend ABC without any
real audio hardware, so tests run on any platform (CI included).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_ducker import VolumeDucker

# ═══════════════════════════════════════════════════════════════════════════
# Fake backend for testing
# ═══════════════════════════════════════════════════════════════════════════


class FakeBackend(VolumeBackend):
    """In-memory VolumeBackend for tests — no real audio hardware.

    ``speaker_active`` controls :meth:`is_speaker_active` — set to
    ``False`` to simulate "no audio playing" (smart-duck skips).
    """

    def __init__(
        self,
        current: float = 0.5,
        muted: bool = False,
        speaker_active: bool = True,
    ) -> None:
        self._current = current
        self._muted = muted
        self._speaker_active = speaker_active
        self._set_calls: list[tuple[float, bool | None]] = []
        self._fade_calls: list[tuple[float, int]] = []
        self._ducked_sessions = False
        self._restored_sessions = False
        # Track is_speaker_active() call count so tests can verify
        # smart-duck actually queried the backend.
        self.is_speaker_active_calls: int = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def supports_per_session(self) -> bool:
        return True

    def initialize(self) -> bool:
        return True

    def get_state(self) -> VolumeState | None:
        return VolumeState(linear=self._current, muted=self._muted)

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        self._current = max(0.0, min(1.0, level))
        if muted is not None:
            self._muted = muted
        self._set_calls.append((level, muted))
        return True

    def fade_to(self, target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
        self._current = max(0.0, min(1.0, target_linear))
        self._fade_calls.append((target_linear, duration_ms))
        return True

    def is_speaker_active(self) -> bool:
        self.is_speaker_active_calls += 1
        return self._speaker_active

    def duck_other_sessions(self, level: float) -> bool:
        self._ducked_sessions = True
        return True

    def restore_other_sessions(self) -> bool:
        self._restored_sessions = True
        return True


class FakeFailingBackend(VolumeBackend):
    """Backend that always fails — simulates missing device/library."""

    @property
    def name(self) -> str:
        return "failing"

    @property
    def supports_per_session(self) -> bool:
        return False

    def initialize(self) -> bool:
        return False

    def get_state(self) -> VolumeState | None:
        return None

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        return False

    def fade_to(self, target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(current=0.5, muted=False)


@pytest.fixture
def ducker(backend: FakeBackend) -> VolumeDucker:
    return VolumeDucker(backend=backend)


@pytest.fixture
def recovery_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def crash_recovery(recovery_dir: Path) -> DuckCrashRecovery:
    return DuckCrashRecovery(config_dir=recovery_dir)


# ═══════════════════════════════════════════════════════════════════════════
# VolumeState tests
# ═══════════════════════════════════════════════════════════════════════════


class TestVolumeState:
    def test_is_frozen(self) -> None:
        state = VolumeState(linear=0.5, muted=False)
        with pytest.raises(AttributeError):
            state.linear = 0.9  # type: ignore[misc]

    def test_equality(self) -> None:
        a = VolumeState(linear=0.5, muted=False)
        b = VolumeState(linear=0.5, muted=False)
        assert a == b


# ═══════════════════════════════════════════════════════════════════════════
# VolumeDucker basic lifecycle tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDuckRestore:
    def test_duck_saves_and_restores(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(0.25)
        assert ducker.is_ducked
        assert backend._fade_calls[-1] == (0.25, 150)

        ducker.restore()
        assert not ducker.is_ducked
        assert backend._fade_calls[-1] == (0.5, 150)

    def test_restore_without_duck_is_noop(self, ducker: VolumeDucker) -> None:
        ducker.initialize()
        assert ducker.restore() is True  # no-op success
        assert not ducker.is_ducked

    def test_double_duck_does_not_resave(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(0.25)
        saved = ducker._saved_state
        assert saved is not None

        backend._current = 0.25  # simulate ducked state
        ducker.duck(0.15)  # update level
        assert ducker._saved_state is saved  # same object — not re-saved
        assert backend._fade_calls[-1][0] == 0.15

    def test_double_restore_is_noop(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(0.25)
        ducker.restore()
        backend._fade_calls.clear()
        ducker.restore()  # second restore
        assert backend._fade_calls == []  # no fade happened

    def test_duck_clamps_level(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(1.5)  # above max
        assert backend._fade_calls[-1][0] == 1.0
        ducker.restore()

        ducker.duck(-0.5)  # below min
        assert backend._fade_calls[-1][0] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Mute state tests
# ═══════════════════════════════════════════════════════════════════════════


class TestMuteState:
    def test_mute_state_preserved_on_restore(self) -> None:
        backend = FakeBackend(current=0.5, muted=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ducker.duck(0.25)

        # Duck leaves mute unchanged (set_linear with muted=None)
        backend._muted = False  # duck didn't touch mute
        ducker.restore()
        # Restore should re-mute
        assert any(call[1] is True for call in backend._set_calls), "restore should have set muted=True"


# ═══════════════════════════════════════════════════════════════════════════
# Manual override tests
# ═══════════════════════════════════════════════════════════════════════════


class TestManualOverride:
    def test_manual_volume_override_detected(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(0.25)

        backend._current = 0.8  # user cranked volume while ducked
        ducker.restore()
        # Should restore to current (0.8), not saved (0.5)
        assert backend._fade_calls[-1][0] == 0.8

    def test_force_restore_ignores_override(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(0.25)

        backend._current = 0.8  # user changed it
        ducker.restore(force=True)
        assert backend._fade_calls[-1][0] == 0.5  # restored to saved

    def test_small_change_does_not_trigger_override(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        ducker.initialize()
        ducker.duck(0.25)

        backend._current = 0.27  # within 5% of ducked level
        ducker.restore()
        assert backend._fade_calls[-1][0] == 0.5  # restored to saved


# ═══════════════════════════════════════════════════════════════════════════
# Concurrency tests
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    def test_concurrent_cancel_and_stop(self, ducker: VolumeDucker, backend: FakeBackend) -> None:
        """ESC cancel + stop fire simultaneously — lock must serialize."""
        ducker.initialize()
        ducker.duck(0.25)

        errors: list[Exception] = []

        def cancel() -> None:
            try:
                ducker.restore()
            except Exception as e:
                errors.append(e)

        def stop() -> None:
            try:
                ducker.restore()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=cancel)
        t2 = threading.Thread(target=stop)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert not errors
        assert not ducker.is_ducked  # exactly one restore happened


# ═══════════════════════════════════════════════════════════════════════════
# Crash recovery tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrashRecovery:
    def test_crash_recovery_restores_stale_state(self, crash_recovery: DuckCrashRecovery) -> None:
        backend = FakeBackend(current=0.1)  # system stuck at ducked level
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
        )
        crash_recovery.save(VolumeState(linear=0.7, muted=False))

        ducker.initialize()
        # Volume should be restored to 0.7 (saved value)
        assert backend._current == 0.7
        # Stale file should be cleared
        assert crash_recovery.load_stale() is None

    def test_crash_recovery_callback_invoked(self, crash_recovery: DuckCrashRecovery) -> None:
        called_with: list[VolumeState] = []

        def on_restore(state: VolumeState) -> None:
            called_with.append(state)

        backend = FakeBackend(current=0.1)
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
            on_crash_restore=on_restore,
        )
        crash_recovery.save(VolumeState(linear=0.7, muted=False))

        ducker.initialize()
        assert len(called_with) == 1
        assert called_with[0].linear == 0.7

    def test_no_stale_file_means_no_restore(self, crash_recovery: DuckCrashRecovery) -> None:
        backend = FakeBackend(current=0.3)
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
        )
        ducker.initialize()
        # Volume unchanged
        assert backend._current == 0.3

    def test_duck_persists_state_for_crash_recovery(self, crash_recovery: DuckCrashRecovery) -> None:
        backend = FakeBackend(current=0.6)
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
        )
        ducker.initialize()
        ducker.duck(0.25)
        assert crash_recovery.load_stale() is not None
        assert crash_recovery.load_stale().linear == 0.6  # type: ignore[union-attr]

    def test_restore_clears_crash_recovery(self, crash_recovery: DuckCrashRecovery) -> None:
        backend = FakeBackend(current=0.6)
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
        )
        ducker.initialize()
        ducker.duck(0.25)
        assert crash_recovery.load_stale() is not None

        ducker.restore()
        assert crash_recovery.load_stale() is None


# ═══════════════════════════════════════════════════════════════════════════
# Backend failure tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBackendFailure:
    def test_failing_backend_initialize_returns_false(self) -> None:
        backend = FakeFailingBackend()
        ducker = VolumeDucker(backend=backend)
        assert not ducker.initialize()
        assert not ducker.is_available

    def test_failing_backend_duck_returns_false(self) -> None:
        backend = FakeFailingBackend()
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        assert not ducker.duck(0.25)

    def test_failing_backend_restore_returns_false(self) -> None:
        backend = FakeFailingBackend()
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        # restore() when not ducked is no-op success regardless of backend.
        # But duck() should fail, and restore() after a failed duck should
        # also return True (no-op, since _saved_state was never set).
        assert not ducker.duck(0.25)  # duck fails — backend not ready
        assert ducker.restore() is True  # not ducked → no-op success

    def test_none_backend_initialize_returns_false(self) -> None:
        ducker = VolumeDucker(backend=None)
        # initialize will try platform.get_volume_backend() which may return
        # a real backend on this platform.  To test the None path, we mock.
        import voice_typer.server.server_platform as plat

        original = plat.get_volume_backend
        plat.get_volume_backend = lambda: None  # type: ignore[assignment]
        try:
            assert not ducker.initialize()
            assert not ducker.is_available
        finally:
            plat.get_volume_backend = original  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════
# Introspection tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntrospection:
    def test_backend_name(self, ducker: VolumeDucker) -> None:
        ducker.initialize()
        assert ducker.backend_name == "fake"

    def test_supports_per_session(self, ducker: VolumeDucker) -> None:
        ducker.initialize()
        assert ducker.supports_per_session is True

    def test_is_available_after_init(self, ducker: VolumeDucker) -> None:
        assert not ducker.is_available
        ducker.initialize()
        assert ducker.is_available


# ═══════════════════════════════════════════════════════════════════════════
# DuckCrashRecovery unit tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDuckCrashRecoveryFile:
    def test_save_and_load(self, crash_recovery: DuckCrashRecovery) -> None:
        state = VolumeState(linear=0.42, muted=True)
        crash_recovery.save(state)
        loaded = crash_recovery.load_stale()
        assert loaded is not None
        assert loaded.linear == 0.42
        assert loaded.muted is True

    def test_load_returns_none_when_no_file(self, crash_recovery: DuckCrashRecovery) -> None:
        assert crash_recovery.load_stale() is None

    def test_clear_deletes_file(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        assert crash_recovery.path.exists()
        crash_recovery.clear()
        assert not crash_recovery.path.exists()

    def test_clear_when_no_file_is_noop(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.clear()  # should not raise
        assert not crash_recovery.path.exists()

    def test_corrupt_file_returns_none_and_clears(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.path.parent.mkdir(parents=True, exist_ok=True)
        crash_recovery.path.write_text("{invalid json")
        assert crash_recovery.load_stale() is None
        assert not crash_recovery.path.exists()


# ═══════════════════════════════════════════════════════════════════════════
# UE-23: restore() must call _stop_smart_duck_monitor() under self._lock
# ═══════════════════════════════════════════════════════════════════════════


class TestUE23StopMonitorUnderLock:
    """UE-23: restore() must call _stop_smart_duck_monitor() from inside
    self._lock so the stop + _saved_state-clear is atomic with respect
    to a concurrent duck() (which calls _start_smart_duck_monitor()
    under the same lock).
    """

    def test_stop_smart_duck_monitor_called_under_lock(self) -> None:
        """_stop_smart_duck_monitor() is invoked while self._lock is held."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()
        ducker.duck(0.25)  # smart-duck skips, monitor starts
        assert ducker.is_monitor_running

        spy_done = threading.Event()
        proceed = threading.Event()
        lock_held_during_stop: list[bool] = [False]
        original_stop = ducker._stop_smart_duck_monitor

        def spy_stop() -> None:
            # From a worker thread, try to acquire the lock with a short
            # timeout.  If restore() holds the lock (the UE-23 fix), the
            # worker times out and acquired is False.  We use a worker
            # thread because Lock.acquire(blocking=False) from the same
            # thread that holds the lock also returns False (Lock is not
            # reentrant) -- we cannot distinguish "held by current
            # thread" from "held by another thread" without a second
            # thread.
            result: list[bool] = [True]

            def worker() -> None:
                acquired = ducker._lock.acquire(timeout=0.1)
                if acquired:
                    ducker._lock.release()
                else:
                    result[0] = False

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=1.0)
            lock_held_during_stop[0] = not result[0]
            # Signal the test thread that we've recorded the lock state.
            spy_done.set()
            # Block here so the test thread can read lock_held_during_stop
            # before the restore thread continues (and potentially
            # releases the lock, which would make a later re-check
            # incorrect).
            proceed.wait(timeout=2.0)
            original_stop()

        ducker._stop_smart_duck_monitor = spy_stop  # type: ignore[assignment]

        restore_thread = threading.Thread(target=ducker.restore)
        restore_thread.start()
        assert spy_done.wait(timeout=2.0), "_stop_smart_duck_monitor not called by restore()"
        assert lock_held_during_stop[0], (
            "UE-23: _stop_smart_duck_monitor() must be called while "
            "self._lock is held (prevents premature-stop race with "
            "concurrent duck())"
        )
        # Release the spy so restore() can finish.
        proceed.set()
        restore_thread.join(timeout=2.0)
        assert not restore_thread.is_alive(), "restore thread did not finish"

    def test_concurrent_duck_after_restore_starts_fresh_monitor(self) -> None:
        """Functional race test: after restore() + a new duck(), the new
        dictation's monitor is alive (UE-23 regression).

        This is the user-facing scenario: dictation 1 ends (restore),
        dictation 2 starts immediately (duck with smart-duck skip).  The
        monitor for dictation 2 must be running so audio starting
        mid-dictation triggers a retroactive duck.
        """
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        # Dictation 1: smart-duck skips, monitor starts.
        ducker.duck(0.25)
        assert ducker.is_monitor_running
        ducker.restore()
        # Give the old monitor a moment to wind down.
        deadline = time.monotonic() + 1.0
        while ducker.is_monitor_running and time.monotonic() < deadline:
            time.sleep(0.01)

        # Dictation 2: smart-duck skips again -- a FRESH monitor must start.
        ducker.duck(0.25)
        assert ducker.is_monitor_running, (
            "UE-23: the second dictation's smart-duck monitor must be running (premature-stop race would leave it dead)"
        )
        ducker.restore()


# ═══════════════════════════════════════════════════════════════════════════
# UE-12-F6: duck() must drop self._lock during backend.fade_to()
# ═══════════════════════════════════════════════════════════════════════════


class TestUE12F6DuckDropsLockDuringFade:
    """UE-12-F6: duck() must NOT hold self._lock during backend.fade_to()
    (up to 150 ms).  Holding the lock serialises restore() (ESC cancel)
    behind the fade -- visible as a 150 ms "ESC doesn't respond" delay.

    The pattern mirrors level_monitor/worker._process_level_chunk:
    snapshot shared state under the lock, release for the heavy work,
    re-acquire for the shared-state writes.
    """

    @staticmethod
    def _make_lock_probing_fade(ducker: VolumeDucker, backend: FakeBackend) -> tuple[Callable[..., bool], list[bool]]:
        """Wrap backend.fade_to so it records whether self._lock was held.

        Returns (spy_fade, lock_held_during_fade) where
        lock_held_during_fade[0] is True if the lock was held.
        """
        lock_held_during_fade: list[bool] = [False]
        original_fade = backend.fade_to

        def spy_fade(target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
            # Worker thread tries to acquire the lock with a short
            # timeout.  If duck() holds the lock during fade_to, the
            # worker times out.
            result: list[bool] = [True]

            def worker() -> None:
                acquired = ducker._lock.acquire(timeout=0.1)
                if acquired:
                    ducker._lock.release()
                else:
                    result[0] = False

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=1.0)
            lock_held_during_fade[0] = not result[0]
            return original_fade(target_linear, duration_ms, steps)

        return spy_fade, lock_held_during_fade

    def test_first_duck_drops_lock_during_fade(self) -> None:
        """First-duck path: the lock is NOT held during backend.fade_to()."""
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        spy_fade, lock_held = self._make_lock_probing_fade(ducker, backend)
        backend.fade_to = spy_fade  # type: ignore[assignment]

        ok = ducker.duck(0.25)
        assert ok is True
        assert lock_held[0] is False, (
            "UE-12-F6: duck() must NOT hold self._lock during "
            "backend.fade_to() on the first-duck path (ESC-cancel "
            "would wait 150ms for the fade to complete)"
        )

    def test_level_update_drops_lock_during_fade(self) -> None:
        """Already-ducked path: the lock is NOT held during the level-update fade."""
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ducker.duck(0.25)  # first duck (uses real fade_to -- unspied)
        backend._fade_calls.clear()

        spy_fade, lock_held = self._make_lock_probing_fade(ducker, backend)
        backend.fade_to = spy_fade  # type: ignore[assignment]

        ok = ducker.duck(0.15)  # level update
        assert ok is True
        assert lock_held[0] is False, (
            "UE-12-F6: duck() must NOT hold self._lock during backend.fade_to() on the level-update path"
        )

    def test_smart_duck_skip_does_not_fade(self) -> None:
        """Sanity: the smart-duck skip path returns early (no fade, lock
        released normally).  Guards against the UE-12-F6 refactor
        accidentally introducing a fade on the skip path."""
        backend = FakeBackend(current=0.5, speaker_active=False)
        ducker = VolumeDucker(backend=backend)
        ducker.set_smart_duck_poll_interval(50)
        ducker.initialize()

        fade_called = [False]
        original_fade = backend.fade_to

        def spy_fade(target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
            fade_called[0] = True
            return original_fade(target_linear, duration_ms, steps)

        backend.fade_to = spy_fade  # type: ignore[assignment]
        ok = ducker.duck(0.25)
        assert ok is True
        assert fade_called[0] is False, "Smart-duck skip must NOT call fade_to"
        ducker.restore()

    def test_duck_state_consistent_after_fade(self) -> None:
        """After duck() returns, _actually_ducked is True and _saved_state
        is set -- the post-fade re-acquire block updated them correctly."""
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()
        ok = ducker.duck(0.25)
        assert ok is True
        assert ducker.actually_ducked is True
        assert ducker.is_ducked is True
        assert backend._fade_calls[-1][0] == 0.25

    def test_restore_during_duck_fade_does_not_corrupt_state(self) -> None:
        """If restore() runs during duck()'s fade (now possible because
        duck() drops the lock), the post-fade re-acquire block must NOT
        mark _actually_ducked = True (restore() already cleared
        _saved_state and faded back)."""
        backend = FakeBackend(current=0.5, speaker_active=True)
        ducker = VolumeDucker(backend=backend)
        ducker.initialize()

        # Block the fade so we can interleave a restore() call.
        fade_started = threading.Event()
        proceed = threading.Event()
        original_fade = backend.fade_to

        def blocking_fade(target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
            fade_started.set()
            proceed.wait(timeout=2.0)
            return original_fade(target_linear, duration_ms, steps)

        backend.fade_to = blocking_fade  # type: ignore[assignment]

        errors: list[Exception] = []

        def duck_thread() -> None:
            try:
                ducker.duck(0.25)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=duck_thread)
        t.start()
        assert fade_started.wait(timeout=2.0), "fade_to not called"

        # While duck()'s fade is blocked (lock released per UE-12-F6),
        # run restore().  It should acquire the lock, clear _saved_state,
        # and fade back.
        ducker.restore()
        assert not ducker.is_ducked, "restore() should have cleared ducked state"

        # Release the duck fade -- it completes, re-acquires the lock,
        # sees _saved_state is None, skips the state update.
        proceed.set()
        t.join(timeout=2.0)
        assert not t.is_alive(), "duck thread did not finish"
        assert not errors, f"duck() raised: {errors}"

        # Final state: restore() won.  _actually_ducked must be False
        # (restore() set it) -- NOT True (which would happen if the
        # post-fade block didn't re-check _saved_state).
        assert ducker.actually_ducked is False, (
            "UE-12-F6: after restore() ran during duck()'s fade, "
            "_actually_ducked must remain False (post-fade re-check "
            "must skip the state update)"
        )
        assert not ducker.is_ducked
