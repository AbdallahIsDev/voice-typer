"""Microphone enumeration helpers.

Phase 4.5 /  — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Contains:
  - :func:`_sd_dev_as_dict` — coerce a sounddevice device entry to ``dict``.
  - :func:`list_microphones` — enumerate available input devices.
  - :func:`find_microphone_by_name` — case-insensitive partial-name lookup.
  - :func:`find_microphone_by_id` — exact ID lookup.

Patch-path compatibility
------------------------
Tests patch ``list_microphones`` via
``monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", ...)``
and then call ``find_microphone_by_name`` / ``find_microphone_by_id``.
For the patch to take effect, those two helpers must look up
``list_microphones`` through the package namespace at call time — hence
``from voice_typer.server import server_platform as _pkg`` and the
``_pkg.list_microphones()`` reference below.

``_is_non_mic_device`` (used by ``list_microphones``) lives in
:mod:`.remote_session`; it is NOT patched by any test, so a direct import
is safe (and avoids the import-time circular dependency that
``_pkg._is_non_mic_device`` would create — :mod:`.remote_session` is
loaded before this module).

``inspect.getsource`` compatibility
-----------------------------------
All four functions are genuinely defined here, so
``inspect.getsource(list_microphones)`` etc. continue to read from this
file.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

# Patch-path bridge: route lookups of ``list_microphones`` through the
# package namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", ...)``
# keep affecting production code defined here (specifically the
# ``find_microphone_by_name`` / ``find_microphone_by_id`` callers).  The
# package ``__init__.py`` re-exports ``list_microphones`` from this
# module; we look it up at call time rather than binding at import time
# so the patch takes effect.
from voice_typer.server import server_platform as _pkg
from voice_typer.server._audio_constants import SILERO_VAD_SAMPLE_RATES

from .remote_session import _is_invalid_device_name, _is_non_mic_device

log = logging.getLogger(__name__)


# ─── module-level TTL cache for list_microphones() ─────────────────────
# ``list_microphones()`` invokes three PortAudio round-trips
# (``sd.query_devices(kind="input")``, ``sd.query_hostapis()``,
# ``sd.query_devices()``) — 50-200 ms latency per call on Windows/macOS.
# The production caller ``find_microphone_by_name`` /
# ``find_microphone_by_id`` (called by ``device_manager`` during
# device-restart-after-disconnect) re-enumerate all devices on every
# call, so a single recovery sequence can fetch the same PortAudio data
# 2-3 times. The cache holds the result for ``_LIST_MICS_CACHE_TTL_S``
# seconds; the OS device-change watcher invalidates it immediately via
# :func:`invalidate_microphone_list_cache` (called from
# ``MicrophoneDeviceWatcher._invoke_callback``).
#
# The cache tuple is ``(timestamp, mics_list, sd_module_identity)``. The
# ``sd_module_identity`` field is used to detect tests that swap
# ``sys.modules["sounddevice"]`` for a MagicMock — when the identity
# changes, the cache is treated as stale so a test that patches
# sounddevice to raise sees a fresh call (not cached data from a prior
# test that used the real sounddevice).
_LIST_MICS_CACHE_TTL_S: float = 5.0
_LIST_MICS_CACHE_LOCK = threading.Lock()
_LIST_MICS_CACHE: tuple[float, list[dict], Any] | None = None


def invalidate_microphone_list_cache() -> None:
    """Clear the module-level TTL cache used by :func:`list_microphones`.

    Called by :class:`MicrophoneDeviceWatcher` when the OS reports a
    device plug/unplug event so the next ``list_microphones()`` call
    re-queries PortAudio immediately rather than waiting for the 5 s
    TTL to expire. Safe to call from any thread.
    """
    global _LIST_MICS_CACHE
    with _LIST_MICS_CACHE_LOCK:
        _LIST_MICS_CACHE = None


def _sd_dev_as_dict(dev: Any) -> dict[str, Any] | None:
    """Coerce a sounddevice device entry to a ``dict``.

    ``sounddevice.query_devices()`` returns a ``DeviceList``
        (a ``tuple`` subclass) whose entries are dicts at runtime, but the
        package has no inline type annotations, so pyrefly treats the
        elements as ``tuple[Unknown, ...] | dict[Unknown, Unknown] | str``.
        Iterating and indexing through that union triggers a cascade of
        ``bad-index`` / ``missing-attribute`` errors.  This helper performs
        an explicit ``isinstance`` narrow so callers get a clean ``dict``.
    """
    if isinstance(dev, dict):
        return dev
    return None


# ─── Stable device identifiers ─────────────────────────────────────────
#
# PortAudio device indices are NOT stable across reboots / replugs /
# host-API changes (the OS re-enumerates devices in a different order).
# Persisting the index as the microphone id made the renderer's saved
# selection go stale: after restart the stored index pointed at a
# different device (or none), and the UI fell back to "Unknown".
#
# The id is instead built from the STABLE attributes PortAudio reports —
# host API name + device display name — with a ``#N`` disambiguator when
# two live input devices share both. Legacy configs that persisted a bare
# index string keep working via the index fallback in
# :func:`find_microphone_by_id` and ``DeviceManager._resolve_device``.


def _stable_device_id(host_api: str, name: str, seen: set[str]) -> str:
    """Build a stable per-enumeration id from *host_api* + *name*.

    Uniqueness within one enumeration is guaranteed by appending ``#2``,
    ``#3``, ... when a (host API, name) pair repeats (two identical USB
    mics plugged in at once). Stable across reboots for an UNCHANGED
    device set, with one caveat: for same-name twins the ``#N`` suffix
    follows PortAudio's enumeration order, which a replug may swap —
    a persisted ``"#2"`` id then addresses the OTHER physical twin.
    The base-id fallback in ``find_microphone_by_id`` still lands on
    *a* twin (best-effort for an inherently ambiguous situation); it
    never resolves to a different-NAMED device.
    """
    base = f"{host_api}|{name}"
    candidate = base
    n = 2
    while candidate in seen:
        candidate = f"{base}#{n}"
        n += 1
    seen.add(candidate)
    return candidate


def _resolve_legacy_compound_id(wanted: str) -> dict | None:
    """Resolve the pre-stable-id compound form ``"<index>|<name>[|<host api>]"``.

    The leading segment must be purely numeric — that discriminator keeps
    new-style stable ids (``"<host api>|<name>"``, whose host-API segment is
    never numeric) out of this parser. A live name match wins over the stale
    index (mirrors ``DeviceManager._resolve_device``); an empty name fragment
    skips straight to the index fallback because substring-matching ``""``
    would return the first enumerated device.
    """
    parts = wanted.split("|")
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    saved_name = parts[1].strip()
    if saved_name:
        match = _pkg.find_microphone_by_name(saved_name)
        if match is not None:
            return match
    try:
        legacy_index = int(parts[0])
    except ValueError:
        return None
    for mic in _pkg.list_microphones():
        if mic.get("index") == legacy_index:
            return mic
    return None


def _match_canonical_mic_by_name(mic_id: str) -> dict | None:
    """Last-resort id resolution: exact display-name match, nothing more.

    Persisted stable ids pin the host API that was canonical when they
    were saved; after host-API normalization that API may no longer be
    enumerated at all (``"MME|Microphone (Realtek(R) Audio)"`` with WASAPI
    now canonical). When every exact/base-id/legacy strategy above has
    failed, strip any ``#N`` suffix, take the segment after the FIRST
    ``|`` as the display name, and accept ONLY an unambiguous exact-name
    match among the live canonical devices.

    Names whose endpoint naming differs across host APIs do NOT match
    (e.g. WDM-KS ``"Microphone (Realtek HD Audio Mic input)"`` vs its
    WASAPI twin ``"Microphone (Realtek(R) Audio)"``) → ``None`` → system
    default + caller warning. Resolving to a *wrong* device would be
    worse than falling back to the default.
    """
    if "|" not in mic_id:
        return None
    base = mic_id.split("#", 1)[0]
    # Split ONCE: the display name is everything after the first "|".
    # Device names may themselves contain "|" — taking parts[1] of a
    # full split would resolve "WASAPI|A|B" against an unrelated device
    # literally named "A".
    name = base.split("|", 1)[1].strip()
    if not name:
        return None
    matches = [m for m in _pkg.list_microphones() if m.get("name") == name]
    return matches[0] if len(matches) == 1 else None


def resolve_mic_id_to_device_index(mic_id: str | int | None) -> int | None:
    """Resolve a persisted/IPC microphone id to a live PortAudio index.

    Accepts every historical id shape so callers (level monitor,
    microphone test) need no per-caller migration logic — all matching
    lives in :func:`find_microphone_by_id`, the single source of truth:

    - ``None`` → ``None`` (system default);
    - new-style stable ids (``"<host api>|<name>[#N]"``) → the live
      device whose generated id matches exactly;
    - legacy bare-index strings (``"5"``) → the live device currently
      enumerated at index 5 (same semantics as the pre-stable-id code);
    - legacy compound strings (``"<index>|<name>[|<host api>]"``) →
      name-based match first, then the saved index;
    - anything unresolvable → ``None`` (caller falls back to the system
      default rather than crashing).

    Returns the integer PortAudio index, or ``None`` when no live device
    matches (device unplugged / garbage id / system default requested).
    """
    if mic_id is None:
        return None
    # Normalize to str so legacy int-index configs satisfy
    # find_microphone_by_id's str contract (bare digits resolve via its
    # legacy-index fallback).
    mic = find_microphone_by_id(str(mic_id))
    if mic is None:
        return None
    try:
        return int(mic["index"])
    except (KeyError, TypeError, ValueError):
        return None


# ─── Canonical host-API selection ──────────────────────────────────────
#
# PortAudio enumerates every OS audio endpoint once PER HOST API, so a
# single physical microphone appears up to four times in
# ``sd.query_devices()`` (MME / DirectSound / WASAPI / WDM-KS on Windows).
# The OS audio UI shows exactly one view of each endpoint; MME truncates
# names at 31 chars and WDM-KS additionally exposes disabled endpoints.
# PortAudio's own multi-host-API proposal says clients should display
# devices from ONE host API at a time, and python-sounddevice has no
# cross-API unique id — so picking a canonical host API per platform is
# the strongest identity strategy available.


def _preferred_host_api_substring() -> str | None:
    """Return the host-API name fragment canonical for this platform.

    Resolved through :mod:`voice_typer.server.platform_utils` at call time
    so test monkeypatches of ``sys.platform`` propagate. ``None`` means no
    canonical filter is defined for the platform (keep all records).
    """
    from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

    if is_windows():
        # Matches the Windows Settings "Input" page (MMDevice endpoints)
        # with full, untruncated names.
        return "WASAPI"
    if is_macos():
        return "Core Audio"
    if is_linux():
        return "PulseAudio"
    return None


# Last logged (host_api, kept, dropped) summary so the info line below
# fires once per enumeration CHANGE instead of on every TTL expiry.
_LAST_CANON_SUMMARY: tuple[str, int, int] | None = None


def _list_microphones_uncached() -> list[dict]:
    """Return available input devices with stable identifiers.

    Each dict:
        {
            "id": str,          # stable id "<host api>|<name>[#N]"
            "index": int,       # sounddevice device index (unstable)
            "name": str,        # display name
            "host_api": str,    # host API name (e.g. "Windows WASAPI")
            "channels": int,    # max input channels
            "default": bool,    # True if system default input device
            "is_bluetooth": bool,  # AUDIO-BT: True if Bluetooth/HFP device
        }
    Returns empty list on failure.

    Canonical host-API normalization: PortAudio exposes each endpoint once
    per host API; the platform's preferred host API (Windows → WASAPI,
    macOS → Core Audio, Linux → PulseAudio) is kept when it yields any
    device, and every other host API's duplicate view of the same
    endpoints is dropped. When the preferred API yields nothing (broken
    PortAudio install, exotic setup) the unfiltered list is returned —
    never empty, never hiding all devices.

    This is the underlying PortAudio query; :func:`list_microphones`
    wraps it with a 5 s TTL cache.
    """
    try:
        import sounddevice as sd

        default_input_raw = sd.query_devices(kind="input")
        default_input = _sd_dev_as_dict(default_input_raw)
        default_index = default_input["index"] if default_input else -1
        # PERF-: batch the host-API name lookups. Pre-fix, each
        # input device triggered a separate ``sd.query_hostapis(idx)``
        # syscall. Querying all host APIs once and building an
        # ``idx → name`` dict turns N syscalls into one.
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
        for i, dev_raw in enumerate(sd.query_devices()):
            dev = _sd_dev_as_dict(dev_raw)
            if dev is None:
                continue
            if dev["max_input_channels"] <= 0:
                continue
            raw_name = dev.get("name", "")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            if _is_non_mic_device(name):
                continue
            # Placeholder endpoints ("Input ()", empty/whitespace names)
            # have no real device behind them — never offer them.
            if _is_invalid_device_name(name):
                continue
            host_api = host_api_names.get(dev.get("hostapi", 0), "")
            # Prefer the device's self-reported PortAudio index (the same
            # value ``sd.query_devices(kind='input')['index']`` carries,
            # which the default-device comparison below uses); fall back
            # to the enumeration position when a fake/partial entry omits
            # or corrupts it.
            try:
                portaudio_index = int(dev.get("index", i))
            except (TypeError, ValueError):
                portaudio_index = i
            # AUDIO-BT: detect Bluetooth devices by name or sample rate.
            # Bluetooth HFP (Hands-Free Profile) devices typically have
            # "Bluetooth", "HFP", or "Hands-Free" in the device name,
            # and operate at 8 or 16 kHz sample rate.
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
                    "channels": dev["max_input_channels"],
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
    """Reduce the per-host-API duplicate views to the platform's canonical one.

    When the preferred host API owns at least one enumerated input device,
    only its records are returned and the ``default`` flag is recomputed
    from that host API's own ``default_input_device`` (the PortAudio global
    default can live on a non-canonical host API — e.g. an MME record — so
    comparing against it would leave the canonical list without any
    default). Two same-name records within the canonical API stay distinct
    via their ``#N`` ids; exactly one of them can carry the default index.
    Falls back to the unfiltered list whenever the preferred API is empty,
    keeping the existing global-default flags untouched. Edge: if the
    preferred API is active but reports no default input device
    (``default_input_device == -1``), the recomputation is skipped and the
    canonical list may carry NO default flag — the UI degrades to no
    "Default" badge rather than flagging a wrong device.
    """
    global _LAST_CANON_SUMMARY

    preferred = _preferred_host_api_substring()
    if preferred is None:
        return devices

    preferred_lower = preferred.lower()
    subset = [d for d in devices if preferred_lower in d["host_api"].lower()]
    if not subset:
        # Graceful degradation: a preferred host API with no devices must
        # never empty the list.
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

    Wraps :func:`_list_microphones_uncached` with a 5 s module-level TTL
    cache so repeated calls within a single device-restart sequence
    (e.g. ``find_microphone_by_name`` → ``find_microphone_by_id``)
    don't re-query PortAudio 2-3 times in 50-200 ms each. The cache is
    invalidated immediately by
    :func:`invalidate_microphone_list_cache` (called from
    ``MicrophoneDeviceWatcher._invoke_callback`` on OS device-change
    events) so hot-plug propagation latency is unaffected.

    Returns a fresh shallow-copied list on every call — callers may
    mutate the outer list without corrupting the cache. Inner dicts
    are shared with the cache (callers must not mutate them in place;
    the production callers only read).
    """
    global _LIST_MICS_CACHE
    now = time.monotonic()
    sd_mod = sys.modules.get("sounddevice")
    with _LIST_MICS_CACHE_LOCK:
        cache = _LIST_MICS_CACHE
        if cache is not None:
            cache_ts, cache_mics, cache_sd = cache
            # Identity check on the sounddevice module detects test
            # patches that swap ``sys.modules["sounddevice"]`` for a
            # MagicMock — when the identity differs, the cache is
            # treated as stale so the patched module is actually used.
            if (now - cache_ts) < _LIST_MICS_CACHE_TTL_S and cache_sd is sd_mod:
                return list(cache_mics)  # defensive shallow copy

    result = _list_microphones_uncached()

    # Populate the cache. Re-read sd_mod under the lock in case a
    # concurrent thread changed sys.modules["sounddevice"] between the
    # cache-miss check above and now.
    with _LIST_MICS_CACHE_LOCK:
        sd_mod = sys.modules.get("sounddevice")
        _LIST_MICS_CACHE = (now, list(result), sd_mod)
    return result


def find_microphone_by_name(partial_name: str) -> dict | None:
    """Find a microphone whose name contains *partial_name* (case-insensitive)."""
    lower = partial_name.lower()
    for mic in _pkg.list_microphones():
        if lower in mic["name"].lower():
            return mic
    return None


def find_microphone_by_id(mic_id: str) -> dict | None:
    """Find a microphone by its stable ID (``"<host api>|<name>[#N]"``).

    Backward compatibility — configs persisted before stable ids existed
    store two older shapes, both resolved here (see
    :func:`_resolve_legacy_compound_id` for the compound parser):

    - bare PortAudio index string (``"5"``) → whatever device is
      enumerated at that index today — identical to the pre-stable-id
      behavior;
    - compound string (``"<index>|<name>[|<host api>]"``) → name-based
      match first, then the saved index;
    - disambiguated stable id whose twin set changed
      (``"MME|USB Mic#2"``, exact match gone because one identical unit
      was unplugged) → the device carrying the base id ``"MME|USB Mic"``
      — best-effort recovery instead of silently dropping to default;
    - stable id whose host API is no longer enumerated (``"MME|Mic"`` or
      ``"Windows WDM-KS|Mic"`` after host-API canonicalization) →
      unambiguous exact-name match via
      :func:`_match_canonical_mic_by_name`; endpoint names that differ
      across host APIs stay unresolved (system default) rather than
      guessing.

    In all legacy/recovery cases the returned dict carries the NEW
    stable id so the caller can persist it going forward.
    """
    wanted = str(mic_id)
    for mic in _pkg.list_microphones():
        if mic["id"] == wanted:
            return mic
    if wanted.isdigit():
        try:
            legacy_index = int(wanted)
        except ValueError:
            return None
        for mic in _pkg.list_microphones():
            if mic.get("index") == legacy_index:
                return mic
    if "#" in wanted:
        base = wanted.split("#", 1)[0]
        for mic in _pkg.list_microphones():
            if mic["id"] == base:
                return mic
    resolved = _resolve_legacy_compound_id(wanted)
    if resolved is not None:
        return resolved
    return _match_canonical_mic_by_name(wanted)
