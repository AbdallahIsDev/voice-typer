"""Stable microphone identifiers + invalid-name filtering."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server.server_platform.microphone_list import (
    _is_non_mic_device,
    _stable_device_id,
    resolve_mic_id_to_device_index,
)
from voice_typer.server.server_platform.remote_session import _is_invalid_device_name


@pytest.fixture(autouse=True)
def _reset_list_cache():
    """Isolate the module-level TTL cache between tests."""
    from voice_typer.server.server_platform import microphone_list as _ml

    _ml.invalidate_microphone_list_cache()
    yield
    _ml.invalidate_microphone_list_cache()


def _install_fake_sounddevice(monkeypatch, devices, hostapis=None, default_input=None):
    """Install a fake ``sounddevice`` module returning the given data."""
    if hostapis is None:
        hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    if default_input is None and devices:
        default_input = dict(devices[0], index=devices[0].get("index", -1))

    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = lambda *args, **kwargs: (
        default_input if kwargs.get("kind") == "input" else devices
    )
    fake_sd.query_hostapis.return_value = hostapis
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    return fake_sd


def _sd_device(index: int, name: str, hostapi: int = 0, channels: int = 2, rate: float = 48000.0):
    return {
        "index": index,
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": channels,
        "default_samplerate": rate,
    }


class TestInvalidDeviceNamePredicate:
    @pytest.mark.parametrize(
        "name",
        [
            "Microphone (Realtek Audio)",
            "Blue Yeti",
            "Line 1 (Virtual Audio Cable)",
            "Headset Microphone (Corsair VOID RGB)",
            "Microphone () (Realtek)",  # one empty group among real content
            "Input (Front panel)",
            "Ægirs Mic",  # non-ASCII alphanumerics count as content
            "Микрофон (Realtek Audio)",  # Cyrillic + real endpoint description
            "麦克风 (USB Audio)",  # CJK + real endpoint description
            "Микрофон ()",  # generic label in another language stays valid
        ],
    )
    def test_valid_names(self, name):
        assert _is_invalid_device_name(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            "\t\n",
            None,
            123,
            "---",
            "()",
            "( )",
            "Input ()",
            "Microphone ()",
            "Mic ( )",
            "input()",
            "Recording ()",
            "OUTPUT ()",
            "--- ()",  # punctuation + empty parens, no alphanumeric content
        ],
    )
    def test_invalid_names(self, name):
        assert _is_invalid_device_name(name) is True

    def test_trailing_whitespace_is_ignored(self):
        assert _is_invalid_device_name("Blue Yeti   ") is False

    def test_generic_label_without_empty_parens_stays_valid(self):
        """A bare generic word WITHOUT an empty parenthetical is not our"""
        assert _is_invalid_device_name("Microphone Array") is False

    def test_coexists_with_non_mic_predicate(self):
        """The new predicate is orthogonal to the stereo-mix/line-in"""
        assert _is_non_mic_device("Stereo Mix (Realtek Audio)")
        assert not _is_invalid_device_name("Stereo Mix (Realtek Audio)")


class TestStableDeviceId:
    def test_format_is_host_api_pipe_name(self):
        seen: set[str] = set()
        assert _stable_device_id("Windows WASAPI", "Blue Yeti", seen) == "Windows WASAPI|Blue Yeti"

    def test_duplicates_get_disambiguator(self):
        seen: set[str] = set()
        first = _stable_device_id("MME", "USB Mic", seen)
        second = _stable_device_id("MME", "USB Mic", seen)
        third = _stable_device_id("MME", "USB Mic", seen)
        assert first == "MME|USB Mic"
        assert second == "MME|USB Mic#2"
        assert third == "MME|USB Mic#3"

    def test_same_name_different_host_api_is_distinct(self):
        seen: set[str] = set()
        a = _stable_device_id("MME", "USB Mic", seen)
        b = _stable_device_id("Windows WASAPI", "USB Mic", seen)
        assert a != b

    def test_deterministic_across_enumerations(self):
        def build():
            seen: set[str] = set()
            return [_stable_device_id(h, n, seen) for h, n in [("MME", "A"), ("MME", "B"), ("MME", "A")]]

        assert build() == build()


class TestListMicrophonesFiltering:
    def test_placeholder_and_invalid_names_are_filtered(self, monkeypatch):
        devices = [
            _sd_device(0, "Microphone (Realtek Audio)"),
            _sd_device(1, "Input ()"),
            _sd_device(2, ""),
            _sd_device(3, "   "),
            _sd_device(4, "Stereo Mix (Realtek Audio)"),  # existing non-mic filter
            _sd_device(5, "Blue Yeti"),
            _sd_device(6, "Speakers (Realtek)", channels=0),  # output-only
        ]
        _install_fake_sounddevice(monkeypatch, devices)

        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        names = [m["name"] for m in mics]
        assert names == ["Microphone (Realtek Audio)", "Blue Yeti"]

    def test_contract_shape_preserved(self, monkeypatch):
        devices = [_sd_device(2, "Blue Yeti")]
        _install_fake_sounddevice(monkeypatch, devices)

        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert len(mics) == 1
        mic = mics[0]
        assert set(mic.keys()) == {"id", "index", "name", "host_api", "channels", "default", "is_bluetooth"}
        assert mic["id"] == "MME|Blue Yeti"
        assert mic["index"] == 2
        assert isinstance(mic["id"], str)
        assert isinstance(mic["index"], int)

    def test_names_are_trimmed(self, monkeypatch):
        devices = [_sd_device(0, "  Blue Yeti  ")]
        _install_fake_sounddevice(monkeypatch, devices)

        from voice_typer.server.server_platform import list_microphones

        (mic,) = list_microphones()
        assert mic["name"] == "Blue Yeti"
        assert mic["id"] == "MME|Blue Yeti"

    def test_duplicate_names_get_unique_ids(self, monkeypatch):
        devices = [
            _sd_device(3, "USB Mic"),
            _sd_device(7, "USB Mic"),
        ]
        _install_fake_sounddevice(monkeypatch, devices)

        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        ids = [m["id"] for m in mics]
        assert len(set(ids)) == 2
        assert ids[0] == "MME|USB Mic"
        assert ids[1] == "MME|USB Mic#2"


class TestStableIdsAcrossReenumeration:
    def test_id_survives_index_shift(self, monkeypatch):
        """Simulate a reboot/replug: same physical device set, different"""
        before_devices = [
            _sd_device(3, "Microphone (Realtek Audio)", hostapi=1),
            _sd_device(5, "Blue Yeti", hostapi=0),
        ]
        after_devices = [
            _sd_device(9, "Blue Yeti", hostapi=0),
            _sd_device(11, "Microphone (Realtek Audio)", hostapi=1),
        ]

        from voice_typer.server.server_platform import list_microphones

        _install_fake_sounddevice(monkeypatch, before_devices)
        before = {m["name"]: m["id"] for m in list_microphones()}

        _install_fake_sounddevice(monkeypatch, after_devices)
        after = {m["name"]: m["id"] for m in list_microphones()}

        assert before == after

    def test_ids_unique_per_enumeration(self, monkeypatch):
        devices = [
            _sd_device(0, "A"),
            _sd_device(1, "B"),
            _sd_device(2, "A"),
        ]
        _install_fake_sounddevice(monkeypatch, devices)

        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert len({m["id"] for m in mics}) == 3


class TestDefaultFlag:
    def test_exactly_one_default(self, monkeypatch):
        devices = [
            _sd_device(0, "Mic A"),
            _sd_device(4, "Mic B"),
            _sd_device(9, "Mic C"),
        ]
        _install_fake_sounddevice(monkeypatch, devices, default_input=_sd_device(4, "Mic B"))

        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert [m["default"] for m in mics].count(True) == 1
        assert next(m for m in mics if m["default"])["name"] == "Mic B"

    def test_no_default_when_query_fails(self, monkeypatch):
        devices = [_sd_device(0, "Mic A")]
        fake_sd = _install_fake_sounddevice(monkeypatch, devices)
        fake_sd.query_devices.side_effect = RuntimeError("no audio")

        from voice_typer.server.server_platform import list_microphones

        assert list_microphones() == []

    def test_no_crash_when_default_input_missing(self, monkeypatch):
        """No default input (kind='input' query returns nothing usable)"""
        devices = [_sd_device(0, "Mic A")]
        _install_fake_sounddevice(monkeypatch, devices, default_input={"index": -1, "name": ""})

        from voice_typer.server.server_platform import list_microphones

        mics = list_microphones()
        assert [m["default"] for m in mics].count(True) == 0


class TestFindMicrophoneByIdLegacyCompat:
    _mics = [
        {"id": "Windows WASAPI|Blue Yeti", "index": 5, "name": "Blue Yeti", "host_api": "Windows WASAPI"},
        {"id": "MME|USB Mic", "index": 7, "name": "USB Mic", "host_api": "MME"},
    ]

    def test_exact_stable_id_match(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: [dict(m) for m in self._mics],
        )
        from voice_typer.server.server_platform import find_microphone_by_id

        mic = find_microphone_by_id("Windows WASAPI|Blue Yeti")
        assert mic is not None
        assert mic["index"] == 5

    def test_legacy_digit_id_resolves_by_live_index(self, monkeypatch):
        """Old persisted id \"7\" resolves to whatever is enumerated at"""
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: [dict(m) for m in self._mics],
        )
        from voice_typer.server.server_platform import find_microphone_by_id

        mic = find_microphone_by_id("7")
        assert mic is not None
        assert mic["index"] == 7
        assert mic["id"] == "MME|USB Mic"

    def test_unknown_digit_id_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: [dict(m) for m in self._mics],
        )
        from voice_typer.server.server_platform import find_microphone_by_id

        assert find_microphone_by_id("99") is None

    def test_gone_stable_id_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: [dict(m) for m in self._mics],
        )
        from voice_typer.server.server_platform import find_microphone_by_id

        assert find_microphone_by_id("MME|Vanished Mic") is None


class TestResolveMicIdToDeviceIndex:
    def _patch_mics(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: [
                {"id": "Windows WASAPI|Blue Yeti", "index": 5, "name": "Blue Yeti", "host_api": "Windows WASAPI"},
                {"id": "MME|USB Mic", "index": 7, "name": "USB Mic", "host_api": "MME"},
            ],
        )

    def test_none_is_system_default(self, monkeypatch):
        self._patch_mics(monkeypatch)
        assert resolve_mic_id_to_device_index(None) is None

    def test_stable_id_resolves_to_live_index(self, monkeypatch):
        self._patch_mics(monkeypatch)
        assert resolve_mic_id_to_device_index("Windows WASAPI|Blue Yeti") == 5

    def test_legacy_digit_id_resolves_to_current_index(self, monkeypatch):
        self._patch_mics(monkeypatch)
        assert resolve_mic_id_to_device_index("7") == 7

    def test_legacy_digit_int_input_resolves(self, monkeypatch):
        """int mic ids (defensive, config schema is str|None) still resolve."""
        self._patch_mics(monkeypatch)
        assert resolve_mic_id_to_device_index(5) == 5

    def test_legacy_compound_id_prefers_name_match(self, monkeypatch):
        """Old compound \"<index>|<name>\": the saved index is stale (device"""
        self._patch_mics(monkeypatch)
        assert resolve_mic_id_to_device_index("5|Blue Yeti") == 5
        mics = [
            {"id": "Windows WASAPI|Blue Yeti", "index": 12, "name": "Blue Yeti", "host_api": "Windows WASAPI"},
            {"id": "MME|USB Mic", "index": 7, "name": "USB Mic", "host_api": "MME"},
        ]
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: mics)
        assert resolve_mic_id_to_device_index("5|Blue Yeti|Windows WASAPI") == 12

    def test_legacy_compound_id_falls_back_to_saved_index(self, monkeypatch):
        """Old compound form whose device vanished by NAME still resolves"""
        mics = [{"id": "MME|Other Mic", "index": 5, "name": "Other Mic", "host_api": "MME"}]
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: mics)
        assert resolve_mic_id_to_device_index("5|Blue Yeti") == 5

    def test_legacy_compound_id_empty_name_skips_substring_match(self, monkeypatch):
        """Corrupt value \"5|\" must NOT substring-match \"\" (which would"""
        mics = [
            {"id": "MME|First Mic", "index": 0, "name": "First Mic", "host_api": "MME"},
            {"id": "MME|Other", "index": 5, "name": "Other", "host_api": "MME"},
        ]
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: mics)
        assert resolve_mic_id_to_device_index("5|") == 5

    def test_unresolvable_id_returns_none(self, monkeypatch):
        self._patch_mics(monkeypatch)
        assert resolve_mic_id_to_device_index("MME|Gone") is None
        assert resolve_mic_id_to_device_index("not an id at all") is None


class TestFindMicrophoneByIdCompoundCompat:
    """find_microphone_by_id resolves every persisted id shape."""

    _mics = [
        {"id": "Windows WASAPI|Blue Yeti", "index": 12, "name": "Blue Yeti", "host_api": "Windows WASAPI"},
        {"id": "MME|USB Mic", "index": 7, "name": "USB Mic", "host_api": "MME"},
    ]

    def _patch(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.list_microphones",
            lambda: [dict(m) for m in self._mics],
        )

    def test_compound_name_match_returns_full_dict_with_new_stable_id(self, monkeypatch):
        self._patch(monkeypatch)
        from voice_typer.server.server_platform import find_microphone_by_id

        mic = find_microphone_by_id("5|Blue Yeti")
        assert mic is not None
        assert mic["index"] == 12
        assert mic["id"] == "Windows WASAPI|Blue Yeti"

    def test_compound_gone_by_name_falls_back_to_index(self, monkeypatch):
        self._patch(monkeypatch)
        from voice_typer.server.server_platform import find_microphone_by_id

        mic = find_microphone_by_id("7|Vanished Name")
        assert mic is not None
        assert mic["index"] == 7

    def test_compound_fully_gone_returns_none(self, monkeypatch):
        self._patch(monkeypatch)
        from voice_typer.server.server_platform import find_microphone_by_id

        assert find_microphone_by_id("99|Ghost Mic") is None

    def test_stable_id_never_enters_compound_parser(self, monkeypatch):
        """A stable id whose host-API segment is non-numeric must resolve"""
        seen_calls = []
        real = self._mics

        def spy():
            seen_calls.append(1)
            return [dict(m) for m in real]

        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", spy)
        from voice_typer.server.server_platform import find_microphone_by_id

        assert find_microphone_by_id("Windows WASAPI|Blue Yeti")["index"] == 12


class TestDeviceManagerResolveDeviceStableId:
    def _make_dm(self, microphone_value):
        from tests.test_device_manager import _make_device_manager

        recorder = MagicMock()
        recorder.config = MagicMock(sample_rate=16000, microphone=microphone_value)
        return _make_device_manager(recorder=recorder), recorder

    def test_none_stays_system_default(self):
        dm, _ = self._make_dm(None)
        assert dm._resolve_device() is None

    def test_stable_id_resolves_to_enumerated_index(self, monkeypatch):
        dm, _ = self._make_dm("Windows WASAPI|Blue Yeti")
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.resolve_mic_id_to_device_index",
            lambda mic_id: 5,
        )
        assert dm._resolve_device() == 5

    def test_unresolvable_stable_id_returns_none(self, monkeypatch):
        dm, _ = self._make_dm("Windows WASAPI|Gone")
        monkeypatch.setattr(
            "voice_typer.server.server_platform.microphone_list.resolve_mic_id_to_device_index",
            lambda mic_id: None,
        )
        assert dm._resolve_device() is None

    def test_bare_numeric_string_resolves_via_canonical(self, monkeypatch):
        dm, _ = self._make_dm("5")
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


class TestSetConfigMicrophoneValidator:
    def test_accepts_new_style_stable_id(self):
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"microphone": "Windows WASAPI|Blue Yeti"})
        assert errors == []
        assert validated == {"microphone": "Windows WASAPI|Blue Yeti"}

    def test_accepts_disambiguated_id(self):
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"microphone": "MME|USB Mic#2"})
        assert errors == []
        assert validated["microphone"] == "MME|USB Mic#2"

    def test_accepts_null_clearing_selection(self):
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"microphone": None})
        assert errors == []
        assert validated == {"microphone": None}

    def test_accepts_legacy_index_id(self):
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"microphone": "7"})
        assert errors == []
        assert validated == {"microphone": "7"}

    def test_rejects_non_string_non_null(self):
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"microphone": 123})
        assert errors
        assert "microphone" not in validated

    def test_rejects_over_length_id(self):
        from voice_typer.server.config_validators import validate_config_update

        validated, errors = validate_config_update({"microphone": "MME|" + "x" * 512})
        assert errors
        assert "microphone" not in validated


class TestPersistedIdRoundTripAcrossReboot:
    """list_microphones() → persist the stable id → indices shuffle"""

    def test_resolve_mic_id_to_device_index_survives_index_shuffle(self, monkeypatch):
        # All devices share the canonical host API: a record enumerated
        before_devices = [
            _sd_device(3, "Microphone (Realtek Audio)", hostapi=1),
            _sd_device(5, "Blue Yeti", hostapi=1),
            _sd_device(8, "USB Mic", hostapi=1),
        ]
        after_devices = [
            _sd_device(9, "Blue Yeti", hostapi=1),
            _sd_device(10, "USB Mic", hostapi=1),
            _sd_device(14, "Microphone (Realtek Audio)", hostapi=1),
        ]

        from voice_typer.server.server_platform import list_microphones

        _install_fake_sounddevice(monkeypatch, before_devices)
        saved = {m["name"]: m["id"] for m in list_microphones()}

        _install_fake_sounddevice(monkeypatch, after_devices)
        assert resolve_mic_id_to_device_index(saved["Blue Yeti"]) == 9
        assert resolve_mic_id_to_device_index(saved["Microphone (Realtek Audio)"]) == 14
        assert resolve_mic_id_to_device_index(saved["USB Mic"]) == 10

    def test_find_microphone_by_id_survives_index_shuffle(self, monkeypatch):
        before_devices = [_sd_device(5, "Blue Yeti", hostapi=0)]
        after_devices = [_sd_device(21, "Blue Yeti", hostapi=0)]

        from voice_typer.server.server_platform import find_microphone_by_id, list_microphones

        _install_fake_sounddevice(monkeypatch, before_devices)
        (saved,) = list_microphones()

        _install_fake_sounddevice(monkeypatch, after_devices)
        mic = find_microphone_by_id(saved["id"])
        assert mic is not None
        assert mic["index"] == 21
        assert mic["id"] == saved["id"]

    def test_duplicate_disambiguator_survives_when_twin_vanishes(self, monkeypatch):
        """Two identical USB mics (#2 disambiguator); after reboot only"""
        before_devices = [
            _sd_device(2, "USB Mic"),
            _sd_device(6, "USB Mic"),
        ]
        # After reboot the FIRST twin vanished; the remaining device must
        after_devices = [_sd_device(11, "USB Mic")]

        from voice_typer.server.server_platform import list_microphones

        _install_fake_sounddevice(monkeypatch, before_devices)
        mics = list_microphones()
        assert [m["id"] for m in mics] == ["MME|USB Mic", "MME|USB Mic#2"]

        _install_fake_sounddevice(monkeypatch, after_devices)
        assert resolve_mic_id_to_device_index("MME|USB Mic#2") == 11


class TestDeviceManagerResolveDeviceRebootRoundTrip:
    """DeviceManager._resolve_device against REAL enumeration (fake sd),"""

    def _make_dm(self, microphone_value, monkeypatch):
        from voice_typer.server import microphone_watcher as _mw
        from voice_typer.server.recording.device_manager import DeviceManager

        watcher_stub = MagicMock()
        monkeypatch.setattr(_mw, "MicrophoneDeviceWatcher", watcher_stub)

        recorder = MagicMock()
        recorder.config = MagicMock(sample_rate=16000, microphone=microphone_value)
        return DeviceManager(recorder)

    def test_persisted_stable_id_resolves_after_reboot(self, monkeypatch):
        _install_fake_sounddevice(
            monkeypatch,
            [_sd_device(3, "Microphone (Realtek Audio)", hostapi=1), _sd_device(5, "Blue Yeti", hostapi=0)],
        )
        dm = self._make_dm("Windows WASAPI|Microphone (Realtek Audio)", monkeypatch)

        _install_fake_sounddevice(
            monkeypatch,
            [_sd_device(9, "Blue Yeti", hostapi=0), _sd_device(14, "Microphone (Realtek Audio)", hostapi=1)],
        )
        assert dm._resolve_device() == 14

    def test_corrupt_compound_empty_name_uses_saved_index(self, monkeypatch):
        """Corrupt value \"7|\" must NOT name-match \"\" (which would return"""
        _install_fake_sounddevice(monkeypatch, [_sd_device(0, "First Mic"), _sd_device(7, "Other")])
        dm = self._make_dm("7|", monkeypatch)
        assert dm._resolve_device() == 7

    def test_device_list_cache_has_no_legacy_id_field(self, monkeypatch):
        """The recorder-side cache must not carry the OLD str(index) id —"""
        _install_fake_sounddevice(monkeypatch, [_sd_device(0, "Mic A"), _sd_device(4, "Mic B")])
        dm = self._make_dm(None, monkeypatch)
        cached = dm._refresh_device_list()
        assert len(cached) == 2
        for entry in cached:
            assert "id" not in entry
            assert {"index", "name", "max_input_channels", "default_samplerate", "hostapi"} <= set(entry)
