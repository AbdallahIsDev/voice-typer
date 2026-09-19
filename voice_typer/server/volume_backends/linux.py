"""Linux volume backend, pactl → wpctl → amixer."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState

log = logging.getLogger(__name__)


# Smart-duck polling on Linux is expensive: every ``is_speaker_active()``
_LINUX_MIN_SMART_DUCK_POLL_MS = 1500

# number of consecutive backend failures before a WARNING is
_BACKEND_ERROR_WARN_THRESHOLD = 3


class LinuxVolumeBackend(VolumeBackend):
    """Linux volume control with automatic backend detection."""

    def __init__(self) -> None:
        self._tool: str | None = None
        # consecutive-error counter for ``_alsa_is_playing``
        self._consecutive_errors: int = 0

    @property
    def name(self) -> str:
        return f"linux ({self._tool})" if self._tool else "linux (uninitialised)"

    @property
    def supports_per_session(self) -> bool:
        return False

    @property
    def _set_linear_is_subprocess(self) -> bool:
        """Linux backends always spawn a subprocess (pactl/wpctl/amixer)."""
        return True

    @property
    def min_poll_interval_ms(self) -> int:
        """1500 ms, Linux smart-duck polls spawn ``pactl list sink-inputs``"""
        return _LINUX_MIN_SMART_DUCK_POLL_MS

    def initialize(self) -> bool:
        if self._tool is not None:
            return True
        # reset the error counter on a fresh initialize() attempt.
        self._consecutive_errors = 0
        for tool in ("pactl", "wpctl", "amixer"):
            if shutil.which(tool):
                self._tool = tool
                log.info("[VOLUME-LINUX] Using %s", tool)
                return True
        log.info("[VOLUME-LINUX] No volume tool found (pactl/wpctl/amixer)")
        return False

    # See ``WinVolumeBackend._record_error`` / ``_record_success`` for

    def _record_error(self, context: str, exc: BaseException) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors % _BACKEND_ERROR_WARN_THRESHOLD == 0:
            log.warning(
                "[VOLUME-LINUX] %s failed %d times in a row (last error: %s) "
                "— safe-default returned, duck state preserved",
                context,
                self._consecutive_errors,
                exc,
            )

    def _record_success(self) -> None:
        if self._consecutive_errors:
            self._consecutive_errors = 0

    def get_state(self) -> VolumeState | None:
        if self._tool == "pactl":
            return self._pactl_get()
        if self._tool == "wpctl":
            return self._wpctl_get()
        if self._tool == "amixer":
            return self._amixer_get()
        return None

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._tool == "pactl":
            return self._pactl_set(level, muted)
        if self._tool == "wpctl":
            return self._wpctl_set(level, muted)
        if self._tool == "amixer":
            return self._amixer_set(level, muted)
        return False

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio is currently playing on the default sink."""
        if self._tool == "pactl" or self._tool == "wpctl":
            # Try pactl first (works on PulseAudio + PipeWire compat).
            out = self._run(["pactl", "list", "sink-inputs"], timeout=1.5)
            if out is not None:
                # Output contains blocks like:
                return "State: running" in out
            # pactl not available (wpctl-only PipeWire), fall through
        if self._tool == "amixer" or self._tool == "wpctl":
            # ALSA procfs fallback: scan all cards' playback substreams
            return self._alsa_is_playing()
        return True  # unknown tool, duck to be safe

    def _alsa_is_playing(self) -> bool:
        """Check /proc/asound for any actively-rendering PCM substream."""
        try:
            # (test compat): look up ``Path`` via the package
            from voice_typer.server import volume_backends as _vb_pkg

            Path = _vb_pkg.Path  # noqa: N806
            asound = Path("/proc/asound")
            if not asound.exists():
                # success (we successfully determined "not Linux")
                self._record_success()
                return True  # not Linux?, duck to be safe
            for card_dir in asound.iterdir():
                if not card_dir.name.startswith("card"):
                    continue
                # Playback substreams live under pcm*p/ (the 'p' suffix
                for pcm_dir in card_dir.glob("pcm*p"):
                    for sub in pcm_dir.glob("sub*"):
                        status_file = sub / "status"
                        if not status_file.exists():
                            continue
                        try:
                            content = status_file.read_text()
                            if "state: RUNNING" in content:
                                # success, a substream is running.
                                self._record_success()
                                return True
                        except (OSError, PermissionError):
                            continue
            # success, we successfully scanned /proc/asound and
            self._record_success()
            return False  # no running substreams found
        except Exception as exc:
            log.debug("[VOLUME-LINUX] _alsa_is_playing failed: %s", exc)
            # surface a WARNING after N consecutive failures so a
            self._record_error("_alsa_is_playing", exc)
            return True  # safe default

    def _run(self, cmd: list[str], timeout: float = 2.0) -> str | None:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0:
                log.debug("[VOLUME-LINUX] %s error: %s", cmd[0], result.stderr.strip())
                return None
            return result.stdout.strip()
        except Exception as exc:
            log.debug("[VOLUME-LINUX] %s failed: %s", cmd[0], exc)
            return None

    def _pactl_get(self) -> VolumeState | None:
        # ``pactl get-sink-volume`` and ``pactl get-sink-mute`` are
        with ThreadPoolExecutor(max_workers=2) as pool:
            vol_future = pool.submit(self._run, ["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            mute_future = pool.submit(self._run, ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
            out = vol_future.result()
            mute_out = mute_future.result()
        if not out:
            return None
        # Output: "Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: ..."
        match = re.search(r"(\d+)%", out)
        if not match:
            return None
        vol = int(match.group(1)) / 100.0
        muted = mute_out is not None and "yes" in mute_out.lower()
        return VolumeState(linear=vol, muted=muted)

    def _pactl_set(self, level: float, muted: bool | None) -> bool:
        pct = int(level * 100)
        ok = self._run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"]) is not None
        if muted is not None:
            mute_val = "1" if muted else "0"
            self._run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", mute_val])
        return ok

    def _wpctl_get(self) -> VolumeState | None:
        out = self._run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        if not out:
            return None
        # Output: "Volume: 0.50" or "Volume: 0.50 [MUTED]"
        match = re.search(r"Volume:\s*([\d.]+)", out)
        if not match:
            return None
        vol = float(match.group(1))
        muted = "[MUTED]" in out.upper()
        return VolumeState(linear=max(0.0, min(1.0, vol)), muted=muted)

    def _wpctl_set(self, level: float, muted: bool | None) -> bool:
        ok = self._run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}"]) is not None
        if muted is not None:
            mute_cmd = "mute" if muted else "unmute"
            self._run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", mute_cmd])
        return ok

    def _amixer_get(self) -> VolumeState | None:
        out = self._run(["amixer", "-D", "default", "sget", "Master"])
        if not out:
            return None
        # Output: "  Mono: Playback 50% [50%] [-6.00dB] [on]"
        match = re.search(r"\[(\d+)%\]", out)
        if not match:
            return None
        vol = int(match.group(1)) / 100.0
        muted = "[off]" in out.lower()
        return VolumeState(linear=vol, muted=muted)

    def _amixer_set(self, level: float, muted: bool | None) -> bool:
        pct = int(level * 100)
        ok = self._run(["amixer", "-D", "default", "sset", "Master", f"{pct}%"]) is not None
        if muted is not None:
            mute_val = "mute" if muted else "unmute"
            self._run(["amixer", "-D", "default", "sset", "Master", mute_val])
        return ok
