"""AB-15 regression tests for the smart-duck poll-interval clamp."""

from __future__ import annotations

from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_ducker import VolumeDucker


class SlowSubprocessBackend(VolumeBackend):
    """Backend mimicking Linux ``pactl`` / macOS ``osascript``."""

    @property
    def name(self) -> str:
        return "slow-subprocess"

    @property
    def supports_per_session(self) -> bool:
        return False

    def initialize(self) -> bool:
        return True

    def get_state(self) -> VolumeState | None:
        return VolumeState(linear=0.5, muted=False)

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        return True

    def fade_to(self, target_linear: float, duration_ms: int = 150, steps: int = 10) -> bool:
        return True

    def is_speaker_active(self) -> bool:
        return False

    @property
    def min_poll_interval_ms(self) -> int:
        return 1500


class TestClampPollInterval:
    """AB-15: the ``min_poll_interval_ms`` floor must apply on every"""

    def test_initialize_still_applies_floor(self) -> None:
        """Original XV-57 behaviour preserved: ``initialize`` clamps"""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker._smart_duck_poll_ms == 500  # pre-init default
        assert ducker.initialize() is True
        assert ducker._smart_duck_poll_ms == 1500

    def test_set_smart_duck_poll_interval_after_initialize_is_clamped(self) -> None:
        """AB-15 regression: after ``initialize`` has run (so the 2nd"""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        assert ducker._smart_duck_poll_ms == 1500

        # Simulate the second dictation's _duck_volume path: it calls
        ducker.set_smart_duck_poll_interval(500)
        assert ducker._smart_duck_poll_ms == 1500  # clamped, NOT 500

        # The second initialize() call is a no-op and must not undo the clamp.
        assert ducker.initialize() is True
        assert ducker._smart_duck_poll_ms == 1500

    def test_set_smart_duck_poll_interval_respects_user_value_above_floor(self) -> None:
        """Users who explicitly configure a SLOWER cadence are honoured."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(2000)
        assert ducker._smart_duck_poll_ms == 2000

    def test_set_smart_duck_poll_interval_clamps_below_50_floor(self) -> None:
        """The [50, 5000] hard clamp still applies; then the backend"""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(10)
        # 10 -> max(50, min(5000, 10)) = 50 -> max(50, 1500) = 1500
        assert ducker._smart_duck_poll_ms == 1500

    def test_set_smart_duck_poll_interval_clamps_above_5000_ceiling(self) -> None:
        """The 5000 ms ceiling is applied before the backend floor —"""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(7000)
        # 7000 -> max(50, min(5000, 7000)) = 5000 -> max(5000, 1500) = 5000
        assert ducker._smart_duck_poll_ms == 5000

    def test_set_smart_duck_poll_interval_before_initialize_passes_through(self) -> None:
        """When no backend is bound yet (production ducker is created"""
        ducker = VolumeDucker(backend=None)
        ducker.set_smart_duck_poll_interval(500)
        assert ducker._smart_duck_poll_ms == 500

    def test_set_smart_duck_poll_interval_with_in_process_backend_unaffected(self) -> None:
        """In-process backends (Windows IAudioMeterInformation, macOS"""

        class InProcessBackend(SlowSubprocessBackend):
            @property
            def name(self) -> str:
                return "in-process"

            @property
            def min_poll_interval_ms(self) -> int:
                return 0  # default, no floor

        ducker = VolumeDucker(backend=InProcessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(100)
        assert ducker._smart_duck_poll_ms == 100  # user value honoured

    def test_repeated_set_smart_duck_poll_interval_never_bypasses_floor(self) -> None:
        """Stress test: many calls in a row (mimicking many dictations"""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        for _ in range(50):
            ducker.set_smart_duck_poll_interval(500)
            assert ducker._smart_duck_poll_ms == 1500

    def test_clamp_poll_interval_helper_directly(self) -> None:
        """Unit-test the private helper in isolation."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker._clamp_poll_interval(500) == 1500
        assert ducker._clamp_poll_interval(2000) == 2000
        assert ducker._clamp_poll_interval(0) == 1500

        # No-backend ducker: helper passes value through unchanged.
        no_backend = VolumeDucker(backend=None)
        assert no_backend._clamp_poll_interval(500) == 500
        assert no_backend._clamp_poll_interval(0) == 0
