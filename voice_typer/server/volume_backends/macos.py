"""macOS volume backend — CoreAudio (pyobjc) with osascript fallback.

Extracted from the original ``voice_typer/server/volume_backends.py``
monolith per PVT-24.  See ``voice_typer/server/volume_backends/__init__.py``
for the package-level docstring and re-exports.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from types import SimpleNamespace

from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState

log = logging.getLogger(__name__)

# UE-25: number of consecutive backend failures before a WARNING is
# surfaced.  See ``voice_typer/server/volume_backends/windows.py`` for
# the full rationale.  macOS's ``_osascript_run`` and
# ``_osascript_get_state`` swallow errors and return safe defaults
# (``None``) at DEBUG, so a persistently broken osascript path (e.g.
# revoked AppleScript permission on macOS 13+) would degrade ducking
# to a silent no-op with no log breadcrumb.  The counter surfaces the
# failure after ``_BACKEND_ERROR_WARN_THRESHOLD`` consecutive errors.
_BACKEND_ERROR_WARN_THRESHOLD = 3


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

        # RW-6 (pyrefly): build the namespace inside the try-block so
        # the imported names are obviously bound. Pyrefly 1.x does not
        # propagate "the except branch always raises" into "the names
        # from the import are bound on the success path", so building
        # the namespace after the try/except triggered 11 `unbound-name`
        # false positives. Keeping the construction here (where the
        # names are bound by the import) preserves the runtime semantics
        # (ImportError is still re-raised by the except clause).
        return SimpleNamespace(
            get_property=AudioObjectGetPropertyData,
            set_property=AudioObjectSetPropertyData,
            # Per-device selectors
            prop_device_is_running=kAudioDevicePropertyDeviceIsRunning,
            # System-object selectors
            prop_default_output_device=kAudioHardwarePropertyDefaultOutputDevice,
            # HardwareService (system-wide virtual master) selectors —
            # these operate on the *default* output device via the
            # system object, so we don't need to track the device ID
            # for volume/mute.
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
        # UE-25: consecutive-error counter for ``_osascript_run`` and
        # ``_osascript_get_state`` (the only error-tracked methods on
        # this backend per UE-25 — the CoreAudio path already logs at
        # WARNING on failure and falls through to osascript, so its
        # errors are tracked via the osascript counter).  See
        # ``WinVolumeBackend._consecutive_errors`` for the full
        # rationale.  Initialized here (and reset in ``initialize``)
        # so the methods are callable before ``initialize`` without
        # ``AttributeError``.
        self._consecutive_errors: int = 0

    @property
    def name(self) -> str:
        return "CoreAudio (pyobjc)" if self._use_coreaudio else "osascript"

    @property
    def supports_per_session(self) -> bool:
        return False

    @property
    def _set_linear_is_subprocess(self) -> bool:
        """``True`` only on the osascript fallback path.

        When CoreAudio (pyobjc) is active, ``set_linear`` is an
        in-process C call (<1 ms) and the multi-step ``fade_to`` ramp
        is smooth.  When pyobjc is unavailable and we fall back to
        ``osascript``, each ``set_linear`` spawns an ``osascript``
        subprocess (200–500 ms) — ``fade_to`` collapses to a single
        call to avoid 10× subprocess overhead (2–5 s of audible
        stepping).
        """
        return not self._use_coreaudio

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
        # UE-25: reset the error counter on a fresh initialize() attempt.
        self._consecutive_errors = 0
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
                "[VOLUME-MAC] osascript backend ready (pyobjc unavailable: %s)",
                exc,
            )
        return True  # osascript is always available on macOS

    # ── UE-25 error-tracking helpers ──────────────────────────────────
    # See ``WinVolumeBackend._record_error`` / ``_record_success`` for
    # the full rationale.  The counter is shared across this backend's
    # error-tracked methods (``_osascript_run`` and
    # ``_osascript_get_state`` per UE-25 scope).  To avoid
    # double-counting, ``_osascript_get_state`` only records errors for
    # parsing failures (``ValueError``) — subprocess failures are
    # already recorded by ``_osascript_run``.

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
            # CoreAudio failed — fall through to osascript so the ducker
            # never silently skips a save/restore cycle.
            log.debug("[VOLUME-MAC] CoreAudio get_state failed — using osascript")
        return self._osascript_get_state()

    def set_linear(self, level: float, muted: bool | None = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._use_coreaudio:
            if self._coreaudio_set(level, muted):
                return True
            log.debug("[VOLUME-MAC] CoreAudio set failed — using osascript")
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

        osascript fallback: returns ``True`` unconditionally.  Smart-duck
        is disabled for the osascript backend by
        :meth:`VolumeDucker.initialize` (the osascript
        ``is_speaker_active`` heuristic was dead code — a 200–500 ms
        per-call subprocess poll that was never invoked because the
        ducker short-circuits smart-duck on osascript backends).  The
        previous heuristic (matching foreground process names like
        "Spotify" / "Safari" / "Chrome") was both expensive AND
        unreliable (a paused YouTube tab still counted as "active"),
        so removing it is strictly an improvement.  The default
        ``True`` (duck anyway) preserves the safe behaviour the
        ducker relies on when smart-duck is disabled.

        If neither path can determine activity, returns ``True`` (safe
        default — duck anyway).
        """
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
                    "[VOLUME-MAC] CoreAudio is_speaker_active failed: %s — "
                    "returning True (duck anyway) since smart-duck is disabled "
                    "on the osascript path",
                    exc,
                )
                # Fall through to the safe-default return below.
        # osascript fallback: no cheap way to query speaker activity.
        # Smart-duck is disabled on this path (see VolumeDucker.initialize),
        # so the return value only matters as a safe default.  ``True``
        # means "duck anyway" — the conservative choice that never
        # silently skips a needed duck.
        return True

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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterVolume get status=%d",
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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterMute get status=%d",
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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterVolume set status=%d",
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
                    "[VOLUME-MAC] kAudioHardwareServiceDeviceProperty_VirtualMasterMute set status=%d",
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
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                log.debug("[VOLUME-MAC] osascript error: %s", result.stderr.strip())
                # UE-25: track non-zero exit as an error (osascript ran
                # but returned an error — e.g. revoked AppleScript
                # permission on macOS 13+).  The safe-default ``None``
                # return is preserved.
                self._record_error(
                    "_osascript_run",
                    RuntimeError(f"osascript exit {result.returncode}: {result.stderr.strip()}"),
                )
                return None
            # UE-25: success — reset the counter.
            self._record_success()
            return result.stdout.strip()
        except Exception as exc:
            log.debug("[VOLUME-MAC] osascript failed: %s", exc)
            # UE-25: surface a WARNING after N consecutive failures so a
            # persistently broken osascript path doesn't degrade ducking
            # to a silent no-op.  The safe-default ``None`` return is
            # preserved.
            self._record_error("_osascript_run", exc)
            return None

    def _osascript_get_state(self) -> VolumeState | None:
        vol_str = self._osascript_run("output volume of (get volume settings)")
        if vol_str is None:
            # ``_osascript_run`` already recorded the error — don't
            # double-count.
            return None
        try:
            vol = int(vol_str) / 100.0
        except ValueError as exc:
            log.debug(
                "[VOLUME-MAC] _osascript_get_state parse failed: %s (vol_str=%r)",
                exc,
                vol_str,
            )
            # UE-25: parsing failure is a distinct error from a
            # subprocess failure (tracked in ``_osascript_run``).
            self._record_error("_osascript_get_state", exc)
            return None
        mute_str = self._osascript_run("output muted of (get volume settings)")
        muted = mute_str is not None and mute_str.lower() == "true"
        # UE-25: only reset the counter if BOTH queries succeeded.  If
        # the mute query failed, ``_osascript_run`` already recorded the
        # error — don't double-count or reset.
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
