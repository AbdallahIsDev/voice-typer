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
from typing import Optional

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
    def get_state(self) -> Optional[VolumeState]:
        """Read current volume + mute state.

        Returns ``None`` on failure (device disconnected, API error).
        """

    @abstractmethod
    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
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
        """
        current = self.get_state()
        if current is None:
            return self.set_linear(target_linear)

        if duration_ms <= 0 or steps <= 1:
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
