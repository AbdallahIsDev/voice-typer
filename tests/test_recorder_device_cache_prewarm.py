"""Tests for the recorder device-cache pre-warm + cached channel lookup."""

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
    """Build a real ``Recorder`` (mock config unless one is injected)."""
    if config is None:
        from tests.fixtures.ipc_test_helpers import make_fake_recorder

        return make_fake_recorder()
    return make_recorder(config)


class TestPrewarmDeviceCache:
    """``_prewarm_device_cache`` spawns a daemon thread that populates the cache."""

    def test_prewarm_spawns_named_daemon_thread(self):
        """The pre-warm thread is a daemon named ``recorder-device-cache-prewarm``."""
        r = _make_recorder()
        # The thread is started in __init__; it may have already finished
        r._prewarm_device_cache()
        # Find the prewarm thread (it may have exited already, so enumerate
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
        # Cache stays None (or empty), no crash.
        assert r._devices._device_list_cache is None or r._devices._device_list_cache == []


def _wait_for_prewarm_threads_to_exit(timeout: float = 5.0) -> None:
    """Block until no ``recorder-device-cache-prewarm`` threads are alive."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not any(t.name == "recorder-device-cache-prewarm" and t.is_alive() for t in threading.enumerate()):
            return
        time.sleep(0.005)


class TestPrewarmHiddenStartGate:
    """app started hidden (autostart, ``VT_START_HIDDEN=1``) that must not"""

    def test_hidden_start_skips_stream_prewarm(self, monkeypatch):
        """InputStream (``Recorder._prewarm_input_stream`` stays uncalled)"""
        from voice_typer.server.recording import Recorder

        monkeypatch.setenv("VT_START_HIDDEN", "1")
        spy = MagicMock()
        monkeypatch.setattr(Recorder, "_prewarm_input_stream", spy)

        r = _make_recorder()
        # Re-invoke for determinism (the __init__ prewarm may have
        r._prewarm_device_cache()
        _wait_for_prewarm_threads_to_exit()

        (
            spy.assert_not_called(),
            (
                "the prewarm opened an InputStream on a hidden start, the OS "
                "mic indicator lights while the user has not shown the app "
                "(C-BG-1 privacy contract)."
            ),
        )
        # The query-only cache warm is NOT gated.
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            if r._devices._device_list_cache is not None:
                break
            time.sleep(0.005)
        assert r._devices._device_list_cache is not None, (
            "the device-list cache warm must keep running on hidden starts "
            "(it is a device enumeration, not a microphone stream)."
        )

    def test_visible_start_runs_stream_prewarm(self, monkeypatch):
        """No ``VT_START_HIDDEN`` → the prewarm still opens the stream"""
        from voice_typer.server.recording import Recorder

        monkeypatch.delenv("VT_START_HIDDEN", raising=False)
        spy = MagicMock()
        monkeypatch.setattr(Recorder, "_prewarm_input_stream", spy)

        _make_recorder()
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            if spy.call_count > 0:
                break
            time.sleep(0.005)
        assert spy.call_count > 0, (
            "the stream prewarm did not run on a visible start, the hidden-start gate is too broad."
        )


class TestPrewarmStreamStartFailureCloses:
    """``prewarm_input_stream`` must close the stream when ``start()``"""

    def test_start_failure_still_closes_stream(self, monkeypatch):
        import voice_typer.server.recording as recording_mod

        events: list[str] = []

        class StartFailsStream:
            def __init__(self, *a, **kw):
                events.append("init")

            def start(self):
                events.append("start")
                raise OSError("device busy")

            def stop(self):
                events.append("stop")

            def close(self):
                events.append("close")

        # Construct with the hidden-start gate ON so the __init__ prewarm
        monkeypatch.setenv("VT_START_HIDDEN", "1")
        r = _make_recorder()
        monkeypatch.delenv("VT_START_HIDDEN", raising=False)
        monkeypatch.setattr(recording_mod.sd, "InputStream", StartFailsStream)

        r._prewarm_input_stream()

        assert "close" in events, (
            "prewarm stream leaked: start() raised after the constructor "
            "opened the stream but close() never ran (OS mic indicator "
            "stays lit)"
        )
        assert "stop" not in events, "stop() must only run when start() succeeded"


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
        # Device 99 isn't in the cache, safe fallback is 1 (mono).
        assert r._cached_max_input_channels(99) == 1

    def test_returns_one_when_cache_empty(self):
        """Empty cache → fallback to 1 for int devices."""
        r = _make_recorder()
        r._devices._device_list_cache = None
        assert r._cached_max_input_channels(0) == 1

    def test_falls_back_to_direct_query_for_default_device(self, monkeypatch):
        """For ``device=None`` (OS default), the channel count must come"""
        import voice_typer.server.recording as recording_mod

        calls = []

        def query_devices(device=None, kind=None):
            calls.append((device, kind))
            return {
                "index": 3,
                "max_input_channels": 2,
                "name": "Default Mic",
                "default_samplerate": 48000,
            }

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        r = _make_recorder()
        # Warm the device-list cache with the OS-default device (index 3).
        r._devices._device_list_cache = [
            {"index": 3, "name": "Default Mic", "max_input_channels": 2},
        ]
        r._devices._device_list_cache_time = time.monotonic()

        assert r._cached_max_input_channels(None) == 2
        assert (None, "input") in calls, "must resolve the OS default input device"
        # Repeated calls within the same device-list generation are served
        count_before = len(calls)
        assert r._cached_max_input_channels(None) == 2
        assert len(calls) == count_before, (
            "default-device lookup re-queried PortAudio for an unchanged "
            "device-list cache, the cached count should be reused."
        )

    def test_default_device_cache_miss_uses_resolved_query_info(self, monkeypatch):
        """If the OS-default device is NOT yet in the cached list (cold"""
        import voice_typer.server.recording as recording_mod

        def query_devices(device=None, kind=None):
            return {
                "index": 7,
                "max_input_channels": 3,
                "name": "Just-Plugged Mic",
                "default_samplerate": 48000,
            }

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        r = _make_recorder()
        # Cache does not contain index 7.
        r._devices._device_list_cache = [
            {"index": 0, "name": "Mic A", "max_input_channels": 1},
        ]
        r._devices._device_list_cache_time = time.monotonic()
        assert r._cached_max_input_channels(None) == 3

    def test_direct_query_failure_returns_one(self, monkeypatch):
        """If the default-device resolution fails, return 1 (mono)."""
        import voice_typer.server.recording as recording_mod

        def boom(*a, **kw):
            raise OSError("no default input device")

        monkeypatch.setattr(recording_mod.sd, "query_devices", boom)
        r = _make_recorder()
        assert r._cached_max_input_channels(None) == 1

    def test_default_device_requery_after_device_list_refresh(self, monkeypatch):
        """A new device-list generation (TTL refresh / event"""
        import voice_typer.server.recording as recording_mod

        state = {"index": 3, "max_input_channels": 2}

        def query_devices(device=None, kind=None):
            if device is None and kind == "input":
                return {
                    "index": state["index"],
                    "max_input_channels": state["max_input_channels"],
                    "name": "Default Mic",
                    "default_samplerate": 48000,
                }
            return [
                {"index": state["index"], "name": "Default Mic", "max_input_channels": state["max_input_channels"]},
            ]

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})
        r = _make_recorder()

        # Generation 1: default device index 3, 2 channels.
        r._devices._device_list_cache = [
            {"index": 3, "name": "Default Mic", "max_input_channels": 2},
        ]
        r._devices._device_list_cache_time = time.monotonic() - 5.0
        assert r._cached_max_input_channels(None) == 2

        state["index"] = 5
        state["max_input_channels"] = 1
        r._devices._device_list_cache = [
            {"index": 0, "name": "Mic A", "max_input_channels": 1},
            {"index": 5, "name": "New Default Mic", "max_input_channels": 1},
        ]
        r._devices._device_list_cache_time = time.monotonic()
        assert r._cached_max_input_channels(None) == 1, (
            "stale memoized channel count served across a device-list "
            "refresh, the default device changed but the lookup did not "
            "re-resolve."
        )


class TestStartUsesCachedLookup:
    """``start()`` should use ``_cached_max_input_channels`` instead of a"""

    def test_start_does_not_call_query_devices_for_channels_on_int_candidate(self, monkeypatch):
        """With a warm cache, ``start()`` must NOT issue"""
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
            opened.append(kw)
            orig_init(self, *a, **kw)

        OkStream.__init__ = capture_init
        monkeypatch.setattr(recording_mod.sd, "InputStream", OkStream)

        config = MagicMock(sample_rate=16000, microphone="0", recording_channels=5)
        r = make_recorder(config)
        # Pre-populate the cache so _cached_max_input_channels(0) returns 2
        r._devices._device_list_cache = [
            {"index": 0, "name": "Test Mic", "max_input_channels": 2},
        ]
        r._devices._device_list_cache_time = time.monotonic()

        r.start()
        try:
            # Filter out prewarm calls (callback=None), only keep real
            real_opens = [kw for kw in opened if kw.get("callback") is not None]
            assert real_opens, "start() should have opened an InputStream with a callback"
            channels_used = real_opens[0].get("channels")
            assert channels_used == 2, (
                f"channels should be capped at 2 (from cache); got {channels_used!r}. "
                "If this is 1, the cached lookup fallback fired, the cache "
                "may not have been consulted."
            )
        finally:
            r.stop()
