"""Windows volume backend, pycaw (IAudioEndpointVolume)."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from voice_typer.server._paths import APP_SLUG
from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState

log = logging.getLogger(__name__)

# number of consecutive backend failures before a WARNING is
_BACKEND_ERROR_WARN_THRESHOLD = 3


class WinVolumeBackend(VolumeBackend):
    """Windows volume control via pycaw / COM."""

    def __init__(self) -> None:
        self._vol = None  # IAudioEndpointVolume COM pointer
        self._meter = None  # IAudioMeterInformation COM pointer
        self._sessions: list = []  # saved (session, original_volume) tuples
        self._com_initialized = False
        # consecutive-error counter for observability.  Reset on
        self._consecutive_errors: int = 0

    @property
    def name(self) -> str:
        return "pycaw (WASAPI)"

    @property
    def supports_per_session(self) -> bool:
        return True

    def initialize(self) -> bool:
        if self._vol is not None:
            return True
        # reset the error counter on a fresh initialize() attempt.
        self._consecutive_errors = 0
        try:
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            if devices is None:
                log.warning("[VOLUME-WIN] No speakers endpoint found")
                return False

            # (pyrefly): bind the endpoint volume pointer to a
            vol_ptr: Any
            try:
                # pycaw >= 20251023: EndpointVolume is a direct property
                vol_ptr = devices.EndpointVolume
            except AttributeError:
                # pycaw < 20251023: use Activate
                interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                vol_ptr = cast(interface, POINTER(IAudioEndpointVolume))
            self._vol = vol_ptr
            # Get IAudioMeterInformation for smart-duck detection.
            try:
                from pycaw.pycaw import IAudioMeterInformation

                self._meter = vol_ptr.QueryInterface(IAudioMeterInformation)
            except Exception:
                self._meter = None
            self._com_initialized = True
            return True
        except ImportError:
            log.info("[VOLUME-WIN] pycaw not installed, ducking disabled")
            return False
        except Exception as exc:
            log.warning("[VOLUME-WIN] initialize failed: %s", exc)
            return False

    # ``_record_error`` increments the consecutive-failure counter and

    def _record_error(self, context: str, exc: BaseException) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors % _BACKEND_ERROR_WARN_THRESHOLD == 0:
            log.warning(
                "[VOLUME-WIN] %s failed %d times in a row (last error: %s) "
                "— safe-default returned, duck state preserved",
                context,
                self._consecutive_errors,
                exc,
            )

    def _record_success(self) -> None:
        if self._consecutive_errors:
            self._consecutive_errors = 0

    def get_state(self) -> VolumeState | None:
        if self._vol is None:
            return None
        try:
            scalar = float(self._vol.GetMasterVolumeLevelScalar())
            muted = bool(self._vol.GetMute())
            scalar = max(0.0, min(1.0, scalar))
            self._record_success()
            return VolumeState(linear=scalar, muted=muted)
        except Exception as exc:
            # per-failure DEBUG only; the threshold-based WARNING
            log.debug("[VOLUME-WIN] get_state failed: %s", exc)
            self._record_error("get_state", exc)
            return None

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        if self._vol is None:
            return False
        try:
            level = max(0.0, min(1.0, level))
            self._vol.SetMasterVolumeLevelScalar(level, None)
            if muted is not None:
                self._vol.SetMute(1 if muted else 0, None)
            return True
        except Exception as exc:
            # per-failure DEBUG; threshold WARNING handled by _record_error.
            log.debug("[VOLUME-WIN] set_linear failed: %s", exc)
            self._record_error("set_linear", exc)
            return False

    def is_speaker_active(self) -> bool:
        """Return ``True`` if any application is currently playing audio."""
        if self._meter is None:
            return True
        try:
            peak = float(self._meter.GetPeakValue())
            # Threshold at ~ -40 dBFS.  Below this, nothing audible is
            active = peak >= 0.01
            self._record_success()
            return active
        except Exception as exc:
            log.debug("[VOLUME-WIN] is_speaker_active failed: %s", exc)
            self._record_error("is_speaker_active", exc)
            return True

    def get_other_sessions(self) -> list:
        """Return foreign pycaw ``AudioSession`` objects (excluding own process)."""
        try:
            from pycaw.pycaw import AudioUtilities

            # PROC-FILTER-FIX: previously only excluded processes whose
            own_pid = os.getpid()
            sessions = []
            for session in AudioUtilities.GetAllSessions():
                proc = session.Process
                if proc is None:
                    continue
                # Definitive backstop: never duck ourselves by PID.
                try:
                    if proc.pid == own_pid:
                        continue
                except (AttributeError, OSError):
                    pass
                proc_name = proc.name().lower()
                # Substring match covers: voice_typer.exe (dev mode
                if (
                    "voice_typer" in proc_name
                    or APP_SLUG in proc_name
                    or proc_name in ("python", "python3", "pythonw", "pythonw.exe")
                    or re.match(r"^python\d+(\.\d+)*(\.exe)?$", proc_name)
                ):
                    continue
                sessions.append(session)
            return sessions
        except Exception as exc:
            log.debug("[VOLUME-WIN] get_other_sessions failed: %s", exc)
            return []

    def duck_other_sessions(self, level: float) -> bool:
        """Duck all foreign sessions to *level*, saving their original volume."""
        sessions = self.get_other_sessions()
        if not sessions:
            return False
        self._sessions = []
        level = max(0.0, min(1.0, level))
        for session in sessions:
            try:
                # pycaw >= 20251023: SimpleAudioVolume property
                vol = getattr(session, "SimpleAudioVolume", getattr(session, "_ctl", None))
                if vol is None:
                    continue
                original = vol.GetMasterVolume()
                vol.SetMasterVolume(level, None)
                self._sessions.append((vol, original))
            except Exception as exc:
                log.debug("[VOLUME-WIN] duck session failed: %s", exc)
        return len(self._sessions) > 0

    def restore_other_sessions(self) -> bool:
        """Restore foreign sessions to their pre-duck volume."""
        if not self._sessions:
            return False
        for vol, original in self._sessions:
            try:
                vol.SetMasterVolume(original, None)
            except Exception as exc:
                log.debug("[VOLUME-WIN] restore session failed: %s", exc)
        self._sessions = []
        return True
