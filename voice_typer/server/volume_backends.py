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
import sys
from pathlib import Path
from types import SimpleNamespace

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

    def get_state(self) -> VolumeState | None:
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
                if "voice_typer" in proc_name or proc_name == "python":
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


def _try_import_coreaudio() -> SimpleNamespace:
    """Lazy import of pyobjc CoreAudio symbols needed by ``MacVolumeBackend``.

    Returns a ``SimpleNamespace`` exposing the symbols needed by the
    backend.  Raises ``ImportError`` (with a clear, actionable message)
    on non-macOS platforms or when ``pyobjc-framework-CoreAudio`` is
    not installed.

    The caller (``MacVolumeBackend.initialize``) catches ``ImportError``
    and falls back to the osascript path.  This keeps the module
    importable on Linux/Windows where ``pyobjc-framework-CoreAudio``
    cannot be installed.

    The symbols are packaged in a ``SimpleNamespace`` (rather than
    imported at module scope) so that the module's top-level import
    surface stays stdlib-only — the same pattern used by
    :mod:`voice_typer.server.microphone_watcher_coreaudio`.
    """
    if sys.platform != "darwin":
        raise ImportError(
            "MacVolumeBackend's CoreAudio path is only available on macOS "
            f"(current platform: {sys.platform}). The osascript fallback "
            "will be used instead."
        )

    try:
        from CoreAudio import (  # noqa: PLC0415 — lazy import is the point
            AudioObjectGetPropertyData,
            AudioObjectSetPropertyData,
            kAudioDevicePropertyDeviceIsRunning,
            kAudioHardwarePropertyDefaultOutputDevice,
            kAudioHardwareServiceDeviceProperty_VirtualMasterMute,
            kAudioHardwareServiceDeviceProperty_VirtualMasterVolume,
            kAudioHardwareServiceSystemObject,
            kAudioObjectPropertyElementMaster,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyScopeOutput,
            kAudioObjectSystemObject,
        )
    except ImportError as exc:
        raise ImportError(
            "pyobjc-framework-CoreAudio is required for the CoreAudio "
            "volume backend. Install with: pip install "
            "pyobjc-framework-CoreAudio. The osascript fallback will "
            "be used instead."
        ) from exc

    return SimpleNamespace(
        get_property=AudioObjectGetPropertyData,
        set_property=AudioObjectSetPropertyData,
        # Per-device selectors
        prop_device_is_running=kAudioDevicePropertyDeviceIsRunning,
        # System-object selectors
        prop_default_output_device=kAudioHardwarePropertyDefaultOutputDevice,
        # HardwareService (system-wide virtual master) selectors — these
        # operate on the *default* output device via the system object,
        # so we don't need to track the device ID for volume/mute.
        prop_master_volume=kAudioHardwareServiceDeviceProperty_VirtualMasterVolume,
        prop_master_mute=kAudioHardwareServiceDeviceProperty_VirtualMasterMute,
        # Object IDs
        system_object=kAudioObjectSystemObject,
        hws_system_object=kAudioHardwareServiceSystemObject,
        # Scopes / elements
        scope_global=kAudioObjectPropertyScopeGlobal,
        scope_output=kAudioObjectPropertyScopeOutput,
        element_master=kAudioObjectPropertyElementMaster,
    )


class MacVolumeBackend(VolumeBackend):
    """macOS volume control.

    Primary path: ``CoreAudio`` framework via pyobjc (in-process, <1 ms).
    Fallback: ``osascript`` shell command (200–500 ms latency, requires
    AppleScript permission on macOS 13+).

    The CoreAudio path is enabled automatically when
    ``pyobjc-framework-CoreAudio`` is importable.  If pyobjc is missing
    (or any CoreAudio call fails at runtime), every method falls back
    to osascript — so the backend degrades gracefully without losing
    functionality.

    macOS has no clean native per-app volume API, so
    :attr:`supports_per_session` is always ``False``.

    Runtime testing on macOS is required: the CoreAudio calls cannot
    be exercised on Linux/Windows because pyobjc-framework-CoreAudio
    is macOS-only.  The implementation is code-correct per Apple's
    ``AudioHardwareService.h`` / ``AudioObject.h`` documentation, but
    no test in this repo exercises the live pyobjc path.
    """

    def __init__(self) -> None:
        self._use_coreaudio = False
        self._default_device_id: int | None = None
        # pyobjc CoreAudio symbols — loaded lazily in ``initialize()`` so
        # the module imports cleanly on Linux/Windows.  ``None`` when
        # pyobjc is unavailable OR ``initialize()`` has not been called.
        self._ca: SimpleNamespace | None = None

    @property
    def name(self) -> str:
        return "CoreAudio (pyobjc)" if self._use_coreaudio else "osascript"

    @property
    def supports_per_session(self) -> bool:
        return False

    @property
    def recommended_poll_interval_ms(self) -> int:
        """100ms when CoreAudio is active (in-process, <1ms per call),
        500ms when forced to osascript (subprocess, 200-500ms per call).

        ``VolumeDucker`` uses ``min(user_config, recommended)`` so this
        acts as a *floor* on polling speed: the monitor never polls
        *slower* than the backend recommends, but the user can always
        go faster via ``config.volume_duck_smart_poll_interval_ms``.
        """
        return 100 if self._use_coreaudio else 500

    def initialize(self) -> bool:
        """Initialize the backend.

        Tries to load ``pyobjc-framework-CoreAudio``.  On macOS with
        pyobjc installed, switches to the in-process CoreAudio path
        (<1 ms per call).  Otherwise, falls back to the osascript
        subprocess path (200–500 ms per call).

        Always returns ``True`` — osascript is always available on
        macOS, and pyobjc failing just means we use the slower path.
        The CoreAudio path can be re-tried on the next call if pyobjc
        becomes available later (e.g. user installs it); ``_use_coreaudio``
        is reset on each ``initialize()`` call.
        """
        try:
            self._ca = _try_import_coreaudio()
            self._use_coreaudio = True
            log.info(
                "[VOLUME-MAC] CoreAudio (pyobjc) backend ready — "
                "is_speaker_active will use kAudioDevicePropertyDeviceIsRunning"
            )
        except ImportError as exc:
            self._ca = None
            self._use_coreaudio = False
            log.info(
                "[VOLUME-MAC] osascript backend ready "
                "(pyobjc unavailable: %s)",
                exc,
            )
        return True  # osascript is always available on macOS

    def get_state(self) -> VolumeState | None:
        if self._use_coreaudio:
            state = self._coreaudio_get_state()
            if state is not None:
                return state
            # CoreAudio failed — fall through to osascript so the ducker
            # never silently skips a save/restore cycle.
            log.debug(
                "[VOLUME-MAC] CoreAudio get_state failed — using osascript"
            )
        return self._osascript_get_state()

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._use_coreaudio:
            if self._coreaudio_set(level, muted):
                return True
            log.debug(
                "[VOLUME-MAC] CoreAudio set failed — using osascript"
            )
        return self._osascript_set(level, muted)

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio is currently playing on the default output.

        Primary path (CoreAudio, in-process, <1 ms):

            1. Resolve the default output device's ``AudioDeviceID`` via
               ``AudioObjectGetPropertyData`` on ``kAudioObjectSystemObject``
               with selector ``kAudioHardwarePropertyDefaultOutputDevice``.
            2. Query ``kAudioDevicePropertyDeviceIsRunning`` on that
               device.  Returns a ``UInt32`` that is ``1`` when any
               IOProc is actively rendering audio (i.e. some app has
               an active audio queue), ``0`` when idle.  This is the
               same signal the macOS volume HUD uses to decide whether
               to show the now-playing indicator.

        Fallback path (osascript, 200–500 ms subprocess):

            The ``AudioDeviceList`` AppleScript suite that would let
            osascript query the running state directly isn't available
            on stock macOS, so the fallback is a best-effort check that
            looks for known audio-producing process names (Spotify,
            Safari, Chrome, etc.) in the foreground app list.  This is
            imperfect (a browser tab with paused YouTube still counts
            as "active"), but it's a reasonable heuristic when the
            in-process CoreAudio path isn't available.

        If neither path can determine activity, returns ``True`` (safe
        default — duck anyway).
        """
        if self._use_coreaudio:
            try:
                dev = self._get_default_output_device()
                if dev is None:
                    raise RuntimeError(
                        "could not resolve default output device"
                    )
                running = self._ca_is_device_running(dev)
                if running is None:
                    raise RuntimeError(
                        "kAudioDevicePropertyDeviceIsRunning query failed"
                    )
                return running
            except Exception as exc:
                log.debug(
                    "[VOLUME-MAC] CoreAudio is_speaker_active failed: %s — "
                    "falling back to osascript",
                    exc,
                )
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

    def _coreaudio_get_state(self) -> VolumeState | None:
        """Read volume + mute via CoreAudio.

        Returns ``None`` on any failure (caller falls back to osascript).
        Uses the HardwareService virtual-master selectors which operate
        on the default output device via ``kAudioHardwareServiceSystemObject``
        — so we don't need to resolve the device ID here.
        """
        try:
            vol = self._ca_get_volume()
            if vol is None:
                return None
            muted = self._ca_get_mute()
            if muted is None:
                muted = False
            return VolumeState(linear=vol, muted=muted)
        except Exception as exc:
            log.warning("[VOLUME-MAC] CoreAudio get_state failed: %s", exc)
            return None

    def _coreaudio_set(self, level: float, muted: bool | None) -> bool:
        """Set volume (and optionally mute) via CoreAudio.

        Returns ``False`` on any failure (caller falls back to osascript).
        Uses the HardwareService virtual-master selectors which operate
        on the default output device via ``kAudioHardwareServiceSystemObject``.
        """
        try:
            ok = self._ca_set_volume(level)
            if muted is not None:
                # Best-effort mute set — volume success is the
                # primary signal.  Caller can verify via get_state.
                self._ca_set_mute(muted)
            return ok
        except Exception as exc:
            log.warning("[VOLUME-MAC] CoreAudio set failed: %s", exc)
            return False

    def _get_default_output_device(self) -> int | None:
        """Resolve the default output device's ``AudioDeviceID`` via CoreAudio.

        Queries ``kAudioHardwarePropertyDefaultOutputDevice`` on
        ``kAudioObjectSystemObject`` (scope=Global, element=Master).
        Returns the ``AudioDeviceID`` (UInt32) or ``None`` on failure.

        The device ID is *not* cached — the default device can change
        at runtime (user plugs in headphones, etc.) and the query is
        a single in-process C call (~µs).
        """
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415 — stdlib, kept lazy to mirror existing style

            address = (
                ca.prop_default_output_device,
                ca.scope_global,
                ca.element_master,
            )
            device_id = ctypes.c_uint32(0)
            size = ctypes.c_uint32(ctypes.sizeof(device_id))
            status = ca.get_property(
                ca.system_object,
                address,
                0,  # inQualifierDataSize
                None,  # inQualifierData
                ctypes.byref(size),
                ctypes.byref(device_id),
            )
            if status != 0:
                log.debug(
                    "[VOLUME-MAC] kAudioHardwarePropertyDefaultOutputDevice "
                    "status=%d",
                    status,
                )
                return None
            self._default_device_id = device_id.value
            return device_id.value
        except Exception as exc:
            log.debug(
                "[VOLUME-MAC] _get_default_output_device failed: %s", exc
            )
            return None

    def _ca_is_device_running(self, dev: int) -> bool | None:
        """Query ``kAudioDevicePropertyDeviceIsRunning`` on the given device.

        Returns ``True`` if any IOProc is actively rendering audio on
        the device, ``False`` if idle, or ``None`` on error (caller
        falls back to osascript).

        Scope is ``kAudioObjectPropertyScopeGlobal`` and element is
        ``kAudioObjectPropertyElementMaster`` per Apple's
        ``AudioHardware.h`` documentation for this property.
        """
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415 — stdlib, kept lazy to mirror existing style

            address = (
                ca.prop_device_is_running,
                ca.scope_global,
                ca.element_master,
            )
            is_running = ctypes.c_uint32(0)
            size = ctypes.c_uint32(ctypes.sizeof(is_running))
            status = ca.get_property(
                dev,
                address,
                0,
                None,
                ctypes.byref(size),
                ctypes.byref(is_running),
            )
            if status != 0:
                log.debug(
                    "[VOLUME-MAC] kAudioDevicePropertyDeviceIsRunning "
                    "status=%d (dev=%d)",
                    status,
                    dev,
                )
                return None
            return is_running.value == 1
        except Exception as exc:
            log.debug(
                "[VOLUME-MAC] _ca_is_device_running failed: %s", exc
            )
            return None

    def _ca_get_volume(self) -> float | None:
        """Read the virtual master volume (Float32 in [0.0, 1.0]).

        Uses ``kAudioHardwareServiceDeviceProperty_VirtualMasterVolume``
        on ``kAudioHardwareServiceSystemObject`` (scope=Output,
        element=Master).  This operates on the *default* output device
        so we don't need to resolve the device ID.
        """
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415 — stdlib, kept lazy to mirror existing style

            address = (
                ca.prop_master_volume,
                ca.scope_output,
                ca.element_master,
            )
            volume = ctypes.c_float(0.0)
            size = ctypes.c_uint32(ctypes.sizeof(volume))
            status = ca.get_property(
                ca.hws_system_object,
                address,
                0,
                None,
                ctypes.byref(size),
                ctypes.byref(volume),
            )
            if status != 0:
                log.debug(
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterVolume "
                    "get status=%d",
                    status,
                )
                return None
            # Clamp to [0.0, 1.0] — the API guarantees this but we
            # defend against driver bugs.
            return max(0.0, min(1.0, float(volume.value)))
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_get_volume failed: %s", exc)
            return None

    def _ca_get_mute(self) -> bool | None:
        """Read the virtual master mute state (UInt32, 0 or 1).

        Uses ``kAudioHardwareServiceDeviceProperty_VirtualMasterMute``
        on ``kAudioHardwareServiceSystemObject`` (scope=Output,
        element=Master).
        """
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415 — stdlib, kept lazy to mirror existing style

            address = (
                ca.prop_master_mute,
                ca.scope_output,
                ca.element_master,
            )
            muted = ctypes.c_uint32(0)
            size = ctypes.c_uint32(ctypes.sizeof(muted))
            status = ca.get_property(
                ca.hws_system_object,
                address,
                0,
                None,
                ctypes.byref(size),
                ctypes.byref(muted),
            )
            if status != 0:
                log.debug(
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterMute "
                    "get status=%d",
                    status,
                )
                return None
            return muted.value == 1
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_get_mute failed: %s", exc)
            return None

    def _ca_set_volume(self, level: float) -> bool:
        """Set the virtual master volume (Float32 in [0.0, 1.0]).

        Uses ``AudioObjectSetPropertyData`` with
        ``kAudioHardwareServiceDeviceProperty_VirtualMasterVolume`` on
        ``kAudioHardwareServiceSystemObject``.
        """
        ca = self._ca
        if ca is None:
            return False
        try:
            import ctypes  # noqa: PLC0415 — stdlib, kept lazy to mirror existing style

            address = (
                ca.prop_master_volume,
                ca.scope_output,
                ca.element_master,
            )
            volume = ctypes.c_float(float(level))
            status = ca.set_property(
                ca.hws_system_object,
                address,
                0,
                None,
                ctypes.sizeof(volume),
                ctypes.byref(volume),
            )
            if status != 0:
                log.debug(
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterVolume "
                    "set status=%d",
                    status,
                )
                return False
            return True
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_set_volume failed: %s", exc)
            return False

    def _ca_set_mute(self, muted: bool) -> bool:
        """Set the virtual master mute state (UInt32, 0 or 1).

        Uses ``AudioObjectSetPropertyData`` with
        ``kAudioHardwareServiceDeviceProperty_VirtualMasterMute`` on
        ``kAudioHardwareServiceSystemObject``.
        """
        ca = self._ca
        if ca is None:
            return False
        try:
            import ctypes  # noqa: PLC0415 — stdlib, kept lazy to mirror existing style

            address = (
                ca.prop_master_mute,
                ca.scope_output,
                ca.element_master,
            )
            value = ctypes.c_uint32(1 if muted else 0)
            status = ca.set_property(
                ca.hws_system_object,
                address,
                0,
                None,
                ctypes.sizeof(value),
                ctypes.byref(value),
            )
            if status != 0:
                log.debug(
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterMute "
                    "set status=%d",
                    status,
                )
                return False
            return True
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_set_mute failed: %s", exc)
            return False

    # ── osascript fallback path ─────────────────────────────────────

    def _osascript_run(self, script: str, timeout: float = 2.0) -> str | None:
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

    def _osascript_get_state(self) -> VolumeState | None:
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

    def _osascript_set(self, level: float, muted: bool | None) -> bool:
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
        self._tool: str | None = None

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

    def _run(self, cmd: list[str], timeout: float = 2.0) -> str | None:
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

    def _pactl_get(self) -> VolumeState | None:
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

    def _pactl_set(self, level: float, muted: bool | None) -> bool:
        pct = int(level * 100)
        ok = self._run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"]) is not None
        if muted is not None:
            mute_val = "1" if muted else "0"
            self._run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", mute_val])
        return ok

    # ── wpctl (WirePlumber / PipeWire native) ───────────────────────

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

    # ── amixer (ALSA fallback) ──────────────────────────────────────

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
        ok = self._run(
            ["amixer", "-D", "default", "sset", "Master", f"{pct}%"]
        ) is not None
        if muted is not None:
            mute_val = "mute" if muted else "unmute"
            self._run(["amixer", "-D", "default", "sset", "Master", mute_val])
        return ok
