"""Tests for ``tray_menu.build_microphones_submenu``: pass-through rendering"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _FakeMenuItem:
    """Records construction args (label, action) + kwargs (checked=...)."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakePystrayModule:
    """Stand-in for the pystray module surface used by the submenu builder."""

    def __init__(self):
        self.MenuItem = _FakeMenuItem
        self.Menu = type("Menu", (), {"SEPARATOR": "SEP"})


@pytest.fixture(autouse=True)
def fake_pystray(monkeypatch):
    mock = _FakePystrayModule()
    monkeypatch.setitem(sys.modules, "pystray", mock)
    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_menu_mod, "pystray", mock)


def make_tray(microphones, config_microphone=None):
    return SimpleNamespace(
        _microphones=microphones,
        _config=SimpleNamespace(microphone=config_microphone),
        _controller=SimpleNamespace(change_microphone=MagicMock()),
        _open_page=MagicMock(),
    )


def mic_record(mic_id, name):
    """Record shape produced by server_platform.list_microphones."""
    return {"id": mic_id, "name": name, "index": 0, "host_api": "", "channels": 2, "default": False}


CANONICAL_THREE = [
    mic_record("Windows WASAPI|AudioRelay (Virtual Mic for AudioRelay)", "AudioRelay (Virtual Mic for AudioRelay)"),
    mic_record("Windows WASAPI|WO Mic (WO Mic Device)", "WO Mic (WO Mic Device)"),
    mic_record("Windows WASAPI|Microphone (Realtek(R) Audio)", "Microphone (Realtek(R) Audio)"),
]


def _duplicated_twelve():
    """Old broken shape: 3 endpoints × 4 host APIs (MME/DirectSound/"""
    apis = ("MME", "DirectSound", "Windows WASAPI", "WDM-KS")
    endpoints = (
        "AudioRelay (Virtual Mic for AudioRelay)",
        "WO Mic (WO Mic Device)",
        "Microphone (Realtek(R) Audio)",
    )
    return [mic_record(f"{api}|{ep}", ep) for api in apis for ep in endpoints]


def build(tray):
    from voice_typer.server.tray_menu import build_microphones_submenu

    return build_microphones_submenu(tray)


def menu_items(items):
    return [i for i in items if isinstance(i, _FakeMenuItem)]


def mic_items(items):
    return menu_items(items)[:-1]  # trailing item is always "More microphones..."


class TestCanonicalThreeDevices:
    def test_exactly_three_mic_items_plus_more(self):
        tray = make_tray(CANONICAL_THREE)
        items = build(tray)

        mics = mic_items(items)
        assert len(mics) == 3, (
            f"expected 3 mic items for 3 canonical devices, got {len(mics)}: {[m.args[0] for m in mics]}"
        )
        # Structure: 3 mics + separator + "More microphones...".
        assert "SEP" in items
        more = menu_items(items)[-1]
        assert "More microphones" in str(more.args[0])

    def test_no_duplicate_visible_names(self):
        tray = make_tray(CANONICAL_THREE)
        labels = [str(m.args[0]) for m in mic_items(build(tray))]
        assert len(labels) == len(set(labels)), f"duplicate visible names in submenu: {labels}"


class TestPassThroughWithDuplicatedInput:
    def test_builder_renders_duplicates_verbatim(self):
        """enumeration, the submenu renders 12 items with duplicate visible names."""
        duplicated = _duplicated_twelve()
        assert len(duplicated) == 12

        tray = make_tray(duplicated)
        mics = mic_items(build(tray))

        assert len(mics) == 12, "builder must render one MenuItem per record (pass-through)"
        labels = [str(m.args[0]) for m in mics]
        assert len(labels) != len(set(labels)), "12-record fixture must produce duplicate visible names"
        assert labels.count("Microphone (Realtek(R) Audio)") == 4


class TestActiveMicCheckmark:
    def test_matching_id_is_only_checked_item(self):
        active_id = "Windows WASAPI|Microphone (Realtek(R) Audio)"
        tray = make_tray(CANONICAL_THREE, config_microphone=active_id)

        mics = mic_items(build(tray))
        states = {}
        for m in mics:
            checked_cb = m.kwargs.get("checked")
            assert callable(checked_cb), f"checked= must stay a callable (raw bool crashes pystray); got {checked_cb!r}"
            states[str(m.args[0])] = checked_cb(None)

        checked = [name for name, ok in states.items() if ok]
        assert checked == ["Microphone (Realtek(R) Audio)"], (
            f"exactly the matching device must be checked; got {states}"
        )

    @pytest.mark.parametrize("empty_value", [None, ""])
    def test_no_active_mic_checks_nothing(self, empty_value):
        tray = make_tray(CANONICAL_THREE, config_microphone=empty_value)

        mics = mic_items(build(tray))
        for m in mics:
            assert m.kwargs.get("checked")(None) is False, (
                f"config.microphone={empty_value!r} must leave every item unchecked; got {m.args[0]!r} checked"
            )


class TestLongDeviceNames:
    def test_sixty_char_name_passed_through_untruncated(self):
        long_name = "Very Long Microphone Name For Truncation Testing (Realtek(R) Audio) Extended Suffix"
        assert len(long_name) > 60
        record = mic_record(f"Windows WASAPI|{long_name}", long_name)

        tray = make_tray([record])
        items = build(tray)  # must not raise

        mics = mic_items(items)
        assert len(mics) == 1
        assert str(mics[0].args[0]) == long_name, "our code must pass the full label through (OS may clip visually)"


class TestNeverEmptySubmenu:
    def test_empty_microphones_still_render_more_item(self):
        tray = make_tray([])

        items = build(tray)

        assert items, "submenu must NEVER be empty, Settings entry point must survive an empty enumeration"
        remaining = menu_items(items)
        assert len(remaining) == 1
        assert "More microphones" in str(remaining[0].args[0])
