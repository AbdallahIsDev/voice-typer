"""Canonical default-device resolution for the recorder."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_caches():
    from voice_typer.server.server_platform import microphone_list as _ml

    _ml.invalidate_microphone_list_cache()
    yield
    _ml.invalidate_microphone_list_cache()


def _sd_device(index, name, hostapi, channels=2, rate=48000.0):
    return {
        "index": index,
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": channels,
        "default_samplerate": rate,
    }


_REALTEK = "Microphone (Realtek(R) Audio)"
_WO_MIC = "WO Mic (WO Mic Device)"
_MME, _WASAPI = 0, 1


def _install_fake_sounddevice(monkeypatch, devices, hostapis, default_input=None):
    if default_input is None and devices:
        default_input = dict(devices[0], index=devices[0].get("index", -1))
    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = lambda *args, **kwargs: (
        default_input if kwargs.get("kind") == "input" else devices
    )
    fake_sd.query_hostapis.return_value = hostapis
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return fake_sd


def _windows_hostapis(mme_default=0, wasapi_default=2):
    return [
        {"name": "MME", "devices": [0], "default_input_device": mme_default},
        {"name": "Windows WASAPI", "devices": [1, 2], "default_input_device": wasapi_default},
    ]


def _mixed_devices():
    return [
        _sd_device(0, _REALTEK, _MME),
        _sd_device(1, _WO_MIC, _WASAPI, 1),
        _sd_device(2, _REALTEK, _WASAPI),
    ]


def _make_device_manager(monkeypatch):
    from voice_typer.server.recording.device_manager import DeviceManager

    recorder = MagicMock()
    recorder.config = MagicMock(sample_rate=16000, microphone=None)
    recorder.config.microphone = None
    return DeviceManager(recorder)


class TestCanonicalDefaultIndex:
    def test_prefers_wasapi_default_over_raw_mme_default(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        raw_mme_default = dict(_sd_device(0, _REALTEK, _MME), index=0)
        _install_fake_sounddevice(monkeypatch, _mixed_devices(), _windows_hostapis(), default_input=raw_mme_default)
        from voice_typer.server.server_platform.microphone_list import get_canonical_default_index

        assert get_canonical_default_index() == 2

    def test_twin_fallback_when_canonical_reports_no_default(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        raw_mme_default = dict(_sd_device(0, _REALTEK, _MME), index=0)
        _install_fake_sounddevice(
            monkeypatch,
            _mixed_devices(),
            _windows_hostapis(wasapi_default=-1),
            default_input=raw_mme_default,
        )
        from voice_typer.server.server_platform.microphone_list import get_canonical_default_index

        assert get_canonical_default_index() == 2

    def test_ambiguous_same_name_twins_resolve_to_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        devices = [
            _sd_device(0, _REALTEK, _MME),
            _sd_device(1, _REALTEK, _WASAPI),
            _sd_device(2, _REALTEK, _WASAPI),
        ]
        hostapis = [
            {"name": "MME", "devices": [0], "default_input_device": 0},
            {"name": "Windows WASAPI", "devices": [1, 2], "default_input_device": -1},
        ]
        raw_mme_default = dict(_sd_device(0, _REALTEK, _MME), index=0)
        _install_fake_sounddevice(monkeypatch, devices, hostapis, default_input=raw_mme_default)
        from voice_typer.server.server_platform.microphone_list import get_canonical_default_index

        assert get_canonical_default_index() is None

    def test_empty_enumeration_resolves_to_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        fake_sd = MagicMock()
        fake_sd.query_devices.side_effect = lambda *a, **k: (
            {"index": -1, "name": ""} if k.get("kind") == "input" else []
        )
        fake_sd.query_hostapis.return_value = []
        monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
        from voice_typer.server.server_platform.microphone_list import get_canonical_default_index

        assert get_canonical_default_index() is None


class TestRecorderUsesCanonicalDefault:
    def _make_dm(self, monkeypatch):
        import voice_typer.server.microphone_watcher as _watcher_mod
        import voice_typer.server.recording.device_manager as dm_mod

        monkeypatch.setattr(_watcher_mod, "MicrophoneDeviceWatcher", MagicMock(side_effect=RuntimeError("x")))
        recorder = MagicMock()
        recorder.config = MagicMock(sample_rate=16000, microphone=None)
        recorder.config.microphone = None
        return dm_mod.DeviceManager(recorder)

    def test_resolve_device_keeps_none_for_system_default(self, monkeypatch):
        dm = self._make_dm(monkeypatch)
        assert dm._resolve_device() is None

    def test_same_physical_candidates_return_canonical_index(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        raw_mme_default = dict(_sd_device(0, _REALTEK, _MME), index=0)
        _install_fake_sounddevice(monkeypatch, _mixed_devices(), _windows_hostapis(), default_input=raw_mme_default)
        dm = self._make_dm(monkeypatch)
        assert dm._same_physical_microphone_candidates(None) == [2]

    def test_cached_device_info_for_none_returns_canonical_entry(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        raw_mme_default = dict(_sd_device(0, _REALTEK, _MME), index=0)
        _install_fake_sounddevice(monkeypatch, _mixed_devices(), _windows_hostapis(), default_input=raw_mme_default)
        dm = self._make_dm(monkeypatch)
        dm._refresh_device_list()
        info = dm._cached_device_info(None)
        assert isinstance(info, dict)
        assert info.get("name") == _REALTEK
        assert int(info.get("index", -1)) == 2
