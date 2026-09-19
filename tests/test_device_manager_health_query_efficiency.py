"""Health-checker query-efficiency tests."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    """Headless mock for ``sounddevice`` so tests don't touch real audio HW."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    import voice_typer.server.recording.device_manager as dm_mod

    monkeypatch.setattr(dm_mod, "sd", mock_sd)
    return mock_sd


def _make_device_manager(microphone=None):
    from voice_typer.server.recording.device_manager import DeviceManager

    recorder = MagicMock()
    recorder.config = MagicMock(sample_rate=16000, microphone=microphone)
    recorder.config.microphone = microphone
    return DeviceManager(recorder)


class TestBtClassificationUsesCache:
    """``_build_device_info_for_retry_policy`` prefers the device-list cache."""

    def test_concrete_device_cache_hit_skips_live_query(self, _mock_sounddevice):
        dm = _make_device_manager(microphone="0")
        cached = {
            "index": 0,
            "name": "Cached Mic",
            "default_samplerate": 16000,
            "max_input_channels": 1,
        }
        dm._device_list_cache = [cached]

        result = dm._build_device_info_for_retry_policy()

        assert result is cached
        _mock_sounddevice.query_devices.assert_not_called()

    def test_concrete_device_cache_miss_falls_back_to_live_query(self, _mock_sounddevice):
        dm = _make_device_manager(microphone="3")
        dm._device_list_cache = []  # empty cache → miss
        live = {"index": 3, "name": "Live Mic", "default_samplerate": 48000}
        _mock_sounddevice.query_devices.return_value = live

        result = dm._build_device_info_for_retry_policy()

        assert result is live
        _mock_sounddevice.query_devices.assert_called()

    def test_default_path_uses_stashed_probe(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        stashed = {
            "index": 1,
            "name": "Bluetooth Headset",
            "default_samplerate": 16000,
        }
        dm._last_default_input_info = stashed

        result = dm._build_device_info_for_retry_policy()

        assert result is stashed
        _mock_sounddevice.query_devices.assert_not_called()

    def test_default_path_first_cycle_no_live_query_when_fallback_disabled(
        self,
        _mock_sounddevice,
    ):
        """the first cycle (the default-change probe later in the same"""
        dm = _make_device_manager(microphone=None)
        dm._last_default_input_info = None

        result = dm._build_device_info_for_retry_policy(allow_live_default_fallback=False)

        assert result is None
        _mock_sounddevice.query_devices.assert_not_called()

    def test_default_path_disconnect_retry_still_live_queries(self, _mock_sounddevice):
        """The disconnect-retry path needs a classification NOW, so the"""
        dm = _make_device_manager(microphone=None)
        dm._last_default_input_info = None
        live = {"index": 2, "name": "BT Mic", "default_samplerate": 16000}
        _mock_sounddevice.query_devices.return_value = live

        result = dm._build_device_info_for_retry_policy()

        assert result is live
        _mock_sounddevice.query_devices.assert_called()

    def test_error_still_returns_none(self, _mock_sounddevice):
        """Query failure still yields None (retry policy falls back)."""
        dm = _make_device_manager(microphone="0")
        dm._device_list_cache = []

        def raising(*a, **k):
            raise RuntimeError("portaudio boom")

        _mock_sounddevice.query_devices.side_effect = raising
        assert dm._build_device_info_for_retry_policy() is None


class TestDefaultInputProbeStash:
    """``_check_default_input_device_changed`` stashes its probe result"""

    def test_probe_result_is_stashed(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        info = {"index": 5, "name": "Default Mic", "default_samplerate": 44100}
        _mock_sounddevice.query_devices.return_value = info

        dm._check_default_input_device_changed()

        assert dm._last_default_input_info is info
        # Baseline captured lazily on first successful probe.
        assert dm._stream_open_default_input_index == 5

    def test_prequeried_info_skips_live_query(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        info = {"index": 7, "name": "Prequeried", "default_samplerate": 48000}

        dm._check_default_input_device_changed(current_info=info)

        _mock_sounddevice.query_devices.assert_not_called()
        assert dm._last_default_input_info is info

    def test_unchanged_default_does_not_fire_disconnect(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        dm._stream_open_default_input_index = 3
        dm.recorder._recording_event.is_set.return_value = True
        info = {"index": 3, "name": "Same", "default_samplerate": 48000}

        dm._check_default_input_device_changed(current_info=info)

        assert dm._device_disconnected is False
        dm.recorder._spawn_device_thread.assert_not_called()

    def test_changed_default_fires_disconnect_handler(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        dm._stream_open_default_input_index = 1
        dm.recorder._recording_event.is_set.return_value = True
        dm.recorder._stop_generation = 9
        info = {"index": 4, "name": "New Default", "default_samplerate": 48000}

        dm._check_default_input_device_changed(current_info=info)

        assert dm._device_disconnected is True
        assert dm._stream_open_default_input_index == 4
        dm.recorder._spawn_device_thread.assert_called_once()


class TestEffectiveIntervalClassification:
    """``_effective_device_check_interval_s`` classifies from cache/stash"""

    def test_bt_default_uses_short_interval_from_stash(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        dm._device_check_interval_s = 30.0
        dm._device_check_interval_s_bt = 5.0
        dm._last_default_input_info = {
            "index": 1,
            "name": "Bluetooth Stereo",
            "default_samplerate": 16000,
        }

        assert dm._effective_device_check_interval_s() == pytest.approx(5.0)
        _mock_sounddevice.query_devices.assert_not_called()

    def test_non_bt_default_uses_long_interval(self, _mock_sounddevice):
        dm = _make_device_manager(microphone=None)
        dm._device_check_interval_s = 30.0
        dm._device_check_interval_s_bt = 5.0
        dm._last_default_input_info = {
            "index": 1,
            "name": "USB Studio Mic",
            "default_samplerate": 48000,
        }

        assert dm._effective_device_check_interval_s() == pytest.approx(30.0)

    def test_first_cycle_default_falls_back_to_long_interval(self, _mock_sounddevice):
        """No stash yet → classify as non-BT (30 s) without a live query."""
        dm = _make_device_manager(microphone=None)
        dm._device_check_interval_s = 30.0
        dm._device_check_interval_s_bt = 5.0
        dm._last_default_input_info = None

        assert dm._effective_device_check_interval_s() == pytest.approx(30.0)
        _mock_sounddevice.query_devices.assert_not_called()

    def test_concrete_bt_device_classified_from_cache(self, _mock_sounddevice):
        dm = _make_device_manager(microphone="0")
        dm._device_check_interval_s = 30.0
        dm._device_check_interval_s_bt = 5.0
        dm._device_list_cache = [
            {
                "index": 0,
                "name": "JBL BT Hands-Free",
                "default_samplerate": 16000,
                "max_input_channels": 1,
            },
        ]

        assert dm._effective_device_check_interval_s() == pytest.approx(5.0)
        _mock_sounddevice.query_devices.assert_not_called()
