"""Microphone enumeration helpers."""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES

from .remote_session import _is_invalid_device_name, _is_non_mic_device

log = logging.getLogger(__name__)


# ``list_microphones()`` invokes three PortAudio round-trips
_LIST_MICS_CACHE_TTL_S: float = 5.0
_LIST_MICS_CACHE_LOCK = threading.Lock()
_LIST_MICS_CACHE: tuple[float, list[dict], Any] | None = None


def invalidate_microphone_list_cache() -> None:
    """Clear the module-level TTL cache used by :func:`list_microphones`."""
    global _LIST_MICS_CACHE
    with _LIST_MICS_CACHE_LOCK:
        _LIST_MICS_CACHE = None


def _sd_dev_as_dict(dev: Any) -> dict[str, Any] | None:
    """package has no inline type annotations, so pyrefly treats the"""
    if isinstance(dev, dict):
        return dev
    return None


def iter_filtered_input_devices(
    devices_raw: Any,
) -> list[tuple[int, dict[str, Any], str]]:
    """Walk a PortAudio device list, returning valid input devices.

    Returns a list of ``(enumerate_index, device_dict, stripped_name)``
    """
    result: list[tuple[int, dict[str, Any], str]] = []
    for i, dev_raw in enumerate(devices_raw):
        dev = _sd_dev_as_dict(dev_raw)
        if dev is None:
            continue
        try:
            max_ch_raw = dev.get("max_input_channels", 0)
            max_ch = int(max_ch_raw) if max_ch_raw is not None else 0
        except (TypeError, ValueError):
            max_ch = 0
        if max_ch <= 0:
            continue
        raw_name = dev.get("name", "")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if _is_non_mic_device(name) or _is_invalid_device_name(name):
            continue
        result.append((i, dev, name))
    return result


# PortAudio device indices are NOT stable across reboots / replugs /


def _stable_device_id(host_api: str, name: str, seen: set[str]) -> str:
    """Build a stable per-enumeration id from *host_api* + *name*."""
    base = f"{host_api}|{name}"
    candidate = base
    n = 2
    while candidate in seen:
        candidate = f"{base}#{n}"
        n += 1
    seen.add(candidate)
    return candidate


def _resolve_legacy_compound_id(wanted: str) -> dict | None:
    """Resolve the pre-stable-id compound form ``"<index>|<name>[|<host api>]"``."""
    parts = wanted.split("|")
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    saved_name = parts[1].strip()
    if saved_name:
        match = find_microphone_by_name(saved_name)
        if match is not None:
            return match
    try:
        legacy_index = int(parts[0])
    except ValueError:
        return None
    for mic in list_microphones():
        if mic.get("index") == legacy_index:
            return mic
    return None


def _match_canonical_mic_by_name(mic_id: str) -> dict | None:
    """Last-resort id resolution: exact display-name match, nothing more."""
    if "|" not in mic_id:
        return None
    base = mic_id.split("#", 1)[0]
    # Split ONCE: the display name is everything after the first "|".
    name = base.split("|", 1)[1].strip()
    if not name:
        return None
    matches = [m for m in list_microphones() if m.get("name") == name]
    return matches[0] if len(matches) == 1 else None


def resolve_mic_id_to_device_index(mic_id: str | int | None) -> int | None:
    """Resolve a persisted/IPC microphone id to a live PortAudio index.

    Returns the integer PortAudio index, or ``None`` when no live device
    """
    if mic_id is None:
        return None
    # Normalize to str so legacy int-index configs satisfy
    mic = find_microphone_by_id(str(mic_id))
    if mic is None:
        return None
    try:
        return int(mic["index"])
    except (KeyError, TypeError, ValueError):
        return None


# PortAudio enumerates every OS audio endpoint once PER HOST API, so a


def _preferred_host_api_substring() -> str | None:
    """Return the host-API name fragment canonical for this platform."""
    from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

    if is_windows():
        # Matches the Windows Settings "Input" page (MMDevice endpoints)
        return "WASAPI"
    if is_macos():
        return "Core Audio"
    if is_linux():
        return "PulseAudio"
    return None


# Last logged (host_api, kept, dropped) summary so the info line below
_LAST_CANON_SUMMARY: tuple[str, int, int] | None = None


def _list_microphones_uncached() -> list[dict]:
    """Return available input devices with stable identifiers.

    Returns empty list on failure.
    """
    try:
        import sounddevice as sd

        default_input_raw = sd.query_devices(kind="input")
        default_input = _sd_dev_as_dict(default_input_raw)
        default_index = default_input["index"] if default_input else -1
        # PERF-: batch the host-API name lookups. Pre-fix, each
        host_api_names: dict[int, str] = {}
        host_api_defaults: dict[int, int] = {}
        try:
            for hai, hapi_raw in enumerate(sd.query_hostapis()):
                hapi = _sd_dev_as_dict(hapi_raw)
                if hapi is not None:
                    host_api_names[hai] = hapi.get("name", "")
                    try:
                        host_api_defaults[hai] = int(hapi.get("default_input_device", -1))
                    except (TypeError, ValueError):
                        host_api_defaults[hai] = -1
        except Exception:
            log.debug("[PLATFORM] host API enumeration failed", exc_info=True)
        devices = []
        seen_ids: set[str] = set()
        for i, dev, name in iter_filtered_input_devices(sd.query_devices()):
            host_api = host_api_names.get(dev.get("hostapi", 0), "")
            # Prefer the device's self-reported PortAudio index (the same
            try:
                portaudio_index = int(dev.get("index", i))
            except (TypeError, ValueError):
                portaudio_index = i
            # AUDIO-BT: detect Bluetooth devices by name or sample rate.
            dev_name_lower = name.lower()
            is_bluetooth = (
                any(kw in dev_name_lower for kw in ("bluetooth", "hfp", "hands-free"))
                or dev.get("default_samplerate", 0) in SILERO_VAD_SAMPLE_RATES
            )
            devices.append(
                {
                    "id": _stable_device_id(host_api, name, seen_ids),
                    "index": portaudio_index,
                    "name": name,
                    "host_api": host_api,
                    "channels": dev.get("max_input_channels", 0),
                    "default": portaudio_index == default_index,
                    "is_bluetooth": is_bluetooth,
                }
            )
            if is_bluetooth:
                log.warning(
                    "[PLATFORM] Bluetooth/HFP device detected: %s "
                    "(sample_rate=%s). Audio quality may be limited. "
                    "Consider disabling the hands-free telephony profile "
                    "in Bluetooth settings for better quality.",
                    name,
                    dev.get("default_samplerate", "?"),
                )
        return _canonicalize_host_apis(devices, host_api_names, host_api_defaults)
    except Exception:
        log.debug("Could not enumerate microphones", exc_info=True)
        return []


def _canonicalize_host_apis(
    devices: list[dict],
    host_api_names: dict[int, str],
    host_api_defaults: dict[int, int],
) -> list[dict]:
    """Reduce the per-host-API duplicate views to the platform's canonical one."""
    global _LAST_CANON_SUMMARY

    preferred = _preferred_host_api_substring()
    if preferred is None:
        return devices

    preferred_lower = preferred.lower()
    subset = [d for d in devices if preferred_lower in d["host_api"].lower()]
    if not subset:
        # Graceful degradation: a preferred host API with no devices must
        return devices

    preferred_default = -1
    for hai, api_name in host_api_names.items():
        if preferred_lower in api_name.lower():
            candidate = host_api_defaults.get(hai, -1)
            if candidate >= 0:
                preferred_default = candidate
                break
    if preferred_default >= 0:
        for dev in subset:
            dev["default"] = dev["index"] == preferred_default

    summary = (preferred, len(subset), len(devices) - len(subset))
    if summary != _LAST_CANON_SUMMARY:
        log.info(
            "[PLATFORM] Canonical %s microphones: kept %d, dropped %d duplicate host-API records",
            preferred,
            len(subset),
            len(devices) - len(subset),
        )
        _LAST_CANON_SUMMARY = summary
    return subset


def list_microphones() -> list[dict]:
    """Return available input devices with stable identifiers.

    Returns a fresh shallow-copied list on every call, callers may
    """
    global _LIST_MICS_CACHE
    now = time.monotonic()
    sd_mod = sys.modules.get("sounddevice")
    with _LIST_MICS_CACHE_LOCK:
        cache = _LIST_MICS_CACHE
        if cache is not None:
            cache_ts, cache_mics, cache_sd = cache
            # Identity check on the sounddevice module detects test
            if (now - cache_ts) < _LIST_MICS_CACHE_TTL_S and cache_sd is sd_mod:
                return list(cache_mics)  # defensive shallow copy

    result = _list_microphones_uncached()

    # Populate the cache. Re-read sd_mod under the lock in case a
    with _LIST_MICS_CACHE_LOCK:
        sd_mod = sys.modules.get("sounddevice")
        _LIST_MICS_CACHE = (now, list(result), sd_mod)
    return result


def get_canonical_default_index() -> int | None:
    """Return the PortAudio index of the canonical default input device."""
    try:
        mics = list_microphones()
    except Exception:
        return None
    for mic in mics:
        try:
            if mic.get("default"):
                return int(mic["index"])
        except (KeyError, TypeError, ValueError):
            continue
    try:
        import sounddevice as sd

        raw = sd.query_devices(kind="input")
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    raw_name = raw.get("name", "")
    raw_name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not raw_name:
        return None
    exact = [m for m in mics if m.get("name") == raw_name]
    if len(exact) == 1:
        try:
            return int(exact[0]["index"])
        except (KeyError, TypeError, ValueError):
            return None
    if len(exact) > 1:
        return None
    prefixed = [m for m in mics if isinstance(m.get("name"), str) and m["name"].startswith(raw_name)]
    if len(prefixed) == 1 and len(raw_name) >= 31:
        try:
            return int(prefixed[0]["index"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def find_microphone_by_name(partial_name: str) -> dict | None:
    """Find a microphone whose name contains *partial_name* (case-insensitive)."""
    lower = partial_name.lower()
    for mic in list_microphones():
        if lower in mic["name"].lower():
            return mic
    return None


def find_microphone_by_id(mic_id: str) -> dict | None:
    """Find a microphone by its stable ID (``"<host api>|<name>[#N]"``)."""
    wanted = str(mic_id)
    for mic in list_microphones():
        if mic["id"] == wanted:
            return mic
    if wanted.isdigit():
        try:
            legacy_index = int(wanted)
        except ValueError:
            return None
        for mic in list_microphones():
            if mic.get("index") == legacy_index:
                return mic
    if "#" in wanted:
        base = wanted.split("#", 1)[0]
        for mic in list_microphones():
            if mic["id"] == base:
                return mic
    resolved = _resolve_legacy_compound_id(wanted)
    if resolved is not None:
        return resolved
    return _match_canonical_mic_by_name(wanted)
