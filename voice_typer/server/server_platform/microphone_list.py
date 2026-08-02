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

from .remote_session import _is_non_mic_device

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


def _list_microphones_uncached() -> list[dict]:
    """Return available input devices with stable identifiers.

    Each dict:
        {
            "id": str,          # stable identifier (device index as string)
            "index": int,       # sounddevice device index
            "name": str,        # display name
            "host_api": str,    # host API name (e.g. "Windows WASAPI")
            "channels": int,    # max input channels
            "default": bool,    # True if system default input device
            "is_bluetooth": bool,  # AUDIO-BT: True if Bluetooth/HFP device
        }
    Returns empty list on failure.

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
        try:
            for hai, hapi_raw in enumerate(sd.query_hostapis()):
                hapi = _sd_dev_as_dict(hapi_raw)
                if hapi is not None:
                    host_api_names[hai] = hapi.get("name", "")
        except Exception:
            log.debug("[PLATFORM] host API enumeration failed", exc_info=True)
        devices = []
        for i, dev_raw in enumerate(sd.query_devices()):
            dev = _sd_dev_as_dict(dev_raw)
            if dev is None:
                continue
            if dev["max_input_channels"] <= 0:
                continue
            if _is_non_mic_device(dev["name"]):
                continue
            host_api = host_api_names.get(dev.get("hostapi", 0), "")
            # AUDIO-BT: detect Bluetooth devices by name or sample rate.
            # Bluetooth HFP (Hands-Free Profile) devices typically have
            # "Bluetooth", "HFP", or "Hands-Free" in the device name,
            # and operate at 8 or 16 kHz sample rate.
            dev_name_lower = dev["name"].lower()
            is_bluetooth = (
                any(kw in dev_name_lower for kw in ("bluetooth", "hfp", "hands-free"))
                or dev.get("default_samplerate", 0) in SILERO_VAD_SAMPLE_RATES
            )
            devices.append(
                {
                    "id": str(i),
                    "index": i,
                    "name": dev["name"],
                    "host_api": host_api,
                    "channels": dev["max_input_channels"],
                    "default": i == default_index,
                    "is_bluetooth": is_bluetooth,
                }
            )
            if is_bluetooth:
                log.warning(
                    "[PLATFORM] Bluetooth/HFP device detected: %s "
                    "(sample_rate=%s). Audio quality may be limited. "
                    "Consider disabling the hands-free telephony profile "
                    "in Bluetooth settings for better quality.",
                    dev["name"],
                    dev.get("default_samplerate", "?"),
                )
        return devices
    except Exception:
        log.debug("Could not enumerate microphones", exc_info=True)
        return []


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
    """Find a microphone by its stable ID (device index string)."""
    for mic in _pkg.list_microphones():
        if mic["id"] == mic_id:
            return mic
    return None
