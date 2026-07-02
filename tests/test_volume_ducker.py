"""Tests for VolumeDucker, VolumeBackend ABC, and DuckCrashRecovery.

Uses a FakeBackend that implements the VolumeBackend ABC without any
real audio hardware, so tests run on any platform (CI included).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import pytest

from voice_typer.server.volume_backend import VolumeBackend, VolumeState
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
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
        self._set_calls: list[tuple[float, Optional[bool]]] = []
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

    def get_state(self) -> Optional[VolumeState]:
        return VolumeState(linear=self._current, muted=self._muted)

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
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

    def get_state(self) -> Optional[VolumeState]:
        return None

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
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
        assert any(call[1] is True for call in backend._set_calls), \
            "restore should have set muted=True"


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
    def test_crash_recovery_restores_stale_state(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
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

    def test_crash_recovery_callback_invoked(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
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

    def test_no_stale_file_means_no_restore(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
        backend = FakeBackend(current=0.3)
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
        )
        ducker.initialize()
        # Volume unchanged
        assert backend._current == 0.3

    def test_duck_persists_state_for_crash_recovery(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
        backend = FakeBackend(current=0.6)
        ducker = VolumeDucker(
            backend=backend,
            crash_recovery=crash_recovery,
        )
        ducker.initialize()
        ducker.duck(0.25)
        assert crash_recovery.load_stale() is not None
        assert crash_recovery.load_stale().linear == 0.6  # type: ignore[union-attr]

    def test_restore_clears_crash_recovery(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
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

    def test_corrupt_file_returns_none_and_clears(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
        crash_recovery.path.parent.mkdir(parents=True, exist_ok=True)
        crash_recovery.path.write_text("{invalid json")
        assert crash_recovery.load_stale() is None
        assert not crash_recovery.path.exists()
