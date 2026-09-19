"""macOS volume backend. CoreAudio (pyobjc) with osascript fallback."""

from __future__ import annotations

import logging
import subprocess
import sys
from types import SimpleNamespace

from voice_typer.server.platform_utils import is_macos
from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState

log = logging.getLogger(__name__)

# number of consecutive backend failures before a WARNING is
_BACKEND_ERROR_WARN_THRESHOLD = 3


def _try_import_coreaudio() -> SimpleNamespace:
    """Lazy import of pyobjc CoreAudio symbols needed by ``MacVolumeBackend``.

    Returns a ``SimpleNamespace`` exposing the symbols needed by the
    """
    if not is_macos():
        raise ImportError(
            "MacVolumeBackend's CoreAudio path is only available on macOS "
            f"(current platform: {sys.platform}). The osascript fallback "
            "will be used instead."
        )

    try:
        from CoreAudio import (  # noqa: PLC0415, lazy import is the point
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

        # (pyrefly): build the namespace inside the try-block so
        return SimpleNamespace(
            get_property=AudioObjectGetPropertyData,
            set_property=AudioObjectSetPropertyData,
            # Per-device selectors
            prop_device_is_running=kAudioDevicePropertyDeviceIsRunning,
            # System-object selectors
            prop_default_output_device=kAudioHardwarePropertyDefaultOutputDevice,
            # HardwareService (system-wide virtual master) selectors —
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
    except ImportError as exc:
        raise ImportError(
            "pyobjc-framework-CoreAudio is required for the CoreAudio "
            "volume backend. Install with: pip install "
            "pyobjc-framework-CoreAudio. The osascript fallback will "
            "be used instead."
        ) from exc


class MacVolumeBackend(VolumeBackend):
    """macOS volume control."""

    def __init__(self) -> None:
        self._use_coreaudio = False
        self._default_device_id: int | None = None
        # pyobjc CoreAudio symbols, loaded lazily in ``initialize()`` so
        self._ca: SimpleNamespace | None = None
        # consecutive-error counter for ``_osascript_run`` and
        self._consecutive_errors: int = 0

    @property
    def name(self) -> str:
        return "CoreAudio (pyobjc)" if self._use_coreaudio else "osascript"

    @property
    def supports_per_session(self) -> bool:
        return False

    @property
    def _set_linear_is_subprocess(self) -> bool:
        """``True`` only on the osascript fallback path."""
        return not self._use_coreaudio

    @property
    def recommended_poll_interval_ms(self) -> int:
        """100ms when CoreAudio is active (in-process, <1ms per call),"""
        return 100 if self._use_coreaudio else 500

    def initialize(self) -> bool:
        """Initialize the backend."""
        # reset the error counter on a fresh initialize() attempt.
        self._consecutive_errors = 0
        try:
            self._ca = _try_import_coreaudio()
            self._use_coreaudio = True
            log.info(
                "[VOLUME-MAC] CoreAudio (pyobjc) backend ready, "
                "is_speaker_active will use kAudioDevicePropertyDeviceIsRunning"
            )
        except ImportError as exc:
            self._ca = None
            self._use_coreaudio = False
            log.info(
                "[VOLUME-MAC] osascript backend ready (pyobjc unavailable: %s)",
                exc,
            )
        return True  # osascript is always available on macOS

    # See ``WinVolumeBackend._record_error`` / ``_record_success`` for

    def _record_error(self, context: str, exc: BaseException) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors % _BACKEND_ERROR_WARN_THRESHOLD == 0:
            log.warning(
                "[VOLUME-MAC] %s failed %d times in a row (last error: %s) "
                "— safe-default returned, duck state preserved",
                context,
                self._consecutive_errors,
                exc,
            )

    def _record_success(self) -> None:
        if self._consecutive_errors:
            self._consecutive_errors = 0

    def get_state(self) -> VolumeState | None:
        if self._use_coreaudio:
            state = self._coreaudio_get_state()
            if state is not None:
                return state
            # CoreAudio failed, fall through to osascript so the ducker
            log.debug("[VOLUME-MAC] CoreAudio get_state failed, using osascript")
        return self._osascript_get_state()

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._use_coreaudio:
            if self._coreaudio_set(level, muted):
                return True
            log.debug("[VOLUME-MAC] CoreAudio set failed, using osascript")
        return self._osascript_set(level, muted)

    def is_speaker_active(self) -> bool:
        """Return ``True`` if audio is currently playing on the default output."""
        if self._use_coreaudio:
            try:
                dev = self._get_default_output_device()
                if dev is None:
                    raise RuntimeError("could not resolve default output device")
                running = self._ca_is_device_running(dev)
                if running is None:
                    raise RuntimeError("kAudioDevicePropertyDeviceIsRunning query failed")
                return running
            except Exception as exc:
                log.debug(
                    "[VOLUME-MAC] CoreAudio is_speaker_active failed: %s, "
                    "returning True (duck anyway) since smart-duck is disabled "
                    "on the osascript path",
                    exc,
                )
                # Fall through to the safe-default return below.
        return True

    def _coreaudio_get_state(self) -> VolumeState | None:
        """Read volume + mute via CoreAudio.

        Returns ``None`` on any failure (caller falls back to osascript).
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
        """
        try:
            ok = self._ca_set_volume(level)
            if muted is not None:
                # Best-effort mute set, volume success is the
                self._ca_set_mute(muted)
            return ok
        except Exception as exc:
            log.warning("[VOLUME-MAC] CoreAudio set failed: %s", exc)
            return False

    def _get_default_output_device(self) -> int | None:
        """Resolve the default output device's ``AudioDeviceID`` via CoreAudio."""
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

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
                    "[VOLUME-MAC] kAudioHardwarePropertyDefaultOutputDevice status=%d",
                    status,
                )
                return None
            self._default_device_id = device_id.value
            return device_id.value
        except Exception as exc:
            log.debug("[VOLUME-MAC] _get_default_output_device failed: %s", exc)
            return None

    def _ca_is_device_running(self, dev: int) -> bool | None:
        """Query ``kAudioDevicePropertyDeviceIsRunning`` on the given device.

        Returns ``True`` if any IOProc is actively rendering audio on
        """
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

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
                    "[VOLUME-MAC] kAudioDevicePropertyDeviceIsRunning status=%d (dev=%d)",
                    status,
                    dev,
                )
                return None
            return is_running.value == 1
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_is_device_running failed: %s", exc)
            return None

    def _ca_get_volume(self) -> float | None:
        """Read the virtual master volume (Float32 in [0.0, 1.0])."""
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterVolume get status=%d",
                    status,
                )
                return None
            # Clamp to [0.0, 1.0], the API guarantees this but we
            return max(0.0, min(1.0, float(volume.value)))
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_get_volume failed: %s", exc)
            return None

    def _ca_get_mute(self) -> bool | None:
        """Read the virtual master mute state (UInt32, 0 or 1)."""
        ca = self._ca
        if ca is None:
            return None
        try:
            import ctypes  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterMute get status=%d",
                    status,
                )
                return None
            return muted.value == 1
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_get_mute failed: %s", exc)
            return None

    def _ca_set_volume(self, level: float) -> bool:
        """Set the virtual master volume (Float32 in [0.0, 1.0])."""
        ca = self._ca
        if ca is None:
            return False
        try:
            import ctypes  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterVolume set status=%d",
                    status,
                )
                return False
            return True
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_set_volume failed: %s", exc)
            return False

    def _ca_set_mute(self, muted: bool) -> bool:
        """Set the virtual master mute state (UInt32, 0 or 1)."""
        ca = self._ca
        if ca is None:
            return False
        try:
            import ctypes  # noqa: PLC0415, stdlib, kept lazy to mirror existing style

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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterMute set status=%d",
                    status,
                )
                return False
            return True
        except Exception as exc:
            log.debug("[VOLUME-MAC] _ca_set_mute failed: %s", exc)
            return False

    def _osascript_run(self, script: str, timeout: float = 2.0) -> str | None:
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                log.debug("[VOLUME-MAC] osascript error: %s", result.stderr.strip())
                # track non-zero exit as an error (osascript ran
                self._record_error(
                    "_osascript_run",
                    RuntimeError(f"osascript exit {result.returncode}: {result.stderr.strip()}"),
                )
                return None
            # success, reset the counter.
            self._record_success()
            return result.stdout.strip()
        except Exception as exc:
            log.debug("[VOLUME-MAC] osascript failed: %s", exc)
            # surface a WARNING after N consecutive failures so a
            self._record_error("_osascript_run", exc)
            return None

    def _osascript_get_state(self) -> VolumeState | None:
        vol_str = self._osascript_run("output volume of (get volume settings)")
        if vol_str is None:
            # ``_osascript_run`` already recorded the error, don't
            return None
        try:
            vol = int(vol_str) / 100.0
        except ValueError as exc:
            log.debug(
                "[VOLUME-MAC] _osascript_get_state parse failed: %s (vol_str=%r)",
                exc,
                vol_str,
            )
            # parsing failure is a distinct error from a
            self._record_error("_osascript_get_state", exc)
            return None
        mute_str = self._osascript_run("output muted of (get volume settings)")
        muted = mute_str is not None and mute_str.lower() == "true"
        # only reset the counter if BOTH queries succeeded.  If
        if mute_str is not None:
            self._record_success()
        return VolumeState(linear=max(0.0, min(1.0, vol)), muted=muted)

    def _osascript_set(self, level: float, muted: bool | None) -> bool:
        pct = int(level * 100)
        ok = self._osascript_run(f"set volume output volume {pct}") is not None
        if muted is not None:
            mute_str = "true" if muted else "false"
            self._osascript_run(f"set volume output muted {mute_str}")
        return ok
