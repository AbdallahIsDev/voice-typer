"""AB-15 regression tests for the smart-duck poll-interval clamp.

The backend's ``min_poll_interval_ms`` floor (XV-57) was originally
applied ONLY inside :meth:`VolumeDucker.initialize`, which no-ops
after the first successful call.  Because ``VolumeController.
_duck_volume`` calls ``set_smart_duck_poll_interval`` on EVERY
dictation start (before the now-no-op ``initialize``), the 2nd and
later dictations silently bypassed the floor on Linux ``pactl`` (and
macOS ``osascript``) backends — burning 10–20 % CPU on one core for
the duration of every subsequent dictation.

These tests pin the AB-15 fix: the floor is now applied by a shared
``_clamp_poll_interval`` helper invoked from BOTH ``initialize`` and
``set_smart_duck_poll_interval``, so re-setting the interval after
``initialize`` cannot bypass the floor.
"""

from __future__ import annotations

from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_ducker import VolumeDucker


class SlowSubprocessBackend(VolumeBackend):
    """Backend mimicking Linux ``pactl`` / macOS ``osascript``.

    ``min_poll_interval_ms = 1500`` matches the production Linux
    backend — polling faster than this wastes 10–20 % CPU per core
    on subprocess spawning.
    """

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
    """AB-15: the ``min_poll_interval_ms`` floor must apply on every
    ``set_smart_duck_poll_interval`` call — not only inside ``initialize``.
    """

    def test_initialize_still_applies_floor(self) -> None:
        """Original XV-57 behaviour preserved: ``initialize`` clamps
        the default 500 ms up to the backend's 1500 ms minimum."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker._smart_duck_poll_ms == 500  # pre-init default
        assert ducker.initialize() is True
        assert ducker._smart_duck_poll_ms == 1500

    def test_set_smart_duck_poll_interval_after_initialize_is_clamped(self) -> None:
        """AB-15 regression: after ``initialize`` has run (so the 2nd
        call to ``initialize`` is a no-op), re-setting the interval to
        500 ms MUST still be clamped up to the backend's 1500 ms
        minimum — otherwise the smart-duck monitor polls at 500 ms and
        burns 10–20 % CPU per core on Linux ``pactl`` for the rest of
        the dictation."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        assert ducker._smart_duck_poll_ms == 1500

        # Simulate the second dictation's _duck_volume path: it calls
        # set_smart_duck_poll_interval BEFORE the (now no-op) initialize.
        ducker.set_smart_duck_poll_interval(500)
        assert ducker._smart_duck_poll_ms == 1500  # clamped — NOT 500

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
        """The [50, 5000] hard clamp still applies; then the backend
        floor is applied on top — so an out-of-range low value ends up
        at the backend minimum, not 50."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(10)
        # 10 -> max(50, min(5000, 10)) = 50 -> max(50, 1500) = 1500
        assert ducker._smart_duck_poll_ms == 1500

    def test_set_smart_duck_poll_interval_clamps_above_5000_ceiling(self) -> None:
        """The 5000 ms ceiling is applied before the backend floor —
        an out-of-range high value ends up at 5000 (above the floor)."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(7000)
        # 7000 -> max(50, min(5000, 7000)) = 5000 -> max(5000, 1500) = 5000
        assert ducker._smart_duck_poll_ms == 5000

    def test_set_smart_duck_poll_interval_before_initialize_passes_through(self) -> None:
        """When no backend is bound yet (production ducker is created
        with ``backend=None`` and auto-detects inside ``initialize``),
        the helper returns the value unchanged — the floor is applied
        later by ``initialize``.  This preserves the first-dictation
        behaviour the production code path relies on."""
        ducker = VolumeDucker(backend=None)
        ducker.set_smart_duck_poll_interval(500)
        assert ducker._smart_duck_poll_ms == 500

    def test_set_smart_duck_poll_interval_with_in_process_backend_unaffected(self) -> None:
        """In-process backends (Windows IAudioMeterInformation, macOS
        CoreAudio) keep the default ``min_poll_interval_ms = 0`` —
        their polls are <1 ms, so the floor must NOT slow them down.
        This guards against the fix over-clamping fast backends."""

        class InProcessBackend(SlowSubprocessBackend):
            @property
            def name(self) -> str:
                return "in-process"

            @property
            def min_poll_interval_ms(self) -> int:
                return 0  # default — no floor

        ducker = VolumeDucker(backend=InProcessBackend())
        assert ducker.initialize() is True
        ducker.set_smart_duck_poll_interval(100)
        assert ducker._smart_duck_poll_ms == 100  # user value honoured

    def test_repeated_set_smart_duck_poll_interval_never_bypasses_floor(self) -> None:
        """Stress test: many calls in a row (mimicking many dictations
        in a session) must never let the value drop below the floor."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        assert ducker.initialize() is True
        for _ in range(50):
            ducker.set_smart_duck_poll_interval(500)
            assert ducker._smart_duck_poll_ms == 1500

    def test_clamp_poll_interval_helper_directly(self) -> None:
        """Unit-test the private helper in isolation."""
        ducker = VolumeDucker(backend=SlowSubprocessBackend())
        # Pre-initialize: backend is bound (passed via __init__) so the
        # helper already sees the floor.
        assert ducker._clamp_poll_interval(500) == 1500
        assert ducker._clamp_poll_interval(2000) == 2000
        assert ducker._clamp_poll_interval(0) == 1500

        # No-backend ducker: helper passes value through unchanged.
        no_backend = VolumeDucker(backend=None)
        assert no_backend._clamp_poll_interval(500) == 500
        assert no_backend._clamp_poll_interval(0) == 0
