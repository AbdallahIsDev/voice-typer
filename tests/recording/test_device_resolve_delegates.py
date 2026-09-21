"""Recorder delegates mic-id resolution to the canonical resolver."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _make_dm(monkeypatch, microphone_value):
    from voice_typer.server.recording.device_manager import DeviceManager

    recorder = MagicMock()
    recorder.config = MagicMock(sample_rate=16000, microphone=microphone_value)
    return DeviceManager(recorder)


def test_unresolvable_id_returns_none(monkeypatch):
    dm = _make_dm(monkeypatch, "99|Ghost Mic")
    monkeypatch.setattr(
        "voice_typer.server.server_platform.microphone_list.resolve_mic_id_to_device_index",
        lambda mic_id: None,
    )
    assert dm._resolve_device() is None


def test_resolvable_stable_id_returns_index(monkeypatch):
    dm = _make_dm(monkeypatch, "Windows WASAPI|Blue Yeti")
    monkeypatch.setattr(
        "voice_typer.server.server_platform.microphone_list.resolve_mic_id_to_device_index",
        lambda mic_id: 12,
    )
    assert dm._resolve_device() == 12


def test_bare_numeric_string_uses_canonical_resolver(monkeypatch):
    dm = _make_dm(monkeypatch, "5")
    seen: list = []
    from voice_typer.server.server_platform import microphone_list as mic_list

    real = mic_list.resolve_mic_id_to_device_index

    def spy(mic_id):
        seen.append(mic_id)
        return real(mic_id)

    monkeypatch.setattr(
        "voice_typer.server.server_platform.microphone_list.resolve_mic_id_to_device_index",
        spy,
    )
    dm._resolve_device()
    assert seen == ["5"]
