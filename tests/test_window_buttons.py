"""Tests for voice_typer.server.server_platform.window_buttons.

Covers the Linux window-button system snapshot used by the renderer's
"follow system" title-bar mode: DE classification, button-layout parsing,
the gsettings probe contract (mocked — no subprocess in tests), the
per-process cache, and non-Linux degradation.
"""

from __future__ import annotations

import platform
from unittest.mock import patch

import pytest
from voice_typer.server.server_platform import window_buttons as wb


@pytest.fixture(autouse=True)
def _fresh_cache():
    wb.reset_cache_for_tests()
    yield
    wb.reset_cache_for_tests()


class TestDetectDesktopEnvironment:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({}, "unknown"),
            ({"KDE_FULL_SESSION": "1"}, "kde"),
            ({"XDG_CURRENT_DESKTOP": "KDE"}, "kde"),
            ({"XDG_CURRENT_DESKTOP": "KDE:GNOME"}, "kde"),
            ({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "gnome"),
            ({"XDG_CURRENT_DESKTOP": "GNOME"}, "gnome"),
            ({"XDG_CURRENT_DESKTOP": "Unity:Unity7:ubuntu"}, "gnome"),
            ({"XDG_CURRENT_DESKTOP": "XFCE"}, "xfce"),
            ({"XDG_CURRENT_DESKTOP": "MATE"}, "mate"),
            ({"XDG_CURRENT_DESKTOP": "LXQt"}, "other"),
            ({"XDG_CURRENT_DESKTOP": "kde"}, "kde"),
        ],
    )
    def test_classification(self, env, expected):
        assert wb.detect_desktop_environment(env) == expected

    def test_kde_full_session_beats_xdg(self):
        env = {"KDE_FULL_SESSION": "true", "XDG_CURRENT_DESKTOP": "GNOME"}
        assert wb.detect_desktop_environment(env) == "kde"

    def test_defaults_to_os_environ(self):
        # No env argument → reads the real process env. On CI/dev machines
        # this is "unknown" or a real DE — either way it must not raise.
        assert isinstance(wb.detect_desktop_environment(), str)


class TestParseButtonLayout:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # gsettings returns a quoted GVariant string.
            ("'appmenu:minimize,maximize,close'", {"side": "right", "buttons": ["minimize", "maximize", "close"]}),
            ("'close,minimize,maximize:'", {"side": "left", "buttons": ["close", "minimize", "maximize"]}),
            ("'minimize,maximize'", {"side": "right", "buttons": ["minimize", "maximize"]}),
            # Unknown tokens (appmenu/spacer) are dropped.
            ("'appmenu:spacer,minimize,close'", {"side": "right", "buttons": ["minimize", "close"]}),
            # Bare unquoted value.
            ("minimize,maximize,close", {"side": "right", "buttons": ["minimize", "maximize", "close"]}),
        ],
    )
    def test_valid_values(self, value, expected):
        assert wb.parse_button_layout(value) == expected

    @pytest.mark.parametrize("value", [None, "", "   ", "'appmenu:spacer'", "':'", "'menu'"])
    def test_no_usable_buttons_returns_none(self, value):
        assert wb.parse_button_layout(value) is None


class TestSystemWindowButtons:
    def test_non_linux_platform_has_no_layout_and_no_subprocess(self):
        # This dev/CI machine may be non-Linux; force the non-Linux branch
        # deterministically and assert the gsettings probe is never called.
        with (
            patch.object(wb.platform, "system", return_value="Windows"),
            patch.object(wb, "_query_gsettings") as probe,
        ):
            snap = wb.system_window_buttons({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, force_refresh=True)
        assert snap == {"desktop_environment": "gnome", "layout": None}
        probe.assert_not_called()

    def test_linux_path_uses_gsettings_output(self):
        with (
            patch.object(wb.platform, "system", return_value="Linux"),
            patch.object(wb, "_query_gsettings", return_value="'appmenu:minimize,maximize,close'"),
        ):
            snap = wb.system_window_buttons(force_refresh=True)
        assert snap["layout"] == {"side": "right", "buttons": ["minimize", "maximize", "close"]}

    def test_linux_path_gsettings_failure_degrades_to_none(self):
        with (
            patch.object(wb.platform, "system", return_value="Linux"),
            patch.object(wb, "_query_gsettings", return_value=None),
        ):
            snap = wb.system_window_buttons(force_refresh=True)
        assert snap == {"desktop_environment": "unknown", "layout": None}

    def test_cache_returns_same_object_until_refresh(self):
        with patch.object(wb.platform, "system", return_value="Windows"):
            first = wb.system_window_buttons(force_refresh=True)
            second = wb.system_window_buttons()
        assert first is second
        with (
            patch.object(wb, "_query_gsettings", return_value="'close:'"),
            patch.object(wb.platform, "system", return_value="Linux"),
        ):
            refreshed = wb.system_window_buttons(force_refresh=True)
        assert refreshed["layout"] == {"side": "left", "buttons": ["close"]}

    def test_real_platform_smoke(self):
        # On the real platform the function must never raise and must
        # always expose both keys (this machine: Windows → layout None).
        snap = wb.system_window_buttons(force_refresh=True)
        assert set(snap) == {"desktop_environment", "layout"}
        if platform.system() != "Linux":
            assert snap["layout"] is None
