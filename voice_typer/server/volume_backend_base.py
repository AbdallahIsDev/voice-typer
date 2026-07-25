"""Abstract volume control backend interface.

Defines the platform-agnostic contract for reading and setting system
audio volume.  All concrete backends (Windows pycaw, macOS CoreAudio,
Linux pactl/wpctl/amixer) implement this interface.

Volumes are exchanged in **perceptual-linear** scale [0.0, 1.0]:
  - 0.0 = silent
  - 1.0 = maximum
  - 0.25 = quiet but audible (the default duck level)

Backends convert to/from their native units (dB, percentage, 0–65536)
internally so that ``VolumeDucker`` can work with a single uniform scale.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolumeState:
    """Immutable snapshot of system volume for save/restore.

    ``linear`` is perceptual-linear (0.0 = silent, 1.0 = max) — the same
    scale used by the duck level.  Backends convert to/from their native
    units.
    """

    linear: float
    muted: bool


class VolumeBackend(ABC):
    """Abstract platform volume controller.

    All volumes are exchanged in perceptual-linear scale [0.0, 1.0].
    Backends handle conversion to dB / percent / native units.

    Implementations must be safe to construct on any platform (the
    constructor should not fail), with actual resource acquisition
    deferred to :meth:`initialize`.
    """

    # ── Properties ──────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend identifier, e.g. ``'pycaw (WASAPI)'``."""

    @property
    @abstractmethod
    def supports_per_session(self) -> bool:
        """``True`` if the backend can duck individual audio sessions
        (Windows ``ISimpleAudioVolume``).  ``False`` = master-volume only."""

    # ── Lifecycle ───────────────────────────────────────────────────

    @abstractmethod
    def initialize(self) -> bool:
        """Set up the backend (open devices, load libraries).

        Returns ``True`` if the backend is ready for use, ``False`` if
        unavailable (no device, missing library, permission denied).
        Must be idempotent — safe to call multiple times.
        """

    # ── Volume operations ───────────────────────────────────────────

    @abstractmethod
    def get_state(self) -> VolumeState | None:
        """Read current volume + mute state.

        Returns ``None`` on failure (device disconnected, API error).
        """

    @abstractmethod
    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        """Set volume in perceptual-linear scale.

        Parameters
        ----------
        level:
            Target volume in [0.0, 1.0].  Clamped internally.
        muted:
            If ``True``, mute the output.  If ``False``, unmute.
            If ``None``, leave the current mute state unchanged.

        Returns ``True`` on success, ``False`` on failure.
        """

    def fade_to(
        self,
        target_linear: float,
        duration_ms: int = 150,
        steps: int = 10,
    ) -> bool:
        """Ramp volume to *target_linear* over *duration_ms*.

        Default implementation uses *steps* discrete
        :meth:`set_linear` calls with equal-sized sleeps.  Backends with
        native fade support (e.g. Windows ``VolumeStepDown``) may
        override for smoother behaviour.

        The fade is **synchronous** — the caller blocks until complete.
        This is intentional: ``VolumeDucker`` calls this from a
        background thread, and a 150 ms block is acceptable.

        Subprocess backends (``_set_linear_is_subprocess == True``)
        collapse to a single :meth:`set_linear` call regardless of
        *steps*: each subprocess spawn costs 10–200 ms, so a 10-step
        fade would take 100 ms – 2 s and produce audible stepping
        plus mute artifacts on some DACs.  In-process backends
        (Windows pycaw, macOS CoreAudio) keep the multi-step ramp
        for smooth perceptual fades.
        """
        current = self.get_state()
        if current is None:
            return self.set_linear(target_linear)

        if duration_ms <= 0 or steps <= 1 or self._set_linear_is_subprocess:
            return self.set_linear(target_linear)

        start = current.linear
        sleep_s = duration_ms / steps / 1000.0
        for i in range(1, steps + 1):
            t = i / steps
            val = start + (target_linear - start) * t
            val = max(0.0, min(1.0, val))
            self.set_linear(val)
            if i < steps:
                time.sleep(sleep_s)
        return True

    # ── Subprocess-vs-in-process hint ──────────────────────────────────
    #
    # Used by ``fade_to`` to decide whether to multi-step (in-process
    # backends, <1 ms per call) or collapse to a single set_linear
    # (subprocess backends, 10–200 ms per call).  Subclasses that spawn
    # a subprocess inside ``set_linear`` MUST override this to ``True``.
    #
    # Default ``False`` is the safe choice for in-process backends
    # (Windows pycaw, macOS CoreAudio).  Subprocess backends (Linux
    # pactl/wpctl/amixer, macOS osascript fallback) override to ``True``.
    #
    @property
    def _set_linear_is_subprocess(self) -> bool:
        """Return ``True`` if :meth:`set_linear` spawns a subprocess.

        Subprocess backends have 10–200 ms latency per ``set_linear``
        call.  ``fade_to`` collapses to a single call for these
        backends to avoid 10× subprocess overhead (audible stepping +
        multi-second latency).  In-process backends override to
        ``False`` to keep the smooth multi-step ramp.
        """
        return False

    # ── Speaker-activity detection ────────────────────────────────
    #
    # Smart ducking: if no application is currently playing audio
    # through the speakers, we can skip the duck entirely — no need
    # to animate the volume icon for nothing.
    #
    # The default implementation assumes audio IS playing (always
    # duck), which preserves backward compatibility for backends
    # that can't cheaply query speaker activity.
    #

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio output is currently playing.

        Used by ``VolumeDucker`` to skip unnecessary ducking when
        no application is producing sound.  Backends that can cheaply
        query speaker activity (e.g. Windows ``IAudioMeterInformation``)
        should override this; the default always returns ``True``.
        """
        return True

    # ── Polling hints ────────────────────────────────────────────────
    #
    # Smart-duck background monitor polls ``is_speaker_active()`` every
    # ``poll_interval_ms``.  Backends that can answer this query cheaply
    # (in-process C call, <1ms) can advertise a faster safe cadence;
    # backends that shell out to a subprocess (200-500ms per call)
    # should keep the conservative default so the monitor doesn't
    # starve the audio callback.
    #
    # ``VolumeDucker.initialize`` uses ``min(user_config, recommended)``
    # so the backend's recommendation acts as a *floor* on polling
    # speed: the monitor never polls *slower* than the backend
    # recommends, but the user can always go faster via config.
    #

    @property
    def recommended_poll_interval_ms(self) -> int:
        """Recommended smart-duck poll interval in milliseconds.

        Backends can override to advertise a faster safe cadence.
        ``VolumeDucker`` uses ``min(user_value, recommended)`` so the
        recommendation acts as a *floor* on polling speed (the monitor
        never polls *slower* than the backend recommends).  Users can
        always go faster via ``config.volume_duck_smart_poll_interval_ms``.

        Default of 500ms is conservative; backends with in-process
        queries (Windows IAudioMeterInformation ~0ms, macOS
        CoreAudio ~<1ms, Linux pactl ~50ms) may override with a
        smaller value.
        """
        return 500

    @property
    def min_poll_interval_ms(self) -> int:
        """Minimum safe smart-duck poll interval in milliseconds.

        Backends that spawn an expensive subprocess per
        :meth:`is_speaker_active` call (Linux ``pactl list sink-inputs``
        ~50–100 ms, macOS osascript ~200–500 ms) override this to
        advertise the *slowest* cadence the monitor should adopt.
        ``VolumeDucker`` uses ``max(user_value, min_poll_interval_ms)``
        so the monitor never polls *faster* than the backend can
        handle — preventing CPU waste (10–20% on Linux pactl at the
        default 500 ms cadence) and battery drain on laptops.

        Default ``0`` means no minimum (the monitor can poll as fast
        as the user configures).  In-process backends (Windows
        IAudioMeterInformation, macOS CoreAudio) keep the default
        because each poll is <1 ms.
        """
        return 0

    # ── Per-session support (Windows only) ──────────────────────────

    def get_other_sessions(self) -> list:
        """Return other audio sessions for per-session ducking.

        On Windows, returns a list of pycaw ``AudioSession`` objects
        (excluding this process).  On platforms without per-session
        support, returns an empty list.
        """
        return []

    def duck_other_sessions(self, level: float) -> bool:
        """Duck all foreign audio sessions to *level*.

        Only meaningful on Windows with per-session support enabled.
        Returns ``True`` if any sessions were ducked, ``False`` otherwise.
        """
        return False

    def restore_other_sessions(self) -> bool:
        """Restore foreign audio sessions to their pre-duck volume.

        Only meaningful on Windows with per-session support enabled.
        """
        return False
