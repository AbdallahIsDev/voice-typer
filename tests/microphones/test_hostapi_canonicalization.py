"""Canonical host-API normalization + cross-host-API id resolution.

PortAudio enumerates every OS audio endpoint once PER HOST API. On the
reference Windows machine (sounddevice 0.5.5) the three real input
endpoints (AudioRelay virtual mic, WO Mic virtual mic, Realtek built-in)
appear as duplicate records under MME / DirectSound / WASAPI / WDM-KS;
WDM-KS additionally exposes disabled endpoints (Line In, Stereo Mix) the
OS UI deliberately does not offer, and MME truncates names at 31 chars.

These tests pin:

1. ``list_microphones()`` collapses the duplicate views to the platform's
   canonical host API (Windows → WASAPI — the view matching the Windows
   Settings "Input" page, with full untruncated names), keeping virtual
   microphones and degrading gracefully when the preferred API yields
   nothing.
2. The ``default`` flag comes from the canonical host API's own
   ``default_input_device`` (the PortAudio *global* default can sit on a
   non-canonical API — here an MME record).
3. Persisted stable ids whose host API is no longer enumerated resolve
   via unambiguous exact-name match; endpoint names that differ across
   host APIs stay unresolved rather than guessing a wrong device.

Fixture data mirrors a real captured dump (input devices only). The live
machine reported 17 raw records; the two unlisted ones were additional
placeholder entries already covered by the invalid-name filter, so the
fixture carries the 15 itemized records verbatim.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server.server_platform.microphone_list import (
    find_microphone_by_id,
    resolve_mic_id_to_device_index,
)


@pytest.fixture(autouse=True)
def _reset_list_cache():
    """Isolate the module-level TTL cache between tests."""
    from voice_typer.server.server_platform import microphone_list as _ml

    _ml.invalidate_microphone_list_cache()
    yield
    _ml.invalidate_microphone_list_cache()


def _sd_device(
    index: int,
    name: str,
    hostapi: int,
    channels: int = 2,
    rate: float = 48000.0,
):
    return {
        "index": index,
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": channels,
        "default_samplerate": rate,
    }


# Real captured dump — input records grouped per host API.
_REALTEK = "Microphone (Realtek(R) Audio)"
_WO_MIC = "WO Mic (WO Mic Device)"
_AUDIORELAY_TRUNCATED = "AudioRelay (Virtual Mic for Aud"
_AUDIORELAY_FULL = "AudioRelay (Virtual Mic for AudioRelay)"

_MME, DSOUND, WASAPI, WDMKS = 0, 1, 2, 3


def _real_machine_devices() -> list[dict]:
    mme_rate = 44100.0
    return [
        _sd_device(1, _REALTEK, _MME, 2, mme_rate),
        _sd_device(2, _AUDIORELAY_TRUNCATED, _MME, 2, mme_rate),
        _sd_device(3, _WO_MIC, _MME, 1, mme_rate),
        _sd_device(11, _REALTEK, DSOUND),
        _sd_device(12, _AUDIORELAY_TRUNCATED, DSOUND),
        _sd_device(13, _WO_MIC, DSOUND, 1),
        _sd_device(25, _AUDIORELAY_FULL, WASAPI),
        _sd_device(26, _WO_MIC, WASAPI, 1),
        _sd_device(27, _REALTEK, WASAPI),
        _sd_device(28, "Line In (Realtek HD Audio Line input)", WDMKS),
        _sd_device(29, "Microphone (Realtek HD Audio Mic input)", WDMKS),
        _sd_device(32, "Stereo Mix (Realtek HD Audio Stereo input)", WDMKS),
        _sd_device(33, "Virtual Mic (AudioRelay Wave)", WDMKS),
        _sd_device(36, "Microphone (WO Mic Wave)", WDMKS, 1),
        _sd_device(37, "Input ()", WDMKS, 1),
    ]


def _real_machine_hostapis() -> list[dict]:
    return [
        {
            "name": "MME",
            "devices": [1, 2, 3],
            "default_input_device": 1,
            "default_output_device": -1,
        },
        {
            "name": "Windows DirectSound",
            "devices": [11, 12, 13],
            "default_input_device": 11,
            "default_output_device": -1,
        },
        {
            "name": "Windows WASAPI",
            "devices": [25, 26, 27],
            "default_input_device": 27,
            "default_output_device": -1,
        },
        {
            "name": "Windows WDM-KS",
            "devices": [28, 29, 32, 33, 36, 37],
            "default_input_device": -1,
            "default_output_device": -1,
        },
    ]


def _install_fake_sounddevice(
    monkeypatch,
    devices,
    hostapis,
    default_input=None,
):
    """Install a fake ``sounddevice`` module returning the given data."""
    if default_input is None and devices:
        default_input = dict(devices[0], index=devices[0].get("index", -1))

    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = lambda *args, **kwargs: (
        default_input if kwargs.get("kind") == "input" else devices
    )
    fake_sd.query_hostapis.return_value = hostapis
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return fake_sd


def _canonical_names(mics) -> set[str]:
    return {m["name"] for m in mics}


class TestWindowsCanonicalization:
    def test_multi_api_dump_collapses_to_canonical_wasapi_records(self, monkeypatch):
        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert len(mics) == 3
        assert all(m["host_api"] == "Windows WASAPI" for m in mics)
        assert _canonical_names(mics) == {_AUDIORELAY_FULL, _WO_MIC, _REALTEK}

    def test_virtual_microphones_survive_canonicalization(self, monkeypatch):
        """AudioRelay / WO Mic are software microphones, not junk records —
        dropping every non-hardware name would hide them from users."""
        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        from voice_typer.server.server_platform import list_microphones

        names = _canonical_names(list_microphones())
        assert _AUDIORELAY_FULL in names
        assert _WO_MIC in names

    def test_full_untruncated_name_wins_over_mme_31_char_truncation(self, monkeypatch):
        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        from voice_typer.server.server_platform import list_microphones

        names = _canonical_names(list_microphones())
        assert _AUDIORELAY_FULL in names
        assert _AUDIORELAY_TRUNCATED not in names

    def test_default_flag_lands_on_wasapi_default_not_global_mme_record(self, monkeypatch):
        """The PortAudio GLOBAL default input (kind='input') is the MME
        record at index 1; the canonical default must instead come from
        the WASAPI host API's own default_input_device (=27)."""
        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        defaults = [m for m in mics if m["default"]]
        assert len(defaults) == 1
        assert defaults[0]["name"] == _REALTEK
        assert defaults[0]["index"] == 27

    def test_same_name_twins_within_preferred_api_both_kept_with_disambiguator(self, monkeypatch):
        devices = [
            _sd_device(1, _REALTEK, _MME),
            _sd_device(27, _REALTEK, WASAPI),
            _sd_device(30, _REALTEK, WASAPI),
        ]
        hostapis = [
            {"name": "MME", "devices": [1], "default_input_device": 1},
            {"name": "Windows DirectSound", "devices": [], "default_input_device": -1},
            {
                "name": "Windows WASAPI",
                "devices": [27, 30],
                "default_input_device": 27,
            },
        ]
        _install_fake_sounddevice(monkeypatch, devices, hostapis)
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        ids = sorted(m["id"] for m in mics)
        assert ids == [
            "Windows WASAPI|Microphone (Realtek(R) Audio)",
            "Windows WASAPI|Microphone (Realtek(R) Audio)#2",
        ]
        flagged = [m for m in mics if m["default"]]
        assert len(flagged) == 1
        assert flagged[0]["index"] == 27

    def test_disabled_wdmks_endpoints_absent_from_canonical_output(self, monkeypatch):
        """Answers "did we hide a real mic?": Line In / Stereo Mix are
        DISABLED endpoints the OS Input page never offers (and the
        pre-existing non-mic filter already drops them); the WDM-KS
        naming variant of the Realtek mic must not leak back in either."""
        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        from voice_typer.server.server_platform import list_microphones

        names = _canonical_names(list_microphones())
        assert "Line In (Realtek HD Audio Line input)" not in names
        assert "Stereo Mix (Realtek HD Audio Stereo input)" not in names
        assert "Microphone (Realtek HD Audio Mic input)" not in names


class TestGracefulDegradation:
    def test_empty_preferred_api_returns_full_list(self, monkeypatch):
        """A Windows install where WASAPI yields no input devices must get
        the complete unfiltered enumeration — never an empty list."""
        devices = [
            _sd_device(1, _REALTEK, _MME),
            _sd_device(3, _WO_MIC, _MME, 1),
        ]
        hostapis = [{"name": "MME", "devices": [1, 3], "default_input_device": 1}]
        _install_fake_sounddevice(monkeypatch, devices, hostapis)
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert _canonical_names(mics) == {_REALTEK, _WO_MIC}
        assert next(m for m in mics if m["default"])["index"] == 1


class TestPlatformGating:
    def test_linux_prefers_pulseaudio(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        devices = [
            _sd_device(0, "Blue Yeti", 0),
            _sd_device(2, "USB Mic", 1),
        ]
        hostapis = [
            {"name": "PulseAudio", "devices": [0], "default_input_device": 0},
            {"name": "ALSA", "devices": [2], "default_input_device": 2},
        ]
        _install_fake_sounddevice(monkeypatch, devices, hostapis)
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert len(mics) == 1
        assert mics[0]["host_api"] == "PulseAudio"
        assert mics[0]["name"] == "Blue Yeti"
        assert mics[0]["default"] is True

    def test_linux_falls_back_when_no_pulseaudio_devices(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        devices = [_sd_device(2, "USB Mic", 1)]
        hostapis = [
            {"name": "PulseAudio", "devices": [], "default_input_device": -1},
            {"name": "ALSA", "devices": [2], "default_input_device": 2},
        ]
        _install_fake_sounddevice(monkeypatch, devices, hostapis)
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert [m["name"] for m in mics] == ["USB Mic"]
        assert mics[0]["host_api"] == "ALSA"

    def test_macos_prefers_core_audio(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        devices = [
            _sd_device(3, "Built-in Microphone", 0),
            _sd_device(7, "USB Mic", 1),
        ]
        hostapis = [
            {
                "name": "Core Audio",
                "devices": [3],
                "default_input_device": 3,
            },
            {"name": "Some Other API", "devices": [7], "default_input_device": 7},
        ]
        _install_fake_sounddevice(monkeypatch, devices, hostapis)
        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert len(mics) == 1
        assert mics[0]["host_api"] == "Core Audio"
        assert mics[0]["default"] is True

    def test_unknown_platform_keeps_all_records(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd8")
        devices = [_sd_device(0, "Mic A", 0), _sd_device(1, "Mic B", 1)]
        hostapis = [
            {"name": "API One", "devices": [0], "default_input_device": 0},
            {"name": "API Two", "devices": [1], "default_input_device": 1},
        ]
        _install_fake_sounddevice(monkeypatch, devices, hostapis)
        from voice_typer.server.server_platform import list_microphones

        assert [m["name"] for m in list_microphones()] == ["Mic A", "Mic B"]


class TestEnumerationStability:
    def test_repeated_enumeration_produces_identical_ids_no_accumulation(self, monkeypatch):
        from voice_typer.server.server_platform import list_microphones

        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        first_ids = [m["id"] for m in list_microphones()]
        cached_ids = [m["id"] for m in list_microphones()]

        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        second_ids = [m["id"] for m in list_microphones()]

        assert first_ids == cached_ids == second_ids
        assert "#" not in "".join(first_ids)

    def test_unique_names_never_gain_disambiguator_across_enumerations(self, monkeypatch):
        from voice_typer.server.server_platform import list_microphones

        for _ in range(3):
            _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
            ids = [m["id"] for m in list_microphones()]
            assert "Windows WASAPI|Microphone (Realtek(R) Audio)#2" not in ids


# ─── Cross-host-API id resolution ─────────────────────────────────────


_CANONICAL_MICS = [
    {
        "id": f"Windows WASAPI|{_AUDIORELAY_FULL}",
        "index": 25,
        "name": _AUDIORELAY_FULL,
        "host_api": "Windows WASAPI",
    },
    {
        "id": f"Windows WASAPI|{_WO_MIC}",
        "index": 26,
        "name": _WO_MIC,
        "host_api": "Windows WASAPI",
    },
    {
        "id": f"Windows WASAPI|{_REALTEK}",
        "index": 27,
        "name": _REALTEK,
        "host_api": "Windows WASAPI",
        "default": True,
    },
]


def _patch_canonical_mics(monkeypatch, mics=None):
    monkeypatch.setattr(
        "voice_typer.server.server_platform.list_microphones",
        lambda: [dict(m) for m in (mics if mics is not None else _CANONICAL_MICS)],
    )


class TestCrossHostApiIdResolution:
    def test_legacy_mme_stable_id_resolves_to_wasapi_twin_by_exact_name(self, monkeypatch):
        _patch_canonical_mics(monkeypatch)
        mic = find_microphone_by_id(f"MME|{_REALTEK}")
        assert mic is not None
        assert mic["index"] == 27
        assert mic["id"] == f"Windows WASAPI|{_REALTEK}"

    def test_legacy_mme_id_resolves_to_live_portaudio_index(self, monkeypatch):
        _patch_canonical_mics(monkeypatch)
        assert resolve_mic_id_to_device_index(f"MME|{_REALTEK}") == 27

    def test_wdmks_naming_variant_stays_unresolved_not_guessed(self, monkeypatch):
        """The WDM-KS endpoint name differs from its WASAPI twin's name —
        an exact-name match cannot identify it, so resolution must return
        None (system default) rather than picking some other device."""
        _patch_canonical_mics(monkeypatch)
        wdmks_id = "Windows WDM-KS|Microphone (Realtek HD Audio Mic input)"
        assert find_microphone_by_id(wdmks_id) is None
        assert resolve_mic_id_to_device_index(wdmks_id) is None

    def test_ambiguous_name_returns_none(self, monkeypatch):
        mics = [
            {"id": "Core Audio|USB Mic", "index": 4, "name": "USB Mic"},
            {"id": "Core Audio|USB Mic#2", "index": 5, "name": "USB Mic"},
        ]
        _patch_canonical_mics(monkeypatch, mics)
        assert find_microphone_by_id("MME|USB Mic") is None

    def test_pipeless_garbage_skips_name_match(self, monkeypatch):
        _patch_canonical_mics(monkeypatch)
        assert find_microphone_by_id("not an id at all") is None
        assert find_microphone_by_id(f"Windows WASAPI|{_REALTEK}") is not None

    def test_disambiguator_stripped_before_name_match(self, monkeypatch):
        """Persisted "MME|Mic#2" whose host API vanished: the "#N" suffix
        is stripped so the NAME segment can match a canonical device."""
        mics = [{"id": "Core Audio|Mic", "index": 9, "name": "Mic"}]
        _patch_canonical_mics(monkeypatch, mics)
        mic = find_microphone_by_id("MME|Mic#2")
        assert mic is not None
        assert mic["index"] == 9

    def test_pipe_in_device_name_splits_once_not_per_segment(self, monkeypatch):
        """A device name containing "|" must be recovered WHOLE — the name
        is everything after the FIRST "|". Splitting per-segment would
        resolve "WASAPI|A|B" against an unrelated device named "A"."""
        mics = [
            {"id": "Core Audio|A", "index": 3, "name": "A"},
            {"id": "Core Audio|A|B", "index": 4, "name": "A|B"},
        ]
        _patch_canonical_mics(monkeypatch, mics)
        mic = find_microphone_by_id("MME|A|B")
        assert mic is not None
        assert mic["index"] == 4
        assert mic["name"] == "A|B"

    def test_bare_digit_and_compound_ids_still_resolve(self, monkeypatch):
        _patch_canonical_mics(monkeypatch)
        assert resolve_mic_id_to_device_index("26") == 26
        assert resolve_mic_id_to_device_index("5|AudioRelay") == 25

    def test_end_to_end_through_real_enumeration_pipeline(self, monkeypatch):
        """Full pipeline against fake PortAudio data: enumerate (with
        canonicalization active) → resolve a pre-normalization id."""
        _install_fake_sounddevice(monkeypatch, _real_machine_devices(), _real_machine_hostapis())
        mic = find_microphone_by_id(f"MME|{_REALTEK}")
        assert mic is not None
        assert mic["index"] == 27
        assert resolve_mic_id_to_device_index(f"MME|{_WO_MIC}") == 26
