"""Focused tests for the device-list TTL policy and the shared PortAudio walk."""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Headless mock for ``sounddevice`` so tests don't touch real audio HW."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _make_device_manager(recorder=None):
    from voice_typer.server.recording.device_manager import DeviceManager

    if recorder is None:
        recorder = MagicMock()
        recorder.config = MagicMock(sample_rate=16000, microphone=None)
        recorder.config.microphone = None
    return DeviceManager(recorder)


class TestWatcherAbsentFastTtl:
    """``_refresh_device_list`` uses the 5 s TTL for the whole session"""

    def test_watcher_none_uses_fast_ttl_beyond_old_window(self, monkeypatch):
        """A watcher-less DeviceManager must refresh at the 5 s cadence"""
        dm = _make_device_manager()
        dm._mic_watcher = None

        devices = [
            {
                "index": 0,
                "name": "Mic A",
                "max_input_channels": 1,
                "default_samplerate": 48000,
                "hostapi": 0,
            }
        ]
        import voice_typer.server.recording.device_manager as dm_mod

        calls = {"n": 0}

        def query_devices(*a, **k):
            calls["n"] += 1
            return devices

        monkeypatch.setattr(dm_mod.sd, "query_devices", query_devices)

        # First refresh populates the cache.
        result1 = dm._refresh_device_list()
        assert len(result1) == 1
        calls_after_first = calls["n"]

        dm._device_list_cache_time = time.monotonic() - 6.0
        dm._refresh_device_list()
        assert calls["n"] > calls_after_first, (
            "watcher-absent cache must refresh after the 5 s fast TTL even when the failure is older than 60 s"
        )

        # Age the cache to 20 s — still past the fast TTL, still inside
        calls_before = calls["n"]
        dm._device_list_cache_time = time.monotonic() - 20.0
        dm._refresh_device_list()
        assert calls["n"] > calls_before, (
            "watcher-absent cache must keep the 5 s TTL for the whole session, not revert to 30 s after a 60 s window"
        )

    def test_watcher_present_uses_default_ttl(self, monkeypatch):
        """When the watcher is running, the default 30 s TTL applies."""
        import voice_typer.server.recording.device_manager as dm_mod

        dm = _make_device_manager()
        dm._mic_watcher = MagicMock()  # watcher present
        devices = [{"index": 0, "name": "Mic", "max_input_channels": 1, "default_samplerate": 48000, "hostapi": 0}]
        calls = {"n": 0}

        def query_devices(*a, **k):
            calls["n"] += 1
            return devices

        monkeypatch.setattr(dm_mod.sd, "query_devices", query_devices)

        dm._refresh_device_list()
        calls_after_first = calls["n"]

        # Age the cache to 10 s: inside the 30 s default TTL → no re-query
        dm._device_list_cache_time = time.monotonic() - 10.0
        dm._refresh_device_list()
        assert calls["n"] == calls_after_first, "watcher-present cache must stay warm inside the 30 s default TTL"


class TestRestartStreamCacheFirst:
    """``restart_stream`` reads name / channel info from the device-list"""

    def test_original_index_name_match_uses_cache_not_live_query(self, monkeypatch):
        """When the cache already holds the original device, the name-match"""
        import voice_typer.server.recording.disconnect_handler as dh_mod
        from voice_typer.server.recording.recorder import Recorder

        from tests.fixtures.ipc_test_helpers import make_fake_recorder

        # Disable the construction-time prewarm daemon so it cannot
        monkeypatch.setattr(Recorder, "_prewarm_device_cache", lambda self: None)

        devices = [
            {
                "index": 0,
                "name": "USB Mic",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            }
        ]
        r = make_fake_recorder()
        monkeypatch.setattr(r, "_current_callback", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(r._devices, "_resolve_device", lambda: 0)
        monkeypatch.setattr(
            r._devices,
            "_same_physical_microphone_candidates",
            lambda _d: [0],
        )
        monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", lambda _d: (48000, None))
        monkeypatch.setattr(dh_mod, "refresh_vad_caches", lambda rec: None)

        # Pre-populate the device-list cache (the recovery path's
        r._devices._device_list_cache = [
            {
                "index": 0,
                "name": "USB Mic",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            }
        ]
        r._devices._device_list_cache_time = time.monotonic()

        live_calls: list = []

        def query_devices(device=None, kind=None):
            live_calls.append((device, kind))
            if kind == "input":
                return devices[0]
            if device is None:
                return devices
            return devices[0]

        monkeypatch.setattr(dh_mod.sd, "query_devices", query_devices)
        fake_stream = MagicMock()
        monkeypatch.setattr(dh_mod.sd, "InputStream", lambda **kw: fake_stream)

        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        DisconnectHandler(r).restart_stream(_captured_generation=0)

        # Name match + channel probe must both be served from the cache.
        per_index_calls = [c for c in live_calls if c[0] is not None and c[1] is None]
        assert per_index_calls == [], (
            "restart_stream must not issue per-index live sd.query_devices "
            f"calls when the cache is warm; got {per_index_calls}"
        )
        assert r._stream_lifecycle._stream is fake_stream

    def test_alternate_candidate_name_match_uses_cache(self, monkeypatch):
        """When the original index is gone, the alternate-candidate loop"""
        import voice_typer.server.recording.disconnect_handler as dh_mod
        from voice_typer.server.recording.recorder import Recorder

        from tests.fixtures.ipc_test_helpers import make_fake_recorder

        monkeypatch.setattr(Recorder, "_prewarm_device_cache", lambda self: None)

        r = make_fake_recorder()
        monkeypatch.setattr(r, "_current_callback", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(r._devices, "_resolve_device", lambda: 0)
        monkeypatch.setattr(
            r._devices,
            "_same_physical_microphone_candidates",
            lambda _d: [0, 1],
        )
        monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", lambda _d: (48000, None))
        monkeypatch.setattr(dh_mod, "refresh_vad_caches", lambda rec: None)

        # Index 0 is gone; index 1 is the same physical mic.
        r._devices._device_list_cache = [
            {
                "index": 1,
                "name": "USB Mic",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 0,
            }
        ]
        r._devices._device_list_cache_time = time.monotonic()

        live_calls: list = []

        def query_devices(device=None, kind=None):
            live_calls.append((device, kind))
            if kind == "input":
                return {"index": 1, "name": "USB Mic", "max_input_channels": 1}
            if device == 1:
                return {"index": 1, "name": "USB Mic", "max_input_channels": 1}
            raise RuntimeError(f"device {device} gone")

        monkeypatch.setattr(dh_mod.sd, "query_devices", query_devices)
        fake_stream = MagicMock()
        monkeypatch.setattr(dh_mod.sd, "InputStream", lambda **kw: fake_stream)

        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        DisconnectHandler(r).restart_stream(_captured_generation=0)

        # No per-index live queries: index 0 miss and index 1 hit both
        index_1_live = [c for c in live_calls if c[0] == 1 and c[1] is None]
        assert index_1_live == [], "alternate candidate at a cached index must not live-query"
        assert r._stream_lifecycle._stream is fake_stream
        assert r._actual_channels == 1

    def test_live_fallback_on_cache_miss_still_works(self, monkeypatch):
        """A cache miss must still fall back to a live query (never"""
        import voice_typer.server.recording.disconnect_handler as dh_mod
        from voice_typer.server.recording.recorder import Recorder

        from tests.fixtures.ipc_test_helpers import make_fake_recorder

        monkeypatch.setattr(Recorder, "_prewarm_device_cache", lambda self: None)

        r = make_fake_recorder()
        monkeypatch.setattr(r, "_current_callback", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(r._devices, "_resolve_device", lambda: None)
        monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", lambda _d: (48000, None))
        monkeypatch.setattr(dh_mod, "refresh_vad_caches", lambda rec: None)

        # Cold cache.
        r._devices._device_list_cache = None
        r._devices._device_list_cache_time = 0.0

        monkeypatch.setattr(
            dh_mod.sd,
            "query_devices",
            lambda *a, **k: {"index": 0, "name": "Default Mic", "max_input_channels": 2},
        )
        fake_stream = MagicMock()
        monkeypatch.setattr(dh_mod.sd, "InputStream", lambda **kw: fake_stream)

        from voice_typer.server.recording.disconnect_handler import DisconnectHandler

        DisconnectHandler(r).restart_stream(_captured_generation=0)

        # System-default path: live kind="input" query is the documented
        assert r._actual_channels == 2
        assert r._stream_lifecycle._stream is fake_stream


class TestSharedPortAudioWalk:
    """``iter_filtered_input_devices`` is the single walk + filter used"""

    def _devices(self):
        return [
            {  # 0: valid mic
                "index": 10,
                "name": "Built-in Mic",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {  # 1: output-only (zero input channels)
                "index": 11,
                "name": "Speakers",
                "max_input_channels": 0,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {  # 2: placeholder name
                "index": 12,
                "name": "Input ()",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
            {  # 3: non-mic loopback with input channels (name filter)
                "index": 13,
                "name": "Stereo Mix",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {  # 4: another valid mic, whitespace-padded name
                "index": 14,
                "name": "  USB Headset  ",
                "max_input_channels": 1,
                "default_samplerate": 16000,
                "hostapi": 1,
            },
            "not-a-dict",  # 5: non-dict entry must be skipped
        ]

    def test_helper_filters_and_returns_triples(self):
        from voice_typer.server.server_platform.microphone_list import (
            iter_filtered_input_devices,
        )

        result = iter_filtered_input_devices(self._devices())
        # Only the two valid mics survive (index 0 and 4).
        assert len(result) == 2
        i0, dev0, name0 = result[0]
        i1, dev1, name1 = result[1]
        assert i0 == 0 and name0 == "Built-in Mic"
        assert i1 == 4 and name1 == "USB Headset"  # stripped
        assert dev0["max_input_channels"] == 2
        assert dev1["default_samplerate"] == 16000

    def test_helper_skips_missing_channel_key(self):
        from voice_typer.server.server_platform.microphone_list import (
            iter_filtered_input_devices,
        )

        result = iter_filtered_input_devices([{"index": 0, "name": "NoCh"}])
        assert result == []

    def test_canonical_list_shape_unchanged(self, monkeypatch):
        """``list_microphones`` still emits id / index / name / host_api /"""
        from voice_typer.server.server_platform import microphone_list as _ml

        _ml.invalidate_microphone_list_cache()
        devices = [
            {
                "index": 0,
                "name": "Built-in Mic",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {
                "index": 1,
                "name": "Speakers",
                "max_input_channels": 0,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
        ]
        fake_sd = MagicMock()
        fake_sd.query_devices.side_effect = lambda *a, **k: devices[0] if k.get("kind") == "input" else devices
        fake_sd.query_hostapis.return_value = [{"name": "MME", "default_input_device": 0}]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

        mics = _ml.list_microphones()
        assert len(mics) == 1
        mic = mics[0]
        assert set(mic.keys()) == {"id", "index", "name", "host_api", "channels", "default", "is_bluetooth"}
        assert mic["name"] == "Built-in Mic"
        assert mic["channels"] == 2
        assert mic["index"] == 0
        _ml.invalidate_microphone_list_cache()

    def test_recorder_list_shape_unchanged(self, monkeypatch):
        """``DeviceManager._refresh_device_list`` still emits index / name /"""
        import voice_typer.server.recording.device_manager as dm_mod

        dm = _make_device_manager()
        dm._mic_watcher = MagicMock()  # default 30 s TTL
        devices = [
            {
                "index": 0,
                "name": "Built-in Mic",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {
                "index": 1,
                "name": "Speakers",
                "max_input_channels": 0,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {
                "index": 2,
                "name": "Input ()",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
        ]
        monkeypatch.setattr(dm_mod.sd, "query_devices", lambda *a, **k: devices)

        result = dm._refresh_device_list()
        # Speakers (0 ch) and "Input ()" (placeholder) are filtered out.
        assert len(result) == 1
        entry = result[0]
        assert set(entry.keys()) == {"index", "name", "max_input_channels", "default_samplerate", "hostapi"}
        assert entry["index"] == 0
        assert entry["name"] == "Built-in Mic"
        assert entry["max_input_channels"] == 2
        assert entry["default_samplerate"] == 48000
        assert entry["hostapi"] == 0

    def test_both_adapters_agree_on_which_devices_survive(self, monkeypatch):
        """The shared walk must produce the same survivor set for both"""
        from voice_typer.server.server_platform import microphone_list as _ml

        devices = [
            {"index": 0, "name": "Good Mic", "max_input_channels": 1, "default_samplerate": 48000, "hostapi": 0},
            {"index": 1, "name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000, "hostapi": 0},
            {"index": 2, "name": "Input ()", "max_input_channels": 1, "default_samplerate": 48000, "hostapi": 0},
            {"index": 3, "name": "BT Headset", "max_input_channels": 1, "default_samplerate": 16000, "hostapi": 0},
        ]

        # Canonical list
        _ml.invalidate_microphone_list_cache()
        fake_sd = MagicMock()
        fake_sd.query_devices.side_effect = lambda *a, **k: devices[0] if k.get("kind") == "input" else devices
        fake_sd.query_hostapis.return_value = [{"name": "MME", "default_input_device": 0}]
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        canonical_names = {m["name"] for m in _ml.list_microphones()}
        _ml.invalidate_microphone_list_cache()

        # Recorder list
        import voice_typer.server.recording.device_manager as dm_mod

        dm = _make_device_manager()
        dm._mic_watcher = MagicMock()
        monkeypatch.setattr(dm_mod.sd, "query_devices", lambda *a, **k: devices)
        recorder_names = {e["name"] for e in dm._refresh_device_list()}

        assert canonical_names == {"Good Mic", "BT Headset"}
        assert recorder_names == canonical_names, (
            "canonical UI list and recorder candidate list must agree on which devices survive the shared filters"
        )
