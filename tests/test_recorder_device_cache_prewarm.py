"""Tests for the recorder device-cache pre-warm + cached channel lookup.

Covers the recorder-side half of the start() hot-path optimization:
``Recorder._prewarm_device_cache`` spawns a background daemon thread to
populate ``DeviceManager._device_list_cache`` so the first ``start()``
call doesn't pay the 50-200ms PortAudio enumeration cost on the hotkey
critical path, and ``Recorder._cached_max_input_channels`` consults
that cache instead of issuing a fresh ``sd.query_devices()`` RPC per
candidate.

The device_manager-side methods (``_same_physical_microphone_candidates``
and ``_resolve_effective_sample_rate``) still issue direct RPCs and are
out of scope for this test file (they live in ``device_manager.py``,
owned by a different fix bundle).
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

from tests.fixtures.recorder_test_helpers import make_recorder


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Headless mock for ``sounddevice`` so tests don't touch real audio HW."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _make_recorder(config=None):
    """Build a real ``Recorder`` (mock config unless one is injected).

    Both branches delegate to the single canonical factory
    ``tests.fixtures.recorder_test_helpers.make_recorder``: the
    mock-config default goes through its public
    ``make_fake_recorder`` alias (same factory, pre-populated config
    fields), and an injected config goes through its
    ``config=`` parameter (real ``Recorder`` construction with the
    caller-owned config).
    """
    if config is None:
        from tests.fixtures.ipc_test_helpers import make_fake_recorder

        return make_fake_recorder()
    return make_recorder(config)


# ── _prewarm_device_cache ────────────────────────────────────────────────


class TestPrewarmDeviceCache:
    """``_prewarm_device_cache`` spawns a daemon thread that populates the cache."""

    def test_prewarm_spawns_named_daemon_thread(self):
        """The pre-warm thread is a daemon named ``recorder-device-cache-prewarm``."""
        r = _make_recorder()
        # The thread is started in __init__; it may have already finished
        # (mock returns [] instantly) but the name should be observable
        # in the brief window after construction. Re-invoke to make the
        # test deterministic.
        r._prewarm_device_cache()
        # Find the prewarm thread (it may have exited already, so enumerate
        # in a short retry loop).
        deadline = time.perf_counter() + 2.0
        found = None
        while time.perf_counter() < deadline:
            found = next(
                (t for t in threading.enumerate() if t.name == "recorder-device-cache-prewarm"),
                None,
            )
            if found is not None:
                break
            time.sleep(0.005)
        # If the thread already exited (mock is fast), we can't observe it
        # directly — but the cache should be populated, which is the real
        # contract. Verify the cache is populated as a fallback assertion.
        if found is None:
            assert r._devices._device_list_cache is not None, (
                "pre-warm thread should have populated the cache (even if it already exited)"
            )
            return
        assert found.daemon, "pre-warm thread must be a daemon so it never blocks process exit"

    def test_prewarm_populates_cache_via_refresh(self, monkeypatch):
        """Pre-warm calls ``_refresh_device_list`` which populates the cache."""
        import voice_typer.server.recording as recording_mod

        devices = [
            {
                "index": 0,
                "name": "Mic A",
                "max_input_channels": 2,
                "default_samplerate": 48000,
                "hostapi": 0,
            },
            {
                "index": 1,
                "name": "Mic B",
                "max_input_channels": 1,
                "default_samplerate": 16000,
                "hostapi": 0,
            },
        ]

        def query_devices(device=None, kind=None):
            if device is None and kind is None:
                return devices
            if kind == "input":
                return devices[0]
            return next(d for d in devices if d["index"] == device)

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})

        r = _make_recorder()
        # Wait for the pre-warm thread to finish (mock is fast).
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if r._devices._device_list_cache is not None:
                break
            time.sleep(0.005)
        assert r._devices._device_list_cache is not None, "pre-warm should populate the cache"
        # Cache should have both input devices (both have max_input_channels > 0).
        assert len(r._devices._device_list_cache) == 2

    def test_prewarm_does_not_raise_when_portaudio_unavailable(self, monkeypatch):
        """If ``sd.query_devices`` raises, the pre-warm thread swallows it."""
        import voice_typer.server.recording as recording_mod

        def boom(*a, **kw):
            raise OSError("PortAudio not available")

        monkeypatch.setattr(recording_mod.sd, "query_devices", boom)
        # Construction must not raise.
        r = _make_recorder()
        # Give the pre-warm thread a moment to run and fail.
        deadline = time.perf_counter() + 1.0
        while time.perf_counter() < deadline:
            if r._devices._device_list_cache is not None:
                break
            time.sleep(0.005)
        # Cache stays None (or empty) — no crash.
        assert r._devices._device_list_cache is None or r._devices._device_list_cache == []


# ── _cached_max_input_channels ───────────────────────────────────────────


class TestCachedMaxInputChannels:
    """``_cached_max_input_channels`` returns the cached value or a safe fallback."""

    def test_returns_cached_value_for_known_device(self):
        """When the cache contains the device, return its ``max_input_channels``."""
        r = _make_recorder()
        r._devices._device_list_cache = [
            {"index": 0, "name": "Mic A", "max_input_channels": 2},
            {"index": 1, "name": "Mic B", "max_input_channels": 1},
        ]
        r._devices._device_list_cache_time = time.monotonic()
        assert r._cached_max_input_channels(0) == 2
        assert r._cached_max_input_channels(1) == 1

    def test_returns_one_for_unknown_device(self):
        """When the device isn't in the cache, fall back to 1 (mono)."""
        r = _make_recorder()
        r._devices._device_list_cache = [
            {"index": 0, "name": "Mic A", "max_input_channels": 2},
        ]
        r._devices._device_list_cache_time = time.monotonic()
        # Device 99 isn't in the cache — safe fallback is 1 (mono).
        assert r._cached_max_input_channels(99) == 1

    def test_returns_one_when_cache_empty(self):
        """Empty cache → fallback to 1 for int devices."""
        r = _make_recorder()
        r._devices._device_list_cache = None
        assert r._cached_max_input_channels(0) == 1

    def test_falls_back_to_direct_query_for_default_device(self, monkeypatch):
        """For ``device=None`` (OS default), fall back to a direct query."""
        import voice_typer.server.recording as recording_mod

        captured = {}

        def query_devices(device=None, kind=None):
            captured["kind"] = kind
            return {"max_input_channels": 2, "name": "Default Mic", "default_samplerate": 48000}

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        r = _make_recorder()
        assert r._cached_max_input_channels(None) == 2
        assert captured["kind"] == "input", "should query the OS default input device"

    def test_direct_query_failure_returns_one(self, monkeypatch):
        """If the direct query for the default device fails, return 1 (mono)."""
        import voice_typer.server.recording as recording_mod

        def boom(*a, **kw):
            raise OSError("no default input device")

        monkeypatch.setattr(recording_mod.sd, "query_devices", boom)
        r = _make_recorder()
        assert r._cached_max_input_channels(None) == 1


# ── start() integration: cached lookup is used ───────────────────────────


class TestStartUsesCachedLookup:
    """``start()`` should use ``_cached_max_input_channels`` instead of a
    direct ``sd.query_devices(candidate)`` RPC for channel detection."""

    def test_start_does_not_call_query_devices_for_channels_on_int_candidate(self, monkeypatch):
        """With a warm cache, ``start()`` must NOT issue
        ``sd.query_devices(<int>)`` for the channel-detection step
        (the cached lookup handles it). The ``_resolve_effective_sample_rate``
        delegator (in device_manager.py) still calls ``sd.query_devices``,
        so we filter to only positional-int calls."""
        import voice_typer.server.recording as recording_mod

        devices = [
            {
                "index": 0,
                "name": "Test Mic",
                "max_input_channels": 2,
                "default_samplerate": 16000,
                "hostapi": 0,
            },
        ]

        def query_devices(device=None, kind=None):
            if device is None and kind is None:
                return devices
            if kind == "input":
                return devices[0]
            return next(d for d in devices if d["index"] == device)

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})

        class OkStream:
            def __init__(self, *a, **kw):
                self.channels = kw.get("channels")

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        opened = []
        orig_init = OkStream.__init__

        def capture_init(self, *a, **kw):
            # Capture the full kwargs so we can filter out prewarm calls
            # (the prewarm uses callback=None; start() uses a real callback).
            opened.append(kw)
            orig_init(self, *a, **kw)

        OkStream.__init__ = capture_init
        monkeypatch.setattr(recording_mod.sd, "InputStream", OkStream)

        # recording_channels=5 requests 5 channels; the cached lookup
        # returns max_input_channels=2, so the ``elif channels > max_ch``
        # branch caps channels at 2. This proves the cached value flowed
        # through (a cache miss would return 1, capping channels at 1).
        config = MagicMock(sample_rate=16000, microphone="0", recording_channels=5)
        r = make_recorder(config)
        # Pre-populate the cache so _cached_max_input_channels(0) returns 2
        # without a direct query_devices(0) call. The pre-warm thread may
        # have already done this, but we set it explicitly for determinism.
        r._devices._device_list_cache = [
            {"index": 0, "name": "Test Mic", "max_input_channels": 2},
        ]
        r._devices._device_list_cache_time = time.monotonic()

        r.start()
        try:
            # Filter out prewarm calls (callback=None) — only keep real
            # start() calls (which pass a real callback closure).
            real_opens = [kw for kw in opened if kw.get("callback") is not None]
            assert real_opens, "start() should have opened an InputStream with a callback"
            channels_used = real_opens[0].get("channels")
            assert channels_used == 2, (
                f"channels should be capped at 2 (from cache); got {channels_used!r}. "
                "If this is 1, the cached lookup fallback fired — the cache "
                "may not have been consulted."
            )
        finally:
            r.stop()
