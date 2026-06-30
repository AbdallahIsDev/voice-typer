"""Concrete volume backends for Windows, macOS, and Linux.

Each backend implements :class:`voice_typer.server.volume_backend.VolumeBackend`.

Import order matters: ``get_volume_backend()`` in ``platform.py`` selects
the first backend whose :meth:`initialize` succeeds for the current
platform.  All imports of platform-specific libraries (pycaw, pyobjc,
subprocess CLI tools) are guarded so that the module imports cleanly on
any OS — the backend simply returns ``False`` from :meth:`initialize`
if its native library is unavailable.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from voice_typer.server.volume_backend import VolumeBackend, VolumeState

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Windows — pycaw (IAudioEndpointVolume)
# ═══════════════════════════════════════════════════════════════════════════


class WinVolumeBackend(VolumeBackend):
    """Windows volume control via pycaw / COM.

    Uses ``SetMasterVolumeLevelScalar`` (perceptual-linear) rather than
    ``SetMasterVolumeLevel`` (decibels) so that the 0.0–1.0 scale matches
    what the Windows volume slider shows — no non-linear dB conversion
    needed.

    Per-session ducking (ducking other apps' audio without touching the
    master volume, like Skype/Teams do) is supported via
    ``ISimpleAudioVolume``.
    """

    def __init__(self) -> None:
        self._vol = None  # IAudioEndpointVolume COM pointer
        self._meter = None  # IAudioMeterInformation COM pointer
        self._sessions: list = []  # saved (session, original_volume) tuples
        self._com_initialized = False

    @property
    def name(self) -> str:
        return "pycaw (WASAPI)"

    @property
    def supports_per_session(self) -> bool:
        return True

    def initialize(self) -> bool:
        if self._vol is not None:
            return True
        try:
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            if devices is None:
                log.warning("[VOLUME-WIN] No speakers endpoint found")
                return False
            try:
                # pycaw >= 20251023: EndpointVolume is a direct property
                self._vol = devices.EndpointVolume
            except AttributeError:
                # pycaw < 20251023: use Activate
                interface = devices.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                self._vol = cast(interface, POINTER(IAudioEndpointVolume))
            # Get IAudioMeterInformation for smart-duck detection.
            # Available on both old and new pycaw via QueryInterface
            # on the IAudioEndpointVolume pointer.
            try:
                from pycaw.pycaw import IAudioMeterInformation
                self._meter = self._vol.QueryInterface(IAudioMeterInformation)
            except Exception:
                self._meter = None
            self._com_initialized = True
            return True
        except ImportError:
            log.info("[VOLUME-WIN] pycaw not installed — ducking disabled")
            return False
        except Exception as exc:
            log.warning("[VOLUME-WIN] initialize failed: %s", exc)
            return False

    def get_state(self) -> Optional[VolumeState]:
        if self._vol is None:
            return None
        try:
            scalar = float(self._vol.GetMasterVolumeLevelScalar())
            muted = bool(self._vol.GetMute())
            scalar = max(0.0, min(1.0, scalar))
            return VolumeState(linear=scalar, muted=muted)
        except Exception as exc:
            log.warning("[VOLUME-WIN] get_state failed: %s", exc)
            return None

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
        if self._vol is None:
            return False
        try:
            level = max(0.0, min(1.0, level))
            self._vol.SetMasterVolumeLevelScalar(level, None)
            if muted is not None:
                self._vol.SetMute(1 if muted else 0, None)
            return True
        except Exception as exc:
            log.warning("[VOLUME-WIN] set_linear failed: %s", exc)
            return False

    def is_speaker_active(self) -> bool:
        """Return ``True`` if any application is currently playing audio.

        Uses ``IAudioMeterInformation.GetPeakValue()`` on the default
        render endpoint.  If no audio is playing, the peak is ≈ 0.0 and
        we can skip ducking — no point animating the volume icon for
        silence.
        """
        if self._meter is None:
            return True
        try:
            peak = float(self._meter.GetPeakValue())
            # Threshold at ~ -40 dBFS.  Below this, nothing audible is
            # coming out of the speakers.
            return peak >= 0.01
        except Exception as exc:
            log.debug("[VOLUME-WIN] is_speaker_active failed: %s", exc)
            return True

    def get_other_sessions(self) -> list:
        """Return foreign pycaw ``AudioSession`` objects (excluding own process)."""
        try:
            from pycaw.pycaw import AudioUtilities

            sessions = []
            for session in AudioUtilities.GetAllSessions():
                proc = session.Process
                if proc is None:
                    continue
                proc_name = proc.name().lower()
                if "voice_typer" in proc_name or "python" == proc_name:
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
                # pycaw < 20251023: private _ctl attribute
                vol = getattr(session, "SimpleAudioVolume",
                              getattr(session, "_ctl", None))
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


# ═══════════════════════════════════════════════════════════════════════════
# macOS — CoreAudio (pyobjc) with osascript fallback
# ═══════════════════════════════════════════════════════════════════════════


class MacVolumeBackend(VolumeBackend):
    """macOS volume control.

    Primary path: ``CoreAudio`` framework via pyobjc (in-process, <5 ms).
    Fallback: ``osascript`` shell command (200–500 ms latency, requires
    AppleScript permission on macOS 13+).

    macOS has no clean native per-app volume API, so
    :attr:`supports_per_session` is always ``False``.
    """

    def __init__(self) -> None:
        self._use_coreaudio = False
        self._default_device_id: Optional[int] = None

    @property
    def name(self) -> str:
        return "CoreAudio (pyobjc)" if self._use_coreaudio else "osascript"

    @property
    def supports_per_session(self) -> bool:
        return False

    def initialize(self) -> bool:
        # UX-2/BUGFIX: the CoreAudio (pyobjc) path is a STUB — every
        # _ca_* method returns None/False. Previously, if pyobjc was
        # installed, _use_coreaudio flipped to True and hijacked the
        # working osascript path, causing get_state() to return None
        # and duck() to silently fail ("get_state failed — not ducking").
        # Fix: always use osascript until CoreAudio is fully implemented.
        # The import check is kept (commented) to document what would
        # be needed for a future CoreAudio implementation.
        #
        # try:
        #     from CoreAudio import (
        #         AudioObjectGetPropertyData,
        #         kAudioHardwareServiceDeviceProperty_VirtualMasterVolume,
        #         kAudioHardwareServiceSystemObject,
        #         kAudioObjectPropertyElementMaster,
        #         kAudioObjectPropertyScopeOutput,
        #     )
        #     self._use_coreaudio = True
        #     ...
        # except ImportError:
        #     pass
        self._use_coreaudio = False
        log.info("[VOLUME-MAC] osascript backend ready (CoreAudio path is a stub — using osascript)")
        return True  # osascript is always available on macOS

    def get_state(self) -> Optional[VolumeState]:
        if self._use_coreaudio:
            return self._coreaudio_get_state()
        return self._osascript_get_state()

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._use_coreaudio:
            return self._coreaudio_set(level, muted)
        return self._osascript_set(level, muted)

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio is currently playing on the default output.

        Tries the CoreAudio ``kAudioDevicePropertyDeviceIsRunning``
        property first (in-process, <1 ms).  Falls back to ``osascript``
        querying the default output device's running state via the
        ``AudioDeviceList`` AppleScript suite — but that suite isn't
        available on stock macOS, so the osascript fallback is a
        best-effort check that looks for known audio-producing process
        names (Spotify, Safari, Chrome, etc.).  If neither path can
        determine activity, returns ``True`` (safe default — duck
        anyway).

        ``kAudioDevicePropertyDeviceIsRunning`` returns 1 when the
        device is actively rendering audio (i.e. some app has an
        active audio queue), 0 when idle.  This is the same signal
        the macOS volume HUD uses to decide whether to show the
        now-playing indicator.
        """
        if self._use_coreaudio:
            try:
                dev = self._get_default_output_device()
                if dev is None:
                    return True
                # Query kAudioDevicePropertyDeviceIsRunning (UInt32).
                # This is a per-device property; if any IOProc is
                # running on the device, it returns 1.
                import ctypes

                from CoreAudio import (  # type: ignore[import-not-found]
                    kAudioDevicePropertyDeviceIsRunning,
                    kAudioObjectPropertyElementMaster,
                    kAudioObjectPropertyScopeGlobal,
                )
                # AudioObjectPropertyAddress: (mSelector, mScope, mElement)
                # We use ctypes to allocate the struct and read the UInt32.
                # Address = (selector, scope, element) — all 4 bytes each.
                selector = ctypes.c_uint32(kAudioDevicePropertyDeviceIsRunning)
                scope = ctypes.c_uint32(kAudioObjectPropertyScopeGlobal)
                element = ctypes.c_uint32(kAudioObjectPropertyElementMaster)
                # The property address struct layout:
                # struct AudioObjectPropertyAddress {
                #     AudioObjectPropertySelector mSelector;
                #     AudioObjectPropertyScope     mScope;
                #     AudioObjectPropertyElement   mElement;
                # };
                addr = ctypes.c_uint32 * 3
                # We can't easily call AudioObjectGetPropertyData without
                # the full pyobjc bindings, so fall through to osascript.
                raise NotImplementedError("pyobjc struct handling deferred")
            except Exception as exc:
                log.debug("[VOLUME-MAC] CoreAudio is_speaker_active failed: %s", exc)
                # Fall through to osascript check below.

        # osascript fallback: check if any common audio-producing app
        # is running.  This is imperfect (a running app isn't necessarily
        # playing audio), but it's the best we can do without the full
        # CoreAudio pyobjc path.  Returns True (duck anyway) on any
        # error so we never silently skip ducking when we should.
        try:
            # `osascript -e 'tell application "System Events" to get name of '
            # `'every process whose background only is false'`
            # returns a comma-separated list of foreground app names.
            # We check for known audio-producing apps.  This isn't
            # perfect (a browser tab with paused YouTube still counts),
            # but it's a reasonable heuristic that avoids ducking when
            # the user is just dictating in a text editor with no media
            # app open.
            result = self._osascript_run(
                'tell application "System Events" to get name of every process '
                'whose background only is false',
                timeout=1.5,
            )
            if result is None:
                return True  # couldn't determine — duck to be safe
            # Known audio-producing apps.  If any of these is in the
            # foreground list, we assume audio *might* be playing and
            # duck.  This is conservative (we'd rather duck unnecessarily
            # than skip ducking when audio is actually playing).
            audio_apps = (
                "spotify", "safari", "chrome", "firefox", "edge",
                "music", "podcasts", "tv", "quicktime", "vlc",
                "youtube", "netflix", "disney", "hbo", "plex",
                "audible", "amazon music", "tidal", "deezer",
                "obs", "zoom", "teams", "discord", "slack",
                "meet", "webex", "google meet",
            )
            lower = result.lower()
            return any(app in lower for app in audio_apps)
        except Exception as exc:
            log.debug("[VOLUME-MAC] osascript is_speaker_active failed: %s", exc)
            return True  # safe default

    # ── CoreAudio (pyobjc) path ─────────────────────────────────────

    def _coreaudio_get_state(self) -> Optional[VolumeState]:
        try:

            # Get default output device
            dev = self._get_default_output_device()
            if dev is None:
                return None
            # Read volume scalar (0.0–1.0)
            vol = self._ca_get_volume(dev)
            muted = self._ca_get_mute(dev)
            if vol is None:
                return None
            return VolumeState(linear=vol, muted=bool(muted))
        except Exception as exc:
            log.warning("[VOLUME-MAC] CoreAudio get_state failed: %s", exc)
            return None

    def _coreaudio_set(self, level: float, muted: Optional[bool]) -> bool:
        try:
            dev = self._get_default_output_device()
            if dev is None:
                return False
            ok = self._ca_set_volume(dev, level)
            if muted is not None:
                self._ca_set_mute(dev, muted)
            return ok
        except Exception as exc:
            log.warning("[VOLUME-MAC] CoreAudio set failed: %s", exc)
            return False

    def _get_default_output_device(self) -> Optional[int]:
        """Return the default audio output device ID."""
        try:

            # Property address for default output device
            # AudioObjectPropertyAddress is a struct; use ctypes to get the int.
            # This is a simplification — in production code we'd use the full
            # pyobjc struct. For now, use osascript to get the device and
            # fall back if it fails.
            # NOTE: Full CoreAudio implementation requires careful pyobjc
            # struct handling. The osascript fallback is reliable, so we
            # delegate volume get/set to osascript even in "coreaudio" mode
            # for v1, and reserve the pyobjc path for v2 where we can test
            # on real macOS hardware.
            raise NotImplementedError("delegating to osascript")
        except Exception:
            return None

    def _ca_get_volume(self, dev: int) -> Optional[float]:
        return None

    def _ca_get_mute(self, dev: int) -> Optional[bool]:
        return None

    def _ca_set_volume(self, dev: int, level: float) -> bool:
        return False

    def _ca_set_mute(self, dev: int, muted: bool) -> bool:
        return False

    # ── osascript fallback path ─────────────────────────────────────

    def _osascript_run(self, script: str, timeout: float = 2.0) -> Optional[str]:
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                log.debug("[VOLUME-MAC] osascript error: %s", result.stderr.strip())
                return None
            return result.stdout.strip()
        except Exception as exc:
            log.debug("[VOLUME-MAC] osascript failed: %s", exc)
            return None

    def _osascript_get_state(self) -> Optional[VolumeState]:
        vol_str = self._osascript_run("output volume of (get volume settings)")
        if vol_str is None:
            return None
        try:
            vol = int(vol_str) / 100.0
        except ValueError:
            return None
        mute_str = self._osascript_run("output muted of (get volume settings)")
        muted = mute_str is not None and mute_str.lower() == "true"
        return VolumeState(linear=max(0.0, min(1.0, vol)), muted=muted)

    def _osascript_set(self, level: float, muted: Optional[bool]) -> bool:
        pct = int(level * 100)
        ok = self._osascript_run(f"set volume output volume {pct}") is not None
        if muted is not None:
            mute_str = "true" if muted else "false"
            self._osascript_run(f"set volume output muted {mute_str}")
        return ok


# ═══════════════════════════════════════════════════════════════════════════
# Linux — pactl (PulseAudio/PipeWire) → wpctl (PipeWire) → amixer (ALSA)
# ═══════════════════════════════════════════════════════════════════════════


class LinuxVolumeBackend(VolumeBackend):
    """Linux volume control with automatic backend detection.

    Detection order:
      1. ``pactl`` — works on both PulseAudio and PipeWire (via compat layer).
         Handles ~95% of desktop Linux installs.
      2. ``wpctl`` — WirePlumber CLI, native to PipeWire-only systems
         that dropped the PulseAudio compat layer.
      3. ``amixer`` — ALSA hardware mixer, the last-resort fallback for
         bare ALSA systems (Raspbian Lite, minimal servers, embedded).

    Per-session ducking is theoretically possible via
    ``pactl set-sink-input-volume`` but enumeration is fragile, so
    :attr:`supports_per_session` is ``False`` for v1.
    """

    def __init__(self) -> None:
        self._tool: Optional[str] = None

    @property
    def name(self) -> str:
        return f"linux ({self._tool})" if self._tool else "linux (uninitialised)"

    @property
    def supports_per_session(self) -> bool:
        return False

    def initialize(self) -> bool:
        if self._tool is not None:
            return True
        for tool in ("pactl", "wpctl", "amixer"):
            if shutil.which(tool):
                self._tool = tool
                log.info("[VOLUME-LINUX] Using %s", tool)
                return True
        log.info("[VOLUME-LINUX] No volume tool found (pactl/wpctl/amixer)")
        return False

    def get_state(self) -> Optional[VolumeState]:
        if self._tool == "pactl":
            return self._pactl_get()
        if self._tool == "wpctl":
            return self._wpctl_get()
        if self._tool == "amixer":
            return self._amixer_get()
        return None

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._tool == "pactl":
            return self._pactl_set(level, muted)
        if self._tool == "wpctl":
            return self._wpctl_set(level, muted)
        if self._tool == "amixer":
            return self._amixer_set(level, muted)
        return False

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio is currently playing on the default sink.

        Per-tool implementation:

        - **pactl**: ``pactl list sink-inputs`` — if any sink-input has
          ``State: running``, audio is being rendered.  Works on both
          PulseAudio and PipeWire (via the PulseAudio compat layer).
        - **wpctl**: PipeWire's ``pw-top`` would give per-client
          activity, but it's heavy.  Instead we try ``pactl list
          sink-inputs`` first (PipeWire ships the PulseAudio compat
          layer on most distros); if that fails, fall back to checking
          ``/proc/asound`` for ALSA-level activity.
        - **amixer (ALSA-only)**: scan
          ``/proc/asound/card*/pcm0p/sub*/status`` for
          ``state: RUNNING``.  This is the kernel-level signal that an
          audio stream is actively being rendered.  Works on bare ALSA
          systems without a sound server.

        Returns ``True`` (duck anyway) on any error so we never
        silently skip ducking when we should.
        """
        if self._tool == "pactl" or self._tool == "wpctl":
            # Try pactl first (works on PulseAudio + PipeWire compat).
            # For wpctl-only systems without pactl, _run will return None
            # and we fall through to the ALSA procfs check.
            out = self._run(["pactl", "list", "sink-inputs"], timeout=1.5)
            if out is not None:
                # Output contains blocks like:
                #   Sink Input #42
                #       State: running
                #       ...
                # We look for any "State: running" or "State: corked"
                # (corked = temporarily paused, but the stream exists).
                # Only "running" means audio is actually being produced.
                return "State: running" in out
            # pactl not available (wpctl-only PipeWire) — fall through
            # to the ALSA procfs check below.
        if self._tool == "amixer" or self._tool == "wpctl":
            # ALSA procfs fallback: scan all cards' playback substreams
            # for "state: RUNNING".  This is the kernel-level signal.
            return self._alsa_is_playing()
        return True  # unknown tool — duck to be safe

    def _alsa_is_playing(self) -> bool:
        """Check /proc/asound for any actively-rendering PCM substream."""
        try:
            asound = Path("/proc/asound")
            if not asound.exists():
                return True  # not Linux? — duck to be safe
            for card_dir in asound.iterdir():
                if not card_dir.name.startswith("card"):
                    continue
                # Playback substreams live under pcm*p/ (the 'p' suffix
                # means playback; 'c' means capture).  Each substream
                # has a `status` file that contains "state: RUNNING"
                # when audio is being rendered.
                for pcm_dir in card_dir.glob("pcm*p"):
                    for sub in pcm_dir.glob("sub*"):
                        status_file = sub / "status"
                        if not status_file.exists():
                            continue
                        try:
                            content = status_file.read_text()
                            if "state: RUNNING" in content:
                                return True
                        except (OSError, PermissionError):
                            continue
            return False  # no running substreams found
        except Exception as exc:
            log.debug("[VOLUME-LINUX] _alsa_is_playing failed: %s", exc)
            return True  # safe default

    # ── pactl (PulseAudio / PipeWire compat) ────────────────────────

    def _run(self, cmd: list[str], timeout: float = 2.0) -> Optional[str]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                log.debug("[VOLUME-LINUX] %s error: %s", cmd[0], result.stderr.strip())
                return None
            return result.stdout.strip()
        except Exception as exc:
            log.debug("[VOLUME-LINUX] %s failed: %s", cmd[0], exc)
            return None

    def _pactl_get(self) -> Optional[VolumeState]:
        out = self._run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        if not out:
            return None
        # Output: "Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: ..."
        match = re.search(r"(\d+)%", out)
        if not match:
            return None
        vol = int(match.group(1)) / 100.0
        mute_out = self._run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        muted = mute_out is not None and "yes" in mute_out.lower()
        return VolumeState(linear=vol, muted=muted)

    def _pactl_set(self, level: float, muted: Optional[bool]) -> bool:
        pct = int(level * 100)
        ok = self._run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"]) is not None
        if muted is not None:
            mute_val = "1" if muted else "0"
            self._run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", mute_val])
        return ok

    # ── wpctl (WirePlumber / PipeWire native) ───────────────────────

    def _wpctl_get(self) -> Optional[VolumeState]:
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

    def _wpctl_set(self, level: float, muted: Optional[bool]) -> bool:
        ok = self._run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}"]) is not None
        if muted is not None:
            mute_cmd = "mute" if muted else "unmute"
            self._run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", mute_cmd])
        return ok

    # ── amixer (ALSA fallback) ──────────────────────────────────────

    def _amixer_get(self) -> Optional[VolumeState]:
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

    def _amixer_set(self, level: float, muted: Optional[bool]) -> bool:
        pct = int(level * 100)
        ok = self._run(
            ["amixer", "-D", "default", "sset", "Master", f"{pct}%"]
        ) is not None
        if muted is not None:
            mute_val = "mute" if muted else "unmute"
            self._run(["amixer", "-D", "default", "sset", "Master", mute_val])
        return ok
