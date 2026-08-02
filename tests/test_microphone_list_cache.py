"""Tests for the module-level TTL cache in
``voice_typer.server.server_platform.microphone_list``.

Covers the fix where ``list_microphones()`` previously invoked
``sd.query_devices(kind="input")``, ``sd.query_hostapis()``, and
``sd.query_devices()`` on every call. A 5 s TTL cache was added so a
device-restart sequence (``find_microphone_by_name`` →
``find_microphone_by_id`` → ``list_microphones``) doesn't re-query
PortAudio 2-3 times in 50-200 ms each. The cache is invalidated
immediately by ``invalidate_microphone_list_cache`` (called from
``MicrophoneDeviceWatcher._invoke_callback`` on OS device-change
events).
"""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_cache_between_tests(monkeypatch):
    """Reset the module-level cache before AND after each test so the
    cache state set by one test doesn't leak into another."""
    from voice_typer.server.server_platform import microphone_list as _ml

    _ml.invalidate_microphone_list_cache()
    yield
    _ml.invalidate_microphone_list_cache()


def _install_fake_sounddevice(monkeypatch, devices, hostapis=None, default_input=None):
    """Install a fake ``sounddevice`` module into ``sys.modules`` that
    returns the supplied device list / host-api list / default input.

    Returns the fake module so the test can inspect ``query_devices``
    call counts via ``fake_sd.query_devices.call_count``.
    """
    if hostapis is None:
        hostapis = [{"name": "ALSA"}, {"name": "MME"}]
    if default_input is None:
        default_input = devices[0] if devices else {"index": -1, "name": "none"}

    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = lambda *args, **kwargs: (
        default_input if kwargs.get("kind") == "input" else devices
    )
    fake_sd.query_hostapis.return_value = hostapis
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return fake_sd


class TestListMicrophonesCache:
    def test_second_call_within_ttl_hits_cache(self, monkeypatch):
        """Two consecutive ``list_microphones()`` calls within the TTL
        window must invoke the underlying PortAudio query exactly once
        — the second call is served from the cache."""
        from voice_typer.server.server_platform import microphone_list as _ml

        devices = [
            {
                "index": 0,
                "name": "Built-in Mic",
                "max_input_channels": 2,
                "hostapi": 0,
                "default_samplerate": 48000,
            }
        ]
        fake_sd = _install_fake_sounddevice(monkeypatch, devices)

        # First call — cache miss, invokes PortAudio.
        result1 = _ml.list_microphones()
        assert len(result1) == 1
        assert result1[0]["name"] == "Built-in Mic"
        first_call_count = fake_sd.query_devices.call_count

        # Second call within TTL — must be served from cache (no new
        # PortAudio calls).
        result2 = _ml.list_microphones()
        assert fake_sd.query_devices.call_count == first_call_count, (
            "second list_microphones() call within TTL must NOT re-query PortAudio"
        )
        # Result must be equivalent (cache returns a copy of the list).
        assert result2 == result1

    def test_invalidate_forces_fresh_query(self, monkeypatch):
        """``invalidate_microphone_list_cache()`` must force the next
        ``list_microphones()`` to re-query PortAudio even if the TTL
        has not expired."""
        from voice_typer.server.server_platform import microphone_list as _ml

        devices = [
            {
                "index": 0,
                "name": "Mic A",
                "max_input_channels": 1,
                "hostapi": 0,
                "default_samplerate": 48000,
            }
        ]
        fake_sd = _install_fake_sounddevice(monkeypatch, devices)

        _ml.list_microphones()
        first_call_count = fake_sd.query_devices.call_count

        _ml.invalidate_microphone_list_cache()
        _ml.list_microphones()
        assert fake_sd.query_devices.call_count > first_call_count, (
            "invalidate_microphone_list_cache() must force a fresh PortAudio query"
        )

    def test_sounddevice_module_swap_invalidates_cache(self, monkeypatch):
        """When ``sys.modules['sounddevice']`` is swapped for a
        different module object (e.g. a test patches it to a
        MagicMock that raises), the cache must treat the entry as
        stale and re-query. This protects the existing
        ``test_returns_empty_on_failure`` test pattern."""
        from voice_typer.server.server_platform import microphone_list as _ml

        devices = [
            {
                "index": 0,
                "name": "Mic A",
                "max_input_channels": 1,
                "hostapi": 0,
                "default_samplerate": 48000,
            }
        ]
        _install_fake_sounddevice(monkeypatch, devices)
        # Populate the cache with the first fake module.
        result1 = _ml.list_microphones()
        assert len(result1) == 1

        # Swap to a second fake module whose query_devices raises.
        bad_sd = MagicMock()
        bad_sd.query_devices.side_effect = RuntimeError("boom")
        monkeypatch.setitem(sys.modules, "sounddevice", bad_sd)

        # Cache must detect the module swap and re-query → returns [].
        result2 = _ml.list_microphones()
        assert result2 == [], (
            "module swap must invalidate cache so the patched raising sd is actually used (got non-empty result)"
        )

    def test_cache_returns_shallow_copy_not_internal_list(self, monkeypatch):
        """The cached list returned to callers must be a fresh shallow
        copy — mutating the outer list must not corrupt the cache."""
        from voice_typer.server.server_platform import microphone_list as _ml

        devices = [
            {
                "index": 0,
                "name": "Mic A",
                "max_input_channels": 1,
                "hostapi": 0,
                "default_samplerate": 48000,
            }
        ]
        _install_fake_sounddevice(monkeypatch, devices)

        result1 = _ml.list_microphones()
        result1.append({"id": "fake", "name": "intruder"})

        result2 = _ml.list_microphones()
        assert not any(m.get("id") == "fake" for m in result2), (
            "caller mutation of the returned list must not leak into the cache"
        )

    def test_ttl_expiry_forces_fresh_query(self, monkeypatch):
        """After the TTL window elapses, the next call must re-query
        PortAudio even without an explicit invalidate call."""
        from voice_typer.server.server_platform import microphone_list as _ml

        devices = [
            {
                "index": 0,
                "name": "Mic A",
                "max_input_channels": 1,
                "hostapi": 0,
                "default_samplerate": 48000,
            }
        ]
        fake_sd = _install_fake_sounddevice(monkeypatch, devices)

        _ml.list_microphones()
        first_call_count = fake_sd.query_devices.call_count

        # Backdate the cache timestamp so it's older than the TTL.
        with _ml._LIST_MICS_CACHE_LOCK:
            stale_ts = time.monotonic() - _ml._LIST_MICS_CACHE_TTL_S - 1.0
            cached = _ml._LIST_MICS_CACHE
            if cached is not None:
                _ml._LIST_MICS_CACHE = (stale_ts, cached[1], cached[2])

        _ml.list_microphones()
        assert fake_sd.query_devices.call_count > first_call_count, (
            "TTL-expired cache must force a fresh PortAudio query"
        )


class TestInvalidateExported:
    def test_invalidate_is_re_exported_from_package(self):
        """``invalidate_microphone_list_cache`` must be importable from
        ``voice_typer.server.server_platform`` (the package re-exports
        it from ``.microphone_list``) so callers (notably
        ``microphone_watcher._invoke_callback``) can reach it without
        importing the submodule directly."""
        from voice_typer.server import server_platform as sp

        assert hasattr(sp, "invalidate_microphone_list_cache")
        assert callable(sp.invalidate_microphone_list_cache)
